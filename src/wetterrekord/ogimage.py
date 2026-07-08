"""Server-side Open Graph image: the current record map rendered as PNG.

Shared links (Twitter/X, Mastodon, WhatsApp, ...) get a live picture of the
day instead of a stale screenshot: Germany outline, all stations, broken and
near records in the site's colors plus the headline numbers.
"""

import json
import math
from datetime import datetime
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ASSETS = Path(__file__).parent / "assets"

WIDTH, HEIGHT = 1200, 630
BG = "#14181f"
LAND_FILL = "#1c2431"
LAND_EDGE = "#3b475e"
TEXT = "#e8e8e8"
MUTED = "#9aa4b5"
ACCENT = "#ff9248"
HEAT_COLORS = {"alltime": "#8b0012", "month": "#e63946", "quinzaine": "#f4692e", "day": "#ff9248"}
COLD_COLORS = {"alltime": "#021c8f", "month": "#2f6fed", "quinzaine": "#38a3e0", "day": "#6ec6ff"}
GUST_COLOR = "#9c4dcc"
RAIN_COLOR = "#2bb5a0"
PRESSURE_COLOR = "#ddc233"
NONE_COLOR = "#5a6577"
NODATA_COLOR = "#333a48"

WEEKDAYS = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]

# map area on the right of the canvas
MAP_BOX = (740, 24, 1184, 606)


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    return ImageFont.truetype(str(ASSETS / name), size)


def _germany_rings() -> list[list[tuple[float, float]]]:
    geo = json.loads((ASSETS / "germany.geo.json").read_text(encoding="utf-8"))
    rings = []
    for feature in geo["features"]:
        geom = feature["geometry"]
        polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
        for poly in polys:
            rings.append([(lon, lat) for lon, lat in poly[0]])
    return rings


def _projection(rings):
    """Fit an equirectangular projection (lon scaled by cos(mid lat)) into MAP_BOX."""
    lons = [p[0] for ring in rings for p in ring]
    lats = [p[1] for ring in rings for p in ring]
    lon0, lon1, lat0, lat1 = min(lons), max(lons), min(lats), max(lats)
    kx = math.cos(math.radians((lat0 + lat1) / 2))
    w, h = (lon1 - lon0) * kx, lat1 - lat0
    bx0, by0, bx1, by1 = MAP_BOX
    scale = min((bx1 - bx0) / w, (by1 - by0) / h)
    ox = bx0 + ((bx1 - bx0) - w * scale) / 2
    oy = by0 + ((by1 - by0) - h * scale) / 2

    def project(lon: float, lat: float) -> tuple[float, float]:
        return (ox + (lon - lon0) * kx * scale, oy + (lat1 - lat) * scale)

    return project


def _fmt(v: float) -> str:
    return f"{v:.1f}".replace(".", ",") + " °C"


def render(data: dict) -> bytes:
    """Render the OG image from an /api/stations payload."""
    img = Image.new("RGB", (WIDTH, HEIGHT), BG)
    d = ImageDraw.Draw(img)

    rings = _germany_rings()
    project = _projection(rings)
    for ring in rings:
        d.polygon([project(lon, lat) for lon, lat in ring], fill=LAND_FILL, outline=LAND_EDGE)

    stations = data.get("stations", [])
    # draw order: background dots first, then broken records on top
    # ("near record" is deliberately not shown in the shared image)
    plain, broken = [], []
    counts = {"heat": 0, "cold": 0, "gust": 0, "precip": 0, "pressure": 0}
    hottest = coldest = None
    for st in stations:
        params = st.get("params", {})
        counts["heat"] += 1 if st["heat"]["level"] else 0
        counts["cold"] += 1 if st["cold"]["level"] else 0
        for key, name in (("gust", "gust"), ("precip", "precip")):
            counts[name] += 1 if params.get(key, {}).get("status", {}).get("level") else 0
        counts["pressure"] += sum(
            1 for key in ("phigh", "plow") if params.get(key, {}).get("status", {}).get("level")
        )
        if st["tmax_today"] is not None and (hottest is None or st["tmax_today"] > hottest["tmax_today"]):
            hottest = st
        if st["tmin_today"] is not None and (coldest is None or st["tmin_today"] < coldest["tmin_today"]):
            coldest = st
        # marker color: temperature first, then the other parameters
        if st["heat"]["level"]:
            broken.append((st, HEAT_COLORS[st["heat"]["level"]]))
        elif st["cold"]["level"]:
            broken.append((st, COLD_COLORS[st["cold"]["level"]]))
        elif params.get("gust", {}).get("status", {}).get("level"):
            broken.append((st, GUST_COLOR))
        elif params.get("precip", {}).get("status", {}).get("level"):
            broken.append((st, RAIN_COLOR))
        elif any(
            params.get(key, {}).get("status", {}).get("level") for key in ("phigh", "plow")
        ):
            broken.append((st, PRESSURE_COLOR))
        else:
            plain.append((st, NODATA_COLOR if st["tmax_today"] is None else NONE_COLOR))

    def dot(st, color, r):
        x, y = project(st["lon"], st["lat"])
        d.ellipse((x - r, y - r, x + r, y + r), fill=color, outline="#0b0e13")

    for st, color in plain:
        dot(st, color, 4)
    for st, color in broken:
        dot(st, color, 9)

    # text column on the left
    x = 56
    d.text((x, 60), "wetter", font=_font(64, bold=True), fill=TEXT)
    w = d.textlength("wetter", font=_font(64, bold=True))
    d.text((x + w, 60), "rekord", font=_font(64, bold=True), fill=ACCENT)
    w2 = d.textlength("rekord", font=_font(64, bold=True))
    d.text((x + w + w2, 60), ".de", font=_font(64, bold=True), fill=TEXT)
    d.text((x, 148), "Wetterrekorde Deutschland live", font=_font(30), fill=MUTED)

    try:
        date = datetime.fromisoformat(data["date"])
        date_str = f"{WEEKDAYS[date.weekday()]}, {date.strftime('%d.%m.%Y')}"
    except (KeyError, ValueError):
        date_str = ""
    d.text((x, 206), date_str, font=_font(26), fill=MUTED)

    total = sum(counts.values())
    d.text(
        (x, 274),
        f"{total} Rekord{'' if total == 1 else 'e'} gebrochen",
        font=_font(46, bold=True),
        fill=TEXT,
    )
    y = 352
    xc = x
    for color, label in (
        (HEAT_COLORS["day"], f"{counts['heat']} Hitze"),
        (COLD_COLORS["day"], f"{counts['cold']} Kälte"),
        (GUST_COLOR, f"{counts['gust']} Böen"),
        (RAIN_COLOR, f"{counts['precip']} Regen"),
        (PRESSURE_COLOR, f"{counts['pressure']} Druck"),
    ):
        d.ellipse((xc, y + 5, xc + 14, y + 19), fill=color)
        d.text((xc + 22, y), label, font=_font(24), fill=TEXT)
        xc += 22 + d.textlength(label, font=_font(24)) + 26

    y = 420
    if hottest:
        d.text((x, y), f"Höchste Temperatur: {_fmt(hottest['tmax_today'])}", font=_font(26), fill=TEXT)
        d.text((x, y + 36), hottest["name"], font=_font(24), fill=MUTED)
        y += 88
    if coldest:
        d.text((x, y), f"Tiefste Temperatur: {_fmt(coldest['tmin_today'])}", font=_font(26), fill=TEXT)
        d.text((x, y + 36), coldest["name"], font=_font(24), fill=MUTED)

    d.text(
        (x, HEIGHT - 52),
        f"Datenquelle: DWD Open Data · {len(stations)} Wetterstationen",
        font=_font(20),
        fill=MUTED,
    )

    buf = BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
