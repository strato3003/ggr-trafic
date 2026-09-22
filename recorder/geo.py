"""Géodésie simple (haversine, centroïde) — pas de dépendance numpy."""

from __future__ import annotations

import math
import re

EARTH_KM = 6371.0


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance orthodromique en kilomètres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * EARTH_KM * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1 - a)))


def initial_bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Cap initial (0–360°) de (lat1, lon1) vers (lat2, lon2), 0 = nord."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlmb = math.radians(lon2 - lon1)
    y = math.sin(dlmb) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dlmb)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def azimuth_delta(az1: float, az2: float) -> float:
    """Écart de cap en degrés (0–180)."""
    return abs((float(az1) - float(az2) + 180.0) % 360.0 - 180.0)


def cross_track_km(
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float,
    latp: float,
    lonp: float,
) -> float:
    """Distance (km) au grand cercle (lat1,lon1) → (lat2,lon2), y compris au-delà du 2e point."""
    d13 = haversine_km(lat1, lon1, latp, lonp) / EARTH_KM
    theta13 = math.radians(initial_bearing(lat1, lon1, latp, lonp))
    theta12 = math.radians(initial_bearing(lat1, lon1, lat2, lon2))
    xt = math.asin(max(-1.0, min(1.0, math.sin(d13) * math.sin(theta13 - theta12))))
    return abs(xt) * EARTH_KM


def along_track_frac(
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float,
    latp: float,
    lonp: float,
) -> float:
    """Fraction le long du GC 1→2 : 0 à l’origine, 1 à la flotte, >1 au-delà, <0 en arrière."""
    d12 = haversine_km(lat1, lon1, lat2, lon2) / EARTH_KM
    if d12 < 1e-9:
        return 0.0
    d13 = haversine_km(lat1, lon1, latp, lonp) / EARTH_KM
    theta13 = math.radians(initial_bearing(lat1, lon1, latp, lonp))
    theta12 = math.radians(initial_bearing(lat1, lon1, lat2, lon2))
    at = math.atan2(math.sin(d13) * math.cos(theta13 - theta12), math.cos(d13))
    return at / d12


def centroid(points: list[tuple[float, float]]) -> tuple[float, float] | None:
    """Centroïde sphérique approximé (moyenne vectorielle unitaire)."""
    if not points:
        return None
    x = y = z = 0.0
    for lat, lon in points:
        p = math.radians(lat)
        l = math.radians(lon)
        x += math.cos(p) * math.cos(l)
        y += math.cos(p) * math.sin(l)
        z += math.sin(p)
    n = len(points)
    x, y, z = x / n, y / n, z / n
    hyp = math.sqrt(x * x + y * y)
    lon = math.degrees(math.atan2(y, x))
    lat = math.degrees(math.atan2(z, hyp))
    return lat, lon


def along_great_circle(
    lat1: float, lon1: float, lat2: float, lon2: float, frac: float
) -> tuple[float, float]:
    """Point à la fraction *frac* (0–1) sur l’orthodromie (lat1,lon1) → (lat2,lon2)."""
    t = min(max(float(frac), 0.0), 1.0)
    p1, l1 = math.radians(lat1), math.radians(lon1)
    p2, l2 = math.radians(lat2), math.radians(lon2)
    dphi = p2 - p1
    dlmb = l2 - l1
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    d = 2 * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1.0 - a)))
    if d < 1e-9:
        return float(lat1), float(lon1)
    u = math.sin((1.0 - t) * d) / math.sin(d)
    v = math.sin(t * d) / math.sin(d)
    x = u * math.cos(p1) * math.cos(l1) + v * math.cos(p2) * math.cos(l2)
    y = u * math.cos(p1) * math.sin(l1) + v * math.cos(p2) * math.sin(l2)
    z = u * math.sin(p1) + v * math.sin(p2)
    lat = math.degrees(math.atan2(z, math.sqrt(x * x + y * y)))
    lon = math.degrees(math.atan2(y, x))
    return lat, lon


def fmt_latlon(lat: float, lon: float) -> str:
    ns = "N" if lat >= 0 else "S"
    ew = "E" if lon >= 0 else "W"
    return f"{abs(lat):.3f}°{ns} {abs(lon):.3f}°{ew}"


_FMT_RE = re.compile(
    r"([0-9]+(?:\.[0-9]+)?)\s*°\s*([NSns])\s+([0-9]+(?:\.[0-9]+)?)\s*°\s*([EWew])"
)


def parse_fmt_latlon(fmt: str | None) -> tuple[float, float] | None:
    """Inverse de fmt_latlon — pour les snaps Kiwi sans lat/lon numériques."""
    if not fmt:
        return None
    m = _FMT_RE.search(str(fmt))
    if not m:
        return None
    lat = float(m.group(1)) * (-1 if m.group(2).upper() == "S" else 1)
    lon = float(m.group(3)) * (-1 if m.group(4).upper() == "W" else 1)
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None
    return lat, lon


def point_in_ring(lon: float, lat: float, ring: list) -> bool:
    """Ray-casting dans un anneau GeoJSON [lon, lat] (sans shapely)."""
    inside = False
    n = len(ring)
    if n < 4:
        return False
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi + 1e-18) + xi:
            inside = not inside
        j = i
    return inside


def point_in_feature(lat: float, lon: float, feat: dict) -> bool:
    """True si (lat, lon) est dans le polygone GeoJSON (trous exclus)."""
    geom = (feat or {}).get("geometry") or {}
    gtype = geom.get("type")
    coords = geom.get("coordinates")
    if not coords:
        return False
    polys = coords if gtype == "MultiPolygon" else [coords]
    for poly in polys:
        if not poly or not point_in_ring(lon, lat, poly[0]):
            continue
        if any(point_in_ring(lon, lat, hole) for hole in poly[1:]):
            continue
        return True
    return False
