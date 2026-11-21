"""Guided, recorded physical validation session (owner present). Read-only: it never sends input to JARVIS or the PC.

  python scripts/physical_session.py                 # run all steps
  python scripts/physical_session.py --only voice-calc

For each step it tells you what to say or send, watches the service ledger for the resulting task, records stage timings and an
INDEPENDENT check (window state / Windows media session), asks you only what a machine cannot know (did you hear it), and writes a
report (no speech text, no tokens) to the reports folder. A step that is not observed is recorded as NOT_OBSERVED, never as passed.
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
LIVE_DATA = Path(r"C:\Users\you\ECC\jarvis\data")
REPORTS = Path(r"C:\Users\you\ECC-browser-runtime\reports")


def window(app, present):
    def check():
        from tools import apps
        seen = bool(apps.visible_windows_for_app(app))
        return seen == present, f"{app} window {'visible' if seen else 'not visible'}"
    return check


def playing(expect):
    def check():
        from core import mediastate
        rows = [s for s in mediastate.sessions() if s["status"] == "Playing"]
        return bool(rows) == expect, "playing: " + (", ".join(f"{s['app']}" for s in rows) or "nothing")
    return check


STEPS = [
    {"id": "voice-calc", "say": "Say aloud: 'Hey Jarvis, open Calculator.'", "needs_audio": True, "check": window("calculator", True)},
    {"id": "voice-calc-close", "say": "Say aloud: 'Hey Jarvis, close Calculator.'", "needs_audio": True, "check": window("calculator", False)},
    {"id": "voice-youtube", "say": "Say: 'Hey Jarvis, search YouTube for Blender rigging tutorial and open the first video.'", "needs_audio": True,
     "settle": 8, "check": playing(True)},
    {"id": "voice-pause", "say": "Say: 'Hey Jarvis, pause the video.'", "needs_audio": True, "check": playing(False)},
    {"id": "phone-open", "say": "From the paired phone chat send: 'Open Notepad'.", "needs_audio": False, "check": window("notepad", True)},
    {"id": "phone-close", "say": "From the paired phone chat send: 'Close Notepad'.", "needs_audio": False, "check": window("notepad", False)},
]


def new_rows(history, since):
    return [r for r in history() if r["at"] > since]


def summarize(rows):
    stages = {r["stage"]: r["milliseconds"] for r in rows if r.get("component") == "latency" and "stage" in r}
    tasks = [r for r in rows if r.get("component") == "local_automation"]
    return stages, tasks


def run_step(step, *, history, ask, say, wait=120, poll=.5, sleep=time.sleep, clock=time.time):
    say(f"\n== {step['id']} ==\n{step['say']}")
    since = clock()
    deadline = since + wait
    tasks = []
    while clock() < deadline:
        _, tasks = summarize(new_rows(history, since))
        if tasks:
            break
        sleep(poll)
    if not tasks:
        return {"id": step["id"], "result": "NOT_OBSERVED", "note": f"no task reached the ledger within {wait} s (service not on this branch, wake word missed, or nothing was said)"}
    sleep(step.get("settle", 1.5))
    stages, tasks = summarize(new_rows(history, since))
    ok, detail = step["check"]()
    heard = None
    if step.get("needs_audio"):
        heard = ask("Did you hear JARVIS answer out loud? [y/n] ").strip().lower().startswith("y")
    verified = all(t.get("verified", True) is not False for t in tasks)
    result = "PASS" if ok and verified and heard is not False else "FAIL"
    return {"id": step["id"], "result": result, "independent_check": {"passed": ok, "detail": detail}, "ledger_verified": verified,
            "audible_confirmed_by_owner": heard, "stage_ms": stages, "tools": [t.get("tool") for t in tasks], "methods": [t.get("method") for t in tasks]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", action="append", help="step id (repeatable)")
    parser.add_argument("--wait", type=int, default=120)
    parser.add_argument("--data", default=str(LIVE_DATA))
    args = parser.parse_args()
    from core import storage
    storage.DATA = Path(args.data)
    from core import pcledger
    chosen = [s for s in STEPS if not args.only or s["id"] in args.only]
    print("Guided physical session. Close Calculator/Notepad and pause any media first. Ctrl+C to stop; completed steps are saved.")
    results = []
    try:
        for step in chosen:
            results.append(run_step(step, history=pcledger.history, ask=input, say=print, wait=args.wait))
            print("->", results[-1]["result"], {k: v for k, v in results[-1].items() if k in ("stage_ms", "note", "independent_check")})
    except KeyboardInterrupt:
        print("\nStopped early; saving what was recorded.")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / f"physical-{stamp}.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    lines = [f"# Physical validation {stamp}", "", "| step | result | stage ms | independent check |", "|---|---|---|---|"]
    for r in results:
        lines.append(f"| {r['id']} | {r['result']} | {json.dumps(r.get('stage_ms', {}))} | {r.get('independent_check', {}).get('detail', r.get('note', ''))} |")
    (REPORTS / f"physical-{stamp}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    passed = sum(r["result"] == "PASS" for r in results)
    print(f"\n{passed}/{len(results)} passed. Report: {REPORTS / ('physical-' + stamp + '.md')}")


if __name__ == "__main__":
    main()
