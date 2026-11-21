"""Append-only, hash-chained audit log with secret redaction.

Each record stores the hash of the previous one, so editing or deleting a line
in the middle is detectable with verify(). Truncating the END of the file is
not detectable by this alone; ship the latest hash somewhere else (e.g. the
7 AM report) if you need that.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_REDACT = "[REDACTED]"

_PATTERNS = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S), _REDACT),
    (re.compile(r"(--?(?:password|passwd|token|secret|api-?key|apikey)(?:=|\s+))\S+", re.I), r"\1" + _REDACT),
    (re.compile(
        r"(\b(?:password|passwd|pwd|token|secret|api[_-]?key|apikey|access[_-]?key|client[_-]?secret)\b\s*[=:]\s*)\S+",
        re.I), r"\1" + _REDACT),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"), _REDACT),
    (re.compile(r"\bghp_[A-Za-z0-9]{20,}"), _REDACT),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"), _REDACT),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), _REDACT),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"), _REDACT),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{16,}"), "Bearer " + _REDACT),
    (re.compile(r"(://)[^/\s:@]+:[^/\s@]+@"), r"\1" + _REDACT + "@"),
]


def redact(text: str) -> str:
    for rx, repl in _PATTERNS:
        text = rx.sub(repl, text)
    return text


def _redact_obj(obj: Any) -> Any:
    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, dict):
        return {k: _redact_obj(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_redact_obj(v) for v in obj]
    return obj


def _hash(rec: Dict[str, Any]) -> str:
    blob = json.dumps(rec, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class AuditLog:
    def __init__(self, path: Optional[str] = None):
        self.path = Path(path) if path else None
        self._lock = threading.Lock()
        self._seq = 0
        self._prev = "0" * 64
        self.records: List[Dict[str, Any]] = []  # populated only when path is None
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.exists():
                last = None
                for line in self.path.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        last = line
                if last:
                    try:
                        rec = json.loads(last)
                        self._seq, self._prev = int(rec["seq"]), rec["hash"]
                    except (ValueError, KeyError):
                        self._prev = hashlib.sha256(last.encode("utf-8")).hexdigest()

    def record(self, event: str, **fields: Any) -> Dict[str, Any]:
        with self._lock:
            rec: Dict[str, Any] = {
                "seq": self._seq + 1,
                "ts": datetime.now(timezone.utc).isoformat(),
                "event": event,
            }
            rec.update(_redact_obj(fields))
            rec["prev"] = self._prev
            rec["hash"] = _hash(rec)
            if self.path is not None:
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, sort_keys=True, default=str) + "\n")
            else:
                self.records.append(rec)
            self._seq, self._prev = rec["seq"], rec["hash"]
            return rec

    def verify(self) -> Tuple[bool, Optional[str]]:
        """Check the hash chain. Returns (ok, error_message)."""
        if self.path is None:
            recs = list(self.records)
        else:
            if not self.path.exists():
                return True, None
            recs = []
            for n, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), 1):
                if not line.strip():
                    continue
                try:
                    recs.append(json.loads(line))
                except ValueError:
                    return False, f"line {n}: not valid JSON"
        prev, seq = "0" * 64, 0
        for rec in recs:
            h = rec.pop("hash", None)
            if rec.get("prev") != prev:
                return False, f"seq {rec.get('seq')}: chain broken"
            if rec.get("seq") != seq + 1:
                return False, f"seq {rec.get('seq')}: sequence gap"
            if h != _hash(rec):
                return False, f"seq {rec.get('seq')}: record modified"
            prev, seq = h, rec["seq"]
        return True, None
