"""Owner-configured isolated browser broker and reusable, revocable site scopes."""
import json
import os
from pathlib import Path
import re
import time
import tempfile
import uuid
from urllib.parse import urlsplit
from core import storage,research,users,teaching

SENSITIVE=re.compile(r'checkout|purchase|payment|delete|publish|send|password|security|credential|grant|install',re.I)


def configure(runtime):
    if users.get(users.current()).get('role')!='owner':raise PermissionError('Owner browser setup only')
    python=Path(runtime['python']).resolve();browsers=Path(runtime['browsers']).resolve()
    if not python.is_file() or not browsers.is_dir():raise ValueError('Installed isolated browser runtime required')
    storage.save_global('research-browser.json',{'python':str(python),'browsers':str(browsers),'playwright':runtime['playwright']})


def scopes():
    return storage.load('research/site-scopes.json',{})


def grant(spec):
    if users.get(users.current()).get('role')!='owner':raise PermissionError('Owner site grants only')
    required={'name','origin','page_path','actions','hours'}
    if set(spec)-required-{'field_selector','save_selector','post_path','session_file'} or not required<=set(spec):
        raise ValueError('Use a named, explicit site/action scope')
    if not re.fullmatch(r'[a-z0-9_-]{1,40}',spec['name']):raise ValueError('Invalid scope name')
    url=research.public_url(spec['origin'])
    if urlsplit(url).path!='/' or urlsplit(url).query:raise ValueError('Scope origin must contain no path/query')
    for name in ('page_path','post_path'):
        path=spec.get(name,'/')
        if not isinstance(path,str) or not re.fullmatch(r'/[A-Za-z0-9_./-]*',path) or '//' in path or '..' in path or SENSITIVE.search(path):
            raise ValueError('Sensitive or invalid site path')
    if (not isinstance(spec['actions'],list) or not spec['actions'] or
            set(spec['actions'])-{'read','fill_draft','save_draft'}):raise ValueError('Only read and reversible draft actions are supported')
    if type(spec['hours']) not in (int,float) or not 0<spec['hours']<=168:raise ValueError('Scope expiration must be within one week')
    if 'fill_draft' in spec['actions'] or 'save_draft' in spec['actions']:
        selector=spec.get('field_selector','')
        if not selector or len(selector)>150 or SENSITIVE.search(selector):raise ValueError('Use a precise non-sensitive draft field selector')
    if 'save_draft' in spec['actions'] and not all(spec.get(k) for k in ('post_path','save_selector')):
        raise ValueError('Saving drafts requires an exact POST path and save selector')
    if SENSITIVE.search(spec.get('save_selector','')):raise ValueError('Sensitive selector rejected')
    if spec.get('session_file') and not Path(spec['session_file']).is_file():raise ValueError('Use an existing private authorized session file')
    rows=scopes();rows[spec['name']]={**spec,'origin':url.rstrip('/'),'expires':time.time()+spec['hours']*3600}
    storage.save('research/site-scopes.json',rows)
    return {'name':spec['name'],'actions':spec['actions'],'expires':rows[spec['name']]['expires']}


def revoke(name):
    if users.get(users.current()).get('role')!='owner':raise PermissionError('Owner scope revocation only')
    rows=scopes();rows.pop(name,None);storage.save('research/site-scopes.json',rows)


def authorize(url,scope_id,action):
    if action not in {'read','fill_draft','save_draft'}:raise PermissionError('Consequential transactions require separate authorization')
    if scope_id:
        if users.get(users.current()).get('role')!='owner':raise PermissionError('Account scopes are owner-only')
        scope=scopes().get(scope_id)
        p=urlsplit(url)
        if (not scope or scope['expires']<=time.time() or action not in scope['actions'] or
            p.scheme+'://'+p.netloc!=scope['origin'] or p.path!=scope['page_path'] or p.query):
            raise PermissionError('Site/action scope absent, expired or mismatched')
        return scope
    elif action!='read':raise PermissionError('A standing owner site/action scope is required')
    return None


def browser(url,scope_id=None,action='read',text='',*,_fixture=None):
    settings=storage.load_global('research-browser.json',{})
    if not settings:raise ValueError('An isolated browser runtime has not been configured')
    url=research.public_url(url)
    scope=authorize(url,scope_id,action)
    job={'url':url,'action':action}
    if scope:
        job['scope']=scope
        if scope.get('session_file'):job['session_file']=scope['session_file']
    if not isinstance(text,str) or len(text)>2000 or teaching.contains_credentials(text):raise ValueError('Draft text must be short and contain no credentials')
    job['text']=text
    env={k:v for k,v in os.environ.items() if k.upper() in {'SYSTEMROOT','WINDIR','PATH','TEMP','TMP','LOCALAPPDATA','USERPROFILE'}}
    env['PLAYWRIGHT_BROWSERS_PATH']=settings['browsers']
    from core.research_process import run
    def execute():
        command=[settings['python'],str(Path(__file__).resolve().parents[1]/'scripts/research_browser.py')]
        if _fixture:
            p=urlsplit(_fixture)
            if p.scheme!='http' or p.hostname!='127.0.0.1' or not p.port or p.path or p.query or p.fragment or p.username is not None:
                raise ValueError('Synthetic fixture must be one exact loopback origin')
            command+=['--fixture-origin',_fixture] # Internal test-only argument, absent from MCP/tool/HTTP schemas.
        try:
            temp_root=storage.path('research/browser-tmp/.keep').parent.resolve()
            with tempfile.TemporaryDirectory(prefix='session-',dir=temp_root) as directory:
                assert Path(directory).resolve().is_relative_to(temp_root)
                env['TEMP']=env['TMP']=directory
                code,out,error=run(command,env,45,research.stopped,
                    input_text=json.dumps(job),memory_bytes=1536*1024*1024,process_limit=12)
        except Exception as exc:
            detail=getattr(exc,'provider_stderr','')[-8000:]
            storage.path('research/browser-runs/'+uuid.uuid4().hex+'.json').write_text(json.dumps(
                {'at':time.time(),'error':type(exc).__name__,'stderr':detail if not teaching.contains_credentials(detail) else '[withheld]'},indent=2))
            raise
        diagnostic={'at':time.time(),'exit_code':code,'action':action,'scope_id':scope_id,
                    'stderr':error[-10000:] if not teaching.contains_credentials(error) else '[withheld credential-like diagnostic]'}
        storage.path('research/browser-runs/'+uuid.uuid4().hex+'.json').write_text(json.dumps(diagnostic,indent=2))
        if code:raise RuntimeError('Isolated browser failed; inspect private diagnostics')
        return json.loads(out)
    result=research.invoke('isolated_browser',execute)
    return research.save(url,{**result,'modality':'isolated_browser_text','account_scope':scope_id,
                              'limits_note':'45 seconds, 24 requests, four navigations, 16MiB network and 1.5GiB process-tree memory'})
