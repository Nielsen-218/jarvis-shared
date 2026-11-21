import hashlib
import json
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from core import teaching, practice_agent


def lesson():
    return {'title':'Integer methods','topic':'Python','content':'Use bounded loops and test edge cases.',
        'source':'owner-authored','license':'owner-authorized','version':'1'}


@pytest.mark.parametrize('text',[
    '{"api_key": "not-a-real-secret-value"}',
    "'password': 'not-a-real-password'",
    'Authorization: Bearer placeholder-token-with-long-value',
])
def test_quoted_credentials_rejected_before_storage(text):
    token=teaching.grant('codex')
    with pytest.raises(ValueError): teaching.submit({**lesson(),'content':text},token)
    assert teaching.search('',token)==[]


def test_mcp_has_narrow_authentication_and_never_claims_verification(tmp_path,monkeypatch):
    from scripts import teaching_mcp
    token=teaching.grant('claude-code-local',scopes=['submit'])
    path=tmp_path/'credential';path.write_text(token)
    monkeypatch.setenv('JARVIS_MENTOR_TOKEN_FILE',str(path))
    result=json.loads(teaching_mcp.submit_lesson('Python loops','Python','Use bounded loops',
        'def solve(a,b): return a+b','Check invariants','Try sum with a loop','Python'))
    assert result['mentor']=='claude-code-local' and result['state']=='indexed_unverified'
    assert token not in json.dumps(result)
    teaching.revoke('claude-code-local')
    with pytest.raises(PermissionError):
        teaching_mcp.submit_lesson('Python loops','Python','Use bounded loops','code','methods','exercise','Python')


@pytest.mark.parametrize('failure_after_write',[False,True])
def test_projection_failure_retains_receipt_and_retries_without_duplicate_index(monkeypatch,failure_after_write):
    from tools import engineering
    token=teaching.grant('codex'); original=engineering._save_doc
    def failed(*a,**k):
        if failure_after_write: original(*a,**k)
        raise OSError('simulated interrupted library projection')
    monkeypatch.setattr(engineering,'_save_doc',failed)
    received=teaching.submit(lesson(),token)
    assert received['state']=='received_unindexed'
    assert teaching.search('integer',token)[0]['id']==received['id']
    monkeypatch.setattr(engineering,'_save_doc',original)
    retried=teaching.submit(lesson(),token)
    assert retried['duplicate'] and retried['id']==received['id']
    assert retried['state']=='indexed_unverified' and len(engineering._index())==1


def test_heldout_failure_never_saves_a_tested_method(monkeypatch):
    row=teaching.submit(lesson(),teaching.grant('codex'))
    # This deliberately overfits the four public training examples. Real hidden
    # executions reject it, proving training success alone cannot promote it.
    source='''def solve(a,b):
    if a == 0:
        return 0
    if a == 2:
        return 14
    if a == -3:
        return 0
    if a == 8:
        return 0
    return 123
'''
    monkeypatch.setattr(practice_agent,'local_generate',lambda *a,**k:({'source':source},{'test_model':True}))
    result=practice_agent.run('range_sum','python',[row['id']])
    assert result['attempts'][0]['training']['passed']
    assert result['state']=='heldout_failed' and not result['mastered']
    assert practice_agent.methods()==[]


def test_successful_methods_persist_with_evidence_without_mastery(monkeypatch):
    row=teaching.submit(lesson(),teaching.grant('codex'))
    monkeypatch.setattr(practice_agent,'local_generate',lambda *a,**k:({'source':'def solve(a,b): return a+b'},{'test_model':True}))
    result=practice_agent.run('addition','python',[row['id']])
    assert result['state']=='tested_on_cases' and not result['mastered']
    saved=practice_agent.methods('addition','python')
    assert len(saved)==1 and saved[0]['lessons']==json.dumps([row['id']])
    with teaching.database() as db:
        record=db.execute('SELECT report FROM practice_runs WHERE id=?',(saved[0]['id'],)).fetchone()
    from pathlib import Path
    assert hashlib.sha256(Path(record['report']).read_bytes()).hexdigest()==saved[0]['evidence_hash']


def test_stop_is_persisted_and_never_promoted(tmp_path,monkeypatch):
    row=teaching.submit(lesson(),teaching.grant('codex'))
    stop=tmp_path/'STOP';stop.touch()
    monkeypatch.setattr(practice_agent,'local_generate',lambda *a,**k:pytest.fail('must stop before inference'))
    result=practice_agent.run('addition','python',[row['id']],stop_file=stop)
    assert result['state']=='cancelled' and not practice_agent.methods()


def test_practice_api_rejects_untyped_or_arbitrary_inputs():
    from core.teaching_api import install
    app=FastAPI();install(app,lambda:('owner',{}));client=TestClient(app)
    assert client.post('/api/teaching/practice',json={'task':'shell','language':'cpp','lesson_ids':[]}).status_code==400
    assert client.post('/api/teaching/practice',json={'task':'addition','language':'python','lesson_ids':[],'command':'unsafe'}).status_code==400


def test_real_app_file_route_does_not_bypass_teaching_scope():
    from core import appserver,users
    saved=teaching.submit(lesson(),teaching.grant('codex'))
    app=appserver.AppServer(lambda uid:pytest.fail('file retrieval must not initialize a brain')).app
    client=TestClient(app)
    owner={'Authorization':'Bearer '+appserver.add_device('owner','test')}
    member=users.add('Member',{})
    guest={'Authorization':'Bearer '+appserver.add_device(member,'test')}
    private=f"/api/file/h/engineering/docs/{saved['doc_id']}.json"
    assert client.get(private,headers=owner).status_code==200
    for path in (private,'/api/file/h/engineering/index.json','/api/file/h/teaching/gateway.sqlite3'):
        assert client.get(path,headers=guest).status_code==403
    from core import storage
    storage.path('household/learned-procedures-pc.json').write_text('[]')
    storage.path('household/engineering/docs/orphan.json').write_text('{}')
    for path in ('/api/file/h/learned-procedures-pc.json','/api/file/h/engineering/docs/orphan.json'):
        assert client.get(path,headers=guest).status_code==403
    import os
    if os.name=='nt':
        for path in (private.upper().replace('/API/FILE/H/','/api/file/h/'),'/api/file/h/TEACHING/gateway.sqlite3','/api/file/h/LEARNED-PROCEDURES-PC.JSON'):
            assert client.get(path,headers=guest).status_code==403


def test_stop_interrupts_an_inflight_local_model_request(monkeypatch):
    import http.client
    import http.server
    import io
    import threading
    import time
    class Slow(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            time.sleep(2)
            try:
                self.send_response(200);self.end_headers();self.wfile.write(b'{}')
            except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError): pass
        def log_message(self,*a): pass
    server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Slow)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    original=http.client.HTTPConnection
    monkeypatch.setattr(practice_agent.http.client,'HTTPConnection',lambda *a,**k:original('127.0.0.1',server.server_port,timeout=5))
    class Installed:
        def open(self,*a,**k): return io.BytesIO(json.dumps({'models':[{'name':'local-test'}]}).encode())
    monkeypatch.setattr(practice_agent.urllib.request,'build_opener',lambda *a,**k:Installed())
    cancel=threading.Event();threading.Timer(.2,cancel.set).start()
    started=time.monotonic()
    try:
        with pytest.raises(InterruptedError): practice_agent.local_generate('test','local-test',cancel=cancel)
        assert time.monotonic()-started<1.5
    finally:
        server.shutdown();server.server_close()
