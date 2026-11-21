"""Offline-first official Apex patch discovery. No model calls or gameplay capture.

The official feeds are public HTML indexes, NOT automatically trusted structured
patch data. New URLs are queued with provenance; article excerpts remain
unreviewed until verified and NEVER silently change playable Legend kits.

Only official EA hosts and explicitly allowed Apex paths are fetched. No
external redirects, user-configured URLs, authenticated crawling or paid API.
"""
from __future__ import annotations
from datetime import date,datetime,timezone,timedelta
from html.parser import HTMLParser
from threading import Lock
from urllib.parse import urljoin,urlsplit,urlunsplit,unquote
from urllib.request import Request,build_opener,HTTPRedirectHandler
import re
import hashlib

from core import storage

STORE="apex-patch-watch.json"
FEEDS={
    "ea_news":"https://www.ea.com/games/apex-legends/apex-legends/news",
    "ea_hotfix":"https://forums.ea.com/category/apex-legends-en/blog/apex-legends-game-info-hub-en",
}
ALLOWED={
    "www.ea.com":"/games/apex-legends/apex-legends/news",
    "forums.ea.com":"/blog/apex-legends-game-info-hub-en",
}
PATTERN=re.compile(r"patch|hotfix|balance|update|fix|roadmap",re.I)
LEGEND_WORDS=frozenset(("ballistic","bangalore","fuse","maggie","revenant",
    "alter","ash","axle","horizon","octane","pathfinder","wraith",
    "bloodhound","crypto","seer","sparrow","valkyrie","vantage",
    "catalyst","caustic","rampart","wattson","conduit","gibraltar",
    "lifeline","loba","mirage","newcastle"))
LOCK=Lock()
MAX_BYTES=800_000
MAX_QUEUE=60

def _now():
    return datetime.now(timezone.utc).isoformat()

def canonical(url:str,*,must_be_article:bool=False)->str:
    """Normalize a strict EA-only URL. Reject encoded path escapes/credentials."""
    p=urlsplit(url)
    host=(p.hostname or "").lower()
    if (p.scheme!="https" or host not in ALLOWED or p.username
        or p.password or p.port not in (None,443)):
        raise ValueError("Only official HTTPS EA Apex pages are supported")
    path=unquote(p.path)
    if (not path.startswith("/") or "//" in path or "\\" in path
        or any(x in {".",".."} for x in path.split("/")) or
        any(ord(c)<32 for c in path)):
        raise ValueError("Unsafe Apex page path")
    prefix=ALLOWED[host]
    is_list_index=(host=="forums.ea.com"
                   and path=="/category/apex-legends-en/blog/apex-legends-game-info-hub-en")
    if not (path==prefix or path.startswith(prefix.rstrip("/")+"/") or is_list_index):
        raise ValueError("Not an official Apex news path")
    feed=urlsplit(FEEDS["ea_news"] if host=="www.ea.com"
                  else FEEDS["ea_hotfix"]).path.rstrip("/")
    if must_be_article and path.rstrip("/")==feed:
        raise ValueError("Provide a specific patch article")
    return urlunsplit(("https",host,path.rstrip("/") or "/", "",""))

class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        raise ValueError("External redirects are not followed")

def fetch_official(url:str)->str:
    """Bounded, fail-closed HTTP GET for an allowlisted official host."""
    url=canonical(url)
    req=Request(url,headers={"User-Agent":"Jarvis-ApexPatchWatch/1.0",
                             "Accept":"text/html"})
    with build_opener(_NoRedirect()).open(req,timeout=12) as response:
        if canonical(response.geturl())!=url:raise ValueError("Endpoint changed")
        kind=response.headers.get("Content-Type","").lower()
        if "text/html" not in kind:raise ValueError("Expected public HTML")
        raw=response.read(MAX_BYTES+1)
        if len(raw)>MAX_BYTES:raise ValueError("Patch page exceeds size limit")
        try:return raw.decode("utf-8")
        except UnicodeDecodeError:
            # Some current EA articles contain Windows-1252 punctuation.
            return raw.decode("windows-1252",errors="replace")

class LinkReader(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links=[]
        self.href=None
        self.parts=[]
        self.ignore=0
    def handle_starttag(self,tag,attrs):
        if tag in {"script","style"}:self.ignore+=1
        if tag=="a" and not self.ignore:
            self.href=dict(attrs).get("href","")
            self.parts=[]
    def handle_data(self,data):
        if self.href is not None and not self.ignore:
            self.parts.append(data[:300])
    def handle_endtag(self,tag):
        if tag=="a" and self.href is not None:
            self.links.append((self.href," ".join(self.parts).strip()[:200]))
            self.href=None
            self.parts=[]
        if tag in {"script","style"} and self.ignore:self.ignore-=1

class TextReader(HTMLParser):
    """Plain-text paragraph/headline extraction from a single known article."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks=[]
        self.active=False
        self.ignore=0
        self.parts=[]
        self.stack=[]
        self.records=[]
    def _flush(self,frame):
        line=" ".join(" ".join(frame[1]).split())[:650]
        frame[1].clear()
        if len(line)>1:
            self.blocks.append(line)
            self.records.append({"tag":frame[0],"text":line})
    def handle_starttag(self,tag,attrs):
        if tag in ("script","style","nav","footer"):self.ignore+=1
        if tag in ("h1","h2","h3","h4","p","li") and not self.ignore:
            if self.stack:self._flush(self.stack[-1])
            self.stack.append([tag,[]])
    def handle_data(self,data):
        if self.stack and not self.ignore:
            self.stack[-1][1].append(data)
    def handle_endtag(self,tag):
        if self.stack and self.stack[-1][0]==tag and not self.ignore:
            self._flush(self.stack.pop())
        if tag in ("script","style","nav","footer") and self.ignore:self.ignore-=1

def published_date(text:str,*,staff_title:bool=False)->str|None:
    """Only explicit calendar dates, never relative ages or retrieval timestamps."""
    match=re.search(r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),?\s+(20\d{2})\b",text,re.I)
    if match:
        try:return datetime.strptime(" ".join(match.groups()),"%B %d %Y").date().isoformat()
        except ValueError:pass
    if staff_title:
        # EA staff titles use DD/MM/YY[YY], with occasional explicit US
        # MM/DD/YYYY titles. Never infer dates from relative ages or URL slugs.
        match=re.search(r"\b(\d{1,2})/(\d{1,2})/(20\d{2}|\d{2})\b",text)
        if match:
            first,second,year=map(int,match.groups())
            if year<100:year+=2000
            day,month=(second,first) if second>12 and first<=12 else (first,second)
            try:return date(year,month,day).isoformat()
            except ValueError:pass
    return None

def source_date(title:str,url:str,parser:TextReader|None=None)->str|None:
    """Prefer the article heading; forum body dates may belong to profiles."""
    staff=urlsplit(url).hostname=="forums.ea.com"
    headings=[r['text'] for r in parser.records if r['tag']=='h1'] if parser else []
    for text in headings+[title]:
        result=published_date(text,staff_title=staff)
        if result:return result
    # EA news has an explicit long-form publication date near its heading.
    # Forums render relative post ages alongside explicit account-join dates.
    return published_date(" ".join(parser.blocks[:8])) if parser and not staff else None

def article_evidence(parser:TextReader)->tuple[list[dict],list[dict]]:
    """Retain heading/ability context, including nested upgrade bullet effects.

    This verifies attribution to a source section, not in-game correctness.
    """
    from core.gamecoach_meta import LEGENDS
    names={n.casefold():n for n in LEGENDS}
    legend=None
    section="general"
    mentions=[]
    changes=[]
    for n,record in enumerate(parser.records):
        text=record["text"]
        low=text.casefold().strip(": ")
        if record["tag"].startswith("h"):
            if low in names:
                legend=names[low];section="general"
            elif record["tag"] in {"h1","h2","h3"}:
                legend=None;section="general"
        if low in {"passive","tactical","ultimate","upgrades"}:section=low
        explicit=[name for token,name in names.items() if re.search(r"\b"+re.escape(token)+r"\b",low)]
        targets=[legend] if legend else explicit
        for target in targets:
            row={"legend":target,"section":section,"text":text[:380],"paragraph":n}
            if len(mentions)<180:mentions.append(row)
            if (legend and record["tag"] in {"li","p"} and section!="general" and len(text)>7
                and low not in {"passive","tactical","ultimate","upgrades"}
                and not low.startswith(("dev note","from ")) and len(changes)<160):
                changes.append({**row,"verification":"publisher_section_attributed_not_game_tested"})
    return mentions,changes

def roster_candidates(parser:TextReader)->list[str]:
    """Unrecognized Legend-section headings require review, not a guessed kit."""
    from core.gamecoach_meta import LEGENDS
    known={x.casefold() for x in LEGENDS}
    active=False
    unknown=[]
    for row in parser.records:
        text=row['text'];low=text.casefold()
        if row['tag']=='h2':active='legend' in low and low not in known
        if active and row['tag']=='h3' and low not in known and low not in {'passive','tactical','ultimate','upgrades'}:
            unknown.append(text[:90])
    return list(dict.fromkeys(unknown))[:12]

def discover(html:str,feed_url:str)->list[dict]:
    """Discover topical links only; a link is not proof of a new patch."""
    parser=LinkReader()
    parser.feed(html[:MAX_BYTES])
    found={}
    for href,label in parser.links[:1800]:
        try:link=canonical(urljoin(feed_url,href),must_be_article=True)
        except ValueError:continue
        if link==canonical(feed_url):continue
        if not PATTERN.search(label+" "+urlsplit(link).path.rsplit("/",1)[-1]):continue
        if link not in found:
            found[link]={"url":link,"title":label or link.rsplit("/",1)[-1].replace("-"," ")[:100],
                         "feed":feed_url,"status":"discovered_unreviewed"}
        if len(found)>=80:break
    return list(found.values())

def _blank()->dict:
    return {"version":1,"checked_at":None,"feed_status":{},
            "seen":[],"pending":[],"studied":[]}

def state()->dict:
    s=storage.load_global(STORE,_blank())
    return s if isinstance(s,dict) and isinstance(s.get("pending"),list) else _blank()

def scan(*,download=None,is_gaming=None)->dict:
    """Look for new official patch links once, without rewriting verified data."""
    if is_gaming is None:
        from tools.gaming import game_mode,running_game
        is_gaming=lambda:game_mode() or bool(running_game())
    if is_gaming():return {"status":"deferred_game_running"}
    download=download or fetch_official
    with LOCK:
        s=state()
        successes=0
        newly=0
        # A historically seen but unprocessed URL may become patch-relevant
        # after its publisher title/category changes. Seen is not acknowledgement.
        existing={x.get("url") for x in s["pending"]+s["studied"]}
        for key,url in FEEDS.items():
            if is_gaming():
                storage.save_global(STORE,s)
                return {"status":"deferred_game_running"}
            try:
                links=discover(download(url),url)
                if not links:
                    s["feed_status"][key]={"status":"no_matching_html_links","at":_now()}
                    continue
                successes+=1
                for item in links[:40]:
                    if item["url"] not in existing and len(s["pending"])<MAX_QUEUE:
                        s["pending"].append(item)
                        newly+=1
                        existing.add(item["url"])
                s["feed_status"][key]={"status":"checked","found":len(links),"at":_now()}
            except (OSError,TimeoutError,ValueError,UnicodeError) as exc:
                s["feed_status"][key]={"status":"unavailable",
                    "error":type(exc).__name__,"http_status":getattr(exc,"code",None),"at":_now()}
        s["seen"]=list(dict.fromkeys(s["seen"]+sorted(existing)))[-500:]
        # Migrate old over-broad discovery results without discarding history.
        topical=[]
        ignored=s.get("non_patch_discoveries",[])
        for item in s["pending"]:
            if PATTERN.search(item["title"]+" "+item["url"].rsplit("/",1)[-1]):topical.append(item)
            else:ignored.append(item)
        s["non_patch_discoveries"]=ignored[-80:]
        s["pending"]=topical
        # Recent dated updates precede historical seasons; stable ties retain
        # publisher feed order. Discovery does not activate an announced season.
        s["pending"].sort(key=lambda x:(-(date.fromisoformat(source_date(x["title"],x["url"])).toordinal()
                                        if source_date(x["title"],x["url"]) else 0),
                                        "patch-notes" not in x["url"]))
        s["checked_at"]=_now()
        storage.save_global(STORE,s)
        return {"status":"checked" if successes else "all_feeds_unavailable",
                "new":newly,"pending":len(s["pending"]),"successful_feeds":successes}

def study_next(*,download=None,is_gaming=None)->dict:
    """Download ONE discovered article and save bounded *unreviewed* excerpts."""
    if is_gaming is None:
        from tools.gaming import game_mode,running_game
        is_gaming=lambda:game_mode() or bool(running_game())
    if is_gaming():return {"status":"deferred_game_running"}
    download=download or fetch_official
    with LOCK:
        s=state()
        revisiting=not s["pending"] and bool(s["studied"])
        if not s["pending"] and not s["studied"]:
            return {"status":"nothing_queued"}
        # Round-robin refresh prevents silently checking only one article forever.
        if revisiting:
            cursor=s.get("recheck_cursor",0)
            if not isinstance(cursor,int):cursor=0
            item=s["studied"][cursor%len(s["studied"])]
            s["recheck_cursor"]=(cursor+1)%len(s["studied"])
        else:item=s["pending"][0]
        url=canonical(item["url"],must_be_article=True)
        try:
            html=download(url)
            if len(html)>MAX_BYTES:raise ValueError("Article exceeds size limit")
            parser=TextReader()
            parser.feed(html)
            if not parser.blocks:raise ValueError("No readable article text")
            # Tracking IDs/scripts/advertising don't constitute mechanical edits.
            digest=hashlib.sha256("\n".join(parser.blocks).encode("utf-8")).hexdigest()
            if revisiting and item.get("article_sha256")==digest:
                item["last_rechecked_at"]=_now()
                storage.save_global(STORE,s)
                return {"status":"unchanged_article","title":item["title"]}
            matches,changes=article_evidence(parser)
            previous={(x.get("legend"),x.get("section"),x["text"]) for x in item.get("changes",[])}
            current={(x.get("legend"),x.get("section"),x["text"]) for x in changes}
            history=item.get("history",[])
            if revisiting:
                history=history+[{k:item.get(k) for k in ("article_sha256","studied_at","published_date","changes")}]
            seasons=sorted({int(x) for x in re.findall(r"\bSeason\s+(\d{1,2})\b"," ".join(parser.blocks[:18]),re.I)})
            entry={**item,"status":"updated_official_unreviewed" if revisiting
                   else "indexed_official_unreviewed",
                   "studied_at":_now(),"legend_mentions":matches,
                   "last_rechecked_at":_now(),
                   "published_date":source_date(item["title"],url,parser),
                   "changes":changes,"history":history[-4:],
                   "diff":{"added":[list(x) for x in sorted(current-previous)],
                           "removed":[list(x) for x in sorted(previous-current)]},
                   "mentioned_seasons":seasons,
                   "roster_review_candidates":roster_candidates(parser),
                   "paragraphs":len(parser.blocks),
                   "article_sha256":digest,
                   "revision_count":int(item.get("revision_count",0))+1}
            if revisiting:
                s["studied"][s["studied"].index(item)]=entry
            else:
                s["pending"].pop(0)
                s["studied"].append(entry)
            s["studied"]=s["studied"][-70:]
            storage.save_global(STORE,s)
            return {"status":entry["status"],
                    "title":item["title"],"legend_mentions":len(matches),
                    "revision_count":entry["revision_count"]}
        except (ValueError,TimeoutError,OSError,UnicodeError) as exc:
            item["last_attempt_failed_at"]=_now()
            if revisiting:item["last_rechecked_at"]=_now()
            else:s["pending"].append(s["pending"].pop(0))
            storage.save_global(STORE,s)
            return {"status":"article_unavailable","error":type(exc).__name__}

def legend_evidence(name:str)->dict:
    """Return bounded EA-attributed excerpt candidates, never verified kit facts."""
    from core.gamecoach_meta import get_legend
    title=get_legend(name).name
    token=re.compile(r"\b"+re.escape(title.casefold())+r"\b")
    from core.gamecoach_upgrades import hotfixes
    rows=[{"source_url":r['source'],"article_title":"Reviewed EA staff hotfix "+r['published_date'],
           "excerpt":r['effect'],"section":"hotfix","published_date":r['published_date'],
           "review_status":r['verification'],"platform_scope":r['platform_scope']}
          for r in hotfixes(title)]
    pages=sorted(state()["studied"][-30:],key=lambda x:x.get("published_date") or "",reverse=True)
    for page in pages:
        specific=[x for x in page.get("changes",[]) if x.get("legend")==title]
        for mention in specific or page.get("legend_mentions",[]):
            text=str(mention.get("text",""))
            if len(text)<16:continue
            if mention.get("legend")==title or token.search(text.casefold()):
                rows.append({"source_url":page["url"],"article_title":page["title"],
                             "excerpt":text[:380],"section":mention.get("section","general"),
                             "published_date":page.get("published_date"),"review_status":page["status"]})
                if len(rows)>=4:break
        if len(rows)>=4:break
    rows.sort(key=lambda r:r.get('published_date') or '',reverse=True)
    return {"legend":title,"excerpts":rows[:4],
            "status":"official_excerpts_unverified" if rows else "no_indexed_patch_evidence",
            "note":"Official excerpt candidates are not certified current ability facts; check date and full patch."}

def report()->dict:
    s=state()
    return {"last_checked":s["checked_at"],"feeds":s["feed_status"],
            "coverage_complete":all(s["feed_status"].get(k,{}).get("status")=="checked" for k in FEEDS),
            "season_transition_detected":any(any(n>30 for n in x.get("mentioned_seasons",[])) for x in s["studied"]),
            "current_build_verified":False,
            "roster_review_candidates":sorted({name for x in s['studied'] for name in x.get('roster_review_candidates',[])}),
            "pending":len(s["pending"]),"studied":len(s["studied"]),
            "last_articles":[{"title":x["title"],"url":x["url"],
                             "status":x["status"]}
                            for x in s["studied"][-5:]],
            "meaning":"Article excerpts are source-indexed, not automatic ability verification or model training"}
