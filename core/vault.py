"""The memory vault: recordings, videos, photos and notes the household asks Jarvis to remember.

Originals go to the vault folder (the laptop's 2 TB drive when it's mounted at /mnt/jarvis-vault, else
<jarvis>/vault), one folder per person and day. Each item is transcribed (speech) or described (photos), given
a title, summary, tags and people by the fast model, and indexed in SQLite full-text search, so Jarvis can find
it by keywords and dates and point back to the original file.

Nothing records on its own: items only exist because someone pressed Record, took a photo or asked Jarvis to
save a note.
"""
import json
import os
import shutil
import sqlite3
import threading
import uuid
from datetime import datetime
from pathlib import Path

from core.config import ROOT

DRIVE = Path("/mnt/jarvis-vault")
KINDS = ("audio", "video", "photo", "note")
_lock = threading.Lock()


def root() -> Path:
    custom = os.getenv("JARVIS_VAULT")
    if custom:
        return Path(custom)
    return DRIVE if DRIVE.is_mount() else ROOT / "vault"


def _db() -> sqlite3.Connection:
    r = root()
    r.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(r / "index.db", timeout=30, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.executescript("""
        CREATE TABLE IF NOT EXISTS items (
            id TEXT PRIMARY KEY, uid TEXT, kind TEXT, created TEXT, title TEXT, summary TEXT, transcript TEXT,
            tags TEXT, people TEXT, note TEXT, path TEXT, mime TEXT, size INTEGER, seconds REAL,
            status TEXT, source TEXT);
        CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(
            id UNINDEXED, title, summary, transcript, tags, people, note, tokenize = 'porter unicode61');
    """)
    return con


def _row(r: sqlite3.Row) -> dict:
    return {k: r[k] for k in r.keys()}


def _index(con, item: dict) -> None:
    con.execute("DELETE FROM items_fts WHERE id = ?", (item["id"],))
    con.execute("INSERT INTO items_fts (id, title, summary, transcript, tags, people, note) VALUES (?,?,?,?,?,?,?)",
                (item["id"], item.get("title") or "", item.get("summary") or "", item.get("transcript") or "",
                 item.get("tags") or "", item.get("people") or "", item.get("note") or ""))


def add(uid: str, kind: str, data: bytes | None = None, src: Path | None = None, ext: str = "", mime: str = "",
        note: str = "", source: str = "phone", text: str = "", background: bool = True, title: str = "") -> dict:
    """Store an original (bytes, or a temp file moved in) and process it for search. A `title` the person gave is
    kept as the title — the background labelling only invents one when there isn't."""
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    now = datetime.now()
    item_id = now.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    path = ""
    if kind != "note":
        folder = root() / uid / now.strftime("%Y/%m/%d")
        folder.mkdir(parents=True, exist_ok=True)
        dest = folder / f"{item_id}{ext}"
        if src is not None:
            shutil.move(str(src), dest)
        else:
            dest.write_bytes(data or b"")
        path = dest.relative_to(root()).as_posix()
    item = {"id": item_id, "uid": uid, "kind": kind, "created": now.isoformat(timespec="seconds"),
            "title": (title or "").strip()[:120], "summary": "", "transcript": text if kind == "note" else "",
            "tags": "", "people": "",
            "note": note.strip()[:2000], "path": path, "mime": mime,
            "size": (root() / path).stat().st_size if path else len(text), "seconds": None,
            "status": "processing", "source": source}
    with _lock, _db() as con:
        con.execute(f"INSERT INTO items ({','.join(item)}) VALUES ({','.join('?' * len(item))})", list(item.values()))
        _index(con, item)
    if background:
        threading.Thread(target=process, args=(item_id,), daemon=True, name=f"vault-{item_id}").start()
    else:
        process(item_id)
    return get(item_id) or item


def get(item_id: str, uid: str | None = None) -> dict | None:
    with _db() as con:
        r = con.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    item = _row(r) if r else None
    return item if item and (uid is None or item["uid"] == uid) else None


def file_path(item: dict) -> Path | None:
    if not item.get("path"):
        return None
    base = root().resolve()
    p = (base / item["path"]).resolve()
    return p if p.is_relative_to(base) and p.is_file() else None


def _update(item_id: str, **fields) -> None:
    with _lock, _db() as con:
        con.execute(f"UPDATE items SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?", [*fields.values(), item_id])
        r = con.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
        if r:
            _index(con, _row(r))


# -- processing: speech -> text, photo -> description, then title / summary / tags ---------------------
_stt = None  # the server's Whisper model (shared with phone voice notes)


def set_transcriber(stt) -> None:
    global _stt
    _stt = stt


def _transcribe(p: Path) -> tuple[str, float]:
    import av
    import numpy as np

    chunks = []
    with av.open(str(p)) as container:
        if not container.streams.audio:
            return "", 0.0
        rs = av.AudioResampler(format="s16", layout="mono", rate=16000)
        for frame in container.decode(audio=0):
            for out in rs.resample(frame):
                chunks.append(out.to_ndarray().reshape(-1))
        for out in rs.resample(None):
            chunks.append(out.to_ndarray().reshape(-1))
    audio = np.concatenate(chunks).astype(np.int16) if chunks else np.zeros(0, np.int16)
    seconds = len(audio) / 16000
    if _stt is None or seconds < 0.5:
        return "", seconds
    return _stt.transcribe(audio), seconds


MODEL = os.getenv("JARVIS_FAST_MODEL", "claude-haiku-5-5")


def _client():
    from core.intelligence import local_only,LocalFailure
    if local_only():
        raise LocalFailure("local_only_no_cloud")
    import anthropic

    return anthropic.Anthropic()


def _describe_photo(p: Path, mime: str, note: str) -> str:
    from core.intelligence import local_only
    if local_only():
        return note  # Retain owner evidence; do not invent an image description.
    import base64

    data = base64.b64encode(p.read_bytes()).decode()
    r = _client().messages.create(model=MODEL, max_tokens=600, messages=[{"role": "user", "content": [
        {"type": "image", "source": {"type": "base64", "media_type": mime or "image/jpeg", "data": data}},
        {"type": "text", "text": "Describe this photo for a personal memory archive so it can be found later by "
                                 "search: what and who is in it (by role, don't guess names), where it seems to be, "
                                 "any readable text (receipts, signs, labels: copy key numbers, prices, dates) and "
                                 f"anything notable. 3-6 sentences. Their note: {note or '(none)'}"}]}])
    return "".join(b.text for b in r.content if b.type == "text").strip()


def _label(kind: str, text: str, note: str, created: str) -> dict:
    """Title, summary, tags and people from the transcript / description (fast model, JSON out)."""
    from core.intelligence import local_only
    if local_only():
        evidence=note or text
        return {"title":" ".join(evidence.split()[:8])[:120] or
                f"{kind.title()} from {created[:16].replace('T',' ')}",
                "summary":text[:1500],"tags":"","people":""}
    if not (text or note):
        return {"title": f"{kind.title()} from {created[:16].replace('T', ' ')}", "summary": "", "tags": "", "people": ""}
    prompt = ("You index a personal memory archive. From this " + kind + " (recorded " + created + "), return JSON "
              "only: {\"title\": short title (max 8 words), \"summary\": 1-3 sentences with the key facts, decisions, "
              "numbers, dates and promises, \"tags\": 5-12 lowercase search keywords including synonyms (e.g. car, "
              "vehicle, brakes, mechanic), \"people\": names or roles mentioned, comma-separated}.\nNote from the user: "
              + (note or "(none)") + "\nContent:\n" + text[:24000])
    r = _client().messages.create(model=MODEL, max_tokens=700, messages=[{"role": "user", "content": prompt}])
    raw = "".join(b.text for b in r.content if b.type == "text").strip()
    raw = raw[raw.find("{"): raw.rfind("}") + 1]
    try:
        out = json.loads(raw)
    except ValueError:
        out = {"title": (note or text)[:60], "summary": text[:300]}

    def as_text(v):
        return ", ".join(map(str, v)) if isinstance(v, list) else str(v or "")
    return {"title": as_text(out.get("title"))[:120], "summary": as_text(out.get("summary"))[:1500],
            "tags": as_text(out.get("tags"))[:500], "people": as_text(out.get("people"))[:300]}


def process(item_id: str) -> None:
    item = get(item_id)
    if not item:
        return
    try:
        text, seconds = item.get("transcript") or "", None
        p = file_path(item)
        if item["kind"] in ("audio", "video") and p:
            text, seconds = _transcribe(p)
        elif item["kind"] == "photo" and p:
            text = _describe_photo(p, item.get("mime", ""), item.get("note", ""))
        labels = _label(item["kind"], text, item.get("note", ""), item["created"])
        if item.get("title"):
            labels["title"] = item["title"]          # the person's own title beats a generated one
        _update(item_id, transcript=text, seconds=seconds, status="ready", **labels)
    except Exception as e:
        print(f"[vault] processing {item_id} failed: {type(e).__name__}: {e}")
        fallback = {}
        if item["kind"] == "note" and not item.get("title") and (item.get("transcript") or "").strip():
            # The labelling model was unreachable: a note is still named by its own words, never "(untitled)".
            fallback["title"] = " ".join(item["transcript"].split()[:8])[:120]
        _update(item_id, status="error", **fallback)


def reprocess_pending() -> None:
    """After a restart: finish anything that was still processing (or failed, e.g. no internet)."""
    with _db() as con:
        ids = [r["id"] for r in con.execute("SELECT id FROM items WHERE status IN ('processing', 'error')")]
    for i in ids:
        process(i)


# -- search -----------------------------------------------------------------------------------------
_STOP = {"the", "and", "what", "did", "we", "about", "talk", "talked", "regarding", "remember", "my", "me", "you",
         "was", "were", "that", "this", "with", "for", "from", "when", "where", "who", "said", "say", "an", "to",
         "of", "in", "on", "it", "is", "do", "does", "yesterday", "today", "last", "week"}


def _fts_query(q: str) -> str:
    words = [w for w in "".join(c if c.isalnum() else " " for c in q.lower()).split() if len(w) > 1 and w not in _STOP]
    return " OR ".join(f'"{w}"*' for w in words[:16])


def search(uid: str, query: str = "", start: str = "", end: str = "", kind: str = "", limit: int = 8) -> list[dict]:
    where, args, join = ["i.uid = ?"], [uid], ""
    q = _fts_query(query) if query else ""
    if q:
        join = " JOIN items_fts f ON f.id = i.id"
        where.append("items_fts MATCH ?")
        args.append(q)
    if start:
        where.append("i.created >= ?")
        args.append(start)
    if end:
        where.append("i.created < ?")
        args.append(end)
    if kind in KINDS:
        where.append("i.kind = ?")
        args.append(kind)
    order = "bm25(items_fts), i.created DESC" if q else "i.created DESC, i.rowid DESC"
    sql = f"SELECT i.* FROM items i{join} WHERE {' AND '.join(where)} ORDER BY {order} LIMIT ?"
    args.append(max(1, min(int(limit or 8), 50)))
    with _db() as con:
        return [_row(r) for r in con.execute(sql, args)]


def delete(item_id: str, uid: str) -> bool:
    item = get(item_id, uid)
    if not item:
        return False
    p = file_path(item)
    if p:
        p.unlink(missing_ok=True)
    with _lock, _db() as con:
        con.execute("DELETE FROM items WHERE id = ?", (item_id,))
        con.execute("DELETE FROM items_fts WHERE id = ?", (item_id,))
    return True


def stats() -> dict:
    r = root()
    with _db() as con:
        rows = con.execute("SELECT kind, COUNT(*), COALESCE(SUM(size), 0) FROM items GROUP BY kind").fetchall()
    return {"where": str(r), "on_drive": r == DRIVE, "items": {k: c for k, c, _ in rows},
            "bytes": sum(s for _, _, s in rows), "free_gb": round(shutil.disk_usage(r).free / 1e9, 1)}
