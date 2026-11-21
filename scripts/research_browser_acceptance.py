"""Explicit synthetic authenticated browser acceptance; no real accounts or Hub."""
import argparse
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import tempfile
import threading
from unittest.mock import patch
from urllib.parse import urlsplit

from core import storage,research,research_browser


def main():
    p=argparse.ArgumentParser();p.add_argument('--runtime',type=Path,required=True);p.add_argument('--outdir',type=Path,required=True);a=p.parse_args()
    cookie=secrets.token_urlsafe(24);saved=[];canary=[];proxy_auth_leaked=[]
    class Site(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            if self.headers.get('Proxy-Authorization'):proxy_auth_leaked.append(True)
            if self.path=='/canary':canary.append(True)
            if self.headers.get('Cookie')!='jarvis_fixture='+cookie:
                self.send_response(401);self.end_headers();return
            self.send_response(200);self.send_header('Content-Type','text/html');self.end_headers()
            self.wfile.write(b'''<title>Scoped draft fixture</title><textarea id="draft"></textarea><button id="save">Save draft</button><div id="result"></div><div id="private"></div><script>
                const p=document.createElement('p');p.innerText='JavaScript READY';document.body.appendChild(p);
                const c=document.createElement('p');c.innerText=document.cookie?'COOKIE VISIBLE':'HttpOnly protected';document.body.appendChild(c);
                document.getElementById('save').onclick=()=>fetch('/draft/save',{method:'POST',body:document.getElementById('draft').value}).then(r=>r.text()).then(t=>document.getElementById('result').innerText=t);
                fetch('/publish',{method:'POST',body:'forbidden'}).catch(()=>{});
                fetch('CANARY_URL').then(()=>document.getElementById('private').innerText='PRIVATE LEAK').catch(()=>document.getElementById('private').innerText='Private blocked');
                new WebSocket('CANARY_WS');
            </script>'''.replace(b'CANARY_URL',(canary_origin+'/canary').encode()).replace(b'CANARY_WS',(canary_origin.replace('http:','ws:')+'/canary').encode()))
        def do_POST(self):
            if self.headers.get('Proxy-Authorization'):proxy_auth_leaked.append(True)
            if self.path!='/draft/save' or self.headers.get('Cookie')!='jarvis_fixture='+cookie:
                self.send_response(403);self.end_headers();return
            saved.append(self.rfile.read(int(self.headers.get('Content-Length','0'))).decode())
            self.send_response(200);self.end_headers();self.wfile.write(b'Draft saved')
    site=ThreadingHTTPServer(('127.0.0.1',0),Site);threading.Thread(target=site.serve_forever,daemon=True).start()
    origin='http://127.0.0.1:'+str(site.server_address[1])
    other=ThreadingHTTPServer(('127.0.0.1',0),Site);threading.Thread(target=other.serve_forever,daemon=True).start()
    canary_origin='http://127.0.0.1:'+str(other.server_address[1])
    report={'synthetic_fixture_only':True,'production_modified':False,'runs':[]}
    try:
        with tempfile.TemporaryDirectory(prefix='jarvis-browser-acceptance-') as folder:
            storage.DATA=Path(folder)/'data';research_browser.configure(json.loads(a.runtime.read_text()))
            session=Path(folder)/'session.json';session.write_text(json.dumps({'cookies':[{'name':'jarvis_fixture','value':cookie,'domain':'127.0.0.1','path':'/','httpOnly':True,'secure':False,'sameSite':'Strict','expires':-1}],'origins':[]}))
            original=research.public_url
            def fixture_url(url,**kwargs):
                p=urlsplit(url)
                if p.scheme+'://'+p.netloc==origin:return url if p.path else url+'/'
                return original(url,**kwargs)
            # Only this owned synthetic origin is exempt in this parent test.
            # The browser's proxy still independently denies every other private destination.
            with patch.object(research,'public_url',fixture_url):
                grant=research_browser.grant({'name':'fixture-draft','origin':origin,'page_path':'/draft',
                    'actions':['read','fill_draft','save_draft'],'hours':1,'field_selector':'#draft','save_selector':'#save',
                    'post_path':'/draft/save','session_file':str(session)})
                report['grant']={k:grant[k] for k in ('name','actions','expires')}
                for text in ['First synthetic draft','Second synthetic draft']:
                    try:r=research_browser.browser(origin+'/draft','fixture-draft','save_draft',text,_fixture=origin)
                    except Exception:
                        report['failure_diagnostics']=[json.loads(p.read_text()) for p in storage.path('research/browser-runs').glob('*.json')]
                        raise
                    assert 'JavaScript READY' in r['text'] and 'Draft saved' in r['text']
                    assert 'Private blocked' in r['text'] and 'PRIVATE LEAK' not in r['text']
                    assert 'HttpOnly protected' in r['text'] and 'COOKIE VISIBLE' not in r['text']
                    assert r['sandbox_attestation']['attested'] is True
                    assert cookie not in json.dumps(r) and r['limits']['denied']>=1
                    report['runs'].append(r)
                assert saved==['First synthetic draft','Second synthetic draft']
                assert not canary,'Browser reached a different private-network origin'
                assert not proxy_auth_leaked,'Proxy authentication leaked to a website'
                for action in ['publish','delete','purchase']:
                    try:research_browser.browser(origin+'/draft','fixture-draft',action,_fixture=origin)
                    except PermissionError:pass
                    else:raise AssertionError('Sensitive action permitted')
                research_browser.revoke('fixture-draft')
                try:research_browser.browser(origin+'/draft','fixture-draft','save_draft','third',_fixture=origin)
                except PermissionError:report['revocation_enforced']=True
                else:raise AssertionError('Revoked scope still worked')
            from core.browser_proxy import Egress
            for target in ['https://127.0.0.1/','https://169.254.169.254/','https://10.0.0.1/']:
                try:Egress().address(target)
                except ValueError:pass
                else:raise AssertionError('Private egress accepted')
            report.update(drafts_saved=len(saved),sensitive_actions_denied=True,private_destinations_denied=True,
                          session_credentials_exposed=False,extra_approval_dialogs=0,canary_requests=len(canary),
                          proxy_credentials_forwarded=len(proxy_auth_leaked))
    finally:
        site.shutdown();site.server_close();other.shutdown();other.server_close();a.outdir.mkdir(parents=True,exist_ok=True)
        (a.outdir/'real-browser-authenticated.json').write_text(json.dumps(report,indent=2))
    print(json.dumps({k:report[k] for k in ('drafts_saved','revocation_enforced','session_credentials_exposed','sensitive_actions_denied')}))


if __name__=='__main__':main()
