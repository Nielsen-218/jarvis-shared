"""V5: game-specific model contracts, bounded inference, review repair, player learning."""
import hashlib
import json
import time
from pathlib import Path

import numpy as np
import pytest

from core.gamecoach_enemy_detector import (load_config, decode, preprocess,
                                             GameEnemyDetector,EnemyCue)
from core.gamecoach_enemy_filter import EnemyEvidenceFilter
from core.gamecoach_vision_worker import VisionWorker
from core.gamecoach_feedback import category_preferences,combine_training_weights
from core.gamecoach_review_parser import parse_model_json,sanitize_review
from core.gamecoach_tactics import TacticalCoach,Evidence

@pytest.mark.parametrize('flag',['excluded','needs_recheck'])
def test_certification_rejects_disputed_or_excluded_frames_before_inference(tmp_path,flag):
    from types import SimpleNamespace
    from scripts.gamecoach_vision_validate import evaluate
    rows=[{'image':f'{i}.png','boxes':[],'human_reviewed':True} for i in range(200)]
    rows[-1][flag]=True
    detector=SimpleNamespace(config=SimpleNamespace(game='apex'))
    with pytest.raises(ValueError,match='Excluded or disputed'):
        evaluate(detector,{'game':'apex','split':'heldout','cases':rows},tmp_path)


def onnx_fixture(tmp_path, game="apex", precision=.99, cases=210):
    p=tmp_path/"real_model.onnx"
    p.write_bytes(b"fake model for adapter tests only")
    manifest=tmp_path/"vision-apex.json"
    data={"game":game,"model":p.name,"sha256":hashlib.sha256(p.read_bytes()).hexdigest(),
          "contract":"xyxy-nms-v1","reviewed":True,"classes":{"enemy":1},
          "validation_cases":cases,"measured_precision":precision,
          "measured_recall":.90,"validation_dataset_sha256":"a"*64,
          "input_size":320,"min_confidence":.92}
    manifest.write_text(json.dumps(data))
    return manifest,data

def test_model_requires_real_human_review_and_checksum(tmp_path):
    p,data=onnx_fixture(tmp_path)
    c=load_config(p,"apex")
    assert c.validated and c.enemy_class==1
    with pytest.raises(ValueError):load_config(p,"cod")
    data["validation_cases"]=100
    p.write_text(json.dumps(data))
    with pytest.raises(ValueError):load_config(p,"apex")
    data["validation_cases"]=300
    data["measured_precision"]=.4
    p.write_text(json.dumps(data))
    with pytest.raises(ValueError):load_config(p,"apex")
    data["measured_precision"]=.99
    data["model"]="../real_model.onnx"
    p.write_text(json.dumps(data))
    with pytest.raises(ValueError):load_config(p,"apex")

def test_checksum_mismatch_disables_model(tmp_path):
    p,_=onnx_fixture(tmp_path)
    (tmp_path/"real_model.onnx").write_bytes(b"changed weights")
    with pytest.raises(ValueError):load_config(p,"apex")

def test_untrained_coco_person_model_cannot_substitute(tmp_path):
    p,data=onnx_fixture(tmp_path)
    data["classes"]={"person":1}
    p.write_text(json.dumps(data))
    with pytest.raises(ValueError):load_config(p,"apex")

def test_preprocess_shape_dtype_and_image_geometry():
    frame=np.full((100,200,3),128,dtype=np.uint8)
    x,(scale,xoff,yoff)=preprocess(frame,320)
    assert x.shape==(1,3,320,320) and x.dtype==np.float32
    assert scale==1.6 and yoff==80 and xoff==0
    with pytest.raises(ValueError):
        preprocess(np.zeros((10,10,3),np.uint8),320)


def test_model_preprocessing_is_explicit_and_legacy_models_keep_pixels(tmp_path):
    p,data=onnx_fixture(tmp_path)
    assert load_config(p,'apex').preprocessing=='pillow-bilinear'
    data['preprocessing']='opencv-linear';p.write_text(json.dumps(data))
    assert load_config(p,'apex').preprocessing=='opencv-linear'
    data['preprocessing']=['unknown'];p.write_text(json.dumps(data))
    with pytest.raises(ValueError,match='preprocessing'):load_config(p,'apex')


def test_opencv_letterbox_preserves_rgb_and_avoids_pillow_antialias_difference():
    cv2=pytest.importorskip('cv2')
    frame=np.zeros((57,103,3),np.uint8)
    frame[::2,::2,0]=255;frame[1::2,1::2,2]=180;frame[:,:,1]=40
    tensor,(scale,x,y)=preprocess(frame,32,'opencv-linear')
    w,h=round(103*scale),round(57*scale)
    expected=np.full((32,32,3),114,np.uint8)
    expected[y:y+h,x:x+w]=cv2.resize(frame,(w,h),interpolation=cv2.INTER_LINEAR)
    assert np.array_equal(tensor[0].transpose(1,2,0),expected.astype(np.float32)/255)
    legacy,_=preprocess(frame,32)
    assert not np.array_equal(tensor,legacy)
    with pytest.raises(ValueError,match='preprocessing'):preprocess(frame,32,'unknown')


def test_letterbox_matches_actual_ultralytics_training_transform():
    pytest.importorskip('ultralytics')
    from ultralytics.data.augment import LetterBox
    frame=np.random.default_rng(96).integers(0,256,(73,117,3),dtype=np.uint8)
    tensor,_=preprocess(frame,64,'opencv-linear')
    expected=LetterBox(new_shape=(64,64),auto=False)(image=frame)
    assert np.array_equal(tensor[0].transpose(1,2,0),expected.astype(np.float32)/255)

def test_parser_only_accepts_valid_visible_bboxes_and_deduplicates():
    rows=np.array([[20,85,100,210,.98,1],
                   [21,86,101,211,.97,1],
                   [110,90,180,210,.99,0],
                   [105,95,140,130,.4,1]],np.float32)
    boxes=decode(rows,(1.6,0,80),(100,200),1,.92)
    assert len(boxes)==1
    assert boxes[0][4] == pytest.approx(.98,rel=1e-6)

def test_parser_rejects_nonfinite_and_wrong_output():
    assert decode(np.array([[np.nan,1,2,3,.99,1]],np.float32),(1,0,0),(100,100),1,.92)==[]
    assert decode(np.zeros((3,5)),(1,0,0),(100,100),1,.92)==[]
    assert decode(np.zeros((2001,6)),(1,0,0),(100,100),1,.92)==[]

class Session:
    class Input:
        name="images"
        shape=[1,3,320,320]
        type="tensor(float)"
    def __init__(self,boxes):self.boxes=boxes
    def get_inputs(self):return [self.Input()]
    def run(self,output_names,feed):
        assert feed["images"].shape==(1,3,320,320)
        return [np.asarray(self.boxes,dtype=np.float32)]

def test_dummy_onnx_contract_integration_infers_visible_enemy_only(tmp_path):
    p,_=onnx_fixture(tmp_path)
    model=GameEnemyDetector(load_config(p,"apex"),
        Session([[20,85,100,200,.98,1],[120,85,200,200,.99,1]]))
    now=time.monotonic()
    cue=model.infer(np.full((100,200,3),128,np.uint8),now)
    assert cue is not None and cue.visible_count==2
    assert model.summary()["recognized"]==1
    assert "enemy" in cue.proof


def test_predict_boxes_counts_detection_and_infer_does_not_double_count(tmp_path):
    p,_=onnx_fixture(tmp_path)
    model=GameEnemyDetector(load_config(p,'apex'),Session([[20,85,100,200,.98,1]]))
    frame=np.full((100,200,3),128,np.uint8)
    assert model.predict_boxes(frame,time.monotonic())
    assert model.summary()['recognized']==1
    assert model.infer(frame,time.monotonic()) is not None
    assert model.summary()['recognized']==2

def test_model_failure_never_produces_fake_enemies(tmp_path):
    p,_=onnx_fixture(tmp_path)
    model=GameEnemyDetector(load_config(p,"apex"),Session([[20,85,100,200,.2,1]]))
    now=time.monotonic()
    assert model.infer(np.full((100,200,3),128,np.uint8),now) is None


@pytest.mark.parametrize("box",[
    [20,85,100,200,1.1,1], [20,85,400,200,.98,1],
    [.1,.2,.5,.9,.98,1], [20,85,100,200,.98,.5],
])
def test_live_invalid_predictions_are_unknown_not_certification_negatives(tmp_path,box):
    p,_=onnx_fixture(tmp_path)
    model=GameEnemyDetector(load_config(p,"apex"),Session([box]))
    assert model.predict_boxes(np.zeros((100,200,3),np.uint8),1000.,lambda:1000.) is None
    assert model.stats["errors"]==1


def test_future_capture_timestamp_cannot_become_fresh_enemy_evidence(tmp_path):
    p,_=onnx_fixture(tmp_path)
    model=GameEnemyDetector(load_config(p,"apex"),Session([[20,85,100,200,.98,1]]))
    assert model.predict_boxes(np.zeros((100,200,3),np.uint8),1001.,lambda:1000.) is None

def test_two_distinct_frames_required_and_count_conservative():
    f=EnemyEvidenceFilter()
    assert f.observe(EnemyCue(3,1000.,.99,"valid 3 boxes"),1000.) is None
    val=f.observe(EnemyCue(1,1000.4,.95,"one now"),1000.4)
    assert val is not None and val[0]==1
    assert f.stats.confirmed==1

def test_far_apart_or_stale_enemies_do_not_confirm():
    f=EnemyEvidenceFilter()
    assert f.observe(EnemyCue(2,1000.,.99,"visible"),1000.) is None
    assert f.observe(EnemyCue(2,1001.,.99,"visible"),1001.) is None
    assert f.observe(EnemyCue(2,1001.1,.99,"visible"),1002.) is None

def test_async_worker_does_not_build_inference_queue():
    class Slow:
        def infer(self,frame,captured_at,clock):
            time.sleep(.08)
            return EnemyCue(1,captured_at,.96,"fake trained test")
    worker=VisionWorker(Slow(),interval_s=.1)
    worker.start()
    try:
        frame=np.zeros((32,32,3),np.uint8)
        assert worker.offer(frame,time.monotonic())
        # Busy worker or pending frame MUST reject more work.
        assert not worker.offer(frame,time.monotonic())
        deadline=time.monotonic()+2.
        found=None
        while time.monotonic()<deadline and found is None:
            found=worker.poll()
            time.sleep(.01)
        assert found is not None and found.visible_count==1
        assert worker.stats.accepted==1
        assert worker.summary()["one_frame_slot"]
    finally:
        assert worker.stop(timeout=1.)

def test_worker_discards_old_frames_without_calling_inference():
    class Spy:
        def __init__(self):self.n=0
        def infer(self,frame,captured_at,clock):self.n+=1
    spy=Spy()
    worker=VisionWorker(spy)
    worker.start()
    try:
        assert not worker.offer(np.zeros((32,32,3),np.uint8),time.monotonic()-5)
        assert spy.n==0
    finally:worker.stop(1.)

def test_bad_clip_json_parser_accepts_plain_and_fenced_json():
    payload=json.dumps({"summary":"Good cover","weak_points":[]})
    assert parse_model_json(payload)["summary"]=="Good cover"
    fence=chr(96)*3
    assert parse_model_json(fence+"json\n"+payload+"\n"+fence)["summary"]=="Good cover"
    assert parse_model_json("Here is my review: "+payload)["summary"]=="Good cover"
    with pytest.raises(ValueError):parse_model_json("none")

def test_clip_review_discards_invented_timestamps_and_invalid_items():
    raw=json.dumps({"summary":"Watch cover","strengths":["Spacing"],
                    "weak_points":[
                        {"time":"0:10","category":"positioning","what":"Visible open peek","fix":"Play cover"},
                        {"time":"9:59","category":"aim","what":"Imagined fight","fix":"Do better"},
                        {"time":"0:20","category":"bad","what":"nonsense","fix":"none"}],
                    "drills":["Cover practice"]})
    result=sanitize_review(raw,["0:10","0:20"])
    assert len(result["weak_points"])==1
    assert result["weak_points"][0]["time"]=="0:10"

def test_feedback_requires_repeated_explicit_owner_ratings():
    rows=[{"source":"owner_confirmed","game":"apex","kind":"take_height",
           "helpful":False} for _ in range(3)]
    bias,samples=category_preferences("apex",rows)
    assert samples["positioning"]==3 and bias["positioning"]==-0.4
    assert category_preferences("cod",rows)==({},{})

def test_user_preferences_cannot_override_emergency_priority():
    learned=combine_training_weights({"positioning":.4,"teamplay":.2},
                                      {"positioning":-.4,"teamplay":.4})
    assert learned=={"positioning":0.,"teamplay":.4}
    t=1000.
    evidence={key:Evidence(True,t,.99,"pixels","visible frame") for key in
              ("health_low","exposed","squad_split","enemy_visible")}
    decision=TacticalCoach("apex",learning_weights={"teamplay":.4,"positioning":-.4}).choose(evidence,t)
    assert decision.kind=="break_sightline"

def test_service_does_not_start_invalid_enemy_vision(tmp_path):
    from core.gamecoach_service import CoachService
    service=CoachService(root=tmp_path,platform="win32")
    profile=service.profile_file("apex","low_health")
    profile.write_text("{}")
    answer=service.start("apex",tactical_vision=True)
    assert "Not enabled" in answer
    assert service.status()=="Gaming coach is off."


def test_candidate_model_allowed_for_offline_validation_not_live(tmp_path):
    p,data=onnx_fixture(tmp_path)
    data["reviewed"]=False
    data["validation_cases"]=0
    data["measured_precision"]=0.
    data["measured_recall"]=0.
    data.pop("validation_dataset_sha256")
    p.write_text(json.dumps(data))
    candidate=load_config(p,"apex",allow_candidate=True)
    assert candidate.validated is False
    with pytest.raises(ValueError):
        load_config(p,"apex")

def test_real_annotation_evaluator_scores_precision_recall_and_negatives(tmp_path):
    from scripts.gamecoach_vision_validate import evaluate
    from PIL import Image
    from types import SimpleNamespace
    root=tmp_path/"heldout"
    root.mkdir()
    cases=[]
    for i in range(200):
        positive=i<100
        image=np.zeros((32,32,3),np.uint8)
        image[0,1]=[i,0,0]  # Each held-out frame must contain distinct pixels.
        if positive:image[0,0]=[255,0,0]
        name=f"frame{i:03}.png"
        Image.fromarray(image).save(root/name)
        cases.append({"image":name,"boxes":[[8,5,27,29]] if positive else [],
                      "human_reviewed":True})
    class VerifiedSimulation:
        config=SimpleNamespace(game="apex")
        def __init__(self,inject_fps=0):self.inject_fps=inject_fps;self.count=0
        def predict_boxes(self,frame,captured_at):
            self.count+=1
            if int(frame[0,0,0])==255 or (
                self.inject_fps and self.count in (101,102,103)):
                return [(8,5,27,29,.99)]
            return []
    catalog={"game":"apex","split":"heldout","cases":cases}
    ok=evaluate(VerifiedSimulation(),catalog,root)
    assert ok["passed"] and ok["true_positives"]==100 and ok["false_positives"]==0
    bad=evaluate(VerifiedSimulation(inject_fps=3),catalog,root)
    assert not bad["passed"] and bad["false_positives"]==3
    assert bad["precision"]<.98
    unreviewed=json.loads(json.dumps(catalog))
    unreviewed["cases"][1]["human_reviewed"]=False
    with pytest.raises(ValueError):evaluate(VerifiedSimulation(),unreviewed,root)
    (root/"frame001.png").write_bytes((root/"frame000.png").read_bytes())
    with pytest.raises(ValueError,match="Duplicate held-out frame pixels"):
        evaluate(VerifiedSimulation(),catalog,root)

def test_real_annotation_dataset_rejects_train_split(tmp_path):
    from scripts.gamecoach_vision_validate import evaluate
    from types import SimpleNamespace
    class Fake:
        config=SimpleNamespace(game="apex")
    with pytest.raises(ValueError):
        evaluate(Fake(),{"game":"apex","split":"train","cases":[]},tmp_path)


def test_low_hp_plus_actual_enemy_changes_callout_without_inventing_cover():
    from core.gamecoach import PRIORITY,MESSAGES
    t=1000.
    cues={"health_low":Evidence(True,t,.97,"pixels","low health bar"),
          "enemy_visible":Evidence(True,t,.98,"pixels","trained enemy visible")}
    decision=TacticalCoach("apex").choose(cues,t)
    assert decision.kind=="contested_low_health"
    assert "Break contact" in decision.text
    assert decision.kind in PRIORITY and MESSAGES[decision.kind]==decision.text
    assert "cover" not in decision.text.lower()

def test_contested_shields_and_ammo_use_conservative_action():
    t=1000.
    inputs={"shields_low":Evidence(True,t,.99,"pixels","confirmed bar"),
            "enemy_visible":Evidence(True,t,.99,"pixels","visible model")}
    assert TacticalCoach("apex").choose(inputs,t).kind=="contested_low_shields"
    del inputs["shields_low"]
    inputs["ammo_low"]=Evidence(True,t,.99,"pixels","confirmed ammo bar")
    assert TacticalCoach("cod").choose(inputs,t).kind=="contested_low_ammo"

def test_open_revive_remains_higher_priority_than_threat_only():
    t=1000.
    cues={"teammate_down":Evidence(True,t,.99,"pixels","teammate badge"),
          "enemy_visible":Evidence(True,t,.99,"pixels","visible enemy"),
          "exposed":Evidence(True,t,.99,"pixels","validated open area")}
    assert TacticalCoach("apex").choose(cues,t).kind=="avoid_open_revive"

def test_no_enemy_no_new_fight_hallucinations():
    t=1000.
    cues={"health_low":Evidence(True,t,.99,"pixels","low health")}
    assert TacticalCoach("apex").choose(cues,t) is None
