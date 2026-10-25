from __future__ import annotations

import os
import re
import time
from pathlib import Path

from dotenv import load_dotenv

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"

DEFAULT_ASSISTANT_ID = "779b0be3-e9ce-48f6-a130-1498576f2888"

FIELDS = [
    {
        "key": "TOMTOM_API_KEY",
        "label": "TomTom API key",
        "adds": "Live traffic flow tiles on the judges' map.",
        "fallback": "No traffic layer on the map",
        "fallback_kind": "not_set",
        "link": "https://developer.tomtom.com/user/register",
    },
    {
        "key": "CESIUM_ION_TOKEN",
        "label": "Cesium ion access token",
        "adds": "Stored for a future 3D globe view.",
        "fallback": "Not used yet",
        "fallback_kind": "not_set",
        "link": "https://ion.cesium.com/signup",
    },
    {
        "key": "FIRMS_MAP_KEY",
        "label": "NASA FIRMS map key",
        "adds": "Active wildfire detections (VIIRS, last 24 h) as red dots on the judges' map.",
        "fallback": "No fire layer on the map",
        "fallback_kind": "not_set",
        "link": "https://firms.modaps.eosdis.nasa.gov/api/map_key/",
    },
    {
        "key": "OPENSKY_CLIENT_ID",
        "label": "OpenSky client ID",
        "adds": "Stored for OpenSky as an alternate flight source.",
        "fallback": "Flights come from adsb.lol, no key needed",
        "fallback_kind": "not_set",
        "link": "https://opensky-network.org/my-opensky/account",
    },
    {
        "key": "OPENSKY_CLIENT_SECRET",
        "label": "OpenSky client secret",
        "adds": "Pairs with the OpenSky client ID.",
        "fallback": "Flights come from adsb.lol, no key needed",
        "fallback_kind": "not_set",
        "link": "https://opensky-network.org/my-opensky/account",
    },
    {
        "key": "VAPI_PUBLIC_KEY",
        "label": "Vapi public key",
        "adds": "Stored for a browser \"Talk to dispatcher\" button.",
        "fallback": "The phone line (858) 348-9340 works without it",
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
KEY_LINE = re.compile(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")
UNSAFE = re.compile(r"[\r\n\"]")

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


def report(key: str, ok: bool, detail: str = "") -> None:
    health[key] = {"ok": ok, "detail": detail[:200], "ts": time.time()}


def _write_env(updates: dict[str, str | None]) -> None:
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else []
    out, done = [], set()
    for line in lines:
        m = KEY_LINE.match(line)
        if m and m.group(1) in updates:
            key = m.group(1)
            if updates[key] is not None and key not in done:
                out.append(f'{key}="{updates[key]}"')
            done.add(key)
        else:
            out.append(line)
    out += [f'{k}="{v}"' for k, v in updates.items() if v is not None and k not in done]
    text = "\n".join(out).strip("\n") + "\n" if out else ""
    for attempt in range(6):
        try:
            with open(ENV_PATH, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
            return
        except PermissionError:
            if attempt == 5:
                raise
            time.sleep(0.25)


def save(values: dict, clear: list) -> list[str]:
    updates: dict[str, str | None] = {}
    for key, value in (values or {}).items():
        value = UNSAFE.sub("", str(value or "")).strip()
        if key in KEYS and value:
            updates[key] = value
    for key in clear or []:
        if key in KEYS:
            updates[key] = None
    if updates:
        _write_env(updates)
        for key in updates:
            health.pop(key, None)
    reload()
    return list(updates)


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
