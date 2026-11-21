"""Apex player-owned train/validation conversion; no GPU or YOLO dependency."""
import json
import numpy as np
import pytest
from PIL import Image
from pathlib import Path
from scripts.gamecoach_apex_train import prepare_training,train_and_export
from scripts.gamecoach_apex_train import verify_export
import sys
from types import SimpleNamespace


def mock_export(monkeypatch, rows, shape=None, dtype="tensor(float)"):
    class Session:
        def get_inputs(self):
            return [SimpleNamespace(name="images",shape=shape or [1,3,320,320],type=dtype)]
        def run(self, names, feed):
            assert feed["images"].shape==(1,3,320,320)
            return [np.asarray(rows,dtype=np.float32)]
    monkeypatch.setitem(sys.modules,"onnxruntime",SimpleNamespace(
        SessionOptions=lambda:SimpleNamespace(),InferenceSession=lambda *a,**kw:Session()))


@pytest.mark.parametrize("rows",[
    [[[10,20,90,160,.97,0],[0,0,0,0,0,0]]],
    np.zeros((1,300,6)), np.zeros((1,0,6)),
])
def test_export_accepts_pixel_contract_and_zero_padding(monkeypatch,rows):
    mock_export(monkeypatch,rows)
    verify_export(Path("synthetic-test-only.onnx"))


@pytest.mark.parametrize("rows",[
    np.zeros((1,6,300)), np.zeros((1,2001,6)),
    [[[10,20,90,160,1.2,0]]], [[[10,20,90,160,-.1,0]]],
    [[[10,20,90,160,.98,1]]], [[[10,20,90,160,.98,.5]]],
    [[[10,20,90,160,.98,-1]]], [[[10,20,90,160,np.nan,0]]],
    [[[.1,.2,.5,.9,.98,0]]], [[[10,20,321,160,.98,0]]],
    [[[90,20,10,160,.98,0]]], [[[-1,20,90,160,.98,0]]],
    [[[10,20,90,160,.98,0],[11,21,91,161,.97,0]]],
])
def test_export_rejects_malformed_semantics_and_repeated_boxes(monkeypatch,rows):
    mock_export(monkeypatch,rows)
    with pytest.raises(ValueError):verify_export(Path("synthetic-test-only.onnx"))


@pytest.mark.parametrize("shape,dtype",[
    ([1,320,320,3],"tensor(float)"),([1,3,640,640],"tensor(float)"),
    ([1,3,320,320],"tensor(uint8)"),
])
def test_export_rejects_wrong_input_contract(monkeypatch,shape,dtype):
    mock_export(monkeypatch,np.zeros((1,300,6)),shape,dtype)
    with pytest.raises(ValueError):verify_export(Path("synthetic-test-only.onnx"))

def make_group(root:Path,split:str,base:int)->Path:
    root.mkdir()
    cases=[]
    for i in range(5):
        arr=np.full((40,60,3),(base+i,20+i,120),np.uint8)
        name=f"image_{i}.png"
        Image.fromarray(arr).save(root/name)
        cases.append({"image":name,"boxes":[[10,10,40,35]] if i%2==0 else [],
                      "human_reviewed":True})
    catalog={"game":"apex","split":split,
             "source_clip_sha256":f"{base:064x}","cases":cases}
    (root/"dataset.json").write_text(json.dumps(catalog))
    return root/"dataset.json"


def agent_case(path):
    from hashlib import sha256
    from scripts.gamecoach_apex_train import agent_annotation_hash
    data=json.loads(path.read_text())
    row=data['cases'][0]
    with Image.open(path.parent/row['image']) as im:pixels=np.asarray(im.convert('RGB'))
    row['human_reviewed']=False
    row['agent_annotation_review']={'annotator':'vision-a','reviewer':'vision-b',
        'independent_agreement':True,'pixel_sha256':sha256(str(pixels.shape).encode()+pixels.tobytes()).hexdigest()}
    row['agent_annotation_review']['annotation_sha256']=agent_annotation_hash(
        row['agent_annotation_review']['pixel_sha256'],row['boxes'])
    path.write_text(json.dumps(data))
    return data


def test_agent_training_requires_explicit_policy_and_independent_pixel_bound_review(tmp_path):
    a=make_group(tmp_path/'train','train',22);b=make_group(tmp_path/'val','val',23)
    agent_case(a)
    with pytest.raises(ValueError,match='human reviewed'):prepare_training(a,b,tmp_path/'blocked')
    result=prepare_training(a,b,tmp_path/'prepared',allow_agent_reviewed=True)
    assert result['agent_reviewed_training_frames']==1
    assert result['annotation_policy']=='explicit independent agent agreement for training only'


def test_agent_annotations_never_qualify_as_development_ground_truth(tmp_path):
    a=make_group(tmp_path/'train','train',22);b=make_group(tmp_path/'val','val',23)
    agent_case(b)
    with pytest.raises(ValueError,match='human reviewed'):
        prepare_training(a,b,tmp_path/'blocked',allow_agent_reviewed=True)


@pytest.mark.parametrize('change',['pixels','reviewer','boxes'])
def test_agent_review_rejects_changed_pixels_or_same_reviewer(tmp_path,change):
    a=make_group(tmp_path/'train','train',22);b=make_group(tmp_path/'val','val',23)
    data=agent_case(a)
    if change=='pixels':Image.new('RGB',(60,40)).save(a.parent/data['cases'][0]['image'])
    elif change=='reviewer':
        data['cases'][0]['agent_annotation_review']['reviewer']='vision-a'
        a.write_text(json.dumps(data))
    else:
        data['cases'][0]['boxes']=[[10,10,30,35]]
        a.write_text(json.dumps(data))
    with pytest.raises(ValueError):prepare_training(a,b,tmp_path/'blocked',allow_agent_reviewed=True)

def test_prepare_rejects_repeated_enter_boxes_in_human_labels(tmp_path):
    a=make_group(tmp_path/'train','train',22)
    b=make_group(tmp_path/'val','val',23)
    data=json.loads(a.read_text())
    data['cases'][0]['boxes']*=2
    a.write_text(json.dumps(data))
    with pytest.raises(ValueError,match='Repeated identical enemy box'):
        prepare_training(a,b,tmp_path/'prepared')


@pytest.mark.parametrize('update',[
    {'boxes':[[10,10,40,35],[11,10,40,35]]},
    {'boxes':[[10,10,11,11]]},
    {'excluded':True},
    {'needs_recheck':True},
])
def test_prepare_preserves_disputed_labels_and_refuses_training(tmp_path,update):
    a=make_group(tmp_path/'train','train',22);b=make_group(tmp_path/'val','val',23)
    data=json.loads(a.read_text());data['cases'][0].update(update);a.write_text(json.dumps(data))
    before=a.read_bytes()
    with pytest.raises(ValueError):prepare_training(a,b,tmp_path/'prepared')
    assert a.read_bytes()==before and not (tmp_path/'prepared').exists()


def test_rejected_prepared_snapshot_cannot_bypass_audit_by_reusing_old_cache(tmp_path):
    from scripts.gamecoach_apex_train import train_and_export
    a=make_group(tmp_path/'train','train',22);b=make_group(tmp_path/'val','val',23)
    prepared=tmp_path/'prepared';prepare_training(a,b,prepared)
    summary=prepared/'dataset-summary.json';meta=json.loads(summary.read_text())
    meta['quality_audit_status']='rejected';summary.write_text(json.dumps(meta))
    with pytest.raises(ValueError,match='rejected by audit'):
        train_and_export(prepared,tmp_path/'missing.pt',tmp_path/'output')
    assert not (tmp_path/'output').exists()


def test_prepare_uses_whole_clips_and_creates_normalized_enemy_labels(tmp_path):
    a=make_group(tmp_path/"train","train",22)
    b=make_group(tmp_path/"val","val",23)
    output=tmp_path/"prepared"
    result=prepare_training(a,b,output)
    assert result["train_frames"]==5 and result["val_frames"]==5
    assert "0: enemy" in (output/"apex.yaml").read_text()
    label=(output/"labels"/"train"/"frame_00000.txt").read_text().strip()
    cls,xc,yc,w,h=label.split()
    assert cls=="0" and float(xc)==pytest.approx(50/120,abs=1e-5)
    assert float(yc)==pytest.approx(45/80,abs=1e-5)
    assert float(w)==pytest.approx(.5) and float(h)==pytest.approx(.625)
    assert (output/"labels"/"train"/"frame_00001.txt").read_text()==""
    with pytest.raises(FileExistsError):prepare_training(a,b,output)

def test_prepare_rejects_train_and_val_from_same_clip(tmp_path):
    a=make_group(tmp_path/"train","train",12)
    b=make_group(tmp_path/"val","val",13)
    catalog=json.loads(b.read_text())
    catalog["source_clip_sha256"]=json.loads(a.read_text())["source_clip_sha256"]
    b.write_text(json.dumps(catalog))
    with pytest.raises(ValueError,match="SAME original clip"):
        prepare_training(a,b,tmp_path/"out")

def test_prepare_rejects_mislabelled_unreviewed_or_outside_boxes(tmp_path):
    a=make_group(tmp_path/"train","train",12)
    b=make_group(tmp_path/"val","val",13)
    raw=json.loads(a.read_text())
    raw["cases"][0]["human_reviewed"]=False
    a.write_text(json.dumps(raw))
    with pytest.raises(ValueError,match="human reviewed"):
        prepare_training(a,b,tmp_path/"out")
    raw["cases"][0]["human_reviewed"]=True
    raw["cases"][0]["boxes"]=[[0,0,8000,50]]
    a.write_text(json.dumps(raw))
    with pytest.raises(ValueError,match="outside image"):
        prepare_training(a,b,tmp_path/"out")

def test_prepare_rejects_reused_images_across_groups(tmp_path):
    a=make_group(tmp_path/"train","train",12)
    b=make_group(tmp_path/"val","val",13)
    (b.parent/"image_0.png").write_bytes((a.parent/"image_0.png").read_bytes())
    with pytest.raises(ValueError,match="Duplicated images"):
        prepare_training(a,b,tmp_path/"out")


def test_multiple_training_recordings_preserve_sources_and_all_labels(tmp_path):
    first=make_group(tmp_path/'first','train',12)
    second=make_group(tmp_path/'second','train',22)
    val=make_group(tmp_path/'val','val',32)
    before={p:p.read_bytes() for p in (first,second,val)}
    out=tmp_path/'prepared'
    result=prepare_training([first,second],val,out)
    assert result['train_frames']==10 and result['val_frames']==5
    assert result['train_clip_sha256'] is None
    assert result['train_clip_sha256s']==[f'{12:064x}',f'{22:064x}']
    assert len(list((out/'images/train').glob('*.png')))==10
    assert (out/'images/train/frame_00005.png').read_bytes()==(second.parent/'image_0.png').read_bytes()
    assert (out/'labels/train/frame_00005.txt').read_text().startswith('0 ')
    assert all(p.read_bytes()==value for p,value in before.items())


def test_multisource_rejects_validation_overlap_and_repeated_train_source(tmp_path):
    first=make_group(tmp_path/'first','train',12)
    second=make_group(tmp_path/'second','train',22)
    val=make_group(tmp_path/'val','val',22)
    with pytest.raises(ValueError,match='SAME original clip'):
        prepare_training([first,second],val,tmp_path/'prepared')
    with pytest.raises(ValueError,match='Repeated training recording'):
        prepare_training([first,first],val,tmp_path/'prepared')
    assert not (tmp_path/'prepared').exists()

def test_missing_local_weights_never_downloads_pretraining(tmp_path):
    p=tmp_path/"prepared"
    p.mkdir()
    (p/"apex.yaml").write_text("train: images/train")
    (p/"dataset-summary.json").write_text(json.dumps({"train_clip_sha256":"1"*64,"val_clip_sha256":"2"*64}))
    with pytest.raises(FileNotFoundError,match="LOCAL"):
        train_and_export(p,tmp_path/"missing_weights.pt",tmp_path/"output")


@pytest.mark.parametrize('end2end',[False,True])
def test_export_preserves_checkpoint_head_instead_of_forcing_one2one(tmp_path,end2end):
    from scripts.gamecoach_apex_train import export_trained_yolo
    calls=[]
    model=SimpleNamespace(names={0:'enemy'},model=SimpleNamespace(end2end=end2end),
        ckpt_path=str(tmp_path/'best.pt'),export=lambda **kw:calls.append(kw) or tmp_path/'best.onnx')
    export_trained_yolo(model,640)
    assert calls[0]['nms'] is (not end2end)
    if not end2end:assert calls[0]['conf']==.001 and calls[0]['iou']==.499
    assert model.model.end2end is end2end


def test_export_preserves_existing_raw_artifact(tmp_path):
    from scripts.gamecoach_apex_train import export_trained_yolo
    (tmp_path/'best.onnx').write_bytes(b'preserve')
    model=SimpleNamespace(names={0:'enemy'},ckpt_path=str(tmp_path/'best.pt'))
    with pytest.raises(FileExistsError,match='Preserve'):export_trained_yolo(model)
    assert (tmp_path/'best.onnx').read_bytes()==b'preserve'
