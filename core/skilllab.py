"""The ISOLATED practice environment for learned PC methods.

Isolation, by construction:
  * a separate, never-shown Windows desktop ("JarvisSkillLab"). Windows only lets a process see and drive windows on its OWN desktop,
    so the practice process (and the method inside it) cannot see, focus, click or close anything on the owner's desktop;
  * a throwaway Edge profile per trial, in a lab folder, so no owner tabs, cookies, history or sign-ins exist there;
  * no network: Edge resolves every host name to 127.0.0.1 and gets its pages from a local offline server;
  * a Windows Job object: hard memory cap, CPU-time cap, kill-on-close (Edge included), wall-clock limit;
  * suspension: the moment the owner touches the keyboard or mouse, the whole job is killed and reported "suspended".

Ground truth for the judge (core/skillcontracts.judge) comes from Edge's own DevTools page list and the desktop's window list,
both read by THIS code, never by the method under test.
"""
import ctypes
import ctypes.wintypes as w
import json
import os
import socket
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
from pathlib import Path

DESKTOP = "JarvisSkillLab"
EDGE = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
JOB_MEMORY = 4 << 30
JOB_CPU_SECONDS = 1800
ROOT = Path(os.environ.get("LOCALAPPDATA", os.environ.get("TEMP", "."))) / "JarvisSkillLab"


class Suspended(RuntimeError):
    """The owner started using the PC; the job was stopped and can run again later."""


def available() -> str:
    """'' when a lab can run here, otherwise why not."""
    if os.name != "nt":
        return "the practice lab needs the Windows PC"
    if not EDGE.exists():
        return "Microsoft Edge is not installed where the lab expects it"
    return ""


def _last_input():
    """Changes whenever the owner touches the keyboard, mouse or a game controller."""
    from core import inputwatch
    return inputwatch.activity_token()


class _SI(ctypes.Structure):
    _fields_ = [('cb', w.DWORD), ('lpReserved', w.LPWSTR), ('lpDesktop', w.LPWSTR), ('lpTitle', w.LPWSTR), ('dwX', w.DWORD),
                ('dwY', w.DWORD), ('dwXSize', w.DWORD), ('dwYSize', w.DWORD), ('dwXCountChars', w.DWORD), ('dwYCountChars', w.DWORD),
                ('dwFillAttribute', w.DWORD), ('dwFlags', w.DWORD), ('wShowWindow', w.WORD), ('cbReserved2', w.WORD),
                ('lpReserved2', ctypes.c_void_p), ('hStdInput', w.HANDLE), ('hStdOutput', w.HANDLE), ('hStdError', w.HANDLE)]


class _PI(ctypes.Structure):
    _fields_ = [('hProcess', w.HANDLE), ('hThread', w.HANDLE), ('dwProcessId', w.DWORD), ('dwThreadId', w.DWORD)]


class _BASIC(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64), ("LimitFlags", w.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t), ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", w.DWORD),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", w.DWORD), ("SchedulingClass", w.DWORD)]


class _IO(ctypes.Structure):
    _fields_ = [(n, ctypes.c_uint64) for n in ("r", "wr", "o", "rb", "wb", "ob")]


class _EXT(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", _BASIC), ("IoInfo", _IO), ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]


def run_job(trials: list, seconds: float = 900, guard: bool = True, env: dict | None = None, label: str = "job") -> dict:
    """Run trials in the lab. -> {"status": "done"|"suspended"|"timeout"|"crashed", "results": [...], "log": str}.
    Each trial: {"world": <skillcontracts world>, "action": {"type": "method"|"native"|"observe"|"jarvis", ...}}."""
    why = available()
    if why:
        raise RuntimeError(why)
    import msvcrt
    ROOT.mkdir(parents=True, exist_ok=True)
    lock = open(ROOT / "lab.lock", "a+")
    try:
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)       # one job at a time: two labs on one desktop would see each other
    except OSError:
        lock.close()
        raise RuntimeError("another practice job is using the lab") from None
    try:
        return _run_job(trials, seconds, guard, env, label)
    finally:
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        lock.close()


def _run_job(trials, seconds, guard, env, label) -> dict:
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    u32 = ctypes.WinDLL("user32", use_last_error=True)
    u32.CreateDesktopW.restype = w.HANDLE
    k32.CreateJobObjectW.restype = w.HANDLE
    job_dir = ROOT / f"{time.strftime('%Y%m%d-%H%M%S')}-{label}-{os.getpid()}"
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "job.json").write_text(json.dumps({"trials": trials}), encoding="utf-8")
    desk = u32.CreateDesktopW(DESKTOP, None, None, 0, 0x10000000, None)
    if not desk:
        raise RuntimeError(f"could not create the lab desktop (error {ctypes.get_last_error()})")
    job = k32.CreateJobObjectW(None, None)
    ext = _EXT()
    ext.BasicLimitInformation.LimitFlags = 0x2000 | 0x200 | 0x4      # KILL_ON_JOB_CLOSE | JOB_MEMORY | JOB_TIME
    ext.BasicLimitInformation.PerJobUserTimeLimit = int(JOB_CPU_SECONDS * 1e7)
    ext.JobMemoryLimit = JOB_MEMORY
    k32.SetInformationJobObject(job, 9, ctypes.byref(ext), ctypes.sizeof(ext))
    si = _SI(); si.cb = ctypes.sizeof(_SI); si.lpDesktop = f"WinSta0\\{DESKTOP}"
    pi = _PI()
    py = Path(sys.executable)
    py = py.with_name("python.exe") if py.name.lower() == "pythonw.exe" else py
    cmd = f'"{py}" -m core.skilllab child "{job_dir}"'
    child_env = {**os.environ, "JARVIS_SKILL_LAB": "1", "PYTHONIOENCODING": "utf-8", **(env or {})}
    block = "".join(f"{k}={v}\0" for k, v in child_env.items()) + "\0"
    CREATE_SUSPENDED, NO_WINDOW, UNICODE_ENV = 0x4, 0x08000000, 0x400
    ok = k32.CreateProcessW(None, ctypes.create_unicode_buffer(cmd), None, None, False, CREATE_SUSPENDED | NO_WINDOW | UNICODE_ENV,
                            ctypes.create_unicode_buffer(block), str(Path(__file__).resolve().parent.parent), ctypes.byref(si), ctypes.byref(pi))
    if not ok:
        raise RuntimeError(f"could not start the lab process (error {ctypes.get_last_error()})")
    k32.AssignProcessToJobObject(job, pi.hProcess)
    k32.ResumeThread(pi.hThread)
    started, input_at = time.monotonic(), _last_input()
    status = "done"
    try:
        while k32.WaitForSingleObject(pi.hProcess, 250) == 0x102:
            if guard and _last_input() != input_at:
                status = "suspended"
                break
            if time.monotonic() - started > seconds:
                status = "timeout"
                break
        if status != "done":
            k32.TerminateJobObject(job, 1)
    finally:
        k32.CloseHandle(pi.hThread); k32.CloseHandle(pi.hProcess); k32.CloseHandle(job)
        u32.CloseDesktop(desk)
    out = job_dir / "results.json"
    results = json.loads(out.read_text(encoding="utf-8")) if out.exists() else []
    log = (job_dir / "child.log").read_text(encoding="utf-8", errors="replace")[-4000:] if (job_dir / "child.log").exists() else ""
    if status == "done" and len(results) < len(trials):
        status = "crashed"
    return {"status": status, "results": results, "log": log, "dir": str(job_dir), "seconds": round(time.monotonic() - started, 1)}


# ------------------------------------------------------------------------------------------------ child (runs ON the lab desktop)

def _cert(job_dir: Path):
    crt, key = job_dir / "lab.crt", job_dir / "lab.key"
    if not crt.exists():
        import datetime
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import NameOID
        k = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "jarvis-skill-lab")])
        now = datetime.datetime.now(datetime.timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(k.public_key()).serial_number(1)
                .not_valid_before(now - datetime.timedelta(days=1)).not_valid_after(now + datetime.timedelta(days=2)).sign(k, hashes.SHA256()))
        crt.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        key.write_bytes(k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return str(crt), str(key)


def _free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    return port


class _World:
    PAGES = {}

    @classmethod
    def serve(cls, job_dir):
        import ssl
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                page = cls.PAGES.get(self.path.split("?")[0])
                body = (f"<!doctype html><html><head><meta charset='utf-8'><title>{page['title_html']}</title></head><body>{page['body']}</body></html>"
                        if page else "<html><head><title>lab</title></head><body></body></html>").encode("utf-8")
                self.send_response(200 if page else 404)
                self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Content-Length", str(len(body)))
                self.end_headers(); self.wfile.write(body)

            def log_message(self, *a):
                pass
        port = _free_port()
        srv = ThreadingHTTPServer(("127.0.0.1", port), H)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(*_cert(job_dir))
        srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return port


def _cdp(port, path):
    return json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5).read())


def _pages(port):
    rows = _cdp(port, "/json/list")
    return [{"id": r["id"], "url": r["url"], "title": r.get("title", "")} for r in rows
            if r.get("type") == "page" and not r["url"].startswith(("devtools://", "chrome-extension://"))]


def _browser_windows() -> int:
    """Visible top-level Edge windows on THIS (the lab) desktop."""
    u32 = ctypes.windll.user32
    found = []
    PROTO = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)

    def cb(hwnd, _):
        cls = ctypes.create_unicode_buffer(64)
        u32.GetClassNameW(hwnd, cls, 64)
        n = u32.GetWindowTextLengthW(hwnd)
        if cls.value == "Chrome_WidgetWin_1" and u32.IsWindowVisible(hwnd) and n:
            t = ctypes.create_unicode_buffer(n + 1); u32.GetWindowTextW(hwnd, t, n + 1)
            if "Edge" in t.value:
                found.append(hwnd)
        return True
    u32.EnumWindows(PROTO(cb), 0)
    return len(found)


def _snapshot(port):
    try:
        pages = _pages(port)
    except OSError:                              # the browser exited (e.g. its last window was closed): nothing is open any more
        pages = []
    return {"pages": pages, "windows": _browser_windows()}


def _kill_tree(proc):
    import psutil
    try:
        p = psutil.Process(proc.pid)
        for c in p.children(recursive=True):
            c.kill()
        p.kill()
    except Exception:  # noqa: BLE001
        pass


def _edge_args(profile, port, extension=None):
    # The DevTools port is the JUDGE's ground truth only: no method or procedure is ever given it. The tab-bridge extension
    # (core/browserbridge) is the supported API a procedure uses, loaded only into this throwaway profile.
    features = "msEdgeSidebarV2,msUndersideButton" + (",DisableLoadExtensionCommandLineSwitch" if extension else "")
    return [str(EDGE), f"--user-data-dir={profile}", f"--remote-debugging-port={port}", "--no-first-run", "--no-default-browser-check",
            "--disable-sync", "--disable-background-networking", "--host-resolver-rules=MAP * 127.0.0.1", "--ignore-certificate-errors",
            "--test-type", "--disable-gpu", "--hide-crash-restore-bubble", f"--disable-features={features}",
            "--window-size=1200,800"] + ([f"--load-extension={extension}", f"--disable-extensions-except={extension}"] if extension else [])


def build_world(world, job_dir, trial_no, server_port, bridge=None):
    """Launch a throwaway Edge with exactly the world's windows and tabs; -> (edge process, devtools port)."""
    import html as _html
    profile = job_dir / f"profile-{trial_no}"
    ext = None
    if bridge is not None:
        bridge.reset()
        ext = str(bridge.extension_dir(job_dir / f"ext-{trial_no}"))
    port = _free_port()
    urls = []
    for wi, win in enumerate(world["windows"]):
        row = []
        for ti, tab in enumerate(win["tabs"]):
            path = f"/t{trial_no}/w{wi}/p{ti}"
            _World.PAGES[path] = {"title_html": _html.escape(tab["title"]), "body": tab.get("body", "")}
            row.append(f"https://{tab['host']}:{server_port}{path}")
        urls.append(row)
    proc = subprocess.Popen(_edge_args(profile, port, ext) + ["--new-window"] + urls[0])
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        try:
            _cdp(port, "/json/version"); break
        except Exception:  # noqa: BLE001
            time.sleep(.3)
    for extra in urls[1:]:
        time.sleep(1.0)
        subprocess.Popen(_edge_args(profile, port, ext) + ["--new-window"] + extra)
    want = sum(len(r) for r in urls)
    while time.monotonic() < deadline:
        pages = [p for p in _pages(port) if "/t%d/" % trial_no in p["url"]]
        if len(pages) >= want and all(p["title"] and not p["title"].startswith("https://") for p in pages) and _browser_windows() >= len(urls):
            break
        time.sleep(.3)
    # stray first-run pages (welcome, new tab) are closed so the world is exactly as specified
    for p in _pages(port):
        if "/t%d/" % trial_no not in p["url"]:
            try:
                _cdp(port, "/json/close/" + p["id"])
            except Exception:  # noqa: BLE001
                pass
    by_url = {p["url"].split(f":{server_port}")[-1]: p["id"] for p in _pages(port)}
    for wi, win in enumerate(world["windows"]):
        pid = by_url.get(f"/t{trial_no}/w{wi}/p{win.get('active', 0)}")
        if pid:
            try:
                _cdp(port, "/json/activate/" + pid)
            except Exception:  # noqa: BLE001
                pass
    time.sleep(1.2)
    if bridge is not None and not bridge.wait_connected(15):
        raise RuntimeError("lab: the tab-bridge extension did not connect (not loaded by this browser?)")
    return proc, port


def observe(max_lines=220):
    """A compact UI Automation tree of the browser windows on this desktop: what a method would be able to see."""
    from pywinauto import Desktop
    lines = []

    def walk(el, depth):
        if len(lines) >= max_lines or depth > 9:
            return
        info = el.element_info
        name = (info.name or "")[:70]
        if info.control_type in ("TabItem", "Tab", "Button", "Window", "Pane", "ToolBar", "Document", "TitleBar", "Group") or name:
            lines.append("  " * depth + f"{info.control_type} {name!r}")
        if info.control_type == "Document":
            return                                                     # page content: its own subtree, summarised by its title
        for c in el.children():
            walk(c, depth + 1)
    for win in Desktop(backend="uia").windows():
        if "Edge" in (win.window_text() or ""):
            walk(win, 0)
    return "\n".join(lines)


def child(job_dir):
    job_dir = Path(job_dir)
    log = open(job_dir / "child.log", "a", encoding="utf-8")
    sys.stdout = sys.stderr = log
    trials = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))["trials"]
    results = []
    from core import skillcontracts
    desktop_only = all(t["world"].get("world") == "desktop" for t in trials)      # core/labworlds: no browser, no server
    server_port = None if desktop_only else _World.serve(job_dir)
    bridge = None
    if any(t["action"]["type"] in ("procedure", "skill") for t in trials):
        from core import browserbridge
        bridge = browserbridge.Bridge()
    for i, trial in enumerate(trials):
        world, action = trial["world"], trial["action"]
        row = {"trial": i, "seed": world.get("seed"), "params": world.get("params"), "action": action["type"]}
        proc = None
        if world.get("world") == "desktop":               # a purpose-built target window instead of a browser (core/labworlds)
            from core import labworlds
            try:
                row.update(labworlds.run_trial(world, action, job_dir, i))
            except Exception:  # noqa: BLE001 - one broken trial is evidence, not the end of the job
                row["lab_error"] = traceback.format_exc()[-1500:]
            results.append(row)
            (job_dir / "results.json").write_text(json.dumps(results, indent=1), encoding="utf-8")
            continue
        try:
            uses_bridge = action["type"] in ("procedure", "skill")
            proc, port = build_world(world, job_dir, i, server_port, bridge if uses_bridge else None)
            before = _snapshot(port)
            row["before"] = before
            started = time.monotonic()
            if action["type"] == "observe":
                result = {"ok": True, "tree": observe()}
            elif action["type"] == "method":
                from core import methodlang
                result = methodlang.run(action["source"], methodlang.UIA(), world["params"])
            elif action["type"] == "native":
                from core import pcactions
                try:
                    out = pcactions._close_named_site_tab(world["params"]["site"])
                    result = {"ok": bool(out.get("verified")), "message": out.get("detail", ""), "error": ""}
                except Exception as error:  # noqa: BLE001
                    result = {"ok": False, "error": f"{type(error).__name__}: {error}", "message": ""}
            elif action["type"] == "procedure":          # a composed procedure over developer-built capabilities
                from core import skillspec
                result = skillspec.run_in_lab(action["source"], action["caps"], world["params"], bridge)
            elif action["type"] == "skill":              # a RETAINED skill, loaded from disk by this fresh process
                from core import skillspec
                result = skillspec.run_stored(action["skill"], world["params"], bridge, store=action.get("store"),
                                              version=action.get("version"))
            elif action["type"] == "jarvis":
                from core import acquire
                result = acquire.lab_jarvis_turn(action["text"])
            else:
                raise ValueError(action["type"])
            row["seconds"] = round(time.monotonic() - started, 2)
            time.sleep(1.0)
            after = _snapshot(port)
            row["after"], row["result"] = after, result
            if action["type"] != "observe":
                row["judge"] = skillcontracts.judge(world, before, after, result)
        except Exception:  # noqa: BLE001 - one broken trial is evidence, not the end of the job
            row["lab_error"] = traceback.format_exc()[-1500:]
        finally:
            if proc:
                _kill_tree(proc)
                time.sleep(.8)
        results.append(row)
        (job_dir / "results.json").write_text(json.dumps(results, indent=1), encoding="utf-8")
    log.close()


if __name__ == "__main__" and len(sys.argv) == 3 and sys.argv[1] == "child":
    child(sys.argv[2])
