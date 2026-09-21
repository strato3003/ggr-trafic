"""Visites de l’UI publique : comptage Prometheus + géoloc (sans IP dans les métriques)."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import socket
from typing import Any

import httpx
from prometheus_client import Counter, Gauge
from starlette.requests import Request

from recorder.config import version

log = logging.getLogger(__name__)

# Pages HTML réellement rendues (pas les redirections /flotte, /a-propos, sondes, tuiles).
_PAGE_EXACT = {"/"}
_PAGE_PREFIXES = ("/trafic/",)
_SKIP_PREFIXES = ("/health", "/metrics", "/static", "/api/", "/media/", "/auth", "/login")

# Coordonnées absentes : Prometheus exige les mêmes labels à chaque observation.
NO_COORD = "none"

VISITS = Counter(
    "ggr_http_visits_total",
    "Chargements de pages GGR Trafic (hors sondes, API, tuiles, static)",
    ["country", "city", "latitude", "longitude", "path"],
)
UNIQUE = Gauge(
    "ggr_http_visitors",
    "Adresses IP publiques distinctes depuis le démarrage du pod (jamais exposées en label)",
)
REPLAYS = Counter(
    "ggr_http_replays_total",
    "Lancements de lecture mixer (bouton Lecture), par enregistrement",
    ["country", "city", "replay"],
)

_unique_ips: set[str] = set()
_geo_cache: dict[str, dict[str, str]] = {}
_pending: set[str] = set()
# Surcharge tests (ip → dict geo).
lookup_override = None


def reset_for_tests() -> None:
    """Réinitialise le cache (les compteurs Prometheus ne se remettent pas à zéro)."""
    _unique_ips.clear()
    _geo_cache.clear()
    _pending.clear()
    UNIQUE.set(0)
    global lookup_override
    lookup_override = None


def is_page_visit(method: str, path: str) -> bool:
    if method != "GET":
        return False
    if path.startswith(_SKIP_PREFIXES):
        return False
    if path in _PAGE_EXACT:
        return True
    return any(path.startswith(p) for p in _PAGE_PREFIXES)


def page_label(path: str) -> str:
    """Chemin agrégé pour Prometheus / journal (pas d’IP, pas de query-string)."""
    raw = (path or "/").split("?", 1)[0]
    if not raw.startswith("/"):
        raw = "/" + raw
    if raw.startswith("/#"):
        tab = raw[2:].split("/", 1)[0].lower()
        if tab in {"trafic", "metarea", "setup", "apropos"}:
            return "/#" + tab
        return "/"
    if raw != "/" and raw.endswith("/"):
        raw = raw.rstrip("/") or "/"
    if raw in _PAGE_EXACT:
        return "/"
    if raw.startswith("/trafic/"):
        vid = _lab(raw[8:].split("/", 1)[0], fallback="")
        return "/trafic/" + vid if vid else "/trafic"
    if raw.startswith("/api/trafic/") and raw.endswith("/play"):
        vid = _lab(raw[12:].split("/", 1)[0], fallback="")
        return "/api/trafic/" + vid + "/play" if vid else "/api/trafic/play"
    return _lab(raw)[:80]


def is_public_ip(raw: str) -> bool:
    try:
        return ipaddress.ip_address(raw.strip()).is_global
    except ValueError:
        return False


def _clean_ip(raw: str) -> str:
    """Retire quotes, crochets IPv6 et port (X-Forwarded-For / Forwarded)."""
    s = (raw or "").strip().strip('"').strip("'")
    if s.startswith("["):
        end = s.find("]")
        if end > 0:
            return s[1:end]
    if s.count(":") == 1:
        left, right = s.rsplit(":", 1)
        if right.isdigit():
            return left
    return s.split("%")[0]


def client_ip(request: Request) -> str | None:
    """IP publique du navigateur (X-Forwarded-For / Forwarded / X-Real-IP), jamais l’IP du pod."""
    candidates: list[str] = []
    xff = request.headers.get("x-forwarded-for") or ""
    candidates.extend(p.strip() for p in xff.split(",") if p.strip())
    forwarded = request.headers.get("forwarded") or ""
    for part in forwarded.split(","):
        match = re.search(r"for=([^;,\s]+)", part, flags=re.I)
        if match:
            candidates.append(match.group(1))
    real = (request.headers.get("x-real-ip") or "").strip()
    if real:
        candidates.append(real)
    if request.client and request.client.host:
        candidates.append(request.client.host)
    for raw in candidates:
        host = _clean_ip(raw)
        if is_public_ip(host):
            return host
    return None


def _lab(value: str, fallback: str = "inconnu", limit: int = 80) -> str:
    text = re.sub(r"[\n\r\\]+", " ", (value or "").strip())
    text = text[:limit]
    return text or fallback


def _opt(value: Any, limit: int = 80) -> str:
    return _lab(str(value or ""), fallback="", limit=limit)


def _coord(value: Any, digits: int = 4) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return NO_COORD


def _prom_labels(labels: dict[str, str]) -> dict[str, str]:
    """Seuls les labels Prometheus (pas de région / FAI / PTR)."""
    return {
        "country": labels.get("country") or "inconnu",
        "city": labels.get("city") or "inconnu",
        "latitude": labels.get("latitude") or NO_COORD,
        "longitude": labels.get("longitude") or NO_COORD,
    }


def _geo_labels(data: dict[str, Any] | None) -> dict[str, str]:
    empty = {
        "country": "inconnu",
        "city": "inconnu",
        "latitude": NO_COORD,
        "longitude": NO_COORD,
        "region": "",
        "postal": "",
        "isp": "",
        "ptr": "",
    }
    if not data:
        return empty
    return {
        "country": _lab(str(data.get("country") or "")),
        "city": _lab(str(data.get("city") or "")),
        "latitude": _coord(data.get("latitude")),
        "longitude": _coord(data.get("longitude")),
        "region": _opt(data.get("region")),
        "postal": _opt(data.get("postal")),
        "isp": _opt(data.get("isp")),
        "ptr": _opt(data.get("ptr"), limit=120),
    }


def _journal_coords(labels: dict[str, str]) -> tuple[str, str]:
    lat = labels.get("latitude") or ""
    lon = labels.get("longitude") or ""
    if lat == NO_COORD:
        lat = ""
    if lon == NO_COORD:
        lon = ""
    return lat, lon


def _count(ip: str, labels: dict[str, str], path: str) -> None:
    VISITS.labels(**_prom_labels(labels), path=page_label(path)).inc()
    _unique_ips.add(ip)
    UNIQUE.set(len(_unique_ips))
    from app import visitlog

    lat, lon = _journal_coords(labels)
    visitlog.append(
        ip,
        "page",
        page_label(path),
        labels.get("country") or "",
        labels.get("city") or "",
        region=labels.get("region") or "",
        postal=labels.get("postal") or "",
        isp=labels.get("isp") or "",
        latitude=lat,
        longitude=lon,
        ptr=labels.get("ptr") or "",
    )


def _count_replay(ip: str, labels: dict[str, str], replay: str) -> None:
    rid = replay_label(replay)
    prom = _prom_labels(labels)
    REPLAYS.labels(
        country=prom["country"],
        city=prom["city"],
        replay=rid,
    ).inc()
    _unique_ips.add(ip)
    UNIQUE.set(len(_unique_ips))
    from app import visitlog

    lat, lon = _journal_coords(labels)
    visitlog.append(
        ip,
        "replay",
        "/api/trafic/" + rid + "/play",
        labels.get("country") or "",
        labels.get("city") or "",
        region=labels.get("region") or "",
        postal=labels.get("postal") or "",
        isp=labels.get("isp") or "",
        latitude=lat,
        longitude=lon,
        ptr=labels.get("ptr") or "",
    )


def replay_label(vid: str) -> str:
    return _lab(vid, fallback="inconnu")[:80]


def schedule_replay(request: Request, trafic_id: str) -> None:
    """Comptage d’une lecture mixer, même géoloc que les visites de page."""
    ip = client_ip(request)
    if not ip:
        return
    rid = replay_label(trafic_id)
    if not rid or rid == "inconnu":
        return
    cached = _geo_cache.get(ip)
    if cached is not None:
        _count_replay(ip, cached, rid)
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        _count_replay(ip, _geo_labels(None), rid)
        return
    key = "replay:" + ip
    if key in _pending:
        return
    _pending.add(key)
    loop.create_task(_resolve_and_count_replay(ip, rid, key))


async def _resolve_and_count_replay(ip: str, rid: str, key: str) -> None:
    try:
        labels = _geo_cache.get(ip)
        if labels is None:
            labels = _geo_labels(await geolocate(ip))
            _geo_cache[ip] = labels
        _count_replay(ip, labels, rid)
    except Exception:
        log.exception("Géoloc replay %s", ip.split(".")[0] + ".x")
        labels = _geo_labels(None)
        _geo_cache.setdefault(ip, labels)
        _count_replay(ip, labels, rid)
    finally:
        _pending.discard(key)


def schedule(request: Request) -> None:
    """Comptage non bloquant : cache mémoire, géoloc en tâche de fond."""
    if not is_page_visit(request.method, request.url.path):
        return
    ip = client_ip(request)
    if not ip:
        return
    path = request.url.path
    cached = _geo_cache.get(ip)
    if cached is not None:
        _count(ip, cached, path)
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        _count(ip, _geo_labels(None), path)
        return
    if ip in _pending:
        return
    _pending.add(ip)
    loop.create_task(_resolve_and_count(ip, path))


def schedule_nav(request: Request, path: str) -> None:
    """Onglets hash / ouverture mixer : URL telle qu’appelée, hors sondes."""
    labeled = page_label(path)
    if labeled not in {"/", "/#metarea", "/#setup", "/#apropos", "/#trafic"} and not labeled.startswith(
        "/trafic/"
    ):
        return
    if labeled == "/#trafic":
        labeled = "/"
    ip = client_ip(request)
    if not ip:
        return
    cached = _geo_cache.get(ip)
    if cached is not None:
        _count(ip, cached, labeled)
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        _count(ip, _geo_labels(None), labeled)
        return
    key = "nav:" + ip + ":" + labeled
    if key in _pending:
        return
    _pending.add(key)
    loop.create_task(_resolve_and_count_nav(ip, labeled, key))


async def _resolve_and_count_nav(ip: str, path: str, key: str) -> None:
    try:
        labels = _geo_cache.get(ip)
        if labels is None:
            labels = _geo_labels(await geolocate(ip))
            _geo_cache[ip] = labels
        _count(ip, labels, path)
    except Exception:
        log.exception("Géoloc nav %s", ip.split(".")[0] + ".x")
        labels = _geo_labels(None)
        _geo_cache.setdefault(ip, labels)
        _count(ip, labels, path)
    finally:
        _pending.discard(key)


async def _resolve_and_count(ip: str, path: str) -> None:
    try:
        labels = _geo_cache.get(ip)
        if labels is None:
            labels = _geo_labels(await geolocate(ip))
            _geo_cache[ip] = labels
        _count(ip, labels, path)
    except Exception:
        log.exception("Géoloc visite %s", ip.split(".")[0] + ".x")
        labels = _geo_labels(None)
        _geo_cache.setdefault(ip, labels)
        _count(ip, labels, path)
    finally:
        _pending.discard(ip)


async def _ptr(ip: str) -> str:
    """Reverse DNS : souvent le nœud Orange/Wanadoo (ANantes-…), plus parlant que la ville MaxMind."""
    try:
        info = await asyncio.wait_for(asyncio.to_thread(socket.gethostbyaddr, ip), timeout=2.0)
        name = (info[0] or "").strip().strip(".")
        if name and name != ip:
            return name[:120]
    except Exception:
        return ""
    return ""


def _from_ipwho(data: dict[str, Any]) -> dict[str, Any]:
    conn = data.get("connection") if isinstance(data.get("connection"), dict) else {}
    return {
        "country": data.get("country") or data.get("country_code"),
        "city": data.get("city"),
        "region": data.get("region"),
        "postal": data.get("postal"),
        "isp": conn.get("isp") or conn.get("org"),
        "latitude": data.get("latitude"),
        "longitude": data.get("longitude"),
    }


def _from_ipapi(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "country": data.get("country"),
        "city": data.get("city"),
        "region": data.get("regionName"),
        "postal": data.get("zip"),
        "isp": data.get("isp") or data.get("org"),
        "latitude": data.get("lat"),
        "longitude": data.get("lon"),
        "ptr": data.get("reverse"),
    }


async def geolocate(ip: str) -> dict[str, Any] | None:
    if lookup_override is not None:
        return lookup_override(ip)
    ua = f"GGR-Trafic/{version(None)} (F6KUF; visites monitoring)"
    timeout = httpx.Timeout(4.0, connect=2.0)
    found: dict[str, Any] | None = None
    async with httpx.AsyncClient(timeout=timeout, headers={"User-Agent": ua}) as client:
        try:
            r = await client.get(f"https://ipwho.is/{ip}")
            r.raise_for_status()
            data = r.json()
            if data.get("success") is not False:
                found = _from_ipwho(data)
        except Exception:
            log.debug("ipwho.is indisponible, essai ip-api.com", exc_info=True)
        if found is None:
            try:
                r = await client.get(
                    f"http://ip-api.com/json/{ip}",
                    params={"fields": "status,country,regionName,city,zip,lat,lon,isp,org,reverse"},
                )
                r.raise_for_status()
                data = r.json()
                if data.get("status") == "success":
                    found = _from_ipapi(data)
            except Exception:
                log.warning("Géoloc IP en échec")
                return None
    if found is None:
        return None
    if not found.get("ptr"):
        found["ptr"] = await _ptr(ip)
    return found
