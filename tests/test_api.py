from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from starlette.testclient import TestClient

from recordpy import config, db
from recordpy.app import app, past_values

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
