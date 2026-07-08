from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from starlette.testclient import TestClient

from wetterrekord import config, db
from wetterrekord.app import app, past_values

TZ = ZoneInfo("Europe/Berlin")


def test_past_values_aggregation(tmp_path: Path):
    conn = db.connect(tmp_path / "test.sqlite")
    rows = [
        # previous day — must not leak into the aggregation
        ("00001", "2026-07-07T23:50:00+02:00", 25.0),
        ("00001", "2026-07-08T06:00:00+02:00", 14.2),
        ("00001", "2026-07-08T12:00:00+02:00", 28.4),
        ("00001", "2026-07-08T14:00:00+02:00", 31.0),  # after 'at'
        ("00002", "2026-07-08T12:10:00+02:00", 22.2),
    ]
    conn.executemany("INSERT INTO measurements VALUES (?,?,?)", rows)

    at = datetime(2026, 7, 8, 13, 0, tzinfo=TZ)
    values = past_values(conn, at)
    tmax, tmin, last_ts = values["00001"]
    assert tmax == 28.4
    assert tmin == 14.2
    assert last_ts == "2026-07-08T12:00:00+02:00"
    assert values["00002"][0] == 22.2
    assert "00003" not in values


def _client():
    # no lifespan: the imprint/index routes need no DB or scheduler
    return TestClient(app)


def test_imprint_hidden_when_unconfigured(monkeypatch):
    monkeypatch.setattr(config, "IMPRINT_HTML", "")
    client = _client()
    assert client.get("/impressum").status_code == 404
    assert "Impressum" not in client.get("/").text


def test_imprint_shown_when_configured(monkeypatch):
    monkeypatch.setattr(config, "IMPRINT_HTML", "<p>Cygnus Networks GmbH</p>")
    client = _client()
    page = client.get("/impressum")
    assert page.status_code == 200
    assert "Cygnus Networks GmbH" in page.text
    assert "Datenschutzerklärung" in page.text
    index = client.get("/").text
    assert '<a href="impressum">' in index


def _st(name, lon, lat, tmax, tmin, heat_level=None, heat_near=None):
    return {
        "id": name, "name": name, "bundesland": "Hessen", "lat": lat, "lon": lon,
        "altitude": 100, "first_year": 1950, "tmax_today": tmax, "tmin_today": tmin,
        "last_measurement": None,
        "records": {"high": {}, "low": {}},
        "heat": {"level": heat_level, "near": heat_near},
        "cold": {"level": None, "near": None},
    }


def test_og_image_render():
    from wetterrekord import ogimage

    data = {
        "date": "2026-07-08",
        "stations": [
            _st("Frankfurt", 8.6, 50.1, 38.2, 21.0, heat_level="day"),
            _st("Kassel", 9.4, 51.3, 31.0, 15.0, heat_near="day"),
            _st("Fulda", 9.7, 50.5, 25.0, 12.0),
            _st("Offline", 8.0, 49.8, None, None),
        ],
    }
    png = ogimage.render(data)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_og_image_endpoint(tmp_path, monkeypatch):
    from wetterrekord import app as appmod

    monkeypatch.setattr(appmod, "conn", db.connect(tmp_path / "og.sqlite"))
    monkeypatch.setattr(appmod, "_og_cache", None)
    client = _client()
    resp = client.get("/og-image.png")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert resp.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert client.head("/og-image.png").status_code == 200
