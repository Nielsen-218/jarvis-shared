"""The typed PC verbs for files, projects, text, screen and terminal: real capabilities behind the gateway's plans.

Each verb returns the shape core.pcmethods expects ({"verified": bool, "detail": str, ...}) and raises RuntimeError with a plain
reason when it cannot do or prove its job. Nothing here decides whether it MAY run: core.authz did that from the step's effect,
and `approved` says whether the owner said yes to this exact plan.
"""
from __future__ import annotations

import os
import re
import time
from typing import Callable, Dict, List

# Inside an allowed root, these first-level folders are never the owner's working space (credentials, app data, system).
_DENY_UNDER_HOME = {"appdata", ".ssh", ".gnupg", ".aws", ".azure", ".kube", ".docker", ".config", ".git-credentials",
                    "ntuser.dat", "ntuser.ini", "application data", "local settings", "cookies"}
_DENY_UNDER_DRIVE = {"windows", "program files", "program files (x86)", "programdata", "$recycle.bin", "system volume information",
                     "recovery", "users", "boot", "efi", "pagefile.sys", "hiberfil.sys", "swapfile.sys"}
_C_PROJECT_FOLDERS = (r"C:\Projects", r"C:\Unreal Projects", r"C:\UnrealProjects", r"C:\dev", r"C:\src")


def _real(path: str) -> str:
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


def owner_roots() -> List[str]:
    """The explicit places the owner's files live: the profile, the other FIXED non-system drives, and project folders on
    C: that exist and are named in core.projects. Canonical (junctions and symlinks resolved)."""
    roots = list(user_roots())
    roots += [p for p in _C_PROJECT_FOLDERS if os.path.isdir(p)]
    out = []
    for r in roots:
        try:
            rr = _real(r)
        except (OSError, ValueError):
            continue
        if rr not in out:
            out.append(rr)
    return out


def _in_user_space(path: str) -> bool:
    """May a file verb open, list or save at `path`? Only inside an explicit owner root, judged on the CANONICAL path (so a
    junction or symlink inside the profile that points at C:\\Windows is C:\\Windows), never a bare drive root, never
    AppData or credential folders, never Windows / Program Files / another user's profile, never UNC, device (\\\\?\\),
    alternate-data-stream or unexpanded-variable paths."""
    if not isinstance(path, str) or not path.strip() or "\x00" in path:
        return False
    p = path.strip().strip('"')
    if p.startswith(("\\\\", "//")) or re.search(r"%[A-Za-z_]\w*%|\$env:", p, re.I):
        return False
    if ":" in p[2:]:                                   # 'file.txt:stream' or a second drive spec
        return False
    try:
        real = _real(os.path.expanduser(p))
    except (OSError, ValueError):
        return False
    if real.startswith("\\\\"):
        return False
    home = _real(os.path.expanduser("~"))
    for root in owner_roots():
        prefix = root if root.endswith(os.sep) else root + os.sep
        if real == root.rstrip(os.sep) or real == root:
            return root == home                        # the profile itself yes; a bare drive root no
        if not real.startswith(prefix):
            continue
        first = real[len(prefix):].split(os.sep)[0]
        if root == home:
            return first not in _DENY_UNDER_HOME
        if re.fullmatch(r"[a-z]:\\", root):
            return first not in _DENY_UNDER_DRIVE
        return True
    return False


def _fence(what: str) -> None:
    from jarvis_pc.guardrails.stepfence import fence
    fence(what)


def _require_foreground(what: str) -> None:
    from jarvis_pc.guardrails.stepfence import require_foreground
    require_foreground(what)


def _keys(*keys: str) -> None:
    """One keystroke or chord, refused once this step has passed its time limit (jarvis_pc.guardrails.stepfence), and in
    background mode: real keys land in whatever is in front, which a mission never touches (2026-11-10)."""
    _require_foreground("press " + "+".join(keys))
    import pyautogui
    if len(keys) == 1:
        pyautogui.press(keys[0])
    else:
        pyautogui.hotkey(*keys)




# ---------------------------------------------------------------------------------------------------- projects / files
def find_project(args: Dict, *, approved=False, cancelled: Callable[[], bool] = lambda: False) -> Dict:
    from core import projects
    found = projects.find(args["name"])
    if cancelled():
        raise InterruptedError("Stopped after looking for the project.")
    if not found:
        raise RuntimeError(f"I did not find a project matching '{args['name']}' in the usual places (profile, Documents, Desktop, "
                           "UnrealProjects, source, the other drives).")
    detail = projects.describe(found)
    if projects.ambiguous(found):
        # Two real candidates: the owner chooses; a guess here would launch the wrong project.
        return {"verified": False, "detail": detail + " Which one do you mean?", "error_kind": "needs_choice",
                "choices": [c.path for c in found], "candidates": [c.brief() for c in found]}
    best = found[0]
    return {"verified": True, "detail": detail + f" I would {projects.launch_method(best.path)}.", "found": best.path,
            "candidates": [c.brief() for c in found], "method": projects.launch_method(best.path)}


def _windows_with(words: List[str]) -> List[dict]:
    from tools import pcops
    low = [w.casefold() for w in words]
    return [w for w in pcops.windows() if w.get("title") and all(x in w["title"].casefold() for x in low)]


# What Windows RUNS when it is 'opened' (PATHEXT plus the other code-carrying types): never treated as a document.
_RUNS_CODE = {".exe", ".com", ".bat", ".cmd", ".ps1", ".psm1", ".psd1", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".wsh", ".msc",
              ".cpl", ".py", ".pyw", ".pyz", ".lnk", ".url", ".msi", ".msp", ".scr", ".hta", ".reg", ".pif", ".appref-ms",
              ".application", ".jar", ".inf", ".sct", ".scf", ".gadget", ".msix", ".appx", ".appinstaller", ".ws", ".sys", ".dll"}
_SCRIPT_TEXT = {".bat", ".cmd", ".ps1", ".psm1"}


def runs_code(path: str) -> bool:
    ext = os.path.splitext(path)[1].casefold()
    pathext = {e.strip().casefold() for e in os.environ.get("PATHEXT", "").split(";") if e.strip()}
    return ext in _RUNS_CODE or ext in pathext


def script_tier(path: str) -> int:
    """The terminal gate's tier for a script file: the highest tier of its command lines (a .bat/.cmd/.ps1 the gate can
    read); 2 for anything it cannot read (a program, a shortcut, Python): those run only on the owner's yes."""
    ext = os.path.splitext(path)[1].casefold()
    if ext not in _SCRIPT_TEXT:
        return 2
    try:
        with open(path, "rb") as f:
            raw = f.read(65537)
    except OSError:
        return 2
    if len(raw) > 65536:
        return 2
    text = raw.decode("utf-8", "replace").replace("\x00", "")
    worst = 2                                     # running any script asks first, even if every line is read-only
    for line in text.splitlines():
        s = line.strip().lstrip("@")
        if not s or s.casefold().startswith(("rem ", "::", "#", "echo off")):
            continue
        worst = max(worst, terminal_tier(s))
        if worst >= 3:
            break
    return worst


def open_path(args: Dict, *, approved=False, cancelled: Callable[[], bool] = lambda: False) -> Dict:
    """Open a file or folder with its registered program, as the signed-in user (never elevated), and prove a window for it
    appeared. An Unreal project gets up to two minutes (the editor compiles shaders before its window shows)."""
    from core import projects
    from tools.software import launch_as_user
    path = args["path"].strip().strip('"')
    if path in ("", "<found>"):
        raise RuntimeError("No path was found to open yet.")
    if not os.path.exists(path):
        raise RuntimeError(f"There is no file or folder at {path}.")
    if not _in_user_space(path):
        raise RuntimeError("That path is outside your own folders, so I did not open it.")
    ext = os.path.splitext(path)[1].casefold()
    if not os.path.isdir(path) and ext not in projects.LAUNCHABLE:
        raise RuntimeError(f"I don't open {ext or 'that kind of'} files directly.")
    if not os.path.isdir(path) and runs_code(path):
        # Opening a script or program RUNS it: that is a terminal-class action, not an 'open'. It never runs unasked, and a
        # script containing a command the gate never runs (T3) never runs at all.
        tier = script_tier(path)
        if tier >= 3:
            raise RuntimeError(f"{os.path.basename(path)} contains a command I never run, so I did not start it.")
        if not approved:
            raise RuntimeError(f"{os.path.basename(path)} is a program or script; opening it runs code, so it needs your yes first.")
    from jarvis_pc.guardrails.stepfence import require_foreground
    require_foreground(f"open {os.path.basename(path)}")   # its window comes to the front: never from a mission
    words = projects.expected_title_words(path)
    before = {w["hwnd"] for w in _windows_with(words)}
    launch_as_user(path)
    timeout = 150 if ext == ".uproject" else 40 if ext in (".sln", ".blend") else 15
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cancelled():
            raise InterruptedError("Stopped while waiting for the window.")
        new = [w for w in _windows_with(words) if w["hwnd"] not in before and not w.get("minimized")]
        if new:
            return {"verified": True, "detail": f"Opened {os.path.basename(path)}: a window titled '{new[0]['title'][:70]}' is up "
                                                f"({new[0]['process'].removesuffix('.exe')}).", "window": new[0]["title"], "process": new[0]["process"]}
        time.sleep(0.5)
    raise RuntimeError(f"I asked Windows to open {os.path.basename(path)}, but no window named after it appeared within {timeout} s.")


def find_files(args: Dict, *, approved=False, cancelled: Callable[[], bool] = lambda: False) -> Dict:
    from tools import pcskills
    folder = os.path.expanduser(os.path.expandvars(args["folder"]))
    known = {"downloads": "Downloads", "documents": "Documents", "desktop": "Desktop", "pictures": "Pictures", "videos": "Videos", "music": "Music"}
    if folder.casefold().strip() in known:
        folder = os.path.join(os.path.expanduser("~"), known[folder.casefold().strip()])
    if not os.path.isdir(folder):
        raise RuntimeError(f"There is no folder at {folder}.")
    if not _in_user_space(folder):
        raise RuntimeError("That folder is outside your own folders, so I did not list it.")
    out = pcskills.skills().find_files(folder, args["pattern"] or "*")
    return {"verified": True, "detail": f"In {folder}: " + out.replace("\n", "; ")[:1500], "folder": folder}


# ---------------------------------------------------------------------------------------------------------- text / screen
WINDOW_WAIT_S = 8.0
TERMINAL_TIMEOUT_S = 30          # an ordinary shell step; core.pcmethods bounds the whole step a little above this


def _foreground_hwnd() -> int:
    try:
        import ctypes
        return int(ctypes.windll.user32.GetForegroundWindow() or 0)
    except Exception:  # noqa: BLE001
        return 0


def _one_window(app: str, cancelled: Callable[[], bool] = lambda: False, wait: float = WINDOW_WAIT_S):
    """The one window of `app` to work in. A just-launched app's process exists before its window does (Windows 11 Notepad:
    1-2 s), so wait for it, bounded. Several windows: the one in front is the one the owner is working in; otherwise ask."""
    from tools import apps
    canonical = apps.resolve_app(app)
    if not canonical:
        raise RuntimeError(f"'{app}' is not an installed app.")
    deadline = time.monotonic() + wait
    while True:
        found = [w for w in apps.visible_windows_for_app(canonical) if not w.get("minimized")]
        if len(found) == 1:
            return canonical, found[0]
        if len(found) > 1:
            front = _foreground_hwnd()
            chosen = next((w for w in found if front and w.get("hwnd") == front), None)
            if chosen:
                return canonical, chosen
            raise RuntimeError(f"{len(found)} {canonical} windows are open and none is in front; tell me which one to use.")
        if cancelled():
            raise InterruptedError(f"Stopped while waiting for the {canonical} window.")
        if time.monotonic() >= deadline:
            raise RuntimeError(f"No visible {canonical} window appeared within {wait:.0f} s.")
        time.sleep(0.25)


def _doc_name(title: str) -> str:
    """'*README.TXT - Notepad' -> 'README.TXT'."""
    head = (title or "").rsplit(" - ", 1)[0] if " - " in (title or "") else (title or "")
    return head.lstrip("*●").strip()


def _untitled(title: str) -> bool:
    return _doc_name(title).casefold().startswith("untitled")


def _live_title(win) -> str:
    try:
        return win.window_text() or ""
    except Exception:  # noqa: BLE001
        return ""


def _foreign_document(canonical: str, title: str) -> bool:
    """Notepad is showing a document the owner did not name in this request (a tab Windows restored, or their own file).
    A title with no document in it yet ('Notepad' while it loads) counts as foreign: a new tab never hurts, typing into an
    unknown document can. The app's own name is never evidence that the owner named the document."""
    if canonical.casefold() != "notepad":
        return False
    if " - " not in (title or ""):
        return True
    doc = _doc_name(title)
    if _untitled(title):
        return False
    from core import pcbridge
    said = (pcbridge.original_message() or "").casefold()
    stem = os.path.splitext(doc)[0].casefold()
    if stem in (canonical.casefold(), "notepad", ""):
        return True
    return not (doc.casefold() in said or (len(stem) >= 3 and stem in said))


_OURS: Dict[tuple, float] = {}       # (window handle, document name) of new documents Jarvis typed into -> when


def _started_here(hwnd, title: str) -> None:
    now = time.time()
    for k in [k for k, t in _OURS.items() if now - t > 3600]:
        _OURS.pop(k, None)
    _OURS[(hwnd, _doc_name(title).casefold())] = now


def _ours(hwnd, title: str) -> bool:
    return (hwnd, _doc_name(title).casefold()) in _OURS


def _same_document(before: str, after: str, typed: str) -> bool:
    """Is the document in front after typing the one that was in front before? Windows 11 Notepad renames an unsaved new tab
    after its first line ('Untitled' -> 'hello world'), so that rename is the same document; any other change is not."""
    a, b = _doc_name(before).casefold(), _doc_name(after).casefold()
    if a == b:
        return True
    if _untitled(before):
        # Notepad drops characters a file name cannot hold (':' and '\' : 'C:\Users\me' -> 'CUsersme') and cuts long lines,
        # so compare letters and digits only.
        squash = lambda s: re.sub(r"[^0-9a-z]+", "", s.casefold())  # noqa: E731
        first = squash((typed or "").strip().splitlines()[0]) if (typed or "").strip() else ""
        b_key = squash(b)
        return _untitled(after) or (bool(b_key) and first.startswith(b_key))
    return False


def _settled_title(win, fallback: str, wait: float = 3.0) -> str:
    """The window's title once it names a document ('X - Notepad'); a just-launched Notepad shows only 'Notepad' first."""
    deadline = time.monotonic() + wait
    title = _live_title(win) or fallback
    while " - " not in title and time.monotonic() < deadline:
        time.sleep(0.15)
        title = _live_title(win) or title
    return title


def _editable(win):
    for kind in ("Document", "Edit"):
        for c in win.descendants(control_type=kind):
            try:
                if c.is_enabled() and c.is_visible():
                    return c
            except Exception:  # noqa: BLE001
                continue
    raise RuntimeError("I could not find a text area in that window.")


def _text_areas(win) -> list:
    """Every enabled Document/Edit text area in the window, visible or not (a hidden tab's editor counts)."""
    out = []
    for kind in ("Document", "Edit"):
        try:
            found = win.descendants(control_type=kind)
        except Exception:  # noqa: BLE001
            continue
        for c in found:
            try:
                if c.is_enabled():
                    out.append(c)
            except Exception:  # noqa: BLE001
                continue
    return out


def _text_of(ctl) -> str:
    for getter in (lambda: ctl.get_value(), lambda: "\n".join(ctl.texts()), lambda: ctl.window_text()):
        try:
            value = getter()
            if value:
                return str(value)
        except Exception:  # noqa: BLE001
            continue
    return ""


def type_text(args: Dict, *, approved=False, cancelled: Callable[[], bool] = lambda: False) -> Dict:
    """Put text into the named app's text area through the clipboard (exact characters, any language), then read it back."""
    from core import pcvision
    from jarvis_pc.uiact import uia
    canonical, window = _one_window(args["app"], cancelled)
    win = uia.window(window["title"])     # bound to the window's handle: survives title changes and new tabs
    from jarvis_pc.guardrails.stepfence import is_background
    if is_background():
        return _type_text_bg(args, canonical, window, win, cancelled)
    uia.focus(win)
    new_tab = False
    showing = _settled_title(win, window["title"])          # what is in front NOW, not the title seen at launch
    if _foreign_document(canonical, showing):
        # Windows 11 Notepad reopens the owner's last tabs; typing there would change a document they never mentioned
        # (2026-11-06 acceptance: Notepad came up on a README). A new tab is where 'open Notepad and type ...' belongs.
        _keys("ctrl", "n")
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and not _untitled(_live_title(win)):
            time.sleep(0.15)
        if not _untitled(_live_title(win)):
            raise RuntimeError(f"{canonical} is showing '{_doc_name(showing)}', which you didn't mention, and no new tab "
                               "opened, so I typed nothing.")
        new_tab = True
    # In a new tab the caret is already in its editor. Never pick 'the first editor' there: the restored tab's editor is
    # still in the tree, and focusing it switches back to that tab (which is how the text once landed in the README).
    ctl = None if new_tab else _editable(win)
    if ctl is not None:
        name = ""
        try:
            name = (ctl.element_info.name or "") + " " + (ctl.element_info.automation_id or "")
        except Exception:  # noqa: BLE001
            pass
        if pcvision._SECRET.search(name):
            raise RuntimeError("That field looks like a password or card field; I never type into those.")
        _require_foreground("focus the text area")          # set_focus brings the window forward (2026-11-10)
        try:
            ctl.set_focus()
        except Exception:  # noqa: BLE001
            pass
    time.sleep(0.2)
    if cancelled():
        raise InterruptedError("Stopped before typing.")
    before_title = _live_title(win)
    from tools.computer import _type_text
    _type_text(args["text"])
    time.sleep(0.4)
    after_title = _live_title(win)
    if not _same_document(before_title, after_title, args["text"]):
        raise RuntimeError(f"The text went somewhere unexpected: {canonical} switched from '{_doc_name(before_title)}' to "
                           f"'{_doc_name(after_title)}' while I typed. Please check that window.")
    back = _text_of(_focused_editor() if new_tab else _editable(win))
    want = " ".join(args["text"].split())
    if want and want not in " ".join(back.split()):
        raise RuntimeError(f"I pasted the text into {canonical}, but reading the window back I don't see it (it shows '{back[:60]}').")
    if new_tab or _untitled(before_title):
        _started_here(window.get("hwnd"), after_title)
    where = f" (a new tab; '{_doc_name(showing)}' was left as it was)" if new_tab else ""
    return {"verified": True, "detail": f"Typed '{args['text'][:60]}' into {canonical}{where}; the window now shows it.", "app": canonical,
            "document": _doc_name(after_title)}


def _type_text_bg(args: Dict, canonical: str, window: Dict, win, cancelled: Callable[[], bool]) -> Dict:
    """type_text for a mission (2026-11-10, Mark VI part 3): the window stays where it is, behind the owner's windows. No
    focus, no Ctrl+N, no clipboard: the text goes into the window's own text area (uia.set_text_bg: UIA Value, else window
    messages to its native handle) and is read back from THAT text area, never from the global focused element (which is
    whatever the owner has in front)."""
    from core import pcvision
    from jarvis_pc.guardrails.stepfence import BackgroundUnavailable
    from jarvis_pc.uiact import uia
    showing = _settled_title(win, window["title"])
    if _foreign_document(canonical, showing):
        # In the foreground a new tab (Ctrl+N) is opened here; a chord can't be sent behind the owner's windows.
        raise BackgroundUnavailable(f"type into {canonical}", f"{canonical} is showing '{_doc_name(showing)}', which you "
                                    "didn't mention, and I'd have to open a new tab, which needs you at the PC")
    # 2026-11-10 review: never 'the first editor' (the foreground path's README lesson): Windows 11 Notepad keeps a hidden
    # tab's editor in the tree, enabled and even 'visible'. From behind his windows the tab in front can't be told apart,
    # so with more than one text area the step refuses instead of guessing.
    editors = _text_areas(win)
    if len(editors) > 1:
        raise BackgroundUnavailable(f"type into {canonical}", f"{canonical} has more than one document open and I can't tell "
                                    "which tab is in front from behind your windows, so it needs you at the PC")
    ctl = editors[0] if editors else _editable(win)
    name = ""
    try:
        name = (ctl.element_info.name or "") + " " + (ctl.element_info.automation_id or "")
    except Exception:  # noqa: BLE001
        pass
    if pcvision._SECRET.search(name):
        raise RuntimeError("That field looks like a password or card field; I never type into those.")
    if cancelled():
        raise InterruptedError("Stopped before typing.")
    before_title = _live_title(win)
    uia.set_text_bg(ctl, args["text"], append=True)
    after_title = _live_title(win)
    if not _same_document(before_title, after_title, args["text"]):
        raise RuntimeError(f"The text went somewhere unexpected: {canonical} switched from '{_doc_name(before_title)}' to "
                           f"'{_doc_name(after_title)}' while I typed. Please check that window.")
    back = _text_of(_editable(win))
    want = " ".join(args["text"].split())
    if want and want not in " ".join(back.split()):
        raise RuntimeError(f"I put the text into {canonical}, but reading the window back I don't see it (it shows '{back[:60]}').")
    if _untitled(before_title):
        _started_here(window.get("hwnd"), after_title)
    return {"verified": True, "detail": f"Typed '{args['text'][:60]}' into {canonical} without bringing it forward; the window "
            "now shows it.", "app": canonical, "document": _doc_name(after_title), "background": True}


def _is_save_title(t: str) -> bool:
    t = (t or "").casefold()
    return ("save" in t and "as" in t) or t.startswith("save")


def _save_dialog(uia, win):
    """The Save As dialog: a top-level window for classic apps, a window INSIDE the app's own window for Windows 11 Notepad."""
    try:
        for d in uia._desktop().windows():
            if _is_save_title(d.window_text()):
                return d
    except Exception:  # noqa: BLE001
        pass
    try:
        for d in win.wrapper_object().children(control_type="Window"):
            if _is_save_title(d.window_text()):
                return d
    except Exception:  # noqa: BLE001
        pass
    return None


def _file_name_field(dialog):
    """The common file dialog's 'File name' box (automation id 1001), never the first edit box: in the modern dialog that
    is the search or address box, and a path typed there searches instead of saving."""
    fallback = None
    for c in dialog.descendants(control_type="Edit"):
        try:
            if not (c.is_enabled() and c.is_visible()):
                continue
            info = c.element_info
            if (info.automation_id or "") == "1001":
                return c
            if "file name" in (info.name or "").casefold() and fallback is None:
                fallback = c
        except Exception:  # noqa: BLE001
            continue
    return fallback


def _focused_editor():
    """The control that has the keyboard focus (the editor of the tab just opened)."""
    from pywinauto.controls.uiawrapper import UIAWrapper
    from pywinauto.uia_defines import IUIA
    from pywinauto.uia_element_info import UIAElementInfo
    return UIAWrapper(UIAElementInfo(IUIA().get_focused_element()))


def save_document(args: Dict, *, approved=False, cancelled: Callable[[], bool] = lambda: False) -> Dict:
    """Ctrl+S in the named app, fill the Save As dialog with the path, and prove the file exists afterwards. An existing file is
    never overwritten here (core.authz makes that an irreversible step that asks first; even approved, it must be gone)."""
    from jarvis_pc.guardrails.stepfence import BackgroundUnavailable, is_background
    from jarvis_pc.uiact import uia
    from tools.computer import _type_text
    if is_background():
        # Saving needs Ctrl+S and the Save dialog in front; a mission refuses before it even makes the folder
        # (2026-11-10, Mark VI part 3).
        raise BackgroundUnavailable(f"save the document as {os.path.basename(str(args.get('path', '')))}",
                                    "saving needs the Save dialog in front, which needs you at the PC")
    path = os.path.expanduser(os.path.expandvars(args["path"].strip().strip('"')))
    if not os.path.isabs(path):
        path = os.path.join(os.path.expanduser("~"), "Documents", path)
    if not _in_user_space(path):
        raise RuntimeError("That path is outside your own folders, so I did not save there.")
    if os.path.exists(path) and not approved:
        raise RuntimeError(f"{path} already exists; I won't overwrite it without your yes.")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    canonical, window = _one_window(args["app"], cancelled)
    win = uia.window(window["title"])
    _require_foreground("save a document (Ctrl+S and the Save dialog)")    # a mission never saves this way (2026-11-10)
    uia.focus(win)
    if cancelled():
        raise InterruptedError("Stopped before saving.")
    live = _live_title(win) or window["title"]
    showing = _doc_name(live)
    if showing and not _untitled(showing) and showing.casefold() != os.path.basename(path).casefold() and not _ours(window.get("hwnd"), live):
        # Ctrl+S on a document that already has a file saves THAT file, silently: never on one the owner did not name.
        raise RuntimeError(f"{canonical} is showing '{showing}', not a new document for {os.path.basename(path)}, so I did not press "
                           "Save (it would have saved over that file).")
    _keys("ctrl", "s")
    deadline = time.monotonic() + 8
    dialog = None
    while time.monotonic() < deadline and dialog is None:
        if cancelled():
            raise InterruptedError("Stopped while waiting for the Save dialog (it may still be open).")
        time.sleep(0.3)
        dialog = _save_dialog(uia, win)
    if dialog is None:
        if os.path.exists(path):
            return {"verified": True, "detail": f"Saved {canonical}'s document to {path}.", "path": path}
        raise RuntimeError(f"Ctrl+S did not open a Save dialog in {canonical}, and {path} does not exist.")
    field = _file_name_field(dialog)
    if field is None:
        raise RuntimeError("The Save dialog has no 'File name' field I can see, so I typed nothing into it.")
    _require_foreground("fill the Save dialog")        # fence() plus: never in background (2026-11-10)
    field.set_focus()
    time.sleep(0.2)
    _keys("ctrl", "a")
    _type_text(path)
    time.sleep(0.2)
    _keys("enter")
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if os.path.exists(path):
            time.sleep(0.3)
            return {"verified": True, "detail": f"Saved {canonical}'s document to {path} ({os.path.getsize(path)} bytes).", "path": path}
        time.sleep(0.3)
    raise RuntimeError(f"The Save dialog closed but {path} did not appear.")


def read_screen(args: Dict, *, approved=False, cancelled: Callable[[], bool] = lambda: False) -> Dict:
    from tools import screenread
    app = (args.get("app") or "").strip()
    if app.casefold() in ("screen", "desktop", "the screen"):
        app = ""
    if not app:
        # No app named: the spoken answer is the open apps (pcstate, 0.05 s). The full-screen OCR (11.6 s warm, 27.5 s
        # measured live 2026-11-08) was computed and never said; it now fills the cache in the background, so a follow-up
        # ("what does it say?", "follow this") finds it ready.
        try:
            from core import pcstate
            head = pcstate.spoken()
        except Exception:  # noqa: BLE001
            head = ""
        if head:
            import threading
            threading.Thread(target=lambda: screenread.read_screen(""), name="screen-ocr", daemon=True).start()
            return {"verified": True, "detail": head, "screen": ""}
    text = screenread.read_screen(app)
    if not text.strip() or "Nothing readable locally" in text:
        raise RuntimeError("I could not read anything from the screen.")
    # Spoken: the open apps in plain words; a named window's readable text only when one was asked for. The raw text stays
    # in "screen" for the planner and for following instructions (owner 2026-11-07: the reply read out OCR symbols).
    try:
        from core import pcstate
        head = pcstate.spoken()
    except Exception:  # noqa: BLE001
        head = ""
    words = readable(text)
    detail = head
    if app and words:
        detail = (head + " " if head else "") + f"{app} shows: {words[:300]}"
    return {"verified": True, "detail": detail or (f"On screen: {words[:300]}" if words else "I could not read the screen."),
            "screen": text}


def readable(text: str) -> str:
    """Screen text a person could read aloud: fragments with real words, without control lists and symbol noise."""
    keep = []
    for part in re.split(r"\s*\|\s*|\n+", text or ""):
        part = re.sub(r"^(?:On-screen controls|Visible text):\s*", "", part.strip())
        part = re.sub(r"\s*\[[A-Za-z]+\]", "", part)                  # "Close [Button]" -> "Close"
        if len(re.findall(r"[A-Za-z]{3,}", part)) >= 2 or (len(re.findall(r"[A-Za-z]{4,}", part)) >= 1 and len(part) >= 8):
            keep.append(part)
    return " ".join(dict.fromkeys(keep))


# -------------------------------------------------------------------------------------------------------- terminal / health
def user_roots() -> List[str]:
    """The folders a terminal step may change under the owner's standing autonomy: the profile and the other FIXED drives
    (not removable, network or optical ones: GetDriveTypeW == DRIVE_FIXED), never the system drive's root."""
    import string
    out = [os.path.expanduser("~")]
    if os.name == "nt":
        import ctypes
        system = (os.environ.get("SystemDrive", "C:") + "\\").upper()
        for d in string.ascii_uppercase:
            root = f"{d}:\\"
            if root.upper() != system and os.path.isdir(root) and ctypes.windll.kernel32.GetDriveTypeW(root) == 3:
                out.append(root)
    return out


def expand_home(command: str) -> str:
    """'~/Downloads' and '~\\Downloads' as the shell would read them, so the gate classifies the real path."""
    home = os.path.expanduser("~")
    return re.sub(r"(?<![\w])~(?=[\\/])", home.replace("\\", "\\\\"), command)


_PROFILE_PATH = re.compile(r"(?i)\b[a-z]:[\\/]+users[\\/]+([^\\/\s\"'<>|]+)")
_SHARED_PROFILES = {"public", "default", "default user", "all users"}


def real_home_path(text: str) -> str:
    """A model that does not know the owner's user name writes C:\\Users\\Owner\\Downloads (2026-11-06 acceptance run). A
    profile that does not exist on this PC can only mean the owner's own, so it becomes the real home. A profile that DOES
    exist (another user's) is left exactly as written: the gate then classifies it as outside the owner's space and asks."""
    if not isinstance(text, str) or "users" not in text.casefold():
        return text
    home = os.path.expanduser("~")
    users_dir = os.path.dirname(home)

    def fix(m):
        name = m.group(1)
        if name.casefold() in _SHARED_PROFILES or os.path.isdir(os.path.join(users_dir, name)):
            return m.group(0)
        return home

    return _PROFILE_PATH.sub(fix, text)


_SPOKEN_EXT = re.compile(r"(?<=\w)[\s_]+(?:dot\s+)?(txt|md|csv|json|log|py|html|pdf|docx?)$", re.I)


def spoken_file_name(name: str) -> str:
    """'Hello TXT' / 'notes dot md' (how speech recognition writes a file name) -> 'Hello.txt' / 'notes.md'."""
    return _SPOKEN_EXT.sub(lambda m: "." + m.group(1).lower(), (name or "").strip())


def spoken_paths_in(command: str) -> str:
    """spoken_path for the absolute paths inside a shell command (quoted ones, or unquoted ones without spaces)."""
    def fix(m):
        quote, body = m.group(1) or "", m.group(2)
        return quote + spoken_path(body) + quote
    return re.sub(r"""(["'])([a-zA-Z]:[\\/][^"']*)\1|()(?<!\S)([a-zA-Z]:[\\/]\S*)""",
                  lambda m: fix(m) if m.group(2) else spoken_path(m.group(4)), command or "")


def heard(key: str, value):
    """One plan argument as speech recognition may have written it, made to mean what exists on this PC: an invented
    profile is the real home, 'Jarvis acceptance' is the existing JarvisAcceptance, 'Hello TXT' is Hello.txt."""
    if not isinstance(value, str):
        return value
    v = real_home_path(value)
    if key == "command":
        return spoken_paths_in(v)
    if key in ("path", "file", "zip"):
        v = spoken_file_name(v)
    if key in ("path", "folder", "dir", "file", "dest", "zip"):
        v = spoken_path(v)
    return v


def _squash_name(s: str) -> str:
    return re.sub(r"[^0-9a-z]+", "", s.casefold())


def spoken_path(path: str) -> str:
    """A path as heard ('C:\\Users\\me\\Documents\\Jarvis acceptance') -> the existing one ('...\\JarvisAcceptance') when a
    missing part matches exactly ONE existing folder entry ignoring spaces, case and punctuation. Anything else is left
    as written (a new name stays new; two look-alikes are not guessed between)."""
    if not isinstance(path, str) or not re.match(r"^[a-z]:[\\/]", path.strip(), re.I) or os.path.exists(path):
        return path
    parts = re.split(r"[\\/]+", path.strip().rstrip("\\/"))
    current = parts[0] + "\\"
    for i, part in enumerate(parts[1:], 1):
        nxt = os.path.join(current, part)
        if not os.path.exists(nxt):
            try:
                same = [e for e in os.listdir(current) if _squash_name(e) == _squash_name(part)]
            except OSError:
                return path
            if len(same) != 1:
                return path if i < len(parts) - 1 else os.path.join(current, part)   # only the last part may be new
            nxt = os.path.join(current, same[0])
        current = nxt
    return current


def terminal_tier(command: str) -> int:
    """jarvis_pc's tier for a shell command, run in the owner's home, with one hardening: a T1 (reversible) change that
    touches any path outside owner space (_in_user_space: canonical, junction-aware, no AppData/credentials/system/drive
    roots) is T2 and asks first. Used by core.authz (ask or run) and run_terminal (run), so both judge the same way."""
    from jarvis_pc.guardrails.allowlist import extract_paths
    from jarvis_pc.guardrails.tiers import PathAllowlist, classify
    home = os.path.expanduser("~")
    command = expand_home(command)
    tier = int(classify(command, cwd=home, allowlist=PathAllowlist(user_roots())).tier)
    if tier == 1:
        for p in extract_paths(command):
            full = p if re.match(r"^[a-z]:[\\/]|^\\\\", p, re.I) else os.path.join(home, p)
            if not _in_user_space(full):
                return 2
    return tier


def run_terminal(args: Dict, *, approved=False, cancelled: Callable[[], bool] = lambda: False) -> Dict:
    """One shell command through jarvis_pc's gate and runner: read-only and reversible tiers (inside the owner's own folders) run
    under the owner's autonomy; a T2 command runs only with the owner's yes to this exact plan; T3 never."""
    from jarvis_pc.actions.shell import run as shell_run
    from jarvis_pc.guardrails.gate import Guardrails
    from jarvis_pc.guardrails.tiers import Tier
    from tools import pcskills
    command = expand_home(args["command"].strip())
    home = os.path.expanduser("~")                 # the terminal step's working folder: the owner's own
    tier = Tier(terminal_tier(command))
    if tier >= Tier.T3:
        raise RuntimeError("That command is one I never run.")
    if tier >= Tier.T2 and not approved:
        raise RuntimeError(f"'{command[:60]}' changes the PC (tier {tier.name}); it needs your yes.")
    s = pcskills.skills()
    # dry_run=False: this is the owner's request being carried out, not a plan preview. Guardrails defaults to dry-run (fail
    # safe), and left at the default every T1 change was audited and silently not executed (2026-11-06: 'mkdir
    # ~/Downloads/jarvis-test' -> "dry-run: not executed"). The gate still classifies, snapshots before T1/T2 (pre_change),
    # needs the approver for T2 and refuses T3.
    guard = Guardrails(allowlist_roots=user_roots(), audit_path=os.path.join(s.root, "audit.jsonl"),
                       approver=(lambda req: True) if approved else None, pre_change=s.pre_change, dry_run=False)
    # Stopping the command, not only the wait: the step's time limit (stepfence) and the owner's stop both trip the gate's
    # kill switch, and jarvis_pc's runner then kills the command's whole process tree within a quarter second.
    import threading
    from jarvis_pc.guardrails import stepfence
    _fence(f"run '{command[:40]}'")
    token = stepfence.current()
    if token is not None:
        token.on_expire(lambda: guard.killswitch.trigger("the step passed its time limit"))
    done = threading.Event()

    def watch():
        while not done.wait(0.2):
            if cancelled():
                guard.killswitch.trigger("stop requested")
                return
    threading.Thread(target=watch, daemon=True, name="run_terminal-stop").start()
    try:
        res = shell_run(guard, command, cwd=home, intent="gateway: run_terminal", timeout=TERMINAL_TIMEOUT_S)
    finally:
        done.set()
    if res.killed:
        raise RuntimeError(f"'{command[:60]}' was stopped before it finished (its programs were ended).")
    out = (res.stdout or "").strip()
    err = (res.stderr or "").strip()
    if not res.ran:
        # the denial comes last, after the classification text (which can be long enough to hide it)
        raise RuntimeError("The command was not allowed to run: " + "; ".join(res.decision.reasons[-2:])[:200])
    if not res.ok:
        raise RuntimeError(f"'{command[:60]}' failed (exit {res.exit_code}{', ' + res.error_kind if res.error_kind else ''}): {(err or out)[:300]}")
    return {"verified": True, "detail": f"Ran '{command[:80]}'" + (f": {out[:700]}" if out else " (no output)."), "stdout": out[:4000]}


def investigate_slowness(args: Dict, *, approved=False, cancelled: Callable[[], bool] = lambda: False) -> Dict:
    """Facts behind 'my computer is slow': CPU, memory, disk, the busiest processes, uptime, and the read-only diagnosis."""
    import psutil
    facts = []
    cpu = psutil.cpu_percent(interval=1.0)
    mem = psutil.virtual_memory()
    facts.append(f"CPU {cpu:.0f}% busy; memory {mem.percent:.0f}% used ({mem.used / 2**30:.1f} of {mem.total / 2**30:.1f} GB)")
    try:
        du = psutil.disk_usage(os.environ.get("SystemDrive", "C:") + "\\")
        facts.append(f"system drive {du.percent:.0f}% full ({du.free / 2**30:.0f} GB free)")
    except Exception:  # noqa: BLE001
        pass
    procs = []
    for p in psutil.process_iter(["name", "cpu_percent", "memory_info"]):
        try:
            procs.append((p.info["cpu_percent"] or 0.0, (p.info["memory_info"].rss if p.info["memory_info"] else 0), p.info["name"] or "?"))
        except Exception:  # noqa: BLE001
            continue
    time.sleep(0.5)
    procs = []
    for p in psutil.process_iter(["name", "cpu_percent", "memory_info"]):
        try:
            procs.append((p.cpu_percent(interval=None), (p.info["memory_info"].rss if p.info["memory_info"] else 0), p.info["name"] or "?"))
        except Exception:  # noqa: BLE001
            continue
    by_cpu = sorted(procs, key=lambda x: -x[0])[:4]
    by_mem = sorted(procs, key=lambda x: -x[1])[:4]
    facts.append("busiest: " + ", ".join(f"{n} {c:.0f}%" for c, _, n in by_cpu if c > 0) or "busiest: nothing is using much CPU")
    facts.append("biggest in memory: " + ", ".join(f"{n} {m / 2**30:.1f} GB" for _, m, n in by_mem))
    up = time.time() - psutil.boot_time()
    from core.words import plural
    facts.append("up for " + plural(round(up / 3600), "hour") + (" (a restart may help)" if up > 7 * 86400 else ""))
    if cancelled():
        raise InterruptedError("Stopped before the diagnosis.")
    try:
        from tools import pcskills
        report = pcskills.skills().diagnose("slow")
        facts.append("diagnosis: " + " ".join(report.split())[:700])
    except Exception:  # noqa: BLE001
        facts.append("the detailed diagnosis could not run")
    return {"verified": True, "detail": "; ".join(facts), "cpu": cpu, "memory_percent": mem.percent}
