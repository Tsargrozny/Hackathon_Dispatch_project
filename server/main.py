"""Organ Relay server: run with `uvicorn server.main:app --host 0.0.0.0 --port 8000`."""
from __future__ import annotations

import asyncio
import json
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

load_dotenv()

from . import voice  # noqa: E402  (env must be loaded first)
from .auth import BasicAuth  # noqa: E402
from .demo_feeds import DemoFeeds  # noqa: E402
from .engine import BOX_OFFLINE_SEC, Engine  # noqa: E402
from .feeds import POLL_SEC, Feeds  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TICK_SEC = 0.5

engine = Engine()
engine.demo = DemoFeeds()  # SIMULATED stand-ins for feeds that aren't integrated; see demo_feeds.py
feeds = Feeds()
clients: set[WebSocket] = set()


def integrations():
    return {"claude": voice.claude_enabled(), "twilio": voice.twilio_enabled()}


async def broadcast():
    if not clients:
        return
    msg = json.dumps(engine.snapshot(integrations(), feeds.snapshot()))
    dead = []
    for ws in list(clients):
        try:
            await ws.send_text(msg)
        except Exception:
            dead.append(ws)
    for ws in dead:
        clients.discard(ws)


async def run_escalation(reason: str, manual=False):
    esc = engine.open_escalation(reason, manual=manual)
    await broadcast()
    snap = engine.snapshot(integrations())
    esc["script"] = await voice.make_script(snap, esc["options"], reason, esc["script"])
    if voice.twilio_enabled():
        ok, info = await voice.place_call(esc["id"])
        esc["call"] = {"ok": ok, "info": info}
        engine.event("call", f"Calling coordinator: {'ringing' if ok else 'FAILED ' + info}",
                     audience=["control", "map"])
    else:
        esc["call"] = {"ok": True, "info": "browser coordinator phone (Twilio not configured)"}
    await broadcast()


async def loop():
    last = time.monotonic()
    while True:
        await asyncio.sleep(TICK_SEC)
        now = time.monotonic()
        engine.tick(now - last)
        engine.demo.apply_to(engine)
        engine.check_box()
        last = now
        if engine.needs_escalation:
            engine.needs_escalation = False
            reason = engine.last_trouble or (engine.reasons[0] if engine.reasons else "Arrival is close to the safe limit")
            asyncio.create_task(run_escalation(reason))
        await broadcast()


async def feed_loop():
    while True:
        await feeds.poll()
        feeds.apply_to(engine)
        await asyncio.sleep(POLL_SEC)


@asynccontextmanager
async def lifespan(app):
    task = asyncio.create_task(loop())
    feed_task = asyncio.create_task(feed_loop())
    print("\n  Organ Relay running. Open http://localhost:8000\n"
          f"  Integrations: {integrations()}\n")
    if engine.demo.enabled:
        print("  NOTE: simulated demo feeds are ON (flight track, schedule, road, cargo hours, charter, theatre). "
              "They are not live data. Set DEMO_FEEDS=0 to disable.\n")
    if os.getenv("PUBLIC_URL") and not AUTH:
        print("  WARNING: PUBLIC_URL is set but BASIC_AUTH_USER/BASIC_AUTH_PASS are not. "
              "Anyone with the URL can use the control panel.\n")
    yield
    task.cancel()
    feed_task.cancel()


app = FastAPI(title="Organ Relay", lifespan=lifespan)
AUTH = (os.getenv("BASIC_AUTH_USER"), os.getenv("BASIC_AUTH_PASS"))
if all(AUTH):
    app.add_middleware(BasicAuth, user=AUTH[0], password=AUTH[1])


# ------------------------------------------------------------------ API
@app.get("/api/state")
async def state():
    return engine.snapshot(integrations(), feeds.snapshot())


@app.post("/api/control")
async def control(req: Request):
    body = await req.json()
    a = body.get("action")
    if a == "play":
        engine.running = True
    elif a == "pause":
        engine.running = False
    elif a == "reset":
        engine.reset(body.get("organ"), body.get("preservation"))
        feeds.apply_to(engine)
        engine.demo.apply_to(engine)
    elif a == "speed":
        engine.speed = float(body.get("value", 120))
        engine.speed_before_escalation = None
    elif a == "delay":
        engine.add_delay(float(body.get("minutes", 60)), body.get("label", "Delay"))
    elif a == "temp":
        engine.set_temp(float(body["value"]))
    elif a == "shock":
        engine.shock(float(body.get("g", 3.2)), "simulated")
    elif a == "escalate":
        if engine.escalation and not engine.escalation.get("decided"):
            return JSONResponse({"ok": False, "error": "Escalation already open"}, 409)
        engine.escalation = None
        asyncio.create_task(run_escalation(body.get("reason", "Manual check requested from control panel"), manual=True))
    elif a == "box_offline":  # demo: the box goes silent
        engine.device_report({})
        engine.device["ts"] -= BOX_OFFLINE_SEC + 1
        engine.check_box()
    elif a == "box_online":
        engine.device_report({k: (engine.device or {}).get(k) for k in ("lat", "lng", "accuracy")})
        engine.check_box()
    elif a == "demo":
        engine.demo.set_condition(body.get("name", ""), bool(body.get("value")), engine)
    elif a == "demo_enabled":
        engine.demo.set_enabled(bool(body.get("value")), engine)
    elif a == "feeds":
        feeds.apply = bool(body.get("value"))
        feeds.apply_to(engine)
    elif a == "clear_escalation":
        engine.escalation = None
    else:
        return JSONResponse({"ok": False, "error": f"unknown action {a}"}, 400)
    await broadcast()
    return {"ok": True}


@app.post("/api/telemetry")
async def telemetry(req: Request):
    """Real sensor data from the phone inside the cooler."""
    body = await req.json()
    if body.get("kind") == "shock":
        engine.shock(float(body.get("g", 3)), "phone accelerometer")
    elif body.get("kind") == "geo":
        engine.device_report({k: body.get(k) for k in ("lat", "lng", "accuracy")})
    await broadcast()
    return {"ok": True}


@app.post("/api/decide")
async def decide(req: Request):
    body = await req.json()
    esc = engine.escalation
    if not esc or esc.get("decided"):
        return JSONResponse({"ok": False, "error": "No open escalation"}, 409)
    key = body.get("choice")
    utter = body.get("utterance")
    if not key and utter:
        key, refusal = await understand(utter, esc["options"], body.get("source", "coordinator phone"))
        if refusal:
            await broadcast()
            return {"ok": False, "refused": True, "say": refusal}
        if not key:
            return {"ok": False, "understood": False}
    ok, info = engine.decide(key, body.get("source", "coordinator phone"), utter)
    await broadcast()
    return {"ok": ok, "choice": info}


async def understand(utterance: str, options: list[dict], source: str):
    """-> (option key, refusal text). Recipient and clinical requests are refused (Tier 3) and logged."""
    refusal = voice.clinical_refusal(utterance)
    key = None if refusal else await voice.interpret(utterance, options)
    refusal = refusal or (None if key else voice.recipient_refusal(utterance))
    if refusal:
        engine.event("refusal", "Request refused: recipient, allocation and clinical decisions are not made by the system.",
                     audience=["control", "map"], why="outside logistics scope (Tier 3)", who=source)
    return key, refusal


async def twilio_form(req: Request):
    """Return the form body if Twilio's signature checks out, else None. Without credentials, always None."""
    token, base = os.getenv("TWILIO_AUTH_TOKEN"), os.getenv("PUBLIC_URL")
    if not token or not base:
        return None
    form = dict(await req.form())
    url = base.rstrip("/") + req.url.path + (f"?{req.url.query}" if req.url.query else "")
    return form if voice.valid_signature(url, form, req.headers.get("X-Twilio-Signature", ""), token) else None


FORBIDDEN = Response("Forbidden", status_code=403)


# ------------------------------------------------------- Twilio webhooks
def _hints():
    esc = engine.escalation
    return [o["key"] for o in esc["options"]] if esc else ["charter", "reroute", "hold"]


@app.post("/voice/twiml")
async def twiml(req: Request, eid: str = ""):
    if await twilio_form(req) is None:
        return FORBIDDEN
    esc = engine.escalation
    if not esc or esc["id"] != eid or esc.get("decided"):
        return Response(voice.twiml_say("This alert has already been handled. Goodbye."), media_type="application/xml")
    action = f"/voice/answer?eid={eid}&tries=1"
    return Response(voice.twiml_gather(esc["script"], action, _hints()), media_type="application/xml")


@app.post("/voice/answer")
async def answer(req: Request, eid: str = "", tries: int = 1):
    form = await twilio_form(req)
    if form is None:
        return FORBIDDEN
    speech = str(form.get("SpeechResult", ""))
    esc = engine.escalation
    if not esc or esc["id"] != eid or esc.get("decided"):
        return Response(voice.twiml_say("This alert has already been handled. Goodbye."), media_type="application/xml")
    key, refusal = await understand(speech, esc["options"], "phone call") if speech else (None, None)
    if not key:
        if tries >= 2:
            return Response(voice.twiml_say((refusal + " " if refusal else "I still didn't catch that. ")
                                            + "The alert stays open on the dashboard."),
                            media_type="application/xml")
        opts = ", ".join(_hints())
        return Response(voice.twiml_gather(f"{refusal or 'Sorry, I didn\'t catch that.'} Say {opts}.",
                                           f"/voice/answer?eid={eid}&tries={tries + 1}", _hints()),
                        media_type="application/xml")
    engine.decide(key, "phone call", speech)
    await broadcast()
    decision = next((e for e in reversed(engine.events) if e["kind"] == "decision"), None)
    said = (decision or {}).get("say") or "Confirmed."
    return Response(voice.twiml_say(said + " Thank you."), media_type="application/xml")


# ------------------------------------------------------------- websocket
@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    clients.add(ws)
    try:
        await ws.send_text(json.dumps(engine.snapshot(integrations(), feeds.snapshot())))
        while True:
            await ws.receive_text()  # keepalive; clients don't need to send anything
    except WebSocketDisconnect:
        pass
    finally:
        clients.discard(ws)


@app.get("/")
async def index():
    return RedirectResponse("/index.html")


app.mount("/", StaticFiles(directory=ROOT / "static", html=True), name="static")
