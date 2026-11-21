import json
import socket
import time
import pytest
from core import research_browser,storage,users
from core.browser_proxy import Egress


@pytest.fixture
def public_dns(monkeypatch):
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**k:[(2,1,6,'',('93.184.216.34',443))])


def spec():
    return {'name':'draft-site','origin':'https://example.com','page_path':'/draft',
            'actions':['read','fill_draft','save_draft'],'hours':1,
            'field_selector':'#draft','save_selector':'#save','post_path':'/draft/save'}


def runtime(tmp_path):
    python=tmp_path/'python.exe';python.write_text('fixture');browsers=tmp_path/'browsers';browsers.mkdir()
    research_browser.configure({'python':str(python),'browsers':str(browsers),'playwright':'fixture'})


def test_standing_grant_reuse_expiry_and_revocation(public_dns,tmp_path,monkeypatch):
    runtime(tmp_path);research_browser.grant(spec())
    from core import research_process
    calls=[]
    def run(command,env,timeout,cancelled,**kwargs):
        calls.append(json.loads(kwargs['input_text']))
        return 0,json.dumps({'title':'draft','text':'Saved draft','url':'https://example.com/draft'}),''
    monkeypatch.setattr(research_process,'run',run)
    for _ in range(2):research_browser.browser('https://example.com/draft','draft-site','save_draft','example draft')
    assert len(calls)==2
    assert all(j['scope']['post_path']=='/draft/save' for j in calls)
    rows=research_browser.scopes();rows['draft-site']['expires']=time.time()-1
    storage.save('research/site-scopes.json',rows)
    with pytest.raises(PermissionError):research_browser.browser('https://example.com/draft','draft-site','save_draft')
    research_browser.grant(spec());research_browser.revoke('draft-site')
    with pytest.raises(PermissionError):research_browser.browser('https://example.com/draft','draft-site','save_draft')


@pytest.mark.parametrize('field,value',[('origin','https://127.0.0.1'),('page_path','/account/delete'),
    ('post_path','/publish'),('save_selector','#purchase'),('field_selector','#password'),
    ('hours',169),('actions',['execute_js']),('name','../../escape'),('post_path','/%64elete'),
    ('post_path','/draft/../publish'),('page_path','//other.example')])
def test_grants_cannot_authorize_sensitive_or_private_actions(public_dns,field,value):
    row=spec();row[field]=value
    with pytest.raises((ValueError,PermissionError)):research_browser.grant(row)


def test_member_cannot_grant_or_use_account_scopes(public_dns,tmp_path):
    runtime(tmp_path);research_browser.grant(spec());member=users.add('scope member',{})
    with users.acting_as(member):
        with pytest.raises(PermissionError):research_browser.grant(spec())
        with pytest.raises(PermissionError):research_browser.browser('https://example.com/draft','draft-site')


@pytest.mark.parametrize('url,action',[('https://other.example/draft','read'),
    ('https://example.com/other','save_draft'),('https://example.com/draft?extra=1','save_draft'),
    ('https://example.com/draft','publish'),('https://example.com/draft','purchase')])
def test_site_path_query_and_action_scope_are_exact(public_dns,tmp_path,url,action):
    runtime(tmp_path);research_browser.grant(spec())
    with pytest.raises(PermissionError):research_browser.browser(url,'draft-site',action)


def test_public_context_never_inherits_session_or_grant(public_dns,tmp_path,monkeypatch):
    runtime(tmp_path)
    from core import research_process
    jobs=[]
    def run(command,env,*args,**kwargs):
        jobs.append(json.loads(kwargs['input_text']))
        assert not any(k.startswith(('OPENAI_','ANTHROPIC_')) for k in env)
        return 0,json.dumps({'text':'Public page text','url':'https://example.com/'}),''
    monkeypatch.setattr(research_process,'run',run)
    research_browser.browser('https://example.com/')
    assert 'session_file' not in jobs[0] and 'scope' not in jobs[0]


def test_proxy_pins_validated_address_and_rejects_mixed_dns(monkeypatch):
    addresses=[(2,1,6,'',('93.184.216.34',443))]
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**k:addresses)
    policy=Egress();assert policy.address('https://example.com/')==('93.184.216.34',443)
    addresses.append((2,1,6,'',('127.0.0.1',443)))
    with pytest.raises(ValueError):policy.address('https://example.com/')


def test_account_proxy_denies_cross_origin_even_for_public_hosts(public_dns):
    policy=Egress(allowed_origin='https://example.com')
    assert policy.address('https://example.com:443')==('93.184.216.34',443)
    with pytest.raises(ValueError):policy.address('https://other.example:443')


def test_proxy_transfer_budget_is_shared_across_connections():
    policy=Egress();policy.account(16*1024*1024)
    with pytest.raises(ValueError):policy.account(1)


def test_http_model_cannot_supply_synthetic_fixture_bypass(public_dns):
    from fastapi.testclient import TestClient
    from core import appserver
    token=appserver.add_device('owner','fixture test')
    with TestClient(appserver.AppServer(lambda uid:None).app) as client:
        result=client.post('/api/teaching/research/browse',json={'url':'https://example.com/','_fixture':'http://127.0.0.1:9'},headers={'Authorization':'Bearer '+token})
        assert result.status_code==400


def test_scope_records_never_include_raw_session_credentials(public_dns,tmp_path):
    session=tmp_path/'session.json';session.write_text(json.dumps({'cookies':[{'name':'example','value':'fixture-only'}]}))
    row=spec();row['session_file']=str(session);result=research_browser.grant(row)
    assert 'session_file' not in result and 'fixture-only' not in json.dumps(result)


def test_ephemeral_proxy_requires_authentication_before_any_egress():
    import http.client
    from core.browser_proxy import start
    policy=Egress();server=start(policy)
    try:
        c=http.client.HTTPConnection('127.0.0.1',server.server_address[1],timeout=2)
        c.request('CONNECT','127.0.0.1:443');r=c.getresponse()
        assert r.status==407 and policy.requests==0
        c.close()
        c=http.client.HTTPConnection('127.0.0.1',server.server_address[1],timeout=2)
        c.request('CONNECT','127.0.0.1:443',headers={'Proxy-Authorization':policy.authorization});r=c.getresponse()
        assert r.status==403 and policy.requests==0
        c.close()
    finally:server.shutdown();server.server_close()


def test_owner_can_discover_reusable_scopes_without_credentials(public_dns):
    from core import research
    research_browser.grant(spec())
    scopes=research.status()['available_site_scopes']
    assert scopes[0]['name']=='draft-site'
    assert set(scopes[0])=={'name','origin','page_path','actions','expires'}
    member=users.add('scope status member',{})
    with users.acting_as(member):assert research.status()['available_site_scopes']==[]
