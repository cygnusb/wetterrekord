import sqlite3
from pathlib import Path

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS stations (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    bundesland TEXT NOT NULL,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    altitude INTEGER NOT NULL,
    first_year INTEGER NOT NULL,
    last_year INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS daily_records (
    station_id TEXT NOT NULL,
    month INTEGER NOT NULL,
    day INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('high', 'low')),
    value REAL NOT NULL,
    record_date TEXT NOT NULL,
    PRIMARY KEY (station_id, month, day, kind)
);
CREATE TABLE IF NOT EXISTS quinzaine_records (
    station_id TEXT NOT NULL,
    month INTEGER NOT NULL,
    half INTEGER NOT NULL CHECK (half IN (1, 2)),
    kind TEXT NOT NULL CHECK (kind IN ('high', 'low')),
    value REAL NOT NULL,
    record_date TEXT NOT NULL,
    PRIMARY KEY (station_id, month, half, kind)
);
CREATE TABLE IF NOT EXISTS monthly_records (
    station_id TEXT NOT NULL,
    month INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('high', 'low')),
    value REAL NOT NULL,
    record_date TEXT NOT NULL,
    PRIMARY KEY (station_id, month, kind)
);
CREATE TABLE IF NOT EXISTS alltime_records (
    station_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('high', 'low')),
    value REAL NOT NULL,
    record_date TEXT NOT NULL,
    PRIMARY KEY (station_id, kind)
);
CREATE TABLE IF NOT EXISTS measurements (
    station_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    tt REAL NOT NULL,
    PRIMARY KEY (station_id, ts)
);
CREATE TABLE IF NOT EXISTS live_state (
    station_id TEXT PRIMARY KEY,
    date TEXT NOT NULL,
    tmax_today REAL NOT NULL,
    tmin_today REAL NOT NULL,
    last_measurement_at TEXT NOT NULL
);
"""


def connect(path: Path | None = None) -> sqlite3.Connection:
    target = path or config.DB_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn
