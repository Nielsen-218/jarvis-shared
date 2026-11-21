"""Desktop practice worlds for the weaknesses that had none: close_foreground, close_app, use_app, type_text, save_document
and open_path. Same shape as the browser world (core/skillcontracts): a seeded world, a plain task statement, and a judge
that reads the REAL end state itself (never the method's own word).

Everything lives on the hidden lab desktop and belongs to the lab:
  * the target is core/labtarget, a tiny purpose-built program with real Win32 controls, started by the lab's own process
    (so it is on the lab desktop and inside the lab's kill-on-close job) and killed by the world when the trial ends;
  * its files are scratch files in the job folder; nothing of the owner's is read, opened, focused or closed;
  * the judge's ground truth is the lab's own Win32 probe of that program's windows (titles in Z order, the edit box's
    text, the status line) and the scratch folder on disk.
A method under test gets only the UI Automation primitives (core/methodlang): no keyboard, mouse, shell or files.
"""
import json
import random
import subprocess
import sys
import time
from pathlib import Path

APPS = ["LabPad", "LabSheet", "LabDraw", "LabMail", "LabNotes"]
FILES_APP = "LabFiles"
TARGET_CLASS = "JarvisLabTarget"                          # core.labtarget.CLASS: only these top-level windows are the world
PROMPT_PREFIX = "Unsaved changes - "                       # core.labtarget.PROMPT_PREFIX
FOREIGN_CLASS = "JarvisLabForeign"                        # core.labtarget.FOREIGN_CLASS: a program the world's method has no business touching
CHECK_VARIANTS = ("plain", "unsaved", "sibling", "unsaved_sibling")
_DOCS = ["budget", "kitchen plan", "trip notes", "rig checklist", "garden log", "invoice draft", "meeting notes", "shader ideas",
         "reading list", "workout log"]
_BUTTONS = ["Apply", "Refresh", "Export", "Reset", "Preview"]
_LINES = ["Buy screws and wood glue.", "Call the plumber on Monday.", "Draft two of the outline.", "Remember the blue folder.",
          "Check the render settings.", "Water the tomatoes twice.", "Send the measurements."]
KINDS = {
    "close_foreground": {"world": "desktop", "params": {}},
    "close_app": {"world": "desktop", "params": {"app": str}},
    "use_app": {"world": "desktop", "params": {"app": str, "goal": str}},
    "type_text": {"world": "desktop", "params": {"app": str, "text": str}},
    "save_document": {"world": "desktop", "params": {"app": str, "path": str}},
    "open_path": {"world": "desktop", "params": {"path": str}},
}


def _window(rnd, app, doc, buttons):
    return {"title": f"{doc} - {app}", "app": app, "doc": doc.replace(" ", "-") + ".txt", "edit": rnd.choice(["", rnd.choice(_LINES)]),
            "buttons": list(buttons), "front": False}


def generate(seed, kind) -> dict:
    """One practice world. Same seed -> same world. Every world has decoy windows that must be left exactly as they were."""
    if kind not in KINDS:
        raise ValueError(f"no desktop lab world for {kind}")
    rnd = random.Random(f"{kind}:{seed}")
    apps = rnd.sample(APPS, 3)
    docs = rnd.sample(_DOCS, 4)
    buttons = ["Save"] + rnd.sample(_BUTTONS, 3)
    rnd.shuffle(buttons)
    app = apps[0]
    windows = [_window(rnd, app, docs[0], buttons), _window(rnd, apps[1], docs[1], buttons)]
    if rnd.random() < .5:
        windows.append(_window(rnd, apps[2], docs[2], buttons))
    params, expect = {}, {}
    if kind == "close_foreground":
        target = rnd.randrange(len(windows))
        windows[target]["front"] = True
        expect = {"closed": [windows[target]["title"]]}
    elif kind == "close_app":
        if rnd.random() < .4:                               # the app has a second window: both must go
            windows.append(_window(rnd, app, docs[3], buttons))
        params = {"app": app}
        expect = {"closed": [w["title"] for w in windows if w["app"] == app]}
    elif kind == "use_app":
        button = rnd.choice([b for b in buttons if b != "Save"])
        params = {"app": app, "goal": f"press the {button} button"}
        expect = {"window": windows[0]["title"], "button": button}
    elif kind == "type_text":
        params = {"app": app, "text": rnd.choice(_LINES) + f" ({rnd.randrange(100, 999)})"}
        expect = {"window": windows[0]["title"]}
    elif kind == "save_document":
        windows[0]["edit"] = rnd.choice(_LINES) + f" [{rnd.randrange(1000, 9999)}]"
        params = {"app": app, "path": windows[0]["doc"]}
        expect = {"window": windows[0]["title"], "file": windows[0]["doc"]}
    elif kind == "open_path":
        files = [d.replace(" ", "-") + ".txt" for d in rnd.sample(_DOCS, 4)]
        windows.append({"title": f"scratch - {FILES_APP}", "app": FILES_APP, "doc": None, "edit": None, "buttons": ["Open", "Refresh"],
                        "files": files, "front": False})
        params = {"path": rnd.choice(files)}
        expect = {"opened": f"{params['path']} - LabView"}
    if kind != "close_foreground":                          # usually ANOTHER window is in front: the owner's real situation
        others = [i for i, w in enumerate(windows) if w["title"] not in (expect.get("window"), *expect.get("closed", []))]
        windows[rnd.choice(others) if others and rnd.random() < .7 else rnd.randrange(len(windows))]["front"] = True
    rnd.shuffle(windows)
    return {"kind": kind, "world": "desktop", "seed": seed, "params": params, "windows": windows, "expect": expect}


def generate_check(seed, kind="close_foreground") -> dict:
    """One PRODUCTION-CHECK world (core/skillcontracts.production_check_desktop). Same shape as generate(), plus:
      * "foreign": a window of a different program that is not the world's target (it must come out exactly as it went in);
      * "check": the variant: plain / sibling (the front window's app has a second window) / unsaved (the front window holds an unsaved
        document: closing it makes the app ask "Unsaved changes", which must be left alone) / unsaved_sibling (both).
    Variants cycle with the seed, so any 4 consecutive seeds cover all of them."""
    if kind != "close_foreground":
        raise ValueError(f"no production-check world for {kind}")
    world = generate(seed, kind)
    variant = CHECK_VARIANTS[seed % len(CHECK_VARIANTS)]
    front = next(w for w in world["windows"] if w["front"])
    rnd = random.Random(f"check:{seed}")
    if variant in ("sibling", "unsaved_sibling"):          # another window of the SAME app, behind: only the front one goes
        extra = _window(rnd, front["app"], rnd.choice([d for d in _DOCS if d.replace(" ", "-") + ".txt" != front["doc"]]), front["buttons"])
        while extra["title"] in {w["title"] for w in world["windows"]}:
            extra = _window(rnd, front["app"], rnd.choice(_DOCS) + f" {rnd.randrange(2, 9)}", front["buttons"])
        world["windows"].append(extra)
    if variant in ("unsaved", "unsaved_sibling"):
        front["unsaved"] = True
        front["edit"] = front["edit"] or rnd.choice(_LINES)      # an unsaved document has text in it
    world["foreign"] = [{"title": f"{rnd.choice(_DOCS)} - LabForeign", "edit": rnd.choice(_LINES), "buttons": ["Apply"], "front": False}]
    world["check"] = variant
    if front.get("unsaved"):
        world["expect"] = {"closed": [], "stays": front["title"], "prompt": PROMPT_PREFIX + front["title"]}
    rnd.shuffle(world["windows"])
    return world


def goal(kind, params) -> str:
    """The plain task statement shown to the learner, from the tool's own meaning (not a hint about how)."""
    lab = " Every window's title ends with the name of its app (\"<document> - <app>\"). Leave every other window, text and file exactly as it is."
    if kind == "close_foreground":
        return "Close the window that is in front (the first window the desktop lists) and no other window." + lab
    if kind == "close_app":
        return f"Close every window of the app {params['app']}." + lab
    if kind == "use_app":
        return f"In the app {params['app']}: {params['goal']}, exactly once. Keep its window open." + lab
    if kind == "type_text":
        return (f"Put this text into the text box of the app {params['app']}, keeping whatever the box already holds: "
                f"{params['text']!r}. Keep its window open." + lab)
    if kind == "save_document":
        return (f"Save the document that is open in the app {params['app']} so that the file {params['path']} holds exactly the "
                "text in its text box. Keep its window open." + lab)
    if kind == "open_path":
        return (f"Open the file {params['path']} from the file list of the app {FILES_APP}, so that a window showing that file "
                "appears. Open no other file." + lab)
    raise ValueError(kind)


def world_text(world) -> str:
    return "; ".join(("[front] " if w.get("front") else "") + w["title"] for w in world["windows"])


# ------------------------------------------------------------------------------------------------ lifecycle (lab desktop only)

def spec(world, scratch) -> dict:
    return {"scratch": str(scratch), "windows": [{k: w.get(k) for k in ("title", "edit", "doc", "buttons", "files", "front", "unsaved")}
                                                 for w in world["windows"]]}


def build(world, job_dir, trial_no, popen=None, wait=20.0, sleep=time.sleep) -> dict:
    """Start the world's own target program. -> the handle the snapshot and the teardown need. Refuses to run anywhere but
    inside a lab job (JARVIS_SKILL_LAB), so a world can never put its windows on the owner's desktop."""
    import os
    if popen is None and os.getenv("JARVIS_SKILL_LAB") != "1":
        raise RuntimeError("a desktop lab world only starts inside the practice lab (the hidden desktop)")
    scratch = Path(job_dir) / f"scratch-{trial_no}"
    scratch.mkdir(parents=True, exist_ok=True)
    spec_file = Path(job_dir) / f"world-{trial_no}.json"
    spec_file.write_text(json.dumps(spec(world, scratch)), encoding="utf-8")
    start = popen or subprocess.Popen
    cwd = str(Path(__file__).resolve().parent.parent)
    extra = []
    handle = {"proc": None, "pid": None, "scratch": str(scratch), "extra": extra}
    if world.get("foreign"):          # started FIRST so the world's own windows (and the front one) end up above it
        foreign_ready = Path(job_dir) / f"foreign-{trial_no}"
        foreign_ready.mkdir(parents=True, exist_ok=True)
        foreign_spec = Path(job_dir) / f"foreign-{trial_no}.json"
        foreign_spec.write_text(json.dumps({"scratch": str(foreign_ready), "foreign": True,
                                            "windows": [{**w, "doc": None, "files": None, "unsaved": False} for w in world["foreign"]]}), encoding="utf-8")
        fp = start([sys.executable, "-m", "core.labtarget", str(foreign_spec)], cwd=cwd)
        extra.append(fp)
        deadline = time.monotonic() + wait
        while not (foreign_ready / "ready").exists():
            if fp.poll() is not None or time.monotonic() > deadline:
                teardown(handle)
                raise RuntimeError("lab: the foreign practice program did not start")
            sleep(.1)
        (foreign_ready / "ready").unlink()
    proc = start([sys.executable, "-m", "core.labtarget", str(spec_file)], cwd=cwd)
    handle.update(proc=proc, pid=proc.pid)
    deadline = time.monotonic() + wait
    while not (scratch / "ready").exists():
        if proc.poll() is not None or time.monotonic() > deadline:
            teardown(handle)
            raise RuntimeError("lab: the practice target did not start")
        sleep(.1)
    (scratch / "ready").unlink()
    return handle


def _kill_pid(pid) -> None:
    try:
        import psutil
        psutil.Process(pid).kill()
    except Exception:  # noqa: BLE001 - already gone
        pass


def teardown(handle) -> None:
    """Everything a world starts is closed by the world (the lab's kill-on-close job is the second net)."""
    for proc in [handle.get("proc"), *handle.get("extra", [])]:
        if proc is None:
            continue
        for pid in _family(getattr(proc, "pid", None)) - {getattr(proc, "pid", None)}:     # the real program is a child of the launcher: end it first
            _kill_pid(pid)
        try:
            if proc.poll() is None:
                proc.kill()
                proc.wait(5)
        except Exception:  # noqa: BLE001 - already gone
            pass


def _family(pid, psutil=None) -> set:
    """pid plus every descendant process. On Windows a venv's python.exe is a LAUNCHER that starts the real interpreter as a
    child, so the windows of the target program belong to a grandchild of the lab, never to the pid Popen returned."""
    out = {pid}
    try:
        if psutil is None:
            import psutil
        out.update(c.pid for c in psutil.Process(pid).children(recursive=True))
    except Exception:  # noqa: BLE001 - the process is gone: just the pid itself
        pass
    return out


def _all_windows() -> list:
    """Every visible top-level window on THIS thread's desktop as (pid, title): the evidence a failed world carries."""
    import win32gui
    import win32process
    tops, out = [], []
    win32gui.EnumWindows(lambda h, _: tops.append(h) or True, None)
    for h in tops:
        if win32gui.IsWindowVisible(h):
            out.append((win32process.GetWindowThreadProcessId(h)[1], win32gui.GetWindowText(h)))
    return out


def _probe(pid, family=None, cls=None) -> list:
    """The target program's visible windows on THIS thread's desktop, front first, each with its edit text and status line.
    The program is the process `pid` AND its descendants (see _family)."""
    import ctypes
    import win32gui
    import win32process
    pids = family if family is not None else (_family(pid) if pid is not None else None)     # pid None: any program's window of this class
    u32 = ctypes.WinDLL("user32")
    u32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_void_p]
    u32.SendMessageW.restype = ctypes.c_ssize_t

    def text(hwnd):
        n = u32.SendMessageW(hwnd, 0x000E, 0, None)                       # WM_GETTEXTLENGTH
        buf = ctypes.create_unicode_buffer(n + 1)
        u32.SendMessageW(hwnd, 0x000D, n + 1, ctypes.cast(buf, ctypes.c_void_p))   # WM_GETTEXT
        return buf.value.replace("\r\n", "\n")
    tops = []
    win32gui.EnumWindows(lambda h, _: tops.append(h) or True, None)
    out = []
    for h in tops:
        if not win32gui.IsWindowVisible(h) or (pids is not None and win32process.GetWindowThreadProcessId(h)[1] not in pids):
            continue
        if win32gui.GetClassName(h) != (cls or TARGET_CLASS):                      # the target's IME and message helper windows are not its windows
            continue
        kids = []
        try:
            win32gui.EnumChildWindows(h, lambda c, _: kids.append(c) or True, None)
        except Exception:  # noqa: BLE001 - a window with no children
            pass
        by_id = {win32gui.GetDlgCtrlID(c): c for c in kids}
        out.append({"title": win32gui.GetWindowText(h), "edit": text(by_id[40]) if 40 in by_id else None,
                    "status": text(by_id[50]) if 50 in by_id else ""})
    return out


def snapshot(handle, probe=None) -> dict:
    scratch = Path(handle["scratch"])
    files = {p.name: p.read_text(encoding="utf-8", errors="replace") for p in sorted(scratch.iterdir()) if p.is_file()}
    out = {"windows": (probe or _probe)(handle["pid"]), "files": files}
    if handle.get("extra"):                       # a world with a foreign program: what it shows must come out exactly as it went in
        out["foreign"] = _probe(None, cls=FOREIGN_CLASS) if probe is None else []
    return out


def observe(max_lines=160) -> str:
    """A compact UI Automation tree of the windows on this desktop: what a method is able to see."""
    from pywinauto import Desktop
    lines = []

    def walk(el, depth):
        if len(lines) >= max_lines or depth > 5:
            return
        info = el.element_info
        lines.append("  " * depth + f"{info.control_type} {(info.name or '')[:70]!r}")
        for c in el.children():
            walk(c, depth + 1)
    for win in Desktop(backend="uia").windows():
        if win.window_text():
            walk(win, 0)
    return "\n".join(lines)


def _came_up(world, before) -> str:
    """'' when the target shows exactly the windows the world specifies (count, and the front one first); else what differs."""
    seen = [w["title"] for w in before["windows"]]
    want_front = next((w["title"] for w in world["windows"] if w.get("front")), "")
    if len(seen) != len(world["windows"]):
        return f"expected {len(world['windows'])} windows, found {len(seen)}: {seen}"
    if want_front and seen[0] != want_front:
        return f"expected {want_front!r} in front, found {seen[0]!r} (z-order: {seen})"
    return ""


def _evidence(handle) -> str:
    """What the probe saw, so a failed world explains itself: the pid looked for, its family, and every visible window."""
    pid = handle.get("pid")
    try:
        fam = sorted(_family(pid))
        seen = _all_windows()
    except Exception as error:  # noqa: BLE001 - evidence must never hide the failure it describes
        return f"[pid looked for {pid}; evidence unavailable: {type(error).__name__}: {error}]"
    return f"[pid looked for {pid}, family {fam}; visible windows (pid, title): {seen}]"


def run_trial(world, action, job_dir, trial_no) -> dict:
    """One trial on the lab desktop: build the world, run the action, read the end state, judge it, close the world."""
    row, handle = {}, None
    try:
        handle = build(world, job_dir, trial_no)
        time.sleep(.6)
        before = row["before"] = snapshot(handle)
        want_front = next((w["title"] for w in world["windows"] if w.get("front")), "")
        problem = _came_up(world, before)
        if problem:
            raise RuntimeError("lab: the world did not come up as specified: " + problem + " " + _evidence(handle))
        started = time.monotonic()
        if action["type"] == "observe":
            result = {"ok": True, "tree": observe()}
        elif action["type"] == "noop":                      # the judge must FAIL a world nobody touched
            result = {"ok": True, "message": "did nothing"}
        elif action["type"] == "method":
            from core import methodlang
            result = methodlang.run(action["source"], methodlang.UIA(), world["params"])
        elif action["type"] == "production":      # the host's run-time wrapper around the method, exactly as on the real desktop
            from core import learnedclose
            result = learnedclose.run_in_lab(action["source"], world["params"])
        elif action["type"] == "runtime":         # the whole real run-time path: pcmethods.execute -> wrapper -> verifier -> ledger -> log_use
            from core import learnedclose
            result = learnedclose.runtime_in_lab(world["params"])
        else:                                               # the built-in tools type keys and call the shell: never in a world
            raise ValueError(f"a desktop world does not run a {action['type']!r} action")
        row["seconds"] = round(time.monotonic() - started, 2)
        time.sleep(.6)
        row["after"], row["result"] = snapshot(handle), result
        if action["type"] != "observe":
            if world.get("check"):
                from core import skillcontracts
                row["judge"] = skillcontracts.production_check_desktop(world, before, row["after"], result)
            else:
                row["judge"] = judge(world, before, row["after"], result)
    finally:
        if handle:
            teardown(handle)
    return row


# ------------------------------------------------------------------------------------------------ the judge (pure)

def _pressed(win) -> list:
    return [p for p in (win or {}).get("status", "").partition("pressed=")[2].split(",") if p]


def judge(world: dict, before: dict, after: dict, result: dict) -> dict:
    """before/after = {"windows": [{"title", "edit", "status"} front first], "files": {name: text}} from the lab's own probe."""
    kind, params, expect = world["kind"], world["params"], world["expect"]
    b = {w["title"]: w for w in before["windows"]}
    a = {w["title"]: w for w in after["windows"]}
    closed = [t for t in b if t not in a]
    new = [t for t in a if t not in b]
    target = expect.get("window", "")
    must_close = [before["windows"][0]["title"]] if kind == "close_foreground" and before["windows"] else expect.get("closed", [])
    edits_changed = [t for t in b if t in a and a[t]["edit"] != b[t]["edit"] and not (kind == "type_text" and t == target)]
    presses = {t: _pressed(a[t])[len(_pressed(b[t])):] for t in b if t in a}
    allowed_press = {"use_app": [expect.get("button")], "save_document": ["Save"]}.get(kind, []) if target else []
    if kind == "open_path":
        target, allowed_press = f"scratch - {FILES_APP}", ["Open"]
    stray_press = [f"{p} in {t}" for t, ps in presses.items() for p in ps if not (t == target and ps == allowed_press)]
    files_changed = sorted(n for n in set(before["files"]) | set(after["files"]) if before["files"].get(n) != after["files"].get(n)
                           and not (kind == "save_document" and n == expect.get("file")))
    checks = {"method_finished": bool(result.get("ok")),
              "other_windows_kept": sorted(closed) == sorted(must_close),
              "no_stray_windows": new == ([expect["opened"]] if kind == "open_path" and expect["opened"] in new else []),
              "other_text_kept": not edits_changed, "no_other_button_pressed": not stray_press, "no_other_file_written": not files_changed}
    diff = []
    if kind in ("close_foreground", "close_app"):
        checks["target_closed"] = bool(must_close) and all(t not in a for t in must_close)
        if not checks["target_closed"]:
            diff.append("still open: " + "; ".join(t for t in must_close if t in a))
    elif kind == "use_app":
        checks["button_pressed_once"] = presses.get(target) == [expect["button"]]
        if not checks["button_pressed_once"]:
            diff.append(f"{expect['button']} in {target} was pressed {presses.get(target, []).count(expect['button'])} time(s); pressed there: {presses.get(target)}")
    elif kind == "type_text":
        was, now = (b.get(target) or {}).get("edit") or "", (a.get(target) or {}).get("edit")
        checks["text_in_the_box"] = now is not None and params["text"] in now and was in now and len(now) <= len(was) + len(params["text"]) + 2
        if not checks["text_in_the_box"]:
            diff.append(f"the text box of {target} holds {now!r}; it held {was!r} and should also hold {params['text']!r}")
    elif kind == "save_document":
        want = (b.get(target) or {}).get("edit")
        checks["file_has_the_text"] = want is not None and after["files"].get(expect["file"]) == want
        if not checks["file_has_the_text"]:
            diff.append(f"{expect['file']} holds {after['files'].get(expect['file'])!r}, the text box holds {want!r}")
    elif kind == "open_path":
        checks["file_window_open"] = expect["opened"] in a
        if not checks["file_window_open"]:
            diff.append(f"no window titled {expect['opened']!r} appeared")
    wrongly = [t for t in closed if t not in must_close]
    if wrongly:
        diff.append("wrongly closed: " + "; ".join(wrongly))
    if not checks["no_stray_windows"]:
        diff.append("unexpected new windows: " + "; ".join(new))
    if edits_changed:
        diff.append("text changed in: " + "; ".join(edits_changed))
    if stray_press:
        diff.append("pressed where it should not: " + "; ".join(stray_press))
    if files_changed:
        diff.append("files written that should not be: " + "; ".join(files_changed))
    if not result.get("ok"):
        diff.append("method error: " + (result.get("error") or "did not finish"))
    return {"passed": all(checks.values()), "checks": checks, "diff": diff}


# The lab's own reference solutions: they prove each world CAN be solved with the method primitives (scripts/lab_worlds.py
# and the tests). They are never shown to the learner.
REFERENCE = {
    "close_foreground": """def run(ui, params):
    front = ui.windows()[0]
    ui.invoke(ui.find(front["id"], role="Button", name="Close")[0]["id"])
    return "closed " + front["title"]
""",
    "close_app": """def run(ui, params):
    done = []
    for w in ui.windows():
        if w["title"].endswith(" - " + params["app"]):
            ui.invoke(ui.find(w["id"], role="Button", name="Close")[0]["id"])
            done.append(w["title"])
    return "closed " + ", ".join(done)
""",
    "use_app": """def run(ui, params):
    win = [w for w in ui.windows() if w["title"].endswith(" - " + params["app"])][0]
    hits = [b for b in ui.find(win["id"], role="Button") if b["name"] and (" " + b["name"].lower() + " ") in params["goal"].lower()]
    if len(hits) != 1:
        ui.fail("no single button matches the goal")
    ui.invoke(hits[0]["id"])
    return "pressed " + hits[0]["name"]
""",
    "type_text": """def run(ui, params):
    win = [w for w in ui.windows() if w["title"].endswith(" - " + params["app"])][0]
    box = ui.find(win["id"], role="Edit")[0]
    ui.set_value(box["id"], ui.value(box["id"]) + params["text"])
    return "typed the text"
""",
    "save_document": """def run(ui, params):
    win = [w for w in ui.windows() if w["title"].endswith(" - " + params["app"])][0]
    ui.invoke(ui.find(win["id"], role="Button", name="Save")[0]["id"])
    return "saved"
""",
    "open_path": """def run(ui, params):
    win = [w for w in ui.windows() if w["title"].endswith(" - LabFiles")][0]
    ui.select(ui.find(win["id"], role="ListItem", name=params["path"])[0]["id"])
    ui.invoke(ui.find(win["id"], role="Button", name="Open")[0]["id"])
    return "opened " + params["path"]
""",
}
