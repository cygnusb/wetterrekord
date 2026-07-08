"""Live poller: fetch 10-minute data and maintain today's max/min per station."""

import logging
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import config
from .dwd import DwdClient

log = logging.getLogger(__name__)

TZ = ZoneInfo(config.LOCAL_TZ)

# Keep a bit more than the 48 h the timeline can go back.
MEASUREMENT_RETENTION_HOURS = 50


def poll_station(
    client: DwdClient, station_id: str
) -> tuple[str, float, float, datetime, list[tuple[datetime, float]]] | None:
    """Return (local date, tmax, tmin, last measurement, today's readings) — or None."""
    values = [(ts.astimezone(TZ), tt) for ts, tt in client.now_values(station_id)]
    today = datetime.now(TZ).date()
    todays = [(ts, tt) for ts, tt in values if ts.date() == today]
    if not todays:
        return None
    temps = [tt for _, tt in todays]
    last_ts = max(ts for ts, _ in todays)
    return today.isoformat(), max(temps), min(temps), last_ts, todays


def poll_all(conn: sqlite3.Connection, client: DwdClient | None = None) -> None:
    own_client = client is None
    client = client or DwdClient()
    station_ids = [row["id"] for row in conn.execute("SELECT id FROM stations")]
    log.info("Live poll for %d stations", len(station_ids))

    results = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(poll_station, client, sid): sid for sid in station_ids}
        for future in as_completed(futures):
            sid = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                log.warning("Station %s: %s", sid, exc)
                continue
            if result:
                results.append((sid, *result))

    cutoff = (datetime.now(TZ) - timedelta(hours=MEASUREMENT_RETENTION_HOURS)).isoformat()
    with conn:
        for sid, day, tmax, tmin, last_ts, todays in results:
            conn.execute(
                "INSERT OR REPLACE INTO live_state VALUES (?,?,?,?,?)",
                (sid, day, tmax, tmin, last_ts.isoformat()),
            )
            conn.executemany(
                "INSERT OR REPLACE INTO measurements VALUES (?,?,?)",
                [(sid, ts.isoformat(), tt) for ts, tt in todays],
            )
        conn.execute("DELETE FROM measurements WHERE ts < ?", (cutoff,))
    log.info("Live poll done: %d/%d stations with data for today", len(results), len(station_ids))
    if own_client:
        client.close()
