"""The report card: which subjects are mastered, how each was learned, what it cost, and whether JARVIS has graduated.

One small JSON file, written atomically. Nothing here decides anything; the School reads and updates it so that progress
survives restarts and the person can always ask "how is school going".
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Callable, Dict, List, Optional

NEW, LEARNING, MASTERED, NEEDS_COLLEGE, STUCK = "new", "learning", "mastered", "needs_college", "stuck"
HISTORY_KEEP = 12


def _blank() -> Dict[str, Any]:
    return {"state": NEW, "playbook": "", "learned_by": "", "local_tries": 0, "api_tries": 0, "exam_streak": 0,
            "exams": 0, "failures": 0, "last_note": "", "history": [], "mastered_at": 0.0, "updated": 0.0}


class Transcript:
    def __init__(self, path: str, *, clock: Callable[[], float] = time.time):
        self.path, self.clock = path, clock
        self._lock = threading.RLock()
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)

    # ------------------------------------------------------------------ storage
    def _load(self) -> Dict[str, Any]:
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {}
        data.setdefault("subjects", {})
        data.setdefault("api_spend", {})
        data.setdefault("graduated", None)
        return data

    def _save(self, data: Dict[str, Any]) -> None:
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1, sort_keys=True)
        os.replace(tmp, self.path)

    # ------------------------------------------------------------------ subjects
    def get(self, subject_id: str) -> Dict[str, Any]:
        with self._lock:
            return {**_blank(), **self._load()["subjects"].get(subject_id, {})}

    def update(self, subject_id: str, note: str = "", touch: bool = True, **fields: Any) -> Dict[str, Any]:
        with self._lock:
            data = self._load()
            row = {**_blank(), **data["subjects"].get(subject_id, {})}
            row.update(fields)
            if touch:  # `updated` is when the skill was last proven; bookkeeping flags must not reset the review clock
                row["updated"] = self.clock()
            if note:
                row["last_note"] = note[:300]
                row["history"] = (row["history"] + [{"t": self.clock(), "note": note[:200]}])[-HISTORY_KEEP:]
            data["subjects"][subject_id] = row
            self._save(data)
            return row

    def state(self, subject_id: str) -> str:
        return self.get(subject_id)["state"]

    def mastered_ids(self) -> List[str]:
        with self._lock:
            return [k for k, v in self._load()["subjects"].items() if v.get("state") == MASTERED]

    # ------------------------------------------------------------------ API budget (per day, per provider)
    @staticmethod
    def _day(t: float) -> str:
        return time.strftime("%Y-%m-%d", time.localtime(t))

    @staticmethod
    def _day_row(value: Any) -> Dict[str, int]:
        return dict(value) if isinstance(value, dict) else ({"api": int(value)} if value else {})  # older files stored a bare count

    def api_calls_today(self, label: Optional[str] = None) -> int:
        with self._lock:
            row = self._day_row(self._load()["api_spend"].get(self._day(self.clock())))
            return int(row.get(label, 0)) if label else int(sum(row.values()))

    def api_calls_total(self, label: Optional[str] = None) -> int:
        with self._lock:
            rows = [self._day_row(v) for v in self._load()["api_spend"].values()]
            return int(sum((r.get(label, 0) if label else sum(r.values())) for r in rows))

    def spend_api(self, label: str = "api") -> None:
        with self._lock:
            data = self._load()
            day = self._day(self.clock())
            row = self._day_row(data["api_spend"].get(day))
            row[label] = int(row.get(label, 0)) + 1
            data["api_spend"][day] = row
            self._save(data)

    def refund_api(self, label: str = "api") -> None:
        """Give back one call of today's budget (the provider never answered, so nothing was spent)."""
        with self._lock:
            data = self._load()
            day = self._day(self.clock())
            row = self._day_row(data["api_spend"].get(day))
            if row.get(label, 0) > 0:
                row[label] -= 1
                data["api_spend"][day] = row
                self._save(data)

    # ------------------------------------------------------------------ graduation
    def graduated(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._load()["graduated"]

    def graduate(self, note: str) -> Dict[str, Any]:
        with self._lock:
            data = self._load()
            data["graduated"] = {"at": self.clock(), "note": note}
            self._save(data)
            return data["graduated"]

    def revoke_graduation(self) -> None:
        with self._lock:
            data = self._load()
            if data["graduated"]:
                data["graduated"] = None
                self._save(data)
