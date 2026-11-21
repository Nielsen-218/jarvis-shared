"""Topic-only tutorial discovery, honest modality evidence and isolated practice."""
import html
import json
from pathlib import Path
import re
import sqlite3
import tempfile
import threading
import time
import uuid
from urllib.parse import urlsplit, parse_qs

from core import research, storage, teaching, users

LOCK = threading.Lock()


def video_url(url):
    p = urlsplit(research.public_url(url, resolve=False))
    host = p.hostname
    if host in ('www.youtube.com', 'youtube.com','m.youtube.com') and p.path == '/watch':
        vid = parse_qs(p.query).get('v', [''])[0]
    elif host in ('www.youtube.com','youtube.com','m.youtube.com') and re.fullmatch(r'/(?:shorts|live|embed|v)/[A-Za-z0-9_-]{11}',p.path):
        vid=p.path.rsplit('/',1)[1]
    elif host == 'youtu.be':
        vid = p.path.strip('/')
    else:
        raise ValueError('Use a canonical public YouTube video URL')
    if not re.fullmatch(r'[A-Za-z0-9_-]{11}', vid):
        raise ValueError('Invalid YouTube video identifier')
    return 'https://www.youtube.com/watch?v=' + vid


def run_ytdlp(arguments, timeout=35):
    from tools.reach import _exe
    import os
    env = {k:v for k,v in os.environ.items() if k.upper() in
           {'SYSTEMROOT', 'WINDIR', 'PATH', 'TEMP', 'TMP', 'LOCALAPPDATA'}}
    command = [_exe('yt-dlp'), '--ignore-config', '--no-plugin-dirs', '--no-cache-dir',
               '--no-remote-components', '--socket-timeout', '10', '--retries', '0',
               '--fragment-retries', '0', *arguments]
    from core.research_process import run
    return run(command,env,timeout,research.stopped)


def discover(topic, limit=3):
    if not isinstance(topic, str) or not 3 <= len(topic) <= 200 or teaching.contains_credentials(topic):
        raise ValueError('Use a short public learning topic without secrets')
    if type(limit) is not int or not 2 <= limit <= 5:
        raise ValueError('Select 2-5 candidates')
    if research.stopped():raise InterruptedError('Research STOP is active')
    from datetime import datetime
    words = set(re.findall(r'[a-z]{3,}', topic.lower()))
    for saved in research.sources(topic):
        if (saved.get('modality')=='video_metadata_only' and
                len(saved.get('candidates',[]))>=limit and
                time.time()-datetime.fromisoformat(saved['retrieved_at']).timestamp()<3600):
            return {**saved,'cache_hit':True,'availability':'Cached live discovery within one hour; not a fresh caption check'}
    research.reserve('youtube_search')
    code, raw, errors = run_ytdlp(['--flat-playlist', '--dump-single-json', '--skip-download',
                                  f'ytsearch{limit}:{topic} tutorial'])
    if code:
        raise ValueError('YouTube search unavailable; no videos were studied')
    entries = json.loads(raw).get('entries', [])
    candidates = []
    for e in entries[:limit]:
        try: url = video_url(e['url'])
        except (KeyError, ValueError): continue
        title = e.get('title', '')[:300]
        score = len(words & set(re.findall(r'[a-z]{3,}', title.lower())))
        candidates.append({'url': url, 'title': title, 'creator': e.get('channel'),
                           'published_at': e.get('timestamp'), 'duration_seconds': e.get('duration'),
                           'topic_match_score': score, 'quality': 'Metadata relevance only; content not yet assessed'})
    if len(candidates) < 2:
        raise ValueError('Fewer than two usable tutorial candidates; discovery is incomplete')
    candidates.sort(key=lambda c: (-c['topic_match_score'], c.get('duration_seconds') or 999999))
    return research.save('youtube-search:' + topic, {'title': topic, 'candidates': candidates,
                                                   'modality': 'video_metadata_only'})


def cues(vtt):
    result = []; seen = set()
    for block in re.split(r'\n\s*\n', vtt.replace('\r', '')):
        lines = block.splitlines()
        idx = next((i for i,l in enumerate(lines) if '-->' in l), None)
        if idx is None: continue
        timing = lines[idx].split('-->')
        if not all(re.fullmatch(r'(?:\d{2}:)?\d{2}:\d{2}\.\d{3}',t.strip().split()[0]) for t in timing):continue
        text = html.unescape(re.sub(r'<[^>]*>', '', ' '.join(lines[idx+1:]))).strip()
        if text and text not in seen:
            seen.add(text)
            result.append({'start': timing[0].strip(), 'end': timing[1].strip().split()[0], 'text': text[:800]})
    return result[:250]


def transcript(url):
    url = video_url(url)
    with research.database() as db:
        row = db.execute("SELECT value FROM settings WHERE key='youtube_caption_cooldown'").fetchone()
    if row and float(row[0]) > time.time():
        return {'available': False, 'reason': 'YouTube caption rate-limit cooldown is active', 'cues': []}
    research.reserve('youtube_captions')
    with tempfile.TemporaryDirectory(prefix='jarvis-captions-') as folder:
        code, out, error = run_ytdlp(['--skip-download', '--no-simulate', '--write-subs',
            '--write-auto-subs', '--sub-langs', 'en', '--sub-format', 'vtt', '--no-playlist',
            '--print', '%(title)s', '-o', str(Path(folder)/'caption.%(ext)s'), url])
        if '429' in error:
            with research.database() as db:
                db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',
                           ('youtube_caption_cooldown', str(time.time()+3600)))
            return {'available': False, 'reason': 'YouTube HTTP429; paused captions for one hour; no bypass or paid fallback', 'cues': []}
        paths = list(Path(folder).glob('caption*.vtt'))
        if code or not paths:
            return {'available': False, 'reason': 'Authorized English captions unavailable; browser transcript backend is not connected', 'cues': []}
        if paths[0].stat().st_size > 2*1024*1024:
            raise ValueError('Caption file exceeds quota')
        chunks = cues(paths[0].read_text(encoding='utf-8-sig'))
    return {'available': bool(chunks), 'reason': 'At most 250 timestamped caption excerpts; no frames or audio observed', 'cues': chunks,
            'excerpt_limit_reached':len(chunks)==250}


def study(topic):
    if users.get(users.current()).get('role') != 'owner':
        raise PermissionError('Only the owner can add shared tutorial lessons')
    if not isinstance(topic,str) or not 3<=len(topic)<=200 or teaching.contains_credentials(topic):
        raise ValueError('Use a short public learning topic without secrets')
    if not LOCK.acquire(blocking=False):
        raise ValueError('A tutorial study is already running')
    report = {'id': uuid.uuid4().hex, 'topic': topic, 'state': 'failed', 'mastered': False,
              'cloud_inference': False, 'visuals_observed': False}
    try:
        found = discover(topic); report['discovery'] = found
        studied = {r['url'] for r in research.sources() if r.get('modality') == 'timestamped_captions'}
        chosen = next((c for c in found['candidates'] if c['url'] not in studied), None)
        if not chosen:
            report['state'] = 'already_studied'; return report
        report['selected'] = chosen
        data = transcript(chosen['url']); report['transcript'] = data
        if data['available']:
            source = research.save(chosen['url'], {'title': chosen['title'], 'cues': data['cues'],
                'modality': 'timestamped_captions', 'creator': chosen['creator'],
                'limitations': 'Caption reference only. Not full audiovisual understanding.'})
        else:
            # A separate publisher reference is explicit fallback, never a fake transcript.
            report['state'] = 'transcript_unavailable'
            if 'python' not in topic.lower(): return report
            source = research.open_page('https://docs.python.org/3/tutorial/controlflow.html')
            report['fallback'] = {'url': source['url'], 'modality': source['modality'],
                                  'reason': 'Official documentation; YouTube was NOT studied'}
        content = '\n'.join(c['start']+' '+c['text'] for c in source.get('cues', []))
        if source.get('instruction_indicators'):
            report['state']='source_instructions_withheld'
            return report
        if not content:
            from core.autolearn import _passage, _tokens
            content = _passage(source.get('text', ''), _tokens(topic), limit=12000)
        content = content[:18000]
        # Extractive source material is retained as data, never installed as system instructions.
        saved = teaching.submit({'title': 'Tutorial research: '+topic, 'topic': topic,
            'source': source['url'], 'version': source['sha256'],
            'license': 'Public reference excerpts for private study; publisher rights retained',
            'content': content, 'architecture': 'Untrusted source-linked reference; no execution authority.',
            'techniques': 'Candidate reference only; verify through independent practice.',
            'mistakes': data['reason'], 'exercises': 'Use a distinct registered sandbox exercise.',
            'tests': 'Unverified source reference. No skill mastery or model weight update.'}, owner=True)
        report['lesson_id'] = saved['id']; report['source_sha256'] = source['sha256']
        report['state'] = 'candidate_saved' if data['available'] else 'documentation_fallback_saved'
        return report
    except Exception as exc:
        report['error_kind'] = type(exc).__name__
        report['state'] = ('cancelled' if isinstance(exc,InterruptedError) else 'storage_unavailable' if isinstance(exc,sqlite3.Error)
                           else 'provider_unavailable' if isinstance(exc,(OSError,RuntimeError)) else 'source_or_internal_error')
        report['error'] = ('Tutorial study stopped: '+report['state']+'. Existing Jarvis capabilities remain available. No paid fallback.')
        return report
    finally:
        p = storage.path('research/tutorial-runs/'+report['id']+'.json')
        p.write_text(json.dumps(report, indent=2), encoding='utf-8')
        LOCK.release()


def practice(lesson_id, task='word_totals', procedure_id=None):
    if users.get(users.current()).get('role') != 'owner':
        raise PermissionError('Owner practice only')
    if research.stopped(): raise InterruptedError('Research STOP is active')
    from core import project_agent
    return project_agent.run(task, procedure_id=procedure_id, lesson_id=lesson_id)
