"""Organ Relay server: run with `uvicorn server.main:app --host 0.0.0.0 --port 8000`."""
from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

load_dotenv()

from . import voice  # noqa: E402  (env must be loaded first)
from .engine import Engine  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TICK_SEC = 0.5

engine = Engine()
clients: set[WebSocket] = set()


def integrations():
    return {"claude": voice.claude_enabled(), "twilio": voice.twilio_enabled()}


async def broadcast():
    if not clients:
        return
    msg = json.dumps(engine.snapshot(integrations()))
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
        last = now
        if engine.needs_escalation:
            engine.needs_escalation = False
            reason = engine.last_trouble or (engine.reasons[0] if engine.reasons else "Arrival is close to the safe limit")
            asyncio.create_task(run_escalation(reason))
        await broadcast()


@asynccontextmanager
async def lifespan(app):
    task = asyncio.create_task(loop())
    print("\n  Organ Relay running. Open http://localhost:8000\n"
          f"  Integrations: {integrations()}\n")
    yield
    task.cancel()


app = FastAPI(title="Organ Relay", lifespan=lifespan)


# ------------------------------------------------------------------ API
@app.get("/api/state")
async def state():
    return engine.snapshot(integrations())


@app.post("/api/control")
async def control(req: Request):
    body = await req.json()
    a = body.get("action")
    if a == "play":
        engine.running = True
    elif a == "pause":
        engine.running = False
    elif a == "reset":
        engine.reset(body.get("organ"))
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
        key = await voice.interpret(utter, esc["options"])
        if not key:
            return {"ok": False, "understood": False}
    ok, info = engine.decide(key, body.get("source", "coordinator phone"), utter)
    await broadcast()
    return {"ok": ok, "choice": info}


# ------------------------------------------------------- Twilio webhooks
def _hints():
    esc = engine.escalation
    return [o["key"] for o in esc["options"]] if esc else ["charter", "reroute", "hold"]


@app.post("/voice/twiml")
async def twiml(eid: str = ""):
    esc = engine.escalation
    if not esc or esc["id"] != eid or esc.get("decided"):
        return Response(voice.twiml_say("This alert has already been handled. Goodbye."), media_type="application/xml")
    action = f"/voice/answer?eid={eid}&tries=1"
    return Response(voice.twiml_gather(esc["script"], action, _hints()), media_type="application/xml")


@app.post("/voice/answer")
async def answer(req: Request, eid: str = "", tries: int = 1):
    form = await req.form()
    speech = str(form.get("SpeechResult", ""))
    esc = engine.escalation
    if not esc or esc["id"] != eid or esc.get("decided"):
        return Response(voice.twiml_say("This alert has already been handled. Goodbye."), media_type="application/xml")
    key = await voice.interpret(speech, esc["options"]) if speech else None
    if not key:
        if tries >= 2:
            return Response(voice.twiml_say("I still didn't catch that. The alert stays open on the dashboard."),
                            media_type="application/xml")
        opts = ", ".join(_hints())
        return Response(voice.twiml_gather(f"Sorry, I didn't catch that. Say {opts}.",
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
        await ws.send_text(json.dumps(engine.snapshot(integrations())))
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
