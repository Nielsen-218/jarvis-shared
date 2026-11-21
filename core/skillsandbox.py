"""Run externally supplied skill code ONLY inside JARVIS's existing Windows AppContainer broker (core/project_sandbox.py), never in-process
and never with JARVIS's own (elevated) token. There is no uncontained fallback: if the AppContainer cannot be used, nothing runs.

What the operating system enforces (proved by tests/test_skillsandbox_adversarial.py, which runs hostile code with every Python-level rule OFF):
  * the process token is an AppContainer token with ZERO capabilities and a verified package identity: no network, no access to the owner's files,
    registry, other processes or JARVIS's memory (nothing but the lab is granted to that identity);
  * the lab workspace and runtime are READ-ONLY to that identity, and its own profile folder is sealed against writes: there is nowhere to write;
  * a Job object with ActiveProcessLimit=1 (no child processes), a 256 MB memory cap, a CPU-time cap, kill-on-close, and a wall-clock timeout;
  * the process starts suspended, is put in the Job and has its token checked BEFORE the first instruction of the package runs;
  * the environment is two variables (LOCALAPPDATA, SystemRoot): none of JARVIS's keys or settings are inherited;
  * stdin/stdout/stderr are anonymous pipes with size quotas.
On top of that the worker (below) applies the SAME Python-level import/audit rules as before, as a second, independent layer ("harden").
Honest limits: AppContainer protects against the ordinary-user attack surface, not kernel exploits; CPU-heavy code can use one core for the timeout.
Windows only (the laptop cannot run installed skills; the tool is desk-only)."""
import json
import os
import shutil
import threading
import uuid
from pathlib import Path

from core import project_sandbox as sb
from core import skillpkg

PER_CALL_S, TOTAL_S = 5, 60
OUTPUT_LIMIT = 2_000_000          # bytes of result per sandbox process (the broker default of 128 KB is too small for a batch of 1000-person splits)
_lock = threading.Lock()
_ready = {}


class Unavailable(RuntimeError):
    """The OS-enforced sandbox cannot be used here. Callers must refuse to run the skill; there is no weaker mode."""


def available():
    return os.name == "nt"


def lab_root(base=None):
    env = os.environ.get("JARVIS_SKILLS_LAB")
    if env:
        return Path(env)
    from core import skillinstall
    return Path(base or skillinstall.root()) / "lab"


def _lab(base=None):
    if not available():
        raise Unavailable("installed skills need the Windows AppContainer sandbox; this machine has none")
    root = lab_root(base).resolve()
    with _lock:
        try:
            if not (root / ".jarvis-project-lab").is_file():
                root.parent.mkdir(parents=True, exist_ok=True)
                sb.initialize(root)
            if _ready.get(root) is None:                         # full integrity check of the copied runtime once per process
                for attempt in range(4):
                    try:
                        sb.prepare_runtime(root / "runtime")
                        sb.seal_profile(root)
                        break
                    except sb.SandboxUnavailable:
                        if attempt == 3:
                            raise
                        import time
                        time.sleep(1 + attempt)
                _ready[root] = True
        except (sb.SandboxUnavailable, OSError, ImportError) as e:
            raise Unavailable(f"AppContainer lab unavailable: {e}")
    return root


_WORKER = r'''
import sys, json, io, os, threading
_REQ = json.loads(%(req)s)
_out = sys.stdout
_USER_OK = set(_REQ["modules"])
_BASE = ("builtins", "sys", "encodings", "codecs", "io", "abc", "types", "_collections_abc", "collections", "re", "enum", "functools", "operator",
    "itertools", "math", "keyword", "reprlib", "copyreg", "warnings", "weakref", "_weakrefset", "typing", "contextlib", "decimal", "_decimal",
    "_pydecimal", "numbers", "fractions", "json", "datetime", "_datetime", "string", "textwrap", "heapq", "_heapq", "bisect", "_bisect", "statistics",
    "random", "_random", "_operator", "_functools", "_json", "unicodedata", "copy", "dataclasses", "inspect", "_sre", "sre_compile", "sre_parse",
    "sre_constants", "genericpath", "ntpath", "stat", "_stat", "linecache", "tokenize", "token", "dis", "opcode", "_opcode", "ast", "_ast", "traceback",
    "__future__", "locale", "_locale", "time", "_thread", "threading", "errno", "nt", "os", "importlib", "zipimport", "_signal", "atexit", "gc", "_contextvars",
    "contextvars", "_weakref", "_abc", "_io", "_codecs", "_warnings", "marshal", "posixpath", "_winapi", "_struct", "struct", "_string", "_typing", "_datetime",
    "_bisect", "_statistics", "_pylong", "_sitebuiltins", "_frozen_importlib", "_frozen_importlib_external", "_imp", "pyexpat")
_STD = os.path.dirname(os.__file__).lower()
_armed = False
_LOCKED = ("socket.", "subprocess.", "os.system", "os.exec", "os.spawn", "os.posix_spawn", "os.remove", "os.rename", "os.rmdir", "os.mkdir", "os.chmod",
           "os.truncate", "os.link", "os.symlink", "os.startfile", "ctypes.", "shutil.", "webbrowser.", "urllib.", "http.", "ftplib.", "smtplib.",
           "msvcrt.", "winreg.", "pty.", "os.kill", "os.putenv", "os.unsetenv", "os.add_dll_directory")

def _hook(event, args):
    if not _armed:
        return
    if event == "open":
        path, mode, flags = args
        p = str(path).lower() if not isinstance(path, int) else ""
        writing = (isinstance(mode, str) and any(c in mode for c in "wax+")) or (isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC))
        if writing or not (p.startswith(_STD) or p.endswith((".pyc", ".pyd", ".dll"))):
            raise PermissionError("skill sandbox: file access is not permitted")
    elif event.startswith(_LOCKED):
        raise PermissionError("skill sandbox: " + event.split(".")[0] + " is not permitted")
    elif event == "import":
        name = args[0].split(".")[0]
        if name not in _BASE and name not in _USER_OK and not args[0].startswith(("encodings", "_", "re.")):
            raise ImportError("skill sandbox: import of " + args[0] + " is not permitted")

if _REQ["harden"]:
    sys.addaudithook(_hook)

def _execute(src, entry, calls, per_call):
    ns = {"__name__": "skill"}
    buf = io.StringIO()
    sys.stdout = sys.stderr = buf
    results = []
    global _armed
    try:
        exec(compile(src, "procedure.py", "exec"), ns)
    except BaseException as e:
        sys.stdout = _out
        return [{"ok": False, "error": "load_failed", "kind": type(e).__name__, "message": str(e)[:200]} for _ in calls]
    fn = ns.get(entry)
    for params in calls:
        box = {}
        def target():
            try:
                v = fn(**params)
                json.dumps(v)
                box["r"] = {"ok": True, "output": v}
            except BaseException as e:
                code = getattr(e, "code", None) if type(e).__name__ == "SkillError" else None
                box["r"] = {"ok": False, "error": code if isinstance(code, str) else "crashed", "kind": type(e).__name__, "message": str(e)[:300]}
        _armed = _REQ["harden"]
        t = threading.Thread(target=target, daemon=True)
        t.start()
        t.join(per_call)
        _armed = False
        if t.is_alive():
            results.append({"ok": False, "error": "timeout", "message": "stopped after %%ss" %% per_call})
            results.extend({"ok": False, "error": "not_run", "message": "an earlier call timed out"} for _ in calls[len(results):])
            break
        results.append(box["r"])
    sys.stdout = _out
    return results

_res = _execute(_REQ["source"], _REQ["entry"], _REQ["calls"], _REQ["per_call"])
_out.write("@@RESULT@@" + json.dumps(_res)); _out.flush()
os._exit(0)
'''


def run_many(source, entry, calls, modules=(), per_call=PER_CALL_S, total=TOTAL_S, harden=True, base=None):
    """Run `entry(**params)` for each params dict, in ONE AppContainer process. -> list of {"ok", "output"} | {"ok": False, "error", "message"}.
    Raises Unavailable when the OS sandbox cannot be used (callers refuse; nothing runs). `harden=False` is for adversarial TESTS ONLY."""
    lab = _lab(base)
    req = json.dumps({"source": source, "entry": entry, "calls": calls, "modules": sorted(set(modules) & skillpkg.SAFE_MODULES),
                      "per_call": per_call, "harden": bool(harden)})
    ws = lab / ("ws-" + uuid.uuid4().hex[:10])
    ws.mkdir()
    try:
        script = ws / "w.py"
        script.write_text(_WORKER % {"req": repr(req)}, encoding="utf-8")
        sb.grant_readonly(ws, lab)
        r = sb.run(lab / "runtime", ws, script, timeout=total, output_limit=OUTPUT_LIMIT)
    finally:
        shutil.rmtree(ws, ignore_errors=True)
    line = next((x for x in r.get("stdout", "").splitlines() if x.startswith("@@RESULT@@")), None)
    if not r.get("passed_boundary") or line is None:
        why = r.get("error") or f"exit {r.get('exit_code')}"
        kind = "timeout" if "wall-clock" in why else "sandbox_failed"
        return [{"ok": False, "error": kind, "message": why[:200]} for _ in calls]
    out = json.loads(line[len("@@RESULT@@"):])
    r["boundary"] = {k: r.get(k) for k in ("backend", "app_container", "capability_count", "package_identity_verified", "job_assigned_before_resume")}
    for item in out:
        item["boundary"] = r["boundary"]
    return out


def run(source, entry, params, modules=(), timeout=PER_CALL_S, harden=True, base=None):
    """One call. Never returns partial trust: any sandbox problem is {"ok": False, "error": ...}; Unavailable propagates."""
    return run_many(source, entry, [params], modules, per_call=timeout, total=timeout + 10, harden=harden, base=base)[0]


_SELFTEST = r'''
import os, socket, ctypes
def denied(fn):
    try:
        fn()
        return False
    except BaseException:
        return True
def run(**a):
    out = {}
    out["cannot read private files"] = all(denied(lambda p=p: open(p, "rb").read(1)) for p in a["private"])
    out["cannot write host files"] = denied(lambda: open(a["sentinel"], "wb").write(b"x"))
    out["cannot connect to the host over loopback"] = denied(lambda: socket.create_connection(("127.0.0.1", a["port"]), timeout=2))
    def openproc():
        k = ctypes.windll.kernel32
        k.OpenProcess.restype = ctypes.c_void_p
        if not k.OpenProcess(0x0410, False, a["pid"]):
            raise PermissionError("refused")
    out["cannot open the host process"] = denied(openproc)
    out["cannot start a process"] = denied(lambda: __import__("subprocess").run(["cmd", "/c", "exit"], timeout=5))
    return out
'''


def selftest(private_files=()):
    """Run a tiny hostile probe inside the real sandbox from THIS process (run it from the same kind of shell JARVIS runs in, elevated or not) and
    report what the operating system refused. -> {"ok": bool, "checks": {name: bool}, "boundary": {...}}. Writes nothing; reads nothing it returns."""
    import socket
    import tempfile
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    sentinel = Path(tempfile.gettempdir()) / "jskill_selftest_must_not_exist.txt"
    sentinel.unlink(missing_ok=True)
    try:
        r = run(_SELFTEST, "run", {"private": [str(p) for p in private_files], "sentinel": str(sentinel), "port": listener.getsockname()[1], "pid": os.getpid()},
                harden=False, timeout=30)
    finally:
        listener.close()
    checks = dict(r.get("output") or {})
    checks["token is an AppContainer token with no capabilities"] = bool((r.get("boundary") or {}).get("app_container") and (r.get("boundary") or {}).get("capability_count") == 0)
    checks["host-side sentinel file was not created"] = not sentinel.exists()
    sentinel.unlink(missing_ok=True)
    import ctypes
    try:
        elevated = bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:                                    # noqa: BLE001
        elevated = None
    return {"ok": bool(r.get("ok")) and all(checks.values()), "checks": checks, "boundary": r.get("boundary"), "elevated": elevated,
            "error": None if r.get("ok") else r.get("message")}
