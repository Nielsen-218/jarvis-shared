import json
import os
import threading
import pytest
from core import project_agent as agent, project_tasks as tasks, project_sandbox as sandbox

pytestmark = pytest.mark.serial   # AppContainer profile setup isn't safe across parallel workers


@pytest.mark.parametrize('bad',[
    {'../escape.py':['pass']}, {'solution.py':['pass']},
    {n:['a\nb'] for n in tasks.FILES}, {n:['x'*19000] for n in tasks.FILES},
])
def test_project_edits_have_strict_paths_and_quotas(bad):
    with pytest.raises(ValueError):agent.validate_files(bad)


def test_trusted_contract_edges():
    assert tasks.expected('event_ledger','x|a|bad\nx|a|2\nx|b|4\ny|a|-2')=={'balances':{'a':0},'duplicates':1,'rejected':1}
    assert tasks.expected('revision_ledger','x|9|3\nx|10|2\nx|10|5\ny|١|4')=={'values':{'x':2},'stale':1,'rejected':1}
    for task in tasks.TASKS:
        assert not {c['input'] for c in tasks.cases(task,'public')} & {c['input'] for c in tasks.cases(task,'heldout')}


def test_project_is_opt_in(monkeypatch):
    monkeypatch.delenv('JARVIS_PROJECT_LAB_ROOT',raising=False)
    with pytest.raises(ValueError,match='disabled'):agent.run('event_ledger')


def test_failed_boundary_never_calls_model_or_promotes(tmp_path,monkeypatch):
    lab=sandbox.initialize(tmp_path/'lab');monkeypatch.setenv('JARVIS_PROJECT_LAB_ROOT',str(lab))
    monkeypatch.setattr(agent,'attest',lambda _: {'attested':False})
    monkeypatch.setattr(agent,'local_generate',lambda *a,**k:pytest.fail('Inference must not start'))
    result=agent.run('event_ledger')
    assert result['state']=='blocked' and result['mastered'] is False and agent.methods()==[]
    from core import storage
    packages=list(storage.DATA.rglob('lesson.json'))
    assert len(packages)==1 and 'blocked' in json.loads(packages[0].read_text())['tests']


def test_project_routes_are_owner_only(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from core import teaching_api,users
    who=['member'];app=FastAPI();teaching_api.install(app,lambda:(who[0],'device'))
    monkeypatch.setattr(users,'get',lambda uid:{'role':'owner' if uid=='owner' else 'member'})
    with TestClient(app) as client:
        assert client.post('/api/teaching/project',json={'task':'event_ledger'}).status_code==403
        assert client.get('/api/teaching/project/methods').status_code==403
        who[0]='owner';monkeypatch.delenv('JARVIS_PROJECT_LAB_ROOT',raising=False)
        assert client.post('/api/teaching/project',json={'task':'event_ledger'}).status_code==404
        monkeypatch.setenv('JARVIS_PROJECT_LAB_ROOT','unused')
        assert client.post('/api/teaching/project',json={'task':'event_ledger','path':'C:/'}).status_code==400


def test_real_app_device_auth_is_separate_from_mentor_grants():
    from fastapi.testclient import TestClient
    from core import appserver,teaching
    mentor=teaching.grant('submit-only-project-check',scopes=['submit'])
    with TestClient(appserver.AppServer(lambda uid:None).app) as client:
        assert client.post('/api/teaching/project',json={'task':'event_ledger'}).status_code==401
        assert client.post('/api/teaching/project',json={'task':'event_ledger'},headers={'Authorization':'Bearer '+mentor}).status_code==401


@pytest.mark.skipif(os.name!='nt',reason='Native Windows boundary test; no Linux execution fallback')
def test_native_boundary_compile_runtime_deadline_and_stop(tmp_path):
    lab=sandbox.initialize(tmp_path/'lab')
    attestation=agent.attest(lab)
    assert attestation['attested'],attestation
    project=lab/'limits';project.mkdir()
    worker=project/'worker.py';worker.write_text('while True: pass\n')
    sandbox.grant_readonly(project,lab)
    limited=sandbox.run(lab/'runtime',project,worker,timeout=.3)
    assert limited['resumed'] and not limited['passed_boundary'] and 'TimeoutError' in limited['error']
    assert limited['duration_seconds']<3
    stop=threading.Event();stop.set()
    stopped=sandbox.run(lab/'runtime',project,worker,cancel=stop)
    assert not stopped['passed_boundary'] and 'STOP' in stopped['error']
    files={'parser.py':['def parse(text): return text'], 'ledger.py':['def result(text): return {"input":text}'],
           'solution.py':['from parser import parse','from ledger import result','def solve(text): return result(parse(text))']}
    evidence=agent.evaluate(lab,lab/'good',files,[{'input':'abc','expected':{'input':'abc'}}])
    assert evidence['passed'],evidence
    files['solution.py']=['this is invalid python !!!']
    failed=agent.evaluate(lab,lab/'bad',files,[{'input':'abc','expected':{}}])
    assert not failed['passed'] and failed['compile']['exit_code']!=0 and failed['cases']==[]


@pytest.mark.skipif(os.name!='nt',reason='Native Windows mandatory Job failure test')
def test_native_job_failure_cannot_resume_candidate(tmp_path,monkeypatch):
    import win32job
    lab=sandbox.initialize(tmp_path/'lab');sandbox.prepare_runtime(lab/'runtime')
    project=lab/'project';project.mkdir();worker=project/'worker.py';worker.write_text('print("must never run")')
    sandbox.grant_readonly(project,lab)
    def unavailable(*a):raise RuntimeError('injected Job assignment failure')
    monkeypatch.setattr(win32job,'AssignProcessToJobObject',unavailable)
    result=sandbox.run(lab/'runtime',project,worker)
    assert not result['resumed'] and not result['passed_boundary'] and result['stdout']==''


@pytest.mark.skipif(os.name!='nt',reason='Native Windows isolation between independent task identities')
def test_new_identity_cannot_read_previous_task_source_even_with_known_path(tmp_path):
    first=sandbox.initialize(tmp_path/'first');previous=first/'project';previous.mkdir()
    secret=previous/'solution.py';secret.write_text('retained source must not be visible')
    sandbox.grant_readonly(previous,first)
    second=sandbox.initialize(tmp_path/'second');sandbox.prepare_runtime(second/'runtime')
    current=second/'project';current.mkdir();worker=current/'probe.py'
    worker.write_text('from pathlib import Path\ntry:\n    Path('+repr(str(secret))+').read_text()\n    print("ALLOWED")\nexcept PermissionError:\n    print("DENIED")\n')
    sandbox.grant_readonly(current,second)
    result=sandbox.run(second/'runtime',current,worker)
    assert result.get('exit_code')==0 and result.get('passed_boundary') and result['stdout'].strip()=='DENIED',result
