from datetime import date, timezone
from pathlib import Path

from wetterrekord.dwd import (
    DailyValue,
    parse_10min,
    parse_daily_kl,
    parse_station_list,
    read_zip_member,
)
from wetterrekord.records import compute_records, quinzaine_of

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_station_list():
    text = (FIXTURES / "kl_stations_sample.txt").read_bytes().decode("latin-1")
    stations = parse_station_list(text)
    assert len(stations) == 48
    aach = stations[0]
    assert aach.id == "00001"
    assert aach.name == "Aach"
    assert aach.bundesland == "Baden-Württemberg"
    assert aach.von == date(1937, 1, 1)
    assert aach.altitude == 478
    assert abs(aach.lat - 47.8413) < 1e-6
    # station name containing spaces/parentheses
    donaueschingen = next(s for s in stations if s.id == "00011")
    assert donaueschingen.name == "Donaueschingen (Landeplatz)"


def test_parse_daily_kl():
    values = parse_daily_kl((FIXTURES / "produkt_klima_tag_sample.txt").read_bytes())
    first = values[0]
    assert first.day == date(1957, 9, 1)
    assert first.tmax == 16.8
    assert first.tmin == 11.9
    # SDK is -999 in the first line — must not affect TXK/TNK
    assert all(v.tmax is None or -60 < v.tmax < 60 for v in values)
    assert any(v.fx is not None for v in values)
    assert any(v.rsk is not None for v in values)
    assert any(v.pm is not None for v in values)


def test_parse_10min_now_zip():
    data = read_zip_member((FIXTURES / "10minutenwerte_TU_02667_now.zip").read_bytes())
    values = parse_10min(data, ["TT_10", "PP_10"])
    assert values
    ts, (tt, _pp) = values[0]
    assert ts.tzinfo == timezone.utc
    assert -60 < tt < 60


def test_parse_10min_missing_column():
    data = read_zip_member((FIXTURES / "10minutenwerte_TU_02667_now.zip").read_bytes())
    values = parse_10min(data, ["TT_10", "NO_SUCH_COLUMN"])
    assert values
    assert all(v[1][1] is None for v in values)


def test_compute_records():
    values = [
        DailyValue(date(2000, 7, 7), tmax=30.0, tmin=15.0),
        DailyValue(date(2001, 7, 7), tmax=32.0, tmin=14.0),
        DailyValue(date(2001, 7, 8), tmax=28.0, tmin=None),
        DailyValue(date(2002, 1, 1), tmax=5.0, tmin=-10.0),
    ]
    r = compute_records(values)
    assert r.first_year == 2000 and r.last_year == 2002
    assert r.daily[("temp", "high", 7, 7)].value == 32.0
    assert r.daily[("temp", "high", 7, 7)].record_date == date(2001, 7, 7)
    assert r.daily[("temp", "low", 7, 7)].value == 14.0
    assert ("temp", "low", 7, 8) not in r.daily  # tmin fehlt
    assert r.monthly[("temp", "high", 7)].value == 32.0
    assert r.alltime[("temp", "high")].value == 32.0
    assert r.alltime[("temp", "low")].value == -10.0


def test_compute_records_extra_params(monkeypatch):
    from wetterrekord import config

    monkeypatch.setattr(config, "MIN_YEARS", 2)
    values = [
        DailyValue(date(2000, 7, 7), tmax=30.0, fx=20.0, rsk=5.0, pm=1010.0),
        DailyValue(date(2001, 7, 7), tmax=31.0, fx=28.5, rsk=42.1, pm=985.3),
        DailyValue(date(2002, 7, 8), tmax=29.0, fx=15.0, rsk=0.0, pm=1032.0),
    ]
    r = compute_records(values)
    assert r.alltime[("gust", "high")].value == 28.5
    assert r.daily[("precip", "high", 7, 7)].value == 42.1
    assert r.alltime[("pressure", "high")].value == 1032.0
    assert r.alltime[("pressure", "low")].value == 985.3
    assert ("gust", "low") not in r.alltime  # gusts only have high records


def test_compute_records_min_years_per_param():
    # 31 years of temperature, but only one year of wind: no gust records
    values = [DailyValue(date(1990 + i, 7, 7), tmax=30.0) for i in range(31)]
    values.append(DailyValue(date(2021, 7, 8), tmax=28.0, fx=25.0))
    r = compute_records(values)
    assert r.alltime[("temp", "high")].value == 30.0
    assert ("gust", "high") not in r.alltime


def test_quinzaine_boundaries():
    assert quinzaine_of(date(2026, 7, 1)) == (7, 1)
    assert quinzaine_of(date(2026, 7, 15)) == (7, 1)
    assert quinzaine_of(date(2026, 7, 16)) == (7, 2)
    assert quinzaine_of(date(2026, 2, 28)) == (2, 2)


def test_quinzaine_records():
    values = [
        DailyValue(date(2000, 7, 7), tmax=30.0, tmin=15.0),
        DailyValue(date(2001, 7, 14), tmax=33.0, tmin=12.0),
        DailyValue(date(2001, 7, 20), tmax=36.0, tmin=18.0),
    ]
    r = compute_records(values)
    assert r.quinzaine[("temp", "high", 7, 1)].value == 33.0
    assert r.quinzaine[("temp", "high", 7, 2)].value == 36.0
    assert r.quinzaine[("temp", "low", 7, 1)].value == 12.0
    assert r.monthly[("temp", "high", 7)].value == 36.0


def test_status_levels():
    from wetterrekord.app import _status

    records = {
        "day": {"value": 30.0, "date": "1990-07-07"},
        "quinzaine": {"value": 32.0, "date": "1995-07-10"},
        "month": {"value": 34.0, "date": "2003-07-20"},
        "alltime": {"value": 38.0, "date": "2019-07-25"},
    }
    assert _status(None, records, "heat") == {"level": None, "near": None}
    assert _status(29.5, records, "heat") == {"level": None, "near": "day"}
    assert _status(30.0, records, "heat")["level"] == "day"
    assert _status(32.5, records, "heat")["level"] == "quinzaine"
    assert _status(33.5, records, "heat") == {"level": "quinzaine", "near": "month"}
    assert _status(34.5, records, "heat")["level"] == "month"
    assert _status(38.2, records, "heat")["level"] == "alltime"
    # cold: lower values break records
    cold = {"day": {"value": 5.0, "date": "1985-07-07"}, "alltime": {"value": -20.0, "date": "1987-01-12"}}
    assert _status(4.0, cold, "cold")["level"] == "day"
    assert _status(5.8, cold, "cold") == {"level": None, "near": "day"}
