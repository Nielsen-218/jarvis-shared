"""A real demonstration video for each exercise of Project Captain America, picked by rules (never a model).

The page (app/captain.html) always shows its own animated figure for every exercise, offline; this is the optional
second layer. When he taps "Watch video demo" the page asks for demo(key): a YouTube video found by TITLE search
(tools/pca_music.search: the same hard timeout, cache and failure memory, but its own cache file so an exercise video
can never be picked as a song). Only titles, ids, channels and durations are read; the video plays in YouTube's own
embedded player (youtube-nocookie.com) inside the page, created only after his tap.

The rules, all deterministic:
  * QUERIES: two searches per exercise key ("trap bar deadlift proper form", ...).
  * Accept a result only when it is 20 s to 8 min long, its title holds the exercise's key words (REQUIRED: every group
    must match one of its synonyms), holds none of the exercise's EXCLUDE words (a front squat is not a back squat), and
    none of the BANNED words (fail, funny, reaction, compilation, challenge, vs, motivation, prank, review, and "workout",
    which means a whole routine, unless the exercise is itself a warm-up or cool-down drill).
  * Rank: a PREFERRED coaching/education channel first (for the Army Fitness Test events the U.S. Army ranks above the
    rest), then how well the title matches (form/technique words and the exercise's BONUS words), then the shorter video.
Per person, in fitness/demo.json (written atomically by core/storage): the current video per key, a pinned one ("Keep
this one") and the ids he rejected ("Different video"), which never come back.
"""
import re
import threading
import time

from core import coach_engine as coach
from core import pca_program as program
from core import storage
from tools import fitness, pca_music
from tools import fitness_library as lib

STATE = "fitness/demo.json"
CACHE = "fitness/demo-cache.json"
MIN_S, MAX_S = 20, 8 * 60
PER_QUERY = 10
ID_RX = re.compile(r"[A-Za-z0-9_-]{11}")
KEY_RX = re.compile(r"[a-z_]{1,40}")
_lock = threading.RLock()

# key -> the searches, best first
QUERIES = {
    "deadlift": ("trap bar deadlift proper form", "hex bar deadlift technique"),
    "back_squat": ("barbell back squat proper form", "how to back squat technique"),
    "rdl": ("romanian deadlift proper form", "how to romanian deadlift technique"),
    "box_jump": ("box jump proper form", "how to box jump technique landing"),
    "broad_jump": ("broad jump technique", "standing broad jump how to"),
    "calf_raise": ("standing calf raise proper form", "calf raise technique full range"),
    "hanging_leg_raise": ("hanging leg raise proper form", "hanging leg raise technique"),
    "bench_press": ("barbell bench press proper form", "how to bench press technique"),
    "pull_up": ("pull up proper form", "how to do a pull up technique"),
    "incline_press": ("incline dumbbell press proper form", "incline dumbbell press technique"),
    "chest_supported_row": ("chest supported dumbbell row form", "incline chest supported row technique"),
    "lateral_raise": ("dumbbell lateral raise proper form", "lateral raise technique"),
    "curl": ("incline dumbbell curl proper form", "incline curl technique"),
    "overhead_triceps": ("overhead triceps extension proper form", "overhead cable triceps extension technique"),
    "plank": ("army aft plank standard", "army fitness test plank technique"),
    "hand_release_pushup": ("army hand release push up standard", "army fitness test hand release push up technique"),
    "side_plank": ("side plank proper form", "side plank technique"),
    "dead_bug": ("dead bug exercise proper form", "dead bug core exercise technique"),
    "sprint": ("sprint technique acceleration form", "how to sprint faster technique"),
    "sdc": ("army sprint drag carry standards technique", "aft sprint drag carry technique"),
    "bulgarian_split_squat": ("bulgarian split squat proper form", "bulgarian split squat technique"),
    "nordic_curl": ("nordic hamstring curl proper form", "nordic curl technique"),
    "hip_thrust": ("barbell hip thrust proper form", "hip thrust technique"),
    "farmer_carry": ("farmers carry proper form", "farmer walk technique"),
    "copenhagen_plank": ("copenhagen plank exercise form", "copenhagen adductor plank technique"),
    "pallof_press": ("pallof press proper form", "pallof press technique"),
    "dip": ("chest dip proper form", "how to do dips technique"),
    "cable_crossover": ("low to high cable fly form", "low to high cable crossover technique"),
    "face_pull": ("face pull proper form", "cable face pull technique"),
    "overhead_press": ("overhead press proper form", "standing barbell overhead press technique"),
    "row": ("barbell bent over row proper form", "bent over row technique"),
    "pushdown": ("triceps pushdown proper form", "cable triceps pushdown technique"),
    "run": ("running form technique tips", "proper running form cadence"),
    "chest_fly": ("dumbbell chest fly proper form", "cable chest fly technique"),
    "glute_bridge": ("glute bridge proper form", "glute bridge exercise technique"),
    "hip_abduction": ("hip abduction machine proper form", "banded hip abduction technique"),
    "glute_kickback": ("cable glute kickback proper form", "glute kickback technique"),
    "step_up": ("step up exercise proper form", "how to do step ups technique"),
    "kettlebell_carry": ("army aft kettlebell carry technique", "kettlebell farmer carry form"),
    "kettlebell_swing": ("kettlebell swing proper form", "kettlebell swing technique hinge"),
    "lateral_shuffle": ("army aft lateral shuffle technique", "lateral shuffle drill form"),
    "lunge": ("walking lunge proper form", "lunge technique"),
    "rear_delt_fly": ("rear delt fly proper form", "reverse dumbbell fly technique"),
    "sled_drag": ("army aft sled drag technique", "backward sled drag form"),
    "squat": ("goblet squat proper form", "goblet squat technique"),
    "bike": ("stationary bike setup and form", "rowing machine proper technique"),
    "arm_circles": ("arm circles warm up", "arm circles shoulder warm up how to"),
    "band_pull_apart": ("band pull apart proper form", "band pull apart technique"),
    "box_breathing": ("box breathing technique", "how to do box breathing"),
    "calf_stretch": ("calf stretch against wall", "standing calf stretch how to"),
    "cat_cow": ("cat cow stretch how to", "cat cow exercise form"),
    "childs_pose": ("child's pose stretch how to", "childs pose lat stretch"),
    "couch_stretch": ("couch stretch hip flexor how to", "couch stretch technique"),
    "doorway_pec_stretch": ("doorway pec stretch how to", "doorway chest stretch"),
    "hamstring_stretch": ("hamstring stretch how to", "standing hamstring stretch technique"),
    "high_knees": ("a skip drill technique", "high knees running drill form"),
    "inchworm": ("inchworm exercise how to", "inchworm warm up form"),
    "jumping_jack": ("jumping jacks how to", "jumping jack proper form"),
    "leg_swing": ("leg swings warm up how to", "leg swings dynamic warm up"),
    "pigeon": ("pigeon stretch how to", "pigeon pose hip stretch"),
    "ramp_sets": ("how to do warm up sets before lifting", "ramp up warm up sets strength"),
    "scap_pushup": ("scapular push up proper form", "scap push up how to"),
    "worlds_greatest_stretch": ("world's greatest stretch how to", "worlds greatest stretch technique"),
}

# key -> groups of synonyms; every group must appear in the title (matched at the start of a word: "squat" matches "squats").
# Written against the sibling exercises: a title must name THIS exercise, not just share a word with it.
FORMISH = ("form", "technique", "tutorial", "how to", "proper", "tips", "guide", "explained", "cues", "drill", "standard")
REQUIRED = {
    "deadlift": [("deadlift", "dead lift")], "back_squat": [("squat",)], "rdl": [("romanian", "rdl")],
    "box_jump": [("box jump",)], "broad_jump": [("broad jump", "standing long jump")], "calf_raise": [("calf raise", "heel raise")],
    "hanging_leg_raise": [("hanging",), ("leg raise", "knee raise")], "bench_press": [("bench press", "bench pressing")],
    "pull_up": [("pull up", "pullup", "chin up", "chinup", "lat pulldown", "pulldown")], "incline_press": [("incline",), ("press", "bench")],
    "chest_supported_row": [("chest supported", "seal row", "incline row", "incline dumbbell row", "incline bench row"), ("row",)],
    "lateral_raise": [("lateral raise", "side raise", "lateral delt raise", "side lateral raise")],
    "curl": [("curl",), ("incline", "dumbbell", "bicep")], "overhead_triceps": [("tricep",), ("extension",)], "plank": [("plank",)],
    "hand_release_pushup": [("hand release",), ("push up", "pushup")], "side_plank": [("side plank",)], "dead_bug": [("dead bug", "deadbug")],
    "sprint": [("sprint", "acceleration")], "sdc": [("sprint drag carry", "sdc")],
    "bulgarian_split_squat": [("bulgarian", "rear foot elevated", "rfess")], "nordic_curl": [("nordic",), ("curl", "hamstring", "ham")],
    "hip_thrust": [("hip thrust",)], "farmer_carry": [("farmer", "loaded carry")], "copenhagen_plank": [("copenhagen",)],
    "pallof_press": [("pallof",)], "dip": [("dip",), ("chest", "tricep", "parallel bar", "bar") + FORMISH],
    "cable_crossover": [("cable", "crossover"), ("fly", "flye", "crossover")],
    "face_pull": [("face pull",)], "overhead_press": [("overhead press", "shoulder press", "military press", "ohp")],
    "row": [("row",), ("bent over", "barbell", "dumbbell", "bent row")], "pushdown": [("pushdown", "push down", "pressdown", "press down")],
    "run": [("run",), ("form", "technique", "cadence", "posture", "how to")],
    "chest_fly": [("fly", "flye"), ("chest", "dumbbell", "cable", "pec")], "glute_bridge": [("glute bridge", "hip bridge")],
    "hip_abduction": [("abduct",), ("hip", "glute", "machine", "band", "seated", "standing", "side lying", "cable")],
    "glute_kickback": [("kickback", "kick back", "donkey kick"), ("glute", "cable", "band", "donkey", "booty")],
    "step_up": [("step up", "stepup")],
    "kettlebell_carry": [("carry", "farmer"), ("kettlebell", "kb", "acft", "aft", "army")],
    "kettlebell_swing": [("kettlebell swing", "kb swing")], "lateral_shuffle": [("shuffle",), ("lateral", "side", "acft", "aft", "army", "defensive")],
    "lunge": [("lunge",)], "rear_delt_fly": [("rear delt", "reverse fly", "reverse flye")], "sled_drag": [("sled",), ("drag", "pull")],
    "squat": [("goblet",), ("squat",)], "bike": [("bike", "cycling", "rowing", "rower"), ("form", "setup", "set up", "technique", "how to", "position")],
    "arm_circles": [("arm circle",)], "band_pull_apart": [("band",), ("pull apart",)], "box_breathing": [("box breathing",)],
    "calf_stretch": [("calf",), ("stretch",)], "cat_cow": [("cat cow", "cat camel")], "childs_pose": [("childs pose", "child pose")],
    "couch_stretch": [("couch stretch",)], "doorway_pec_stretch": [("door",), ("pec", "chest"), ("stretch",)],
    "hamstring_stretch": [("hamstring",), ("stretch",)], "high_knees": [("high knee", "a skip")], "inchworm": [("inchworm", "inch worm")],
    "jumping_jack": [("jumping jack",)], "leg_swing": [("leg swing",)], "pigeon": [("pigeon",), ("stretch", "pose")],
    "ramp_sets": [("warm up set", "warmup set", "ramp up set", "warm up sets", "warming up")],
    "scap_pushup": [("scap",), ("push up", "pushup")], "worlds_greatest_stretch": [("worlds greatest", "world greatest")],
}

# key -> words that mean a different exercise (a sibling) or the wrong thing altogether
EXCLUDE = {
    "deadlift": ("romanian", "rdl", "stiff leg", "straight leg", "sumo", "single leg", "deficit", "rack pull", "b stance", "snatch grip"),
    "rdl": ("single leg", "b stance", "kettlebell"),
    "back_squat": ("smith", "safety bar", "front squat", "front foot", "goblet", "split", "bulgarian", "pistol", "hack", "sissy", "box squat",
                   "overhead", "elevated", "jump", "zercher", "belt squat", "landmine", "sumo", "cossack", "wall", "leg press", "bodyweight"),
    "squat": ("split", "bulgarian", "jump", "pistol"),
    "bulgarian_split_squat": ("jump",),
    "lunge": ("jump", "lateral", "side lunge", "curtsy"),
    "box_jump": ("broad", "depth jump", "box squat", "step up"),
    "broad_jump": ("box",),
    "calf_raise": ("stretch", "seated"), "calf_stretch": ("raise",),
    "hanging_leg_raise": ("lying", "toes to bar", "captain"),
    "bench_press": ("incline", "decline", "close grip", "dumbbell", "floor", "smith", "machine", "bench dip"),
    "incline_press": ("walk", "treadmill", "smith", "machine", "push up", "pushup", "fly", "row", "curl"),
    "chest_fly": ("rear", "reverse", "machine"),
    "cable_crossover": ("rear", "reverse", "face pull"),
    "chest_supported_row": ("upright", "bent over", "cable row", "barbell row", "machine"),
    "row": ("rowing", "rower", "upright", "chest supported", "seal row", "incline", "cable", "machine", "renegade", "inverted"),
    "pull_up": ("kipping", "muscle up", "assisted machine", "band pull", "face pull", "butterfly"),
    "dip": ("toes", "hip dip", "bench dip", "dip your", "skinny dip", "chip"),
    "lateral_raise": ("front raise", "rear", "upright row"),
    "overhead_press": ("tricep", "push press", "landmine", "machine", "arnold"),
    "face_pull": ("pull up", "pullup"),
    "curl": ("leg curl", "hamstring", "nordic", "wrist", "hammer", "preacher", "spider", "machine"),
    "overhead_triceps": ("press", "kickback", "pushdown", "push down", "skull", "dip"),
    "pushdown": ("push up", "pushup"),
    "plank": ("side plank", "copenhagen", "reverse plank", "row", "jack", "up down", "updown", "shoulder tap", "walk", "commando", "knee"),
    "side_plank": ("copenhagen", "dip", "reach", "rotation", "star"),
    "nordic_curl": ("walking", "ski", "pole"),
    "hip_thrust": ("glute bridge", "single leg", "machine"), "glute_bridge": ("hip thrust", "single leg"),
    "hip_abduction": ("adduct", "shoulder", "arm abduct", "kidnap", "alien"),
    "glute_kickback": ("tricep", "fire hydrant"),
    "step_up": ("lateral", "jump", "box jump", "crossover", "cross over", "dance", "aerobic", "plyo", "explosive", "movie", "trailer"),
    "farmer_carry": ("suitcase",), "kettlebell_carry": ("suitcase", "overhead carry", "rack carry"),
    "kettlebell_swing": ("american",),
    "lateral_shuffle": ("dance", "card", "music", "playlist"),
    "sled_drag": ("push",),
    "sprint": ("drag carry", "triathlon"),
    "run": ("interview", "dmc", "treadmill workout", "running man", "dance", "temple run", "chicken", "jewels", "song", "lyrics"),
    "bike": ("motorcycle", "mountain", "bmx", "dirt", "motor"),
    "hamstring_stretch": ("nordic", "curl"),
    "jumping_jack": ("plank", "seal"),
    "pigeon": ("bird", "racing", "feed"),
    "high_knees": ("dance",),
}

# key -> words that make a title a better match (on top of the form/technique words every key gets)
BONUS = {
    "deadlift": ("trap bar", "hex bar", "army", "aft", "acft"), "back_squat": ("back squat", "barbell"),
    "hand_release_pushup": ("army", "aft", "acft", "standard"), "plank": ("army", "aft", "acft", "standard", "forearm"),
    "sdc": ("army", "aft", "acft", "standard"), "kettlebell_carry": ("army", "aft", "acft"), "lateral_shuffle": ("army", "aft", "acft"),
    "sled_drag": ("army", "aft", "acft", "backward"), "nordic_curl": ("hamstring",), "cable_crossover": ("low to high", "upper chest"),
    "curl": ("incline",), "overhead_triceps": ("extension",), "row": ("bent over", "barbell"), "run": ("form", "cadence"),
    "sprint": ("acceleration", "mechanics"), "pull_up": ("pull up",), "squat": ("goblet squat",), "hip_thrust": ("barbell",),
}
FORM_WORDS = ("form", "technique", "how to", "tutorial", "proper", "guide", "standard", "correctly", "tips", "explained", "cues", "fix")
BANNED = ("fail", "fails", "funny", "reaction", "react", "compilation", "challenge", "vs", "versus", "motivation", "motivational",
          "prank", "review", "workout", "workouts", "meme", "memes", "asmr", "nsfw", "gone wrong", "dies", "died", "death", "worst",
          "roast", "roasting", "1000 reps", "for 30 days", "every day for", "transformation", "i tried", "vlog", "what happens",
          "ego lift", "ego lifting", "world record", "shorts challenge")
RAW_BANNED = ("18+", "nsfw")               # checked on the raw title: normalising drops the "+"
TEST_EVENT_KEYS = ("deadlift", "hand_release_pushup", "sdc", "plank", "run")   # a world-record test lift can still show the standard
PREFERRED = ("us army", "squat university", "jeff nippard", "renaissance periodization", "athlean x", "alan thrall", "barbell medicine",
             "e3 rehab", "nasm", "nsca", "scott herman fitness", "megsquats", "the run experience", "bodybuilding com")
# The Army Fitness Test events and their parts: the Army's own videos show the test standard, so they rank first.
AFT_KEYS = ("deadlift", "hand_release_pushup", "sdc", "plank", "sled_drag", "lateral_shuffle", "kettlebell_carry")


def known_keys() -> set:
    """Every exercise key the program, its swaps, the warm-ups and the cool-downs can show."""
    drills = {k for _, items in (p for v in lib.WARMUPS.values() for p in v) for k, _ in items} | \
             {k for v in lib.COOLDOWNS.values() for k, _ in v}
    return set(program.EX) | set(fitness.EXERCISES) | set(coach.TAGS) | drills | {"bike"}


def is_drill(key: str) -> bool:
    """A warm-up or cool-down drill (a 'workout' title can be the drill itself), not a trained exercise."""
    return key not in program.EX and key not in coach.TAGS


# ---- the rules (pure) ------------------------------------------------------------------------------------------------
def norm(text) -> str:
    """'Hand-Release Push-Up | U.S. Army' -> 'hand release push up us army' (apostrophes and dots join, the rest splits)."""
    t = str(text or "").lower().replace("'", "").replace("’", "").replace(".", "")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9#]+", " ", t)).strip()


def _has(title: str, phrase: str) -> bool:
    return re.search(r"(?<![a-z0-9])" + re.escape(norm(phrase)), title) is not None


def _whole(title: str, word: str) -> bool:
    return re.search(r"(?<![a-z0-9#])" + re.escape(word) + r"(?![a-z0-9])", title) is not None


def required(key: str) -> list:
    if key in REQUIRED:
        return REQUIRED[key]
    name = (fitness.EXERCISES.get(key) or (key.replace("_", " "),))[0]
    return [(w,) for w in norm(name).split() if len(w) > 2][:2] or [(key.replace("_", " "),)]


def accept(row: dict, key: str) -> bool:
    """A demonstration of THIS exercise, of a sensible length, and not a fail video, a reaction or a whole routine."""
    if not isinstance(row, dict) or not ID_RX.fullmatch(str(row.get("id") or "")):
        return False
    dur = row.get("duration_s")
    if not isinstance(dur, (int, float)) or isinstance(dur, bool) or not MIN_S <= dur <= MAX_S:
        return False
    title = norm(row.get("title"))
    if not title:
        return False
    if any(_whole(title, w) for w in BANNED if not (w.startswith("workout") and is_drill(key)) and not (w == "world record" and key in TEST_EVENT_KEYS)):
        return False
    raw = str(row.get("title") or "").lower()
    if any(w in raw for w in RAW_BANNED):
        return False
    if any(_has(title, w) for w in EXCLUDE.get(key, ())):
        return False
    return all(any(_has(title, s) for s in group) for group in required(key))


def channel_rank(channel: str, key: str) -> int:
    """2: the U.S. Army for an Army Fitness Test event; 1: a preferred coaching channel; 0: anyone else."""
    c = norm(channel)
    hit = next((p for p in PREFERRED if c == p or c.startswith(p + " ")), None)
    if not hit:
        return 0
    return 2 if hit == "us army" and key in AFT_KEYS else 1


SHORT_S = 45                             # under this a clip barely shows a rep: it ranks lower (still accepted)


def match_quality(title: str, key: str, duration_s=None) -> int:
    """How well a title promises a demonstration of THIS exercise: the exercise's own BONUS words count most; generic
    form/technique words count at most twice (a keyword-stuffed title must not beat the specific one); a clip shorter
    than SHORT_S loses two."""
    t = norm(title)
    q = min(2, sum(_has(t, w) for w in FORM_WORDS)) + 3 * sum(_has(t, w) for w in BONUS.get(key, ()))
    return q - (2 if isinstance(duration_s, (int, float)) and duration_s < SHORT_S else 0)


def rank(rows: list, key: str, rejected=()) -> list[dict]:
    """The acceptable results, best first, each id once (the copy that names its channel), never a rejected one."""
    best: dict = {}
    for r in rows:
        if isinstance(r, dict) and r.get("id") not in rejected and accept(r, key):
            if r["id"] not in best or (not best[r["id"]].get("channel") and r.get("channel")):
                best[r["id"]] = r
    return sorted(best.values(), key=lambda r: (-channel_rank(r.get("channel"), key), -match_quality(r.get("title"), key, r["duration_s"]),
                                               r["duration_s"]))


# ---- state and search ------------------------------------------------------------------------------------------------
def _state() -> dict:
    try:
        st = storage.load(STATE, {})
    except OSError:
        st = {}
    st = st if isinstance(st, dict) else {}
    for k in ("current", "pinned", "rejected"):
        st[k] = st.get(k) if isinstance(st.get(k), dict) else {}
    return st


def _clean_key(key) -> str:
    k = str(key or "").strip().lower() if isinstance(key, str) else ""
    return k if KEY_RX.fullmatch(k) and k in known_keys() else ""


def _video(row: dict, pinned: bool) -> dict:
    return {"id": row["id"], "title": str(row.get("title") or "")[:200], "channel": str(row.get("channel") or "")[:120],
            "duration_s": int(row["duration_s"]), "pinned": pinned}


def _queries(key: str) -> list[str]:
    if key in QUERIES:
        return list(QUERIES[key])
    name = (fitness.EXERCISES.get(key) or (key.replace("_", " "),))[0]
    return [f"{name} proper form", f"how to {name} technique"]


def _cached_pool(key: str) -> tuple[list, list]:
    """(rows from the fresh cached searches, the queries not searched yet)."""
    rows, todo = [], []
    for q in _queries(key):
        hit = pca_music.cached(q, CACHE)
        if hit is None:
            todo.append(q)
        else:
            rows += hit
    return rows, todo


def _search(queries: list[str]) -> list:
    """Run the searches not cached yet (no lock held; pca_music caps each one and all of them in time)."""
    found = pca_music.search_all(queries, PER_QUERY, CACHE)
    return [r for q in queries for r in found.get(q) or []]


def _no_video(key: str, searched: bool) -> dict:
    down = time.monotonic() - pca_music._failed.get("search", -1e9) < pca_music.FAIL_S
    msg = ("I could not reach YouTube search just now, so there is no video; the animated figure shows the movement." if down else
           "No video passed my checks for this exercise, so there is none to show; the animated figure shows the movement."
           if searched else "There is no video for this exercise yet.")
    return {"ok": False, "key": key, "video": None, "alternatives": 0, "message": msg}


def _answer(key: str, video: dict, others: int, message: str = "") -> dict:
    by = f" by {video['channel']}" if video.get("channel") else ""
    return {"ok": True, "key": key, "video": video, "alternatives": others,
            "message": message or f"Video{by} on YouTube, chosen automatically; not reviewed by a coach."}


def _pick(key: str, fresh_search: bool) -> dict:
    """The best acceptable video not rejected, searching only for queries with nothing cached. Saves it as current."""
    with _lock:
        st = _state()
        rejected = list(st["rejected"].get(key) or [])
        rows, todo = _cached_pool(key)
    ranked = rank(rows, key, rejected)
    searched = False
    if not ranked and todo and fresh_search:
        rows += _search(todo)
        searched = True
        ranked = rank(rows, key, rejected)
    if not ranked:
        return _no_video(key, searched or not todo)
    video = _video(ranked[0], False)
    with _lock:
        st = _state()
        st["current"][key] = video
        storage.save(STATE, st)
    return _answer(key, video, len(ranked) - 1)


def _others(key: str, st: dict, current_id: str) -> int:
    rows, _ = _cached_pool(key)
    return len([r for r in rank(rows, key, list(st["rejected"].get(key) or [])) if r["id"] != current_id])


# ---- the API ---------------------------------------------------------------------------------------------------------
BAD_KEY = "Which exercise? Send one of the program's exercise keys."
BAD_ID = "That is not a YouTube video id."


def demo(key) -> dict:
    """The video for an exercise: the pinned one, else the current pick, else the best one found (a search runs only
    when nothing is cached). -> {"ok", "key", "video": {id, title, channel, duration_s, pinned} | None, "alternatives",
    "message"}."""
    key = _clean_key(key)
    if not key:
        return {"ok": False, "key": None, "video": None, "alternatives": 0, "message": BAD_KEY}
    with _lock:
        st = _state()
        rejected = set(st["rejected"].get(key) or [])
        for slot in ("pinned", "current"):
            v = st[slot].get(key)
            if isinstance(v, dict) and ID_RX.fullmatch(str(v.get("id") or "")) and v["id"] not in rejected:
                video = dict(v, pinned=slot == "pinned")
                return _answer(key, video, _others(key, st, v["id"]),
                               "Your kept video for this exercise." if slot == "pinned" else "")
    return _pick(key, True)


def demo_next(key, video_id=None) -> dict:
    """'Different video': the one showing is rejected for good (and unpinned), and the next best comes back."""
    key = _clean_key(key)
    if not key:
        return {"ok": False, "key": None, "video": None, "alternatives": 0, "message": BAD_KEY}
    if video_id is not None and not (isinstance(video_id, str) and ID_RX.fullmatch(video_id)):
        return {"ok": False, "key": key, "video": None, "alternatives": 0, "message": BAD_ID}
    with _lock:
        st = _state()
        cur = st["pinned"].get(key) or st["current"].get(key)
        gone = video_id or (cur or {}).get("id")
        if gone:
            st["rejected"][key] = (list(st["rejected"].get(key) or []) + [gone])[-200:]
            if (st["pinned"].get(key) or {}).get("id") == gone:
                st["pinned"].pop(key)
            if (st["current"].get(key) or {}).get("id") == gone:
                st["current"].pop(key)
            storage.save(STATE, st)
    out = _pick(key, True)
    if not out["video"]:
        out["message"] = "No other video passed my checks for this exercise; the animated figure shows the movement."
    return out


def demo_pin(key, video_id=None) -> dict:
    """'Keep this one': the current video stays for this exercise until he asks for a different one or unpins it."""
    key = _clean_key(key)
    if not key:
        return {"ok": False, "key": None, "video": None, "alternatives": 0, "message": BAD_KEY}
    if video_id is not None and not (isinstance(video_id, str) and ID_RX.fullmatch(video_id)):
        return {"ok": False, "key": key, "video": None, "alternatives": 0, "message": BAD_ID}
    with _lock:
        st = _state()
        cur = st["current"].get(key) or st["pinned"].get(key)
        if not isinstance(cur, dict) or (video_id and cur.get("id") != video_id):
            return {"ok": False, "key": key, "video": None, "alternatives": 0,
                    "message": "There is no video showing for this exercise to keep."}
        st["pinned"][key] = dict(cur, pinned=True)
        st["current"][key] = dict(cur, pinned=False)
        storage.save(STATE, st)
        return _answer(key, dict(cur, pinned=True), _others(key, st, cur["id"]), "Kept: this video stays for this exercise.")


def demo_unpin(key) -> dict:
    key = _clean_key(key)
    if not key:
        return {"ok": False, "key": None, "video": None, "alternatives": 0, "message": BAD_KEY}
    with _lock:
        st = _state()
        was = st["pinned"].pop(key, None)
        if not was:
            cur = st["current"].get(key)
            return {**(_answer(key, dict(cur, pinned=False), _others(key, st, cur["id"])) if isinstance(cur, dict) else
                       {"ok": True, "key": key, "video": None, "alternatives": 0}), "message": "That video was not kept."}
        storage.save(STATE, st)
        return _answer(key, dict(was, pinned=False), _others(key, st, was["id"]), "Unpinned: it stays for now, and can change.")
