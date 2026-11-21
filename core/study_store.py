"""The study companion's document store: courses, the material in them (PDF, text, markdown, pasted notes) split into
chapter/section passages with page numbers, and a lexical BM25 search so answers and questions can cite the page.

Files, per person (data/users/<uid>/study/), written so the PC and the laptop never fight over one file (data/ is
synced between them):
  courses/<course_id>.json     one small file per course (title, exam date). Rewritten only when he changes the course.
  materials/<material_id>.json one file per document, written ONCE when it is added (its id is a hash of course + text,
                               so adding the same document on both machines produces the same bytes).
Progress (answers, card reviews) is not here: core/study_engine keeps it as per-machine event files.
The built-in IT fundamentals course (core/study_seed) is never written to disk.

No embedding model and no new dependency: PDFs are read with pypdf when it is installed, otherwise only text works."""
import hashlib
import io
import math
import re
import unicodedata
from datetime import datetime

from core import storage, study_seed

MAX_BYTES = 40 * 1024 * 1024      # one upload
MAX_CHUNKS = 6000                 # one document
CHUNK_CHARS = 1100                # a passage: about 250 tokens, small enough to quote, large enough to hold a definition
_CONFLICT = ".sync-conflict"


class StudyError(ValueError):
    """Something he can fix: said to him as it is."""


def pdf_ready() -> bool:
    try:
        import pypdf  # noqa: F401
        return True
    except Exception:
        return False


# ---- text out of a document -------------------------------------------------------------------------------------
def _clean(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "").replace("­", "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)                 # a word hyphenated across a line break
    return re.sub(r"[ \t]+", " ", text)


def pages_of(data: bytes, filename: str = "") -> list[tuple[int | None, str]]:
    """-> [(page number or None, text)]. PDFs give real page numbers; text gives pages only where it has form feeds."""
    if len(data) > MAX_BYTES:
        raise StudyError("That file is over 40 MB. Split it by chapter and add the parts.")
    name = filename.lower()
    if name.endswith(".pdf") or data[:5] == b"%PDF-":
        if not pdf_ready():
            raise StudyError("I can't read PDFs on this machine yet (no PDF reader installed). Text and markdown work.")
        from pypdf import PdfReader
        try:
            reader = PdfReader(io.BytesIO(data))
            pages = [(n, _clean(p.extract_text() or "")) for n, p in enumerate(reader.pages, 1)]
        except Exception as e:
            raise StudyError(f"I couldn't open that PDF ({type(e).__name__}).") from None
        if sum(len(t.strip()) for _, t in pages) < 200:
            raise StudyError("That PDF has no text layer (it looks scanned). I need a text PDF or the notes as text.")
        return _without_running_lines(pages)
    if name.endswith((".pptx", ".docx", ".doc", ".ppt", ".epub")):
        raise StudyError("I can't read that format yet. Export it as PDF or text and add that.")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("cp1252", "replace")
    parts = _clean(text).split("\f")
    if len(parts) > 1:
        return [(n, p) for n, p in enumerate(parts, 1)]
    return [(None, parts[0])]


def _without_running_lines(pages):
    """Drop the book title / chapter header / footer that repeats on most pages (noise in every passage)."""
    if len(pages) < 4:
        return pages
    seen = {}
    for _, text in pages:
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        for line in set(lines[:2] + lines[-2:]):
            key = re.sub(r"\d+", "#", line)
            seen[key] = seen.get(key, 0) + 1
    noise = {k for k, n in seen.items() if n >= max(3, len(pages) * 0.5) and len(k) < 90}
    out = []
    for number, text in pages:
        lines = text.split("\n")
        out.append((number, "\n".join(l for l in lines if re.sub(r"\d+", "#", l.strip()) not in noise)))
    return out


# ---- chapters, sections, passages -------------------------------------------------------------------------------
_CHAPTER = re.compile(r"^(?:#\s+(?P<md>.+)|(?P<kw>(?:chapter|module|unit|lecture|week|lesson|part)\s+(?:\d+|[ivxlc]+)\b.{0,80}))$", re.I)
_SECTION = re.compile(r"^(?:#{2,4}\s+(?P<md>.+)|(?P<num>\d{1,2}\.\d{1,2}(?:\.\d{1,2})?\s+[A-Z].{2,78}))$")
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")


def _heading(line: str):
    """-> ("chapter"|"section", title) or None. Only short lines that do not end like a sentence."""
    line = line.strip()
    if not line or len(line) > 90 or line.endswith((".", ",", ";", ":")) and not line.startswith("#"):
        return None
    m = _CHAPTER.match(line)
    if m:
        return "chapter", (m.group("md") or m.group("kw")).strip(" #")
    m = _SECTION.match(line)
    if m:
        return "section", (m.group("md") or m.group("num")).strip(" #")
    return None


def chunk(pages) -> list[dict]:
    """Passages that never cross a chapter or section, each with the page it starts and ends on. Paragraphs are kept
    whole; one longer than a passage is split between sentences (never mid-sentence)."""
    chunks, chapter, section = [], "", ""
    buf, start, last = [], None, None

    def flush():
        nonlocal buf, start
        text = "\n".join(buf).strip()
        if len(text) >= 40:
            chunks.append({"chapter": chapter, "section": section, "page": start, "page_end": last, "text": text})
        buf, start = [], None

    def add(piece, page):
        nonlocal start, last
        if buf and sum(len(b) for b in buf) + len(piece) > CHUNK_CHARS:
            flush()
        if start is None:
            start = page
        last = page
        buf.append(piece)

    for page, text in pages:
        for para in re.split(r"\n\s*\n", text) if "\n\n" in text else text.split("\n"):
            lines = [l for l in para.split("\n") if l.strip()]
            if not lines:
                continue
            head = _heading(lines[0])
            if head:
                flush()
                if head[0] == "chapter":
                    chapter, section = head[1], ""
                else:
                    section = head[1]
                lines = lines[1:]
                if not lines:
                    continue
            body = " ".join(l.strip() for l in lines)
            if len(body) <= CHUNK_CHARS:
                add(body, page)
                continue
            piece = ""
            for sentence in _SENTENCE.split(body):
                if piece and len(piece) + len(sentence) > CHUNK_CHARS:
                    add(piece, page)
                    piece = ""
                piece = (piece + " " + sentence).strip()
            if piece:
                add(piece, page)
    flush()
    if len(chunks) > MAX_CHUNKS:
        raise StudyError("That document is too large to add in one piece. Add it a few chapters at a time.")
    return chunks


# ---- courses and materials --------------------------------------------------------------------------------------
def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()).strip("-")[:48]


def _dir(kind: str):
    return storage.user_dir() / "study" / kind


def _files(kind: str):
    folder = _dir(kind)
    return sorted(p for p in folder.glob("*.json") if _CONFLICT not in p.name and ".corrupt" not in p.name) if folder.is_dir() else []


def _seed_course() -> dict:
    return {"id": study_seed.ID, "title": study_seed.TITLE, "builtin": True, "exam_date": None, "created": ""}


def courses() -> list[dict]:
    """Every course of this person, the built-in one last, each with its materials (no passages) and chapter count."""
    own = [read_json(p) for p in _files("courses")]
    own = sorted((c for c in own if isinstance(c, dict) and c.get("id")), key=lambda c: c.get("created", ""))
    mats = [m for m in (_material_meta(p) for p in _files("materials")) if m]
    out = []
    for c in own + [_seed_course()]:
        mine = [m for m in mats if m["course"] == c["id"]]
        if c.get("builtin"):
            mine = [{"id": "seed", "course": c["id"], "title": "Concept outline", "kind": "built-in", "pages": None,
                     "chunks": sum(len(x) for _, x in study_seed.OUTLINE), "added": ""}]
        out.append(dict(c, materials=mine, chapters=len(chapters(c["id"]))))
    return out


def find_course(name: str = "") -> dict | None:
    """By id, title or a few of its words ("networking" finds "Intro to Networking"). No name: his only own course,
    else the built-in one."""
    every = courses()
    want = slug(name)
    if not want:
        own = [c for c in every if not c.get("builtin")]
        return own[-1] if own else every[-1]
    for c in every:
        if want in (c["id"], slug(c["title"])):
            return c
    words = set(want.split("-")) - {"course", "class", "my", "the"}
    best = max(every, key=lambda c: len(words & set(slug(c["title"]).split("-"))))
    return best if words & set(slug(best["title"]).split("-")) else None


def add_course(title: str, exam_date: str | None = None) -> dict:
    title = " ".join((title or "").split())[:80]
    if not slug(title):
        raise StudyError("The course needs a name.")
    cid = slug(title)
    if cid == study_seed.ID:
        return _seed_course()
    course = read_json(_dir("courses") / f"{cid}.json") or {"id": cid, "title": title,
                                                                 "created": datetime.now().isoformat(timespec="seconds")}
    course = dict(course)                                   # never the cached object
    if exam_date is not None:
        course["exam_date"] = exam_date or None
    course.setdefault("exam_date", None)
    storage.save(f"study/courses/{cid}.json", course)
    return course


def set_exam_date(course_id: str, exam_date: str | None) -> None:
    course = read_json(_dir("courses") / f"{course_id}.json")
    if isinstance(course, dict) and course.get("id"):
        course = dict(course)
        course["exam_date"] = exam_date or None
        storage.save(f"study/courses/{course_id}.json", course)


def _material_meta(path) -> dict | None:
    doc = _load_material(path)
    return {k: doc[k] for k in ("id", "course", "title", "kind", "pages", "added")} | {"chunks": len(doc["chunks"])} if doc else None


_cache: dict = {}


def read_json(path):
    """A study file as the other machine may have left it mid-sync: parsed and remembered until the file changes
    (path, size and mtime), or None when it is missing or not whole yet. Never renamed, never "repaired": the next
    read simply tries again."""
    try:
        stat = path.stat()
    except OSError:
        return None
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    if key not in _cache:
        import json
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if len(_cache) > 256:
            _cache.clear()
        _cache[key] = value
    return _cache[key]


def _load_material(path) -> dict | None:
    doc = read_json(path)
    return doc if isinstance(doc, dict) and isinstance(doc.get("chunks"), list) and doc.get("id") else None


def add_material(course: str, title: str, data: bytes | str, filename: str = "") -> dict:
    """Parse, split and store one document in a course (created when new). -> its summary. Adding the same text to the
    same course again changes nothing."""
    c = find_course(course) if slug(course) in {x["id"] for x in courses()} else None
    c = c or add_course(course or "My notes")
    if c.get("builtin"):
        raise StudyError("The built-in course can't take material. Name a course of your own and I'll create it.")
    raw = data.encode("utf-8") if isinstance(data, str) else data
    pages = pages_of(raw, filename)
    chunks = chunk(pages)
    if not chunks:
        raise StudyError("I found no readable text in that.")
    digest = hashlib.sha256(c["id"].encode() + b"\0" + "\n".join(t for _, t in pages).encode("utf-8")).hexdigest()[:12]
    title = " ".join((title or filename or "Notes").split())[:100]
    mid = f"{slug(title)[:28] or 'notes'}-{digest}"
    for n, ch in enumerate(chunks):
        ch["id"] = f"{mid}:{n}"
    kind = "pdf" if pages[0][0] is not None and (filename.lower().endswith(".pdf") or raw[:5] == b"%PDF-") else "text"
    existing = _load_material(_dir("materials") / f"{mid}.json")
    doc = {"id": mid, "course": c["id"], "title": title, "kind": kind,
           "pages": len(pages) if pages[0][0] is not None else None,
           "added": (existing or {}).get("added") or datetime.now().isoformat(timespec="seconds"), "chunks": chunks}
    storage.save(f"study/materials/{mid}.json", doc)
    names = []
    for ch in chunks:
        if ch["chapter"] and ch["chapter"] not in names:
            names.append(ch["chapter"])
    return {"id": mid, "course": c["id"], "course_title": c["title"], "title": title, "kind": kind, "pages": doc["pages"],
            "chunks": len(chunks), "chapters": names, "duplicate": bool(existing)}


def remove_material(material_id: str) -> bool:
    if not re.fullmatch(r"[a-z0-9-]{1,64}", material_id or ""):
        return False
    path = _dir("materials") / f"{material_id}.json"
    if path.is_file():
        path.unlink()
        return True
    return False


def passages(course_id: str) -> list[dict]:
    """Every passage of a course, each carrying its material's title. Seed concepts carry their term as `concept`."""
    if course_id == study_seed.ID:
        return [{"id": f"seed:{slug(chapter)}:{slug(term)}", "chapter": chapter, "section": term, "page": None,
                 "page_end": None, "text": text, "concept": term, "material": "Concept outline"}
                for chapter, items in study_seed.OUTLINE for term, text in items]
    out = []
    for p in _files("materials"):
        doc = _load_material(p)
        if doc and doc["course"] == course_id:
            out += [dict(ch, material=doc["title"], chapter=ch.get("chapter") or doc["title"]) for ch in doc["chunks"]]
    return out


def chapters(course_id: str) -> list[dict]:
    """In reading order: [{chapter, passages, page, page_end}]."""
    out = {}
    for ch in passages(course_id):
        row = out.setdefault(ch["chapter"], {"chapter": ch["chapter"], "passages": 0, "page": ch["page"], "page_end": ch["page_end"]})
        row["passages"] += 1
        row["page_end"] = ch["page_end"] or row["page_end"]
    return list(out.values())


_NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
                 "ten": 10, "eleven": 11, "twelve": 12}


def find_chapter(course_id: str, wanted: str) -> str | None:
    """"3", "chapter three", "networking", "Chapter 3: Switching" -> the chapter's exact name, or None."""
    names = [c["chapter"] for c in chapters(course_id)]
    text = (wanted or "").strip().lower()
    if not text or not names:
        return None
    m = re.search(r"\b(\d{1,2})\b", text)
    number = int(m.group(1)) if m else next((n for w, n in _NUMBER_WORDS.items() if re.search(rf"\b{w}\b", text)), None)
    if number is not None:
        for name in names:
            if re.match(rf"\s*(?:chapter|module|unit|lecture|week|lesson|part)?\s*0*{number}\b", name, re.I):
                return name
        numbered = [n for n in names if re.match(r"\s*(?:chapter|module|unit|lecture|week|lesson|part)\s", n, re.I)]
        if not numbered and number <= len(names):
            return names[number - 1]                            # no numbered headings: the third chapter in order
        return None
    words = set(tokens(re.sub(r"\b(chapter|module|unit|section|the|on|about)\b", " ", text)))
    best = max(names, key=lambda n: len(words & set(tokens(n))))
    return best if words & set(tokens(best)) else None


# ---- lexical search (BM25) --------------------------------------------------------------------------------------
_STOP = set("a an and are as at be by for from has have how in is it its of on or that the this to was what when where "
            "which who why will with does do can you your i me my about into than then them they their there".split())


def _stem(word: str) -> str:
    for suffix, keep in (("ies", 3), ("ing", 4), ("ers", 4), ("es", 4), ("ed", 4), ("s", 3)):
        if word.endswith(suffix) and len(word) - len(suffix) >= keep and not word.endswith("ss"):
            return word[:-len(suffix)] + ("y" if suffix == "ies" else "")
    return word


def tokens(text: str) -> list[str]:
    return [_stem(w) for w in re.findall(r"[a-z0-9][a-z0-9+#./-]*[a-z0-9+#]|[a-z0-9]", (text or "").lower()) if w not in _STOP]


def rank(query: str, docs: list[dict], k: int = 5, k1: float = 1.5, b: float = 0.75) -> list[dict]:
    """Okapi BM25 over the passages given; a query word in the heading counts as two in the text. -> the best k with
    a `score`, best first; passages that share no word with the query are left out."""
    q = set(tokens(query))
    if not q or not docs:
        return []
    bags = []
    for d in docs:
        bag = {}
        for t in tokens(d["text"]) + 2 * tokens(f"{d.get('chapter', '')} {d.get('section', '')}"):
            bag[t] = bag.get(t, 0) + 1
        bags.append(bag)
    avg = sum(sum(bag.values()) for bag in bags) / len(bags) or 1
    scored = []
    for d, bag in zip(docs, bags):
        length = sum(bag.values())
        score = 0.0
        for term in q:
            tf = bag.get(term, 0)
            if tf:
                df = sum(1 for other in bags if term in other)
                idf = math.log(1 + (len(bags) - df + 0.5) / (df + 0.5))
                score += idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * length / avg))
        if score > 0:
            scored.append(dict(d, score=round(score, 4)))
    return sorted(scored, key=lambda d: -d["score"])[:k]


def search(query: str, course_id: str | None = None, chapter: str | None = None, k: int = 5) -> list[dict]:
    """The passages that best match, across one course or all of them, each with `course` and a citation."""
    hits = []
    for c in courses():
        if course_id and c["id"] != course_id:
            continue
        docs = [dict(p, course=c["id"], course_title=c["title"]) for p in passages(c["id"])
                if not chapter or p["chapter"] == chapter]
        hits += rank(query, docs, k)
    return sorted(hits, key=lambda d: -d["score"])[:k]


def cite(passage: dict) -> str:
    """How a passage is named out loud and on screen: "Networking Basics, Chapter 3: Switching, page 41"."""
    parts = [passage.get("material") or ""]
    if passage.get("chapter") and passage["chapter"] != passage.get("material"):
        parts.append(passage["chapter"])
    if passage.get("section") and passage.get("page") is None:
        parts.append(passage["section"])
    if passage.get("page") is not None:
        parts.append(f"page {passage['page']}")
    return ", ".join(p for p in parts if p)
