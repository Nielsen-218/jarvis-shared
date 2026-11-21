"""Check the foreground Windows process without touching game memory or inputs.

Return False for missing/unknown focus. A coach that cannot verify it is
watching the actual game should abstain, not describe someone's desktop.
No game injection, anti-cheat hooks, overlay capture or shell commands.
"""
from __future__ import annotations
import sys

NAMES={
    "apex":frozenset({"r5apex.exe","r5apex_dx12.exe"}),
    "cod":frozenset({"cod.exe","codhq.exe"}),
}

def game_in_foreground(game:str,platform:str|None=None,
                       process_lookup=None)->bool:
    if game not in NAMES:return False
    system=sys.platform if platform is None else platform
    if process_lookup is not None:
        try:return str(process_lookup()).lower() in NAMES[game]
        except Exception:return False
    if system!="win32":return False
    try:
        import ctypes
        from ctypes import wintypes
        import psutil
        user32=ctypes.windll.user32
        user32.GetForegroundWindow.restype=wintypes.HWND
        handle=user32.GetForegroundWindow()
        if not handle:return False
        pid=wintypes.DWORD()
        user32.GetWindowThreadProcessId.argtypes=(wintypes.HWND,ctypes.POINTER(wintypes.DWORD))
        user32.GetWindowThreadProcessId(handle,ctypes.byref(pid))
        if not pid.value:return False
        name=psutil.Process(pid.value).name().lower()
        return name in NAMES[game]
    except (AttributeError,OSError,LookupError,ImportError,Exception):
        # Intentional conservative deny on unexpected Windows metadata errors.
        return False
