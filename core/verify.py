"""Did the skill do what it says it did? A post-condition check for the writes whose outcome can be read back.

A tool's return text is the model's only evidence; a storage write that silently failed (or a list the skill
wrote to under another name) would still be announced as done. Each verifier reads the stored state back and
returns None when it agrees with the result, or a short problem. The brain marks a disagreeing result as an
error with the problem attached, so the reply cannot claim success. Verifiers never raise and never write."""
from __future__ import annotations

from typing import Callable

_VERIFIERS: dict[str, Callable[[dict, str], str | None]] = {}


def verifier(name: str):
    def register(fn):
        _VERIFIERS[name] = fn
        return fn
    return register


def check(name: str, args: dict, output: str) -> str | None:
    """None = verified (or no verifier for this skill); otherwise what disagrees."""
    fn = _VERIFIERS.get(name)
    if fn is None or not isinstance(output, str):
        return None
    try:
        return fn(args, output)
    except Exception as error:                        # a broken verifier must not turn a good result into an error
        return f"verifier failed: {type(error).__name__}"


def verified(name: str) -> bool:
    return name in _VERIFIERS


@verifier("add_note")
def _add_note(args, output):
    from core import storage
    from tools import notes
    if not output.startswith("Added to"):
        return None                                   # the skill itself reported something else
    text, list_name = str(args.get("text", "")).strip().lower(), args.get("list_name", "")
    rows = storage.load(notes._file(list_name), [])
    if not any(n.get("list") == list_name and n.get("text", "").strip().lower() == text and not n.get("done") for n in rows):
        return f"'{text}' is not on the {list_name} list after the write"
    return None


@verifier("remember")
def _remember(args, output):
    from tools import memory
    if not output.startswith("Noted"):
        return None
    fact = str(args.get("fact", ""))
    if not any(memory.compare_facts(fact, m["fact"]) in ("same", "narrower") or m["fact"] == fact.strip()
               for m in memory._all()):
        return "the fact is not in memory after the write"
    return None


@verifier("learn_command")
def _learn_command(args, output):
    from core import commands
    if not output.startswith(("Learned", "Updated")):
        return None
    rule = commands.find(str(args.get("trigger", "")))
    if rule is None:
        return "the command is not on file after the write"
    if commands._clean(str(args.get("response", "")), commands.MAX_RESPONSE) != rule["response"]:
        return f"the command on file answers '{rule['response']}', not what was taught"
    return None


@verifier("set_reminder")
def _set_reminder(args, output):
    from tools import proactive
    if not output.endswith(".") or "set for" not in output:
        return None
    text = str(args.get("text", ""))
    if not any(r.get("text") == text for r in proactive._my_reminders()):
        return "the reminder is not in the list after the write"
    return None
