from __future__ import annotations

import os
import time
from pathlib import Path

from dotenv import load_dotenv, set_key, unset_key

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"

DEFAULT_ASSISTANT_ID = "779b0be3-e9ce-48f6-a130-1498576f2888"

FIELDS = [
    {
        "key": "TOMTOM_API_KEY",
        "label": "TomTom API key",
        "adds": "Live traffic flow overlay on the globe and traffic-aware drive times (TomTom Routing, traffic=true).",
        "fallback": "OSRM public router plus a 25% traffic buffer",
        "fallback_kind": "simulated",
        "link": "https://developer.tomtom.com/user/register",
    },
    {
        "key": "CESIUM_ION_TOKEN",
        "label": "Cesium ion access token",
        "adds": "High-resolution satellite imagery on the 3D globe.",
        "fallback": "OpenStreetMap imagery, no token needed",
        "fallback_kind": "not_set",
        "link": "https://ion.cesium.com/signup",
    },
    {
        "key": "FIRMS_MAP_KEY",
        "label": "NASA FIRMS map key",
        "adds": "Active wildfire detections (VIIRS, last 24 h) across Southern California, with an alert when a fire is within 5 km of a route.",
        "fallback": "Clearly labeled simulated fire points",
        "fallback_kind": "simulated",
        "link": "https://firms.modaps.eosdis.nasa.gov/api/map_key/",
    },
    {
        "key": "OPENSKY_CLIENT_ID",
        "label": "OpenSky client ID",
        "adds": "OpenSky Network as the live flight source (needs the client secret too).",
        "fallback": "adsb.lol live flights, no key needed",
        "fallback_kind": "not_set",
        "link": "https://opensky-network.org/my-opensky/account",
    },
    {
        "key": "OPENSKY_CLIENT_SECRET",
        "label": "OpenSky client secret",
        "adds": "Pairs with the client ID for OpenSky OAuth2 access.",
        "fallback": "adsb.lol live flights, no key needed",
        "fallback_kind": "not_set",
        "link": "https://opensky-network.org/my-opensky/account",
    },
    {
        "key": "VAPI_PUBLIC_KEY",
        "label": "Vapi public key",
        "adds": "The \"Talk to dispatcher\" button: talk to the voice agent from the dashboard in the browser.",
        "fallback": "Button hidden; the phone line still works",
        "fallback_kind": "not_set",
        "link": "https://dashboard.vapi.ai/org/api-keys",
    },
    {
        "key": "VAPI_ASSISTANT_ID",
        "label": "Vapi assistant ID",
        "adds": "Which Vapi assistant the browser button talks to.",
        "fallback": f"Using the default assistant {DEFAULT_ASSISTANT_ID}",
        "fallback_kind": "connected",
        "link": "https://dashboard.vapi.ai/assistants",
        "default": DEFAULT_ASSISTANT_ID,
    },
]

KEYS = [f["key"] for f in FIELDS]

health: dict[str, dict] = {}


def reload() -> None:
    for k in KEYS:
        os.environ.pop(k, None)
    load_dotenv(ENV_PATH, override=True)


def has(key: str) -> bool:
    return bool((os.getenv(key) or "").strip())


def get(key: str) -> str:
    if has(key):
        return os.environ[key].strip()
    field = next((f for f in FIELDS if f["key"] == key), None)
    return (field or {}).get("default", "")


def opensky_ready() -> bool:
    return has("OPENSKY_CLIENT_ID") and has("OPENSKY_CLIENT_SECRET")


def report(key: str, ok: bool, detail: str = "") -> None:
    health[key] = {"ok": ok, "detail": detail[:200], "ts": time.time()}


def save(values: dict, clear: list) -> list[str]:
    if not ENV_PATH.exists():
        ENV_PATH.write_text("", encoding="utf-8")
    changed = []
    for key, value in (values or {}).items():
        value = str(value or "").strip().replace("\n", "").replace("\r", "").replace('"', "")
        if key not in KEYS or not value:
            continue
        set_key(str(ENV_PATH), key, value, quote_mode="always")
        health.pop(key, None)
        changed.append(key)
    for key in clear or []:
        if key in KEYS:
            unset_key(str(ENV_PATH), key, quote_mode="always")
            health.pop(key, None)
            changed.append(key)
    reload()
    return changed


def status() -> dict:
    out = {}
    for f in FIELDS:
        key = f["key"]
        is_set = has(key)
        h = health.get(key)
        if is_set and h and not h["ok"]:
            badge, detail = "error", h["detail"]
        elif is_set:
            badge, detail = "connected", (h or {}).get("detail") or "Key saved on this laptop"
        else:
            badge, detail = f["fallback_kind"], f["fallback"]
        out[key] = {"set": is_set, "status": badge, "detail": detail}
    return out


def public_fields() -> list[dict]:
    return [{k: v for k, v in f.items() if k != "default"} for f in FIELDS]
