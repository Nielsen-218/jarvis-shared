"""Trusted isolated browser worker. No model-selected JavaScript or credentials output."""
import argparse
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from urllib.parse import urlsplit

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from core.browser_proxy import Egress,start

SENSITIVE=re.compile(r'checkout|purchase|payment|delete|publish|send|password|security|credential|grant|install',re.I)


def execute(job,*,fixture=None):
    from playwright.sync_api import sync_playwright
    origin=job.get('scope',{}).get('origin');action=job.get('action','read')
    policy=Egress(fixture,origin);proxy=start(policy)
    url=job['url'];parts=urlsplit(url)
    if parts.username is not None or parts.password is not None:raise ValueError('URL credentials rejected')
    if action!='read' and action not in {'fill_draft','save_draft'}:raise ValueError('Sensitive browser actions require transaction policy')
    if action!='read' and (parts.scheme+'://'+parts.netloc!=origin or SENSITIVE.search(parts.path)):
        raise ValueError('Action outside named site scope')
    allowed_post=job.get('scope',{}).get('post_path') if action=='save_draft' else None
    if allowed_post and SENSITIVE.search(allowed_post):raise ValueError('Sensitive transaction path rejected')
    counts={'requests':0,'navigations':0,'denied':0}
    try:
        with tempfile.TemporaryDirectory(prefix='jarvis-browser-') as folder,sync_playwright() as pw:
            print('browser-stage: launch',file=sys.stderr,flush=True)
            browser=pw.chromium.launch(headless=True,chromium_sandbox=True,
                downloads_path=folder,
                proxy={'server':'http://127.0.0.1:'+str(proxy.server_address[1]),'bypass':'<-loopback>',
                       'username':policy.username,'password':policy.password},
                args=['--enable-automation','--disable-quic','--disable-gpu','--force-webrtc-ip-handling-policy=disable_non_proxied_udp',
                      '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1',
                      '--disable-background-networking','--disable-extensions'])
            session=None
            if job.get('session_file'):
                if not origin:raise ValueError('Account session requires a named origin')
                path=Path(job['session_file'])
                if path.stat().st_size>256000:raise ValueError('Account session quota')
                raw=json.loads(path.read_text(encoding='utf-8'))
                host=urlsplit(origin).hostname
                session={'cookies':[cookie for cookie in raw.get('cookies',[]) if
                    host==cookie.get('domain','').lstrip('.') or host.endswith('.'+cookie.get('domain','').lstrip('.'))],
                    'origins':[o for o in raw.get('origins',[]) if o.get('origin')==origin]}
            context=browser.new_context(accept_downloads=False,service_workers='block',
                storage_state=session,permissions=[],viewport={'width':1000,'height':700})
            print('browser-stage: context',file=sys.stderr,flush=True)
            context.set_default_timeout(8000)
            # Routed sockets are not connected to any server unless explicitly
            # forwarded. This local mock discards messages and exposes no egress.
            context.route_web_socket('**/*',lambda ws:ws.on_message(lambda message:None))
            def route(request_route):
                request=request_route.request;counts['requests']+=1
                p=urlsplit(request.url)
                is_fixture=fixture and p.scheme+'://'+p.netloc==fixture
                safe=p.scheme=='https' or is_fixture
                try:
                    if not safe or counts['requests']>24:raise ValueError('Navigation budget')
                    # The proxy also independently checks/pins DNS for every tunnel.
                    if request.method not in ('GET','HEAD'):
                        if (request.method!='POST' or not allowed_post or p.path!=allowed_post
                                or p.scheme+'://'+p.netloc!=origin):raise ValueError('Write not authorized')
                    if origin and p.scheme+'://'+p.netloc!=origin:raise ValueError('Cross-site account action denied')
                    if request.is_navigation_request():
                        counts['navigations']+=1
                        if counts['navigations']>4:raise ValueError('Navigation quota')
                    if request.resource_type in ('media','font'):raise ValueError('Heavy resource denied')
                    request_route.continue_()
                except ValueError:
                    counts['denied']+=1;request_route.abort()
            context.route('**/*',route)
            page=context.new_page()
            page.on('dialog',lambda dialog:dialog.dismiss())
            context.on('page',lambda popup:popup.close() if popup!=page else None)
            page.on('download',lambda download:download.cancel())
            page.goto(url,wait_until='domcontentloaded',timeout=15000)
            print('browser-stage: navigated',file=sys.stderr,flush=True)
            page.wait_for_timeout(500)
            from core.browser_attestation import attest
            cdp=browser.new_browser_cdp_session()
            args=cdp.send('Browser.getBrowserCommandLine')['arguments']
            if '--no-sandbox' in args or '--disable-web-security' in args:raise RuntimeError('Unsafe browser launch flags')
            profile=next((a.split('=',1)[1] for a in args if a.startswith('--user-data-dir=')),None)
            if not profile or not Path(profile).resolve().is_relative_to(Path(os.environ['TEMP']).resolve()):
                raise RuntimeError('Browser profile must remain in the broker-owned temporary session')
            processes=cdp.send('SystemInfo.getProcessInfo')['processInfo']
            boundary=attest([int(p['id']) for p in processes if p['type'].lower()=='renderer'])
            cdp.detach()
            if action!='read':
                selector=job['scope']['field_selector']
                text=job.get('text','')
                if not isinstance(text,str) or len(text)>2000:raise ValueError('Draft text quota')
                page.locator(selector).fill(text)
                if action=='save_draft':
                    page.locator(job['scope']['save_selector']).click()
                    page.wait_for_timeout(300)
            print('browser-stage: action-complete',file=sys.stderr,flush=True)
            text=page.locator('body').inner_text()[:12000]
            links=page.locator('a[href]').evaluate_all('(items) => items.slice(0,20).map(a => ({text:a.innerText.slice(0,120),url:a.href}))')
            result={'title':page.title(),'url':page.url,'text':text,'links':links,
                    'action':action,'limits':counts,'egress':{'bytes':policy.bytes,'connections':policy.requests,'denied':policy.blocked},
                    'sandbox_attestation':boundary,
                    'ephemeral_profile_verified':True,
                    'isolation':'Ephemeral browser context; Chromium renderer sandbox enabled; pinned public egress proxy; no owner profile',
                    'session_credentials_exposed':False}
            context.close();browser.close()
            print('browser-stage: closed',file=sys.stderr,flush=True)
            return result
    finally:proxy.shutdown();proxy.server_close()


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--fixture-origin');a=p.parse_args()
    raw=sys.stdin.read(16001)
    if len(raw)>16000:raise ValueError('Request quota')
    # Synthetic fixture override is CLI-only and never accepted by production tools.
    print(json.dumps(execute(json.loads(raw),fixture=a.fixture_origin)))
