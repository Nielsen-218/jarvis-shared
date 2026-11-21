"""Jarvis's self-knowledge: report its own technical setup accurately (checked live, never guessed) and list
the household's paired phones. Loads on every machine so the phone can ask "what model are you using?"."""
import os
import platform
import socket

from core import users
from tools.registry import tool

ROLE = os.getenv("JARVIS_ROLE", "all")
MACHINE = {"server": "the always-on laptop", "desk": "the desk PC", "all": "the PC"}.get(ROLE, "this machine")


@tool("Report Jarvis's own technical setup when the user asks what AI model or brain you use (online or "
      "offline), your voice, or how you transcribe speech. Check it live; never guess. Covers the online Claude "
      "models, the local offline fallback model and whether it's reachable right now, the speech-to-text engine, "
      "and which machine is answering.")
def system_info() -> str:
    from core.config import Config

    cfg = Config()
    lines = [f"Running on {MACHINE} ({socket.gethostname()}, {platform.system()})."]
    fast = os.getenv("JARVIS_FAST_MODEL", "claude-haiku-5-5")
    lines.append(f"Online brain (Anthropic API, when there's internet): Claude — smart model '{cfg.model}', "
                 f"quick model '{fast}' for everyday questions.")
    try:
        from core import offline

        model = os.getenv("JARVIS_LOCAL_MODEL", "")
        if offline.available():
            lines.append(f"Offline fallback (used when the internet or API is down): local model "
                         f"'{offline.model_name()}' on {offline.URL} — reachable now.")
        elif model:
            lines.append(f"Offline fallback: local model '{model}' is configured but not reachable at this moment.")
        else:
            lines.append("Offline fallback: none configured on this machine.")
    except Exception as e:
        lines.append(f"Offline fallback: couldn't check ({type(e).__name__}).")
    try:
        from audio import parakeet

        stt = "Parakeet TDT (fast) with Whisper as backup for Kreyol" if parakeet.available() else "Whisper"
        lines.append(f"Speech-to-text: {stt}.")
    except Exception:
        lines.append("Speech-to-text: Whisper.")
    voice = os.getenv("JARVIS_VOICE", "")
    if voice:
        lines.append(f"Voice: {voice.split(':')[-1] or voice} (local, offline).")
    return "\n".join(lines)


@tool("List the household member's own paired phones and devices (name, when linked) — use when asked which "
      "phones are connected or to confirm a pairing, instead of guessing from codes.", required=[])
def list_my_devices() -> str:
    person = users.get(users.current())
    devices = person.get("devices", [])
    if not devices:
        return "No phones are linked to you yet. Say 'pair my phone' for a code."
    lines = [f"- {d.get('name', 'device')}"
             + (f", linked {d['added'][:10]}" if d.get("added") else "")
             + (" (gets notifications)" if d.get("push") else "")
             for d in devices]
    return f"{person.get('name') or 'You'} has {len(devices)} linked device(s):\n" + "\n".join(lines)


@tool("Report how INDEPENDENT Jarvis is: how many skills he has learned (by himself, with his local teacher, with the paid "
      "teacher), what share of recent PC tasks ran with no outside call, whether lessons worked on the next attempt, and "
      "how often the paid teacher was called this month and what it cost. Use when asked how independent or self-reliant "
      "he is, what he has learned on his own, or what the teacher cost. Counted from his own records; never guess.",
      required=[])
def independence_report() -> str:
    import json
    from core import independence, storage
    data = independence.report()
    if data["pc_tasks"]["local_share"] is None:           # this machine keeps no PC ledger (the laptop): use the PC's saved count
        try:
            saved = json.loads((storage.DATA / independence.REPORT).read_text(encoding="utf-8"))
            data["pc_tasks"] = saved["pc_tasks"]
        except (OSError, ValueError, KeyError):
            pass
    return independence.spoken(data)


@tool("List the skills Jarvis LEARNED by himself lately (practised in his lab, passed an exam, approved automatically) and "
      "whether each is in use. Use for 'what did you learn recently', 'which skills did you learn', 'list your learned "
      "skills', 'what new skills do you have'. Read from his own records; never guess.", required=[])
def learned_skills(days: int = 30) -> str:
    from core import learned
    return learned.spoken_recent(max(1, min(int(days or 30), 3650)))


@tool("Remove (undo) a skill Jarvis learned by himself, so he goes back to the built-in way and does not learn it again "
      "unasked. Use for 'undo that skill', 'remove that skill', 'forget the skill you just learned', 'remove the skill for "
      "closing apps'. name: the owner's words for the skill; leave it empty for 'that skill' (the newest one). This only "
      "withdraws a learned skill; it never undoes a PC action.", required=[])
def remove_learned_skill(name: str = "") -> str:
    from core import learned
    return learned.remove(name)
