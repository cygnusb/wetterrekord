import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = Path(os.environ.get("RECORDPY_DATA_DIR", BASE_DIR / "data"))
CACHE_DIR = DATA_DIR / "cache"
DB_PATH = DATA_DIR / "recordpy.sqlite"

DWD_BASE = "https://opendata.dwd.de/climate_environment/CDC/observations_germany/climate"
DAILY_KL_HISTORICAL = f"{DWD_BASE}/daily/kl/historical/"
DAILY_KL_RECENT = f"{DWD_BASE}/daily/kl/recent/"
TU_NOW = f"{DWD_BASE}/10_minutes/air_temperature/now/"

KL_STATIONS_FILE = "KL_Tageswerte_Beschreibung_Stationen.txt"
TU_NOW_STATIONS_FILE = "zehn_now_tu_Beschreibung_Stationen.txt"

# Stations with less than this many years of daily-value history are ignored.
MIN_YEARS = 30
# Within this distance to a record, a station gets the "near" status.
NEAR_RECORD_DELTA = 1.0

LOCAL_TZ = "Europe/Berlin"

# Public base URL, used for canonical link, sitemap and Open Graph tags.
BASE_URL = os.environ.get("RECORDPY_BASE_URL", "https://recordpy.w359.de").rstrip("/")

DOWNLOAD_CONCURRENCY = 4
LIVE_POLL_MINUTES = int(os.environ.get("RECORDPY_LIVE_POLL_MINUTES", "15"))
# The DWD updates daily/kl recent only once per day — a daily ingest is
# enough to keep the records current.
INGEST_HOUR = int(os.environ.get("RECORDPY_INGEST_HOUR", "4"))
