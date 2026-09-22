"""Heuristique de propagation HF pour le choix des KiwiSDR.

Ce n’est pas un modèle VOACAP. Les distances NVIS / zone morte / 1 saut
sont des ordres de grandeur, éventuellement pondérés par F10.7 et Kp NOAA
quand le flux SWPC répond.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import httpx

log = logging.getLogger(__name__)

_CACHE_TTL_S = 3600.0
_cache: dict[str, Any] = {"at": 0.0, "data": {"f107": None, "kp": None, "ok": False}}

# NOAA SWPC (JSON public).
_F107_URL = "https://services.swpc.noaa.gov/json/f107_cm_flux.json"
_KP_URL = "https://services.swpc.noaa.gov/json/planetary_k_index_1m.json"


def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def solar_snapshot(*, timeout_s: float = 2.0, force: bool = False) -> dict[str, Any]:
    """F10.7 (sfu) et Kp courants. Échec silencieux → heuristique sans solaire."""
    now = time.time()
    if os.environ.get("GGR_NO_SWPC"):
        return dict(_cache["data"])
    if not force and now - float(_cache["at"] or 0) < _CACHE_TTL_S:
        return dict(_cache["data"])
    data: dict[str, Any] = {"f107": None, "kp": None, "ok": False}
    try:
        with httpx.Client(timeout=timeout_s) as client:
            flux = _parse_f107(client.get(_F107_URL).json())
            kp = _parse_kp(client.get(_KP_URL).json())
        data = {"f107": flux, "kp": kp, "ok": flux is not None or kp is not None}
    except Exception:
        log.info("NOAA SWPC indisponible — rayons NVIS/saut sans F10.7/Kp")
    _cache["at"] = now
    _cache["data"] = data
    return dict(data)


def _parse_f107(raw: Any) -> float | None:
    rows = raw if isinstance(raw, list) else []
    for row in reversed(rows):
        if not isinstance(row, dict):
            continue
        for key in ("flux", "f107", "f10.7", "observed_flux"):
            try:
                val = float(row.get(key))
            except (TypeError, ValueError):
                continue
            if 50.0 <= val <= 400.0:
                return val
    return None


def _parse_kp(raw: Any) -> float | None:
    rows = raw if isinstance(raw, list) else []
    for row in reversed(rows):
        if isinstance(row, dict):
            for key in ("kp", "kp_index", "estimated_kp"):
                try:
                    val = float(row.get(key))
                except (TypeError, ValueError):
                    continue
                if 0.0 <= val <= 9.0:
                    return val
        elif isinstance(row, (list, tuple)) and len(row) >= 2:
            try:
                val = float(row[-1])
            except (TypeError, ValueError):
                continue
            if 0.0 <= val <= 9.0:
                return val
    return None


def solar_scale(f107: float | None, kp: float | None) -> tuple[float, float]:
    """(activité 0–1, pénalité orage 0–1). F10.7 70→200, Kp ≥ 5 = orage."""
    activity = 0.45
    if f107 is not None:
        activity = _clip((float(f107) - 70.0) / 130.0, 0.0, 1.0)
    storm = 0.0
    if kp is not None:
        storm = _clip((float(kp) - 4.0) / 4.0, 0.0, 1.0)
    return activity, storm


def prop_rings(
    freq_khz: float,
    *,
    hour_utc: float = 12.0,
    f107: float | None = None,
    kp: float | None = None,
) -> dict[str, float]:
    """Rayons NVIS / zone morte / 1 saut / cercle max (km) pour une QRG.

    Antennes flotte = omni. Midi TU (buddy 12:00) : D absorbe le 4 MHz.
    Fin d’après-midi (ACK 18:00, bulletin 14.135) : 12–17 MHz plutôt 1 saut F.
    """
    f = float(freq_khz)
    hour = float(hour_utc) % 24.0
    activity, storm = solar_scale(f107, kp)
    day = 1.0 if 8.0 <= hour < 20.0 else 0.35
    if f < 5500.0:
        nvis = 850.0 * (0.75 + 0.25 * (1.0 - day))
        skip = 1400.0
        hop = 3200.0 + 400.0 * activity
        radius = 3800.0 + 500.0 * activity
    elif f < 9000.0:
        nvis = 550.0 * (0.7 + 0.3 * (1.0 - day))
        skip = 1300.0
        hop = 3500.0 + 400.0 * activity
        radius = 4200.0 + 600.0 * activity
    elif f < 15000.0:
        nvis = 180.0
        skip = 1600.0 - 200.0 * activity
        hop = 4000.0 + 300.0 * activity
        radius = 5200.0 + 400.0 * activity
    else:
        nvis = 80.0
        skip = 2000.0 - 300.0 * activity
        hop = 4500.0 + 200.0 * activity
        radius = 5800.0 + 400.0 * activity
    radius *= 1.0 - 0.25 * storm
    hop *= 1.0 - 0.20 * storm
    return {
        "nvis_km": nvis,
        "skip_km": skip,
        "hop_km": hop,
        "radius_km": max(radius, hop + 200.0),
    }


def prop_zone(dist_km: float, rings: dict[str, float]) -> str:
    d = float(dist_km)
    if d <= float(rings["nvis_km"]):
        return "nvis"
    if d <= float(rings["skip_km"]):
        return "skip"
    if d <= float(rings["hop_km"]):
        return "hop"
    if d <= float(rings["radius_km"]):
        return "far"
    return "dx"


def prop_score(dist_km: float, freq_khz: float, *, hour_utc: float = 12.0, f107: float | None = None, kp: float | None = None) -> float:
    """Score 0–1 pour un Kiwi à *dist_km* de l’omni flotte, sur *freq_khz*."""
    rings = prop_rings(freq_khz, hour_utc=hour_utc, f107=f107, kp=kp)
    d = float(dist_km)
    zone = prop_zone(d, rings)
    f = float(freq_khz)
    if zone == "nvis":
        return 0.95 if f < 9000.0 else 0.40
    if zone == "skip":
        return 0.16
    if zone == "hop":
        return 1.0 if f >= 5500.0 else 0.48
    if zone == "far":
        return 0.55 if f >= 9000.0 else 0.28
    return 0.08
