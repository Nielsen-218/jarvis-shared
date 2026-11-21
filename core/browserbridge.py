"""DEVELOPER-BUILT PRIMITIVE: browser tab control through the browser's own supported tabs API (a Manifest V3 extension).

Why: 24 practice cycles (332 methods, median 100 lines) re-derived the same low-level routines from UI Automation clicks:
find the windows, find the tab strip, read a stale address bar, find each tab's close button, keep a window alive. The
browser already knows each tab's id, URL, title and window exactly. This module exposes that, nothing more.

    Edge (lab profile) --loads--> core/browserbridge_ext (permission: "tabs" only)
          extension  --long-poll HTTP, 127.0.0.1, per-session random token-->  Bridge (inside the lab child process)
          learned procedure --> BrowserAPI (`browser` capability) --> Bridge.call --> extension --> chrome.tabs / chrome.windows

Safety: no remote debugging (the lab's DevTools port stays the JUDGE's private ground truth, never offered to a method); the
bridge listens on 127.0.0.1 only and refuses requests without the session token; the extension implements six operations
(windows, tabs, tab, activate, close, new_tab with about:blank only) — no page content, history, cookies, downloads or
navigation. The extension is loaded only into the lab's throwaway profiles. Using it on the owner's own Edge would need the
owner to install it (or a Native Messaging host) — not done; see docs/plans/compositional-skills.md.
"""
import json
import os
import secrets
import shutil
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

EXT_SRC = Path(__file__).with_name("browserbridge_ext")
CALL_SECONDS = 6.0
POLL_SECONDS = 15.0


class BrowserError(RuntimeError):
    """The browser refused or could not do an operation (e.g. the tab no longer exists)."""


class BridgeDown(BrowserError):
    """No browser is connected to the bridge (extension not loaded, or the browser exited)."""


class _ExclusiveServer(ThreadingHTTPServer):
    """No address reuse: on Windows SO_REUSEADDR would let another program bind the same port and receive the extension's
    polls (and its token). SO_EXCLUSIVEADDRUSE makes the port ours alone, or the bind fails."""
    allow_reuse_address = False
    daemon_threads = True

    def server_bind(self):
        import socket
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class Bridge:
    def __init__(self, port=0, token=None, session=None):
        self.token = token or secrets.token_urlsafe(24)
        # One session per connected browser. A killed browser's last long-poll can still be waiting on the server; without
        # this it would take (and lose) the next browser's first command (seen live 2026-11-08 15:04, world 1002).
        self.session = session or secrets.token_hex(8)
        self._lock = threading.Condition()
        self._queue, self._replies, self._seq = [], {}, 0
        self.last_poll = 0.0
        bridge = self

        class H(BaseHTTPRequestHandler):
            def _authorised(self):
                if not secrets.compare_digest(self.headers.get("X-Bridge-Token", ""), bridge.token):
                    self.send_response(403); self.end_headers()
                    return False
                return True

            def do_GET(self):
                if self.path != "/poll" or not self._authorised():
                    if self.path != "/poll":
                        self.send_response(404); self.end_headers()
                    return
                cmd = bridge._next(POLL_SECONDS, self.headers.get("X-Bridge-Session", ""))
                body = json.dumps(cmd or {}).encode()
                self.send_response(200); self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

            def do_POST(self):
                if self.path != "/reply" or not self._authorised():
                    if self.path != "/reply":
                        self.send_response(404); self.end_headers()
                    return
                n = int(self.headers.get("Content-Length") or 0)
                try:
                    bridge._reply(json.loads(self.rfile.read(min(n, 2_000_000)) or b"{}"))
                except ValueError:
                    pass
                self.send_response(204); self.end_headers()

            def log_message(self, *a):
                pass

        self._srv = _ExclusiveServer(("127.0.0.1", port), H)
        self.port = self._srv.server_address[1]
        threading.Thread(target=self._srv.serve_forever, daemon=True).start()

    # ---- server side
    def _next(self, wait, session=""):
        deadline = time.monotonic() + wait
        with self._lock:
            if session != self.session:
                return None                          # an old browser's poll: it never receives the current browser's work
            self.last_poll = time.monotonic()
            while not self._queue:
                left = deadline - time.monotonic()
                if left <= 0 or session != self.session:
                    return None
                self._lock.wait(left)
            if session != self.session:
                return None
            self.last_poll = time.monotonic()
            return self._queue.pop(0)

    def _reply(self, msg):
        with self._lock:
            self._replies[msg.get("id")] = msg
            self._lock.notify_all()

    # ---- caller side
    def call(self, op, timeout=CALL_SECONDS, **args):
        with self._lock:
            self._seq += 1
            cid = self._seq
            self._queue.append({"id": cid, "op": op, "args": args})
            self._lock.notify_all()
            deadline = time.monotonic() + timeout
            while cid not in self._replies:
                left = deadline - time.monotonic()
                if left <= 0:
                    self._queue = [c for c in self._queue if c["id"] != cid]
                    raise BridgeDown(f"the browser did not answer {op} within {timeout:.0f} s")
                self._lock.wait(left)
            msg = self._replies.pop(cid)
        if not msg.get("ok"):
            raise BrowserError(f"{op} failed: {msg.get('error', 'unknown error')}"[:300])
        return msg.get("result")

    def reset(self):
        """A new browser is about to connect (a new lab world): forget commands meant for the old one."""
        with self._lock:
            self._queue.clear(); self._replies.clear()
            self.session = secrets.token_hex(8)
            self._lock.notify_all()                  # waiting polls of the old session give up now

    def wait_connected(self, seconds=20.0) -> bool:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                self.call("windows", timeout=min(3.0, max(0.5, deadline - time.monotonic())))
                return True
            except BrowserError:
                time.sleep(.3)
        return False

    def extension_dir(self, where: Path) -> Path:
        """A copy of the extension for one throwaway profile, with this session's port and token."""
        dst = Path(where) / "bridge-ext"
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(EXT_SRC, dst)
        (dst / "config.json").write_text(json.dumps({"port": self.port, "token": self.token, "session": self.session}), encoding="utf-8")
        return dst

    def connected(self, within=40.0) -> bool:
        """A browser has polled recently (it polls every <= POLL_SECONDS while alive)."""
        return bool(self.last_poll) and time.monotonic() - self.last_poll < within

    def close(self):
        self._srv.shutdown()
        self._srv.server_close()


# ------------------------------------------------------------------------------------------------ the owner's own Edge (live)
# The PC's live JARVIS listens on a FIXED 127.0.0.1 port with a token kept in its data folder. The owner installs the extension
# once (edge://extensions -> Developer mode -> Load unpacked -> data/browser-bridge/extension); its config.json carries the same
# port, token and session. Nothing else changes in his browser. The bridge never starts in the lab or on the laptop.

PORT = int(os.getenv("JARVIS_BRIDGE_PORT", "47213"))
_LIVE = {"bridge": None, "lock": threading.Lock(), "error": ""}


def _home() -> Path:
    from core import storage
    return Path(os.getenv("JARVIS_BRIDGE_DIR", str(storage.DATA / "browser-bridge")))


def _secret() -> dict:
    f = _home() / "secret.json"
    try:
        s = json.loads(f.read_text(encoding="utf-8"))
        if s.get("token") and s.get("session"):
            return s
    except (OSError, ValueError):
        pass
    s = {"token": secrets.token_urlsafe(32), "session": secrets.token_hex(8)}
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(s), encoding="utf-8")
    return s


def package() -> Path:
    """Write (or refresh) the extension folder the owner loads into his Edge, with the live port, token and session."""
    s = _secret()
    dst = _home() / "extension"
    dst.mkdir(parents=True, exist_ok=True)
    for f in EXT_SRC.iterdir():
        if f.is_file() and f.name != "config.json":
            shutil.copy2(f, dst / f.name)
    (dst / "config.json").write_text(json.dumps({"port": PORT, "token": s["token"], "session": s["session"]}), encoding="utf-8")
    return dst


def live():
    """The live bridge for the owner's Edge (started once per JARVIS process), or None when it cannot run here."""
    if os.getenv("JARVIS_SKILL_LAB") == "1" or os.name != "nt" or os.getenv("JARVIS_BROWSER_BRIDGE", "on").lower() == "off":
        return None
    with _LIVE["lock"]:
        if _LIVE["bridge"] is None and not _LIVE["error"]:
            s = _secret()
            try:
                _LIVE["bridge"] = Bridge(port=PORT, token=s["token"], session=s["session"])
            except OSError as error:                 # the port is taken: say so once, never crash JARVIS
                _LIVE["error"] = f"{type(error).__name__}: {error}"
                print(f"[browserbridge] could not listen on 127.0.0.1:{PORT}: {_LIVE['error']}", flush=True)
        return _LIVE["bridge"]


def start() -> None:
    """Called at JARVIS start on the PC so the extension can connect before the first request."""
    try:
        package()
        if live() is None:
            return
    except Exception as error:  # noqa: BLE001 - an optional integration never stops JARVIS
        print(f"[browserbridge] not started: {type(error).__name__}: {error}", flush=True)
        return

    def report():                                    # scripts/skills.py status reads this (it cannot see into this process)
        while True:
            try:
                (_home() / "status.json").write_text(json.dumps({**status(), "at": time.strftime("%Y-%m-%dT%H:%M:%S")}), encoding="utf-8")
            except OSError:
                pass
            time.sleep(20)
    threading.Thread(target=report, daemon=True, name="browserbridge-status").start()


def status() -> dict:
    b = _LIVE["bridge"]
    return {"listening": bool(b), "port": PORT, "connected": bool(b and b.connected()), "error": _LIVE["error"],
            "extension_folder": str(_home() / "extension")}


def host_of(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


BROWSER_API = ("windows", "tabs", "tab", "activate", "close", "new_tab")

BROWSER_DOC = """`browser` — the browser's own tab API (developer-built primitive; exact, not guessed from titles):
  browser.windows() -> [{"id", "focused", "state", "tab_count"}] every normal browser window
  browser.tabs(window=None) -> [{"id", "window", "index", "url", "host", "title", "active"}] every tab (or one window's);
      `host` is the URL's host name in lower case, e.g. "www.github.com" or "gist.github.com"; `title` is page-chosen text
  browser.tab(tab_id) -> that tab's fresh record, or None once it no longer exists
  browser.activate(tab_id) -> brings that tab to the front of its window (returns its fresh record)
  browser.close(tab_id) -> closes exactly that tab; returns {"closed": True/False, "tab": id}
  browser.new_tab(window) -> opens a blank tab (about:blank) in that window, in the background; returns its record
  Closing a window's LAST tab closes the window itself."""


class BrowserAPI:
    """The `browser` capability handed to a procedure: thin, validated calls over a Bridge."""

    def __init__(self, bridge: Bridge):
        self._b = bridge

    @staticmethod
    def _id(v, what):
        if isinstance(v, dict):
            v = v.get("id")
        if isinstance(v, bool) or not isinstance(v, int):
            raise BrowserError(f"{what} must be an id number (got {v!r})")
        return v

    def _tab(self, t):
        return {**t, "host": host_of(t.get("url", ""))} if isinstance(t, dict) else t

    def windows(self):
        return self._b.call("windows")

    def tabs(self, window=None):
        return [self._tab(t) for t in self._b.call("tabs", **({"window": self._id(window, "window")} if window is not None else {}))]

    def tab(self, tab_id):
        return self._tab(self._b.call("tab", tab=self._id(tab_id, "tab")))

    def activate(self, tab_id):
        return self._tab(self._b.call("activate", tab=self._id(tab_id, "tab")))

    def close(self, tab_id):
        return self._b.call("close", tab=self._id(tab_id, "tab"))

    def new_tab(self, window):
        return self._tab(self._b.call("new_tab", window=self._id(window, "window")))
