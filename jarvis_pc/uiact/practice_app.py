"""A native Win32 practice window for the school's mouse/keyboard/window subjects.

Real Windows controls (BUTTON, EDIT, LISTBOX, checkbox, STATIC), so UI Automation sees them exactly as it would in any real app.
Everything that happens is appended to a log file; that log is the ground truth the school grades against, never a model.

    python -m jarvis_pc.uiact.practice_app --config cfg.json --log events.log

cfg.json: {"title", "buttons": [labels], "field": label, "items": [...], "check": label, "status": text, "go_delay": seconds,
           "x", "y", "w", "h"}. Layout order and positions are shuffled by the harness per variant.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import sys
import time

import win32api
import win32con
import win32gui

CLASS = "JarvisPracticeWindow"
ID_BUTTON, ID_FIELD, ID_LIST, ID_CHECK, ID_SUBMIT, ID_GO = 1000, 2000, 3000, 4000, 5000, 6000


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--log", required=True)
    args = ap.parse_args()
    cfg = json.load(open(args.config, encoding="utf-8"))
    log = open(args.log, "a", encoding="utf-8", buffering=1)

    def note(msg: str) -> None:
        log.write(f"{time.time():.3f} {msg}\n")

    state = {"go_at": time.time() + float(cfg.get("go_delay", 2.5)), "hwnds": {}}

    def wndproc(hwnd, msg, wparam, lparam):
        if msg == win32con.WM_COMMAND:
            cid, code, src = win32api.LOWORD(wparam), win32api.HIWORD(wparam), lparam
            if ID_BUTTON <= cid < ID_BUTTON + 100 and code == win32con.BN_CLICKED:
                note("click:" + cfg["buttons"][cid - ID_BUTTON])
            elif cid == ID_SUBMIT and code == win32con.BN_CLICKED:
                note("submit:" + cfg["field"] + ":" + win32gui.GetWindowText(state["hwnds"]["field"]))
            elif cid == ID_GO and code == win32con.BN_CLICKED:
                note("click:Go")
            elif cid == ID_LIST and code == win32con.LBN_SELCHANGE:
                lb = state["hwnds"]["list"]
                i = win32gui.SendMessage(lb, win32con.LB_GETCURSEL, 0, 0)
                if i >= 0:
                    note("select:" + cfg["items"][i])
            elif cid == ID_CHECK and code == win32con.BN_CLICKED:
                on = win32gui.SendMessage(state["hwnds"]["check"], win32con.BM_GETCHECK, 0, 0)
                note(f"check:{cfg['check']}:{1 if on else 0}")
            return 0
        if msg == win32con.WM_TIMER:
            sel = win32gui.SendMessage(state["hwnds"]["list"], win32con.LB_GETCURSEL, 0, 0)  # also sees selections made by UI Automation
            if sel != state.get("sel", -1) and sel >= 0:
                state["sel"] = sel
                note("select:" + cfg["items"][sel])
            if time.time() >= state["go_at"] and not state.get("go_on"):
                win32gui.EnableWindow(state["hwnds"]["go"], True)
                state["go_on"] = True
            return 0
        if msg == win32con.WM_CLOSE:
            note("closed")
            win32gui.DestroyWindow(hwnd)
            return 0
        if msg == win32con.WM_DESTROY:
            win32gui.PostQuitMessage(0)
            return 0
        return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)

    wc = win32gui.WNDCLASS()
    wc.hInstance = win32api.GetModuleHandle(None)
    wc.lpszClassName = CLASS
    wc.lpfnWndProc = wndproc
    wc.hbrBackground = win32con.COLOR_WINDOW + 1
    wc.hCursor = win32gui.LoadCursor(0, win32con.IDC_ARROW)
    win32gui.RegisterClass(wc)
    hwnd = win32gui.CreateWindow(CLASS, cfg["title"], win32con.WS_OVERLAPPEDWINDOW, cfg.get("x", 120), cfg.get("y", 120),
                                 cfg.get("w", 520), cfg.get("h", 420), 0, 0, wc.hInstance, None)
    child = win32con.WS_CHILD | win32con.WS_VISIBLE

    def make(cls, text, x, y, w, h, style, cid):
        return win32gui.CreateWindow(cls, text, child | style, x, y, w, h, hwnd, cid, wc.hInstance, None)

    y = 10
    for i, label in enumerate(cfg["buttons"]):
        make("BUTTON", label, cfg["bx"][i], y, 110, 28, win32con.BS_PUSHBUTTON | win32con.WS_TABSTOP, ID_BUTTON + i)
        y += 34
    make("STATIC", cfg["field"], 10, y + 4, 110, 22, 0, 0)               # the label right before the edit names it for UI Automation
    state["hwnds"]["field"] = make("EDIT", "", 125, y, 170, 24, win32con.WS_BORDER | win32con.ES_AUTOHSCROLL | win32con.WS_TABSTOP, ID_FIELD)
    make("BUTTON", "Submit", 305, y, 80, 26, win32con.BS_PUSHBUTTON | win32con.WS_TABSTOP, ID_SUBMIT)
    y += 34
    lb = make("LISTBOX", "", 10, y, 200, 70, win32con.LBS_NOTIFY | win32con.WS_BORDER | win32con.WS_VSCROLL | win32con.WS_TABSTOP, ID_LIST)
    state["hwnds"]["list"] = lb
    for it in cfg["items"]:
        win32gui.SendMessage(lb, win32con.LB_ADDSTRING, 0, it)
    y += 78
    state["hwnds"]["check"] = make("BUTTON", cfg["check"], 10, y, 200, 24, win32con.BS_AUTOCHECKBOX | win32con.WS_TABSTOP, ID_CHECK)
    y += 30
    go = make("BUTTON", "Go", 10, y, 80, 26, win32con.BS_PUSHBUTTON | win32con.WS_TABSTOP | win32con.WS_DISABLED, ID_GO)
    state["hwnds"]["go"] = go
    state["hwnds"]["status"] = make("STATIC", cfg["status"], 110, y + 4, 280, 22, 0, 0)
    game = {"x": 0, "y": 0, "size": int(cfg.get("game_size", 5)), "on": bool(cfg.get("game"))}
    ctypes.windll.user32.SetTimer(hwnd, 1, 200, None)
    win32gui.ShowWindow(hwnd, win32con.SW_SHOWNOACTIVATE)
    win32gui.UpdateWindow(hwnd)
    note("ready")

    while True:                                   # own loop so Ctrl+S can be seen wherever the focus is inside the window
        ret, msg = win32gui.GetMessage(None, 0, 0)
        if ret <= 0:
            break
        m_hwnd, m_msg, m_w = msg[0], msg[1], msg[2]
        if m_msg == win32con.WM_KEYDOWN and m_w == ord("S") and win32api.GetKeyState(win32con.VK_CONTROL) < 0:
            note("hotkey:ctrl+s")
            continue
        if game["on"] and m_msg == win32con.WM_KEYDOWN and m_w in (0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28):
            last = game["size"] - 1
            if m_w == 0x24:                                              # Home / End jump to the left / right edge
                game["x"] = 0
            elif m_w == 0x23:
                game["x"] = last
            elif m_w == 0x21:                                            # PageUp / PageDown jump to the top / bottom edge
                game["y"] = 0
            elif m_w == 0x22:
                game["y"] = last
            else:                                                        # arrows move one square; the walls stop the player
                dx, dy = {0x25: (-1, 0), 0x26: (0, -1), 0x27: (1, 0), 0x28: (0, 1)}[m_w]
                game["x"] = min(max(game["x"] + dx, 0), last)
                game["y"] = min(max(game["y"] + dy, 0), last)
            note(f"move:{game['x']},{game['y']}")
            win32gui.SetWindowText(state["hwnds"]["status"], f"Player at column {game['x']} row {game['y']}")
            continue
        win32gui.TranslateMessage(msg)
        win32gui.DispatchMessage(msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
