"""Visites de l’UI publique : comptage Prometheus + géoloc (sans IP dans les métriques)."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
from typing import Any

import httpx
from prometheus_client import Counter, Gauge
from starlette.requests import Request

from recorder.config import version

log = logging.getLogger(__name__)

# Pages HTML réellement rendues (pas les redirections /flotte, /a-propos, sondes, tuiles).
_PAGE_EXACT = {"/"}
_PAGE_PREFIXES = ("/trafic/",)
_SKIP_PREFIXES = ("/health", "/metrics", "/static", "/api/", "/media/")

# Coordonnées absentes : Prometheus exige les mêmes labels à chaque observation.
NO_COORD = "none"

VISITS = Counter(
    "ggr_http_visits_total",
    "Chargements de pages GGR Trafic (hors sondes, API, tuiles, static)",
    ["country", "city", "latitude", "longitude"],
)
UNIQUE = Gauge(
    "ggr_http_visitors",
    "Adresses IP publiques distinctes depuis le démarrage du pod (jamais exposées en label)",
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


def is_public_ip(raw: str) -> bool:
    try:
        return ipaddress.ip_address(raw.strip()).is_global
    except ValueError:
        return False


def client_ip(request: Request) -> str | None:
    """IP publique du navigateur (X-Forwarded-For / X-Real-IP), jamais l’IP du pod."""
    candidates: list[str] = []
    xff = request.headers.get("x-forwarded-for") or ""
    candidates.extend(p.strip() for p in xff.split(",") if p.strip())
    real = (request.headers.get("x-real-ip") or "").strip()
    if real:
        candidates.append(real)
    if request.client and request.client.host:
        candidates.append(request.client.host)
    for raw in candidates:
        host = raw.strip().strip("[]").split("%")[0]
        if is_public_ip(host):
            return host
    return None


def _lab(value: str, fallback: str = "inconnu") -> str:
    text = re.sub(r"[\n\r\\]+", " ", (value or "").strip())
    text = text[:80]
    return text or fallback


def _geo_labels(data: dict[str, Any] | None) -> dict[str, str]:
    if not data:
        return {"country": "inconnu", "city": "inconnu", "latitude": NO_COORD, "longitude": NO_COORD}
    lat, lon = data.get("latitude"), data.get("longitude")
    try:
        lat_s = f"{float(lat):.2f}"
        lon_s = f"{float(lon):.2f}"
    except (TypeError, ValueError):
        lat_s = lon_s = NO_COORD
    return {
        "country": _lab(str(data.get("country") or "")),
        "city": _lab(str(data.get("city") or "")),
        "latitude": lat_s,
        "longitude": lon_s,
    }


def _count(ip: str, labels: dict[str, str]) -> None:
    VISITS.labels(**labels).inc()
    _unique_ips.add(ip)
    UNIQUE.set(len(_unique_ips))


def schedule(request: Request) -> None:
    """Comptage non bloquant : cache mémoire, géoloc en tâche de fond."""
    if not is_page_visit(request.method, request.url.path):
        return
    ip = client_ip(request)
    if not ip:
        return
    cached = _geo_cache.get(ip)
    if cached is not None:
        _count(ip, cached)
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        _count(ip, _geo_labels(None))
        return
    if ip in _pending:
        return
    _pending.add(ip)
    loop.create_task(_resolve_and_count(ip))


async def _resolve_and_count(ip: str) -> None:
    try:
        labels = _geo_cache.get(ip)
        if labels is None:
            labels = _geo_labels(await geolocate(ip))
            _geo_cache[ip] = labels
        _count(ip, labels)
    except Exception:
        log.exception("Géoloc visite %s", ip.split(".")[0] + ".x")
        labels = _geo_labels(None)
        _geo_cache.setdefault(ip, labels)
        _count(ip, labels)
    finally:
        _pending.discard(ip)


async def geolocate(ip: str) -> dict[str, Any] | None:
    if lookup_override is not None:
        return lookup_override(ip)
    ua = f"GGR-Trafic/{version(None)} (F6KUF; visites monitoring)"
    timeout = httpx.Timeout(4.0, connect=2.0)
    async with httpx.AsyncClient(timeout=timeout, headers={"User-Agent": ua}) as client:
        try:
            r = await client.get(f"https://ipwho.is/{ip}")
            r.raise_for_status()
            data = r.json()
            if data.get("success") is False:
                return None
            return {
                "country": data.get("country") or data.get("country_code"),
                "city": data.get("city"),
                "latitude": data.get("latitude"),
                "longitude": data.get("longitude"),
            }
        except Exception:
            log.debug("ipwho.is indisponible, essai ip-api.com", exc_info=True)
        try:
            r = await client.get(
                f"http://ip-api.com/json/{ip}",
                params={"fields": "status,country,city,lat,lon"},
            )
            r.raise_for_status()
            data = r.json()
            if data.get("status") != "success":
                return None
            return {
                "country": data.get("country"),
                "city": data.get("city"),
                "latitude": data.get("lat"),
                "longitude": data.get("lon"),
            }
        except Exception:
            log.warning("Géoloc IP en échec")
            return None
