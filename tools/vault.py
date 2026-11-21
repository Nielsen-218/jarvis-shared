"""Long-term memory with evidence: "Jarvis, remember this" recordings, photos and notes, searchable later.

The vault lives on the always-on laptop (core/vault.py). On the desk PC these tools reach it through the
laptop's phone API (JARVIS_SERVER_URL + JARVIS_SERVER_TOKEN), so "what did we say about the car?" works from
either machine.
"""
import json
import os
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta

from core import approvals, users
from tools.registry import tool

def _remote() -> tuple[str, str] | None:
    """On the desk PC (JARVIS_ROLE=desk) the vault lives on the laptop: reach it over the phone API. Read the
    env at call time so tests and a role change take effect without re-importing."""
    role = os.getenv("JARVIS_ROLE", "all")
    url, token = os.getenv("JARVIS_SERVER_URL", "").rstrip("/"), os.getenv("JARVIS_SERVER_TOKEN", "")
    return (url, token) if role == "desk" and url and token else None


def _call(method: str, path: str, body: dict | None = None, query: dict | None = None):
    url, token = _remote()
    req = urllib.request.Request(url + path + ("?" + urllib.parse.urlencode(query) if query else ""), method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        answer = json.loads(r.read())
    if path != "/api/notify":
        try:
            from core import notify
            notify.flush_outbox_soon()          # the laptop answered: send what the PC kept for it (cross-device audit D)
        except Exception:  # noqa: BLE001
            pass
    return answer


def _when(iso: str) -> str:
    d = datetime.fromisoformat(iso)
    day = d.date()
    label = ("today" if day == date.today() else "yesterday" if day == date.today() - timedelta(days=1)
             else d.strftime("%a %b %d"))
    return f"{label} at {d.strftime('%I:%M %p').lstrip('0')}"


def _snippet(text: str, query: str, width: int = 360) -> str:
    if not text:
        return ""
    low = text.lower()
    hits = [low.find(w) for w in query.lower().split() if len(w) > 3 and low.find(w) >= 0]
    start = max(0, min(hits) - width // 3) if hits else 0
    return ("..." if start else "") + text[start:start + width].strip() + ("..." if start + width < len(text) else "")


# What a status means depends on the kind: a typed note is never "being transcribed" — its text is all there and
# searchable; only the search tags are pending. (kind, status) -> suffix; (None, status) is the default.
_STATUS = {
    ("note", "processing"): " (adding search tags — the text is already saved)",
    ("note", "error"): " (couldn't add search tags; it's still saved and searchable by its words)",
    ("photo", "processing"): " (still being described)",
    (None, "processing"): " (still being transcribed)",
    (None, "error"): " (processing failed — it retries after a restart)",
}


def _fmt(items: list[dict], query: str) -> str:
    if not items:
        return ("Nothing in the memory vault matches that. (Only things they recorded, photographed or asked me to "
                "save are in it.)")
    out = []
    for i in items:
        length = f", {int(i['seconds'] // 60)}:{int(i['seconds'] % 60):02d} long" if i.get("seconds") else ""
        line = f"[{i['id']}] {i['kind']} {_when(i['created'])}{length}: {i.get('title') or '(untitled)'}"
        line += _STATUS.get((i["kind"], i.get("status")), _STATUS.get((None, i.get("status")), ""))
        if i.get("summary"):
            line += f"\n  Summary: {i['summary']}"
        if i["kind"] == "note":
            line += f"\n  Note: {(i.get('transcript') or '')[:400]}"
        else:
            snip = _snippet(i.get("transcript") or "", query)
            if snip:
                line += f"\n  Words: \"{snip}\""
        out.append(line)
    return ("\n".join(out) + "\nOriginals: the Memory page in the Jarvis phone app (tap one to play or view it). "
            "Quote the date/time and what the evidence is; never invent details that aren't here.")


def _range(start_date: str, end_date: str) -> tuple[str, str]:
    s, e = (start_date or "").strip(), (end_date or "").strip()
    if len(e) == 10:  # a whole end day
        e = (date.fromisoformat(e) + timedelta(days=1)).isoformat()
    return s, e


@tool(
    "Search their long-term memory vault (recordings, videos, photos and notes they asked you to remember) and "
    "answer with evidence: date, time, what was said and where the original is. Use for 'what did we talk about "
    "yesterday about the car', 'when did I say...', 'find the receipt photo'. Put several keywords and synonyms in "
    "query; use dates for 'yesterday' / 'last week'. For anything said today or in the last 24 hours ('what were "
    "we just talking about?', 'what did I say at 8 this morning?') use recall_today instead.",
    {"query": {"type": "string", "description": "Keywords + synonyms, e.g. 'car vehicle brakes mechanic quote'"},
     "start_date": {"type": "string", "description": "YYYY-MM-DD, optional"},
     "end_date": {"type": "string", "description": "YYYY-MM-DD (inclusive), optional"},
     "kind": {"type": "string", "enum": ["audio", "video", "photo", "note", ""]}},
    required=[],
)
def recall_memories(query: str = "", start_date: str = "", end_date: str = "", kind: str = "") -> str:
    s, e = _range(start_date, end_date)
    if _remote():
        items = _call("GET", "/api/memory/search", query={"q": query, "start": s, "end": e, "kind": kind})["items"]
    else:
        from core import vault

        items = vault.search(users.current(), query, s, e, kind)
    return _fmt(items, query)


@tool(
    "Save something to their long-term memory vault as a note when they say 'remember this': a fact, where they "
    "parked, a promise, or a conversation you just had (write a faithful summary with the key details, names, "
    "numbers and decisions). Recordings and photos are made from the Memory page in the phone app.",
    {"text": {"type": "string"}, "title": {"type": "string", "description": "Optional short title"}},
    required=["text"],
)
def remember_note(text: str, title: str = "") -> str:
    if _remote():
        item = _call("POST", "/api/memory/note", {"text": text, "title": title})["item"]
    else:
        from core import vault

        item = vault.add(users.current(), "note", text=text.strip(), title=title, note=title,
                         source=os.getenv("JARVIS_ROLE", "all"))
    return f"Saved to the memory vault ({_when(item['created'])}). I can find it later by topic or date."


@tool("The full transcript / description of one vault item (id from recall_memories), to quote it exactly.",
      {"memory_id": {"type": "string"}})
def memory_details(memory_id: str) -> str:
    if _remote():
        item = _call("GET", f"/api/memory/item/{urllib.parse.quote(memory_id)}").get("item")
    else:
        from core import vault

        item = vault.get(memory_id, users.current())
    if not item:
        return "No vault item with that id."
    body = item.get("transcript") or "(no words in it)"
    return (f"{item['kind']} {_when(item['created'])}: {item.get('title')}\nNote: {item.get('note') or '-'}\n"
            f"Full text:\n{body[:8000]}" + (" [cut]" if len(body) > 8000 else ""))


@tool("Delete one item from their memory vault (the original file and its transcript). Only their own items.",
      {"memory_id": {"type": "string"}, "confirmed": {"type": "boolean"}}, required=["memory_id"])
def delete_memory(memory_id: str, confirmed: bool = False) -> str:
    uid = users.current()
    if not (approvals.direct(uid, "delete") or (confirmed and approvals.consume(uid, f"vault:{memory_id}"))):
        return approvals.request(uid, f"vault:{memory_id}", f"delete memory {memory_id} for good")
    if _remote():
        ok = _call("DELETE", f"/api/memory/item/{urllib.parse.quote(memory_id)}")["ok"]
    else:
        from core import vault

        ok = vault.delete(memory_id, uid)
    return "Deleted from the vault." if ok else "No vault item with that id."


@tool("Memory vault status: where it's stored (the 2 TB drive or the laptop's SSD), how many recordings, photos and "
      "notes, space used and free.", {}, required=[])
def vault_status() -> str:
    if _remote():
        s = _call("GET", "/api/memory/stats")
    else:
        from core import vault

        s = vault.stats()
    items = ", ".join(f"{n} {k}" for k, n in s["items"].items()) or "nothing yet"
    where = "the 2 TB drive" if s["on_drive"] else "the laptop's internal SSD (the 2 TB drive isn't mounted yet)"
    return f"Vault on {where}: {items}; {s['bytes'] / 1e9:.2f} GB used, {s['free_gb']} GB free."
