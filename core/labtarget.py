"""The purpose-built practice target for the desktop lab worlds (core/labworlds).

A tiny program with REAL Win32 controls (so UI Automation sees them the way it sees a real app), started by the lab ON the
hidden lab desktop and killed by the lab. It owns nothing of the owner's: every window is its own, every file it writes
goes into the scratch folder named in its spec, and it opens nothing outside itself.

    python -m core.labtarget <spec.json>

spec = {"scratch": folder, "windows": [{"title", "edit": text|None, "doc": file name|None, "buttons": [names],
                                        "files": [names]|None, "front": bool}]}
Buttons: "Save" writes the edit box to scratch/<doc>; "Open" opens a viewer window "<selected file> - LabView"; any other
button only records itself in the window's status line ("pressed=Apply,Export").

Two extras for the close_foreground PRODUCTION check (core/labworlds.generate_check):
  * a window with "unsaved": true answers a close request with a prompt window "Unsaved changes - <title>" (buttons Save,
    Don't Save, Cancel) instead of closing. Every prompt button that is pressed leaves a flag file scratch/prompt-<button>.flag,
    and "Don't Save" also writes scratch/discarded.flag, so the judge can read what was chosen from disk.
  * a spec with "foreign": true builds windows of a DIFFERENT window class (FOREIGN_CLASS), started as its own process: the
    stand-in for a program the lab did not create for the method (it must never be closed, pressed or typed into).
"""
import json
import sys
from pathlib import Path

CLASS = "JarvisLabTarget"
FOREIGN_CLASS = "JarvisLabForeign"
PROMPT_PREFIX = "Unsaved changes - "
PROMPT_BUTTONS = ["Save", "Don't Save", "Cancel"]
ID_EDIT, ID_STATUS, ID_LIST, ID_BUTTON0 = 40, 50, 60, 100
VIEWER_APP = "LabView"


def main(spec_path) -> int:
    import win32api
    import win32con as c
    import win32gui as g
    spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    scratch = Path(spec["scratch"])
    cls = FOREIGN_CLASS if spec.get("foreign") else CLASS     # this process's window class (a foreign program is a different one)
    inst = win32api.GetModuleHandle(None)
    wins = {}                                               # hwnd -> {"spec", "pressed", "edit", "status", "list"}

    def status(hwnd, text):
        g.SetWindowText(wins[hwnd]["status"], text)

    def viewer(name):
        hwnd = g.CreateWindow(cls, f"{name} - {VIEWER_APP}", c.WS_OVERLAPPEDWINDOW | c.WS_VISIBLE, 260, 220, 420, 200, 0, 0, inst, None)
        wins[hwnd] = {"spec": {"buttons": []}, "pressed": [], "edit": 0, "list": 0,
                      "status": g.CreateWindow("STATIC", "viewing " + name, c.WS_CHILD | c.WS_VISIBLE, 10, 10, 380, 22, hwnd, ID_STATUS, inst, None)}

    def command(hwnd, cid):
        st = wins.get(hwnd)
        names = (st or {}).get("spec", {}).get("buttons") or []
        if not st or not ID_BUTTON0 <= cid < ID_BUTTON0 + len(names):
            return
        name = names[cid - ID_BUTTON0]
        if st.get("prompt_for"):                            # the unsaved-changes prompt: record the choice on disk, then act on it
            owner = st["prompt_for"]
            (scratch / f"prompt-{name.replace(chr(39), '').replace(' ', '-').lower()}.flag").write_text("1", encoding="utf-8")
            if name == "Don't Save":
                (scratch / "discarded.flag").write_text("1", encoding="utf-8")
            if name == "Save" and wins.get(owner, {}).get("edit") and wins[owner]["spec"].get("doc"):
                (scratch / wins[owner]["spec"]["doc"]).write_text(g.GetWindowText(wins[owner]["edit"]), encoding="utf-8")
            if name == "Cancel":
                wins[owner]["prompt"] = 0
            g.DestroyWindow(hwnd)
            if name != "Cancel" and owner in wins:
                wins[owner]["spec"]["unsaved"] = False
                g.DestroyWindow(owner)
            return
        if name == "Save" and st["edit"] and st["spec"].get("doc"):
            (scratch / st["spec"]["doc"]).write_text(g.GetWindowText(st["edit"]), encoding="utf-8")
        elif name == "Open" and st["list"]:
            i = g.SendMessage(st["list"], c.LB_GETCURSEL, 0, 0)
            files = st["spec"].get("files") or []
            if 0 <= i < len(files):
                viewer(files[i])
        st["pressed"].append(name)
        status(hwnd, "pressed=" + ",".join(st["pressed"]))

    def prompt(owner):
        st = wins[owner]
        title = PROMPT_PREFIX + st["spec"]["title"]
        hwnd = g.CreateWindow(cls, title, c.WS_OVERLAPPEDWINDOW | c.WS_VISIBLE, 120, 120, 460, 160, owner, 0, inst, None)
        wins[hwnd] = {"spec": {"buttons": list(PROMPT_BUTTONS)}, "pressed": [], "edit": 0, "list": 0, "prompt_for": owner,
                      "status": g.CreateWindow("STATIC", "asking", c.WS_CHILD | c.WS_VISIBLE, 10, 90, 420, 22, hwnd, ID_STATUS, inst, None)}
        for i, name in enumerate(PROMPT_BUTTONS):
            g.CreateWindow("BUTTON", name, c.WS_CHILD | c.WS_VISIBLE | c.BS_PUSHBUTTON, 10 + 105 * i, 40, 100, 28, hwnd, ID_BUTTON0 + i, inst, None)
        st["prompt"] = hwnd

    def proc(hwnd, msg, wparam, lparam):
        if msg == c.WM_CLOSE and wins.get(hwnd, {}).get("spec", {}).get("unsaved"):
            if not wins[hwnd].get("prompt"):
                prompt(hwnd)
            return 0                                        # an unsaved document does not close: it asks
        if msg == c.WM_COMMAND and (wparam >> 16) & 0xFFFF == c.BN_CLICKED:
            command(hwnd, wparam & 0xFFFF)
            return 0
        if msg == c.WM_DESTROY:
            owner = wins.get(hwnd, {}).get("prompt_for")
            if owner in wins:
                wins[owner]["prompt"] = 0
            wins.pop(hwnd, None)
            if not wins:
                g.PostQuitMessage(0)
            return 0
        return g.DefWindowProc(hwnd, msg, wparam, lparam)

    wc = g.WNDCLASS()
    wc.lpfnWndProc, wc.lpszClassName, wc.hInstance = proc, cls, inst
    wc.hbrBackground, wc.hCursor = c.COLOR_WINDOW + 1, g.LoadCursor(0, c.IDC_ARROW)
    g.RegisterClass(wc)
    child = c.WS_CHILD | c.WS_VISIBLE
    front = None
    for n, w in enumerate(spec["windows"]):
        hwnd = g.CreateWindow(cls, w["title"], c.WS_OVERLAPPEDWINDOW | c.WS_VISIBLE, 40 + 30 * n, 40 + 30 * n, 560, 380, 0, 0, inst, None)
        st = wins[hwnd] = {"spec": w, "pressed": [], "edit": 0, "list": 0, "status": 0}
        if w.get("edit") is not None:
            st["edit"] = g.CreateWindow("EDIT", w["edit"], child | c.WS_BORDER | c.ES_MULTILINE | c.ES_AUTOVSCROLL, 10, 10, 520, 120,
                                        hwnd, ID_EDIT, inst, None)
        if w.get("files"):
            st["list"] = g.CreateWindow("LISTBOX", "Files", child | c.WS_BORDER | c.LBS_NOTIFY, 10, 140, 250, 110, hwnd, ID_LIST, inst, None)
            for name in w["files"]:
                g.SendMessage(st["list"], c.LB_ADDSTRING, 0, name)
        for i, name in enumerate(w.get("buttons") or []):
            g.CreateWindow("BUTTON", name, child | c.BS_PUSHBUTTON, 10 + 105 * i, 260, 100, 28, hwnd, ID_BUTTON0 + i, inst, None)
        st["status"] = g.CreateWindow("STATIC", "pressed=", child, 10, 300, 520, 22, hwnd, ID_STATUS, inst, None)
        if w.get("front"):
            front = hwnd
    if front:                                               # "in front" on a desktop nobody sees = top of the Z order
        g.SetWindowPos(front, c.HWND_TOP, 0, 0, 0, 0, c.SWP_NOMOVE | c.SWP_NOSIZE)
    (scratch / "ready").write_text("1", encoding="utf-8")
    g.PumpMessages()
    return 0


if __name__ == "__main__" and len(sys.argv) == 2:
    sys.exit(main(sys.argv[1]))
