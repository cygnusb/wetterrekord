"""FastAPI app: API for the map + static frontend + live scheduler."""

import importlib.metadata
import logging
import sqlite3
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from . import config, db, ingest, live, ogimage

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
# True while the (re-)ingest of the DWD history runs; the frontend blocks
# with a notice because records are inconsistent during the rebuild.
ingest_running = False


def refresh_records() -> None:
    """Recompute the records from the DWD history, then poll immediately."""
    global ingest_running
    ingest_running = True
    try:
        ingest.ingest()
    finally:
        ingest_running = False
    live.poll_all(conn)


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
    # Also re-ingest when a schema upgrade added still-empty records
    # (v0.2: quinzaine table, v0.7: non-temperature parameters) or when the
    # pressure records are still station-level (v0.10: sea-level reduction;
    # mountain stations then have all-time values far below 900 hPa).
    db_empty = (
        conn.execute("SELECT count(*) FROM stations").fetchone()[0] == 0
        or conn.execute("SELECT count(*) FROM quinzaine_records").fetchone()[0] == 0
        or conn.execute(
            "SELECT count(*) FROM quinzaine_records WHERE param != 'temp'"
        ).fetchone()[0]
        == 0
        or conn.execute(
            "SELECT count(*) FROM alltime_records WHERE param = 'pressure' AND value < 900"
        ).fetchone()[0]
        > 0
    )
    if db_empty:
        # First start (e.g. fresh container): load the history in the
        # background; the server is reachable immediately and fills up once
        # the import is done.
        log.info("database empty — starting initial ingest in the background")
        scheduler.add_job(refresh_records, next_run_time=now, misfire_grace_time=None)
    else:
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
    # Daily is enough: the DWD updates the daily/kl recent data only once per day.
    scheduler.add_job(
        refresh_records, "cron", hour=config.INGEST_HOUR, minute=30, misfire_grace_time=None
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
                "tmax_today": tmax_today,
                "tmin_today": tmin_today,
                "last_measurement": last_measurement,
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
        "ingest_running": ingest_running,
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


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
