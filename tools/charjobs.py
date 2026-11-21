"""Find the character the owner means, then rig it: the model open in Blender, a file by name, or a picture.

One resolver in front of rig_character / image_to_character:
  1. a path or picture the owner gave        -> use it
  2. the model open in Blender               -> use the saved file; with unsaved edits use Blender's own autosave
                                               (read-only copy) and say so; never touches the open session
  3. a name ("the vanguard")                 -> search data / workspace / Downloads
  4. ambiguous or nothing                    -> ASK, never guess
Everything is read-only on the owner's files and writes only new files in the Blender workspace.
"""
import os
import re
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

from core import craftjobs, storage
from core.words import plural
from tools import pcops, rigging
from tools.registry import tool

EXTS = (".glb", ".gltf", ".blend")
IMAGES = (".png", ".jpg", ".jpeg", ".webp")
SKIP_DIRS = {"rigged", "converted", "generated", "node_modules", ".git", "__pycache__", ".venv"}
GENERIC = {"my", "the", "a", "an", "character", "characters", "model", "models", "3d", "rig", "rigged", "rigging", "please",
           "jarvis", "file", "one", "this", "that", "open", "find", "for", "me", "game", "ready", "blend", "glb", "of", "with",
           "to", "it", "can", "you", "and", "in", "on", "is", "named", "called", "saved", "version", "use", "from", "scene",
           "blender", "current", "have", "got", "had", "made", "new", "newest", "latest", "last", "recent", "recently", "working", "was", "just",
           "want", "need", "like", "some", "any", "all", "do", "does", "did", "are", "am", "i", "im", "ive", "currently",
           # describing WHAT it is, not WHICH one: "rig this human in my blender" names no file
           "human", "humanoid", "person", "figure", "avatar", "mesh", "guy", "man", "woman", "him", "her", "my"}
TITLE = re.compile(r"^(\*\s*)?(.+?)\s*\[(.+?\.blend)\]", re.I)        # Blender's title: "* name [C:\path\file.blend]"
_OPEN_WORDS = re.compile(r"\b(open|current|this|that|here|blender|scene|one i have)\b", re.I)


def _words(text):
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in GENERIC and len(w) > 1}


def open_blender_models():
    """Models open in Blender right now: [{path, unsaved, pid, title, autosave?, autosave_age_s?}]."""
    found = []
    for w in pcops.windows():
        if w["process"].lower() != "blender.exe":
            continue
        m = TITLE.match(w["title"])
        info = {"path": m.group(3) if m else None, "unsaved": w["title"].lstrip().startswith("*"), "pid": w["pid"],
                "title": w["title"]}
        if m:
            auto = Path(os.environ.get("TEMP", "")) / f"{Path(m.group(3)).stem}_{w['pid']}_autosave.blend"
            if auto.is_file():
                info["autosave"], info["autosave_age_s"] = str(auto), int(time.time() - auto.stat().st_mtime)
        found.append(info)
    return found


def find_files(query="", limit=8):
    """Character files by name words (filename counts most, folder name a little). Newest first on ties."""
    q, hits = _words(query), []
    for root in rigging._allowed_roots():
        if not root.is_dir():
            continue
        for dirpath, dirs, files in os.walk(root):
            depth = len(Path(dirpath).relative_to(root).parts)
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS] if depth < 4 else []
            for f in files:
                low = f.lower()
                if not low.endswith(EXTS) or "_autosave" in low or low == "quit.blend":
                    continue
                p = Path(dirpath) / f
                score = len(q & _words(p.stem)) + 0.5 * len(q & _words(p.parent.name))
                hits.append({"path": str(p), "name": p.stem, "ext": p.suffix.lower(), "score": score, "mtime": p.stat().st_mtime})
    hits.sort(key=lambda h: (h["score"], h["mtime"]), reverse=True)
    return hits[:limit]


def _ago(seconds):
    return plural(int(seconds), "second") if seconds < 90 else plural(int(seconds // 60), "minute")


def _spoken(stem):
    """A file stem the way a person says it: no trailing hash, no long date/number runs, underscores as spaces."""
    s = re.sub(r"[-_]\d{8}-\d{6}$", "", stem)                 # the run stamp JARVIS adds: -20261101-113515
    s = re.sub(r"[-_]?[0-9a-f]{8,}$", "", s)
    s = re.sub(r"[-_]?\d{5,}(?:-\d+)?", "", s)
    return re.sub(r"[_\-]+", " ", s).strip() or stem


_GENERIC_STEMS = {"char_rigged", "char", "model", "scene", "untitled", "mesh", "character", "rigged", "output", "result"}


def _label(o):
    """Every rig is saved as char_rigged.glb ("option 2, char rigged; option 3, char rigged..."): such a file is named by
    its folder ('vanguard reg'), which is what tells them apart."""
    if o["name"].casefold() in _GENERIC_STEMS and o.get("path"):
        return Path(o["path"]).parent.name
    return o["name"]


def _listing(options):
    """Spoken-friendly (no '1.' fragments that the voice reads as separate sentences, no hash names)."""
    kind = {"glb": "GLB", "gltf": "glTF", "blend": "Blender"}
    return "; ".join(f"option {i + 1}, {_spoken(_label(o))}, a {kind.get(o['ext'].lstrip('.'), o['ext'].lstrip('.'))} file from "
                     f"{datetime.fromtimestamp(o['mtime']):%b} {datetime.fromtimestamp(o['mtime']).day}" for i, o in enumerate(options)) + "."


# ---- "number one": a list that was just offered can be answered with its number --------------------------------------------------------------
_PENDING = {}
CHOICE_TTL_S = 600
_ORD = {"one": 0, "first": 0, "1": 0, "two": 1, "second": 1, "2": 1, "three": 2, "third": 2, "3": 2, "four": 3, "fourth": 3, "4": 3,
        "five": 4, "fifth": 4, "5": 4, "last": -1}
_FILLER = {"number", "option", "choice", "item", "the", "a", "please", "yes", "yeah", "go", "ahead", "with", "pick", "select", "use", "take", "i", "ill",
           "want", "will", "mean", "it", "is", "that", "this", "and", "then", "ok", "okay", "sure", "lets", "let", "s", "one's", "of", "them", "for", "me", "rig", "open"}


def remember_choice(tool, options, **args):
    _PENDING.clear()
    _PENDING.update({"tool": tool, "options": list(options), "args": args, "t": time.time()})


def pending():
    """The list offered a moment ago, or None."""
    return dict(_PENDING) if _PENDING and time.time() - _PENDING["t"] < CHOICE_TTL_S else None


def ordinal(text):
    """'number one' / 'option 2' / 'the last one' -> index, else None. Anything with other words is NOT a bare choice."""
    low = re.sub(r"\b(first|second|third|fourth|fifth|last)\s+one\b", r"\1", (text or "").lower())      # "the last one": 'one' is just the noun
    words = re.findall(r"[a-z0-9']+", low)
    picks = {_ORD[w] for w in words if w in _ORD}
    if not picks or len(picks) > 1 or any(w not in _ORD and w not in _FILLER for w in words):
        return None
    return picks.pop()


def pick(text):
    """The offered option the text chooses, or None. Consumes the pending list."""
    p = pending()
    i = ordinal(text) if p else None
    if i is None:
        return None
    try:
        chosen = p["options"][i]
    except IndexError:
        return None
    _PENDING.clear()
    return chosen


@tool("Find the 3D character the owner means: lists models open in Blender right now (and whether they have unsaved "
      "edits) plus character files (.glb/.gltf/.blend) in data, the Blender workspace and Downloads that match a name. "
      "Use for 'find my vanguard character', 'what model do I have open', 'which characters do I have'.",
      {"query": {"type": "string"}}, direct=True)
def find_character(query: str = "") -> str:
    # Spoken aloud: what he asked about first, the open Blender windows in one phrase (live 2026-11-10 it said "Open in
    # Blender: an unsaved scene, saved." four times before the vanguard files).
    parts, models = [], open_blender_models()
    hits, heard = [], set()
    for h in sorted((h for h in find_files(query, 16) if h["score"] > 0 or not _words(query)),
                    key=lambda h: (-float(h["score"]), -float(h["mtime"]))):
        said = (_spoken(_label(h)).casefold(), h["ext"])
        if said not in heard:                   # five regression runs of one rig sound identical: the newest one stands
            heard.add(said)
            hits.append(h)
    if hits:
        parts.append(("Files matching that: " if _words(query) else "Most recent character files: ") + _listing(hits[:6]))
    elif _words(query):
        parts.append(f"I found no character file matching '{query.strip()}' in data, the Blender workspace or Downloads.")
    named = [Path(o["path"]).stem + (" (unsaved edits)" if o["unsaved"] else "") for o in models if o["path"]]
    unnamed = sum(1 for o in models if not o["path"])
    if named:
        parts.append("Open in Blender: " + ", ".join(named) + ".")
    if unnamed and not (hits and _words(query)):
        parts.append(f"Blender also has {plural(unnamed, 'unsaved scene')} open.")
    return " ".join(parts) or "Nothing is open in Blender and I found no character files."


def _start(label, fn, need_gpu_mb=0, quiet=None):
    """Heavy work runs as a background job so the conversation is never blocked for minutes. quiet: a mission's job (see
    craftjobs.quiet_jobs; None = inherit from this thread)."""
    inline = os.getenv("JARVIS_JOBS_INLINE") == "1"
    j = craftjobs.submit(label, fn, need_gpu_mb=need_gpu_mb, wait=inline, quiet=quiet)
    if inline:
        return j["result"]
    return (f"On it: {label}. It runs in the background and takes a few minutes; I will tell you when it is done. "
            "Ask how it is going, or say cancel, any time.")


LAST = "rigged_last.json"
_FILES_IN = re.compile(r"Files are in (.+?)(?::\s+a\s|\.\s|\.$)")


def delivery_root():
    """Where finished characters are copied for the owner: Documents\\JARVIS Rigged Characters (JARVIS_RIGGED_DIR overrides it)."""
    env = os.getenv("JARVIS_RIGGED_DIR")
    if env:
        return Path(env)
    docs = Path.home() / "Documents"
    return (docs if docs.is_dir() else Path.home()) / "JARVIS Rigged Characters"


DOCS_COPY = re.compile(r"A copy is in your Documents folder, in JARVIS Rigged Characters, in a folder called [^.]+\. ")


def copy_to_documents(src):
    """Owner, 2026-11-09: "when he rigs the characters where do they go i cant find them". Copy a finished rig's useful
    files (.glb/.fbx/.blend + rest.png, never logs) into Documents\\JARVIS Rigged Characters\\<rig folder name>.
    The original stays in the Blender workspace. -> the Documents folder, or None if nothing was copied."""
    src = Path(src)
    dest, copied = delivery_root() / src.name, False
    try:
        dest.mkdir(parents=True, exist_ok=True)
        for f in src.iterdir():
            if f.is_file() and (f.suffix.lower() in (".glb", ".fbx", ".blend") or f.name == "rest.png"):
                if (dest / f.name).resolve() != f.resolve():
                    shutil.copy2(f, dest / f.name)
                copied = True
            elif f.is_dir() and f.suffix.lower() == ".fbm":       # the FBX's texture folder: without it the FBX opens untextured
                shutil.copytree(f, dest / f.name, dirs_exist_ok=True)
    except OSError:
        return None
    return dest if copied else None


def last_delivery(max_age_s=None):
    """The most recent finished character {name, folder, original, file, t} (remembered across restarts), or None."""
    rec = storage.load_global(LAST, None)
    if not isinstance(rec, dict) or not rec.get("file") or not Path(rec["file"]).is_file():
        return None
    if max_age_s is not None and time.time() - rec.get("t", 0) > max_age_s:
        return None
    return rec


def _open_file(path):
    """Start Blender on a character file in a NEW window (the open session is never touched). -> (ok, message)."""
    exe = rigging.blender_exe()
    if not exe:
        return False, "Blender is not installed here (looked in Program Files and JARVIS_BLENDER_EXE)."
    path = Path(path)
    if path.suffix.lower() not in EXTS or not path.is_file():
        return False, f"I can only open .blend, .glb or .gltf files, and {path.name} is not one I can see."
    if path.suffix.lower() == ".blend":
        cmd = [str(exe), str(path)]
    else:
        cmd = [str(exe), "--python-expr", "import bpy; bpy.ops.wm.read_factory_settings(use_empty=True); "
               f"bpy.ops.import_scene.gltf(filepath={str(path)!r})"]
    try:
        proc = _spawn(cmd)
        time.sleep(3)
        if proc.poll() is not None:
            return False, f"Blender closed straight away when I tried to open {path.name} (exit code {proc.returncode}). I did not open it."
    except OSError as error:
        return False, f"I could not start Blender: {error}."
    return True, f"Blender is starting with {path.name} in a new window; it can take a few seconds to appear. Your other Blender session was not touched."


def _deliver(result):
    """A finished rig: copy the files somewhere easy to find, remember them, open the character in Blender, and say all of that FIRST."""
    m = _FILES_IN.search(result or "")
    if not m:
        return result
    src = Path(m.group(1).strip())
    if not src.is_dir():
        return result
    result = DOCS_COPY.sub("", result)          # the rig already said where the copy is; this message says it first instead
    dest = copy_to_documents(src)
    copied = dest is not None
    where = dest if copied else src
    pick = next((where / n for n in ("char_rigged.glb",) if (where / n).is_file()), None) or next(iter(sorted(where.glob("*.glb"))), None) \
        or next(iter(sorted(where.glob("*.blend"))), None)
    if pick is None:
        return result
    storage.save_global(LAST, {"name": src.name, "folder": str(where), "original": str(src), "file": str(pick), "t": time.time()})
    opened = ""
    if craftjobs.is_quiet():
        # Mark VI C12: a mission's rig never opens Blender in front of the owner (a mission never takes the foreground).
        opened = "I did not open Blender (this was a background job); say 'open it' when you are ready. "
    elif os.getenv("JARVIS_AUTO_OPEN_RIG", "1") != "0":
        try:
            from tools import gaming
            in_game = gaming.game_mode()
        except Exception:
            in_game = False
        if in_game:
            opened = "You are in a game, so I did not open Blender; say 'open it' when you are ready. "
        else:
            ok, msg = _open_file(pick)
            opened = "I opened it in a new Blender window so you can test it. " if ok else msg + " "
    return (f"{opened}Your rigged character is saved in your Documents folder, in JARVIS Rigged Characters, in a folder called {where.name}"
            f"{'' if copied else ' (inside the Blender workspace; the copy to Documents failed)'}. Say 'where is it' any time, or 'open it' to open it again. " + result)


def _rig(path, kind, profile):
    return _start(f"rigging {Path(path).stem}", lambda: _deliver(rigging.rig_character(str(path), kind, profile)))


@tool("Rig the character the owner means, finding it themselves: the model already open in Blender (uses its autosave "
      "if it has unsaved edits and says so), a file by name ('rig the vanguard'), a path, or a picture (generates the "
      "3D model first). Asks which one if it is unclear instead of guessing. Use for 'rig my character', 'rig the model "
      "I have open', 'make this game ready'. Free, local, read-only on the owner's files.",
      {"which": {"type": "string"}, "kind": {"type": "string", "enum": ["", "humanoid", "quadruped", "creature"]},
       "profile": {"type": "string", "enum": ["blender", "ue5"]}, "use_saved_version": {"type": "boolean"}},
      slow=True, direct=True)
def rig_my_character(which: str = "", kind: str = "", profile: str = "ue5", use_saved_version: bool = False) -> str:
    w = (which or "").strip().strip('"')
    chosen = pick(w) if w else None                      # "number one" after "Which one do you mean?"
    if chosen:
        w = chosen["path"]
    p = Path(w).expanduser() if w else None
    if p is not None and p.is_file():
        if p.suffix.lower() in IMAGES:
            return _start(f"making a 3D character from {p.stem}", lambda: _deliver(rigging.image_to_character(str(p), kind, profile)), need_gpu_mb=7000)
        if p.suffix.lower() in EXTS:
            return _rig(p, kind, profile)
    opened = [o for o in open_blender_models()]
    wants_open = (not w) or (bool(_OPEN_WORDS.search(w)) and not _words(w))
    nothing_open = "Nothing is open in Blender right now. " if (w and wants_open and not opened) else ""
    if opened and wants_open:
        scenes = [o for o in opened if o["path"]]
        if not scenes:
            return "The Blender scene that is open has never been saved. Save it (Ctrl+S) into Downloads, then ask me again."
        if len(scenes) > 1:
            return "Two Blender scenes are open: " + " and ".join(Path(o["path"]).stem for o in scenes) + ". Which one should I rig?"
        o = scenes[0]
        name = Path(o["path"]).stem
        if not o["unsaved"] or use_saved_version:
            return f"Using your open Blender file {name}. " + _rig(o["path"], kind, profile)
        auto = o.get("autosave")
        if auto:
            work = Path(os.getenv("JARVIS_BLENDER_WORKSPACE", storage.DATA / "blender-workspace")) / "converted"
            work.mkdir(parents=True, exist_ok=True)
            copy = work / f"{re.sub(r'[^A-Za-z0-9_-]', '_', name)[:40]}-live-{datetime.now():%Y%m%d-%H%M%S}.blend"
            shutil.copy2(auto, copy)
            return (f"Your open scene {name} has unsaved edits, so I rigged Blender's autosave from {_ago(o['autosave_age_s'])} ago "
                    f"(your session is untouched). If you changed it since, save with Ctrl+S and ask again. ") + _rig(copy, kind, profile)
        return (f"Your open scene {name} has unsaved edits and I can't see them. Save with Ctrl+S and ask again, "
                "or tell me to use the saved version.")
    hits = find_files(w, 8)
    if _words(w):
        strong = [h for h in hits if h["score"] > 0]
        if not strong:
            return (f"I couldn't find a character matching '{w}' in data, the Blender workspace or Downloads. Give me a path, "
                    "drop the file in Downloads, or give me a picture and I will generate it.")
        stems = {_words(h["name"]).__repr__() for h in strong if h["score"] == strong[0]["score"]}
        if len(stems) == 1 or len(strong) == 1:
            return _rig(strong[0]["path"], kind, profile)
        remember_choice("rig_my_character", strong[:5], kind=kind, profile=profile)
        return "Which one do you mean? " + _listing(strong[:5]) + " Say the number."
    recent = [h for h in hits if h["ext"] in EXTS][:5]
    if recent:
        remember_choice("rig_my_character", recent, kind=kind, profile=profile)
        return nothing_open + "Which character should I rig? " + _listing(recent) + " Say the number."
    return "I don't see any character files. Give me a path, a model open in Blender, or a picture."


def _last_result():
    """The character file the last finished rigging job produced (its folder is named in the job's own result), or None."""
    rec = last_delivery()
    if rec:
        return Path(rec["file"])
    j = craftjobs.get()
    if not j or j["state"] != "done":
        return None
    m = _FILES_IN.search(j.get("result") or "")
    if not m:
        return None
    folder = Path(m.group(1))
    for pattern in ("*.blend", "*_rigged.glb", "*.glb", "*.gltf"):
        found = sorted(folder.glob(pattern)) if folder.is_dir() else []
        if found:
            return found[0]
    return None


def _spawn(cmd):
    """Start a detached program. The one place a process is launched, so tests can trap it."""
    flags = (0x00000008 | 0x00000200) if os.name == "nt" else 0       # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    return subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True, creationflags=flags)


_IT = re.compile(r"^\s*(?:it|that|this|them|the (?:rigged|new|finished|last|merged)\b.*|(?:the |my )?(?:character|model|result|file)s?)\s*$", re.I)


@tool("Open a 3D character in Blender in a NEW Blender window (the Blender session already open is never touched): the result "
      "of the job that just finished ('open it', 'open the rigged character'), a file by name, or a path. Asks which one if it is "
      "unclear. Free and local. Use for 'open it in Blender', 'open the rigged vanguard', 'show me the character'.",
      {"which": {"type": "string"}}, direct=True)
def open_in_blender(which: str = "") -> str:
    exe = rigging.blender_exe()
    if not exe:
        return "Blender is not installed here (looked in Program Files and JARVIS_BLENDER_EXE)."
    w = (which or "").strip().strip('"')
    path = None
    chosen = pick(w) if w else None
    if chosen:
        path = Path(chosen["path"])
    elif w and Path(w).expanduser().is_file():
        path = Path(w).expanduser()
    elif not w or _IT.match(w) or not _words(w):
        path = _last_result()
        if path is None:
            return "I don't have a finished character to open. Tell me its name or give me the file path."
    else:
        hits = [h for h in find_files(w, 8) if h["score"] > 0]
        if not hits:
            return f"I couldn't find a character matching '{w}' in data, the Blender workspace or Downloads. Give me the file path."
        best = [h for h in hits if h["score"] == hits[0]["score"]]
        if len({_words(h["name"]).__repr__() for h in best}) > 1:
            remember_choice("open_in_blender", best[:5])
            return "Which one should I open? " + _listing(best[:5]) + " Say the number."
        path = Path(best[0]["path"])
    return _open_file(path)[1]


@tool("Say where the last rigged character was saved, and optionally open that folder in File Explorer. Use for 'where is it', 'where did you put "
      "the rigged character', 'where did you save it', 'show me the folder'.",
      {"open_folder": {"type": "boolean"}}, direct=True)
def where_is_rigged_character(open_folder: bool = False) -> str:
    rec = last_delivery()
    if not rec:
        j = craftjobs.get()
        if j and j["state"] in ("running", "queued"):
            return f"It is not finished yet: {craftjobs.describe(j)} When it is done I will save it to your Documents folder and open it in Blender."
        return "I have no finished rigged character on record. Ask me to rig one and I will tell you exactly where it goes."
    folder = Path(rec["folder"])
    if open_folder and folder.is_dir() and os.name == "nt":
        try:
            _spawn(["explorer.exe", str(folder)])
        except OSError:
            pass
    inside = "Documents, JARVIS Rigged Characters" if delivery_root() in folder.parents else str(folder.parent)
    folder_note = ", and I have opened the folder for you" if open_folder else ", or say 'show me the folder'"     # no backslash inside an f-string expression: Python 3.11 rejects it
    return (f"Your last rigged character, {_spoken(rec['name'])}, is in {inside}, in a folder called {folder.name}. "
            f"It has the .glb for Blender and the .fbx with Unreal bone names. Say 'open it' and I will open it in Blender"
            f"{folder_note}.")


@tool("How is the character job going (rigging or 3D generation running in the background)? Say what step it is on, how "
      "long, or the finished result. Use for 'how is the rig going', 'is it done', 'what happened with the character'.",
      {}, direct=True)
def character_job_status() -> str:
    return craftjobs.describe(craftjobs.get())


@tool("Stop the character job that is running in the background (rigging / 3D generation). Kills its Blender, Hunyuan "
      "or Unreal process; never touches the owner's own files. Use for 'cancel the rig', 'stop that', 'never mind'.",
      {}, direct=True)
def cancel_character_job() -> str:
    j = craftjobs.cancel()
    return f"Stopped {j['label']}." if j else "There is no character job running to stop."


KEEP_NEWEST = 3          # per source model
KEEP_DAYS = 14


def _workspace():
    return Path(os.getenv("JARVIS_BLENDER_WORKSPACE", storage.DATA / "blender-workspace"))


def cleanup_plan(now=None):
    """Which rig/convert runs may go: failed runs and older duplicates beyond the newest KEEP_NEWEST per source, and
    only when older than KEEP_DAYS. The newest passing run of every model is always kept."""
    import json
    now = now or time.time()
    runs = {}
    for sub in ("rigged", "converted"):
        d = _workspace() / sub
        for p in d.iterdir() if d.is_dir() else []:
            stem = re.sub(r"-\d{8}-\d{6}.*$", "", p.name)
            ok = None
            m = p / "manifest.json"
            if m.is_file():
                try:
                    ok = json.loads(m.read_text(encoding="utf-8")).get("ok")
                except Exception:
                    pass
            runs.setdefault((sub, stem), []).append({"path": p, "mtime": p.stat().st_mtime, "ok": ok})
    doomed, kept = [], 0
    for items in runs.values():
        items.sort(key=lambda r: r["mtime"], reverse=True)
        newest_good = next((r for r in items if r["ok"] is not False), None)
        for i, r in enumerate(items):
            old = now - r["mtime"] > KEEP_DAYS * 86400
            if r is newest_good or i < KEEP_NEWEST and r["ok"] is not False or not old:
                kept += 1
            else:
                doomed.append(r["path"])
    return doomed, kept


@tool("Tidy the folders of rigged and converted characters I made: lists (and only on confirm=true deletes) failed runs "
      "and old duplicates, keeping the newest passing run of every model, the 3 newest per model and anything under 14 "
      "days old. Never touches your own files. Use for 'clean up my rig outputs'.",
      {"confirm": {"type": "boolean"}}, direct=True)
def tidy_character_outputs(confirm: bool = False) -> str:
    doomed, kept = cleanup_plan()
    if not doomed:
        return f"Nothing to tidy: {kept} runs kept, none are old failed runs or duplicates."
    size = sum(f.stat().st_size for d in doomed for f in d.rglob("*") if f.is_file())
    mb = round(size / 1e6)
    if not confirm:
        return (f"I could clear {len(doomed)} old runs ({mb} MB) and keep {kept}. Nothing is deleted yet; "
                "say 'confirm cleanup' and I will remove only those folders inside my own workspace.")
    root = _workspace().resolve()
    gone = 0
    for d in doomed:
        if d.resolve().is_relative_to(root) and d.parent.name in ("rigged", "converted"):
            shutil.rmtree(d, ignore_errors=True)
            gone += 1
    return f"Removed {gone} old runs ({mb} MB) from my workspace; {kept} kept."
