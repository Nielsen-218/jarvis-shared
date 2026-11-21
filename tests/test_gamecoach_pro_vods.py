"""Local pro-VOD studies must not turn public videos into fake live enemies."""
import json
from pathlib import Path
import pytest
from core.gamecoach_pro_library import (PROS,MAIN_MNK,OPTIONAL_CONTROLLER,
    catalog,find_pro,training_tip)
from scripts.gamecoach_pro_vods import (video_id,ingest,candidates,approve,
    lesson,acquire,_candidate,_atomic)

def test_primary_five_are_mnk_and_timmys_first():
    assert MAIN_MNK[0]=="iitzTimmy"
    assert len(MAIN_MNK)==5
    assert all(PROS[name].input_device=="mnk" for name in MAIN_MNK)
    assert all(PROS[name].input_device=="controller" for name in OPTIONAL_CONTROLLER)
    assert len(catalog())==5
    assert len(catalog(True))==7
    assert all(not p.input_overlay_verified for p in PROS.values())

def test_unverified_videos_cannot_ground_advice():
    proposed={"player":"iitzTimmy","input_device":"mnk",
       "category":"decision","instruction":"Always take the fight",
       "time_s":15,"video_sha256":"a"*64,
       "source_kind":"local_pro_vod","review_status":"unverified_model_hypothesis"}
    proposed.update(observation_times_s=[15.,15.5],pov_verified_player="iitzTimmy")
    assert training_tip([proposed],"decision") is None
    proposed["review_status"]="human_verified"
    assert training_tip([proposed],"decision") is proposed
    assert training_tip([proposed],"decision","controller") is None

def test_bad_youtube_urls_and_playlist_rejected():
    for url in ("https://evil.example/watch?v=12345678901",
       "http://www.youtube.com/watch?v=12345678901",
       "https://www.youtube.com/playlist?list=ABC",
       "https://www.youtube.com/@someone",
       "https://youtu.be/a"):
        with pytest.raises(ValueError):
            video_id(url)
    assert video_id("https://www.youtube.com/watch?v=yiIuL_Rwu08")=="yiIuL_Rwu08"

def test_local_ingest_multiple_clips_and_source_integrity(tmp_path):
    a=tmp_path/"clip1.mp4"
    b=tmp_path/"clip2.mp4"
    a.write_bytes(b"x"*2048)
    b.write_bytes(b"y"*2048)
    p=ingest("iitzTimmy",a,"https://youtu.be/yiIuL_Rwu08",tmp_path/"local")
    q=ingest("iitzTimmy",b,"",tmp_path/"local")
    assert p["id"]!=q["id"]
    assert p["input_device"]=="mnk"
    assert ingest("iitzTimmy",a,root=tmp_path/"local")["status"]=="already_registered"
    with pytest.raises(ValueError,match="Select exactly one"):
        candidates("iitzTimmy",tmp_path/"local")
    assert candidates("iitzTimmy",tmp_path/"local",p["id"])==[]

def test_local_study_candidates_require_human_confirmation_and_device(tmp_path):
    video=tmp_path/"sample.mp4"
    video.write_bytes(b"0"*2048)
    root=tmp_path/"local"
    reg=ingest("iitzTimmy",video,root=root)
    src=root/"iitzTimmy"/reg["id"]
    entries=[
      _candidate(json.dumps({"category":"decision",
         "observed":"Health bar low and other player appears to retreat",
         "evidence":"One sampled frame",
         "hypothesis":"Back off when low",
         "input_overlay_visible":True}),
         17, json.loads((src/"source.json").read_text()))
    ]
    assert entries[0]["review_status"]=="unverified_model_hypothesis"
    assert entries[0]["confirmed_buttons"]==[]
    entries[0]["observation_times_s"]=[17.,17.5]
    _atomic(src/"candidates.json",entries)
    assert lesson("iitzTimmy","decision",root)["lesson"] is None
    assert candidates("iitzTimmy",root)[0]["input_overlay_candidate"] is True
    result=approve("iitzTimmy",0,"decision","Back off when shields break behind cover",
                   root,keys="W, Shift",pov_confirmed=True)
    assert result["status"]=="human_verified"
    assert result["visible_keys_verified_count"]==2
    assert lesson("iitzTimmy","decision",root)["lesson"].startswith("Back off")
    assert lesson("iitzTimmy","decision",root,"controller")["lesson"] is None
    assert approve("iitzTimmy",0,"decision","Back off when shields break behind cover",
                   root,pov_confirmed=True)["status"]=="already_verified"

def test_controller_pro_never_contaminates_mnk_default(tmp_path):
    clip=tmp_path/"controller.mp4"
    clip.write_bytes(b"c"*2048)
    root=tmp_path/"local"
    registered=ingest("genburten",clip,root=root)
    src=root/"genburten"/registered["id"]
    _atomic(src/"candidates.json",[{
        "review_status":"human_verified","input_device":"controller",
        "source_kind":"local_pro_vod","category":"aim","time_s":4,
        "instruction":"Use shorter aim adjustments before firing",
        "video_sha256":json.loads((src/"source.json").read_text())["video_sha256"],
        "player":"genburten","pov_verified_player":"genburten",
        "observation_times_s":[4.,4.5]
    }])
    assert lesson("genburten","aim",root)["lesson"] is None
    assert lesson("genburten","aim",root,"controller")["lesson"]

def test_acquisition_does_not_invoke_downloader_on_unauthorized_url(tmp_path,monkeypatch):
    monkeypatch.setattr("subprocess.run",lambda *a,**k:
       pytest.fail("Should not attempt download of invalid video"))
    with pytest.raises(ValueError):
        acquire("iitzTimmy","https://youtube.com/playlist?list=abc",tmp_path)


def test_timmys_official_video_starts_at_verified_gameplay_chapter(monkeypatch,tmp_path):
    from scripts import gamecoach_pro_vods as g
    from core import vision
    clip=tmp_path/"timmy.mp4"
    clip.write_bytes(b"t"*2048)
    local=tmp_path/"library"
    g.ingest("iitzTimmy",clip,"https://www.youtube.com/watch?v=PvthoO9Jkso",local)
    monkeypatch.setattr(g,"_game_running",lambda:False)
    seen=[]
    def fake_segments(video,max_segments,spacing,start_s):
        seen.append((max_segments,spacing,start_s))
        return [{"time_s":start_s,
                 "shots":[{"jpeg":"ZmFrZQ==","time_s":start_s},
                          {"jpeg":"ZmFrZQ==","time_s":start_s+.5}]}]
    monkeypatch.setattr(g,"_segments",fake_segments)
    monkeypatch.setattr(vision,"available",lambda:True)
    monkeypatch.setattr(vision,"describe",lambda *a,**k:
       '{"category":"movement","observed":"A player visibly changed angle",'
       '"evidence":"Only two frames","hypothesis":"Seek clean angles",'
       '"input_overlay_visible":false}')
    report=g.study("iitzTimmy",local)
    assert report["analyzed_segments"]==1
    assert report["gameplay_start_s"]==1335
    assert g.candidates("iitzTimmy",local)[0]["observation_times_s"]==[1335.,1335.5]
    assert seen==[(8,30.,1335)]
    assert g.lesson("iitzTimmy","movement",local)["lesson"] is None


def test_pro_vod_legend_and_map_only_added_after_human_approval(tmp_path):
    from scripts.gamecoach_pro_vods import ingest, approve, _atomic
    clip=tmp_path/"clip.mp4"
    clip.write_bytes(b"r"*2048)
    root=tmp_path/"academy"
    info=ingest("iitzTimmy",clip,root=root)
    folder=root/"iitzTimmy"/info["id"]
    _atomic(folder/"candidates.json",[{
        "review_status":"unverified_model_hypothesis",
        "video_sha256":json.loads((folder/"source.json").read_text())["video_sha256"],
        "player":"iitzTimmy","observation_times_s":[22.2,22.7],
        "source_kind":"local_pro_vod",
        "input_device":"mnk","category":"rotation","time_s":22.2,
        "observed":"Player moved from building to open space",
        "hypothesis":"Use alternate rotate","evidence":"sampled frames",
    }])
    with pytest.raises(ValueError):
        approve("iitzTimmy",0,"rotation",
            "Transition when ring movement requires it",root,legend="Fake Hero",map_name="Olympus",pov_confirmed=True)
    row=json.loads((folder/"candidates.json").read_text())[0]
    assert row["review_status"]=="unverified_model_hypothesis"
    approved=approve("iitzTimmy",0,"rotation",
        "Confirm squad safety before taking a wide rotation",
        root,legend="Pathfinder",map_name="World's Edge",pov_confirmed=True)
    assert approved["legend_verified"]=="Pathfinder"
    assert approved["map_verified"]=="World's Edge"
    row=json.loads((folder/"candidates.json").read_text())[0]
    assert row["patch_at_review"].startswith("30:")
    assert row["map_verified"]=="World's Edge"


def test_approval_rejects_misattributed_source_and_unreviewed_pov(tmp_path):
    clip=tmp_path/"clip.mp4"
    clip.write_bytes(b"t"*2048)
    root=tmp_path/"academy"
    info=ingest("iitzTimmy",clip,root=root)
    folder=root/"iitzTimmy"/info["id"]
    meta=json.loads((folder/"source.json").read_text())
    row=_candidate('{"observed":"Opponent near doorway"}',17.,meta)
    row["observation_times_s"]=[17.,17.5]
    _atomic(folder/"candidates.json",[row])
    with pytest.raises(ValueError,match="active POV"):
        approve("iitzTimmy",0,"positioning","Keep cover during the approach",root)
    row["video_sha256"]="f"*64
    _atomic(folder/"candidates.json",[row])
    with pytest.raises(ValueError,match="source video/player"):
        approve("iitzTimmy",0,"positioning","Keep cover during the approach",root,pov_confirmed=True)


@pytest.mark.parametrize("times",[[],[1.],[1.,1.],[float('nan'),2.],[-1.,2.]])
def test_single_or_invalid_observations_cannot_become_verified_lesson(times):
    row={"player":"zero","pov_verified_player":"zero","input_device":"mnk",
         "source_kind":"local_pro_vod","review_status":"human_verified",
         "category":"positioning","instruction":"Hold a covered position",
         "time_s":1.,"video_sha256":"a"*64,"observation_times_s":times}
    assert training_tip([row],"positioning") is None


def test_starter_source_date_is_retained_separately_from_review_patch(tmp_path):
    from core.gamecoach_pro_library import STARTER_VIDEO_METADATA
    clip=tmp_path/"clip.mp4"
    clip.write_bytes(b"t"*2048)
    info=ingest("mande",clip,STARTER_VIDEO_METADATA["mande"]["url"],tmp_path/"academy")
    meta=json.loads(Path(info["path"]).read_text())
    assert meta["source_published"]=="2026-05-04"
