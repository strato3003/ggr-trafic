#!/usr/bin/env python3
"""Découpe les rectangles haute mer METAREA II sur l’océan ∩ polygone OHI II."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from shapely.geometry import box, mapping, shape
from shapely.ops import unary_union
from shapely.validation import make_valid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.metarea import BOXES  # noqa: E402

MET = ROOT / "app/static/geo/metareas.json"
DST = ROOT / "app/static/geo/metarea2-subzones.json"
SIMPLIFY_DEG = 0.02
MIN_AREA = 0.002


def _load_clip():
    spec = importlib.util.spec_from_file_location("clip_metareas", ROOT / "scripts/clip_metareas.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _metarea_ii():
    raw = json.loads(MET.read_text(encoding="utf-8"))
    for feat in raw.get("features") or []:
        if feat.get("properties", {}).get("name") == "II":
            return make_valid(shape(feat["geometry"]))
    raise SystemExit("METAREA II absente de metareas.json")


def _clip(geom, mask, clipmod):
    clipped = make_valid(geom).intersection(mask)
    keep = [p for p in clipmod._polys(clipped) if p.area >= MIN_AREA]
    if not keep:
        keep = clipmod._polys(clipped)
    if not keep:
        return None
    merged = unary_union(keep).simplify(SIMPLIFY_DEG, preserve_topology=True)
    merged = make_valid(merged)
    polys = [p for p in clipmod._polys(merged) if p.area >= MIN_AREA]
    if not polys:
        polys = clipmod._polys(merged)
    if not polys:
        return None
    out = unary_union(polys)
    return None if out.is_empty else out


def main() -> None:
    clipmod = _load_clip()
    ocean = clipmod._load_ocean()
    met2 = _metarea_ii()
    mask = make_valid(ocean.intersection(met2))
    feats = []
    for item in BOXES:
        rect = box(item["w"], item["s"], item["e"], item["n"])
        clipped = _clip(rect, mask, clipmod)
        if clipped is None:
            print(f"  {item['name']:20} VIDE")
            continue
        geo = mapping(clipped)
        geo["coordinates"] = clipmod._round_coords(geo["coordinates"])
        pt = clipped.representative_point()
        props = {
            "name": item["name"],
            "kind": item["kind"],
            "fill": item["fill"],
            "stroke": "#0b1c28",
            "label_lat": round(float(pt.y), 4),
            "label_lon": round(float(pt.x), 4),
            "layer": "subzone",
        }
        feats.append({"type": "Feature", "properties": props, "geometry": geo})
        print(f"  {item['name']:20} {clipped.geom_type:16} area={clipped.area:7.3f}")
    if len(feats) < 10:
        raise SystemExit("trop peu de sous-zones après découpe")
    out = {
        "type": "FeatureCollection",
        "name": "METAREA II sous-zones haute mer",
        "attribution": (
            "Sous-zones du bulletin haute mer METAREA II (FQNT52 LFPW, Météo-France), "
            "rectangles de la carte de prévision, découpés sur l’océan (Natural Earth 50m) "
            "et la METAREA II OHI."
        ),
        "features": feats,
    }
    DST.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"écrit {DST} ({DST.stat().st_size} octets, {len(feats)} zones)")


if __name__ == "__main__":
    main()
