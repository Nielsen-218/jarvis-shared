r"""Real-world acceptance of the gateway, with execution ON in THIS process only (production stays in shadow mode).

Three channels, same brain code: PC chat (Brain.ask in-process), phone (a second AppServer on JARVIS_APP_PORT=8766, /api/pair +
/api/chat), voice = SYNTHESIZED speech (Kokoro TTS audio straight into Whisper -> Brain.ask; no microphone, no speakers, no room:
it tests transcription and routing, not a physical microphone, which stays NOT RUN until the owner does it). The owner's
Notepad session is moved aside and verified byte-identical afterwards (see isolate_notepad_session). Truth is read from the PC itself
(windows, files, volume), never from the reply. Everything the run opens or creates is closed or removed afterwards.

    .venv\Scripts\python.exe scripts\gateway_acceptance.py [--channels=pc,phone,voice] [--only=notepad,hog] [--launch]
                                                           [--procedures] [--case-budget=120] [--suite-budget=900]

The runner enforces its own limits (2026-11-06: a run hung 5 h in one case and nobody was told):
  * every case runs on a worker thread with a budget (default 120 s); past it the case is BLOCKED, every thread's stack is
    written to runs/, the brain is cancelled and replaced, and the run goes on;
  * the whole suite has a budget (default 15 min); cases after it are NOT RUN;
  * a heartbeat every 30 s names the case, its elapsed time and its last step; 60 s without a step starting or finishing
    (a heartbeat or a repeated sentence is not progress) writes the stacks once, so the wait state is on disk;
  * results are written after EVERY case, so a killed run still leaves them.
Statuses: PASS (the PC shows it was done), FAIL (it was not), BLOCKED (timed out / needs the owner), NOT RUN.

--launch      also answer the Shadow World question and let the Unreal Editor open (owner present only).
--procedures  follow a jq guide, start a NEW brain (restart), and ask for jq in new words (installs jq with winget).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ["JARVIS_APP_PORT"] = "8766"
os.environ["JARVIS_GATEWAY"] = "on"
os.environ["JARVIS_PUSH"] = "off"
# Its test commands run Brain.ask on the real data folder as the owner: they must never become his thread (core.thread),
# shown to the phone as what he asked at the PC.
os.environ["JARVIS_THREAD"] = "off"

from jarvis_pc.uiact import uia as _uia  # noqa: E402

_uia.ensure_mta()                        # before anything touches pywinauto (the 2026-11-06 hang)

import core.config  # noqa: E402,F401
from core import config, gateway, intelligence, pcmethods, users  # noqa: E402
from tools import load_all  # noqa: E402

load_all()
OWNER = next(uid for uid, u in users.all_users().items() if u.get("role") == "owner")
users.set_current(OWNER)


def _arg(name, default):
    return next((a.split("=", 1)[1] for a in sys.argv if a.startswith(f"--{name}=")), default)


CHANNELS = [c for c in _arg("channels", "pc,phone,voice").split(",") if c]
ONLY = [w.strip().casefold() for w in _arg("only", "").split(",") if w.strip()]
CASE_BUDGET = float(_arg("case-budget", "120"))
SUITE_BUDGET = float(_arg("suite-budget", "900"))
LAUNCH = "--launch" in sys.argv
PROCEDURES = "--procedures" in sys.argv
STAMP = time.strftime("%Y%m%d-%H%M")
HOME = os.path.expanduser("~")
# Raw results hold real window titles, folder names and replies from the owner's PC: they stay in local storage OUTSIDE
# the repository (PR #216 review); only sanitized summaries are committed (docs/evidence/).
OUT = Path(os.environ.get("JARVIS_EVIDENCE_DIR") or os.path.join(os.environ.get("LOCALAPPDATA", str(ROOT)), "JarvisAcceptance", "runs"))
OUT.mkdir(parents=True, exist_ok=True)
SANDBOX = os.path.join(HOME, "Documents", "JarvisAcceptance")       # the disposable folder the safe cases work in
MARKER = os.path.join(SANDBOX, ".jarvis-acceptance")
PROJECT = os.path.join(SANDBOX, "Quillmere Atlas", "QuillmereAtlas.blend")
BLENDER = r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe"
ROWS: list[dict] = []
SUITE_T0 = time.time()


# ----------------------------------------------------------------------------------------------------- progress + evidence
NOW = {"case": None, "t0": 0.0, "last": "", "last_t": 0.0, "dumped": False, "thread": None, "steps": [], "plan": None}
_print_lock = threading.Lock()


def log(msg):
    with _print_lock:
        print(msg, flush=True)


def progress(msg):
    """A step started or finished, or the request was understood: the only things that count as progress."""
    NOW["last"], NOW["last_t"] = msg, time.time()


def dump_stacks(label, why) -> str:
    path = OUT / f"gateway-acceptance-{STAMP}-{re.sub(r'[^a-z0-9]+', '-', label.casefold()).strip('-')}-stacks.txt"
    names = {t.ident: t.name for t in threading.enumerate()}
    lines = [f"{why} | case {label} | elapsed {time.time() - NOW['t0']:.0f}s | last step: {NOW['last']}", ""]
    for ident, frame in sys._current_frames().items():
        lines.append(f"--- thread {names.get(ident, ident)}")
        lines += [ln.rstrip() for ln in traceback.format_stack(frame)]
    path.write_text("\n".join(lines), encoding="utf-8")
    return str(path)


def wait_state(thread) -> str:
    frame = sys._current_frames().get(getattr(thread, "ident", None))
    if frame is None:
        return "thread gone"
    return " <- ".join(f"{os.path.basename(f.filename)}:{f.lineno} {f.name}" for f in reversed(traceback.extract_stack(frame)[-4:]))


def heartbeat():
    while True:
        time.sleep(30)
        if not NOW["case"]:
            continue
        idle = time.time() - (NOW["last_t"] or NOW["t0"])
        log(f"[heartbeat] {NOW['case']}: {time.time() - NOW['t0']:.0f}s elapsed; last step: {NOW['last'] or 'none yet'} ({idle:.0f}s ago)")
        if idle >= 60 and not NOW["dumped"]:
            NOW["dumped"] = True
            path = dump_stacks(NOW["case"], f"no progress for {idle:.0f}s")
            log(f"[heartbeat] no progress for {idle:.0f}s; waiting in: {wait_state(NOW['thread'])}; stacks -> {path}")


threading.Thread(target=heartbeat, daemon=True, name="acceptance-heartbeat").start()

# what the executor did, step by step (harness-only instrumentation of the real executor)
_invoke, _decide = pcmethods.invoke, gateway.decide


def _traced_invoke(method, step, approved, cancelled):
    tool = step["tool"]
    progress(f"{tool} started")
    try:
        out = _invoke(method, step, approved, cancelled)
    except BaseException as e:  # noqa: BLE001
        NOW["steps"].append(f"{tool}: {type(e).__name__} {str(e)[:90]}")
        progress(f"{tool} failed")
        raise
    NOW["steps"].append(f"{tool}: {'verified' if out.get('verified') else 'UNVERIFIED'}")
    progress(f"{tool} {'verified' if out.get('verified') else 'unverified'}")
    return out


def _traced_decide(*a, **k):
    d = _decide(*a, **k)
    if d is not None:
        NOW["plan"] = {"source": d.source, "model": d.model, "covers": d.covers, "attempts": d.attempts,
                       "intents": [{"kind": i.kind, "skill": i.skill, "values": i.values, "steps": i.steps, "q": i.question} for i in d.intents]}
        progress(f"understood ({len(d.intents)} intent(s))")
    return d


pcmethods.invoke = _traced_invoke
gateway.decide = _traced_decide


class Timeout(Exception):
    pass


def bounded(fn, seconds, name):
    box = {}

    def run():
        try:
            box["v"] = fn()
        except BaseException as e:  # noqa: BLE001
            box["e"] = e
    t = threading.Thread(target=run, daemon=True, name=f"acceptance-{name}")
    t.start()
    NOW["thread"] = t
    t.join(seconds)
    if t.is_alive():
        raise Timeout(t)
    if "e" in box:
        raise box["e"]
    return box.get("v")


# ----------------------------------------------------------------------------------------------------- truth from the PC
def windows():
    from tools import pcops
    return [w for w in pcops.windows() if w.get("title")]


def window_titled(*words):
    low = [w.casefold() for w in words]
    return next((w for w in windows() if all(x in w["title"].casefold() for x in low)), None)


def notepad_text():
    try:
        w = window_titled("notepad")
        if not w:
            return ""
        win = _uia.window(w["title"])
        for kind in ("Document", "Edit"):
            for c in win.descendants(control_type=kind):
                for getter in (lambda: c.get_value(), lambda: "\n".join(c.texts()), lambda: c.window_text()):
                    try:
                        v = getter()
                        if v:
                            return str(v)
                    except Exception:  # noqa: BLE001
                        continue
    except Exception:  # noqa: BLE001
        pass
    return ""


def _pids(name):
    out = subprocess.run(["powershell", "-NoProfile", "-Command", f"(Get-Process {name} -ErrorAction SilentlyContinue).Id"],
                         capture_output=True, text=True, timeout=30).stdout
    return {int(x) for x in out.split() if x.strip().isdigit()}


PREEXISTING = {n: _pids(n) for n in ("Notepad", "mspaint")}   # the owner's own windows: never closed by this run
_TEST_TEXT = ("hello world", "hello from jarvis", "jarvis-test", "mountains", "jarvisacceptance", "documents\\hello.txt",
              str(time.localtime().tm_year) + "-", "javastest")

# ---- the owner's Notepad session is never touched by a test (PR #216 review, item 4)
# Windows 11 Notepad restores the last session's tabs (unsaved text included) at every launch, so a test Notepad would open
# on the owner's documents. Before any Notepad case the session folders are MOVED to local storage outside the repository,
# with a SHA-256 manifest; test Notepads run on an empty, disposable session that is wiped between cases; at the end the test
# session is deleted, the owner's is moved back and every file is verified byte-identical. A run killed half-way leaves a
# restore-pending marker that the next run (or `--restore-notepad` alone) restores first.
NOTEPAD_STATE = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Packages", "Microsoft.WindowsNotepad_8wekyb3d8bbwe", "LocalState")
SESSION_DIRS = ("TabState", "WindowState")
SECURE = os.path.join(os.environ.get("LOCALAPPDATA", ""), "JarvisAcceptance")      # outside the repo, never published
PENDING = os.path.join(SECURE, "notepad-restore-pending.json")
ISOLATION: dict = {}
NOTEPAD_CASES = {"notepad type and save", "failure then recovery", "explorer + terminal + notepad", "unfamiliar: date in notepad",
                 "interruption then next request"}


def _manifest(root: str) -> dict:
    import hashlib
    out = {}
    for d in SESSION_DIRS:
        base = os.path.join(root, d)
        for dirpath, _, files in os.walk(base):
            for f in files:
                p = os.path.join(dirpath, f)
                out[os.path.relpath(p, root)] = hashlib.sha256(Path(p).read_bytes()).hexdigest()
    return out


def restore_notepad_session() -> str:
    """Put the owner's Notepad session back exactly as it was and prove it. Only with no Notepad running."""
    if not os.path.isfile(PENDING):
        return "nothing to restore"
    info = json.loads(Path(PENDING).read_text(encoding="utf-8"))
    if _pids("Notepad") - PREEXISTING.get("Notepad", set()):
        kill_now("Notepad")
    if _pids("Notepad"):
        return "NOT restored: a Notepad is running (restore later with --restore-notepad)"
    for d in SESSION_DIRS:
        live, saved = os.path.join(NOTEPAD_STATE, d), os.path.join(info["backup"], d)
        if os.path.isdir(live):
            shutil.rmtree(live)                     # the test session only: the owner's is in the backup
        if os.path.isdir(saved):
            shutil.move(saved, live)
    now = _manifest(NOTEPAD_STATE)
    same = now == info["manifest"]
    if same:
        os.remove(PENDING)
        shutil.rmtree(info["backup"], ignore_errors=True)
        return f"owner Notepad session restored: {len(now)} files, all byte-identical"
    return f"RESTORED BUT DIFFERENT: {len(now)} files vs {len(info['manifest'])} saved (backup kept in {info['backup']})"


def isolate_notepad_session() -> str:
    if os.path.isfile(PENDING):
        log("[notepad] a previous run left the owner's session aside: " + restore_notepad_session())
    if PREEXISTING["Notepad"] or _pids("Notepad"):
        return "NOT isolated: Notepad is open (the owner's documents); Notepad cases will be NOT RUN"
    if not os.path.isdir(NOTEPAD_STATE):
        ISOLATION["on"] = True
        return "isolated (Notepad has no saved session)"
    manifest = _manifest(NOTEPAD_STATE)
    backup = os.path.join(SECURE, f"notepad-session-{STAMP}")
    os.makedirs(backup, exist_ok=True)
    Path(PENDING).write_text(json.dumps({"backup": backup, "manifest": manifest, "stamp": STAMP}), encoding="utf-8")
    for d in SESSION_DIRS:
        if os.path.isdir(os.path.join(NOTEPAD_STATE, d)):
            shutil.move(os.path.join(NOTEPAD_STATE, d), os.path.join(backup, d))
    ISOLATION["on"] = True
    import atexit
    atexit.register(lambda: os.path.isfile(PENDING) and log("[notepad] at exit: " + restore_notepad_session()))
    return f"isolated: the owner's session ({len(manifest)} files) moved to local storage outside the repo"


def wipe_test_session() -> None:
    """Between Notepad cases: the disposable session goes, so no case starts on a tab an earlier case left."""
    if ISOLATION.get("on") and not _pids("Notepad"):
        for d in SESSION_DIRS:
            shutil.rmtree(os.path.join(NOTEPAD_STATE, d), ignore_errors=True)


def _has_test_text(data: bytes) -> bool:
    text = (data.decode("utf-16-le", "ignore") + data[1:].decode("utf-16-le", "ignore") + data.decode("latin-1")).casefold()
    return any(t in text for t in _TEST_TEXT)


_WATCHED = [os.path.expanduser("~")] + [os.path.join(os.path.expanduser("~"), d) for d in ("Documents", "Downloads")]
ENTRIES_BEFORE = {d: set(os.listdir(d)) for d in _WATCHED if os.path.isdir(d)}


def remove_run_leftovers():
    """What a misheard name left behind ('Hello TXT.txt', 'javastest'): only NEW entries, and only an empty folder or a
    small file holding test text. Anything else new is reported, not touched."""
    out = []
    for d, before in ENTRIES_BEFORE.items():
        for name in set(os.listdir(d)) - before:
            p = os.path.join(d, name)
            try:
                if os.path.isdir(p) and not os.listdir(p):
                    os.rmdir(p); out.append("removed empty folder " + p)
                elif os.path.isfile(p) and os.path.getsize(p) <= 4096 and _has_test_text(Path(p).read_bytes()):
                    os.remove(p); out.append("removed test file " + p)
                elif name != "JarvisAcceptance":
                    out.append("left (not clearly the run's): " + p)
            except OSError as e:
                out.append(f"could not remove {p}: {e}")
    return out


def kill_now(*names):
    for n in names:
        for pid in _pids(n) - PREEXISTING.get(n, set()):
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, timeout=30)
    time.sleep(1.5)


def kill(*names):
    """Close what THIS run opened (a Notepad or Paint open before the run is left alone); a closed test Notepad's
    disposable session is wiped so the next case starts empty."""
    kill_now(*names)
    if any(n.casefold() == "notepad" for n in names):
        wipe_test_session()


def volume():
    from core import pcactions
    return int(re.search(r"(\d{1,3})", pcactions.perform("get_volume", {})["detail"]).group(1))


def minimize(title_word):
    import ctypes
    w = window_titled(title_word)
    if w:
        ctypes.windll.user32.ShowWindow(w["hwnd"], 6)       # SW_MINIMIZE
        time.sleep(0.8)


def blender_pids():
    out = subprocess.run(["powershell", "-NoProfile", "-Command", "(Get-Process blender -ErrorAction SilentlyContinue).Id"],
                         capture_output=True, text=True, timeout=30).stdout
    return {int(x) for x in out.split() if x.strip().isdigit()}


# ----------------------------------------------------------------------------------------------------- channels
_brains: dict = {}


def brain_for(key="main"):
    from brain.llm import Brain
    from tools import memory
    if key not in _brains:
        b = Brain(config.Config.model, config.Config.effort, "You are JARVIS, a precise assistant.", uid=OWNER)
        b.context = memory.context_block
        _brains[key] = b
    return _brains[key]


def pc_chat(text, key="main"):
    said = []
    brain_for(key).ask(text, said.append)
    return " ".join(said)


_server = {}


def phone(text):
    if "token" not in _server:
        from core.appserver import AppServer
        from tools import voice
        AppServer(lambda uid: brain_for("phone"), None).start()
        voice.pair_codes["654321"] = OWNER
        time.sleep(2)
        r = _post("/api/pair", {"code": "654321", "device": "claude-verify"})
        _server["token"] = r.get("token")
    r = _post("/api/chat", {"text": text, "speak": False}, _server["token"])
    return r.get("reply") or r.get("text") or ""


def _post(path, body, token=None):
    req = urllib.request.Request("http://127.0.0.1:8766" + path, json.dumps(body).encode(),
                                 {"Content-Type": "application/json", **({"Authorization": "Bearer " + token} if token else {})})
    return json.load(urllib.request.urlopen(req, timeout=CASE_BUDGET))


_stt = {}


def voice_ready():
    from audio.stt import Transcriber
    from audio.tts import kokoro_say
    if "stt" not in _stt:
        _stt["stt"] = Transcriber(config.Config.whisper_model, config.Config.whisper_device)
        kokoro_say("ready", config.Config.voice, config.Config.voice_rate)


def voice(text):
    import numpy as np
    from scipy.signal import resample_poly
    from audio.tts import kokoro_say
    voice_ready()
    audio = kokoro_say(text, config.Config.voice, config.Config.voice_rate)
    samples = resample_poly(audio.astype(np.float32), 2, 3)
    heard = _stt["stt"].transcribe(np.clip(samples, -32768, 32767).astype(np.int16)).strip()
    progress(f"heard {heard[:40]!r}")
    return pc_chat(heard, key="voice"), heard


SEND = {"pc": lambda t: (pc_chat(t), t), "phone": lambda t: (phone(t), t), "voice": voice}
VOICE_MODE = ("voice channel = SYNTHESIZED speech: Kokoro TTS audio fed straight into Whisper STT, no microphone, no speakers, "
              "no room noise")
BRAIN_KEY = {"pc": "main", "phone": "phone", "voice": "voice"}


# ----------------------------------------------------------------------------------------------------- one case
def write_results():
    counted = [r for r in ROWS if r["channel"] != "-"]
    summary = {"stamp": STAMP, "channels": CHANNELS, "only": ONLY, "cases": len(counted),
               **({"voice_mode": VOICE_MODE} if "voice" in CHANNELS else {}),
               "by_status": {s: sum(1 for r in counted if r["status"] == s) for s in ("PASS", "FAIL", "BLOCKED", "NOT RUN")},
               "suite_seconds": round(time.time() - SUITE_T0, 1)}
    (OUT / f"gateway-acceptance-{STAMP}.json").write_text(json.dumps({"summary": summary, "rows": ROWS}, indent=1, ensure_ascii=False), encoding="utf-8")
    md = [f"# Gateway acceptance {STAMP}", "", "```", json.dumps(summary), "```", "",
          "| status | channel | case | s | said | reply | truth (from the PC) | steps |", "|---|---|---|---|---|---|---|---|"]
    md += [f"| {r['status']} | {r['channel']} | {r['label']} | {r['seconds']} | {r['text'][:60]} | {r['reply'][:120].replace('|', '/')} | "
           f"{r['truth'][:140].replace('|', '/')} | {'; '.join(r.get('steps') or [])[:160].replace('|', '/')} |" for r in ROWS]
    (OUT / f"gateway-acceptance-{STAMP}.md").write_text("\n".join(md), encoding="utf-8")
    return summary


def selected(label):
    return not ONLY or any(w in label.casefold() for w in ONLY)


def row(channel, label, text, status, seconds=0.0, reply="", heard="", truth="", evidence=""):
    r = {"status": status, "channel": channel, "label": label, "text": text, "heard": heard, "reply": (reply or "")[:600],
         "seconds": round(seconds, 1), "truth": str(truth)[:400], "steps": list(NOW["steps"]), "plan": NOW["plan"], "evidence": evidence}
    ROWS.append(r)
    log(f"{status:7s} [{channel:5s}] {label:34s} {seconds:6.1f}s | {text[:48]!r} -> {(reply or '')[:110]!r} | {str(truth)[:90]}"
        + (f" | steps: {'; '.join(NOW['steps'])[:140]}" if NOW["steps"] else "") + (f" | evidence {evidence}" if evidence else ""))
    write_results()
    return r


def case(channel, label, text, check, *, setup=None, cleanup=None, settle=3.0, budget=None, run=None):
    """Setup, send, settle, check, clean up: each bounded. `run` replaces the plain send for a scripted case."""
    if channel not in CHANNELS or not selected(label):
        return None
    if time.time() - SUITE_T0 > SUITE_BUDGET:
        return row(channel, label, text, "NOT RUN", truth=f"suite budget of {SUITE_BUDGET:.0f}s used up")
    if label in NOTEPAD_CASES and not ISOLATION.get("on"):
        return row(channel, label, text, "NOT RUN", truth="the owner's Notepad session could not be isolated (Notepad open)")
    budget = budget or CASE_BUDGET
    NOW.update(case=f"[{channel}] {label}", t0=time.time(), last="", last_t=0.0, dumped=False, steps=[], plan=None)
    log(f"--- [{channel}] {label} (budget {budget:.0f}s)")
    try:
        if setup:
            bounded(setup, 60, "setup")
    except Timeout:
        NOW["case"] = None
        return row(channel, label, text, "BLOCKED", truth="setup did not finish in 60 s", evidence=dump_stacks(label, "setup timeout"))
    except Exception as e:  # noqa: BLE001
        NOW["case"] = None
        return row(channel, label, text, "BLOCKED", truth=f"setup failed: {type(e).__name__}: {e}")
    t0 = time.time()
    status, evidence = None, ""
    try:
        reply, heard = bounded(run or (lambda: SEND[channel](text)), budget, label[:20])
    except Timeout as t:
        evidence = dump_stacks(label, f"case budget {budget:.0f}s exceeded")
        log(f"[timeout] {label}: still running after {budget:.0f}s, waiting in: {wait_state(t.args[0])}")
        b = _brains.pop(BRAIN_KEY.get(channel, "main"), None)      # cancel it, and the next case gets a fresh brain (lock)
        if b is not None:
            b.cancel("")
        reply, heard, status = f"(no reply within {budget:.0f}s)", text, "BLOCKED"
    except Exception as e:  # noqa: BLE001
        reply, heard = f"ERROR {type(e).__name__}: {e}", text
    dt = time.time() - t0
    time.sleep(settle)
    try:
        ok, truth = bounded(lambda: check(reply or ""), 30, "check")
    except Timeout:
        ok, truth = False, "the check itself did not finish in 30 s"
    except Exception as e:  # noqa: BLE001
        ok, truth = False, f"check failed: {type(e).__name__}: {e}"
    if status == "BLOCKED":
        truth = f"timed out after {budget:.0f}s at: {NOW['last'] or 'before the first step'}; " + str(truth)
    r = row(channel, label, text, status or ("PASS" if ok else "FAIL"), dt, reply, heard, truth, evidence)
    NOW["case"] = None
    if cleanup:
        try:
            bounded(cleanup, 60, "cleanup")
        except BaseException as e:  # noqa: BLE001
            log(f"[cleanup] {label}: {type(e).__name__} {e}")
    return r


# ----------------------------------------------------------------------------------------------------- sandbox
def make_sandbox():
    os.makedirs(SANDBOX, exist_ok=True)
    Path(MARKER).write_text("created by scripts/gateway_acceptance.py; removed at the end of the run", encoding="utf-8")
    for name, body in (("notes-alpha.txt", "alpha"), ("notes-beta.txt", "beta"), ("diagram.png", "")):
        Path(SANDBOX, name).write_text(body, encoding="utf-8")
    if not os.path.exists(PROJECT) and os.path.exists(BLENDER):
        os.makedirs(os.path.dirname(PROJECT), exist_ok=True)
        subprocess.run([BLENDER, "-b", "--factory-startup", "--python-expr", f"import bpy; bpy.ops.wm.save_as_mainfile(filepath=r'{PROJECT}')"],
                       capture_output=True, timeout=90)


def remove_sandbox():
    if os.path.isfile(MARKER):                     # only the folder this harness made
        shutil.rmtree(SANDBOX, ignore_errors=True)


def close_new_blender(before):
    import ctypes
    new = blender_pids() - before
    for w in windows():
        if w.get("pid") in new:
            ctypes.windll.user32.PostMessageW(w["hwnd"], 0x0010, 0, 0)   # WM_CLOSE
    deadline = time.time() + 10
    while time.time() < deadline and (blender_pids() & new):
        time.sleep(0.5)
    for pid in blender_pids() & new:
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, timeout=30)


# ----------------------------------------------------------------------------------------------------- the run
def main():
    if "--restore-notepad" in sys.argv:            # recovery only: put the owner's Notepad session back and stop
        log(restore_notepad_session())
        return
    log(f"gateway acceptance {STAMP}: channels={CHANNELS} only={ONLY or 'all'} case budget {CASE_BUDGET:.0f}s suite budget {SUITE_BUDGET:.0f}s "
        f"launch={LAUNCH} fast={intelligence.model('fast')} advanced={intelligence.model('advanced')}")
    start_volume = volume()
    bounded(make_sandbox, 120, "sandbox")
    log(f"sandbox {SANDBOX}; throwaway project exists: {os.path.exists(PROJECT)}; volume {start_volume}%")
    cold = warm = None
    if selected("latency"):
        url, opener = intelligence._ollama_endpoint()
        intelligence._unload(intelligence.model("advanced"), url, opener)
        time.sleep(2)
        t0 = time.time(); bounded(lambda: _decide("open notepad and type hello world"), 120, "cold"); cold = time.time() - t0
        t0 = time.time(); bounded(lambda: _decide("open paint and tell me how much free space I have"), 120, "warm"); warm = time.time() - t0
        log(f"understanding latency: cold {cold:.1f}s (model load + first prompt), warm {warm:.1f}s")
        ROWS.append({"status": "PASS", "channel": "-", "label": "latency cold/warm", "text": "", "heard": "", "reply": "", "seconds": round(cold, 1),
                     "truth": f"cold {cold:.1f}s warm {warm:.1f}s", "steps": [], "plan": None, "evidence": ""})

    hello = os.path.join(HOME, "Documents", "hello.txt")
    jt = os.path.join(HOME, "Downloads", "jarvis-test")
    reports = os.path.join(SANDBOX, "reports")
    try:
        if any(selected(c) for c in NOTEPAD_CASES):
            log("[notepad] " + isolate_notepad_session())
        for ch in CHANNELS:
            if ch == "voice":
                try:
                    t0 = time.time(); bounded(voice_ready, 180, "voice-warm-up")
                    log(f"voice models ready in {time.time() - t0:.1f}s (not counted in case times)")
                except BaseException as e:  # noqa: BLE001
                    row(ch, "voice warm-up", "", "BLOCKED", truth=f"Kokoro/Whisper did not load: {type(e).__name__} {e}")
                    continue
            # 1. a real project with two candidates on this PC -> the gateway must list both and ask, never guess
            def check_project(r):
                found = "livingshadowworld" in r.casefold() and ("which" in r.casefold() or "shadowworld.uproject" in r.casefold())
                return found, "reply lists LivingShadowWorld.uproject and asks which one" if found else "no candidates in reply"
            case(ch, "find Shadow World project", "Hey JARVIS, find my Shadow World project and launch it.", check_project, settle=1)
            if LAUNCH and ch == "pc":
                case(ch, "launch the chosen project", "the living one",
                     lambda r: (window_titled("livingshadowworld") is not None, f"window: {window_titled('livingshadowworld')}"), settle=5, budget=200)
            gateway._CHOICES.clear()
            # 2. an unfamiliar throwaway project: discover it, pick the launch method, open it, verify the window
            before = set()

            def setup_project():
                before.clear(); before.update(blender_pids())
                if not os.path.exists(PROJECT):
                    raise RuntimeError("the throwaway .blend could not be made")
            case(ch, "throwaway project find+open", "find my Quillmere Atlas project and open it",
                 lambda r: (window_titled("quillmereatlas") is not None, f"window: {(window_titled('quillmereatlas') or {}).get('title')}"),
                 setup=setup_project, cleanup=lambda: close_new_blender(before), settle=2, budget=150)
            # 3. notepad: open, type, save -> every part
            def check_notepad_save(r):
                text = notepad_text()
                content = open(hello, encoding="utf-8", errors="replace").read() if os.path.isfile(hello) else ""
                return "hello world" in content.casefold(), f"hello.txt exists={os.path.isfile(hello)} contains hello world={'hello world' in content.casefold()}; notepad shows {text[:30]!r}"

            def clean_notepad():
                kill("Notepad")
                if os.path.isfile(hello) and time.time() - os.path.getmtime(hello) < 600:
                    os.remove(hello)
            case(ch, "notepad type and save", "Open Notepad, type hello world, and save it as hello.txt.", check_notepad_save,
                 setup=lambda: (kill("Notepad"), os.path.isfile(hello) and os.remove(hello)), cleanup=clean_notepad, settle=2)
            # 4. natural-language file discovery in the disposable folder
            case(ch, "file discovery", "which text files are in my JarvisAcceptance folder in Documents?",
                 lambda r: ("notes-alpha" in r.casefold() and "notes-beta" in r.casefold(), "reply names notes-alpha.txt and notes-beta.txt"), settle=0.5)
            # 5. a folder operation through the terminal gate, in the disposable folder
            case(ch, "folder op via terminal", "create a folder called reports inside my JarvisAcceptance folder in Documents",
                 lambda r: (os.path.isdir(reports), f"{reports} exists={os.path.isdir(reports)}"),
                 setup=lambda: os.path.isdir(reports) and os.rmdir(reports), cleanup=lambda: os.path.isdir(reports) and os.rmdir(reports), settle=0.5)
            # 6. a parameterized stored skill (a learned playbook with a {dir} value)
            case(ch, "stored skill with a value", "how many files are in my JarvisAcceptance folder in Documents?",
                 lambda r: (str(len(os.listdir(SANDBOX))) in r or str(len([n for n in os.listdir(SANDBOX) if os.path.isfile(os.path.join(SANDBOX, n))])) in r,
                            f"real count {len(os.listdir(SANDBOX))} entries; route {[(i['kind'], i['skill']) for i in (NOW['plan'] or {}).get('intents', [])]}"),
                 settle=0.5)
            # 7. slow computer -> an investigation with facts
            case(ch, "computer running slow", "My computer is running slow.", lambda r: (bool(re.search(r"\bCPU\b.*%|memory .*%", r)), "facts in reply" if re.search(r"CPU|memory", r) else "no facts"), settle=0.5)
            # 8. screen understanding, then screen observation followed by an action
            case(ch, "look at my screen", "Look at my screen and tell me what's open.",
                 lambda r: (any(w["title"][:12].casefold() in r.casefold() for w in windows() if len(w["title"]) >= 12), "names a real window"), settle=0.5)
            # (closing an app the owner only DESCRIBED, "the drawing app", asks once by design: close is scoped to named targets)
            case(ch, "screen then action", "look at my screen and if Paint is open, close it",
                 lambda r: (window_titled("paint") is None, f"paint still open: {bool(window_titled('paint'))}"),
                 setup=lambda: (kill("mspaint"), subprocess.Popen(["mspaint.exe"]), time.sleep(3)), cleanup=lambda: kill("mspaint"), settle=2)
            # 9. a realistic failure and the recovery: Notepad is minimized, so the first typing attempt finds no window
            case(ch, "failure then recovery", "type hello from jarvis into notepad",
                 lambda r: ("hello from jarvis" in notepad_text().casefold(),
                            f"notepad shows {notepad_text()[:40]!r}; a step failed first: {any('Error' in s or 'UNVERIFIED' in s for s in NOW['steps'])}"),
                 setup=lambda: (kill("Notepad"), subprocess.Popen(["notepad.exe"]), time.sleep(2.5), minimize("notepad")), cleanup=lambda: kill("Notepad"), settle=2)
            # 10. multi-step across Explorer, terminal and a GUI app
            def check_multi(r):
                exists = os.path.isdir(jt)
                text = notepad_text()
                return exists and "jarvis-test" in text.casefold(), f"folder exists={exists}; notepad shows {text[:70]!r}"

            def clean_multi():
                kill("Notepad")
                if os.path.isdir(jt) and not os.listdir(jt):
                    os.rmdir(jt)
                subprocess.run(["powershell", "-NoProfile", "-Command", "(New-Object -ComObject Shell.Application).Windows() | Where-Object { $_.LocationName -eq 'Downloads' } | ForEach-Object { $_.Quit() }"],
                               capture_output=True, timeout=30)
            case(ch, "explorer + terminal + notepad", "Open my Downloads folder, create a folder called jarvis-test in it from the terminal, then open Notepad and type that folder's path.",
                 check_multi, setup=lambda: (kill("Notepad"), os.path.isdir(jt) and os.rmdir(jt)), cleanup=clean_multi, settle=2)
            # 11. unfamiliar wordings
            case(ch, "unfamiliar: draw thing", "bring up the thing I use to draw", lambda r: (window_titled("paint") is not None, f"paint window: {bool(window_titled('paint'))}"), cleanup=lambda: kill("mspaint"), settle=2)
            case(ch, "unfamiliar: date in notepad", "open notepad and write today's date in it",
                 lambda r: (bool(re.search(str(time.localtime().tm_year), notepad_text())), f"notepad shows {notepad_text()[:40]!r}"), setup=lambda: kill("Notepad"), cleanup=lambda: kill("Notepad"), settle=2)
            case(ch, "unfamiliar: sound then app", "turn the sound way down then open paint",
                 lambda r: (volume() < start_volume and window_titled("paint") is not None, f"volume {volume()} (was {start_volume}); paint {bool(window_titled('paint'))}"),
                 cleanup=lambda: (kill("mspaint"), __import__("core.pcactions", fromlist=["x"]).perform("set_volume", {"percent": start_volume})), settle=2)
            def check_hog(r):
                """Truth from the disk: the Downloads item using the most space, and the folder holding the biggest file."""
                dl = os.path.join(HOME, "Downloads")
                sizes, biggest = {}, (0, "")
                for top in os.scandir(dl):
                    total = 0
                    for root, _, files in (os.walk(top.path) if top.is_dir() else [(dl, [], [top.name])]):
                        for f in files:
                            try:
                                s = os.path.getsize(os.path.join(root, f))
                            except OSError:
                                continue
                            total += s
                            if s > biggest[0]:
                                biggest = (s, root)
                    sizes[top.name] = total
                top_item = max(sizes, key=sizes.get) if sizes else ""
                holder = os.path.relpath(biggest[1], dl).split(os.sep)[0] if biggest[1] and biggest[1] != dl else ""
                named = [n for n in (top_item, holder) if n and n.casefold()[:20] in r.casefold()]
                return bool(named), f"largest item '{top_item}' ({sizes.get(top_item, 0) / 2**30:.1f} GB); biggest file under '{holder}'; reply names: {named}"
            case(ch, "unfamiliar: downloads hog", "what's hogging space in my downloads", check_hog, settle=0.5)
            # 12. contextual follow-up
            case(ch, "follow-up: open paint", "open paint", lambda r: (window_titled("paint") is not None, "paint open"), settle=2)
            case(ch, "follow-up: close it", "close it", lambda r: (window_titled("paint") is None, f"paint open after: {bool(window_titled('paint'))}"), cleanup=lambda: kill("mspaint"), settle=2)
            # 13. interruption: cancel mid-plan, the cancelled turn ends, then a new request works
            if ch == "pc":
                mt = os.path.join(HOME, "Documents", "mountains.txt")

                def interruption():
                    b = brain_for("main")
                    said = []
                    t = threading.Thread(target=lambda: b.ask("open notepad and type a long paragraph about mountains then save it as mountains.txt", said.append), daemon=True)
                    t.start(); time.sleep(0.8); b.cancel("")
                    t0 = time.time(); t.join(45)
                    ended = not t.is_alive()
                    progress(f"cancelled turn ended={ended} in {time.time() - t0:.1f}s")
                    after = pc_chat("open paint")
                    return f"{' '.join(said)[:160]} || ended={ended} after cancel in {time.time() - t0:.1f}s || {after}", "cancel; open paint"
                case(ch, "interruption then next request", "cancel mid-plan; open paint",
                     lambda r: (window_titled("paint") is not None and "ended=True" in r and not os.path.exists(mt),
                                f"paint after: {window_titled('paint') is not None}; mountains.txt exists: {os.path.exists(mt)}; {r[:120]}"),
                     setup=lambda: kill("Notepad", "mspaint"), cleanup=lambda: (kill("mspaint", "Notepad"), os.path.exists(mt) and os.remove(mt)), run=interruption, settle=2)
        # 14. learned procedure across a restart (a new Brain instance = a restart of the conversation and routing state)
        if PROCEDURES and "pc" in CHANNELS:
            guide = ("Jarvis, set up jq on this PC and make sure it works:\n```\nwinget install --id jqlang.jq -e --accept-source-agreements --accept-package-agreements\njq --version\n```")

            def procedure():
                r1 = pc_chat(guide)
                r2 = pc_chat("yes")
                _brains.pop("main", None)
                r3 = pc_chat("get jq going on this machine")
                return r1[:120] + " || " + r2[:120] + " || " + r3[:200], "guide -> yes -> new brain -> new words"
            case("pc", "learned procedure after restart", "guide -> yes -> new brain -> new words",
                 lambda r: ("learned" in r.split("||")[-1].casefold() or "jq" in r.split("||")[-1].casefold(), r[-200:]), run=procedure, budget=600)
    finally:
        # ---- wrap up, whatever happened
        try:
            from core import pcactions
            pcactions.perform("set_volume", {"percent": start_volume})
            users.update(OWNER, devices=[d for d in users.get(OWNER).get("devices", []) if d.get("name") != "claude-verify"])
            kill("Notepad", "mspaint")
            remove_sandbox()
            log(f"[wrap-up] run leftovers: {remove_run_leftovers()}")
        except Exception as e:  # noqa: BLE001
            log(f"[wrap-up] {type(e).__name__}: {e}")
        try:
            ISOLATION["result"] = restore_notepad_session() if ISOLATION.get("on") else "not isolated"
            log(f"[wrap-up] {ISOLATION['result']}")
            status = ("NOT RUN" if not ISOLATION.get("on") else
                      "PASS" if "byte-identical" in ISOLATION["result"] or "nothing" in ISOLATION["result"] else "FAIL")
            ROWS.append({"status": status,
                         "channel": "-", "label": "owner Notepad session untouched", "text": "", "heard": "", "reply": "",
                         "seconds": 0, "truth": ISOLATION["result"], "steps": [], "plan": None, "evidence": ""})
        except Exception as e:  # noqa: BLE001
            log(f"[wrap-up] {type(e).__name__}: {e}")
        if "voice" in CHANNELS:
            ROWS.append({"status": "NOT RUN", "channel": "-", "label": "physical microphone test", "text": "", "heard": "",
                         "reply": "", "seconds": 0, "truth": VOICE_MODE + "; a real microphone/room test needs the owner", "steps": [],
                         "plan": None, "evidence": ""})
        summary = write_results()
        log("\n" + json.dumps(summary, indent=1))
        log(f"results: {OUT / f'gateway-acceptance-{STAMP}.md'}")


if __name__ == "__main__":
    main()
    os._exit(0)          # a step left running past its ceiling must not keep the test process alive
