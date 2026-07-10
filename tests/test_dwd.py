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


def test_reduce_pressure():
    from wetterrekord.dwd import reduce_pressure

    assert reduce_pressure(1013.0, 0, 15.0) == 1013.0  # sea level: unchanged
    # Öhringen case: 985 hPa at 276 m and ~25 °C reduces to roughly 1016 hPa
    assert 1013 < reduce_pressure(985.0, 276, 25.0) < 1020
    # Zugspitze: 715 hPa at 2956 m must land in a plausible sea-level range
    assert 990 < reduce_pressure(715.0, 2956, 0.0) < 1050


def test_compute_records_pressure_reduced(monkeypatch):
    from wetterrekord import config
    from wetterrekord.dwd import reduce_pressure

    monkeypatch.setattr(config, "MIN_YEARS", 2)
    values = [
        DailyValue(date(2000, 7, 7), tmax=30.0, tmin=20.0, pm=985.0),
        DailyValue(date(2001, 7, 7), tmax=28.0, tmin=None, pm=984.0),
        DailyValue(date(2002, 7, 7), tmax=None, tmin=None, pm=990.0),  # no temp: dropped
    ]
    r = compute_records(values, altitude=276)
    assert r.alltime[("pressure", "high")].value == round(reduce_pressure(985.0, 276, 25.0), 1)
    # 2001 reduces with tmax alone; 2002 has no temperature and must not count
    assert r.alltime[("pressure", "low")].value == round(reduce_pressure(984.0, 276, 28.0), 1)


def test_compute_records_pressure_plausibility(monkeypatch):
    from wetterrekord import config

    monkeypatch.setattr(config, "MIN_YEARS", 2)
    values = [
        DailyValue(date(2000, 7, 7), tmax=20.0, tmin=10.0, pm=1010.0),
        DailyValue(date(2001, 7, 7), tmax=20.0, tmin=10.0, pm=1005.0),
        # DWD data error (Putbus-style): a 650 hPa "daily mean" at 40 m
        DailyValue(date(2002, 7, 7), tmax=20.0, tmin=10.0, pm=649.8),
    ]
    r = compute_records(values, altitude=40)
    assert r.alltime[("pressure", "low")].record_date == date(2001, 7, 7)


def test_gust_neighbor_outlier_filter():
    from wetterrekord.live import _filter_gust_outliers

    # Greifswald scenario: a lone 84 m/s spike, calm neighbours around 10 m/s
    def result(sid, gust, rows):
        return [sid, "2026-07-10", 20.0, 12.0, gust, 0.0, 1015.0, "ts", rows]

    meta = {
        "A": (54.1, 13.4, 2.0),
        "B": (54.2, 13.9, 10.0),  # ~35 km
        "C": (53.9, 13.0, 40.0),  # ~35 km
        "D": (48.0, 11.0, 20.0),  # far away, must not count
    }
    rows_a = [("t1", 20.0, 9.0, None, None), ("t2", 21.0, 84.0, None, None)]
    results = [
        result("A", 84.0, rows_a),
        result("B", 10.1, []),
        result("C", 11.5, []),
        result("D", 12.0, []),
    ]
    out = {r[0]: r for r in _filter_gust_outliers(results, meta)}
    assert out["A"][4] == 9.0  # spike dropped, valid max remains
    assert out["A"][8][1][2] is None  # offending row fx nulled
    assert out["B"][4] == 10.1  # untouched

    # a genuine regional storm survives (neighbours are high too)
    results = [result("A", 45.0, []), result("B", 38.0, []), result("C", 35.0, [])]
    out = {r[0]: r for r in _filter_gust_outliers(results, meta)}
    assert out["A"][4] == 45.0


def test_fx_plausibility_cap(monkeypatch):
    from wetterrekord import config

    monkeypatch.setattr(config, "MIN_YEARS", 2)
    values = [
        DailyValue(date(2000, 7, 7), tmax=30.0, fx=25.0),
        DailyValue(date(2001, 7, 7), tmax=30.0, fx=84.0),  # sensor error
        DailyValue(date(2002, 7, 7), tmax=30.0, fx=30.0),
    ]
    r = compute_records(values)
    assert r.alltime[("gust", "high")].value == 30.0


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
