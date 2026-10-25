(() => {
  if (typeof map === "undefined" || typeof L === "undefined") return;
  const REFRESH_MS = 60000;
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const plane = (deg) => L.divIcon({
    className: "", iconSize: [20, 20], iconAnchor: [10, 10],
    html: `<svg viewBox="0 0 24 24" width="20" height="20" style="transform:rotate(${Number(deg) || 0}deg)"><path fill="#7dd3fc" stroke="#0b0d10" stroke-width="1" d="M12 2c.8 0 1.3.9 1.3 2v5l7.7 4.5v2l-7.7-2.3V18l2.4 1.8V21L12 20l-3.7 1v-1.2l2.4-1.8v-4.8L3 15.5v-2L10.7 9V4c0-1.1.5-2 1.3-2z"/></svg>`,
  });

  const flights = L.layerGroup().addTo(map);
  const fires = L.layerGroup();
  const control = L.control.layers(null, { "Live flights (adsb.lol)": flights }, { collapsed: false, position: "topleft" }).addTo(map);

  async function getJSON(url) {
    try {
      const r = await fetch(url);
      return r.ok ? await r.json() : null;
    } catch (e) {
      return null;
    }
  }

  async function loadFlights() {
    if (!map.hasLayer(flights)) return;
    const d = await getJSON("/api/medroute/flights");
    if (!d) return;
    flights.clearLayers();
    d.flights.forEach((f) => L.marker([f.lat, f.lng], { icon: plane(f.track), keyboard: false })
      .bindTooltip(`${esc(f.flight)}${f.type ? " · " + esc(f.type) : ""} · ${esc(f.alt ?? "?")} ft`).addTo(flights));
  }

  async function loadFires() {
    if (!map.hasLayer(fires)) return;
    const d = await getJSON("/api/medroute/fires");
    if (!d) return;
    fires.clearLayers();
    d.fires.forEach((f) => L.circleMarker([f.lat, f.lng], { radius: 4 + Math.min(6, f.frp / 20), color: "#ef4444", fillColor: "#f97316", fillOpacity: .85, weight: 1 })
      .bindTooltip(`Fire · ${esc(f.acq)} UTC · FRP ${esc(f.frp)} MW`).addTo(fires));
  }

  map.on("overlayadd", (e) => {
    if (e.layer === flights) loadFlights();
    if (e.layer === fires) loadFires();
  });

  getJSON("/api/medroute/layers").then((l) => {
    if (!l) return;
    if (l.traffic.available) {
      const traffic = L.tileLayer("/api/medroute/traffic/{z}/{x}/{y}.png", { opacity: .85, maxZoom: 18, zIndex: 5, attribution: "Traffic &copy; TomTom" });
      control.addOverlay(traffic, "Traffic (TomTom)");
      traffic.addTo(map);
    }
    if (l.fires.available) {
      control.addOverlay(fires, "Fires (NASA FIRMS, 24 h)");
      fires.addTo(map);
      loadFires();
      setInterval(loadFires, REFRESH_MS);
    }
  });

  loadFlights();
  setInterval(loadFlights, REFRESH_MS);
})();
