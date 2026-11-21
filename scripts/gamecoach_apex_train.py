"""Local Apex ENEMY model training: labeled video -> YOLO -> post-NMS ONNX.

Two-stage, no automatic downloads:
  prepare --train train/dataset.json --val val/dataset.json --out private/apex-yolo
  train --prepared private/apex-yolo --weights local/yolo26n.pt --output data/gaming/

This script does not include pretrained weights or actual match recordings.
You must install the local training dependencies and provide a local checkpoint.
Never train while gaming. Never train on held-out certification clips.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import shutil
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

def _hash_file(path:Path)->str:
    h=sha256()
    with path.open("rb") as src:
        for data in iter(lambda:src.read(1024*1024),b""):h.update(data)
    return h.hexdigest()


def load_local_yolo(weights:Path,settings_dir:Path):
    """Local checkpoint only; disable cloud integrations and automatic downloads."""
    if not weights.is_file() or weights.suffix!='.pt':
        raise FileNotFoundError('Provide installed LOCAL YOLO .pt weights')
    import os
    os.environ['YOLO_OFFLINE']='true'
    os.environ['YOLO_AUTOINSTALL']='False'
    os.environ['WANDB_MODE']='disabled'
    settings_dir.mkdir(parents=True,exist_ok=True)
    os.environ['YOLO_CONFIG_DIR']=str(settings_dir.resolve())
    from ultralytics import YOLO,settings
    disabled={k:False for k in ('sync','hub','wandb','clearml','comet','mlflow','neptune','raytune') if k in settings}
    settings.update(disabled)
    return YOLO(str(weights.resolve()))

def agent_annotation_hash(pixel_sha256:str,boxes:list)->str:
    """Bind independent agreement to both the visible pixels and agreed labels."""
    return sha256(json.dumps([pixel_sha256,boxes],separators=(',',':'),allow_nan=False).encode()).hexdigest()


def _load_catalog(path:Path,split:str,*,allow_agent_reviewed:bool=False):
    data=json.loads(path.read_text(encoding="utf-8"))
    if data.get("game")!="apex" or data.get("split")!=split:
        raise ValueError(f"Expected Apex {split} dataset")
    source=data.get("source_clip_sha256")
    if not isinstance(source,str) or len(source)!=64:
        raise ValueError("Each dataset must have a whole-video clip hash")
    cases=data.get("cases")
    if not isinstance(cases,list) or len(cases)<5 or len(cases)>5000:
        raise ValueError("Expected 5..5000 individually labeled frames")
    def accepted(row):
        if not isinstance(row,dict):return False
        if row.get('human_reviewed') is True:return True
        audit=row.get('agent_annotation_review')
        return (split=='train' and allow_agent_reviewed and isinstance(audit,dict)
                and audit.get('independent_agreement') is True
                and isinstance(audit.get('annotator'),str) and bool(audit['annotator'])
                and isinstance(audit.get('reviewer'),str) and bool(audit['reviewer'])
                and audit['annotator']!=audit['reviewer']
                and isinstance(audit.get('pixel_sha256'),str) and len(audit['pixel_sha256'])==64
                and audit.get('annotation_sha256')==agent_annotation_hash(audit['pixel_sha256'],row.get('boxes')))
    if any(not accepted(r) for r in cases):
        raise ValueError("All training/development frames must be human reviewed")
    if any(r.get('excluded') is True or r.get('needs_recheck') is True for r in cases):
        raise ValueError('Excluded or disputed frames require correction; never train them as negatives')
    return data

def _private_frame(root:Path,filename:str)->Path:
    if (not isinstance(filename,str) or not filename.endswith(".png")
            or filename.startswith("/") or "\\" in filename):
        raise ValueError("Frame must be a local PNG in this labeled group")
    frame=(root/filename).resolve()
    if not frame.is_relative_to(root.resolve()) or not frame.is_file():
        raise ValueError("Frame cannot escape its dataset folder")
    return frame

def prepare_training(train:Path|list[Path],val:Path,out:Path,*,allow_agent_reviewed:bool=False)->dict:
    """Whole clips must be distinct across train and validation groups."""
    from PIL import Image
    train_paths=[train] if isinstance(train,Path) else list(train)
    if not train_paths:raise ValueError('At least one training catalog required')
    training=[(p,_load_catalog(p,'train',allow_agent_reviewed=allow_agent_reviewed)) for p in train_paths]
    b=_load_catalog(val,"val")
    train_sources=[a['source_clip_sha256'] for _,a in training]
    if len(set(train_sources))!=len(train_sources):
        raise ValueError('Repeated training recording; provide each original clip once')
    if b["source_clip_sha256"] in train_sources:
        raise ValueError("Training and validation contain the SAME original clip")
    if out.exists():raise FileExistsError("Choose a fresh private output directory")
    # Validate every frame and its box BEFORE writing converted output.
    pending=[]
    image_hashes={"train":set(),"val":set()}
    indices={'train':0,'val':0}
    for split,catalog,catalog_file in [*(('train',a,p) for p,a in training),('val',b,val)]:
        for row in catalog["cases"]:
            i=indices[split];indices[split]+=1
            src=_private_frame(catalog_file.parent,row.get("image"))
            fingerprint=_hash_file(src)
            if fingerprint in image_hashes[split]:
                raise ValueError("Duplicate frame within one split")
            image_hashes[split].add(fingerprint)
            with Image.open(src) as im:
                width,height=im.size
                if not 16<=width<=7680 or not 16<=height<=4320:
                    raise ValueError("Invalid training image resolution")
                if row.get('human_reviewed') is not True:
                    import numpy as np
                    pixels=np.asarray(im.convert('RGB'))
                    pixel_hash=sha256(str(pixels.shape).encode()+pixels.tobytes()).hexdigest()
                    if pixel_hash!=row['agent_annotation_review']['pixel_sha256']:
                        raise ValueError('Agent annotation pixels changed since independent review')
            boxes=row.get("boxes")
            if not isinstance(boxes,list) or len(boxes)>9:
                raise ValueError("Invalid enemy boxes")
            lines=[]
            seen_boxes=set()
            for box in boxes:
                if not isinstance(box,list) or len(box)!=4 or any(type(v) not in (int,float) for v in box):
                    raise ValueError("Invalid bounding box")
                x1,y1,x2,y2=box
                if not (0<=x1<x2<=width and 0<=y1<y2<=height):
                    raise ValueError("Enemy label outside image")
                if tuple(box) in seen_boxes:raise ValueError('Repeated identical enemy box; review this annotation')
                seen_boxes.add(tuple(box))
                xc=(x1+x2)/(2*width)
                yc=(y1+y2)/(2*height)
                bw=(x2-x1)/width
                bh=(y2-y1)/height
                lines.append(f"0 {xc:.7f} {yc:.7f} {bw:.7f} {bh:.7f}")
            from scripts.gamecoach_label_editor import annotation_issues
            issues=annotation_issues(boxes)
            if issues:raise ValueError(issues[0])
            pending.append((split,i,src,lines))
    if image_hashes["train"] & image_hashes["val"]:
        raise ValueError("Duplicated images across train and validation split")
    out.mkdir(parents=True)
    for split in ("train","val"):
        (out/"images"/split).mkdir(parents=True)
        (out/"labels"/split).mkdir(parents=True)
    for split,i,src,labels in pending:
        dest=f"frame_{i:05}.png"
        shutil.copyfile(src,out/"images"/split/dest)
        (out/"labels"/split/f"frame_{i:05}.txt").write_text(
            "\n".join(labels)+("\n" if labels else ""),encoding="utf-8")
    yaml=(f"path: '{out.resolve().as_posix()}'\n"
          "train: images/train\nval: images/val\nnames:\n  0: enemy\n")
    (out/"apex.yaml").write_text(yaml,encoding="utf-8")
    summary={"game":"apex","prepared":True,
             "train_clip_sha256":train_sources[0] if len(train_sources)==1 else None,
             "train_clip_sha256s":train_sources,
             "val_clip_sha256":b["source_clip_sha256"],
             "train_frames":indices['train'],"val_frames":len(b["cases"]),
             "source_clips_distinct":True,"class":"enemy only",
             "split_policy":"whole clip separation; heldout images NEVER used for training",
             "pretrained_checkpoint_provided":False}
    summary['agent_reviewed_training_frames']=sum(r.get('human_reviewed') is not True for _,a in training for r in a['cases'])
    summary['annotation_policy']='explicit independent agent agreement for training only' if allow_agent_reviewed else 'human reviewed only'
    (out/"dataset-summary.json").write_text(json.dumps(summary,indent=2),encoding="utf-8")
    return summary

def verify_export(model_path:Path,input_size:int=320)->None:
    """Check structure/ranges/NMS, never attest semantics from a blank image."""
    import onnxruntime as ort
    import numpy as np
    opts=ort.SessionOptions()
    opts.intra_op_num_threads=1
    session=ort.InferenceSession(str(model_path),sess_options=opts,
                                 providers=["CPUExecutionProvider"])
    inputs=session.get_inputs()
    if (len(inputs)!=1 or inputs[0].shape!=[1,3,input_size,input_size]
            or inputs[0].type!="tensor(float)"):
        raise ValueError(f"ONNX NCHW input mismatch; expected [1,3,{input_size},{input_size}]")
    outputs=session.run(None,{inputs[0].name:np.zeros((1,3,input_size,input_size),
                                                      dtype=np.float32)})
    if len(outputs)!=1:
        raise ValueError("ONNX expected exactly one detection output")
    rows=np.asarray(outputs[0])
    if rows.ndim!=3 or rows.shape[0]!=1 or rows.shape[2]!=6:
        raise ValueError("ONNX needs [1,N,6] xyxy/score/class. Wrong YOLO export head.")
    if not np.isfinite(rows).all():
        raise ValueError("ONNX exported nonfinite predictions")
    from core.gamecoach_enemy_detector import detection_rows
    from scripts.gamecoach_vision_validate import overlap
    active=detection_rows(rows,input_size,class_id=0)
    # Confidence filtering matches the candidate manifest. Padding and low
    # scores are allowed, but duplicate live detections violate post-NMS.
    active=active[active[:,4]>=.92]
    for i,row in enumerate(active):
        if any(overlap(row,previous)>=.5 for previous in active[:i]):
            raise ValueError("ONNX output contains repeated post-NMS detections")

def export_trained_yolo(model,input_size:int=320)->Path:
    """Preserve the checkpoint's evaluated head; nms=False can switch YOLO26 heads."""
    if model.names!={0:'enemy'}:raise ValueError('Only the trained enemy class can be exported')
    checkpoint=getattr(model,'ckpt_path',None)
    if checkpoint and Path(checkpoint).with_suffix('.onnx').exists():
        raise FileExistsError('Preserve existing raw export; copy checkpoint to a fresh directory before re-export')
    end2end=bool(getattr(model.model,'end2end',False))
    options=dict(format='onnx',imgsz=input_size,batch=1,dynamic=False,nms=not end2end)
    if not end2end:options.update(conf=.001,iou=.499,max_det=300)
    return Path(model.export(**options))


def finalize_export(exported:Path,output:Path,originals:list,input_size:int=320,
                    expected_end2end:bool|None=None,annotation_summary:dict|None=None)->dict:
    """Finish a real trained export without repeating an already completed fit."""
    import onnx,ast
    from scripts.gamecoach_onnx_export import adapt_export
    if input_size not in {320,416,512,640}:raise ValueError('Unsupported training input size')
    if len(originals)<2 or len(set(originals))!=len(originals) or not all(isinstance(x,str) and len(x)==64 for x in originals):
        raise ValueError('Distinct training/development video fingerprints required')
    final=output/'apex-enemy.onnx';path=output/'vision-apex.json'
    if final.exists() or path.exists():raise FileExistsError('Preserve existing model/manifest')
    metadata={p.key:p.value for p in onnx.load(str(exported)).metadata_props}
    if ast.literal_eval(metadata.get('names','{}'))!={0:'enemy'}:
        raise ValueError('Only a trained enemy-class export may become a candidate')
    actual_end2end=ast.literal_eval(metadata.get('end2end','None'))
    if expected_end2end is not None and actual_end2end is not expected_end2end:
        raise ValueError('Export changed checkpoint detection head; refuse candidate')
    adapted=output/'postprocessed-export.onnx'
    adapt_export(exported,adapted,input_size)
    verify_export(adapted,input_size)
    shutil.copyfile(adapted,final)
    manifest={'game':'apex','model':final.name,'sha256':_hash_file(final),
              'contract':'xyxy-nms-v1','classes':{'enemy':0},'input_size':input_size,
              'min_confidence':.92,'reviewed':False,'validation_cases':0,
              'measured_precision':0.,'measured_recall':0.,
              'training_source_clip_sha256':originals,
              'training_clip_sha256s':originals[:-1],
              'development_validation_clip_sha256':originals[-1],
              'preprocessing':'opencv-linear','export_end2end':actual_end2end,
              'postprocessing':'pixel clipping; degenerate/nonfinite suppression; NMS .499; score floor .85'}
    if annotation_summary is not None:
        manifest['training_annotation_policy']=annotation_summary.get('annotation_policy','human reviewed only')
        manifest['agent_reviewed_training_frames']=annotation_summary.get('agent_reviewed_training_frames',0)
    path.write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    return {'model':str(final),'manifest':str(path),'status':'CANDIDATE ONLY; needs human-reviewed held-out certification'}


def train_and_export(prepared:Path,weights:Path,output:Path,
                     epochs:int=30,device:str="0",input_size:int=320)->dict:
    if not (prepared/"apex.yaml").is_file() or not (prepared/"dataset-summary.json").is_file():
        raise ValueError("Run prepare with reviewed, separated clips first")
    prepared_summary=json.loads((prepared/"dataset-summary.json").read_text(encoding="utf-8"))
    if prepared_summary.get('quality_audit_status')=='rejected':
        raise ValueError('Prepared labels rejected by audit; preserve this run and prepare a corrected dataset in a new directory')
    train_sources=prepared_summary.get('train_clip_sha256s',[prepared_summary.get('train_clip_sha256')])
    if not isinstance(train_sources,list) or not train_sources:
        raise ValueError('Missing training source fingerprint list')
    originals=[*train_sources,prepared_summary.get("val_clip_sha256")]
    if len(originals)<2 or len({v for v in originals if isinstance(v,str) and len(v)==64})!=len(originals):
        raise ValueError("Missing distinct whole-source clip fingerprints")
    if not weights.is_file() or weights.suffix!=".pt":
        raise FileNotFoundError("Provide installed LOCAL YOLO pretrained .pt weights; no downloading")
    if not 1<=epochs<=300 or device not in {"0","cpu"} or input_size not in {320,416,512,640}:
        raise ValueError("Unsupported training settings")
    if (output/"apex-enemy.onnx").exists() or (output/"vision-apex.json").exists():
        raise FileExistsError("Candidate model or manifest exists; preserve previous training result")
    model=load_local_yolo(weights,output/'local-yolo-settings')
    # Trains only on private user's labelled frames, not YouTube downloads.
    model.train(data=str(prepared/"apex.yaml"),epochs=epochs,imgsz=input_size,
                device=device,project=str(prepared/"runs"),name="apex-enemy",
                batch=8,workers=0,plots=False,amp=False,seed=0,deterministic=True)
    best=Path(model.trainer.best)
    if not best.is_file():raise RuntimeError("Model training did not produce best.pt")
    model=load_local_yolo(best,output/'local-yolo-settings')
    if model.names!={0:"enemy"}:
        raise ValueError("Trained model must expose only the reviewed enemy class")
    expected_end2end=bool(getattr(model.model,'end2end',False))
    exported=export_trained_yolo(model,input_size)
    return finalize_export(exported,output,originals,input_size,expected_end2end,prepared_summary)

def main()->None:
    ap=argparse.ArgumentParser()
    commands=ap.add_subparsers(dest="command",required=True)
    prep=commands.add_parser("prepare")
    prep.add_argument("--train",type=Path,required=True,action='append',
                      help='Repeat for additional whole-recording training catalogs')
    prep.add_argument("--val",type=Path,required=True)
    prep.add_argument("--out",type=Path,required=True)
    prep.add_argument('--allow-agent-reviewed-training',action='store_true',
                      help='Training only: explicit independent agent agreement, never development/certification')
    train=commands.add_parser("train")
    train.add_argument("--prepared",type=Path,required=True)
    train.add_argument("--weights",type=Path,required=True)
    train.add_argument("--output",type=Path,required=True)
    train.add_argument("--epochs",type=int,default=30)
    train.add_argument("--device",choices=["0","cpu"],default="0")
    train.add_argument('--input-size',type=int,choices=[320,416,512,640],default=320)
    args=ap.parse_args()
    result=(prepare_training(args.train,args.val,args.out,allow_agent_reviewed=args.allow_agent_reviewed_training) if args.command=="prepare"
            else train_and_export(args.prepared,args.weights,args.output,
                                  args.epochs,args.device,args.input_size))
    print(json.dumps(result,indent=2))

if __name__=="__main__":main()
