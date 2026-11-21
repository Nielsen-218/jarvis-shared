"""Live multi-turn conversations with the REAL brain on the REAL local models (Ollama), no cloud.

    .venv\\Scripts\\python.exe scripts\\convo_local.py            # every scenario
    .venv\\Scripts\\python.exe scripts\\convo_local.py memory screen

Like tests/e2e_api.py, but for the local-first tiers: a temporary data folder (real memories, lists and the vault
are never touched), the owner's own personality and tool registry, and the same Brain.ask() the voice loop and the
phone app call. Each turn prints the tools Jarvis chose, which local tier answered, the time it took and what he
said. Screenshots taken by look_at_screen stay on this machine (they only ever reach the local Ollama vision model)
and are never written to the transcript. Exit code 1 when any check failed.
"""
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("JARVIS_INTELLIGENCE", "local-first")
os.environ["JARVIS_LOCAL_ONLY"] = "1"                      # a paid call here would be a bug, not a fallback
for _v in ("JARVIS_PC_URL", "JARVIS_SERVER_URL", "JARVIS_SERVER_TOKEN", "JARVIS_SERVER_SSH", "JARVIS_ROLE"):
    os.environ.pop(_v, None)                               # this machine's own model, not the laptop relay

from core import storage  # noqa: E402

storage.DATA = Path(tempfile.mkdtemp(prefix="jarvis-convo-"))
os.environ["JARVIS_VAULT"] = str(storage.DATA / "vault")
os.environ["JARVIS_COMPANION_DATA_DIR"] = str(storage.DATA / "companion")
# PC plans waiting for review and grants live OUTSIDE storage.DATA (machine-state/pc-control): a plan this harness showed
# stayed pending in the REAL ledger, and the next run's sentence approved it (2026-11-09: Notepad opened and saved a file).
os.environ["JARVIS_PC_STATE_DIR"] = str(storage.DATA / "pc-control")
os.environ["JARVIS_PC_ROOT"] = str(storage.DATA / "jarvis_pc")

import webbrowser  # noqa: E402

from brain.llm import Brain  # noqa: E402
from core import intelligence, users  # noqa: E402
from core.config import Config, load_personality  # noqa: E402
from tools import load_all, memory, registry  # noqa: E402

load_all()
users.set_current("owner")
calls: list[tuple[str, dict]] = []
_run = registry.run


def tracking_run(name, args):
    calls.append((name, args))
    return _run(name, args)


registry.run = tracking_run
models: list[str] = []                                     # which local model each Ollama request went to
_body = intelligence._ollama_body


def tracking_body(body):
    models.append(body["model"])
    return _body(body)


intelligence._ollama_body = tracking_body
webbrowser.open = lambda url, *a, **k: True                # never pop a browser from a scripted chat
cfg = Config()
results: list[tuple[str, bool]] = []
transcript: list[dict] = []
_ERRORS = ("something went wrong", "try again in a moment", "local intelligence is unavailable", "can't reach my servers")


def new_brain() -> Brain:
    b = Brain(cfg.model, cfg.effort, load_personality(cfg, "owner"), uid="owner")
    b.context = memory.context_block
    return b


def _tiers_since(n: int) -> list[str]:
    rows = intelligence.usage_rows()[n:]
    return [f"{r['role']}:{r['status']}" for r in rows]


def ask(brain: Brain, text: str) -> str:
    calls.clear()
    models.clear()
    said: list[str] = []
    before = len(intelligence.usage_rows())
    t = time.time()
    brain.ask(text, said.append)
    took = time.time() - t
    reply = " ".join(said)
    used = [name for name, _ in calls]
    tiers = _tiers_since(before)
    print(f"\nYOU: {text}\n  tools: {used}\n  tiers: {tiers}  models: {models}\n  JARVIS ({took:.1f}s): {reply[:500]}")
    transcript.append({"you": text, "tools": used, "tiers": tiers, "models": list(models), "seconds": round(took, 1),
                       "jarvis": reply})
    return reply


def check(name: str, ok: bool) -> None:
    results.append((name, ok))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}")


def _no_leaks(reply: str) -> bool:
    """A real spoken answer: nothing of the machinery (JSON, tool-call text, tags) and no error message."""
    low = reply.lower()
    return bool(reply.strip()) and not any(x in low for x in (
        "{", "answer_directly", "function call", "tool call", "tool_call", "<background", "<retrieved", "<tool-images",
        "<think>", *_ERRORS))


# -- scenarios ---------------------------------------------------------------------------------------------------
def small_talk():
    b = new_brain()
    r = ask(b, "Hey Jarvis, how are you doing today?")
    check("greeting gets a spoken answer with no tools", bool(r.strip()) and not calls and _no_leaks(r))
    r = ask(b, "Haha, nice. I was just testing you.")
    check("laughter is small talk, not a tool", bool(r.strip()) and not calls and _no_leaks(r))
    r = ask(b, "Thanks, that's all for now.")
    check("thanks and goodbye need no tool", bool(r.strip()) and not calls and _no_leaks(r))


def memory_and_lists():
    b = new_brain()
    r = ask(b, "Remember that my sister Marie lives in Miami.")
    check("remembers a fact with the memory skill", any(n in ("remember", "remember_note") for n, _ in calls))
    check("saved what was said, not an invention", any("marie" in json.dumps(a).lower() and "miami" in
                                                        json.dumps(a).lower() for _, a in calls))
    check("confirms the fact exactly, told back to them", "marie" in r.lower() and "miami" in r.lower()
          and "you live" not in r.lower())
    b = new_brain()  # like restarting Jarvis: the fact must come back from storage, not the chat
    r = ask(b, "Where does my sister live?")
    check("recalls the fact after a restart", "miami" in r.lower() and _no_leaks(r))
    check("a remembered fact is not answered with driving directions", "directions" not in [n for n, _ in calls])
    ask(b, "Add eggs and milk to my shopping list.")
    check("adds to the shopping list", any(n == "add_note" for n, _ in calls))
    r = ask(b, "What's on my shopping list?")
    check("reads the list back", "egg" in r.lower() and "milk" in r.lower() and _no_leaks(r))
    r = ask(b, "What were we just talking about?")
    check("recaps the conversation", any(w in r.lower() for w in ("shopping", "list", "sister", "miami", "egg")))


def questions_without_acting():
    b = new_brain()
    r = ask(b, "What's my Wi-Fi password?")
    check("does not invent a password", not any(n.startswith(("set_", "remember")) for n, _ in calls)
          and any(w in r.lower() for w in ("don't", "not", "no ", "haven't", "never", "unknown", "check")))
    r = ask(b, "What time is it?")
    check("time is read from the clock", "get_time" in [n for n, _ in calls] or any(ch.isdigit() for ch in r))
    r = ask(b, "What's 15 percent of 80?")
    check("percent of a number is exact and instant", r.strip() == "15 percent of 80 is 12." and not calls)
    r = ask(b, "What's 12 volts across 470 ohms, in milliamps?")
    check("engineering maths is right (calculator or Ohm's law in its head)", "25" in r and _no_leaks(r))


def screen():
    b = new_brain()
    r = ask(b, "Take a look at my screen and tell me what you see.")
    looked = any(n in ("look_at_screen", "read_screen", "see_pc_screen") for n, _ in calls)
    check("looks at the screen when asked", looked)
    check("describes the screen in words, no error, no leaked machinery", _no_leaks(r))
    check("answered locally", not any(t.startswith("cloud") for t in transcript[-1]["tiers"]))
    r = ask(b, "Take another screenshot and describe just the layout and the colours, not the text.")
    check("a visual question takes a screenshot", "look_at_screen" in [n for n, _ in calls])
    check("...which the local vision model looks at", (intelligence.vision_model() or "qwen3-vl") in " ".join(models))
    check("...and describes in words", _no_leaks(r) and len(r.split()) >= 8)
    r = ask(b, "Is there a web browser open in it?")
    check("a follow-up about the picture goes back to the vision model", (intelligence.vision_model() or "qwen3-vl") in " ".join(models) and _no_leaks(r))
    r = ask(b, "Okay, thanks. Remind me what we were just doing?")
    check("conversation continues after the screenshot", _no_leaks(r) and any(w in r.lower() for w in ("screen", "look", "screenshot", "layout", "desktop", "window")))


def retention():
    """Facts mentioned in passing survive the conversation: the local model writes the journal notes when a chat
    ends, grounded in what was actually said, and the next conversation knows them."""
    from core import journal
    b = new_brain()
    ask(b, "So my wife Maya and I are thinking of driving down to Miami in December to see my sister Marie.")
    r = ask(b, "Probably the week before Christmas. Don't book anything yet.")
    check("chit-chat about plans gets a spoken reply", _no_leaks(r))
    t = time.time()
    archived = b.archive_if_idle(force=True)                 # the conversation ends: the journal is written locally
    if getattr(b, "_archiving", None):
        b._archiving.join(180)                                # (the brain writes it in the background; wait for it here)
    took = time.time() - t
    entry = journal.recent(1)[-1] if journal.recent(1) else {}
    print(f"\n  [journal {took:.1f}s] {entry.get('source')}: {entry.get('summary', '')[:300]}  open={entry.get('open')}")
    check("the conversation is journaled when it ends", bool(archived) and bool(entry))
    check("...by the local model, with a summary about THIS chat", entry.get("source") == "local_model"
          and any(w in entry.get("summary", "").lower() for w in ("miami", "december", "marie", "trip", "wife")))
    facts = [m["fact"] for m in storage.load(memory.FILE, [])]
    print(f"  [facts] {facts}")
    check("facts they stated were kept", any("maya" in f.lower() or "marie" in f.lower() for f in facts))
    check("nothing they didn't say was kept", not any(w in " ".join(facts).lower() for w in ("corvette", "mars", "password")))
    b = new_brain()                                           # a new conversation, like the next day
    r = ask(b, "What's my wife's name?")
    check("a fact from an earlier chat is recalled", "maya" in r.lower() and _no_leaks(r)
          and not re.search(r"don'?t know|nothing (?:installed|found|in)|no record", r, re.I))
    r = ask(b, "What did we talk about earlier about Miami?")
    check("the earlier conversation is recalled from the journal", _no_leaks(r)
          and any(w in r.lower() for w in ("marie", "december", "trip", "sister", "christmas")))


def intent():
    """Unknown phrases, phone-app messages and misheard fragments run no mode-changing skill (language lock,
    interpreter, pairing); the requests people make for those still do. Live 2026-10-30: 'frozen pencil' from the
    phone app was paired as a person and "Please make corrections too." locked speech recognition to Kreyol."""
    import random
    mode = ("set_conversation_language", "start_interpreter", "pair_phone", "switch_person", "add_household_member")
    note = "[Sent from their Jarvis phone app. Keep replies conversational; no markdown.] "
    rng = random.Random()
    words_a = ["frozen", "amber", "velvet", "copper", "quiet", "hollow", "paper", "crimson"]
    words_b = ["pencil", "lantern", "meadow", "anchor", "falcon", "ledger", "window", "garden"]
    b = new_brain()
    for said in (f"{rng.choice(words_a)} {rng.choice(words_b)}", note + f"{rng.choice(words_a)} {rng.choice(words_b)}",
                 "Please make corrections too.", "we have no request to stop.", note + "Juris."):
        r = ask(b, said)
        check(f"no PC action for {said[-40:]!r}", not str(r or "").lstrip().startswith(("Opened", "Closed", "Should I open")))
        check(f"no language, interpreter or pairing skill for {said[-40:]!r}",
              not any(n in mode for n, _ in calls) and _no_leaks(r))
    b = new_brain()
    ask(b, "From now on, only speak English with me.")
    check("a language switch they asked for runs", any(n == "set_conversation_language" and
                                                       str(a.get("language", "")).lower() in ("english", "en") for n, a in calls))
    ask(b, "Pair my phone, please.")
    check("pairing their own phone runs", any(n == "pair_phone" for n, _ in calls))
    r = ask(b, "What's my wife's name?")
    check("a question about them still looks nothing up by mistake", not any(n in mode for n, _ in calls) and _no_leaks(r))


def research():
    """Live internet research through the real brain and real providers (Exa, Bing, DuckDuckGo, yt-dlp): a clear
    spoken request always searches and the answer names its sources; YouTube discovery returns real videos; a
    saved finding comes back after a 'restart' without another search; random phrases search nothing."""
    import re
    from core import research as store, websearch
    web = ("search_internet", "research_online", "search_web_free", "find_youtube_tutorials", "open_web_page", "read_web_pages")
    note = "[Sent from their Jarvis phone app. Keep replies conversational; no markdown.] "
    b = new_brain()
    r = ask(b, "Jarvis, search the internet for the latest official NVIDIA driver for the RTX 5070 Ti, with the version and date.")
    ran = [n for n, _ in calls]
    check("A: a live search ran for the driver request", any(n in web for n in ran))
    check("A: the answer carries a version number", bool(re.search(r"\b\d{3}\.\d{2}\b", r)) and _no_leaks(r))
    check("A: the answer names its source (nvidia or the site)", "nvidia" in r.lower() or ".com" in r.lower() or "source" in r.lower())
    saved = store.sources("NVIDIA driver")
    check("A: the search and its sources are kept in the research store", bool(saved) and any(s.get("modality") == "search_results" for s in saved))
    r = ask(b, "Google this: Python ModuleNotFoundError no module named yaml. How do I fix it?")
    check("C: 'google this' runs a live search", any(n in web for n, _ in calls))
    check("C: the fix names pip or PyYAML and a source", ("pip" in r.lower() or "pyyaml" in r.lower()) and _no_leaks(r))
    r = ask(b, "Look up three good Blender rigging tutorials on YouTube.")
    check("B: YouTube discovery ran (not the local Blender notes)", "find_youtube_tutorials" in [n for n, _ in calls]
          and "blender_reference" not in [n for n, _ in calls])
    videos = [s for s in store.sources("Blender rigging") if s.get("modality") == "video_metadata_only"
              and s["url"].startswith("https://www.youtube.com/watch?v=")]
    check("B: three real videos with links are saved", len(videos) >= 3 and all(v["url"].startswith("https://www.youtube.com/watch?v=") for v in videos))
    check("B: the reply names a real title or channel from the results", any((v.get("creator") or "").lower() in r.lower() or (v.get("title") or "")[:20].lower() in r.lower() for v in videos) and _no_leaks(r))
    r = ask(b, "Learn this.")
    check("D: 'learn this' saves the finding as retrieved, unverified", r.startswith("Saved") and "unverified" in r)
    check("D: ...without claiming mastery", "master" not in r.lower() or "isn't a practised skill" in r)
    b = new_brain()                                           # a restart: a new conversation, nothing in context
    before = len(calls)
    r = ask(b, "What did you find about Blender rigging tutorials?")
    check("D: after the restart the finding comes back without another internet search", "without searching the internet" in r
          and "youtube.com" in r and not any(n in web for n, _ in calls))
    r = ask(b, note + "frozen pencil")
    check("random phone phrase searches nothing", not any(n in web for n, _ in calls) and _no_leaks(r))
    r = ask(b, "velvet lantern")
    check("random voice phrase searches nothing", not any(n in web for n, _ in calls))
    r = ask(b, note + "Jarvis, look up the Blender 5.0 release date online.")
    check("F: a phone-app request routes to the live search", any(n in web for n, _ in calls) and _no_leaks(r))


def rigging():
    """The rigging skills are reachable by voice: the owner's real question reads the evidence-based status
    (never invents experience), PC-awareness questions look at the PC, and nothing random starts a rig."""
    heavy = ("rig_character", "image_to_character")
    b = new_brain()
    r = ask(b, "What are your rigging skills?")
    check("skills question calls rigging_skills", "rigging_skills" in [n for n, _ in calls])
    check("...and starts no rig", not any(n in heavy for n, _ in calls))
    check("...answer uses the evidence words, not generic Blender advice",
          any(w in r.lower() for w in ("verified", "untested", "failed")) and "tutorial" not in r.lower() and _no_leaks(r))
    r = ask(b, "What windows are open on my PC right now?")
    check("window question answered locally from the real windows, at no cost",
          ("windows are open" in r or "In front:" in r) and not any(t.startswith("cloud") for t in transcript[-1]["tiers"]))
    r = ask(b, "What have you learned about rigging from your tests?")
    check("lessons question recalls recorded lessons", any(n in ("rigging_lessons", "rigging_skills") for n, _ in calls))
    r = ask(b, "Find my vanguard character")
    check("find request lists the vanguard file", "find_character" in [n for n, _ in calls] and "vanguard" in r.lower())
    b2 = new_brain()                                           # fresh conversation: nothing says WHICH character
    r = ask(b2, "Rig my character")
    check("'rig my character' goes to the resolver and ASKS which one (nothing open) instead of guessing",
          "rig_my_character" in [n for n, _ in calls] and ("Which character" in r or "Using your open" in r or "Two Blender" in r))
    note = "[Sent from their Jarvis phone app. Keep replies conversational; no markdown.] "
    r = ask(new_brain(), note + "Rig my character")
    used = transcript[-1]["tools"]
    check("phone-app 'rig my character' reaches the resolver, not the low-level rig tool",
          "rig_my_character" in used and "rig_character" not in used)
    for phrase in ("velvet lantern", "frozen pencil", "Hello Jarvis, how are you?"):
        before = len(calls)
        ask(b, phrase)
        check(f"'{phrase}' starts no rig", not any(n in heavy for n, _ in calls[before:]))


SCENARIOS = {"rigging": rigging, "small_talk": small_talk, "memory": memory_and_lists, "questions": questions_without_acting,
             "screen": screen, "retention": retention, "intent": intent, "research": research}


def main(argv):
    picked = [a for a in argv if a in SCENARIOS] or list(SCENARIOS)
    print(f"data folder: {storage.DATA}\nfast: {intelligence.model('fast')}  advanced: {intelligence.model('advanced')}"
          f"  vision: {os.getenv('JARVIS_LOCAL_VISION_MODEL', 'qwen3-vl:8b-instruct')}")
    for name in picked:
        print(f"\n===== {name} =====")
        try:
            SCENARIOS[name]()
        except Exception as e:  # one scenario's crash must not hide the others
            check(f"{name} ran without crashing ({type(e).__name__}: {e})", False)
    out = Path(os.getenv("JARVIS_CONVO_LOG", storage.DATA / "transcript.json"))
    out.write_text(json.dumps({"results": results, "transcript": transcript}, indent=1), encoding="utf-8")
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed; transcript: {out}")
    for n in failed:
        print(f"  FAIL {n}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
