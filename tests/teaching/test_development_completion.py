import json
import subprocess
import sys
import pytest
from core.development_teaching import complete


@pytest.fixture
def repository(tmp_path):
    root=tmp_path/'repo';root.mkdir()
    def git(*args):
        return subprocess.run(['git',*args],cwd=root,capture_output=True,text=True,check=True).stdout.strip()
    git('init');git('config','user.name','Local test');git('config','user.email','test@example.invalid')
    (root/'README.md').write_text('test repository')
    git('add','README.md');git('commit','-m','base');base=git('rev-parse','HEAD')
    (root/'core').mkdir();(root/'core/example.py').write_text('def add(a,b): return a+b\n')
    (root/'knowledge').mkdir()
    spec={k:'Instructional '+k for k in ('title','content','architecture','techniques','mistakes','dependencies','exercises')}
    path=root/'knowledge/task.json';path.write_text(json.dumps(spec))
    git('add','.');git('commit','-m','feature plus teaching spec')
    return root,path,base


def test_passing_completion_automatically_produces_lesson_and_measured_receipt(repository,tmp_path):
    root,spec,base=repository
    receipt=complete(root,spec,base,[[sys.executable,'-c','print("observed success")']],tmp_path/'out',enforce_spec_update=True)
    assert receipt['state']=='development_complete' and receipt['package_saved']
    assert receipt['skill_state']=='unverified' and receipt['submission']=='queued'
    payload=json.loads((tmp_path/'out/lesson.json').read_text())
    tests=json.loads(payload['tests'])
    assert tests['results'][0]['exit_code']==0
    assert 'observed success' in tests['results'][0]['excerpt']
    assert any(r['path']=='core/example.py' for r in tests['source_files'])


def test_failed_check_keeps_unverified_lesson_and_blocks_completion(repository,tmp_path):
    root,spec,base=repository
    receipt=complete(root,spec,base,[[sys.executable,'-c','raise SystemExit(3)']],tmp_path/'out')
    assert receipt['state']=='development_checks_failed' and receipt['skill_state']=='unverified'
    assert (tmp_path/'out/lesson.json').exists()


def test_gateway_rejection_keeps_durable_outbox(repository,tmp_path):
    root,spec,base=repository
    token=tmp_path/'invalid';token.write_text('invalid-test-placeholder')
    receipt=complete(root,spec,base,[[sys.executable,'-c','pass']],tmp_path/'out',token_file=token)
    assert receipt['checks_passed'] and receipt['submission']=='queued'
    assert (tmp_path/'out/lesson.json').exists()


def test_instructional_spec_must_change_with_task(repository,tmp_path):
    root,spec,base=repository
    current=subprocess.run(['git','rev-parse','HEAD'],cwd=root,text=True,capture_output=True,check=True).stdout.strip()
    with pytest.raises(ValueError,match='Update the instructional'):
        complete(root,spec,current,[[sys.executable,'-c','pass']],tmp_path/'out',enforce_spec_update=True)


def test_source_changed_by_check_keeps_lesson_but_rejects_completion(repository,tmp_path):
    root,spec,base=repository
    command=[sys.executable,'-c',"from pathlib import Path;Path('core/example.py').write_text('def add(a,b): return 0\\n')"]
    receipt=complete(root,spec,base,[command],tmp_path/'out')
    assert receipt['state']=='development_source_changed' and not receipt['checks_passed']
    assert not receipt['source_stable_during_checks'] and receipt['skill_state']=='unverified'
    assert (tmp_path/'out/lesson.json').exists()
