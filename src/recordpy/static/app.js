const HEAT_COLORS = {
  alltime: { color: "#8b0012", label: "Allzeitrekord gebrochen" },
  month: { color: "#e63946", label: "Monatsrekord gebrochen" },
  quinzaine: { color: "#f4692e", label: "Halbmonatsrekord gebrochen" },
  day: { color: "#ff9248", label: "Tagesrekord gebrochen" },
  near: { color: "#ffd166", label: "nah am Rekord (≤1 °C)" },
  none: { color: "#5a6577", label: "kein Rekord" },
  nodata: { color: "#333a48", label: "keine aktuellen Daten" },
};
const COLD_COLORS = {
  alltime: { color: "#021c8f", label: "Allzeitrekord gebrochen" },
  month: { color: "#2f6fed", label: "Monatsrekord gebrochen" },
  quinzaine: { color: "#38a3e0", label: "Halbmonatsrekord gebrochen" },
  day: { color: "#6ec6ff", label: "Tagesrekord gebrochen" },
  near: { color: "#b8e3ff", label: "nah am Rekord (≤1 °C)" },
  none: { color: "#5a6577", label: "kein Rekord" },
  nodata: { color: "#333a48", label: "keine aktuellen Daten" },
};
const LEVEL_LABEL = { alltime: "Allzeit", month: "Monat", quinzaine: "Halbmonat", day: "Tag" };

let mode = "heat";
let view = "map";
let stations = [];
let markers = new Map();
let sortKey = "recordAge";
let sortDir = -1;

const map = L.map("map", { zoomSnap: 0.5 }).setView([51.2, 10.3], 6);
L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/">CARTO</a>',
  maxZoom: 12,
}).addTo(map);

function stStatus(st) {
  return mode === "heat" ? st.heat : st.cold;
}
function stToday(st) {
  return mode === "heat" ? st.tmax_today : st.tmin_today;
}
function stRecords(st) {
  return mode === "heat" ? st.records.high : st.records.low;
}
function statusKey(st) {
  if (stToday(st) === null) return "nodata";
  const s = stStatus(st);
  if (s.level) return s.level;
  if (s.near) return "near";
  return "none";
}
function colors() {
  return mode === "heat" ? HEAT_COLORS : COLD_COLORS;
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
function renderStats() {
  const c = colors();
  const visible = filtered();
  const broken = { alltime: 0, month: 0, quinzaine: 0, day: 0 };
  let near = 0;
  let oldest = null; // {age, name, year}
  for (const st of visible) {
    const s = stStatus(st);
    if (s.level) {
      broken[s.level]++;
      const rec = stRecords(st)[s.level];
      if (rec) {
        const year = Number(rec.date.slice(0, 4));
        const age = new Date().getFullYear() - year;
        if (!oldest || age > oldest.age) oldest = { age, name: st.name, year };
      }
    } else if (s.near) {
      near++;
    }
  }
  const total = broken.alltime + broken.month + broken.quinzaine + broken.day;
  const parts = [];
  parts.push(`<span class="stat"><b>${total}</b> ${mode === "heat" ? "Hitze" : "Kälte"}rekord${total === 1 ? "" : "e"} heute gebrochen</span>`);
  for (const lvl of ["alltime", "month", "quinzaine", "day"]) {
    if (broken[lvl]) parts.push(`<span class="stat"><i style="background:${c[lvl].color}"></i>${broken[lvl]}× ${LEVEL_LABEL[lvl]}</span>`);
  }
  parts.push(`<span class="stat"><i style="background:${c.near.color}"></i><b>${near}</b> nah dran</span>`);
  if (oldest) {
    parts.push(`<span class="stat">ältester gebrochener Rekord: <b>${oldest.name}</b>, von ${oldest.year} (${oldest.age} Jahre)</span>`);
  }
  document.getElementById("stats").innerHTML = parts.join(" · ");
}

// ---- Karte ----
function renderMap() {
  const c = colors();
  for (const st of stations) {
    const m = markers.get(st.id);
    if (view !== "map" || !passesFilter(st)) {
      map.removeLayer(m);
      continue;
    }
    if (!map.hasLayer(m)) m.addTo(map);
    const key = statusKey(st);
    m.setStyle({
      fillColor: c[key].color,
      radius: key === "none" || key === "nodata" ? 4.5 : 7,
    });
    if (key !== "none" && key !== "nodata") m.bringToFront();
  }
}

// ---- Tabelle ----
const COLUMNS = [
  { key: "name", label: "Station", value: (st) => st.name },
  { key: "bundesland", label: "Bundesland", value: (st) => st.bundesland },
  { key: "altitude", label: "Höhe (m)", value: (st) => st.altitude, num: true },
  { key: "today", label: "heute", value: (st) => stToday(st), num: true, fmt: fmtTemp },
  {
    key: "record", label: "Tagesrekord", num: true,
    value: (st) => (stRecords(st).day ? stRecords(st).day.value : null), fmt: fmtTemp,
  },
  { key: "recordAge", label: "Rekord von", value: (st) => recordYear(st), num: true },
  { key: "status", label: "Status", value: (st) => statusKey(st) },
];
const STATUS_ORDER = { alltime: 0, month: 1, quinzaine: 2, day: 3, near: 4, none: 5, nodata: 6 };

function renderTable() {
  const c = colors();
  const rows = filtered().slice().sort((a, b) => {
    const col = COLUMNS.find((x) => x.key === sortKey);
    let va = col.value(a), vb = col.value(b);
    if (sortKey === "status") { va = STATUS_ORDER[va]; vb = STATUS_ORDER[vb]; }
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
    const key = statusKey(st);
    const cells = COLUMNS.map((col) => {
      if (col.key === "status") {
        return `<td><span class="badge" style="background:${c[key].color}">${c[key].label}</span></td>`;
      }
      const v = col.value(st);
      const txt = col.fmt ? col.fmt(v) : v ?? "–";
      return `<td class="${col.num ? "num" : ""}">${txt}</td>`;
    }).join("");
    return `<tr data-id="${st.id}">${cells}</tr>`;
  }).join("");
  document.getElementById("stations-table").innerHTML = `<thead><tr>${head}</tr></thead><tbody>${body}</tbody>`;
}

// ---- Panel ----
function showPanel(st) {
  const c = colors();
  const key = statusKey(st);
  const recs = stRecords(st);
  const rows = [
    ["heutiger Kalendertag", recs.day],
    ["Halbmonat", recs.quinzaine],
    ["laufender Monat", recs.month],
    ["Allzeit", recs.alltime],
  ].filter(([, r]) => r)
    .map(([label, r]) => `<tr><td>${label}</td><td class="val">${fmtTemp(r.value)}</td><td class="date">${fmtDate(r.date)}</td></tr>`)
    .join("");
  document.getElementById("panel-content").innerHTML = `
    <h2>${st.name}</h2>
    <div class="meta">${st.bundesland} · ${st.altitude} m · Daten seit ${st.first_year}</div>
    <span class="badge" style="background:${c[key].color}">${c[key].label}</span>
    <div class="today-vals">
      <span class="hot">▲ ${fmtTemp(st.tmax_today)}</span>
      <span class="cold">▼ ${fmtTemp(st.tmin_today)}</span>
    </div>
    <div class="meta">heutiges Max/Min${st.last_measurement ? ", letzte Messung " + st.last_measurement.slice(11, 16) + " Uhr" : ""}</div>
    <table>
      <tr><th>${mode === "heat" ? "Hitzerekorde" : "Kälterekorde"}</th><th></th><th></th></tr>
      ${rows}
    </table>`;
  document.getElementById("panel").classList.remove("hidden");
}

function renderLegend() {
  const c = colors();
  document.getElementById("legend").innerHTML = ["alltime", "month", "quinzaine", "day", "near", "none", "nodata"]
    .map((k) => `<span><i style="background:${c[k].color}"></i>${c[k].label}</span>`)
    .join("");
}

function render() {
  document.getElementById("map").classList.toggle("hidden", view !== "map");
  document.getElementById("table-view").classList.toggle("hidden", view !== "table");
  renderStats();
  renderLegend();
  if (view === "map") renderMap();
  else renderTable();
}

async function load() {
  const resp = await fetch("api/stations");
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
for (const id of ["filter-land", "filter-age"]) {
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

load();
setInterval(load, 5 * 60 * 1000);
