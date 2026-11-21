"""Owner CLI-only logical learning backup. Offline writers required; fresh restore only.

Archives are private plaintext with an independently retained HMAC key. They do
not contain device credentials, settings, vaults, model runtimes or executables.
Restored mentor grants are ALWAYS revoked. Never restore over the running Hub.
"""
import hashlib
import hmac
import io
import json
from pathlib import Path, PurePosixPath
import sqlite3
import tempfile
import zipfile
from contextlib import closing
from core import storage, teaching

MAX_ARCHIVE=32*1024*1024
MAX_EXPANDED=64*1024*1024


def _safe(path):
    p=PurePosixPath(path)
    if not path or '\\' in path or ':' in path or p.is_absolute() or any(x in ('','..','.') for x in path.split('/')):
        raise ValueError('Unsafe archive path')
    for part in p.parts:
        if part.endswith((' ','.')) or any(ord(x)<32 for x in part) or part.split('.')[0].upper() in {'CON','PRN','AUX','NUL',*(f'COM{i}' for i in range(1,10)),*(f'LPT{i}' for i in range(1,10))}:
            raise ValueError('Unsafe Windows archive path')
    if not path.startswith(('household/teaching/','household/engineering/','household/learned-procedures-')):
        raise ValueError('Archive member outside logical learning scope')
    return p


def _read(path):
    if path.is_symlink() or (getattr(path.stat(),'st_file_attributes',0)&0x400) or path.stat().st_nlink!=1:
        raise ValueError('Linked or reparse backup source rejected')
    raw=path.read_bytes()
    if len(raw)>8*1024*1024:raise ValueError('Backup member too large')
    return raw


def snapshot(key,*,offline=False):
    """Caller must stop all learning writers first. Locks also serialize gateway/index writes."""
    if offline is not True or not isinstance(key,bytes) or len(key)<32:
        raise ValueError('Offline writer shutdown and an independent >=32-byte key required')
    root=storage.DATA.resolve();members={}
    with teaching.database() as db:
        ids={r[0] for r in db.execute('SELECT doc_id FROM lessons WHERE indexed=1')}
        index_path=storage.path('household/engineering/index.json')
        lockpath=storage.path('household/engineering/write-lock.sqlite3')
        with closing(sqlite3.connect(lockpath,timeout=20)) as lock:
            lock.execute('CREATE TABLE IF NOT EXISTS mutex (id INTEGER PRIMARY KEY)');lock.commit();lock.execute('BEGIN IMMEDIATE')
            with tempfile.TemporaryDirectory(prefix='jarvis-learning-backup-') as tmp:
                target=Path(tmp)/'gateway.sqlite3'
                with closing(sqlite3.connect(storage.path('household/teaching/gateway.sqlite3'))) as source,closing(sqlite3.connect(target)) as destination:
                    source.backup(destination)
                members['household/teaching/gateway.sqlite3']=target.read_bytes()
            index=json.loads(_read(index_path)) if index_path.exists() else []
            selected=[r for r in index if r['id'] in ids]
            if {r['id'] for r in selected}!=ids:raise ValueError('Indexed lesson projection missing')
            members['household/engineering/index.json']=json.dumps(selected).encode()
            for row in selected:
                name='household/engineering/docs/'+row['id']+'.json';members[name]=_read(root/name)
            for relative in ('household/teaching/practice-v2','household/teaching/project-runs'):
                folder=root/relative
                if folder.exists():
                    for path in folder.rglob('*'):
                        if path.is_symlink() or (getattr(path.stat(),'st_file_attributes',0)&0x400):raise ValueError('Backup tree contains a reparse point')
                        if path.is_file() and path.suffix in {'.json','.py','.cpp','.md','.txt'}:
                            members[path.relative_to(root).as_posix()]=_read(path)
            for name in ('household/learned-procedures-pc.json','household/learned-procedures-server.json'):
                if (root/name).exists():members[name]=_read(root/name)
            lock.rollback()
    if sum(map(len,members.values()))>MAX_EXPANDED:raise ValueError('Backup quota exceeded')
    manifest={'version':1,'original_data_root':str(root),'scope':'learning only; binaries and runtime rebuildable; no device credentials',
              'members':{name:hashlib.sha256(raw).hexdigest() for name,raw in members.items()}}
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED) as archive:
        for name,raw in members.items():_safe(name);archive.writestr(name,raw)
        archive.writestr('manifest.json',json.dumps(manifest))
    raw=stream.getvalue()
    if len(raw)>MAX_ARCHIVE:raise ValueError('Archive quota exceeded')
    return raw,hmac.new(key,raw,hashlib.sha256).hexdigest(),manifest


def restore(raw,signature,key,destination):
    """Verify before creating anything, then restore into a new directory; no live overwrite."""
    if not isinstance(key,bytes) or len(key)<32 or len(raw)>MAX_ARCHIVE or not hmac.compare_digest(signature,hmac.new(key,raw,hashlib.sha256).hexdigest()):
        raise ValueError('Backup authentication failed')
    destination=Path(destination).absolute()
    if destination.exists() or destination.is_symlink():raise ValueError('Restore requires a fresh destination')
    for parent in destination.parents:
        if parent.exists() and (parent.is_symlink() or getattr(parent.stat(),'st_file_attributes',0)&0x400):
            raise ValueError('Restore ancestor is a reparse point')
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        infos=archive.infolist();names=[i.filename for i in infos]
        if len(names)!=len({n.casefold() for n in names}) or len(names)>10000 or sum(i.file_size for i in infos)>MAX_EXPANDED:
            raise ValueError('Archive inventory or quota invalid')
        if any(i.file_size>8*1024*1024 or (i.external_attr>>16)&0o170000==0o120000 for i in infos):
            raise ValueError('Oversize or linked archive member')
        manifest=json.loads(archive.read('manifest.json'))
        if manifest.get('version')!=1 or set(names)!={'manifest.json',*manifest['members']}:
            raise ValueError('Archive manifest mismatch')
        members={}
        for name,digest in manifest['members'].items():
            _safe(name);payload=archive.read(name)
            if hashlib.sha256(payload).hexdigest()!=digest:raise ValueError('Backup member integrity failed')
            members[name]=payload
    destination.mkdir(parents=False,exist_ok=False)
    try:
        for name,payload in members.items():
            path=destination.joinpath(*PurePosixPath(name).parts);path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(payload)
        with sqlite3.connect(destination/'household/teaching/gateway.sqlite3') as db:
            if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Restored SQLite integrity failed')
            db.execute('UPDATE mentors SET revoked=1')
            tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            old=Path(manifest['original_data_root'])
            for table in ('practice_runs','project_methods'):
                if table not in tables:continue
                for rid,report in db.execute(f'SELECT id,report FROM {table}').fetchall():
                    path=Path(report)
                    if not path.is_relative_to(old):raise ValueError('Evidence reference outside original learning root')
                    new=destination/path.relative_to(old)
                    if not new.is_file():raise ValueError('Restored evidence reference missing')
                    db.execute(f'UPDATE {table} SET report=? WHERE id=?',(str(new),rid))
            teaching.audit(db,'owner_restore','all_mentor_grants_revoked','backup')
        (destination/'RESTORE-RECEIPT.json').write_text(json.dumps({'authenticated':True,'source_manifest':manifest,'all_mentor_grants_revoked':True,'meaning':'Offline logical restore; evidence paths relocated, report bytes preserved.'},indent=2))
    except Exception:
        # Preserve failed restore for diagnosis, never activate or overwrite a live root.
        (destination/'RESTORE-FAILED').touch()
        raise
    return {'authenticated':True,'members':len(members),'destination':str(destination),'all_mentor_grants_revoked':True}
