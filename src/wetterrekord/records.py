"""Computation of records (temperature, gusts, precipitation, pressure)
from a daily-value series."""

from dataclasses import dataclass, field, replace
from datetime import date

from . import config
from .dwd import DailyValue, reduce_pressure


@dataclass
class Record:
    value: float
    record_date: date


# param -> kind -> value extractor. "high" records are broken by larger
# values, "low" records by smaller ones.
PARAM_KINDS = {
    "temp": {"high": lambda v: v.tmax, "low": lambda v: v.tmin},
    "gust": {"high": lambda v: v.fx},
    "precip": {"high": lambda v: v.rsk},
    "pressure": {"high": lambda v: v.pm, "low": lambda v: v.pm},
}


def quinzaine_of(day: date) -> tuple[int, int]:
    """Half-month period of a date: (month, 1) for day 1-15, (month, 2) after."""
    return (day.month, 1 if day.day <= 15 else 2)


@dataclass
class StationRecords:
    # keys: (param, kind, month, day) / (param, kind, month, half) /
    # (param, kind, month) / (param, kind)
    daily: dict[tuple[str, str, int, int], Record] = field(default_factory=dict)
    quinzaine: dict[tuple[str, str, int, int], Record] = field(default_factory=dict)
    monthly: dict[tuple[str, str, int], Record] = field(default_factory=dict)
    alltime: dict[tuple[str, str], Record] = field(default_factory=dict)
    first_year: int | None = None  # of the temperature series
    last_year: int | None = None


def _update(current: Record | None, value: float, day: date, kind: str) -> Record:
    if current is None:
        return Record(value, day)
    # On a tie the more recent date wins ("record equaled").
    better = value >= current.value if kind == "high" else value <= current.value
    return Record(value, day) if better else current


# Plausibility window for reduced sea-level pressure: the German extremes are
# roughly 954/1058 hPa; the DWD history contains obvious errors far outside
# (e.g. Putbus with a 650 hPa "daily mean" at 40 m altitude).
PM_PLAUSIBLE = (930.0, 1085.0)

# Absolute gust cap: the German lowland record is ~51 m/s, Zugspitze holds
# ~93 m/s (335 km/h). Values beyond are sensor errors (e.g. a lone
# 84 m/s / 302 km/h spike at Greifswald on a calm day).
FX_PLAUSIBLE_LOWLAND = 60.0  # m/s, stations below 1000 m
FX_PLAUSIBLE_MOUNTAIN = 100.0


def _fx_plausible(fx: float | None, altitude: float) -> float | None:
    if fx is None:
        return None
    cap = FX_PLAUSIBLE_MOUNTAIN if altitude >= 1000 else FX_PLAUSIBLE_LOWLAND
    return fx if fx <= cap else None


def _reduce_pm(v: DailyValue, altitude: float) -> DailyValue:
    """Replace station-level PM with its sea-level reduction.

    Uses the daily mean temperature approximated from tmax/tmin; without a
    temperature the reduction is unreliable, so the value is dropped —
    as are physically implausible values (data errors in the DWD series).
    """
    if v.pm is None:
        return v
    temps = [t for t in (v.tmax, v.tmin) if t is not None]
    if not temps:
        return replace(v, pm=None)
    reduced = round(reduce_pressure(v.pm, altitude, sum(temps) / len(temps)), 1)
    if not PM_PLAUSIBLE[0] <= reduced <= PM_PLAUSIBLE[1]:
        return replace(v, pm=None)
    return replace(v, pm=reduced)


def compute_records(values: list[DailyValue], altitude: float = 0.0) -> StationRecords:
    values = [
        replace(_reduce_pm(v, altitude) if altitude else v, fx=_fx_plausible(v.fx, altitude))
        for v in values
    ]
    r = StationRecords()
    for param, kinds in PARAM_KINDS.items():
        years = [
            v.day.year for v in values if any(get(v) is not None for get in kinds.values())
        ]
        if not years:
            continue
        if param == "temp":
            r.first_year, r.last_year = years[0], years[-1]
        elif years[-1] - years[0] + 1 < config.MIN_YEARS:
            # station measures this parameter, but not long enough for records
            continue
        for kind, get in kinds.items():
            for v in values:
                val = get(v)
                if val is None:
                    continue
                day = v.day
                for key, table in (
                    ((param, kind, day.month, day.day), r.daily),
                    ((param, kind, *quinzaine_of(day)), r.quinzaine),
                    ((param, kind, day.month), r.monthly),
                    ((param, kind), r.alltime),
                ):
                    table[key] = _update(table.get(key), val, day, kind)
    return r
