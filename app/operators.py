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

# Noms = prénom + nom ANFR (annuaire-amateurs.anfr.fr, page 312), casse titre.
# SWL (Sebastien Delasnerie, Vincent Charbonneau) : pas d’indicatif amateur ANFR.
SHARED_CALLS = frozenset({"SWL"})
SEED: tuple[dict[str, str], ...] = (
    {"name": "Guy Lemoine", "email": "guy.lem2@orange.fr", "callsign": "F4DAI", "localite": "ST CHRISTOPHE DU LIGNERON", "cp": "85670"},
    {"name": "Bernard Perocheau", "email": "berper85@orange.fr", "callsign": "F1FOU", "localite": "POIROUX", "cp": "85440"},
    {"name": "Marc Jamet", "email": "marcdulac@orange.fr", "callsign": "F1GSU", "localite": "LA CHEVROLIERE", "cp": "44118"},
    {"name": "Eric Fort", "email": "f1nzm@mailo.com", "callsign": "F1NZM", "localite": "ST FLORENT DES BOIS", "cp": "85310"},
    {"name": "Patrice Preteseille", "email": "patrice.prete@wanadoo.fr", "callsign": "F4CXN", "localite": "APREMONT", "cp": "85220"},
    {"name": "Bruno Pochon", "email": "f4fhw@wanadoo.fr", "callsign": "F4FHW", "localite": "CHAILLE LES MARAIS", "cp": "85450"},
    {"name": "Philippe Oudry", "email": "philippef4hwm@gmail.com", "callsign": "F4HWM", "localite": "TALMONT SAINT HILAIRE", "cp": "85440"},
    {"name": "Jean-Noel Martineau", "email": "jnmartineau@gmail.com", "callsign": "F4IAE", "localite": "TALMONT ST HILAIRE", "cp": "85440"},
    {"name": "Bruno Cattani", "email": "bruno.cattani@hotmail.fr", "callsign": "F4IJX", "localite": "LA ROCHE SUR YON", "cp": "85000"},
    {"name": "Joel Casteran", "email": "joel.casteran@9online.fr", "callsign": "F4LVK", "localite": "LA ROCHE SUR YON", "cp": "85000"},
    {"name": "Marcel Pitzini", "email": "mpitzini@gmail.com", "callsign": "F5BJV", "localite": "LONGEVILLE SUR MER", "cp": "85560"},
    {"name": "Alain Fassot", "email": "alain.f5oev@gmail.com", "callsign": "F5OEV", "localite": "DOMPIERRE SUR YON", "cp": "85170"},
    {"name": "Lucien Frouard", "email": "f5rfs@orange.fr", "callsign": "F5RFS", "localite": "LA ROCHE SUR YON", "cp": "85000"},
    {"name": "Jean-Pierre Pouyadoux", "email": "jean-pierre.pouyadoux@wanadoo.fr", "callsign": "F6HPS", "localite": "LUCON", "cp": "85400"},
    {"name": "Jean Yves Robin", "email": "jeanyves.robin@free.fr", "callsign": "F1FDA", "localite": "ST NAZAIRE", "cp": "44600"},
    # SWL : pas d’indicatif ANFR ; même suffixe SWL, unicité = e-mail.
    {"name": "Sebastien Delasnerie", "email": "sdelasnerie@gmail.com", "callsign": "SWL"},
    {"name": "Vincent Charbonneau", "email": "v100ch@gmail.com", "callsign": "SWL"},
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


def _shared_call(callsign: str) -> bool:
    """Indicatif non unique (plusieurs SWL). Clé = e-mail."""
    return _norm_call(callsign) in SHARED_CALLS


def _norm_name(raw: str) -> str:
    return str(raw or "").strip()


def _norm_cp(raw: str) -> str:
    return str(raw or "").strip()


def domicile_of(row: dict[str, Any] | None) -> str:
    """Localité + CP ANFR (pas de rue) pour Loki."""
    if not row:
        return ""
    loc = _norm_name(str(row.get("localite") or ""))
    cp = _norm_cp(str(row.get("cp") or ""))
    return f"{loc} {cp}".strip()


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


def _row(
    name: str,
    email: str,
    callsign: str,
    *,
    localite: str = "",
    cp: str = "",
    last_login_at: str | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "email": email,
        "callsign": callsign,
        "localite": _norm_name(localite),
        "cp": _norm_cp(cp),
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
                "localite": _norm_name(str(item.get("localite") or "")),
                "cp": _norm_cp(str(item.get("cp") or "")),
                "created_at": item.get("created_at"),
                "last_login_at": item.get("last_login_at"),
            }
        )
    return out


def save(rows: list[dict[str, Any]], cfg: dict[str, Any] | None = None) -> None:
    _atomic_write(operators_path(cfg), {"operators": rows})


def seed(cfg: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Ajoute les opérateurs de référence s’ils manquent ; aligne nom / e-mail / indicatif / domicile ANFR."""
    rows = load(cfg, seed_if_missing=False)
    by_email = {_norm_email(r["email"]): r for r in rows}
    by_call = {_norm_call(r["callsign"]): r for r in rows}
    added = 0
    updated = 0
    for item in SEED:
        name, email, callsign = _validate(item["name"], item["email"], item["callsign"])
        localite = _norm_name(item.get("localite") or "")
        cp = _norm_cp(item.get("cp") or "")
        existing = by_email.get(email)
        if existing is None and not _shared_call(callsign):
            existing = by_call.get(callsign)
        if existing is not None:
            old_email = existing["email"]
            old_call = existing["callsign"]
            email_taken = by_email.get(email)
            call_taken = by_call.get(callsign)
            if email_taken is not None and email_taken is not existing:
                continue
            if call_taken is not None and call_taken is not existing and not _shared_call(callsign):
                continue
            changed = (
                existing["name"] != name
                or old_email != email
                or old_call != callsign
                or existing.get("localite") != localite
                or existing.get("cp") != cp
            )
            if changed:
                existing["name"] = name
                existing["email"] = email
                existing["callsign"] = callsign
                existing["localite"] = localite
                existing["cp"] = cp
                if old_email != email:
                    by_email.pop(old_email, None)
                    by_email[email] = existing
                if old_call != callsign:
                    by_call.pop(old_call, None)
                    if not _shared_call(callsign):
                        by_call[callsign] = existing
                updated += 1
            continue
        row = _row(name, email, callsign, localite=localite, cp=cp)
        rows.append(row)
        by_email[email] = row
        if not _shared_call(callsign):
            by_call[callsign] = row
        added += 1
    if added or updated or not operators_path(cfg).is_file():
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
    if not _shared_call(callsign) and find_by_callsign(callsign, cfg):
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
    localite: str | None = None,
    cp: str | None = None,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    rows = load(cfg)
    target = None
    k_email = _norm_email(key)
    k_call = _norm_call(key)
    for row in rows:
        if row["email"] == k_email:
            target = row
            break
        if not _shared_call(k_call) and row["callsign"] == k_call:
            target = row
            break
    if target is None:
        raise OperatorError(f"Opérateur introuvable : {key}")
    new_name = _norm_name(name) if name is not None else target["name"]
    new_email = _norm_email(email) if email is not None else target["email"]
    new_call = _norm_call(callsign) if callsign is not None else target["callsign"]
    new_loc = _norm_name(localite) if localite is not None else str(target.get("localite") or "")
    new_cp = _norm_cp(cp) if cp is not None else str(target.get("cp") or "")
    new_name, new_email, new_call = _validate(new_name, new_email, new_call)
    for row in rows:
        if row is target:
            continue
        if row["email"] == new_email:
            raise OperatorError(f"E-mail déjà enregistré : {new_email}")
        if row["callsign"] == new_call and not _shared_call(new_call):
            raise OperatorError(f"Indicatif déjà enregistré : {new_call}")
    target["name"] = new_name
    target["email"] = new_email
    target["callsign"] = new_call
    target["localite"] = new_loc
    target["cp"] = new_cp
    save(rows, cfg)
    return target


def delete(key: str, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    rows = load(cfg)
    k_email = _norm_email(key)
    k_call = _norm_call(key)
    kept: list[dict[str, Any]] = []
    removed: dict[str, Any] | None = None
    for row in rows:
        if row["email"] == k_email or (not _shared_call(k_call) and row["callsign"] == k_call):
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
        "localite": str(row.get("localite") or ""),
        "cp": str(row.get("cp") or ""),
        "domicile": domicile_of(row),
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
    sub.add_parser("seed", help="Ajouter / aligner les opérateurs de référence ANFR")
    p_add = sub.add_parser("add", help="Ajouter Nom,email,indicatif  ou  Nom email indicatif")
    p_add.add_argument("fields", nargs="+", help="JNoel,jnmartineau@gmail.com,F4IAE")
    p_up = sub.add_parser("update", help="Modifier un opérateur (clé = e-mail ou indicatif)")
    p_up.add_argument("key")
    p_up.add_argument("--name")
    p_up.add_argument("--email")
    p_up.add_argument("--callsign")
    p_up.add_argument("--localite")
    p_up.add_argument("--cp")
    p_del = sub.add_parser("delete", help="Retirer un opérateur (e-mail ou indicatif)")
    p_del.add_argument("key")
    args = parser.parse_args(argv)
    cfg = load_config()

    if args.cmd == "list":
        rows = load(cfg)
        if not rows:
            print("Aucun opérateur.")
            return
        print(f"{'INDICATIF':<12} {'NOM':<24} {'E-MAIL':<40} {'DOMICILE ANFR':<36} DERNIÈRE CONNEXION")
        for row in rows:
            login = row.get("last_login_at") or "—"
            print(
                f"{row['callsign']:<12} {row['name']:<24} {row['email']:<40} "
                f"{domicile_of(row):<36} {login}"
            )
        print(f"{len(rows)} opérateur(s)")
        return

    if args.cmd == "seed":
        before = {
            r["email"]: (r["callsign"], r["name"], r.get("localite") or "", r.get("cp") or "")
            for r in load(cfg, seed_if_missing=False)
        }
        rows = seed(cfg)
        added = [r for r in rows if r["email"] not in before]
        changed = [
            r
            for r in rows
            if r["email"] in before
            and before[r["email"]] != (r["callsign"], r["name"], r.get("localite") or "", r.get("cp") or "")
        ]
        if not added and not changed:
            print("Liste déjà à jour.")
        else:
            for row in added:
                print(f"ajouté {row['callsign']} {row['email']}")
            for row in changed:
                print(f"aligné {row['callsign']} {row['name']} {row['email']}")
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
            row = update(
                args.key,
                name=args.name,
                email=args.email,
                callsign=args.callsign,
                localite=args.localite,
                cp=args.cp,
                cfg=cfg,
            )
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
