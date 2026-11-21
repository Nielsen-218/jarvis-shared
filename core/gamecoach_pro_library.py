"""Source-attributed Apex pro study roster: MnK first, controller optional.

Player input devices are verified by public historical competitive records;
this does NOT establish visible keypresses in any individual YouTube video.
No public video is considered visually studied until its locally obtained
frames are actually analyzed and separately verified.
"""
from __future__ import annotations
from dataclasses import dataclass
from math import isfinite

@dataclass(frozen=True)
class Pro:
    key: str
    name: str
    input_device: str
    style: str
    focus: tuple[str,...]
    video_search: str
    verified_input_source: str
    input_overlay_verified: bool=False

PROS={
    "iitzTimmy":Pro("iitzTimmy","iiTzTimmy","mnk",
        "High-speed MnK fighting, movement decisions and improvisation",
        ("movement","decision","aim"),
        "https://www.youtube.com/results?search_query=iiTzTimmy+Apex+Legends+full+ranked+game+2026+keyboard",
        "https://liquipedia.net/apexlegends/IiTzTimmy"),
    "zero":Pro("zero","Zer0","mnk",
        "IGL, power positioning, ring rotations, deliberate fight timing",
        ("rotation","positioning","teamplay"),
        "https://www.youtube.com/results?search_query=Zer0+Apex+ALGS+full+POV+2026",
        "https://liquipedia.net/apexlegends/Zer0"),
    "reps":Pro("reps","Reps","mnk",
        "Team anchoring, cover, recovery and team coordination",
        ("teamplay","positioning","healing"),
        "https://www.youtube.com/results?search_query=Reps+Apex+ALGS+POV+full+game",
        "https://liquipedia.net/apexlegends/Reps"),
    "mande":Pro("mande","Mande","mnk",
        "MnK accuracy, positioning and squad fight decisions",
        ("aim","positioning","movement"),
        "https://www.youtube.com/results?search_query=Mande+Apex+Legends+ranked+full+game+MnK",
        "https://liquipedia.net/apexlegends/Mande"),
    "monsoon":Pro("monsoon","Monsoon","mnk",
        "Competitive MnK fighting, recovery and disciplined positioning",
        ("decision","positioning","teamplay"),
        "https://www.youtube.com/results?search_query=Monsoon+Apex+ALGS+POV+gameplay",
        "https://liquipedia.net/apexlegends/Monsoon"),
    "verhulst":Pro("verhulst","Verhulst","controller",
        "OPTIONAL controller tracking and fight micro",
        ("aim","decision","positioning"),
        "https://www.youtube.com/results?search_query=Verhulst+Apex+controller+handcam+full+game",
        "https://liquipedia.net/apexlegends/Verhulst"),
    "genburten":Pro("genburten","Genburten","controller",
        "OPTIONAL controller tracking and aggressive close-range movement",
        ("aim","movement","decision"),
        "https://www.youtube.com/results?search_query=Genburten+Apex+controller+cam+full+game",
        "https://liquipedia.net/apexlegends/Genburten"),
}

# Direct upload links checked via YouTube metadata; footage itself has NOT
# been downloaded/visually reviewed. No verified on-screen keyboard overlay.
STARTER_VIDEO_METADATA={
    "iitzTimmy":{
        "url":"https://www.youtube.com/watch?v=PvthoO9Jkso",
        "title":"THE NEW APEX LEGENDS SEASON IS HERE",
        "published":"2026-08-05","gameplay_starts_seconds":1335,
        "verified":"original iiTzTimmy upload and chapter markers only",
        "input_overlay":"unverified",
    },
    "mande":{
        "url":"https://www.youtube.com/watch?v=rPmwHiJ-sww",
        "title":"RANKED APEX LEGENDS",
        "published":"2026-05-04","gameplay_starts_seconds":None,
        "verified":"original Mande channel video metadata only",
        "input_overlay":"unverified",
    }
}

MAIN_MNK=("iitzTimmy","mande","zero","reps","monsoon")
OPTIONAL_CONTROLLER=("verhulst","genburten")
TOPICS=frozenset({"decision","rotation","teamplay","positioning","healing",
                  "aim","movement","looting","objective","crosshair","mechanics"})

def find_pro(key:str)->Pro:
    if key not in PROS:raise ValueError("Select a registered pro")
    return PROS[key]

def catalog(include_controller:bool=False)->list[dict]:
    keys=MAIN_MNK+OPTIONAL_CONTROLLER if include_controller else MAIN_MNK
    return [{**vars(PROS[key]),
             "starter_video":STARTER_VIDEO_METADATA.get(key),
             "source_verified_as":"public video metadata / game input profile; not reviewed footage"
            } for key in keys]

def eligible_lesson(item:dict,topic:str,preferred_input:str="mnk")->bool:
    if not isinstance(item,dict) or topic not in TOPICS:return False
    if item.get("review_status")!="human_verified":return False
    if item.get("source_kind")!="local_pro_vod":return False
    if item.get("category")!=topic:return False
    if not isinstance(item.get("instruction"),str) or not item["instruction"].strip():return False
    if not isinstance(item.get("video_sha256"),str) or len(item["video_sha256"])!=64:return False
    if (type(item.get("time_s")) not in (int,float) or not isfinite(item["time_s"])
            or item["time_s"]<0):return False
    times=item.get("observation_times_s",[])
    if (not isinstance(times,list) or not 2<=len(times)<=20
            or any(type(t) not in (int,float) or not isfinite(t) or t<0 for t in times)
            or len(set(times))<2):return False
    if item.get("pov_verified_player")!=item.get("player") or not item.get("player"):return False
    if item.get("input_device")!=preferred_input:return False
    return True

def training_tip(items:list[dict],topic:str,preferred_input:str="mnk")->dict|None:
    matched=[x for x in items if eligible_lesson(x,topic,preferred_input)]
    return matched[-1] if matched else None
