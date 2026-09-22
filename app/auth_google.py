"""Google SSO : e-mail vérifié puis liste blanche indicatif."""

from __future__ import annotations

import logging
import os
from typing import Any
from urllib.parse import urlencode

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse

from app import operators

log = logging.getLogger(__name__)

DENIED = "E-mail non autorisé"
UNVERIFIED = "L'adresse Google n'est pas vérifiée."
CANCELLED = "Connexion Google annulée."
SSO_OFF = "Connexion Google indisponible (client SSO non configuré)."
SENT = "Un e-mail de connexion a été envoyé. Il expire dans 15 minutes."

AUTH_MESSAGES = {
    "denied": DENIED,
    "unverified": UNVERIFIED,
    "cancelled": CANCELLED,
    "sso": SSO_OFF,
    "wait": "Veuillez patienter avant une nouvelle demande.",
    "mail": "Envoi d'e-mail indisponible.",
    "expired": "Lien ou code expiré. Demandez-en un nouveau.",
    "used": "Lien ou code déjà utilisé.",
    "invalid": "Lien ou code invalide.",
}


class AuthDenied(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def google_configured() -> bool:
    return bool(
        (os.environ.get("GGR_GOOGLE_CLIENT_ID") or "").strip()
        and (os.environ.get("GGR_GOOGLE_CLIENT_SECRET") or "").strip()
    )


def session_secret() -> str:
    return (
        (os.environ.get("GGR_SESSION_SECRET") or "").strip()
        or (os.environ.get("GGR_ADMIN_TOKEN") or "").strip()
        or "dev-ggr-trafic-session"
    )


def session_https_only() -> bool:
    raw = (os.environ.get("GGR_SESSION_SECURE") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def public_base(request: Request) -> str:
    env = (os.environ.get("GGR_PUBLIC_URL") or "").rstrip("/")
    if env:
        return env
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    return f"{proto}://{host}"


def auth_error_message(request: Request) -> str | None:
    key = str(request.query_params.get("auth") or "")
    if key in {"sent", "ok"}:
        return None
    return AUTH_MESSAGES.get(key)


def auth_notice_message(request: Request) -> str | None:
    if str(request.query_params.get("auth") or "") == "sent":
        return SENT
    return None


def set_session_operator(request: Request, op: dict[str, Any]) -> None:
    request.session["operator"] = {
        "email": op["email"],
        "callsign": op["callsign"],
        "name": op["name"],
    }
    request.session.pop("login_email", None)
    request.session.pop("login_nonce", None)


def operator_from_mapping(session: Any) -> dict[str, Any] | None:
    if not isinstance(session, dict):
        return None
    blob = session.get("operator")
    if not isinstance(blob, dict):
        return None
    email = str(blob.get("email") or "").strip()
    if not email:
        return None
    found = operators.find_by_email(email)
    return operators.public_view(found)


def session_operator(request: Request) -> dict[str, Any] | None:
    try:
        return operator_from_mapping(request.session)
    except AssertionError:
        return None


def _email_verified(value: Any) -> bool:
    if value is True or value == 1:
        return True
    if isinstance(value, str) and value.strip().lower() in {"true", "1", "yes"}:
        return True
    return False


def accept_google_user(userinfo: dict[str, Any] | None) -> dict[str, Any]:
    """Cas A / B : e-mail Google vérifié et présent dans la liste blanche."""
    info = userinfo if isinstance(userinfo, dict) else {}
    email = str(info.get("email") or "").strip()
    if not email:
        raise AuthDenied("unverified")
    if not _email_verified(info.get("email_verified")):
        raise AuthDenied("unverified")
    op = operators.find_by_email(email)
    if op is None:
        log.warning("SSO refusé : e-mail hors liste blanche")
        raise AuthDenied("denied")
    operators.mark_login(op["email"])
    return op


def login_redirect(reason: str) -> RedirectResponse:
    qs = urlencode({"auth": reason})
    return RedirectResponse(f"/login?{qs}", status_code=302)


_PUBLIC_EXACT = frozenset({"/health", "/metrics", "/login", "/login/otp", "/favicon.ico"})
_PUBLIC_PREFIX = ("/static/", "/auth/")


def is_public_path(path: str) -> bool:
    raw = (path or "/").split("?", 1)[0]
    if raw != "/" and raw.endswith("/"):
        raw = raw.rstrip("/")
    if raw in _PUBLIC_EXACT:
        return True
    return any((path or "/").startswith(p) for p in _PUBLIC_PREFIX)


_oauth = None


def google_client():
    """Client Authlib Google, ou None si le secret n’est pas posé."""
    global _oauth
    if not google_configured():
        return None
    if _oauth is None:
        from authlib.integrations.starlette_client import OAuth

        oauth = OAuth()
        oauth.register(
            name="google",
            client_id=os.environ.get("GGR_GOOGLE_CLIENT_ID"),
            client_secret=os.environ.get("GGR_GOOGLE_CLIENT_SECRET"),
            server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
            client_kwargs={"scope": "openid email profile"},
        )
        _oauth = oauth
    return _oauth


class RequireLoginMiddleware:
    """ASGI pur : lit scope['session'] (BaseHTTPMiddleware ne voit pas le cookie)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path") or "/"
        op = operator_from_mapping(scope.get("session"))
        if op:
            scope["ggr_operator"] = op
        if is_public_path(path) or op:
            await self.app(scope, receive, send)
            return
        if path.startswith("/api/") or path.startswith("/media/"):
            response = JSONResponse({"ok": False, "detail": "Connexion requise"}, status_code=401)
            await response(scope, receive, send)
            return
        response = RedirectResponse("/login", status_code=302)
        await response(scope, receive, send)
