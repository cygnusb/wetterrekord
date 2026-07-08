"""FastAPI app: API for the map + static frontend + live scheduler."""

import logging
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from . import config, db, ingest, live

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"

conn: sqlite3.Connection | None = None


def refresh_records() -> None:
    """Recompute the records from the DWD history, then poll immediately."""
    ingest.ingest()
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
    # Also re-ingest when a schema upgrade added a still-empty records table.
    db_empty = (
        conn.execute("SELECT count(*) FROM stations").fetchone()[0] == 0
        or conn.execute("SELECT count(*) FROM quinzaine_records").fetchone()[0] == 0
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


app = FastAPI(title="recordpy.de", lifespan=lifespan)


def _record(row) -> dict | None:
    if row is None:
        return None
    return {"value": row["value"], "date": row["record_date"]}


STATUS_LEVELS = ("alltime", "month", "quinzaine", "day")


def _status(
    today_val: float | None, records: dict[str, dict | None], kind: str
) -> dict:
    """Compare today's value against the record levels (highest level wins).

    kind="heat": today_val is tmax, record broken if greater.
    kind="cold": today_val is tmin, record broken if smaller.
    Returns {"level": broken level or None, "near": highest level within
    NEAR_RECORD_DELTA or None}.
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
        if result["near"] is None and diff >= -config.NEAR_RECORD_DELTA:
            result["near"] = level
    return result


def past_values(db_conn: sqlite3.Connection, at: datetime) -> dict[str, tuple]:
    """Per-station (tmax, tmin, last ts) from stored measurements between the
    local start of `at`'s day and `at`."""
    day_start = at.replace(hour=0, minute=0, second=0, microsecond=0)
    return {
        r["station_id"]: (r["mx"], r["mn"], r["last_ts"])
        for r in db_conn.execute(
            "SELECT station_id, MAX(tt) mx, MIN(tt) mn, MAX(ts) last_ts"
            " FROM measurements WHERE ts >= ? AND ts <= ? GROUP BY station_id",
            (day_start.isoformat(), at.isoformat()),
        )
    }


@app.get("/api/stations")
def api_stations(at: str | None = None):
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
        values = past_values(conn, at_dt)
        target_date = at_dt.date()
        generated_at = at_dt
    else:
        target_date = now_local.date()
        generated_at = now_local
        values = {
            r["station_id"]: (r["tmax_today"], r["tmin_today"], r["last_measurement_at"])
            for r in conn.execute(
                "SELECT * FROM live_state WHERE date = ?", (target_date.isoformat(),)
            )
        }

    month, day = target_date.month, target_date.day
    half = 1 if day <= 15 else 2

    daily = {
        (r["station_id"], r["kind"]): r
        for r in conn.execute(
            "SELECT * FROM daily_records WHERE month = ? AND day = ?", (month, day)
        )
    }
    quinzaine = {
        (r["station_id"], r["kind"]): r
        for r in conn.execute(
            "SELECT * FROM quinzaine_records WHERE month = ? AND half = ?", (month, half)
        )
    }
    monthly = {
        (r["station_id"], r["kind"]): r
        for r in conn.execute("SELECT * FROM monthly_records WHERE month = ?", (month,))
    }
    alltime = {
        (r["station_id"], r["kind"]): r for r in conn.execute("SELECT * FROM alltime_records")
    }

    stations = []
    for s in conn.execute("SELECT * FROM stations"):
        sid = s["id"]
        tmax_today, tmin_today, last_measurement = values.get(sid, (None, None, None))
        high = {
            "day": _record(daily.get((sid, "high"))),
            "quinzaine": _record(quinzaine.get((sid, "high"))),
            "month": _record(monthly.get((sid, "high"))),
            "alltime": _record(alltime.get((sid, "high"))),
        }
        low = {
            "day": _record(daily.get((sid, "low"))),
            "quinzaine": _record(quinzaine.get((sid, "low"))),
            "month": _record(monthly.get((sid, "low"))),
            "alltime": _record(alltime.get((sid, "low"))),
        }
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
            }
        )
    return {
        "date": target_date.isoformat(),
        "generated_at": generated_at.isoformat(),
        "stations": stations,
    }


@app.get("/api/stations/{station_id}")
def api_station_detail(station_id: str):
    s = conn.execute("SELECT * FROM stations WHERE id = ?", (station_id,)).fetchone()
    if s is None:
        raise HTTPException(status_code=404, detail="unknown station")
    monthly = [
        {"month": r["month"], "kind": r["kind"], "value": r["value"], "date": r["record_date"]}
        for r in conn.execute(
            "SELECT * FROM monthly_records WHERE station_id = ? ORDER BY month", (station_id,)
        )
    ]
    alltime = [
        {"kind": r["kind"], "value": r["value"], "date": r["record_date"]}
        for r in conn.execute("SELECT * FROM alltime_records WHERE station_id = ?", (station_id,))
    ]
    lr = conn.execute("SELECT * FROM live_state WHERE station_id = ?", (station_id,)).fetchone()
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


# methods must include HEAD explicitly: FastAPI's @app.get registers GET only,
# and HEAD requests would fall through to the StaticFiles 404.
@app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
def index():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    imprint_link = (
        '<a href="impressum">Impressum &amp; Datenschutz</a> · ' if config.IMPRINT_HTML else ""
    )
    return html.replace("{{BASE_URL}}", config.BASE_URL).replace(
        "{{IMPRINT_LINK}}", imprint_link
    )


IMPRINT_PAGE = """<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Impressum &amp; Datenschutz | recordpy.de</title>
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
