"""Catalogue des vacations enregistrées (un dossier + metadata.json)."""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from recorder.config import data_dir, load_config

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


def _sdr_key(ch: dict[str, Any], index: int = 0) -> str:
    kiwi = ch.get("kiwi")
    if isinstance(kiwi, dict):
        name = kiwi.get("name")
    else:
        name = kiwi
    return str(name or ch.get("id") or index)


def _decorate(meta: dict[str, Any]) -> dict[str, Any]:
    thumb = None
    tx = None
    for ch in meta.get("channels") or []:
        ch["place"] = channel_place(ch)
        ch["has_audio"] = bool(ch.get("audio"))
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
            "src": ch.get("audio") or "",
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
        channels.append(
            {
                "id": ch.get("id"),
                "label": ch.get("label"),
                "freq_khz": ch.get("freq_khz"),
                "audio": ch.get("audio"),
                "video": ch.get("video"),
                "thumb": ch.get("thumb"),
                "kiwi": kiwi.get("name"),
                "loc": kiwi.get("loc"),
                "place": ch.get("place"),
                "has_audio": bool(ch.get("has_audio")),
                "site_label": ch.get("site_label"),
                "waterfall": ch.get("waterfall") or "",
            }
        )
    tx = decorated.get("tx") or {}
    sdrs = {_sdr_key(c, i) for i, c in enumerate(channels) if c.get("has_audio")}
    return {
        "id": decorated.get("id"),
        "title": decorated.get("title"),
        "started_at": decorated.get("started_at"),
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
        items.append(_decorate(meta))
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
    return _decorate(meta)


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
