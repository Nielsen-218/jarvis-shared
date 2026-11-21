"""Local teaching ingress. Contributions are data, never execution authority.

SQLite serializes mentor writes and owns provenance/audit; extracted content is
stored in the existing engineering library. No network listener is started here.
Local CLI administration relies on the signed-in OS owner's filesystem access.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager

from core import storage
from core.skill_memory import _SECRET

MAX_TEXT = 200_000
MAX_LESSONS = 2000
SCOPES = {"submit", "read"}
FIELDS = {"title", "topic", "source", "license", "version", "content", "architecture",
          "techniques", "mistakes", "dependencies", "examples", "exercises", "tests"}
_QUOTED_SECRET = re.compile(
    r'''(?ix)(?:["']?(?:password|api[_-]?key|access[_-]?token|private[_-]?key|secret)["']?\s*[:=]\s*["']?[^\s"',;}{]{4,}|\bBearer\s+[a-z0-9._-]{16,}|\bgh[pousr]_[a-z0-9]{20,})''')


def contains_credentials(text):
    return bool(_SECRET.search(text) or _QUOTED_SECRET.search(text))


@contextmanager
def database():
    path = storage.path("household/teaching/gateway.sqlite3")
    db = sqlite3.connect(path, timeout=20)
    db.row_factory = sqlite3.Row
    try:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS mentors (
          id TEXT PRIMARY KEY, digest TEXT NOT NULL, scopes TEXT NOT NULL,
          expires REAL NOT NULL, revoked INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS lessons (
          id TEXT PRIMARY KEY, mentor TEXT NOT NULL, digest TEXT NOT NULL,
          revision INTEGER NOT NULL, doc_id TEXT NOT NULL, payload TEXT NOT NULL,
          created REAL NOT NULL, UNIQUE(mentor,digest));
        CREATE TABLE IF NOT EXISTS audit (
          at REAL NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL, target TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS attempts (
          id TEXT PRIMARY KEY, lesson TEXT NOT NULL, evidence TEXT NOT NULL, created REAL NOT NULL);
        """)
        db.execute("BEGIN IMMEDIATE")
        if 'indexed' not in {r[1] for r in db.execute('PRAGMA table_info(lessons)')}:
            db.execute('ALTER TABLE lessons ADD COLUMN indexed INTEGER NOT NULL DEFAULT 1')
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def audit(db, actor, action, target):
    db.execute("INSERT INTO audit VALUES (?,?,?,?)", (time.time(), actor, action, target))


def grant(mentor: str, scopes=("submit", "read"), hours=24) -> str:
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", mentor) or mentor == "owner":
        raise ValueError("Use a mentor ID of 1-64 letters, digits, hyphens or underscores")
    scopes = sorted(set(scopes))
    if not scopes or not set(scopes) <= SCOPES or not 0 < hours <= 8760:
        raise ValueError("Invalid scopes or expiration")
    token = secrets.token_urlsafe(32)
    with database() as db:
        db.execute("INSERT OR REPLACE INTO mentors VALUES (?,?,?,?,0)",
                   (mentor, hashlib.sha256(token.encode()).hexdigest(), json.dumps(scopes), time.time()+hours*3600))
        audit(db, "owner", "grant", mentor)
    return token


def revoke(mentor):
    with database() as db:
        db.execute("UPDATE mentors SET revoked=1 WHERE id=?", (mentor,))
        audit(db, "owner", "revoke", mentor)


def authorize(db, token, scope):
    if not isinstance(token, str) or not token:
        raise PermissionError("Mentor credential required")
    digest = hashlib.sha256(token.encode()).hexdigest()
    row = db.execute("SELECT * FROM mentors WHERE digest=?", (digest,)).fetchone()
    if not row or row["revoked"] or row["expires"] <= time.time() or scope not in json.loads(row["scopes"]):
        raise PermissionError("Mentor credential invalid, expired, revoked or out of scope")
    return row["id"]


def validate(payload):
    if not isinstance(payload, dict) or set(payload) - FIELDS:
        raise ValueError("Unknown lesson fields; identity and status are assigned by the gateway")
    clean = {}
    for key in FIELDS:
        value = payload.get(key, "")
        if not isinstance(value, str):
            raise ValueError(key + " must be text")
        clean[key] = value.strip()
    if any(not clean[k] for k in ("title", "topic", "source", "license", "version", "content")):
        raise ValueError("title, topic, source, license, version and content are required")
    if any(len(clean[k]) > 500 for k in ("title", "topic", "source", "license", "version")):
        raise ValueError("Lesson metadata too long")
    encoded = json.dumps(clean, ensure_ascii=False)
    if len(encoded) > MAX_TEXT or any(contains_credentials(value) for value in clean.values()):
        raise ValueError("Lesson too large or contains credential-like text")
    return clean


def submit(payload, token=None, *, owner=False):
    clean = validate(payload)
    digest = hashlib.sha256(json.dumps(clean, sort_keys=True).encode()).hexdigest()
    with database() as db:
        actor = "owner" if owner else authorize(db, token, "submit")
        old = db.execute("SELECT * FROM lessons WHERE mentor=? AND digest=?", (actor, digest)).fetchone()
        if old:
            lid=old['id']
        else:
            if db.execute("SELECT COUNT(*) FROM lessons").fetchone()[0] >= MAX_LESSONS:
                raise ValueError("Learning quota reached; export/review library before adding more")
            previous = db.execute("SELECT payload FROM lessons").fetchall()
            revision = 1 + sum(json.loads(x[0])["title"].casefold() == clean["title"].casefold()
                               and json.loads(x[0])["topic"].casefold() == clean["topic"].casefold() for x in previous)
            lid = uuid.uuid4().hex
            db.execute("INSERT INTO lessons (id,mentor,digest,revision,doc_id,payload,created,indexed) VALUES (?,?,?,?,?,?,?,0)",
                       (lid,actor,digest,revision,lid,json.dumps(clean),time.time()))
            audit(db,actor,'received',lid)
    # Receipt is durable before a separate library projection. A crash cannot
    # silently discard the lesson; deterministic doc IDs make retries idempotent.
    project(lid)
    with database() as db:
        result=_lesson(db,db.execute('SELECT * FROM lessons WHERE id=?',(lid,)).fetchone())
    return {**result,'duplicate':True} if old else result


def project(lesson_id):
    try:
        with database() as db:
            row=db.execute('SELECT * FROM lessons WHERE id=?',(lesson_id,)).fetchone()
            if row is None: raise ValueError('Unknown lesson')
            if row['indexed']: return True
            from tools.engineering import _save_doc
            payload=json.loads(row['payload'])
            text='\n\n'.join(k.upper()+'\n'+v for k,v in payload.items() if v)
            _save_doc(payload['title'],text,payload['source'],'teaching,'+payload['topic'],
                      owner_only=True,doc_id=row['doc_id'])
            db.execute('UPDATE lessons SET indexed=1 WHERE id=?',(lesson_id,))
            audit(db,row['mentor'],'indexed',lesson_id)
        return True
    except (OSError,sqlite3.Error):
        return False


def _lesson(db, row):
    item = dict(row)
    item["payload"] = json.loads(item["payload"])
    item["state"] = "indexed_unverified" if item['indexed'] else 'received_unindexed'
    item["attempts"] = [json.loads(x[0]) for x in db.execute(
        "SELECT evidence FROM attempts WHERE lesson=? ORDER BY created", (item["id"],))]
    if item["attempts"]:
        item["state"] = "tested_example" if item["attempts"][-1]["passed"] else "practice_failed"
    return item


def search(query="", token=None, *, owner=False, limit=20):
    with database() as db:
        if not owner:
            authorize(db, token, "read")
        terms = set(re.findall(r"[a-z0-9+#]{2,}", query.casefold()))
        ranked = []
        for row in db.execute("SELECT * FROM lessons ORDER BY created DESC"):
            p = json.loads(row["payload"])
            words = set(re.findall(r"[a-z0-9+#]{2,}", " ".join(p.values()).casefold()))
            score = len(terms & words)
            if not terms or score:
                ranked.append((score, row))
        ranked.sort(key=lambda x: -x[0])
        return [_lesson(db, row) for _, row in ranked[:max(1, min(limit, 100))]]


def status():
    with database() as db:
        return {"mentors": [{k:r[k] for k in ("id", "scopes", "expires", "revoked")}
                            for r in db.execute("SELECT * FROM mentors")],
                "audit": [dict(r) for r in db.execute("SELECT * FROM audit ORDER BY at DESC LIMIT 100")],
                "meaning": "Indexed is not mastered. Tested example is not general coding autonomy."}
