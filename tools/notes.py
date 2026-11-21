"""Notes, to-do and shopping lists. To-dos, ideas and notes are personal
(data/users/<id>/notes.json); the shopping list is shared by the household."""
import uuid
import re
from datetime import date

from core import storage
from core.words import plural
from tools.registry import tool

FILE = "notes.json"
SHARED_FILE = "household/notes.json"
LISTS = ["todo", "shopping", "ideas", "notes"]
SHARED_LISTS = {"shopping"}


def _file(list_name: str) -> str:
    return SHARED_FILE if list_name in SHARED_LISTS else FILE


def _all_notes() -> list[dict]:
    return storage.load(FILE, []) + storage.load(SHARED_FILE, [])


@tool(
    "Add an item to a list. To-do, ideas and notes are personal; the shopping list is shared by the household.",
    {"text": {"type": "string"}, "list_name": {"type": "string", "enum": LISTS}},
)
def add_note(text: str, list_name: str) -> str:
    from core import users

    notes = storage.load(_file(list_name), [])
    notes.append({"id": uuid.uuid4().hex[:5], "list": list_name, "text": text.strip(), "done": False,
                  "added": date.today().isoformat(), "by": users.get(users.current()).get("name") or users.current()})
    storage.save(_file(list_name), notes)
    open_count = sum(1 for n in notes if n["list"] == list_name and not n["done"])
    return f"Added to {list_name} ({open_count} open item{'s' if open_count != 1 else ''})."


@tool(
    "Read one of the lists.",
    {"list_name": {"type": "string", "enum": LISTS + ["all"]},
     "include_done": {"type": "boolean", "description": "Also show completed items"}},
)
def read_list(list_name: str, include_done: bool) -> str:
    notes = [n for n in _all_notes() if (list_name == "all" or n["list"] == list_name)
             and (include_done or not n["done"])]
    if not notes:
        return f"The {list_name} list is empty."
    return "\n".join(f"[{n['id']}] ({n['list']}){' [done]' if n['done'] else ''} {n['text']}" for n in notes)


@tool(
    "Search the person's saved notes, to-dos, ideas, and long-term remembered facts. Read-only; use when they ask "
    "to search their notes for a topic. It never searches the internet.",
    {"query": {"type": "string", "description": "Topic or keywords from the person's request"}},
    direct=True,
)
def search_notes(query: str) -> str:
    """One reliable local lookup across the two text stores people call "notes".

    Long-term facts and list entries are intentionally shown separately so a
    remembered fact is never presented as a current to-do (or vice versa).
    """
    words = {w for w in re.findall(r"[a-z0-9]+", query.casefold()) if len(w) > 1}
    if not words:
        return "Tell me what to look for in your notes."

    def score(value: str) -> int:
        text = value.casefold()
        return sum(word in text for word in words)

    entries = [n for n in _all_notes() if n.get("list") in {"notes", "todo", "ideas"}]
    matches = sorted(((score(str(n.get("text", ""))), n) for n in entries), key=lambda item: -item[0])
    matches = [n for found, n in matches if found]

    # Facts are also notes from the owner's point of view, but keeping their
    # origin labelled prevents a stale fact being mistaken for a scheduled item.
    from tools import memory
    facts = sorted(((score(str(m.get("fact", ""))), m) for m in memory._all()), key=lambda item: -item[0])
    facts = [m for found, m in facts if found]
    if not matches and not facts:
        return f"Nothing in your saved notes or remembered facts matches '{query}'. I did not search the internet."
    lines = []
    if matches:
        lines.append("Saved notes:")
        lines.extend(f"[{n['id']}] ({n['list']}){' [done]' if n.get('done') else ''} {n['text']}" for n in matches[:8])
    if facts:
        lines.append("Remembered facts:")
        lines.extend(f"[{m['id']}] ({m['category']}{', household' if m['id'].startswith('h') else ''}) {m['fact']}"
                     for m in facts[:8])
    return "\n".join(lines)


@tool(
    "Mark list items as done, or delete them. Use read_list first to get ids.",
    {"item_ids": {"type": "array", "items": {"type": "string"}},
     "action": {"type": "string", "enum": ["done", "delete"]}},
)
def update_list(item_ids: list, action: str) -> str:
    hit = 0
    for file in (FILE, SHARED_FILE):
        notes = storage.load(file, [])
        for n in list(notes):
            if n["id"] in item_ids:
                hit += 1
                if action == "delete":
                    notes.remove(n)
                else:
                    n["done"] = True
        storage.save(file, notes)
    return f"{plural(hit, 'item')} {'deleted' if action == 'delete' else 'marked done'}."
