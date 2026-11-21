"""Nolagin v7 local readiness, split isolation and sustained HUD regression."""
import json
from pathlib import Path
import subprocess
import sys
import os
import shutil

import numpy as np
import pytest
from PIL import Image

from scripts import nolagin
from scripts.gamecoach_vision_validate import verify_heldout_isolation


@pytest.mark.parametrize("script",["nolagin.py","gamecoach_vision_validate.py","gamecoach_apex_train.py"])
def test_standalone_commands_resolve_project_imports_from_any_working_directory(tmp_path,script):
    source=Path(nolagin.__file__).parent/script
    result=subprocess.run([sys.executable,str(source),"--help"],cwd=tmp_path,
                          capture_output=True,text=True,timeout=15)
    assert result.returncode==0,result.stderr
    assert "usage:" in result.stdout


@pytest.mark.skipif(sys.platform!="win32" or not shutil.which("powershell"),
                    reason="Windows PowerShell runner")
def test_powershell_runner_uses_isolated_interpreter_and_forwards_options(tmp_path):
    local=tmp_path/"local"
    isolated=local/"JarvisNolagin"/"venv"
    subprocess.run([sys.executable,"-m","venv","--without-pip",str(isolated)],
                   check=True,capture_output=True,timeout=30)
    runner=Path(nolagin.__file__).with_name("nolagin_run.ps1")
    result=subprocess.run(["powershell","-NoProfile","-File",str(runner),
                           "doctor","--root",str(tmp_path/"empty"),"--game","cod"],
                          env={**os.environ,"LOCALAPPDATA":str(local)},cwd=tmp_path,
                          capture_output=True,text=True,timeout=30)
    if result.returncode and "UnauthorizedAccess" in result.stderr:
        pytest.skip("Host execution policy blocks PowerShell files; policy left unchanged")
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)["game"]=="cod"


@pytest.mark.parametrize("command",["doctor","extract","label","train","certify"])
@pytest.mark.parametrize("position",["before","after"])
def test_cli_accepts_root_and_game_on_either_side_of_command(monkeypatch,tmp_path,capsys,command,position):
    seen=[]
    monkeypatch.setattr(nolagin,"_run_command",lambda args:seen.append(args) or {})
    options=["--root",str(tmp_path),"--game","apex"]
    arguments={"extract":["--train-clip","a.mp4","--val-clip","b.mp4","--heldout-clip","c.mp4"],
               "label":["--split","train"],"train":["--weights","local.pt"]}.get(command,[])
    argv=options+[command]+arguments if position=="before" else [command]+arguments+options
    assert nolagin.main(argv)==0
    assert seen[0].root==tmp_path and seen[0].game=="apex"


def test_cli_missing_assets_returns_actionable_error_without_traceback(tmp_path,capsys):
    assert nolagin.main(["certify","--root",str(tmp_path)])==2
    output=capsys.readouterr().out
    assert json.loads(output)["error_type"]=="FileNotFoundError"
    assert "Traceback" not in output


def test_failed_recertification_revokes_previous_live_approval(monkeypatch,tmp_path):
    import core.gamecoach_enemy_detector as enemy
    import scripts.gamecoach_vision_validate as validation
    manifest=tmp_path/"vision-apex.json"
    manifest.write_text(json.dumps({"reviewed":True}))
    ds=tmp_path/"nolagin-datasets"/"heldout"
    ds.mkdir(parents=True)
    (ds/"dataset.json").write_text("{}")
    monkeypatch.setattr(enemy,"load_config",lambda *a,**k:object())
    monkeypatch.setattr(enemy,"GameEnemyDetector",lambda cfg:object())
    monkeypatch.setattr(validation,"evaluate",lambda *a:{"passed":False,"cases":200,
        "precision":.8,"recall":.9,"validation_dataset_sha256":"a"*64})
    assert nolagin.certify("apex",tmp_path)["passed"] is False
    assert json.loads(manifest.read_text())["reviewed"] is False
    assert json.loads((tmp_path/"nolagin-apex-certification.json").read_text())["passed"] is False

def test_doctor_reports_unknown_game_and_no_false_hardware_results(tmp_path):
    report=nolagin.inspect(tmp_path,game="apex",platform="win32")
    assert report["windows_gaming_pc"] is True
    assert report["real_live_game_measurements_verified"] is False
    assert report["model"]["ready"] is False
    assert not report["frameview_off_on_off_real_benchmarks_present"]
    assert "heldout" in report["datasets"]
    assert len(report["next_steps"])>0
    with pytest.raises(ValueError):
        nolagin.inspect(tmp_path,"unknown")

def test_video_split_hashes_must_be_unique(tmp_path):
    clips={}
    for split,content in zip(nolagin.SPLITS,(b"training game",b"validation game",b"held-out game")):
        path=tmp_path/(split+".mp4")
        path.write_bytes(content)
        clips[split]=path
    result=nolagin.ensure_distinct_clips(clips)
    assert len(set(result.values()))==3
    clips["heldout"].write_bytes(clips["train"].read_bytes())
    with pytest.raises(ValueError,match="DIFFERENT"):
        nolagin.ensure_distinct_clips(clips)

def test_heldout_certification_refuses_model_train_clip_overlap():
    a="1"*64
    b="2"*64
    c="3"*64
    meta={"training_source_clip_sha256":[a,b]}
    verify_heldout_isolation(meta,{"source_clip_sha256":c})
    with pytest.raises(ValueError,match="overlaps"):
        verify_heldout_isolation(meta,{"source_clip_sha256":a})
    with pytest.raises(ValueError,match="checksum"):
        verify_heldout_isolation(meta,{"split":"heldout"})

def test_game_running_fails_closed_when_training(monkeypatch,tmp_path):
    monkeypatch.setattr(nolagin,"_game_running",lambda game: True)
    with pytest.raises(RuntimeError,match="Close the game"):
        nolagin.train("apex",tmp_path,tmp_path/"weights.pt",30,"0")
    with pytest.raises(ValueError,match="Only Apex"):
        nolagin.train("cod",tmp_path,tmp_path/"weights.pt",30,"0")

def test_doctor_tracks_incomplete_human_annotation_without_leaking_frames(tmp_path):
    ds=tmp_path/"nolagin-datasets"/"heldout"
    ds.mkdir(parents=True)
    (ds/"dataset.json").write_text(json.dumps({
        "game":"apex","split":"heldout","cases":[
            {"image":"a.png","human_reviewed":True,"boxes":[[1,1,12,16]]},
            {"image":"b.png","human_reviewed":False,"boxes":[]}
        ]}))
    s=nolagin.inspect(tmp_path)
    assert s["datasets"]["heldout"]["frames"]==2
    assert s["datasets"]["heldout"]["reviewed"]==1
    assert s["datasets"]["heldout"]["complete"] is False
    assert "boxes" not in str(s)


def test_doctor_separates_exclusions_and_disputes_from_valid_labels(tmp_path):
    path=tmp_path/'nolagin-datasets'/'train';path.mkdir(parents=True)
    (path/'dataset.json').write_text(json.dumps({'game':'apex','split':'train','cases':[
        {'human_reviewed':True,'boxes':[[1,1,12,16]]},
        {'human_reviewed':True,'excluded':True,'boxes':[]},
        {'human_reviewed':True,'needs_recheck':True,'boxes':[[1,1,12,16]]}]}))
    report=nolagin.inspect(tmp_path)['datasets']['train']
    assert report['reviewed']==1 and report['positive']==1 and report['negative']==0
    assert report['excluded']==1 and report['needs_recheck']==1 and report['complete'] is False


def test_empty_roi_cancel_cannot_become_false_negative():
    from scripts.gamecoach_dataset import negative_label_decision
    assert negative_label_decision([],None) is False
    assert negative_label_decision([],"") is False
    assert negative_label_decision([],"escape") is False
    assert negative_label_decision([],"n") is True
    assert negative_label_decision([],"N") is True
    assert negative_label_decision([[10,20,30,60]],None) is True
