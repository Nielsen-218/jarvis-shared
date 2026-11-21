"""Bounded pixel clipping and real NMS for a one-class local YOLO export.

Raw artifacts remain intact. This never certifies recognition accuracy.
"""
from pathlib import Path


def adapt_export(source:Path,destination:Path,size:int=320)->None:
    import numpy as np
    import onnx
    from onnx import helper as h,numpy_helper as nh,TensorProto as T
    import onnxruntime as ort
    if destination.exists():raise FileExistsError('Preserve existing postprocessed export')
    session=ort.InferenceSession(str(source),providers=['CPUExecutionProvider'])
    inputs=session.get_inputs()
    if len(inputs)!=1 or inputs[0].shape!=[1,3,size,size] or inputs[0].type!='tensor(float)':
        raise ValueError('Raw model input contract mismatch')
    result=session.run(None,{inputs[0].name:np.zeros((1,3,size,size),np.float32)})
    if len(result)!=1:raise ValueError('Expected one raw detection output')
    rows=np.asarray(result[0])
    if rows.ndim!=3 or rows.shape[0]!=1 or rows.shape[2]!=6 or rows.shape[1]>2000:
        raise ValueError('Raw head must be pixel xyxy/score/class [1,N,6]')
    if not np.isfinite(rows).all():raise ValueError('Nonfinite raw export')
    meaningful=rows[0][np.any(rows[0]!=0,axis=1)]
    if len(meaningful):
        if np.any(meaningful[:,4]<0) or np.any(meaningful[:,4]>1) or np.any(meaningful[:,5]!=0):
            raise ValueError('Raw score or one-class enemy identity mismatch')
        high=meaningful[meaningful[:,4]>=.85,:4]
        if len(high) and np.any(np.all((high>=0)&(high<=1),axis=1)):
            raise ValueError('Normalized coordinates are not the pixel contract')
    model=onnx.load(str(source))
    if next((x.version for x in model.opset_import if x.domain==''),0)<13:
        raise ValueError('Postprocessing requires ONNX opset >=13')
    graph=model.graph;raw=graph.output[0].name
    prefix='nolagin_post_'
    if any(x.name.startswith(prefix) for x in graph.initializer):raise ValueError('Already adapted model')
    def tensor(name,value,dtype):
        name=prefix+name;graph.initializer.append(nh.from_array(np.asarray(value,dtype=dtype),name));return name
    def node(op,inputs,name,**attrs):
        name=prefix+name;graph.node.append(h.make_node(op,inputs,[name],name=name,**attrs));return name
    i64=np.int64;f32=np.float32
    zero=tensor('zero',0.,f32);limit=tensor('limit',float(size),f32)
    coords=node('Gather',[raw,tensor('coord_ids',[0,1,2,3],i64)],'coords',axis=2)
    clipped=node('Clip',[coords,zero,limit],'clipped')
    x1=node('Gather',[clipped,tensor('x1_id',[0],i64)],'x1',axis=2)
    y1=node('Gather',[clipped,tensor('y1_id',[1],i64)],'y1',axis=2)
    x2=node('Gather',[clipped,tensor('x2_id',[2],i64)],'x2',axis=2)
    y2=node('Gather',[clipped,tensor('y2_id',[3],i64)],'y2',axis=2)
    valid=node('And',[node('Greater',[x2,x1],'positive_width'),node('Greater',[y2,y1],'positive_height')],'positive_area')
    one=tensor('one',1.,f32)
    valid=node('And',[valid,node('Or',[node('Greater',[x2,one],'pixel_x'),node('Greater',[y2,one],'pixel_y')],'pixel_extent')],'valid')
    nonfinite=node('Or',[node('IsNaN',[raw],'nan'),node('IsInf',[raw],'inf')],'nonfinite')
    nonfinite=node('Cast',[nonfinite],'nonfinite_int',to=T.INT64)
    count=node('ReduceSum',[nonfinite,tensor('row_axis',[2],i64)],'nonfinite_count',keepdims=1)
    finite=node('Equal',[count,tensor('zero_int',0,i64)],'finite')
    valid=node('And',[valid,finite],'valid_finite')
    score=node('Gather',[raw,tensor('score_id',[4],i64)],'score',axis=2)
    label=node('Gather',[raw,tensor('label_id',[5],i64)],'label',axis=2)
    masked=node('Where',[valid,score,zero],'valid_scores')
    scores=node('Transpose',[masked],'nms_scores',perm=[0,2,1])
    yxyx=node('Gather',[clipped,tensor('yx_order',[1,0,3,2],i64)],'nms_boxes',axis=2)
    selected=node('NonMaxSuppression',[yxyx,scores,tensor('max_output',300,i64),
                  tensor('iou',.499,f32),tensor('score_floor',.85,f32)],'selected',center_point_box=0)
    indices=node('Gather',[selected,tensor('selected_column',[2],i64)],'selected_column_values',axis=1)
    indices=node('Squeeze',[indices,tensor('squeeze_axis',[1],i64)],'indices')
    final_rows=node('Concat',[clipped,score,label],'rows',axis=2)
    final=node('Gather',[final_rows,indices],'detections',axis=1)
    del graph.output[:]
    graph.output.append(h.make_tensor_value_info(final,T.FLOAT,[1,'detections',6]))
    prop=model.metadata_props.add();prop.key='nolagin_postprocess';prop.value='clip pixel xyxy; discard degenerate; NMS IoU=.499, score floor=.85; scores unchanged'
    onnx.checker.check_model(model)
    destination.parent.mkdir(parents=True,exist_ok=True)
    onnx.save(model,str(destination))
