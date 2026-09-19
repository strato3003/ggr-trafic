"""Géodésie simple (haversine, centroïde) — pas de dépendance numpy."""

from __future__ import annotations

import math

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


def fmt_latlon(lat: float, lon: float) -> str:
    ns = "N" if lat >= 0 else "S"
    ew = "E" if lon >= 0 else "W"
    return f"{abs(lat):.3f}°{ns} {abs(lon):.3f}°{ew}"


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
