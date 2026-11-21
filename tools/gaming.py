"""Gaming: a no-lag game mode, clipping, and an AI coach for Apex Legends.

While a game runs, Jarvis gets out of the way (low priority, GPU model unloaded,
HUD and camera off) and does no analysis at all. Clips are recorded by the
NVIDIA App's Instant Replay on the graphics card's dedicated video encoder;
"clip that" just presses its hotkey. Jarvis never touches the game itself, so
anti-cheat has nothing to see.

After the session, clips are reviewed frame by frame by Claude (vision) for a
debrief; findings are saved per person so trends show what's improving.
"""
import base64
import io
import json
import os
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import psutil

from core import hud, storage, usage
from core.words import plural
from tools.registry import tool

GAMES = {"r5apex.exe": "Apex Legends", "r5apex_dx12.exe": "Apex Legends",
         "cod.exe": "Call of Duty", "codhq.exe": "Call of Duty"}
CLIP_DIRS = [Path(os.getenv("JARVIS_CLIPS_DIR", "")) if os.getenv("JARVIS_CLIPS_DIR") else None,
             Path.home() / "Videos" / "NVIDIA", Path.home() / "Videos" / "Captures",
             Path.home() / "Videos" / "SteelSeries Moments"]
CLIP_HOTKEY = os.getenv("JARVIS_CLIP_HOTKEY", "alt+f10")  # NVIDIA App Instant Replay "save"
REVIEWS = "gaming/reviews.json"
SESSIONS = "gaming/sessions.json"
MAX_FRAMES = 48

state = {"game": None, "started": None}  # the game running right now
model = "claude-sonnet-5-5"

COACH = """You are an elite Apex Legends coach reviewing frames from the player's own gameplay clip
(first-person, sampled in order; each frame is labelled with its time in the clip).
Be specific and evidence-based: only point out what you can actually see in the frames, and cite timestamps.
Assess, where visible: positioning and use of cover/high ground, crosshair placement (head level, pre-aiming
angles), engagement decisions (taking or avoiding fights, third-party awareness), movement (strafing, slides,
peeking), healing/shield timing and recharge safety, ability and ultimate usage, looting efficiency,
rotation and ring awareness, team play (spacing, pings, reviving), and what led to any knock or death.
Reply with JSON only:
{"summary": "2-3 sentences, spoken style, honest but encouraging",
 "strengths": ["..."],
 "weak_points": [{"category": "positioning|crosshair|decision|movement|healing|abilities|rotation|teamplay|aim|looting",
                  "time": "0:42", "what": "what happened", "fix": "what to do instead"}],
 "death": "what caused the knock/death, or null if none in the clip",
 "drills": ["1-3 concrete practice drills (Firing Range or in-match habits)"]}"""


# -- game detection / game mode -------------------------------------------------------------
def running_game() -> str | None:
    for p in psutil.process_iter(["name"]):
        name = (p.info["name"] or "").lower()
        if name in GAMES:
            return GAMES[name]
    return None


class GameWatcher:
    """Turns game mode on and off as games start and stop."""

    def __init__(self, say, on_change=None):
        self.say = say
        self.on_change = on_change

    def start(self) -> None:
        threading.Thread(target=self._loop, daemon=True, name="game-watcher").start()

    def _loop(self) -> None:
        while True:
            try:
                game = running_game()
                if game and not state["game"]:
                    self._enter(game)
                elif not game and state["game"]:
                    self._leave()
            except Exception as e:
                print(f"[gaming] {e}")
            try:
                from core import gameboost
                back = gameboost.tick(game)     # heavy work stays behind the game; put back once it has closed
                if back:
                    print(f"[gaming] gaming protocol put back: {back}")
            except Exception as e:  # noqa: BLE001
                print(f"[gaming] boost: {e}")
            time.sleep(5)

    def _enter(self, game: str) -> None:
        state["game"], state["started"] = game, datetime.now()
        me = psutil.Process()
        try:
            me.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)  # the game always wins the CPU
        except Exception:
            pass
        try:
            from imaging import engine

            engine.unload()  # free the GPU memory the try-on model was holding
        except Exception:
            pass
        try:
            from tools.eyes import eyes

            eyes.stop()
        except Exception:
            pass
        hud.hide()
        print(f"[gaming] {game} started: game mode on")
        if self.on_change:
            self.on_change(True)

    def _leave(self) -> None:
        game, started = state["game"], state["started"]
        state["game"] = None
        # Shut down an opt-in live coach when the game disappears.
        from core.gamecoach_service import service as gamecoach_service
        gamecoach_service.stop()
        try:
            psutil.Process().nice(psutil.NORMAL_PRIORITY_CLASS)
        except Exception:
            pass
        hud.show()
        clips = find_clips(since=started)
        minutes = round((datetime.now() - started).total_seconds() / 60)
        sessions = storage.load(SESSIONS, [])
        sessions.append({"game": game, "start": started.isoformat(timespec="minutes"), "minutes": minutes,
                         "clips": [str(c) for c in clips]})
        storage.save(SESSIONS, sessions)
        print(f"[gaming] {game} closed after {minutes} min, {len(clips)} clip(s): game mode off")
        if self.on_change:
            self.on_change(False)
        if clips:
            self.say(f"Good games, sir. You saved {len(clips)} clip{'s' if len(clips) != 1 else ''} "
                     f"in that {minutes}-minute session. Say 'debrief me' whenever you'd like my review.")


def game_mode() -> bool:
    return state["game"] is not None


# -- clips ----------------------------------------------------------------------------------
def find_clips(since: datetime | None = None, limit: int = 50) -> list[Path]:
    clips = []
    for d in CLIP_DIRS:
        if d and d.exists():
            clips += [p for p in d.rglob("*.mp4")]
    if since:
        clips = [c for c in clips if datetime.fromtimestamp(c.stat().st_mtime) >= since]
    return sorted(clips, key=lambda c: c.stat().st_mtime, reverse=True)[:limit]


@tool(
    "Save a clip of the last couple of minutes of gameplay ('clip that', 'save that'). Uses the "
    "NVIDIA App's Instant Replay. Answer in two or three words; the player is mid-game.",
)
def clip_that() -> str:
    import keyboard

    keyboard.send(CLIP_HOTKEY)
    return "Clip saved (if Instant Replay is on in the NVIDIA App)."


@tool(
    "List recent gameplay clips (newest first) with their age and length.",
    {"days": {"type": "integer", "description": "How many days back"}},
)
def list_clips(days: int) -> str:
    clips = find_clips(since=datetime.now() - timedelta(days=max(days, 1)))
    if not clips:
        return ("No clips found. Turn on Instant Replay in the NVIDIA App (Alt+Z, Record), then say "
                "'clip that' or press Alt+F10 after a good or bad moment.")
    out = []
    for c in clips[:20]:
        when = datetime.fromtimestamp(c.stat().st_mtime).strftime("%a %H:%M")
        out.append(f"{c.name} ({when}, {_duration(c):.0f}s, {c.stat().st_size // 1_000_000} MB)")
    return "\n".join(out)


def _duration(path: Path) -> float:
    import av

    with av.open(str(path)) as c:
        return float(c.duration / 1_000_000) if c.duration else 0.0


def _frames(path: Path, max_frames: int = MAX_FRAMES) -> list[tuple[str, bytes]]:
    """Evenly spaced frames as (timestamp, JPEG bytes), downscaled to 960 px wide."""
    import av

    duration = _duration(path) or 120
    step = max(duration / max_frames, 1.0)
    out, next_t = [], 0.0
    with av.open(str(path)) as c:
        stream = c.streams.video[0]
        stream.thread_type = "AUTO"
        for frame in c.decode(stream):
            t = float(frame.pts * stream.time_base) if frame.pts is not None else 0.0
            if t + 1e-3 < next_t:
                continue
            img = frame.to_image()
            img.thumbnail((960, 960))
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=80)
            out.append((f"{int(t // 60)}:{int(t % 60):02d}", buf.getvalue()))
            next_t += step
            if len(out) >= max_frames:
                break
    return out


@tool(
    "Review a gameplay clip like a pro coach: positioning, crosshair placement, fight decisions, "
    "movement, healing, abilities, rotation, and what caused deaths. Saves the findings for trend "
    "tracking and opens a coaching page. Only while no game is running (it would cost FPS). "
    "Takes ~20-40 s and costs a few cents per clip.",
    {
        "clip": {"type": "string", "description": "'latest', or a clip file name from list_clips"},
        "focus": {"type": "string", "description": "What the player wants looked at, e.g. 'why I lost that 1v1', or empty"},
    },
    slow=True,
)
def review_clip(clip: str, focus: str) -> str:
    from core.intelligence import local_only
    if local_only():
        return review_clip_locally("apex",clip,focus)
    if game_mode():
        return "Not while you're playing; I'll review it after the session so it can't cost you frames."
    clips = find_clips()
    if not clips:
        return "No clips found yet."
    path = clips[0] if clip.strip().lower() in ("latest", "last", "") else \
        next((c for c in clips if c.name == clip or clip.lower() in c.name.lower()), None)
    if path is None:
        return f"No clip called {clip}."
    frames = _frames(path)
    if not frames:
        return "Couldn't read that clip."
    content = []
    for ts, jpg in frames:
        content.append({"type": "text", "text": f"[{ts}]"})
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                    "data": base64.b64encode(jpg).decode()}})
    content.append({"type": "text", "text": "Review this clip." + (f" The player especially wants to know: {focus}" if focus else "")})

    import anthropic

    client = anthropic.Anthropic()
    try:
        resp = client.messages.create(model=model, max_tokens=4000, system=COACH, output_config={"effort": "medium"},
                                      messages=[{"role": "user", "content": content}])
    except anthropic.APIError as e:
        from brain.llm import explain_error

        return f"The review couldn't run: {explain_error(e)}"
    usage.record(model, resp.usage)
    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    try:
        review = json.loads(text[text.index("{"):text.rindex("}") + 1])
    except ValueError:
        return f"The review came back unstructured: {text[:1500]}"

    reviews = storage.load(REVIEWS, [])
    reviews.append({"clip": path.name, "date": date.today().isoformat(), **review})
    storage.save(REVIEWS, reviews)
    _coach_page(path.name, review)
    weak = "; ".join(f"[{w.get('time', '?')}] {w['category']}: {w['what']} -> {w['fix']}" for w in review.get("weak_points", []))
    return (f"Summary: {review.get('summary', '')}\nStrengths: {'; '.join(review.get('strengths', []))}\n"
            f"Weak points: {weak}\nDeath: {review.get('death')}\nDrills: {'; '.join(review.get('drills', []))}\n"
            "(Coaching page opened in the browser.)")


def _coach_page(name: str, review: dict) -> None:
    from tools.tutor import show_lesson_page

    sections = [{"heading": "The verdict", "body": review.get("summary", ""), "terms": [], "diagram": ""}]
    if review.get("strengths"):
        sections.append({"heading": "What you did well", "body": "\n\n".join(review["strengths"]), "terms": [], "diagram": ""})
    if review.get("weak_points"):
        sections.append({"heading": "Weak points", "body": "Timestamped moments to work on.", "diagram": "",
                         "terms": [{"term": f"{w.get('time', '')} · {w['category']}", "meaning": w["what"],
                                    "example": "Fix: " + w["fix"]} for w in review["weak_points"]]})
    if review.get("death"):
        sections.append({"heading": "How you went down", "body": review["death"], "terms": [], "diagram": ""})
    if review.get("drills"):
        sections.append({"heading": "Drills", "body": "\n\n".join(review["drills"]), "terms": [], "diagram": ""})
    show_lesson_page(f"Apex debrief: {name}", "Jarvis coaching review", sections, [])


@tool(
    "The player's recurring weak points and strengths across all reviewed clips (trends over time), "
    "plus recent session stats. Use for 'what are my weak points', 'am I improving'.",
)
def gaming_trends() -> str:
    reviews = storage.load(REVIEWS, [])
    sessions = storage.load(SESSIONS, [])
    if not reviews:
        return f"No clips reviewed yet. {plural(len(sessions), 'session')} logged."
    from collections import Counter

    cats = Counter(w["category"] for r in reviews for w in r.get("weak_points", []))
    recent = Counter(w["category"] for r in reviews[-5:] for w in r.get("weak_points", []))
    older = Counter(w["category"] for r in reviews[:-5] for w in r.get("weak_points", []))
    lines = [f"{plural(len(reviews), 'clip')} reviewed since {reviews[0]['date']}."]
    lines.append("Most common weak points overall: " + ", ".join(f"{c} ({n})" for c, n in cats.most_common(5)))
    if older:
        lines.append("Last 5 clips vs earlier: " + ", ".join(
            f"{c} {recent.get(c, 0)} vs {older.get(c, 0)}" for c, _ in cats.most_common(5)))
    deaths = [r["death"] for r in reviews[-10:] if r.get("death")]
    if deaths:
        lines.append("Recent deaths: " + " | ".join(deaths[-5:]))
    week = [s for s in sessions if s["start"] >= (date.today() - timedelta(days=7)).isoformat()]
    lines.append(f"This week: {plural(len(week), 'session')}, {plural(sum(s['minutes'] for s in week), 'minute')} played.")
    return "\n".join(lines)


@tool(
    "Check what might be costing FPS while gaming: other screen recorders running in the background "
    "(Windows Game Bar, SteelSeries Moments, OBS...) and heavy background apps.",
)
def performance_check() -> str:
    heavy, recorders = [], []
    names = {"steelseriesgg.exe": "SteelSeries GG is running (only costs FPS if Moments recording is on in GG)",
             "obs64.exe": "OBS",
             "gamebar.exe": "Xbox Game Bar", "broadcastdvrserver.exe": "Windows Game DVR background recording",
             "medal.exe": "Medal.tv", "outplayed.exe": "Outplayed"}
    from core.config import ROOT

    from core import procsnap

    root = str(ROOT).lower()

    def _ours(pid: int) -> bool:      # Jarvis itself (already low priority during games)
        try:
            return (psutil.Process(pid).cwd() or "").lower().startswith(root)
        except (psutil.Error, OSError):
            return False

    # One snapshot of every process (core/procsnap): asking each process for its memory took about 11 seconds on the
    # owner's PC and over 20 when it was busy. The working folder is read only for a process that would be reported.
    for pid, name, rss in procsnap.processes():
        n = (name or "").lower()
        if n in names:
            if not _ours(pid):
                recorders.append(names[n])
        elif rss > 1_500_000_000 and n not in GAMES:
            if not _ours(pid):
                heavy.append(f"{name} ({rss // 1_000_000} MB)")
    try:
        import winreg

        # Only background recording ("record what happened") costs FPS; manual captures don't.
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\GameDVR") as k:
            if winreg.QueryValueEx(k, "HistoricalCaptureEnabled")[0]:
                recorders.append("Windows Game Bar background recording is on (Settings > Gaming > Captures)")
    except OSError:
        pass
    return ("Other recorders: " + ("; ".join(sorted(set(recorders))) or "none") +
            ". NVIDIA Instant Replay alone is enough; extra recorders each cost FPS.\n"
            "Heavy background apps: " + (", ".join(heavy) or "none") + ".")


# -- local real-time coaching (opt-in, preserving existing low-FPS game mode) ----------
@tool(
    "Start Jarvis live gaming coach for Apex or Call of Duty when the owner asks to be coached "
    "while playing. Uses only visible, locally captured HUD evidence. Requires that game's own "
    "calibration. Optional CPU speech; does not use paid AI.",
    {"game": {"type": "string", "enum": ["apex", "cod"],
              "description": "apex for Apex Legends, cod for Call of Duty"}},
    direct=True,
)
def start_live_game_coach(game: str) -> str:
    from core.gamecoach_service import service
    return service.start(game, voice=True)


@tool("Stop real-time Jarvis gaming coach without closing the game.", direct=True)
def stop_live_game_coach() -> str:
    from core.gamecoach_service import service
    return service.stop()


@tool("Check whether Jarvis live gaming coach is running or why it stopped.", direct=True)
def live_game_coach_status() -> str:
    from core.gamecoach_service import service
    return service.status()


@tool(
    "Calibrate Jarvis gaming coach on visible FULL HUD bar for the owner's current game. "
    "Opens a region selector on the Windows desktop; owner selects the full bar and presses Enter. "
    "Requires game HUD on-screen; no API cost.",
    {"game": {"type": "string", "enum": ["apex", "cod"]},
     "kind": {"type": "string", "enum": ["low_health", "low_shield", "low_ammo"]}},
    slow=True, direct=True,
)
def calibrate_live_game_coach(game: str, kind: str) -> str:
    from core.gamecoach_service import service
    return service.calibrate(game,kind)

# -- Nolagin tactical coaching & long-term player development -----------------------------
@tool(
    "Record the player's own report of one Apex or Call of Duty match outcome and "
    "the decision category involved, for personalized improvement across sessions. "
    "Use only when the player actually reports an outcome; never infer a win/loss.",
    {
        "game": {"type": "string", "enum": ["apex", "cod"]},
        "category": {"type": "string", "enum": ["positioning", "decision", "healing",
                     "rotation", "teamplay", "aim", "crosshair", "movement",
                     "objective", "looting"]},
        "result": {"type": "string", "enum": ["win", "loss", "practice"]},
        "note": {"type": "string", "description": "Owner's brief explanation, or empty"},
    }, direct=True,
)
def record_gamecoach_match(game: str, category: str, result: str, note: str) -> str:
    from core.gamecoach_training import report, training_plan
    try:
        report(game, category, result, note)
    except ValueError as e:
        return f"Coaching report not saved: {e}"
    return f"Recorded {game.upper()} {result}, focus: {category}. " + training_plan(game)


@tool(
    "Get Jarvis's evidence-grounded personalized Apex or Call of Duty coaching "
    "plan. Uses existing clip-review weaknesses and player-confirmed outcomes. "
    "Give one drill and be honest when evidence is sparse; no API call.",
    {"game": {"type": "string", "enum": ["apex", "cod"]}},
    direct=True,
)
def gamecoach_training_plan(game: str) -> str:
    from core.gamecoach_training import training_plan
    return training_plan(game)


@tool(
    "Review the user's existing gameplay clip AFTER the game with the PC's "
    "LOCAL vision model instead of the paid Claude clip-review API. "
    "Find visible positioning, rotations, cover and teamwork weaknesses. "
    "Do not use during a match; saves uncertain analysis as clip-review hypotheses.",
    {"game": {"type": "string", "enum": ["apex", "cod"]},
     "clip": {"type": "string", "description": "latest, or existing clip filename"},
     "focus": {"type": "string", "description": "Specific coaching question, or empty"}},
    slow=True, direct=True,
)
def review_clip_locally(game: str, clip: str, focus: str) -> str:
    if game_mode():
        return "I won't run a large vision model while you're gaming. Review after the match."
    clips = find_clips(limit=35)
    if not clips:
        return "No gameplay clips available. Save an NVIDIA Instant Replay clip first."
    item = clips[0] if clip.lower().strip() in ("", "latest", "last") else next(
        (p for p in clips if p.name == clip or clip.lower() in p.name.lower()), None)
    if item is None:
        return "I couldn't find that saved clip."
    try:
        from core import vision
        if not vision.available():
            return "No local vision model is reachable. Local clip review was not run; no API was billed."
        frames = _frames(item)
        if not frames:
            return "I could not decode the video frames."
        chosen = frames[::max(1, len(frames)//8)][:8]
        pieces = [f"Frame {i+1}: timestamp {stamp}" for i,(stamp,_) in enumerate(chosen)]
        prompt = ("These are up to eight sampled gameplay frames in chronological order. "
                  "You cannot see intervening moments or off-screen enemies. "
                  "Never assert a kill, movement pattern, fight cause, or enemy location "
                  "unless actually visible. If uncertain say so. "
                  "Review cover, crosshair, rotation, teamwork, healing, positioning, "
                  "and decision-making where there is evidence. "
                  f"Focus: {focus[:200]}. Times: {'; '.join(pieces)}. "
                  "Return JSON only with keys: summary (string), strengths (list of short strings), "
                  "weak_points (list of objects with category from positioning, crosshair, "
                  "decision, movement, healing, rotation, teamplay, aim, looting, objective, "
                  "time from the labels, what, fix), drills (1-3 list of strings). "
                  "No invented facts; an empty weak_points list is fine.")
        imgs = [base64.b64encode(jpeg).decode("ascii") for _,jpeg in chosen]
        raw = vision.describe(imgs, question=prompt, timeout=180)
        from core.gamecoach_review_parser import sanitize_review
        try:
            reviewed=sanitize_review(raw,[stamp for stamp,_ in chosen])
        except ValueError:
            return "Local model did not produce a grounded JSON review; nothing saved."
        review={"clip":item.name,"game":game,"date":date.today().isoformat(),
                "source":"local_vision_unverified","death":None,**reviewed}
        points=review["weak_points"]
        reviews=storage.load(REVIEWS,[])
        if not isinstance(reviews,list):reviews=[]
        reviews.append(review)
        storage.save(REVIEWS,reviews[-200:])
        weak="; ".join(f"{w['category']}: {w['fix']}" for w in points)
        return ("Local clip study (hypotheses, not verified game facts): "
                f"{review['summary']}. {weak or 'No reliable weak points identified.'} "
                "Correct any inaccurate observation; your verified match reports "
                "take priority over model impressions.")
    except Exception as exc:
        return f"Local clip review couldn't finish ({type(exc).__name__}). No paid model was used."


@tool(
    "Give an immediate short tactical FPS decision based ONLY on facts the player "
    "explicitly tells Jarvis mid-game, such as 'I see two enemies, I'm alone', "
    "'I am low HP in the open', or 'zone closing, I'm fighting'. "
    "This makes no screen claims and uses no paid API.",
    {"game": {"type": "string", "enum": ["apex", "cod"]},
     "situation": {"type": "string", "description": "Player's exact words about this fight"}},
    direct=True,
)
def gamecoach_tactical_advice(game: str, situation: str) -> str:
    from core.gamecoach_tactics import advice_from_player_report
    return advice_from_player_report(game, situation)


@tool(
    "Start the OPTIONAL local enemy-perception tactical gaming coach only when a "
    "game-specific model has passed human-reviewed evaluation and is installed. "
    "Uses bounded CPU ONNX inference, never GPU cloud or game controls. "
    "If a validated model is absent, explain it and leave normal HUD coaching available.",
    {"game":{"type":"string","enum":["apex","cod"]}},
    direct=True,
)
def start_tactical_vision_coach(game:str)->str:
    from core.gamecoach_service import service
    return service.start(game,voice=True,tactical_vision=True)


@tool(
    "Save the player's explicit helpful / not-helpful evaluation of a PARTICULAR "
    "Nolagin tactical callout. Repeated owner feedback adjusts tie-breaking between "
    "similar-urgency tactics; never weakens critical survival alarms.",
    {"game":{"type":"string","enum":["apex","cod"]},
     "kind":{"type":"string","enum":["break_sightline","avoid_open_revive",
             "avoid_pinch","retreat_outnumbered","rotate_zone","play_objective",
             "reset_shields","reload_in_cover","regroup","convert_knock",
             "take_height","contested_low_health","contested_low_shields",
             "contested_low_ammo","unsafe_revive_threat",
             "ring_split_regroup","knock_split_discipline"]},
     "helpful":{"type":"boolean"},
     "reason":{"type":"string","description":"Player explanation or empty"}},
    direct=True,
)
def rate_gamecoach_callout(game:str,kind:str,helpful:bool,reason:str)->str:
    from core.gamecoach_feedback import submit,current_preferences
    try:
        submit(game,kind,helpful,reason)
        bias,samples=current_preferences(game)
    except ValueError as exc:
        return f"Couldn't record coaching feedback: {exc}"
    from core.gamecoach_tactics import TACTICS
    category=TACTICS[kind][2]
    n=samples.get(category,0)
    return (f"Recorded {'helpful' if helpful else 'unhelpful'} feedback for {kind.replace('_',' ')}. "
            f"{n} confirmed feedback samples for {category}. "
            "I'll adjust equal-priority choices after enough reports, "
            "but immediate survival warnings always come first.")


# -- Apex coaching academy: public research -> grounded strategy, never live sightings ------
@tool(
    "Get one researched Apex Legends practice drill by topic. Local versioned "
    "coach lessons from EA 2026 patch notes and tactical guide references. "
    "Do not claim the public YouTube channel/playlist suggestions have been watched.",
    {"category":{"type":"string","enum":["positioning","decision","healing",
                  "rotation","teamplay","aim","crosshair","movement",
                  "objective","looting"]}},
    direct=True,
)
def apex_academy_drill(category:str)->str:
    from core.gamecoach_apex_academy import curriculum
    plans=curriculum(category)
    if not plans:
        return (f"No independently reviewed Apex {category} lesson is ready. "
                "Use player-confirmed clips to design that drill.")
    chosen=plans[0]
    return (f"Apex {category} focus: {chosen['drill']} "
            f"Reference: {chosen['sources'][0]}. "
            "This is general coaching, not a live observation.")


@tool(
    "Review an Apex tactical situation with named facts the PLAYER EXPLICITLY "
    "confirmed, such as 'ring_closing,team_split' or "
    "'shield_broken,fighting'. Never supply a cue from imagination or offscreen "
    "information. This is a post-match/training decision review, NOT a "
    "real-time battle sensor.",
    {"confirmed_cues":{"type":"string","description":"Comma-separated canonical facts said by player"},
     "mode":{"type":"string","enum":["core","ranked","unranked","wildcard","mixtape"]}},
    direct=True,
)
def apex_academy_scenario(confirmed_cues:str,mode:str)->str:
    from core.gamecoach_apex_academy import PLAYS,advice
    allowed={key for p in PLAYS for key in p.required+p.blocked}
    names=[x.strip() for x in confirmed_cues.split(",")]
    if not names or len(names)>12 or any(name not in allowed for name in names):
        return "I need explicit, known Apex facts from the reported situation. No tactical guess made."
    result=advice({name:True for name in names},mode)
    if not result["kind"]:
        return result["advice"]
    return (f"Based on your reported facts: {result['advice']} "
            f"Next drill: {result['drill']} "
            f"Study reference: {result['sources'][0]}.")


@tool(
    "Show what the Apex coach has actually learned from EA and publicly indexed "
    "YouTube channels. Explicitly distinguish sources reviewed from queued "
    "video content that still needs authorized local VOD study.",
    direct=True,
)
def apex_academy_research_status()->str:
    from core.gamecoach_apex_academy import source_status
    s=source_status()
    return (f"Apex academy as of {s['as_of']}: "
            f"{s['reviewed_text_sources']} reviewed text sources, "
            f"{s['youtube_sources_queued']} YouTube study resources queued; "
            f"no individual YouTube VODs analyzed yet. "
            f"{s['rule_count']} evidence-gated tactical practice cases ready.")


@tool(
    "Summarize what Jarvis actually observed and called out in the last local "
    "Apex or COD coaching session. Give at most two drills based on measured "
    "coach events. Never invent match result, unseen deaths, accuracy or win-rate. "
    "Works offline and requires a finished local session.",
    direct=True,
)
def last_gamecoach_session_insights()->str:
    from core.gamecoach_service import service
    from core.gamecoach_session import describe
    if service.status().startswith("Coaching "):
        return "The match is still underway. I'll give your recap when coaching stops."
    if not service.last_report:
        return "No completed local coaching session is available yet."
    return describe(service.last_report.get("session_insights",{}))


@tool(
    "List professional Apex gameplay styles Jarvis can learn from. "
    "Five mouse-and-keyboard pros are prioritized including iiTzTimmy. "
    "Controller pros are available only on request. "
    "No video is described as studied without a local checked review.",
    {"include_controller":{"type":"boolean"}},
    direct=True,
)
def list_apex_pro_coaches(include_controller:bool=False)->str:
    from core.gamecoach_pro_library import catalog
    pros=catalog(bool(include_controller))
    return "; ".join(
        f"{p['name']} ({p['input_device']}): {p['style']}"
        for p in pros
    )+". Verified lessons require locally studied video plus human review."


@tool(
    "Read ONLY locally stored HUMAN-VERIFIED lessons from pro Apex gameplay. "
    "Player's primary input is mouse and keyboard, not controller. "
    "Refuse unsupported controller-specific advice unless controller selected. "
    "Offline only; never invent offscreen events, keypresses or a reviewed video.",
    {"player":{"type":"string","enum":["iitzTimmy","zero","reps","mande",
         "monsoon","verhulst","genburten"]},
     "category":{"type":"string","enum":["decision","rotation","teamplay",
         "positioning","healing","aim","movement","looting",
         "objective","crosshair","mechanics"]},
     "input_device":{"type":"string","enum":["mnk","controller"]}},
    direct=True,
)
def apex_pro_verified_lesson(player:str,category:str,input_device:str="mnk")->str:
    from scripts.gamecoach_pro_vods import lesson
    try:
        info=lesson(player,category,preferred_input=input_device)
        if info["status"]!="verified_lesson":
            return ("No human-verified pro footage lesson is available for "
                    f"{player}, category {category}, input {input_device}. "
                    "The saved video must be analyzed offline and reviewed first.")
        return (f"{player} [{input_device}], {category}: {info['lesson']}. "
                f"Verified source timestamp: {info['source_time_s']} seconds. "
                f"Source upload date: {info.get('source_published') or 'unknown'}. "
                f"Reviewed Legend/map: {info.get('legend_verified') or 'unknown'} / "
                f"{info.get('map_verified') or 'unknown'}. "
                "Older footage does not verify current patch mechanics. "
                "This is a practice principle, never proof of the current live fight.")
    except (OSError,ValueError,ImportError,TypeError) as exc:
        return "Pro video study unavailable locally: "+type(exc).__name__


# Apex Legend/map intel is OFFLINE REFERENCE KNOWLEDGE, not a live sighting.
@tool(
    "Explain any of Apex Legends' current 28 Legends, including passive, tactical, "
    "ultimate, strengths and limitations. Updated to Season 30 Marked Split 2. "
    "Mouse-and-keyboard advice is the owner's primary preference. Source-based "
    "reference only; no claim that an ability is ready in the active match.",
    {"legend":{"type":"string","description":"Apex Legend name, e.g. Axle, Bloodhound, iiTzTimmy's selected Legend"},
     "mode":{"type":"string","enum":["core","ranked","unranked","wildcard"],"description":"Mode explicitly stated by owner; default core"},
     "focus":{"type":"string","description":"Use upgrades for upgrade names/effects (not base abilities), an exact upgrade name such as True Predator for one brief effect, or abilities, class_perks, counterplay, hotfixes, all. Choose the most specific requested focus."}},
    required=["legend"],
    direct=True,
)
def apex_legend_intel(legend:str,mode:str="core",focus:str="all")->str:
    from core.gamecoach_meta import describe
    from core.gamecoach_upgrades import upgrade_summary
    from core.gamecoach_legend_details import summary,focused,requested_focus
    from core.gamecoach_patch_watch import legend_evidence
    from core.gamecoach_upgrades import crosscheck_reference
    try:
        brief=focused(legend,requested_focus(legend,focus),mode)
        if brief is not None:return brief
        p=describe(legend)
        evidence=legend_evidence(legend)
        cross=crosscheck_reference(legend)
        additional="; ".join(x['name']+": "+x['effect'][:220] for x in cross.get('abilities',[]))
        latest=" ".join(f"EA source claim {e.get('published_date') or 'date unknown'} [{e['source_url']}]: {e['excerpt']}"
                        for e in evidence["excerpts"][:2])
        return (
            f"{p['legend']} ({p['class']}; {p['patch']}): "
            f"Passive: {p['passive']}. Tactical: {p['tactical']}. "
            f"Ultimate: {p['ultimate']}. Strengths: {p['strengths']}. "
            f"Limitations: {p['weaknesses']}. "
            f"{upgrade_summary(legend) if mode!='wildcard' else 'Core EVO choices do not apply in Wildcard.'} "
            f"{summary(legend,mode)} "
            f"Latest cached publisher evidence (not game-tested): {latest or 'none'}. "
            f"Unofficial ability cross-check, not current verified: {additional or 'none'}. "
            f"Review sources: {', '.join(p['sources'])}. "
            "Reference knowledge only; readiness and enemy locations are NOT observed."
        )
    except ValueError:
        return "Unknown Legend for this verified Season 30 roster. No made-up ability advice."


@tool(
    "Suggest Apex Legends for one of the six Battle Royale maps, accounting for "
    "map features and mouse-and-keyboard play style. This is a *qualitative* "
    "terrain/style/team fit, NOT real win-rate data or an assertion about the "
    "currently active map. Owner must explicitly name the map and optional "
    "teammates. Uses dated Season 30 rotation.",
    {"map_name":{"type":"string","description":"Specific map the PLAYER states"},
     "style":{"type":"string","enum":["mnk_aggressive","mnk_balanced","mnk_defensive","controller"]},
     "squad_names":{"type":"string","description":"Optional comma-separated confirmed teammate Legend names, or empty"}},
    direct=True,
)
def apex_map_legend_picks(map_name:str,style:str="mnk_aggressive",squad_names:str="")->str:
    from core.gamecoach_meta import recommend
    names=tuple(x.strip() for x in squad_names.split(",") if x.strip())
    try:
        p=recommend(map_name,style,names,limit=5)
    except ValueError as exc:
        return "Cannot reliably score that map/squad: "+str(exc)
    rows="; ".join(
        f"{v['legend']} ({v['class']}; terrain features: {', '.join(v['map_features_matched']) or 'none'}; "
        f"watch out: {v['tradeoff']})" for v in p["recommendations"])
    return (
        f"{p['map']}, {p['season']} — qualitative {style} suggestions: {rows}. "
        f"Map: {p['map_notes']}. Not ranked by actual pick/win statistics. "
        "The owner must verify the current map and team; patch rotation may change."
    )


@tool(
    "Give published Apex Ranked/Pubs maps for the dated Season 30 Marked Split 2. "
    "Do not claim to see the currently active live rotation timer.",
    direct=True,
)
def apex_current_patch_map_rotation()->str:
    from core.gamecoach_meta import rotation,SEASON
    p=rotation()
    if not p["is_currently_verified"]:
        return ("Map rotation snapshot expired; check the current official Apex "
                f"patch notes: {p['source']}")
    return (f"{SEASON} published Core Ranked/Pubs maps: "
            f"{', '.join(p['map_names'])}. "
            "This is published rotation information, NOT the live map countdown.")


@tool(
    "Report the official EA Apex patch/hotfix watcher status: the last real "
    "successful/failed checks and discovered articles. Distinguish indexed "
    "unreviewed source excerpts from verified ability facts, without claiming "
    "a currently live game build or date never seen.",
    direct=True,
)
def apex_patch_update_status()->str:
    from core.gamecoach_patch_watch import report
    s=report()
    return (f"Apex official patch watch: last check {s['last_checked'] or 'never'}; "
            f"{s['pending']} official articles queued, "
            f"{s['studied']} article excerpts indexed. "
            f"Feed states: {s['feeds']}. Complete feed coverage: {s['coverage_complete']}. "
            f"Future season mentioned: {s['season_transition_detected']}; current game build verified: false. {s['meaning']}. "
            "Current ability advice still uses separately verified patch data.")


@tool(
    "Check the public official EA Apex news and hotfix feeds RIGHT NOW, "
    "without paid APIs or large model inference, and index one already "
    "discovered official article when no Apex game is running. "
    "A new URL or excerpt is unreviewed, not a verified buff/nerf.",
    slow=True, direct=True,
)
def apex_refresh_patch_knowledge()->str:
    from core.gamecoach_patch_watch import scan,study_next
    try:
        result=scan()
        if result["status"]=="deferred_game_running":
            return "I'll check official Apex updates after your match, not while you're gaming."
        if result["status"]!="checked":
            return "EA patch feeds were unavailable; existing known patch data has not been replaced."
        item=study_next()
        return (f"EA official patch feeds checked: {result['successful_feeds']} available, "
                f"{result['new']} newly discovered article links, "
                f"{result['pending']} queued. "
                f"Study: {item['status']}. "
                "Any new article excerpts still require verification before changing Legend abilities.")
    except (OSError,ValueError,TimeoutError,TypeError) as exc:
        return f"EA patch refresh didn't finish ({type(exc).__name__}). No game knowledge was overwritten."


@tool(
    "Look up source-attributed recent official Apex patch excerpts mentioning "
    "a named Legend. Evidence is saved locally from EA news/hotfix HTML and "
    "is NOT automatically a currently verified skill, upgrade or nerf. "
    "Never treat a patch mention as an observed fight.",
    {"legend":{"type":"string","description":"Specific Apex Legend name"},
     "focus":{"type":"string","description":"latest for dated hotfix evidence; upgrades for upgrade names; exact upgrade name such as True Predator for one brief effect. Do not use base ability names as upgrade names."}},
    required=["legend"],
    direct=True,
)
def apex_patch_evidence_for_legend(legend:str,focus:str="latest")->str:
    from core.gamecoach_patch_watch import legend_evidence
    try:
        from core.gamecoach_legend_details import focused,requested_focus
        brief=focused(legend,requested_focus(legend,focus))
        if brief is not None:return brief
        evidence=legend_evidence(legend)
    except ValueError:
        return "Unknown Legend in the current verified Apex roster."
    if not evidence["excerpts"]:
        return (f"No newly indexed official EA patch excerpts about {evidence['legend']}. "
                "Existing dated Legend reference remains available.")
    latest=max((x.get('published_date') or '' for x in evidence['excerpts']),default='')
    current=[x for x in evidence['excerpts'] if (x.get('published_date') or '')==latest]
    items="; ".join(
        f"EA article {e['article_title']} [{e['source_url']}]: {e['excerpt']}"
        for e in current[:3])
    return (f"Source excerpts for {evidence['legend']} (NOT fully verified "
            "current ability facts): "+items+
            ". Check article dates, latest hotfixes and the current game build.")
