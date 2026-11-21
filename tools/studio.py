"""Fashion studio: the user's avatar and virtual try-on, generated locally on the GPU.

  data/fashion/avatar/    avatar photos (newest = current avatar) and stylized versions
  data/fashion/garments/  garment images pulled from the web
  data/fashion/tryons/    try-on results

Drop any photo of yourself into data/fashion/avatar/ and it becomes the avatar
if it's the newest file there.
"""
import base64
import html
import io
import ipaddress
import json
import re
import socket
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

from PIL import Image

from core import hud
from core.words import plural
from tools.fashion import _load, _open_page, fdir
from tools.registry import tool

IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp")
MAX_DOWNLOAD = 15 * 1024 * 1024
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"


def _blocks(img: Image.Image, note: str, max_side: int = 1024) -> list:
    view = img.convert("RGB")
    view.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    view.save(buf, format="JPEG", quality=85)
    return [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                         "data": base64.b64encode(buf.getvalue()).decode()}},
            {"type": "text", "text": note}]


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _latest(kind: str) -> Path | None:
    """Newest avatar image: kind 'photo' (anything not stylized) or 'stylized'."""
    if not (fdir() / "avatar").exists():
        return None
    files = [f for f in (fdir() / "avatar").iterdir() if f.suffix.lower() in IMAGE_EXT
             and (f.name.startswith("stylized-") == (kind == "stylized"))]
    return max(files, key=lambda f: f.stat().st_mtime) if files else None


def _body() -> str:
    profile = _load("profile.json", {})
    return profile.get("body", "")


# -- avatar -------------------------------------------------------------------------
@tool(
    "Take a new avatar photo of the user with the camera; it becomes the photo used for try-ons. "
    "First make sure (via the live frame) they are standing back, fully in frame head to toe, "
    "facing the camera, in good light.",
)
def take_avatar_photo() -> list:
    import cv2

    from tools.eyes import capture_frame

    from imaging import engine
    from tools.eyes import _open_camera, eyes

    engine.preload()  # a try-on usually follows; warm the GPU model up meanwhile
    # Take the avatar at the camera's full 1080p: the face is small in a full-body
    # shot, and every extra pixel helps keep the likeness. Eyes pause meanwhile.
    was_on, view = eyes.active, eyes.show_view
    if was_on:
        eyes.stop()
    frame = None
    cam = _open_camera(1920, 1080)
    if cam is not None:
        try:
            for _ in range(8):
                ok, f = cam.read()
                frame = f if ok else frame
        finally:
            cam.release()
    if was_on:
        eyes.start(view)
    if frame is None:
        frame = capture_frame(high_detail=True)
    if frame is None:
        return [{"type": "text", "text": "The camera isn't available."}]
    (fdir() / "avatar").mkdir(parents=True, exist_ok=True)
    path = (fdir() / "avatar") / f"photo-{_stamp()}.jpg"
    cv2.imwrite(str(path), frame, [cv2.IMWRITE_JPEG_QUALITY, 92])
    img = Image.open(path)
    hud.media(path, "New avatar photo")
    return _blocks(img, f"Saved as the new avatar photo ({path.name}). Check it shows the full body; "
                        "if not, ask the user to step back and take it again.")


@tool(
    "Create the user's stylized avatar: a character version of their latest avatar photo with "
    "the same face, skin tone, hair and build. Takes about a minute the first time.",
    {"style": {"type": "string", "description": "e.g. '3D animated movie character', 'anime', 'comic book', 'video game character'"}},
    slow=True,
)
def create_stylized_avatar(style: str) -> list:
    from imaging import engine

    photo = _latest("photo")
    if photo is None:
        return [{"type": "text", "text": "No avatar photo yet. Take one first with take_avatar_photo."}]
    prompt = (f"Turn this person into a full-body {style}. Keep the same face, skin tone, hairstyle, facial hair, "
              f"body proportions and build ({_body() or 'as in the photo'}), and the same clothes. "
              "Standing, facing forward, plain light grey studio background.")
    from imaging.person import crop_to_person, output_size

    me = crop_to_person(Image.open(photo).convert("RGB"))
    w, h = output_size(me)
    result = engine.edit(prompt, [me], width=w, height=h)
    path = (fdir() / "avatar") / f"stylized-{_stamp()}.png"
    result.save(path)
    _show_page("avatar.html", "Your avatar", style, [("Photo", photo), ("Stylized", path)])
    hud.media(path, f"Stylized avatar: {style}")
    return _blocks(result, f"Stylized avatar saved ({path.name}) and shown in the browser.")


def rebuild_avatar(sex: str = "", note: str = "", outfit: str = "", hair: str = "", pose: str = "") -> "Path | None":
    """Regenerate the current person's standard avatar: a photoreal full-body render with their REAL
    face preserved. `outfit`, `hair` and `pose` customise the look (empty = keep the reference's /
    a relaxed standing pose); `note` adds extra direction (e.g. complexion). Returns the saved path,
    or None if there's no photo. Used by the phone avatar tab and the voice tool."""
    from imaging import engine
    from imaging.person import crop_to_person, cutout, output_size, restore_face

    photo = _latest("photo")
    if photo is None:
        return None
    me = crop_to_person(Image.open(photo).convert("RGB"))
    w, h = output_size(me)
    who = {"male": "man", "female": "woman"}.get(sex, "person")
    hair_clause = f"{hair.strip()}" if hair.strip() else "the same hairstyle as the reference"
    if not hair.strip() and sex == "male":
        hair_clause += " with facial hair"
    clothes_clause = f"wearing {outfit.strip()}" if outfit.strip() else "wearing the same clothes as the reference"
    pose_clause = pose.strip() if pose.strip() else "a relaxed, natural, confident standing pose, weight slightly on one leg, facing the camera"
    prompt = (f"A full-body professional studio photograph of a {who}, photorealistic and ultra-detailed: "
              f"lifelike skin with natural texture, sharp focus, crisp facial likeness and accurate face shape, "
              f"cinematic soft key light with a gentle rim light and soft shadows, true-to-life build "
              f"({_body() or 'as in the photo'}); {hair_clause}, {clothes_clause}. Keep the SAME face and skin tone "
              f"as the reference. {note} {pose_clause}. Full body visible head to toe, clean seamless light grey "
              f"studio backdrop. Shot on an 85mm lens, high-end fashion editorial photography, tasteful and flattering.")
    result = engine.edit(prompt, [me], width=w, height=h, steps=8)
    result = restore_face(me, result)  # paste the real face back so the likeness is exact
    result = cutout(result)            # drop the studio background so the avatar floats in the holo chamber
    path = (fdir() / "avatar") / f"stylized-{_stamp()}.png"
    result.save(path)
    return path


# Standard avatar people for the phone avatar tab: Male = Alex (owner), Female = Maya.
# AV_STYLE pins each person's complexion so the body stays on-model across regenerations
# (the real face is preserved separately by restore_face).
AV_SEX = {"owner": "male", "maya": "female"}
AV_STYLE = {
    "owner": "keep the person's own skin tone even across the whole body",
    "maya": "keep the person's own skin tone even across the whole body",
}


def _ollama(keep_alive, model="qwen3.5:9b", timeout=10):
    """Nudge the local Ollama model's residency (0 = unload to free VRAM, -1 = keep resident). An empty prompt only
    loads or unloads ("ok" made qwen3.5 think); a load uses the brain's num_ctx, or Ollama would reload it."""
    import urllib.request
    from core import warmth
    body = {"model": model, "keep_alive": keep_alive, "prompt": "", "stream": False}
    if keep_alive != 0:
        body["options"] = {"num_ctx": warmth.NUM_CTX}
    try:
        urllib.request.urlopen(urllib.request.Request(
            "http://127.0.0.1:11434/api/generate",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"}), timeout=timeout)
    except Exception:
        pass


def _warm_brain() -> None:
    """Reload the model the brain actually uses, for 15 minutes (the keeper in core/warmth renews it). It used to pin
    qwen2.5 here forever, and with that stale model squatting in VRAM, Ollama crashed loading the real brain model."""
    try:
        from core import intelligence
        if intelligence.uses_ollama("fast"):
            _ollama("15m", intelligence.model("fast"), timeout=30)
    except Exception:
        pass


def _loaded_ollama_models() -> list:
    """Models Ollama currently holds in memory (so a new vision model can't silently starve FLUX)."""
    import urllib.request
    try:
        with urllib.request.urlopen("http://127.0.0.1:11434/api/ps", timeout=5) as r:
            return [m["name"] for m in json.loads(r.read()).get("models", []) if m.get("name")]
    except Exception:
        return []


def _vram_free_mib() -> "int | None":
    """Free GPU memory in MiB, or None when it can't be measured."""
    import subprocess
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=5)
        return int(out.stdout.strip().splitlines()[0])
    except Exception:
        return None


# FLUX.2 klein staged (imaging/engine.py) peaks around 9-10 GB on the card; layer-by-layer offload needs ~3 GB.
FLUX_FULL_MIB = 10500
FLUX_LOW_MIB = 3500


def _wait_vram_free(min_free_mib: int = FLUX_FULL_MIB, timeout: int = 20) -> bool:
    """Wait until the GPU has room for FLUX — i.e. the text/vision models released their VRAM after
    an unload. Returns True once there's room (or if VRAM can't be measured, so we still try), and
    False if it stays occupied."""
    import time
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        free = _vram_free_mib()
        if free is None or free >= min_free_mib:
            return True                    # room — or can't measure, so best effort
        time.sleep(1.5)
    return False


def _render_once(person: str, outfit: str, hair: str, note: str, pose: str = "") -> "Path | None":
    """The actual render — runs INSIDE the short-lived child process spawned by render_avatar.
    Frees the resident local models, waits for VRAM, then rebuilds the avatar. Returns the path."""
    from core import users
    sex = AV_SEX.get(person, "person")
    full_note = (AV_STYLE.get(person, "") + " " + (note or "")).strip()
    _free_gpu_for_flux()
    try:
        with users.acting_as(person):
            return rebuild_avatar(sex, full_note, outfit=outfit, hair=hair, pose=pose)
    except Exception as error:            # a CUDA OOM here means the GPU filled up (a game) after the check
        blob = (type(error).__name__ + " " + str(error)).lower()
        if "memory" in blob or "out of memory" in blob or "cuda" in blob or "cublas" in blob or "alloc" in blob:
            raise RuntimeError("GPU_BUSY: the PC's GPU is busy (a game or another app is using it). "
                               "Close it and try again.") from None
        raise


def render_avatar(person: str, outfit: str = "", hair: str = "", note: str = "", pose: str = "") -> "Path | None":
    """Render `person`'s standard avatar on the GPU in a short-lived CHILD process, so FLUX's ~13 GB
    is released the instant it finishes — keeping VRAM free for the always-on voice/text models (the
    in-process FLUX otherwise lingers for minutes and starves Whisper). Returns the saved Path, or
    None if there's no photo. Runs on the GPU PC only."""
    return _run_child("_render_once(%r, %r, %r, %r, %r)" % (person, outfit, hair, note, pose))


RENDER_STALL_S = 240      # a render that reports nothing for this long is stuck (the model load alone is ~10 s)
RENDER_MAX_S = 900        # and nothing runs longer than this, however chatty
_EVICT_EVERY_S = 3


def _evict_brain_while(stop) -> None:
    """While a render holds the card, keep Jarvis's own Ollama model out of it. Something else (a turn, the
    keeper) can load it back mid-render, and then the card is full and every step takes minutes."""
    while not stop.wait(_EVICT_EVERY_S):
        try:
            for m in _loaded_ollama_models():
                _ollama(0, m)
        except Exception:  # noqa: BLE001
            pass


def _run_child(call: str, timeout: int = RENDER_MAX_S, progress=None, stall: int = RENDER_STALL_S) -> "Path | None":
    """Run `studio.<call>` in a short-lived child process and return the Path it prints. The child reports what it
    is doing on stdout ("PROGRESS::..."); `progress(text)` gets each line. It is stopped only when it goes quiet for
    `stall` seconds or runs past `timeout`, and the error says which."""
    import subprocess
    import sys
    import threading
    import time
    from core import warmth
    root = Path(__file__).resolve().parent.parent
    pyexe = root / ".venv" / "Scripts" / "python.exe"
    code = ("import sys; sys.path.insert(0, r%r)\n"
            "from dotenv import load_dotenv; load_dotenv()\n"
            "from tools import studio\n"
            "studio._announce_progress()\n"
            "p = studio.%s\n"
            "print('RENDERED::' + (str(p) if p else 'NONE'), flush=True)\n" % (str(root), call))
    lines, last = [], [time.monotonic()]
    stop = threading.Event()
    warmth.render_lease(True)
    threading.Thread(target=_evict_brain_while, args=(stop,), daemon=True).start()
    proc = subprocess.Popen([str(pyexe if pyexe.exists() else sys.executable), "-c", code], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, errors="replace", cwd=str(root))
    err_lines: list = []

    def pump(stream, sink, is_out):
        for line in stream:
            sink.append(line.rstrip("\n"))
            last[0] = time.monotonic()
            if is_out and line.startswith("PROGRESS::") and progress:
                try:
                    progress(line.split("::", 1)[1].strip())
                except Exception:  # noqa: BLE001
                    pass

    readers = [threading.Thread(target=pump, args=(proc.stdout, lines, True), daemon=True),
               threading.Thread(target=pump, args=(proc.stderr, err_lines, False), daemon=True)]
    for t in readers:
        t.start()
    started, why = time.monotonic(), ""
    try:
        while proc.poll() is None:
            time.sleep(0.5)
            now = time.monotonic()
            if now - started > timeout:
                why = f"The render ran longer than {plural(timeout // 60, 'minute')} and was stopped."
            elif now - last[0] > stall:
                why = f"The render stopped responding for {plural(stall // 60, 'minute')} and was stopped."
            if why:
                proc.kill()
                break
        proc.wait(timeout=30)
    finally:
        stop.set()
        warmth.render_lease(False)
        for t in readers:
            t.join(timeout=5)
        _warm_brain()                      # bring Jarvis's own brain model back for a snappy next reply
    if why:
        raise RuntimeError(why)
    for line in lines:
        if line.startswith("RENDERED::"):
            val = line.split("::", 1)[1].strip()
            return None if val == "NONE" else Path(val)
    err = "\n".join(err_lines).strip()
    if "GPU_BUSY" in err:
        raise RuntimeError("The PC's GPU is busy (a game is running). Close it and try again.")
    if "NO_GARMENT::" in err:
        raise ValueError(err.split("NO_GARMENT::", 1)[1].splitlines()[0].strip())
    raise RuntimeError("avatar render subprocess failed: " + (err[-400:] or "no output"))


def _announce_progress() -> None:
    """Child side: let the image engine's progress lines reach the parent."""
    from imaging import engine
    engine.on_progress = lambda text: print("PROGRESS::" + text, flush=True)


def _free_gpu_for_flux() -> None:
    """Make room for FLUX. Normal desktop apps (browser, Electron apps, overlays) hold a few GB, so
    instead of refusing, a tight GPU switches FLUX to its slower low-VRAM mode; only a GPU that's
    genuinely taken (a game) is refused."""
    import os
    from core import intelligence
    for m in _loaded_ollama_models() or sorted(intelligence.tier_models()):
        _ollama(0, m)                      # unload every resident text/vision model first
    if _wait_vram_free():
        return
    free = _vram_free_mib()
    if free is not None and free < FLUX_LOW_MIB:
        raise RuntimeError("GPU_BUSY: the PC's GPU is busy (a game or another app is using it). "
                           "Close it and try again.")
    os.environ["JARVIS_FLUX_LOWVRAM"] = "1"   # this child process only; slower but it renders


TRYON_STEPS = 8
_TOP_KINDS = ("top", "outerwear", "shirt", "jacket", "coat", "sweater", "hoodie", "dress", "jumpsuit", "suit", "garment")
_BOTTOM_KINDS = ("bottom", "pants", "trousers", "jeans", "shorts", "skirt", "dress", "jumpsuit", "suit", "garment")


def tryon_part(categories) -> str:
    """Which part of the person's own clothes the chosen garments replace: "top", "bottom", "both", or "none" (shoes
    or accessories only: the clothes in the photo stay)."""
    cats = [str(c).lower() for c in categories]
    top = any(k in c for c in cats for k in _TOP_KINDS)
    bottom = any(k in c for c in cats for k in _BOTTOM_KINDS)
    if not top and not bottom:
        return "none"
    if top and not bottom:
        return "top"
    if bottom and not top:
        return "bottom"
    return "both"


def tryon_prompt(who: str, pieces: str, part: str, style: str, body: str, notes: str = "", neutral: bool = True) -> str:
    """The try-on request. When the person's own clothes were blanked to grey (neutral), the prompt says so, so the
    model uses image 1 for identity, pose, hands and shoes only and takes everything worn from the product shots."""
    keep = {"top": "his own trousers and shoes", "bottom": "his own top exactly as it is in image 1, prints included, and his own shoes",
            "both": "his own shoes"}.get(part, "his own shoes")
    if neutral and part == "bottom":
        head = ("Image 1 is the person: keep him, his pose, his hands and his top (with its printed text, numbers and "
                "graphics) exactly as in the photo. The flat grey area on his legs is blank; there are no trousers to "
                "copy from it. The other image shows the new trousers only. ")
    elif neutral:
        head = ("Image 1 is the person: use it for his face, skin, hands, pose and body proportions only. "
                "The flat grey areas on his body are blank placeholders; there is no clothing to copy from them. "
                "The other images show clothing only. ")
    else:
        head = "Image 1 is the person; the other images show clothing only. "
    return (f"{head}A full-body professional studio photograph of the {who} from image 1 wearing {pieces}"
            f"{', with ' + keep + (' (unchanged)' if part == 'bottom' else ' as in image 1') if part != 'both' or neutral else ''}. "
            "Use these exact garments: same colors, fabric, weave and details as the product photos. "
            + ("" if part == "bottom" else "The new garments are plain, with no printed text, numbers, logos or graphics, "
               "unless the product photo itself shows them. ") +
            "They fit realistically on this build with natural folds and correct lengths. "
            f"Keep the person's identity exactly: same face, {style}, hair, body shape ({body or 'as in the photo'}); "
            "same body proportions and arm size as image 1, not more muscular. "
            f"{notes} Relaxed natural standing pose facing the camera, full body visible head to toe, clean seamless "
            "light grey studio backdrop, photorealistic.")


def _wardrobe_tryon_once(person: str, garment_owner: str, garment_ids: list, notes: str = "") -> "Path | None":
    """Photo-faithful try-on, run INSIDE the child process: dress `person` (their own photo, real face
    kept) in `garment_owner`'s actual wardrobe photos. Garments are resolved as their owner, so the
    owner's clothes can go on Maya too. Saves a stylized cutout the Fitting Room shows."""
    from core import users
    from imaging import engine
    from imaging.person import crop_to_person, cutout, neutralise_clothes, output_size, restore_face

    with users.acting_as(garment_owner):
        wardrobe = {i["id"]: i for i in _load("wardrobe.json", [])}
        garments = []
        for gid in garment_ids[:3]:
            found = _garment_path(gid)
            if found is None:
                raise ValueError(f"NO_GARMENT::That wardrobe item ({gid}) has no photo to try on.")
            garments.append((gid, *found))
        categories = sorted({wardrobe.get(g, {}).get("category", "garment") for g in garment_ids})
    _say = lambda t: print("PROGRESS::" + t, flush=True)   # noqa: E731  (the parent shows these)
    _say("Making room on the graphics card")
    _free_gpu_for_flux()
    try:
        # One-time clothes-only product shot of each garment, so the face of whoever wore it in the
        # wardrobe photo can't bleed into the result (cached next to the original photo).
        clothes = []
        for n, (gid, path, desc) in enumerate(garments, 1):
            _say(f"Preparing the clothes photo {n} of {len(garments)}")
            clothes.append(_clothes_only(gid, path, desc))
        with users.acting_as(person):
            photo = _latest("photo")
            if photo is None:
                return None
            me = crop_to_person(Image.open(photo).convert("RGB"))
            w, h = output_size(me)
            who = {"male": "man", "female": "woman"}.get(AV_SEX.get(person, ""), "person")
            pieces = "; ".join(f"the {desc} shown in image {i + 2}" for i, (_, _, desc) in enumerate(garments))
            part = tryon_part(categories)
            try:   # blank the photo's own clothes so their print and logos cannot ghost onto the new garments
                base = me if part == "none" else neutralise_clothes(me.resize((w, h), Image.LANCZOS), part)
                neutral = part != "none"
            except Exception:  # noqa: BLE001  (a failed segmentation must not lose the render)
                base, neutral = me, False
            prompt = tryon_prompt(who, pieces, part, AV_STYLE.get(person, "same skin tone"), _body(), notes, neutral)
            result = engine.edit(prompt, [base] + clothes, width=w, height=h, steps=TRYON_STEPS)
            _say("Putting your real face back and cutting you out")
            result = restore_face(me, result)   # the real face, not a generated look-alike
            result = cutout(result)             # floats in the holo chamber like the standard avatar
            (fdir() / "avatar").mkdir(parents=True, exist_ok=True)
            path = (fdir() / "avatar") / f"stylized-{_stamp()}.png"
            result.save(path)
            return path
    except Exception as error:            # a CUDA OOM here means the GPU filled up after the check
        blob = (type(error).__name__ + " " + str(error)).lower()
        if any(k in blob for k in ("out of memory", "cuda", "cublas", "alloc")):
            raise RuntimeError("GPU_BUSY: the PC's GPU is busy. Close the other app and try again.") from None
        raise


def _prepare_clothes_once(person: str, ids: list) -> None:
    """Child side of "prepare my closet": the clothes-only product shot of each given closet piece (cached next to
    its photo). Nothing is returned; the files are the result."""
    from core import users
    _say = lambda t: print("PROGRESS::" + t, flush=True)   # noqa: E731
    with users.acting_as(person):
        todo = [(gid, *found) for gid in ids if (found := _garment_path(gid)) is not None]
    if not todo:
        return None
    _say("Making room on the graphics card")
    _free_gpu_for_flux()
    with users.acting_as(person):
        for n, (gid, path, desc) in enumerate(todo, 1):
            _say(f"Preparing the clothes photo {n} of {len(todo)}")
            _clothes_only(gid, path, desc)
    return None


def prepare_clothes(person: str, garment_ids: list, progress=None) -> None:
    """Make the clothes-only photos for these closet pieces on the GPU, in a child process (never during a game)."""
    game = game_running()
    if game:
        raise RuntimeError(f"The PC's GPU is busy ({game} is running).")
    _run_child("_prepare_clothes_once(%r, %r)" % (person, [str(g) for g in garment_ids][:12]), progress=progress)


def game_running() -> str:
    """The game on this PC right now ('' when none): by Jarvis's own game mode or by the game's process."""
    try:
        from tools import gaming
        if gaming.game_mode():
            return str(gaming.state.get("game") or "a game")
        from core import acquire
        return acquire.running_game() or ""
    except Exception:  # noqa: BLE001
        return ""


def render_wardrobe_tryon(person: str, garment_owner: str, garment_ids: list, notes: str = "", progress=None) -> "Path | None":
    """Dress `person` in `garment_owner`'s real wardrobe photos on the GPU, in a child process.
    Never while a game runs: the game keeps the graphics card and the brain stays loaded."""
    game = game_running()
    if game:
        raise RuntimeError(f"The PC's GPU is busy ({game} is running). The try-on starts when the game is closed.")
    ids = [str(g) for g in garment_ids][:3]
    return _run_child("_wardrobe_tryon_once(%r, %r, %r, %r)" % (person, garment_owner, ids, notes[:200]), progress=progress)


@tool("Show the user's current avatars (photo and stylized) in the browser.")
def show_avatar() -> str:
    pics = [(label, p) for label, p in (("Photo", _latest("photo")), ("Stylized", _latest("stylized"))) if p]
    if not pics:
        return "No avatar yet. Take an avatar photo first."
    return _show_page("avatar.html", "Your avatar", _body(), pics)


# -- garments ------------------------------------------------------------------------
@tool(
    "Download a clothing image from the web to use for a try-on: a Pinterest pin, store product "
    "page, TikTok/YouTube video page, or a direct image link (find it with web search first). "
    "Returns the image so you can check it actually shows the garment.",
    {
        "url": {"type": "string", "description": "http(s) page or image URL"},
        "description": {"type": "string", "description": "What the garment is, e.g. 'washed black boxy heavyweight tee'"},
    },
    slow=True,
)
def fetch_garment_image(url: str, description: str) -> list:
    from imaging import engine

    engine.preload()
    try:
        img = _download_image(url)
    except Exception as e:
        return [{"type": "text", "text": f"Couldn't get an image from that link ({e}). Try another result."}]
    (fdir() / "garments").mkdir(parents=True, exist_ok=True)
    gid = "g" + uuid.uuid4().hex[:5]
    img.save((fdir() / "garments") / f"{gid}.jpg", quality=92)
    meta = _load("garments.json", {})
    meta[gid] = {"description": description, "source": url, "added": _stamp()}
    (fdir() / "garments.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    return _blocks(img, f"Saved as garment {gid}: {description}. Use this id for try_on.", 768)


def _download_image(url: str) -> Image.Image:
    data, ctype = _get(url)
    if not ctype.startswith("image/"):
        page = data.decode("utf-8", errors="ignore")
        m = (re.search(r'<meta[^>]+property=["\']og:image(?::secure_url)?["\'][^>]+content=["\']([^"\']+)', page)
             or re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image', page)
             or re.search(r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)', page))
        if not m:
            raise ValueError("no preview image on that page")
        data, ctype = _get(urljoin(url, html.unescape(m.group(1))))
    img = Image.open(io.BytesIO(data))
    img.load()
    return img.convert("RGB")


def _get(url: str) -> tuple[bytes, str]:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("only http(s) links")
    for info in socket.getaddrinfo(parsed.hostname, None):
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise ValueError("local network addresses are not allowed")
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,image/*,*/*"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = resp.read(MAX_DOWNLOAD + 1)
        if len(data) > MAX_DOWNLOAD:
            raise ValueError("file too large")
        return data, resp.headers.get_content_type()


def _garment_path(gid: str) -> tuple[Path, str] | None:
    """A fetched garment (g12345) or a wardrobe item with a photo."""
    p = (fdir() / "garments") / f"{gid}.jpg"
    if p.exists():
        return p, _load("garments.json", {}).get(gid, {}).get("description", "garment")
    for item in _load("wardrobe.json", []):
        if item["id"] == gid and item.get("photo"):
            return fdir() / item["photo"], item["name"]
    return None


# -- try-on --------------------------------------------------------------------------
@tool(
    "Virtual try-on: generate an image of the user's avatar wearing the given garments, sized "
    "for their build, and show it in the browser. Garments are ids from fetch_garment_image "
    "(g...) or wardrobe items with photos. Takes ~15-40 s (longer the first time).",
    {
        "garment_ids": {"type": "array", "items": {"type": "string"}, "description": "1-3 garment ids"},
        "avatar": {"type": "string", "enum": ["photo", "stylized"]},
        "notes": {"type": "string", "description": "Styling notes, e.g. 'tee untucked, sleeves to mid-bicep, jeans stacked over sneakers'. Empty if none."},
    },
    slow=True,
)
def try_on(garment_ids: list, avatar: str, notes: str) -> list:
    done = _render_tryon(garment_ids, avatar, notes)
    if isinstance(done, str):
        return [{"type": "text", "text": done}]
    result, out, names, person, garments = done
    _show_page("tryon.html", "Try-on", names, [("You", person)] + [(d, p) for p, d in garments] + [("Result", out)])
    hud.media(out, f"Try-on: {names}", link=str((fdir() / "tryon.html").resolve()))
    return _blocks(result, f"Try-on result ({out.name}), shown in the browser. Judge the fit honestly for the user's build.")


@tool(
    "Try several outfits on the user's avatar and show them side by side to compare (2-4 outfits). "
    "Great for 'which looks better', 'shop my closet' combos, or comparing lookbook picks. "
    "Each outfit takes ~20-30 s.",
    {
        "outfits": {"type": "array", "items": {"type": "object", "properties": {
            "label": {"type": "string", "description": "Short name, e.g. 'Henley + dark jeans'"},
            "garment_ids": {"type": "array", "items": {"type": "string"}}},
            "required": ["label", "garment_ids"], "additionalProperties": False}},
        "avatar": {"type": "string", "enum": ["photo", "stylized"]},
        "notes": {"type": "string", "description": "Styling notes for all outfits, or empty"},
    },
    slow=True,
)
def compare_outfits(outfits: list, avatar: str, notes: str) -> list:
    if not 2 <= len(outfits) <= 4:
        return [{"type": "text", "text": "Give between 2 and 4 outfits to compare."}]
    shots = []
    for o in outfits:
        done = _render_tryon(o["garment_ids"], avatar, notes)
        if isinstance(done, str):
            return [{"type": "text", "text": f"{o['label']}: {done}"}]
        shots.append((o["label"], done[0], done[1]))
    board = _collage([(label, img) for label, img, _ in shots])
    out = (fdir() / "tryons") / f"compare-{_stamp()}.jpg"
    board.save(out, quality=90)
    _show_page("compare.html", "Which fit?", " vs ".join(l for l, _, _ in shots),
               [(label, path) for label, _, path in shots])
    hud.media(out, "Side by side: " + " vs ".join(l for l, _, _ in shots),
              link=str((fdir() / "compare.html").resolve()))
    return _blocks(board, "Outfits side by side, left to right: " + "; ".join(l for l, _, _ in shots)
                   + ". Shown in the browser. Give an honest verdict and pick a winner.", 1400)


def _collage(items: list) -> Image.Image:
    from PIL import ImageDraw, ImageFont

    h = 900
    tiles = []
    for label, img in items:
        tile = img.convert("RGB")
        tile = tile.resize((int(tile.width * h / tile.height), h))
        canvas = Image.new("RGB", (tile.width, h + 54), (8, 14, 22))
        canvas.paste(tile, (0, 0))
        draw = ImageDraw.Draw(canvas)
        try:
            font = ImageFont.truetype("segoeui.ttf", 28)
        except OSError:
            font = ImageFont.load_default()
        draw.text((14, h + 10), label, fill=(220, 238, 251), font=font)
        tiles.append(canvas)
    board = Image.new("RGB", (sum(t.width for t in tiles) + 12 * (len(tiles) - 1), h + 54), (5, 11, 18))
    x = 0
    for t in tiles:
        board.paste(t, (x, 0))
        x += t.width + 12
    return board


def _render_tryon(garment_ids: list, avatar: str, notes: str):
    """Generate one try-on. Returns (image, saved path, garment names, avatar path,
    garments) or an error message."""
    from imaging import engine
    from imaging.person import crop_to_person, output_size, restore_face

    person = _latest(avatar)
    if person is None:
        return f"No {avatar} avatar yet."
    garments = []
    for gid in garment_ids[:3]:
        found = _garment_path(gid)
        if found is None:
            return f"Unknown garment id {gid}."
        garments.append(found)

    # Garment pictures often show a model wearing them; their face would bleed into
    # the result. Each garment gets a one-time clothes-only version first.
    clothes = [_clothes_only(gid, path, desc) for gid, (path, desc) in zip(garment_ids, garments)]

    me = crop_to_person(Image.open(person).convert("RGB"))
    w, h = output_size(me)  # same shape as the photo, so nothing gets recomposed
    pieces = "; ".join(f"the {desc} shown in image {i + 2}" for i, (_, desc) in enumerate(garments))
    wardrobe = {i['id']: i for i in _load('wardrobe.json', [])}
    categories = sorted({wardrobe[g].get('category', 'specified garment') for g in garment_ids if g in wardrobe})
    prompt = (f"Image 1 is the person; the other images show clothing only. Dress the person from image 1 "
              f"in {pieces}. Replace ONLY these selected garment categories: {', '.join(categories) or 'the reference garments'}. "
              "Keep all OTHER clothing exactly as in image 1, including its original colors. "
              "Do not invent matching bottoms for a top; do not add logos, buttons or accessories absent from the references. "
              "Keep the person's identity exactly: the same face, "
              f"skin tone, hair, body shape, height ({_body()}), pose, position, background and lighting "
              f"as image 1. The clothes fit realistically on this build with natural folds, correct "
              f"lengths and the original colors, patterns and logos. {notes} Photorealistic.")
    result = engine.edit(prompt, [me] + clothes, width=w, height=h)
    if avatar == "photo":
        result = restore_face(me, result)  # the real face, not a generated look-alike

    (fdir() / "tryons").mkdir(parents=True, exist_ok=True)
    out = (fdir() / "tryons") / f"tryon-{_stamp()}.jpg"
    result.convert("RGB").save(out, quality=92)
    return result, out, ", ".join(desc for _, desc in garments), person, garments


def clean_shot(path: str, desc: str) -> "Path | None":
    """A clean front-facing product shot of a garment photo, cached as <photo>-clothes.jpg (the 3D dressing room
    projects it onto the avatar's garment). FLUX runs in a short-lived child process with the GPU freed first."""
    p = Path(path)
    flat = p.with_name(f"{p.stem}-clothes.jpg")
    if flat.exists():
        return flat
    if game_running():                     # a game keeps the graphics card: the photo is used as it is
        return None
    return _run_child(f"_clean_shot_child({str(p)!r}, {desc[:200]!r})", timeout=420)


def _clean_shot_child(path: str, desc: str) -> "Path | None":
    _free_gpu_for_flux()
    p = Path(path)
    _clothes_only("", p, desc)
    flat = p.with_name(f"{p.stem}-clothes.jpg")
    return flat if flat.exists() else None


def _clothes_only(gid: str, path: Path, desc: str) -> Image.Image:
    """A product-style picture of just the garment (cached next to the original)."""
    from imaging import engine

    flat = path.with_name(f"{path.stem}-clothes.jpg")
    if flat.exists():
        return Image.open(flat).convert("RGB")
    img = Image.open(path).convert("RGB")
    prompt = (f"Product photo of only the clothing in this image: {desc}. Show just the garments, neatly "
              "displayed on an invisible mannequin against a plain white background, same colors, fabric, "
              "fit, details and logos. No person, no face, no skin, no hands.")
    result = engine.edit(prompt, [img], width=768, height=1024)
    result.convert("RGB").save(flat, quality=92)
    return result


def _show_page(filename: str, title: str, subtitle: str, pics: list[tuple[str, Path]]) -> str:
    cards = ["<div class='grid'>"]
    for label, path in pics:
        rel = Path(path).resolve().relative_to(fdir().resolve()).as_posix()
        big = " style='grid-column:1/-1;max-width:640px'" if label == "Result" else ""
        cards.append(f"<div class='card'{big}><img src='{html.escape(rel)}' style='aspect-ratio:auto;object-fit:contain'>"
                     f"<h3>{html.escape(label)}</h3></div>")
    cards.append("</div>")
    return _open_page(filename, title, subtitle, "".join(cards))
