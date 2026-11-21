"""Additive public research, finite budgets and local provenance. Content is data."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import ipaddress
import json
import re
import socket
import sqlite3
import time
from urllib.parse import urlsplit, urljoin, urlunsplit, parse_qsl

from core import storage, teaching

import os as _os
DAILY_CALLS = int(_os.getenv("JARVIS_RESEARCH_DAILY", "120"))   # free-provider calls per day (searches, pages, videos)
DISK_BYTES = 10 * 1024 * 1024
INJECTION = re.compile(r'ignore\s+(?:all\s+)?(?:previous|prior|system)\s+instructions|reveal\s+(?:the\s+)?system\s+prompt|(?:send|upload|exfiltrate)\s+(?:your\s+)?(?:credentials|secrets|cookies)', re.I)


def public_url(url, *, resolve=True):
    if not isinstance(url, str) or len(url) > 2048 or any(c.isspace() for c in url):
        raise ValueError('Use a short public HTTPS URL')
    p = urlsplit(url)
    if (p.scheme != 'https' or not p.hostname or p.username is not None or p.password is not None
            or p.port not in (None, 443) or '\\' in url or teaching.contains_credentials(url)):
        raise ValueError('Credentials, special destinations and non-HTTPS URLs are forbidden')
    host = p.hostname.lower()
    if any(re.search(r'token|secret|password|credential|session|api.?key|signature|authorization', k, re.I)
           for k,v in parse_qsl(p.query)):
        raise ValueError('Credential-like query parameters are forbidden')
    if host == 'localhost' or host.endswith(('.localhost', '.local', '.internal')):
        raise ValueError('Private destinations are forbidden')
    try: address = ipaddress.ip_address(host)
    except ValueError: address = None
    if address is not None and not address.is_global:
        raise ValueError('Private destinations are forbidden')
    if resolve:
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
            raise ValueError('Private DNS destinations are forbidden')
    return urlunsplit(('https', p.netloc.lower().removesuffix(':443'), p.path or '/', p.query, ''))


@contextmanager
def database():
    db = sqlite3.connect(storage.path('research/state.sqlite3'), timeout=15)
    db.row_factory = sqlite3.Row
    try:
        db.executescript('''CREATE TABLE IF NOT EXISTS sources
          (id TEXT PRIMARY KEY, url TEXT, digest TEXT, payload TEXT, created REAL,
           UNIQUE(url,digest));
          CREATE TABLE IF NOT EXISTS audit(at REAL, day TEXT, operation TEXT, state TEXT);
          CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);''')
        db.execute('BEGIN IMMEDIATE')
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def stopped():
    return storage.path('research/STOP').exists()


def disk_bytes():
    root=storage.path('research/.keep').parent
    return sum(p.stat().st_size for p in root.rglob('*') if p.is_file() and 'browser-tmp' not in p.relative_to(root).parts)


def stop(paused=True):
    if type(paused) is not bool: raise ValueError('Paused must be boolean')
    p = storage.path('research/STOP')
    if paused:
        p.touch()
    else:
        p.unlink(missing_ok=True)
    return {'paused': paused}


def reserve(operation):
    if stopped():
        raise InterruptedError('Research STOP is active; other Jarvis capabilities remain available')
    day = datetime.now(timezone.utc).date().isoformat()
    with database() as db:
        count = db.execute('SELECT count(*) FROM audit WHERE day=?', (day,)).fetchone()[0]
        size = db.execute('SELECT coalesce(sum(length(cast(payload as blob))),0) FROM sources').fetchone()[0]
        if count >= DAILY_CALLS or max(size,disk_bytes()) >= DISK_BYTES:
            raise ValueError('Research daily request or storage budget reached')
        db.execute('INSERT INTO audit VALUES(?,?,?,?)', (time.time(), day, operation, 'started'))
        return db.execute('SELECT last_insert_rowid()').fetchone()[0]


def invoke(operation, callback):
    if operation=='mcp_page':
        with database() as db:
            row=db.execute("SELECT value FROM settings WHERE key='exa_cooldown'").fetchone()
        if row and float(row[0])>time.time():raise RuntimeError('Exa quota cooldown is active; public RSS search remains available')
    ticket = reserve(operation)
    state = 'returned'
    try:
        return callback()
    except Exception as exc:
        state = 'failed:' + type(exc).__name__
        if getattr(exc,'retry_after_seconds',0):
            with database() as db:
                db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',('exa_cooldown',str(time.time()+exc.retry_after_seconds)))
        raise
    finally:
        with database() as db:
            db.execute('UPDATE audit SET state=? WHERE rowid=?', (state, ticket))


def save(url, payload):
    if stopped():
        raise InterruptedError('Research cancelled; fetched material was not promoted')
    payload = dict(payload)
    material = json.dumps(payload, ensure_ascii=False)
    if INJECTION.search(material):
        def scrub(value):
            if isinstance(value, str) and INJECTION.search(value): return '[Withheld: instruction-injection indicators.]'
            if isinstance(value, list): return [scrub(v) for v in value]
            if isinstance(value, dict): return {k:scrub(v) for k,v in value.items()}
            return value
        payload = scrub(payload)
        payload['instruction_indicators'] = True
        payload['withheld_content_sha256'] = hashlib.sha256(material.encode()).hexdigest()
        if 'text' in payload: payload['text'] = '[Withheld: instruction-injection indicators; no commands executed.]'
        for result in payload.get('results', []): result['excerpt'] = '[Withheld: instruction-injection indicators.]'
    payload = {**payload, 'url': url, 'retrieved_at': datetime.now(timezone.utc).isoformat(),
               'trust': 'untrusted_reference_data', 'mastered': False}
    raw = json.dumps(payload)
    if teaching.contains_credentials(raw) or len(raw.encode()) > 120000:
        raise ValueError('Refusing sensitive or oversized research material')
    digest = hashlib.sha256(raw_without_time(payload).encode()).hexdigest()
    sid = hashlib.sha256((url + digest).encode()).hexdigest()[:32]
    payload.update(id=sid, sha256=digest)
    with database() as db:
        size = db.execute('SELECT coalesce(sum(length(cast(payload as blob))),0) FROM sources').fetchone()[0]
        old = db.execute('SELECT payload FROM sources WHERE id=?', (sid,)).fetchone()
        if old:
            return json.loads(old[0])
        if max(size,disk_bytes()) + len(raw.encode()) + 8192 > DISK_BYTES:
            raise ValueError('Research storage budget reached')
        db.execute('INSERT INTO sources VALUES(?,?,?,?,?)',
                   (sid, url, digest, json.dumps(payload), time.time()))
    return payload


def raw_without_time(payload):
    return json.dumps({k:v for k,v in payload.items() if k not in
                       {'retrieved_at', 'id', 'sha256'}}, sort_keys=True, ensure_ascii=False)


def sources(query=''):
    with database() as db:
        rows = [json.loads(r[0]) for r in db.execute('SELECT payload FROM sources ORDER BY created DESC LIMIT 100')]
    terms = [t for t in query.lower().split() if t not in
             {'saved','stored','local','offline','research','references','sources','notes','about','what','have','you','the','my','do'}]
    matched=[r for r in rows if all(t in json.dumps(r).lower() for t in terms)]
    unique={}
    for row in matched:unique.setdefault(row['url'],row)
    priority={'timestamped_captions':4,'page_text':3,'isolated_browser_text':3,'video_metadata_only':2,'search_results':1}
    return sorted(unique.values(),key=lambda r:priority.get(r.get('modality'),0),reverse=True)[:20]


def status():
    day = datetime.now(timezone.utc).date().isoformat()
    with database() as db:
        calls = db.execute('SELECT count(*) FROM audit WHERE day=?', (day,)).fetchone()[0]
        count, size = db.execute('SELECT count(*),coalesce(sum(length(cast(payload as blob))),0) FROM sources').fetchone()
        recent = [dict(r) for r in db.execute('SELECT at,operation,state FROM audit ORDER BY rowid DESC LIMIT 5')]
    browser_ready=bool(storage.load_global('research-browser.json',{}))
    from core import users
    site_scopes=[]
    if users.get(users.current()).get('role')=='owner':
        for name,scope in storage.load('research/site-scopes.json',{}).items():
            if scope.get('expires',0)>time.time():
                site_scopes.append({'name':name,'origin':scope['origin'],'page_path':scope['page_path'],
                                    'actions':scope['actions'],'expires':scope['expires']})
    return {'paused': stopped(), 'requests_today': calls, 'daily_limit': DAILY_CALLS,
            'sources': count, 'stored_bytes': disk_bytes(), 'source_payload_bytes':size,'disk_limit': DISK_BYTES,
            'recent_provider_attempts': recent,
            'mcp_provider': 'Bundled STDIO public RSS search; explicit Exa public extraction; live availability measured per call',
            'javascript_browser': 'Configured isolated Chromium via bundled MCP; availability measured per call' if browser_ready else 'No isolated browser configured; existing desktop tools unchanged',
            'interactive_sites': 'Standing owner site scopes support reversible draft actions; no account access without a matching unexpired grant',
            'available_site_scopes':site_scopes,
            'paid_fallback': False, 'mastered': False}


class Page(HTMLParser):
    def __init__(self):
        super().__init__(); self.title = ''; self.text = []; self.links = []
        self.hidden = 0; self.in_title = False; self.date = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ('script', 'style', 'noscript', 'template'):
            self.hidden += 1
        if tag == 'title' and not self.title:   # the document's title only, not every SVG icon's <title>
            self.in_title = True
        if tag == 'a' and a.get('href'): self.links.append(a['href'])
        if tag == 'meta' and a.get('property', a.get('name', '')) in ('article:published_time', 'date', 'datePublished'):
            self.date = a.get('content')

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'noscript', 'template'): self.hidden = max(0, self.hidden-1)
        if tag == 'title': self.in_title = False

    def handle_data(self, data):
        if not self.hidden:
            if self.in_title: self.title += data
            self.text.append(data)


def open_page(url):
    from core.teaching_import import fetch, extract
    url = public_url(url)
    data, mime = invoke('open', lambda: fetch(url))  # Pinned address, TLS, no redirects.
    links = []; title = url; date = None
    if mime == 'text/html':
        page = Page(); page.feed(data.decode('utf-8', errors='replace'))
        text = ' '.join(' '.join(page.text).split()); title = page.title[:300] or url; date = page.date
        for link in page.links[:100]:
            try:
                clean = public_url(urljoin(url, link), resolve=False)
                if clean not in links: links.append(clean)
            except ValueError: continue
    else:
        text = extract(urlsplit(url).path, data, mime)
    if len(text.strip()) < 30:
        raise ValueError('No substantive page text; JavaScript rendering may be required')
    return save(url, {'title': title, 'text': text[:45000], 'links': links[:30],
                      'publisher': urlsplit(url).hostname, 'published_at': date,
                      'modality': 'page_text', 'redirect_policy': 'blocked'})


def follow(source_id, link_index):
    with database() as db:
        row=db.execute('SELECT payload FROM sources WHERE id=?',(source_id,)).fetchone()
    source=json.loads(row[0]) if row else None
    if not source or type(link_index) is not int or not 0 <= link_index < len(source.get('links', [])):
        raise ValueError('Select a stored source and one of its listed links')
    return open_page(source['links'][link_index])


def search(query, limit=3):
    """Public search results, saved (modality search_results). The provider chain lives in core/websearch.py (Exa,
    then Exa over HTTP, then Bing RSS, then DuckDuckGo); the bundled RSS MCP server alone returned unrelated items
    (a mattress shop for an NVIDIA driver query, 2026-10-30) because Bing serves junk to its plain User-Agent."""
    from core import websearch
    if not isinstance(query, str) or not 2 <= len(query.strip()) <= 300 or teaching.contains_credentials(query):
        raise ValueError('Use a public search topic without private information')
    if type(limit) is not int or not 1 <= limit <= 5:
        raise ValueError('Select 1-5 results')
    try:
        pack = websearch.search(query, limit)
    except websearch.SearchUnavailable as e:
        raise RuntimeError('No search provider could answer: ' + str(e)) from e   # (InterruptedError under STOP passes through)
    with database() as db:
        row = db.execute('SELECT payload FROM sources WHERE id=?', (pack.get('source_id'),)).fetchone()
    payload = json.loads(row[0]) if row else {'title': query, 'results': pack['results'], 'modality': 'search_results',
                                              'provider': pack['provider']}
    return {**payload, 'results': payload.get('results', [])[:limit]}


def remote_page(url):
    """Remote public extraction; never claim an attested interactive browser."""
    from core.research_mcp import call
    url = public_url(url)
    text = invoke('mcp_page', lambda: call('web_fetch_exa', {'urls':[url], 'maxCharacters':12000}))
    if len(text.strip()) < 50: raise ValueError('Provider returned no substantive page text')
    return save(url, {'title': url, 'text': text[:14000], 'provider':'Exa MCP',
                      'publisher':urlsplit(url).hostname, 'modality':'remote_page_extraction',
                      'javascript_execution':'not_attested',
                      'limitations':'Public extraction on remote infrastructure; no owner browser session, cookie access or interactive authority.'})


def browse(url,scope_id='',action='read',text=''):
    from core.research_mcp import call
    from core.research_browser import authorize
    url=public_url(url)
    authorize(url,scope_id,action)
    if stopped():raise InterruptedError('Research STOP is active')
    return json.loads(call('public_browser',{'url':url,'scope_id':scope_id,'action':action,'text':text}))
