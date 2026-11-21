"""Nolagin Windows readiness + one-command private dataset bootstrap.

No cloud service, automatic downloads, recordings uploaded, game inputs,
forced package installations, or fake validation passes.

Run:
    python scripts/nolagin.py doctor --root data/gaming
    python scripts/nolagin.py extract --train-clip own1.mp4 --val-clip own2.mp4 \
      --heldout-clip own3.mp4 --root data/gaming
    python scripts/nolagin.py label --root data/gaming --split train
    python scripts/nolagin.py train --root data/gaming --weights C:/models/yolo26n.pt
    python scripts/nolagin.py certify --root data/gaming

Labeling is intentionally an interactive human step: no trained model can
provide trusted enemy/team annotations merely from the video filename.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
from importlib.util import find_spec
import json
import os
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

GAMES={"apex","cod"}
SPLITS=("train","val","heldout")
MODULES=("numpy","PIL","av","cv2","onnxruntime","ultralytics","psutil","dxcam","pyttsx3")

def _file_hash(path:Path)->str:
    h=sha256()
    with path.open("rb") as stream:
        for block in iter(lambda:stream.read(1024*1024),b""):h.update(block)
    return h.hexdigest()

def _model_status(path:Path,game:str)->dict:
    if not path.is_file():
        return {"ready":False,"reason":"No candidate model manifest"}
    try:
        from core.gamecoach_enemy_detector import load_config
        cfg=load_config(path,game)
        return {"ready":True,"precision":cfg.precision,"recall":cfg.recall,
                "validation_cases":cfg.validation_cases}
    except (ValueError,OSError,ImportError,TypeError,KeyError) as exc:
        return {"ready":False,"reason":str(exc)[:170]}

def _dataset_status(path:Path,split:str,game:str)->dict:
    if not path.is_file():
        return {"exists":False,"next":"Extract a separate local recording"}
    try:
        data=json.loads(path.read_text(encoding="utf-8"))
        if data.get("game")!=game or data.get("split")!=split:
            raise ValueError("Wrong game or data split")
        cases=data.get("cases",[])
        if not isinstance(cases,list):raise ValueError("Invalid frame catalog")
        excluded=sum(isinstance(x,dict) and x.get('excluded') is True for x in cases)
        disputed=sum(isinstance(x,dict) and x.get('needs_recheck') is True and x.get('excluded') is not True for x in cases)
        reviewed=[x for x in cases if isinstance(x,dict) and x.get("human_reviewed") is True
                  and x.get('excluded') is not True and x.get('needs_recheck') is not True]
        positive=sum(bool(x.get("boxes")) for x in reviewed)
        return {"exists":True,"frames":len(cases),"reviewed":len(reviewed),
                "positive":positive,"negative":len(reviewed)-positive,
                "excluded":excluded,"needs_recheck":disputed,
                "complete":bool(reviewed) and len(reviewed)+excluded==len(cases),
                "source_clip_sha256":data.get("source_clip_sha256")}
    except (OSError,ValueError,TypeError) as exc:
        return {"exists":True,"error":str(exc)[:160]}

def inspect(root:Path,game:str="apex",platform:str|None=None)->dict:
    """Diagnostic only, no imports of costly ML libraries."""
    if game not in GAMES:raise ValueError("Unknown game")
    os_name=sys.platform if platform is None else platform
    modules={name:find_spec(name) is not None for name in MODULES}
    status={
        "game":game,
        "windows_gaming_pc":os_name=="win32",
        "optional_modules":modules,
        "datasets":{s:_dataset_status(root/"nolagin-datasets"/s/"dataset.json",s,game)
                    for s in SPLITS},
        "calibrated_bars":sorted(p.name for p in root.glob(f"coach-{game}-*.json")),
        "model":_model_status(root/f"vision-{game}.json",game),
        "frameview_off_on_off_real_benchmarks_present":all(
            (root/file).is_file() for file in
            ("frameview-off-a.json","frameview-on.json","frameview-off-b.json")),
        "real_live_game_measurements_verified":False,
    }
    hints=[]
    if not modules["numpy"] or not modules["PIL"]:
        hints.append("Install local NumPy and Pillow before processing game frames")
    if not modules["av"]:
        hints.append("Install local PyAV before extracting recorded game footage")
    if not modules["cv2"]:
        hints.append("Install OpenCV for interactive human enemy-box labeling")
    if not modules["ultralytics"] or not modules["onnxruntime"]:
        hints.append("Install locally approved Ultralytics and ONNX Runtime packages before offline training")
    if not modules["dxcam"] and os_name=="win32":
        hints.append("DXcam optional accelerated Windows capture missing")
    datasets=status["datasets"]
    if any(not d.get("exists") for d in datasets.values()):
        hints.append("Supply three DIFFERENT local recorded Apex sessions: train, val and held-out")
    elif any(not d.get("complete") for d in datasets.values()):
        hints.append("Finish human review of every saved frame; label enemies, not teammates")
    if not status["model"]["ready"]:
        hints.append("Run offline local model training and real held-out certification")
    if not status["frameview_off_on_off_real_benchmarks_present"]:
        hints.append("Collect FrameView OFF/ON/OFF on real Windows gameplay and audible-onset measurements")
    status["next_steps"]=hints[:8]
    return status

def ensure_distinct_clips(clips:dict[str,Path])->dict[str,str]:
    if set(clips)!=set(SPLITS):raise ValueError("Three distinct split clips required")
    digests={}
    for split,clip in clips.items():
        if not clip.is_file() or clip.stat().st_size==0:
            raise FileNotFoundError(f"{split}: missing nonempty local recording")
        digests[split]=_file_hash(clip)
    if len(set(digests.values()))!=len(SPLITS):
        raise ValueError("Training, validation, held-out recordings must be DIFFERENT")
    return digests

def extract_three(game:str,clips:dict[str,Path],root:Path,interval:float=1.,
                  max_frames:int=400)->dict:
    ensure_distinct_clips(clips)
    from scripts.gamecoach_dataset import extract
    target=root/"nolagin-datasets"
    if any((target/s/"dataset.json").exists() for s in SPLITS):
        raise FileExistsError("One or more output catalogs already exist. Preserve them.")
    generated={}
    for s in SPLITS:
        generated[s]=str(extract(game,clips[s],target/s,interval=interval,
                                 max_frames=max_frames,split=s))
    return {"data":generated,"status":"Extracted; human labels required before training"}

def _game_running(game:str)->bool:
    try:
        import psutil
        names={"apex":{"r5apex.exe","r5apex_dx12.exe"},
               "cod":{"cod.exe","codhq.exe"}}[game]
        return any(str(x.info.get("name") or "").lower() in names
                   for x in psutil.process_iter(["name"]))
    except (ImportError,OSError):
        # On Windows, do not silently assume a game is closed when process
        # status cannot be determined.
        if sys.platform=="win32":
            raise RuntimeError("Install psutil / verify game is closed before GPU training")
        return False

def train(game:str,root:Path,weights:Path,epochs:int,device:str)->dict:
    if game!="apex":
        raise ValueError("Only Apex YOLO training implemented; COD needs separate reviewed classes")
    if _game_running(game):
        raise RuntimeError("Close the game before model training; protect your FPS")
    from scripts.gamecoach_apex_train import prepare_training,train_and_export
    base=root/"nolagin-datasets"
    prepared=root/"nolagin-prepared"
    if not prepared.exists():
        prepare_training(base/"train"/"dataset.json",
                         base/"val"/"dataset.json",prepared)
    return train_and_export(prepared,weights,root,epochs,device)

def certify(game:str,root:Path)->dict:
    from core.gamecoach_enemy_detector import load_config,GameEnemyDetector
    from scripts.gamecoach_vision_validate import verify_heldout_isolation,evaluate
    manifest=root/f"vision-{game}.json"
    heldout=root/"nolagin-datasets"/"heldout"/"dataset.json"
    raw=json.loads(heldout.read_text(encoding="utf-8"))
    meta=json.loads(manifest.read_text(encoding="utf-8"))
    verify_heldout_isolation(meta,raw)
    model=GameEnemyDetector(load_config(manifest,game,allow_candidate=True))
    result=evaluate(model,raw,heldout.parent)
    # Always write actual measured evaluation, including failures.
    report=root/f"nolagin-{game}-certification.json"
    temporary=report.with_suffix(".tmp")
    temporary.write_text(json.dumps(result,indent=2),encoding="utf-8")
    temporary.replace(report)
    # A new failed measurement must revoke an earlier live approval.
    meta.update({"reviewed":result["passed"],"validation_cases":result["cases"],
                 "measured_precision":result["precision"],
                 "measured_recall":result["recall"],
                 "validation_dataset_sha256":result["validation_dataset_sha256"]})
    candidate=manifest.with_suffix(".tmp")
    candidate.write_text(json.dumps(meta,indent=2),encoding="utf-8")
    candidate.replace(manifest)
    return {"report":str(report),**result}

def _run_command(args)->dict:
    if args.command=="doctor":
        return inspect(args.root,args.game)
    if args.command=="extract":
        clips={s:getattr(args,s+"_clip") for s in SPLITS}
        return extract_three(args.game,clips,args.root,args.interval,args.max_frames)
    if args.command=="label":
        from scripts.gamecoach_dataset import label
        return label(args.root/"nolagin-datasets"/args.split/"dataset.json")
    if args.command=="propose":
        if _game_running(args.game):raise RuntimeError('Close the game before local proposal inference')
        from scripts.gamecoach_dataset import propose
        return propose(args.root/'nolagin-datasets'/args.split/'dataset.json',args.weights,device=args.device)
    if args.command=="train":
        return train(args.game,args.root,args.weights,args.epochs,args.device)
    return certify(args.game,args.root)


def main(argv=None)->int:
    p=argparse.ArgumentParser(description="Nolagin local-first game coach setup")
    p.add_argument("--root",type=Path,default=Path(__file__).resolve().parents[1]/"data"/"gaming")
    p.add_argument("--game",choices=sorted(GAMES),default="apex")
    sub=p.add_subparsers(dest="command",required=True)
    sub.add_parser("doctor")
    ext=sub.add_parser("extract")
    ext.add_argument("--train-clip",type=Path,required=True)
    ext.add_argument("--val-clip",type=Path,required=True)
    ext.add_argument("--heldout-clip",type=Path,required=True)
    ext.add_argument("--interval",type=float,default=1.)
    ext.add_argument("--max-frames",type=int,default=400,
                     help="Limit human-labeling workload; heldout still needs >=200 frames")
    label=sub.add_parser("label")
    label.add_argument("--split",choices=SPLITS,required=True)
    draft=sub.add_parser('propose',help='Unreviewed local person proposals; never heldout labels')
    draft.add_argument('--split',choices=('train','val'),required=True)
    draft.add_argument('--weights',type=Path,required=True)
    draft.add_argument('--device',choices=('0','cpu'),default='0')
    fitted=sub.add_parser("train")
    fitted.add_argument("--weights",type=Path,required=True)
    fitted.add_argument("--epochs",type=int,default=30)
    fitted.add_argument("--device",choices=("0","cpu"),default="0")
    sub.add_parser("certify")
    # Accept documented options both before and after the command. Suppressed
    # subparser defaults preserve values supplied to the parent parser.
    for command in sub.choices.values():
        command.add_argument("--root",type=Path,default=argparse.SUPPRESS)
        command.add_argument("--game",choices=sorted(GAMES),default=argparse.SUPPRESS)
    args=p.parse_args(argv)
    try:
        result=_run_command(args)
    except (OSError,ValueError,RuntimeError,ImportError,KeyError,TypeError) as exc:
        print(json.dumps({"passed":False,"command":args.command,
                          "error":str(exc),"error_type":type(exc).__name__},indent=2))
        return 2
    print(json.dumps(result,indent=2))
    return 1 if result.get("passed") is False else 0

if __name__=="__main__":
    raise SystemExit(main())
