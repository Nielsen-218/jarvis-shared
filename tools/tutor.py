"""Tutor: language lessons with saved progress and spaced-repetition vocabulary,
plus visual lesson/explainer pages for teaching anything.

data/tutor.json: per language -> level, goals, streak, lesson history, vocabulary.
Vocabulary uses Leitner boxes: a word you get right moves up a box and comes back
later (1, 3, 7, 14, 30 days); a miss sends it back to box 1.
"""
import html
import json
from datetime import date, datetime, timedelta

from core import storage
from core.words import plural
from tools.registry import tool

FILE = "tutor.json"
INTERVALS = [1, 3, 7, 14, 30]  # days until the next review, per box


def _data() -> dict:
    return storage.load(FILE, {})


def _key(data: dict, language: str) -> str:
    """One record per language whatever he calls it ("Kreyol", "Haitian Creole" and "creole" used to be three). A
    record saved under an older spelling is kept and reused."""
    from tools import languages

    raw = language.strip().lower()
    code = languages.code_for(raw)
    if not code:
        return raw
    same = [name for name, c in languages.CODES.items() if c == code]
    return next((k for k in data if k == raw or k in same), same[0])


def _lang(data: dict, language: str) -> dict:
    return data.setdefault(_key(data, language), {
        "level": "unknown", "goals": "", "streak": 0, "last_lesson": None, "lessons": [], "words": []})


@tool(
    "Start or continue a language lesson: returns the learner's level, goals, streak, recent topics "
    "and how many words are due for review. Call it before teaching a language. Also switches speech "
    "recognition so the user can practice speaking that language.",
    {"language": {"type": "string", "description": "e.g. 'Spanish', 'French', 'English', 'Haitian Creole'"}},
)
def tutor_status(language: str) -> str:
    from tools import languages

    data = _data()
    info = _lang(data, language)
    storage.save(FILE, data)
    languages.set_foreign_language(languages.code_for(language))  # hear and speak it natively
    today = date.today().isoformat()
    due = [w for w in info["words"] if w["due"] <= today]
    recent = ", ".join(l["topic"] for l in info["lessons"][-5:]) or "none yet"
    return (f"{language}: level {info['level']}; goals: {info['goals'] or 'not set'}; "
            f"streak {plural(info['streak'], 'day')}; {plural(len(info['words']), 'word')} learned, {len(due)} due for review; "
            f"recent lessons: {recent}.")


@tool(
    "Save the learner's level and goals for a language (e.g. after a placement chat).",
    {"language": {"type": "string"},
     "level": {"type": "string", "description": "e.g. 'beginner (A1)', 'intermediate (B1)'"},
     "goals": {"type": "string", "description": "e.g. 'talk with family in Haiti', 'pass a French exam'"}},
)
def tutor_set_profile(language: str, level: str, goals: str) -> str:
    data = _data()
    info = _lang(data, language)
    info["level"], info["goals"] = level, goals
    storage.save(FILE, data)
    return f"Saved: {language} level {level}, goals: {goals}."


@tool(
    "Add new vocabulary the learner just studied, so it comes back for spaced review.",
    {"language": {"type": "string"},
     "words": {"type": "array", "items": {"type": "object", "properties": {
         "word": {"type": "string"}, "translation": {"type": "string"},
         "example": {"type": "string", "description": "Short example sentence"}},
         "required": ["word", "translation", "example"], "additionalProperties": False}}},
)
def tutor_add_words(language: str, words: list) -> str:
    data = _data()
    info = _lang(data, language)
    known = {w["word"].lower() for w in info["words"]}
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    added = 0
    for w in words:
        if w.get("word") and w["word"].lower() not in known:
            info["words"].append({"word": w["word"], "translation": w.get("translation", ""),
                                  "example": w.get("example", ""), "box": 0, "due": tomorrow})
            added += 1
    storage.save(FILE, data)
    return f"Added {plural(added, 'new word')} to {language} review. Total: {len(info['words'])}."


@tool(
    "Get vocabulary that is due for review (quiz the learner on these, one at a time).",
    {"language": {"type": "string"}, "count": {"type": "integer"}},
)
def tutor_review(language: str, count: int) -> str:
    info = _lang(_data(), language)
    today = date.today().isoformat()
    due = sorted((w for w in info["words"] if w["due"] <= today), key=lambda w: (w["box"], w["due"]))
    if not due:
        nxt = min((w["due"] for w in info["words"]), default=None)
        return "Nothing due for review." + (f" Next review: {nxt}." if nxt else " No words saved yet.")
    return json.dumps([{k: w[k] for k in ("word", "translation", "example")} for w in due[:count]],
                      ensure_ascii=False)


@tool(
    "Record whether the learner got a review word right, which schedules its next review.",
    {"language": {"type": "string"}, "word": {"type": "string"}, "correct": {"type": "boolean"}},
)
def tutor_grade(language: str, word: str, correct: bool) -> str:
    data = _data()
    info = _lang(data, language)
    for w in info["words"]:
        if w["word"].lower() == word.lower():
            w["box"] = min(w["box"] + 1, len(INTERVALS) - 1) if correct else 0
            w["due"] = (date.today() + timedelta(days=INTERVALS[w["box"]])).isoformat()
            storage.save(FILE, data)
            return f"'{word}': {'correct' if correct else 'missed'}; next review {w['due']}."
    return f"'{word}' isn't in the {language} word list."


@tool(
    "Log a finished lesson (updates the daily streak).",
    {"language": {"type": "string"}, "topic": {"type": "string"}},
)
def tutor_log_lesson(language: str, topic: str) -> str:
    data = _data()
    info = _lang(data, language)
    today = date.today()
    last = date.fromisoformat(info["last_lesson"]) if info["last_lesson"] else None
    if last != today:
        info["streak"] = info["streak"] + 1 if last == today - timedelta(days=1) else 1
    info["last_lesson"] = today.isoformat()
    info["lessons"].append({"date": today.isoformat(), "topic": topic})
    storage.save(FILE, data)
    from tools import languages

    if languages.interpreter is None:      # an interpreter session owns the same switches
        languages.set_foreign_language(None)  # lesson over: back to English/French/Kreyol only
    return f"Lesson logged. Streak: {plural(info['streak'], 'day')}."


@tool(
    "Open a visual lesson or explainer page in the browser: headings and explanations, vocabulary "
    "or key-term tables, optional diagrams (Mermaid syntax), and a self-check quiz with hidden answers. "
    "Use it when teaching a language lesson, or when explaining any topic where seeing it helps.",
    {
        "title": {"type": "string"},
        "subtitle": {"type": "string", "description": "One line: level, topic, or goal"},
        "sections": {"type": "array", "items": {"type": "object", "properties": {
            "heading": {"type": "string"},
            "body": {"type": "string", "description": "Explanation in plain language; blank lines separate paragraphs"},
            "terms": {"type": "array", "description": "Vocabulary / key terms (may be empty)", "items": {
                "type": "object", "properties": {"term": {"type": "string"}, "meaning": {"type": "string"},
                                                  "example": {"type": "string"}},
                "required": ["term", "meaning", "example"], "additionalProperties": False}},
            "diagram": {"type": "string", "description": "Mermaid diagram code, or empty"}},
            "required": ["heading", "body", "terms", "diagram"], "additionalProperties": False}},
        "quiz": {"type": "array", "items": {"type": "object", "properties": {
            "question": {"type": "string"}, "answer": {"type": "string"}},
            "required": ["question", "answer"], "additionalProperties": False}},
    },
)
def show_lesson_page(title: str, subtitle: str, sections: list, quiz: list) -> str:
    from tools.fashion import _open_page  # shared Jarvis page style

    parts = []
    has_diagram = False
    for sec in sections:
        parts.append(f"<h2>{html.escape(sec['heading'])}</h2>")
        for para in sec["body"].split("\n\n"):
            if para.strip():
                parts.append(f"<p class='lesson'>{html.escape(para.strip())}</p>")
        if sec.get("terms"):
            rows = "".join(f"<tr><td><b>{html.escape(t['term'])}</b></td><td>{html.escape(t['meaning'])}</td>"
                           f"<td class='muted'>{html.escape(t['example'])}</td></tr>" for t in sec["terms"])
            parts.append(f"<table><tr><th>Term</th><th>Meaning</th><th>Example</th></tr>{rows}</table>")
        if sec.get("diagram", "").strip():
            has_diagram = True
            parts.append(f"<div class='card'><pre class='mermaid'>{html.escape(sec['diagram'])}</pre></div>")
    if quiz:
        parts.append("<h2>Check yourself</h2>")
        for i, q in enumerate(quiz, 1):
            parts.append(f"<details class='card'><summary>{i}. {html.escape(q['question'])}</summary>"
                         f"<p>{html.escape(q['answer'])}</p></details>")
    style = ("<style>.lesson{font-size:16px;max-width:760px}table{border-collapse:collapse;width:100%;"
             "margin:8px 0}td,th{border-bottom:1px solid #1f2a36;padding:8px;text-align:left;vertical-align:top}"
             "th{color:var(--accent);font-weight:600}details{margin:8px 0;cursor:pointer}"
             "summary{font-weight:600}</style>")
    script = ("<script type='module'>import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@11.4.1/dist/"
              "mermaid.esm.min.mjs';mermaid.initialize({startOnLoad:true,theme:'dark'});</script>") if has_diagram else ""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    slug = "".join(c if c.isalnum() else "-" for c in title.lower()).strip("-")[:40] or "lesson"
    return _open_page(f"../lessons/{stamp}-{slug}.html", title, subtitle, style + "".join(parts) + script)
