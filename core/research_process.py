"""Bound trusted provider CLI lifetime/output. This is NOT a code sandbox."""
import os
import subprocess
import tempfile
import time


def run(command, env, timeout, cancelled, *, input_text=None, memory_bytes=512*1024*1024, process_limit=4):
    if cancelled():raise InterruptedError('Research STOP requested before provider launch')
    if input_text is not None and len(input_text.encode())>16000:raise ValueError('Provider input quota')
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        job=None; process=None
        try:
            flags=getattr(subprocess,'CREATE_NO_WINDOW',0)
            if os.name=='nt':
                import win32job
                job=win32job.CreateJobObject(None,'')
                limits=win32job.QueryInformationJobObject(job,win32job.JobObjectExtendedLimitInformation)
                limits['BasicLimitInformation']['LimitFlags']=(win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE |
                    win32job.JOB_OBJECT_LIMIT_ACTIVE_PROCESS | win32job.JOB_OBJECT_LIMIT_JOB_MEMORY)
                limits['BasicLimitInformation']['ActiveProcessLimit']=process_limit
                limits['JobMemoryLimit']=memory_bytes
                win32job.SetInformationJobObject(job,win32job.JobObjectExtendedLimitInformation,limits)
                flags|=4  # CREATE_SUSPENDED: no provider child can escape before Job assignment.
            process=subprocess.Popen(command,stdout=out,stderr=err,stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
                                     env=env,creationflags=flags,start_new_session=(os.name!='nt'))
            if os.name=='nt':
                import ctypes
                from ctypes import wintypes
                win32job.AssignProcessToJobObject(job,process._handle)
                resume=ctypes.WinDLL('ntdll').NtResumeProcess
                resume.argtypes=[wintypes.HANDLE];resume.restype=wintypes.LONG
                if resume(int(process._handle))!=0:raise RuntimeError('Provider resume failed')
            if input_text is not None:
                process.stdin.write(input_text.encode());process.stdin.close()
            deadline=time.monotonic()+timeout
            while process.poll() is None:
                if cancelled():raise InterruptedError('Research STOP requested')
                if time.monotonic()>deadline:raise TimeoutError('Provider wall-clock deadline exceeded')
                if os.fstat(out.fileno()).st_size>200000 or os.fstat(err.fileno()).st_size>20000:
                    raise ValueError('Provider output quota exceeded')
                time.sleep(.05)
            out.seek(0);err.seek(0)
            raw=out.read(200001);errors=err.read(20001)
            if len(raw)>200000 or len(errors)>20000:raise ValueError('Provider output quota exceeded')
            return process.returncode,raw.decode('utf-8',errors='replace'),errors.decode('utf-8',errors='replace')
        except Exception as exc:
            err.seek(0)
            exc.provider_stderr=err.read(20000).decode('utf-8',errors='replace')
            raise
        finally:
            if job:job.Close()  # Kills the trusted CLI and its wrappers/children on every exit.
            elif process:
                import signal
                try:os.killpg(process.pid,signal.SIGKILL)
                except ProcessLookupError:pass
            if process:
                if process.poll() is None:process.kill()
                process.wait(timeout=5)
