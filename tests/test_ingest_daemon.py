import os
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from wetterrekord import config, db
from wetterrekord.app import INGEST_MARKER_MAX_AGE, ingest_running
from wetterrekord.ingest import next_run

TZ = ZoneInfo("Europe/Berlin")


def test_next_run_before_ingest_hour():
    now = datetime(2026, 7, 16, 2, 0, tzinfo=TZ)
    run = next_run(now)
    assert run == datetime(2026, 7, 16, config.INGEST_HOUR, 30, tzinfo=TZ)


def test_next_run_after_ingest_hour_is_tomorrow():
    now = datetime(2026, 7, 16, config.INGEST_HOUR, 30, 1, tzinfo=TZ)
    run = next_run(now)
    assert run == datetime(2026, 7, 17, config.INGEST_HOUR, 30, tzinfo=TZ)
    assert run > now


def test_ingest_running_marker(tmp_path: Path, monkeypatch):
    marker = tmp_path / "ingest.running"
    monkeypatch.setattr(config, "INGEST_MARKER", marker)

    assert ingest_running() is False  # missing

    marker.write_text(datetime.now(TZ).isoformat())
    assert ingest_running() is True  # fresh

    stale = time.time() - INGEST_MARKER_MAX_AGE - 60
    os.utime(marker, (stale, stale))
    assert ingest_running() is False  # leftover from a crashed ingest


def test_needs_ingest(tmp_path: Path):
    conn = db.connect(tmp_path / "test.sqlite")
    assert db.needs_ingest(conn) is True  # fresh database

    conn.execute(
        "INSERT INTO stations VALUES ('00001', 'Test', 'Hessen', 50.0, 9.0, 100, 1990, 2026)"
    )
    conn.execute(
        "INSERT INTO quinzaine_records VALUES ('00001', 'temp', 7, 1, 'high', 35.0, '2019-07-01')"
    )
    assert db.needs_ingest(conn) is True  # v0.7: only temp records

    conn.execute(
        "INSERT INTO quinzaine_records VALUES ('00001', 'gust', 7, 1, 'high', 30.0, '2019-07-01')"
    )
    assert db.needs_ingest(conn) is False

    # v0.10: station-level pressure record (mountain station below 900 hPa)
    conn.execute(
        "INSERT INTO alltime_records VALUES ('00001', 'pressure', 'high', 750.0, '2019-07-01')"
    )
    assert db.needs_ingest(conn) is True
