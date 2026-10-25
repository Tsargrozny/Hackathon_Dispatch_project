"""Live airport conditions: FAA NAS status and METAR weather (both free, no API key).

The feeds are always polled and shown on the control panel. They only change the shipment when
`apply` is on (env FEEDS_APPLY=1 or the control-panel toggle), so a real-world ground stop can't
surprise a demo. Delay minutes are planning heuristics, not predictions.
"""
from __future__ import annotations

import os
import re
import time
import xml.etree.ElementTree as ET

import httpx

from . import scenario as sc

FAA_URL = "https://nasstatus.faa.gov/api/airport-status-information"
METAR_URL = "https://aviationweather.gov/api/data/metar"
POLL_SEC = 120
STALE_SEC = 300  # data older than this is low confidence and is not acted on
TIMEOUT = 8

# Heuristic delay (minutes) added to the next flight touching the airport.
GROUND_STOP_MIN = 120
CLOSURE_MIN = 180
PROGRAM_CAP_MIN = 240
WEATHER_MIN = {"LIFR": 45, "IFR": 20}

# NOTAM closures that only restrict general aviation don't affect cargo or charter ops.
GA_ONLY = re.compile(r"\bGA\b|NON SKED|TRANSIENT", re.I)


def _minutes(text: str | None) -> float:
    m = re.search(r"(?:(\d+)\s*hours?)?\s*(?:and\s*)?(?:(\d+)\s*minutes?)?", text or "")
    return int(m.group(1) or 0) * 60 + int(m.group(2) or 0) if m else 0


def parse_faa(xml_text: str) -> dict[str, dict]:
    """IATA code -> {status, delay_min, note}. Worst condition per airport wins."""
    out: dict[str, dict] = {}

    def note(code, status, minutes, text):
        cur = out.get(code)
        if cur is None or minutes > cur["delay_min"]:
            out[code] = dict(status=status, delay_min=minutes, note=text)

    root = ET.fromstring(xml_text)
    for dt in root.iter("Delay_type"):
        kind = (dt.findtext("Name") or "").lower()
        for entry in dt.iter():
            code = entry.findtext("ARPT")
            if not code:
                continue
            reason = (entry.findtext("Reason") or "").strip()
            if "closure" in kind:
                if GA_ONLY.search(reason):
                    note(code, "normal", 0, "GA-only closure notice, no effect on cargo")
                else:
                    note(code, "closed", CLOSURE_MIN, "Airport closed")
            elif "ground stop" in kind:
                note(code, "ground_stop", GROUND_STOP_MIN, f"Ground stop: {reason}")
            else:
                mins = max((_minutes(e.text) for e in entry.iter() if e.tag in ("Avg", "Max", "Min")), default=0)
                mins = min(mins, PROGRAM_CAP_MIN)
                note(code, "delay", mins, f"Delays up to {int(mins)} min: {reason}")
    return out


def parse_metar(rows: list[dict]) -> dict[str, dict]:
    """ICAO code -> {cat, delay_min, raw}."""
    return {
        r["icaoId"]: dict(cat=r.get("fltCat") or "?", delay_min=WEATHER_MIN.get(r.get("fltCat"), 0),
                          raw=r.get("rawOb", ""))
        for r in rows if r.get("icaoId")
    }


class Feeds:
    def __init__(self):
        self.apply = os.getenv("FEEDS_APPLY", "").lower() in ("1", "true", "yes")
        self.updated: float | None = None
        self.updated_by = {"faa": None, "wx": None}
        self.polled = False
        self._stale_alerted: set[str] = set()
        self.faa_ok = False
        self.wx_ok = False
        self.error: str | None = None
        self.airports: dict[str, dict] = {}

    async def poll(self):
        airports = {k: v for k, v in sc.PLACES.items() if v["kind"] == "airport"}
        errors = []
        faa: dict[str, dict] = {}
        wx: dict[str, dict] = {}
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            try:
                r = await client.get(FAA_URL)
                r.raise_for_status()
                faa = parse_faa(r.text)
                self.faa_ok = True
                self.updated_by["faa"] = time.time()
            except Exception as e:  # network or parse failure: keep last known state
                self.faa_ok = False
                errors.append(f"FAA: {type(e).__name__}")
            try:
                ids = ",".join(v["icao"] for v in airports.values())
                r = await client.get(METAR_URL, params={"ids": ids, "format": "json"})
                r.raise_for_status()
                wx = parse_metar(r.json())
                self.wx_ok = True
                self.updated_by["wx"] = time.time()
            except Exception as e:
                self.wx_ok = False
                errors.append(f"METAR: {type(e).__name__}")
        self.error = "; ".join(errors) or None
        self.polled = True
        if self.faa_ok or self.wx_ok:
            self.updated = time.time()
        for key, place in airports.items():
            cur = self.airports.get(key, {})
            self.airports[key] = dict(
                name=place["short"],
                faa=faa.get(key.upper(), dict(status="normal", delay_min=0, note="")) if self.faa_ok else cur.get("faa"),
                wx=wx.get(place["icao"]) if self.wx_ok else cur.get("wx"),
            )

    def age(self, src: str) -> float | None:
        t = self.updated_by[src]
        return None if t is None else time.time() - t

    def stale(self, src: str) -> bool:
        age = self.age(src)
        return age is None or age > STALE_SEC

    def confidence(self, src: str) -> str:
        return "none" if self.updated_by[src] is None else ("low" if self.stale(src) else "ok")

    def _check_stale(self, engine):
        """Tell the coordinator once when a feed that was working goes stale, and again when it recovers."""
        for src, name in (("faa", "FAA status"), ("wx", "Weather")):
            if not self.polled or self.updated_by[src] is None:
                continue
            if self.stale(src) and src not in self._stale_alerted:
                self._stale_alerted.add(src)
                engine.event("alert", f"{name} data is {int(self.age(src) // 60)} min old: confidence low, "
                                      "live delays paused. Check conditions manually.",
                             audience=["coordinator", "map", "control"], why="feed older than 5 min", who="monitor")
            elif not self.stale(src) and src in self._stale_alerted:
                self._stale_alerted.discard(src)
                engine.event("feed", f"{name} data is fresh again.", audience=["map", "control"],
                             why="feed recovered", who="monitor")

    def apply_to(self, engine):
        """Push fresh feed readings into the engine (no-op unless `apply` is on). Stale data is never acted on."""
        self._check_stale(engine)
        if not self.apply:
            return
        for key, a in self.airports.items():
            faa, wx = a.get("faa"), a.get("wx")
            if not self.stale("faa") and faa:
                engine.apply_feed_delay(f"faa:{key}", key, faa["delay_min"], f"FAA {key.upper()}: {faa['note'] or 'normal'}")
            if not self.stale("wx") and wx:
                engine.apply_feed_delay(f"wx:{key}", key, wx["delay_min"], f"{key.upper()} weather {wx['cat']}")

    def snapshot(self):
        src = {s: dict(age_s=None if self.age(s) is None else round(self.age(s)), confidence=self.confidence(s))
               for s in ("faa", "wx")}
        return dict(apply=self.apply, updated=self.updated, faa_ok=self.faa_ok, wx_ok=self.wx_ok,
                    error=self.error, sources=src, airports=self.airports)
