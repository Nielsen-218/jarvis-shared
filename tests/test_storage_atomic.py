"""A transient Windows file lock must not discard persisted Jarvis knowledge."""
import json
import pytest
from core import storage,fileio

def locked():
    error=PermissionError('destination temporarily held')
    error.winerror=32
    return error

def test_transient_windows_lock_retries_without_modifying_old_json(tmp_path,monkeypatch):
    path=tmp_path/'knowledge.json';path.write_text('{"version":1}')
    original=storage.os.replace
    calls=[]
    def replace(source,target):
        calls.append(1)
        if len(calls)<=2:
            assert json.loads(path.read_text())=={'version':1}
            raise locked()
        original(source,target)
    monkeypatch.setattr(storage.os,'replace',replace)
    monkeypatch.setattr(fileio.time,'sleep',lambda _:None)
    storage._write(path,{'version':2})
    assert len(calls)==3 and json.loads(path.read_text())=={'version':2}

def test_persistent_windows_lock_raises_and_preserves_both_versions(tmp_path,monkeypatch):
    path=tmp_path/'knowledge.json';path.write_text('{"version":1}')
    monkeypatch.setattr(storage.os,'replace',lambda *a:(_ for _ in ()).throw(locked()))
    monkeypatch.setattr(fileio.time,'sleep',lambda _:None)
    with pytest.raises(PermissionError):storage._write(path,{'version':2})
    assert json.loads(path.read_text())=={'version':1}
    assert json.loads(path.with_suffix('.tmp').read_text())=={'version':2}

def test_unrelated_permission_failure_is_not_hidden(tmp_path,monkeypatch):
    def fail(*a):raise PermissionError('actual permissions')
    monkeypatch.setattr(storage.os,'replace',fail)
    monkeypatch.setattr(fileio.time,'sleep',lambda _:pytest.fail('No unrelated permission retries'))
    with pytest.raises(PermissionError):storage._write(tmp_path/'knowledge.json',{})
