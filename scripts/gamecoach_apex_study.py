"""Offline Apex VOD learning index for subtitles you are authorized to analyze.

This does NOT stream/download YouTube, clone media, or automatically grant
tactical authority to spoken coaching advice. It turns *locally supplied*
caption cues into a bounded timestamp index. A human reviews gameplay and
sources before promoting a teaching point to the actual playbook.

Usage:
 python scripts/gamecoach_apex_study.py --captions own_notes.vtt \
    --url https://www.youtube.com/watch?v=YOURVIDEO --out data/gaming/study/lesson.json
"""
from __future__ import annotations
import argparse, json, re
from pathlib import Path
from urllib.parse import urlparse

TOPICS = {
    "positioning": ("cover","angle","peek","position","height","headglitch"),
    "rotation": ("rotate","ring","zone","path","choke","edge"),
    "teamplay": ("team","squad","trade","ping","teammate","comm"),
    "healing": ("heal","shield","battery","reset","crack"),
    "decision": ("push","third party","disengage","commit","fight","isolate"),
    "looting": ("loot","inventory","ammo","deathbox"),
    "mechanics": ("movement","slide","strafe","recoil","aim"),
}
STAMPS = re.compile(r"(?:(\d{2}):)?(\d{2}):(\d{2})[.,](\d{3})")
VIDEO_DOMAINS = {"youtube.com","www.youtube.com","m.youtube.com","youtu.be"}

def seconds_from_stamp(s: str) -> float:
    m=STAMPS.fullmatch(s.strip())
    if not m:raise ValueError("Expected SRT/VTT timestamp")
    h,mi,sec,ms=m.groups()
    return (int(h or 0)*3600 + int(mi)*60 + int(sec) + int(ms)/1000)

def video_url(url: str) -> str:
    p=urlparse(url)
    if p.scheme!="https" or p.hostname not in VIDEO_DOMAINS or not p.path:
        raise ValueError("A specific HTTPS YouTube video link is required")
    if p.hostname!="youtu.be" and not p.path.startswith(("/watch","/shorts/","/live/")):
        raise ValueError("Use a video URL, not a playlist or channel")
    return url

def read_cues(raw: str) -> list[tuple[float,str]]:
    """Strip VTT styling; keep only timestamps and words used for topic matching."""
    raw=raw.replace("\ufeff","")
    cues=[]
    for block in re.split(r"\n\s*\n",raw):
        lines=[x.strip() for x in block.splitlines() if x.strip()]
        pos=next((i for i,s in enumerate(lines) if "-->" in s),None)
        if pos is None:continue
        left=lines[pos].split("-->",1)[0].strip()
        try:stamp=seconds_from_stamp(left)
        except ValueError:continue
        content=" ".join(lines[pos+1:])
        content=re.sub(r"<[^>]*>"," ",content)
        content=re.sub(r"\{[^}]*\}"," ",content)
        content=re.sub(r"\s+"," ",content).lower()
        if content:cues.append((stamp,content[:600]))
        if len(cues)>=25000:break
    return cues

def candidate_index(raw: str, url: str, title: str="") -> dict:
    url=video_url(url)
    cues=read_cues(raw)
    if not cues:raise ValueError("No usable timestamped subtitle cues")
    found={}
    for topic,phrases in TOPICS.items():
        hits=[]
        for stamp,words in cues:
            # Lexical hits are study leads, NOT proof that the instruction is
            # correct or applicable to the active Apex patch.
            if any(re.search(r"\b"+re.escape(term)+r"\b",words) for term in phrases):
                hits.append(int(stamp))
            if len(hits)>=80:break
        found[topic]=sorted(set(hits))
    return {
        "video_url":url,"title":title[:140],"caption_cues":len(cues),
        "status":"UNREVIEWED_INDEX_ONLY",
        "topics_seconds":found,
        "source_kind":"supplied_local_captions",
        "note":"Keyword matches do not verify any mechanics or scene; review the video before writing a play.",
    }

def main()->None:
    ap=argparse.ArgumentParser()
    ap.add_argument("--captions",type=Path,required=True)
    ap.add_argument("--url",required=True)
    ap.add_argument("--title",default="")
    ap.add_argument("--out",type=Path,required=True)
    args=ap.parse_args()
    if args.captions.stat().st_size>4_000_000:
        raise ValueError("Caption file is too large")
    data=candidate_index(args.captions.read_text(encoding="utf-8"),args.url,args.title)
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(data,indent=2),encoding="utf-8")
    print(json.dumps({"indexed":data["caption_cues"],"status":data["status"],"file":str(args.out)},indent=2))

if __name__=="__main__":main()
