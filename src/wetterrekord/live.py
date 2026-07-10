"""Live poller: fetch 10-minute data (temperature/pressure, gusts,
precipitation) and maintain today's aggregates per station."""

import logging
import math
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import config
from .dwd import DwdClient, reduce_pressure
from .records import PM_PLAUSIBLE

log = logging.getLogger(__name__)

TZ = ZoneInfo(config.LOCAL_TZ)

# Keep a bit more than the 30 days the timeline can go back.
MEASUREMENT_RETENTION_HOURS = 31 * 24


def poll_station(client: DwdClient, station_id: str, altitude: float = 0.0) -> tuple | None:
    """Today's merged readings and aggregates of one station — or None.

    Returns (date, tmax, tmin, gust_max, rain_sum, pp_mean, last_ts, rows)
    where rows is [(ts, tt, fx, rr, pp)]; pp is reduced to sea level.
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

    # PP_10 is station-level pressure; reduce it to sea level with the
    # simultaneous TT_10 (same file, same timestamp). Implausible results
    # (sensor glitches) are dropped like in the historical ingest.
    for row in merged.values():
        if row[3] is not None:
            reduced = (
                round(reduce_pressure(row[3], altitude, row[0]), 1) if row[0] is not None else None
            )
            row[3] = reduced if reduced is not None and PM_PLAUSIBLE[0] <= reduced <= PM_PLAUSIBLE[1] else None

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


# Gust sensor glitches (e.g. Greifswald reporting a lone 302 km/h spike on a
# calm day) are caught by comparing against neighbouring stations: a real
# storm is never that isolated. Only suspiciously high values are checked,
# so genuine local thunderstorm gusts survive.
GUST_SUSPECT_MS = 30.0  # ~108 km/h: below this, never question a value
GUST_NEIGHBOR_KM = 75.0
GUST_NEIGHBOR_ALT_DIFF = 300.0
GUST_FACTOR = 3.5


def _filter_gust_outliers(results: list, meta: dict) -> list:
    """Drop gust spikes that dwarf every comparable neighbour.

    results rows: [sid, day, tmax, tmin, gust, rain, pp, last_ts, rows];
    meta: sid -> (lat, lon, altitude). A suspect maximum needs at least two
    neighbours within GUST_NEIGHBOR_KM (similar altitude); if it exceeds
    GUST_FACTOR x their maximum, the offending 10-minute rows are dropped
    and the daily maximum recomputed.
    """
    gusts = {r[0]: r[4] for r in results}
    filtered = []
    for r in results:
        sid, gust = r[0], r[4]
        if gust is None or gust < GUST_SUSPECT_MS or sid not in meta:
            filtered.append(r)
            continue
        lat, lon, alt = meta[sid]
        kx = 111.3 * math.cos(math.radians(lat))
        neighbors = []
        for other, other_gust in gusts.items():
            if other == sid or other_gust is None or other not in meta:
                continue
            olat, olon, oalt = meta[other]
            if abs(oalt - alt) > GUST_NEIGHBOR_ALT_DIFF:
                continue
            dist = math.hypot((olon - lon) * kx, (olat - lat) * 111.3)
            if dist <= GUST_NEIGHBOR_KM:
                neighbors.append(other_gust)
        if len(neighbors) < 2 or gust <= GUST_FACTOR * max(neighbors):
            filtered.append(r)
            continue
        threshold = GUST_FACTOR * max(neighbors)
        rows = [
            (ts, tt, None if fx is not None and fx > threshold else fx, rr, pp)
            for ts, tt, fx, rr, pp in r[8]
        ]
        valid = [fx for _, _, fx, _, _ in rows if fx is not None]
        log.warning(
            "Station %s: gust %.1f m/s dropped as outlier (neighbour max %.1f m/s)",
            sid, gust, max(neighbors),
        )
        filtered.append([r[0], r[1], r[2], r[3], max(valid) if valid else None, *r[5:8], rows])
    return filtered


def poll_all(conn: sqlite3.Connection, client: DwdClient | None = None) -> None:
    own_client = client is None
    client = client or DwdClient()
    meta = {
        row["id"]: (row["lat"], row["lon"], row["altitude"])
        for row in conn.execute("SELECT id, lat, lon, altitude FROM stations")
    }
    log.info("Live poll for %d stations", len(meta))

    results = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {
            pool.submit(poll_station, client, sid, alt): sid
            for sid, (_, _, alt) in meta.items()
        }
        for future in as_completed(futures):
            sid = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                log.warning("Station %s: %s", sid, exc)
                continue
            if result:
                results.append((sid, *result))
    results = _filter_gust_outliers(results, meta)

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
    log.info("Live poll done: %d/%d stations with data for today", len(results), len(meta))
    if own_client:
        client.close()
