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


@app.get("/api/stations")
def api_stations():
    now_local = datetime.now(ZoneInfo(config.LOCAL_TZ))
    today = now_local.date()
    month, day = today.month, today.day
    half = 1 if day <= 15 else 2

    live_rows = {
        r["station_id"]: r
        for r in conn.execute("SELECT * FROM live_state WHERE date = ?", (today.isoformat(),))
    }
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
        lr = live_rows.get(sid)
        tmax_today = lr["tmax_today"] if lr else None
        tmin_today = lr["tmin_today"] if lr else None
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
                "last_measurement": lr["last_measurement_at"] if lr else None,
                "records": {"high": high, "low": low},
                "heat": _status(tmax_today, high, "heat"),
                "cold": _status(tmin_today, low, "cold"),
            }
        )
    return {"date": today.isoformat(), "generated_at": now_local.isoformat(), "stations": stations}


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


@app.get("/", response_class=HTMLResponse)
def index():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    return html.replace("{{BASE_URL}}", config.BASE_URL)


@app.get("/robots.txt", response_class=PlainTextResponse)
def robots_txt():
    return f"User-agent: *\nAllow: /\nDisallow: /api/\nSitemap: {config.BASE_URL}/sitemap.xml\n"


@app.get("/sitemap.xml")
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
