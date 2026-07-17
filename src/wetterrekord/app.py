"""FastAPI app: API for the map + static frontend + live scheduler."""

import importlib.metadata
import logging
import sqlite3
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from . import config, db, live, ogimage

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"

try:
    VERSION = importlib.metadata.version("wetterrekord")
except importlib.metadata.PackageNotFoundError:
    VERSION = "dev"

conn: sqlite3.Connection | None = None
# Request handlers run in the threadpool; sharing one sqlite3 connection
# across threads (and with the scheduler) interleaves cursors under load —
# statements then return no rows. Each request thread gets its own
# connection instead (WAL mode allows concurrent readers).
_request_conns = threading.local()


def request_conn() -> sqlite3.Connection:
    if not hasattr(_request_conns, "conn"):
        _request_conns.conn = db.connect()
    return _request_conns.conn
# The (re-)ingest of the DWD history runs in a separate container
# (python -m wetterrekord.ingest --daemon) and signals via a marker file on
# the shared data volume; the frontend blocks with a notice because records
# are inconsistent during the rebuild. Markers older than this are leftovers
# from a crashed ingest and are ignored.
INGEST_MARKER_MAX_AGE = 3 * 3600


def ingest_running() -> bool:
    try:
        age = time.time() - config.INGEST_MARKER.stat().st_mtime
    except OSError:
        return False
    return age < INGEST_MARKER_MAX_AGE


@asynccontextmanager
async def lifespan(app: FastAPI):
    global conn
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    conn = db.connect()
    scheduler = BackgroundScheduler(timezone=config.LOCAL_TZ)
    # next_run_time must be timezone-aware: APScheduler interprets naive
    # datetimes in the scheduler timezone — in a UTC container the job is
    # then considered misfired and gets discarded.
    now = datetime.now(ZoneInfo(config.LOCAL_TZ))
    scheduler.add_job(live.poll_all, args=[conn], next_run_time=now, misfire_grace_time=None)
    scheduler.add_job(
        live.poll_all,
        "interval",
        args=[conn],
        minutes=config.LIVE_POLL_MINUTES,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=None,
    )
    scheduler.start()
    yield
    scheduler.shutdown(wait=False)
    conn.close()


app = FastAPI(title="wetterrekord.de", lifespan=lifespan)

# unpkg: Leaflet (mit SRI in index.html); tile.openstreetmap.de: Basiskarte;
# data:/blob:: Favicon und die als Data-URL erzeugte Interpolations-Farbfläche.
# 'unsafe-inline' nur für Styles: Leaflet und das UI setzen style-Attribute.
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self' https://unpkg.com; "
        "style-src 'self' https://unpkg.com 'unsafe-inline'; "
        "img-src 'self' data: blob: https://tile.openstreetmap.de https://unpkg.com; "
        "connect-src 'self'; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "form-action 'self'; "
        "frame-ancestors 'self'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "SAMEORIGIN",
    "Referrer-Policy": "strict-origin",
}


@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers.update(SECURITY_HEADERS)
    return response


def _record(row) -> dict | None:
    if row is None:
        return None
    return {"value": row["value"], "date": row["record_date"]}


STATUS_LEVELS = ("alltime", "month", "quinzaine", "day")


def _status(
    today_val: float | None,
    records: dict[str, dict | None],
    kind: str,
    near_delta: float = config.NEAR_RECORD_DELTA,
) -> dict:
    """Compare today's value against the record levels (highest level wins).

    kind="heat": record broken if today's value is greater.
    kind="cold": record broken if smaller.
    Returns {"level": broken level or None, "near": highest level within
    near_delta or None}.
    """
    result = {"level": None, "near": None}
    if today_val is None:
        return result
    sign = 1 if kind == "heat" else -1
    for level in STATUS_LEVELS:
        rec = records.get(level)
        if rec is None:
            continue
        diff = sign * (today_val - rec["value"])
        if diff >= 0:
            result["level"] = level
            return result
        if result["near"] is None and diff >= -near_delta:
            result["near"] = level
    return result


def past_values(db_conn: sqlite3.Connection, at: datetime) -> dict[str, tuple]:
    """Per-station (tmax, tmin, gust, rain, pp, last ts) from stored
    measurements between the local start of `at`'s day and `at`."""
    day_start = at.replace(hour=0, minute=0, second=0, microsecond=0)
    return {
        r["station_id"]: (
            r["mx"],
            r["mn"],
            r["gust"],
            round(r["rain"], 1) if r["rain"] is not None else None,
            round(r["pp"], 1) if r["pp"] is not None else None,
            r["last_ts"],
        )
        for r in db_conn.execute(
            "SELECT station_id, MAX(tt) mx, MIN(tt) mn, MAX(fx) gust,"
            " SUM(rr) rain, AVG(pp) pp, MAX(ts) last_ts"
            " FROM measurements WHERE ts >= ? AND ts <= ? GROUP BY station_id",
            (day_start.isoformat(), at.isoformat()),
        )
    }


def latest_measurements(db_conn: sqlite3.Connection, at: datetime) -> dict[str, dict]:
    """Latest stored measurement row per station on `at`'s local calendar day."""
    day_start = at.replace(hour=0, minute=0, second=0, microsecond=0)
    return {
        r["station_id"]: {
            "ts": r["ts"],
            "tt": r["tt"],
            "fx": r["fx"],
            "rr": r["rr"],
            "pp": r["pp"],
        }
        for r in db_conn.execute(
            "SELECT m.* FROM measurements m "
            "JOIN (SELECT station_id, MAX(ts) ts FROM measurements"
            " WHERE ts >= ? AND ts <= ? GROUP BY station_id) latest "
            "ON latest.station_id = m.station_id AND latest.ts = m.ts",
            (day_start.isoformat(), at.isoformat()),
        )
    }


@app.get("/api/stations")
def api_stations(at: str | None = None):
    c = request_conn()
    tz = ZoneInfo(config.LOCAL_TZ)
    now_local = datetime.now(tz)
    if at is not None:
        try:
            at_dt = datetime.fromisoformat(at)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid 'at' timestamp")
        if at_dt.tzinfo is None:
            at_dt = at_dt.replace(tzinfo=tz)
        # normalize to local time: the local calendar day decides which
        # daily/half-month records apply and where the day starts
        at_dt = at_dt.astimezone(tz)
        if at_dt > now_local:
            raise HTTPException(status_code=400, detail="'at' must not be in the future")
        values = past_values(c, at_dt)
        latest = latest_measurements(c, at_dt)
        target_date = at_dt.date()
        generated_at = at_dt
    else:
        target_date = now_local.date()
        generated_at = now_local
        values = {
            r["station_id"]: (
                r["tmax_today"],
                r["tmin_today"],
                r["gust_today"],
                r["rain_today"],
                r["pp_today"],
                r["last_measurement_at"],
            )
            for r in c.execute(
                "SELECT * FROM live_state WHERE date = ?", (target_date.isoformat(),)
            )
        }
        latest = latest_measurements(c, now_local)

    month, day = target_date.month, target_date.day
    half = 1 if day <= 15 else 2

    daily = {
        (r["station_id"], r["param"], r["kind"]): r
        for r in c.execute(
            "SELECT * FROM daily_records WHERE month = ? AND day = ?", (month, day)
        )
    }
    quinzaine = {
        (r["station_id"], r["param"], r["kind"]): r
        for r in c.execute(
            "SELECT * FROM quinzaine_records WHERE month = ? AND half = ?", (month, half)
        )
    }
    monthly = {
        (r["station_id"], r["param"], r["kind"]): r
        for r in c.execute("SELECT * FROM monthly_records WHERE month = ?", (month,))
    }
    alltime = {
        (r["station_id"], r["param"], r["kind"]): r
        for r in c.execute("SELECT * FROM alltime_records")
    }

    def levels(sid: str, param: str, kind: str) -> dict:
        return {
            "day": _record(daily.get((sid, param, kind))),
            "quinzaine": _record(quinzaine.get((sid, param, kind))),
            "month": _record(monthly.get((sid, param, kind))),
            "alltime": _record(alltime.get((sid, param, kind))),
        }

    def param_block(sid: str, param: str, kind: str, status_kind: str, value) -> dict:
        recs = levels(sid, param, kind)
        return {
            "value": value,
            "records": recs,
            "status": _status(value, recs, status_kind, config.NEAR_DELTA[param]),
        }

    stations = []
    for s in c.execute("SELECT * FROM stations"):
        sid = s["id"]
        tmax_today, tmin_today, gust, rain, pp, last_measurement = values.get(
            sid, (None, None, None, None, None, None)
        )
        high = levels(sid, "temp", "high")
        low = levels(sid, "temp", "low")
        stations.append(
            {
                "id": sid,
                "name": s["name"],
                "bundesland": s["bundesland"],
                "lat": s["lat"],
                "lon": s["lon"],
                "altitude": s["altitude"],
                "first_year": s["first_year"],
                "last_year": s["last_year"],
                "history_years": s["last_year"] - s["first_year"] + 1,
                "tmax_today": tmax_today,
                "tmin_today": tmin_today,
                "last_measurement": last_measurement,
                "now": latest.get(sid),
                "records": {"high": high, "low": low},
                "heat": _status(tmax_today, high, "heat"),
                "cold": _status(tmin_today, low, "cold"),
                "params": {
                    "gust": param_block(sid, "gust", "high", "heat", gust),
                    "precip": param_block(sid, "precip", "high", "heat", rain),
                    "phigh": param_block(sid, "pressure", "high", "heat", pp),
                    "plow": param_block(sid, "pressure", "low", "cold", pp),
                },
            }
        )
    return {
        "date": target_date.isoformat(),
        "generated_at": generated_at.isoformat(),
        # oldest stored measurement — the frontend limits the timeline to this
        "history_start": c.execute("SELECT MIN(ts) FROM measurements").fetchone()[0],
        "ingest_running": ingest_running(),
        "stations": stations,
    }


@app.get("/api/stations/{station_id}")
def api_station_detail(station_id: str):
    c = request_conn()
    s = c.execute("SELECT * FROM stations WHERE id = ?", (station_id,)).fetchone()
    if s is None:
        raise HTTPException(status_code=404, detail="unknown station")
    monthly = [
        {
            "param": r["param"],
            "month": r["month"],
            "kind": r["kind"],
            "value": r["value"],
            "date": r["record_date"],
        }
        for r in c.execute(
            "SELECT * FROM monthly_records WHERE station_id = ? ORDER BY param, month",
            (station_id,),
        )
    ]
    alltime = [
        {"param": r["param"], "kind": r["kind"], "value": r["value"], "date": r["record_date"]}
        for r in c.execute("SELECT * FROM alltime_records WHERE station_id = ?", (station_id,))
    ]
    lr = c.execute("SELECT * FROM live_state WHERE station_id = ?", (station_id,)).fetchone()
    return {
        "id": s["id"],
        "name": s["name"],
        "bundesland": s["bundesland"],
        "lat": s["lat"],
        "lon": s["lon"],
        "altitude": s["altitude"],
        "first_year": s["first_year"],
        "last_year": s["last_year"],
        "monthly_records": monthly,
        "alltime_records": alltime,
        "live": dict(lr) if lr else None,
    }


# Germany outline for the frontend's interpolation overlay (clip path).
# Shares the asset used by the OG image renderer.
@app.get("/germany.geo.json")
def germany_geojson():
    return Response(
        content=(ogimage.ASSETS / "germany.geo.json").read_bytes(),
        media_type="application/geo+json",
        headers={"Cache-Control": "public, max-age=86400"},
    )


# The OG image is rendered from the live records so shared links show the
# current day. Cached for the live-poll interval; the route shadows the
# StaticFiles mount, so no static og-image.png must exist.
_og_cache: tuple[float, bytes] | None = None
# serialize rendering: parallel requests on an expired cache would otherwise
# each render their own PNG (CPU-bound, easy request amplification)
_og_lock = threading.Lock()


@app.api_route("/og-image.png", methods=["GET", "HEAD"])
def og_image():
    global _og_cache
    with _og_lock:
        if _og_cache is None or time.time() - _og_cache[0] > config.LIVE_POLL_MINUTES * 60:
            _og_cache = (time.time(), ogimage.render(api_stations()))
        content = _og_cache[1]
    return Response(
        content=content,
        media_type="image/png",
        headers={"Cache-Control": f"public, max-age={config.LIVE_POLL_MINUTES * 60}"},
    )


# methods must include HEAD explicitly: FastAPI's @app.get registers GET only,
# and HEAD requests would fall through to the StaticFiles 404.
@app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
def index():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    imprint_link = (
        '<a href="impressum">Impressum &amp; Datenschutz</a> · ' if config.IMPRINT_HTML else ""
    )
    html = (
        html.replace("{{BASE_URL}}", config.BASE_URL)
        .replace("{{IMPRINT_LINK}}", imprint_link)
        .replace("{{VERSION}}", VERSION)
        .replace("{{MIN_YEARS}}", str(config.MIN_YEARS))
    )
    # no-cache (= revalidate, not "don't store"): the asset URLs carry the
    # version, so a fresh index is all it takes for deploys to reach browsers
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


IMPRINT_PAGE = """<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Impressum &amp; Datenschutz | wetterrekord.de</title>
<style>
body {{ background: #14181f; color: #e8e8e8; font-family: -apple-system, "Segoe UI", Roboto, sans-serif;
       max-width: 46rem; margin: 0 auto; padding: 24px 16px; line-height: 1.6; font-size: 0.95rem; }}
h1 {{ font-size: 1.4rem; }} h2 {{ font-size: 1.1rem; margin-top: 2em; }}
a {{ color: #8ab4f8; }}
</style>
</head>
<body>
<p><a href="./">&larr; zurück zur Karte</a></p>
<h1>Impressum</h1>
{imprint}
<h2>Datenschutzerklärung</h2>
<p>Verantwortlich für die Datenverarbeitung auf dieser Website ist der oben im
Impressum genannte Betreiber.</p>
<p><strong>Server-Logfiles:</strong> Beim Aufruf dieser Website speichert der
Webserver automatisch Zugriffsdaten (IP-Adresse, Zeitpunkt, aufgerufene URL,
User-Agent). Diese Daten dienen ausschließlich dem technischen Betrieb und der
Fehleranalyse (Art. 6 Abs. 1 lit. f DSGVO) und werden nach kurzer Zeit
gelöscht.</p>
<p><strong>Cookies und Tracking:</strong> Diese Website verwendet keine Cookies
und kein Tracking. Im lokalen Speicher des Browsers wird lediglich ein
technisches Merkmal ohne Personenbezug abgelegt (ob der Einführungstext bereits
angezeigt wurde).</p>
<p><strong>Externe Inhalte:</strong> Die Kartendarstellung lädt Kartenkacheln
von CARTO (auf Basis von OpenStreetMap) sowie die Bibliothek Leaflet vom
CDN unpkg.com. Dabei wird Ihre IP-Adresse an die jeweiligen Anbieter
übertragen.</p>
<p><strong>Datenquelle:</strong> Die dargestellten Wetterdaten stammen vom
Deutschen Wetterdienst (DWD, CDC Open Data) und enthalten keine
personenbezogenen Daten.</p>
<p><strong>Ihre Rechte:</strong> Sie haben nach der DSGVO das Recht auf
Auskunft, Berichtigung, Löschung, Einschränkung der Verarbeitung, Widerspruch
und Datenübertragbarkeit sowie das Recht auf Beschwerde bei einer
Datenschutz-Aufsichtsbehörde.</p>
</body>
</html>
"""


@app.api_route("/impressum", methods=["GET", "HEAD"], response_class=HTMLResponse)
def impressum():
    if not config.IMPRINT_HTML:
        raise HTTPException(status_code=404, detail="not configured")
    return IMPRINT_PAGE.format(imprint=config.IMPRINT_HTML)


@app.api_route("/robots.txt", methods=["GET", "HEAD"], response_class=PlainTextResponse)
def robots_txt():
    return f"User-agent: *\nAllow: /\nDisallow: /api/\nSitemap: {config.BASE_URL}/sitemap.xml\n"


@app.api_route("/sitemap.xml", methods=["GET", "HEAD"])
def sitemap_xml():
    today = datetime.now(ZoneInfo(config.LOCAL_TZ)).date().isoformat()
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"  <url><loc>{config.BASE_URL}/</loc><lastmod>{today}</lastmod>"
        "<changefreq>hourly</changefreq></url>\n"
        "</urlset>\n"
    )
    return Response(content=xml, media_type="application/xml")


_STATUS_TABLES = (
    "stations",
    "daily_records",
    "quinzaine_records",
    "monthly_records",
    "alltime_records",
    "measurements",
    "live_state",
)


def _format_bytes(size: int) -> str:
    units = ("B", "KB", "MB", "GB")
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{size} B"


def _status_payload() -> dict[str, Any]:
    c = request_conn()
    db_path = config.DB_PATH
    tables = {
        name: c.execute(f"SELECT count(*) FROM {name}").fetchone()[0] for name in _STATUS_TABLES
    }
    meas = c.execute(
        "SELECT MIN(ts) oldest, MAX(ts) newest, count(*) n FROM measurements"
    ).fetchone()
    live_row = c.execute(
        "SELECT count(*) n, MAX(last_measurement_at) latest FROM live_state"
    ).fetchone()
    params = {
        r["param"]: r["n"]
        for r in c.execute(
            "SELECT param, count(*) n FROM alltime_records GROUP BY param ORDER BY param"
        )
    }
    sqlite_files = []
    if db_path.parent.exists():
        for path in sorted(db_path.parent.glob(db_path.name + "*")):
            sqlite_files.append(
                {
                    "name": path.name,
                    "size": path.stat().st_size,
                    "size_human": _format_bytes(path.stat().st_size),
                }
            )
    cache_size = 0
    cache_files = 0
    if config.CACHE_DIR.exists():
        for p in config.CACHE_DIR.rglob("*"):
            if p.is_file():
                cache_files += 1
                cache_size += p.stat().st_size
    return {
        "version": VERSION,
        "ingest_running": ingest_running(),
        "tables": tables,
        "alltime_by_param": params,
        "measurements": {
            "count": meas["n"],
            "oldest": meas["oldest"],
            "newest": meas["newest"],
        },
        "live_state": {"count": live_row["n"], "latest": live_row["latest"]},
        "sqlite_files": sqlite_files,
        "cache": {
            "files": cache_files,
            "size": cache_size,
            "size_human": _format_bytes(cache_size),
        },
        "config": {
            "min_years": config.MIN_YEARS,
            "live_poll_minutes": config.LIVE_POLL_MINUTES,
            "ingest_hour": config.INGEST_HOUR,
            "local_tz": config.LOCAL_TZ,
        },
    }


@app.get("/_status.json")
def status_json():
    """Operational status for monitoring. Put access control in the reverse proxy."""
    return _status_payload()


@app.get("/_status", response_class=HTMLResponse)
def status_page():
    """Unlinked operational status page. Protect via reverse proxy if needed."""
    data = _status_payload()
    rows = "".join(
        f"<tr><th>{key}</th><td>{value}</td></tr>"
        for key, value in (
            ("Version", data["version"]),
            ("Ingest läuft", data["ingest_running"]),
            ("Messungen", data["measurements"]["count"]),
            ("Messungen von", data["measurements"]["oldest"] or "—"),
            ("Messungen bis", data["measurements"]["newest"] or "—"),
            ("Live-Stationen", data["live_state"]["count"]),
            ("Letzte Messung", data["live_state"]["latest"] or "—"),
            ("Cache", f"{data['cache']['files']} Dateien · {data['cache']['size_human']}"),
            (
                "SQLite",
                ", ".join(f"{f['name']} ({f['size_human']})" for f in data["sqlite_files"])
                or "—",
            ),
        )
    )
    table_rows = "".join(
        f"<tr><th>{name}</th><td>{count}</td></tr>" for name, count in data["tables"].items()
    )
    param_rows = "".join(
        f"<tr><th>{param}</th><td>{count}</td></tr>"
        for param, count in data["alltime_by_param"].items()
    )
    return HTMLResponse(
        f"""<!DOCTYPE html>
<html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>Status | wetterrekord.de</title>
<style>
body {{ background:#14181f; color:#e8e8e8; font-family:-apple-system,"Segoe UI",Roboto,sans-serif;
  max-width:48rem; margin:0 auto; padding:24px 16px; line-height:1.5; }}
h1 {{ font-size:1.3rem; }} h2 {{ font-size:1.05rem; margin-top:1.6em; color:#9aa4b5; }}
table {{ border-collapse:collapse; width:100%; font-size:0.9rem; }}
th, td {{ text-align:left; padding:6px 8px; border-bottom:1px solid #2a3140; }}
th {{ color:#9aa4b5; font-weight:500; width:40%; }}
a {{ color:#8ab4f8; }}
</style></head><body>
<p><a href="./">&larr; zurück zur Karte</a> · <a href="/_status.json">JSON</a></p>
<h1>Betriebsstatus</h1>
<table>{rows}</table>
<h2>Tabellen</h2>
<table>{table_rows}</table>
<h2>Allzeit-Rekorde nach Parameter</h2>
<table>{param_rows or "<tr><td colspan=2>—</td></tr>"}</table>
</body></html>"""
    )


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
