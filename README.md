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
| `TOMTOM_API_KEY` | Live traffic flow tiles and the TomTom night basemap on the map | No traffic layer; the map uses the Esri dark-gray basemap |
| `FIRMS_MAP_KEY` | NASA FIRMS active fires (last 24 h) on the map | No fire layer |
| `CESIUM_ION_TOKEN`, `OPENSKY_CLIENT_ID`, `OPENSKY_CLIENT_SECRET`, `VAPI_PUBLIC_KEY`, `VAPI_ASSISTANT_ID` | Stored for later integrations (the default assistant is `779b0be3-e9ce-48f6-a130-1498576f2888`) | Nothing changes |

### Connecting Vapi

1. Run `ngrok http 8000` (or a Cloudflare Tunnel, see [Public URL](#public-url-ngrok-or-cloudflare-tunnel)) and copy the https URL.
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
| Basemap | **Live** TomTom night tiles (proxied and cached for an hour) with `TOMTOM_API_KEY`; otherwise Esri dark-gray. Esri satellite is always selectable from the map's layer switcher |
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

### Public URL (ngrok or Cloudflare Tunnel)

Motion sensors, GPS and screen wake-lock need HTTPS, and Vapi needs a public URL. Use either tunnel. Both give an https URL that forwards to the server on port 8000.

**ngrok:** install [ngrok](https://ngrok.com/download), then run:

```powershell
ngrok http 8000
```

**Cloudflare Tunnel (no account or login needed):** install [cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/), then run:

```powershell
cloudflared tunnel --url http://localhost:8000
```

It prints a `https://<random-words>.trycloudflare.com` URL. Judges and phones only need that link. They never touch your Cloudflare account, and there is no Cloudflare username or password to share. The URL changes every time you restart the tunnel, so update `PUBLIC_URL` and the Vapi Server URL (`<url>/vapi`) each time.

Anyone with the URL can reach the app. Share `<url>/map.html` with judges and keep the control panel (`/control.html`) to yourself, or set `BASIC_AUTH_USER` and `BASIC_AUTH_PASS` in `.env` to require a login.

### Phones

Open the tunnel's https URL (`https://….ngrok-free.app` or `https://….trycloudflare.com`) on each phone. Use Android for the box and the courier if you can, because vibration and Web NFC don't work in iPhone Safari.

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

## Live airport conditions and preservation mode

- `server/feeds.py` polls the **FAA NAS status** and **METAR** feeds every 2 minutes (free, no keys) and shows them on the control panel. Ground stops, closures and delay programs, and IFR/LIFR weather, become heuristic delay minutes on the next flight touching that airport. GA-only closure notices are ignored.
- Feeds are **display-only by default**. Tick "Apply live feeds" on the control panel (or set `FEEDS_APPLY=1`) to let them change the shipment. Real-world delays can otherwise surprise a demo.
- The control panel's reset has a **Static ice / Perfusion pump** choice. The pump window and packaging time are demo assumptions in `PRESERVATION` in `scenario.py`.

## Simulated demo feeds (demo only, not live)

`server/demo_feeds.py` supplies **static stand-ins** for data sources that aren't integrated yet, so the demo can show how they would change a shipment. **None of it is real or live.** The values are invented for demonstration, and the file says so at the top.

| Stand-in for | What the demo uses |
|---|---|
| ADS-B flight track | A canned RNO to LAX path, with altitude and speed derived from progress. The waypoints are illustrative, not a real track. |
| Airline schedule | Scheduled vs estimated times, derived from the demo route. |
| Road data (Google Routes) | A fixed traffic factor. The "road incident" switch adds 60% to drive times. |
| Cargo office hours | An invented hours table. "LAX cargo closed" holds the organ until 07:00. |
| Charter availability | An invented table. "No charter at RNO" moves the charter to the next free airport. |
| Theatre availability | An invented table. A centre that isn't ready is dropped from reroute options, or flagged if it's the destination. |
| Recipient travel time | A fixed 40 minutes, producing a "call the recipient now" announcement. |

- It is **on by default**. Switch it off with `DEMO_FEEDS=0` or the control-panel checkbox. Switching it off removes any delays it added.
- It is labelled everywhere: a "DEMO ONLY" badge on the control panel, "includes SIMULATED demo data" on the judges' map, a `[DEMO]` prefix on the delay events it causes, and the decision log records the actor as `demo feed, simulated`.
- The scenario buttons on the control panel flip each condition on and off.
- Replace each table with a real feed as it is integrated. The real FAA and METAR feeds are separate (`feeds.py`).

## Safety, audit and access

- **Decision log:** every event, custody change and decision is appended to `decision_log.jsonl` (what, why, who, UTC time, unique action ID). It is never rewritten, so it survives resets. The file is git-ignored.
- **Access:** set `BASIC_AUTH_USER` and `BASIC_AUTH_PASS` in `.env` before exposing the app with ngrok. This protects every page and API except `/voice/*`, which instead rejects any request without a valid Twilio signature (and always rejects when Twilio isn't configured). `POST /vapi` is also exempt, because Vapi cannot send basic-auth credentials, so keep the ngrok URL private. The server prints a warning if `PUBLIC_URL` is set without basic auth.
- **Out of scope:** recipient choice, accepting or declining organs, and clinical or viability questions are refused (spoken, tapped or by phone) and logged. The system only handles logistics.
- **Box offline:** if the box has reported before and then stays silent for 10 minutes (`BOX_OFFLINE_SEC`), the status goes amber, the coordinator is alerted and the map greys the last known location. Use **Box goes silent** on the control panel to demo it.
- **Stale data:** a live feed older than 5 minutes is marked low confidence, is never applied to the shipment, and triggers one coordinator alert. The control panel shows each feed's age.
- **Critical:** when no option fits inside the planned window, the status says so plainly, the call leads with it, and the best available ETA is shown.
- **Privacy:** no personal names (couriers and crews are role labels), and the shipment code is never sent to Claude.

## Roadmap (intended future features)

Not built yet. Each entry notes what it needs.

**Transport**
- **ADS-B (adsb.lol):** live aircraft position for the flight leg. Needs a real shipment tied to a real flight number.
- **Airline schedules:** planned vs actual times, so we can tell a flight is late and not only where it is. Needs a paid API.
- **Cargo office hours:** if the cargo office is shut when the organ lands, nobody can collect it. Needs real opening hours per airport.
- **Road data (Google Routes, Caltrans 511):** live drive times, accidents and closures in place of `ground_minutes()`. Google Routes needs a billing-enabled key. Caltrans 511 doesn't cover Nevada.
- **Charter and drone availability:** which backup aircraft are free right now, replacing the fixed 45-minute call-out. Needs a partner feed.
- **TAF forecasts:** METAR is live today. TAF adds the next 24 to 30 hours.

**Coordination**
- **Theatre availability:** don't offer a reroute to a centre whose OR or surgeon isn't ready.
- **Recipient travel time:** time the OR announcements to when the patient needs to be called.
- **Confirmation log:** who acknowledged what, and when, including the OR and courier. Today only the coordinator's decision is logged.
- **TransNet scan and OCR at checkpoints:** a scan or a phone-camera read of the label or air waybill as a second custody source that overrides the geofence guess. Needs a scan/camera screen.

**History and prediction**
- **MedRoute run history:** save every finished run (events, custody, margins) to disk.
- **OPTN STAR files:** calibrate organ windows and thresholds from past CIT and distances. Needs a signed data use agreement.
- **Incident and near-miss reports:** past failures by route, airline and airport.

**Product ideas**
- **Team transport:** route the surgeons who fly out to procure hearts, livers and lungs, as well as the organ.
- **Storm mode:** when an airport shuts, replan every organ in the region at once. Needs the engine to handle several shipments.
- **Auto incident report:** generate the standardised report HRSA now wants, from `events` and `custody`.
- **Cost report export:** turn transport costs into line items for OPO Medicare cost reports.
- **Coordinator training simulator:** reuse the control panel as a flight simulator for failures.
- **Other cargo:** blood, bone marrow, tissue and time-critical lab samples.
- **Feasibility score on offers:** "arrives at 9h CIT vs 17h CIT" to help centres accept faster. Advisory only. The options already carry ETA and margin.

## API (for teammates)

- `POST /api/control` `{action: play|pause|reset|speed|delay|temp|shock|escalate|feeds, ...}` (`reset` takes `organ` and `preservation`; `feeds` takes `value`)
- `POST /api/telemetry` `{kind: "shock", g}` or `{kind: "geo", lat, lng, accuracy}`
- `POST /api/decide` `{choice}` or `{utterance}`
- `GET /api/state`, `WS /ws` (full state about twice a second)
