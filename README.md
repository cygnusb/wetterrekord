# wetterrekord.de

**➡️ Live: [https://wetterrekord.de](https://wetterrekord.de)**

[![CI](https://github.com/cygnusb/wetterrekord/actions/workflows/ci.yml/badge.svg)](https://github.com/cygnusb/wetterrekord/actions/workflows/ci.yml)
[![GHCR](https://img.shields.io/badge/ghcr.io-cygnusb%2Fwetterrekord-blue?logo=github)](https://github.com/cygnusb/wetterrekord/pkgs/container/wetterrekord.de)
[![Docker Hub](https://img.shields.io/docker/pulls/cygnusbn/wetterrekord?logo=docker)](https://hub.docker.com/r/cygnusbn/wetterrekord)
[![Python](https://img.shields.io/badge/python-3.14-blue?logo=python&logoColor=white)](pyproject.toml)

Live map of weather records in Germany — inspired by [recordpy.fr](https://recordpy.fr),
built on [DWD Open Data](https://opendata.dwd.de/climate_environment/CDC/) (Climate Data Center
of the German Meteorological Service).

For every weather station with at least 30 years of measurement history and
current 10-minute observations, the map shows how close today's values are
to the historical records: daily record (same calendar day), half-month,
monthly and all-time record. Tracked parameters: temperature (heat/Tmax and
cold/Tmin), wind gusts (daily maximum), precipitation (daily sum) and air
pressure (daily mean, high and low) — each parameter only at stations with
30+ years of history for that parameter.

## Running with Docker (recommended)

```sh
docker compose up -d
```

Then open <http://localhost:8000>. On first start the container automatically
downloads the full DWD history (~340 station ZIP files, takes a few minutes)
into the `wetterrekord-data` volume; the map fills up as soon as the import is done.

Two scheduler jobs run inside the container:

- **Live poll** every 15 min (`WETTERREKORD_LIVE_POLL_MINUTES`): today's max/min
  for all stations from the DWD 10-minute data — this is the "current state of
  the day" shown on the map. Polling more often than ~15 min is pointless, as
  the DWD publishes this data with ~30 min latency.
- **Ingest** daily at `WETTERREKORD_INGEST_HOUR`:30 (default 04:30): recompute the
  records from the daily climate history. Daily is enough because the DWD
  updates the `daily/kl` recent data only once per day.

Prebuilt images: `ghcr.io/cygnusb/wetterrekord` and `cygnusbn/wetterrekord` (Docker Hub).

### Configuration

| Variable | Default | Purpose |
|---|---|---|
| `WETTERREKORD_BASE_URL` | `https://wetterrekord.de` | public base URL (canonical link, sitemap, OG tags) |
| `WETTERREKORD_IMPRINT_HTML` | *(unset)* | HTML fragment with the operator's legal notice; the `/impressum` page (imprint + privacy policy, German) and its footer link only appear when set |
| `WETTERREKORD_LIVE_POLL_MINUTES` | `15` | live poll interval |
| `WETTERREKORD_INGEST_HOUR` | `4` | daily record recomputation hour (local time) |

## Running without Docker

```sh
uv sync
uv run python -m wetterrekord.ingest   # once: download history, compute records
uv run wetterrekord                    # web server on port 8000
```

## Architecture

- `dwd.py` — download + parsing of the DWD files (station lists, daily values `daily/kl`, 10-minute values)
- `records.py` / `ingest.py` — record computation and import into SQLite (`data/wetterrekord.sqlite`)
- `live.py` — poller for today's max/min values (`10_minutes/air_temperature/now`, ~30 min latency)
- `app.py` — FastAPI: `/api/stations` (map), `/api/stations/{id}` (details), static frontend
- `static/` — Leaflet map, heat/cold/gust/precip/pressure, **Rekorde|Jetzt**,
  Simple/Advanced, filters (state, altitude, history years), collapsible mobile chrome
- `/_status` / `/_status.json` — operational status (protect via reverse proxy if public)

## Data license

Data source: Deutscher Wetterdienst (German Meteorological Service), own
elements added. The DWD data is provided under the
[GeoNutzV](https://www.gesetze-im-internet.de/geonutzv/) — attribution required.

## Tests

```sh
uv run pytest
```
