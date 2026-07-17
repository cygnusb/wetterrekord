"""Import: select stations, download history, write records to the DB.

Usage: python -m wetterrekord.ingest [--limit N] [--daemon]

--daemon runs forever (separate container): an immediate ingest when the
database is empty, then one run per day at INGEST_HOUR:30 local time.
"""

import argparse
import logging
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from . import config, db
from .dwd import DwdClient, StationInfo
from .records import StationRecords, compute_records

log = logging.getLogger(__name__)


def select_stations(client: DwdClient) -> list[StationInfo]:
    """Active stations with live data and a sufficiently long history."""
    live_ids = {s.id for s in client.tu_now_stations()}
    cutoff_active = date.today() - timedelta(days=14)
    selected = []
    for s in client.kl_stations():
        if s.id not in live_ids:
            continue
        if s.bis < cutoff_active:
            continue
        if (s.bis - s.von).days < config.MIN_YEARS * 365:
            continue
        selected.append(s)
    return selected


def store_station(
    conn: sqlite3.Connection, station: StationInfo, records: StationRecords
) -> None:
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO stations VALUES (?,?,?,?,?,?,?,?)",
            (
                station.id,
                station.name,
                station.bundesland,
                station.lat,
                station.lon,
                station.altitude,
                records.first_year,
                records.last_year,
            ),
        )
        for (param, kind, month, day), rec in records.daily.items():
            conn.execute(
                "INSERT OR REPLACE INTO daily_records VALUES (?,?,?,?,?,?,?)",
                (station.id, param, month, day, kind, rec.value, rec.record_date.isoformat()),
            )
        for (param, kind, month, half), rec in records.quinzaine.items():
            conn.execute(
                "INSERT OR REPLACE INTO quinzaine_records VALUES (?,?,?,?,?,?,?)",
                (station.id, param, month, half, kind, rec.value, rec.record_date.isoformat()),
            )
        for (param, kind, month), rec in records.monthly.items():
            conn.execute(
                "INSERT OR REPLACE INTO monthly_records VALUES (?,?,?,?,?,?)",
                (station.id, param, month, kind, rec.value, rec.record_date.isoformat()),
            )
        for (param, kind), rec in records.alltime.items():
            conn.execute(
                "INSERT OR REPLACE INTO alltime_records VALUES (?,?,?,?,?)",
                (station.id, param, kind, rec.value, rec.record_date.isoformat()),
            )


def ingest(limit: int | None = None) -> None:
    client = DwdClient()
    conn = db.connect()
    # marker on the shared volume: the app blocks the frontend with a notice
    # while the rebuild runs, because records are inconsistent in between
    config.INGEST_MARKER.write_text(datetime.now(ZoneInfo(config.LOCAL_TZ)).isoformat())
    try:
        stations = select_stations(client)
        if limit:
            stations = stations[:limit]
        log.info("%d stations selected", len(stations))

        def process(station: StationInfo) -> tuple[StationInfo, StationRecords]:
            return station, compute_records(client.daily_values(station.id), station.altitude)

        done = failed = 0
        with ThreadPoolExecutor(max_workers=config.DOWNLOAD_CONCURRENCY) as pool:
            futures = [pool.submit(process, s) for s in stations]
            for future in as_completed(futures):
                try:
                    station, records = future.result()
                except Exception:
                    failed += 1
                    log.exception("station failed")
                    continue
                if records.first_year is None:
                    failed += 1
                    continue
                store_station(conn, station, records)
                done += 1
                if done % 25 == 0:
                    log.info("%d/%d stations imported", done, len(stations))
        log.info("done: %d imported, %d failed", done, failed)
    finally:
        config.INGEST_MARKER.unlink(missing_ok=True)
        client.close()
        conn.close()


def next_run(now: datetime) -> datetime:
    """Next INGEST_HOUR:30 local time strictly after `now`."""
    run = now.replace(hour=config.INGEST_HOUR, minute=30, second=0, microsecond=0)
    if run <= now:
        run += timedelta(days=1)
    return run


def daemon() -> None:
    """Ingest on a fresh database, then daily at INGEST_HOUR:30.

    Failures are logged and the loop continues — the DWD being unreachable
    tonight must not crash-loop the container.
    """
    conn = db.connect()
    initial = db.needs_ingest(conn)
    conn.close()
    if initial:
        log.info("database empty — running initial ingest")
        try:
            ingest()
        except Exception:
            log.exception("initial ingest failed")
    tz = ZoneInfo(config.LOCAL_TZ)
    while True:
        now = datetime.now(tz)
        run_at = next_run(now)
        log.info("next ingest at %s", run_at.isoformat())
        time.sleep(max(0.0, (run_at - now).total_seconds()))
        try:
            ingest()
        except Exception:
            log.exception("ingest failed")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Import DWD history and compute records")
    parser.add_argument("--limit", type=int, help="only the first N stations (for testing)")
    parser.add_argument(
        "--daemon", action="store_true", help="run forever, one ingest per day"
    )
    args = parser.parse_args()
    if args.daemon:
        daemon()
    else:
        ingest(limit=args.limit)


if __name__ == "__main__":
    main()
