"""Experimental Windows AppContainer broker. No uncontained execution fallback.

Only newly created lab directories receive a package-SID READ/EXECUTE ACE.
The process starts suspended, receives mandatory Job limits and token checks,
then resumes. No network capabilities or child processes. Candidate directories
are read-only; code changes are brokered, not written by the candidate process.
"""
from __future__ import annotations
import ctypes as c
from ctypes import wintypes as w
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time


class SandboxUnavailable(RuntimeError): pass


MARKER='Jarvis isolated project lab v1'


def initialize(root):
    """Initialize only a fresh broker-owned lab, never an existing host tree."""
    root=Path(root).resolve()
    root.mkdir(parents=True,exist_ok=False)
    (root/'.jarvis-project-lab').write_text(MARKER,encoding='ascii')
    return root


def _lab(root):
    root=Path(root).resolve()
    marker=root/'.jarvis-project-lab'
    if not marker.is_file() or marker.read_text(encoding='ascii')!=MARKER:
        raise SandboxUnavailable('Broker-owned lab marker required')
    return root


def seal_profile(root):
    """Deny this newly created package identity writes to its private directory."""
    import win32security,ntsecuritycon
    root=_lab(root)
    sid=win32security.SID(_sid(root))
    name='Jarvis.ProjectLab.'+hashlib.sha256(str(root).encode()).hexdigest()[:24]
    profile=Path(os.environ['LOCALAPPDATA'])/'Packages'/name
    if not profile.is_dir():raise SandboxUnavailable('Private profile not found')
    denied=(0x2|0x4|0x10|0x100|0x40|ntsecuritycon.DELETE|
            ntsecuritycon.WRITE_DAC|ntsecuritycon.WRITE_OWNER)
    for path in [profile,*profile.rglob('*')]:
        if path.is_symlink() or path.stat().st_file_attributes&0x400:
            raise SandboxUnavailable('Profile reparse point rejected')
        sd=win32security.GetNamedSecurityInfo(str(path),win32security.SE_FILE_OBJECT,win32security.DACL_SECURITY_INFORMATION)
        acl=sd.GetSecurityDescriptorDacl()
        acl.SetEntriesInAcl([{'AccessPermissions':denied,'AccessMode':win32security.DENY_ACCESS,
            'Inheritance':3,'Trustee':{'TrusteeForm':win32security.TRUSTEE_IS_SID,
            'TrusteeType':win32security.TRUSTEE_IS_UNKNOWN,'Identifier':sid}}])
        win32security.SetNamedSecurityInfo(str(path),win32security.SE_FILE_OBJECT,win32security.DACL_SECURITY_INFORMATION,None,None,acl,None)
    return profile


def _windows():
    if os.name!='nt': raise SandboxUnavailable('Windows AppContainer required; no unrestricted fallback')


def _sid(identity_root):
    _windows()
    userenv=c.WinDLL('userenv',use_last_error=True)
    derive=userenv.DeriveAppContainerSidFromAppContainerName
    derive.argtypes=[w.LPCWSTR,c.POINTER(c.c_void_p)];derive.restype=c.c_long
    ptr=c.c_void_p()
    moniker='Jarvis.ProjectLab.'+hashlib.sha256(str(Path(identity_root).resolve()).encode()).hexdigest()[:24]
    create=userenv.CreateAppContainerProfile
    create.argtypes=[w.LPCWSTR,w.LPCWSTR,w.LPCWSTR,c.c_void_p,w.DWORD,c.POINTER(c.c_void_p)];create.restype=c.c_long
    hr=create(moniker,moniker,'Read-only isolated Jarvis project lab',None,0,c.byref(ptr))
    if c.c_uint32(hr).value==0x800700b7:
        hr=derive(moniker,c.byref(ptr))
    if hr: raise SandboxUnavailable(f'Cannot derive sandbox identity: HRESULT {hr:#x}')
    adv=c.WinDLL('advapi32',use_last_error=True)
    adv.GetLengthSid.argtypes=[c.c_void_p];adv.GetLengthSid.restype=w.DWORD
    size=adv.GetLengthSid(ptr)
    raw=c.string_at(ptr,size)
    adv.FreeSid.argtypes=[c.c_void_p];adv.FreeSid(ptr)
    return raw


def grant_readonly(root,identity_root=None):
    """Call only for broker-created lab content; never for the host Python tree."""
    import win32security,ntsecuritycon
    root=Path(root).resolve()
    identity_root=_lab(identity_root or root.parent)
    if not root.is_relative_to(identity_root) or root==identity_root:
        raise SandboxUnavailable('Only lab child content may receive access')
    sid=win32security.SID(_sid(identity_root))
    for path in [root,*root.rglob('*')]:
        if path.is_symlink() or (path.stat().st_file_attributes & 0x400):
            raise SandboxUnavailable('Reparse points are forbidden in sandbox content')
        sd=win32security.GetNamedSecurityInfo(str(path),win32security.SE_FILE_OBJECT,win32security.DACL_SECURITY_INFORMATION)
        acl=sd.GetSecurityDescriptorDacl()
        # Replace this sandbox identity's existing ACE; never accumulate write rights.
        for index in reversed(range(acl.GetAceCount())):
            if acl.GetAce(index)[2]==sid:acl.DeleteAce(index)
        acl.AddAccessAllowedAceEx(win32security.ACL_REVISION,0,ntsecuritycon.FILE_GENERIC_READ|ntsecuritycon.FILE_GENERIC_EXECUTE,sid)
        win32security.SetNamedSecurityInfo(str(path),win32security.SE_FILE_OBJECT,win32security.DACL_SECURITY_INFORMATION,None,None,acl,None)


def prepare_runtime(root):
    """Copy the installed stdlib/runtime, without site-packages, into a NEW lab."""
    _windows();root=Path(root).resolve()
    _lab(root.parent)
    if root.exists():
        manifest=root/'runtime-manifest.json'
        if not manifest.exists(): raise SandboxUnavailable('Existing runtime lacks a verified manifest')
        saved=json.loads(manifest.read_text())
        actual={p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file() and p!=manifest}
        if actual!=set(saved['files']):raise SandboxUnavailable('Runtime file inventory changed')
        for path in root.rglob('*'):
            if path.is_symlink() or (path.stat().st_file_attributes&0x400):raise SandboxUnavailable('Runtime reparse point rejected')
        for name,digest in saved['files'].items():
            path=root/name
            if path.stat().st_nlink!=1 or hashlib.sha256(path.read_bytes()).hexdigest()!=digest:
                raise SandboxUnavailable('Runtime integrity check failed')
        return root
    root.mkdir(parents=True)
    base=Path(sys.base_prefix)
    for name in ['pythonw.exe',*[p.name for p in base.glob('*.dll')]]:
        shutil.copyfile(base/name,root/name)
    shutil.copytree(base/'DLLs',root/'DLLs',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    shutil.copytree(base/'Lib',root/'Lib',ignore=shutil.ignore_patterns('site-packages','__pycache__','*.pyc','test','tests','ensurepip','idlelib','tkinter','turtledemo'))
    (root/f'python{sys.version_info.major}{sys.version_info.minor}._pth').write_text('Lib\nDLLs\n.\n',encoding='ascii')
    files={p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}
    (root/'runtime-manifest.json').write_text(json.dumps({'python':sys.version,'files':files},indent=2),encoding='utf-8')
    grant_readonly(root)
    return root


class STARTUPINFO(c.Structure):
    _fields_=[('cb',w.DWORD),('lpReserved',w.LPWSTR),('lpDesktop',w.LPWSTR),('lpTitle',w.LPWSTR),
        ('dwX',w.DWORD),('dwY',w.DWORD),('dwXSize',w.DWORD),('dwYSize',w.DWORD),
        ('dwXCountChars',w.DWORD),('dwYCountChars',w.DWORD),('dwFillAttribute',w.DWORD),
        ('dwFlags',w.DWORD),('wShowWindow',w.WORD),('cbReserved2',w.WORD),
        ('lpReserved2',c.c_void_p),('hStdInput',w.HANDLE),('hStdOutput',w.HANDLE),('hStdError',w.HANDLE)]


class STARTUPINFOEX(c.Structure):
    _fields_=[('StartupInfo',STARTUPINFO),('lpAttributeList',c.c_void_p)]


class PROCESS_INFORMATION(c.Structure):
    _fields_=[('hProcess',w.HANDLE),('hThread',w.HANDLE),('dwProcessId',w.DWORD),('dwThreadId',w.DWORD)]


class SECURITY_CAPABILITIES(c.Structure):
    _fields_=[('AppContainerSid',c.c_void_p),('Capabilities',c.c_void_p),('CapabilityCount',w.DWORD),('Reserved',w.DWORD)]


def run(runtime,workspace,script,stdin='',*,timeout=8,cancel=None,output_limit=131072):
    _windows()
    import msvcrt,win32job
    runtime=Path(runtime).resolve();workspace=Path(workspace).resolve();script=Path(script).resolve()
    lab=_lab(runtime.parent)
    if not workspace.is_relative_to(lab) or workspace==runtime:
        raise SandboxUnavailable('Project workspace must be a lab child')
    prepare_runtime(runtime)
    seal_profile(lab)
    if not script.is_relative_to(workspace) or script.is_symlink(): raise SandboxUnavailable('Worker must be in the lab')
    kernel=c.WinDLL('kernel32',use_last_error=True);adv=c.WinDLL('advapi32',use_last_error=True)
    def bind(lib,name,args,result=w.BOOL):
        fn=getattr(lib,name);fn.argtypes=args;fn.restype=result;return fn
    init=bind(kernel,'InitializeProcThreadAttributeList',[c.c_void_p,w.DWORD,w.DWORD,c.POINTER(c.c_size_t)])
    update=bind(kernel,'UpdateProcThreadAttribute',[c.c_void_p,w.DWORD,c.c_size_t,c.c_void_p,c.c_size_t,c.c_void_p,c.c_void_p])
    delete=bind(kernel,'DeleteProcThreadAttributeList',[c.c_void_p],None)
    create=bind(kernel,'CreateProcessW',[w.LPCWSTR,w.LPWSTR,c.c_void_p,c.c_void_p,w.BOOL,w.DWORD,c.c_void_p,w.LPCWSTR,c.POINTER(STARTUPINFOEX),c.POINTER(PROCESS_INFORMATION)])
    close=bind(kernel,'CloseHandle',[w.HANDLE]);wait=bind(kernel,'WaitForSingleObject',[w.HANDLE,w.DWORD],w.DWORD)
    resume=bind(kernel,'ResumeThread',[w.HANDLE],w.DWORD)
    token_open=bind(adv,'OpenProcessToken',[w.HANDLE,w.DWORD,c.POINTER(w.HANDLE)])
    token_info=bind(adv,'GetTokenInformation',[w.HANDLE,w.DWORD,c.c_void_p,w.DWORD,c.POINTER(w.DWORD)])
    size=c.c_size_t();init(None,3,0,c.byref(size));attrs=c.create_string_buffer(size.value)
    if not init(attrs,3,0,c.byref(size)): raise c.WinError(c.get_last_error())
    sid_buffer=c.create_string_buffer(_sid(runtime.parent))
    capabilities=SECURITY_CAPABILITIES(c.cast(sid_buffer,c.c_void_p),None,0,0)
    child_policy=w.DWORD(1)
    in_read,in_write=os.pipe();out_read,out_write=os.pipe();err_read,err_write=os.pipe()
    inherited=[msvcrt.get_osfhandle(fd) for fd in (in_read,out_write,err_write)]
    for handle in inherited: os.set_handle_inheritable(handle,True)
    handle_array=(w.HANDLE*3)(*inherited)
    info=STARTUPINFOEX();info.StartupInfo.cb=c.sizeof(info);info.StartupInfo.dwFlags=0x100
    info.StartupInfo.hStdInput,info.StartupInfo.hStdOutput,info.StartupInfo.hStdError=inherited
    info.lpAttributeList=c.cast(attrs,c.c_void_p)
    proc=PROCESS_INFORMATION();job=None;token=w.HANDLE();started=time.monotonic()
    streams={'stdout':bytearray(),'stderr':bytearray()};overflow=threading.Event();readers=[]
    record={'backend':'Windows AppContainer','capability_count':0,'resumed':False,'passed_boundary':False}
    try:
        record['stage']='attributes'
        for attr,obj in [(0x20009,capabilities),(0x2000e,child_policy),(0x20002,handle_array)]:
            if not update(attrs,0,attr,c.byref(obj),c.sizeof(obj),None,None): raise c.WinError(c.get_last_error())
        command=c.create_unicode_buffer(subprocess.list2cmdline([str(runtime/'pythonw.exe'),'-I','-S','-B',str(script)]))
        environment=c.create_unicode_buffer('LOCALAPPDATA='+os.environ['LOCALAPPDATA']+'\0SystemRoot='+os.environ['SystemRoot']+'\0WINDIR='+os.environ['SystemRoot']+'\0\0')
        record['stage']='create_process'
        if not create(str(runtime/'pythonw.exe'),command,None,None,True,0x80000|0x4|0x400|0x08000000,
                      environment,str(workspace),c.byref(info),c.byref(proc)):
            raise c.WinError(c.get_last_error())
        # No candidate instruction has run yet.
        record['stage']='job'
        job=win32job.CreateJobObject(None,'')
        limits=win32job.QueryInformationJobObject(job,win32job.JobObjectExtendedLimitInformation)
        limits['BasicLimitInformation']['LimitFlags']=(win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE|win32job.JOB_OBJECT_LIMIT_PROCESS_MEMORY|win32job.JOB_OBJECT_LIMIT_ACTIVE_PROCESS)
        limits['BasicLimitInformation']['ActiveProcessLimit']=1;limits['ProcessMemoryLimit']=256*1024*1024
        limits['BasicLimitInformation']['LimitFlags']|=win32job.JOB_OBJECT_LIMIT_PROCESS_TIME
        limits['BasicLimitInformation']['PerProcessUserTimeLimit']=30_000_000
        win32job.SetInformationJobObject(job,win32job.JobObjectExtendedLimitInformation,limits)
        win32job.AssignProcessToJobObject(job,int(proc.hProcess))
        record['stage']='token'
        if not token_open(proc.hProcess,8,c.byref(token)):raise c.WinError(c.get_last_error())
        values={}
        for key,number in [('app_container',29)]:
            record['stage']='token_'+key
            value=w.DWORD();length=w.DWORD()
            if not token_info(token,number,c.byref(value),c.sizeof(value),c.byref(length)):raise c.WinError(c.get_last_error())
            values[key]=bool(value.value)
        if not all(values.values()): raise SandboxUnavailable('Mandatory AppContainer token check failed')
        buffer=c.create_string_buffer(4096);length=w.DWORD()
        if not token_info(token,30,buffer,len(buffer),c.byref(length)):raise c.WinError(c.get_last_error())
        actual_capabilities=c.cast(buffer,c.POINTER(w.DWORD)).contents.value
        if actual_capabilities!=0:raise SandboxUnavailable('Unexpected token capabilities')
        if not token_info(token,31,buffer,len(buffer),c.byref(length)):raise c.WinError(c.get_last_error())
        token_sid=c.cast(buffer,c.POINTER(c.c_void_p)).contents.value
        equal=bind(adv,'EqualSid',[c.c_void_p,c.c_void_p])
        if not equal(token_sid,c.cast(sid_buffer,c.c_void_p)):raise SandboxUnavailable('Unexpected package identity')
        record['capability_count']=actual_capabilities;record['package_identity_verified']=True
        record.update(values);record['job_assigned_before_resume']=True
        for fd in (in_read,out_write,err_write):os.close(fd)
        in_read=out_write=err_write=None
        def collect(fd,key):
            with os.fdopen(fd,'rb') as stream:
                while chunk:=stream.read(4096):
                    if len(streams[key])+len(chunk)>output_limit: overflow.set();return
                    streams[key].extend(chunk)
        for fd,key in [(out_read,'stdout'),(err_read,'stderr')]:
            thread=threading.Thread(target=collect,args=(fd,key),daemon=True);thread.start();readers.append(thread)
        if resume(proc.hThread)==0xffffffff:raise c.WinError(c.get_last_error())
        record['resumed']=True
        # Payload fits the anonymous pipe without waiting for a hostile reader.
        if len(stdin.encode())>2048:raise SandboxUnavailable('Input quota exceeded')
        with os.fdopen(in_write,'wb') as pipe:pipe.write(stdin.encode())
        in_write=None
        while wait(proc.hProcess,25)==258:
            if overflow.is_set():raise SandboxUnavailable('Output quota exceeded')
            if cancel and cancel.is_set():raise InterruptedError('STOP requested')
            if time.monotonic()-started>timeout:raise TimeoutError('Sandbox wall-clock limit exceeded')
        code=w.DWORD()
        bind(kernel,'GetExitCodeProcess',[w.HANDLE,c.POINTER(w.DWORD)])(proc.hProcess,c.byref(code))
        record['exit_code']=code.value;record['pid']=proc.dwProcessId
        for thread in readers:thread.join(timeout=1)
        if overflow.is_set():raise SandboxUnavailable('Output quota exceeded')
        record['passed_boundary']=True
    except Exception as exc:
        record['error']=type(exc).__name__+': '+str(exc)
        if proc.hProcess:bind(kernel,'TerminateProcess',[w.HANDLE,w.UINT])(proc.hProcess,99)
    finally:
        if job:job.Close()
        for handle in (token,proc.hThread,proc.hProcess):
            if handle:close(handle)
        for fd in (in_read,in_write,out_write,err_write,*([] if readers else [out_read,err_read])):
            if fd is not None:
                try:os.close(fd)
                except OSError:pass
        for thread in readers:thread.join(timeout=1)
        delete(attrs)
    record['stdout']=streams['stdout'].decode('utf-8',errors='replace')
    record['stderr']=streams['stderr'].decode('utf-8',errors='replace')
    record['duration_seconds']=round(time.monotonic()-started,3)
    return record





