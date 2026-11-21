"""Local offline game-video frames -> human-reviewed labeled heldout dataset.

No cloud, networking, PC automation or game process access. Requires a LOCAL
saved clip, PyAV and OpenCV (only for human annotation UI).

Extract:
  python scripts/gamecoach_dataset.py extract --game apex --clip local_clip.mp4 --out data/gaming/heldout/ --interval 1.0
Label:
  python scripts/gamecoach_dataset.py label --dataset data/gaming/heldout/dataset.json

Labeling: manually select ALL visible enemy boxes; empty means ZERO visible
enemy boxes in this captured frame only. Do not label teammates as enemies.
All clips in the heldout set must be separate from model training footage.
"""
from __future__ import annotations
import argparse,json,sys
from hashlib import sha256
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def _save_catalog(path:Path,data:dict)->None:
    temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data,indent=2),encoding='utf-8')
    from core.fileio import replace_atomic
    replace_atomic(temporary,path)


def propose(path:Path,weights:Path,*,device:str='0',model=None)->dict:
    """Draft COCO person regions, NEVER enemy truth or held-out annotations."""
    data=json.loads(path.read_text(encoding='utf-8'))
    if data.get('game')!='apex' or data.get('split') not in {'train','val'}:
        raise ValueError('Person proposals are allowed only for Apex train/val, never heldout')
    if device not in {'0','cpu'}:raise ValueError('Unsupported local device')
    if not weights.is_file() or weights.suffix!='.pt':raise FileNotFoundError('Local .pt checkpoint required')
    if model is None:
        from scripts.gamecoach_apex_train import load_local_yolo
        model=load_local_yolo(weights,path.parent.parent.parent/'local-yolo-settings')
    if model.names.get(0)!='person':raise ValueError('Proposals require the explicit COCO person class')
    digest=sha256(weights.read_bytes()).hexdigest()
    source_version=sha256(path.read_bytes()).hexdigest()
    count=regions=0
    from PIL import Image
    for row in data['cases']:
        if row.get('human_reviewed') is True:continue
        name=row.get('image')
        if not isinstance(name,str) or Path(name).name!=name or not name.endswith('.png'):
            raise ValueError('Proposal frame must be an adjacent PNG')
        image=path.parent/name
        with Image.open(image) as pic:width,height=pic.size
        prediction=model.predict(source=str(image),classes=[0],conf=.15,imgsz=640,
                                 device=device,max_det=9,verbose=False)[0]
        draft=[]
        for box in prediction.boxes.xyxy.cpu().tolist():
            x1,y1,x2,y2=[int(round(v)) for v in box]
            if 0<=x1<x2<=width and 0<=y1<y2<=height:draft.append([x1,y1,x2,y2])
        row['candidate_boxes']=draft
        row['candidate_provenance']={'model_sha256':digest,'class':'person, not enemy',
                                     'status':'unreviewed; may include teammates or miss opponents'}
        # Preserve the authoritative human fields exactly, including negatives.
        count+=1;regions+=len(draft)
        if sha256(path.read_bytes()).hexdigest()!=source_version:
            raise RuntimeError('Dataset changed during proposals; preserving the other reviewer edits')
        _save_catalog(path,data)
        source_version=sha256(path.read_bytes()).hexdigest()
    return {'frames_proposed':count,'candidate_regions':regions,'human_labels_created':0,
            'status':'Person proposals only; human must remove teammates and add every missed enemy'}


def reviewed_proposal(boxes:list,key:str)->list|None:
    """Only explicit N confirms a negative; positive drafts need individual selection."""
    if key.lower()=='n':return []
    # A opens the editor now; it cannot batch-promote generic people to enemies.
    return None


def unique_boxes(boxes:list)->list:
    result=[]
    for box in boxes:
        if list(box) not in result:result.append(list(box))
    return result


def crop_proposals(frame,region,weights:Path,*,split:str,device='0',model=None)->list:
    """Zoomed draft people, for development splits only; never human truth."""
    if split not in {'train','val'}:raise ValueError('No model suggestions on heldout')
    if model is None:
        from scripts.nolagin import _game_running
        if _game_running('apex'):raise RuntimeError('Close Apex before local suggestions')
        from scripts.gamecoach_apex_train import load_local_yolo
        model=load_local_yolo(weights,weights.parent/'local-settings')
    if model.names.get(0)!='person':raise ValueError('Explicit person-class checkpoint required')
    from PIL import Image
    x1,y1,x2,y2=map(int,region)
    if not 0<=x1<x2<=frame.shape[1] or not 0<=y1<y2<=frame.shape[0]:raise ValueError('Invalid zoom region')
    prediction=model.predict(source=Image.fromarray(frame[y1:y2,x1:x2]),classes=[0],conf=.10,
                             imgsz=640,device=device,max_det=9,verbose=False)[0]
    boxes=[]
    for a,b,c,d in prediction.boxes.xyxy.cpu().tolist():
        box=[max(x1,round(a+x1)),max(y1,round(b+y1)),min(x2,round(c+x1)),min(y2,round(d+y1))]
        if box[0]<box[2] and box[1]<box[3]:boxes.append(box)
    return unique_boxes(boxes)


def edit_enemy_boxes(frame,initial,*,suggest=None,initial_view=None):
    from scripts.gamecoach_label_editor import edit_boxes
    return edit_boxes(frame,initial,suggest=suggest,initial_view=initial_view)

def extract(game:str,clip:Path,out:Path,interval:float=1.,max_frames:int=1000,
            split:str='heldout')->Path:
    if (game not in {"apex","cod"} or split not in {"train","val","heldout"}
        or not .25<=interval<=10 or not 1<=max_frames<=5000):
        raise ValueError("Unsupported game or frame sampling")
    if not clip.is_file():raise FileNotFoundError(clip)
    import av
    from PIL import Image
    out.mkdir(parents=True,exist_ok=True)
    existing=out/"dataset.json"
    if existing.exists():
        raise FileExistsError("Use an empty private output folder, not an existing labeled dataset")
    # Whole-clip fingerprint blocks training/holdout leakage through identical videos.
    digest=sha256()
    with clip.open("rb") as stream:
        for block in iter(lambda:stream.read(1024*1024),b""):
            digest.update(block)
    cases=[]
    with av.open(str(clip)) as video:
        if not video.streams.video:raise ValueError("No video stream")
        stream=video.streams.video[0]
        last=-float("inf")
        for frame in video.decode(stream):
            t=float(frame.pts*frame.time_base) if frame.pts is not None else 0.
            if t-last<interval:continue
            last=t
            pic=frame.to_image().convert("RGB")
            if pic.width>1920 or pic.height>1080:
                pic.thumbnail((1920,1080))
            filename=f"frame_{len(cases):05d}.png"
            pic.save(out/filename)
            cases.append({"image":filename,"boxes":[],"human_reviewed":False,
                          "clip_time_s":round(t,3)})
            if len(cases)>=max_frames:break
    if not cases:raise ValueError("Could not decode sampled frames")
    path=out/"dataset.json"
    path.write_text(json.dumps({"game":game,"split":split,
                                "source_clip_sha256":digest.hexdigest(),
                                "source_note":"keep entire original clips isolated across train/val/heldout",
                                "cases":cases},indent=2),encoding="utf-8")
    return path

def negative_label_decision(boxes:list,answer:str|None)->bool:
    """Empty ROI selection/cancel can NEVER silently certify a negative frame."""
    if boxes:return True
    return isinstance(answer,str) and answer.strip().lower()=="n"

def label(path:Path)->dict:
    from PIL import Image
    import cv2
    import numpy as np
    data=json.loads(path.read_text(encoding="utf-8"))
    if data.get("game") not in ("apex","cod") or not isinstance(data.get("cases"),list):
        raise ValueError("Bad dataset")
    reviewed=0
    proposal_model=[]
    try:
        for row in data["cases"]:
            if row.get('excluded') is True:continue
            if row.get("human_reviewed") is True and row.get('needs_recheck') is not True:
                reviewed+=1
                continue
            name=row.get("image")
            if (not isinstance(name,str) or not name.endswith(".png")
                    or "/" in name or "\\" in name or ".." in name):
                raise ValueError("Dataset image must be an adjacent PNG")
            frame=np.asarray(Image.open(path.parent/name).convert("RGB"))
            candidates=row.get('candidate_boxes',[]) if data.get('split')!='heldout' else []
            title='Nolagin human review (LOCAL ONLY)'
            cv2.namedWindow(title,cv2.WINDOW_NORMAL)
            cv2.resizeWindow(title,1280,760)
            converted=None
            quit_review=False
            while converted is None:
                preview=cv2.cvtColor(frame,cv2.COLOR_RGB2BGR)
                for x1,y1,x2,y2 in candidates:cv2.rectangle(preview,(x1,y1),(x2,y2),(0,180,255),2)
                preview=cv2.copyMakeBorder(preview,0,100,0,0,cv2.BORDER_CONSTANT,value=(0,0,0))
                message=f"{data.get('split')} {name} at {row.get('clip_time_s')}s | reviewed {reviewed}/{len(data['cases'])}"
                cv2.putText(preview,message,(10,frame.shape[0]+26),cv2.FONT_HERSHEY_SIMPLEX,.7,(255,255,255),1)
                cv2.putText(preview,'A/R choose enemy boxes | N no enemies | X exclude replay/menu | S skip | Q save/exit',
                            (10,frame.shape[0]+55),cv2.FONT_HERSHEY_SIMPLEX,.65,(255,255,255),1)
                warning=row.get('quality_warning') or 'Orange is NOT a label. Click real enemies GREEN; never select portraits, allies or your replay self.'
                cv2.putText(preview,str(warning)[:155],
                            (10,frame.shape[0]+85),cv2.FONT_HERSHEY_SIMPLEX,.65,(0,180,255),1)
                cv2.imshow(title,preview)
                pressed=cv2.waitKey(0)
                if pressed<0:
                    try:
                        if cv2.getWindowProperty(title,cv2.WND_PROP_VISIBLE)<1:quit_review=True;break
                    except cv2.error:quit_review=True;break
                    continue
                key=chr(pressed&255).lower()
                if key in {'q','s','\x1b'}:
                    quit_review=key!='s';break
                if key=='x':
                    row['excluded']=True
                    row['exclusion_reason']='explicit local X review: replay/menu/non-gameplay'
                    _save_catalog(path,data)
                    break
                if key=='r' or (key=='a' and candidates):
                    weights=path.parent.parent.parent/'local-weights'/'yolo26n.pt'
                    suggest=None
                    if data.get('split') in {'train','val'} and weights.is_file():
                        def suggest(region):
                            from scripts.nolagin import _game_running
                            if _game_running('apex'):raise RuntimeError('Close Apex before local suggestions')
                            if not proposal_model:
                                from scripts.gamecoach_apex_train import load_local_yolo
                                proposal_model.append(load_local_yolo(weights,weights.parent/'local-settings'))
                            return crop_proposals(frame,region,weights,split=data['split'],model=proposal_model[0])
                    selected=edit_enemy_boxes(frame,candidates,suggest=suggest,initial_view=row.get('review_view'))
                    if selected:converted=unique_boxes(selected)
                else:converted=reviewed_proposal(candidates,key)
            if quit_review:break
            if converted is None:continue
            row["boxes"]=converted
            row["human_reviewed"]=True
            row['needs_recheck']=False
            row['review_method']='explicit local visual keyboard/ROI review'
            reviewed+=1
            # Save each frame progress atomically; never lose an hour of labels.
            _save_catalog(path,data)
    finally:
        cv2.destroyAllWindows()
    return {"total":len(data["cases"]),"human_reviewed":reviewed,
            "excluded":sum(x.get('excluded') is True for x in data['cases']),
            "positive":sum(bool(x["boxes"]) for x in data["cases"] if x.get("human_reviewed") and not x.get('excluded') and not x.get('needs_recheck')),
            "negative":sum(not x["boxes"] for x in data["cases"] if x.get("human_reviewed") and not x.get('excluded') and not x.get('needs_recheck'))}

def main():
    p=argparse.ArgumentParser()
    commands=p.add_subparsers(dest="command",required=True)
    a=commands.add_parser("extract")
    a.add_argument("--game",required=True,choices=["apex","cod"])
    a.add_argument("--clip",type=Path,required=True)
    a.add_argument("--out",type=Path,required=True)
    a.add_argument("--interval",type=float,default=1.)
    a.add_argument("--max-frames",type=int,default=1000)
    a.add_argument("--split",choices=["train","val","heldout"],default="heldout")
    b=commands.add_parser("label")
    b.add_argument("--dataset",type=Path,required=True)
    options=p.parse_args()
    if options.command=="extract":
        print(extract(options.game,options.clip,options.out,options.interval,options.max_frames,options.split))
    else:print(json.dumps(label(options.dataset),indent=2))

if __name__=="__main__":main()
