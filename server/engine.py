"""Shipment simulator + risk engine.

The engine owns all state. It advances on an accelerated clock, infers custody
handoffs from where the box is (no scanning), computes the margin between the
organ's remaining safe time and its ETA, and decides when a human is needed.
"""
from __future__ import annotations

import math
import os
import time
from copy import deepcopy

from . import decision_log
from . import scenario as sc
from .demo_feeds import RECIPIENT_CALL_BUFFER_MIN, RECIPIENT_TRAVEL_MIN
from .demo_feeds import WHO as DEMO_WHO
from .geo import flight_minutes, ground_minutes, haversine_km, lerp

DEFAULT_SPEED = 120.0          # simulated minutes per real minute (2 sim-min per second)
ESCALATION_SPEED = 10.0        # slow the world down while a human decides
BOX_OFFLINE_SEC = float(os.getenv("BOX_OFFLINE_SEC", 600))  # no box report for this long = box offline


def fmt_hm(minutes: float) -> str:
    m = int(round(minutes))
    sign = "-" if m < 0 else ""
    m = abs(m)
    return f"{sign}{m // 60}h {m % 60:02d}m"


def say_hm(minutes: float) -> str:
    m = int(round(abs(minutes)))
    h, mm = divmod(m, 60)
    parts = []
    if h:
        parts.append(f"{h} hour{'s' if h != 1 else ''}")
    if mm or not h:
        parts.append(f"{mm} minute{'s' if mm != 1 else ''}")
    return " ".join(parts)


class Engine:
    demo = None  # optional DemoFeeds: simulated stand-ins for feeds that aren't integrated yet

    def __init__(self):
        self.reset()

    # ------------------------------------------------------------------ setup
    def reset(self, organ: str | None = None, preservation: str | None = None):
        cfg = sc.SCENARIO
        self.organ_key = organ or cfg["organ"]
        organ_cfg = sc.ORGANS[self.organ_key]
        self.preservation_key = preservation if preservation in sc.PRESERVATION else "ice"
        pres = sc.PRESERVATION[self.preservation_key]
        self.window_min = organ_cfg["window_min"] * pres["window_mult"]
        # Kidneys often wait hours during allocation; hearts/livers leave soon after recovery.
        self.elapsed0 = cfg["elapsed_at_start_min"] if self.organ_key == "kidney" else 60
        self.shipment_id = cfg["shipment_id"]
        self.destination = cfg["destination"]

        self.sim_min = 0.0
        self.speed = DEFAULT_SPEED
        self.speed_before_escalation = None
        self.running = False
        self.delivered = False

        self.leg_idx = 0
        self.legs = [dict(l, delay=0.0) for l in sc.initial_legs()]
        if self.organ_key != "kidney":
            # Short-window organs fly charter with the recovery team; aircraft is already on standby.
            charter = [l for l in self._charter_legs(sc.p("donor"), self.destination) if l["mode"] != "stationary"]
            self.legs = [dict(l, delay=0.0) for l in [self.legs[0]] + charter]
        self.legs[0]["duration"] += pres["extra_packaging_min"]
        self.planned = [l["duration"] for l in self.legs]  # original durations, for schedule comparisons
        self.leg_idx = 0
        self.leg_elapsed = 0.0
        self.plan_version = 1
        self.feed_applied: dict[str, tuple[int, float]] = {}  # feed source -> (leg index, minutes applied)
        self.path: list[list[float]] = []
        self._last_path_sim = -999.0

        self.temp_base = 4.2
        self.temp = self.temp_base
        self.last_shock_sim: float | None = None
        self.device: dict | None = None
        self.box_offline = False
        self.critical = False
        self.critical_best_eta: float | None = None

        self.events: list[dict] = []
        self.custody: list[dict] = []
        self._event_id = 0
        self.status = "green"
        self.reasons: list[str] = []
        self.escalation: dict | None = None
        self.escalated_versions: set[int] = set()
        self.needs_escalation = False
        self.announced: set[tuple] = set()
        self.last_trouble: str | None = None

        self._log_custody(self.legs[0]["custodian"], "Shipment created at cross-clamp")
        self.event("system", f"{self.organ_label()} {self.shipment_id} packaged at {sc.PLACES['donor']['name']}. "
                             f"Destination {sc.PLACES[self.destination]['name']}.", audience=["all"])
        self._record_path(force=True)
        self._recompute_status(initial=True)

    def _demo(self):
        return self.demo if self.demo and self.demo.enabled else None

    def leg_start_sim(self, i: int) -> float:
        """Simulated minute at which leg `i` starts (or started)."""
        if i <= self.leg_idx:
            return self.sim_min - self.leg_elapsed
        return (self.sim_min + self.leg["duration"] - self.leg_elapsed
                + sum(l["duration"] for l in self.legs[self.leg_idx + 1:i]))

    def minute_of_day(self, at_sim: float) -> int:
        h, m = map(int, sc.SCENARIO["clock_start"].split(":"))
        return (h * 60 + m + int(at_sim)) % (24 * 60)

    def organ_label(self):
        return sc.ORGANS[self.organ_key]["label"]

    # ------------------------------------------------------------- utilities
    def clock(self, at_min: float | None = None) -> str:
        h, m = map(int, sc.SCENARIO["clock_start"].split(":"))
        total = h * 60 + m + int(self.sim_min if at_min is None else at_min)
        total %= 24 * 60
        return f"{total // 60:02d}:{total % 60:02d}"

    def event(self, kind: str, text: str, say: str | None = None, audience=None, vibrate=None,
              why: str = "", who: str = "system"):
        self._event_id += 1
        ev = dict(id=self._event_id, t=round(self.sim_min, 1), clock=self.clock(), kind=kind, text=text,
                  say=say, audience=audience or ["all"], vibrate=vibrate, real_ts=time.time(),
                  action_id=decision_log.new_action_id(), utc=decision_log.utc_now())
        decision_log.write(ts_utc=ev["utc"], action_id=ev["action_id"], shipment=self.shipment_id, kind=kind,
                           what=text, why=why, who=who, plan_version=self.plan_version)
        self.events.append(ev)
        self.events = self.events[-120:]
        return ev

    def _log_custody(self, who: str, how: str):
        self.custody.append(dict(t=round(self.sim_min, 1), clock=self.clock(), custodian=who, how=how,
                                 utc=decision_log.utc_now()))
        decision_log.write(ts_utc=self.custody[-1]["utc"], action_id=decision_log.new_action_id(),
                           shipment=self.shipment_id, kind="custody", what=f"Custody: {who}", why=how,
                           who="system", plan_version=self.plan_version)

    def _record_path(self, force=False):
        if force or self.sim_min - self._last_path_sim >= 2:
            lat, lng = self.position()
            self.path.append([round(lat, 5), round(lng, 5)])
            self.path = self.path[-800:]
            self._last_path_sim = self.sim_min

    # ------------------------------------------------------------ derived values
    @property
    def leg(self):
        return self.legs[min(self.leg_idx, len(self.legs) - 1)]

    def position(self):
        leg = self.leg
        if self.delivered:
            return leg["end"]
        t = self.leg_elapsed / leg["duration"] if leg["duration"] > 0 else 1
        if leg["mode"] == "stationary":
            return leg["start"]
        d = self._demo()
        if d and leg["mode"] == "flight":
            pos = d.replay_position(self._airport_key(leg["start"]), self._airport_key(leg["end"]), t)
            if pos:
                return pos
        return lerp(leg["start"], leg["end"], t)

    def elapsed(self):
        return self.elapsed0 + self.sim_min

    def remaining(self):
        return self.window_min - self.elapsed()

    def eta(self):
        if self.delivered:
            return 0.0
        rest = self.leg["duration"] - self.leg_elapsed
        return rest + sum(l["duration"] for l in self.legs[self.leg_idx + 1:])

    def margin(self):
        return self.remaining() - self.eta()

    # ------------------------------------------------------------------- tick
    def tick(self, real_dt: float):
        if not self.running or self.delivered:
            return
        adv = real_dt * self.speed / 60.0
        self.sim_min += adv
        self.leg_elapsed += adv
        while not self.delivered and self.leg_elapsed >= self.leg["duration"]:
            overflow = self.leg_elapsed - self.leg["duration"]
            self._complete_leg()
            self.leg_elapsed = overflow if not self.delivered else 0
        # gentle temperature drift around the set point
        self.temp = self.temp_base + 0.15 * math.sin(self.sim_min / 7)
        self._record_path()
        self._recompute_status()
        self._or_announcements()

    def _complete_leg(self):
        done = self.leg
        if self.leg_idx >= len(self.legs) - 1:
            self.delivered = True
            dest = sc.PLACES[self.destination]
            self._log_custody(f"Transplant OR, {dest['short']}", "Arrived inside hospital geofence")
            self.event("delivered",
                       f"Delivered to {dest['name']}. Total cold time {fmt_hm(self.elapsed())}.",
                       say=f"{self.organ_label()} delivered to {dest['short']}. Total cold time {say_hm(self.elapsed())}.",
                       audience=["all"], vibrate=[100, 50, 100, 50, 400])
            self.running = False
            return
        self.leg_idx += 1
        nxt = self.leg
        if nxt["custodian"] != done["custodian"]:
            self._log_custody(nxt["custodian"], "Auto-logged: box entered geofence / proximity handoff")
            courier_turn = nxt["mode"] == "ground"
            self.event("handoff", f"Custody: {done['custodian']} → {nxt['custodian']}",
                       say=None, audience=["courier", "map", "control"] if courier_turn else ["map", "control"],
                       vibrate=[80, 80, 80] if courier_turn else None)
        self.event("leg", f"Now: {nxt['name']}", audience=["map", "control"])

    # ------------------------------------------------------------ risk engine
    def _recompute_status(self, initial=False):
        reasons = []
        status = "green"
        margin = self.margin()
        amber_at = self.window_min * sc.AMBER_MARGIN_SHARE
        red_at = self.window_min * sc.RED_MARGIN_SHARE
        margin_red = False

        if self.delivered:
            status = "green"
        else:
            if margin < red_at:
                status, margin_red = "red", True
                reasons.append(f"ETA leaves only {fmt_hm(margin)} of safe time")
            elif margin < amber_at:
                status = "amber"
                reasons.append(f"Margin shrinking: {fmt_hm(margin)} spare")
            if self.temp > sc.TEMP_RED_ABOVE:
                status = "red"
                reasons.append(f"Temperature excursion {self.temp:.1f}°C")
            elif not (sc.TEMP_OK[0] <= self.temp <= sc.TEMP_OK[1]):
                status = "red" if status == "red" else "amber"
                reasons.append(f"Temperature {self.temp:.1f}°C outside 1–8°C")
            if self.last_shock_sim is not None and self.sim_min - self.last_shock_sim < 20:
                status = "red" if status == "red" else "amber"
                reasons.append("Shock detected, inspect packaging")

            d = self._demo()
            if d and not d.theatre_ok(self.destination):
                status = "red" if status == "red" else "amber"
                reasons.append(f"[DEMO] {sc.PLACES[self.destination]['short']} OR not ready (simulated)")
            if self.critical and margin < 0:
                status = "red"
                reasons.insert(0, f"CRITICAL, no feasible route inside the planned window. "
                                  f"Best available ETA {fmt_hm(self.critical_best_eta or self.eta())}")
            if self.box_offline:
                status = "red" if status == "red" else "amber"
                reasons.append("Box offline: last known location only")

        prev = self.status
        self.status, self.reasons = status, reasons
        if initial or status == prev:
            pass
        else:
            words = {"green": "back on track", "amber": "margin shrinking", "red": "action needed"}
            self.event("status", f"Status {prev.upper()} → {status.upper()}: {'; '.join(reasons) or 'on track'}",
                       say=f"Status {status}. {words[status]}." if status != "green" else "Back on track.",
                       audience=["box", "map", "control", "courier"],
                       vibrate=[600] if status == "red" else ([250, 100, 250] if status == "amber" else [60]),
                       why="; ".join(reasons) or "back within thresholds")
        open_esc = self.escalation and not self.escalation.get("decided")
        if (margin_red and not open_esc and self.plan_version not in self.escalated_versions
                and not self.delivered):
            self.needs_escalation = True

    def _or_announcements(self):
        if self.delivered:
            return
        eta = self.eta()
        for mark in (120, 60, 30, 10):
            key = (self.plan_version, mark)
            if eta <= mark and key not in self.announced:
                self.announced.add(key)
                dest = sc.PLACES[self.destination]
                self.event("or", f"{dest['short']}: organ {mark} min out",
                           say=f"{self.organ_label()} for {dest['short']} is {mark} minutes out."
                               + (" Begin anesthesia." if mark == 60 else "")
                               + (" Prepare the back table." if mark == 30 else ""),
                           audience=["or", "map"])
        d = self._demo()
        key = (self.plan_version, "recipient")
        if d and eta <= RECIPIENT_TRAVEL_MIN + RECIPIENT_CALL_BUFFER_MIN and key not in self.announced:
            self.announced.add(key)
            dest = sc.PLACES[self.destination]
            self.event("or", f"[DEMO] Call the recipient now: about {RECIPIENT_TRAVEL_MIN} min to reach {dest['short']}",
                       say=f"Call the recipient now. They need about {RECIPIENT_TRAVEL_MIN} minutes to reach {dest['short']}.",
                       audience=["or", "map"], why="simulated recipient travel time", who=DEMO_WHO)

    # ----------------------------------------------------- controls (demo panel)
    def add_delay(self, minutes: float, label: str):
        if self.delivered:
            return
        target = self.leg_idx
        flights = [i for i, l in enumerate(self.legs) if l["mode"] in ("flight", "charter") and i > self.leg_idx]
        if self.leg["mode"] not in ("flight", "charter") and flights:
            target = max(self.leg_idx, flights[0] - 1)
        self.legs[target]["duration"] += minutes
        self.legs[target]["delay"] += minutes
        self.last_trouble = f"{label}, adding {say_hm(minutes)}"
        self.event("delay", f"{label}: +{fmt_hm(minutes)} on '{self.legs[target]['name']}'",
                   audience=["map", "control", "courier"], vibrate=[400, 150, 400],
                   why=label, who="control panel")
        self._recompute_status()

    def _airport_key(self, pt) -> str | None:
        for k, v in sc.PLACES.items():
            if v["kind"] == "airport" and haversine_km(pt, sc.p(k)) < 3:
                return k
        return None

    def apply_feed_delay(self, source: str, airport: str, minutes: float, label: str):
        """Set the delay a live feed implies for `airport` on the next flight that touches it.

        Idempotent: `source` remembers what was already applied, so repeated polls only add the change.
        """
        if self.delivered:
            return
        idx = next((i for i in range(self.leg_idx, len(self.legs))
                    if self.legs[i]["mode"] in ("flight", "charter")
                    and airport in (self._airport_key(self.legs[i]["start"]), self._airport_key(self.legs[i]["end"]))), None)
        self._apply_delay_at(source, idx, minutes, label, "live feed")

    def _apply_delay_at(self, source: str, idx: int | None, minutes: float, label: str, who: str):
        """Set the delay `source` implies on leg `idx`. Idempotent: only the change since last call is applied."""
        if self.delivered:
            return
        prev_idx, prev_min = self.feed_applied.get(source, (idx, 0.0))
        if idx is None:
            idx = prev_idx
        if idx is None or idx >= len(self.legs) or idx < self.leg_idx:
            self.feed_applied.pop(source, None)
            return
        delta = minutes - prev_min
        if abs(delta) < 1:
            return
        leg = self.legs[idx]
        leg["duration"] = max(1.0, leg["duration"] + delta)
        leg["delay"] = max(0.0, leg.get("delay", 0) + delta)
        self.feed_applied[source] = (idx, minutes)
        if delta > 0:
            self.last_trouble = f"{label}, adding {say_hm(delta)}"
            self.event("delay", f"{label}: +{fmt_hm(delta)} on '{leg['name']}' ({who})",
                       audience=["map", "control", "courier"], vibrate=[400, 150, 400], why=label, who=who)
        else:
            self.event("delay", f"{label}: delay eased by {fmt_hm(-delta)} ({who})", audience=["map", "control"],
                       why=label, who=who)
        self._recompute_status()

    def set_temp(self, value: float):
        self.temp_base = value
        self.temp = value
        self._recompute_status()

    def shock(self, g: float = 3.0, source="sim"):
        self.last_shock_sim = self.sim_min
        self.event("shock", f"Shock detected ({g:.1f} g, {source}). Packaging check requested at next handoff.",
                   say="Shock detected. Please check the packaging.", audience=["box", "map", "control", "courier"],
                   vibrate=[200, 100, 200, 100, 200], why=f"{g:.1f} g reading", who=source)
        self._recompute_status()

    # ------------------------------------------------------------ escalation
    def _charter_airports(self, strict=True):
        airports = [k for k, v in sc.PLACES.items() if v["kind"] == "airport" and v.get("charter")]
        d = self._demo()
        if not d:
            return airports
        free = [k for k in airports if d.charter_ok(k)]
        return free if strict else (free or airports)

    def _nearest_charter_airport(self, pt, strict=False):
        return min(self._charter_airports(strict), key=lambda k: haversine_km(pt, sc.p(k)))

    def _charter_callout(self, airport_key):
        d = self._demo()
        return d.charter_callout(airport_key) if d else 45

    def _charter_legs(self, pt, dest_key):
        origin_ap = self._nearest_charter_airport(pt, strict=True) if self._charter_airports() else self._nearest_charter_airport(pt)
        dest_ap = self._nearest_charter_airport(sc.p(dest_key))
        cust = self.leg["custodian"]
        legs = [dict(name="Charter call-out (crew + aircraft)", mode="stationary", start=pt, end=pt,
                     duration=self._charter_callout(origin_ap), custodian=cust)]
        if haversine_km(pt, sc.p(origin_ap)) > 3:
            legs.append(dict(name=f"Courier to {sc.PLACES[origin_ap]['short']}", mode="ground", start=pt,
                             end=sc.p(origin_ap), duration=ground_minutes(pt, sc.p(origin_ap)),
                             custodian=sc.COURIER_A))
        legs.append(dict(name=f"Charter {sc.PLACES[origin_ap]['short']} to {sc.PLACES[dest_ap]['short']}",
                         mode="charter", start=sc.p(origin_ap), end=sc.p(dest_ap),
                         duration=flight_minutes(sc.p(origin_ap), sc.p(dest_ap)),
                         custodian=sc.CHARTER_CREW))
        legs.append(dict(name=f"Courier to {sc.PLACES[dest_key]['short']}", mode="ground", start=sc.p(dest_ap),
                         end=sc.p(dest_key), duration=ground_minutes(sc.p(dest_ap), sc.p(dest_key)),
                         custodian=sc.COURIER_B))
        return legs

    def compute_options(self):
        pt = self.position()
        remaining = self.remaining()
        dest = sc.PLACES[self.destination]
        opts = []

        def mk(key, label, legs, dest_key, summary):
            eta = sum(l["duration"] for l in legs)
            margin = remaining - eta
            return dict(key=key, label=label, eta_min=round(eta), margin_min=round(margin),
                        destination=dest_key, summary=summary, legs=[dict(l, delay=0.0) for l in legs])

        in_air = self.leg["mode"] in ("flight", "charter")
        # hold = keep the current plan
        hold_legs = deepcopy(self.legs[self.leg_idx:])
        hold_legs[0]["duration"] -= self.leg_elapsed
        opts.append(mk("hold", f"Hold: keep current plan to {dest['short']}", hold_legs, self.destination,
                       f"Stay on the current plan to {dest['short']}"))
        if not in_air and self._charter_airports():
            c = mk("charter", f"Charter jet to {dest['short']}", self._charter_legs(pt, self.destination),
                   self.destination, f"Book a charter, same recipient at {dest['short']}")
            opts.append(c)
            d = self._demo()
            alts = [k for k, v in sc.PLACES.items() if v["kind"] == "center" and k != self.destination
                    and (not d or d.theatre_ok(k))]
            best = None
            for k in alts:
                legs = [dict(name=f"Courier to {sc.PLACES[k]['short']}", mode="ground", start=pt, end=sc.p(k),
                             duration=ground_minutes(pt, sc.p(k)), custodian=sc.COURIER_A)]
                o = mk("reroute", f"Reroute to {sc.PLACES[k]['short']}", legs, k,
                       f"Drive to the next matched recipient at {sc.PLACES[k]['short']}; "
                       f"the {dest['short']} patient would not receive this organ")
                if best is None or o["margin_min"] > best["margin_min"]:
                    best = o
            if best:
                opts.append(best)
        return opts

    def open_escalation(self, reason: str, manual=False):
        opts = self.compute_options()
        self.escalation = dict(
            id=f"E{self.plan_version}-{int(time.time())}", opened_clock=self.clock(), reason=reason,
            options=opts, script=self.template_script(opts, reason), call=None, decided=None, manual=manual,
        )
        self.escalated_versions.add(self.plan_version)
        self.needs_escalation = False
        best = max(opts, key=lambda o: o["margin_min"])
        self.critical = best["margin_min"] < 0
        self.critical_best_eta = best["eta_min"] if self.critical else None
        self.escalation["critical"] = self.critical
        self._recompute_status()
        if self.running and self.speed > ESCALATION_SPEED:
            self.speed_before_escalation = self.speed
            self.speed = ESCALATION_SPEED
        self.event("escalation", f"Escalating to coordinator: {reason}",
                   audience=["coordinator", "map", "control", "box"], why=reason, who="risk engine")
        return self.escalation

    def template_script(self, opts, reason):
        dest = sc.PLACES[self.destination]
        by = {o["key"]: o for o in opts}
        hold = by["hold"]
        crit = all(o["margin_min"] < 0 for o in opts)
        parts = ["Critical. No route fits inside the planned window. The best available option arrives "
                 f"{say_hm(max(o['margin_min'] for o in opts))} past it."] if crit else []
        parts += [f"Organ relay alert for {self.organ_label().lower()} {self.shipment_id.replace('-', ' ')}, "
                 f"headed to {dest['short']}. {reason}. "
                 f"On the current plan it arrives with {say_hm(hold['margin_min'])} "
                 f"{'to spare' if hold['margin_min'] >= 0 else 'past its safe window'}."]
        choices = []
        if "charter" in by:
            parts.append(f"A charter keeps the same recipient and arrives with {say_hm(by['charter']['margin_min'])} to spare.")
            choices.append("charter")
        if "reroute" in by:
            r = by["reroute"]
            parts.append(f"Or reroute to {sc.PLACES[r['destination']]['short']} with {say_hm(r['margin_min'])} to spare.")
            choices.append("reroute")
        choices.append("hold")
        parts.append("Say " + ", ".join(choices[:-1]) + f", or {choices[-1]}.")
        return " ".join(parts)

    def decide(self, key: str, source: str, utterance: str | None = None):
        if not self.escalation or self.escalation.get("decided"):
            return False, "No open escalation"
        # Recompute so the plan reflects where the box is *now*.
        fresh = {o["key"]: o for o in self.compute_options()}
        if key not in fresh:
            return False, f"Option '{key}' not available right now"
        opt = fresh[key]
        old_dest = self.destination
        self.critical = opt["margin_min"] < 0
        self.critical_best_eta = opt["eta_min"] if self.critical else None
        why = f"{opt['summary']}; ETA {fmt_hm(opt['eta_min'])}, margin {fmt_hm(opt['margin_min'])}"
        self.escalation["decided"] = dict(key=key, source=source, utterance=utterance, clock=self.clock())
        if key != "hold":
            prev_custodian = self.leg["custodian"]
            self.legs = opt["legs"]
            self.feed_applied = {}
            self.leg_idx = 0
            self.leg_elapsed = 0.0
            self.destination = opt["destination"]
            self.plan_version += 1
            self.last_trouble = None
            if self.leg["custodian"] != prev_custodian:
                self._log_custody(self.leg["custodian"], f"Reassigned by coordinator decision ({key})")
        if self.speed_before_escalation:
            self.speed = self.speed_before_escalation
            self.speed_before_escalation = None
        new_dest = sc.PLACES[self.destination]
        who = f"{source}" + (f' ("{utterance}")' if utterance else "")
        if key == "charter":
            self.event("decision", f"Coordinator chose CHARTER via {who}. New ETA {fmt_hm(opt['eta_min'])}.",
                       say=f"Charter confirmed. New arrival in {say_hm(opt['eta_min'])}.",
                       audience=["all"], vibrate=[100, 60, 100, 60, 100], why=why, who=who)
        elif key == "reroute":
            self.event("decision", f"Coordinator chose REROUTE to {new_dest['name']} via {who}.",
                       say=f"Rerouting to {new_dest['short']}. Arrival in {say_hm(opt['eta_min'])}.",
                       audience=["all"], vibrate=[100, 60, 100, 60, 100], why=why, who=who)
            self.event("or", f"{sc.PLACES[old_dest]['short']}: organ reallocated, stand down OR",
                       say=f"Attention {sc.PLACES[old_dest]['short']}. The incoming {self.organ_label().lower()} "
                           f"has been reallocated. Please stand down.", audience=["or"])
            self.event("or", f"{new_dest['short']}: organ inbound, ETA {fmt_hm(opt['eta_min'])}",
                       say=f"Attention {new_dest['short']}. A {self.organ_label().lower()} is inbound in "
                           f"{say_hm(opt['eta_min'])}.", audience=["or"])
        else:
            self.event("decision", f"Coordinator chose HOLD via {who}.", say="Holding current plan.",
                       audience=["all"], why=why, who=who)
        self._recompute_status()
        self.escalation_closed_at = time.time()
        return True, key

    # -------------------------------------------------------------- telemetry
    def device_report(self, data: dict):
        self.device = dict(data, ts=time.time())

    def device_age(self) -> float | None:
        return None if not self.device else time.time() - self.device["ts"]

    def check_box(self):
        """Flag the box offline once it has reported before and then gone quiet."""
        age = self.device_age()
        if age is None:
            return
        offline = age > BOX_OFFLINE_SEC
        if offline == self.box_offline:
            return
        self.box_offline = offline
        if offline:
            self.event("alert", f"Box offline: no report for {int(age // 60)} min. Showing last known location.",
                       say="Box offline. No signal from the cooler.", audience=["coordinator", "map", "control", "courier"],
                       vibrate=[600], why=f"no telemetry for over {int(BOX_OFFLINE_SEC // 60)} min", who="monitor")
        else:
            self.event("box", "Box back online.", audience=["map", "control"], why="telemetry resumed", who="monitor")
        self._recompute_status()

    # --------------------------------------------------------------- snapshot
    def snapshot(self, integrations: dict, feeds: dict | None = None):
        dest = sc.PLACES[self.destination]
        lat, lng = self.position()
        legs = []
        for i, l in enumerate(self.legs):
            legs.append(dict(name=l["name"], mode=l["mode"], start=list(l["start"]), end=list(l["end"]),
                             duration=round(l["duration"]), delay=round(l.get("delay", 0)),
                             custodian=l["custodian"], done=i < self.leg_idx or self.delivered,
                             current=i == self.leg_idx and not self.delivered))
        esc = None
        if self.escalation:
            esc = {k: v for k, v in self.escalation.items() if k != "options"}
            esc["options"] = [{k: v for k, v in o.items() if k != "legs"} for o in self.escalation["options"]]
        return dict(
            shipment_id=self.shipment_id, organ=self.organ_key, organ_label=self.organ_label(),
            clock=self.clock(), sim_min=round(self.sim_min, 1), speed=self.speed, running=self.running,
            delivered=self.delivered, status=self.status, reasons=self.reasons,
            temp_c=round(self.temp, 1), window_min=self.window_min, elapsed_min=round(self.elapsed()),
            remaining_min=round(self.remaining()), eta_min=round(self.eta()), margin_min=round(self.margin()),
            eta_clock=self.clock(self.sim_min + self.eta()),
            position=dict(lat=lat, lng=lng),
            leg=dict(name=self.leg["name"], mode=self.leg["mode"], custodian=self.leg["custodian"],
                     progress=0 if self.leg["duration"] == 0 else min(1, self.leg_elapsed / self.leg["duration"])),
            legs=legs, path=self.path, destination=dict(key=self.destination, **dest), places=sc.PLACES,
            custody=self.custody[-30:], events=self.events[-60:], escalation=esc, device=self.device,
            box=dict(reporting=self.device is not None, offline=self.box_offline,
                     age_s=None if self.device_age() is None else round(self.device_age())),
            critical=self.critical, demo=self.demo.snapshot(self) if self.demo else None,
            plan_version=self.plan_version, integrations=integrations, feeds=feeds,
            preservation=dict(key=self.preservation_key, label=sc.PRESERVATION[self.preservation_key]["label"]),
        )
