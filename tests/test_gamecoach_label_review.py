"""Local proposals are drafts; only explicit visual review creates truth."""
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from PIL import Image
from scripts import gamecoach_dataset as ds


def catalog(tmp_path,split='train'):
    Image.new('RGB',(32,24),(20,40,60)).save(tmp_path/'frame.png')
    data={'game':'apex','split':split,'source_clip_sha256':'a'*64,
          'cases':[{'image':'frame.png','boxes':[],'human_reviewed':False,'clip_time_s':1.0}]}
    path=tmp_path/'dataset.json';path.write_text(json.dumps(data))
    return path


def model():
    coordinates=SimpleNamespace(cpu=lambda:SimpleNamespace(tolist=lambda:[[2,3,7,11]]))
    return SimpleNamespace(names={0:'person'},predict=lambda **kw:[SimpleNamespace(boxes=SimpleNamespace(xyxy=coordinates))])


def test_person_proposal_cannot_create_enemy_label(tmp_path):
    path=catalog(tmp_path);weights=tmp_path/'local.pt';weights.write_bytes(b'local-test')
    report=ds.propose(path,weights,device='cpu',model=model())
    row=json.loads(path.read_text())['cases'][0]
    assert report['candidate_regions']==1 and report['human_labels_created']==0
    assert row['candidate_boxes']==[[2,3,7,11]]
    assert row['boxes']==[] and row['human_reviewed'] is False
    assert 'not enemy' in row['candidate_provenance']['class']


def test_drafts_never_touch_heldout(tmp_path):
    path=catalog(tmp_path,'heldout');before=path.read_bytes()
    with pytest.raises(ValueError,match='never heldout'):ds.propose(path,tmp_path/'missing.pt')
    assert path.read_bytes()==before


def test_human_labels_survive_proposals(tmp_path):
    path=catalog(tmp_path);weights=tmp_path/'local.pt';weights.write_bytes(b'test')
    data=json.loads(path.read_text());data['cases'][0].update(boxes=[[2,3,7,11]],human_reviewed=True)
    path.write_text(json.dumps(data));before=json.loads(path.read_text())
    assert ds.propose(path,weights,model=model())['frames_proposed']==0
    assert json.loads(path.read_text())==before


def test_proposals_abort_if_a_human_edits_during_inference(tmp_path):
    path=catalog(tmp_path);weights=tmp_path/'local.pt';weights.write_bytes(b'test')
    original=model();predict=original.predict
    def human_edit(**kwargs):
        data=json.loads(path.read_text());data['cases'][0].update(boxes=[[1,1,8,12]],human_reviewed=True)
        path.write_text(json.dumps(data))
        return predict(**kwargs)
    original.predict=human_edit
    with pytest.raises(RuntimeError,match='preserving'):ds.propose(path,weights,model=original)
    assert json.loads(path.read_text())['cases'][0]['human_reviewed'] is True


def test_proposal_cli_pauses_before_any_inference_when_apex_runs(monkeypatch,tmp_path,capsys):
    from scripts import nolagin
    monkeypatch.setattr(nolagin,'_game_running',lambda _:True)
    assert nolagin.main(['propose','--split','train','--weights',str(tmp_path/'none.pt')])==2
    assert 'Close the game' in capsys.readouterr().out


@pytest.mark.parametrize('key',['','s','q','r','\x1b','a'])
def test_empty_draft_never_becomes_reviewed_negative_implicitly(key):
    assert ds.reviewed_proposal([],key) is None


def test_explicit_positive_and_negative_decisions():
    boxes=[[2,3,7,11]]
    assert ds.reviewed_proposal(boxes,'A') is None
    assert ds.reviewed_proposal(boxes,'N')==[]
    assert ds.reviewed_proposal([],'N')==[]


def fake_gui(monkeypatch,keys,rois=()):
    import sys,numpy as np
    events=iter(keys)
    gui=SimpleNamespace(WINDOW_NORMAL=0,COLOR_RGB2BGR=0,BORDER_CONSTANT=0,FONT_HERSHEY_SIMPLEX=0,
        namedWindow=lambda *a:None,resizeWindow=lambda *a:None,rectangle=lambda *a:None,
        cvtColor=lambda a,*_:a.copy(),copyMakeBorder=lambda a,*args,**kw:a,
        resize=lambda a,size:np.zeros((size[1],size[0],3),dtype=np.uint8),
        setMouseCallback=lambda *a:None,getWindowProperty=lambda *a:1,WND_PROP_VISIBLE=0,error=RuntimeError,
        putText=lambda *a:None,imshow=lambda *a:None,waitKey=lambda _:ord(next(events)),
        selectROIs=lambda *a,**kw:np.array(rois),destroyWindow=lambda *a:None,destroyAllWindows=lambda:None)
    monkeypatch.setitem(sys.modules,'cv2',gui)


def test_roi_review_keeps_small_partial_enemy_and_saves_atomically(tmp_path,monkeypatch):
    path=catalog(tmp_path)
    fake_gui(monkeypatch,['r'],[[2,3,2,4]])
    monkeypatch.setattr(ds,'edit_enemy_boxes',lambda *a,**kw:[[2,3,4,7]])
    result=ds.label(path)
    row=json.loads(path.read_text())['cases'][0]
    assert result['positive']==1 and row['boxes']==[[2,3,4,7]] and row['human_reviewed'] is True


def test_cancelled_roi_selection_then_quit_stays_unreviewed(tmp_path,monkeypatch):
    path=catalog(tmp_path)
    fake_gui(monkeypatch,['r','q'])
    monkeypatch.setattr(ds,'edit_enemy_boxes',lambda *a,**kw:None)
    assert ds.label(path)['human_reviewed']==0
    assert json.loads(path.read_text())['cases'][0]['human_reviewed'] is False


def test_heldout_ignores_all_candidate_boxes(tmp_path,monkeypatch):
    path=catalog(tmp_path,'heldout')
    data=json.loads(path.read_text());data['cases'][0]['candidate_boxes']=[[2,3,7,11]]
    path.write_text(json.dumps(data))
    # A must not accept leaked drafts; only the later explicit N reviews it.
    fake_gui(monkeypatch,['a','n'])
    assert ds.label(path)['negative']==1
    assert json.loads(path.read_text())['cases'][0]['boxes']==[]


def test_local_loader_disables_cloud_and_auto_downloads(tmp_path,monkeypatch):
    import sys
    from scripts.gamecoach_apex_train import load_local_yolo
    settings={k:True for k in ('sync','hub','wandb','clearml')}
    calls=[]
    monkeypatch.setitem(sys.modules,'ultralytics',SimpleNamespace(settings=settings,YOLO=lambda p:calls.append(p) or 'local'))
    for key in ('YOLO_OFFLINE','YOLO_AUTOINSTALL','WANDB_MODE','YOLO_CONFIG_DIR'):monkeypatch.setenv(key,'before')
    weights=tmp_path/'local.pt';weights.write_bytes(b'test')
    assert load_local_yolo(weights,tmp_path/'settings')=='local'
    assert calls==[str(weights.resolve())] and not any(settings.values())
    import os
    assert os.environ['YOLO_OFFLINE']=='true' and os.environ['YOLO_AUTOINSTALL']=='False'


def test_enter_saves_once_and_space_cannot_duplicate_boxes():
    from scripts.gamecoach_label_editor import key_action,BoxView
    state=BoxView(1920,1080,[[949,488,961,508]]*16)
    assert len(state.boxes)==1
    assert key_action(13)=='save' and key_action(10)=='save'
    assert key_action(32) is None
    assert ds.reviewed_proposal([[2,3,7,11]]*16,'a') is None


def test_zoom_round_trip_keeps_original_annotation_coordinates():
    from scripts.gamecoach_label_editor import BoxView
    view=BoxView(1920,1080)
    view.zoom(4,(960,540))
    for x,y in [(950,530),(970,550)]:
        restored=view.source(*view.screen(x,y))
        assert abs(restored[0]-x)<1 and abs(restored[1]-y)<1
    view.add([949,488,961,508]);view.add([960,530,980,560])
    assert len(view.boxes)==2


def test_crop_suggestions_keep_original_pixels_and_never_touch_heldout(tmp_path):
    import numpy as np
    frame=np.zeros((100,100,3),dtype=np.uint8)
    boxes=ds.crop_proposals(frame,(20,30,50,60),tmp_path/'fake.pt',split='train',device='cpu',model=model())
    assert boxes==[[22,33,27,41]]
    with pytest.raises(ValueError,match='heldout'):
        ds.crop_proposals(frame,(20,30,50,60),tmp_path/'fake.pt',split='heldout',model=model())


def test_actual_editor_enter_finishes_once_without_selectrois(tmp_path,monkeypatch):
    import numpy as np
    from scripts.gamecoach_label_editor import edit_boxes
    fake_gui(monkeypatch,['\r','\x1b'])
    assert edit_boxes(np.zeros((32,32,3),dtype=np.uint8),[[2,3,7,11]]*16) is None


def test_empty_enter_then_escape_does_not_save_negative(tmp_path,monkeypatch):
    import numpy as np
    from scripts.gamecoach_label_editor import edit_boxes
    fake_gui(monkeypatch,['\r','\x1b'])
    assert edit_boxes(np.zeros((32,32,3),dtype=np.uint8),[]) is None


def test_visible_save_button_finishes_the_editor(monkeypatch):
    import sys,numpy as np
    from scripts.gamecoach_label_editor import edit_boxes,BoxView
    fake_gui(monkeypatch,[])
    gui=sys.modules['cv2'];callbacks={}
    gui.EVENT_LBUTTONUP=1;gui.EVENT_LBUTTONDOWN=2;gui.EVENT_RBUTTONDOWN=3
    gui.EVENT_MOUSEMOVE=4;gui.EVENT_RBUTTONUP=5;gui.EVENT_MOUSEWHEEL=6
    gui.setMouseCallback=lambda title,callback:callbacks.update(mouse=callback)
    x,y=BoxView(32,32).screen(4,7)
    def click_then_save(_):
        callbacks['mouse'](2,x,y,0,None)
        callbacks['mouse'](1,x,y,0,None)
        callbacks['mouse'](1,40,20,0,None)
        return -1
    gui.waitKey=click_then_save
    assert edit_boxes(np.zeros((32,32,3),dtype=np.uint8),[[2,3,7,11],[20,20,29,29]])==[[2,3,7,11]]


def test_a_opens_individual_review_and_cancel_cannot_accept_people(tmp_path,monkeypatch):
    path=catalog(tmp_path);data=json.loads(path.read_text())
    data['cases'][0]['candidate_boxes']=[[2,3,7,11]];path.write_text(json.dumps(data))
    fake_gui(monkeypatch,['a','q']);seen=[]
    monkeypatch.setattr(ds,'edit_enemy_boxes',lambda frame,boxes,**kwargs:seen.append(boxes) or None)
    assert ds.label(path)['human_reviewed']==0
    assert seen==[[[2,3,7,11]]]
    assert json.loads(path.read_text())['cases'][0]['boxes']==[]


def test_exclude_replay_is_resumable_and_never_becomes_negative(tmp_path,monkeypatch):
    path=catalog(tmp_path);fake_gui(monkeypatch,['x'])
    report=ds.label(path);row=json.loads(path.read_text())['cases'][0]
    assert report['excluded']==1 and report['negative']==0
    assert row['excluded'] is True and row['human_reviewed'] is False
    fake_gui(monkeypatch,[])
    assert ds.label(path)['excluded']==1


def test_only_clicked_or_drawn_boxes_are_selected_and_suggestions_never_confirm():
    from scripts.gamecoach_label_editor import BoxView
    state=BoxView(1920,1080,[[900,400,940,490],[50,890,150,970]])
    assert state.selected()==[]
    state.toggle((920,440))
    assert state.selected()==[[900,400,940,490]]
    state.add([901,401,939,489],confirmed=False)
    assert len(state.boxes)==2
    state.remove(state.boxes[0]);state.add([900,400,940,490],confirmed=False)
    assert state.selected()==[]
    state.add([1000,400,1030,450])
    assert state.selected()==[[1000,400,1030,450]]
