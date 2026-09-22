"""Liste blanche opérateurs : e-mail ↔ indicatif (fichier JSON sur le PVC)."""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from recorder.config import data_dir, load_config

_CALL = re.compile(r"^[A-Z0-9/]{3,15}$")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

SEED: tuple[dict[str, str], ...] = (
    {"name": "JNoel", "email": "jnmartineau@gmail.com", "callsign": "F4IAE"},
    {"name": "Guy", "email": "guy.lem2@orange.fr", "callsign": "F4DAI"},
    {"name": "PhilippeHWM", "email": "philippef4hwm@gmail.com", "callsign": "F4HWM"},
    {"name": "Jean-Yves", "email": "jeanyves.robin@free.fr", "callsign": "F1FDA"},
)


class OperatorError(ValueError):
    """Erreur de saisie CLI / API interne."""


def operators_path(cfg: dict[str, Any] | None = None) -> Path:
    return data_dir(cfg) / "operators.json"


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _norm_email(raw: str) -> str:
    return str(raw or "").strip().lower()


def _norm_call(raw: str) -> str:
    return str(raw or "").strip().upper()


def _norm_name(raw: str) -> str:
    return str(raw or "").strip()


def _validate(name: str, email: str, callsign: str) -> tuple[str, str, str]:
    name = _norm_name(name)
    email = _norm_email(email)
    callsign = _norm_call(callsign)
    if not name:
        raise OperatorError("Nom obligatoire.")
    if not _EMAIL.match(email):
        raise OperatorError(f"E-mail invalide : {email}")
    if not _CALL.match(callsign):
        raise OperatorError(f"Indicatif invalide : {callsign}")
    return name, email, callsign


def _row(name: str, email: str, callsign: str, *, last_login_at: str | None = None) -> dict[str, Any]:
    return {
        "name": name,
        "email": email,
        "callsign": callsign,
        "created_at": _now(),
        "last_login_at": last_login_at,
    }


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def load(cfg: dict[str, Any] | None = None, *, seed_if_missing: bool = True) -> list[dict[str, Any]]:
    path = operators_path(cfg)
    if not path.is_file():
        if seed_if_missing:
            return seed(cfg)
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rows = raw.get("operators") if isinstance(raw, dict) else raw
    if not isinstance(rows, list):
        return []
    out: list[dict[str, Any]] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        email = _norm_email(str(item.get("email") or ""))
        callsign = _norm_call(str(item.get("callsign") or ""))
        name = _norm_name(str(item.get("name") or callsign))
        if not email or not callsign:
            continue
        out.append(
            {
                "name": name,
                "email": email,
                "callsign": callsign,
                "created_at": item.get("created_at"),
                "last_login_at": item.get("last_login_at"),
            }
        )
    return out


def save(rows: list[dict[str, Any]], cfg: dict[str, Any] | None = None) -> None:
    _atomic_write(operators_path(cfg), {"operators": rows})


def seed(cfg: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Ajoute les opérateurs de référence s’ils manquent (e-mail / indicatif)."""
    rows = load(cfg, seed_if_missing=False)
    by_email = {_norm_email(r["email"]): r for r in rows}
    by_call = {_norm_call(r["callsign"]): r for r in rows}
    added = 0
    for item in SEED:
        name, email, callsign = _validate(item["name"], item["email"], item["callsign"])
        if email in by_email or callsign in by_call:
            continue
        row = _row(name, email, callsign)
        rows.append(row)
        by_email[email] = row
        by_call[callsign] = row
        added += 1
    if added or not operators_path(cfg).is_file():
        save(rows, cfg)
    return rows


def find_by_email(email: str, cfg: dict[str, Any] | None = None) -> dict[str, Any] | None:
    key = _norm_email(email)
    if not key:
        return None
    for row in load(cfg):
        if row["email"] == key:
            return row
    return None


def find_by_callsign(callsign: str, cfg: dict[str, Any] | None = None) -> dict[str, Any] | None:
    key = _norm_call(callsign)
    if not key:
        return None
    for row in load(cfg):
        if row["callsign"] == key:
            return row
    return None


def add(name: str, email: str, callsign: str, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    name, email, callsign = _validate(name, email, callsign)
    rows = load(cfg)
    if find_by_email(email, cfg):
        raise OperatorError(f"E-mail déjà enregistré : {email}")
    if find_by_callsign(callsign, cfg):
        raise OperatorError(f"Indicatif déjà enregistré : {callsign}")
    row = _row(name, email, callsign)
    rows.append(row)
    save(rows, cfg)
    return row


def update(
    key: str,
    *,
    name: str | None = None,
    email: str | None = None,
    callsign: str | None = None,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    rows = load(cfg)
    target = None
    k_email = _norm_email(key)
    k_call = _norm_call(key)
    for row in rows:
        if row["email"] == k_email or row["callsign"] == k_call:
            target = row
            break
    if target is None:
        raise OperatorError(f"Opérateur introuvable : {key}")
    new_name = _norm_name(name) if name is not None else target["name"]
    new_email = _norm_email(email) if email is not None else target["email"]
    new_call = _norm_call(callsign) if callsign is not None else target["callsign"]
    new_name, new_email, new_call = _validate(new_name, new_email, new_call)
    for row in rows:
        if row is target:
            continue
        if row["email"] == new_email:
            raise OperatorError(f"E-mail déjà enregistré : {new_email}")
        if row["callsign"] == new_call:
            raise OperatorError(f"Indicatif déjà enregistré : {new_call}")
    target["name"] = new_name
    target["email"] = new_email
    target["callsign"] = new_call
    save(rows, cfg)
    return target


def delete(key: str, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    rows = load(cfg)
    k_email = _norm_email(key)
    k_call = _norm_call(key)
    kept: list[dict[str, Any]] = []
    removed: dict[str, Any] | None = None
    for row in rows:
        if row["email"] == k_email or row["callsign"] == k_call:
            removed = row
            continue
        kept.append(row)
    if removed is None:
        raise OperatorError(f"Opérateur introuvable : {key}")
    save(kept, cfg)
    return removed


def mark_login(email: str, cfg: dict[str, Any] | None = None) -> dict[str, Any] | None:
    rows = load(cfg)
    key = _norm_email(email)
    found = None
    for row in rows:
        if row["email"] == key:
            row["last_login_at"] = _now()
            found = row
            break
    if found is None:
        return None
    save(rows, cfg)
    return found


def public_view(row: dict[str, Any] | None) -> dict[str, str] | None:
    if not row:
        return None
    return {
        "name": str(row.get("name") or ""),
        "email": str(row.get("email") or ""),
        "callsign": str(row.get("callsign") or ""),
    }


def _parse_triple(raw: str) -> tuple[str, str, str]:
    parts = [p.strip() for p in raw.split(",")]
    if len(parts) != 3:
        raise OperatorError("Format attendu : Nom,email,indicatif")
    return parts[0], parts[1], parts[2]


def main(argv: list[str] | None = None) -> None:
    """CRUD de la liste blanche e-mail ↔ indicatif (hors UI)."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m app.operators",
        description="Gérer les opérateurs autorisés (e-mail ↔ indicatif).",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="Lister nom, e-mail, indicatif")
    sub.add_parser("seed", help="Ajouter les 4 opérateurs de référence s’ils manquent")
    p_add = sub.add_parser("add", help="Ajouter Nom,email,indicatif  ou  Nom email indicatif")
    p_add.add_argument("fields", nargs="+", help="JNoel,jnmartineau@gmail.com,F4IAE")
    p_up = sub.add_parser("update", help="Modifier un opérateur (clé = e-mail ou indicatif)")
    p_up.add_argument("key")
    p_up.add_argument("--name")
    p_up.add_argument("--email")
    p_up.add_argument("--callsign")
    p_del = sub.add_parser("delete", help="Retirer un opérateur (e-mail ou indicatif)")
    p_del.add_argument("key")
    args = parser.parse_args(argv)
    cfg = load_config()

    if args.cmd == "list":
        rows = load(cfg)
        if not rows:
            print("Aucun opérateur.")
            return
        print(f"{'INDICATIF':<12} {'NOM':<16} {'E-MAIL':<36} DERNIÈRE CONNEXION")
        for row in rows:
            login = row.get("last_login_at") or "—"
            print(f"{row['callsign']:<12} {row['name']:<16} {row['email']:<36} {login}")
        print(f"{len(rows)} opérateur(s)")
        return

    if args.cmd == "seed":
        before = {r["email"] for r in load(cfg, seed_if_missing=False)}
        rows = seed(cfg)
        added = [r for r in rows if r["email"] not in before]
        if not added:
            print("Liste déjà à jour.")
        else:
            for row in added:
                print(f"ajouté {row['callsign']} {row['email']}")
        print(f"{len(rows)} opérateur(s)")
        return

    if args.cmd == "add":
        joined = " ".join(args.fields).strip()
        if joined.count(",") == 2:
            name, email, callsign = _parse_triple(joined)
        elif len(args.fields) == 3:
            name, email, callsign = args.fields
        else:
            print("Usage : add Nom email indicatif   ou   add Nom,email,indicatif", file=sys.stderr)
            raise SystemExit(2)
        try:
            row = add(name, email, callsign, cfg)
        except OperatorError as exc:
            print(str(exc), file=sys.stderr)
            raise SystemExit(1) from exc
        print(f"ajouté {row['callsign']} {row['email']}")
        return

    if args.cmd == "update":
        try:
            row = update(args.key, name=args.name, email=args.email, callsign=args.callsign, cfg=cfg)
        except OperatorError as exc:
            print(str(exc), file=sys.stderr)
            raise SystemExit(1) from exc
        print(f"modifié {row['callsign']} {row['email']}")
        return

    try:
        row = delete(args.key, cfg)
    except OperatorError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc
    print(f"supprimé {row['callsign']} {row['email']}")


if __name__ == "__main__":
    main()
