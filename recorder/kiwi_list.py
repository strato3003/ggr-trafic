"""Sélection des KiwiSDR les plus adaptés à la position de la flotte."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from recorder.geo import fmt_latlon, haversine_km, initial_bearing

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
    d = float(dist_km)
    if d <= 850:
        return "nvis"
    if d <= 1400:
        return "skip"
    if d <= 3200:
        return "hop"
    if d <= 5500:
        return "far"
    return "dx"


def hf_midday_prop_score(dist_km: float, freq_khz: float) -> float:
    """Score 0–1 : NVIS / zone morte / 1 saut F, midi TU.

    Heuristique générale (pas un modèle VOACAP) : vers 12:00 TU la couche D
    absorbe le 4 MHz sur les trajets longs ; le 6 MHz ouvre plutôt en 1 saut
    (~1400–3200 km). Un Kiwi dans la zone morte (~850–1400 km) est pénalisé.
    """
    d = float(dist_km)
    f = float(freq_khz)
    if f < 5500.0:
        if d <= 850:
            return 1.0
        if d <= 1400:
            return 0.18
        if d <= 2500:
            return 0.42
        if d <= 4000:
            return 0.22
        return 0.10
    if d <= 500:
        return 0.72
    if d <= 1400:
        return 0.28
    if d <= 3200:
        return 1.0
    if d <= 5500:
        return 0.58
    return 0.20


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


def bulletin_tx_qth(cfg: dict[str, Any], fleet_lat: float, fleet_lon: float) -> dict[str, Any]:
    """QTH d’émission du bulletin 14.135 MHz : F6KUF, ou Tahiti après le cap."""
    sites = (cfg.get("sdr") or {}).get("sites") or {}
    if fleet_uses_tahiti_tx(fleet_lat, fleet_lon):
        tahiti = sites.get("tahiti") or {}
        return {
            "id": "tahiti",
            "label": tahiti.get("label") or "Tahiti",
            "lat": float(tahiti.get("lat") if tahiti.get("lat") is not None else -17.5350),
            "lon": float(tahiti.get("lon") if tahiti.get("lon") is not None else -149.5697),
            "radius_km": float(tahiti.get("radius_km") or 2500),
        }
    france = sites.get("france") or {}
    return {
        "id": "france",
        "label": france.get("label") or "F6KUF",
        "lat": float(france.get("lat") if france.get("lat") is not None else 46.5025),
        "lon": float(france.get("lon") if france.get("lon") is not None else -1.7888),
        "radius_km": float(france.get("radius_km") or 1500),
    }


def listen_sites(cfg: dict[str, Any], fleet_lat: float, fleet_lon: float) -> list[dict[str, Any]]:
    """Sites d’écoute ACK : près de la flotte, France (F6KUF), Tahiti (relais océan Indien)."""
    raw = ((cfg.get("sdr") or {}).get("sites") or {})
    france = raw.get("france") or {}
    tahiti = raw.get("tahiti") or {}
    return [
        {
            "id": "fleet",
            "label": "flotte",
            "lat": float(fleet_lat),
            "lon": float(fleet_lon),
            "radius_km": None,
        },
        {
            "id": "france",
            "label": france.get("label") or "France",
            "lat": float(france.get("lat") if france.get("lat") is not None else 46.5025),
            "lon": float(france.get("lon") if france.get("lon") is not None else -1.7888),
            "radius_km": float(france.get("radius_km") or 1500),
        },
        {
            "id": "tahiti",
            "label": tahiti.get("label") or "Tahiti",
            "lat": float(tahiti.get("lat") if tahiti.get("lat") is not None else -17.5350),
            "lon": float(tahiti.get("lon") if tahiti.get("lon") is not None else -149.5697),
            "radius_km": float(tahiti.get("radius_km") or 2500),
        },
    ]


def assign_vacation_kiwis(
    pool: list[dict[str, Any]],
    *,
    fleet_lat: float,
    fleet_lon: float,
    cfg: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Bulletin 14.135 : Kiwi près de l’émetteur (F6KUF ou Tahiti) + Kiwi près de la flotte.

    ACK : flotte + France + Tahiti, Kiwi distincts.
    """
    sdr = cfg.get("sdr") or {}
    tx_slots = int(sdr.get("min_free_slots") or 2)
    out: dict[str, dict[str, Any]] = {}
    used: set[str] = set()

    club = bulletin_tx_qth(cfg, fleet_lat, fleet_lon)
    tx = pick_nearest(
        pool,
        float(club["lat"]),
        float(club["lon"]),
        min_free=tx_slots,
        min_snr=5.0,
        radius_km=club.get("radius_km"),
    )
    if tx is None:
        tx = pick_nearest(pool, float(club["lat"]), float(club["lon"]), min_free=1, min_snr=0.0)
    if tx is None:
        return out
    chosen_tx = dict(tx)
    chosen_tx["site"] = "tx"
    chosen_tx["site_label"] = f"{club['label']} (bulletin)"
    chosen_tx["club_id"] = club["id"]
    chosen_tx["site_km"] = round(haversine_km(float(club["lat"]), float(club["lon"]), float(tx["lat"]), float(tx["lon"])), 1)
    out["tx"] = chosen_tx
    used.add(kiwi_key(tx))
    log.info(
        "Kiwi TX bulletin émetteur → %s (%s, %.0f km)",
        chosen_tx.get("name"),
        club["label"],
        chosen_tx["site_km"],
    )

    fleet_tx = pick_nearest(pool, fleet_lat, fleet_lon, exclude=used, min_free=1, min_snr=5.0)
    if fleet_tx is None:
        fleet_tx = pick_nearest(pool, fleet_lat, fleet_lon, exclude=used, min_free=1, min_snr=0.0)
    if fleet_tx is not None:
        chosen_fleet = dict(fleet_tx)
        chosen_fleet["site"] = "tx_fleet"
        chosen_fleet["site_label"] = "flotte (bulletin)"
        chosen_fleet["site_km"] = round(
            haversine_km(fleet_lat, fleet_lon, float(fleet_tx["lat"]), float(fleet_tx["lon"])),
            1,
        )
        out["tx_fleet"] = chosen_fleet
        used.add(kiwi_key(fleet_tx))
        log.info(
            "Kiwi TX bulletin flotte → %s (%.0f km de la flotte)",
            chosen_fleet.get("name"),
            chosen_fleet["site_km"],
        )

    for site in listen_sites(cfg, fleet_lat, fleet_lon):
        kiwi = pick_nearest(
            pool,
            float(site["lat"]),
            float(site["lon"]),
            exclude=used,
            min_free=1,
            min_snr=5.0,
            radius_km=site.get("radius_km"),
        )
        if kiwi is None:
            log.info("Aucun Kiwi distinct pour l’ACK %s", site["label"])
            continue
        chosen = dict(kiwi)
        chosen["site"] = site["id"]
        chosen["site_label"] = site["label"]
        chosen["site_km"] = round(
            haversine_km(float(site["lat"]), float(site["lon"]), float(kiwi["lat"]), float(kiwi["lon"])),
            1,
        )
        out[str(site["id"])] = chosen
        used.add(kiwi_key(kiwi))
        log.info("Kiwi ACK %s → %s (%.0f km du site)", site["label"], chosen.get("name"), chosen["site_km"])
    _pad_spread_kiwis(out, pool, used, want=4, lat=fleet_lat, lon=fleet_lon)
    return out


def _buddy_freqs_khz(cfg: dict[str, Any]) -> list[float]:
    buddy = cfg.get("buddy") or {}
    main = float((buddy.get("main") or {}).get("freq_khz") or 4483.0)
    alt = float((buddy.get("alternate") or {}).get("freq_khz") or 6516.0)
    return [main, alt]


def assign_buddy_kiwis(
    pool: list[dict[str, Any]],
    *,
    lat: float,
    lon: float,
    cfg: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Plusieurs Kiwi : NVIS, saut 1 hop, second azimut, puis complément distant.

    Le plus proche n’est retenu que s’il est en zone NVIS ; un récepteur dans
    la zone morte (~1000 km) est évité au profit d’un 1 saut vers 6 MHz.
    """
    kiwi_cfg = ((cfg.get("buddy") or {}).get("kiwi") or {})
    want = 4
    sep = float(kiwi_cfg.get("min_separation_km") or 400)
    used: set[str] = set()
    out: dict[str, dict[str, Any]] = {}

    def far_enough(kiwi: dict[str, Any]) -> bool:
        for other in out.values():
            if haversine_km(float(kiwi["lat"]), float(kiwi["lon"]), float(other["lat"]), float(other["lon"])) < sep:
                return False
        return True

    def take(role: str, label: str, pred, *, min_az_from: float | None = None) -> dict[str, Any] | None:
        best: dict[str, Any] | None = None
        best_score = -1.0
        for kiwi in pool:
            if kiwi_key(kiwi) in used:
                continue
            if not pred(kiwi):
                continue
            if out and not far_enough(kiwi):
                continue
            if min_az_from is not None:
                az = initial_bearing(lat, lon, float(kiwi["lat"]), float(kiwi["lon"]))
                if _az_sep(az, min_az_from) < 50.0:
                    continue
            sc = float(kiwi.get("score") or 0.0)
            if sc > best_score:
                best, best_score = kiwi, sc
        if best is None:
            return None
        chosen = dict(best)
        dist = round(haversine_km(lat, lon, float(best["lat"]), float(best["lon"])), 1)
        chosen["site"] = role
        chosen["site_label"] = label
        chosen["site_km"] = dist
        chosen["prop_zone"] = hf_midday_zone(dist)
        out[role] = chosen
        used.add(kiwi_key(best))
        log.info(
            "Kiwi buddy %s → %s (%.0f km, zone %s, score %s)",
            label,
            chosen.get("name"),
            dist,
            chosen["prop_zone"],
            chosen.get("score"),
        )
        return chosen

    take("nvis", "proche / NVIS", lambda k: float(k.get("distance_km") or 0) <= 850)
    hop = take("hop", "saut 1 hop", lambda k: 1400 <= float(k.get("distance_km") or 0) <= 3200)
    hop_az = None
    if hop:
        hop_az = initial_bearing(lat, lon, float(hop["lat"]), float(hop["lon"]))
    take(
        "hop2",
        "saut 1 hop (autre azimut)",
        lambda k: 1400 <= float(k.get("distance_km") or 0) <= 4000,
        min_az_from=hop_az,
    )
    take("far", "saut long", lambda k: 2800 <= float(k.get("distance_km") or 0) <= 5500)

    n = 0
    while len(out) < want:
        n += 1
        extra = take(f"rx{n}", "diversité", lambda k: hf_midday_zone(float(k.get("distance_km") or 0)) != "skip")
        if extra is None:
            extra = take(f"rx{n}", "diversité", lambda _k: True)
        if extra is None:
            break
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
