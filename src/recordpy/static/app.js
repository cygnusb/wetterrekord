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
const NONE_COLOR = "#5a6577";
const NODATA_COLOR = "#333a48";
const LEVEL_LABEL = { alltime: "Allzeit", month: "Monat", quinzaine: "Halbmonat", day: "Tag" };
const LEVEL_LABEL_LONG = {
  alltime: "Allzeitrekord", month: "Monatsrekord", quinzaine: "Halbmonatsrekord", day: "Tagesrekord",
};

let mode = "heat";
let view = "map";
let stations = [];
let markers = new Map();
let sortKey = "recordAge";
let sortDir = -1;
let timelineOffset = 0; // 0 = jetzt, negative Schritte à 30 min

const map = L.map("map", { zoomSnap: 0.5 }).setView([51.2, 10.3], 6);
L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/">CARTO</a>',
  maxZoom: 12,
}).addTo(map);

function levelColors() {
  return mode === "heat" ? HEAT_COLORS : COLD_COLORS;
}
function stStatus(st) {
  return mode === "heat" ? st.heat : st.cold;
}
function stToday(st) {
  return mode === "heat" ? st.tmax_today : st.tmin_today;
}
function stRecords(st) {
  return mode === "heat" ? st.records.high : st.records.low;
}
// Status einer Station im aktiven Modus: {type: broken|near|none|nodata, level}
function statusInfo(st) {
  if (stToday(st) === null) return { type: "nodata", level: null };
  const s = stStatus(st);
  if (s.level) return { type: "broken", level: s.level };
  if (s.near) return { type: "near", level: s.near };
  return { type: "none", level: null };
}
function badgeText(info) {
  if (info.type === "broken") return `${LEVEL_LABEL_LONG[info.level]} gebrochen`;
  if (info.type === "near") return `nah am ${LEVEL_LABEL_LONG[info.level]}`;
  if (info.type === "nodata") return "keine aktuellen Daten";
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

function fmtTemp(v) {
  return v === null || v === undefined ? "–" : v.toFixed(1).replace(".", ",") + " °C";
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
  return (m === "heat" ? st.heat : st.cold).level ? 1 : 0;
}
function renderStats() {
  const c = levelColors();
  const visible = filtered();
  let heatTotal = 0, coldTotal = 0;
  const broken = { alltime: 0, month: 0, quinzaine: 0, day: 0 };
  let near = 0;
  let oldest = null;
  for (const st of visible) {
    heatTotal += brokenCount(st, "heat");
    coldTotal += brokenCount(st, "cold");
    const info = statusInfo(st);
    if (info.type === "broken") {
      broken[info.level]++;
      const rec = stRecords(st)[info.level];
      if (rec) {
        const year = Number(rec.date.slice(0, 4));
        const age = new Date().getFullYear() - year;
        if (!oldest || age > oldest.age) oldest = { age, name: st.name, year };
      }
    } else if (info.type === "near") {
      near++;
    }
  }
  const total = heatTotal + coldTotal;
  const when = timelineOffset === 0 ? "Heute" : "Zu diesem Zeitpunkt";
  const parts = [
    `<span class="stat"><b>${total}</b> Rekord${total === 1 ? "" : "e"} gebrochen (${when}: 🔥 ${heatTotal} / ❄️ ${coldTotal})</span>`,
  ];
  for (const lvl of ["alltime", "month", "quinzaine", "day"]) {
    if (broken[lvl]) parts.push(`<span class="stat"><i style="background:${c[lvl]}"></i>${broken[lvl]}× ${LEVEL_LABEL[lvl]}</span>`);
  }
  parts.push(`<span class="stat"><b>${near}</b> nah dran</span>`);
  if (oldest) {
    parts.push(`<span class="stat">ältester gebrochener Rekord: <b>${oldest.name}</b>, von ${oldest.year} (${oldest.age} Jahre)</span>`);
  }
  document.getElementById("stats").innerHTML = parts.join(" · ");
}

// ---- Karte ----
function renderMap() {
  const c = levelColors();
  for (const st of stations) {
    const m = markers.get(st.id);
    if (view !== "map" || !passesFilter(st)) {
      map.removeLayer(m);
      continue;
    }
    if (!map.hasLayer(m)) m.addTo(map);
    const info = statusInfo(st);
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
  { key: "today", label: "aktuell", value: (st) => stToday(st), num: true, fmt: fmtTemp },
  {
    key: "record", label: "Tagesrekord", num: true,
    value: (st) => (stRecords(st).day ? stRecords(st).day.value : null), fmt: fmtTemp,
  },
  { key: "recordAge", label: "Rekord von", value: (st) => recordYear(st), num: true },
  { key: "status", label: "Status", value: (st) => statusInfo(st) },
];
const STATUS_ORDER = { broken: 0, near: 1, none: 2, nodata: 3 };
const LEVEL_ORDER = { alltime: 0, month: 1, quinzaine: 2, day: 3, null: 4 };

function renderTable() {
  let rows = filtered();
  if (document.getElementById("filter-records").checked) {
    rows = rows.filter((st) => ["broken", "near"].includes(statusInfo(st).type));
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
      return `<tr><td>${mark}${label}</td><td class="val">${fmtTemp(r.value)}</td><td class="date">${fmtDate(r.date)}</td></tr>`;
    })
    .join("");
  document.getElementById("panel-content").innerHTML = `
    <h2>${st.name}</h2>
    <div class="meta">${st.bundesland} · ${st.altitude} m · Daten seit ${st.first_year}</div>
    ${badgeHtml(info)}
    <div class="today-vals">
      <span class="hot">▲ ${fmtTemp(st.tmax_today)}</span>
      <span class="cold">▼ ${fmtTemp(st.tmin_today)}</span>
    </div>
    <div class="meta">Max/Min des Tages${st.last_measurement ? ", letzte Messung " + st.last_measurement.slice(11, 16) + " Uhr" : ""}</div>
    <table>
      <tr><th>${mode === "heat" ? "Hitzerekorde" : "Kälterekorde"}</th><th></th><th></th></tr>
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
    `<span><i class="ring" style="border-color:${c.day}"></i>Ring = nah dran (≤1 °C)</span>` +
    `<span><i style="background:${NONE_COLOR}"></i>kein Rekord</span>` +
    `<span><i style="background:${NODATA_COLOR}"></i>keine Daten</span>`;
}

function render() {
  document.getElementById("map").classList.toggle("hidden", view !== "map");
  document.getElementById("table-view").classList.toggle("hidden", view !== "table");
  document.getElementById("filter-records-label").classList.toggle("hidden", view !== "table");
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
  if (timelineOffset === 0) {
    label.textContent = "jetzt";
  } else {
    const d = timelineDate();
    label.textContent = d.toLocaleString("de-DE", {
      weekday: "short", hour: "2-digit", minute: "2-digit",
    }) + " Uhr";
  }
  document.getElementById("timeline-now").classList.toggle("hidden", timelineOffset === 0);
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
  render();
}

// ---- Events ----
function setToggle(groupIds, activeId) {
  for (const id of groupIds) document.getElementById(id).classList.toggle("active", id === activeId);
}
document.getElementById("mode-heat").addEventListener("click", () => {
  mode = "heat"; setToggle(["mode-heat", "mode-cold"], "mode-heat"); render();
});
document.getElementById("mode-cold").addEventListener("click", () => {
  mode = "cold"; setToggle(["mode-heat", "mode-cold"], "mode-cold"); render();
});
document.getElementById("view-map").addEventListener("click", () => {
  view = "map"; setToggle(["view-map", "view-table"], "view-map"); render();
  map.invalidateSize();
});
document.getElementById("view-table").addEventListener("click", () => {
  view = "table"; setToggle(["view-map", "view-table"], "view-table"); render();
});
for (const id of ["filter-land", "filter-age", "filter-records"]) {
  document.getElementById(id).addEventListener("change", render);
}
document.getElementById("filter-alt").addEventListener("input", render);
document.getElementById("panel-close").addEventListener("click", () =>
  document.getElementById("panel").classList.add("hidden")
);
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

// About-Sektion: nur beim ersten Besuch aufgeklappt
const aboutBody = document.getElementById("about-body");
const aboutArrow = document.getElementById("about-arrow");
function setAbout(open) {
  aboutBody.classList.toggle("hidden", !open);
  aboutArrow.textContent = open ? "▾" : "▸";
  setTimeout(() => map.invalidateSize(), 50);
}
setAbout(!localStorage.getItem("recordpyAboutSeen"));
localStorage.setItem("recordpyAboutSeen", "1");
document.getElementById("about-toggle").addEventListener("click", () => {
  setAbout(aboutBody.classList.contains("hidden"));
});

updateTimelineLabel();
load();
setInterval(() => { if (timelineOffset === 0) load(); }, 5 * 60 * 1000);
