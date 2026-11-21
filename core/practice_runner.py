"""Compile and test only code regenerated from the validated scalar language IR."""
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from core.practice_language import checked, emit, parse

PY_HELPERS = '''
steps=0
def tick():
    global steps
    steps+=1
    if steps>10000: raise ValueError('step budget exceeded')
def bound(x):
    if not isinstance(x,int) or abs(x)>1000000000: raise ValueError('integer bound exceeded')
    return int(x)
def safe_add(a,b): return bound(a+b)
def safe_sub(a,b): return bound(a-b)
def safe_mul(a,b): return bound(a*b)
def safe_floor(a,b): return bound(a//b)
def safe_mod(a,b): return bound(a%b)
def safe_cdiv(a,b): return bound((abs(a)//abs(b))*(-1 if (a<0)!=(b<0) else 1))
def safe_cmod(a,b): return bound(a-safe_cdiv(a,b)*b)
def safe_neg(a): return bound(-a)
def safe_pos(a): return bound(a)
def safe_abs(a): return bound(abs(a))
def safe_min(a,b): return min(a,b)
def safe_max(a,b): return max(a,b)
'''

CPP_HELPERS = r'''
#include <iostream>
#include <stdexcept>
long long steps=0;
void tick(){ if(++steps>10000) throw std::runtime_error("step budget exceeded"); }
long long bound(long long x){if(x < -1000000000LL || x > 1000000000LL) throw std::runtime_error("integer bound exceeded"); return x;}
long long safe_add(long long a,long long b){return bound(a+b);}
long long safe_sub(long long a,long long b){return bound(a-b);}
long long safe_mul(long long a,long long b){return bound(a*b);}
long long safe_cdiv(long long a,long long b){if(!b)throw std::runtime_error("zero divisor");return bound(a/b);}
long long safe_cmod(long long a,long long b){if(!b)throw std::runtime_error("zero divisor");return bound(a%b);}
long long safe_floor(long long a,long long b){long long q=safe_cdiv(a,b);return q-((a%b!=0)&&((a<0)!=(b<0)));}
long long safe_mod(long long a,long long b){return bound(a-safe_floor(a,b)*b);}
long long safe_neg(long long a){return bound(-a);}
long long safe_pos(long long a){return bound(a);}
long long safe_abs(long long a){return a<0?-a:a;}
long long safe_min(long long a,long long b){return a<b?a:b;}
long long safe_max(long long a,long long b){return a>b?a:b;}
'''


def execute(source, language, cases, directory, *, cancel=None):
    """Return actual compilation/runtime evidence. Invalid source never reaches a compiler."""
    directory=Path(directory); directory.mkdir(parents=True,exist_ok=False)
    record={'language':language,'passed':False,'boundary':'validated scalar IR; arbitrary programs unsupported',
            'source_sha256':hashlib.sha256(source.encode()).hexdigest(),'commands':[],'cases':[]}
    (directory/('submitted.py' if language=='python' else 'submitted.cpp')).write_text(source,encoding='utf-8')
    try:
        if not 1<=len(cases)<=40: raise ValueError('1-40 test cases required')
        for case in cases:
            if len(case['input'])!=2: raise ValueError('Exactly two integer inputs required')
            for x in case['input']: checked(x)
        ir=parse(source,language); normalized=emit(ir,language)
        record['ir_sha256']=hashlib.sha256(json.dumps(ir).encode()).hexdigest()
        if language=='python':
            candidate=directory/'candidate.py'
            candidate.write_text(PY_HELPERS+normalized+"\nimport sys\na,b=map(int,sys.stdin.read().split())\nprint(solve(bound(a),bound(b)))\n",encoding='utf-8')
            compile_command=[sys.executable,'-I','-S','-m','py_compile',str(candidate)]
            command=[sys.executable,'-I','-S',str(candidate)]
            environment={k:v for k,v in os.environ.items() if k.upper() in {'SYSTEMROOT','WINDIR','PATH','TEMP','TMP'}}
        else:
            from core.teaching_practice import toolchain
            compiler,environment,kind=toolchain()
            record['compiler_family']=kind;record['compiler_path']=compiler
            candidate=directory/'candidate.cpp'; binary=directory/'candidate.exe'
            candidate.write_text(CPP_HELPERS+normalized+r'''
int main(){long long a,b; if(!(std::cin>>a>>b))return 3; try{std::cout<<solve(bound(a),bound(b))<<"\n";return 0;}catch(const std::exception& e){std::cerr<<e.what();return 2;}}
''',encoding='utf-8')
            compile_command=([compiler,'/nologo','/EHsc','/std:c++17',str(candidate),'/Fe:'+str(binary)] if kind=='msvc'
                else [compiler,'-std=c++17',str(candidate),'-o',str(binary)])
            command=[str(binary)]
        record['normalized_source_sha256']=hashlib.sha256(candidate.read_bytes()).hexdigest()
        def run(argv, stdin='', timeout=20):
            if cancel and cancel.is_set(): raise InterruptedError('STOP requested')
            process=subprocess.Popen(argv,cwd=directory,env=environment,stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,text=True,creationflags=(getattr(subprocess,'CREATE_NO_WINDOW',0)
                    |(0x4 if os.name=='nt' else 0)))
            job=None
            if os.name=='nt':
                try:
                    import win32api, win32job
                    job=win32job.CreateJobObject(None,'')
                    limits=win32job.QueryInformationJobObject(job,win32job.JobObjectExtendedLimitInformation)
                    limits['BasicLimitInformation']['LimitFlags']=(win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
                        |win32job.JOB_OBJECT_LIMIT_PROCESS_MEMORY|win32job.JOB_OBJECT_LIMIT_ACTIVE_PROCESS)
                    limits['BasicLimitInformation']['ActiveProcessLimit']=8
                    limits['ProcessMemoryLimit']=768*1024*1024
                    win32job.SetInformationJobObject(job,win32job.JobObjectExtendedLimitInformation,limits)
                    handle=win32api.OpenProcess(0x101,False,process.pid)
                    try: win32job.AssignProcessToJobObject(job,handle)
                    finally: handle.Close()
                    # Mandatory containment before the primary thread executes.
                    import psutil
                    psutil.Process(process.pid).resume()
                except Exception as exc:
                    if job: job.Close(); job=None
                    process.kill(); process.communicate()
                    record['job_setup_failure']={'type':type(exc).__name__,'message':str(exc)[:1000]}
                    raise RuntimeError('Mandatory Windows Job setup failed before execution') from exc
            deadline=time.monotonic()+timeout; first=True
            try:
                while True:
                    if cancel and cancel.is_set(): raise InterruptedError('STOP requested')
                    if time.monotonic()>deadline: raise subprocess.TimeoutExpired(argv,timeout)
                    try:
                        stdout,stderr=process.communicate(input=stdin if first else None,timeout=.1)
                        break
                    except subprocess.TimeoutExpired: first=False
            except BaseException as exc:
                if job: job.Close(); job=None
                process.kill(); stdout,stderr=process.communicate()
                record['commands'].append({'argv':argv,'exit_code':process.returncode,
                    'stdout':stdout[:6000],'stderr':stderr[:6000],'error':type(exc).__name__+': '+str(exc)[:1000]})
                raise
            finally:
                if job: job.Close()
            item={'argv':argv,'exit_code':process.returncode,'stdout':stdout[:6000],'stderr':stderr[:6000],
                  'windows_job_limit':os.name=='nt' and 'job_limit_unavailable' not in record}
            record['commands'].append(item)
            return item
        compiled=run(compile_command)
        if compiled['exit_code']:
            record['error']='compilation_failed'
        else:
            all_passed=True
            for case in cases:
                result=run(command,' '.join(map(str,case['input'])),timeout=2)
                try: actual=int(result['stdout'].strip())
                except ValueError: actual=None
                passed=result['exit_code']==0 and actual==case['expected']
                record['cases'].append({'input':case['input'],'expected':case['expected'],'actual':actual,'passed':passed})
                all_passed &= passed
            record['passed']=bool(all_passed)
    except (ValueError, SyntaxError, RecursionError, OSError, RuntimeError, subprocess.TimeoutExpired, InterruptedError) as exc:
        record['error']=type(exc).__name__+': '+str(exc)[:1000]
    (directory/'evidence.json').write_text(json.dumps(record,indent=2),encoding='utf-8')
    return record
