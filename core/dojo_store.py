"""JARVIS's long-term memory for learning a craft (Blender first; the same shape serves any skill).

Everything is plain JSON under data/dojo/<skill>/ so it survives restarts, can be read by the owner, and is never
mixed into the repo:
  events.jsonl   append-only ledger of everything he did (sessions, experiments, promotions, demotions)
  lessons.json   what he tried and what happened: verified AND refuted (so a failed idea is not retried blindly)
  playbook.json  learned strategies (parameter sets / guard-checked code overlays) with the evidence that earned them
  cases.json     per practice case: latest result, whether its failures were ever studied ("burned" for held-out use)
  state.json     the current/last practice session (resume + status)
  STOP           flag file: the owner's kill switch (checked between experiments)
  episodes.jsonl one row per judged attempt (core/learn.py): task, split, action, outcome, judge hash, assisted flag
  sealed.json    held-out task ids sealed before training (content hash + date); a sealed task can never be trained on
  policies/      versioned learned policies (core/learn_policy.py): JSON with parent, hash, training episode ids
"""
import json
import os
import threading
import time
import uuid
from pathlib import Path

from core import storage


class Store:
    def __init__(self, skill="blender", root=None):
        self.root = Path(root) if root else Path(storage.DATA) / "dojo" / skill
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "overlays").mkdir(exist_ok=True)
        self.lock = threading.RLock()

    # ------------------------------------------------------------------ plumbing
    def _read(self, name, default):
        p = self.root / name
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return default

    def _write(self, name, data):
        p = self.root / name
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=1, default=str), encoding="utf-8")
        os.replace(tmp, p)                                   # atomic: a crash never leaves half a memory

    # ------------------------------------------------------------------ ledger
    def event(self, kind, **data):
        row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "kind": kind, **data}
        with self.lock, open(self.root / "events.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(row, default=str) + "\n")
        return row

    def events(self, limit=200, kind=None):
        p = self.root / "events.jsonl"
        if not p.is_file():
            return []
        rows = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
        return [r for r in rows if kind is None or r["kind"] == kind][-limit:]

    # ------------------------------------------------------------------ lessons
    def lessons(self):
        return self._read("lessons.json", [])

    def add_lesson(self, **lesson):
        with self.lock:
            rows = self.lessons()
            lesson = {"id": uuid.uuid4().hex[:8], "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), **lesson}
            rows.append(lesson)
            self._write("lessons.json", rows[-1000:])
        return lesson

    def relevant_lessons(self, signature, k=12):
        """Lessons whose failure signature shares the most tokens with `signature` (a set of strings); newest first on ties."""
        sig = set(signature)
        scored = []
        for i, l in enumerate(self.lessons()):
            overlap = len(sig & set(l.get("signature", [])))
            if overlap:
                scored.append((overlap, i, l))
        scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
        return [l for _, _, l in scored[:k]]

    def tried(self, case=None):
        """(change-key set) of everything already tried, so an idea is never re-run blindly."""
        return {l["change_key"] for l in self.lessons() if l.get("change_key") and (case is None or l.get("case") == case)}

    # ------------------------------------------------------------------ playbook (learned strategies)
    def playbook(self):
        return self._read("playbook.json", [])

    def add_variant(self, params, overlay_source=None, evidence=None, name=""):
        with self.lock:
            rows = self.playbook()
            vid = uuid.uuid4().hex[:8]
            row = {"id": vid, "name": name, "params": params or {}, "overlay_file": None, "status": "pending_review" if overlay_source else "active", "tries": 0, "wins": 0,
                   "evidence": evidence or {}, "created": time.strftime("%Y-%m-%dT%H:%M:%S")}
            if overlay_source:
                (self.root / "overlays" / f"{vid}.py").write_text(overlay_source, encoding="utf-8")
                row["overlay_file"] = f"{vid}.py"
            rows.append(row)
            self._write("playbook.json", rows)
        return row

    def set_variant_status(self, vid, status, why=""):
        with self.lock:
            rows = self.playbook()
            for r in rows:
                if r["id"] == vid:
                    r["status"] = status
                    r["status_why"] = why
            self._write("playbook.json", rows)

    def record_variant_use(self, vid, won):
        with self.lock:
            rows = self.playbook()
            for r in rows:
                if r["id"] == vid:
                    r["tries"] += 1
                    r["wins"] += 1 if won else 0
            self._write("playbook.json", rows)

    def runtime_variants(self, include_pending=False):
        """Learned strategies as the engine consumes them (best win rate first). Strategies containing CODE the learner wrote for
        itself are `pending_review` until the owner approves them: the dojo may test them, real rigging never runs them."""
        out = []
        for r in sorted(self.playbook(), key=lambda r: -((r["wins"] + 1) / (r["tries"] + 2))):
            if r["status"] != "active" and not (include_pending and r["status"] == "pending_review"):
                continue
            src = None
            if r.get("overlay_file"):
                f = self.root / "overlays" / r["overlay_file"]
                if not f.is_file():
                    continue
                src = f.read_text(encoding="utf-8")
            out.append({"id": r["id"], "params": r["params"], "overlay_source": src})
        return out

    # ------------------------------------------------------------------ cases
    def cases(self):
        return self._read("cases.json", {})

    def case(self, name):
        return self.cases().get(name, {})

    def update_case(self, name, **fields):
        with self.lock:
            rows = self.cases()
            rows.setdefault(name, {}).update(fields)
            self._write("cases.json", rows)
            return rows[name]

    # ------------------------------------------------------------------ session state + kill switch
    def state(self):
        return self._read("state.json", {})

    def set_state(self, **fields):
        with self.lock:
            st = self.state()
            st.update(fields)
            self._write("state.json", st)
            return st

    def stop_requested(self):
        return (self.root / "STOP").exists()

    def request_stop(self):
        (self.root / "STOP").write_text(time.strftime("%Y-%m-%dT%H:%M:%S"), encoding="utf-8")

    def clear_stop(self):
        try:
            (self.root / "STOP").unlink()
        except FileNotFoundError:
            pass

    # ------------------------------------------------------------------ episodes, sealed exams, policies (core/learn.py)
    def add_episode(self, ep):
        row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "id": uuid.uuid4().hex[:10], **ep}
        with self.lock, open(self.root / "episodes.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(row, default=str) + "\n")
        return row

    def episodes(self, purpose=None, task=None, limit=None):
        p = self.root / "episodes.jsonl"
        if not p.is_file():
            return []
        rows = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
        rows = [r for r in rows if (purpose is None or r.get("purpose") == purpose) and (task is None or r.get("task") == task)]
        return rows[-limit:] if limit else rows

    def sealed(self):
        return self._read("sealed.json", {})

    def seal(self, task_id, digest):
        """Seal a held-out task: from now on it may only be used for exams. Returns False if it was already trained on."""
        with self.lock:
            if any(e.get("task") == task_id and e.get("purpose") == "train" for e in self.episodes()):
                return False
            rows = self.sealed()
            rows.setdefault(task_id, {"digest": digest, "sealed": time.strftime("%Y-%m-%dT%H:%M:%S")})
            self._write("sealed.json", rows)
            return True

    def save_policy(self, policy):
        d = self.root / "policies"
        d.mkdir(exist_ok=True)
        (d / f"{policy['version']}.json").write_text(json.dumps(policy, indent=1), encoding="utf-8")
        self._write("policy_head.json", {"version": policy["version"]})

    def load_policy(self, version=None):
        version = version or self._read("policy_head.json", {}).get("version")
        if not version:
            return None
        try:
            return json.loads((self.root / "policies" / f"{version}.json").read_text(encoding="utf-8"))
        except Exception:
            return None

    # ------------------------------------------------------------------ knowledge cards (research results)
    def cards(self):
        return self._read("knowledge.json", [])

    def add_card(self, card):
        with self.lock:
            rows = [c for c in self.cards() if c.get("query_name") != card.get("query_name")]
            rows.append(card)
            self._write("knowledge.json", rows[-300:])
        return card

    def card_for(self, name):
        """Offline recall: the best stored card for a creature name (token overlap), or None."""
        want = set(str(name).lower().replace("-", " ").split())
        best = (0, None)
        for c in self.cards():
            have = set((c.get("query_name", "") + " " + c.get("creature", "")).lower().split())
            score = len(want & have)
            if score > best[0]:
                best = (score, c)
        return best[1]

    def pending_review(self):
        return [r for r in self.playbook() if r["status"] == "pending_review"]
