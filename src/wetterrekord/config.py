import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = Path(os.environ.get("WETTERREKORD_DATA_DIR", BASE_DIR / "data"))
CACHE_DIR = DATA_DIR / "cache"
DB_PATH = DATA_DIR / "wetterrekord.sqlite"
# Exists while the ingest (separate container) rebuilds the records; the app
# reads it to show the rebuild notice. Shared via the data volume.
INGEST_MARKER = DATA_DIR / "ingest.running"

DWD_BASE = "https://opendata.dwd.de/climate_environment/CDC/observations_germany/climate"
DAILY_KL_HISTORICAL = f"{DWD_BASE}/daily/kl/historical/"
DAILY_KL_RECENT = f"{DWD_BASE}/daily/kl/recent/"
TU_NOW = f"{DWD_BASE}/10_minutes/air_temperature/now/"
# wind gusts (FX_10) live in extreme_wind, NOT in the wind dataset
EXTREME_WIND_NOW = f"{DWD_BASE}/10_minutes/extreme_wind/now/"
PRECIP_NOW = f"{DWD_BASE}/10_minutes/precipitation/now/"

KL_STATIONS_FILE = "KL_Tageswerte_Beschreibung_Stationen.txt"
TU_NOW_STATIONS_FILE = "zehn_now_tu_Beschreibung_Stationen.txt"

# Stations with less than this many years of daily-value history are ignored.
# Non-temperature parameters need the same span in their own series, otherwise
# no records are computed for that parameter at that station.
MIN_YEARS = 30
# Within this distance to a record, a station gets the "near" status.
NEAR_RECORD_DELTA = 1.0
# Per-parameter "near record" distance in the parameter's unit
# (temp °C, gust m/s, precip mm, pressure hPa).
NEAR_DELTA = {"temp": NEAR_RECORD_DELTA, "gust": 2.0, "precip": 5.0, "pressure": 2.0}

LOCAL_TZ = "Europe/Berlin"

# Public base URL, used for canonical link, sitemap and Open Graph tags.
BASE_URL = os.environ.get("WETTERREKORD_BASE_URL", "https://wetterrekord.de").rstrip("/")

# HTML fragment with the site operator's imprint (legal notice). The imprint/
# privacy page and its footer link only appear when this is set.
IMPRINT_HTML = os.environ.get("WETTERREKORD_IMPRINT_HTML", "")

DOWNLOAD_CONCURRENCY = 4
LIVE_POLL_MINUTES = int(os.environ.get("WETTERREKORD_LIVE_POLL_MINUTES", "15"))
# The DWD updates daily/kl recent only once per day — a daily ingest is
# enough to keep the records current.
INGEST_HOUR = int(os.environ.get("WETTERREKORD_INGEST_HOUR", "4"))
