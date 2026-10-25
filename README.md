# Hackathon_Dispatch_project

## MedRoute dispatcher line: call **(858) 348-9340**

Call the number, say which organ you have, where it is going and how long ago it was recovered. The Vapi voice agent calls our `plan_organ_transport` tool, the Organ Relay engine below re-plans the shipment and starts playback, and the agent reads back the destination, ETA and margin in one sentence. Every call shows up live in the event feed on the judges' map (`/map.html`) and the control panel.

### Quick start (Windows Command Prompt)

```bat
cd %USERPROFILE%\OneDrive\Documents\GitHub\Hackathon_Dispatch_project
python -m venv .venv
.venv\Scripts\activate.bat
pip install -r requirements.txt
uvicorn server.main:app --host 0.0.0.0 --port 8000
```

Open http://localhost:8000/settings to paste API keys, and http://localhost:8000/map.html for the judges' map.

### API keys: `/settings`

`http://localhost:8000/settings` is a dark page for pasting keys. They are saved to `.env` on this laptop, which git ignores, and reloaded without a restart. `GET /api/settings` only reports which keys are set and never returns the values. The settings page and `POST /api/settings` only accept requests from this laptop, so they stay locked even when ngrok exposes the server for Vapi. Every key is optional.

| Key | Adds | Without it |
|---|---|---|
| `TOMTOM_API_KEY` | Live traffic flow tiles on the map | No traffic layer |
| `FIRMS_MAP_KEY` | NASA FIRMS active fires (last 24 h) on the map | No fire layer |
| `CESIUM_ION_TOKEN`, `OPENSKY_CLIENT_ID`, `OPENSKY_CLIENT_SECRET`, `VAPI_PUBLIC_KEY`, `VAPI_ASSISTANT_ID` | Stored for later integrations (the default assistant is `779b0be3-e9ce-48f6-a130-1498576f2888`) | Nothing changes |

### Connecting Vapi

1. Run `ngrok http 8000` and copy the https URL.
2. In the Vapi dashboard, set the assistant's **Server URL** to `https://<your-ngrok-host>/vapi`.
3. Add a function tool named `plan_organ_transport` with parameters `organ` (string), `from_hospital` (string), `to_hospital` (string) and `minutes_since_recovery` (number).

`POST /vapi` always answers HTTP 200. Tool calls get the Vapi `{"results": [{"toolCallId", "result"}]}` reply. The destination is a loose name match against the transplant centers in `server/scenario.py` (Cedars-Sinai, UC Davis, UCSF), and falls back to Cedars-Sinai. `status-update`, final `transcript`, `conversation-update` and `end-of-call-report` messages are logged as engine events ("Phone line: call connected", "Caller: …", "Dispatcher: …", "Call report: summary"). Token-by-token messages such as `model-output` and `speech-update` are ignored to keep the feed readable.

### Live vs simulated data

| Data | Live or simulated |
|---|---|
| Phone call, speech recognition, voice agent replies | **Live** (Vapi) |
| Shipment movement, ETA, margin, custody handoffs, cooler temperature | **Simulated** by the Organ Relay engine on an accelerated clock. Delays, temperature and shocks are injected from the control panel |
| Box phone accelerometer shocks and GPS | **Live** from the phone's sensors |
| Hospital and airport coordinates | Real |
| Organs, people, couriers, flight numbers | Fictional |
| Flights layer on the map | **Live** from adsb.lol (no key) |
| Traffic layer | **Live** TomTom flow tiles, only with `TOMTOM_API_KEY` |
| Wildfire layer | **Live** NASA FIRMS VIIRS detections, only with `FIRMS_MAP_KEY` |

Each layer is fetched by the backend through `/api/medroute/*` and cached for 60 seconds. If a source fails, its layer simply stays empty.

### How we used AI tools

- **Claude Code** built the backend and the map from our written specs: the `/settings` key store, the `POST /vapi` webhook with the `plan_organ_transport` tool, and the live data layers. We reviewed and tested each step and committed it phase by phase.
- **Vapi** runs the voice agent on (858) 348-9340. It handles speech-to-text, the conversation and text-to-speech, and calls our server for the tool.
- **Claude** (optional, `ANTHROPIC_API_KEY`) writes the spoken escalation alert and interprets free-form coordinator replies in the Organ Relay engine.

# Organ Relay

**The cooler that coordinates itself.** An invisible interface for donor-organ transport.

- **Sensing replaces data entry.** The box infers where it is, who has it, and how cold it is. Custody handoffs log themselves from geofences.
- **Light and voice replace screens.** The box glows green, amber or red. The OR's light warms as the organ approaches. The courier's phone buzzes in patterns.
- **Humans are only interrupted for decisions.** When a delay pushes the organ's ETA past its safe window, the coordinator's phone rings with the options already worked out. They answer with one word: *charter*, *reroute* or *hold*.

## Run it (Windows, PowerShell, in VS Code's terminal)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn server.main:app --host 0.0.0.0 --port 8000
```

Open http://localhost:8000. You can also press **F5** in VS Code, which uses the "Organ Relay server" launch config.

If PowerShell blocks the activate script, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once.

### Phones

Motion sensors, GPS and screen wake-lock need HTTPS. Install [ngrok](https://ngrok.com/download), then run:

```powershell
ngrok http 8000
```

Open the `https://….ngrok-free.app` URL on each phone. Use Android for the box and the courier if you can, because vibration and Web NFC don't work in iPhone Safari.

## The views

| Page | Who / where | What it does |
|---|---|---|
| `box.html` | Phone inside the cooler | Glows by status, speaks changes, reports real shocks (accelerometer) and GPS |
| `courier.html` | Courier's pocket | Haptic patterns + spoken instructions; nearly blank screen |
| `coordinator.html` | OPO coordinator | Silent until needed, then rings; answer by voice (Chrome) or tap |
| `or.html` | Recipient OR wall screen | Ambient light warms with ETA; 120/60/30/10-minute announcements. `?center=cedars` or `?center=ucdavis` makes it one specific hospital |
| `map.html` | Projector for judges | What the system sees: route, margin, auto-logged chain of custody |
| `control.html` | Backstage laptop | "Wizard of Oz" controls: clock speed, delays, temperature, shocks |

## Demo script (about 3 minutes)

1. Open the box, coordinator and OR pages on separate devices and tap start on each. Put the map on the projector.
2. On the control panel, press **Play**. The box glows green and handoffs log themselves. *Nobody has typed anything.*
3. Press **Flight cancelled (+5h)**. The box turns red and speaks, the story clock slows, and the coordinator phone rings.
4. Answer and say **"reroute"** or **"charter"**. Free-form replies like "yeah, send it to Davis" also work. The box goes green, the courier buzzes, the old OR is told to stand down and the new one is told the organ is inbound.
5. Set the speed to 900× and let it deliver. Show the custody chain on the map.

## Optional integrations (`.env`)

Copy `.env.example` to `.env`.

- **Claude** (`ANTHROPIC_API_KEY`) writes the spoken alert and understands free-form replies that keywords miss. Without it, the demo uses a template script and keyword matching.
- **Twilio** (`TWILIO_*`, `COORDINATOR_PHONE`, `PUBLIC_URL`) places a real phone call that listens for the spoken answer. Set `PUBLIC_URL` to your ngrok https URL. Trial accounts can only call verified numbers, so verify the demo phone ahead of time.

The browser coordinator phone works either way. Keep it open as a backup on stage.

## How it works

```
server/
  scenario.py  route, places, organ windows, thresholds  ← edit to change the story
  engine.py    simulator + risk engine + escalation options
  voice.py     Claude script/interpretation, Twilio call + TwiML
  main.py      FastAPI: websocket broadcast, control/telemetry/decide APIs, Twilio webhooks
static/        the six views (plain HTML/JS, Leaflet bundled for offline use)
```

- **Margin** = safe time left in the organ's cold window minus ETA. Status is amber below 20% of the window and red below 8%, or red for a temperature excursion above 10 °C.
- When the margin goes red, the engine computes options from where the box is *now*: hold, charter (call-out + nearest charter airports + ground) and reroute (best-margin alternate center by ground). It slows the clock while the human decides, then applies the choice.
- Organs and people in the scenario are fictional. Hospital and airport coordinates are real so the map looks right. Change `SCENARIO["organ"]`, or use the organ picker on the control panel: heart and liver start on a charter route, because they'd never fly as commercial cargo.

## API (for teammates)

- `POST /api/control` `{action: play|pause|reset|speed|delay|temp|shock|escalate, ...}`
- `POST /api/telemetry` `{kind: "shock", g}` or `{kind: "geo", lat, lng, accuracy}`
- `POST /api/decide` `{choice}` or `{utterance}`
- `GET /api/state`, `WS /ws` (full state about twice a second)
