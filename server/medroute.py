from __future__ import annotations

import asyncio
import csv
import io
import json
import re
import time
from collections import deque
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse, Response

from . import keystore, net
from . import scenario as sc
from .engine import say_hm

PAGES = Path(__file__).resolve().parent.parent / "static" / "medroute"
LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}
DEFAULT_DEST = "cedars"
CALL_AUDIENCE = ["map", "control"]
NOISY_TYPES = {"speech-update", "model-output", "voice-input", "user-interrupted", "assistant.started"}
STOPWORDS = {"medical", "center", "hospital", "health", "of", "the", "uc", "la", "intl"}

router = APIRouter()
_recent_lines: deque[str] = deque(maxlen=30)


def is_local(req: Request) -> bool:
    if any(h in req.headers for h in ("x-forwarded-for", "x-forwarded-host", "x-real-ip")):
        return False
    return bool(req.client) and req.client.host in LOCAL_HOSTS


def local_only():
    return JSONResponse({"ok": False, "error": "Settings can only be changed from this laptop (http://localhost:8000/settings)."}, 403)


@router.get("/settings")
async def settings_page(req: Request):
    if not is_local(req):
        return local_only()
    return FileResponse(PAGES / "settings.html")


@router.get("/api/settings")
async def settings_get():
    return {"fields": keystore.public_fields(), "keys": keystore.status(), "env_file": keystore.ENV_PATH.exists()}


@router.post("/api/settings")
async def settings_post(req: Request):
    if not is_local(req):
        return local_only()
    body = await req.json()
    changed = keystore.save(body.get("values") or {}, body.get("clear") or [])
    return {"ok": True, "changed": changed, "keys": keystore.status()}


def _app():
    from . import main
    return main.engine, main.broadcast


def _clean(text, limit: int = 300) -> str:
    return re.sub(r"[<>]", "", re.sub(r"<[^>]*>", "", str(text or ""))).strip()[:limit]


def _tokens(text: str) -> set[str]:
    return set(re.sub(r"[^a-z0-9 ]", " ", (text or "").lower()).split()) - STOPWORDS


def _organ_key(text) -> str | None:
    t = str(text or "").lower()
    for key, cfg in sc.ORGANS.items():
        if key in t or cfg["label"].lower() in t:
            return key
    return None


def _match_center(text) -> str | None:
    t = str(text or "").lower()
    squashed = re.sub(r"[^a-z0-9]", "", t)
    words = _tokens(t)
    best, best_score = None, 0
    for key, p in sc.PLACES.items():
        if p["kind"] != "center":
            continue
        score = len(words & _tokens(f"{p['name']} {p['short']}"))
        if key in squashed:
            score += 2
        if score > best_score:
            best, best_score = key, score
    return best


def _number(value) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    m = re.search(r"\d+(\.\d+)?", str(value or ""))
    if not m:
        return None
    n = float(m.group())
    return n * 60 if "hour" in str(value).lower() else n


def _args(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        out = json.loads(raw or "{}")
        return out if isinstance(out, dict) else {}
    except Exception:
        return {}


async def plan_organ_transport(args: dict) -> str:
    engine, broadcast = _app()
    organ = _organ_key(args.get("organ"))
    organ_note = ""
    if not organ:
        organ = "kidney"
        organ_note = f" (\"{_clean(args.get('organ'), 40)}\" is not in the scenario, planned as kidney)" if args.get("organ") else ""
    dest = _match_center(args.get("to_hospital")) or DEFAULT_DEST
    minutes = _number(args.get("minutes_since_recovery"))

    engine.reset(organ)
    if dest != engine.destination:
        engine.destination = dest
        charter = [l for l in engine._charter_legs(sc.p("donor"), dest) if l["mode"] != "stationary"]
        engine.legs = [dict(l, delay=0.0) for l in [engine.legs[0]] + charter]
        engine.path = []
        engine._record_path(force=True)
    if minutes is not None:
        engine.elapsed0 = max(0.0, min(minutes, engine.window_min))
    engine.running = True
    engine._recompute_status()

    place = sc.PLACES[dest]
    origin = _clean(args.get("from_hospital"), 80) or sc.PLACES["donor"]["name"]
    engine.event("call", f"Phone dispatch (Vapi): {engine.organ_label()} from {origin} to {place['name']}, "
                         f"{engine.elapsed():.0f} min since recovery{organ_note}. Playback started.", audience=["all"])
    await broadcast()

    eta, margin = engine.eta(), engine.margin()
    spare = "to spare" if margin >= 0 else "past its safe window"
    return (f"The {engine.organ_label().lower()} is headed to {place['name']} with an ETA of {say_hm(eta)}, "
            f"arriving with {say_hm(margin)} {spare}.")


TOOLS = {"plan_organ_transport": plan_organ_transport}


async def run_tool(name: str, args: dict) -> str:
    fn = TOOLS.get(name)
    if not fn:
        return f"Unknown tool {name}."
    try:
        return await fn(args)
    except Exception as e:
        print(f"[vapi] tool {name} failed: {e!r}")
        return "Sorry, I could not plan that transport right now. Please try again."


def _caller(msg: dict) -> str:
    customer = (msg.get("call") or {}).get("customer") or msg.get("customer") or {}
    number = str(customer.get("number") or "")
    return f" from caller ending {number[-4:]}" if len(number) >= 4 else ""


def _line(text: str) -> str | None:
    text = _clean(text)
    if not text or text in _recent_lines:
        return None
    _recent_lines.append(text)
    return text


def _describe(kind: str, msg: dict) -> str | None:
    if kind == "status-update":
        status = str(msg.get("status") or "unknown")
        if status == "in-progress":
            return f"Phone line: call connected{_caller(msg)}"
        if status == "ended":
            return f"Phone line: call ended ({_clean(msg.get('endedReason') or 'no reason', 60)})"
        return f"Phone line: call {status}"
    if kind.startswith("transcript"):
        if msg.get("transcriptType", "final") != "final" and "final" not in kind:
            return None
        who = "Caller" if msg.get("role") == "user" else "Dispatcher"
        return _line(f"{who}: {msg.get('transcript', '')}")
    if kind == "conversation-update":
        spoken = [m for m in (msg.get("messages") or msg.get("conversation") or [])
                  if isinstance(m, dict) and m.get("role") in ("user", "assistant", "bot") and (m.get("message") or m.get("content"))]
        if not spoken:
            return None
        last = spoken[-1]
        who = "Caller" if last.get("role") == "user" else "Dispatcher"
        return _line(f"{who}: {last.get('message') or last.get('content')}")
    if kind == "end-of-call-report":
        summary = msg.get("summary") or (msg.get("analysis") or {}).get("summary") or ""
        reason = _clean(msg.get("endedReason") or "", 60)
        text = "Call report" + (f" ({reason})" if reason else "")
        return text + (f": {_clean(summary, 240)}" if summary else ": no summary")
    if kind in NOISY_TYPES or not kind:
        return None
    return f"Vapi event: {_clean(kind, 60)}"


@router.post("/vapi")
async def vapi(req: Request):
    try:
        body = await req.json()
    except Exception:
        return {"ok": True}
    msg = body.get("message") if isinstance(body, dict) and isinstance(body.get("message"), dict) else body
    if not isinstance(msg, dict):
        return {"ok": True}
    kind = str(msg.get("type") or "")
    try:
        if kind == "tool-calls":
            results = []
            for call in msg.get("toolCallList") or msg.get("toolCalls") or []:
                fn = call.get("function") or {}
                name = fn.get("name") or call.get("name") or ""
                result = await run_tool(name, _args(fn.get("arguments", call.get("arguments"))))
                results.append({"toolCallId": call.get("id"), "name": name, "result": result})
            return {"results": results}
        if kind == "function-call":
            fc = msg.get("functionCall") or {}
            return {"result": await run_tool(fc.get("name") or "", _args(fc.get("parameters")))}
        text = _describe(kind, msg)
        if text:
            engine, broadcast = _app()
            engine.event("call", text, audience=CALL_AUDIENCE)
            await broadcast()
    except Exception as e:
        print(f"[vapi] could not handle {kind}: {e!r}")
    return {"ok": True}


LAYER_TTL = 60
FLIGHT_RADIUS_NM = 81
FLIGHT_CENTERS = ["rno", "smf", "lax"]
FIRE_BBOX = "-123,32,-114,40.5"
FIRE_SOURCES = ["VIIRS_SNPP_NRT", "VIIRS_NOAA20_NRT"]
TOMTOM_TILE = "https://api.tomtom.com/traffic/map/4/tile/flow/relative0-dark/{z}/{x}/{y}.png"
TILE_CACHE_MAX = 400

_layer_cache: dict[str, tuple[float, object]] = {}
_tile_cache: dict[str, tuple[float, bytes]] = {}


async def _cached(key: str, fetch, empty):
    hit = _layer_cache.get(key)
    if hit and time.time() - hit[0] < LAYER_TTL:
        return hit[1]
    try:
        value = await fetch()
    except Exception as e:
        print(f"[layers] {key} failed: {e!r}")
        value = hit[1] if hit else empty
    _layer_cache[key] = (time.time(), value)
    return value


async def _adsb(lat: float, lng: float) -> list[dict]:
    r = await net.client().get(f"https://api.adsb.lol/v2/point/{lat}/{lng}/{FLIGHT_RADIUS_NM}", timeout=15)
    r.raise_for_status()
    return r.json().get("ac") or []


async def _fetch_flights() -> list[dict]:
    rows = await asyncio.gather(*[_adsb(*sc.p(k)) for k in FLIGHT_CENTERS], return_exceptions=True)
    seen: dict[str, dict] = {}
    for batch in rows:
        if isinstance(batch, Exception):
            print(f"[layers] adsb.lol region failed: {batch!r}")
            continue
        for a in batch:
            if a.get("lat") is None or a.get("lon") is None or a.get("alt_baro") == "ground":
                continue
            seen[a.get("hex")] = {
                "id": a.get("hex"), "flight": (a.get("flight") or a.get("r") or a.get("hex") or "").strip(),
                "lat": a["lat"], "lng": a["lon"], "track": a.get("track") or a.get("true_heading") or 0,
                "alt": a.get("alt_baro"), "gs": a.get("gs"), "type": a.get("t"),
            }
    if not seen and all(isinstance(b, Exception) for b in rows):
        raise RuntimeError("all adsb.lol regions failed")
    return list(seen.values())


async def _fetch_fires() -> list[dict]:
    key = keystore.get("FIRMS_MAP_KEY")
    if not key:
        return []
    base = "https://firms.modaps.eosdis.nasa.gov/api/area/csv/{key}/{src}/{bbox}/1"
    responses = await asyncio.gather(*[net.client().get(base.format(key=key, src=s, bbox=FIRE_BBOX), timeout=20) for s in FIRE_SOURCES],
                                     return_exceptions=True)
    fires, ok = [], False
    for r in responses:
        if isinstance(r, Exception) or r.status_code != 200 or not r.text.startswith("latitude"):
            continue
        ok = True
        for row in csv.DictReader(io.StringIO(r.text)):
            try:
                fires.append({"lat": float(row["latitude"]), "lng": float(row["longitude"]), "frp": float(row.get("frp") or 0),
                              "confidence": row.get("confidence"), "acq": f"{row.get('acq_date')} {row.get('acq_time')}",
                              "satellite": row.get("satellite")})
            except (KeyError, ValueError):
                continue
    keystore.report("FIRMS_MAP_KEY", ok, f"{len(fires)} detections in the last 24 h" if ok else "FIRMS rejected the key or is unreachable")
    if not ok:
        raise RuntimeError("FIRMS returned no usable data")
    return fires


@router.get("/api/medroute/layers")
async def layers():
    return {
        "flights": {"available": True, "source": "adsb.lol"},
        "traffic": {"available": keystore.has("TOMTOM_API_KEY"), "source": "TomTom traffic flow"},
        "fires": {"available": keystore.has("FIRMS_MAP_KEY"), "source": "NASA FIRMS VIIRS, last 24 h"},
    }


@router.get("/api/medroute/flights")
async def flights():
    items = await _cached("flights", _fetch_flights, [])
    return {"source": "adsb.lol", "count": len(items), "flights": items}


@router.get("/api/medroute/fires")
async def fires():
    if not keystore.has("FIRMS_MAP_KEY"):
        return {"source": "NASA FIRMS", "available": False, "count": 0, "fires": []}
    items = await _cached("fires", _fetch_fires, [])
    return {"source": "NASA FIRMS", "available": True, "count": len(items), "fires": items}


@router.get("/api/medroute/traffic/{z}/{x}/{y}.png")
async def traffic_tile(z: int, x: int, y: int):
    key = keystore.get("TOMTOM_API_KEY")
    if not key or not (0 <= z <= 22):
        return Response(status_code=404)
    tile_key = f"{z}/{x}/{y}"
    hit = _tile_cache.get(tile_key)
    if hit and time.time() - hit[0] < LAYER_TTL:
        return Response(hit[1], media_type="image/png")
    try:
        r = await net.client().get(TOMTOM_TILE.format(z=z, x=x, y=y), params={"key": key, "tileSize": 256}, timeout=8)
        r.raise_for_status()
    except Exception as e:
        keystore.report("TOMTOM_API_KEY", False, f"Traffic tiles failed: {type(e).__name__}")
        return Response(status_code=204)
    keystore.report("TOMTOM_API_KEY", True, "Traffic tiles loading")
    if len(_tile_cache) >= TILE_CACHE_MAX:
        _tile_cache.pop(next(iter(_tile_cache)))
    _tile_cache[tile_key] = (time.time(), r.content)
    return Response(r.content, media_type="image/png")
