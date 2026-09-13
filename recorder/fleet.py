"""Position moyenne de la flotte GGR via Yellowbrick (binaire Positions3)."""

from __future__ import annotations

import asyncio
import logging
import struct
import time
import unicodedata
from datetime import datetime, timezone
from typing import Any

from recorder.geo import centroid, fmt_latlon, haversine_km, initial_bearing

log = logging.getLogger(__name__)

# Identifiants historiques / « fantômes » sur le tracker GGR 2026 (RKJ, Moitessier, etc.)
GHOST_TEAM_ID_FROM = 900


def parse_positions3(buf: bytes) -> list[dict[str, Any]]:
    """Décode le binaire Yellowbrick Positions3 (AllPositions3 / LatestPositions3).

    Format public du viewer YB : drapeau, epoch, puis par bateau une liste de
    « moments » en coordonnées × 1e5. Implémentation originale d'après le
    protocole du viewer (DataView big-endian), pas une copie du JS minifié.
    """
    if len(buf) < 5:
        return []
    flags = buf[0]
    has_alt = bool(flags & 1)
    has_dtf = bool(flags & 2)
    has_lap = bool(flags & 4)
    has_pc = bool(flags & 8)
    epoch = struct.unpack(">I", buf[1:5])[0]
    offset = 5
    teams: list[dict[str, Any]] = []
    end = len(buf)
    while offset + 4 <= end:
        team_id = struct.unpack(">H", buf[offset : offset + 2])[0]
        offset += 2
        n_moments = struct.unpack(">H", buf[offset : offset + 2])[0]
        offset += 2
        moments: list[dict[str, Any]] = []
        prev: dict[str, Any] | None = None
        for _ in range(n_moments):
            if offset >= end:
                break
            first = buf[offset]
            moment: dict[str, Any] = {}
            if first & 128:
                if prev is None:
                    break
                packed = struct.unpack(">H", buf[offset : offset + 2])[0]
                offset += 2
                dlat = struct.unpack(">h", buf[offset : offset + 2])[0]
                offset += 2
                dlon = struct.unpack(">h", buf[offset : offset + 2])[0]
                offset += 2
                if has_alt:
                    moment["alt"] = struct.unpack(">h", buf[offset : offset + 2])[0]
                    offset += 2
                if has_dtf:
                    dtf_delta = struct.unpack(">h", buf[offset : offset + 2])[0]
                    offset += 2
                    moment["dtf"] = prev.get("dtf", 0) + dtf_delta
                    if has_lap:
                        moment["lap"] = buf[offset]
                        offset += 1
                if has_pc:
                    pc_delta = struct.unpack(">h", buf[offset : offset + 2])[0] / 32000.0
                    offset += 2
                    moment["pc"] = prev.get("pc", 0.0) + pc_delta
                moment["lat"] = prev["lat"] + dlat
                moment["lon"] = prev["lon"] + dlon
                moment["at"] = prev["at"] - (packed & 32767)
            else:
                dt = struct.unpack(">I", buf[offset : offset + 4])[0]
                offset += 4
                lat_i = struct.unpack(">i", buf[offset : offset + 4])[0]
                offset += 4
                lon_i = struct.unpack(">i", buf[offset : offset + 4])[0]
                offset += 4
                if has_alt:
                    moment["alt"] = struct.unpack(">h", buf[offset : offset + 2])[0]
                    offset += 2
                if has_dtf:
                    moment["dtf"] = struct.unpack(">i", buf[offset : offset + 4])[0]
                    offset += 4
                    if has_lap:
                        moment["lap"] = buf[offset]
                        offset += 1
                if has_pc:
                    moment["pc"] = struct.unpack(">i", buf[offset : offset + 4])[0] / 21000000.0
                    offset += 4
                moment["lat"] = lat_i
                moment["lon"] = lon_i
                moment["at"] = epoch + dt
            moments.append(moment)
            prev = moment
        for moment in moments:
            moment["lat"] = moment["lat"] / 1e5
            moment["lon"] = moment["lon"] / 1e5
        teams.append({"id": team_id, "moments": moments})
    return teams


def _is_racing_team(team: dict[str, Any], skip_from: int) -> bool:
    if int(team.get("id") or 0) >= skip_from:
        return False
    status = str(team.get("status") or "").upper()
    return status in ("", "RACING")


def _latest_fix(moments: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not moments:
        return None
    return max(moments, key=lambda m: m.get("at") or 0)


def _team_colour(meta: dict[str, Any]) -> str:
    raw = str(meta.get("colour") or "c9a227").strip().lstrip("#")
    if len(raw) in (3, 6) and all(c in "0123456789abcdefABCDEF" for c in raw):
        return f"#{raw}"
    return "#c9a227"


def _heading_deg(moments: list[dict[str, Any]]) -> float | None:
    """Cap d’après les deux derniers points distincts (≥ 50 m)."""
    ordered = sorted(moments, key=lambda m: m.get("at") or 0)
    if len(ordered) < 2:
        return None
    last = ordered[-1]
    for prev in reversed(ordered[:-1]):
        if haversine_km(prev["lat"], prev["lon"], last["lat"], last["lon"]) >= 0.05:
            return round(initial_bearing(prev["lat"], prev["lon"], last["lat"], last["lon"]), 1)
    return None


def _sog_kn(moments: list[dict[str, Any]]) -> float | None:
    """Vitesse fond (kn) entre les deux derniers points GPS distincts."""
    ordered = sorted(moments, key=lambda m: m.get("at") or 0)
    if len(ordered) < 2:
        return None
    last = ordered[-1]
    for prev in reversed(ordered[:-1]):
        dt = float(last.get("at") or 0) - float(prev.get("at") or 0)
        if dt <= 0:
            continue
        dist_km = haversine_km(prev["lat"], prev["lon"], last["lat"], last["lon"])
        if dist_km < 0.05:
            continue
        return round((dist_km / 1.852) / (dt / 3600.0), 1)
    return None


def _nm(metres: Any) -> float | None:
    try:
        val = float(metres)
    except (TypeError, ValueError):
        return None
    return round(val / 1852.0, 1)


def _gps_at(epoch: Any) -> str | None:
    try:
        ts = int(epoch)
    except (TypeError, ValueError):
        return None
    if ts <= 0:
        return None
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M TU")


def _leaderboard_by_id(payload: dict[str, Any] | None) -> dict[int, dict[str, Any]]:
    best: list[dict[str, Any]] = []
    for tag in (payload or {}).get("tags") or []:
        teams = [t for t in (tag.get("teams") or []) if isinstance(t, dict) and "id" in t]
        if len(teams) > len(best):
            best = teams
    return {int(t["id"]): t for t in best}


def parse_yb_grib2(buf: bytes, epoch0: int) -> list[dict[str, Any]]:
    """Décode le GRIB2 Yellowbrick (PredictWind) : vent kn + direction °."""
    if not buf:
        return []
    n = buf[0]
    offset = 1
    end = len(buf)
    grids: list[dict[str, Any]] = []
    try:
        for _ in range(n):
            if offset + 5 > end:
                break
            offset += 4
            n_grids = buf[offset]
            offset += 1
            for _g in range(n_grids):
                if offset + 19 > end:
                    return grids
                at = struct.unpack(">i", buf[offset : offset + 4])[0] + int(epoch0)
                offset += 4
                lat0 = struct.unpack(">i", buf[offset : offset + 4])[0] / 1e5
                offset += 4
                lon0 = struct.unpack(">i", buf[offset : offset + 4])[0] / 1e5
                offset += 4
                space = buf[offset] / 100.0
                offset += 1
                ncols = struct.unpack(">H", buf[offset : offset + 2])[0]
                offset += 2
                npts = struct.unpack(">I", buf[offset : offset + 4])[0]
                offset += 4
                need = npts * 2
                if offset + need > end:
                    return grids
                dirs = [2 * buf[offset + 2 * i] for i in range(npts)]
                spds = [3 * buf[offset + 2 * i + 1] / 10.0 for i in range(npts)]
                offset += need
                grids.append(
                    {
                        "at": at,
                        "lat": lat0,
                        "lon": lon0,
                        "space": space,
                        "ncols": max(int(ncols), 1),
                        "dirs": dirs,
                        "spds": spds,
                    }
                )
    except (struct.error, IndexError):
        return grids
    return grids


def _wind_at(
    grids: list[dict[str, Any]], lat: float, lon: float, at: int | None
) -> tuple[float, float] | None:
    """Vent GRIB le plus proche (kn, °) à l’heure GPS du bateau."""
    if not grids:
        return None
    target = int(at or 0)
    slot = min((int(g["at"]) for g in grids), key=lambda t: abs(t - target) if target else t)
    best_d = 1e18
    best: tuple[float, float] | None = None
    for g in grids:
        if int(g["at"]) != slot:
            continue
        ncols = int(g["ncols"])
        space = float(g["space"] or 1)
        for idx, spd in enumerate(g["spds"]):
            row, col = divmod(idx, ncols)
            glat = float(g["lat"]) + space * row
            glon = ((float(g["lon"]) + space * col + 540) % 360) - 180
            dlat = glat - lat
            dlon = ((glon - lon + 180) % 360) - 180
            dist2 = dlat * dlat + dlon * dlon
            if dist2 < best_d:
                best_d = dist2
                best = (round(float(spd), 1), int(g["dirs"][idx]) % 360)
    if best is None or best_d > 9:
        return None
    return best


_grib_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_GRIB_TTL_S = 3600.0


def _track_tail(moments: list[dict[str, Any]], limit: int = 36) -> list[list[float]]:
    ordered = sorted(moments, key=lambda m: m.get("at") or 0)[-limit:]
    return [[float(m["lat"]), float(m["lon"])] for m in ordered]


async def fetch_fleet(
    cfg: dict[str, Any], client: Any | None = None, *, with_wx: bool = False
) -> dict[str, Any]:
    """Retourne le centroïde de la flotte en course, avec repli configuré."""
    import httpx

    fleet_cfg = cfg.get("fleet") or {}
    skip_from = int(fleet_cfg.get("skip_team_id_from") or GHOST_TEAM_ID_FROM)
    fallback = fleet_cfg.get("fallback") or {}
    result: dict[str, Any] = {
        "source": "fallback",
        "lat": float(fallback.get("lat", 46.5025)),
        "lon": float(fallback.get("lon", -1.7888)),
        "label": fallback.get("label") or "Position de repli",
        "n_boats": 0,
        "boats": [],
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }

    if str(fleet_cfg.get("source") or "") != "yellowbrick":
        result["fmt"] = fmt_latlon(result["lat"], result["lon"])
        return result

    race_id = fleet_cfg.get("race_id") or "ggr2026"
    host = fleet_cfg.get("tracker_host") or "cf.yb.tl"
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=30.0, headers={"User-Agent": "ggr-vacations/0.1"})
    try:
        setup_url = f"https://yb.tl/JSON/{race_id}/RaceSetup"
        pos_url = f"https://{host}/BIN/{race_id}/AllPositions3"
        lb_url = f"https://{host}/JSON/{race_id}/Leaderboard"
        setup_resp, pos_resp, lb_resp = await asyncio.gather(
            client.get(setup_url),
            client.get(pos_url),
            client.get(lb_url),
            return_exceptions=True,
        )
        if isinstance(setup_resp, Exception) or isinstance(pos_resp, Exception):
            raise setup_resp if isinstance(setup_resp, Exception) else pos_resp
        setup_resp.raise_for_status()
        pos_resp.raise_for_status()
        setup = setup_resp.json()
        teams_meta = {int(t["id"]): t for t in setup.get("teams") or [] if "id" in t}
        parsed = parse_positions3(pos_resp.content)
        lb_by: dict[int, dict[str, Any]] = {}
        if not isinstance(lb_resp, Exception):
            try:
                lb_resp.raise_for_status()
                lb_by = _leaderboard_by_id(lb_resp.json())
            except Exception:
                log.warning("Leaderboard Yellowbrick indisponible")
        grids: list[dict[str, Any]] = []
        if with_wx:
            try:
                epoch0 = int(setup.get("start") or 0)
                day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                grib_key = f"{race_id}:{day}"
                now_m = time.monotonic()
                hit = _grib_cache.get(grib_key)
                if hit and now_m - hit[0] < _GRIB_TTL_S:
                    grids = hit[1]
                else:
                    grib_resp = await client.get(
                        f"https://{host}/BIN/{race_id}/Grib2/{day}", timeout=12.0
                    )
                    grib_resp.raise_for_status()
                    grids = parse_yb_grib2(grib_resp.content, epoch0)
                    _grib_cache[grib_key] = (now_m, grids)
            except Exception:
                log.warning("GRIB Yellowbrick indisponible")
        boats: list[dict[str, Any]] = []
        points: list[tuple[float, float]] = []
        for team in parsed:
            tid = int(team["id"])
            meta = teams_meta.get(tid) or {"id": tid, "name": f"Bateau {tid}", "status": "RACING"}
            if not _is_racing_team(meta, skip_from):
                continue
            moments = team.get("moments") or []
            fix = _latest_fix(moments)
            if not fix:
                continue
            lb = lb_by.get(tid) or {}
            vmg_kmh = lb.get("vmgR")
            try:
                vmg_kn = round(float(vmg_kmh) / 1.852, 1) if vmg_kmh is not None else None
            except (TypeError, ValueError):
                vmg_kn = None
            wind = _wind_at(grids, float(fix["lat"]), float(fix["lon"]), fix.get("at")) if grids else None
            finish_at = lb.get("eFinishR")
            boats.append(
                {
                    "id": tid,
                    "name": meta.get("name"),
                    "sail": meta.get("sail"),
                    "status": meta.get("status") or lb.get("status"),
                    "country": meta.get("country"),
                    "flag": meta.get("flag"),
                    "model": meta.get("model"),
                    "owner": meta.get("owner"),
                    "colour": _team_colour(meta),
                    "lat": fix["lat"],
                    "lon": fix["lon"],
                    "heading": _heading_deg(moments),
                    "sog_kn": _sog_kn(moments),
                    "track": _track_tail(moments),
                    "at": fix.get("at"),
                    "gps_at": _gps_at(fix.get("at")),
                    "dtf_nm": _nm(fix.get("dtf") if fix.get("dtf") is not None else lb.get("dtf")),
                    "d24_nm": _nm(lb.get("d24")),
                    "dmg_nm": _nm(lb.get("dmg")),
                    "vmg_kn": vmg_kn,
                    "rank": lb.get("rankR"),
                    "finish_at": _gps_at(finish_at) if finish_at else None,
                    "wind_kn": wind[0] if wind else None,
                    "wind_deg": wind[1] if wind else None,
                }
            )
            points.append((fix["lat"], fix["lon"]))
        center = centroid(points)
        if center:
            result.update(
                {
                    "source": "yellowbrick",
                    "lat": center[0],
                    "lon": center[1],
                    "label": f"Centroïde flotte GGR ({len(points)} bateaux)",
                    "n_boats": len(points),
                    "boats": boats,
                    "race_id": race_id,
                }
            )
        else:
            result["warning"] = "Tracker joignable mais aucune position récente"
            log.warning("Flotte GGR : aucune position exploitable, repli utilisé")
    except Exception as exc:
        result["warning"] = f"Tracker indisponible : {exc}"
        log.warning("Flotte GGR : %s", exc)
    finally:
        if owns_client:
            await client.aclose()
    result["fmt"] = fmt_latlon(result["lat"], result["lon"])
    return result


def _fold_name(text: str) -> str:
    """Minuscules, sans accents, espaces normalisés — pour matcher les skippers."""
    nfkd = unicodedata.normalize("NFKD", text or "")
    stripped = "".join(ch for ch in nfkd if not unicodedata.combining(ch))
    return " ".join(stripped.lower().split())


def skipper_matches(boat: dict[str, Any], names: list[str], team_ids: list[int]) -> bool:
    if team_ids and int(boat.get("id") or 0) in team_ids:
        return True
    boat_name = _fold_name(str(boat.get("name") or ""))
    if not boat_name:
        return False
    for raw in names:
        needle = _fold_name(str(raw))
        if needle and (needle == boat_name or needle in boat_name or boat_name in needle):
            return True
    return False


def buddy_aim(fleet: dict[str, Any], cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Centroïde d’écoute buddy : trio (ou liste) avec ou sans le reste de la flotte."""
    cfg = cfg or {}
    buddy = cfg.get("buddy") or {}
    cent = buddy.get("centroid") or {}
    names = [str(x).strip() for x in (cent.get("skippers") or []) if str(x).strip()]
    try:
        team_ids = [int(x) for x in (cent.get("team_ids") or [])]
    except (TypeError, ValueError):
        team_ids = []
    include_fleet = bool(cent.get("include_fleet"))
    boats = [b for b in (fleet.get("boats") or []) if isinstance(b, dict)]
    core = [b for b in boats if skipper_matches(b, names, team_ids)]
    chosen = boats if include_fleet else core
    points = [
        (float(b["lat"]), float(b["lon"]))
        for b in chosen
        if b.get("lat") is not None and b.get("lon") is not None
    ]
    center = centroid(points) if points else None
    core_names = [str(b.get("name") or "?").strip() for b in core]
    if center:
        if include_fleet:
            label = f"Centroïde flotte ({len(points)} bateaux"
            if core_names:
                label += f", dont {', '.join(core_names)}"
            label += ")"
        else:
            label = "Centroïde buddy : " + (", ".join(core_names) or "aucun skipper")
        return {
            "lat": center[0],
            "lon": center[1],
            "fmt": fmt_latlon(center[0], center[1]),
            "label": label,
            "n_boats": len(points),
            "skippers": core,
            "skipper_names": core_names,
            "include_fleet": include_fleet,
            "source": fleet.get("source") or "buddy",
        }
    return {
        "lat": float(fleet.get("lat") or 46.5025),
        "lon": float(fleet.get("lon") or -1.7888),
        "fmt": fleet.get("fmt") or fmt_latlon(float(fleet.get("lat") or 46.5025), float(fleet.get("lon") or -1.7888)),
        "label": (fleet.get("label") or "Flotte") + " (repli buddy)",
        "n_boats": int(fleet.get("n_boats") or 0),
        "skippers": core,
        "skipper_names": core_names,
        "include_fleet": include_fleet,
        "source": fleet.get("source") or "fallback",
        "warning": "Skippers buddy introuvables — centroïde flotte utilisé",
    }
