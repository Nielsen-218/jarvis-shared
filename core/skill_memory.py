"""Durable, offline coding-procedure notebook.

Saving a recipe is NOT executing it, certifying it, installing a tool or updating
model weights. Only trusted execution/test infrastructure may eventually promote
entries to verified executable skills.
"""
from __future__ import annotations

import re
import threading
import sqlite3
from contextlib import contextmanager, closing
import uuid
from datetime import datetime, timezone

from core import storage

_LOCK = threading.Lock()
_FILES = (
    "household/learned-procedures-pc.json",
    "household/learned-procedures-server.json",
)
_SECRET = re.compile(
    r"(?i)(?:\b(?:password|api[_-]?key|access[_-]?token|private[_-]?key|secret)\b\s*[:=]\s*\S+|"
    r"\bsk-[a-z0-9_-]{12,}|-----BEGIN (?:RSA |OPENSSH )?PRIVATE KEY-----)"
)
_WORDS = re.compile(r"[a-z0-9+#.]{2,}")


@contextmanager
def _writers():
    """Serialize read/append/write across local mentor and application processes."""
    with closing(sqlite3.connect(storage.path('household/teaching/notebook-write-lock.sqlite3'),timeout=20)) as db:
        db.execute('CREATE TABLE IF NOT EXISTS mutex (id INTEGER PRIMARY KEY)');db.commit()
        db.execute('BEGIN IMMEDIATE')
        try:yield
        finally:db.rollback()


def _mine() -> str:
    from core.pcbridge import local_pc
    return _FILES[0] if local_pc() else _FILES[1]


def _read(name: str) -> list[dict]:
    value = storage.load(name, [])
    return [x for x in value if isinstance(x, dict) and isinstance(x.get("id"), str)] if isinstance(value, list) else []


def entries() -> list[dict]:
    """Read both machine-owned stores so synced knowledge survives a restart."""
    seen = set()
    rows = []
    for name in _FILES:
        for item in _read(name):
            if item["id"] not in seen:
                seen.add(item["id"])
                rows.append(item)
    return sorted(rows, key=lambda item: (item.get("saved", ""), item["id"]), reverse=True)


def _field(value: str, label: str, max_length: int, min_length: int = 1) -> str:
    if not isinstance(value, str):
        raise ValueError(label + " must be text")
    value = value.strip()
    if not min_length <= len(value) <= max_length:
        raise ValueError(f"{label} must be {min_length}-{max_length} characters")
    if _SECRET.search(value):
        raise ValueError("Do not save credentials or private keys in learned procedures")
    return value


def capture(language: str, title: str, procedure: str, source: str, how_to_test: str) -> dict:
    """Save an attributed candidate; caller-reported success is not a verified test."""
    language = _field(language, "Language", 64)
    title = _field(title, "Skill title", 120, 4)
    procedure = _field(procedure, "Procedure", 8000, 20)
    source = _field(source, "Source", 500, 4)
    how_to_test = _field(how_to_test, "Acceptance check", 1000, 8)
    with _LOCK, _writers():
        current = entries()
        for old in current:
            if (old.get("language", "").casefold() == language.casefold()
                    and old.get("title", "").casefold() == title.casefold()
                    and old.get("procedure") == procedure
                    and old.get("source") == source
                    and old.get("how_to_test") == how_to_test):
                return {**old, "duplicate": True}
        revision = 1 + max((int(x.get("revision", 0)) for x in current
                            if x.get("language", "").casefold() == language.casefold()
                            and x.get("title", "").casefold() == title.casefold()), default=0)
        entry = {
            "id": uuid.uuid4().hex,
            "language": language,
            "title": title,
            "procedure": procedure,
            "source": source,
            "how_to_test": how_to_test,
            "revision": revision,
            "state": "captured_unverified",
            "saved": datetime.now(timezone.utc).isoformat(),
        }
        own = _read(_mine())
        own.append(entry)
        storage.save(_mine(), own)
        return entry


def search(query: str, limit: int = 3) -> list[dict]:
    """No cloud, command execution or dynamic imports. Return attributed data only."""
    query = _field(query, "Search", 300)
    terms = set(_WORDS.findall(query.casefold()))
    if not terms:
        return []
    ranked = []
    for row in entries():
        title = set(_WORDS.findall(str(row.get("title", "")).casefold()))
        language = set(_WORDS.findall(str(row.get("language", "")).casefold()))
        body = set(_WORDS.findall(str(row.get("procedure", "")).casefold()))
        score = 5 * len(terms & title) + 4 * len(terms & language) + len(terms & body)
        if score:
            ranked.append((score, row))
    ranked.sort(key=lambda pair: (-pair[0], pair[1].get("title", "")))
    return [row for _, row in ranked[:max(1, min(int(limit), 5))]]
