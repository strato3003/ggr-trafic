"""Liste blanche opérateurs et filtre SSO Google."""

from __future__ import annotations

import pytest

from app import auth_google, operators


def test_seed_and_cli_crud(tmp_path, monkeypatch):
    monkeypatch.setenv("GGR_DATA_DIR", str(tmp_path))
    rows = operators.seed()
    calls = {r["callsign"] for r in rows}
    emails = {r["email"] for r in rows}
    assert calls == {item["callsign"] for item in operators.SEED}
    assert len(calls) == len({item["callsign"] for item in operators.SEED})
    assert operators.find_by_email("jnmartineau@gmail.com")["name"] == "Jean-Noel Martineau"
    assert operators.find_by_callsign("F4DAI")["name"] == "Guy Lemoine"
    assert operators.find_by_callsign("F4HWM")["name"] == "Philippe Oudry"
    assert operators.find_by_callsign("F1FDA")["name"] == "Jean Yves Robin"
    assert operators.domicile_of(operators.find_by_email("jnmartineau@gmail.com")) == "TALMONT ST HILAIRE 85440"
    assert operators.find_by_callsign("F4DAI")["cp"] == "85670"
    assert len(rows) == 17
    swl = {r["email"] for r in rows if r["callsign"] == "SWL"}
    assert swl == {"sdelasnerie@gmail.com", "v100ch@gmail.com"}
    assert operators.find_by_email("v100ch@gmail.com")["name"] == "Vincent Charbonneau"
    assert operators.find_by_email("sdelasnerie@gmail.com")["name"] == "Sebastien Delasnerie"
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


def test_seed_aligns_existing_names(tmp_path, monkeypatch):
    monkeypatch.setenv("GGR_DATA_DIR", str(tmp_path))
    operators.seed()
    operators.update("F4IAE", name="JNoel")
    assert operators.find_by_email("jnmartineau@gmail.com")["name"] == "JNoel"
    rows = operators.seed()
    assert operators.find_by_email("jnmartineau@gmail.com")["name"] == "Jean-Noel Martineau"
    assert {r["callsign"] for r in rows} == {item["callsign"] for item in operators.SEED}


def test_add_rejects_duplicate_email(tmp_path, monkeypatch):
    monkeypatch.setenv("GGR_DATA_DIR", str(tmp_path))
    operators.seed()
    with pytest.raises(operators.OperatorError, match="déjà"):
        operators.add("X", "jnmartineau@gmail.com", "F4XXX")


def test_swl_share_callsign_unique_email(tmp_path, monkeypatch):
    monkeypatch.setenv("GGR_DATA_DIR", str(tmp_path))
    operators.seed()
    with pytest.raises(operators.OperatorError, match="déjà"):
        operators.add("Autre", "v100ch@gmail.com", "SWL")
    with pytest.raises(operators.OperatorError, match="introuvable"):
        operators.delete("SWL")
    operators.main(["update", "v100ch@gmail.com", "--name", "Vincent C."])
    assert operators.find_by_email("v100ch@gmail.com")["name"] == "Vincent C."
    assert operators.find_by_email("sdelasnerie@gmail.com")["callsign"] == "SWL"
    operators.main(["delete", "sdelasnerie@gmail.com"])
    assert operators.find_by_email("sdelasnerie@gmail.com") is None
    assert operators.find_by_email("v100ch@gmail.com")["callsign"] == "SWL"


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
    assert auth_google.AUTH_MESSAGES["denied"] == "E-mail non autorisé"


def test_public_paths_and_login_redirect(tmp_path, monkeypatch):
    monkeypatch.setenv("GGR_DATA_DIR", str(tmp_path))
    assert auth_google.is_public_path("/health")
    assert auth_google.is_public_path("/login/otp")
    assert auth_google.is_public_path("/auth/email/abc")
    assert auth_google.is_public_path("/auth/google")
    assert auth_google.is_public_path("/static/css/app.css")
    assert auth_google.is_public_path("/lang/en")
    assert auth_google.is_public_path("/lang/fr")
    assert not auth_google.is_public_path("/")
    assert not auth_google.is_public_path("/api/trafic")
    assert not auth_google.is_public_path("/media/x/a.mp3")
    loc = auth_google.login_redirect("denied").headers["location"]
    assert loc.startswith("/login?")
    assert "auth=denied" in loc
    assert auth_google.is_public_path("/login/")
    assert auth_google.operator_from_mapping({"operator": {"email": "nobody@x.test"}}) is None
