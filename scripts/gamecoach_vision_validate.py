"""Evaluate a CANDIDATE local enemy model on real manually-labeled HELD-OUT clips.

A general person detector, invented precision, or synthetic-only replay cannot
pass the live gate. This script reads only locally saved labeled RGB frames and
box annotations; no downloading, cloud calls, image uploads, game hooks, or GPU.

Dataset JSON:
{"split":"heldout","game":"apex","cases":[
  {"image":"frames/001.png","boxes":[[10,20,150,280]],"human_reviewed":true},
  {"image":"frames/002.png","boxes":[],"human_reviewed":true}
]}
Requires >=200 unique frames, >=80 positive frames, >=80 negative frames.
"""
from __future__ import annotations
import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys
from time import monotonic
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from core.gamecoach_enemy_detector import GameEnemyDetector,load_config

def overlap(a,b)->float:
    x1,y1,x2,y2=a[:4]
    p1,q1,p2,q2=b[:4]
    inter=max(0,min(x2,p2)-max(x1,p1))*max(0,min(y2,q2)-max(y1,q1))
    union=(x2-x1)*(y2-y1)+(p2-p1)*(q2-q1)-inter
    return inter/union if union>0 else 0.

def match_frame(predicted:list,truth:list)->tuple[int,int,int]:
    # Score-sorted one-to-one matching: repeated predictions cannot win twice.
    remaining=set(range(len(truth)))
    tp=0
    for result in sorted(predicted,key=lambda b:b[4],reverse=True):
        choices=[(overlap(result,truth[i]),i) for i in remaining]
        if not choices:continue
        score,index=max(choices)
        if score>=.5:
            remaining.remove(index)
            tp+=1
    return tp,len(predicted)-tp,len(remaining)

def _local_frame(root:Path,filename:str)->Path:
    if (not isinstance(filename,str) or not filename.lower().endswith((".png",".jpg",".jpeg"))
            or filename.startswith("/") or "\\" in filename):
        raise ValueError("Only local labeled image paths are accepted")
    path=(root/filename).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("Image cannot escape the private dataset folder")
    if not path.is_file():raise FileNotFoundError(filename)
    return path

def verify_heldout_isolation(manifest:dict,catalog:dict)->None:
    """Reject same original recording appearing in both training and held-out.

    This is automatically enforced for manifests created by the v7 trainer.
    Older third-party manifests require separate independent human validation.
    """
    sources=manifest.get("training_source_clip_sha256")
    if sources is None:
        return
    if not isinstance(sources,list) or len(sources)<2 or not all(
            isinstance(x,str) and len(x)==64 for x in sources):
        raise ValueError("Invalid training video provenance")
    if len(set(sources))!=len(sources):raise ValueError('Invalid repeated source provenance')
    if len(sources)>2 or 'training_clip_sha256s' in manifest:
        training=manifest.get('training_clip_sha256s')
        validation=manifest.get('development_validation_clip_sha256')
        if not isinstance(training,list) or not training or [*training,validation]!=sources:
            raise ValueError('Invalid training/validation source roles')
    heldout=catalog.get("source_clip_sha256")
    if not isinstance(heldout,str) or len(heldout)!=64:
        raise ValueError("Held-out dataset lacks original-video checksum")
    if heldout in sources:
        raise ValueError("Held-out video overlaps training or model-selection video")

def evaluate(detector:GameEnemyDetector,catalog:dict,root:Path)->dict:
    if catalog.get("game")!=detector.config.game or catalog.get("split")!="heldout":
        raise ValueError("Only game-matched held-out test frames qualify")
    cases=catalog.get("cases")
    if not isinstance(cases,list) or len(cases)<200 or len(cases)>5000:
        raise ValueError("Need 200..5000 reviewed held-out frames")
    if any(isinstance(row,dict) and (row.get('excluded') is True or row.get('needs_recheck') is True)
           for row in cases):
        raise ValueError('Excluded or disputed held-out frames cannot certify a model')
    seen=set()
    seen_pixels=set()
    tp=fp=fn=0
    positive=negative=negative_false=0
    digest=sha256()
    digest.update(json.dumps(catalog,sort_keys=True,separators=(",",":")).encode("utf-8"))
    from PIL import Image
    for row in cases:
        if not isinstance(row,dict) or row.get("human_reviewed") is not True:
            raise ValueError("Each held-out frame requires a human-review flag")
        name=row.get("image")
        path=_local_frame(root,name)
        if path in seen:raise ValueError("Duplicate held-out frame")
        seen.add(path)
        payload=path.read_bytes()
        digest.update(name.encode("utf-8")+sha256(payload).digest())
        # No large unbounded image or decompression-bomb inputs.
        if len(payload)>12_000_000:
            raise ValueError("Image exceeds local validation memory budget")
        with Image.open(path) as image:
            if image.width>7680 or image.height>4320:
                raise ValueError("Unbounded image dimensions")
            frame=np.asarray(image.convert("RGB"))
        h,w=frame.shape[:2]
        fingerprint=sha256(str(frame.shape).encode()+frame.tobytes()).digest()
        if fingerprint in seen_pixels:
            raise ValueError("Duplicate held-out frame pixels under different filenames")
        seen_pixels.add(fingerprint)
        boxes=row.get("boxes")
        if not isinstance(boxes,list) or len(boxes)>9:
            raise ValueError("Expected <=9 visible, manually boxed enemy regions")
        for box in boxes:
            if (not isinstance(box,list) or len(box)!=4 or
                    any(type(x) not in (int,float) or not np.isfinite(x) for x in box) or
                    not 0<=box[0]<box[2]<=w or not 0<=box[1]<box[3]<=h):
                raise ValueError("Incorrect enemy annotation coordinates")
        if boxes:positive+=1
        else:negative+=1
        detections=detector.predict_boxes(frame,monotonic())
        if detections is None:
            raise RuntimeError("Candidate vision model failed or produced invalid inference")
        a,b,c=match_frame(detections,boxes)
        tp+=a;fp+=b;fn+=c
        if not boxes and detections:negative_false+=1
    if positive<80 or negative<80:
        raise ValueError("Test needs at least 80 positive and 80 hard-negative frames")
    precision=tp/(tp+fp) if tp+fp else 0.
    recall=tp/(tp+fn) if tp+fn else 0.
    fpr=negative_false/negative
    passed=(precision>=.98 and recall>=.85 and fpr<=.01)
    return {"passed":passed,"game":detector.config.game,"cases":len(cases),
            "positive_frames":positive,"negative_frames":negative,
            "true_positives":tp,"false_positives":fp,"false_negatives":fn,
            "negative_false_positive_frames":negative_false,
            "precision":round(precision,6),"recall":round(recall,6),
            "negative_frame_fpr":round(fpr,6),
            "validation_dataset_sha256":digest.hexdigest(),
            "measurement":"real human-annotated held-out local RGB frames",
            "real_game_fps_measured":False}

def main()->int:
    ap=argparse.ArgumentParser()
    ap.add_argument("--manifest",type=Path,required=True)
    ap.add_argument("--dataset",type=Path,required=True)
    ap.add_argument("--report",type=Path)
    ap.add_argument("--certify",action="store_true",help="Write measured stats to candidate manifest after successful test")
    args=ap.parse_args()
    try:
        raw=json.loads(args.dataset.read_text(encoding="utf-8"))
        verify_heldout_isolation(json.loads(args.manifest.read_text(encoding="utf-8")),raw)
        model=GameEnemyDetector(load_config(args.manifest,raw["game"],allow_candidate=True))
        result=evaluate(model,raw,args.dataset.parent)
        print(json.dumps(result,indent=2))
        if args.report:
            args.report.parent.mkdir(parents=True,exist_ok=True)
            args.report.write_text(json.dumps(result,indent=2),encoding="utf-8")
        if args.certify:
            meta=json.loads(args.manifest.read_text(encoding="utf-8"))
            meta.update({"reviewed":result["passed"],"validation_cases":result["cases"],
                         "measured_precision":result["precision"],
                         "measured_recall":result["recall"],
                         "validation_dataset_sha256":result["validation_dataset_sha256"]})
            args.manifest.write_text(json.dumps(meta,indent=2),encoding="utf-8")
        return 0 if result["passed"] else 1
    except (OSError,ValueError,KeyError,RuntimeError) as exc:
        print(f"Not certified: {type(exc).__name__}: {exc}")
        return 2

if __name__=="__main__":
    raise SystemExit(main())
