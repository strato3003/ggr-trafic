"""Sélection des KiwiSDR les plus adaptés à la position de la flotte."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from recorder.geo import (
    along_track_frac,
    azimuth_delta,
    cross_track_km,
    fmt_latlon,
    haversine_km,
    initial_bearing,
)
from recorder.prop import prop_rings, prop_score, prop_zone, solar_snapshot

log = logging.getLogger(__name__)

_ARRAY_RE = re.compile(r"=\s*(\[[\s\S]*\])\s*;?\s*$")
DIRECTORY_TTL_S = 3600
DIRECTORY_CACHE = "kiwi-directory.json"


def parse_kiwi_directory(raw: str) -> list[dict[str, Any]]:
    """Parse le JS rx.linkfanel.net/kiwisdr_com.js (JSON presque valide)."""
    match = _ARRAY_RE.search(raw)
    blob = match.group(1) if match else raw
    blob = re.sub(r",\s*([\]}])", r"\1", blob)
    data = json.loads(blob)
    if not isinstance(data, list):
        return []
    return [row for row in data if isinstance(row, dict)]


def directory_cache_path(cfg: dict[str, Any] | None = None) -> Path:
    from recorder.config import data_dir

    return data_dir(cfg) / DIRECTORY_CACHE


def read_directory_cache(cfg: dict[str, Any] | None = None) -> tuple[list[dict[str, Any]], datetime | None]:
    path = directory_cache_path(cfg)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return [], None
    rows = data.get("receivers") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return [], None
    at = None
    raw_at = data.get("fetched_at") if isinstance(data, dict) else None
    if raw_at:
        try:
            at = datetime.fromisoformat(str(raw_at).replace("Z", "+00:00"))
        except ValueError:
            at = None
    return [r for r in rows if isinstance(r, dict)], at


def write_directory_cache(cfg: dict[str, Any] | None, rows: list[dict[str, Any]]) -> None:
    path = directory_cache_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "count": len(rows),
        "receivers": rows,
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


async def fetch_directory_raw(cfg: dict[str, Any], client: Any | None = None) -> list[dict[str, Any]]:
    import httpx

    sdr_cfg = cfg.get("sdr") or {}
    url = sdr_cfg.get("directory_url") or "http://rx.linkfanel.net/kiwisdr_com.js"
    owns = client is None
    client = client or httpx.AsyncClient(timeout=40.0, headers={"User-Agent": "GGR-Trafic/kiwi-directory"})
    try:
        resp = await client.get(url)
        resp.raise_for_status()
        rows = parse_kiwi_directory(resp.text)
    finally:
        if owns:
            await client.aclose()
    write_directory_cache(cfg, rows)
    log.info("Annuaire KiwiSDR : %s récepteurs enregistrés", len(rows))
    return rows


async def kiwi_directory(
    cfg: dict[str, Any],
    *,
    max_age_s: int = DIRECTORY_TTL_S,
    refresh: bool = False,
    client: Any | None = None,
) -> list[dict[str, Any]]:
    """Annuaire local. Téléchargement si absent, trop vieux, ou refresh=True."""
    rows, at = read_directory_cache(cfg)
    now = datetime.now(timezone.utc)
    age = None
    if at is not None:
        age = (now - at.astimezone(timezone.utc)).total_seconds()
    if rows and not refresh and age is not None and age <= max_age_s:
        return rows
    try:
        return await fetch_directory_raw(cfg, client=client)
    except Exception:
        log.exception("Téléchargement annuaire KiwiSDR")
        return rows


def map_kiwis(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Tous les Kiwi avec GPS (calque SDR potentiels), hors hors-ligne."""
    out: list[dict[str, Any]] = []
    for row in rows:
        if str(row.get("offline") or "").lower() not in ("no", "0", ""):
            continue
        gps = _parse_gps(str(row.get("gps") or ""))
        if not gps:
            continue
        url = str(row.get("url") or "").strip().rstrip("/")
        try:
            users = int(row.get("users") or 0)
            users_max = int(row.get("users_max") or 0)
        except (TypeError, ValueError):
            users, users_max = 0, 0
        out.append(
            {
                "id": row.get("id"),
                "name": row.get("name") or url or "kiwi",
                "lat": gps[0],
                "lon": gps[1],
                "loc": row.get("loc"),
                "url": url,
                "snr_hf": _snr_hf(str(row.get("snr") or "")),
                "free_slots": max(0, users_max - users),
                "users_max": users_max,
            }
        )
    return out


def _parse_gps(raw: str) -> tuple[float, float] | None:
    match = re.search(r"\(\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\)", raw or "")
    if not match:
        return None
    return float(match.group(1)), float(match.group(2))


def _parse_bands(raw: str) -> tuple[int, int] | None:
    match = re.search(r"(-?\d+)\s*-\s*(-?\d+)", raw or "")
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def _snr_hf(raw: str) -> float:
    # Champ « snr » Kiwi : "HF,MF" approximatif, premier nombre = HF
    try:
        return float(str(raw).split(",")[0])
    except (TypeError, ValueError):
        return 0.0


def _covers_hf(bands: tuple[int, int] | None, min_hz: int, max_hz: int) -> bool:
    if not bands:
        return False
    lo, hi = bands
    return lo <= min_hz and hi >= max_hz


def _covers_freqs(bands: tuple[int, int] | None, freqs_hz: list[int]) -> bool:
    """True si chaque QRG demandée est dans la bande du Kiwi (pas tout l’intervalle min–max)."""
    if not bands or not freqs_hz:
        return False
    lo, hi = bands
    return all(lo <= int(freq) <= hi for freq in freqs_hz)


def _az_sep(a: float, b: float) -> float:
    d = abs(float(a) - float(b)) % 360.0
    return min(d, 360.0 - d)


def _spread_pick(
    pool: list[dict[str, Any]],
    used: set[str],
    chosen: list[dict[str, Any]],
    *,
    min_free: int = 1,
    min_snr: float = 0.0,
    min_sep_km: float = 400.0,
) -> dict[str, Any] | None:
    """Kiwi qui maximise l’écart aux déjà retenus (SNR comme départage)."""
    best: dict[str, Any] | None = None
    best_score = -1.0
    for kiwi in pool:
        if kiwi_key(kiwi) in used:
            continue
        if int(kiwi.get("free_slots") or 0) < int(min_free):
            continue
        if float(kiwi.get("snr_hf") or 0) < float(min_snr):
            continue
        if not chosen:
            score = float(kiwi.get("snr_hf") or 0.0)
        else:
            dmin = min(
                haversine_km(float(kiwi["lat"]), float(kiwi["lon"]), float(other["lat"]), float(other["lon"]))
                for other in chosen
            )
            if dmin < float(min_sep_km):
                continue
            score = dmin * (1.0 + min(float(kiwi.get("snr_hf") or 0.0) / 40.0, 1.0))
        if score > best_score:
            best, best_score = kiwi, score
    return best


def _pad_spread_kiwis(
    out: dict[str, dict[str, Any]],
    pool: list[dict[str, Any]],
    used: set[str],
    *,
    want: int,
    lat: float,
    lon: float,
    min_sep_km: float = 400.0,
) -> None:
    """Complète jusqu’à *want* Kiwi distincts, éloignés les uns des autres."""
    n = 0
    while len(out) < int(want):
        n += 1
        extra = _spread_pick(pool, used, list(out.values()), min_free=1, min_snr=5.0, min_sep_km=min_sep_km)
        if extra is None:
            extra = _spread_pick(pool, used, list(out.values()), min_free=1, min_snr=0.0, min_sep_km=min_sep_km / 2)
        if extra is None:
            extra = _spread_pick(pool, used, list(out.values()), min_free=1, min_snr=0.0, min_sep_km=0.0)
        if extra is None:
            break
        chosen = dict(extra)
        dist = round(haversine_km(lat, lon, float(extra["lat"]), float(extra["lon"])), 1)
        role = f"rx{n}"
        chosen["site"] = role
        chosen["site_label"] = "diversité"
        chosen["site_km"] = dist
        out[role] = chosen
        used.add(kiwi_key(extra))
        log.info("Kiwi diversité → %s (%.0f km)", chosen.get("name"), dist)


def hf_midday_zone(dist_km: float) -> str:
    """Zone ionosphérique approximative à 12:00 TU pour 4–7 MHz."""
    return prop_zone(dist_km, prop_rings(4483.0, hour_utc=12.0))


def hf_midday_prop_score(dist_km: float, freq_khz: float) -> float:
    """Score 0–1 : NVIS / zone morte / 1 saut F, midi TU (heuristique, pas VOACAP)."""
    return prop_score(dist_km, freq_khz, hour_utc=12.0)


def score_buddy_kiwi(
    kiwi: dict[str, Any],
    lat: float,
    lon: float,
    freqs_khz: list[float],
) -> float:
    """Propagation 4/6 MHz + SNR + places — pas la seule proximité."""
    dist = float(kiwi.get("distance_km") or haversine_km(lat, lon, float(kiwi["lat"]), float(kiwi["lon"])))
    props = [hf_midday_prop_score(dist, f) for f in freqs_khz] or [0.0]
    prop = max(props)
    snr = min(max(float(kiwi.get("snr_hf") or 0) / 40.0, 0.0), 1.0)
    free = min(max(float(kiwi.get("free_slots") or 0) / 4.0, 0.0), 1.0)
    bands = kiwi.get("bands_hz")
    hz = [int(round(f * 1000.0)) for f in freqs_khz]
    both = 1.0 if (isinstance(bands, (list, tuple)) and len(bands) == 2 and _covers_freqs((int(bands[0]), int(bands[1])), hz)) else 0.55
    return 0.50 * prop + 0.28 * snr + 0.12 * free + 0.10 * both


def _host_port(url: str) -> tuple[str, int, bool] | None:
    parsed = urlparse(url if "://" in url else f"http://{url}")
    if not parsed.hostname:
        return None
    https = parsed.scheme == "https"
    port = parsed.port or (443 if https else 8073)
    return parsed.hostname, port, https


def kiwi_key(kiwi: dict[str, Any]) -> str:
    return str(kiwi.get("id") or kiwi.get("url") or kiwi.get("name") or "")


def pick_nearest(
    kiwis: list[dict[str, Any]],
    lat: float,
    lon: float,
    *,
    exclude: set[str] | None = None,
    min_free: int = 1,
    min_snr: float = 5.0,
    radius_km: float | None = None,
) -> dict[str, Any] | None:
    """Kiwi le plus proche d’un point (SNR comme départage, rayon optionnel)."""
    skip = exclude or set()
    scored: list[tuple[float, float, dict[str, Any]]] = []
    for kiwi in kiwis:
        if kiwi_key(kiwi) in skip:
            continue
        if int(kiwi.get("free_slots") or 0) < int(min_free):
            continue
        dist = haversine_km(lat, lon, float(kiwi["lat"]), float(kiwi["lon"]))
        if radius_km is not None and dist > float(radius_km):
            continue
        scored.append((dist, -float(kiwi.get("snr_hf") or 0.0), kiwi))
    if not scored:
        return None
    with_snr = [row for row in scored if -row[1] >= float(min_snr)]
    pool = with_snr or scored
    pool.sort(key=lambda row: (row[0], row[1]))
    return pool[0][2]


# Cap de Bonne-Espérance 34°21′26″S 18°28′24″E (WGS84).
CAPE_GOOD_HOPE_LAT = -34.357222
CAPE_GOOD_HOPE_LON = 18.473333
# Cap Horn ≈ 55°59′S 67°16′W : au-delà, retour Atlantique → F6KUF.
CAPE_HORN_LON = -67.266667
# Trop nord = encore Atlantique (Canaries, etc.), pas le cap.
_TAHITI_TX_LAT_MAX = -30.0
# Recouvrement France / Cap Town / Tahiti autour du méridien du cap (± 15°).
TX_OVERLAP_LON_DEG = 15.0
# Boîte Cap Town (METAREA VII + marge GGR) : 8°S–52°S, 35°W–58°E.
_CAPE_LAT_MIN, _CAPE_LAT_MAX = -52.0, -8.0
_CAPE_LON_MIN, _CAPE_LON_MAX = -35.0, 58.0
# Skippers assez écartés pour un Kiwi flotte ouest / est en plus du centroïde.
_FLEET_EXTREME_SPAN_DEG = 8.0
_FLEET_EXTREME_MIN_KM = 400.0
_FLEET_ACK_RADIUS_KM = 2500.0
# Faisceau 20 m F6KUF → flotte, y compris au-delà des bateaux (Brésil, Argentine).
_BEAM_COUNT = 4
_BEAM_MIN_FLEET_KM = 800.0
_BEAM_MIN_TX_KM = 400.0
_BEAM_MAX_XT_KM = 1500.0
_BEAM_MAX_DAZ_DEG = 16.0
_BEAM_MIN_SEP_KM = 400.0
_BEAM_MIN_PATH_KM = 1500.0
_BEAM_MAX_TX_KM = 12000.0
# Au-delà du milieu du trajet TX→flotte (pas l’Europe derrière les bateaux).
_BEAM_MIN_ALONG = 0.75
_OMNI_FORWARD_DEG = 110.0
_OMNI_MIN = 4
_OMNI_MAX = 10
_OMNI_DEFAULT = 6

_SDR_SITE_DEFAULTS: dict[str, dict[str, Any]] = {
    "france": {"label": "Philippe F4HWM / F6KUF", "lat": 46.46806, "lon": -1.61694, "radius_km": 1500.0},
    "cape": {"label": "Cap Town", "lat": -33.9249, "lon": 18.4241, "radius_km": 2000.0},
    "tahiti": {"label": "Michel FO5QB / F6KUF", "lat": -17.5350, "lon": -149.5697, "radius_km": 2500.0},
}


def fleet_uses_tahiti_tx(lat: float, lon: float) -> bool:
    """True après Bonne-Espérance, jusqu’au cap Horn (Michel depuis Tahiti)."""
    if float(lat) > _TAHITI_TX_LAT_MAX:
        return False
    lon = float(lon)
    if lon >= CAPE_GOOD_HOPE_LON:
        return True
    if lon <= CAPE_HORN_LON:
        return True
    return False


def _in_cape_lon_overlap(lon: float) -> bool:
    return abs(float(lon) - CAPE_GOOD_HOPE_LON) <= TX_OVERLAP_LON_DEG


def boat_hears_france(lat: float, lon: float) -> bool:
    """Atlantique, ou encore dans le recouvrement juste à l’est du cap."""
    if not fleet_uses_tahiti_tx(lat, lon):
        return True
    return float(lat) <= _TAHITI_TX_LAT_MAX and _in_cape_lon_overlap(lon)


def boat_hears_tahiti(lat: float, lon: float) -> bool:
    """Indien / Pacifique, ou déjà dans le recouvrement à l’ouest du cap."""
    if fleet_uses_tahiti_tx(lat, lon):
        return True
    return float(lat) <= _TAHITI_TX_LAT_MAX and _in_cape_lon_overlap(lon)


def boat_hears_cape(lat: float, lon: float) -> bool:
    """Bateau dans la zone d’écoute Cap Town (SA / cap / début Indien)."""
    return _CAPE_LAT_MIN <= float(lat) <= _CAPE_LAT_MAX and _CAPE_LON_MIN <= float(lon) <= _CAPE_LON_MAX


def _beam_cfg(cfg: dict[str, Any]) -> dict[str, Any]:
    raw = ((cfg.get("sdr") or {}).get("beam") or {})
    max_xt = raw.get("max_xt_km")
    if max_xt is None:
        max_xt = raw.get("max_slack_km")
    return {
        "count": int(raw["count"] if raw.get("count") is not None else _BEAM_COUNT),
        "min_fleet_km": float(raw["min_fleet_km"] if raw.get("min_fleet_km") is not None else _BEAM_MIN_FLEET_KM),
        "min_tx_km": float(raw["min_tx_km"] if raw.get("min_tx_km") is not None else _BEAM_MIN_TX_KM),
        "max_xt_km": float(max_xt if max_xt is not None else _BEAM_MAX_XT_KM),
        "max_daz_deg": float(raw["max_daz_deg"] if raw.get("max_daz_deg") is not None else _BEAM_MAX_DAZ_DEG),
        "min_separation_km": float(
            raw["min_separation_km"] if raw.get("min_separation_km") is not None else _BEAM_MIN_SEP_KM
        ),
        "min_path_km": float(raw["min_path_km"] if raw.get("min_path_km") is not None else _BEAM_MIN_PATH_KM),
        "max_tx_km": float(raw["max_tx_km"] if raw.get("max_tx_km") is not None else _BEAM_MAX_TX_KM),
        "min_along": float(raw["min_along"] if raw.get("min_along") is not None else _BEAM_MIN_ALONG),
    }


def bulletin_beam_qth(
    cfg: dict[str, Any],
    fleet_lat: float,
    fleet_lon: float,
    boats: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Origine du faisceau 20 m : Vendée tant que F6KUF est audible, sinon Michel."""
    points = _boat_points(boats, fleet_lat, fleet_lon)
    if any(boat_hears_france(lat, lon) for lat, lon in points):
        return _sdr_site(cfg, "france")
    return _sdr_site(cfg, "tahiti")


def select_bulletin_beam_kiwis(
    pool: list[dict[str, Any]],
    *,
    tx_lat: float,
    tx_lon: float,
    fleet_lat: float,
    fleet_lon: float,
    cfg: dict[str, Any],
    exclude: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Jusqu’à 4 Kiwi dans le cône 20 m émetteur → flotte, y compris au-delà des skippers."""
    beam = _beam_cfg(cfg)
    want = max(0, min(int(beam["count"]), 4))
    if want <= 0:
        return []
    path_km = haversine_km(tx_lat, tx_lon, fleet_lat, fleet_lon)
    if path_km < float(beam["min_path_km"]):
        log.info("Faisceau bulletin ignoré (trajet émetteur–flotte %.0f km)", path_km)
        return []
    az_fleet = initial_bearing(tx_lat, tx_lon, fleet_lat, fleet_lon)
    used = set(exclude or ())
    min_sep = float(beam["min_separation_km"])

    def eligible(min_snr: float) -> list[tuple[float, float, float, dict[str, Any], float, float]]:
        rows: list[tuple[float, float, float, dict[str, Any], float, float]] = []
        for kiwi in pool:
            key = kiwi_key(kiwi)
            if not key or key in used:
                continue
            if int(kiwi.get("free_slots") or 0) < 1:
                continue
            if float(kiwi.get("snr_hf") or 0) < min_snr:
                continue
            try:
                lat, lon = float(kiwi["lat"]), float(kiwi["lon"])
            except (TypeError, ValueError, KeyError):
                continue
            d_tx = haversine_km(tx_lat, tx_lon, lat, lon)
            d_fleet = haversine_km(fleet_lat, fleet_lon, lat, lon)
            if d_fleet < float(beam["min_fleet_km"]) or d_tx < float(beam["min_tx_km"]):
                continue
            if d_tx > float(beam["max_tx_km"]):
                continue
            daz = azimuth_delta(az_fleet, initial_bearing(tx_lat, tx_lon, lat, lon))
            if daz > float(beam["max_daz_deg"]):
                continue
            xt = cross_track_km(tx_lat, tx_lon, fleet_lat, fleet_lon, lat, lon)
            if xt > float(beam["max_xt_km"]):
                continue
            along = along_track_frac(tx_lat, tx_lon, fleet_lat, fleet_lon, lat, lon)
            if along < float(beam["min_along"]):
                continue
            rows.append((d_tx, xt, -float(kiwi.get("snr_hf") or 0), kiwi, d_fleet, daz))
        rows.sort(key=lambda row: row[0])
        return rows

    cands = eligible(5.0)
    if len(cands) < want:
        cands = eligible(0.0)
    if not cands:
        return []

    picked: list[dict[str, Any]] = []
    picked_keys: set[str] = set()

    def take_near(target: float) -> dict[str, Any] | None:
        best: tuple[tuple[float, float, float], dict[str, Any], float, float, float, float] | None = None
        for d_tx, xt, nsnr, kiwi, d_fleet, daz in cands:
            key = kiwi_key(kiwi)
            if key in picked_keys:
                continue
            if any(
                haversine_km(float(kiwi["lat"]), float(kiwi["lon"]), float(other["lat"]), float(other["lon"])) < min_sep
                for other in picked
            ):
                continue
            score = (abs(d_tx - target), xt, nsnr)
            if best is None or score < best[0]:
                best = (score, kiwi, d_tx, d_fleet, xt, daz)
        if best is None:
            return None
        _, kiwi, d_tx, d_fleet, xt, daz = best
        chosen = dict(kiwi)
        chosen["beam_xt_km"] = round(xt, 1)
        chosen["beam_daz_deg"] = round(daz, 1)
        chosen["fleet_km"] = round(d_fleet, 1)
        chosen["site_km"] = round(d_tx, 1)
        picked.append(chosen)
        picked_keys.add(kiwi_key(kiwi))
        return chosen

    n = len(cands)
    if n <= want:
        for row in cands:
            take_near(row[0])
    else:
        last = max(want - 1, 1)
        for i in range(want):
            idx = int(round(i * (n - 1) / last))
            take_near(cands[idx][0])

    return picked


def bind_bulletin_beam(
    out: dict[str, dict[str, Any]],
    pool: list[dict[str, Any]],
    used: set[str],
    *,
    tx_lat: float,
    tx_lon: float,
    fleet_lat: float,
    fleet_lon: float,
    cfg: dict[str, Any],
    bind,
) -> None:
    """Jusqu’à 4 Kiwi dans le faisceau 20 m bulletin → flotte (y compris au-delà)."""
    selected = select_bulletin_beam_kiwis(
        pool,
        tx_lat=tx_lat,
        tx_lon=tx_lon,
        fleet_lat=fleet_lat,
        fleet_lon=fleet_lon,
        cfg=cfg,
        exclude=used,
    )
    for n, kiwi in enumerate(selected, start=1):
        role = f"tx_beam{n}"
        bound = bind(
            role,
            float(kiwi["lat"]),
            float(kiwi["lon"]),
            f"portée TX bulletin {n}",
            radius_km=80.0,
            extra={
                "beam_xt_km": kiwi.get("beam_xt_km"),
                "beam_daz_deg": kiwi.get("beam_daz_deg"),
                "fleet_km": kiwi.get("fleet_km"),
            },
            relax_radius=False,
        )
        if bound is None:
            continue
        if bound.get("site_km") is None:
            bound["site_km"] = kiwi.get("site_km")
        log.info(
            "Kiwi TX bulletin faisceau %s → %s (%.0f km de la flotte, écart GC %.0f km)",
            n,
            bound.get("name"),
            float(kiwi.get("fleet_km") or 0),
            float(kiwi.get("beam_xt_km") or 0),
        )


def _sdr_site(cfg: dict[str, Any], key: str) -> dict[str, Any]:
    defaults = _SDR_SITE_DEFAULTS[key]
    raw = ((cfg.get("sdr") or {}).get("sites") or {}).get(key) or {}
    return {
        "id": key,
        "label": raw.get("label") or defaults["label"],
        "lat": float(raw["lat"] if raw.get("lat") is not None else defaults["lat"]),
        "lon": float(raw["lon"] if raw.get("lon") is not None else defaults["lon"]),
        "radius_km": float(raw["radius_km"] if raw.get("radius_km") is not None else defaults["radius_km"]),
    }


def _boat_points(
    boats: list[dict[str, Any]] | None,
    fleet_lat: float,
    fleet_lon: float,
) -> list[tuple[float, float]]:
    pts: list[tuple[float, float]] = []
    for boat in boats or []:
        if not isinstance(boat, dict):
            continue
        if boat.get("lat") is None or boat.get("lon") is None:
            continue
        try:
            pts.append((float(boat["lat"]), float(boat["lon"])))
        except (TypeError, ValueError):
            continue
    if not pts:
        pts.append((float(fleet_lat), float(fleet_lon)))
    return pts


def bulletin_tx_qths(
    cfg: dict[str, Any],
    fleet_lat: float,
    fleet_lon: float,
    boats: list[dict[str, Any]] | None = None,
    *,
    when: datetime | None = None,
    include_france: bool | None = None,
) -> list[dict[str, Any]]:
    """QTH 14.135 : F6KUF les jours de bulletin, Michel tous les jours, Cap Town en zone SA."""
    from recorder.config import france_bulletin_day, tahiti_daily

    points = _boat_points(boats, fleet_lat, fleet_lon)
    if include_france is None:
        include_france = True if when is None else france_bulletin_day(cfg, when)
    want = {
        "france": bool(include_france) and any(boat_hears_france(lat, lon) for lat, lon in points),
        "cape": any(boat_hears_cape(lat, lon) for lat, lon in points),
        "tahiti": tahiti_daily(cfg) or any(boat_hears_tahiti(lat, lon) for lat, lon in points),
    }
    if not any(want.values()):
        want["tahiti"] = fleet_uses_tahiti_tx(fleet_lat, fleet_lon)
        want["france"] = not want["tahiti"]
    return [_sdr_site(cfg, key) for key in ("france", "cape", "tahiti") if want[key]]


def bulletin_tx_qth(
    cfg: dict[str, Any],
    fleet_lat: float,
    fleet_lon: float,
    when: datetime | None = None,
) -> dict[str, Any]:
    """QTH principal (screencast) : F6KUF les jours de bulletin, sinon Michel."""
    from recorder.config import france_bulletin_day

    if fleet_uses_tahiti_tx(fleet_lat, fleet_lon):
        return _sdr_site(cfg, "tahiti")
    if when is not None and not france_bulletin_day(cfg, when):
        return _sdr_site(cfg, "tahiti")
    return _sdr_site(cfg, "france")


def bulletin_tx_label(qths: list[dict[str, Any]] | None, fallback: str = "F6KUF") -> str:
    labels = [str(row.get("label") or "").strip() for row in qths or []]
    labels = [row for row in labels if row]
    return " · ".join(labels) or fallback


def listen_sites(
    cfg: dict[str, Any],
    fleet_lat: float,
    fleet_lon: float,
    boats: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """ACK : flotte + France + Tahiti, et Cap Town dès qu’un bateau est dans la boîte SA."""
    points = _boat_points(boats, fleet_lat, fleet_lon)
    sites = [
        {
            "id": "fleet",
            "label": "flotte",
            "lat": float(fleet_lat),
            "lon": float(fleet_lon),
            "radius_km": None,
        },
        _sdr_site(cfg, "france"),
    ]
    if any(boat_hears_cape(lat, lon) for lat, lon in points):
        sites.append(_sdr_site(cfg, "cape"))
    sites.append(_sdr_site(cfg, "tahiti"))
    return sites


def _fleet_extremes(points: list[tuple[float, float]]) -> tuple[tuple[float, float] | None, tuple[float, float] | None]:
    if len(points) < 2:
        return None, None
    west = min(points, key=lambda p: p[1])
    east = max(points, key=lambda p: p[1])
    south = min(points, key=lambda p: p[0])
    north = max(points, key=lambda p: p[0])
    lon_span = abs(east[1] - west[1])
    lat_span = abs(north[0] - south[0])
    if lon_span < _FLEET_EXTREME_SPAN_DEG and lat_span < _FLEET_EXTREME_SPAN_DEG:
        return None, None
    return west, east


def assign_vacation_kiwis(
    pool: list[dict[str, Any]],
    *,
    fleet_lat: float,
    fleet_lon: float,
    cfg: dict[str, Any],
    boats: list[dict[str, Any]] | None = None,
    when: datetime | None = None,
) -> dict[str, dict[str, Any]]:
    """Bulletin 14.135 : Kiwi de chaque QTH encore audible + Kiwi flotte (centroïde / extrêmes).

    ACK : flotte + France + Tahiti (+ Cap Town en zone SA). Si plus de Kiwi distinct,
    on réutilise le Kiwi TX du même site.
    """
    sdr = cfg.get("sdr") or {}
    tx_slots = int(sdr.get("min_free_slots") or 2)
    out: dict[str, dict[str, Any]] = {}
    used: set[str] = set()
    points = _boat_points(boats, fleet_lat, fleet_lon)
    qths = bulletin_tx_qths(cfg, fleet_lat, fleet_lon, boats=boats, when=when)
    club = bulletin_tx_qth(cfg, fleet_lat, fleet_lon, when=when)

    def bind(
        role: str,
        lat: float,
        lon: float,
        label: str,
        *,
        radius_km: float | None = None,
        min_free: int = 1,
        extra: dict[str, Any] | None = None,
        reuse: str | None = None,
        relax_radius: bool = True,
    ) -> dict[str, Any] | None:
        kiwi = pick_nearest(pool, lat, lon, exclude=used, min_free=min_free, min_snr=5.0, radius_km=radius_km)
        if kiwi is None:
            kiwi = pick_nearest(pool, lat, lon, exclude=used, min_free=1, min_snr=0.0, radius_km=radius_km)
        if kiwi is None and radius_km is not None and relax_radius:
            kiwi = pick_nearest(pool, lat, lon, exclude=used, min_free=1, min_snr=0.0)
        if kiwi is None:
            if reuse and reuse in out:
                chosen = dict(out[reuse])
                chosen["site"] = role
                chosen["site_label"] = label
                if extra:
                    chosen.update(extra)
                out[role] = chosen
                return chosen
            return None
        chosen = dict(kiwi)
        chosen["site"] = role
        chosen["site_label"] = label
        chosen["site_km"] = round(haversine_km(lat, lon, float(kiwi["lat"]), float(kiwi["lon"])), 1)
        if extra:
            chosen.update(extra)
        out[role] = chosen
        used.add(kiwi_key(kiwi))
        return chosen

    tx = bind(
        "tx",
        float(club["lat"]),
        float(club["lon"]),
        f"{club['label']} (bulletin)",
        radius_km=club.get("radius_km"),
        min_free=tx_slots,
        extra={"club_id": club["id"]},
    )
    if tx is None:
        return out
    log.info(
        "Kiwi TX bulletin émetteur → %s (%s, %.0f km)",
        tx.get("name"),
        club["label"],
        tx.get("site_km") or 0,
    )

    for qth in qths:
        if qth["id"] == club["id"]:
            continue
        extra_tx = bind(
            f"tx_{qth['id']}",
            float(qth["lat"]),
            float(qth["lon"]),
            f"{qth['label']} (bulletin)",
            radius_km=qth.get("radius_km"),
            extra={"club_id": qth["id"]},
            relax_radius=False,
        )
        if extra_tx is not None:
            log.info(
                "Kiwi TX bulletin recouvrement → %s (%s, %.0f km)",
                extra_tx.get("name"),
                qth["label"],
                extra_tx.get("site_km") or 0,
            )

    fleet_tx = bind("tx_fleet", fleet_lat, fleet_lon, "flotte (bulletin)")
    if fleet_tx is not None:
        log.info(
            "Kiwi TX bulletin flotte → %s (%.0f km de la flotte)",
            fleet_tx.get("name"),
            fleet_tx.get("site_km") or 0,
        )

    west, east = _fleet_extremes(points)
    for role, point, label in (
        ("tx_fleet_west", west, "flotte ouest (bulletin)"),
        ("tx_fleet_east", east, "flotte est (bulletin)"),
    ):
        if point is None:
            continue
        if haversine_km(fleet_lat, fleet_lon, point[0], point[1]) < _FLEET_EXTREME_MIN_KM:
            continue
        extreme = bind(role, point[0], point[1], label)
        if extreme is not None:
            log.info(
                "Kiwi TX bulletin %s → %s (%.0f km)",
                label,
                extreme.get("name"),
                extreme.get("site_km") or 0,
            )

    beam_qth = bulletin_beam_qth(cfg, fleet_lat, fleet_lon, boats=boats)
    bind_bulletin_beam(
        out,
        pool,
        used,
        tx_lat=float(beam_qth["lat"]),
        tx_lon=float(beam_qth["lon"]),
        fleet_lat=fleet_lat,
        fleet_lon=fleet_lon,
        cfg=cfg,
        bind=bind,
    )

    ack_freqs = [
        float(row.get("freq_khz") or 0)
        for row in ((cfg.get("radio") or {}).get("ack") or [])
        if row.get("freq_khz")
    ] or [16551.0, 12418.0]
    hour = float((when or datetime.now(timezone.utc)).hour) + float((when or datetime.now(timezone.utc)).minute) / 60.0
    omni = assign_omni_fleet_kiwis(
        pool,
        lat=fleet_lat,
        lon=fleet_lon,
        freqs_khz=ack_freqs,
        cfg=cfg,
        hour_utc=hour,
        section=("sdr", "ack_omni"),
        exclude=None,
        tx_lat=float(beam_qth["lat"]),
        tx_lon=float(beam_qth["lon"]),
    )
    for role, kiwi in omni.items():
        key = kiwi_key(kiwi)
        chosen = dict(kiwi)
        chosen["site"] = role
        out[role] = chosen
        used.add(key)
        log.info(
            "Kiwi ACK omni %s → %s (%.0f km, zone %s)",
            chosen.get("site_label"),
            chosen.get("name"),
            chosen.get("site_km") or 0,
            chosen.get("prop_zone"),
        )
    return out


def _buddy_freqs_khz(cfg: dict[str, Any]) -> list[float]:
    buddy = cfg.get("buddy") or {}
    main = float((buddy.get("main") or {}).get("freq_khz") or 4483.0)
    alt = float((buddy.get("alternate") or {}).get("freq_khz") or 6516.0)
    out = [main, alt]
    for raw in buddy.get("extras") or []:
        if not isinstance(raw, dict):
            continue
        try:
            khz = float(raw.get("freq_khz"))
        except (TypeError, ValueError):
            continue
        if 1000.0 <= khz <= 30000.0:
            out.append(khz)
    return out


def _omni_want(cfg: dict[str, Any], section: tuple[str, str]) -> tuple[int, float]:
    root, key = section
    raw = ((cfg.get(root) or {}).get(key) or {})
    if root == "buddy" and key == "kiwi":
        raw = ((cfg.get("buddy") or {}).get("kiwi") or {})
    n = int(raw["count"] if raw.get("count") is not None else _OMNI_DEFAULT)
    n = max(_OMNI_MIN, min(_OMNI_MAX, n))
    sep = float(raw["min_separation_km"] if raw.get("min_separation_km") is not None else 400.0)
    return n, sep


def assign_omni_fleet_kiwis(
    pool: list[dict[str, Any]],
    *,
    lat: float,
    lon: float,
    freqs_khz: list[float],
    cfg: dict[str, Any],
    hour_utc: float,
    section: tuple[str, str] = ("buddy", "kiwi"),
    exclude: set[str] | None = None,
    tx_lat: float | None = None,
    tx_lon: float | None = None,
) -> dict[str, dict[str, Any]]:
    """4 à 10 Kiwi autour du centroïde (omni flotte), répartis en azimut.

    Cercle agrandi selon la QRG (NVIS + 1 saut). Zone morte pénalisée.
    F10.7 / Kp NOAA si disponibles. Les sauts privilégient l’hémisphère
    dans le prolongement émetteur → flotte (pas l’Europe à l’opposé).
    """
    want, sep = _omni_want(cfg, section)
    solar = solar_snapshot()
    f107, kp = solar.get("f107"), solar.get("kp")
    freqs = [float(f) for f in freqs_khz if f]
    if not freqs:
        freqs = [4483.0]
    radius = max(
        float(prop_rings(f, hour_utc=hour_utc, f107=f107, kp=kp)["radius_km"]) for f in freqs
    )
    hop_km = max(
        float(prop_rings(f, hour_utc=hour_utc, f107=f107, kp=kp)["hop_km"]) for f in freqs
    )
    nvis_max = max(
        float(prop_rings(f, hour_utc=hour_utc, f107=f107, kp=kp)["nvis_km"]) for f in freqs
    )
    skip = exclude or set()
    forward_az = None
    if tx_lat is not None and tx_lon is not None:
        forward_az = (initial_bearing(lat, lon, float(tx_lat), float(tx_lon)) + 180.0) % 360.0
        # 1,5 saut : Brésil dans le prolongement, pas seulement l’Europe au 1er saut.
        radius = max(radius, hop_km * 1.7)

    def in_forward(az: float) -> bool:
        if forward_az is None:
            return True
        return azimuth_delta(az, forward_az) <= _OMNI_FORWARD_DEG

    cands: list[tuple[float, float, float, dict[str, Any]]] = []
    for kiwi in pool:
        key = kiwi_key(kiwi)
        if not key or key in skip:
            continue
        if int(kiwi.get("free_slots") or 0) < 1:
            continue
        try:
            klat, klon = float(kiwi["lat"]), float(kiwi["lon"])
        except (TypeError, ValueError, KeyError):
            continue
        dist = haversine_km(lat, lon, klat, klon)
        if dist > radius:
            continue
        az = initial_bearing(lat, lon, klat, klon)
        score = max(prop_score(dist, f, hour_utc=hour_utc, f107=f107, kp=kp) for f in freqs)
        snr = min(max(float(kiwi.get("snr_hf") or 0) / 40.0, 0.0), 1.0)
        free = min(max(float(kiwi.get("free_slots") or 0) / 4.0, 0.0), 1.0)
        total = 0.62 * score + 0.26 * snr + 0.12 * free
        cands.append((az, dist, total, kiwi))
    if not cands:
        return {}

    picked: list[dict[str, Any]] = []
    picked_keys: set[str] = set()

    def far_enough(kiwi: dict[str, Any]) -> bool:
        for other in picked:
            if haversine_km(float(kiwi["lat"]), float(kiwi["lon"]), float(other["lat"]), float(other["lon"])) < sep:
                return False
        return True

    def take(kiwi: dict[str, Any], dist: float, role: str, label: str) -> None:
        chosen = dict(kiwi)
        rings = prop_rings(freqs[0], hour_utc=hour_utc, f107=f107, kp=kp)
        chosen["site"] = role
        chosen["site_label"] = label
        chosen["site_km"] = round(dist, 1)
        chosen["prop_zone"] = prop_zone(dist, rings)
        chosen["omni_radius_km"] = round(radius, 0)
        picked.append(chosen)
        picked_keys.add(kiwi_key(kiwi))

    nvis = [
        row
        for row in cands
        if row[1] <= nvis_max and kiwi_key(row[3]) not in picked_keys
    ]
    nvis.sort(key=lambda row: (-row[2], row[1]))
    if nvis:
        _az, dist, _sc, kiwi = nvis[0]
        take(kiwi, dist, "nvis", "proche / NVIS")

    remaining = want - len(picked)
    if remaining > 0:
        # Secteurs dans le demi-espace « cap course » (prolongement TX→flotte).
        span = 2.0 * _OMNI_FORWARD_DEG if forward_az is not None else 360.0
        width = span / remaining
        if forward_az is not None:
            offset = (forward_az - span / 2.0) % 360.0
        else:
            offset = (
                0.0
                if not picked
                else (initial_bearing(lat, lon, float(picked[0]["lat"]), float(picked[0]["lon"])) + width / 2.0) % 360.0
            )

        def best_in(pred) -> tuple[float, float, float, dict[str, Any]] | None:
            hit = None
            best_sc = -1.0
            for az, dist, sc, kiwi in cands:
                if kiwi_key(kiwi) in picked_keys or not far_enough(kiwi):
                    continue
                if not pred(az):
                    continue
                if sc > best_sc:
                    hit, best_sc = (az, dist, sc, kiwi), sc
            return hit

        for i in range(remaining):
            a0 = (offset + i * width) % 360.0
            a1 = (a0 + width) % 360.0

            def in_sec(az: float, lo=a0, hi=a1) -> bool:
                return lo <= az < hi if lo < hi else az >= lo or az < hi

            best = best_in(lambda az: in_sec(az) and in_forward(az))
            if best is None:
                best = best_in(in_forward)
            if best is None:
                hit = None
                best_key = None
                for az, dist, sc, kiwi in cands:
                    if kiwi_key(kiwi) in picked_keys or not far_enough(kiwi):
                        continue
                    daz = 0.0 if forward_az is None else azimuth_delta(az, forward_az)
                    key = (daz, -sc)
                    if best_key is None or key < best_key:
                        hit, best_key = (az, dist, sc, kiwi), key
                best = hit
            if best is None:
                continue
            _az, dist, _sc, kiwi = best
            n = len(picked) + 1
            zone = prop_zone(dist, prop_rings(freqs[0], hour_utc=hour_utc, f107=f107, kp=kp))
            label = {"nvis": "proche / NVIS", "skip": "zone morte", "hop": "saut 1 hop", "far": "saut long"}.get(
                zone, "diversité"
            )
            take(kiwi, dist, f"omni{n}", label)

    out: dict[str, dict[str, Any]] = {}
    for kiwi in picked:
        out[str(kiwi["site"])] = kiwi
        log.info(
            "Kiwi omni %s → %s (%.0f km, zone %s)",
            kiwi.get("site_label"),
            kiwi.get("name"),
            kiwi.get("site_km") or 0,
            kiwi.get("prop_zone"),
        )
    return out


def assign_buddy_kiwis(
    pool: list[dict[str, Any]],
    *,
    lat: float,
    lon: float,
    cfg: dict[str, Any],
    tx_lat: float | None = None,
    tx_lon: float | None = None,
) -> dict[str, dict[str, Any]]:
    """Buddy 4483 / 6516 / 8294 / 12353 : 4–10 Kiwi omni autour du centroïde (NVIS + sauts)."""
    return assign_omni_fleet_kiwis(
        pool,
        lat=lat,
        lon=lon,
        freqs_khz=_buddy_freqs_khz(cfg),
        cfg=cfg,
        hour_utc=12.0,
        section=("buddy", "kiwi"),
        tx_lat=tx_lat,
        tx_lon=tx_lon,
    )


def pick_near_fleet_kiwis(
    pool: list[dict[str, Any]],
    *,
    lat: float,
    lon: float,
    count: int = 2,
    radius_km: float = 800.0,
    exclude: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Kiwi les plus proches du centroïde (écoute « comme à la flotte »), pas le ranking SNR."""
    skip = exclude or set()
    scored: list[tuple[float, float, dict[str, Any]]] = []
    for kiwi in pool:
        key = kiwi_key(kiwi)
        if not key or key in skip:
            continue
        try:
            klat, klon = float(kiwi["lat"]), float(kiwi["lon"])
        except (TypeError, ValueError, KeyError):
            continue
        dist = float(kiwi.get("distance_km") or haversine_km(lat, lon, klat, klon))
        if dist > float(radius_km):
            continue
        scored.append((dist, -float(kiwi.get("snr_hf") or 0.0), kiwi))
    scored.sort(key=lambda row: (row[0], row[1]))
    out: list[dict[str, Any]] = []
    for dist, _snr, kiwi in scored[: max(0, int(count))]:
        chosen = dict(kiwi)
        chosen["site"] = "fleet"
        chosen["site_label"] = "écoute flotte"
        chosen["site_km"] = round(dist, 1)
        out.append(chosen)
    return out


def score_kiwi(
    kiwi: dict[str, Any],
    fleet_lat: float,
    fleet_lon: float,
) -> float:
    """Score 0–1 : proximité flotte, SNR HF, places libres."""
    dist = float(kiwi.get("distance_km") or 0)
    snr = min(max(float(kiwi.get("snr_hf") or 0) / 40.0, 0.0), 1.0)
    free = min(max(float(kiwi.get("free_slots") or 0) / 4.0, 0.0), 1.0)
    near = 1.0 / (1.0 + dist / 2000.0)
    return 0.45 * near + 0.40 * snr + 0.15 * free


def normalize_receiver(
    row: dict[str, Any],
    fleet_lat: float,
    fleet_lon: float,
    cfg: dict[str, Any],
    *,
    min_free: int | None = None,
    cover_hz: list[int] | None = None,
    score_mode: str = "fleet",
) -> dict[str, Any] | None:
    if str(row.get("offline") or "").lower() not in ("no", "0", ""):
        return None
    if str(row.get("status") or "active").lower() not in ("active", ""):
        return None
    gps = _parse_gps(str(row.get("gps") or ""))
    if not gps:
        return None
    bands = _parse_bands(str(row.get("bands") or ""))
    sdr_cfg = cfg.get("sdr") or {}
    if cover_hz:
        if not _covers_freqs(bands, cover_hz):
            return None
    else:
        min_hz = int(sdr_cfg.get("min_freq_hz") or 12_000_000)
        max_hz = int(sdr_cfg.get("max_freq_hz") or 17_000_000)
        if not _covers_hf(bands, min_hz, max_hz):
            return None
    url = str(row.get("url") or "").strip()
    hp = _host_port(url)
    if not hp:
        return None
    host, port, https = hp
    try:
        users = int(row.get("users") or 0)
        users_max = int(row.get("users_max") or 0)
    except (TypeError, ValueError):
        return None
    free = max(0, users_max - users)
    min_free_slots = int(min_free if min_free is not None else sdr_cfg.get("min_free_slots") or 1)
    if free < min_free_slots:
        return None
    if str(row.get("ant_connected") or "1") in ("0", "no", "false"):
        return None
    dist = haversine_km(fleet_lat, fleet_lon, gps[0], gps[1])
    kiwi = {
        "id": row.get("id"),
        "name": row.get("name") or host,
        "url": url.rstrip("/"),
        "host": host,
        "port": port,
        "https": https,
        "lat": gps[0],
        "lon": gps[1],
        "locator": row.get("grid"),
        "loc": row.get("loc"),
        "antenna": row.get("antenna"),
        "snr_hf": _snr_hf(str(row.get("snr") or "")),
        "users": users,
        "users_max": users_max,
        "free_slots": free,
        "distance_km": round(dist, 1),
        "fmt": fmt_latlon(gps[0], gps[1]),
        "bands_hz": list(bands) if bands else None,
        "prop_zone": hf_midday_zone(dist),
    }
    if score_mode == "buddy":
        kiwi["score"] = round(score_buddy_kiwi(kiwi, fleet_lat, fleet_lon, _buddy_freqs_khz(cfg)), 4)
    else:
        kiwi["score"] = round(score_kiwi(kiwi, fleet_lat, fleet_lon), 4)
    return kiwi


async def fetch_ranked_kiwis(
    cfg: dict[str, Any],
    fleet_lat: float,
    fleet_lon: float,
    client: Any | None = None,
    limit: int = 12,
    min_free: int | None = None,
    cover_hz: list[int] | None = None,
    score_mode: str = "fleet",
) -> list[dict[str, Any]]:
    rows = await kiwi_directory(cfg, client=client)
    ranked: list[dict[str, Any]] = []
    for row in rows:
        kiwi = normalize_receiver(
            row,
            fleet_lat,
            fleet_lon,
            cfg,
            min_free=min_free,
            cover_hz=cover_hz,
            score_mode=score_mode,
        )
        if kiwi:
            ranked.append(kiwi)
    ranked.sort(key=lambda k: k["score"], reverse=True)
    log.info("KiwiSDR : %s récepteurs classés (flotte %.3f, %.3f)", len(ranked), fleet_lat, fleet_lon)
    if limit and limit > 0:
        return ranked[:limit]
    return ranked


# Colormap WF Kiwi (URL ``wfm=min,max``). Les défauts station sont souvent
# −140 / −10 dB : une USB faible reste audible (AGC) mais invisible sur le WF.
WF_MIN_DB = -110
WF_MAX_DB = -40


def kiwi_tune_url(kiwi: dict[str, Any], freq_khz: float, mode: str = "usb", zoom: int = 10) -> str:
    """URL KiwiSDR pré-accordée (QRG kHz + mode USB + zoom waterfall)."""
    base = kiwi["url"].rstrip("/")
    return (
        f"{base}/?f={freq_khz:.2f}{mode}z{int(zoom)}"
        f"&wfm={int(WF_MIN_DB)},{int(WF_MAX_DB)}"
    )
