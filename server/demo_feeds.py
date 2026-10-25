"""SIMULATED DEMO DATA. Not live. Not real. For demonstration only.

Static stand-ins for feeds that aren't integrated yet, so the demo can show how they would change a
shipment. Nothing here is fetched from anywhere:

  ADS-B flight track      -> a canned RNO-LAX flight path (waypoints are illustrative, not a real track)
  Airline schedule        -> planned times derived from the demo route
  Road data (Google etc.) -> a fixed traffic factor you can switch to "incident"
  Cargo office hours      -> an invented hours table
  Charter availability    -> an invented availability/call-out table
  Theatre availability    -> an invented ready/not-ready table
  Recipient travel time   -> a fixed number

Safeguards so it can't be mistaken for real data: everything is labelled in the snapshot and UI, delays it
causes are prefixed "[DEMO]", and the decision log records the actor as "demo feed, simulated".
Turn it off with DEMO_FEEDS=0 or the control-panel switch. Replace each table with a real feed when one exists.
"""
from __future__ import annotations

import os

from . import scenario as sc
from .geo import haversine_km

LABEL = "SIMULATED DEMO DATA, not live"
WHO = "demo feed, simulated"

# Illustrative path only. Not a real flight track.
REPLAY = {
    ("rno", "lax"): [(39.4991, -119.7681), (38.55, -119.40), (37.30, -118.85),
                     (36.00, -118.55), (34.90, -118.45), (33.9416, -118.4085)],
}
FLIGHT_CALLSIGN = "Flight 1186 (demo)"
CRUISE_FT = 33000

ROAD_INCIDENT_FACTOR = 1.6  # drive time multiplier while the incident is on

# Minutes of the day the office is open: (open, close). Invented.
CARGO_HOURS_NORMAL = {"rno": (0, 1440), "lax": (0, 1440)}
CARGO_HOURS_CLOSED_EARLY = {"lax": (7 * 60, 22 * 60)}  # LAX office shut until 07:00

CHARTER_CALLOUT_MIN = {"rno": 45, "smf": 45, "vny": 45}  # invented
RECIPIENT_TRAVEL_MIN = 40                                  # invented
RECIPIENT_CALL_BUFFER_MIN = 30                             # call them this long before they must leave

CONDITIONS = {
    "road_incident": "Road incident on the courier routes (+60% drive time)",
    "cargo_closed": "LAX cargo office closed until 07:00",
    "charter_down_rno": "No charter aircraft free at RNO",
    "or_down_cedars": "Cedars-Sinai OR not ready",
    "or_down_ucdavis": "UC Davis OR not ready",
}


def _hm(minute_of_day: int) -> str:
    return f"{minute_of_day // 60 % 24:02d}:{minute_of_day % 60:02d}"


class DemoFeeds:
    def __init__(self):
        self.enabled = os.getenv("DEMO_FEEDS", "1").lower() not in ("0", "false", "no")
        self.on = {k: False for k in CONDITIONS}

    # ------------------------------------------------------------ queries used by the engine
    def replay_position(self, a_key, b_key, t):
        path = REPLAY.get((a_key, b_key))
        if not path:
            return None
        t = max(0.0, min(1.0, t))
        segs = [haversine_km(path[i], path[i + 1]) for i in range(len(path) - 1)]
        target, acc = t * sum(segs), 0.0
        for i, s in enumerate(segs):
            if acc + s >= target or i == len(segs) - 1:
                f = 0 if s == 0 else (target - acc) / s
                a, b = path[i], path[i + 1]
                return (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f)
            acc += s

    def charter_ok(self, airport_key: str) -> bool:
        return not (airport_key == "rno" and self.on["charter_down_rno"])

    def charter_callout(self, airport_key: str) -> int:
        return CHARTER_CALLOUT_MIN.get(airport_key, 45)

    def theatre_ok(self, center_key: str) -> bool:
        return not self.on.get(f"or_down_{center_key}", False)

    def cargo_hours(self, airport_key: str) -> tuple[int, int]:
        table = dict(CARGO_HOURS_NORMAL)
        if self.on["cargo_closed"]:
            table.update(CARGO_HOURS_CLOSED_EARLY)
        return table.get(airport_key, (0, 1440))

    # --------------------------------------------------------------------- control
    def set_condition(self, name: str, value: bool, engine):
        if name in self.on:
            self.on[name] = bool(value)
            self.apply_to(engine)

    def set_enabled(self, value: bool, engine):
        self.enabled = bool(value)
        if not self.enabled:
            for source, (idx, _mins) in list(engine.feed_applied.items()):
                if source.startswith("demo:"):
                    engine._apply_delay_at(source, idx, 0, "[DEMO] simulated conditions switched off", WHO)
        else:
            self.apply_to(engine)

    def apply_to(self, engine):
        """Turn the current simulated conditions into delays on the shipment. Safe to call every tick."""
        if not self.enabled or engine.delivered:
            return
        factor = ROAD_INCIDENT_FACTOR if self.on["road_incident"] else 1.0
        for i in range(engine.leg_idx, len(engine.legs)):
            leg = engine.legs[i]
            if leg["mode"] == "ground":
                base = leg.setdefault("base", leg["duration"] - leg.get("delay", 0.0))
                engine._apply_delay_at(f"demo:road:{i}", i, base * (factor - 1), f"[DEMO] Road incident on '{leg['name']}'", WHO)
            elif leg["mode"] == "stationary" and leg["name"].startswith("Cargo recovery"):
                ap = engine._airport_key(leg["start"])
                if not ap:
                    continue
                arrive = engine.minute_of_day(engine.leg_start_sim(i))
                opens, closes = self.cargo_hours(ap)
                wait = 0 if opens <= arrive < closes else (opens - arrive) % 1440
                engine._apply_delay_at(f"demo:cargo:{i}", i, wait,
                                       f"[DEMO] {sc.PLACES[ap]['short']} cargo office closed on arrival", WHO)

    # -------------------------------------------------------------------- snapshot
    def _flight_reading(self, engine) -> str:
        leg = engine.leg
        if engine.delivered or leg["mode"] != "flight":
            return "No flight airborne"
        t = max(0.0, min(1.0, engine.leg_elapsed / leg["duration"])) if leg["duration"] else 1.0
        alt = int(CRUISE_FT * min(1.0, t / 0.2, (1 - t) / 0.2))
        kt = int(250 + 200 * min(1.0, alt / CRUISE_FT))
        return f"{FLIGHT_CALLSIGN}: {alt:,} ft, {kt} kt, {round(t * 100)}% of route"

    def _schedule_reading(self, engine) -> str:
        fl = next((i for i, leg in enumerate(engine.legs) if leg["mode"] == "flight"), None)
        if fl is None or engine.plan_version != 1:
            return "No scheduled flight on the current plan"
        dep0 = sum(engine.planned[:fl])
        arr0 = dep0 + engine.planned[fl]
        if fl < engine.leg_idx or engine.delivered:
            return f"{FLIGHT_CALLSIGN}: landed (scheduled {engine.clock(arr0)})"
        arr = engine.leg_start_sim(fl) + engine.legs[fl]["duration"]
        late = round(arr - arr0)
        status = "on time" if late < 5 else f"late by {late} min"
        return (f"{FLIGHT_CALLSIGN}: scheduled {engine.clock(dep0)} to {engine.clock(arr0)}, "
                f"estimated arrival {engine.clock(arr)} ({status})")

    def snapshot(self, engine) -> dict:
        items = []
        if self.enabled:
            hrs = {k: self.cargo_hours(k) for k in ("rno", "lax")}
            items = [
                dict(name="Flight track (ADS-B stand-in)", reading=self._flight_reading(engine)),
                dict(name="Airline schedule", reading=self._schedule_reading(engine)),
                dict(name="Road data", reading=f"Traffic x{ROAD_INCIDENT_FACTOR if self.on['road_incident'] else 1.0:g}"),
                dict(name="Cargo office hours", reading=", ".join(
                    f"{sc.PLACES[k]['short']} {'24h' if h == (0, 1440) else _hm(h[0]) + '-' + _hm(h[1])}" for k, h in hrs.items())),
                dict(name="Charter availability", reading=", ".join(
                    f"{sc.PLACES[k]['short']} {'free, ' + str(CHARTER_CALLOUT_MIN[k]) + ' min call-out' if self.charter_ok(k) else 'none free'}"
                    for k in CHARTER_CALLOUT_MIN)),
                dict(name="Theatre availability", reading=", ".join(
                    f"{sc.PLACES[k]['short']} {'ready' if self.theatre_ok(k) else 'NOT ready'}"
                    for k, v in sc.PLACES.items() if v["kind"] == "center")),
                dict(name="Recipient travel time", reading=f"{RECIPIENT_TRAVEL_MIN} min to reach the hospital"),
            ]
        return dict(enabled=self.enabled, label=LABEL,
                    conditions={k: dict(label=v, on=self.on[k]) for k, v in CONDITIONS.items()}, items=items)
