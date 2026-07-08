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
    # draw order: background dots first, then near-record rings, then broken
    plain, near, broken = [], [], []
    heat_total = cold_total = 0
    hottest = coldest = None
    for st in stations:
        if st["heat"]["level"]:
            heat_total += 1
        if st["cold"]["level"]:
            cold_total += 1
        if st["tmax_today"] is not None and (hottest is None or st["tmax_today"] > hottest["tmax_today"]):
            hottest = st
        if st["tmin_today"] is not None and (coldest is None or st["tmin_today"] < coldest["tmin_today"]):
            coldest = st
        if st["heat"]["level"]:
            broken.append((st, HEAT_COLORS[st["heat"]["level"]]))
        elif st["cold"]["level"]:
            broken.append((st, COLD_COLORS[st["cold"]["level"]]))
        elif st["heat"]["near"]:
            near.append((st, HEAT_COLORS[st["heat"]["near"]]))
        elif st["cold"]["near"]:
            near.append((st, COLD_COLORS[st["cold"]["near"]]))
        else:
            plain.append((st, NODATA_COLOR if st["tmax_today"] is None else NONE_COLOR))

    def dot(st, color, r, outline=None, width=1, fill=True):
        x, y = project(st["lon"], st["lat"])
        d.ellipse(
            (x - r, y - r, x + r, y + r),
            fill=color if fill else None,
            outline=outline or "#0b0e13",
            width=width,
        )

    for st, color in plain:
        dot(st, color, 4)
    for st, color in near:
        dot(st, color, 8, outline=color, width=3, fill=False)
    for st, color in broken:
        dot(st, color, 9)

    # text column on the left
    x = 56
    d.text((x, 60), "wetter", font=_font(64, bold=True), fill=TEXT)
    w = d.textlength("wetter", font=_font(64, bold=True))
    d.text((x + w, 60), "rekord", font=_font(64, bold=True), fill=ACCENT)
    w2 = d.textlength("rekord", font=_font(64, bold=True))
    d.text((x + w + w2, 60), ".de", font=_font(64, bold=True), fill=TEXT)
    d.text((x, 148), "Temperaturrekorde Deutschland live", font=_font(30), fill=MUTED)

    try:
        date = datetime.fromisoformat(data["date"])
        date_str = f"{WEEKDAYS[date.weekday()]}, {date.strftime('%d.%m.%Y')}"
    except (KeyError, ValueError):
        date_str = ""
    d.text((x, 206), date_str, font=_font(26), fill=MUTED)

    total = heat_total + cold_total
    d.text(
        (x, 274),
        f"{total} Rekord{'' if total == 1 else 'e'} gebrochen",
        font=_font(46, bold=True),
        fill=TEXT,
    )
    y = 352
    d.ellipse((x, y + 6, x + 18, y + 24), fill=HEAT_COLORS["day"])
    d.text((x + 30, y), f"{heat_total} Hitze", font=_font(30), fill=TEXT)
    xc = x + 30 + d.textlength(f"{heat_total} Hitze", font=_font(30)) + 40
    d.ellipse((xc, y + 6, xc + 18, y + 24), fill=COLD_COLORS["day"])
    d.text((xc + 30, y), f"{cold_total} Kälte", font=_font(30), fill=TEXT)

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
