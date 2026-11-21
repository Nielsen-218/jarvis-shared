"""Temporary visible fullscreen acceptance fixture; no input injection or real files.
Run with --info pointing to an ISOLATED companion_harness info JSON.
Creates its OWN Tk window for six seconds and records renderer suspension/recovery.
"""
import argparse
import ctypes
import json
import time
import tkinter as tk
import urllib.request
from pathlib import Path


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--info",type=Path,required=True)
    parser.add_argument("--out",type=Path,required=True)
    parser.add_argument("--seconds",type=float,default=6)
    args=parser.parse_args()
    info=json.loads(args.info.read_text(encoding="utf-8"))
    root=tk.Tk();root.title("Jarvis acceptance fullscreen")
    root.attributes("-fullscreen",True);root.configure(background="#122336")
    tk.Label(root,text="JARVIS fullscreen acceptance test\nCloses automatically in 6 seconds",fg="white",bg="#122336",font=("Segoe UI",22)).pack(expand=True)
    records=[];start=time.monotonic();closed=False
    # Record actual Win32 foreground geometry: a failed fixture focus must not
    # be mistaken for a product fullscreen-detection failure.
    user32=ctypes.WinDLL("user32",use_last_error=True)
    class Rect(ctypes.Structure):
        _fields_=[(name,ctypes.c_long) for name in ("left","top","right","bottom")]
    user32.GetForegroundWindow.restype=ctypes.c_void_p
    user32.GetWindowRect.argtypes=[ctypes.c_void_p,ctypes.POINTER(Rect)]
    def sample():
        nonlocal closed
        elapsed=time.monotonic()-start
        if elapsed>args.seconds and not closed:
            closed=True;root.withdraw()
        try:
            with urllib.request.urlopen(info["renderer_state_url"],timeout=1) as response: state=json.load(response)
            hwnd=user32.GetForegroundWindow();rect=Rect();user32.GetWindowRect(hwnd,ctypes.byref(rect))
            records.append({"foreground":int(hwnd or 0),"rect":[rect.left,rect.top,rect.right-rect.left,rect.bottom-rect.top],"fixture":root.winfo_id(),"seconds":round(elapsed,2),"suspended":state.get("suspended"),"fps":state.get("fps"),
                "scene":(state.get("presence")or{}).get("scene"),"body_play":(state.get("direct_play")or{}).get("active")})
        except OSError: pass
        if elapsed>args.seconds+4:
            args.out.parent.mkdir(parents=True,exist_ok=True);args.out.write_text(json.dumps(records,indent=2),encoding="utf-8");root.destroy();return
        root.after(250,sample)
    root.after(200,lambda:root.focus_force());root.after(500,sample);root.mainloop()
    return 0


if __name__=="__main__":raise SystemExit(main())
