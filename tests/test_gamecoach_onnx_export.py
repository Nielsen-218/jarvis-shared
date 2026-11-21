"""Real CPU ONNX graph tests; synthetic heads are NOT recognition benchmarks."""
from pathlib import Path
import json
import numpy as np
import pytest
onnx=pytest.importorskip('onnx')
ort=pytest.importorskip('onnxruntime')
from onnx import helper as h,numpy_helper as nh,TensorProto as T
from scripts.gamecoach_onnx_export import adapt_export
from scripts.gamecoach_apex_train import verify_export,finalize_export


def raw_head(path:Path,rows,*,overflow=False,names=None,size=320):
    rows=np.asarray(rows,dtype=np.float32)
    nodes=[h.make_node('Constant',[],['constant'],value=nh.from_array(rows))]
    if overflow:
        nodes += [h.make_node('ReduceMean',['images'],['mean'],keepdims=0),
                  h.make_node('Constant',[],['one'],value=nh.from_array(np.asarray(1.,np.float32))),
                  h.make_node('Sub',['one','mean'],['denominator']),
                  h.make_node('Div',['one','denominator'],['scale']),
                  h.make_node('Mul',['constant','scale'],['raw'])]
    else:nodes.append(h.make_node('Identity',['constant'],['raw']))
    graph=h.make_graph(nodes,'synthetic-head',[h.make_tensor_value_info('images',T.FLOAT,[1,3,size,size])],
                       [h.make_tensor_value_info('raw',T.FLOAT,list(rows.shape))])
    model=h.make_model(graph,opset_imports=[h.make_opsetid('',18)],ir_version=10)
    if names is not None:
        prop=model.metadata_props.add();prop.key='names';prop.value=str(names)
    onnx.save(model,str(path));return path


def run(path,value=0.):
    s=ort.InferenceSession(str(path),providers=['CPUExecutionProvider'])
    return s.run(None,{'images':np.full((1,3,320,320),value,np.float32)})[0]


def test_actual_graph_clips_suppresses_duplicates_and_keeps_scores(tmp_path):
    source=raw_head(tmp_path/'raw.onnx',[[[-4,10,80,90,.97,0],[-3,11,79,91,.96,0],
        [200,200,300,300,.93,0],[10,10,10,30,.98,0],[30,30,50,50,.5,0],[0,0,0,0,0,0]]])
    dest=tmp_path/'safe.onnx';adapt_export(source,dest)
    rows=run(dest)
    assert rows.shape==(1,2,6)
    assert rows[0,0,:4]==pytest.approx([0,10,80,90])
    assert rows[0,:,4]==pytest.approx([.97,.93])
    verify_export(dest)


@pytest.mark.parametrize('row',[[10,20,30,40,np.nan,0],[10,20,30,40,1.1,0],
    [10,20,30,40,.97,1],[.1,.2,.8,.9,.97,0]])
def test_adapter_does_not_hide_invalid_heads(tmp_path,row):
    source=raw_head(tmp_path/'raw.onnx',[[row]])
    with pytest.raises(ValueError):adapt_export(source,tmp_path/'safe.onnx')


def test_runtime_overflow_abstains_instead_of_manufacturing_clipped_box(tmp_path):
    source=raw_head(tmp_path/'raw.onnx',[[[10,20,80,90,.97,0]]],overflow=True)
    dest=tmp_path/'safe.onnx';adapt_export(source,dest)
    assert run(dest,0.).shape==(1,1,6)
    assert run(dest,1.).shape==(1,0,6)


def test_generic_person_export_cannot_be_promoted_as_enemy(tmp_path):
    source=raw_head(tmp_path/'raw.onnx',[[[10,20,80,90,.97,0]]],names={0:'person'})
    with pytest.raises(ValueError,match='enemy-class'):
        finalize_export(source,tmp_path/'candidate',['a'*64,'b'*64])
    assert not (tmp_path/'candidate/vision-apex.json').exists()


def test_finalize_rejects_export_of_a_different_checkpoint_head(tmp_path):
    source=raw_head(tmp_path/'raw.onnx',[[[10,20,80,90,.97,0]]],names={0:'enemy'})
    model=onnx.load(str(source));prop=model.metadata_props.add();prop.key='end2end';prop.value='True';onnx.save(model,str(source))
    with pytest.raises(ValueError,match='changed checkpoint detection head'):
        finalize_export(source,tmp_path/'candidate',['a'*64,'b'*64],expected_end2end=False)
    assert not (tmp_path/'candidate').exists()


def test_multiple_training_sources_keep_validation_role_and_heldout_exclusion(tmp_path):
    from scripts.gamecoach_vision_validate import verify_heldout_isolation
    source=raw_head(tmp_path/'raw.onnx',[[[10,20,80,90,.97,0]]],names={0:'enemy'})
    output=tmp_path/'candidate';output.mkdir()
    finalize_export(source,output,['a'*64,'b'*64,'c'*64])
    meta=json.loads((output/'vision-apex.json').read_text())
    assert meta['training_clip_sha256s']==['a'*64,'b'*64]
    assert meta['development_validation_clip_sha256']=='c'*64
    assert meta['reviewed'] is False and meta['min_confidence']==.92
    for digest in meta['training_source_clip_sha256']:
        with pytest.raises(ValueError,match='overlaps'):
            verify_heldout_isolation(meta,{'source_clip_sha256':digest})
    verify_heldout_isolation(meta,{'source_clip_sha256':'d'*64})


def test_adapter_preserves_existing_artifacts(tmp_path):
    dest=tmp_path/'safe.onnx';dest.write_bytes(b'preserve')
    with pytest.raises(FileExistsError):adapt_export(tmp_path/'missing.onnx',dest)
    assert dest.read_bytes()==b'preserve'


def test_higher_resolution_export_retains_pixel_coordinates(tmp_path):
    source=raw_head(tmp_path/'raw.onnx',[[[400,300,660,620,.97,0]]],size=640)
    dest=tmp_path/'safe.onnx';adapt_export(source,dest,640)
    session=ort.InferenceSession(str(dest),providers=['CPUExecutionProvider'])
    rows=session.run(None,{'images':np.zeros((1,3,640,640),np.float32)})[0]
    assert rows[0,0]==pytest.approx([400,300,640,620,.97,0])
    verify_export(dest,640)
    with pytest.raises(ValueError,match='input mismatch'):
        verify_export(dest,320)
