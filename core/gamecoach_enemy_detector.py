"""Opt-in, OFFLINE enemy-visibility adapter for game-specific validated ONNX detectors.

The generic parser is implemented; a **trained, validated Apex/CoD model is NOT
bundled**. Never substitute a general COCO "person" model for enemy/ally
recognition. No downloads, network requests, gameplay input or game-process
inspection occur here.

Model contract:
* RGB input batch [1,3,H,W], float32 scaled to [0,1], letterboxed.
* One output [N,6] or [1,N,6]: xyxy pixels in model input, score, class_id.
* Manifest explicitly maps "enemy" to a unique integer class ID.
* Game-specific human-validated precision and checksum required to enable.
* Absence of visible enemies is NOT proof that no enemies exist.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from json import loads
from math import isfinite
from pathlib import Path
from time import monotonic
from typing import Any, Callable
import numpy as np

@dataclass(frozen=True)
class EnemyCue:
    visible_count: int
    captured_at: float
    confidence: float
    proof: str

@dataclass(frozen=True)
class VisionConfig:
    game: str
    model_path: Path
    checksum: str
    input_size: int
    enemy_class: int
    min_confidence: float
    precision: float
    validation_cases: int
    validated: bool
    recall: float = 0.
    dataset_hash: str = ""
    preprocessing: str = "pillow-bilinear"

def load_config(path: Path, game: str, allow_candidate: bool=False) -> VisionConfig:
    """Checks explicit calibration attestation and model identity before inference."""
    import re
    data=loads(path.read_text(encoding="utf-8"))
    if not isinstance(data,dict) or data.get("game")!=game or game not in {"apex","cod"}:
        raise ValueError("Incorrect game-specific vision manifest")
    name=data.get("model")
    if not isinstance(name,str) or not name.endswith(".onnx") or "/" in name or "\\" in name or ".." in name:
        raise ValueError("Model filename must be a local sibling ONNX file")
    checksum=data.get("sha256","")
    if not isinstance(checksum,str) or not re.fullmatch(r"[a-fA-F0-9]{64}",checksum):
        raise ValueError("Exact ONNX SHA256 required")
    if data.get("contract")!="xyxy-nms-v1":
        raise ValueError("Unsupported ONNX post-NMS contract")
    classes=data.get("classes")
    if (not isinstance(classes,dict) or list(classes)!=["enemy"]
            or type(classes["enemy"]) is not int or classes["enemy"]<0):
        raise ValueError("Only an explicit game-specific enemy class is supported")
    n=data.get("validation_cases")
    precision=data.get("measured_precision")
    recall=data.get("measured_recall",0.)
    dataset_hash=data.get("validation_dataset_sha256","")
    verified=(data.get("reviewed") is True and type(n) is int and n>=200 and
              type(precision) in (int,float) and .98<=precision<=1 and
              type(recall) in (int,float) and .85<=recall<=1 and
              isinstance(dataset_hash,str) and bool(re.fullmatch(r"[a-fA-F0-9]{64}",dataset_hash)))
    if not allow_candidate and not verified:
        raise ValueError("Model needs >=200 labeled test cases, >=98% precision, >=85% recall, dataset checksum and review approval")
    size=data.get("input_size")
    if type(size) is not int or size not in {256,320,384,416,512,640}:
        raise ValueError("Unsupported fixed input resolution")
    cutoff=data.get("min_confidence",.92)
    if type(cutoff) not in (int,float) or not .85<=cutoff<=.995:
        raise ValueError("Confidence threshold invalid")
    preprocessing=data.get('preprocessing','pillow-bilinear')
    if not isinstance(preprocessing,str) or preprocessing not in {'pillow-bilinear','opencv-linear'}:
        raise ValueError('Unsupported model preprocessing contract')
    model=path.parent/name
    if not model.is_file():
        raise FileNotFoundError("Local ONNX file is not installed")
    h=sha256()
    with model.open("rb") as stream:
        for block in iter(lambda:stream.read(1024*1024),b""):
            h.update(block)
    if h.hexdigest().lower()!=checksum.lower():
        raise ValueError("ONNX model checksum mismatch; cannot use")
    return VisionConfig(game,model,checksum,size,classes["enemy"],float(cutoff),
                        float(precision) if isinstance(precision,(float,int)) else 0.,
                        n if type(n) is int else 0,verified,
                        float(recall) if type(recall) in (int,float) else 0.,dataset_hash,preprocessing)

def preprocess(frame: np.ndarray, size: int, backend: str = 'pillow-bilinear') -> tuple[np.ndarray, tuple[float,int,int]]:
    """RGB letterbox; preserve legacy pixels or match Ultralytics OpenCV resize."""
    from PIL import Image
    if (not isinstance(frame,np.ndarray) or frame.dtype!=np.uint8 or frame.ndim!=3
            or frame.shape[2]!=3 or not 16<=frame.shape[0]<=4320
            or not 16<=frame.shape[1]<=7680):
        raise ValueError("Expected bounded RGB uint8 game screenshot")
    h,w=frame.shape[:2]
    scale=min(size/w,size/h)
    rw,rh=max(1,round(w*scale)),max(1,round(h*scale))
    if backend=='opencv-linear':
        import cv2
        small=cv2.resize(frame,(rw,rh),interpolation=cv2.INTER_LINEAR)
    elif backend=='pillow-bilinear':
        small=np.asarray(Image.fromarray(frame).resize((rw,rh),Image.Resampling.BILINEAR))
    else:raise ValueError('Unsupported model preprocessing contract')
    padded=np.full((size,size,3),114,dtype=np.uint8)
    x=(size-rw)//2
    y=(size-rh)//2
    padded[y:y+rh,x:x+rw]=small
    inp=np.ascontiguousarray(padded.transpose(2,0,1)[None].astype(np.float32)/255)
    return inp,(scale,x,y)

def detection_rows(raw: Any, size: int, class_id: int | None = None) -> np.ndarray:
    """Validate post-NMS pixel rows, ignoring only all-zero padding.

    Structural checks cannot prove xyxy semantics or recognition accuracy;
    independent, human-labeled held-out frames remain mandatory.
    """
    rows=np.asarray(raw)
    if rows.ndim==3 and rows.shape[0]==1:
        rows=rows[0]
    if (rows.ndim!=2 or rows.shape[1]!=6 or rows.shape[0]>2000
            or rows.dtype.kind not in "fi" or not np.isfinite(rows).all()):
        raise ValueError("Invalid post-NMS detection tensor")
    rows=rows[np.any(rows!=0,axis=1)]
    if not len(rows):return rows
    boxes,scores,labels=rows[:,:4],rows[:,4],rows[:,5]
    if (np.any(scores<0) or np.any(scores>1) or np.any(labels<0)
            or np.any(labels!=np.floor(labels))
            or (class_id is not None and np.any(labels!=class_id))):
        raise ValueError("Invalid detection score or class ID")
    if (np.any(boxes<0) or np.any(boxes>size)
            or np.any(boxes[:,0]>=boxes[:,2]) or np.any(boxes[:,1]>=boxes[:,3])):
        raise ValueError("Expected pixel-space xyxy boxes inside model input")
    if np.any(np.max(boxes,axis=1)<=1):
        raise ValueError("Normalized coordinates do not satisfy pixel-space contract")
    return rows


def decode(raw: Any, transform: tuple[float,int,int], original_hw: tuple[int,int],
           class_id: int, threshold: float, input_size: int | None = None
           ) -> list[tuple[float,float,float,float,float]]:
    """Strict post-NMS parser. No bounding box or score is ever invented."""
    try:
        scale,x_off,y_off=transform
        h,w=original_hw
        if not isfinite(scale) or scale<=0:return []
        size=input_size or max(round(w*scale)+2*x_off,round(h*scale)+2*y_off)
        rows=detection_rows(raw,size)
    except (ValueError,TypeError):
        return []
    out=[]
    for x1,y1,x2,y2,confidence,label in rows:
        if confidence<threshold or label!=class_id:
            continue
        a=max(0.,min(float(w),(x1-x_off)/scale))
        b=max(0.,min(float(h),(y1-y_off)/scale))
        c=max(0.,min(float(w),(x2-x_off)/scale))
        d=max(0.,min(float(h),(y2-y_off)/scale))
        if c-a<8 or d-b<12:
            continue
        out.append((a,b,c,d,float(confidence)))
    # The model contract already requires NMS, but overlapping detections may
    # still duplicate one player. Merge extreme overlaps conservatively.
    out.sort(key=lambda item:item[4],reverse=True)
    unique=[]
    for candidate in out:
        ax,ay,bx,by,_=candidate
        area=(bx-ax)*(by-ay)
        duplicate=False
        for keep in unique:
            cx,cy,dx,dy,_=keep
            intersect=max(0,min(bx,dx)-max(ax,cx))*max(0,min(by,dy)-max(ay,cy))
            union=area+(dx-cx)*(dy-cy)-intersect
            if union>0 and intersect/union>=.5:
                duplicate=True
                break
        if not duplicate:
            unique.append(candidate)
        if len(unique)>=9:
            break
    return unique

class GameEnemyDetector:
    """One verified model; default CPU-only provider with 1 inference thread.

    The owner may elect another provider after independent FPS benchmarks.
    """
    def __init__(self,config:VisionConfig,session:Any|None=None):
        self.config=config
        if config.preprocessing=='opencv-linear':
            try:import cv2
            except ImportError as exc:
                raise ValueError('This model requires OpenCV; install optional Nolagin runtime dependencies') from exc
        if session is None:
            import onnxruntime as ort
            opts=ort.SessionOptions()
            opts.intra_op_num_threads=1
            opts.inter_op_num_threads=1
            opts.graph_optimization_level=ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            session=ort.InferenceSession(str(config.model_path),sess_options=opts,
                                         providers=["CPUExecutionProvider"])
        self.session=session
        inputs=session.get_inputs()
        if (len(inputs)!=1 or inputs[0].shape!=[1,3,config.input_size,config.input_size]
                or inputs[0].type!="tensor(float)"):
            raise ValueError("Expected fixed float32 NCHW model input matching manifest")
        self.input_name=inputs[0].name
        self.stats={"frames":0,"recognized":0,"errors":0,"inference_ms":[]}

    def predict_boxes(self,frame:np.ndarray,captured_at:float,
                      clock:Callable[[],float]=monotonic):
        """Return verified-shape boxes or None on stale/failed input; [] means no detections."""
        age=clock()-captured_at
        if not isfinite(captured_at) or not 0<=age<=.6:
            return None
        start=clock()
        try:
            tensor,transform=preprocess(frame,self.config.input_size,self.config.preprocessing)
            raw=self.session.run(None,{self.input_name:tensor})
            if len(raw)!=1:
                raise ValueError("Expected one post-NMS model output")
            detection_rows(raw[0],self.config.input_size)
            boxes=decode(raw[0],transform,frame.shape[:2],
                         self.config.enemy_class,self.config.min_confidence,
                         self.config.input_size)
        except Exception:
            self.stats["errors"]+=1
            return None
        elapsed=(clock()-start)*1000
        self.stats["frames"]+=1
        if boxes:self.stats["recognized"]+=1
        vals=self.stats["inference_ms"]
        vals.append(elapsed)
        if len(vals)>512:del vals[:len(vals)-512]
        return boxes

    def infer(self,frame:np.ndarray,captured_at:float,clock:Callable[[],float]=monotonic)->EnemyCue|None:
        boxes=self.predict_boxes(frame,captured_at,clock)
        if not boxes:return None
        return EnemyCue(len(boxes),captured_at,min(b[4] for b in boxes),
                        f"{len(boxes)} game-trained visible enemy boxes in current captured frame")

    def summary(self)->dict:
        times=sorted(self.stats["inference_ms"])
        return {"frames":self.stats["frames"],"recognized":self.stats["recognized"],
                "errors":self.stats["errors"],
                "inference_p95_ms":round(times[int((len(times)-1)*.95)],2) if times else None,
                "validation_cases":self.config.validation_cases,
                "declared_precision":self.config.precision,
                "note":"Model-specific validation must be checked against real game clips"}
