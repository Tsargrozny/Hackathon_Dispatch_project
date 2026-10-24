// Shared helpers for every Organ Relay view.
window.OR = (() => {
  const STATUS_COLORS = { green: "#22c55e", amber: "#f59e0b", red: "#ef4444" };

  function fmtHM(min) {
    const m = Math.round(Math.abs(min));
    const s = min < 0 ? "-" : "";
    return `${s}${Math.floor(m / 60)}h ${String(m % 60).padStart(2, "0")}m`;
  }

  // Live state over a websocket, reconnecting forever.
  function connect(onState) {
    let ws;
    const open = () => {
      ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
      ws.onmessage = (m) => onState(JSON.parse(m.data));
      ws.onclose = () => setTimeout(open, 1000);
      ws.onerror = () => ws.close();
    };
    open();
    setInterval(() => ws && ws.readyState === 1 && ws.send("ping"), 20000);
  }

  async function post(path, body) {
    const r = await fetch(path, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body || {}) });
    return r.json();
  }

  // Calls cb(event) once for each *new* event meant for this audience.
  // Events that already existed when the page loaded are skipped.
  function eventStream(audience, cb) {
    let lastId = null;
    let lastPlan = null;
    return (state) => {
      const evs = state.events || [];
      const maxId = evs.length ? evs[evs.length - 1].id : 0;
      if (lastId === null || maxId < lastId) { lastId = maxId; return; } // first load or server reset
      for (const e of evs) {
        if (e.id <= lastId) continue;
        if (e.audience.includes("all") || e.audience.includes(audience)) cb(e, state);
      }
      lastId = maxId;
    };
  }

  // ---- voice
  let voiceOn = false;
  const speechQueue = [];
  function enableVoice() {
    voiceOn = true;
    try { speechSynthesis.speak(new SpeechSynthesisUtterance(" ")); } catch (e) {}
  }
  function speak(text, { interrupt = false, onend } = {}) {
    if (!voiceOn || !text || !("speechSynthesis" in window)) { onend && onend(); return; }
    if (interrupt) speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(text);
    u.rate = 1.0;
    if (onend) u.onend = onend;
    speechSynthesis.speak(u);
  }

  function vibrate(pattern) {
    if (pattern && navigator.vibrate) navigator.vibrate(pattern);
  }

  async function wakeLock() {
    try {
      if ("wakeLock" in navigator) {
        let lock = await navigator.wakeLock.request("screen");
        document.addEventListener("visibilitychange", async () => {
          if (document.visibilityState === "visible") lock = await navigator.wakeLock.request("screen");
        });
        return true;
      }
    } catch (e) { console.warn("wake lock", e); }
    return false;
  }

  // Full-screen "tap to start" overlay. Browsers only allow sound, motion
  // sensors and wake lock after a user gesture.
  function startOverlay(title, subtitle, onStart) {
    const el = document.createElement("div");
    el.className = "overlay";
    el.innerHTML = `<div><h1>${title}</h1><p>${subtitle}</p><button>Tap to start</button></div>`;
    document.body.appendChild(el);
    el.querySelector("button").onclick = async () => {
      enableVoice();
      await onStart();
      el.remove();
    };
  }

  return { STATUS_COLORS, fmtHM, connect, post, eventStream, speak, enableVoice, vibrate, wakeLock, startOverlay };
})();
