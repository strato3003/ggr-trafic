"""Catalogue des vacations enregistrées (un dossier + metadata.json)."""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from recorder.config import data_dir, load_config
from recorder.geo import parse_fmt_latlon

_VACATION_ID = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._-]{0,80}$")


def channel_place(ch: dict[str, Any]) -> str:
    """Emplacement lisible du Kiwi (ville / locator), pas seulement le rôle NVIS/hop."""
    kiwi = ch.get("kiwi") or {}
    loc = kiwi.get("loc") or ch.get("loc")
    if isinstance(loc, str) and loc.strip():
        return loc.strip()
    name = str(kiwi.get("name") or "").strip()
    if " | " in name:
        tail = name.rsplit(" | ", 1)[-1].strip()
        if tail:
            return tail
    parts = [p.strip() for p in name.split(",") if p.strip()]
    if len(parts) >= 3:
        return ", ".join(parts[-2:])
    if name:
        return name
    fmt = kiwi.get("fmt")
    if fmt:
        return str(fmt)
    return str(ch.get("site_label") or "")


def vacations_root(cfg: dict[str, Any] | None = None) -> Path:
    return data_dir(cfg) / "vacations"


_PLAY_EXT = (".mp3", ".m4a")


def _play_name(folder: Path | None, audio: str, existing: str = "") -> str:
    """Fichier Lecture (MP3/M4A s’il existe), sinon le WAV d’archive."""
    audio = (audio or "").strip()
    if not audio:
        return ""
    lower = audio.lower()
    if lower.endswith(_PLAY_EXT):
        return audio
    if folder is not None:
        stem = Path(audio).stem
        for ext in _PLAY_EXT:
            path = folder / (stem + ext)
            if path.is_file() and path.stat().st_size > 64:
                return path.name
    kept = (existing or "").strip()
    if kept.lower().endswith(_PLAY_EXT):
        return kept
    return audio


def _wav_name(audio: str) -> str:
    raw = (audio or "").strip()
    if not raw:
        return ""
    lower = raw.lower()
    if lower.endswith(_PLAY_EXT):
        return Path(raw).stem + ".wav"
    return raw


def _sdr_key(ch: dict[str, Any], index: int = 0) -> str:
    kiwi = ch.get("kiwi")
    if isinstance(kiwi, dict):
        name = kiwi.get("name")
    else:
        name = kiwi
    return str(name or ch.get("id") or index)


def _decorate(meta: dict[str, Any], folder: Path | None = None) -> dict[str, Any]:
    thumb = None
    tx = None
    for ch in meta.get("channels") or []:
        ch["place"] = channel_place(ch)
        ch["has_audio"] = bool(ch.get("audio"))
        ch["play"] = _play_name(folder, str(ch.get("audio") or ""), str(ch.get("play") or ""))
        if ch.get("id") == "tx":
            tx = ch
        if ch.get("thumb") and not thumb:
            thumb = ch["thumb"]
    if tx is None:
        tx = next((c for c in (meta.get("channels") or []) if c.get("video") or c.get("screencast")), None)
    meta["thumb"] = thumb
    meta["tx"] = tx
    meta["is_test"] = meta.get("reason") in ("test-20m", "test-hunt", "manual-qrg")
    meta["is_buddy"] = str(meta.get("reason") or "").startswith("buddy")
    meta["mixer_tracks"] = [
        {
            "id": ch.get("id"),
            "src": ch.get("play") or ch.get("audio") or "",
            "wav": _wav_name(str(ch.get("audio") or "")),
            "freq_khz": ch.get("freq_khz"),
            "place": ch.get("place"),
            "site_label": ch.get("site_label"),
            "label": ch.get("label"),
            "has_audio": bool(ch.get("audio")),
            "waterfall": ch.get("waterfall") or "",
        }
        for ch in (meta.get("channels") or [])
    ]
    meta["sdrs"] = len(
        {_sdr_key(ch, i) for i, ch in enumerate(meta.get("channels") or []) if ch.get("has_audio")}
    )
    return meta


def _sched_hhmm(raw: Any, fallback: str) -> tuple[int, int]:
    text = str(raw or fallback).strip()
    try:
        parts = text.split(":")
        hh = int(parts[0])
        mm = int(parts[1][:2]) if len(parts) > 1 else 0
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            return hh, mm
    except (TypeError, ValueError, IndexError):
        pass
    fh, fm = fallback.split(":", 1)
    return int(fh), int(fm[:2])


def _air_at(meta: dict[str, Any]) -> str | None:
    """Heure d’antenne (12:00 / 18:00 TU), pas le démarrage d’enregistrement (avance)."""
    started = _parse_iso(meta.get("started_at"))
    raw = meta.get("started_at")
    if started is None:
        return str(raw) if raw else None
    if meta.get("is_test"):
        return started.isoformat()
    if meta.get("is_buddy"):
        blob = meta.get("buddy") or {}
        hh, mm = _sched_hhmm(blob.get("time_utc"), "12:00")
        lead = int(blob.get("lead_minutes") or 1)
    else:
        blob = meta.get("schedule") or {}
        hh, mm = _sched_hhmm(blob.get("time_utc"), "18:00")
        lead = int(blob.get("lead_minutes") or 1)
    scheduled = started.replace(hour=hh, minute=mm, second=0, microsecond=0)
    window = timedelta(minutes=max(0, lead) + 1)
    if timedelta(0) <= (scheduled - started) <= window:
        return scheduled.isoformat()
    return started.isoformat()


def globe_vacation(meta: dict[str, Any]) -> dict[str, Any]:
    """Carte légère pour le globe 3D : position, pistes audio, pas tout le metadata."""
    decorated = _decorate(dict(meta))
    fleet = decorated.get("fleet") or {}
    aim = decorated.get("buddy_aim") or {}
    try:
        lat = float(aim.get("lat") if aim.get("lat") is not None else fleet.get("lat"))
        lon = float(aim.get("lon") if aim.get("lon") is not None else fleet.get("lon"))
    except (TypeError, ValueError):
        lat = lon = None
    channels = []
    for ch in decorated.get("channels") or []:
        kiwi = ch.get("kiwi") or {}
        lat_k = kiwi.get("lat")
        lon_k = kiwi.get("lon")
        if lat_k is None or lon_k is None:
            parsed = parse_fmt_latlon(kiwi.get("fmt"))
            if parsed:
                lat_k, lon_k = parsed
        channels.append(
            {
                "id": ch.get("id"),
                "label": ch.get("label"),
                "freq_khz": ch.get("freq_khz"),
                "audio": ch.get("audio"),
                "play": ch.get("play") or ch.get("audio"),
                "wav": _wav_name(str(ch.get("audio") or "")),
                "video": ch.get("video"),
                "thumb": ch.get("thumb"),
                "kiwi": kiwi.get("name"),
                "loc": kiwi.get("loc"),
                "fmt": kiwi.get("fmt"),
                "lat": lat_k,
                "lon": lon_k,
                "place": ch.get("place"),
                "has_audio": bool(ch.get("has_audio")),
                "site_label": ch.get("site_label"),
                "site_km": kiwi.get("site_km") if kiwi.get("site_km") is not None else kiwi.get("distance_km"),
                "prop_zone": kiwi.get("prop_zone") or ch.get("prop_zone"),
                "waterfall": ch.get("waterfall") or "",
            }
        )
    tx = decorated.get("tx") or {}
    sdrs = {_sdr_key(c, i) for i, c in enumerate(channels) if c.get("has_audio")}
    return {
        "id": decorated.get("id"),
        "title": decorated.get("title"),
        "started_at": decorated.get("started_at"),
        "air_at": _air_at(decorated),
        "status": decorated.get("status"),
        "is_buddy": bool(decorated.get("is_buddy")),
        "is_test": bool(decorated.get("is_test")),
        "lat": lat,
        "lon": lon,
        "fmt": aim.get("fmt") or fleet.get("fmt") or decorated.get("fleet_fmt"),
        "thumb": decorated.get("thumb"),
        "video": tx.get("video"),
        "sdrs": len(sdrs),
        "channels": channels[:12],
    }


def list_vacations(cfg: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    root = vacations_root(cfg)
    if not root.exists():
        return []
    items: list[dict[str, Any]] = []
    for folder in sorted(root.iterdir(), reverse=True):
        meta_path = folder / "metadata.json"
        if not meta_path.is_file():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        meta["id"] = meta.get("id") or folder.name
        items.append(_decorate(meta, folder))
    return items


def get_vacation(vacation_id: str, cfg: dict[str, Any] | None = None) -> dict[str, Any] | None:
    folder = vacations_root(cfg) / vacation_id
    meta_path = folder / "metadata.json"
    if not meta_path.is_file():
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    meta["id"] = meta.get("id") or vacation_id
    meta["_dir"] = str(folder)
    return _decorate(meta, folder)


def media_path(vacation_id: str, filename: str, cfg: dict[str, Any] | None = None) -> Path | None:
    if "/" in filename or filename.startswith("."):
        return None
    path = vacations_root(cfg) / vacation_id / filename
    if not path.is_file():
        return None
    return path


def recording_in_progress(cfg: dict[str, Any] | None = None) -> bool:
    return (data_dir(cfg) / ".recording.lock").exists()


def _parse_iso(raw: str | None) -> datetime | None:
    if not raw or not str(raw).strip():
        return None
    try:
        dt = datetime.fromisoformat(str(raw).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _running_vacation(cfg: dict[str, Any] | None) -> dict[str, Any] | None:
    root = vacations_root(cfg)
    if not root.exists():
        return None
    best: dict[str, Any] | None = None
    best_started: datetime | None = None
    for folder in root.iterdir():
        meta_path = folder / "metadata.json"
        if not folder.is_dir() or not meta_path.is_file():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if meta.get("status") != "running":
            continue
        started = _parse_iso(meta.get("started_at"))
        if best is None or (started and (best_started is None or started > best_started)):
            best = meta
            best_started = started
    return best


def _recording_label(reason: str) -> str:
    if reason.startswith("buddy"):
        return "Buddy call"
    if reason in ("test-20m", "test-hunt"):
        return "Test 20 m"
    if reason == "manual-qrg":
        return "Record"
    return "Bulletin"


def recording_state(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Cadenas + vacation « running » : libellé et fin prévue pour le décompte UI."""
    cfg = cfg or load_config()
    idle = {
        "active": False,
        "started_at": None,
        "ends_at": None,
        "label": None,
        "id": None,
        "remaining_hms": "00:00:00",
    }
    lock = data_dir(cfg) / ".recording.lock"
    if not lock.is_file():
        return idle
    started = _parse_iso(lock.read_text(encoding="utf-8"))
    running = _running_vacation(cfg)
    reason = str((running or {}).get("reason") or "")
    label = _recording_label(reason) if running else "Enregistrement"
    minutes = None
    if running and running.get("duration_minutes") is not None:
        try:
            minutes = max(1, int(running["duration_minutes"]))
        except (TypeError, ValueError):
            minutes = None
    if minutes is None:
        if label == "Buddy call":
            minutes = int((cfg.get("buddy") or {}).get("duration_minutes") or 15)
        elif label == "Record":
            minutes = 2
        else:
            minutes = int((cfg.get("schedule") or {}).get("duration_minutes") or 10)
    if running:
        started = started or _parse_iso(running.get("started_at"))
    if started is None:
        started = datetime.fromtimestamp(lock.stat().st_mtime, tz=timezone.utc)
    ends = started + timedelta(minutes=minutes)
    remain = max(0, int((ends - datetime.now(timezone.utc)).total_seconds()))
    hh, rem = divmod(remain, 3600)
    mm, ss = divmod(rem, 60)
    return {
        "active": True,
        "started_at": started.isoformat(),
        "ends_at": ends.isoformat(),
        "label": label,
        "id": (running or {}).get("id"),
        "remaining_hms": f"{hh:02d}:{mm:02d}:{ss:02d}",
    }


def delete_vacation(vacation_id: str, cfg: dict[str, Any] | None = None) -> str | None:
    """Supprime le dossier d’une vacation. None = ok, sinon motif d’échec."""
    if not _VACATION_ID.match(vacation_id or "") or ".." in vacation_id:
        return "identifiant invalide"
    folder = vacations_root(cfg) / vacation_id
    if not folder.is_dir() or not (folder / "metadata.json").is_file():
        return "introuvable"
    meta = get_vacation(vacation_id, cfg)
    if meta and meta.get("status") == "running" and recording_in_progress(cfg):
        return "enregistrement en cours"
    shutil.rmtree(folder)
    return None


def _folder_bytes(folder: Path) -> int:
    total = 0
    try:
        for path in folder.rglob("*"):
            if path.is_file():
                total += path.stat().st_size
    except OSError:
        pass
    return total


def _fmt_size(n: int) -> str:
    if n < 1024:
        return f"{n} o"
    if n < 1024 * 1024:
        return f"{n / 1024:.0f} kio"
    if n < 1024 * 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} Mio"
    return f"{n / (1024 * 1024 * 1024):.2f} Gio"


def _kind_label(meta: dict[str, Any]) -> str:
    if meta.get("is_buddy"):
        return "buddy"
    if meta.get("is_test"):
        return "test"
    return "bulletin"


def vacation_summaries(cfg: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Liste compacte (id, heure d’antenne, taille) pour purge manuelle."""
    cfg = cfg or load_config()
    rows: list[dict[str, Any]] = []
    for meta in list_vacations(cfg):
        vid = str(meta.get("id") or "")
        folder = vacations_root(cfg) / vid
        rows.append(
            {
                "id": vid,
                "kind": _kind_label(meta),
                "air_at": _air_at(meta),
                "started_at": meta.get("started_at"),
                "status": meta.get("status"),
                "bytes": _folder_bytes(folder) if folder.is_dir() else 0,
            }
        )
    return rows


def purge_older_than(
    cfg: dict[str, Any] | None = None,
    *,
    days: int | None = None,
    before: datetime | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Supprime les vacations dont started_at est strictement avant la date de coupure."""
    cfg = cfg or load_config()
    if before is None:
        n = int(days if days is not None else (cfg.get("storage") or {}).get("retention_days") or 14)
        cutoff = datetime.now(timezone.utc) - timedelta(days=max(0, n))
    else:
        cutoff = before if before.tzinfo else before.replace(tzinfo=timezone.utc)
        cutoff = cutoff.astimezone(timezone.utc)
    targets: list[str] = []
    deleted: list[str] = []
    errors: list[tuple[str, str]] = []
    for meta in list_vacations(cfg):
        vid = str(meta.get("id") or "")
        stamp = _parse_iso(str(meta.get("started_at") or ""))
        if stamp is None or stamp >= cutoff:
            continue
        targets.append(vid)
        if dry_run:
            continue
        err = delete_vacation(vid, cfg)
        if err:
            errors.append((vid, err))
        else:
            deleted.append(vid)
    return {
        "cutoff": cutoff.isoformat(),
        "dry_run": dry_run,
        "targets": targets,
        "deleted": deleted,
        "errors": errors,
    }


def iso_to_label(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%d/%m/%Y %H:%M TU")
    except ValueError:
        return iso


def default_cfg() -> dict[str, Any]:
    return load_config()


def _cli_air(iso: str | None) -> str:
    text = (iso or "").replace("T", " ")
    if len(text) >= 16:
        text = text[:16]
    return f"{text} TU" if text else "—"


def main(argv: list[str] | None = None) -> None:
    """Lister / supprimer des vacations (hors UI)."""
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        prog="python -m app.store",
        description="Lister ou purger les trafics enregistrés (pas de menu UI).",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="Lister id, type, heure d’antenne TU, taille")
    p_del = sub.add_parser("delete", help="Supprimer un ou plusieurs identifiants")
    p_del.add_argument("ids", nargs="+", help="ex. 2026-09-11T1759Z")
    p_purge = sub.add_parser(
        "purge",
        help="Supprimer les vacations plus anciennes que N jours, ou avant une date TU",
    )
    p_purge.add_argument(
        "--days",
        type=int,
        default=None,
        help="âge minimum (défaut : retention_days de la config)",
    )
    p_purge.add_argument("--before", metavar="AAAA-MM-JJ", help="coupure exclusive TU (ex. 2026-09-14)")
    p_purge.add_argument("--dry-run", action="store_true", help="afficher sans supprimer")
    args = parser.parse_args(argv)
    cfg = load_config()

    if args.cmd == "list":
        rows = vacation_summaries(cfg)
        if not rows:
            print("Aucune vacation.")
            return
        total = 0
        print(f"{'ID':<32} {'TYPE':<9} {'ANTENNE':<20} {'TAILLE':>8}  STATUT")
        for row in rows:
            total += int(row["bytes"] or 0)
            print(
                f"{row['id']:<32} {row['kind']:<9} {_cli_air(row.get('air_at')):<20} "
                f"{_fmt_size(int(row['bytes'] or 0)):>8}  {row.get('status') or '—'}"
            )
        print(f"{len(rows)} vacation(s) · {_fmt_size(total)}")
        return

    if args.cmd == "delete":
        failed = 0
        for vid in args.ids:
            err = delete_vacation(vid, cfg)
            if err:
                print(f"{vid} : {err}", file=sys.stderr)
                failed += 1
            else:
                print(f"supprimé {vid}")
        if failed:
            raise SystemExit(1)
        return

    before = None
    if args.before:
        try:
            before = datetime.strptime(args.before, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            print("Date --before invalide (AAAA-MM-JJ).", file=sys.stderr)
            raise SystemExit(2)
    result = purge_older_than(cfg, days=args.days, before=before, dry_run=args.dry_run)
    verb = "à supprimer" if result["dry_run"] else "supprimé"
    if not result["targets"]:
        print(f"Rien à purger (coupure {result['cutoff'][:16].replace('T', ' ')} TU).")
        return
    for vid in result["targets"]:
        mark = vid if result["dry_run"] or vid in result["deleted"] else f"{vid} (échec)"
        print(f"{verb} {mark}")
    for vid, err in result["errors"]:
        print(f"{vid} : {err}", file=sys.stderr)
    print(f"{len(result['targets'])} cible(s), coupure {result['cutoff'][:16].replace('T', ' ')} TU")
    if result["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
