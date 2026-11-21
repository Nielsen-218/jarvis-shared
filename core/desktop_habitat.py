"""Real desktop ledges; read-only shell observation plus pure, physical-pixel anchor decisions.

No control is drawn, clicked, moved or renamed. Slow UIA runs on ONE daemon worker,
never in the render/presence loops. Expired observations are dropped, not invented.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
import os
import threading
import time

@dataclass(frozen=True)
class Surface:
    id: str
    kind: str
    rect: tuple
    edge: str = "bottom"
    dpi: int = 96
    auto_hide: bool = False
    hwnd: int = 0
    occluders: tuple = ()
    owner_rect: tuple = ()


def _overlap(a, b):
    x,y,w,h=a; X,Y,W,H=b
    return max(0,min(x+w,X+W)-max(x,X))*max(0,min(y+h,Y+H)-max(y,Y))


def protected_local(rects, window):
    """Protected shell/control rectangles clipped into native client coordinates."""
    x,y,w,h=window; out=[]
    for X,Y,W,H in rects:
        l,t,r,b=max(x,X),max(y,Y),min(x+w,X+W),min(y+h,Y+H)
        if r>l and b>t: out.append((int(l-x),int(t-y),int(r-l),int(b-t)))
    return out


def anchor_for(surface, size, monitors, *, fraction=.5, blocked=(), contact=None):
    if surface.auto_hide or not monitors: return None
    x,y,w,h=surface.rect; rw,rh=size
    if min(w,h,rw,rh)<=0: return None
    f=max(.1,min(.9,float(fraction)))
    pose="ledge_sit"
    rx,ry=(contact if contact and len(contact)==2 and all(isinstance(v,(int,float)) and 0<=v<=1 for v in contact)
           else {"bottom":(.5,.72),"top":(.5,.10),"left":(.12,.5),"right":(.88,.5)}[surface.edge])
    if surface.kind=="folder":
        ax=x+w-int(rw*.32); ay=y-int(rh*.38); pose="peek"
    elif surface.edge=="left":
        ax=x+w-int(rw*rx); ay=y+int(h*f-rh/2); pose="ledge_hang"
    elif surface.edge=="right":
        ax=x-int(rw*rx); ay=y+int(h*f-rh/2); pose="ledge_hang"
    elif surface.edge=="top":
        ax=x+int(w*f-rw/2); ay=y+h-int(rh*ry); pose="ledge_hang"
    else:
        ax=x+int(w*f-rw/2); ay=y-int(rh*ry)
    m=next((m for m in monitors if m.x<=x+w/2<m.right and m.y<=y+h/2<m.bottom),None)
    if not m: return None
    # A window too close to the top cannot be a usable perch; don't pretend its edge moved.
    if surface.kind=="window" and ay<m.y: return None
    # Don't clamp an anchored contact off its real surface. Large seated robots
    # may not fit over a thin taskbar: decline and use another grounded ledge.
    if ay<m.y or ay+rh>m.bottom: return None
    if (surface.kind=="folder" or surface.edge in ("left","right")) and (ax<m.x or ax+rw>m.right): return None
    ax=max(m.x,min(ax,m.right-rw))
    box=(ax,ay,rw,rh)
    if any(_overlap(box,b)>0 for b in (*surface.occluders,*blocked)): return None
    return {"x":int(ax),"y":int(ay),"pose":pose,"surface_id":surface.id,"kind":surface.kind,
            "rect":list(surface.rect),"edge":surface.edge,"dpi":surface.dpi,"fraction":f}


def choose_anchor(surfaces, size, monitors, *, preferred=None, kind=None, blocked=(), current=None):
    ranks={"search":0,"taskbar":1,"folder":2,"window":3}
    ordered=sorted(surfaces,key=lambda s:(s.id!=preferred,ranks.get(s.kind,9)))
    for s in ordered:
        if kind and s.kind!=kind: continue
        for f in (.5,.25,.75):
            a=anchor_for(s,size,monitors,fraction=f,blocked=blocked if s.kind=="folder" else ())
            if a: return a
    return None


class ShellProbe:
    """Non-blocking cached shell snapshot. Native geometry each sample; UIA <=once/8s."""
    def __init__(self):
        self._lock=threading.Lock(); self._busy=False; self._at=0.; self._uia=[]; self._error=None

    def snapshot(self, windows=()):
        bars=[]
        if os.name=="nt":
            try: bars=_taskbars()
            except Exception as e: self._error=type(e).__name__
        now=time.monotonic()
        with self._lock:
            extra=list(self._uia) if now-self._at<12 else []
            if not self._busy and now-self._at>3:
                self._busy=True
                threading.Thread(target=self._refresh,args=(bars,),daemon=True,name="companion-shell-uia").start()
        # Search must still belong to a currently visible taskbar, never retain a stale search box.
        from dataclasses import replace
        refreshed=[]
        for s in extra:
            if s.kind=="search":
                bar=next((b for b in bars if _overlap(s.rect,b.rect)>0),None)
                if not bar: continue
                s=replace(s,auto_hide=bar.auto_hide,dpi=bar.dpi,edge=bar.edge)
            elif s.kind=="folder":
                if s.hwnd:
                    owner=next((r for h,r in windows if h==s.hwnd),None)
                    if not owner or not s.owner_rect or owner[2:]!=s.owner_rect[2:]: continue
                    dx,dy=owner[0]-s.owner_rect[0],owner[1]-s.owner_rect[1]
                    higher=[]
                    for h,r in windows:
                        if h==s.hwnd: break
                        higher.append(r)
                    s=replace(s,rect=(s.rect[0]+dx,s.rect[1]+dy,*s.rect[2:]),owner_rect=tuple(owner),occluders=tuple(higher))
                else: s=replace(s,occluders=tuple(r for _,r in windows))
            refreshed.append(s)
        extra=refreshed
        surfaces=bars+extra
        higher=[]
        for hwnd,rect in windows:
            if rect[2]>180 and rect[3]>100:
                # Keep only unoccluded top ledges (EnumWindows order is topmost-first).
                ledge=(rect[0],rect[1],rect[2],max(1,32))
                if not any(_overlap(ledge,r)>ledge[2]*ledge[3]*.2 for r in higher):
                    surfaces.append(Surface(f"window:{hwnd}","window",tuple(rect),hwnd=hwnd,occluders=tuple(higher)))
            higher.append(rect)
        protected=[s.rect for s in bars+extra]
        protected += [(s.rect[0],s.rect[1],s.rect[2],max(24,round(32*s.dpi/96))) for s in surfaces if s.kind=="window"]
        return {"surfaces":surfaces,"protected":protected,"strict_protected":[b.rect for b in bars],"uia_age":round(now-self._at,1) if self._at else None,
                "uia_pending":self._busy,"error":self._error}

    def _refresh(self,bars):
        try:
            result=_uia_surfaces(bars)
            with self._lock: self._uia=result; self._error=None
        except Exception as e:
            with self._lock: self._uia=[]; self._error=type(e).__name__
        finally:
            with self._lock: self._at=time.monotonic(); self._busy=False


def _taskbars():
    import ctypes as C
    from ctypes import wintypes as W
    u=C.WinDLL("user32",use_last_error=True); shell=C.WinDLL("shell32",use_last_error=True)
    class APPBAR(C.Structure):
        _fields_=[("cbSize",W.DWORD),("hWnd",W.HWND),("uCallbackMessage",W.UINT),("uEdge",W.UINT),("rc",W.RECT),("lParam",W.LPARAM)]
    shell.SHAppBarMessage.argtypes=[W.DWORD,C.POINTER(APPBAR)]; shell.SHAppBarMessage.restype=C.c_size_t
    u.GetMonitorInfoW.argtypes=[W.HANDLE,C.c_void_p];u.GetMonitorInfoW.restype=W.BOOL
    u.GetWindowRect.argtypes=[W.HWND,C.POINTER(W.RECT)];u.GetWindowRect.restype=W.BOOL
    found=[]
    callback=C.WINFUNCTYPE(W.BOOL,W.HWND,W.LPARAM)
    def visit(hwnd,unused):
        name=C.create_unicode_buffer(128);u.GetClassNameW(hwnd,name,128)
        if name.value not in ("Shell_TrayWnd","Shell_SecondaryTrayWnd") or not u.IsWindowVisible(hwnd): return True
        r=W.RECT()
        if not u.GetWindowRect(hwnd,C.byref(r)): return True
        w,h=r.right-r.left,r.bottom-r.top
        if not w or not h: return True
        # Orientation comes from the monitor, including negative-origin/vertical displays.
        mi=(W.LONG*10)();mi[0]=C.sizeof(mi)
        u.MonitorFromWindow.argtypes=[W.HWND,W.DWORD];u.MonitorFromWindow.restype=W.HANDLE
        hm=u.MonitorFromWindow(hwnd,2)
        if not u.GetMonitorInfoW(hm,C.byref(mi)): return True
        mx,my,mr,mb=mi[1],mi[2],mi[3],mi[4]
        edge=("top" if abs(r.top-my)<abs(r.bottom-mb) else "bottom") if w>=h else ("left" if abs(r.left-mx)<abs(r.right-mr) else "right")
        abd=APPBAR();abd.cbSize=C.sizeof(abd);abd.hWnd=hwnd
        auto=bool(shell.SHAppBarMessage(4,C.byref(abd))&1)
        try: dpi=int(u.GetDpiForWindow(hwnd)) or 96
        except AttributeError: dpi=96
        found.append(Surface(f"taskbar:{mx}:{my}:{edge}","taskbar",(r.left,r.top,w,h),edge,dpi,auto,int(hwnd)))
        return True
    cb=callback(visit);u.EnumWindows(cb,0)
    return found


def _desktop_views():
    """Desktop icon list may live under a WorkerW omitted from UIA root windows."""
    import ctypes as C
    from ctypes import wintypes as W
    u=C.WinDLL("user32",use_last_error=True)
    u.FindWindowExW.argtypes=[W.HWND,W.HWND,W.LPCWSTR,W.LPCWSTR];u.FindWindowExW.restype=W.HWND
    views=[]
    def visit(hwnd,unused):
        definition=u.FindWindowExW(hwnd,0,"SHELLDLL_DefView",None)
        if definition:
            view=u.FindWindowExW(definition,0,"SysListView32",None)
            if view and u.IsWindowVisible(view): views.append(int(view))
        return True
    cb=C.WINFUNCTYPE(W.BOOL,W.HWND,W.LPARAM)(visit);u.EnumWindows(cb,0)
    return views


def _uia_surfaces(bars):
    if os.name!="nt": return []
    import ctypes
    import hashlib
    from pathlib import Path
    import comtypes
    comtypes.CoInitialize()
    try:
        from pywinauto import Desktop
        desk=Desktop(backend="uia");out=[]
        for bar in bars:
            root=desk.window(handle=bar.hwnd)
            for child in root.descendants():
                info=child.element_info
                if "search" not in (info.automation_id or "").lower(): continue
                r=child.rectangle(); box=(r.left,r.top,r.width(),r.height())
                if min(box[2:])>0 and _overlap(box,bar.rect)>0:
                    out.append(Surface(f"search:{bar.id}","search",box,bar.edge,bar.dpi,bar.auto_hide,bar.hwnd));break
        folder_names=set()
        for csidl in (0x10,0x19):
            buf=ctypes.create_unicode_buffer(260)
            if ctypes.windll.shell32.SHGetFolderPathW(None,csidl,None,0,buf)==0:
                try: folder_names.update(p.name for p in Path(buf.value).iterdir() if p.is_dir())
                except OSError: pass
        # Desktop list providers can be underneath Progman or WorkerW; use visible list items only.
        for view in _desktop_views():
            root=desk.window(handle=view)
            for item in root.descendants(control_type="ListItem")[:64]:
                if item.window_text() not in folder_names: continue
                r=item.rectangle(); box=(r.left,r.top,r.width(),r.height())
                if min(box[2:])<=0: continue
                key=hashlib.blake2s(item.window_text().encode(),digest_size=8).hexdigest()
                out.append(Surface(f"folder:{key}","folder",box))
        # Explorer exposes actual folder items too. Query its own shell folder,
        # never infer folders from an icon image or remember private path names.
        try:
            from win32com.client import Dispatch
            shell_windows=Dispatch("Shell.Application").Windows()
            for window in shell_windows:
                try:
                    hwnd=int(window.HWND)
                    if not ctypes.windll.user32.IsWindowVisible(hwnd): continue
                    folder=Path(str(window.Document.Folder.Self.Path))
                    names={p.name for p in folder.iterdir() if p.is_dir()}
                    if not names: continue
                    root=desk.window(handle=hwnd);owner=root.rectangle()
                    for item in root.descendants(control_type="ListItem")[:128]:
                        name=item.window_text()
                        if name not in names: continue
                        r=item.rectangle();box=(r.left,r.top,r.width(),r.height())
                        if min(box[2:])<=0 or _overlap(box,(owner.left,owner.top,owner.width(),owner.height()))<=0: continue
                        # Details providers expose a full table ROW, not the icon.
                        # Decline that geometry instead of pretending its far edge is a folder.
                        if box[2]>max(180,box[3]*4): continue
                        key=hashlib.blake2s(str(folder/name).encode(),digest_size=8).hexdigest()
                        out.append(Surface(f"folder:{key}","folder",box,hwnd=hwnd,
                            owner_rect=(owner.left,owner.top,owner.width(),owner.height())))
                except Exception: continue
        except Exception: pass
        return out
    finally: comtypes.CoUninitialize()
