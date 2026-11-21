"""Keep Jarvis's own models warm so the first thing the owner says after a break is fast, and step aside for games.

Measured 2026-11-08 (core/turntrace): models were kept 15 minutes, so the first sentence after any break hit a cold model:
"How you doing?" took 14.1 s (8.8 s loading the planner, 4.2 s the fast model) and 3.8 s warm. The practice learner
(gpt-oss, 12.7 GB) pushed them out too, and game mode never released them, so a game shared the 16 GB card with ~12 GB of
idle models.

Policy (one place):
  * normal          -> the fast and the planner models stay loaded for hours; if anything pushed them out (practice, a
                       render, another app), a keeper loads them back within a minute, only when the card has room
  * a game running  -> the planner is unloaded (the game gets the memory); only the small fast model stays
  * practice        -> the learner may take the card while the owner is away; the moment it stops, it is unloaded and
                       Jarvis's models are loaded back (core/acquire calls handback())
  * one brain       -> since 2026-11-09 fast and advanced are the same model (qwen3.5:9b, ~6 GB): it is listed once,
                       stays loaded in a game like the fast one always did, and a learner that IS the brain is never unloaded
Nothing here changes what a model says; it only decides what sits in graphics memory.
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.request

KEEP = os.getenv("JARVIS_MODEL_KEEP_ALIVE", "8h")       # how long Jarvis's models stay loaded after use
IN_GAME_KEEP = "2m"
CHECK_SECONDS = 60
NEED_GB = (6.2, 8.0)                     # api/ps size_vram on the owner's card: qwen3.5:9b 6.0 GB at num_ctx 16384 (2026-11-09); a 2nd model
HEADROOM_GB = 0.4                        # both loaded + Whisper = 15.5 of 16.3 GB, which runs fine
_thread = None
_state = {"game": False, "last_warm": 0.0, "render": 0.0}
RENDER_LEASE_S = 20 * 60                 # a render that never reported back must not hold the models out for ever


def render_lease(on: bool) -> None:
    """A photoreal try-on holds the card (tools/studio._run_child): the keeper must not load the brain back under it,
    or the card is full and the render crawls (measured 2026-11-12: no step in 9 minutes)."""
    _state["render"] = time.time() if on else 0.0


def rendering() -> bool:
    return bool(_state["render"]) and time.time() - _state["render"] < RENDER_LEASE_S


def _ollama() -> str:
    return os.getenv("JARVIS_OLLAMA_URL", "http://127.0.0.1:11434/v1").rstrip("/").removesuffix("/v1")


def _post(path: str, body: dict, timeout: float = 120) -> bool:
    try:
        req = urllib.request.Request(_ollama() + path, json.dumps(body).encode(), {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp.read(4096)
        return True
    except OSError:
        return False


def resident() -> dict:
    try:
        with urllib.request.urlopen(_ollama() + "/api/ps", timeout=3) as resp:
            return {m.get("name"): int(m.get("size_vram") or 0) for m in json.loads(resp.read(128001)).get("models", [])}
    except (OSError, ValueError):
        return {}


def loaded() -> dict | None:
    """{model: (size, size_vram)} for every model Ollama has loaded now, in bytes; None when Ollama can't be asked."""
    try:
        with urllib.request.urlopen(_ollama() + "/api/ps", timeout=3) as resp:
            return {m.get("name"): (int(m.get("size") or 0), int(m.get("size_vram") or 0))
                    for m in json.loads(resp.read(128001)).get("models", [])}
    except (OSError, ValueError):
        return None


def jarvis_models() -> list:
    """[fast, advanced] as configured (only those Ollama serves), each name once: with one brain it is [brain]."""
    try:
        from core import intelligence
        if not intelligence.enabled():
            return []
        return list(dict.fromkeys(intelligence.model(r) for r in ("fast", "advanced") if intelligence.uses_ollama(r)))
    except Exception:  # noqa: BLE001
        return []


def gaming() -> bool:
    try:
        from tools import gaming as g
        if g.game_mode():
            return True
    except Exception:  # noqa: BLE001
        pass
    try:
        from core import acquire
        return bool(acquire.running_game())
    except Exception:  # noqa: BLE001
        return False


def keep_alive(model: str) -> str:
    """The keep-alive to send with a request for `model`: hours normally; minutes for the planner while a game runs."""
    models = jarvis_models()
    if _state["game"] and len(models) > 1 and model == models[1] and model != models[0]:
        return IN_GAME_KEEP
    return KEEP


def room_for(gb: float) -> bool:
    """Is there free graphics memory for a model this size (Whisper, the voice and the desktop count too)?"""
    try:
        from core import intelligence
        free = intelligence._free_vram_gb()
    except Exception:  # noqa: BLE001
        free = None
    return free is None or free >= gb + HEADROOM_GB


def unload(name: str) -> bool:
    return _post("/api/generate", {"model": name, "keep_alive": 0}, timeout=15)


NUM_CTX = 16384   # MUST match the turns' own requests (core.intelligence._ollama_body): another size makes Ollama reload


def ollama_fields(name: str, fallback_keep="10m") -> dict:
    """The request fields every direct Ollama caller shares, so a call to the brain from the eyes, the judge or a lesson
    neither reloads it (same num_ctx), nor shortens its keep-alive, nor leaves qwen3.5 thinking for half a minute.
    Merge them in: body.update(ollama_fields(name)); add your own options (temperature) to body["options"] after."""
    fields = {"keep_alive": keep_alive(name) if name in jarvis_models() else fallback_keep, "options": {"num_ctx": NUM_CTX}}
    try:
        from core import intelligence
        if intelligence.thinks(name):
            fields["think"] = False
    except Exception:  # noqa: BLE001
        pass
    return fields


def load(name: str) -> bool:
    """Load a model into graphics memory without generating anything (an empty prompt), with the context size real turns
    use, or refresh its keep-alive if it is already loaded (a no-op for Ollama otherwise)."""
    return _post("/api/generate", {"model": name, "prompt": "", "keep_alive": keep_alive(name),
                                   "options": {"num_ctx": NUM_CTX}}, timeout=180)


def warm(reason: str = "", refresh: bool = False) -> list:
    """Load whichever of Jarvis's models are missing. -> the names loaded. During a game only the fast one."""
    models = jarvis_models()
    if not models or rendering() or (reason != "practice stopped" and practising()):
        return []
    want = models[:1] if _state["game"] else models
    have = resident()
    loaded = []
    for i, name in enumerate(want):
        if name in have:
            if refresh:
                load(name)              # same settings: only its keep-alive is renewed (a model loaded with 15 min stays)
            continue
        if not room_for(NEED_GB[min(i, 1)]):
            break                       # something else holds the card (practice, a render): never fight it for memory
        if load(name):
            loaded.append(name)
    if loaded:
        _state["last_warm"] = time.time()
        _note(f"warmed {','.join(loaded)}" + (f" ({reason})" if reason else ""))
    return loaded


PRACTICE_MARKER = "practice-active"      # data/learning/: written by the practice watcher while a cycle runs


def practising() -> bool:
    """A practice cycle holds the card (its model unloads between calls, which looked like free room: the keeper and the
    learner took turns evicting each other, 2026-11-08). A marker older than 3 h is a crash leftover and is ignored."""
    try:
        from core import storage
        marker = storage.DATA / "learning" / PRACTICE_MARKER
        return marker.exists() and time.time() - marker.stat().st_mtime < 3 * 3600
    except OSError:
        return False


def game_started() -> None:
    _state["game"] = True
    models = jarvis_models()
    if len(models) > 1 and models[1] in resident() and unload(models[1]):
        _note(f"game: unloaded {models[1]} so the game gets the graphics memory")


def game_ended() -> None:
    _state["game"] = False
    warm("game ended")


def handback(learner: str = "") -> list:
    """Practice stopped (the owner is back, or it finished): free the learner's memory and load Jarvis's models now."""
    if learner and learner in resident() and learner not in jarvis_models():   # the learner may BE the brain
        unload(learner)
    return warm("practice stopped")


def _note(text: str) -> None:
    print(f"[vram] {text}", flush=True)
    try:
        from core import intelligence
        intelligence.audit("vram", text[:80])
    except Exception:  # noqa: BLE001
        pass


def first_turn() -> None:
    """Right after a start, the first thing he says should not pay for what could be done before it (live 2026-11-12,
    76 s after a restart: "How's it going?" 6.1 s with 3.8 s to the model's first token, the next line 9.0 s with 6.1 s
    in the gateway of which only 2.5 s was a model call). So, once the model is loaded: the chat persona's prompt is read
    (intelligence.prewarm_chat) and the gateway's context (the app index, the skill cards) is built once. Local, free,
    and never in the way of a turn that is already running; a failure only leaves the first turn as slow as it was."""
    try:
        from core import config, intelligence
        intelligence.prewarm_chat(config.load_persona_core(None, "owner"), wait=True)
    except Exception:  # noqa: BLE001
        pass
    try:
        from core import gateway_context, users
        with users.acting_as("owner"):
            gateway_context.build("how is it going", [])
    except Exception:  # noqa: BLE001
        pass
    rehearse()


# One of each kind of line, so every decider's patterns are compiled and every module a turn imports is loaded.
_REHEARSAL = ("how is it going", "why is the sky blue", "open notepad and type hello", "what time is it", "thanks")


def rehearse() -> float:
    """Walk the deciders a turn runs BEFORE any model is asked, once at start, on a few stock lines. Measured 2026-11-14
    in a fresh process: the first chat turn took 1.8 to 2.6 s with a 0.5 s model call, the second 0.4 to 0.6 s; the
    difference was modules imported and patterns compiled on first use. Only functions that DECIDE are called: nothing
    here runs a skill, asks a model, speaks, or writes a file, and each step may fail without stopping the rest.
    Returns the seconds it took."""
    started = time.monotonic()
    steps = []
    import importlib
    for name in ("coachintent", "contextfit", "convsummary", "langintent", "pcfast", "quick", "recorderintent",
                 "studyintent", "turnstate", "webintent"):
        try:                                 # one module that fails to import leaves the others loaded
            importlib.import_module(f"core.{name}")
        except Exception:  # noqa: BLE001
            pass
    try:
        from core import chatturn, gateway, gateway_context, intelligence, screenguide, selfcard, speech, users
        with users.acting_as("owner"):
            ctx = gateway_context.build(_REHEARSAL[0], [])       # built once: the stock lines only need its shape

        def decide(line: str) -> None:
            history = [{"role": "user", "content": line}]
            with users.acting_as("owner"):
                gateway.plainly_talk(line, ctx, [])
            chatturn.plain_talk(line)
            chatturn.plan("fast", history, [], False, None)
            intelligence.small_talk(line, False)
            intelligence.about_them(line)
            intelligence.just_asking(line)
            selfcard.answer(line)
            screenguide.asked(line)

        def polish(line: str) -> None:
            voice = speech.Polisher(lambda sentence: None, asked=line, earlier=[("How are you?", "Well enough, thank you.")])
            for sentence in ("Well enough, thank you.", "It is a quiet afternoon here."):
                voice(sentence)
            voice.finish()

        steps = [(fn, line) for line in _REHEARSAL for fn in (decide, polish)]
    except Exception:  # noqa: BLE001  (an import failed: nothing to rehearse, the first turn is as slow as it was)
        pass
    for fn, line in steps:
        try:
            fn(line)
        except Exception:  # noqa: BLE001
            pass
    return time.monotonic() - started


def _loop() -> None:
    warm("start", refresh=True)
    first_turn()
    ticks = 0
    while True:
        time.sleep(CHECK_SECONDS)
        try:
            playing = gaming()
            if playing and not _state["game"]:
                game_started()
            elif not playing and _state["game"]:
                game_ended()
            elif not playing:
                ticks += 1
                warm("reloaded after something else used the card", refresh=ticks % 30 == 0)   # renew every ~30 min
        except Exception:  # noqa: BLE001  (the keeper must never take Jarvis down)
            pass


def start() -> None:
    """The keeper (Jarvis's process only, on the gaming PC: the laptop server relays model calls and has no GPU)."""
    global _thread
    if os.name != "nt" or os.getenv("JARVIS_ROLE", "desk") == "server":
        return
    if _thread is None:
        _thread = threading.Thread(target=_loop, name="model-warmth", daemon=True)
        _thread.start()
