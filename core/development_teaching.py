"""Development completion is tests AND an automatically generated lesson/outbox.

This developer/CI module is not exposed to mentors or Jarvis tool execution. Check
commands are explicit repository-owned argv arrays, never shell strings. Test
success is measured here; importing the generated lesson never certifies a skill.
"""
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import time

from core import teaching

REQUIRED={'title','content','architecture','techniques','mistakes','dependencies','exercises'}
ALLOWED_ROOTS={'core','tools','scripts','app','hud','tests','docs','knowledge'}
CODE_EXT={'.py','.cpp','.h','.js','.ts','.html','.md','.txt','.yml','.yaml'}


def git(root,*args):
    return subprocess.run(['git',*args],cwd=root,capture_output=True,text=True,check=True).stdout.strip()


def capture_sources(root,base):
    names=git(root,'diff','--name-only',base,'HEAD').splitlines()
    # Local completion also captures unstaged/staged tracked edits. CI sees a
    # clean committed tree. Untracked code must be selected by staging it first.
    names+=git(root,'diff','--name-only','HEAD').splitlines()
    references=[]; snippets=[]; total=0
    for name in sorted(set(names)):
        relative=Path(name)
        extra=(len(relative.parts)==1 and relative.name.startswith('requirements') and relative.suffix=='.txt') or relative.as_posix().startswith('.github/workflows/')
        if not relative.parts or (relative.parts[0] not in ALLOWED_ROOTS and not extra) or relative.suffix not in CODE_EXT: continue
        path=(root/relative).resolve()
        if not path.is_relative_to(root) or not path.is_file(): continue
        content=path.read_bytes()
        item={'path':name,'sha256':hashlib.sha256(content).hexdigest(),'bytes':len(content)}
        references.append(item)
        if len(content)>100000:
            item['excerpt']='omitted: source size'; continue
        text=content.decode('utf-8')
        if teaching.contains_credentials(text):
            item['excerpt']='omitted: credential-like literals; inspect source locally'; continue
        excerpt=text[:4500]
        if total+len(excerpt)<=45000:
            snippets.append(name+'\n'+excerpt);total+=len(excerpt)
    if not references: raise ValueError('No task source changes found relative to base')
    return references,'\n\n'.join(snippets)


def complete(root,spec_path,base,checks,outdir,*,token_file=None,timeout=600,enforce_spec_update=False):
    root=Path(root).resolve(); outdir=Path(outdir).resolve()
    spec_path=Path(spec_path).resolve()
    if not spec_path.is_relative_to(root) or spec_path.stat().st_size>16000:
        raise ValueError('Select a small repository-owned teaching specification')
    spec=json.loads(spec_path.read_text(encoding='utf-8'))
    if set(spec)!=REQUIRED or any(not isinstance(spec[k],str) or not spec[k].strip() for k in REQUIRED):
        raise ValueError('Teaching specification must explain all required instructional fields')
    if enforce_spec_update:
        changed=git(root,'diff','--name-only',base,'HEAD').splitlines()
        if spec_path.relative_to(root).as_posix() not in changed:
            raise ValueError('Update the instructional task specification for this development change')
    if not checks or any(not isinstance(c,list) or not c or any(not isinstance(x,str) for x in c) for c in checks):
        raise ValueError('At least one explicit check argv array is required')
    revision=git(root,'rev-parse','HEAD')
    refs,examples=capture_sources(root,base)
    outdir.mkdir(parents=True,exist_ok=True)
    results=[]
    for index,argv in enumerate(checks):
        started=time.monotonic(); report_path=outdir/f'check-{index}.txt'
        with report_path.open('wb') as log:
            try:
                result=subprocess.run(argv,cwd=root,stdout=log,stderr=subprocess.STDOUT,timeout=timeout)
                code=result.returncode
            except subprocess.TimeoutExpired:
                code=None
        raw=report_path.read_bytes()
        # Whole local report remains private. Never export credential-like text
        # into a lesson; hashing still binds the actual captured report.
        excerpt=raw[-16000:].decode('utf-8',errors='replace')
        if teaching.contains_credentials(excerpt): excerpt='Report excerpt withheld: credential-like text; inspect private local report.'
        results.append({'argv':argv,'exit_code':code,'duration_seconds':round(time.monotonic()-started,3),
            'report_sha256':hashlib.sha256(raw).hexdigest(),'excerpt':excerpt})
    try:
        after,_=capture_sources(root,base)
        stable=git(root,'rev-parse','HEAD')==revision and after==refs
    except (OSError,ValueError,UnicodeError,subprocess.CalledProcessError):stable=False
    passed=all(r['exit_code']==0 for r in results) and stable
    payload=teaching.validate({**spec,'topic':'Jarvis development','source':'git:'+revision,
        'license':'owner-authorized repository development','version':revision,
        'examples':examples,'tests':json.dumps({'development_checks_passed':passed,
            'results':results,'source_files':refs,'source_stable_during_checks':stable,
            'meaning':'Measured development checks; not skill mastery or instructor certification.'})})
    package_path=outdir/'lesson.json'
    package_path.write_text(json.dumps(payload,indent=2),encoding='utf-8')
    receipt={'checks_passed':passed,'package_saved':True,'package_sha256':hashlib.sha256(package_path.read_bytes()).hexdigest(),
        'state':('development_complete' if passed else 'development_checks_failed') if stable else 'development_source_changed',
        'source_stable_during_checks':stable,
        'skill_state':'unverified','source_revision':revision,'source_files':refs,'submission':'queued'}
    if token_file:
        try:
            saved=teaching.submit(payload,Path(token_file).read_text(encoding='utf-8').strip())
            receipt['submission']='indexed' if saved['indexed'] else 'received_unindexed'
            receipt['lesson_id']=saved['id']
        except (OSError,ValueError,PermissionError,sqlite3.Error):
            receipt['submission']='queued'
    (outdir/'completion.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
    return receipt
