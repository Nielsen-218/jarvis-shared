"""Read-only readiness check before the owner-assisted physical tests. Changes nothing, restarts nothing.

  python scripts/homecoming_check.py

Prints PASS / WARN / FAIL per item with the exact fix, then a go / no-go line.
"""
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIVE = Path(r"C:\Users\you\ECC\jarvis")
sys.path.insert(0, str(ROOT))
rows = []


def check(name, fn):
    try:
        status, detail = fn()
    except Exception as error:
        status, detail = "FAIL", f"{type(error).__name__}: {str(error)[:150]}"
    rows.append((status, name, detail))
    print(f"{status:4} {name}: {detail}", flush=True)


def git(*args, cwd=ROOT):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True).stdout.strip()


def service():
    import psutil
    found = [p for p in psutil.process_iter(["pid", "cmdline", "cwd"]) if p.info["cmdline"] and "main.py" in " ".join(p.info["cmdline"]) and "python" in p.info["cmdline"][0].lower()]
    if not found:
        return "FAIL", "JARVIS is not running. Start it with start-jarvis.bat (on the branch under test)."
    cwd = found[0].info.get("cwd") or "?"
    return "PASS", f"running (pid {found[0].pid}); its working folder is {cwd}"


def code_under_test():
    live_head, live_branch = git("rev-parse", "--short", "HEAD", cwd=LIVE), git("branch", "--show-current", cwd=LIVE)
    mine = git("rev-parse", "--short", "HEAD")
    if live_branch == "codex/pc-control-reliability":
        return "PASS", f"service checkout is on the branch ({live_head})"
    return "WARN", (f"the running service checkout is '{live_branch}' ({live_head}), NOT this branch ({mine}). The browser, media and follow-up "
                    "features are not live until you authorize a restart on the branch (see docs/PRODUCTION-REVIEW-pc-control-browser.md section 4).")


def remote():
    local, remote_head = git("rev-parse", "HEAD"), git("ls-remote", "origin", "refs/heads/codex/pc-control-reliability").split("\t")[0]
    return ("PASS", "branch is on GitHub at the same commit") if local == remote_head else ("WARN", f"local {local[:7]} differs from remote {remote_head[:7] or 'none'}")


def audio():
    import sounddevice as sd
    default = sd.default.device
    inp, out = sd.query_devices(default[0]), sd.query_devices(default[1])
    return "PASS", f"input '{inp['name'][:40]}', output '{out['name'][:40]}'"


def ollama():
    tags = json.load(urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=3))["models"]
    loaded = [m["name"] for m in json.load(urllib.request.urlopen("http://127.0.0.1:11434/api/ps", timeout=3)).get("models", [])]
    names = {m["name"] for m in tags}
    need = {"llama3.2:3b", "qwen3-vl:8b-instruct"} - names
    if need:
        return "WARN", "missing models: " + ", ".join(sorted(need))
    return ("PASS" if loaded else "WARN"), ("loaded: " + ", ".join(loaded)) if loaded else "models installed but none loaded: the first reply will take ~7 s"


def browser_runtime():
    from core import storage, webbrowse
    storage.DATA = LIVE / "data"
    registered = bool(storage.load_global("research-browser.json", {}).get("python"))
    if registered:
        return "PASS", "browser runtime is registered for the service"
    dev = webbrowse.available()
    return "WARN", ("not registered for the service (expected until you authorize it); " + ("a development opt-in is available" if dev else "no runtime found")
                    + "; browser actions will fall back to plain research")


def clean_slate():
    from core import mediastate
    from tools import apps
    open_apps = [a for a in ("calculator", "notepad") if apps.visible_windows_for_app(a)]
    playing = [s for s in mediastate.sessions() if s["status"] == "Playing"]
    if open_apps or playing:
        return "WARN", f"close {', '.join(open_apps) or 'nothing'} and pause {len(playing)} playing session(s) first so results are unambiguous"
    return "PASS", "Calculator and Notepad closed, nothing playing"


def no_game():
    import psutil
    games = [p.info["name"] for p in psutil.process_iter(["name"]) if (p.info["name"] or "").lower() in ("r5apex.exe", "cod.exe", "codhq.exe")]
    return ("FAIL", "exit the game before audio tests: " + ", ".join(games)) if games else ("PASS", "no competitive game running")


def tests():
    out = subprocess.run([str(LIVE / ".venv/Scripts/python.exe"), "-m", "pytest", "tests/test_pc_control_reliability.py", "tests/test_webbrowse.py", "-q", "-p", "no:cacheprovider"],
                         cwd=ROOT, capture_output=True, text=True, env={**os.environ, "JARVIS_BROWSER_RUNTIME": r"C:\Users\you\ECC-browser-runtime"}, timeout=240)
    line = (out.stdout.strip().splitlines() or ["no output"])[-1]
    return ("PASS" if out.returncode == 0 else "FAIL"), line


if __name__ == "__main__":
    for name, fn in (("JARVIS service", service), ("code under test", code_under_test), ("GitHub backup", remote), ("microphone / speakers", audio),
                     ("Ollama models", ollama), ("browser runtime", browser_runtime), ("clean slate", clean_slate), ("no game running", no_game),
                     ("branch tests", tests)):
        check(name, fn)
    fails, warns = [r for r in rows if r[0] == "FAIL"], [r for r in rows if r[0] == "WARN"]
    print("\nGO for the physical tests." if not fails else "\nNO-GO: fix the FAIL items first.")
    if warns:
        print(f"{len(warns)} warning(s) above: none blocks the tests, but the WARN about the running checkout decides which features you can try today.")
    sys.exit(1 if fails else 0)
