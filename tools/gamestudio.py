"""Voice front of the studio manager for Living Shadow World (core/studio.py): his note about the game becomes a work
order he approves on his phone; "what's the status of the boss work?" answers from the real orders.

Both machines: a note is queued as a mission (the laptop keeps the queue; the PC drafts the order because the game's repo
is there). The orders live in data/studio/ (synced), so the status answer works on the phone too."""
from __future__ import annotations

from core import studio
from tools.registry import tool

NOT_OWNER = "The game studio belongs to the owner, so only he can give it notes or ask about its work."


def _owner() -> bool:
    from core import authz
    return authz.is_owner()


@tool("Turn the owner's NOTE about his game Living Shadow World (Shadow World, 'the game') into a work order for the coding "
      "team: 'note for the game: the boss feels too slow in phase 2', 'tell the studio the jump feels floaty', 'make a work "
      "order: the title menu loses focus'. Nothing runs until he approves the order on his phone.",
      {"note": {"type": "string", "description": "his note, in his own words"}}, required=["note"], direct=True)
def studio_note(note: str) -> str:
    if not _owner():
        return NOT_OWNER
    note = " ".join(str(note or "").split())
    if len(note) < 8:
        return "What's the note, sir? Tell me what feels wrong in the game."
    from tools import missions as mtools
    goal = f"{studio.PREFIX} {note}"[:500]
    try:
        if mtools._remote():
            mtools._call("POST", "/api/missions", {"goal": goal, "for_uid": mtools._me()})
        else:
            from core import missions
            missions.add(goal)
    except ValueError as e:
        return str(e)
    except Exception as e:  # noqa: BLE001
        return f"I couldn't reach the laptop that keeps the work queue, so the note wasn't taken ({type(e).__name__})."
    return (f"Noted: \"{note}\". I'll write it up as a work order on the PC: the team lane, the files, what done means and "
            "the tests. It comes to your phone for your OK, and nothing is built until you approve it.")


@tool("Status of the game studio's work on Living Shadow World: work orders, which worker has them, build and test "
      "results, and how to play the result. Use for 'what's the status of the boss work', 'is the jump fix done', "
      "'how is the studio doing', 'what did the coders do on my game'.",
      {"about": {"type": "string", "description": "what work he means (e.g. 'boss'), or empty for everything active"}},
      required=[], direct=True)
def studio_status(about: str = "") -> str:
    if not _owner():
        return NOT_OWNER
    if (about or "").strip().casefold() in ("learning", "lessons", "what the studio learned"):
        from core import studiolearn
        return studiolearn.spoken_metrics()
    said = studio.status(about or "")
    if "credit" in (about or "").casefold():
        c = studio.credit_state()
        said += (f" The studio has spent ${c['spent']:.2f} of the API credit (cap ${c['cap']:.0f}); "
                 f"${c['left']:.2f} is still usable until {c['expires']}.")
    return said
