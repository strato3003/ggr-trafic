"""Journal horodaté des visites (IP, page, replay). Pas exposé dans Prometheus."""

from __future__ import annotations

import logging
import sqlite3
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
          kind TEXT NOT NULL,
          target TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_ip_ts ON events(ip, ts)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts)")
    conn.commit()
    _state["path"] = path
    _state["conn"] = conn
    return conn


def append(ip: str, kind: str, target: str, country: str = "", city: str = "") -> None:
    ip = (ip or "").strip()
    kind = (kind or "").strip()
    target = (target or "").strip()[:120]
    if not ip or kind not in {"page", "replay"} or not target:
        return
    ts = datetime.now(timezone.utc).isoformat()
    country = (country or "")[:80]
    city = (city or "")[:80]
    try:
        with _lock:
            conn = _conn()
            conn.execute(
                "INSERT INTO events (ts, ip, country, city, kind, target) VALUES (?,?,?,?,?,?)",
                (ts, ip, country, city, kind, target),
            )
            cutoff = (datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)).isoformat()
            conn.execute("DELETE FROM events WHERE ts < ?", (cutoff,))
            conn.commit()
    except Exception:
        log.exception("Journal visites")


def list_events(ip: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    n = max(1, min(int(limit or 200), 500))
    ip_f = (ip or "").strip()
    with _lock:
        conn = _conn()
        if ip_f:
            rows = conn.execute(
                "SELECT ts, ip, country, city, kind, target FROM events WHERE ip = ? ORDER BY ts DESC LIMIT ?",
                (ip_f, n),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT ts, ip, country, city, kind, target FROM events ORDER BY ts DESC LIMIT ?",
                (n,),
            ).fetchall()
    return [dict(r) for r in rows]
