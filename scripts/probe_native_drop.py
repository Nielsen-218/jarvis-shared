"""Proof that NATIVE Windows file-drop recovers a dropped file's FULL PATH — the thing the HTML5
drop route cannot do (it only exposes name + bytes).

This is the mechanism behind completing companion feature #152 (launch a dropped shortcut by its
exact path). It creates a real (message-only) window, enables file drops with DragAcceptFiles, then
delivers a synthetic WM_DROPFILES carrying a real path and shows the window recovering that exact
path with DragQueryFileW — exactly what a real Explorer drop delivers via CF_HDROP.

Run:  python scripts/probe_native_drop.py  (Windows only; prints JSON, exits non-zero on mismatch)
"""
from __future__ import annotations

import ctypes
import json
import sys
from ctypes import wintypes

WM_DROPFILES = 0x0233
GMEM_MOVEABLE = 0x0002
GMEM_ZEROINIT = 0x0040

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)

# Set 64-bit-safe signatures (default restype is 32-bit int, which truncates handles/pointers).
kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
user32.DefWindowProcW.restype = ctypes.c_longlong
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.CreateWindowExW.restype = wintypes.HWND
user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
shell32.DragAcceptFiles.argtypes = [wintypes.HWND, wintypes.BOOL]
shell32.DragFinish.argtypes = [wintypes.HANDLE]
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
user32.SendMessageW.restype = ctypes.c_longlong
user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.RegisterClassW.restype = wintypes.ATOM

WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_longlong, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASS(ctypes.Structure):
    _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]


class DROPFILES(ctypes.Structure):
    _fields_ = [("pFiles", wintypes.DWORD), ("pt_x", wintypes.LONG), ("pt_y", wintypes.LONG),
                ("fNC", wintypes.BOOL), ("fWide", wintypes.BOOL)]


def _make_hdrop(paths):
    """Build an HDROP (HGLOBAL) exactly as the shell delivers on a real drop: a DROPFILES header
    followed by a double-null-terminated list of wide paths."""
    joined = "".join(p + "\0" for p in paths) + "\0"
    blob = bytes(joined.encode("utf-16-le"))
    header = DROPFILES(pFiles=ctypes.sizeof(DROPFILES), pt_x=10, pt_y=10, fNC=0, fWide=1)
    total = ctypes.sizeof(DROPFILES) + len(blob)
    hglobal = kernel32.GlobalAlloc(GMEM_MOVEABLE | GMEM_ZEROINIT, total)
    ptr = kernel32.GlobalLock(hglobal)
    ctypes.memmove(ptr, ctypes.byref(header), ctypes.sizeof(DROPFILES))
    ctypes.memmove(ptr + ctypes.sizeof(DROPFILES), blob, len(blob))
    kernel32.GlobalUnlock(hglobal)
    return hglobal


def main() -> int:
    if sys.platform != "win32":
        print(json.dumps({"skipped": "not windows"}))
        return 0

    expected = [r"C:\Users\Example\Desktop\Notepad.lnk", r"C:\Tools\build.bat"]
    captured: list[str] = []

    shell32.DragQueryFileW.argtypes = [wintypes.HANDLE, wintypes.UINT, wintypes.LPWSTR, wintypes.UINT]
    shell32.DragQueryFileW.restype = wintypes.UINT

    def _recover(hdrop):
        n = shell32.DragQueryFileW(hdrop, 0xFFFFFFFF, None, 0)       # how many files
        for i in range(n):
            need = shell32.DragQueryFileW(hdrop, i, None, 0) + 1
            buf = ctypes.create_unicode_buffer(need)
            shell32.DragQueryFileW(hdrop, i, buf, need)
            captured.append(buf.value)                               # the FULL path — what HTML5 can't give

    def _wndproc(hwnd, msg, wparam, lparam):
        if msg == WM_DROPFILES:
            _recover(wparam)
            shell32.DragFinish(wparam)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    proc = WNDPROC(_wndproc)
    hinst = kernel32.GetModuleHandleW(None)
    cls = WNDCLASS()
    cls.lpfnWndProc = proc
    cls.hInstance = hinst
    cls.lpszClassName = "JarvisDropProbe"
    atom = user32.RegisterClassW(ctypes.byref(cls))
    if not atom:
        raise ctypes.WinError(ctypes.get_last_error())

    # a normal, never-shown 0x0 window (DragAcceptFiles + WM_DROPFILES work on any window)
    hwnd = user32.CreateWindowExW(0, "JarvisDropProbe", "probe", 0, 0, 0, 0, 0, None, None, hinst, None)
    if not hwnd:
        raise ctypes.WinError(ctypes.get_last_error())

    shell32.DragAcceptFiles(hwnd, True)                             # enable file drops on this window
    hdrop = _make_hdrop(expected)
    user32.SendMessageW(hwnd, WM_DROPFILES, hdrop, 0)               # deliver a drop the way the OS would
    user32.DestroyWindow(hwnd)
    user32.UnregisterClassW("JarvisDropProbe", hinst)

    ok = captured == expected
    print(json.dumps({"ok": ok, "expected": expected, "captured": captured}))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
