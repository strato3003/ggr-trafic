"""Tuiles carte « toponymes latins » : océan uni pour éviter les taches de LOD."""

from __future__ import annotations

import asyncio
import io
import logging
from pathlib import Path

import httpx
from PIL import Image

from recorder.config import load_config

log = logging.getLogger(__name__)

OSM_MAX_Z = 8
# Esri World Street Map : alphabet latin (pas d’arabe / cyrillique / CJK). XYZ Esri = z/y/x.
MAP_TILE = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}"
OSM_UA = "GGR-Trafic/0.3.9 (F6KUF; https://ggr-trafic.k3s.lpb.ovh)"
# Océan uni (#0a3558), identique à la sphère sous les tuiles.
OCEAN_RGBA = (10, 53, 88, 255)
_FETCH_SEM = asyncio.Semaphore(6)


def is_osm_water(r: int, g: int, b: int) -> bool:
    """Eau Esri (bleu ciel) ou cyan OSM Carto, pas la crème des terres ni la végétation."""
    if r >= 225:
        return False
    return b >= 150 and g >= 145 and (b - r) >= 28 and (g - r) >= 18


def punch_water(png: bytes) -> bytes:
    im = Image.open(io.BytesIO(png)).convert("RGBA")
    px = im.load()
    width, height = im.size
    for y in range(height):
        for x in range(width):
            r, g, b, a = px[x, y]
            if is_osm_water(r, g, b):
                px[x, y] = OCEAN_RGBA
    out = io.BytesIO()
    im.save(out, format="PNG", optimize=True)
    return out.getvalue()


def _cache_dir(cfg: dict | None = None) -> Path:
    cfg = cfg or load_config()
    raw = (cfg.get("storage") or {}).get("data_dir") or "data"
    return Path(raw) / "esri-latin-v1"


def tile_cache_path(z: int, x: int, y: int, cfg: dict | None = None) -> Path:
    return _cache_dir(cfg) / str(z) / str(x) / f"{y}.png"


def valid_tile(z: int, x: int, y: int) -> bool:
    if z < 0 or z > OSM_MAX_Z:
        return False
    n = 1 << z
    return 0 <= x < n and 0 <= y < n


async def land_tile_png(z: int, x: int, y: int, cfg: dict | None = None) -> bytes:
    if not valid_tile(z, x, y):
        raise ValueError("tuile hors limites")
    path = tile_cache_path(z, x, y, cfg)
    if path.is_file() and path.stat().st_size > 32:
        return path.read_bytes()
    url = MAP_TILE.format(z=z, x=x, y=y)
    async with _FETCH_SEM:
        async with httpx.AsyncClient(timeout=20.0, headers={"User-Agent": OSM_UA}) as client:
            res = await client.get(url)
            res.raise_for_status()
            raw = res.content
    png = await asyncio.to_thread(punch_water, raw)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png)
    return png
