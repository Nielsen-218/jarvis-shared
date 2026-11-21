"""Read-only CPU diagnosis on reviewed DEVELOPMENT frames, never certification.

Trace raw export -> baked NMS -> actual Jarvis decoder, compare the same tensor
in PyTorch/ONNX, and sweep diagnostic thresholds without changing live policy.
"""
from __future__ import annotations
import argparse,ast,json
from pathlib import Path
from time import monotonic
import sys
import numpy as np
from PIL import Image
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from core.gamecoach_enemy_detector import GameEnemyDetector,load_config,preprocess,detection_rows,decode
from scripts.gamecoach_apex_train import _hash_file,_private_frame,load_local_yolo
from scripts.gamecoach_vision_validate import overlap,match_frame

THRESHOLDS=(.001,.01,.025,.05,.1,.25,.5,.85,.92)


def development_catalog(path:Path,manifest:dict)->dict:
    data=json.loads(path.read_text(encoding='utf-8'))
    split=data.get('split')
    if split not in {'train','val'} or data.get('game')!=manifest.get('game'):
        raise ValueError('Diagnosis accepts only game-matched train/val, never heldout')
    sources=manifest.get('training_source_clip_sha256',[])
    train_sources=manifest.get('training_clip_sha256s',sources[:-1] if len(sources)==2 else [])
    val_source=manifest.get('development_validation_clip_sha256',sources[-1] if len(sources)==2 else None)
    if (len(sources)<2 or len(set(sources))!=len(sources) or not train_sources
        or sources!=[*train_sources,val_source] or val_source in train_sources
        or data.get('source_clip_sha256') not in (train_sources if split=='train' else [val_source])):
        raise ValueError('Development source fingerprint differs; heldout must remain untouched')
    cases=data.get('cases')
    if not isinstance(cases,list) or not 1<=len(cases)<=5000:
        raise ValueError('Need 1..5000 development cases')
    if any(not isinstance(r,dict) or r.get('human_reviewed') is not True
           or r.get('excluded') or r.get('needs_recheck') for r in cases):
        raise ValueError('Only undisputed, human-reviewed development labels qualify')
    for row in cases:_private_frame(path.parent,row.get('image'))
    return data


def pixel_rows(raw,size,class_id):
    """Inspect pre-clipping export rows; never reinterpret raw xywh/anchor axes."""
    rows=np.asarray(raw)
    if rows.ndim==3 and rows.shape[0]==1:rows=rows[0]
    if rows.ndim!=2 or rows.shape[1]!=6 or len(rows)>2000 or not np.isfinite(rows).all():
        raise ValueError('Raw export must be finite [1,N,6] pixel xyxy/score/class, not an anchor head')
    rows=rows[np.any(rows!=0,axis=1)].copy()
    if not len(rows):return rows.reshape(0,6)
    if np.any(rows[:,4]<0) or np.any(rows[:,4]>1) or np.any(rows[:,5]!=class_id):
        raise ValueError('Raw score or enemy class mapping mismatch')
    rows[:,:4]=np.clip(rows[:,:4],0,size)
    rows=rows[(rows[:,2]>rows[:,0])&(rows[:,3]>rows[:,1])]
    if len(rows) and np.any(np.max(rows[:,:4],axis=1)<=1):
        raise ValueError('Normalized coordinates are not the pixel-space contract')
    return rows


def diagnostic_boxes(rows,threshold,transform,shape,size,class_id):
    kept=[]
    for row in rows[np.argsort(-rows[:,4],kind='stable')]:
        if row[4]>=threshold and all(overlap(row,k)<=.499 for k in kept):kept.append(row)
    return decode(np.asarray(kept,dtype=np.float32).reshape(1,-1,6),transform,shape,class_id,threshold,size)


def add_counts(stats,boxes,truth):
    a,b,c=match_frame(boxes,truth)
    stats['tp']+=a;stats['fp']+=b;stats['fn']+=c
    stats['negative_fp_frames']+=int(not truth and bool(boxes))


def counts():return {'tp':0,'fp':0,'fn':0,'negative_fp_frames':0}


def graph_score_floors(path):
    import onnx
    from onnx import numpy_helper
    graph=onnx.load(str(path)).graph
    constants={x.name:numpy_helper.to_array(x) for x in graph.initializer}
    for node in graph.node:
        if node.op_type=='Constant':
            for attr in node.attribute:
                if attr.name=='value':constants[node.output[0]]=numpy_helper.to_array(attr.t)
    floors=[]
    for node in graph.node:
        if node.op_type=='NonMaxSuppression' and len(node.input)>4 and node.input[4] in constants:
            value=np.asarray(constants[node.input[4]])
            if value.size==1 and np.isfinite(value).all():floors.append(float(value.reshape(-1)[0]))
    return floors


def finish(stats,negatives):
    for s in stats.values():
        s['precision']=s['tp']/(s['tp']+s['fp']) if s['tp']+s['fp'] else None
        s['recall']=s['tp']/(s['tp']+s['fn']) if s['tp']+s['fn'] else None
        s['negative_frame_fpr']=s['negative_fp_frames']/negatives if negatives else None
    return stats


class TraceSession:
    def __init__(self,session):self.session=session
    def get_inputs(self):return self.session.get_inputs()
    def run(self,names,feeds):
        self.tensor=next(iter(feeds.values()))
        self.outputs=self.session.run(names,feeds)
        return self.outputs


def pytorch_rows(model,tensor,end2end):
    import torch
    from ultralytics.utils.nms import non_max_suppression
    original=bool(model.model.end2end)
    try:
        model.model.end2end=end2end
        with torch.inference_mode():
            result=model.model(torch.from_numpy(tensor))
            result=result[0] if isinstance(result,(tuple,list)) else result
            if end2end:rows=result[0]
            else:rows=non_max_suppression(result,conf_thres=.001,iou_thres=.499,nc=1,max_det=300)[0]
            return rows.cpu().numpy()
    finally:model.model.end2end=original


def diagnose(manifest:Path,raw_model:Path,dataset:Path,*,checkpoint:Path|None=None,
             prepared:Path|None=None,settings_dir:Path|None=None)->dict:
    meta=json.loads(manifest.read_text(encoding='utf-8'))
    catalog=development_catalog(dataset,meta)  # Refuse heldout before model loading/inference.
    if prepared:
        summary=json.loads((prepared/'dataset-summary.json').read_text())
        if summary.get('quality_audit_status')=='rejected':raise ValueError('Prepared dataset rejected by audit')
        if summary.get(catalog['split']+'_frames')!=len(catalog['cases']):
            raise ValueError('Prepared cohort size differs; use the exact frozen cohort catalog')
        if summary.get(catalog['split']+'_clip_sha256')!=catalog['source_clip_sha256']:
            raise ValueError('Prepared cohort source differs from the reviewed catalog')
    cfg=load_config(manifest,catalog['game'],allow_candidate=True)
    import onnxruntime as ort
    opts=ort.SessionOptions();opts.intra_op_num_threads=1;opts.inter_op_num_threads=1
    def session(path):return ort.InferenceSession(str(path),sess_options=opts,providers=['CPUExecutionProvider'])
    raw_session=session(raw_model)
    inp=raw_session.get_inputs()
    if len(inp)!=1 or inp[0].shape!=[1,3,cfg.input_size,cfg.input_size] or inp[0].type!='tensor(float)':
        raise ValueError('Raw ONNX input differs from the runtime contract')
    metadata=raw_session.get_modelmeta().custom_metadata_map
    if metadata.get('nolagin_postprocess'):raise ValueError('Provide the RAW export, not the already floor-filtered candidate')
    if ast.literal_eval(metadata.get('names','{}'))!={cfg.enemy_class:'enemy'}:
        raise ValueError('Raw export lacks the matching enemy class mapping')
    exported_head=ast.literal_eval(metadata.get('end2end','None'))
    trace=TraceSession(session(cfg.model_path));detector=GameEnemyDetector(cfg,session=trace)
    pt=None;checkpoint_head=None
    if checkpoint:
        import torch
        torch.set_num_threads(1)
        pt=load_local_yolo(checkpoint,settings_dir or manifest.parent/'diagnostic-yolo-settings')
        pt.model.cpu().float().eval();checkpoint_head=bool(pt.model.end2end)
        if pt.names!={cfg.enemy_class:'enemy'} or type(exported_head) is not bool:
            raise ValueError('Need an explicit matching checkpoint/export head for numerical parity')
    variants={'raw_export':{str(t):counts() for t in THRESHOLDS},
              'alternative_resize':{str(t):counts() for t in THRESHOLDS}}
    if pt:variants['checkpoint_head']={str(t):counts() for t in THRESHOLDS}
    stages={'raw_rows_at_85':0,'postprocessed_rows':0,'runtime_boxes':0}
    details=[];parity=[];projected=[];label_files=0
    for index,row in enumerate(catalog['cases']):
        image=_private_frame(dataset.parent,row['image'])
        with Image.open(image) as im:frame=np.asarray(im.convert('RGB'))
        if not isinstance(row.get('boxes'),list):raise ValueError('Invalid ground truth')
        for box in row['boxes']:
            if len(box)!=4 or not np.isfinite(box).all() or not (0<=box[0]<box[2]<=frame.shape[1] and 0<=box[1]<box[3]<=frame.shape[0]):
                raise ValueError('Invalid ground truth coordinates')
        if prepared:
            split=catalog['split'];name=f'frame_{index:05}.png'
            if _hash_file(prepared/'images'/split/name)!=_hash_file(image):raise ValueError('Prepared/source frame mismatch')
            labels=np.asarray([[float(x) for x in line.split()] for line in (prepared/'labels'/split/Path(name).with_suffix('.txt')).read_text().splitlines() if line.strip()]).reshape(-1,5)
            expected=np.asarray([[cfg.enemy_class,(a+c)/2/frame.shape[1],(b+d)/2/frame.shape[0],(c-a)/frame.shape[1],(d-b)/frame.shape[0]] for a,b,c,d in row['boxes']]).reshape(-1,5)
            if labels.shape!=expected.shape or not np.allclose(labels,expected,atol=1e-6,rtol=0):raise ValueError('Normalized labels differ from owner boxes')
            label_files+=1
        runtime_boxes=detector.predict_boxes(frame,monotonic())
        if runtime_boxes is None:raise RuntimeError('Runtime inference failed; do not manufacture accuracy')
        tensor,transform=preprocess(frame,cfg.input_size,cfg.preprocessing)
        if not np.array_equal(tensor,trace.tensor):raise ValueError('Runtime input trace differs')
        raw=raw_session.run(None,{inp[0].name:trace.tensor})[0]
        rows=pixel_rows(raw,cfg.input_size,cfg.enemy_class)
        post=detection_rows(trace.outputs[0],cfg.input_size,cfg.enemy_class)
        stages['raw_rows_at_85']+=int(np.sum(rows[:,4]>=.85));stages['postprocessed_rows']+=len(post);stages['runtime_boxes']+=len(runtime_boxes)
        alternative='opencv-linear' if cfg.preprocessing=='pillow-bilinear' else 'pillow-bilinear'
        alt_tensor,_=preprocess(frame,cfg.input_size,alternative)
        alt_rows=pixel_rows(raw_session.run(None,{inp[0].name:alt_tensor})[0],cfg.input_size,cfg.enemy_class)
        sources={'raw_export':rows,'alternative_resize':alt_rows}
        if pt:
            selected=pixel_rows(pytorch_rows(pt,tensor,checkpoint_head),cfg.input_size,cfg.enemy_class)
            matching=selected if exported_head==checkpoint_head else pixel_rows(pytorch_rows(pt,tensor,exported_head),cfg.input_size,cfg.enemy_class)
            sources['checkpoint_head']=selected
            a=matching[matching[:,4]>=.001];b=rows[rows[:,4]>=.001]
            a=a[np.argsort(-a[:,4])];b=b[np.argsort(-b[:,4])]
            parity.append({'image':row['image'],'pt_active':len(a),'onnx_active':len(b),
                'score_max_abs_error':float(np.max(np.abs(a[:,4]-b[:,4]))) if len(a)==len(b) and len(a) else (0. if not len(a) and not len(b) else None),
                'coordinate_max_abs_error':float(np.max(np.abs(a[:,:4]-b[:,:4]))) if len(a)==len(b) and len(a) else None})
        for source,source_rows in sources.items():
            for t in THRESHOLDS:
                boxes=diagnostic_boxes(source_rows,t,transform,frame.shape[:2],cfg.input_size,cfg.enemy_class)
                add_counts(variants[source][str(t)],boxes,row['boxes'])
        detail={'image':row['image'],'positive':bool(row['boxes']),
                'max_raw_score':float(rows[:,4].max()) if len(rows) else 0.,'post_rows':len(post),'runtime_boxes':len(runtime_boxes),
                'alternative_resize_max_score':float(alt_rows[:,4].max()) if len(alt_rows) else 0.,
                'resize_input_mean_abs_error':float(np.mean(np.abs(tensor-alt_tensor)))}
        if row['boxes']:
            swapped=np.ascontiguousarray(tensor[:,::-1])
            wrong=pixel_rows(raw_session.run(None,{inp[0].name:swapped})[0],cfg.input_size,cfg.enemy_class)
            detail['wrong_bgr_control_max_score']=float(wrong[:,4].max()) if len(wrong) else 0.
        details.append(detail)
        projected.extend([[(c-a)*transform[0],(d-b)*transform[0]] for a,b,c,d in row['boxes']])
    negatives=sum(not r['boxes'] for r in catalog['cases'])
    valid_errors=[p['score_max_abs_error'] for p in parity if p['score_max_abs_error'] is not None]
    return {'diagnostic_only':True,'certification':False,'heldout_used':False,'training_steps':0,
        'split':catalog['split'],'frames':len(catalog['cases']),'positive_frames':len(catalog['cases'])-negatives,'negative_frames':negatives,
        'dataset_sha256':_hash_file(dataset),'raw_model_sha256':_hash_file(raw_model),'runtime_model_sha256':meta['sha256'],
        'checkpoint_sha256':_hash_file(checkpoint) if checkpoint else None,
        'checkpoint_end2end':checkpoint_head,'export_end2end':exported_head,
        'head_mismatch':checkpoint_head!=exported_head if pt else None,
        'runtime_preprocessing':cfg.preprocessing,'alternative_preprocessing':alternative,
        'live_cutoff_unchanged':cfg.min_confidence,'postprocessing_metadata':meta.get('postprocessing'),
        'baked_nms_score_floors':graph_score_floors(cfg.model_path),
        'stages':stages,'threshold_sweeps':{k:finish(v,negatives) for k,v in variants.items()},
        'parity_matching_export_head':{'frames':len(parity),'active_count_mismatches':sum(p['pt_active']!=p['onnx_active'] for p in parity),'maximum_score_abs_error':max(valid_errors,default=None),'cases':parity},
        'projected_ground_truth_wh_min':np.min(projected,axis=0).tolist() if projected else None,
        'projected_ground_truth_wh_median':np.median(projected,axis=0).tolist() if projected else None,
        'prepared_image_and_label_pairs_verified':label_files,'runtime_inference':detector.summary(),'cases':details,
        'warning':'Below-.85 thresholds are analysis only. No live settings or review/certification flags were changed. Head-aligned numerical parity does not imply enemy recognition quality.'}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('manifest','raw-model','dataset','report'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--checkpoint',type=Path);p.add_argument('--prepared',type=Path)
    args=p.parse_args()
    from scripts.nolagin import _game_running
    if _game_running('apex'):p.error('Close Apex before CPU diagnostic work to protect gaming performance')
    try:
        result=diagnose(args.manifest,args.raw_model,args.dataset,checkpoint=args.checkpoint,prepared=args.prepared,settings_dir=args.report.parent/'local-settings')
        args.report.parent.mkdir(parents=True,exist_ok=True)
        args.report.write_text(json.dumps(result,indent=2),encoding='utf-8')
        print(json.dumps({k:v for k,v in result.items() if k not in {'cases','parity_matching_export_head'}},indent=2))
        return 0
    except (ValueError,OSError,RuntimeError,ImportError) as e:
        print(json.dumps({'diagnostic_only':True,'passed':False,'error':str(e)},indent=2));return 2
if __name__=='__main__':raise SystemExit(main())
