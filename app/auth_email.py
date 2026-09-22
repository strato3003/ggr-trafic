"""Connexion locale : e-mail liste blanche → lien magique / code OTP, session cookie."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import smtplib
import threading
import time
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Callable

from fastapi import Request

from app import auth_google, operators
from recorder.config import data_dir

log = logging.getLogger(__name__)

TTL_SEC = 15 * 60
RATE_SEC = 60
OTP_DIGITS = 6
OTP_MAX_TRIES = 5
TOKEN_BYTES = 32

MESSAGES = {
    "denied": "E-mail non autorisé",
    "wait": "Veuillez patienter avant une nouvelle demande.",
    "mail": "Envoi d'e-mail indisponible.",
    "expired": "Lien ou code expiré. Demandez-en un nouveau.",
    "used": "Lien ou code déjà utilisé.",
    "invalid": "Lien ou code invalide.",
}

_LOCK = threading.Lock()


class AuthEmailError(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(MESSAGES.get(reason) or reason)


def tokens_path(cfg: dict[str, Any] | None = None) -> Path:
    return data_dir(cfg) / "login_tokens.json"


def smtp_configured() -> bool:
    return bool(
        (os.environ.get("GGR_SMTP_HOST") or "").strip()
        and (os.environ.get("GGR_SMTP_FROM") or "").strip()
    )


def _mail_log() -> bool:
    return (os.environ.get("GGR_MAIL_LOG") or "").strip().lower() in {"1", "true", "yes", "on"}


def client_ip(request: Request) -> str:
    forwarded = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    if forwarded:
        return forwarded
    if request.client and request.client.host:
        return request.client.host
    return "0.0.0.0"


def _now() -> float:
    return time.time()


def _secret() -> bytes:
    return auth_google.session_secret().encode("utf-8")


def _digest(value: str) -> str:
    return hmac.new(_secret(), value.encode("utf-8"), hashlib.sha256).hexdigest()


def _otp_digest(email: str, otp: str) -> str:
    return _digest(f"{operators._norm_email(email)}:{otp}")


def _load(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    path = tokens_path(cfg)
    if not path.is_file():
        return {"challenges": [], "rate": {}}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"challenges": [], "rate": {}}
    if not isinstance(raw, dict):
        return {"challenges": [], "rate": {}}
    challenges = raw.get("challenges")
    rate = raw.get("rate")
    if not isinstance(challenges, list):
        challenges = []
    if not isinstance(rate, dict):
        rate = {}
    return {"challenges": challenges, "rate": rate}


def _save(payload: dict[str, Any], cfg: dict[str, Any] | None = None) -> None:
    path = tokens_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _prune(store: dict[str, Any], now: float) -> None:
    keep: list[dict[str, Any]] = []
    for item in store.get("challenges") or []:
        if not isinstance(item, dict):
            continue
        try:
            exp = float(item.get("expires_at") or 0)
        except (TypeError, ValueError):
            continue
        if exp > now:
            keep.append(item)
    store["challenges"] = keep
    rate = store.get("rate") if isinstance(store.get("rate"), dict) else {}
    fresh: dict[str, Any] = {}
    for key, ts in rate.items():
        try:
            when = float(ts)
        except (TypeError, ValueError):
            continue
        if now - when < RATE_SEC * 2:
            fresh[str(key)] = when
    store["rate"] = fresh


def _rate_blocked(store: dict[str, Any], key: str, now: float) -> bool:
    rate = store.get("rate") if isinstance(store.get("rate"), dict) else {}
    try:
        last = float(rate.get(key) or 0)
    except (TypeError, ValueError):
        last = 0.0
    return last > 0 and (now - last) < RATE_SEC


def _rate_hit(store: dict[str, Any], key: str, now: float) -> None:
    rate = store.setdefault("rate", {})
    if not isinstance(rate, dict):
        store["rate"] = {}
        rate = store["rate"]
    rate[key] = now


def _smtp_port() -> int:
    try:
        return int((os.environ.get("GGR_SMTP_PORT") or "587").strip() or "587")
    except ValueError:
        return 587


def send_login_email(op: dict[str, Any], url: str, otp: str) -> None:
    """Envoi SMTP (STARTTLS 587 / SSL 465). GGR_MAIL_LOG=1 n’écrit jamais le jeton."""
    if _mail_log():
        log.info("E-mail de connexion simulé vers un opérateur autorisé (%s)", op.get("callsign"))
        return
    if not smtp_configured():
        raise AuthEmailError("mail")
    host = (os.environ.get("GGR_SMTP_HOST") or "").strip()
    port = _smtp_port()
    user = (os.environ.get("GGR_SMTP_USER") or "").strip()
    password = os.environ.get("GGR_SMTP_PASSWORD") or ""
    from_addr = (os.environ.get("GGR_SMTP_FROM") or "").strip()
    force_ssl = (os.environ.get("GGR_SMTP_SSL") or "").strip().lower() in {"1", "true", "yes", "on"}
    starttls = (os.environ.get("GGR_SMTP_STARTTLS") or "").strip().lower()
    use_ssl = force_ssl or port == 465
    if starttls in {"0", "false", "no", "off"}:
        use_starttls = False
    elif starttls in {"1", "true", "yes", "on"}:
        use_starttls = True
    else:
        use_starttls = not use_ssl

    name = str(op.get("name") or op.get("callsign") or "")
    callsign = str(op.get("callsign") or "")
    to_addr = str(op.get("email") or "")
    msg = EmailMessage()
    msg["Subject"] = f"GGR Trafic — connexion {callsign}"
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg.set_content(
        f"Bonjour {name},\n\n"
        f"Voici votre lien de connexion à GGR Trafic "
        f"(valable 15 minutes, usage unique) :\n\n"
        f"{url}\n\n"
        f"Ou saisissez ce code : {otp}\n\n"
        f"Si vous n’êtes pas à l’origine de cette demande, ignorez ce message.\n\n"
        f"— GGR Trafic · F6KUF\n"
    )

    timeout = 12
    if use_ssl:
        smtp: smtplib.SMTP = smtplib.SMTP_SSL(host, port, timeout=timeout)
    else:
        smtp = smtplib.SMTP(host, port, timeout=timeout)
    try:
        smtp.ehlo()
        if use_starttls and not use_ssl:
            smtp.starttls()
            smtp.ehlo()
        if user:
            smtp.login(user, password)
        smtp.send_message(msg)
    finally:
        try:
            smtp.quit()
        except Exception:
            pass


def request_login(
    email: str,
    ip: str,
    public_base: str,
    *,
    now: float | None = None,
    send: Callable[[dict[str, Any], str, str], None] | None = None,
    cfg: dict[str, Any] | None = None,
) -> str:
    """Si l’e-mail est listé, crée un défi unique et envoie lien + code. Retourne l’e-mail normalisé."""
    when = _now() if now is None else now
    addr = operators._norm_email(email)
    ip_key = f"ip:{(ip or '').strip() or '0.0.0.0'}"
    mail_key = f"email:{addr}" if addr else ""
    sender = send or send_login_email
    if send is None and not smtp_configured() and not _mail_log():
        raise AuthEmailError("mail")

    with _LOCK:
        store = _load(cfg)
        _prune(store, when)
        if _rate_blocked(store, ip_key, when) or (mail_key and _rate_blocked(store, mail_key, when)):
            _save(store, cfg)
            raise AuthEmailError("wait")
        _rate_hit(store, ip_key, when)
        if mail_key:
            _rate_hit(store, mail_key, when)
        if not addr or operators.find_by_email(addr, cfg) is None:
            _save(store, cfg)
            raise AuthEmailError("denied")
        op = operators.find_by_email(addr, cfg)
        assert op is not None
        token = secrets.token_urlsafe(TOKEN_BYTES)
        otp = f"{secrets.randbelow(10 ** OTP_DIGITS):0{OTP_DIGITS}d}"
        challenges = [
            item
            for item in store["challenges"]
            if isinstance(item, dict) and item.get("email") != addr
        ]
        challenges.append(
            {
                "email": addr,
                "token_hash": _digest(token),
                "otp_hash": _otp_digest(addr, otp),
                "expires_at": when + TTL_SEC,
                "tries": 0,
            }
        )
        store["challenges"] = challenges
        _save(store, cfg)

    url = f"{public_base.rstrip('/')}/auth/email/{token}"
    try:
        sender(op, url, otp)
    except AuthEmailError:
        _drop_email(addr, cfg)
        raise
    except Exception:
        log.exception("SMTP connexion")
        _drop_email(addr, cfg)
        raise AuthEmailError("mail") from None
    return addr


def _drop_email(email: str, cfg: dict[str, Any] | None = None) -> None:
    addr = operators._norm_email(email)
    with _LOCK:
        store = _load(cfg)
        store["challenges"] = [
            item
            for item in store.get("challenges") or []
            if not (isinstance(item, dict) and item.get("email") == addr)
        ]
        _save(store, cfg)


def consume_token(token: str, *, now: float | None = None, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    raw = (token or "").strip()
    when = _now() if now is None else now
    if not raw:
        raise AuthEmailError("invalid")
    digest = _digest(raw)
    with _LOCK:
        store = _load(cfg)
        remaining: list[dict[str, Any]] = []
        found: dict[str, Any] | None = None
        expired = False
        for item in store.get("challenges") or []:
            if not isinstance(item, dict):
                continue
            if found is None and hmac.compare_digest(str(item.get("token_hash") or ""), digest):
                found = item
                try:
                    expired = float(item.get("expires_at") or 0) <= when
                except (TypeError, ValueError):
                    expired = True
                continue
            remaining.append(item)
        store["challenges"] = remaining
        _prune(store, when)
        _save(store, cfg)
        if found is None:
            raise AuthEmailError("invalid")
        if expired:
            raise AuthEmailError("expired")
    return _operator_after_challenge(found, when, cfg)


def consume_otp(
    email: str,
    otp: str,
    *,
    now: float | None = None,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    addr = operators._norm_email(email)
    code = "".join(ch for ch in (otp or "") if ch.isdigit())
    when = _now() if now is None else now
    if not addr or len(code) != OTP_DIGITS:
        raise AuthEmailError("invalid")
    want = _otp_digest(addr, code)
    with _LOCK:
        store = _load(cfg)
        _prune(store, when)
        remaining: list[dict[str, Any]] = []
        found: dict[str, Any] | None = None
        for item in store.get("challenges") or []:
            if not isinstance(item, dict):
                continue
            if found is None and item.get("email") == addr:
                found = item
                continue
            remaining.append(item)
        if found is None:
            _save(store, cfg)
            raise AuthEmailError("invalid")
        try:
            exp = float(found.get("expires_at") or 0)
        except (TypeError, ValueError):
            exp = 0.0
        if exp <= when:
            store["challenges"] = remaining
            _save(store, cfg)
            raise AuthEmailError("expired")
        try:
            tries = int(found.get("tries") or 0)
        except (TypeError, ValueError):
            tries = 0
        if not hmac.compare_digest(str(found.get("otp_hash") or ""), want):
            tries += 1
            if tries >= OTP_MAX_TRIES:
                store["challenges"] = remaining
                _save(store, cfg)
                raise AuthEmailError("invalid")
            found["tries"] = tries
            remaining.append(found)
            store["challenges"] = remaining
            _save(store, cfg)
            raise AuthEmailError("invalid")
        store["challenges"] = remaining
        _save(store, cfg)
    return _operator_after_challenge(found, when, cfg)


def _operator_after_challenge(
    challenge: dict[str, Any],
    when: float,
    cfg: dict[str, Any] | None,
) -> dict[str, Any]:
    try:
        exp = float(challenge.get("expires_at") or 0)
    except (TypeError, ValueError):
        exp = 0.0
    if exp <= when:
        raise AuthEmailError("expired")
    op = operators.find_by_email(str(challenge.get("email") or ""), cfg)
    if op is None:
        raise AuthEmailError("denied")
    operators.mark_login(op["email"], cfg)
    return op
