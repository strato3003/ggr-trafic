"""Liste blanche opérateurs et filtre SSO Google."""

from __future__ import annotations

import pytest

from app import auth_google, operators


def test_seed_and_cli_crud(tmp_path, monkeypatch):
    monkeypatch.setenv("GGR_DATA_DIR", str(tmp_path))
    rows = operators.seed()
    calls = {r["callsign"] for r in rows}
    emails = {r["email"] for r in rows}
    assert calls == {"F4IAE", "F4DAI", "F4HWM", "F1FDA"}
    assert "jnmartineau@gmail.com" in emails
    assert "guy.lem2@orange.fr" in emails
    assert "philippef4hwm@gmail.com" in emails
    assert "jeanyves.robin@free.fr" in emails

    operators.main(["list"])
    operators.main(["add", "Test,test.op@example.com,F4TST"])
    assert operators.find_by_email("TEST.OP@example.com")["callsign"] == "F4TST"
    operators.main(["update", "F4TST", "--name", "Testé"])
    assert operators.find_by_callsign("F4TST")["name"] == "Testé"
    operators.main(["delete", "test.op@example.com"])
    assert operators.find_by_callsign("F4TST") is None


def test_add_rejects_duplicate_email(tmp_path, monkeypatch):
    monkeypatch.setenv("GGR_DATA_DIR", str(tmp_path))
    operators.seed()
    with pytest.raises(operators.OperatorError, match="déjà"):
        operators.add("X", "jnmartineau@gmail.com", "F4XXX")


def test_google_whitelist_cases(tmp_path, monkeypatch):
    monkeypatch.setenv("GGR_DATA_DIR", str(tmp_path))
    operators.seed()

    op = auth_google.accept_google_user(
        {"email": "JNMARTINEAU@gmail.com", "email_verified": True}
    )
    assert op["callsign"] == "F4IAE"
    assert operators.find_by_email("jnmartineau@gmail.com")["last_login_at"]

    with pytest.raises(auth_google.AuthDenied) as unverified:
        auth_google.accept_google_user({"email": "jnmartineau@gmail.com", "email_verified": False})
    assert unverified.value.reason == "unverified"

    with pytest.raises(auth_google.AuthDenied) as unknown:
        auth_google.accept_google_user({"email": "inconnu@gmail.com", "email_verified": True})
    assert unknown.value.reason == "denied"
    assert auth_google.AUTH_MESSAGES["denied"] == (
        "Cet e-mail n'est pas autorisé à accéder à l'application."
    )


def test_public_paths_and_login_redirect(tmp_path, monkeypatch):
    monkeypatch.setenv("GGR_DATA_DIR", str(tmp_path))
    assert auth_google.is_public_path("/health")
    assert auth_google.is_public_path("/login")
    assert auth_google.is_public_path("/auth/google")
    assert auth_google.is_public_path("/static/css/app.css")
    assert not auth_google.is_public_path("/")
    assert not auth_google.is_public_path("/api/trafic")
    assert not auth_google.is_public_path("/media/x/a.mp3")
    loc = auth_google.login_redirect("denied").headers["location"]
    assert loc.startswith("/login?")
    assert "auth=denied" in loc
    assert auth_google.is_public_path("/login/")
    assert auth_google.operator_from_mapping({"operator": {"email": "nobody@x.test"}}) is None
