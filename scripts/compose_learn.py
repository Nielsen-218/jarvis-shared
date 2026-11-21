"""Run the COMPOSITIONAL learner (core/compose) on one weakness, only while the owner is away, within its bounded budget.

    python scripts/compose_learn.py [--now] [--hours 6]

Waits for the same idle rules as practice (15 min without keyboard/mouse/controller input, no game, GPU not busy, learner model
served); the lab stops the moment the owner comes back. Ends when the skill is learned (and passed the fresh-process
persistence test), when every strategy's budget has stalled, or at the deadline. Never merges, deploys, restarts JARVIS or
approves anything for production. Log: data/learning/compose-learn.log
"""
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import scripts.acquire_watch as watch  # noqa: E402,F401 - same environment: live misses file, teacher keys, usage ledger

LOG = ROOT / "data" / "learning" / "compose-learn.log"


def say(text):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {text}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def main(now=False, hours=6.0, repair=None, keep_going=False):
    """repair = "skill.id@version": strategy "repair", starting from that stored procedure (his own), e.g. one disabled because
    its verify() disagreed with the judge."""
    from core import acquire, compose, skillspec
    start = None
    if repair:
        sid, _, ver = repair.partition("@")
        man, src = skillspec.library().get(sid, int(ver))
        start = {"source": src, "caps": man["spec"]["dependencies"]["capabilities"], "spec": man["spec"], "ref": f"{sid}-v{ver}.py"}
    deadline = time.time() + hours * 3600
    ready = [c for c in acquire.discover() if c["status"] == "ready"]
    if not ready:
        say("nothing ready to learn")
        return
    cand = ready[0]
    say(f"compositional learning on {cand['tool']} ({cand['id']}) for up to {hours:g} h")
    waiting = ""
    while time.time() < deadline:
        why = acquire.learner_ready() if now else (acquire.idle_ready() or acquire.learner_ready())
        if why:
            if why != waiting:
                say("waiting: " + why)
                waiting = why
            time.sleep(60)
            continue
        waiting = ""
        if now:
            os.environ["JARVIS_LAB_GUARD"] = "off"
        else:
            os.environ.pop("JARVIS_LAB_GUARD", None)
        say("owner away: starting a compositional cycle" if not now else "starting a compositional cycle now, on the owner's command")
        now = keep_going                    # --keep-going (owner 2026-11-08 "I'm not home, use the PC, don't stop"): every cycle
        watch.MARKER.parent.mkdir(parents=True, exist_ok=True)
        watch.MARKER.write_text(time.strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8")   # live Jarvis's keeper leaves the card alone
        try:
            rec = compose.learn(cand, strategy="repair" if start else None, start=start)
            start = None
        except Exception as error:  # noqa: BLE001
            say(f"cycle crashed: {type(error).__name__}: {error}")
            rec = None
        finally:
            watch.MARKER.unlink(missing_ok=True)
            try:
                from core import warmth
                say(f"handed the graphics card back: {warmth.handback(acquire.LEARNER) or 'nothing to load'}")
            except Exception as error:  # noqa: BLE001
                say(f"handback failed: {type(error).__name__}")
        if rec is None:
            time.sleep(300)
            continue
        say(f"cycle ended: {rec['status']} ({rec.get('folder', rec.get('why', ''))})")
        if rec["status"] in ("learned", "budget_exhausted", "retained_but_persistence_failed", "judge_changed"):
            say("stopping: " + rec["status"])
            return
    say("deadline reached")


if __name__ == "__main__":
    h = float(sys.argv[sys.argv.index("--hours") + 1]) if "--hours" in sys.argv else 6.0
    main(now="--now" in sys.argv or "--keep-going" in sys.argv, keep_going="--keep-going" in sys.argv, hours=h, repair=sys.argv[sys.argv.index("--repair") + 1] if "--repair" in sys.argv else None)
