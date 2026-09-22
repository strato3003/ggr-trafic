"""Connexion e-mail : liste blanche, jeton unique, OTP, rate-limit."""

from __future__ import annotations

import json

import pytest

from app import auth_email, operators


def _seed(tmp_path, monkeypatch):
    monkeypatch.setenv("GGR_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("GGR_SESSION_SECRET", "test-session-secret")
    return operators.seed()


def test_unknown_email_denied(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    sent: list[tuple] = []
    with pytest.raises(auth_email.AuthEmailError) as exc:
        auth_email.request_login(
            "inconnu@example.com",
            "1.2.3.4",
            "https://ggr.test",
            send=lambda op, url, otp: sent.append((op, url, otp)),
        )
    assert exc.value.reason == "denied"
    assert str(exc.value) == "E-mail non autorisé"
    assert sent == []


def test_magic_link_once_and_hashed(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    sent: list[tuple] = []
    auth_email.request_login(
        "JNMARTINEAU@gmail.com",
        "1.2.3.4",
        "https://ggr.test",
        send=lambda op, url, otp: sent.append((op, url, otp)),
    )
    assert len(sent) == 1
    op, url, otp = sent[0]
    assert op["callsign"] == "F4IAE"
    assert otp.isdigit() and len(otp) == 6
    token = url.rsplit("/", 1)[-1]
    raw = (tmp_path / "login_tokens.json").read_text(encoding="utf-8")
    assert token not in raw
    assert otp not in raw
    payload = json.loads(raw)
    assert payload["challenges"][0]["token_hash"]
    got = auth_email.consume_token(token)
    assert got["callsign"] == "F4IAE"
    assert operators.find_by_email("jnmartineau@gmail.com")["last_login_at"]
    with pytest.raises(auth_email.AuthEmailError) as exc:
        auth_email.consume_token(token)
    assert exc.value.reason == "invalid"


def test_expired_token(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    sent: list[tuple] = []
    t0 = 1_000_000.0
    auth_email.request_login(
        "jnmartineau@gmail.com",
        "1.2.3.4",
        "https://ggr.test",
        now=t0,
        send=lambda op, url, otp: sent.append((url, otp)),
    )
    token = sent[0][0].rsplit("/", 1)[-1]
    with pytest.raises(auth_email.AuthEmailError) as exc:
        auth_email.consume_token(token, now=t0 + auth_email.TTL_SEC + 1)
    assert exc.value.reason == "expired"


def test_otp_success_and_lockout(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    sent: list[tuple] = []
    t0 = 4_000_000.0
    auth_email.request_login(
        "guy.lem2@orange.fr",
        "8.8.8.8",
        "https://ggr.test",
        now=t0,
        send=lambda op, url, otp: sent.append((op, url, otp)),
    )
    otp = sent[0][2]
    got = auth_email.consume_otp("GUY.LEM2@orange.fr", f"{otp[:3]} {otp[3:]}", now=t0 + 1)
    assert got["callsign"] == "F4DAI"
    with pytest.raises(auth_email.AuthEmailError):
        auth_email.consume_otp("guy.lem2@orange.fr", otp, now=t0 + 2)

    sent.clear()
    t1 = t0 + auth_email.RATE_SEC + 1
    auth_email.request_login(
        "philippef4hwm@gmail.com",
        "9.9.9.9",
        "https://ggr.test",
        now=t1,
        send=lambda op, url, otp: sent.append((op, url, otp)),
    )
    for _ in range(auth_email.OTP_MAX_TRIES):
        with pytest.raises(auth_email.AuthEmailError) as exc:
            auth_email.consume_otp("philippef4hwm@gmail.com", "000000", now=t1 + 1)
        assert exc.value.reason == "invalid"
    with pytest.raises(auth_email.AuthEmailError):
        auth_email.consume_otp("philippef4hwm@gmail.com", sent[0][2], now=t1 + 2)


def test_rate_limit_ip_and_email(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    sent: list = []
    t0 = 3_000_000.0
    auth_email.request_login(
        "jeanyves.robin@free.fr",
        "10.0.0.1",
        "https://ggr.test",
        now=t0,
        send=lambda op, url, otp: sent.append(url),
    )
    with pytest.raises(auth_email.AuthEmailError) as exc:
        auth_email.request_login(
            "jeanyves.robin@free.fr",
            "10.0.0.2",
            "https://ggr.test",
            now=t0 + 10,
            send=lambda op, url, otp: sent.append(url),
        )
    assert exc.value.reason == "wait"
    with pytest.raises(auth_email.AuthEmailError) as exc:
        auth_email.request_login(
            "jnmartineau@gmail.com",
            "10.0.0.1",
            "https://ggr.test",
            now=t0 + 10,
            send=lambda op, url, otp: sent.append(url),
        )
    assert exc.value.reason == "wait"
    auth_email.request_login(
        "jnmartineau@gmail.com",
        "10.0.0.3",
        "https://ggr.test",
        now=t0 + auth_email.RATE_SEC + 1,
        send=lambda op, url, otp: sent.append(url),
    )
    assert len(sent) == 2


def test_send_failure_drops_challenge(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)

    def boom(op, url, otp):
        raise RuntimeError("smtp down")

    with pytest.raises(auth_email.AuthEmailError) as exc:
        auth_email.request_login("jnmartineau@gmail.com", "1.1.1.1", "https://ggr.test", send=boom)
    assert exc.value.reason == "mail"
    payload = json.loads((tmp_path / "login_tokens.json").read_text(encoding="utf-8"))
    assert payload["challenges"] == []


def test_smtp_missing_refuses_before_token(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    monkeypatch.delenv("GGR_SMTP_HOST", raising=False)
    monkeypatch.delenv("GGR_SMTP_FROM", raising=False)
    with pytest.raises(auth_email.AuthEmailError) as exc:
        auth_email.request_login("jnmartineau@gmail.com", "1.2.3.4", "https://ggr.test")
    assert exc.value.reason == "mail"
    assert not (tmp_path / "login_tokens.json").is_file()
