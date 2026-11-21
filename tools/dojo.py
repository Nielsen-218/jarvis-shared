"""Voice front for JARVIS's practice mode (core/dojo.py): start, status, stop, and 'what have you learned'."""
import time

from core import dojo
from core.words import plural
from core.dojo_store import Store
from tools import rigging
from tools.registry import tool

_current: dict = {"p": None}


def _running():
    p = _current["p"]
    return p if p and p.thread and p.thread.is_alive() else None


@tool("Start practising Blender rigging on my own, on this PC's hardware, for a set time (default 60 minutes): I re-run the "
      "characters I fail on, work out why from measurements, research, try fixes, keep only what is proven not to break "
      "anything, and remember it. Free and local. Use for 'practice rigging', 'train yourself', 'get better at blender'.",
      {"minutes": {"type": "integer"}}, direct=True)
def start_practice(minutes: int = 60) -> str:
    if _running():
        return "I am already practising. Ask me how it is going, or say stop practising."
    if rigging.blender_exe() is None:
        return "Blender is not installed here, so I cannot practise."
    if dojo._gaming():
        return "A game is running, so I will not practise now. Ask me again when you are done."
    m = max(5, min(int(minutes or 60), 480))
    p = dojo.Practice(Store(), {"max_minutes": m, "max_experiments": min(40, max(4, m // 5))}).start()
    _current["p"] = p
    return (f"Starting {plural(m, 'minute')} of practice. I will study the characters I still fail on, test my own ideas in a sandbox, and keep only "
            "what makes things better without breaking anything that worked. Your real jobs always go first, and I stop for games. "
            "Ask how it is going any time, or say stop practising.")


@tool("How is the practice session going (what is being studied right now, how many experiments, what was kept)?",
      {}, direct=True)
def practice_status() -> str:
    st = Store().state()
    p = _running()
    if p:
        return (f"I am practising: {st.get('step', 'working')}. {plural(p.n_exp, 'experiment')} so far, "
                f"{plural(round((time.time() - p.t0) / 60), 'minute')} in.")
    last = st.get("last_summary")
    if not last:
        return "I am not practising and have no practice sessions on record yet."
    kept = last.get("promoted", 0)
    return (f"My last practice session ended ({last.get('stopped')}): {plural(last.get('experiments', 0), 'experiment')} in {last.get('minutes')} minutes, "
            f"{plural(kept, 'idea')} kept.")


@tool("Stop the practice session now. Kills the Blender run in progress; nothing of the owner's is touched and everything learned so far is kept.",
      {}, direct=True)
def stop_practice() -> str:
    p = _running()
    if not p:
        Store().request_stop()
        return "I was not practising."
    p.stop()
    return "Stopping practice now. What I learned so far is saved."


@tool("What has JARVIS learned from practising (experiments that worked, ideas disproved, strategies kept) and how honest is "
      "his mastery claim? Use for 'what did you learn from practice', 'what did you learn overnight', 'are you a master yet'.",
      {}, direct=True)
def what_have_you_learned() -> str:
    store = Store()
    text = dojo.spoken_learned(store)
    m = dojo.mastery(store)
    if m:
        bits = [f"{k}: {v['passing']} of {v['cases']} characters pass, {v['label']}" for k, v in m.items()]
        text += " Honest status, " + "; ".join(bits) + ". I do not call anything mastered without enough characters I have never studied."
    return text


@tool("List the learned strategies waiting for the owner's review (code JARVIS wrote for himself is never used on real rigs until "
      "approved) and the active ones, with the evidence behind each. Use for 'what strategies are waiting for review'.",
      {}, direct=True)
def review_learned_strategies() -> str:
    store = Store()
    pend, act = store.pending_review(), [v for v in store.playbook() if v["status"] == "active"]
    if not pend and not act:
        return "I have no learned strategies yet."
    parts = []
    for v in pend:
        ev = v.get("evidence") or {}
        parts.append(f"Waiting for you: {v['id']} ({v.get('name', '')[:70]}), contains code I wrote; on {ev.get('case')} it took tests from {ev.get('before')} to {ev.get('after')} "
                     f"and {len(ev.get('regression_checked') or [])} other characters still passed. Say 'approve strategy {v['id']}' to let real rigs use it.")
    if act:
        parts.append(f"{len(act)} approved or parameter-only strateg{'y is' if len(act) == 1 else 'ies are'} active.")
    return " ".join(parts)


@tool("Approve a learned strategy that contains JARVIS's own code so real rigging may use it (the owner's explicit review). "
      "Use for 'approve strategy <id>'.", {"strategy_id": {"type": "string"}}, direct=True)
def approve_learned_strategy(strategy_id: str) -> str:
    store = Store()
    row = next((v for v in store.pending_review() if v["id"] == strategy_id.strip()), None)
    if not row:
        return f"No strategy {strategy_id} is waiting for review."
    store.set_variant_status(row["id"], "active", "approved by owner")
    store.event("approved", variant=row["id"])
    return f"Approved {row['id']}. Real rigs will now try it after the default fit fails, and it can be switched off again at any time."


@tool("Reject a learned strategy (keeps it recorded but never runs it). Use for 'reject strategy <id>'.",
      {"strategy_id": {"type": "string"}}, direct=True)
def reject_learned_strategy(strategy_id: str) -> str:
    store = Store()
    row = next((v for v in store.playbook() if v["id"] == strategy_id.strip() and v["status"] in ("pending_review", "active")), None)
    if not row:
        return f"No strategy {strategy_id} is active or waiting."
    store.set_variant_status(row["id"], "quarantined", "rejected by owner")
    store.event("rejected", variant=row["id"])
    return f"Rejected {row['id']}. It stays in my records as an attempt but will never run."
