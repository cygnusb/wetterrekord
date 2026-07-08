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
    param TEXT NOT NULL,
    month INTEGER NOT NULL,
    day INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('high', 'low')),
    value REAL NOT NULL,
    record_date TEXT NOT NULL,
    PRIMARY KEY (station_id, param, month, day, kind)
);
CREATE TABLE IF NOT EXISTS quinzaine_records (
    station_id TEXT NOT NULL,
    param TEXT NOT NULL,
    month INTEGER NOT NULL,
    half INTEGER NOT NULL CHECK (half IN (1, 2)),
    kind TEXT NOT NULL CHECK (kind IN ('high', 'low')),
    value REAL NOT NULL,
    record_date TEXT NOT NULL,
    PRIMARY KEY (station_id, param, month, half, kind)
);
CREATE TABLE IF NOT EXISTS monthly_records (
    station_id TEXT NOT NULL,
    param TEXT NOT NULL,
    month INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('high', 'low')),
    value REAL NOT NULL,
    record_date TEXT NOT NULL,
    PRIMARY KEY (station_id, param, month, kind)
);
CREATE TABLE IF NOT EXISTS alltime_records (
    station_id TEXT NOT NULL,
    param TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('high', 'low')),
    value REAL NOT NULL,
    record_date TEXT NOT NULL,
    PRIMARY KEY (station_id, param, kind)
);
CREATE TABLE IF NOT EXISTS measurements (
    station_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    tt REAL,
    fx REAL,
    rr REAL,
    pp REAL,
    PRIMARY KEY (station_id, ts)
);
CREATE TABLE IF NOT EXISTS live_state (
    station_id TEXT PRIMARY KEY,
    date TEXT NOT NULL,
    tmax_today REAL,
    tmin_today REAL,
    gust_today REAL,
    rain_today REAL,
    pp_today REAL,
    last_measurement_at TEXT NOT NULL
);
"""

# v1 (< 0.7.0) tables that need a rebuild: record tables gained the param
# primary-key column, measurements/live_state gained nullable value columns.
_V1_MARKERS = {
    "daily_records": "param",
    "quinzaine_records": "param",
    "monthly_records": "param",
    "alltime_records": "param",
    "measurements": "fx",
    "live_state": "gust_today",
}

_V1_COPY = {
    "daily_records": "SELECT station_id, 'temp', month, day, kind, value, record_date FROM {}",
    "quinzaine_records": "SELECT station_id, 'temp', month, half, kind, value, record_date FROM {}",
    "monthly_records": "SELECT station_id, 'temp', month, kind, value, record_date FROM {}",
    "alltime_records": "SELECT station_id, 'temp', kind, value, record_date FROM {}",
    "measurements": "SELECT station_id, ts, tt, NULL, NULL, NULL FROM {}",
    "live_state": (
        "SELECT station_id, date, tmax_today, tmin_today, NULL, NULL, NULL,"
        " last_measurement_at FROM {}"
    ),
}


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]


def _migrate(conn: sqlite3.Connection) -> None:
    """Rename v1 tables aside, recreate with the current schema, copy data over."""
    legacy = [
        table
        for table, marker in _V1_MARKERS.items()
        if _columns(conn, table) and marker not in _columns(conn, table)
    ]
    for table in legacy:
        conn.execute(f"ALTER TABLE {table} RENAME TO {table}_v1")
    conn.executescript(SCHEMA)
    with conn:
        for table in legacy:
            conn.execute(f"INSERT INTO {table} {_V1_COPY[table].format(table + '_v1')}")
            conn.execute(f"DROP TABLE {table}_v1")


def connect(path: Path | None = None) -> sqlite3.Connection:
    target = path or config.DB_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    _migrate(conn)
    return conn
