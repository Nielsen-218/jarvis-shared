"""Native Windows companion harness — no brain, no microphone, no cloud.

Purpose: open the real pywebview companion window using the actual code in
hud/app.py and app/companion.html so a human (or Codex via Windows Computer
Use) can visually verify native transparency, drag, hide/show-resume, mouth
amplitude sync and panel-forwarding without activating the live Jarvis.

Design:
  * A tiny FastAPI server on 127.0.0.1 serves app/ as static files (so
    /companion.html, /companion/behavior.js and /vendor/three/* load) and
    exposes POST /control for synthetic events (state, level, line, show,
    hide, mode, ...). Validated through core/companion.validate_event.
  * A multiprocessing.Process spawns hud.app.run in "companion" mode
    pointed at that local server, exactly as core/hud.start() would.
  * The parent process writes an info file (pid/title/port/control_url)
    so another operator can drive it.

Nothing in this file touches the brain, audio input, Ollama or paid APIs.
Launch with ``pythonw.exe`` for a hidden console if you don't want the
terminal to appear.

Usage (from the repository root):
    python scripts/companion_harness.py --demo          # canned sequence
    python scripts/companion_harness.py --interactive   # wait for /control
    python scripts/companion_harness.py --help

CLI control (while --interactive):
    curl -X POST http://127.0.0.1:<port>/control -H "content-type: application/json" \
         -d '{"type":"state","value":"speaking"}'
    curl -X POST http://127.0.0.1:<port>/control -d '{"type":"level","value":0.6}'
    curl -X POST http://127.0.0.1:<port>/control -d '{"type":"line","who":"jarvis","text":"Hello."}'
    curl -X POST http://127.0.0.1:<port>/control -d '{"type":"hide"}'
    curl -X POST http://127.0.0.1:<port>/control -d '{"type":"show"}'
    curl -X POST http://127.0.0.1:<port>/control -d '{"type":"quit"}'
"""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
from multiprocessing import managers as _managers
import os
import queue as _q
import socket
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import companion as _companion  # noqa: E402

DEFAULT_INFO_PATH = Path(
    "C:/Users/you/Documents/Codex/2026-10-22/referenced-chatgpt-conversation-this-is-an/outputs/harness-info.json"
)
APP_DIR = ROOT / "app"
WINDOW_TITLE = "Jarvis (harness)"   # disambiguated from the live Jarvis window
HARNESS_VERSION = 1


# ---------------------------------------------------------------- control queue
# This queue is the single bridge between HTTP POSTs and the HUD mp.Queue.
# The main process pulls validated events off it and forwards to the HUD.
_control_q: "_q.Queue[dict]" = _q.Queue(maxsize=512)
_shutdown = threading.Event()
_stats = {"queued": 0, "forwarded": 0, "dropped": 0, "rejected": 0}

# Populated after start(): the mp Manager's dict proxy. The companion
# renderer pushes periodic snapshots here via the pywebview Api, and
# GET /renderer-state exposes them.
_shared_state = None  # type: ignore[var-annotated]


def _validate(ev: dict) -> dict | None:
    """Accept the harness-local shutdown sentinel and anything core.companion
    validates. Nothing else reaches the HUD."""
    if not isinstance(ev, dict):
        return None
    if ev.get("type") == "quit":
        return {"type": "quit"}
    return _companion.validate_event(ev)


def _build_fastapi(static_dir: Path):
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import JSONResponse
    from fastapi.staticfiles import StaticFiles

    app = FastAPI(title="companion-harness", docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/healthz")
    def _healthz():
        return {"ok": True, "version": HARNESS_VERSION}

    @app.get("/stats")
    def _stats_route():
        return {**_stats, "queued_now": _control_q.qsize(), "shutdown": _shutdown.is_set()}

    @app.get("/renderer-state")
    def _renderer_state():
        """Live companion-renderer state (mode/jaw/level/frames) pushed by
        the renderer every ~1 s. ``available=False`` means no snapshot has
        arrived yet — the renderer hasn't booted or the shared state is
        running without a Manager (e.g. --no-shared)."""
        if _shared_state is None or "renderer" not in _shared_state:
            return {"available": False, "reason": "no snapshot yet"}
        return {"available": True, **dict(_shared_state["renderer"])}

    @app.post("/control")
    def _control(ev: dict):
        validated = _validate(ev)
        if validated is None:
            _stats["rejected"] += 1
            raise HTTPException(status_code=400, detail="unknown or malformed event")
        try:
            _control_q.put_nowait(validated)
            _stats["queued"] += 1
        except _q.Full:
            _stats["dropped"] += 1
            raise HTTPException(status_code=503, detail="control queue full")
        return JSONResponse({"ok": True, "type": validated["type"]})

    # His menu's Skills browser, for testing it here: the REAL catalogue (core/skills_catalog, read-only), but
    # only harmless read-only skills actually run, and "explain" hands the result back as it is (no brain here).
    harmless = {"get_time", "get_system_status", "companion"}

    @app.get("/api/skills")
    def _skills():
        import tools
        from core import skills_catalog
        tools.load_all()
        return skills_catalog.catalog()

    @app.post("/api/skills/run")
    def _skills_run(body: dict):
        import tools
        from core import skills_catalog
        tools.load_all()
        name = str((body or {}).get("name") or "")
        from core.companion import ROBOT_ACTIONS
        physical = name == "companion" and ((body or {}).get("args") or {}).get("action") in ROBOT_ACTIONS
        if name not in harmless or (name == "companion" and not physical):
            return {"ok": False, "text": "The test harness only runs read-only skills (the time, the computer's "
                                         "health) — try this one in Jarvis.", "seconds": 0.0}
        return skills_catalog.run("owner", name, (body or {}).get("args") or {})

    @app.post("/api/skills/explain")
    def _skills_explain(body: dict):
        return {"reply": str((body or {}).get("text") or "")[:600], "audio": ""}

    # Must be mounted LAST so /control and /healthz win.
    app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")
    return app


def _pick_free_port() -> int:
    """Ask the kernel for a currently-free loopback port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _run_server_blocking(app, host: str, port: int, ready: threading.Event) -> None:
    """Run uvicorn on the current thread. ready is set as soon as a client
    receives /healthz 200."""
    import uvicorn

    config = uvicorn.Config(app, host=host, port=port, log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    # Signal ready as soon as uvicorn marks itself started.
    orig_startup = server.startup

    async def _startup(*args, **kwargs):
        await orig_startup(*args, **kwargs)
        ready.set()
    server.startup = _startup  # type: ignore[method-assign]

    def _watch():
        while not _shutdown.is_set():
            time.sleep(0.25)
        server.should_exit = True
    threading.Thread(target=_watch, daemon=True).start()
    server.run()


def _spawn_hud(base_url: str, shared=None) -> tuple[mp.Process, mp.Queue]:
    """Spawn the HUD subprocess in companion mode pointed at our static server.
    `shared` is a Manager().dict() proxy carried into the HUD process so the
    renderer can push diagnostics snapshots back here."""
    from hud.app import run as hud_run

    hud_queue: mp.Queue = mp.Queue(maxsize=_companion.MAX_EVENT_QUEUE)
    proc = mp.Process(
        target=hud_run,
        args=(hud_queue, os.getpid(), base_url, "", "companion", WINDOW_TITLE, shared),
        daemon=False,                      # the harness waits on it explicitly
        name="jarvis-companion-harness",
    )
    proc.start()
    return proc, hud_queue


def _pump_control_to_hud(hud_queue: mp.Queue) -> None:
    """Pull validated events off the HTTP queue and forward to the HUD process."""
    while not _shutdown.is_set():
        try:
            ev = _control_q.get(timeout=0.25)
        except _q.Empty:
            continue
        if ev.get("type") == "quit":
            try:
                hud_queue.put_nowait(ev)
            except Exception:
                pass
            _shutdown.set()
            return
        try:
            hud_queue.put_nowait(ev)
            _stats["forwarded"] += 1
        except Exception:
            _stats["dropped"] += 1


DEMO_SCRIPT: list[tuple[float, dict]] = [
    # (seconds-from-start, event)
    (0.5,  {"type": "state", "value": "idle"}),
    (2.5,  {"type": "state", "value": "listening"}),
    (4.5,  {"type": "state", "value": "thinking"}),
    (6.0,  {"type": "state", "value": "speaking"}),
    (6.1,  {"type": "line",  "who": "jarvis", "text": "This is a harness smoke test."}),
    # Short burst of level packets to drive the mouth.
    (6.3,  {"type": "level", "value": 0.35}),
    (6.5,  {"type": "level", "value": 0.55}),
    (6.7,  {"type": "level", "value": 0.72}),
    (6.9,  {"type": "level", "value": 0.42}),
    (7.1,  {"type": "level", "value": 0.68}),
    # Now cut off the amplitude producer; the stale-level timeout (500 ms) must
    # close the mouth even though we're still "speaking".
    (8.5,  {"type": "state", "value": "idle"}),
    # Exercise companion hide/show-resume independently of the panel.
    (10.0, {"type": "companion_hide"}),
    (12.0, {"type": "companion_show"}),
    (12.5, {"type": "state", "value": "listening"}),
    (14.0, {"type": "state", "value": "idle"}),
]


def _run_demo_script(start: float) -> None:
    for delay, ev in DEMO_SCRIPT:
        when = start + delay
        while time.monotonic() < when:
            if _shutdown.is_set():
                return
            time.sleep(0.05)
        try:
            _control_q.put_nowait(ev)
            _stats["queued"] += 1
        except Exception:
            _stats["dropped"] += 1


def _write_info(path: Path, pid: int, port: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # The harness opens TWO windows in companion mode: the existing 400x660
    # panel (title = WINDOW_TITLE) and the companion (title =
    # WINDOW_TITLE + " (companion)"). Codex finds each by title.
    info = {
        "harness_version": HARNESS_VERSION,
        "pid": pid,
        "window_title": WINDOW_TITLE,                        # legacy / primary
        "panel_title": WINDOW_TITLE,
        "companion_title": f"{WINDOW_TITLE} (companion)",
        "port": port,
        "control_url": f"http://127.0.0.1:{port}/control",
        "healthz_url": f"http://127.0.0.1:{port}/healthz",
        "stats_url": f"http://127.0.0.1:{port}/stats",
        "renderer_state_url": f"http://127.0.0.1:{port}/renderer-state",
        "static_root": f"http://127.0.0.1:{port}/",
        "companion_url": f"http://127.0.0.1:{port}/companion.html",
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "note": "Synthetic-event harness. No brain, no microphone, no cloud, no paid API.",
    }
    path.write_text(json.dumps(info, indent=2), encoding="utf-8")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Native companion harness (no brain / no mic).")
    p.add_argument("--port", type=int, default=0, help="static-server port; 0 = pick free")
    p.add_argument("--info-file", type=Path, default=DEFAULT_INFO_PATH,
                   help="JSON file receiving pid/title/port/control_url")
    p.add_argument("--data-dir", type=Path, default=ROOT / "data" / "companion-harness-isolated",
                   help="isolated companion state root (never the live owner state)")
    p.add_argument("--demo", action="store_true", help="run a scripted sequence and exit when done")
    p.add_argument("--interactive", action="store_true",
                   help="idle after launching; drive via POST /control (default when --demo not set)")
    p.add_argument("--max-seconds", type=float, default=0,
                   help="auto-shutdown after N seconds (0 = no cap). Safety for CI/background runs.")
    return p.parse_args()


def main() -> int:
    global _shared_state
    args = _parse_args()
    os.environ["JARVIS_COMPANION_DATA_DIR"] = str(args.data_dir.resolve())
    args.data_dir.mkdir(parents=True, exist_ok=True)
    if not args.demo and not args.interactive:
        args.interactive = True

    port = args.port or _pick_free_port()

    # Spin up the mp Manager BEFORE building the FastAPI app so /renderer-state
    # can read a stable proxy dict. The Manager owns its own helper process.
    manager = _managers.SyncManager()
    manager.start()
    _shared_state = manager.dict()

    from core import storage
    storage.DATA = args.data_dir.resolve() / "storage"
    app = _build_fastapi(APP_DIR)

    server_ready = threading.Event()
    server_thread = threading.Thread(
        target=_run_server_blocking, args=(app, "127.0.0.1", port, server_ready), daemon=True)
    server_thread.start()
    if not server_ready.wait(timeout=10):
        print("[harness] FAILED: static server did not come up in 10 s", file=sys.stderr)
        return 2

    base_url = f"http://127.0.0.1:{port}"
    hud_proc, hud_queue = _spawn_hud(base_url, shared=_shared_state)
    # Reuse the real registry's existing companion skill, routed to THIS isolated
    # HUD process/queue. No second registry, fake tool execution or live-owner HUD.
    from core import hud as harness_hud
    harness_hud._process, harness_hud._queue, harness_hud._mode = hud_proc, hud_queue, "companion"
    pump_thread = threading.Thread(target=_pump_control_to_hud, args=(hud_queue,), daemon=True)
    pump_thread.start()

    _write_info(args.info_file, hud_proc.pid or -1, port)
    print(f"[harness] HUD pid={hud_proc.pid} port={port} control={base_url}/control info={args.info_file}")

    if args.demo:
        threading.Thread(target=_run_demo_script, args=(time.monotonic(),), daemon=True).start()

    deadline = time.monotonic() + args.max_seconds if args.max_seconds > 0 else None
    try:
        while not _shutdown.is_set():
            if not hud_proc.is_alive():
                print("[harness] HUD process exited", file=sys.stderr)
                break
            if deadline is not None and time.monotonic() > deadline:
                print("[harness] --max-seconds reached; shutting down")
                break
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("[harness] interrupted")

    _shutdown.set()
    try:
        hud_queue.put_nowait({"type": "quit"})
    except Exception:
        pass
    hud_proc.join(timeout=5)
    if hud_proc.is_alive():
        hud_proc.terminate()
    print(f"[harness] stats={_stats}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
