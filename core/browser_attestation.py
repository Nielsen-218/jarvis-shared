"""Read-only Windows renderer token checks. Never enable debug privileges."""
import ctypes as c
from ctypes import wintypes as w
import os


def attest(pids):
    if os.name!='nt':raise RuntimeError('Native browser sandbox attestation currently requires Windows')
    pids=sorted(set(pids))
    if not pids or len(pids)>8:raise RuntimeError('Renderer process quota or identification failed')
    kernel=c.WinDLL('kernel32',use_last_error=True);adv=c.WinDLL('advapi32',use_last_error=True)
    def bind(lib,name,args,result=w.BOOL):
        fn=getattr(lib,name);fn.argtypes=args;fn.restype=result;return fn
    open_process=bind(kernel,'OpenProcess',[w.DWORD,w.BOOL,w.DWORD],w.HANDLE)
    open_token=bind(adv,'OpenProcessToken',[w.HANDLE,w.DWORD,c.POINTER(w.HANDLE)])
    info=bind(adv,'GetTokenInformation',[w.HANDLE,w.DWORD,c.c_void_p,w.DWORD,c.POINTER(w.DWORD)])
    restricted=bind(adv,'IsTokenRestricted',[w.HANDLE])
    count=bind(adv,'GetSidSubAuthorityCount',[c.c_void_p],c.POINTER(c.c_ubyte))
    authority=bind(adv,'GetSidSubAuthority',[c.c_void_p,w.DWORD],c.POINTER(w.DWORD))
    close=bind(kernel,'CloseHandle',[w.HANDLE])
    rows=[]
    for pid in pids:
        process=open_process(0x1000,False,pid);token=w.HANDLE()
        if not process:continue
        try:
            if not open_token(process,0x8,c.byref(token)):continue
            size=w.DWORD();buffer=c.create_string_buffer(1024)
            if not info(token,25,buffer,len(buffer),c.byref(size)):continue
            sid=c.cast(buffer,c.POINTER(c.c_void_p)).contents.value
            level=authority(sid,count(sid).contents.value-1).contents.value
            app=w.DWORD()
            if not info(token,29,c.byref(app),c.sizeof(app),c.byref(size)):continue
            rows.append({'pid':pid,'integrity_rid':level,'restricted_token':bool(restricted(token)),
                         'app_container':bool(app.value)})
        finally:
            if token:close(token)
            close(process)
    passed=len(rows)==len(pids) and all((r['app_container'] or r['restricted_token']) and r['integrity_rid']<=0x1000 for r in rows)
    if not passed:raise RuntimeError('Chromium renderer token sandbox could not be attested')
    return {'attested':True,'renderers':rows,'privileges_changed':False}
