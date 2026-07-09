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
const PHIGH_COLORS = {
  alltime: "#7a5c00",
  month: "#c7a500",
  quinzaine: "#ddc233",
  day: "#f3e57e",
};
const PLOW_COLORS = {
  alltime: "#59103f",
  month: "#ad1457",
  quinzaine: "#d81b60",
  day: "#f48fb1",
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

const MODES = {
  heat: {
    icon: "🔥", noun: "Hitzerekorde", todayLabel: "Max/Min des Tages", nearText: "≤1 °C",
    colors: HEAT_COLORS, value: (st) => st.tmax_today, records: (st) => st.records.high,
    status: (st) => st.heat, fmt: fmtTemp, short: shortTemp,
  },
  cold: {
    icon: "❄️", noun: "Kälterekorde", todayLabel: "Max/Min des Tages", nearText: "≤1 °C",
    colors: COLD_COLORS, value: (st) => st.tmin_today, records: (st) => st.records.low,
    status: (st) => st.cold, fmt: fmtTemp, short: shortTemp,
  },
  gust: {
    icon: "💨", noun: "Sturmrekorde (Böen)", todayLabel: "stärkste Böe heute", nearText: "≤7 km/h",
    colors: GUST_COLORS, value: (st) => st.params.gust.value, records: (st) => st.params.gust.records,
    status: (st) => st.params.gust.status, fmt: fmtGust, short: fmtGust,
  },
  precip: {
    icon: "🌧️", noun: "Regenrekorde (Tagessumme)", todayLabel: "Niederschlag heute (läuft auf)", nearText: "≤5 mm",
    colors: RAIN_COLORS, value: (st) => st.params.precip.value, records: (st) => st.params.precip.records,
    status: (st) => st.params.precip.status, fmt: fmtRain, short: fmtRain,
  },
  phigh: {
    icon: "⬆️", noun: "Hochdruckrekorde (Tagesmittel)", todayLabel: "Luftdruck-Tagesmittel (bisher)", nearText: "≤2 hPa",
    colors: PHIGH_COLORS, value: (st) => st.params.phigh.value, records: (st) => st.params.phigh.records,
    status: (st) => st.params.phigh.status, fmt: fmtPress, short: fmtPress,
  },
  plow: {
    icon: "⬇️", noun: "Tiefdruckrekorde (Tagesmittel)", todayLabel: "Luftdruck-Tagesmittel (bisher)", nearText: "≤2 hPa",
    colors: PLOW_COLORS, value: (st) => st.params.plow.value, records: (st) => st.params.plow.records,
    status: (st) => st.params.plow.status, fmt: fmtPress, short: fmtPress,
  },
};

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
  if (s.level) return { type: "broken", level: s.level };
  if (s.near) return { type: "near", level: s.near };
  return { type: "none", level: null };
}
function badgeText(info) {
  if (info.type === "broken") return `${LEVEL_LABEL_LONG[info.level]} gebrochen`;
  if (info.type === "near") return `nah am ${LEVEL_LABEL_LONG[info.level]}`;
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
  const minAge = document.getElementById("filter-age").value;
  if (minAge !== "") {
    const age = recordAge(st);
    if (age === null || age < Number(minAge)) return false;
  }
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
function renderMap() {
  const c = levelColors();
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
    // im Nur-Rekorde-Modus den Messwert direkt an der Station anzeigen
    m.unbindTooltip();
    if (recordsOnly.map) {
      m.bindTooltip(tempLabel(st), {
        permanent: true, direction: "top", offset: [0, -6], className: "temp-label",
      });
    }
    if (info.type === "broken") {
      m.setStyle({ fillColor: c[info.level], fillOpacity: 0.95, color: "#0b0e13", weight: 1, radius: 7 });
      m.bringToFront();
    } else if (info.type === "near") {
      m.setStyle({ fillColor: c[info.level], fillOpacity: 0.15, color: c[info.level], weight: 2.5, radius: 7 });
      m.bringToFront();
    } else {
      m.setStyle({
        fillColor: info.type === "nodata" ? NODATA_COLOR : NONE_COLOR,
        fillOpacity: 0.95, color: "#0b0e13", weight: 1, radius: 4.5,
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
  const recs = stRecords(st);
  const rows = [
    ["day", "heutiger Kalendertag"],
    ["quinzaine", "Halbmonat"],
    ["month", "laufender Monat"],
    ["alltime", "Allzeit"],
  ].filter(([lvl]) => recs[lvl])
    .map(([lvl, label]) => {
      const r = recs[lvl];
      let mark = "";
      if (info.level === lvl) {
        mark = info.type === "broken"
          ? `<span style="color:${c[lvl]}">●</span> `
          : `<span style="color:${c[lvl]}">○</span> `;
      }
      return `<tr><td>${mark}${label}</td><td class="val">${MODES[mode].fmt(r.value)}</td><td class="date">${fmtDate(r.date)}</td></tr>`;
    })
    .join("");
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
    <table>
      <tr><th>${MODES[mode].noun}</th><th></th><th></th></tr>
      ${rows}
    </table>`;
  document.getElementById("panel").classList.remove("hidden");
}

function renderLegend() {
  const c = levelColors();
  const levels = ["alltime", "month", "quinzaine", "day"]
    .map((k) => `<span><i style="background:${c[k]}"></i>${LEVEL_LABEL_LONG[k]}</span>`)
    .join("");
  document.getElementById("legend").innerHTML =
    `${levels}<span class="sep">|</span>` +
    `<span><i style="background:${c.day}"></i>gefüllt = gebrochen</span>` +
    `<span><i class="ring" style="border-color:${c.day}"></i>Ring = nah dran (${MODES[mode].nearText})</span>` +
    `<span><i style="background:${NONE_COLOR}"></i>kein Rekord</span>` +
    `<span><i style="background:${NODATA_COLOR}"></i>keine Daten</span>`;
}

function render() {
  document.getElementById("map").classList.toggle("hidden", view !== "map");
  document.getElementById("table-view").classList.toggle("hidden", view !== "table");
  document.getElementById("filter-records").checked = recordsOnly[view];
  renderStats();
  renderLegend();
  if (view === "map") renderMap();
  else renderTable();
}

// ---- Zeitleiste ----
function timelineDate() {
  return new Date(Date.now() + timelineOffset * 30 * 60 * 1000);
}
function updateTimelineLabel() {
  const label = document.getElementById("timeline-label");
  const atNow = timelineOffset === 0;
  if (!atNow) {
    const d = timelineDate();
    // beyond 24 h the weekday alone is ambiguous — add the date
    const opts = timelineOffset < -48
      ? { weekday: "short", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }
      : { weekday: "short", hour: "2-digit", minute: "2-digit" };
    label.textContent = "→ " + d.toLocaleString("de-DE", opts) + " Uhr";
  }
  label.classList.toggle("hidden", atNow);
  document.getElementById("timeline-now").classList.toggle("hidden", atNow);
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
  stations = data.stations;
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
for (const id of ["filter-land", "filter-age"]) {
  document.getElementById(id).addEventListener("change", render);
}
document.getElementById("filter-records").addEventListener("change", (ev) => {
  recordsOnly[view] = ev.target.checked;
  render();
});
document.getElementById("filter-alt").addEventListener("input", render);
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
