"""Practise only while the owner is away (owner 2026-11-07: "Run while I'm away").

Every minute: if the PC has had no keyboard/mouse input for 15 minutes and no game is running, run ONE bounded learning cycle on the
top ready weakness. The lab stops the moment the owner touches the PC; the next idle window continues from the best method so far.
Stops for good when: a method is learned (then the lab retest runs once), MAX_FAILED cycles end not_learned, or the deadline passes.
Writes progress to data/learning/acquire-watch.log. Never merges, deploys, restarts JARVIS or approves anything for production.
"""
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core import storage                      # noqa: E402
LIVE_ROOT = storage.live_root()               # the live checkout, even when this script runs from one of its worktrees
DATA_ROOT = LIVE_ROOT / "data"
# Live Jarvis touches this on every voice/phone request: that is the owner being here, as much as the keyboard is.
os.environ.setdefault("JARVIS_TURN_DIR", str(DATA_ROOT / "learning"))
# The owner's real misses live in live Jarvis's data, not this worktree's (2026-11-08: a relaunch without this variable found
# "nothing ready to learn" and the watcher stopped for the night).
os.environ.setdefault("JARVIS_MISSES_FILE", str(DATA_ROOT / "learning" / "live-misses.jsonl"))
os.chdir(ROOT)
# The teacher's keys and limits come from live Jarvis's .env (only these names), and its spending goes in live Jarvis's ledger.
_TEACHER_KEYS = ("ANTHROPIC_API_KEY", "JARVIS_MONTHLY_BUDGET", "JARVIS_TEACHER_AFTER", "JARVIS_TEACHER_LOCAL", "JARVIS_TEACHER_LOCAL_MAX",
                 "JARVIS_TEACHER_MODEL", "JARVIS_TEACHER_SKILL_USD", "JARVIS_TEACHER_CREDIT_USD", "JARVIS_TEACHER_CREDIT_AS_OF",
                 "JARVIS_TEACHER_CREDIT_EXPIRES")
_LIVE_ENV = Path(os.getenv("JARVIS_ENV_FILE", str(LIVE_ROOT / ".env")))
if _LIVE_ENV.exists():
    for _line in _LIVE_ENV.read_text(encoding="utf-8", errors="replace").splitlines():
        _k, _, _v = _line.partition("=")
        if _k.strip() in _TEACHER_KEYS and _v.strip():
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))
os.environ.setdefault("JARVIS_USAGE_FILE", str(DATA_ROOT / "usage.json"))
HOURS = float(os.getenv("ACQUIRE_WATCH_HOURS", "14"))
MAX_FAILED = 3
MAX_LAB_FAULTS = 3


def say(text):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {text}"
    print(line, flush=True)
    with open(DATA_ROOT / "learning" / "acquire-watch.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")


MARKER = DATA_ROOT / "learning" / "practice-active"   # core.warmth reads it (live Jarvis's data folder)
INBOX = Path(os.getenv("JARVIS_NEWS_INBOX", str(DATA_ROOT / "learning" / "news-inbox.jsonl")))


def news(cand, rec):
    """Tell the owner (through live Jarvis's attention judge, core.attention) what practice did while he was away."""
    said = cand["examples"][0]["said"]
    scores = [(sum(r["passed"] for r in s["data"]["rows"]), len(s["data"]["rows"])) for s in rec["stages"]
              if s["stage"] == "practice" and isinstance(s.get("data"), dict) and s["data"].get("rows")]
    best = max(scores, default=None)
    helped = (rec.get("provenance") or {}).get("provenance") == "teacher_assisted"
    if rec["status"] == "learned" and helped:
        text, urgency = (f"I learned a new PC skill while you were away, with a teacher's hints: '{said}' now passes all 12 sealed "
                         "tests. I wrote the method myself, but I did not work it out alone. It waits for your OK before I use it."), "high"
    elif rec["status"] == "learned":
        text, urgency = (f"I taught myself a new PC skill while you were away, with no help: '{said}' now passes all 12 sealed tests. "
                         "It waits for your OK before I use it."), "high"
    elif best:
        text, urgency = f"I practised '{said}' while you were away. Best so far {best[0]} of {best[1]}; not learned yet.", "low"
    else:
        return
    try:
        INBOX.parent.mkdir(parents=True, exist_ok=True)
        with open(INBOX, "a", encoding="utf-8") as f:
            f.write(json.dumps({"text": text, "urgency": urgency, "key": "practice:" + cand["id"], "at": time.time()}) + "\n")
    except OSError as error:
        say(f"could not leave news for the owner: {error}")


def main(now=False):
    """now=True (owner 2026-11-08: "Start now"): the FIRST cycle starts at once and is not stopped by his own input (it runs on
    the hidden lab desktop); every later cycle waits for him to be away again, as usual."""
    from core import acquire
    deadline, failed, waiting, last_note = time.time() + HOURS * 3600, 0, "", 0.0
    lab_broken = 0
    say("starting a cycle now, on the owner's command" if now else
        f"watching: practice only while the owner is away, for up to {HOURS:g} h")
    while time.time() < deadline:
        if now:
            os.environ["JARVIS_LAB_GUARD"] = "off"
        else:
            os.environ.pop("JARVIS_LAB_GUARD", None)
        why = acquire.learner_ready() if now else (acquire.idle_ready() or acquire.learner_ready())
        if why:
            if why != waiting or time.time() - last_note > 1800:   # also every 30 min, with the numbers behind it
                from core import inputwatch
                say(f"waiting: {why} (keyboard/mouse idle {inputwatch.keyboard_mouse_idle():.0f} s, "
                    f"controller idle {inputwatch.controller_idle():.0f} s)")
                waiting, last_note = why, time.time()
            time.sleep(60)
            continue
        waiting = ""
        ready = [c for c in acquire.discover() if c["status"] == "ready" and c["id"] not in acquire.paused()]
        if not ready:
            say("nothing ready to learn with click scripts (paused weaknesses go to scripts/compose_learn.py); stopping")
            return
        say(f"{'owner command' if now else 'owner away'}: starting a cycle on {ready[0]['examples'][0]['said']!r}")
        now = False                         # only this one cycle ignores his presence
        MARKER.parent.mkdir(parents=True, exist_ok=True)
        MARKER.write_text(time.strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8")   # live Jarvis's keeper leaves the card alone
        try:
            rec = acquire.learn(ready[0])
        except Exception as error:  # noqa: BLE001 - a crash is evidence; never retry blindly in a loop
            MARKER.unlink(missing_ok=True)
            say(f"cycle crashed: {type(error).__name__}: {error}")
            failed += 1
            if failed >= MAX_FAILED:
                say(f"{failed} cycles crashed or ended without learning; stopping so the evidence can be reviewed")
                return
            time.sleep(600)                 # never crash-loop: give whatever broke time to come back
            continue
        MARKER.unlink(missing_ok=True)
        say(f"cycle ended: {rec['status']} ({rec['folder']})")
        news(ready[0], rec)
        try:                            # the owner is back (or the cycle is over): his Jarvis gets the graphics card back now
            from core import warmth
            say(f"handed the graphics card back: {warmth.handback(acquire.LEARNER) or 'nothing to load'}")
        except Exception as error:  # noqa: BLE001
            say(f"handback failed: {type(error).__name__}")
        if rec["status"] == "learned":
            pid = rec["method"]["id"]
            # Staging copy only (this worktree's data folder, not production): approved so the lab retest can use it.
            acquire.approve(pid, by="claude-lab-retest (staging only, not the owner)")
            out = acquire.retest("Close GitHub.", env_file=os.getenv("JARVIS_ENV_FILE", str(ROOT / ".env")))
            (DATA_ROOT / "learning" / "acquire-retest.json").write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
            say(f"retest after restart: {sum(r['passed'] for r in out['rows'])}/{len(out['rows'])} passed ({out['status']})")
            return
        if rec["status"] == "error" and rec.get("cause") == "lab":
            lab_broken += 1                 # the LAB failed, not the learner: not a failed cycle, never a teacher stall; retried later
            say(f"the practice lab itself failed ({rec.get('error', '')[:200]}); attempt {lab_broken} of {MAX_LAB_FAULTS}")
            if lab_broken >= MAX_LAB_FAULTS:
                say("the lab failed on every trial in several cycles in a row; stopping so it can be repaired (no lesson was failed)")
                return
            time.sleep(300)
            continue
        lab_broken = 0
        if rec["status"] in ("not_learned", "not_reproduced"):
            failed += 1
            if failed >= MAX_FAILED:
                from core import teacher
                helpable, why = teacher.can_help(ready[0]["id"]) if rec["status"] == "not_learned" else (False, "not reproduced")
                if helpable:
                    say(f"{failed} cycles ended without learning; I'm stuck, so the next cycle starts with a {why} teacher's lesson")
                    failed = 0
                    continue
                say(f"{failed} cycles ended without learning and no more help is available ({why}); stopping so the evidence "
                    "can be reviewed")
                return
    say("deadline reached; stopping")


if __name__ == "__main__":
    main(now="--now" in sys.argv)
