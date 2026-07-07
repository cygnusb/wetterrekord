"""Initial import: select stations, download history, write records to the DB.

Usage: python -m recordpy.ingest [--limit N]
"""

import argparse
import logging
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta

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
        for (month, day), rec in records.daily_high.items():
            conn.execute(
                "INSERT OR REPLACE INTO daily_records VALUES (?,?,?,?,?,?)",
                (station.id, month, day, "high", rec.value, rec.record_date.isoformat()),
            )
        for (month, day), rec in records.daily_low.items():
            conn.execute(
                "INSERT OR REPLACE INTO daily_records VALUES (?,?,?,?,?,?)",
                (station.id, month, day, "low", rec.value, rec.record_date.isoformat()),
            )
        for (month, half), rec in records.quinzaine_high.items():
            conn.execute(
                "INSERT OR REPLACE INTO quinzaine_records VALUES (?,?,?,?,?,?)",
                (station.id, month, half, "high", rec.value, rec.record_date.isoformat()),
            )
        for (month, half), rec in records.quinzaine_low.items():
            conn.execute(
                "INSERT OR REPLACE INTO quinzaine_records VALUES (?,?,?,?,?,?)",
                (station.id, month, half, "low", rec.value, rec.record_date.isoformat()),
            )
        for month, rec in records.monthly_high.items():
            conn.execute(
                "INSERT OR REPLACE INTO monthly_records VALUES (?,?,?,?,?)",
                (station.id, month, "high", rec.value, rec.record_date.isoformat()),
            )
        for month, rec in records.monthly_low.items():
            conn.execute(
                "INSERT OR REPLACE INTO monthly_records VALUES (?,?,?,?,?)",
                (station.id, month, "low", rec.value, rec.record_date.isoformat()),
            )
        for kind, rec in (("high", records.alltime_high), ("low", records.alltime_low)):
            if rec:
                conn.execute(
                    "INSERT OR REPLACE INTO alltime_records VALUES (?,?,?,?)",
                    (station.id, kind, rec.value, rec.record_date.isoformat()),
                )


def ingest(limit: int | None = None) -> None:
    client = DwdClient()
    conn = db.connect()
    stations = select_stations(client)
    if limit:
        stations = stations[:limit]
    log.info("%d stations selected", len(stations))

    def process(station: StationInfo) -> tuple[StationInfo, StationRecords]:
        return station, compute_records(client.daily_values(station.id))

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
    client.close()
    conn.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Import DWD history and compute records")
    parser.add_argument("--limit", type=int, help="only the first N stations (for testing)")
    args = parser.parse_args()
    ingest(limit=args.limit)


if __name__ == "__main__":
    main()
