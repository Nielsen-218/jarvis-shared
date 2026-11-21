"""Offline Apex pro gameplay lessons, MnK first; controller is optional.

The user chooses/downloads videos when permitted, or supplies local files.
No automatic channel scraping, game input control, or paid API requests.

Usage (from Jarvis repo root):
 python scripts/gamecoach_pro_vods.py list
 python scripts/gamecoach_pro_vods.py ingest --player iitzTimmy --file C:/Videos/timmy.mp4 --url https://www.youtube.com/watch?v=VIDEO_ID
 python scripts/gamecoach_pro_vods.py acquire --player zero --url https://www.youtube.com/watch?v=VIDEO_ID
 python scripts/gamecoach_pro_vods.py study --player iitzTimmy
 python scripts/gamecoach_pro_vods.py candidates --player iitzTimmy
 python scripts/gamecoach_pro_vods.py approve --player iitzTimmy --index 0 --category decision --instruction "Retreat when outnumbered rather than taking a 1v3."
"""
from __future__ import annotations

import argparse
import base64
from datetime import date
import hashlib
import io
import json
from math import isfinite
import pathlib
import re
import subprocess
import sys
from urllib.parse import parse_qs, urlparse

ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from core.gamecoach_pro_library import PROS, TOPICS, MAIN_MNK, STARTER_VIDEO_METADATA, catalog, find_pro, training_tip
from core.gamecoach_meta import get_legend, get_map, SEASON, PATCH_AS_OF

DEFAULT_ROOT=ROOT/"data"/"gaming"/"pro_academy"
VIDEO_EXT={".mp4",".mkv",".mov",".webm"}
SHA=re.compile(r"[0-9a-f]{64}$")

def video_id(url:str)->str:
    parsed=urlparse(url)
    if parsed.scheme!="https" or parsed.hostname not in {"youtube.com","www.youtube.com","m.youtube.com","youtu.be"}:
        raise ValueError("Use a specific HTTPS YouTube video link")
    if parsed.hostname=="youtu.be":
        ident=parsed.path.strip("/")
    elif parsed.path=="/watch":
        ids=parse_qs(parsed.query).get("v",[])
        ident=ids[0] if len(ids)==1 else ""
    elif parsed.path.startswith("/shorts/"):
        ident=parsed.path.split("/")[2]
    else:raise ValueError("Specific video URL required; not channel or playlist")
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}",ident):
        raise ValueError("Invalid YouTube video identifier")
    return ident

def file_info(path:pathlib.Path)->tuple[pathlib.Path,str]:
    video=path.expanduser().resolve()
    if not video.is_file() or video.suffix.lower() not in VIDEO_EXT:
        raise ValueError("Need an existing local MP4/MKV/MOV/WEBM")
    size=video.stat().st_size
    if size<1024 or size>20_000_000_000:
        raise ValueError("Video is empty or exceeds 20GB bound")
    h=hashlib.sha256()
    with video.open("rb") as stream:
        for block in iter(lambda:stream.read(1024*1024),b""):
            h.update(block)
    return video,h.hexdigest()

def _atomic(path:pathlib.Path,data)->None:
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix(".tmp")
    temp.write_text(json.dumps(data,indent=2),encoding="utf-8")
    temp.replace(path)

def ingest(player:str,file:pathlib.Path,source_url:str="",root:pathlib.Path=DEFAULT_ROOT,
           input_display:str="unknown")->dict:
    """Register metadata only, without copying/uploading copyrighted frames."""
    p=find_pro(player)
    if source_url:video_id(source_url)
    if input_display not in {"unknown","none","keyboard_overlay","keyboard_cam",
                              "controller_overlay","controller_cam"}:
        raise ValueError("Invalid input display; don't claim keys if not shown")
    path,sha=file_info(file)
    location=root/player/sha[:16]
    target=location/"source.json"
    if target.is_file():
        prior=json.loads(target.read_text(encoding="utf-8"))
        if prior.get("video_sha256")==sha:
            return {"status":"already_registered","player":player,"id":sha[:16]}
        raise ValueError("Source manifest conflict")
    _atomic(target,{"player":player,"input_device":p.input_device,
                    "video_file":str(path),"video_sha256":sha,
                    "source_url":source_url,"input_display_claimed":input_display,
                    "source_published":next((v["published"] for v in STARTER_VIDEO_METADATA.values()
                        if v["url"]==source_url),None),
                    "input_buttons_human_verified":False,
                    "state":"REGISTERED_NOT_VISUALLY_STUDIED"})
    return {"status":"registered","player":player,"id":sha[:16],
            "input_device":p.input_device,"path":str(target)}

def acquire(player:str,url:str,root:pathlib.Path=DEFAULT_ROOT)->dict:
    """Explicitly acquire one owner-permitted public video with optional yt-dlp.

    No credentials, DRM bypass, paid access, playlist or channel crawls.
    If the publisher/platform doesn't allow it, supply authorized local media.
    """
    find_pro(player)
    ident=video_id(url)
    target=root/"downloads"/player
    target.mkdir(parents=True,exist_ok=True)
    template=str(target/(ident+".%(ext)s"))
    command=[sys.executable,"-m","yt_dlp","--no-playlist","--ignore-config",
             "--no-overwrites","--no-mtime","--no-warnings",
             "--max-filesize","2000M","--merge-output-format","mp4",
             "-f","bv*[height<=1080]+ba/b[height<=1080]",
             "-o",template,url]
    result=subprocess.run(command,capture_output=True,text=True,timeout=900,
                          check=False)
    if result.returncode:
        raise RuntimeError("Single-video acquisition failed; use a video you are permitted to save locally")
    matches=[f for f in target.iterdir() if f.is_file() and f.stem==ident
             and f.suffix.lower() in VIDEO_EXT]
    if len(matches)!=1:raise RuntimeError("Expected one downloaded video")
    return ingest(player,matches[0],url,root)

def _sources(player:str,root:pathlib.Path)->list[pathlib.Path]:
    find_pro(player)
    return sorted((root/player).glob("*/source.json")) if (root/player).is_dir() else []

def _source(player:str,root:pathlib.Path,source_id:str="")->tuple[pathlib.Path,dict]:
    choices=_sources(player,root)
    if source_id:
        if not re.fullmatch("[a-f0-9]{16}",source_id):raise ValueError("Invalid local clip ID")
        choices=[f for f in choices if f.parent.name==source_id]
    if len(choices)!=1:raise ValueError("Select exactly one registered clip using --id; use 'sources' to list")
    record=json.loads(choices[0].read_text(encoding="utf-8"))
    if record.get("player")!=player or not SHA.fullmatch(str(record.get("video_sha256",""))):
        raise ValueError("Unrecognized source manifest")
    video,sha=file_info(pathlib.Path(record["video_file"]))
    if sha!=record["video_sha256"]:raise ValueError("Local video file changed since registration")
    return choices[0],record

def _segments(file:pathlib.Path,max_segments:int=8,spacing:float=30.,
              start_s:float=0.)->list[dict]:
    if not 1<=max_segments<=20 or not 5<=spacing<=180 or not 0<=start_s<=200000:
        raise ValueError("Invalid bounded offline study window")
    import av
    frames=[]
    with av.open(str(file)) as reader:
        if not reader.streams.video:raise ValueError("Video has no decodable stream")
        stream=reader.streams.video[0]
        if reader.duration is None:raise ValueError("Need a video with known duration")
        duration=reader.duration/1e6
        for i in range(max_segments):
            t=max(3.,start_s)+i*spacing
            if t+2>duration:break
            # Seek in AV_TIME_BASE units for robust container offsets.
            reader.seek(int(max(0,t-1)*1e6),backward=True,any_frame=False)
            shots=[]
            for frame in reader.decode(stream):
                if frame.time is None:continue
                stamp=float(frame.time)
                if stamp<t:continue
                if stamp>t+1.7:break
                if shots and stamp-shots[-1]["time_s"]<.45:continue
                pic=frame.to_image().convert("RGB")
                pic.thumbnail((960,540))
                memory=io.BytesIO()
                pic.save(memory,format="JPEG",quality=75)
                shots.append({"time_s":round(stamp,2),
                              "jpeg":base64.b64encode(memory.getvalue()).decode("ascii")})
                if len(shots)>=4:break
            if len(shots)>=2:frames.append({"time_s":round(t,2),"shots":shots})
    return frames

def _game_running()->bool:
    try:
        from scripts.nolagin import _game_running as check
        return check("apex")
    except ImportError:
        # On Windows, absence of psutil is not a reason to assume Apex is closed.
        if sys.platform=="win32":raise RuntimeError("Install psutil before offline study")
        return False

def _candidate(raw:str,when:float,source:dict)->dict:
    from core.gamecoach_review_parser import parse_model_json
    obj=parse_model_json(raw)
    cat=obj.get("category")
    if cat not in TOPICS:cat="decision"
    def short(key:str)->str:
        value=obj.get(key,"")
        return value[:320] if isinstance(value,str) else ""
    return {"player":source["player"],"input_device":source["input_device"],
            "video_sha256":source["video_sha256"],"source_kind":"local_pro_vod",
            "time_s":when,"category":cat,"observed":short("observed"),
            "hypothesis":short("hypothesis"),"evidence":short("evidence"),
            "pov_name_candidate":short("pov_name"),
            "legend_candidate":short("legend"),"map_candidate":short("map"),
            "source_published":source.get("source_published"),
            "input_overlay_candidate":obj.get("input_overlay_visible") is True,
            "confirmed_buttons":[],"review_status":"unverified_model_hypothesis"}

def study(player:str,root:pathlib.Path=DEFAULT_ROOT,source_id:str="",
          max_segments:int=8,start_s:float|None=None,spacing_s:float=30.)->dict:
    if _game_running():raise RuntimeError("Quit Apex before running local video model")
    manifest,source=_source(player,root,source_id)
    from core import vision
    if not vision.available():raise RuntimeError("No free local VLM is available; no analysis was performed")
    if start_s is None:
        example=STARTER_VIDEO_METADATA.get(player)
        start_s=(example.get("gameplay_starts_seconds") or 0.) if (
            example and example.get("url")==source.get("source_url")) else 0.
    windows=_segments(pathlib.Path(source["video_file"]),max_segments,
                      spacing=spacing_s,start_s=start_s)
    if not windows:raise ValueError("No usable video segments decoded")
    notes=[]
    for segment in windows:
        prompt=("Four sampled gameplay frames only; NOT continuous motion. "
                "Identify only what is visible: cover, engagement, positioning, "
                "healing, rotation, team coordination, movement. "
                "Never invent offscreen enemies, actions between frames or inputs. "
                "Read the active POV player's HUD name; broadcasts switch players. "
                "Do not attribute a teammate's or controller player's actions to the video title's player. "
                "Report Legend/map only if directly identifiable, otherwise unknown. "
                "Visible buttons/keyboard overlay must be directly displayed; "
                "a default keybind table alone is NOT proof of presses. "
                "Reply JSON with category, observed, evidence, hypothesis, pov_name, legend, map, "
                "input_overlay_visible (true/false). "
                "Treat the proposed coaching lesson as UNVERIFIED.")
        images=[shot["jpeg"] for shot in segment["shots"]]
        try:
            raw=vision.describe(images,question=prompt,timeout=120)
            note=_candidate(raw,segment["time_s"],source)
            note["observation_times_s"]=[shot["time_s"] for shot in segment["shots"]]
            notes.append(note)
        except (ValueError,TimeoutError,OSError):
            continue
    _atomic(manifest.parent/"candidates.json",notes)
    return {"player":player,"clip_id":manifest.parent.name,"analyzed_segments":len(notes),
            "status":"unverified_candidate_lessons_not_training_truth",
            "input_device":source["input_device"],"gameplay_start_s":start_s}

def candidates(player:str,root:pathlib.Path=DEFAULT_ROOT,source_id:str="")->list[dict]:
    source,_=_source(player,root,source_id)
    path=source.parent/"candidates.json"
    if not path.is_file():return []
    rows=json.loads(path.read_text(encoding="utf-8"))
    return [{"index":i,"at_s":r.get("time_s"),"observed":r.get("observed"),
             "hypothesis":r.get("hypothesis"),"input_overlay_candidate":r.get("input_overlay_candidate"),
             "evidence":r.get("evidence"),"observation_times_s":r.get("observation_times_s",[]),
             "pov_name_candidate":r.get("pov_name_candidate"),
             "legend_candidate":r.get("legend_candidate"),"map_candidate":r.get("map_candidate"),
             "status":r.get("review_status")} for i,r in enumerate(rows)]

def approve(player:str,index:int,category:str,instruction:str,
            root:pathlib.Path=DEFAULT_ROOT,source_id:str="",
            keys:str="",legend:str="",map_name:str="",pov_confirmed:bool=False)->dict:
    """Human confirmation of the actual video timestamp before promotion."""
    if category not in TOPICS or len(instruction.strip())<15 or len(instruction)>400:
        raise ValueError("Need a specific reviewed tactical teaching point")
    source,meta=_source(player,root,source_id)
    path=source.parent/"candidates.json"
    raw=json.loads(path.read_text(encoding="utf-8"))
    if type(index) is not int or not 0<=index<len(raw):
        raise IndexError("Candidate index does not exist")
    entry=raw[index]
    if entry.get("video_sha256")!=meta["video_sha256"] or entry.get("player")!=player:
        raise ValueError("Candidate does not belong to this source video/player")
    times=entry.get("observation_times_s",[])
    if (not isinstance(times,list) or len(times)<2 or len(times)>20
            or any(type(t) not in (int,float) or not isfinite(t) or t<0 for t in times)
            or len(set(times))<2):
        raise ValueError("Review at least two distinct actual frame timestamps before approval")
    if not pov_confirmed:
        raise ValueError("Human must confirm the active POV is this player; use --confirm-pov")
    verified_legend=get_legend(legend).name if legend else None
    verified_map=get_map(map_name).name if map_name else None
    if entry.get("review_status")=="human_verified":
        return {"status":"already_verified","index":index}
    entry["category"]=category
    entry["instruction"]=instruction.strip()
    entry["review_status"]="human_verified"
    entry["pov_verified_player"]=player
    entry["legend_verified"]=verified_legend
    entry["map_verified"]=verified_map
    entry["patch_at_review"]=SEASON
    entry["patch_date_at_review"]=date.today().isoformat()
    entry["source_published"]=meta.get("source_published")
    # Do NOT infer control keys from video or published keybinding. Owner must
    # personally check the overlay and specify only actually visible buttons.
    entry["confirmed_buttons"]=[k.strip() for k in keys.split(",") if k.strip()][:12]
    entry["input_device"]=meta["input_device"]
    _atomic(path,raw)
    return {"status":"human_verified","player":player,"category":category,"index":index,
            "visible_keys_verified_count":len(entry["confirmed_buttons"]),
            "map_verified":verified_map,"legend_verified":verified_legend}

def lesson(player:str,topic:str,root:pathlib.Path=DEFAULT_ROOT,
           preferred_input:str="mnk")->dict:
    if preferred_input not in {"mnk","controller"}:raise ValueError("Invalid input")
    if topic not in TOPICS:raise ValueError("Unknown tactical category")
    all_lessons=[]
    for path in _sources(player,root):
        # Recall uses the approved historical source identity; never rehash a
        # multi-GB video on a live voice request. Study/approval verify bytes.
        meta=json.loads(path.read_text(encoding="utf-8"))
        if meta.get("player")!=player or not SHA.fullmatch(str(meta.get("video_sha256",""))):continue
        if meta.get("input_device")!=preferred_input:continue
        candidate_path=path.parent/"candidates.json"
        if candidate_path.exists():
            all_lessons.extend({**r,"source_url":meta.get("source_url"),
                               "source_published":meta.get("source_published")}
                for r in json.loads(candidate_path.read_text(encoding="utf-8"))
                if isinstance(r,dict) and r.get("player")==player
                and r.get("video_sha256")==meta["video_sha256"])
    item=training_tip(all_lessons,topic,preferred_input)
    return {"status":"verified_lesson" if item else "no_human_verified_lesson",
            "input_device":preferred_input,"topic":topic,
            "lesson":item.get("instruction") if item else None,
            "source_time_s":item.get("time_s") if item else None,
            "source_url":item.get("source_url") if item else None,
            "source_published":item.get("source_published") if item else None,
            "observation_times_s":item.get("observation_times_s",[]) if item else [],
            "legend_verified":item.get("legend_verified") if item else None,
            "map_verified":item.get("map_verified") if item else None,
            "patch_date_at_review":item.get("patch_date_at_review") if item else None}

def main(argv=None)->int:
    parser=argparse.ArgumentParser(description="Offline pro Apex VOD study, mouse/keyboard first")
    parser.add_argument("--root",type=pathlib.Path,default=DEFAULT_ROOT)
    commands=parser.add_subparsers(dest="command",required=True)
    ls=commands.add_parser("list")
    ls.add_argument("--controller",action="store_true")
    for key in ("sources","candidates","study","approve","lesson","acquire","ingest"):
        sub=commands.add_parser(key)
        sub.add_argument("--player",required=True,choices=sorted(PROS))
        if key in {"candidates","study","approve","lesson"}:
            sub.add_argument("--id",default="")
        if key=="study":
            sub.add_argument("--max-segments",type=int,default=8)
            sub.add_argument("--start-seconds",type=float,default=None)
            sub.add_argument("--spacing-seconds",type=float,default=30.)
        if key=="acquire":sub.add_argument("--url",required=True)
        if key=="ingest":
            sub.add_argument("--file",required=True,type=pathlib.Path)
            sub.add_argument("--url",default="")
            sub.add_argument("--input-display",default="unknown",
                             choices=["unknown","none","keyboard_overlay","keyboard_cam",
                                      "controller_overlay","controller_cam"])
        if key=="approve":
            sub.add_argument("--index",type=int,required=True)
            sub.add_argument("--category",required=True,choices=sorted(TOPICS))
            sub.add_argument("--instruction",required=True)
            sub.add_argument("--keys",default="")
            sub.add_argument("--legend",default="",help="Legend truly seen during video review")
            sub.add_argument("--map",default="",help="BR map actually confirmed in video")
            sub.add_argument("--confirm-pov",action="store_true",
                             help="Human reviewed the active POV name, not just the video title")
        if key=="lesson":
            sub.add_argument("--category",required=True,choices=sorted(TOPICS))
            sub.add_argument("--input",choices=("mnk","controller"),default="mnk")
    args=parser.parse_args(argv)
    try:
        if args.command=="list":result=catalog(args.controller)
        elif args.command=="sources":
            result=[{"id":p.parent.name,"path":str(p)}
                    for p in _sources(args.player,args.root)]
        elif args.command=="ingest":
            result=ingest(args.player,args.file,args.url,args.root,args.input_display)
        elif args.command=="acquire":
            result=acquire(args.player,args.url,args.root)
        elif args.command=="study":
            result=study(args.player,args.root,args.id,args.max_segments,
                         args.start_seconds,args.spacing_seconds)
        elif args.command=="candidates":
            result=candidates(args.player,args.root,args.id)
        elif args.command=="approve":
            result=approve(args.player,args.index,args.category,args.instruction,
                           args.root,args.id,args.keys,args.legend,args.map,args.confirm_pov)
        else:
            result=lesson(args.player,args.category,args.root,args.input)
        print(json.dumps(result,indent=2))
        return 0
    except (OSError,ValueError,RuntimeError,IndexError,ImportError,subprocess.TimeoutExpired) as exc:
        print(json.dumps({"status":"blocked","reason":str(exc)[:240],
                          "command":args.command},indent=2))
        return 2

if __name__=="__main__":
    raise SystemExit(main())
