"""Live poller: fetch 10-minute data (temperature/pressure, gusts,
precipitation) and maintain today's aggregates per station."""

import logging
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import config
from .dwd import DwdClient

log = logging.getLogger(__name__)

TZ = ZoneInfo(config.LOCAL_TZ)

# Keep a bit more than the 30 days the timeline can go back.
MEASUREMENT_RETENTION_HOURS = 31 * 24


def poll_station(client: DwdClient, station_id: str) -> tuple | None:
    """Today's merged readings and aggregates of one station — or None.

    Returns (date, tmax, tmin, gust_max, rain_sum, pp_mean, last_ts, rows)
    where rows is [(ts, tt, fx, rr, pp)].
    """
    today = datetime.now(TZ).date()
    merged: dict[datetime, list] = {}  # local ts -> [tt, fx, rr, pp]

    def add(series, slots):
        for ts_utc, vals in series:
            ts = ts_utc.astimezone(TZ)
            if ts.date() != today:
                continue
            row = merged.setdefault(ts, [None, None, None, None])
            for slot, val in zip(slots, vals):
                if val is not None:
                    row[slot] = val

    add(client.now_values(station_id), (0, 3))  # TT_10, PP_10
    add(client.now_gusts(station_id), (1,))  # FX_10
    add(client.now_precip(station_id), (2,))  # RWS_10
    if not merged:
        return None

    def series(slot):
        return [row[slot] for row in merged.values() if row[slot] is not None]

    temps, gusts, rains, pressures = series(0), series(1), series(2), series(3)
    return (
        today.isoformat(),
        max(temps) if temps else None,
        min(temps) if temps else None,
        max(gusts) if gusts else None,
        round(sum(rains), 1) if rains else None,
        round(sum(pressures) / len(pressures), 1) if pressures else None,
        max(merged),
        sorted((ts, *row) for ts, row in merged.items()),
    )


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
        for sid, day, tmax, tmin, gust, rain, pp, last_ts, rows in results:
            conn.execute(
                "INSERT OR REPLACE INTO live_state VALUES (?,?,?,?,?,?,?,?)",
                (sid, day, tmax, tmin, gust, rain, pp, last_ts.isoformat()),
            )
            conn.executemany(
                "INSERT OR REPLACE INTO measurements VALUES (?,?,?,?,?,?)",
                [(sid, ts.isoformat(), tt, fx, rr, ppv) for ts, tt, fx, rr, ppv in rows],
            )
        conn.execute("DELETE FROM measurements WHERE ts < ?", (cutoff,))
    log.info("Live poll done: %d/%d stations with data for today", len(results), len(station_ids))
    if own_client:
        client.close()
