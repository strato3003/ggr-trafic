"""Tuiles OSM « terre seule » : l’océan est transparent pour éviter les taches de LOD."""

from __future__ import annotations

import asyncio
import io
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
from PIL import Image

from recorder.config import load_config

log = logging.getLogger(__name__)

OSM_MAX_Z = 8
OSM_TILE = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
OSM_UA = "GGR-Trafic/1.1.11 (F6KUF; https://ggr-trafic.k3s.lpb.ovh)"
# Océan uni (#0a3558), identique à la sphère sous les tuiles.
OCEAN_RGBA = (10, 53, 88, 255)
# OSM Carto @water-color #aad3df — https://github.com/gravitystorm/openstreetmap-carto
_FETCH_SEM = asyncio.Semaphore(4)
# Pool dédié : ne pas saturer le default executor (FileResponse /media waterfall).
_PUNCH_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="osm-punch")


def is_osm_water(r: int, g: int, b: int) -> bool:
    """Cyan des océans / lacs Carto, pas la glace (r trop élevé) ni la végétation (b trop bas)."""
    if r >= 210:
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
    return Path(raw) / "osm-land-v2"


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
    url = OSM_TILE.format(z=z, x=x, y=y)
    async with _FETCH_SEM:
        async with httpx.AsyncClient(timeout=20.0, headers={"User-Agent": OSM_UA}) as client:
            res = await client.get(url)
            res.raise_for_status()
            raw = res.content
    loop = asyncio.get_running_loop()
    png = await loop.run_in_executor(_PUNCH_POOL, punch_water, raw)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png)
    return png
