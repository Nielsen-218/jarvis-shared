"""Engineering workbench: a unit-aware calculator and a growing engineering library.

The library (shared by the household) lives in data/household/engineering/:
  docs/<id>.json   extracted text of each document, split into searchable chunks
  index.json       titles, sources, tags
  inbox/           drop any PDF here (datasheets, manuals, books); it's read on the next search
Jarvis adds to it whenever it looks something up (add_to_library), and can save
its own notes (library_note), so knowledge it gathers is kept and reusable offline.
"""
import html
import io
import json
import math
import re
import sqlite3
import uuid
from contextlib import closing
from collections import Counter
from datetime import date

from core import storage
from tools.registry import tool

BASE = "household/engineering"
MAX_DOWNLOAD = 80 * 1024 * 1024
CHUNK = 1500


# -- calculator --------------------------------------------------------------------------
_ureg = None


def _units():
    global _ureg
    if _ureg is None:
        import pint

        _ureg = pint.UnitRegistry(autoconvert_offset_to_baseunit=True)
    return _ureg


_FUNCS = {"sqrt": math.sqrt, "log": math.log, "log10": math.log10, "log2": math.log2, "exp": math.exp,
          "sin": math.sin, "cos": math.cos, "tan": math.tan, "asin": math.asin, "acos": math.acos,
          "atan": math.atan, "atan2": math.atan2, "degrees": math.degrees, "radians": math.radians,
          "abs": abs, "min": min, "max": max, "round": round, "ceil": math.ceil, "floor": math.floor}


def _plain_math(expr: str) -> float:
    """Evaluate unitless math with functions, using a whitelist (no Python eval of names)."""
    import ast

    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp):
            ops = {ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b, ast.Mult: lambda a, b: a * b,
                   ast.Div: lambda a, b: a / b, ast.Pow: lambda a, b: a ** b, ast.Mod: lambda a, b: a % b}
            return ops[type(node.op)](ev(node.left), ev(node.right))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            return -ev(node.operand) if isinstance(node.op, ast.USub) else ev(node.operand)
        if isinstance(node, ast.Name) and node.id in ("pi", "e"):
            return math.pi if node.id == "pi" else math.e
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCS:
            return _FUNCS[node.func.id](*[ev(a) for a in node.args])
        raise ValueError("unsupported expression")

    return ev(ast.parse(expr, mode="eval"))


# pint reads "12 V / 470 ohm" left to right as (12 V / 470) * ohm, so the tool's own example came back as "those units
# are incompatible (it measures [mass] ** 2 * [length] ** 4 ...)". Each "number unit" is one quantity: group it.
_QUANTITY = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)\s+([A-Za-zµΩ°][\w°µ]*(?:\s*\*\*\s*-?\d+)?)(?![\w(])")


def _grouped(expr: str) -> str:
    return _QUANTITY.sub(r"(\1 \2)", expr)


@tool(
    "Exact engineering calculation with units (never estimate numbers in your head). Units work "
    "naturally: '(5 V - 2.1 V) / (20 mA)', '12 V * 2.5 A * 3 hour', '0.5 * 470 uF * (12 V)**2', "
    "'2200 mAh / (150 mA)'. For unitless math with functions use e.g. 'sqrt(3) * 230 * 16'.",
    {
        "expression": {"type": "string"},
        "convert_to": {"type": "string", "description": "Unit for the answer, e.g. 'ohm', 'W', 'hour', 'mm**2'; empty for automatic"},
    },
)
def engineering_calc(expression: str, convert_to: str) -> str:
    expr = expression.strip()
    if not expr:
        return "Give me something to calculate, e.g. '12 V / 470 ohm' or 'sqrt(3) * 230 V'."
    ureg = _units()
    try:
        q = ureg.parse_expression(_grouped(expr))  # pint's own parser (no Python eval)
    except Exception:
        try:
            return f"{expression} = {_plain_math(expr):.6g}"  # functions like sqrt(), no units
        except ZeroDivisionError:
            return f"{expression} divides by zero, so it has no finite value."
        except (ValueError, KeyError, TypeError, SyntaxError, OverflowError):
            return (f"I couldn't evaluate '{expression}'. Use numbers with units, + - * / ** and functions like "
                    "sqrt(), sin(), log() — e.g. '12 V / 470 ohm', '2 * pi * 50 Hz' or 'sqrt(3) * 230 V'.")
    if not hasattr(q, "units"):
        return f"{expression} = {q:.6g}"
    target = convert_to.strip()
    if q.dimensionless and target.lower().strip("%s ") in ("", "percent", "percentage", "pct"):
        # "What's 15 percent of 80?" came as engineering_calc('0.15 * 80', convert_to='percent') and was read out
        # as "1200 percent": a plain number asked for "in percent" is the model echoing the question, not a unit.
        return f"{expression} = {q.to('dimensionless').magnitude:.6g}"
    if target:
        try:
            q = q.to(target)
        except Exception as e:  # pint DimensionalityError (incompatible) / UndefinedUnitError (unknown unit)
            why = "that unit isn't recognised" if "Undefined" in type(e).__name__ else "those units are incompatible"
            return f"I can't express {expression} in '{target}': {why} (it measures {q.dimensionality})."
    else:
        # Simplify to a familiar unit when one fits (V/ohm -> A, A*V -> W, ...), then pick a prefix.
        for unit in ("A", "V", "ohm", "W", "J", "F", "H", "Hz", "C", "N", "Pa", "m", "s", "kg"):
            if q.dimensionality == ureg(unit).dimensionality:
                q = q.to(unit)
                break
        q = q.to_compact()
    return f"{expression} = {q:.6g}"  # full unit names ("ohm", "milliampere") read well aloud


# -- library ---------------------------------------------------------------------------------
def _index() -> list[dict]:
    return storage.load(f"{BASE}/index.json", [])


def visible_index() -> list[dict]:
    from core import users
    owner = users.get(users.current()).get("role") == "owner"
    return [row for row in _index() if owner or row.get("access", "household") == "household"]


def _save_doc(title: str, text: str, source: str, tags: str, pages: list[str] | None = None, *, owner_only=False, doc_id=None) -> dict:
    # Mentor CLI processes and the idle study worker share this existing index.
    # Atomic file replacement alone does not protect its read/append/write cycle.
    with closing(sqlite3.connect(storage.path(f"{BASE}/write-lock.sqlite3"), timeout=20)) as lock:
        lock.execute("CREATE TABLE IF NOT EXISTS mutex (id INTEGER PRIMARY KEY)")
        lock.commit()
        lock.execute("BEGIN IMMEDIATE")
        try:
            result = _save_doc_unlocked(title, text, source, tags, pages, owner_only=owner_only,doc_id=doc_id)
            lock.commit()
            return result
        except Exception:
            lock.rollback()
            raise


def _save_doc_unlocked(title: str, text: str, source: str, tags: str, pages: list[str] | None = None, *, owner_only=False, doc_id=None) -> dict:
    doc_id = doc_id or uuid.uuid4().hex[:8]
    if not re.fullmatch(r'[a-f0-9]{8,32}',doc_id): raise ValueError('Invalid internal document ID')
    chunks = []
    if pages:  # keep page numbers so answers can cite them
        for n, page in enumerate(pages, 1):
            for i in range(0, len(page), CHUNK):
                chunks.append({"page": n, "text": page[i:i + CHUNK]})
    else:
        for i in range(0, len(text), CHUNK):
            chunks.append({"page": None, "text": text[i:i + CHUNK]})
    storage.save(f"{BASE}/docs/{doc_id}.json", {"id": doc_id, "chunks": chunks})
    entry = {"id": doc_id, "title": title, "source": source, "tags": tags, "added": date.today().isoformat(),
             "chunks": len(chunks), "chars": sum(len(c["text"]) for c in chunks)}
    if owner_only:
        entry["access"] = "owner"
    index = [row for row in _index() if row['id']!=doc_id]
    index.append(entry)
    storage.save(f"{BASE}/index.json", index)
    return entry


def _pdf_pages(data: bytes) -> list[str]:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    return [re.sub(r"[ \t]+", " ", (p.extract_text() or "")).strip() for p in reader.pages]


def _html_text(page: str) -> str:
    page = re.sub(r"(?is)<(script|style|nav|footer|header)[^>]*>.*?</\1>", " ", page)
    page = re.sub(r"(?i)<br\s*/?>|</(p|div|li|h\d|tr)>", "\n", page)
    text = html.unescape(re.sub(r"<[^>]+>", " ", page))
    return re.sub(r"\n\s*\n+", "\n\n", re.sub(r"[ \t]+", " ", text)).strip()


def _download(url: str) -> tuple[bytes, str]:
    from tools import studio

    old = studio.MAX_DOWNLOAD
    studio.MAX_DOWNLOAD = MAX_DOWNLOAD
    try:
        return studio._get(url)  # http(s) only, no local network, size-capped
    finally:
        studio.MAX_DOWNLOAD = old


@tool(
    "Save a web page or PDF (datasheet, pinout, manual, guide) into the engineering library so it's "
    "kept for good and searchable later, even offline. Do this whenever you look up specific "
    "technical reference material.",
    {"url": {"type": "string"}, "title": {"type": "string"},
     "tags": {"type": "string", "description": "Comma-separated, e.g. 'esp32, datasheet, wifi'"}},
    slow=True,
)
def add_to_library(url: str, title: str, tags: str) -> str:
    if any(d["source"] == url for d in visible_index()):
        return f"'{title}' is already in the library."
    data, ctype = _download(url)
    if ctype == "application/pdf" or data[:4] == b"%PDF":
        pages = _pdf_pages(data)
        if not "".join(pages).strip():
            return "That PDF has no extractable text (probably scanned images)."
        e = _save_doc(title, "", url, tags, pages)
        return f"Saved '{title}' ({len(pages)} pages) to the engineering library."
    text = _html_text(data.decode("utf-8", errors="ignore"))
    if len(text) < 200:
        return "That page had almost no readable text."
    e = _save_doc(title, text, url, tags)
    return f"Saved '{title}' ({e['chars'] // 1000}k characters) to the engineering library."


@tool(
    "Save your own engineering note to the library (a worked design, a pinout summary, a wiring "
    "plan, a lesson learned), so it can be found again later.",
    {"title": {"type": "string"}, "content": {"type": "string"}, "tags": {"type": "string"}},
)
def library_note(title: str, content: str, tags: str) -> str:
    _save_doc(title, content, "note", tags)
    return f"Noted '{title}' in the engineering library."


def _ingest_inbox() -> list[str]:
    inbox = storage.path(f"{BASE}/inbox/.keep").parent
    added = []
    for pdf in inbox.glob("*.pdf"):
        try:
            pages = _pdf_pages(pdf.read_bytes())
            _save_doc(pdf.stem, "", f"inbox:{pdf.name}", "inbox", pages)
            pdf.rename(pdf.with_suffix(".pdf.done"))
            added.append(pdf.stem)
        except Exception as e:
            print(f"[library] couldn't read {pdf.name}: {e}")
    return added


_WORD = re.compile(r"[a-z0-9]+(?:[.-][a-z0-9]+)*")


@tool(
    "Search the engineering library (datasheets, manuals, books, saved notes) for exact facts: "
    "pinouts, ratings, formulas, register names, wiring. Returns the best passages with sources "
    "and page numbers. Prefer this over memory for specific numbers.",
    {"query": {"type": "string"}, "max_results": {"type": "integer"}},
)
def library_search(query: str, max_results: int) -> str:
    new = _ingest_inbox()
    terms = [t for t in _WORD.findall(query.lower()) if len(t) > 1]
    if not terms:
        return "Give some keywords to search for."
    index = {d["id"]: d for d in visible_index()}
    if not index:
        return "The engineering library is empty so far."
    hits = []
    df = Counter()
    docs = {}
    for doc_id in index:
        doc = storage.load(f"{BASE}/docs/{doc_id}.json", {"chunks": []})
        docs[doc_id] = doc
        for ch in doc["chunks"]:
            words = set(_WORD.findall(ch["text"].lower()))
            for t in set(terms):
                if t in words:
                    df[t] += 1
    total = sum(len(d["chunks"]) for d in docs.values()) or 1
    for doc_id, doc in docs.items():
        title_words = set(_WORD.findall(index[doc_id]["title"].lower() + " " + index[doc_id]["tags"].lower()))
        for ch in doc["chunks"]:
            low = ch["text"].lower()
            counts = Counter(_WORD.findall(low))
            score = sum((1 + math.log(counts[t])) * math.log(1 + total / (1 + df[t])) for t in terms if counts[t])
            score += sum(2.0 for t in terms if t in title_words)
            if " ".join(terms) in low:
                score *= 1.5
            if score > 0:
                hits.append((score, doc_id, ch))
    hits.sort(key=lambda h: -h[0])
    out = [f"(New from inbox: {', '.join(new)})"] if new else []
    for score, doc_id, ch in hits[:max(1, min(max_results, 8))]:
        d = index[doc_id]
        where = f", page {ch['page']}" if ch["page"] else ""
        out.append(f"### {d['title']}{where} [{d['source']}]\n{ch['text'].strip()[:1200]}")
    return "\n\n".join(out) if hits else f"Nothing in the library about '{query}'."


@tool("List what's in the engineering library.")
def library_list() -> str:
    _ingest_inbox()
    index = visible_index()
    if not index:
        return "The engineering library is empty."
    return "\n".join(f"{d['title']} ({d['tags']}) - {d['chunks']} sections, added {d['added']}" for d in index)


# Free, freely licensed or freely distributed references to start the library with.
STARTER = [
    ("Lessons in Electric Circuits Vol I - DC", "https://www.ibiblio.org/kuphaldt/electricCircuits/DC/DC.pdf", "textbook, dc, ohms law, circuits"),
    ("Lessons in Electric Circuits Vol II - AC", "https://www.ibiblio.org/kuphaldt/electricCircuits/AC/AC.pdf", "textbook, ac, reactance, transformers"),
    ("Lessons in Electric Circuits Vol III - Semiconductors", "https://www.ibiblio.org/kuphaldt/electricCircuits/Semi/SEMI.pdf", "textbook, diodes, transistors, op-amps"),
    ("Lessons in Electric Circuits Vol IV - Digital", "https://www.ibiblio.org/kuphaldt/electricCircuits/Digital/DIGI.pdf", "textbook, logic, digital"),
    ("Lessons in Electric Circuits Vol VI - Experiments", "https://www.ibiblio.org/kuphaldt/electricCircuits/Exper/EXP.pdf", "textbook, projects, experiments"),
    ("Raspberry Pi 4 Model B datasheet", "https://datasheets.raspberrypi.com/rpi4/raspberry-pi-4-datasheet.pdf", "raspberry pi, pi 4, gpio, power, composite"),
    ("Raspberry Pi Pico datasheet", "https://datasheets.raspberrypi.com/pico/pico-datasheet.pdf", "raspberry pi pico, rp2040, microcontroller"),
    ("ESP32 datasheet", "https://www.espressif.com/sites/default/files/documentation/esp32_datasheet_en.pdf", "esp32, wifi, bluetooth, microcontroller"),
    ("Arduino Uno R3 datasheet", "https://docs.arduino.cc/resources/datasheets/A000066-datasheet.pdf", "arduino, uno, atmega328p, pinout"),
    ("NE555 timer datasheet", "https://www.ti.com/lit/ds/symlink/ne555.pdf", "555, timer, oscillator"),
    ("LM358 op-amp datasheet", "https://www.ti.com/lit/ds/symlink/lm358.pdf", "op-amp, lm358, amplifier"),
    ("LM340 / LM7805 5V regulator datasheet", "https://www.ti.com/lit/ds/symlink/lm340.pdf", "7805, regulator, 5v, power"),
    ("L298 dual H-bridge motor driver datasheet", "https://www.st.com/resource/en/datasheet/l298.pdf", "l298n, motor driver, h-bridge"),
    ("Raspberry Pi config.txt (video, composite, GPIO)", "https://www.raspberrypi.com/documentation/computers/config_txt.html", "raspberry pi, config.txt, composite, video"),
]


def seed_library() -> list[str]:
    results = []
    for title, url, tags in STARTER:
        try:
            results.append(add_to_library(url, title, tags))
        except Exception as e:
            results.append(f"Skipped '{title}': {e}")
    return results
