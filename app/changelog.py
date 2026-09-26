"""Lecture du journal des versions (CHANGELOG.md) pour l’onglet À propos."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

_HEADING = re.compile(r"^##\s+(\S+)\s*(?:—|--|-)\s*(.+?)\s*$")
_BULLET = re.compile(r"^[-*]\s+(.+)$")

# app/ → dépôt (…/CHANGELOG.md) ; image Docker : /app/CHANGELOG.md
_CANDIDATES = (
    Path(__file__).resolve().parent.parent / "CHANGELOG.md",
    Path("/app/CHANGELOG.md"),
)


def _changelog_path() -> Path | None:
    for path in _CANDIDATES:
        if path.is_file():
            return path
    return None


@lru_cache(maxsize=1)
def load_entries() -> tuple[dict, ...]:
    """Entrées {version, date, bullets} dans l’ordre du fichier (récent d’abord)."""
    path = _changelog_path()
    if path is None:
        return ()
    entries: list[dict] = []
    current: dict | None = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.rstrip()
        m = _HEADING.match(line)
        if m:
            if current is not None:
                entries.append(current)
            current = {
                "version": m.group(1).strip(),
                "date": m.group(2).strip(),
                "bullets": [],
            }
            continue
        if current is None:
            continue
        b = _BULLET.match(line.strip())
        if b:
            current["bullets"].append(b.group(1).strip())
    if current is not None:
        entries.append(current)
    return tuple(entries)
