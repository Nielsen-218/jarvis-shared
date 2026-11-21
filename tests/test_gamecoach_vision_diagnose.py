"""Diagnosis guards and actual toy ONNX tracing; not enemy accuracy evidence."""
import json,hashlib
from pathlib import Path
import numpy as np
from PIL import Image
import pytest
from scripts.gamecoach_vision_diagnose import development_catalog,pixel_rows,diagnose


def dataset(tmp_path,split='val'):
    cases=[]
    for i in range(5):
        name=f'frame_{i}.png';Image.new('RGB',(32,32),(30+i,50,80)).save(tmp_path/name)
        cases.append({'image':name,'human_reviewed':True,'boxes':[[8,8,20,25]] if i==0 else []})
    path=tmp_path/'dataset.json';path.write_text(json.dumps({'game':'apex','split':split,'source_clip_sha256':'b'*64,'cases':cases}))
    return path


@pytest.mark.parametrize('change',[
    {'split':'heldout'}, {'source_clip_sha256':'c'*64},
])
def test_diagnosis_cannot_touch_heldout_even_renamed_as_development(tmp_path,change):
    path=dataset(tmp_path);data=json.loads(path.read_text());data.update(change);path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        development_catalog(path,{'game':'apex','training_source_clip_sha256':['a'*64,'b'*64]})


@pytest.mark.parametrize('flag',['excluded','needs_recheck'])
def test_disputed_development_labels_cannot_produce_accuracy(tmp_path,flag):
    path=dataset(tmp_path);data=json.loads(path.read_text());data['cases'][0][flag]=True;path.write_text(json.dumps(data))
    with pytest.raises(ValueError,match='undisputed'):
        development_catalog(path,{'game':'apex','training_source_clip_sha256':['a'*64,'b'*64]})


def test_multisource_diagnosis_requires_explicit_matching_roles(tmp_path):
    path=dataset(tmp_path)
    meta={'game':'apex','training_source_clip_sha256':['a'*64,'c'*64,'b'*64],
          'training_clip_sha256s':['a'*64,'c'*64],'development_validation_clip_sha256':'b'*64}
    assert development_catalog(path,meta)['split']=='val'
    for update in ({'development_validation_clip_sha256':'c'*64},
                   {'training_clip_sha256s':['a'*64]},
                   {'training_source_clip_sha256':['a'*64,'b'*64,'c'*64]}):
        with pytest.raises(ValueError,match='fingerprint'):
            development_catalog(path,{**meta,**update})


def test_rejected_prepared_cohort_fails_before_model_loading(tmp_path):
    path=dataset(tmp_path);manifest=tmp_path/'missing-model-manifest.json'
    manifest.write_text(json.dumps({'game':'apex','training_source_clip_sha256':['a'*64,'b'*64]}))
    prepared=tmp_path/'prepared';prepared.mkdir()
    (prepared/'dataset-summary.json').write_text(json.dumps({'quality_audit_status':'rejected'}))
    with pytest.raises(ValueError,match='rejected by audit'):
        diagnose(manifest,tmp_path/'missing.onnx',path,prepared=prepared)


@pytest.mark.parametrize('rows',[
    np.zeros((1,5,8400)), [[[10,20,80,100,np.nan,0]]],
    [[[10,20,80,100,.9,1]]], [[[.1,.2,.5,.7,.9,0]]],
])
def test_diagnostic_never_reinterprets_anchor_heads_or_invalid_rows(rows):
    with pytest.raises(ValueError):pixel_rows(rows,320,0)


def test_actual_onnx_trace_explains_baked_floor_without_lowering_live_gate(tmp_path):
    onnx=pytest.importorskip('onnx');pytest.importorskip('onnxruntime');pytest.importorskip('cv2')
    from onnx import helper as h,numpy_helper as nh,TensorProto as T
    from scripts.gamecoach_onnx_export import adapt_export
    source=tmp_path/'raw.onnx';final=tmp_path/'candidate.onnx'
    graph=h.make_graph([h.make_node('Constant',[],['rows'],value=nh.from_array(np.array([[[80,80,200,250,.84,0]]],np.float32)))],
        'toy-diagnostic-head',[h.make_tensor_value_info('images',T.FLOAT,[1,3,320,320])],[h.make_tensor_value_info('rows',T.FLOAT,[1,1,6])])
    model=h.make_model(graph,opset_imports=[h.make_opsetid('',18)],ir_version=10)
    for key,value in {'names':"{0: 'enemy'}",'end2end':'True'}.items():
        prop=model.metadata_props.add();prop.key=key;prop.value=value
    onnx.save(model,str(source));adapt_export(source,final)
    manifest=tmp_path/'vision.json';manifest.write_text(json.dumps({'game':'apex','model':final.name,
        'sha256':hashlib.sha256(final.read_bytes()).hexdigest(),'contract':'xyxy-nms-v1','classes':{'enemy':0},
        'input_size':320,'min_confidence':.92,'reviewed':False,'training_source_clip_sha256':['a'*64,'b'*64]}))
    path=dataset(tmp_path);before={p:p.read_bytes() for p in (source,final,manifest,path)}
    report=diagnose(manifest,source,path)
    assert report['diagnostic_only'] and report['heldout_used'] is False and report['training_steps']==0
    assert report['threshold_sweeps']['raw_export']['0.5']['tp']==1
    assert report['threshold_sweeps']['raw_export']['0.5']['fp']==4
    assert report['stages']['postprocessed_rows']==0 and report['stages']['runtime_boxes']==0
    assert report['live_cutoff_unchanged']==.92
    assert max(report['baked_nms_score_floors'])==pytest.approx(.85)
    assert all(p.read_bytes()==payload for p,payload in before.items())
