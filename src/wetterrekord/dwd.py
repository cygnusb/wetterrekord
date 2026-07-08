"""Download and parsing of the DWD open data files (CDC)."""

import io
import re
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

import httpx

from . import config

MISSING = -999.0


@dataclass(frozen=True)
class StationInfo:
    id: str  # five digits, e.g. "02667"
    von: date
    bis: date
    altitude: int
    lat: float
    lon: float
    name: str
    bundesland: str


@dataclass(frozen=True)
class DailyValue:
    day: date
    tmax: float | None = None  # TXK
    tmin: float | None = None  # TNK
    fx: float | None = None  # FX, daily max wind gust (m/s)
    rsk: float | None = None  # RSK, daily precipitation sum (mm)
    pm: float | None = None  # PM, daily mean pressure at station altitude (hPa)


_STATION_RE = re.compile(
    r"^(?P<id>\d{5}) (?P<von>\d{8}) (?P<bis>\d{8})\s+(?P<alt>-?\d+)\s+"
    r"(?P<lat>-?\d+\.\d+)\s+(?P<lon>-?\d+\.\d+)\s+(?P<rest>\S.*)$"
)


def parse_station_list(text: str) -> list[StationInfo]:
    """Parse a DWD station description file (latin-1 decoded text)."""
    stations = []
    for line in text.splitlines()[2:]:  # skip header and separator line
        m = _STATION_RE.match(line.rstrip())
        if not m:
            continue
        # Name and federal state are separated by >=2 spaces; names may
        # contain single spaces ("Donaueschingen (Landeplatz)").
        rest = re.split(r"\s{2,}", m.group("rest"))
        stations.append(
            StationInfo(
                id=m.group("id"),
                von=datetime.strptime(m.group("von"), "%Y%m%d").date(),
                bis=datetime.strptime(m.group("bis"), "%Y%m%d").date(),
                altitude=int(m.group("alt")),
                lat=float(m.group("lat")),
                lon=float(m.group("lon")),
                name=rest[0],
                bundesland=rest[1] if len(rest) > 1 else "",
            )
        )
    return stations


def _field_value(fields: list[str], idx: int | None) -> float | None:
    if idx is None or len(fields) <= idx:
        return None
    try:
        v = float(fields[idx])
    except ValueError:
        return None
    return None if v == MISSING else v


def parse_daily_kl(data: bytes) -> list[DailyValue]:
    """Extract TXK/TNK/FX/RSK/PM from a produkt_klima_tag file (raw bytes)."""
    lines = data.decode("latin-1").splitlines()
    header = [h.strip() for h in lines[0].split(";")]
    i_date = header.index("MESS_DATUM")
    idx = {c: header.index(c) if c in header else None for c in ("TXK", "TNK", "FX", "RSK", "PM")}
    values = []
    for line in lines[1:]:
        fields = line.split(";")
        if len(fields) <= i_date or not fields[i_date].strip().isdigit():
            continue
        values.append(
            DailyValue(
                day=datetime.strptime(fields[i_date].strip(), "%Y%m%d").date(),
                tmax=_field_value(fields, idx["TXK"]),
                tmin=_field_value(fields, idx["TNK"]),
                fx=_field_value(fields, idx["FX"]),
                rsk=_field_value(fields, idx["RSK"]),
                pm=_field_value(fields, idx["PM"]),
            )
        )
    return values


def parse_10min(data: bytes, columns: list[str]) -> list[tuple[datetime, tuple[float | None, ...]]]:
    """Extract (UTC timestamp, values) rows from a 10-minute product file.

    Rows where all requested columns are missing (-999 or absent) are skipped.
    """
    lines = data.decode("latin-1").splitlines()
    header = [h.strip() for h in lines[0].split(";")]
    i_date = header.index("MESS_DATUM")
    idx = [header.index(c) if c in header else None for c in columns]
    values = []
    for line in lines[1:]:
        fields = line.split(";")
        if len(fields) <= i_date or not fields[i_date].strip().isdigit():
            continue
        vals = tuple(_field_value(fields, i) for i in idx)
        if all(v is None for v in vals):
            continue
        ts = datetime.strptime(fields[i_date].strip(), "%Y%m%d%H%M").replace(tzinfo=timezone.utc)
        values.append((ts, vals))
    return values


def read_zip_member(data: bytes, prefix: str = "produkt") -> bytes:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for name in zf.namelist():
            if Path(name).name.startswith(prefix):
                return zf.read(name)
    raise FileNotFoundError(f"no member starting with {prefix!r} in zip")


class DwdClient:
    def __init__(self, cache_dir: Path | None = None):
        self.cache_dir = cache_dir or config.CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.http = httpx.Client(timeout=60, follow_redirects=True)
        self._historical_index: dict[str, str] | None = None

    def close(self) -> None:
        self.http.close()

    def _get(self, url: str) -> bytes:
        resp = self.http.get(url)
        resp.raise_for_status()
        return resp.content

    def _get_cached(self, url: str, filename: str, refresh: bool = False) -> bytes:
        path = self.cache_dir / filename
        if path.exists() and not refresh:
            return path.read_bytes()
        data = self._get(url)
        path.write_bytes(data)
        return data

    def kl_stations(self) -> list[StationInfo]:
        data = self._get(config.DAILY_KL_HISTORICAL + config.KL_STATIONS_FILE)
        return parse_station_list(data.decode("latin-1"))

    def tu_now_stations(self) -> list[StationInfo]:
        data = self._get(config.TU_NOW + config.TU_NOW_STATIONS_FILE)
        return parse_station_list(data.decode("latin-1"))

    def _historical_zip_name(self, station_id: str) -> str | None:
        """The historical file names contain the data period and have to be
        looked up in the directory index."""
        if self._historical_index is None:
            html = self._get(config.DAILY_KL_HISTORICAL).decode("latin-1")
            self._historical_index = {
                m.group(1): m.group(0)
                for m in re.finditer(r"tageswerte_KL_(\d{5})_\d{8}_\d{8}_hist\.zip", html)
            }
        return self._historical_index.get(station_id)

    def daily_values(self, station_id: str) -> list[DailyValue]:
        """Complete daily-value series of a station (historical + recent)."""
        values: dict[date, DailyValue] = {}
        zip_name = self._historical_zip_name(station_id)
        if zip_name:
            data = self._get_cached(config.DAILY_KL_HISTORICAL + zip_name, zip_name)
            for v in parse_daily_kl(read_zip_member(data)):
                values[v.day] = v
        try:
            data = self._get(config.DAILY_KL_RECENT + f"tageswerte_KL_{station_id}_akt.zip")
        except httpx.HTTPStatusError:
            data = None
        if data:
            for v in parse_daily_kl(read_zip_member(data)):
                values[v.day] = v  # recent overrides historical at the boundary
        return [values[d] for d in sorted(values)]

    def now_values(self, station_id: str) -> list[tuple[datetime, tuple[float | None, ...]]]:
        """Live temperature + station pressure: [(ts, (TT_10, PP_10))]."""
        data = self._get(config.TU_NOW + f"10minutenwerte_TU_{station_id}_now.zip")
        return parse_10min(read_zip_member(data), ["TT_10", "PP_10"])

    def _now_optional(self, url: str) -> bytes | None:
        """Not every station appears in every 10-minute dataset — 404 is normal."""
        try:
            data = self._get(url)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise
        return read_zip_member(data)

    def now_gusts(self, station_id: str) -> list[tuple[datetime, tuple[float | None, ...]]]:
        """Live wind gusts: [(ts, (FX_10,))] — empty if the station has none."""
        raw = self._now_optional(
            config.EXTREME_WIND_NOW + f"10minutenwerte_extrema_wind_{station_id}_now.zip"
        )
        return [] if raw is None else parse_10min(raw, ["FX_10"])

    def now_precip(self, station_id: str) -> list[tuple[datetime, tuple[float | None, ...]]]:
        """Live 10-minute precipitation: [(ts, (RWS_10,))] — empty if none."""
        raw = self._now_optional(config.PRECIP_NOW + f"10minutenwerte_nieder_{station_id}_now.zip")
        return [] if raw is None else parse_10min(raw, ["RWS_10"])
