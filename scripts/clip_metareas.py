#!/usr/bin/env python3
"""Découpe les polygones METAREA OHI sur l’océan (côtes Natural Earth 50m)."""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

from shapely.geometry import mapping, shape
from shapely.ops import unary_union
from shapely.validation import make_valid

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "scripts/metareas-iho.json"
DST = ROOT / "app/static/geo/metareas.json"
OCEAN_URL = (
    "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/"
    "master/geojson/ne_50m_ocean.geojson"
)
CACHE = Path("/tmp/ne_50m_ocean.geojson")
# ~5 km : coller aux côtes. Recul océan ~4 km pour ne pas peindre les ports (NE 50m).
SIMPLIFY_DEG = 0.05
OCEAN_INSET_DEG = 0.035
MIN_AREA_DEG2 = 0.015


def _round_coords(obj, n=4):
    if isinstance(obj, float):
        return round(obj, n)
    if isinstance(obj, list):
        return [_round_coords(x, n) for x in obj]
    return obj


def _polys(geom):
    if geom is None or geom.is_empty:
        return []
    g = make_valid(geom)
    if g.is_empty:
        return []
    t = g.geom_type
    if t == "Polygon":
        return [g]
    if t == "MultiPolygon":
        return [p for p in g.geoms if not p.is_empty]
    if t == "GeometryCollection":
        out = []
        for part in g.geoms:
            out.extend(_polys(part))
        return out
    return []


def _load_ocean():
    if not CACHE.exists() or CACHE.stat().st_size < 10_000:
        print(f"téléchargement {OCEAN_URL}")
        urllib.request.urlretrieve(OCEAN_URL, CACHE)
    raw = json.loads(CACHE.read_text(encoding="utf-8"))
    parts = []
    for feat in raw.get("features") or []:
        parts.extend(_polys(shape(feat["geometry"])))
    ocean = make_valid(unary_union(parts).buffer(-OCEAN_INSET_DEG))
    print(f"océan Natural Earth : {ocean.geom_type} area={ocean.area:.1f}")
    return ocean


def _clip(met, ocean):
    g = make_valid(shape(met["geometry"]))
    clipped = g.intersection(ocean)
    keep = [p for p in _polys(clipped) if p.area >= MIN_AREA_DEG2]
    if not keep:
        keep = _polys(clipped)
    if not keep:
        return None
    merged = unary_union(keep).simplify(SIMPLIFY_DEG, preserve_topology=True)
    merged = make_valid(merged)
    polys = [p for p in _polys(merged) if p.area >= MIN_AREA_DEG2]
    if not polys:
        polys = _polys(merged)
    if not polys:
        return None
    out = unary_union(polys)
    if out.is_empty:
        return None
    return out


def main() -> None:
    ocean = _load_ocean()
    src = json.loads(SRC.read_text(encoding="utf-8"))
    feats = []
    for feat in src["features"]:
        name = feat["properties"]["name"]
        clipped = _clip(feat, ocean)
        if clipped is None:
            raise SystemExit(f"METAREA {name} vide après découpe océan")
        geo = mapping(clipped)
        geo["coordinates"] = _round_coords(geo["coordinates"])
        nvert = json.dumps(geo["coordinates"]).count("[")
        pt = clipped.representative_point()
        props = dict(feat["properties"])
        props["label_lon"] = round(float(pt.x), 4)
        props["label_lat"] = round(float(pt.y), 4)
        feats.append({"type": "Feature", "properties": props, "geometry": geo})
        print(f"  {name:7} {clipped.geom_type:16} area={clipped.area:8.1f}  verts~{nvert}")
    out = {
        "type": "FeatureCollection",
        "name": "METAREA",
        "attribution": (
            "Limites METAREA : OHI (Iridium SafetyCast), découpées sur l’océan "
            "(Natural Earth 50m). Teintes d’après la carte OMM Limits of METAREAS, mai 2021."
        ),
        "features": feats,
    }
    DST.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"écrit {DST} ({DST.stat().st_size} octets)")


if __name__ == "__main__":
    main()
