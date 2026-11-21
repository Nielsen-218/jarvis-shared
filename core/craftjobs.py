"""Background craft jobs (rig / generate): one heavy job at a time, progress, cancel, and a GPU guard.

Heavy 3D work takes minutes; the voice turn must not block on it. A job runs on a worker thread, its subprocesses
(Blender, Hunyuan, Unreal) are tracked so "cancel" really kills them, and finishing notifies the owner."""
import json
import os
import subprocess
import threading
import time
import uuid

_lock = threading.RLock()
_jobs: dict[str, dict] = {}
_queue: list[str] = []
_running = {"id": None}
_tls = threading.local()
_quiet = threading.local()
KEEP = 20


def quiet_jobs():
    """Mark VI C12: jobs submitted on THIS thread inside the block are quiet: no Blender window opens when they finish and
    no push of their own is sent (a mission reports once, from the laptop). The owner's own requests are never quiet."""
    from contextlib import contextmanager

    @contextmanager
    def scope():
        before = getattr(_quiet, "on", False)
        _quiet.on = True
        try:
            yield
        finally:
            _quiet.on = before
    return scope()


def is_quiet() -> bool:
    """The job running on this thread (or being submitted from it) is quiet."""
    job = getattr(_tls, "job", None)
    return bool((job or {}).get("quiet") or getattr(_quiet, "on", False))


class Cancelled(Exception):
    pass


# ----------------------------------------------------------------------------- cancellable subprocess
def _kill_tree(proc):
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, timeout=20)
        else:
            proc.kill()
    except Exception:
        proc.kill()


def run(cmd, timeout=None, **kw):
    """Drop-in for subprocess.run(capture_output=True, text=True) that a job's cancel can interrupt."""
    job = getattr(_tls, "job", None)
    kw.pop("capture_output", None)
    kw.pop("check", None)
    text = kw.pop("text", True)
    if os.name == "nt":
        # 2026-11-10 review: Hunyuan's python.exe and blender.exe --background are console programs; started from pythonw
        # they would each open a visible console window in front (a quiet mission job must stay out of sight).
        kw["creationflags"] = kw.get("creationflags", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=text, **kw)
    if job:
        job["_procs"].append(p)
    t0 = time.time()
    try:
        while True:
            if job and job["_cancel"].is_set():
                _kill_tree(p)
                p.communicate()
                raise Cancelled()
            try:
                out, err = p.communicate(timeout=0.5)
                return subprocess.CompletedProcess(cmd, p.returncode, out, err)
            except subprocess.TimeoutExpired:
                if timeout and time.time() - t0 > timeout:
                    _kill_tree(p)
                    p.communicate()
                    raise subprocess.TimeoutExpired(cmd, timeout)
    finally:
        if job and p in job["_procs"]:
            job["_procs"].remove(p)


def check_cancel():
    """Raise Cancelled when the current job was cancelled (call between attempts, so a killed process is not retried)."""
    job = getattr(_tls, "job", None)
    if job and job["_cancel"].is_set():
        raise Cancelled()


def step(text):
    """Called from inside a job: what it is doing now."""
    job = getattr(_tls, "job", None)
    if job:
        job["step"] = text
        job["steps"].append((round(time.time() - job["started"], 1), text))


# ----------------------------------------------------------------------------- GPU guard
GAME_PROCESSES = ("eldenring", "cyberpunk2077", "fortniteclient", "valorant", "cs2", "gta5", "witcher3", "bg3", "helldivers2",
                  "-Win64-Shipping")


def gpu_state(gpu_fn=None, procs_fn=None, unload_fn=None, need_mb=7000):
    """{'ok': bool, 'say': str}. Frees Ollama's VRAM when that is what is in the way; refuses while something else
    (a game, another heavy job) holds the GPU. Injectable functions keep it testable without a GPU."""
    from tools import pcops
    gpu_fn = gpu_fn or pcops.gpu
    g = gpu_fn()
    if g is None:
        return {"ok": True, "say": "", "note": "no NVIDIA GPU info; continuing"}
    if g["free_mb"] >= need_mb:
        return {"ok": True, "say": "", "free_mb": g["free_mb"]}
    freed = (unload_fn or unload_ollama)()
    if freed:
        time.sleep(1.5)
        g = gpu_fn()
        if g["free_mb"] >= need_mb:
            return {"ok": True, "say": f"I unloaded my language model ({', '.join(freed)}) to make room on the graphics card.",
                    "free_mb": g["free_mb"], "unloaded": freed}
    games = (procs_fn or (lambda: pcops.processes_matching(*GAME_PROCESSES)))()
    if games or g.get("utilization_pct", 0) > 60:
        why = "a game or another heavy app is using it" if games else f"it is {g['utilization_pct']}% busy"
        return {"ok": False, "say": f"The graphics card is needed for this and {why}; only {g['free_mb']} MB is free. "
                                    "Close it or tell me to wait, and I will try again."}
    return {"ok": False, "say": f"Only {g['free_mb']} MB of graphics memory is free and this needs about {need_mb} MB. "
                                "Something is holding it; I did not touch it."}


def unload_ollama():
    """Ask Ollama to drop loaded models (keep_alive=0). They reload on demand, so nothing is lost. Returns names."""
    import urllib.request
    base = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434")
    base = base if base.startswith("http") else "http://" + base
    try:
        loaded = json.loads(urllib.request.urlopen(base + "/api/ps", timeout=3).read()).get("models", [])
        names = [m["name"] for m in loaded]
        for n in names:
            req = urllib.request.Request(base + "/api/generate", json.dumps({"model": n, "keep_alive": 0}).encode(),
                                         {"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=15).read()
        return names
    except Exception:
        return []


# ----------------------------------------------------------------------------- jobs
def submit(label, fn, uid=None, need_gpu_mb=0, wait=False, quiet=None):
    """Queue fn() -> str. Returns the job dict. wait=True runs inline (tests, scripts). quiet=True (or inside quiet_jobs()):
    a mission's job - no window opens and no push is sent when it finishes."""
    quiet = bool(getattr(_quiet, "on", False) if quiet is None else quiet)
    from core import users
    j = {"id": uuid.uuid4().hex[:8], "label": label, "state": "queued", "step": "queued", "steps": [], "result": "",
         "created": time.time(), "started": None, "finished": None, "uid": uid, "need_gpu_mb": need_gpu_mb,
         "quiet": quiet, "_fn": fn, "_cancel": threading.Event(), "_procs": [],
         "asker": uid or users.current()}   # whose request it was (tools/charjobs passes no uid): core/notice speaks only his
    with _lock:
        _jobs[j["id"]] = j
        for old in sorted(_jobs.values(), key=lambda x: x["created"])[:-KEEP]:
            if old["state"] in ("done", "failed", "cancelled"):
                _jobs.pop(old["id"], None)
        _queue.append(j["id"])
    if wait:
        _drain()
        while j["state"] in ("queued", "running"):       # another job may hold the worker: wait for ours to finish
            time.sleep(0.05)
            _drain()
    else:
        threading.Thread(target=_drain, daemon=True, name="craft-job").start()
    return j


def _drain():
    with _lock:
        if _running["id"] or not _queue:
            return
        jid = _queue.pop(0)
        _running["id"] = jid
    j = _jobs[jid]
    try:
        if j["_cancel"].is_set():
            j["state"], j["result"] = "cancelled", "Cancelled before it started."
        else:
            _execute(j)
    finally:
        with _lock:
            _running["id"] = None
        _announce(j)
        _drain()


def _execute(j):
    j["state"], j["started"] = "running", time.time()
    _tls.job = j
    try:
        if j["need_gpu_mb"] and os.getenv("JARVIS_SKIP_GPU_GUARD") != "1":
            step("checking the graphics card")
            g = gpu_state(need_mb=j["need_gpu_mb"])
            if not g["ok"]:
                j["state"], j["result"] = "failed", g["say"]
                return
            j["gpu_note"] = g.get("say", "")
        step("working")
        out = j["_fn"]()
        j["result"] = (j.get("gpu_note", "") + " " + str(out)).strip()
        j["state"] = "cancelled" if j["_cancel"].is_set() else "done"
    except Cancelled:
        j["state"], j["result"] = "cancelled", "Cancelled. Partial files, if any, were left in place and nothing of yours was touched."
    except Exception as e:
        j["state"], j["result"] = "failed", f"The job crashed: {type(e).__name__}: {str(e)[:200]}"
    finally:
        j["finished"] = time.time()
        _tls.job = None


def _announce(j):
    if not j.get("started") or j.get("quiet"):
        return                       # a quiet (mission) job: the mission's own report is the only push
    try:
        from core import notify
        notify.notify("JARVIS finished" if j["state"] == "done" else "JARVIS job " + j["state"], j["result"][:400], j.get("uid"))
    except Exception:
        pass
    if j["state"] in ("done", "failed") and (j.get("uid") or j.get("asker") or "owner") == "owner":
        # Plan upgrade 9 (core/notice): the voice at the PC may mention it once he is there and free (he cancelled the
        # others himself). The owner's jobs only: his slots are never said to anyone else.
        from core import notice
        label = str(j.get("label") or "the job")
        took = _dur((j.get("finished") or time.time()) - (j.get("started") or j["created"]))
        notice.finished("craft", j["id"], f"{label[:1].upper()}{label[1:120]} "
                        f"{'finished' if j['state'] == 'done' else 'failed'} after {took}.")


def get(job_id=None):
    with _lock:
        if job_id:
            return _jobs.get(job_id)
        live = [j for j in _jobs.values() if j["state"] in ("running", "queued")]
        pool = live or list(_jobs.values())
        return max(pool, key=lambda x: x["created"]) if pool else None


def cancel(job_id=None):
    j = get(job_id)
    if not j or j["state"] not in ("running", "queued"):
        return None
    j["_cancel"].set()
    for p in list(j["_procs"]):
        _kill_tree(p)
    if j["state"] == "queued":
        with _lock:
            if j["id"] in _queue:
                _queue.remove(j["id"])
        j["state"], j["result"], j["finished"] = "cancelled", "Cancelled before it started.", time.time()
    return j


def _dur(s):
    s = int(s)
    return f"{s // 60} minute{'s' if s // 60 != 1 else ''} {s % 60} seconds" if s >= 60 else f"{s} seconds"


def describe(j):
    if j is None:
        return "I have no character jobs right now."
    if j["state"] in ("running", "queued"):
        return f"Still working on {j['label']}: {j['step']}, {_dur(time.time() - (j['started'] or j['created']))} in."
    took = _dur((j["finished"] or time.time()) - (j["started"] or j["created"]))
    return f"{j['label'][:1].upper()}{j['label'][1:]} {j['state']} after {took}. {j['result']}"
