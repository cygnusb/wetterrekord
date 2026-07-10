const HEAT_COLORS = {
  alltime: "#8b0012",
  month: "#e63946",
  quinzaine: "#f4692e",
  day: "#ff9248",
};
const COLD_COLORS = {
  alltime: "#021c8f",
  month: "#2f6fed",
  quinzaine: "#38a3e0",
  day: "#6ec6ff",
};
const GUST_COLORS = {
  alltime: "#3f0e63",
  month: "#7b1fa2",
  quinzaine: "#9c4dcc",
  day: "#c58af9",
};
const RAIN_COLORS = {
  alltime: "#00463d",
  month: "#00897b",
  quinzaine: "#2bb5a0",
  day: "#7fd8c8",
};
const PRESS_COLORS = {
  alltime: "#7a5c00",
  month: "#c7a500",
  quinzaine: "#ddc233",
  day: "#f3e57e",
};
const NONE_COLOR = "#5a6577";
const NODATA_COLOR = "#333a48";
const LEVEL_LABEL = { alltime: "Allzeit", month: "Monat", quinzaine: "Halbmonat", day: "Tag" };
const LEVEL_LABEL_LONG = {
  alltime: "Allzeitrekord", month: "Monatsrekord", quinzaine: "Halbmonatsrekord", day: "Tagesrekord",
};

function fmtTemp(v) {
  return v === null || v === undefined ? "–" : v.toFixed(1).replace(".", ",") + " °C";
}
function fmtGust(v) {
  // FX kommt in m/s vom DWD, angezeigt wird km/h
  return v === null || v === undefined ? "–" : Math.round(v * 3.6) + " km/h";
}
function fmtRain(v) {
  return v === null || v === undefined ? "–" : v.toFixed(1).replace(".", ",") + " mm";
}
function fmtPress(v) {
  return v === null || v === undefined ? "–" : v.toFixed(1).replace(".", ",") + " hPa";
}
const shortTemp = (v) => v.toFixed(1).replace(".", ",") + "°";

// Farbskalen der Farbfläche: [Wert, r, g, b, a]-Stops, dazwischen linear.
// Temperatur nach dem klassischen Modellkarten-Schema (meteologix/GFS-Stil):
// violett → blau → grün → gelb → orange → rot → dunkelrot, und jenseits von
// ~40 °C der charakteristische Umschlag über rosa nach weiß und grau.
const TEMP_SCALE = [
  [-25, 150, 0, 150, 210], [-18, 90, 0, 160, 210], [-12, 30, 30, 200, 210],
  [-6, 60, 130, 235, 210], [0, 150, 210, 235, 210], [2, 0, 110, 60, 210],
  [8, 60, 170, 75, 210], [14, 175, 220, 90, 210], [17, 250, 230, 80, 210],
  [21, 250, 180, 45, 210], [25, 245, 130, 30, 210], [29, 230, 65, 30, 210],
  [32, 200, 20, 20, 210], [35, 150, 0, 10, 210], [38, 120, 20, 30, 210],
  [40, 220, 150, 160, 210], [43, 245, 220, 225, 210], [45, 255, 255, 255, 210],
  [48, 200, 200, 200, 210], [50, 150, 150, 150, 210],
];
// Böen in m/s (Anzeige km/h): ~29/40/61/90/119/162 km/h, an Warnstufen angelehnt
const GUST_SCALE = [
  [0, 100, 160, 120, 40], [8, 120, 200, 80, 120], [11, 235, 220, 60, 160],
  [17, 245, 150, 40, 190], [25, 225, 50, 40, 210], [33, 150, 30, 160, 230],
  [45, 90, 0, 120, 240],
];
const RAIN_SCALE = [
  [0, 80, 120, 180, 0], [0.5, 110, 160, 210, 60], [2, 80, 140, 220, 120],
  [10, 40, 90, 200, 170], [25, 30, 40, 160, 210], [50, 130, 40, 170, 230],
];
const PRESSURE_SCALE = [
  [985, 60, 80, 200, 180], [1000, 70, 160, 200, 160], [1013, 110, 190, 120, 140],
  [1025, 235, 190, 70, 160], [1040, 230, 90, 40, 190],
];

const MODES = {
  heat: {
    icon: "🔥", noun: "Hitzerekorde", todayLabel: "Max/Min des Tages", nearText: "≤1 °C",
    colors: HEAT_COLORS, value: (st) => st.tmax_today, records: (st) => st.records.high,
    status: (st) => st.heat, fmt: fmtTemp, short: shortTemp, scale: TEMP_SCALE,
  },
  cold: {
    icon: "❄️", noun: "Kälterekorde", todayLabel: "Max/Min des Tages", nearText: "≤1 °C",
    colors: COLD_COLORS, value: (st) => st.tmin_today, records: (st) => st.records.low,
    status: (st) => st.cold, fmt: fmtTemp, short: shortTemp, scale: TEMP_SCALE,
  },
  gust: {
    icon: "💨", noun: "Sturmrekorde (Böen)", todayLabel: "stärkste Böe heute", nearText: "≤7 km/h",
    colors: GUST_COLORS, value: (st) => st.params.gust.value, records: (st) => st.params.gust.records,
    status: (st) => st.params.gust.status, fmt: fmtGust, short: fmtGust, scale: GUST_SCALE,
  },
  precip: {
    icon: "🌧️", noun: "Regenrekorde (Tagessumme)", todayLabel: "Niederschlag heute (läuft auf)", nearText: "≤5 mm",
    colors: RAIN_COLORS, value: (st) => st.params.precip.value, records: (st) => st.params.precip.records,
    status: (st) => st.params.precip.status, fmt: fmtRain, short: fmtRain, scale: RAIN_SCALE,
  },
  press: {
    // ein Tagesmittelwert, aber zwei Rekordrichtungen (Hoch-/Tiefdruck):
    // Status und Rekordtabelle kommen von der "gewinnenden" Richtung
    icon: "🌀", noun: "Luftdruckrekorde (Tagesmittel)", todayLabel: "Luftdruck-Tagesmittel auf Meereshöhe (bisher)", nearText: "≤2 hPa",
    colors: PRESS_COLORS, value: (st) => st.params.phigh.value, records: (st) => pressWinner(st).records,
    status: (st) => pressWinner(st).status, fmt: fmtPress, short: fmtPress, scale: PRESSURE_SCALE,
  },
};

const LEVEL_RANK = { alltime: 4, month: 3, quinzaine: 2, day: 1 };
// Hoch- oder Tiefdruck — welcher Rekordvergleich "gewinnt" für Marker/Badge?
function pressWinner(st) {
  const hi = { ...st.params.phigh, kind: "high" };
  const lo = { ...st.params.plow, kind: "low" };
  const pick = (get) => {
    const h = get(hi.status);
    const l = get(lo.status);
    if (h && (!l || LEVEL_RANK[h] >= LEVEL_RANK[l])) return hi;
    if (l) return lo;
    return null;
  };
  return pick((s) => s.level) || pick((s) => s.near) || hi;
}

let mode = "heat";
let view = "map";
let stations = [];
let markers = new Map();
let sortKey = "recordAge";
let sortDir = -1;
let timelineOffset = 0; // 0 = jetzt, negative Schritte à 30 min
let selectedStationId = null;
// "nur Rekorde" gilt pro Ansicht: Tabelle standardmäßig an, Karte aus
const recordsOnly = { map: false, table: true };

const map = L.map("map", { zoomSnap: 0.5 }).setView([51.2, 10.3], 6);
L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/">CARTO</a>',
  maxZoom: 12,
}).addTo(map);

// ---- Farbfläche: IDW-interpoliertes Messwertfeld über Deutschland ----
// zwischen Kachel-Ebene (200) und Marker-Overlay (400)
map.createPane("interp").style.zIndex = 350;
map.getPane("interp").style.pointerEvents = "none";

let overlayEnabled = localStorage.getItem("wetterrekordOverlay") !== "0";
let overlayLayer = null;
let overlayKey = null; // memo: nur neu rechnen, wenn sich etwas geändert hat
let dataStamp = 0; // wird bei jedem load() hochgezählt
let germanyRings = null; // Polygon-Ringe [[lon, lat], ...]

fetch("germany.geo.json")
  .then((r) => r.json())
  .then((geo) => {
    germanyRings = [];
    for (const f of geo.features) {
      const polys = f.geometry.type === "MultiPolygon" ? f.geometry.coordinates : [f.geometry.coordinates];
      for (const p of polys) germanyRings.push(p[0]);
    }
    renderOverlay();
  });

function scaleColor(stops, v) {
  // liefert [r, g, b, a]; außerhalb der Stops wird geklemmt
  if (v <= stops[0][0]) return stops[0].slice(1);
  const last = stops[stops.length - 1];
  if (v >= last[0]) return last.slice(1);
  for (let i = 1; i < stops.length; i++) {
    if (v <= stops[i][0]) {
      const [v0, ...c0] = stops[i - 1];
      const [v1, ...c1] = stops[i];
      const t = (v - v0) / (v1 - v0);
      return [0, 1, 2, 3].map((k) => c0[k] + t * (c1[k] - c0[k]));
    }
  }
  return last.slice(1);
}

// IDW (Potenz 2) über die k nächsten Stationen; Koordinaten äquirektangular
// (lon mit cos(lat) gestaucht), auf der Skala Deutschlands genau genug
const IDW_K = 12;
const LON_SCALE = Math.cos((51 * Math.PI) / 180);

function idw(pts, x, y) {
  const nearest = []; // [d2, v], aufsteigend, max. IDW_K Einträge
  for (const p of pts) {
    const dx = (p.x - x) * LON_SCALE;
    const dy = p.y - y;
    const d2 = dx * dx + dy * dy;
    if (nearest.length === IDW_K && d2 >= nearest[IDW_K - 1][0]) continue;
    let i = nearest.length;
    while (i > 0 && nearest[i - 1][0] > d2) i--;
    nearest.splice(i, 0, [d2, p.v]);
    if (nearest.length > IDW_K) nearest.pop();
  }
  let wsum = 0, vsum = 0;
  for (const [d2, v] of nearest) {
    if (d2 < 1e-10) return v;
    const w = 1 / d2;
    wsum += w;
    vsum += w * v;
  }
  return vsum / wsum;
}

function overlayPoints() {
  return stations
    .map((st) => ({ x: st.lon, y: st.lat, v: stToday(st) }))
    .filter((p) => p.v !== null && p.v !== undefined);
}

function renderOverlay() {
  const show = overlayEnabled && view === "map" && germanyRings;
  const pts = show ? overlayPoints() : [];
  document.getElementById("map").classList.toggle("overlay-crosshair", pts.length >= 3);
  if (pts.length < 3) {
    if (overlayLayer) { map.removeLayer(overlayLayer); overlayLayer = null; }
    overlayKey = null;
    return;
  }
  const key = `${mode}|${dataStamp}|${map.getBounds().toBBoxString()}`;
  if (key === overlayKey) return;
  overlayKey = key;

  const size = map.getSize();
  const STEP = 4; // grobes Raster, das Hochskalieren glättet
  const gw = Math.ceil(size.x / STEP);
  const gh = Math.ceil(size.y / STEP);
  const grid = document.createElement("canvas");
  grid.width = gw;
  grid.height = gh;
  const gctx = grid.getContext("2d");
  const img = gctx.createImageData(gw, gh);
  const scale = MODES[mode].scale;
  for (let j = 0; j < gh; j++) {
    for (let i = 0; i < gw; i++) {
      const ll = map.containerPointToLatLng([(i + 0.5) * STEP, (j + 0.5) * STEP]);
      const c = scaleColor(scale, idw(pts, ll.lng, ll.lat));
      const o = (j * gw + i) * 4;
      img.data[o] = c[0];
      img.data[o + 1] = c[1];
      img.data[o + 2] = c[2];
      img.data[o + 3] = c[3];
    }
  }
  gctx.putImageData(img, 0, 0);

  // aufs Deutschland-Polygon zuschneiden
  const canvas = document.createElement("canvas");
  canvas.width = size.x;
  canvas.height = size.y;
  const ctx = canvas.getContext("2d");
  ctx.beginPath();
  for (const ring of germanyRings) {
    ring.forEach(([lon, lat], i) => {
      const pt = map.latLngToContainerPoint([lat, lon]);
      if (i === 0) ctx.moveTo(pt.x, pt.y);
      else ctx.lineTo(pt.x, pt.y);
    });
    ctx.closePath();
  }
  ctx.clip();
  ctx.imageSmoothingEnabled = true;
  ctx.drawImage(grid, 0, 0, size.x, size.y);

  const layer = L.imageOverlay(canvas.toDataURL(), map.getBounds(), {
    pane: "interp", opacity: 0.55, interactive: false,
  }).addTo(map);
  if (overlayLayer) map.removeLayer(overlayLayer);
  overlayLayer = layer;
}

map.on("moveend zoomend", renderOverlay);
map.on("zoomend", () => { if (view === "map") renderMap(); });

// Mouseover irgendwo auf der Fläche: interpolierten Wert unterm Cursor
// zeigen — deutlich als interpoliert markiert
function inGermany(lng, lat) {
  let inside = false;
  for (const ring of germanyRings) {
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
      const [xi, yi] = ring[i];
      const [xj, yj] = ring[j];
      if (yi > lat !== yj > lat && lng < ((xj - xi) * (lat - yi)) / (yj - yi) + xi) inside = !inside;
    }
  }
  return inside;
}

const interpTip = L.tooltip({ direction: "top", offset: [0, -10], className: "temp-label interp-tip" });
function hideInterpTip() {
  if (map.hasLayer(interpTip)) map.removeLayer(interpTip);
}
map.on("mousemove", (ev) => {
  // über einem Stationsmarker hat dessen eigener Tooltip Vorrang
  const overMarker = ev.originalEvent.target?.classList?.contains("leaflet-interactive");
  if (!overlayEnabled || view !== "map" || !germanyRings || overMarker
      || !inGermany(ev.latlng.lng, ev.latlng.lat)) {
    hideInterpTip();
    return;
  }
  const pts = overlayPoints();
  if (pts.length < 3) {
    hideInterpTip();
    return;
  }
  const v = idw(pts, ev.latlng.lng, ev.latlng.lat);
  interpTip.setContent(`≈ ${MODES[mode].fmt(v)}<br><span class="interp-note">interpoliert</span>`);
  interpTip.setLatLng(ev.latlng);
  if (!map.hasLayer(interpTip)) interpTip.addTo(map);
});
map.on("mouseout", hideInterpTip);

function levelColors() {
  return MODES[mode].colors;
}
function stStatus(st) {
  return MODES[mode].status(st);
}
function stToday(st) {
  return MODES[mode].value(st);
}
function stRecords(st) {
  return MODES[mode].records(st);
}
// Status einer Station im aktiven Modus: {type: broken|near|none|nodata, level}
function statusInfo(st) {
  const recs = stRecords(st);
  const hasRecords = recs && Object.values(recs).some((r) => r);
  if (stToday(st) === null || stToday(st) === undefined || !hasRecords) {
    return { type: "nodata", level: null };
  }
  const s = stStatus(st);
  const kind = mode === "press" ? pressWinner(st).kind : null;
  if (s.level) return { type: "broken", level: s.level, kind };
  if (s.near) return { type: "near", level: s.near, kind };
  return { type: "none", level: null };
}
function badgeText(info) {
  const prefix = info.kind ? (info.kind === "high" ? "Hochdruck: " : "Tiefdruck: ") : "";
  if (info.type === "broken") return `${prefix}${LEVEL_LABEL_LONG[info.level]} gebrochen`;
  if (info.type === "near") return `${prefix}nah am ${LEVEL_LABEL_LONG[info.level]}`;
  if (info.type === "nodata") return "keine Daten";
  return "kein Rekord";
}
function badgeHtml(info) {
  const c = levelColors();
  if (info.type === "broken") {
    return `<span class="badge" style="background:${c[info.level]}">${badgeText(info)}</span>`;
  }
  if (info.type === "near") {
    return `<span class="badge badge-near" style="border-color:${c[info.level]};color:${c[info.level]}">${badgeText(info)}</span>`;
  }
  return `<span class="badge" style="background:${info.type === "nodata" ? NODATA_COLOR : NONE_COLOR}">${badgeText(info)}</span>`;
}
function recordYear(st) {
  const rec = stRecords(st).day;
  return rec ? Number(rec.date.slice(0, 4)) : null;
}
function recordAge(st) {
  const y = recordYear(st);
  return y === null ? null : new Date().getFullYear() - y;
}

function fmtDate(iso) {
  if (!iso) return "–";
  const [y, m, d] = iso.split("-");
  return `${d}.${m}.${y}`;
}

function passesFilter(st) {
  const land = document.getElementById("filter-land").value;
  if (land && st.bundesland !== land) return false;
  const maxAlt = document.getElementById("filter-alt").value;
  if (maxAlt !== "" && st.altitude > Number(maxAlt)) return false;
  return true;
}
function filtered() {
  return stations.filter(passesFilter);
}

// ---- Statistik-Leiste ----
function brokenCount(st, m) {
  return MODES[m].status(st).level ? 1 : 0;
}
function renderStats() {
  const c = levelColors();
  const visible = filtered();
  const counts = Object.fromEntries(Object.keys(MODES).map((m) => [m, 0]));
  const broken = { alltime: 0, month: 0, quinzaine: 0, day: 0 };
  let oldest = null;
  for (const st of visible) {
    for (const m of Object.keys(MODES)) counts[m] += brokenCount(st, m);
    const info = statusInfo(st);
    if (info.type === "broken") {
      broken[info.level]++;
      const rec = stRecords(st)[info.level];
      if (rec) {
        const year = Number(rec.date.slice(0, 4));
        const age = new Date().getFullYear() - year;
        if (!oldest || age > oldest.age) oldest = { age, name: st.name, year };
      }
    }
  }
  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  const when = timelineOffset === 0 ? "Heute" : "Zu diesem Zeitpunkt";
  const breakdown = Object.entries(MODES)
    .map(([m, cfg]) => `${cfg.icon} ${counts[m]}`)
    .join(" · ");
  const parts = [
    `<span class="stat"><b>${total}</b> Rekord${total === 1 ? "" : "e"} gebrochen (${when}: ${breakdown})</span>`,
  ];
  for (const lvl of ["alltime", "month", "quinzaine", "day"]) {
    if (broken[lvl]) parts.push(`<span class="stat"><i style="background:${c[lvl]}"></i>${broken[lvl]}× ${LEVEL_LABEL[lvl]}</span>`);
  }
  if (oldest) {
    parts.push(`<span class="stat">ältester gebrochener Rekord: <b>${oldest.name}</b>, von ${oldest.year} (${oldest.age} Jahre)</span>`);
  }
  document.getElementById("stats").innerHTML = parts.join(" · ");
}

// ---- Karte ----
function tempLabel(st) {
  const v = stToday(st);
  return v === null || v === undefined ? "" : MODES[mode].short(v);
}
// Markergröße wächst mit dem Zoom: in der Deutschland-Übersicht klein,
// beim Reinzoomen größer (Faktor 1 bei Zoom 8)
function zoomFactor() {
  return Math.min(1.8, Math.max(0.55, 1 + (map.getZoom() - 8) * 0.22));
}
function renderMap() {
  const c = levelColors();
  const zf = zoomFactor();
  for (const st of stations) {
    const m = markers.get(st.id);
    const info = statusInfo(st);
    const hideNonRecord = recordsOnly.map && info.type !== "broken";
    if (view !== "map" || !passesFilter(st) || hideNonRecord) {
      m.unbindTooltip();
      map.removeLayer(m);
      continue;
    }
    if (!map.hasLayer(m)) m.addTo(map);
    m.unbindTooltip();
    if (recordsOnly.map) {
      // im Nur-Rekorde-Modus den Messwert permanent an der Station anzeigen
      m.bindTooltip(tempLabel(st), {
        permanent: true, direction: "top", offset: [0, -6], className: "temp-label",
      });
    } else {
      m.bindTooltip(`<b>${st.name}</b><br>${MODES[mode].fmt(stToday(st))}`, {
        direction: "top", offset: [0, -6], className: "temp-label",
      });
    }
    if (info.type === "broken") {
      m.setStyle({ fillColor: c[info.level], fillOpacity: 0.95, color: "#0b0e13", weight: 1, radius: 7 * zf });
      m.bringToFront();
    } else if (info.type === "near") {
      m.setStyle({ fillColor: c[info.level], fillOpacity: 0.15, color: c[info.level], weight: 2.5, radius: 7 * zf });
      m.bringToFront();
    } else {
      m.setStyle({
        fillColor: info.type === "nodata" ? NODATA_COLOR : NONE_COLOR,
        fillOpacity: 0.95, color: "#0b0e13", weight: 1, radius: 4.5 * zf,
      });
    }
  }
}

// ---- Tabelle ----
const COLUMNS = [
  { key: "name", label: "Station", value: (st) => st.name },
  { key: "bundesland", label: "Bundesland", value: (st) => st.bundesland },
  { key: "altitude", label: "Höhe (m)", value: (st) => st.altitude, num: true },
  { key: "today", label: "aktuell", value: (st) => stToday(st), num: true, fmt: (v) => MODES[mode].fmt(v) },
  {
    key: "record", label: "Tagesrekord", num: true,
    value: (st) => (stRecords(st).day ? stRecords(st).day.value : null), fmt: (v) => MODES[mode].fmt(v),
  },
  { key: "recordAge", label: "Rekord von", value: (st) => recordYear(st), num: true },
  { key: "status", label: "Status", value: (st) => statusInfo(st) },
];
const STATUS_ORDER = { broken: 0, near: 1, none: 2, nodata: 3 };
const LEVEL_ORDER = { alltime: 0, month: 1, quinzaine: 2, day: 3, null: 4 };

function renderTable() {
  let rows = filtered();
  if (recordsOnly.table) {
    rows = rows.filter((st) => statusInfo(st).type === "broken");
  }
  rows = rows.slice().sort((a, b) => {
    const col = COLUMNS.find((x) => x.key === sortKey);
    let va = col.value(a), vb = col.value(b);
    if (sortKey === "status") {
      va = STATUS_ORDER[va.type] * 10 + LEVEL_ORDER[va.level];
      vb = STATUS_ORDER[vb.type] * 10 + LEVEL_ORDER[vb.level];
    }
    if (va === null) return 1;
    if (vb === null) return -1;
    if (va < vb) return -sortDir;
    if (va > vb) return sortDir;
    return 0;
  });
  const head = COLUMNS.map(
    (col) => `<th data-key="${col.key}" class="${col.key === sortKey ? "sorted" : ""}">${col.label}${col.key === sortKey ? (sortDir > 0 ? " ▲" : " ▼") : ""}</th>`
  ).join("");
  const body = rows.map((st) => {
    const info = statusInfo(st);
    const cells = COLUMNS.map((col) => {
      if (col.key === "status") return `<td>${badgeHtml(info)}</td>`;
      const v = col.value(st);
      const txt = col.fmt ? col.fmt(v) : v ?? "–";
      return `<td class="${col.num ? "num" : ""}">${txt}</td>`;
    }).join("");
    return `<tr data-id="${st.id}">${cells}</tr>`;
  }).join("");
  const empty = rows.length === 0
    ? `<tr><td colspan="${COLUMNS.length}" class="empty">Keine Stationen mit Rekord${timelineOffset === 0 ? " (bisher heute)" : ""} — Filter „nur Rekorde" abwählen, um alle zu sehen.</td></tr>`
    : "";
  document.getElementById("stations-table").innerHTML = `<thead><tr>${head}</tr></thead><tbody>${body}${empty}</tbody>`;
}

// ---- Panel ----
function showPanel(st) {
  selectedStationId = st.id;
  const c = levelColors();
  const info = statusInfo(st);

  function recordTable(title, recs, status) {
    const rows = [
      ["day", "heutiger Kalendertag"],
      ["quinzaine", "Halbmonat"],
      ["month", "laufender Monat"],
      ["alltime", "Allzeit"],
    ].filter(([lvl]) => recs[lvl])
      .map(([lvl, label]) => {
        const r = recs[lvl];
        let mark = "";
        if (status.level === lvl) mark = `<span style="color:${c[lvl]}">●</span> `;
        else if (status.near === lvl) mark = `<span style="color:${c[lvl]}">○</span> `;
        return `<tr><td>${mark}${label}</td><td class="val">${MODES[mode].fmt(r.value)}</td><td class="date">${fmtDate(r.date)}</td></tr>`;
      })
      .join("");
    return rows ? `<table><tr><th>${title}</th><th></th><th></th></tr>${rows}</table>` : "";
  }
  // Druck: ein Messwert, aber zwei Rekordrichtungen — beide Tabellen zeigen
  const tables = mode === "press"
    ? recordTable("Hochdruckrekorde", st.params.phigh.records, st.params.phigh.status)
      + recordTable("Tiefdruckrekorde", st.params.plow.records, st.params.plow.status)
    : recordTable(MODES[mode].noun, stRecords(st), stStatus(st));

  const todayVals = mode === "heat" || mode === "cold"
    ? `<span class="hot">▲ ${fmtTemp(st.tmax_today)}</span>
       <span class="cold">▼ ${fmtTemp(st.tmin_today)}</span>`
    : `<span>${MODES[mode].icon} ${MODES[mode].fmt(stToday(st))}</span>`;
  document.getElementById("panel-content").innerHTML = `
    <h2>${st.name}</h2>
    <div class="meta">${st.bundesland} · ${st.altitude} m · Daten seit ${st.first_year}</div>
    ${badgeHtml(info)}
    <div class="today-vals">${todayVals}</div>
    <div class="meta">${MODES[mode].todayLabel}${st.last_measurement ? ", letzte Messung " + st.last_measurement.slice(11, 16) + " Uhr" : ""}</div>
    ${tables}`;
  document.getElementById("panel").classList.remove("hidden");
}

function renderLegend() {
  const c = levelColors();
  const levels = ["alltime", "month", "quinzaine", "day"]
    .map((k) => `<span><i style="background:${c[k]}"></i>${LEVEL_LABEL_LONG[k]}</span>`)
    .join("");
  let scalebar = "";
  if (overlayEnabled && view === "map") {
    const stops = MODES[mode].scale;
    const lo = stops[0][0];
    const hi = stops[stops.length - 1][0];
    const grad = stops
      .map(([v, r, g, b, a]) => `rgba(${r},${g},${b},${a / 255}) ${(((v - lo) / (hi - lo)) * 100).toFixed(1)}%`)
      .join(", ");
    scalebar =
      `<span class="sep">|</span><span class="scalebar">${MODES[mode].short(lo)}` +
      `<i style="background:linear-gradient(90deg, ${grad})"></i>${MODES[mode].short(hi)}</span>`;
  }
  document.getElementById("legend").innerHTML =
    `${levels}<span class="sep">|</span>` +
    `<span><i style="background:${c.day}"></i>gefüllt = gebrochen</span>` +
    `<span><i class="ring" style="border-color:${c.day}"></i>Ring = nah dran (${MODES[mode].nearText})</span>` +
    `<span><i style="background:${NONE_COLOR}"></i>kein Rekord</span>` +
    `<span><i style="background:${NODATA_COLOR}"></i>keine Daten</span>` +
    scalebar;
}

function render() {
  document.getElementById("map").classList.toggle("hidden", view !== "map");
  document.getElementById("table-view").classList.toggle("hidden", view !== "table");
  document.getElementById("filter-records").checked = recordsOnly[view];
  renderStats();
  renderLegend();
  if (view === "map") renderMap();
  else renderTable();
  renderOverlay();
}

// ---- Zeitleiste ----
function timelineDate() {
  return new Date(Date.now() + timelineOffset * 30 * 60 * 1000);
}
function updateTimelineLabel() {
  const label = document.getElementById("timeline-label");
  const atNow = timelineOffset === 0;
  if (atNow) {
    label.textContent = "jetzt";
  } else {
    const d = timelineDate();
    // beyond 24 h the weekday alone is ambiguous — add the date
    const opts = timelineOffset < -48
      ? { weekday: "short", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }
      : { weekday: "short", hour: "2-digit", minute: "2-digit" };
    label.textContent = d.toLocaleString("de-DE", opts) + " Uhr";
  }
  // Label und Button sind immer gerendert (Button nur deaktiviert), damit
  // die Zeile beim Schieben nie umbricht oder springt
  document.getElementById("timeline-now").disabled = atNow;
  document.querySelector(".timeline").classList.toggle("tl-past", !atNow);
}

// slider range: at most 30 days back, but never further than stored data
const MAX_TIMELINE_OFFSET = -30 * 48; // 30 min steps
function updateTimelineRange(historyStart) {
  if (!historyStart) return;
  const steps = Math.ceil((new Date(historyStart) - Date.now()) / (30 * 60 * 1000));
  const min = Math.max(MAX_TIMELINE_OFFSET, Math.min(-1, steps));
  const tl = document.getElementById("timeline");
  tl.min = min;
  document.getElementById("timeline-ticks").innerHTML =
    `<option value="${min}"></option><option value="${Math.round(min / 2)}"></option><option value="0"></option>`;
  const hours = -min / 2;
  document.getElementById("timeline-min-label").innerHTML = hours >= 48
    ? `−${Math.round(hours / 24)}&thinsp;T`
    : `−${Math.round(hours)}&thinsp;h`;
  if (timelineOffset < min) {
    timelineOffset = min;
    tl.value = min;
    updateTimelineLabel();
  }
}

async function load() {
  const url = timelineOffset === 0
    ? "api/stations"
    : "api/stations?at=" + encodeURIComponent(timelineDate().toISOString());
  const resp = await fetch(url);
  if (!resp.ok) return;
  const data = await resp.json();
  // während des Rekord-Neuaufbaus sind die Daten inkonsistent: Hinweis
  // einblenden und regelmäßig nachfragen, bis der Import fertig ist
  document.getElementById("ingest-overlay").classList.toggle("hidden", !data.ingest_running);
  if (data.ingest_running) {
    setTimeout(load, 30 * 1000);
    return;
  }
  stations = data.stations;
  dataStamp++;
  document.getElementById("generated-at").textContent = data.generated_at.slice(0, 16).replace("T", " ");
  updateTimelineRange(data.history_start);

  const laender = [...new Set(stations.map((s) => s.bundesland))].sort();
  const sel = document.getElementById("filter-land");
  const current = sel.value;
  sel.length = 1;
  for (const l of laender) sel.add(new Option(l, l));
  sel.value = current;

  for (const m of markers.values()) map.removeLayer(m);
  markers.clear();
  for (const st of stations) {
    const m = L.circleMarker([st.lat, st.lon], {
      radius: 5, weight: 1, color: "#0b0e13", fillOpacity: 0.95,
    });
    m.on("click", () => showPanel(st));
    markers.set(st.id, m);
  }
  if (selectedStationId !== null && !document.getElementById("panel").classList.contains("hidden")) {
    const updated = stations.find((s) => s.id === selectedStationId);
    if (updated) showPanel(updated);
    else document.getElementById("panel").classList.add("hidden");
  }
  render();
}

// ---- Events ----
function setToggle(groupIds, activeId) {
  for (const id of groupIds) document.getElementById(id).classList.toggle("active", id === activeId);
}
const MODE_BUTTON_IDS = Object.keys(MODES).map((m) => "mode-" + m);
for (const m of Object.keys(MODES)) {
  document.getElementById("mode-" + m).addEventListener("click", () => {
    mode = m; setToggle(MODE_BUTTON_IDS, "mode-" + m); render();
  });
}
document.getElementById("view-map").addEventListener("click", () => {
  view = "map"; setToggle(["view-map", "view-table"], "view-map"); render();
  map.invalidateSize();
});
document.getElementById("view-table").addEventListener("click", () => {
  view = "table"; setToggle(["view-map", "view-table"], "view-table"); render();
});
document.getElementById("filter-land").addEventListener("change", render);
document.getElementById("filter-records").addEventListener("change", (ev) => {
  recordsOnly[view] = ev.target.checked;
  render();
});
document.getElementById("filter-alt").addEventListener("input", render);
const overlayToggle = document.getElementById("overlay-toggle");
overlayToggle.checked = overlayEnabled;
overlayToggle.addEventListener("change", () => {
  overlayEnabled = overlayToggle.checked;
  localStorage.setItem("wetterrekordOverlay", overlayEnabled ? "1" : "0");
  renderLegend();
  renderOverlay();
});
document.getElementById("panel-close").addEventListener("click", () => {
  selectedStationId = null;
  document.getElementById("panel").classList.add("hidden");
});
document.getElementById("stations-table").addEventListener("click", (ev) => {
  const th = ev.target.closest("th");
  if (th) {
    const key = th.dataset.key;
    if (key === sortKey) sortDir = -sortDir;
    else { sortKey = key; sortDir = key === "name" || key === "bundesland" ? 1 : -1; }
    renderTable();
    return;
  }
  const tr = ev.target.closest("tr[data-id]");
  if (tr) {
    const st = stations.find((s) => s.id === tr.dataset.id);
    if (st) showPanel(st);
  }
});

const timeline = document.getElementById("timeline");
// Browser stellen Formularwerte beim Reload wieder her — der Regler stünde
// dann nicht auf "jetzt", obwohl die App live lädt. Explizit zurücksetzen.
timeline.value = 0;
timeline.addEventListener("input", () => {
  timelineOffset = Number(timeline.value);
  updateTimelineLabel();
});
timeline.addEventListener("change", () => load());
document.getElementById("timeline-now").addEventListener("click", () => {
  timeline.value = 0;
  timelineOffset = 0;
  updateTimelineLabel();
  load();
});

// About-Sektion: nur beim ersten Besuch aufgeklappt; eingeklappt verschwindet
// sie komplett und wird über den Footer-Link wieder geöffnet
const aboutSection = document.getElementById("about");
function setAbout(open) {
  aboutSection.classList.toggle("hidden", !open);
  setTimeout(() => map.invalidateSize(), 50);
}
setAbout(!localStorage.getItem("wetterrekordAboutSeen"));
localStorage.setItem("wetterrekordAboutSeen", "1");
document.getElementById("about-toggle").addEventListener("click", () => setAbout(false));
document.getElementById("about-open").addEventListener("click", (ev) => {
  ev.preventDefault();
  setAbout(true);
});

updateTimelineLabel();
load();
setInterval(() => { if (timelineOffset === 0) load(); }, 5 * 60 * 1000);
