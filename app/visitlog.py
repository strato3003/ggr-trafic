"""Journal horodaté des visites (IP, page, replay). Pas d’IP dans Prometheus : ligne JSON stdout → Loki."""

from __future__ import annotations

import json
import logging
import sqlite3
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from recorder.config import data_dir, load_config

log = logging.getLogger(__name__)
RETENTION_DAYS = 14
_lock = threading.Lock()
_state: dict[str, Any] = {"path": None, "conn": None}


def _db_path() -> Path:
    return Path(data_dir(load_config())) / "visit-log.sqlite"


def reset_for_tests() -> None:
    with _lock:
        conn = _state.get("conn")
        if conn is not None:
            conn.close()
        _state["path"] = None
        _state["conn"] = None


def _conn() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if _state["conn"] is not None and _state["path"] == path:
        return _state["conn"]
    if _state["conn"] is not None:
        _state["conn"].close()
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS events (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          ts TEXT NOT NULL,
          ip TEXT NOT NULL,
          country TEXT,
          city TEXT,
          region TEXT,
          postal TEXT,
          isp TEXT,
          latitude TEXT,
          longitude TEXT,
          ptr TEXT,
          callsign TEXT,
          email TEXT,
          surnom TEXT,
          kind TEXT NOT NULL,
          target TEXT NOT NULL
        )
        """
    )
    have = {row[1] for row in conn.execute("PRAGMA table_info(events)")}
    for col in ("region", "postal", "isp", "latitude", "longitude", "ptr", "callsign", "email", "surnom"):
        if col not in have:
            conn.execute(f"ALTER TABLE events ADD COLUMN {col} TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_ip_ts ON events(ip, ts)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts)")
    conn.commit()
    _state["path"] = path
    _state["conn"] = conn
    return conn


def append(
    ip: str,
    kind: str,
    target: str,
    country: str = "",
    city: str = "",
    *,
    region: str = "",
    postal: str = "",
    isp: str = "",
    latitude: str = "",
    longitude: str = "",
    ptr: str = "",
    callsign: str = "",
    email: str = "",
    surnom: str = "",
) -> None:
    ip = (ip or "").strip()
    kind = (kind or "").strip()
    target = (target or "").strip()[:120]
    if not ip or kind not in {"page", "replay"} or not target:
        return
    ts = datetime.now(timezone.utc).isoformat()
    country = (country or "")[:80]
    city = (city or "")[:80]
    region = (region or "")[:80]
    postal = (postal or "")[:16]
    isp = (isp or "")[:80]
    latitude = (latitude or "")[:24]
    longitude = (longitude or "")[:24]
    ptr = (ptr or "")[:120]
    callsign = (callsign or "")[:16]
    email = (email or "")[:120]
    surnom = (surnom or "")[:40]
    try:
        with _lock:
            conn = _conn()
            conn.execute(
                """
                INSERT INTO events (
                  ts, ip, country, city, region, postal, isp, latitude, longitude, ptr,
                  callsign, email, surnom, kind, target
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    ts,
                    ip,
                    country,
                    city,
                    region,
                    postal,
                    isp,
                    latitude,
                    longitude,
                    ptr,
                    callsign,
                    email,
                    surnom,
                    kind,
                    target,
                ),
            )
            cutoff = (datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)).isoformat()
            conn.execute("DELETE FROM events WHERE ts < ?", (cutoff,))
            conn.commit()
        _emit_json(
            {
                "ggr_visit": True,
                "ts": ts,
                "ip": ip,
                "kind": kind,
                "target": target,
                "country": country,
                "city": city,
                "region": region,
                "postal": postal,
                "isp": isp,
                "ptr": ptr,
                "callsign": callsign,
                "email": email,
                "surnom": surnom,
            }
        )
    except Exception:
        log.exception("Journal visites")


def _emit_json(payload: dict[str, Any]) -> None:
    """Une ligne JSON sur stdout (Promtail / Loki). Pas de label IP."""
    try:
        sys.stdout.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
        sys.stdout.flush()
    except Exception:
        pass


def list_events(
    ip: str | None = None,
    target: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    n = max(1, min(int(limit or 200), 500))
    ip_f = (ip or "").strip()
    tgt = (target or "").strip()[:120]
    cols = "ts, ip, country, city, region, postal, isp, latitude, longitude, ptr, callsign, email, surnom, kind, target"
    with _lock:
        conn = _conn()
        if ip_f and tgt:
            rows = conn.execute(
                f"SELECT {cols} FROM events WHERE ip = ? AND instr(target, ?) > 0 ORDER BY ts DESC LIMIT ?",
                (ip_f, tgt, n),
            ).fetchall()
        elif ip_f:
            rows = conn.execute(
                f"SELECT {cols} FROM events WHERE ip = ? ORDER BY ts DESC LIMIT ?",
                (ip_f, n),
            ).fetchall()
        elif tgt:
            rows = conn.execute(
                f"SELECT {cols} FROM events WHERE instr(target, ?) > 0 ORDER BY ts DESC LIMIT ?",
                (tgt, n),
            ).fetchall()
        else:
            rows = conn.execute(
                f"SELECT {cols} FROM events ORDER BY ts DESC LIMIT ?",
                (n,),
            ).fetchall()
    return [dict(r) for r in rows]
