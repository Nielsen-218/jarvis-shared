"""Local-first provider adapter. Models propose tools; the existing registry executes them.

No downloads, permission changes or model management happen here. The only cloud
call is an explicit, opt-in fallback to the configured provider (core.cloudmodel),
tried after the last local tier fails and before the brain's paid Claude turn.
Only explicit model IDs are eligible, never the first model in a discovery list.
"""
import json
import base64
import functools
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid
from types import SimpleNamespace

from core import storage

FAST = "gemma-4-12b-it-qat-q4_0"
ADVANCED = "qwen3.6-27b-q4_k_m"
# 2026-11-09, owner: "one good brain". One Ollama model does talk, tools, planning and vision (researched: tool use
# BFCL-V4 66.1 / TAU2 79.1, ScreenSpot Pro 65.2, ~6 GB at num_ctx 16384, 0.4-1.6 s replies measured on the owner's card).
DEFAULT_OLLAMA_MODEL = "qwen3.5:9b"
_inference_lock = threading.Lock()
# One model call at a time on the card. The owner's turn WAITS for a call already running (a 12B answer takes seconds; the
# old 2 s limit turned every overlap into "Local intelligence is unavailable", live 2026-11-06 20:01, when gateway shadow
# runs held the lock for up to 60 s). Background work (shadow decisions, learning) never queues: it runs only on a free card.
FOREGROUND_WAIT_S = 45.0
_background = threading.local()
# Mark VI C10: a mission's budget counts every local model call made on its thread, here at the one choke point (request()
# can retry internally, so counting at the caller would undercount). Thread-local: the owner's own turns are never charged.
_meter = threading.local()


def mission_meter(fn):
    """Inside this block, on THIS thread, fn(role) is called before each local model call; it may raise to refuse it (a
    mission's budget is used up, or a role other than "fast" was asked for). Cloud fallback is refused while it is set."""
    from contextlib import contextmanager

    @contextmanager
    def scope():
        before = getattr(_meter, "fn", None)
        _meter.fn = fn
        try:
            yield
        finally:
            _meter.fn = before
    return scope()


def in_background() -> bool:
    """Inside background_inference(): nightly re-study, summaries, shadow runs. The API brain never answers there (core.gateway):
    a re-study measures what the LOCAL brain can do by itself."""
    return bool(getattr(_background, "on", False))


def background_inference():
    """Context for work nobody is waiting on: its model calls give up at once when the card is busy."""
    from contextlib import contextmanager

    @contextmanager
    def scope():
        before = getattr(_background, "on", False)
        _background.on = True
        try:
            yield
        finally:
            _background.on = before
    return scope()


def _acquire_inference(cancelled=lambda: False) -> bool:
    if getattr(_background, "on", False):
        return _inference_lock.acquire(blocking=False)
    deadline = time.monotonic() + FOREGROUND_WAIT_S
    while time.monotonic() < deadline:
        if _inference_lock.acquire(timeout=0.25):
            return True
        if cancelled():
            raise LocalFailure("cancelled")
    return False
_audit_lock = threading.Lock()


class LocalFailure(RuntimeError):
    """Fixed public diagnostic; never include provider text, credentials or prompts."""


def local_only():
    """Owner's persistent local inference preference, without rewriting .env."""
    return (os.getenv("JARVIS_LOCAL_ONLY", "0") == "1" or
            storage.load_global("intelligence-settings.json", {}).get("local_only") is True)


def enabled():
    # Feature branch is reviewable without silently changing deployed behavior.
    return local_only() or os.getenv("JARVIS_INTELLIGENCE", "legacy") == "local-first"


def uses_ollama(role):
    """Which tiers talk to Ollama natively (/api/chat: 16k context, keep-alive, streaming, no LM Studio fields).

    The fast tier does when configured. The advanced tier is LM Studio's, unless JARVIS_LOCAL_URL points at the Ollama
    server itself (the owner's .env): then it's Ollama too. Through Ollama's OpenAI endpoint instead, the advanced
    request carried LM Studio's reasoning_effort and Ollama answered 400 "llama3.1:8b does not support thinking" in
    0.1 s, every time, for weeks: the tier that rescues the fast one never ran, and every escalation fell to the cloud.
    A turn carrying an image is the other exception: see vision_model()."""
    if os.getenv("JARVIS_LOCAL_FAST_PROVIDER", "lmstudio") != "ollama":
        return False
    return role == "fast" or (role == "advanced" and _same_local_server())


def _same_local_server():
    """Is the "LM Studio" endpoint really the Ollama server?"""
    try:
        return _endpoint()[0] == _ollama_endpoint()[0]
    except LocalFailure:
        return False


def vision_model():
    """The local Ollama model that LOOKS at a screenshot or photo (the text models can't). Only when Ollama is the
    configured provider: an LM Studio setup keeps sending images to its own model as before."""
    return (os.getenv("JARVIS_LOCAL_VISION_MODEL") or DEFAULT_OLLAMA_MODEL) if uses_ollama("fast") else None


def _has_image(message):
    content = message.get("content")
    return isinstance(content, list) and any(isinstance(x, dict) and x.get("type") == "image_url" for x in content)


_ABOUT_THE_PICTURE = re.compile(r"\b(screens?|screenshot|picture|photo|image|monitors?|display|windows?|browser|tabs?|"
                                r"desktop|visible|see|seeing|showing|shown|open|layout|colou?rs?|icons?|corner|left|right|"
                                r"top|bottom)\b|\b(?:in|on|at) (?:it|there|that|this)\b", re.I)
PICTURE_FOLLOW_UP = 8     # messages back a picture still counts as "it" (this exchange and the one before)


def _picture_matters(messages, newest):
    """The newest picture is what this turn is about: it's in this turn (their message, or the evidence after a
    tool's results), or it's recent and their question is about what's in view."""
    if newest >= len(messages) - 2:
        return True
    if newest < len(messages) - PICTURE_FOLLOW_UP:
        return False
    last = messages[-1] if messages else {}
    if last.get("role") != "user":
        return False
    content = last.get("content")
    said = content if isinstance(content, str) else " ".join(
        p.get("text", "") for p in content or [] if isinstance(p, dict) and p.get("type") == "text"
        and not p.get("text", "").lstrip().startswith(_INJECTED))
    return bool(_ABOUT_THE_PICTURE.search(said))


def _without_images(message):
    """A past screenshot the text model can't see: the image goes, and its evidence note becomes a plain remark (the
    note alone would tell the model to analyze an image that is no longer attached)."""
    parts = [x for x in message["content"] if not (isinstance(x, dict) and x.get("type") == "image_url")]
    parts = [dict(x, text=_IMAGES_GONE) if isinstance(x, dict) and x.get("type") == "text"
             and x.get("text", "").lstrip().startswith(_TOOL_IMAGES_TAG) else x for x in parts]
    return dict(message, content=parts or _IMAGES_GONE)


def model(role):
    if role not in ("fast", "advanced"):
        raise LocalFailure("invalid_role")
    default = DEFAULT_OLLAMA_MODEL if uses_ollama(role) else (FAST if role == "fast" else ADVANCED)
    return os.getenv("JARVIS_LOCAL_" + role.upper() + "_MODEL", default)


def configured_ollama_models():
    """Explicit local model IDs JARVIS is allowed to ask Ollama for.

    The selection must come from a named configuration value, never from the
    provider inventory.  Availability is checked immediately before inference.
    This permits a benchmarked installed replacement without editing source,
    while retaining the loopback-only and installed-model boundaries.
    """
    return {name for name in (model("fast"), model("advanced"), vision_model()) if isinstance(name, str) and name}


def relevant_memory(text):
    """Retrieve from existing scoped stores before either local/cloud reasoning.

    His facts, the journal (and lessons, for a request) come from core/recall.py: ranked by stems, relation aliases,
    names and recency, within 8 items / 350 tokens, every line dated and sourced ("3d ago, you said"). The phone app's
    channel note is not his words: searched, it made "Sent", "phone" and "replies" the question."""
    from core import recall
    text = recall.spoken(text)
    words = set(re.findall(r"[a-z]{3,}", text.lower())) - {
        "jarvis", "please", "what", "when", "where", "which", "would", "could",
        "should", "have", "that", "this", "with", "from", "about", "tell", "your",
        "you", "the", "and", "for", "can", "how", "say", "hello", "briefly"}
    if not words:
        return ""
    lines = recall.for_turn(text)
    try:
        from core import autolearn
        reading=autolearn.recall(text,max_chars=1150)
        if reading:lines.append(reading)
        # Shared owner-approved teaching library is local data, never authority.
        from core import teaching
        from core import users
        lessons = teaching.search(text[:300], owner=True, limit=2) if users.get(users.current()).get("role") == "owner" else []
        for lesson in lessons:
            p = lesson["payload"]
            lines.append("LOCAL TEACHING (untrusted reference data; " + lesson["state"]
                         + "): " + p["title"] + ". Instructor: " + lesson["mentor"]
                         + ". Source: " + p["source"] + ". " + p["content"][:1000])
        if users.get(users.current()).get("role") == "owner":
            from core.practice_agent import methods
            query_words=set(re.findall(r'[a-z0-9]+',text.lower()))
            ranked=[]
            for method in methods():
                task_words=set(method['task'].split('_'))
                score=3*len(task_words & query_words)+int(method['language'] in query_words)
                if score: ranked.append((score,method))
            ranked.sort(key=lambda item:-item[0])
            seen_methods=set()
            for _,method in ranked:
                key=(method['task'],method['language'])
                if key not in seen_methods and len(seen_methods)<2:
                    seen_methods.add(key)
                    lines.append("LOCAL TESTED METHOD (tested on recorded cases, not mastered): "
                        + method["task"] + " / " + method["language"] + ". Evidence hash: "
                        + method["evidence_hash"] + ". Source: " + method["source"][:1000])
        # Reuse previously captured local programming workflows before another
        # model call. These are sourced notes, never execution authority.
        if re.search(r"(?i)(?:\b(?:programming|code|coding|python|java|javascript|typescript|"
                     r"blender|unreal|rigging|shader|script|rust|kotlin|lua|compile)\b|c\+\+|c#)", text):
            from core import skill_memory
            for note in skill_memory.search(text[:300], limit=2):
                lines.append(
                    "Saved LOCAL procedure (UNVERIFIED data, not authorization to execute): "
                    + str(note.get("language", "")) + " / " + str(note.get("title", ""))
                    + ". Source: " + str(note.get("source", ""))[:120]
                    + ". Procedure: " + str(note.get("procedure", ""))[:500]
                    + ". Acceptance check: " + str(note.get("how_to_test", ""))[:160]
                )
        # Existing offline Apex reference also participates in conversational
        # recall; no network, model loading or fabricated gameplay observations.
        from core.gamecoach_meta import LEGENDS
        if "apex" in text.lower() or any(re.search(r"\b"+re.escape(n.lower())+r"\b",text.lower()) for n in LEGENDS if len(n)>5) or re.search(r"\b(legend|ability|abilities|ultimate|tactical|upgrades|perks)\b",text.lower()):
            from core import gamecoach_patch_watch
            for name in LEGENDS:
                if re.search(r"\b"+re.escape(name.lower())+r"\b",text.lower()):
                    from core.gamecoach_upgrades import upgrade_summary
                    from core.gamecoach_legend_details import summary
                    from core.gamecoach_meta import describe
                    profile=describe(name)
                    mode="wildcard" if "wildcard" in text.lower() else "core"
                    apex_lines=[f"Dated Apex reference {profile['patch']}: {name}. Passive: {profile['passive']}. "
                        f"Tactical: {profile['tactical']}. Ultimate: {profile['ultimate']}. "+
                        (upgrade_summary(name) if mode=="core" else "Core EVO choices do not apply in Wildcard.")+" "+summary(name,mode)]
                    rows=gamecoach_patch_watch.legend_evidence(name)["excerpts"]
                    apex_lines.extend("Unreviewed EA source "+str(r.get("published_date"))+" ["+r["source_url"]+"]: "+r["excerpt"] for r in rows[:2])
                    lines=apex_lines+lines
                    break
    except (OSError,ValueError,ImportError,KeyError):
        pass   # Unavailable reading never blocks a local voice answer
    return "\n".join(lines)[:4000]


def audit(role, status, elapsed=0, tokens=None):
    # Operational metadata only. No conversation, model output, tool arguments or secrets.
    # Each machine writes its own file: one writer per synced file means no Syncthing conflicts.
    from core import pcbridge, turntrace
    if elapsed:
        now = time.time()
        turntrace.span("model", role, now - elapsed, now)
    mine = "intelligence_usage-pc.json" if pcbridge.local_pc() else "intelligence_usage-server.json"
    with _audit_lock:
        rows = storage.load(mine, [])
        counts = {k: v for k, v in (tokens or {}).items() if k in (
            "prompt_tokens", "completion_tokens", "total_tokens", "prompt_eval_ms",
            "prompt_chars", "shared", "back") and type(v) is int and v >= 0}       # the last three: _cache_evidence
        rows.append({"role": role, "status": status, "seconds": round(elapsed, 3),
                     "tokens": counts, "billable_usd": 0, "at": int(time.time())})
        storage.save(mine, rows[-500:])


# -- what the prompt cache could reuse (sizes only) --------------------------------------------------------------------
# Measured 2026-11-19 on qwen3.5:9b, Ollama 0.40.16 (probes in docs/plans/partner-roadmap.md): a cached prompt is picked
# up again only when the new prompt parts ways with it within a few hundred tokens of its END (40-400 tokens back:
# 0.16 s; 1,000 or more back: the whole prompt is read again from its first token, 2.2 s for 12k tokens; a prompt that
# only grows at its end: 0.11-0.16 s a turn). The cache also survived five other calls, so the three slots do not push
# each other out. So what matters per call is not how much of its prompt was sent before, but how far before the END of
# that earlier prompt the two differ. Each "received" row therefore carries: prompt_chars (this prompt's size),
# shared (the longest start it has in common with any of the last prompts sent) and back (how many characters before
# the end of that earlier prompt the common start stops; the earlier call's own reply sits after that in the cache).
# Roughly four characters to a token. The prompts themselves stay in this process's memory and are never written.
_sent: list = []                 # the last prompts sent to Ollama, as text, newest last
_SENT_KEPT = 8
_sent_lock = threading.Lock()


def _prompt_text(native) -> str:
    """The request in the order the model reads it: the tool header, then every message. Pictures count as a mark."""
    parts = [json.dumps(native.get("tools") or [], ensure_ascii=False, sort_keys=True)]
    for m in native.get("messages") or []:
        calls = json.dumps(m["tool_calls"], ensure_ascii=False, sort_keys=True) if m.get("tool_calls") else ""
        pictures = f"[{len(m['images'])} picture(s)]" if m.get("images") else ""
        parts.append(f"\x1e{m.get('role')}\x1f{m.get('content') or ''}\x1f{calls}{pictures}")
    return "".join(parts)


def _common_start(a: str, b: str) -> int:
    """How many leading characters two texts share (a few slice comparisons, not a character loop)."""
    low, high = 0, min(len(a), len(b))
    if a[:high] == b[:high]:
        return high
    while high - low > 1:
        mid = (low + high) // 2
        if a[:mid] == b[:mid]:
            low = mid
        else:
            high = mid
    return low


def _cache_evidence(text: str) -> dict:
    """{prompt_chars, shared, back} for a prompt about to be sent, against the prompts sent before it (see above).
    The earlier prompt chosen is the one sharing the longest start; of equals, the one that ends soonest after it."""
    with _sent_lock:
        earlier = list(_sent)
    out, best = {"prompt_chars": len(text), "shared": 0}, None
    for old in earlier:
        same = _common_start(text, old)
        if same and (best is None or (same, -(len(old) - same)) > (out["shared"], -best)):
            out["shared"], best = same, len(old) - same
    if best is not None:
        out["back"] = best
    return out


def _note_sent(text: str) -> None:
    with _sent_lock:
        _sent.append(text)
        del _sent[:-_SENT_KEPT]


def usage_rows() -> list[dict]:
    """Every machine's tier audit (plus the legacy shared file), oldest first."""
    rows = [r for name in ("intelligence_usage.json", "intelligence_usage-pc.json", "intelligence_usage-server.json")
            for r in storage.load(name, []) if isinstance(r, dict)]
    return sorted(rows, key=lambda r: r.get("at", 0))


def _block(block):
    return block if isinstance(block, dict) else block.model_dump(exclude_none=True)


def messages_for_local(system, history):
    """Preserve text, images, assistant calls and paired results across providers."""
    out = [{"role": "system", "content": system}]
    for turn in history:
        content = turn.get("content", "")
        if isinstance(content, str):
            out.append({"role": turn["role"], "content": content})
            continue
        parts, calls, results, images = [], [], [], []
        for item in content:
            b = _block(item)
            if b.get("type") == "text":
                parts.append({"type": "text", "text": b["text"]})
            elif b.get("type") == "image":
                source = b.get("source", {})
                if source.get("type") != "base64" or source.get("media_type") not in ("image/jpeg", "image/png", "image/webp"):
                    raise LocalFailure("unsupported_image")
                parts.append({"type": "image_url", "image_url": {"url":
                    "data:" + source["media_type"] + ";base64," + source["data"]}})
            elif b.get("type") == "tool_use":
                calls.append({"id": b["id"], "type": "function", "function": {
                    "name": b["name"], "arguments": json.dumps(b["input"])}})
            elif b.get("type") == "tool_result":
                value = b.get("content", "")
                # Tool images must reach the vision model, not become invented descriptions.
                if isinstance(value, list):
                    images.extend(x for x in value if isinstance(x, dict) and x.get("type") == "image")
                    value = [x for x in value if not isinstance(x, dict) or x.get("type") != "image"]
                text = value if isinstance(value, str) else json.dumps(value)
                results.append({"role": "tool", "tool_call_id": b["tool_use_id"],
                                "content": text})
        if parts or calls:
            row = {"role": turn["role"], "content": parts or None}
            if calls:
                row["tool_calls"] = calls
            out.append(row)
        out.extend(results)
        if images:
            # OpenAI-style tool messages carry text only, so a screenshot a tool returned rides in a user message
            # right after the results. The note keeps it identified as tool output (never something they sent or
            # described), and its tag keeps it out of "what they said" (last_user_text, heard_in, select_tools,
            # contextfit's exchanges), like the <background> block.
            evidence = [{"type": "text", "text": _TOOL_IMAGES_NOTE}, *images]
            out.extend(messages_for_local("", [{"role": "user", "content": evidence}])[1:])
    return out


_TOOL_IMAGES_TAG = "<tool-images>"
_TOOL_IMAGES_NOTE = (f"{_TOOL_IMAGES_TAG}\nThe image(s) below are the output of the tool call that just completed (a "
                     "screenshot or a camera picture), not something they sent or described. Describe only what is "
                     "actually visible in them. Never infer window or application names from the conversation, invent "
                     "notifications, or claim to read text that is too small: say it is unreadable and offer "
                     "read_screen for the exact text.\n</tool-images>")
# Echoed once as "it's no longer attached": a remark that reads naturally if the model repeats it.
_IMAGES_GONE = "(the screenshot taken at this step, described in the reply that follows)"


def _endpoint():
    return _validated_endpoint(os.getenv("JARVIS_LOCAL_URL", "http://127.0.0.1:1234/v1"))


def _ollama_endpoint():
    return _validated_endpoint(os.getenv("JARVIS_OLLAMA_URL", "http://127.0.0.1:11434/v1"))


def _validated_endpoint(url):
    from urllib.parse import urlsplit
    from core.pcbridge import _NoRedirect
    url = url.rstrip("/")
    parsed = urlsplit(url)
    if (parsed.scheme != "http" or parsed.hostname not in ("localhost", "127.0.0.1", "::1")
            or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path != "/v1"):
        raise LocalFailure("local_endpoint_requires_loopback")
    return url, _opener_for(url, _NoRedirect)


_openers: dict = {}
_openers_lock = threading.Lock()


def _opener_for(url, redirect_handler):
    """One opener per loopback server, built once. A fresh urllib opener builds an HTTPS context that loads the Windows
    certificate store (about 15 ms), and one local request asked for an endpoint 18 times (uses_ollama -> _same_local_server
    -> two endpoints): measured 2026-11-13, 0.28 s of a 0.46 s meaning call was this and nothing else."""
    with _openers_lock:
        opener = _openers.get(url)
        if opener is None:
            opener = _openers[url] = urllib.request.build_opener(redirect_handler())
        return opener


def _prepare_model(role, url, opener, cancelled):
    """Manage only JARVIS's two known models, never evict somebody else's workload.

    LM Studio's own guardrails remain active. CLI argument arrays, no shell/code.
    Explicit endpoint/model overrides are still supported without management.
    """
    if (not enabled() or os.getenv("JARVIS_LOCAL_MANAGE_MODELS", "on") != "on"
            or model(role) not in (FAST, ADVANCED)):
        return
    from urllib.parse import urlsplit
    if urlsplit(url).port != 1234:
        return
    with opener.open(url[:-3] + "/api/v1/models", timeout=3) as response:
        raw = response.read(256001)
    if len(raw) > 256000:
        raise LocalFailure("invalid_model_inventory")
    inventory = json.loads(raw)["models"]
    instances = [i["id"] for m in inventory if m["type"] == "llm" for i in m.get("loaded_instances", [])]
    if model(role) in instances:
        return
    # Both entries are canonical installed model keys, not fuzzy file matching.
    use_fast = model(role) == FAST
    key = "gemma-4-12b-it-qat" if use_fast else "qwen_qwen3.6-27b"
    if key not in [m["key"] for m in inventory]:
        raise LocalFailure("configured_model_not_installed")
    if any(i not in (FAST, ADVANCED) for i in instances):
        raise LocalFailure("unmanaged_model_loaded")
    exe = shutil.which("lms")
    if not exe:
        raise LocalFailure("model_manager_unavailable")
    for instance in instances:
        if cancelled():
            raise LocalFailure("cancelled")
        subprocess.run([exe, "unload", instance], check=True, capture_output=True, timeout=20)
    if cancelled():
        raise LocalFailure("cancelled")
    subprocess.run([exe, "load", key, "--identifier", model(role), "--context-length", "16384",
                    "--gpu", "max" if use_fast else "0.5", "--parallel", "1", "--ttl", "600", "--yes"],
                   check=True, capture_output=True, timeout=90)


def _release_pressure(role):
    if not enabled() or role != "advanced" or model(role) != ADVANCED:
        return False
    import psutil
    low = psutil.virtual_memory().available < 1024 ** 3
    if (low and os.getenv("JARVIS_LOCAL_MANAGE_MODELS", "on") == "on"
            and model(role) == ADVANCED and shutil.which("lms")):
        # Release only our known Qwen instance, never terminate another process. Best effort (a leftover `lms`
        # whose LM Studio is gone fails; that must not fail the turn).
        try:
            subprocess.run([shutil.which("lms"), "unload", ADVANCED],
                           check=True, capture_output=True, timeout=20)
        except (OSError, subprocess.SubprocessError):
            pass
    return low


_lm_down_until = 0.0


def _lm_models(opener, lm_url):
    """LM Studio's model inventory, or None when it is not running.

    A closed loopback port costs ~2 s to refuse on Windows, and this probe used to run before EVERY Ollama
    turn (measured: 2.1 s of a 2.2 s warm voice turn).  A refusal is remembered for 30 s, and the
    probe itself gives up after 0.6 s (a live LM Studio answers a loopback inventory in milliseconds)."""
    global _lm_down_until
    if time.monotonic() < _lm_down_until:
        return None
    try:
        with opener.open(lm_url[:-3] + "/api/v1/models", timeout=.6) as response:
            return response.read(256001)
    except (OSError, ValueError):
        _lm_down_until = time.monotonic() + 30
        return None


def _switch_local_provider(role, cancelled):
    """Release only the configured idle tier before moving between local servers."""
    if not enabled() or not uses_ollama("fast"):
        return
    if cancelled():
        raise LocalFailure("cancelled")
    if uses_ollama(role):
        url, opener = _endpoint()
        raw = _lm_models(opener, url)
        if raw is None:
            return  # An offline LM Studio must not prevent Ollama from answering.
        try:
            inventory = json.loads(raw)
        except ValueError:
            return
        instances = [i["id"] for m in inventory.get("models", [])
                     for i in m.get("loaded_instances", []) if i.get("id") == FAST]
        exe = shutil.which("lms")
        if exe:
            for instance in instances:
                if cancelled():
                    raise LocalFailure("cancelled")
                # Best effort: making room must never fail the turn. 2026-11-19: LM Studio was uninstalled but its
                # `lms` CLI stayed on PATH, so `lms unload` failed and took the whole request down with it.
                try:
                    subprocess.run([exe, "unload", instance], check=True, capture_output=True, timeout=20)
                except (OSError, subprocess.SubprocessError):
                    break
    elif model(role) == FAST:
        # Only make room when LM Studio is really there to use it. With JARVIS_LOCAL_URL pointing at Ollama, or
        # LM Studio closed, this request fails at once anyway, and unloading Ollama's fast model first made the
        # NEXT reply reload it (~4 s for a one-line answer).
        lm_url, lm_opener = _endpoint()
        url, opener = _ollama_endpoint()
        if lm_url == url:
            return
        if _lm_models(lm_opener, lm_url) is None:
            return
        try:
            with opener.open(url[:-3] + "/api/ps", timeout=3) as response:
                loaded = json.loads(response.read(128001)).get("models", [])
            if any(m.get("name") == model("fast") for m in loaded):
                if cancelled():
                    raise LocalFailure("cancelled")
                req = urllib.request.Request(url[:-3] + "/api/generate",
                    data=json.dumps({"model": model("fast"), "keep_alive": 0}).encode(),
                    headers={"Content-Type": "application/json"})
                with opener.open(req, timeout=10) as response:
                    response.read(4096)
        except (OSError, ValueError):
            return


# Circuit breaker per local server. Three transport failures in a row (connection refused, a hung request that ran
# out its 60-90 s timeout...) open it: the next turns fail in milliseconds with local_circuit_open instead of each
# waiting the full timeout while the brain's lock is held. While open, every turn first asks the server for its model
# list (2 s): an answer closes the circuit at once, so recovery is automatic and never slower than one turn.
CIRCUIT_TRIPS = 3
CIRCUIT_OPEN_SECONDS = (30.0, 60.0, 120.0, 300.0)
_circuits = {}            # url -> {"failures": n, "open_until": monotonic, "trips": n, "last": "ErrorType: text"}
_circuit_lock = threading.Lock()


def _circuit(url):
    return _circuits.setdefault(url, {"failures": 0, "open_until": 0.0, "trips": 0, "last": ""})


def _circuit_check(url, opener):
    with _circuit_lock:
        state = _circuit(url)
        open_until = state["open_until"]
    if time.monotonic() >= open_until:
        return
    try:
        with opener.open(urllib.request.Request(url + "/models"), timeout=2) as response:
            response.read(4096)
    except Exception:
        raise LocalFailure("local_circuit_open") from None
    with _circuit_lock:                              # it answers again: closed, as if it had never failed
        state.update(failures=0, open_until=0.0)
    audit("local", "circuit_closed")


_ollama_started_at = 0.0


def _ollama_exe():
    exe = shutil.which("ollama")
    if exe:
        return exe
    local = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Ollama", "ollama.exe")
    return local if os.path.isfile(local) else None


def _probe(url, opener):
    try:
        with opener.open(urllib.request.Request(url + "/models"), timeout=1.5) as response:
            response.read(4096)
        return True
    except Exception:  # noqa: BLE001
        return False


OLLAMA_LOG_LIMIT = 5 * 1024 * 1024
OLLAMA_LOG_KEEP = 3


def _ollama_log_dir():
    return os.getenv("JARVIS_OLLAMA_LOG_DIR") or os.path.join(str(storage.DATA.parent), "machine-state", "ollama")


def _rotate_ollama_log():
    """server.log -> server.log.1 -> .2 ... (the oldest goes) once it is over OLLAMA_LOG_LIMIT."""
    base = os.path.join(_ollama_log_dir(), "server.log")
    try:
        if os.path.getsize(base) <= OLLAMA_LOG_LIMIT:
            return
        for n in range(OLLAMA_LOG_KEEP - 1, 0, -1):          # .1 -> .2, then the log -> .1
            src = f"{base}.{n}"
            if os.path.exists(src):
                os.replace(src, f"{base}.{n + 1}")
        os.replace(base, base + ".1")
    except OSError:
        _copytruncate_ollama_log(base)


def _copytruncate_ollama_log(base):
    """A running server holds server.log open, so (on Windows) it cannot be renamed: copy it to .1 and empty it in place."""
    try:
        if os.path.getsize(base) <= OLLAMA_LOG_LIMIT:
            return
        for n in range(OLLAMA_LOG_KEEP - 1, 0, -1):
            src = f"{base}.{n}"
            if os.path.exists(src):
                os.replace(src, f"{base}.{n + 1}")
        import shutil
        shutil.copyfile(base, base + ".1")
        with open(base, "r+b") as f:
            f.truncate(0)
    except OSError:
        pass


_log_checked_at = -1e9


def _trim_live_ollama_log():
    """Bound server.log while the server runs (rotation at spawn alone never fires for a server up for weeks).
    At most one size check per 10 minutes."""
    global _log_checked_at
    if time.monotonic() - _log_checked_at < 600:
        return
    _log_checked_at = time.monotonic()
    _rotate_ollama_log()


def _spawn_ollama(exe, reason=""):
    """`ollama serve`, detached, its stdout/stderr kept in machine-state/ollama/server.log (it used to go to DEVNULL, so
    a crash left nothing to read)."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
    out = subprocess.DEVNULL
    try:
        os.makedirs(_ollama_log_dir(), exist_ok=True)
        _rotate_ollama_log()
        out = open(os.path.join(_ollama_log_dir(), "server.log"), "ab")
        out.write(("\n===== " + time.strftime("%Y-%m-%d %H:%M:%S") + " jarvis starts ollama serve ("
                   + (reason or "unspecified") + ") =====\n").encode())
        out.flush()
    except OSError:
        out = subprocess.DEVNULL
    try:
        return subprocess.Popen([exe, "serve"], stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT if out is not subprocess.DEVNULL else subprocess.DEVNULL,
                                creationflags=flags, close_fds=True)
    finally:
        if out is not subprocess.DEVNULL:
            try:
                out.close()
            except OSError:
                pass


_REAL_SPAWN_OLLAMA = _spawn_ollama


def _kill_ollama():
    subprocess.run(["taskkill", "/F", "/IM", "ollama.exe"], capture_output=True, timeout=20)


def _listening(url, timeout=1.0):
    """True when something accepts a TCP connection on the server's port. A busy server (loading a model, GPU
    contention) still accepts; only a port nobody listens on refuses."""
    import socket
    from urllib.parse import urlsplit
    parts = urlsplit(url)
    try:
        with socket.create_connection((parts.hostname or "127.0.0.1", parts.port or 80), timeout=timeout):
            return True
    except OSError:
        return False


_busy_noted_at = -1e9


def ensure_ollama_up(url, opener, wait=12.0, probe=None, start=None, listening=None):
    """Ollama lives in the owner's profile and is not a service: when nothing listens on its loopback port (it was closed,
    or crashed, or never started at sign-in), every local request failed and Jarvis was left without a brain (live
    2026-11-06: every UI step "could not decide"). So Jarvis starts `ollama serve` himself, at most once per 2 minutes, and
    waits for it to answer.

    A server that does not ANSWER the probe within 1.5 s but still accepts connections is busy, not dead (2026-11-13: six
    false "not running" lines while one ollama.exe served for days): it is left alone and the request waits on it as
    it always does. Only a port nobody listens on (checked twice) is restarted. Returns True when the server is there."""
    global _ollama_started_at, _busy_noted_at
    probe = probe or (lambda: _probe(url, opener))
    listening = listening or (lambda: _listening(url))
    _trim_live_ollama_log()
    if probe():
        return True
    if os.name != "nt":                               # no auto-start off Windows: do not even spend a TCP timeout
        return False
    if listening():
        if time.monotonic() - _busy_noted_at > 60:
            _busy_noted_at = time.monotonic()
            audit("local", "ollama_busy_not_restarted")
        return True
    if time.monotonic() - _ollama_started_at < 120:
        return False
    time.sleep(0.3)
    if listening():                                   # it was only between two accepts
        return True
    _ollama_started_at = time.monotonic()
    exe = _ollama_exe()
    if not exe:
        return False
    try:
        (start or (lambda e: _spawn_ollama(e, reason="port_closed")))(exe)
    except OSError:
        return False
    audit("local", "ollama_started:port_closed")
    print("[intelligence] the local model server was not running (nothing listening on its port); started ollama serve")
    from core import progress
    progress.step("restarting my language model")
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        time.sleep(0.25)
        if probe():
            with _circuit_lock:
                _circuit(url).update(failures=0, trips=0, open_until=0.0)
            return True
    return False


OLLAMA_SETTINGS_FILE = "ollama-settings.json"


def apply_ollama_settings(url=None, opener=None, kill=None, start=None, wait=30.0):
    """The OLLAMA_* server options in .env (e.g. OLLAMA_NUM_PARALLEL) only take effect when `ollama serve` starts, and
    the server Jarvis started is elevated, so nothing else can restart it. At start-up: when those options differ from
    the ones last applied, Jarvis restarts his local model server once (the brain reloads in seconds), then remembers
    them. 2026-11-10: one brain shared by every caller needed 3 cache slots (OLLAMA_NUM_PARALLEL=3). -> True if restarted."""
    want = {k: v for k, v in sorted(os.environ.items()) if k.startswith("OLLAMA_")}
    have = storage.load_global(OLLAMA_SETTINGS_FILE, None)
    if not want or have == want or os.name != "nt":   # no OLLAMA_* options set: never restart a server we did not configure
        return False
    if url is None:
        url, opener = _ollama_endpoint()
    if not _probe(url, opener):                    # not running: whoever starts it next (ensure_ollama_up) uses these
        storage.save_global(OLLAMA_SETTINGS_FILE, want)
        return False
    exe = _ollama_exe()
    if not exe:
        return False
    (kill or _kill_ollama)()
    time.sleep(2)
    try:
        (start or (lambda e: _spawn_ollama(e, reason="settings_changed")))(exe)
    except OSError:
        return False
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        time.sleep(0.5)
        if _probe(url, opener):
            storage.save_global(OLLAMA_SETTINGS_FILE, want)
            audit("local", "ollama_restarted_for_settings")
            print("[intelligence] restarted the local model server to apply: "
                  + ", ".join(f"{k}={v}" for k, v in want.items() if have is None or have.get(k) != v))
            return True
    return False


def _circuit_failed(url, error):
    with _circuit_lock:
        state = _circuit(url)
        state["failures"] += 1
        state["last"] = f"{type(error).__name__}: {str(error)[:80]}"
        if state["failures"] < CIRCUIT_TRIPS:
            return
        wait = CIRCUIT_OPEN_SECONDS[min(state["trips"], len(CIRCUIT_OPEN_SECONDS) - 1)]
        state.update(failures=0, open_until=time.monotonic() + wait, trips=state["trips"] + 1)
    audit("local", f"circuit_open:{int(wait)}s")
    print(f"[intelligence] local server at {url} failed {CIRCUIT_TRIPS} times ({state['last']}); not asked for {wait:.0f} s")


def _circuit_ok(url):
    with _circuit_lock:
        state = _circuit(url)
        state.update(failures=0, trips=0)


def circuit_status():
    """For the dashboard and tests: each local server's breaker state."""
    with _circuit_lock:
        now = time.monotonic()
        return {url: {"open": s["open_until"] > now, "seconds_left": max(0.0, round(s["open_until"] - now, 1)),
                      "failures": s["failures"], "trips": s["trips"], "last": s["last"]} for url, s in _circuits.items()}


def _resident(url, opener):
    """The models Ollama holds in VRAM right now: name -> size in bytes ({} when it can't be asked)."""
    try:
        with opener.open(urllib.request.Request(url[:-3] + "/api/ps"), timeout=3) as response:
            return {m.get("name"): int(m.get("size_vram") or m.get("size") or 0)
                    for m in json.loads(response.read(128001)).get("models", []) if m.get("name")}
    except Exception:          # a best-effort probe: an odd answer means "unknown", never a failed turn
        return {}


def _unload(name, url, opener):
    try:
        req = urllib.request.Request(url[:-3] + "/api/generate", data=json.dumps({"model": name, "keep_alive": 0}).encode(),
                                     headers={"Content-Type": "application/json"})
        with opener.open(req, timeout=15) as response:
            response.read(4096)
        return True
    except OSError:
        return False


def tier_models():
    """Every model the local tiers may ask Ollama for."""
    return {m for m in (model("fast"), model("advanced"), vision_model()) if m}


def reclaim_vram(url=None, opener=None):
    """Unload what the local tiers never use. Measured on the owner's PC: the planner's qwen2.5:7b stayed pinned
    (keep_alive -1, 4.4 GB) beside llama3.2, llama3.1 and qwen3-vl on a 16 GB card, 15.2 GB in use: Ollama's runner
    crashed under the pressure ("llama-server process has terminated", HTTP 500 on vision calls) and the GPU voice
    hit "CUDA error: unknown error". Only with local-first on, and only models outside the configured tiers."""
    if not (enabled() and uses_ollama("fast")):
        return []
    if url is None:
        url, opener = _ollama_endpoint()
    gone = [name for name in _resident(url, opener) if name not in tier_models() and _unload(name, url, opener)]
    if gone:
        audit("vram", "reclaimed:" + ",".join(sorted(gone)))
    return gone


NEEDS_GB = {"vision": 7.5, "advanced": 6.8}      # measured resident sizes at num_ctx 16384 (runs/bench-models.json)
HEADROOM_GB = 1.0


def _free_vram_gb():
    """Free GPU memory as the driver sees it (Whisper, the voice and the desktop count too, not only Ollama);
    None when nvidia-smi isn't there."""
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=3).stdout.strip().split(",")
        used, total = (int(x) for x in out[:2])
        return (total - used) / 1024
    except Exception:
        return None


def _make_room(chosen, url, opener):
    """Before loading `chosen`, drop what it can't sit beside. The advanced and vision models (6.8 GB and 7.5 GB) are
    alternates: with both resident next to the fast one the card was full and the runner died. The fast model stays
    unless even then the card lacks the room: with 15.9 of 16.3 GB in use (Whisper and the voice hold ~2 GB too) a
    vision turn that takes 6 s took 110-180 s and timed out, because the driver was paging VRAM. The fast model reloads
    in 2.4 s. Nothing is done when `chosen` is already loaded."""
    resident = _resident(url, opener)
    if not resident or chosen in resident:
        return []
    others = {model("advanced"), vision_model()} - {chosen, model("fast"), None}
    gone = [name for name in resident if name in others and _unload(name, url, opener)]
    gone += [name for name in resident if name not in tier_models() and name not in gone and _unload(name, url, opener)]
    need = NEEDS_GB["vision" if chosen == vision_model() else "advanced"]
    freed = sum(resident[name] for name in gone) / 2 ** 30
    free = _free_vram_gb()
    fast = model("fast")
    if free is not None and fast in resident and fast not in gone and free + freed < need + HEADROOM_GB:
        if _unload(fast, url, opener):
            gone.append(fast)
            audit("vram", f"fast_unloaded_for:{chosen}")
    return gone


def _evict_other_ollama_models(keep, url, opener):
    """Unload every resident Ollama model except `keep` (it couldn't load beside them)."""
    try:
        with opener.open(url[:-3] + "/api/ps", timeout=3) as response:
            loaded = [m.get("name") for m in json.loads(response.read(128001)).get("models", [])]
    except (OSError, ValueError):
        return
    for name in loaded:
        if name and name != keep:
            try:
                req = urllib.request.Request(url[:-3] + "/api/generate", data=json.dumps({"model": name, "keep_alive": 0}).encode(),
                                             headers={"Content-Type": "application/json"})
                with opener.open(req, timeout=15) as response:
                    response.read(4096)
            except OSError:
                pass
    time.sleep(1)


def _ollama_body(body):
    messages = []
    for row in body["messages"]:
        row = dict(row)
        if isinstance(row.get("content"), list):
            parts = row["content"]
            row["content"] = "\n".join(
                p.get("text", "") for p in parts
                if p.get("type") == "text")
            images = [
                p["image_url"]["url"].split(",", 1)[1]
                for p in parts if p.get("type") == "image_url"
            ]
            if images:
                row["images"] = images
        row["content"] = row.get("content") or ""
        if row.get("tool_calls"):
            row["tool_calls"] = [{"function": {"name": c["function"]["name"],
                "arguments": json.loads(c["function"]["arguments"])}} for c in row["tool_calls"]]
        messages.append(row)
    from core import warmth
    native = {"model": body["model"], "messages": messages, "stream": False,
              "keep_alive": warmth.keep_alive(body["model"]), "options": {"temperature": body["temperature"],
              "num_predict": body["max_tokens"], "num_ctx": 16384}}
    for key in ("top_p", "top_k", "presence_penalty"):        # a chat profile's sampling (PROFILES); else Ollama's own
        if body.get(key) is not None:
            native["options"][key] = body[key]
    if body.get("tools"):
        native["tools"] = body["tools"]
    if body.get("response_format"):
        native["format"] = body["response_format"]["json_schema"]["schema"]
    if thinks(body["model"]):
        # A thinking build (gemma4, qwen3) reasons in a hidden channel first. Given a rich prompt it spent the whole
        # 3072-token budget there and returned NO content, so every such turn failed as incomplete after ~50 s
        # (gateway eval 2026-11-06: 18 of 60). Jarvis answers in the open; hidden reasoning is off. Sent only to
        # models that advertise the capability: Ollama rejects the field for the others. On only for the one profile
        # that asks for it (PROFILES["think"]: a depth question), per call and under a hard cap (THINK_CAP).
        native["think"] = bool(body.get("think"))
    return _tools_after_system(native)


# Conversation is sampled like conversation. Until 2026-11-12 every call went out at temperature 0.2 with no other
# sampling field: right for tool JSON, flat and repetitive for talk (docs/mission/audit-brain-pipeline.md R3). A chat
# turn (core.chatturn) names one of these; the classifier, tool turns and every other caller keep the strict settings.
# The values are the model card's for non-thinking qwen3.5 (docs/mission/research-brain-models.md: 0.7 / 0.8 / 20,
# presence penalty 1.5) and, for the thinking profile, the family's thinking settings (0.6 / 0.95 / 20).
# max_tokens is the reply budget: a remark needs little, a why/how answer of four to eight sentences needs room.
# Only the NAME crosses the laptop -> PC relay (/api/pc/intelligence); the numbers are applied here, on the PC.
THINK_CAP = 320          # tokens of hidden reasoning a depth answer may spend; past it the call is dropped (thinking_over_budget)
PROFILES = {
    "chat": {"temperature": 0.7, "top_p": 0.8, "top_k": 20, "presence_penalty": 1.5, "max_tokens": 220},
    "depth": {"temperature": 0.7, "top_p": 0.8, "top_k": 20, "presence_penalty": 1.5, "max_tokens": 480},
    "think": {"temperature": 0.6, "top_p": 0.95, "top_k": 20, "presence_penalty": 1.5, "max_tokens": 480 + THINK_CAP,
              "think": True},
}


# A reply in Haitian Creole is sampled with a lighter presence penalty: at 1.5 every word already used is pushed down, and
# in Creole the words used again are the plain ones (mwen, ou, yon, pou, a), so the model reached for rarer and invented
# words (measured on 49 everyday questions: 8 replies needed a retry at 0.4, 11 to 14 at 1.5). Read from the note the
# turn carries (core.kreyol_bank.is_note), so a relayed turn gets it on the PC with no new field.
CREOLE_PRESENCE = 0.4


def _creole_turn(messages) -> bool:
    last = messages[-1].get("content") if messages and isinstance(messages[-1], dict) else None
    if isinstance(last, list):
        last = " ".join(str(p.get("text", "")) for p in last if isinstance(p, dict))
    return isinstance(last, str) and "(Language: they are speaking Haitian Creole" in last


def profile_sampling(profile: str, messages) -> dict:
    """PROFILES[profile], with the lighter presence penalty when the turn is to be answered in Creole."""
    out = dict(PROFILES[profile])
    if _creole_turn(messages):
        out["presence_penalty"] = CREOLE_PRESENCE
    return out


_TURN_TOOLS = ("<turn-tools>\nFor THIS request you may call these functions, exactly like a tool above (in a <tool_call>), "
               "and only these:\n{}\n</turn-tools>")


def _tools_after_system(native):
    """Ollama's qwen3.5 renderer writes the tool list BEFORE the system text, and the tools offered change every turn
    (select_tools), so every turn re-read the whole ~12k-token prompt: 1.45-3 s. Measured 2026-11-10: with ONE fixed
    tool in the header (the never-offered placeholder small talk already sends) and this turn's tools described after
    the system text, in the latest user message, the prompt re-read fell to 0.14 s and the calls came back as proper
    tool_calls (open_app, add_to_list). Thinking models (qwen3.5) only; JARVIS_TOOLS_IN_TEXT=off restores the header."""
    tools = native.get("tools") or []
    # Only the family measured (qwen3.5): the kept backup gemma4 and others keep their real tools header (review 2026-11-10).
    # JARVIS_TOOLS_IN_TEXT_MODELS opts in more name prefixes once they have been measured.
    prefixes = tuple(p.strip().casefold() for p in os.getenv("JARVIS_TOOLS_IN_TEXT_MODELS", "qwen3.5").split(",") if p.strip())
    if (not tools or not (native.get("model") or "").casefold().startswith(prefixes)
            or os.getenv("JARVIS_TOOLS_IN_TEXT", "on").lower() == "off"):
        return native
    real = [t for t in tools if (t.get("function") or {}).get("name") != _NO_TOOL["function"]["name"]]
    native["tools"] = [_NO_TOOL]
    if not real:
        return native
    last = next((i for i in range(len(native["messages"]) - 1, -1, -1) if native["messages"][i].get("role") == "user"), None)
    if last is None:
        native["tools"] = tools
        return native
    note = _TURN_TOOLS.format("\n".join(json.dumps(t["function"], ensure_ascii=False, separators=(",", ":")) for t in real))
    row = dict(native["messages"][last])
    row["content"] = f"{row.get('content') or ''}\n\n{note}"
    native["messages"] = native["messages"][:last] + [row] + native["messages"][last + 1:]
    return native


# Ollama builds that reason in a hidden channel ("thinking" capability). By name, not by asking the server: a lookup
# per turn would be one more request on the voice path, and the fakes in the tests count every call. An instruct build
# of the same family (qwen3:4b-instruct, qwen3-vl:8b-instruct) does not think and would be refused the field.
_THINKING = re.compile(r"^(?:gemma4|qwen3(?!-vl)|qwq|deepseek-r1|gpt-oss|magistral|phi4-reasoning)\b", re.I)


def thinks(name):
    """Does this Ollama model reason in a hidden channel (so 'think': false must be sent)? JARVIS_OLLAMA_THINKING
    names more, comma-separated, without a code change."""
    extra = {m.strip().casefold() for m in os.getenv("JARVIS_OLLAMA_THINKING", "").split(",") if m.strip()}
    low = (name or "").casefold()
    if low in extra:
        return True
    return bool(_THINKING.match(low)) and "instruct" not in low


def _ollama_reply(native):
    if native.get("done") is not True:
        raise LocalFailure("incomplete_response")
    message = dict(native["message"])
    message.pop("thinking", None)                    # hidden reasoning is never part of a reply (PROFILES["think"])
    calls = message.get("tool_calls") or []
    if calls:
        message["tool_calls"] = [{"id": "ollama-" + uuid.uuid4().hex, "type": "function",
            "function": {"name": c["function"]["name"],
                         "arguments": json.dumps(c["function"]["arguments"])}} for c in calls]
    reason = native.get("done_reason", "stop")
    prompt, completion = native.get("prompt_eval_count", 0), native.get("eval_count", 0)
    # prompt_tokens is the WHOLE prompt's size on Ollama 0.40 (measured 2026-11-19: a 10,282-token call right after a
    # 10,175-token one reported 10,282 and read in 0.24 s), not what was read again. What the cache saved shows only in the
    # read time, prompt_eval_ms (scripts/latency_report.py), and in the row's shared / back (_cache_evidence).
    read_ms = native.get("prompt_eval_duration")
    usage = {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": prompt + completion}
    if type(read_ms) is int and read_ms >= 0:
        usage["prompt_eval_ms"] = read_ms // 1000000
    return {"model": native.get("model"), "choices": [{"message": message,
            "finish_reason": "tool_calls" if calls and reason == "stop" else reason}],
            "usage": usage}


_last_local = {}  # role -> monotonic time that model last answered (it's loaded, its cache holds the prompt)

# Ollama keeps the last prompt cached and only reads what changed, but Llama 3's template writes a tool note into the
# SYSTEM header whenever tools are attached (and the tool list into the last user message). Small talk goes without
# tools, so every switch between small talk and a request changed the prompt right after the system text: Ollama
# re-read the whole conversation, 0.5 s early on, 1.5 s ten turns in (12 s over a 12-turn chat). A turn without tools
# therefore still attaches this never-offered placeholder (same header) and ends on an empty assistant message: the
# user's message is no longer "last", so it gets no "answer with a function call" block, and the model simply writes
# the reply there. Calling it is rejected like any tool that wasn't offered.
_NO_TOOL = {"type": "function", "function": {"name": "no_tool", "description": "Placeholder, never call it.",
                                             "parameters": {"type": "object", "properties": {}}}}
# Mid-conversation, small talk sometimes came back as a tool call typed out, copying the calls above it ("How are you
# doing today?" -> {"name": "get_usage"...}: 3 of 12 samples before this layout, 6 of 12 with it, 0 of 24 with this
# note), and each one escalated to the slow tiers. Only in this request, never in the kept history.
_PLAIN = "(Answer in plain words. No function call.)"


def _same_prompt_start(body):
    """For Ollama turns without tools: see _NO_TOOL and _PLAIN."""
    if body.get("chat"):
        # A chat turn (core.chatturn) has its own short system prompt, so there is no manual prefix to keep in step
        # with: no placeholder tool (the model is not told about functions at all) and no note. Always the same
        # layout, so its own cached prefix (persona, what he knows, the conversation so far) holds from turn to turn.
        return body
    if (not body.get("tools") and not body.get("response_format") and body["messages"]
            and body["messages"][-1].get("role") == "user"):
        last = dict(body["messages"][-1])
        content = last.get("content")
        last["content"] = (content + [{"type": "text", "text": _PLAIN}] if isinstance(content, list)
                           else f"{content or ''}\n\n{_PLAIN}")
        body["tools"] = [_NO_TOOL]
        # The empty assistant message ends Llama 3's prompt on the reply. Qwen 3.5 reads it as a prefill and skips the
        # empty think block thinking-off needs: it reasoned out loud ("Thinking Process: 1. Analyze...") until the
        # token limit, 3 of 3 probes (2026-11-09). Without it: 5 of 5 clean replies in 0.4-1.0 s. Thinking models only.
        tail = [] if thinks(body.get("model")) else [{"role": "assistant", "content": ""}]
        body["messages"] = body["messages"][:-1] + [last] + tail
    return body


def prewarm(system):
    """Called at the wake word, while they're still talking: load the fast model and read the system prompt now,
    so the reply waits for neither (measured: ~2 s to load it after Ollama's keep-alive lapses, ~1.6 s to read the
    ~9k-token prompt cold, vs 0.1-0.6 s for a warm turn). Local and free: the same Ollama call as a turn, one token
    long, same context size (a different one would reload the model). The turn shares the system-prompt prefix, so
    it reuses that cache. Skipped when the model answered in the last two minutes."""
    from core import pcbridge
    if (not (enabled() and uses_ollama("fast")) or time.monotonic() - _last_local.get("fast", -1e9) < 120
            or not (pcbridge.local_pc() or not os.getenv("JARVIS_PC_URL"))):  # turns go to the PC's model
        return None

    def run():
        try:
            url, opener = _ollama_endpoint()
            reclaim_vram(url, opener)                    # what the tiers never use goes before the fast model loads
            body = _ollama_body({"model": model("fast"), "messages": [{"role": "system", "content": system}],
                                 "temperature": 0.2, "max_tokens": 1, "tools": [_NO_TOOL]})  # header as a turn has
            request = urllib.request.Request(url[:-3] + "/api/chat", data=json.dumps(body).encode(),
                                             headers={"Content-Type": "application/json"})
            with opener.open(request, timeout=60) as response:
                response.read(65536)
            _last_local["fast"] = time.monotonic()
        except (OSError, ValueError, LocalFailure):
            pass  # a cold turn is slower, nothing worse

    thread = threading.Thread(target=run, daemon=True, name="prewarm-local")
    thread.start()
    return thread


_chat_warmed = {"prefix": "", "at": -1e9}


def prewarm_chat(chat_system, wait=False):
    """The chat persona's prompt, read by the local model ahead of the first plain-talk turn: at start (core.warmth) and at
    the wake word (Brain.warm_up), as prewarm() does for the manual. The server keeps both prefixes (measured 2026-11-12:
    a 16k-token prompt stayed cached across calls with another prompt; reading one cold costs about a second per 4,500
    tokens, and the very first call after a start far more). One token long, the layout a chat turn has (no tools), the
    turns' own context size and keep-alive. Skipped while the same text was warmed in the last ten minutes, and where
    the turns go to the PC's model."""
    from core import pcbridge
    if (not chat_system or not (enabled() and uses_ollama("fast"))
            or (_chat_warmed["prefix"] == chat_system and time.monotonic() - _chat_warmed["at"] < 600)
            or not (pcbridge.local_pc() or not os.getenv("JARVIS_PC_URL"))):
        return None

    def run():
        try:
            url, opener = _ollama_endpoint()
            body = _ollama_body({"model": model("fast"), "messages": [{"role": "system", "content": chat_system}],
                                 "max_tokens": 1, "tools": [], **{k: v for k, v in PROFILES["chat"].items()
                                                                   if k != "max_tokens"}})
            request = urllib.request.Request(url[:-3] + "/api/chat", data=json.dumps(body).encode(),
                                             headers={"Content-Type": "application/json"})
            with opener.open(request, timeout=60) as response:
                response.read(65536)
            _chat_warmed.update(prefix=chat_system, at=time.monotonic())
        except (OSError, ValueError, LocalFailure):
            pass  # a cold first turn is slower, nothing worse

    if wait:
        run()
        return None
    thread = threading.Thread(target=run, daemon=True, name="prewarm-chat")
    thread.start()
    return thread


def _read_ollama_stream(response, on_text, cancelled, think_cap=0):
    """Ollama's streamed /api/chat (one JSON object per line) -> the same final object a non-streamed call returns,
    handing each piece of text to on_text as it arrives. Hidden reasoning (message.thinking, only when the call asked
    for it) is never handed on and never kept; with `think_cap` (tokens, estimated at 3.5 chars each) a call that
    reasons past it is dropped, so a depth answer can never sit thinking while he waits."""
    text, calls, final, size, thought = [], [], None, 0, 0
    for line in response:
        size += len(line)
        if size > 2000000:
            raise LocalFailure("response_too_large")
        if cancelled():
            raise LocalFailure("cancelled")
        if not line.strip():
            continue
        chunk = json.loads(line)
        message = chunk.get("message") or {}
        if think_cap and message.get("thinking"):
            thought += len(str(message["thinking"]))
            if thought / 3.5 > think_cap:
                raise LocalFailure("thinking_over_budget")
        if message.get("content"):
            text.append(message["content"])
            on_text(message["content"])
        calls.extend(message.get("tool_calls") or [])
        if chunk.get("done"):
            final = dict(chunk)
            break
    if final is None:
        raise LocalFailure("incomplete_response")
    final["message"] = {"role": "assistant", "content": "".join(text), **({"tool_calls": calls} if calls else {})}
    return final


def _first_token_timer(on_text, role, since):
    """The first streamed piece of a reply marks the model's time to first token in the turn's ledger (core.turntrace)."""
    seen = []

    def feed(piece):
        if not seen:
            seen.append(True)
            try:
                from core import turntrace
                turntrace.span("ttft", role, since, time.time())
            except Exception:  # noqa: BLE001  (timing never breaks a reply)
                pass
        on_text(piece)
    return feed


def complete_local(payload, cancelled=lambda: False, on_text=None):
    """Loopback only; also used behind the existing authenticated PC relay.

    on_text: receive the reply's text as Ollama writes it (Jarvis starts speaking before it's finished). The whole
    reply is still returned, and validated by the caller, exactly as without it."""
    if cancelled():
        raise LocalFailure("cancelled")
    role = payload.get("role")
    meter = getattr(_meter, "fn", None)
    if meter is not None:
        meter(role)                     # a mission's budget: may raise (core/missionworker.MissionBudget)
    messages = list(payload.get("messages", [])) if isinstance(payload.get("messages"), list) else []
    pictured = [n for n, m in enumerate(messages) if isinstance(m, dict) and _has_image(m)]
    # A screenshot or photo in this turn (the newest user message, or the evidence right after a tool's results) goes
    # to the local vision model, whichever tier asked. So does a follow-up about that picture ("Is there a browser
    # open in it?": the text model, picture gone, answered "Yes" from nothing). Older pictures are taken out for the
    # text models: Ollama's text models reject images, and a stale screenshot only invites a description of the
    # wrong screen.
    vision = vision_model() if pictured and _picture_matters(messages, pictured[-1]) else None
    ollama = uses_ollama(role) or bool(vision)
    if ollama and pictured:
        for n in pictured:
            if not (vision and n == pictured[-1]):
                messages[n] = _without_images(messages[n])
        payload = dict(payload, messages=messages)
    chosen = vision or model(role)
    if ollama and chosen not in configured_ollama_models():
        raise LocalFailure("unsupported_ollama_model")
    if (set(payload) - {"role", "messages", "tools", "response_format", "profile"}
            or not {"role", "messages", "tools"} <= set(payload)
            or not isinstance(payload["messages"], list) or not isinstance(payload["tools"], list)):
        raise LocalFailure("invalid_request")
    profile = payload.get("profile")
    if profile is not None and (not isinstance(profile, str) or profile not in PROFILES or payload["tools"]
                                or payload.get("response_format")):
        # A chat profile is a name from the fixed table, and a chat turn carries no tool and no JSON schema: checked
        # here too, where the laptop's relayed turns arrive, so talk can never come back as an action.
        raise LocalFailure("invalid_request")
    if len(payload["messages"]) > 200 or len(payload["tools"]) > 32:
        raise LocalFailure("context_too_large")
    for message in payload["messages"]:
        if not isinstance(message, dict) or message.get("role") not in ("system", "user", "assistant", "tool"):
            raise LocalFailure("invalid_messages")
        content = message.get("content")
        if content is not None and not isinstance(content, (str, list)):
            raise LocalFailure("invalid_messages")
        if isinstance(content, list):
            for part in content:
                if not isinstance(part, dict) or part.get("type") not in ("text", "image_url"):
                    raise LocalFailure("invalid_messages")
                if part["type"] == "image_url":
                    value = part.get("image_url", {}).get("url", "")
                    if not isinstance(value, str) or not re.fullmatch(r"data:image/(?:png|jpeg|webp);base64,[A-Za-z0-9+/=]+", value):
                        raise LocalFailure("remote_image_refused")
                    try:
                        image_bytes = base64.b64decode(value.split(",", 1)[1], validate=True)
                    except (ValueError, base64.binascii.Error):
                        raise LocalFailure("invalid_image") from None
                    if not image_bytes or len(image_bytes) > 8 * 1024 * 1024:
                        raise LocalFailure("invalid_image_size")
    # Never a prompt bigger than the window (core.contextfit.guard): the oldest exchanges go first, whole; the rules and his
    # newest line never. What still does not fit is refused out loud, not cut from the front by Ollama.
    from core import contextfit
    fitted = contextfit.guard(payload["messages"], payload["tools"])
    if fitted is not payload["messages"]:
        audit(role, "context_trimmed", 0)
        payload = dict(payload, messages=fitted)
    if contextfit.tokens(payload["messages"], payload["tools"]) > contextfit.BUDGET:
        raise LocalFailure("context_too_large")
    url, opener = _ollama_endpoint() if ollama else _endpoint()
    if ollama:
        ensure_ollama_up(url, opener)                # a closed Ollama is started, not mourned
    _circuit_check(url, opener)                      # a server that keeps failing isn't waited on for 60 s each turn
    started = time.monotonic()
    if not _acquire_inference(cancelled):
        raise LocalFailure("local_model_busy")
    try:
        _switch_local_provider(role, cancelled)
        if ollama and chosen != model("fast"):
            _make_room(chosen, url, opener)          # the advanced and vision models take turns on the card
        if not ollama:
            _prepare_model(role, url, opener, cancelled)
        if _release_pressure(role):
            raise LocalFailure("insufficient_memory_headroom")
        with opener.open(url + "/models", timeout=3) as response:
            raw = response.read(128001)
        if len(raw) > 128000 or chosen not in [x.get("id") for x in json.loads(raw).get("data", [])]:
            raise LocalFailure("configured_model_unavailable")
        if cancelled():
            raise LocalFailure("cancelled")
        body = {"model": chosen, "messages": payload["messages"], "temperature": 0.2,
                "max_tokens": 1536 if role == "fast" else 3072, "stream": False}
        think_cap = 0
        if profile:
            body.update(profile_sampling(profile, payload["messages"]))
            if ollama:
                body["chat"] = True                  # its own prompt layout (_same_prompt_start)
                think_cap = THINK_CAP if body.get("think") and thinks(chosen) else 0
            else:                                    # LM Studio's OpenAI endpoint: only the fields it knows
                for key in ("top_k", "think"):
                    body.pop(key, None)
        if not ollama:
            body["reasoning_effort"] = "none" if role == "fast" or thinks(chosen) else "low"   # "low" turns qwen3.5 thinking ON
        if payload["tools"]:
            body["tools"] = payload["tools"]
        if payload.get("response_format"):
            fmt = payload["response_format"]
            if not isinstance(fmt, dict) or fmt.get("type") != "json_schema":
                raise LocalFailure("invalid_response_format")
            body["response_format"] = fmt
        # A call that reasons first is always read as a stream, heard or not (the phone's relayed turn): the cap on its
        # hidden reasoning is enforced while it is written.
        stream = (bool(on_text) or bool(think_cap)) and ollama
        if stream:
            on_text = _first_token_timer(on_text or (lambda piece: None), role, time.time() - (time.monotonic() - started))
        native = _ollama_body(_same_prompt_start(body)) if ollama else body
        native.pop("chat", None)
        native["stream"] = stream
        sent_text, evidence = "", {}
        if ollama:
            try:
                sent_text = _prompt_text(native)
                evidence = _cache_evidence(sent_text)
            except Exception:  # noqa: BLE001 - diagnostics never break a turn
                sent_text, evidence = "", {}
        data = json.dumps(native).encode()
        if len(data) > 6 * 1024 * 1024:
            raise LocalFailure("context_too_large")
        request = urllib.request.Request(url[:-3] + "/api/chat" if ollama else url + "/chat/completions", data=data,
                                         headers={"Content-Type": "application/json"})

        def fetch(timeout):
            with opener.open(request, timeout=timeout) as response:
                if stream:
                    return _read_ollama_stream(response, on_text, cancelled, think_cap)
                raw = response.read(2000001)
            if len(raw) > 2000000:
                raise LocalFailure("response_too_large")
            return json.loads(raw)
        try:
            result = fetch(60 if ollama else (30 if role == "fast" else 90))
        except urllib.error.HTTPError as error:
            # Ollama on Windows crashes its runner ("llama-server process has terminated") instead of evicting
            # when another model is pinned in VRAM. Free the GPU of every other model and try once more.
            if not (ollama and error.code == 500 and b"terminated" in error.read(4096)):
                raise
            _evict_other_ollama_models(chosen, url, opener)
            result = fetch(90)
        if ollama:
            result = _ollama_reply(result)
        _circuit_ok(url)
        _release_pressure(role)
        if cancelled():
            raise LocalFailure("cancelled")
        _last_local[role] = time.monotonic()
        if sent_text:
            _note_sent(sent_text)                    # only a prompt the server answered is in its cache
        audit(role, "received", time.monotonic() - started, {**(result.get("usage") or {}), **evidence})
        return result
    except LocalFailure:
        raise
    except Exception as error:
        _circuit_failed(url, error)
        _audit_failure(role, error, time.monotonic() - started)
        raise LocalFailure("local_request_failed") from None
    finally:
        _inference_lock.release()


def _audit_failure(role, error, elapsed):
    """A failed local attempt leaves a row (and a model span) with its kind and time: it used to vanish into the turn's
    "other" seconds (2026-11-13: 19 s of "other" that was a request waiting on a busy server)."""
    try:
        audit(role, "failed:" + type(error).__name__, round(elapsed, 3))
    except Exception:  # noqa: BLE001 - diagnostics never break a turn
        pass


def cloud_on() -> bool:
    """The paid brain may answer (core.cloudmodel: Claude when JARVIS_CLOUD_FALLBACK=claude), and not inside a mission."""
    if getattr(_meter, "fn", None) is not None:
        return False
    try:
        from core import cloudmodel
        return cloudmodel.available()
    except Exception:  # noqa: BLE001
        return False


def hard_first() -> bool:
    """Owner 2026-11-21: harder questions go to the API brain first (JARVIS_CLOUD_HARD=off sends them local again)."""
    from core import cloudmodel
    return (os.getenv("JARVIS_CLOUD_HARD", "on").lower() != "off" and cloudmodel.provider() == "claude" and cloud_on())


def _cloud_raw(payload, cancelled, why, kind, parse=None, said=None):
    """One cloud call, audited and logged as an API assist (core.apiassist); `parse` turns the raw reply into the answer
    (its LocalFailure is logged as a failed assist too). Returns parse(raw), or raw. `said`: his words for the log (the
    newest message may carry the turn's notes)."""
    if getattr(_meter, "fn", None) is not None:
        raise LocalFailure("mission_no_cloud")      # Mark VI: a mission spends $0 (never the paid fallback)
    from core import apiassist, cloudmodel
    if not cloudmodel.available():
        raise LocalFailure("cloud_provider_unconfigured")
    started = time.monotonic()
    said = said if said is not None else last_user_text(payload.get("messages"))
    response = {}
    try:
        response = cloudmodel.complete(payload, cancelled)
        if cancelled():
            raise LocalFailure("cancelled")
        out = parse(response) if parse else response
    except LocalFailure as error:
        audit("cloud", str(error), time.monotonic() - started)
        apiassist.log(kind, f"{why}; failed {error}", said, model=str(response.get("model") or cloudmodel.provider()),
                      cost=response.get("usd", 0.0))       # a reply that failed its checks was still paid for
        raise
    audit("cloud", "received", time.monotonic() - started, response.get("usage"))
    apiassist.log(kind, why, said, model=str(response.get("model") or cloudmodel.provider()), cost=response.get("usd", 0.0))
    return out


def _cloud_fallback(payload, names, cancelled, why="local_failed"):
    """Hand the already-built payload to the cloud provider, parse and audit it."""
    messages = payload.get("messages")
    if payload.get("response_format"):
        return _cloud_raw(payload, cancelled, why, "plan", _structured)     # the JSON asked for IS the answer
    return _cloud_raw(payload, cancelled, why, "tool" if names else "chat",
                      lambda r: parse_reply(r, names, said=last_user_text(messages), asked=jarvis_asked(messages),
                                            recent=used_in(messages)))


def _schemas(tools):
    """Registry tool schemas -> the function definitions a local model is offered."""
    return [{"type": "function", "function": {"name": t["name"], "description": t.get("description", ""),
                                              "parameters": t["input_schema"]}} for t in tools if "input_schema" in t]


def request(role, system, history, tools, cancelled=lambda: False, response_format=None, on_text=None,
            summary=None, upto=0, note="", chat_system=None, on_chat=None, talk_only="", language=""):
    """One local turn. on_text gets the reply's text while it's written, but only for a reply that can't turn into a
    tool call halfway: when no tools are offered (small talk) or it's the answer after a tool ran. A first-round
    reply with tools is still checked whole before a word of it is said.
    summary/upto: the running summary of this conversation and how many of its first exchanges it covers
    (core/convsummary): the local window shows it in their place (core/contextfit). The cloud fallback gets everything.
    note: added to their newest message in every local call of this request while that message is the last one (the
    Brain's one more try after a draft answered an earlier question: core.speech.latest_only); never in the history,
    and never on a tool's <tool-images> evidence. With a note, a local failure is raised, never handed to the cloud
    fallback: the Brain says its free draft instead.
    chat_system: the short chat persona (core.config.load_persona_core). With it, a turn that is conversation
    (core.chatturn.plan: no skill pointed at, nothing to look up, no order) takes the chat path: that prompt instead of
    the manual, no tools, conversation sampling, more history, a reply budget that fits the question. Whatever the chat
    path cannot answer falls back to the path below, unchanged. on_chat(True) is called before a chat reply is
    written (and on_chat(False) if it falls back), so whoever speaks the stream judges its sentences as
    parse_chat_reply does (chat_unsaid) and the two stay in step.
    talk_only: his line is a short fragment with no intent in it (low_signal_fragment). It is answered on the chat
    path or not at all: no tool is ever offered for it, and if the chat path cannot answer, this text (the Brain's
    plain "I did not catch that") is the reply. A fragment can never reach the manual's path and act."""
    every_tool = tools
    from core import kreyol_bank
    kreyol = kreyol_bank.is_note(language)           # the reply is to be Haitian Creole: checked before it is said
    schemas = _schemas(select_tools(history, tools))
    topic = _new_subject_note(history)
    payload = {"role": role, "messages": messages_for_local(system, history), "tools": schemas}
    if response_format:
        payload["response_format"] = response_format
    names = {t["function"]["name"] for t in schemas}
    retry_note, cloud_tried = note, [False]     # the Brain's one more try never pays; one API try per request
    if role == "cloud":
        # Asked for the API brain itself (the gateway's last try on a plan the local model could not make): the whole
        # payload, no local tier, logged as an API assist.
        return _cloud_fallback(payload, names, cancelled, why="local_could_not_plan" if response_format else "asked_cloud")
    newest = history[-1].get("content") if history else None
    after_tool = isinstance(newest, list) and any(_block(i).get("type") == "tool_result" for i in newest)
    said = last_user_text(history)
    nothing_ran = not after_tool and not just_asking(said)    # a "done!" now would be false (see claims_done)
    # His bare yes / no / "I don't see it" belongs to what was just offered or reported: said once, on his own line (a later round
    # of the same turn, after a tool, already acts on it).
    from core import chatturn as _bind
    bound = "" if (after_tool or talk_only or response_format or not history) else _bind.binding_note(history)
    # no skill ran this turn in a conversation that could call one: a promise to act would be empty (see promises_action).
    # Not for the tool-less calls (an interpreter's translation is someone else's "I'll send it").
    idle = not after_tool and bool(tools)
    # Fast tier only: it never falls back to the cloud here, so a half-spoken reply can't be answered twice. After a
    # lookup it's said as written (it may look again); anything else without a "One sec, sir." opener.
    # The opener goes either way: before an answer it announces a lookup that never comes, and after a tool ran
    # ("One sec, sir. Eggs and milk have been added.") it announces one that already did.
    stream_to = None
    results = _short_results(history) if after_tool else None     # the skill's own words, when they're few
    if on_text and role == "fast" and not response_format and (not schemas or after_tool):
        # After a short result, speech starts only once the reply has shown it's about that result (see _answers).
        stream_to = _past_the_opening(on_text, nothing_ran, _anchor(results, said) if results else None,
                                      said=None if after_tool else said, idle=idle)
    # The local models' 16k window: trim what matters least instead of letting Ollama drop the oldest messages
    # (the background notes). The cloud fallback below still gets everything.
    from core import contextfit, pcbridge
    offered = schemas + [ANSWER] if schemas and uses_ollama(role) and not response_format else schemas
    local = dict(payload, tools=offered, messages=contextfit.fit(payload["messages"], offered, summary, upto))

    def call(body, stream):
        # Only on HIS message: after a tool returned a screenshot, the last user message is its <tool-images> evidence
        # (messages_for_local), which is no message of his (review 2026-11-11).
        if note and body.get("messages") and _his_message(body["messages"][-1]):
            body = dict(body, messages=_with_note(body["messages"], note))
        # Which language to answer in (audio.language.reply_note: he switches by speaking it). On his newest message,
        # in every local call of the request, chat path and tool path alike; the persona, the tools and the
        # conversation before it are the same bytes, so the cached prefix holds. '' for an all-English conversation.
        # The round that words a tool's result ends on the result, not on his message, and needs it most (the result
        # is English): the note stays on the message that asked, the same bytes as the round before (review 2026-11-12).
        # A new subject on the manual's path gets the same one-request note as on the chat path (chatturn.note adds
        # its own there): "did you catch the game?" after a workout answer was answered with the workout.
        extra = language if "profile" in body else f"{language} {topic}".strip()
        plain_extra = extra
        extra = f"{extra} {bound}".strip()     # his bare yes / no / "I don't see it" belongs to what was just said (chatturn.binding_note)
        if not response_format:     # both rounds of the turn carry it (the cached prompt stays the same after a tool)
            extra = f"{extra} {_bind.decided_note(history)}".strip()   # he heard the warning and decided: no second one
        asked = _asked_at(body.get("messages") or []) if extra else None
        plain_body = body                      # the same request without that note, for the one retry below
        if asked is not None:
            plain_body = dict(body, messages=_with_note(body["messages"], plain_extra, asked)) if bound else body
            body = dict(body, messages=_with_note(body["messages"], extra, asked))
        if not pcbridge.local_pc() and os.getenv("JARVIS_PC_URL"):
            # Same private authenticated link; never expose LM Studio itself to the network.
            try:
                return pcbridge.json_request("/api/pc/intelligence", "POST", body)
            except pcbridge.PCError:
                if "profile" in body:
                    # The laptop is deployed before the PC: a PC that does not know the chat profile yet refuses the
                    # field. Once more without it: the same chat turn (persona, no tools) at the PC's own settings.
                    try:
                        return pcbridge.json_request("/api/pc/intelligence", "POST",
                                                     {k: v for k, v in body.items() if k != "profile"})
                    except pcbridge.PCError:
                        pass
                # The laptop's own model used to step in while the PC was off, but on the laptop's CPU it reads
                # ~33 tokens/s: Jarvis's ~10k-token prompt never fit in its 60 s, so each turn waited a minute for
                # nothing. An unavailable PC goes straight to the next tier (Gemini, then Claude).
                raise LocalFailure("pc_local_provider_unavailable") from None
        try:
            return complete_local(body, cancelled, on_text=stream)
        except LocalFailure as failure:
            # The note is only help. Live probe 2026-11-15: with the "I don't see it" note the model wrote a tool call Ollama
            # could not parse (HTTP 500 "expected element type <function>") on both tiers, 5 of 5: once more without it, so a
            # failure of the model's own making never costs the turn ("I lost my train of thought").
            if bound and plain_body is not body and str(failure) == "local_request_failed" and not cancelled():
                audit(role, "bound_note_dropped")
                return complete_local(plain_body, cancelled, on_text=stream)
            raise

    def converse(kind):
        """The chat path: the persona, the conversation (more of it: contextfit.fit(chat=True)), what he knows about
        them worded as knowledge, no tools. Spoken as it is written, minus anything of the machinery. Returns the reply,
        or raises LocalFailure with `.spoke` set when part of it was already said."""
        from core import chatturn
        heard = [False]
        plain = kind == chatturn.CHAT or bool(chatturn.restyle(said) and chatturn.restyle_target(said, history))

        def spoken(piece):
            heard[0] = True
            on_text(piece)

        def checked(response):
            # The one greeting word the clock contradicts is set right, here as in the stream (chatturn.Hold).
            out = parse_chat_reply(response, said, kind)
            for block in out.content:
                block.text = chatturn.right_greeting(block.text, said)
            return out
        if on_chat:
            on_chat(kind)                            # which chat profile: the stream judges sentences as the parse does
        base = messages_for_local(chat_system, history)
        if not about_them(said) and not chatturn.wants_recall(said):
            base = chatturn.without_recall(base)         # a plain remark gets no lookup to recite (chatturn.without_recall)
        base = chatturn.lean_messages(base, said, kind)  # nor his facts and old conversations (chatturn.lean_messages)
        base = chatturn.lean_history(base, said)         # an acknowledgement needs no lesson text (chatturn.lean_history)
        base = chatturn.restyle_history(base, said) if chatturn.restyle_target(said, history) else base   # "Simpler.": the last exchange, in full
        fitted = _as_known(contextfit.fit(base, [], summary, upto, chat=True), last_user_text(history))
        # The hour goes on his newest message with the turn's note: the persona and the conversation before it stay
        # the same bytes, so the cached prefix holds.
        note = f"{chatturn.clock_note()} {chatturn.note(kind, history)}"
        body = {"role": role, "messages": _with_note(fitted, note), "tools": [], "profile": kind}
        # The stream's first words wait while the reply could still be one he just gave (chatturn.Hold): a repeat
        # is replaced before any of it is said.
        # A Creole reply is checked whole before a word of it is said (core.kreyol_bank): nothing is streamed.
        anchor = None if kreyol else chatturn.subject_check(history, said)     # a restyle's subject (chatturn.restyle_subject)
        cap = chatturn.length_cap(said, history) if anchor else None          # Shorter / Simpler: bounded in code (chatturn.length_cap)
        hold = chatturn.Hold(spoken, history, said, plain, subject=anchor[0] if anchor and anchor[2] else None, restyle=bool(anchor), cap=cap) if on_text and not kreyol else None

        def in_creole(out):
            """A Creole reply with words that are not Creole is written once more with those words named; still bad,
            its clean sentences or a short plain bank sentence are said instead. Never silence, never nonsense."""
            from audio import language as tongue
            text = " ".join(b.text for b in out.content)
            if not out.content or not text.strip() or "[ignore]" in text.lower():
                return out
            code, sure = tongue.identify(text)
            if code == "en" and sure >= tongue.CLEAR:
                return out                           # he wrote a whole English sentence and was answered in English
            def regenerate(more):
                """The one more try: the same chat turn with the note that names what was wrong."""
                try:
                    retry = _with_note(fitted, f"{note} {more}")
                    return " ".join(b.text for b in checked(call(dict(body, messages=retry), None)).content)
                except LocalFailure:
                    if cancelled():
                        raise
                    return ""
            text = kreyol_bank.settle(text, said, regenerate, audit=lambda what: audit(role, what))
            out.content[0].text = text
            del out.content[1:]
            return out
        def restyled(out):
            """A restyle reply: no opening commentary (a sentence about the request or earlier replies, an echoed style
            word), the length rule of Shorter / Simpler, and on the subject. Written once more, with what was wrong named,
            only when nothing is left after the commentary, a chained reply has none of its subject's words, or one
            sentence is already over the cap; the better of the two is kept and never an empty reply."""
            def text_of(o):
                return " ".join(b.text for b in o.content)

            def tidy(text):
                return chatturn.fit_length(chatturn.strip_opener(text, said), cap)
            raw = text_of(out)
            if not raw.strip() or "[ignore]" in raw.lower():
                return out
            text = tidy(raw)
            if len(text.split()) < 2:
                why, more_note = "chat_restyle_commentary", chatturn.COMMENTARY_NOTE
            elif anchor[2] and not chatturn.subject_overlap(text, anchor[0]):
                why, more_note = "chat_restyle_off_subject", chatturn.subject_note(anchor[1])
            elif cap and len(text.split()) > cap:
                why, more_note = "chat_restyle_long", chatturn.length_note(cap)
            else:
                why = ""
            if why and not heard[0]:
                audit(role, why)
                try:
                    second = checked(call(dict(body, messages=_with_note(fitted, f"{note} {more_note}")), None))
                    more_raw = text_of(second)
                    more = tidy(more_raw)
                    if "[ignore]" not in more_raw.lower() and more.strip():
                        if why == "chat_restyle_commentary":
                            better = len(text.split()) < 2 or chatturn.commentary_share(more_raw) < chatturn.commentary_share(raw)
                        elif why == "chat_restyle_off_subject":
                            better = chatturn.subject_overlap(more, anchor[0]) > 0
                        else:
                            better = len(more.split()) < len(text.split())
                        if better:
                            out, raw, text = second, more_raw, more
                except LocalFailure:
                    if cancelled():
                        raise
            if len(text.split()) < 2:
                text = raw                           # all of it was commentary, twice: the least commentary, never silence
            if text != raw.strip() or len(out.content) > 1:
                out.content[0].text = text
                del out.content[1:]
            return out

        if (kind in (chatturn.DEPTH, chatturn.THINK) and not kreyol and not anchor and not cancelled()
                and not retry_note and not cloud_tried[0] and hard_first()):
            cloud_tried[0] = True        # one paid try per request (THINK falling back to DEPTH does not ask again)
            # A harder question (why / how / explain / an opinion): the API brain answers first, with everything the
            # local prompt would have (cloud gets the whole conversation, not the 16k-trimmed window). Spoken whole
            # once written. Any failure: the local model answers as before.
            try:
                cloud_body = {"role": "cloud", "messages": _with_note(_as_known(base, last_user_text(history)), note),
                              "tools": []}
                return _cloud_raw(cloud_body, cancelled, "hard_question", "chat", checked, said=said)
            except LocalFailure as error:
                if cancelled():
                    raise
                audit(role, "cloud_hard_" + str(error)[:40])
        try:
            reply = call(body, hold.feed if hold else None)
            if cancelled():
                raise LocalFailure("cancelled")
            out = checked(reply)
            first = " ".join(b.text for b in out.content)
            why, earlier = chatturn.stale(first, said, history) if plain and not heard[0] else ("", "")
            if why:
                audit(role, "chat_again_" + why)
                again = _with_note(fitted, f"{note} {chatturn.again_note(why, earlier or first)}")
                try:
                    second = checked(call(dict(body, messages=again), None))
                    if "[ignore]" not in " ".join(b.text for b in second.content).lower():
                        out = second                 # kept whatever it is: one more try, never a third, never silence
                except LocalFailure:
                    if cancelled():
                        raise
            if anchor:
                out = restyled(out)
            return in_creole(out) if kreyol else out
        except LocalFailure as error:
            if kind == chatturn.THINK and not heard[0] and not cancelled() and str(error) != "cancelled":
                audit(role, "chat_think_" + str(error)[:40])   # reasoned too long, or nothing came after it: without
                return converse(chatturn.DEPTH)
            error.spoke = heard[0]
            raise

    def in_words(note="", again=True, stream=True):
        """Ask again with no tools: the model answers them itself (streamed, like small talk; `stream=False` when the
        reply may be judged and replaced, so a wrong first answer is never half-spoken)."""
        messages = _with_note(contextfit.fit(payload["messages"], [], summary, upto), note)
        reply = call(dict(local, tools=[], messages=messages),
                     _past_the_opening(on_text, nothing_ran, said=None if after_tool else said, idle=idle)
                     if on_text and role == "fast" and stream else None)
        if cancelled():
            raise LocalFailure("cancelled")
        if again and _only_opening(reply):           # nothing of it was said (see _past_the_opening)
            return in_words(f"{note} {_SAY_IT}".strip(), again=False)
        return parse_reply(reply, set(), said=said, nothing_ran=nothing_ran, answering=True, unchecked=not after_tool,
                           idle=idle)

    def without_acting(refused=False):
        """Answer without doing anything: in words, or, for a question about them, by looking it up first. `refused`:
        an action was turned down, so the words must not claim it happened ("I approve the $1 limit" -> its refused
        call -> "The budget is now set to $1")."""
        if not about_them(said):
            return in_words(_DID_NOTHING if refused and not just_asking(said) else "")
        if about_this_chat(said):
            return in_words(_THIS_CHAT)                  # the answer is the conversation itself, not a lookup
        notes = retrieved_notes(history)
        if notes and not _IN_VIEW.search(said):
            out = in_words(_FROM_NOTES, stream=False)    # the lookup already happened: its findings are attached
            if _denies(out) and _shares(said, {w for w in _words(notes, hints=False) if len(w) >= 3}):
                # "I don't know your wife's name." with "Maya is the owner's wife." right above (llama3.2, 1 run
                # in 3): once more with the notes quoted, then the stronger tier. Neither attempt streams: the
                # denial was being spoken before the retry's answer could replace it.
                out = in_words(f"(Your notes say: {notes[:600]} Answer from them in one sentence.)", again=False,
                               stream=False)
                if _denies(out):
                    raise LocalFailure("ignored_notes")
            return out
        looks = bool(_IN_VIEW.search(said))           # "what's behind me?": the camera is the lookup
        extra = _ALWAYS_LOOKUP + (("look_through_camera", "what_am_i_holding", "look_at_screen") if looks else ())
        if _PAST_TALK.search(said) and _OLDER_TALK.search(said):
            extra = ("recall_conversations",)          # "what did we talk about yesterday?": the journal, not the vault
        pool = schemas + [t for t in _schemas(every_tool) if t["function"]["name"] in extra]
        lookups = list({t["function"]["name"]: t for t in pool if not _ACTS.match(t["function"]["name"])
                        and (looks or t["function"]["name"] not in _CAPTURE)}.values())
        body = dict(local, tools=lookups, messages=contextfit.fit(payload["messages"], lookups, summary, upto))
        try:
            reply = call(body, None)
            if cancelled():
                raise LocalFailure("cancelled")
            return parse_reply(reply, {t["function"]["name"] for t in lookups}, said=said, answering=True, idle=idle)
        except LocalFailure:
            if cancelled():
                raise
            return in_words(_HONEST)                 # no lookup came back usable: answer, but never invent
    if chat_system:
        from core import chatturn
        kind = chatturn.plan(role, history, schemas, after_tool, response_format)
        if talk_only and not after_tool and not response_format:
            kind = kind or chatturn.CHAT
        if kind and kreyol:
            kind = chatturn.CHAT                     # the model's Creole is weak: one or two short sentences, never an essay
        if kind:
            try:
                return converse(kind)
            except LocalFailure as error:
                if cancelled() or str(error) == "cancelled" or getattr(error, "spoke", False):
                    raise                            # he cut in, or part of the answer is out loud: the Brain ends on it
                if talk_only:
                    from anthropic.types import TextBlock
                    audit(role, "chat_fragment_" + str(error)[:40])
                    if on_text:
                        on_text(talk_only + " ")
                    return SimpleNamespace(content=[TextBlock(type="text", text=talk_only)], stop_reason="end_turn",
                                           model=None)
                audit(role, "chat_path_" + str(error)[:40])     # then the manual's path below answers, as before
                if on_chat:
                    on_chat(False)
    try:
        if not after_tool and role == "fast" and not response_format and about_this_chat(said):
            return in_words(_THIS_CHAT)              # "what were we just talking about?": the chat itself, no tool
        if not after_tool and role == "fast" and not response_format and about_them(said) and (
                not schemas or _notes_answer(history, said)):
            # No skill matched a question about them: look, don't guess. Or its answer is already in the notes
            # attached: with tools offered too, the model searched the installed programs for "Maya".
            return without_acting()
        response = call(local, stream_to)
        if cancelled():
            raise LocalFailure("cancelled")
        if not schemas and not response_format and _only_opening(response):
            # Small talk answered with only "One sec, sir." (see canned_small_talk): once more, then a word of our own.
            response = call(dict(local, messages=_with_note(local["messages"], _SAY_IT)), stream_to)
            if cancelled():
                raise LocalFailure("cancelled")
            if _only_opening(response) and canned_small_talk(said):
                from anthropic.types import TextBlock
                return SimpleNamespace(content=[TextBlock(type="text", text=_titled(canned_small_talk(said)))],
                                       stop_reason="end_turn", model=response.get("model"))
        if offered is not schemas:
            picked = {c.get("function", {}).get("name") for c in _calls(response)}
            # ...or it wrote out that it will: "I will use the `answer_directly` function to respond. {..."
            typed = not picked and ANSWER["function"]["name"] in str(_content(response))
            if picked == {ANSWER["function"]["name"]} or typed:
                return without_acting()              # it chose to answer, not to act
            response = _without_answer(response)     # an answer_directly next to a real call: run the real one
        try:
            if response_format:
                return _structured(response)             # the JSON asked for IS the answer, not a leaked call
            out = parse_reply(response, names, said=said, heard=heard_in(history), nothing_ran=nothing_ran,
                              answering=True, asked=jarvis_asked(history), recent=used_in(history), unchecked=not after_tool,
                              idle=idle)
            evidence = _web_evidence(history) if after_tool and out.stop_reason == "end_turn" else None
            if evidence:
                worded = " ".join(b.text for b in out.content if b.type == "text")
                if (not _shares(worded, _anchor([evidence], said)) or _unsourced_urls(worded, evidence)
                        or _unsourced_numbers(worded, evidence) or not _web_content_grounded(worded, evidence)):
                    # They asked for a live search and got one; a reply about something else is the model's memory
                    # talking, and a link that is not in the evidence is invented (live, laptop: the 3B model turned
                    # nvidia.com/.../details/282412/ into .../details/621.01/). The next tier tries; failing that,
                    # the brain speaks the sources itself.
                    raise LocalFailure("ungrounded_answer")
            if results and out.stop_reason == "end_turn":
                if not _answers(out, results, said):
                    # add_note said "Added to shopping (1 open item)." and the 3B model announced "You have a little
                    # over two hundred dollars left on your budget." A reply about a short result must be about it (or
                    # about what they asked); otherwise the next tier tries, and failing that the result is said as is.
                    raise LocalFailure("ungrounded_answer")
                text = " ".join(b.text for b in out.content if b.type == "text")
                if any(_RESULT_FAILED.search(r) for r in results) and any(map(claims_done, _SENTENCE.split(text))):
                    # set_reminder answered "Couldn't read that time" and the reply was "I've set a reminder for you".
                    raise LocalFailure("claimed_despite_failure")
                if just_asking(said) and not _informs(text, results, said):
                    # read_list answered "[cc8fa] (shopping) eggs, milk" and the reply was "You can view the shopping
                    # list by saying 'read shopping list'": an answer to a question must carry what was found.
                    raise LocalFailure("uninformative_answer")
                if any(_RESULT_FAILED.search(r) for r in results) and _embellishes(text, results, said):
                    # "The weather service isn't reachable right now." became "The weather service is currently down
                    # for maintenance": a failure is reported in the skill's own words, never with an invented cause.
                    raise LocalFailure("embellished_error")
            return out
        except LocalFailure as error:
            # A call they didn't ask for (an action on a plain question, an app nobody named, a made-up amount...),
            # or a question answered with a typed-out call: answer here without acting, rather than act or escalate
            # a "what's my wife's name?" to the slow tiers.
            if role == "fast" and not response_format and (str(error) == "ungrounded_tool_call" or (
                    just_asking(said) and str(error) in ("invalid_response", "invalid_tool_call"))):
                return without_acting(refused=str(error) == "ungrounded_tool_call")
            raise
    except LocalFailure as error:
        # Local tiers always come first. On the final local tier ("advanced"),
        # try the configured cloud provider (Gemini) before the brain gives up to
        # Claude — keeping the cascade local → cloud fallback → Claude.
        # Never for the Brain's one more try (`note`): a free draft is there, and a paid call must not replace it (the
        # Brain says the draft instead, review 2026-11-11).
        if role != "advanced" or cancelled() or note:
            raise
        audit(role, str(error))      # the LOCAL reason, before the cloud's statuses hide it (they did, for weeks)
        return _cloud_fallback(payload, names, cancelled)


# Purely social turns get NO tools. With any tool attached, the local Llama's template tells it to answer with a
# function call, so small talk fired random tools: "You like that?" ran review_clip, "We'll see you next time" ran
# get_time, "Are you there?" typed out a look_at_screen call. Each misfire was rejected and escalated to the slow
# tiers: 8-16 s of silence before a one-line reply. With no tools the same model answers in well under a second.
# Matching is whole-clause and conservative: anything that could be a request keeps the tools.
_VOCATIVE = re.compile(r"^(?:(?:hey|hi|yo|ok|okay)\s+)?(?:jarvis|charvis|jervis|travis|javis|jarvas|jarves|jarvus)\b\s*"
                       r"|\s*\b(?:jarvis|charvis|jervis|travis|javis|jarvas|jarves|jarvus|sir|bro|buddy|dude|my friend)$")
_TALK = (
    ("social", re.compile(
        r"h(?:i|ello|ey|iya|owdy)(?: there)?|yo|sup|wassup|what'?s up|what'?s good|good (?:morning|afternoon|evening|day)"
        r"|morning|evening|(?:good ?)?bye(?: bye)?|good ?night|night(?: night)?"
        r"|(?:(?:we'?ll|i'?ll) )?see (?:you|ya)(?: (?:later|soon|tomorrow|next time|around))?"
        r"|talk (?:to you )?(?:later|soon|tomorrow)|catch you later|take care|ciao|peace(?: out)?|sleep well"
        r"|have a (?:good|great|nice) (?:one|night|day|evening)"
        r"|how are you(?: doing)?(?: today| tonight)?|how(?:'s| is) it going|how you doing|how have you been|how'?s your day"
        r"|(?:are )?you (?:there|still there|here|still here|awake|ok|okay|alright|good|listening)"
        r"|(?:ha)+h?|(?:he)+|lol|lmao|wow|whoa|woah|oh+|ah+|aha|hmm+|huh|oops|yikes|damn|dang|geez|jeez|ugh|meh|bah|pfft"
        r"|oh (?:my )?(?:god|gosh|man|wow|snap)|my (?:god|gosh)|no way"
        r"|(?:did )?you (?:like|hear|heard) (?:that|it|this)|you like it"
        r"|good (?:job|work|stuff)|well done|nice (?:job|work|one)|you rock"
        r"|you(?:'re| are) (?:the best|awesome|amazing|funny|hilarious|a legend|smart)"
        r"|that(?:'s| is| was) (?:funny|hilarious|so funny|awesome|amazing|impressive|crazy|wild|sick)"
        r"|(?:i )?love (?:you|it|that)|(?:i was|i'?m) just (?:testing|messing with|kidding|joking|playing)(?: (?:you|around|with you))?")),
    ("thanks", re.compile(r"thank(?:s| you)(?: (?:so|very) much| a lot| again| for (?:that|everything|the help|your help))?"
                          r"|cheers|much appreciated|(?:i )?appreciate (?:it|you|that)")),
    ("decline", re.compile(r"nothing(?: else| much| for now)?|that'?s (?:all|it)(?: for now)?|that will be all|no thanks"
                           r"|no thank you|i'?m (?:good|fine|all set)|we'?re good|you'?re good|all set|not (?:right )?now"
                           r"|maybe later|never ?mind|forget it")),
    ("no", re.compile(r"no|nah|nope|no no")),
    ("yes", re.compile(r"yes|yeah|yep|yup|yes sir|yeah yeah|sure")),      # answers something: never small talk alone
    ("react", re.compile(r"cool|nice|great|awesome|perfect|excellent|brilliant|sweet|good|very good|so good|pretty good|not bad"
                         r"|that(?:'s| is| was) (?:good|great|nice|cool|fine|perfect|very good|so good)|all good|it'?s all good"
                         r"|no worries|no problem")),
    ("ok", re.compile(r"ok|okay|alright|all right|got it|gotcha|understood|i see|makes sense|fair enough|sounds good"
                      r"|that(?:'s| is) (?:ok|okay|alright|all right)")),
)


def _talk_kinds(said):
    """The kinds of social clause in `said` (see _TALK), or None when any clause could be a request."""
    kinds = set()
    for clause in re.split(r"[.!?,;:…]+|\s[-–—]\s", said.lower().replace("’", "'")):
        clause = _VOCATIVE.sub("", clause.strip()).strip()
        if not clause:
            continue
        kind = next((k for k, rx in _TALK if rx.fullmatch(clause)), None)
        if kind is None:
            return None
        kinds.add(kind)
    return kinds


def small_talk(said, asked=False):
    """Is this purely social: greetings, thanks, laughter, compliments, goodbyes, a polite no?

    `asked` = Jarvis's last message was a question, so a bare "thanks" / "okay" may be the answer to it (consent to
    act): then only clearly social talk or a decline counts. Words that could be a request never count."""
    kinds = _talk_kinds(said)
    if not kinds or kinds <= {"no", "ok", "yes"}:  # a bare "no" / "okay" / "yes" answers something
        return False
    if asked:
        return kinds <= {"social"} or "decline" in kinds or ("no" in kinds and bool(kinds & {"thanks", "social"}))
    return True


# Measured on the owner's own lines: "Thank you.", "Yes.", "Okay." and "Bah!" each got back ONLY "One sec, sir." from
# llama3.2 (7 of 11 refused replies in a 40-line probe). Nothing to look up, nothing to say, and the turn escalated.
# Asked once more it usually answers; if not, a plain word in Jarvis's voice beats "Local intelligence is unavailable".
# The kinds of purely social line answered in code when the model gives nothing usable; the words are core/character.py's
# (one voice with the character sheet, varied, "sir" on about one in three: wave 3b 2026-11-21, was "Very good, sir." every time).
_CANNED = ("thanks", "decline", "no", "react", "social", "yes", "ok")


def canned_small_talk(said):
    """A short reply for a purely social line, in Jarvis's voice, when the model couldn't produce one."""
    from core import character
    kinds = _talk_kinds(said or "") or set()
    for kind in _CANNED:
        if kind in kinds:
            return character.line("decline" if kind == "no" else kind)
    return character.line("fallback") if kinds else None


_STOP = set("the and you can please jarvis that this with have what whats how who when where why which are was were "
            "will would could should does did for from about into its his her our your their them they him she there "
            "here then than some any all not but too very just now got let lets need want know tell give going gonna "
            "really much many sir yes yeah okay like also been being had has may might must".split())
# Everyday words for a skill that never say its name: "is it going to rain?" means the weather.
_HINTS = {"weather": "rain umbrella snow sunny cloudy forecast temperature hot cold warm chilly freezing windy wind "
                     "storm stormy humid degrees outside jacket coat",
          "directions": "far distance drive away miles navigate",
          "calc": "percent plus minus times divided multiply calculate math sqrt",
          "feed": "news headlines",
          "media": "music song songs pause skip track",
          "volume": "louder quieter loud quiet mute",
          # "Google this" / "look it up online" mean the live internet search.
          "search": "google googled googling bing online web internet lookup browse",
          "internet": "google googled googling bing online web lookup browse",
          # "do some tricks" ran performance_check; the little robot's play is the companion skill.
          "companion": "tricks trick dance juggle jetpack kick catch robot buddy mascot stretch joke game games trivia "
                       "rock paper scissors reflex",
          # "What did we talk about yesterday?" searched the memory vault; past talk is the journal's.
          "conversation": "talk talked talking discussed discuss discussing chat chatted said conversation yesterday",
          # "did you add my son's powdered milk?" looked for a recipe; the shopping list is a list.
          "list": "added add shopping groceries grocery bought buy",
          # "and the RAM?" / "is it overheating?" after "how's my PC doing?": get_system_status measures them, and a
          # reading nothing measured is never said (unchecked_reading).
          "system": "ram vram cpu gpu fan fans overheat overheating thermal uptime",
          # "Am I free on Tuesday?" offered no skill at all, and the brain guessed (sandbox 2026-11-20): his Google Calendar.
          "calendar": "schedule agenda appointment appointments meeting meetings busy free event events dentist doctor"}
# Past talk ("what did we talk about yesterday?") is answered from the journal, not the memory vault: with both
# offered, the 3B model searched the vault every time.
_PAST_TALK = re.compile(r"\b(talk(?:ed|ing)?|discuss(?:ed|ing)?|chat(?:ted|ting)?|conversation|said|decided?|agreed?)\b", re.I)


def _stem(word):
    """remind/reminder/reminders, rain/raining, workout/workouts -> one form (applied to both sides alike)."""
    for suffix, keep in (("ings", 4), ("ing", 3), ("ers", 4), ("er", 4), ("ies", 3), ("es", 4), ("s", 3), ("ed", 3)):
        if word.endswith(suffix) and len(word) - len(suffix) >= keep:
            return word[:-len(suffix)] + ("y" if suffix == "ies" else "")
    return word


_HINT_OF = {}
for _skill, _everyday in _HINTS.items():
    for _w in _everyday.split():
        _HINT_OF.setdefault(_stem(_w), set()).add(_stem(_skill))


def _words(text, hints=True):
    stems = {_stem(w) for w in re.findall(r"[a-z]{3,}", text.lower().replace("'", "")) if w not in _STOP}
    return stems.union(*(_HINT_OF.get(s, ()) for s in stems)) if hints else stems


@functools.lru_cache(maxsize=512)
def _tool_words(name, description):
    return _words(name.replace("_", " ") + " " + description, hints=False)


def _last_assistant_text(history):
    """What Jarvis said just before the newest user turn."""
    for turn in reversed(history[:-1]):
        if turn.get("role") == "assistant":
            value = turn.get("content") or ""     # a tool call with no words has content None (messages_for_local)
            return value if isinstance(value, str) else " ".join(
                _block(i).get("text", "") for i in value if _block(i).get("type") == "text")
    return ""


_RIG_ASK = re.compile(r"\b(?:rigging|rigged|rig)\b.*\b(?:skills?|abilit(?:y|ies)|good at|able to|can you|do you)\b|"
                      r"\b(?:your|you) (?:\w+ )?(?:rigging|blender) (?:skills?|abilit(?:y|ies))\b|\bcan you rig\b|"
                      r"\bgood\b.*\bat (?:rigging|rig|blender)\b", re.I)
_RIG_LESSONS = re.compile(r"\b(?:learn(?:ed|t)?|lessons?|mistakes?|tests?)\b.*\b(?:rigging|rig)\b|\b(?:rigging|rig)\b.*\b(?:learn(?:ed|t)?|lessons?|mistakes?)\b", re.I)
_RIG_DO = re.compile(r"\b(?:rig|rigged|rigging)\b.*\b(?:this|that|my|the|an?)\b.*\b(?:image|picture|photo|model|glb|character|creature|robot)\b|"
                     r"\b(?:image|picture|photo)\b.*\b(?:rigged|rig)\b|\bgame[- ]ready character\b|"
                     r"\brig\s+(?:my|the|this|that|it|him|her|them)\b|\brig\b.*\b(?:i have open|open in blender|already open)\b|"
                     r"\bmake\b.*\b(?:rigged|game[- ]ready)\b", re.I)
_CAN_YOU_RIG = re.compile(r"\b(?:can|could|are you able to|do you know how to) you (?:\w+ )?rig an?\b", re.I)
_SPECIFIC_THING = re.compile(r"\b(?:my|the|this|that|it|these|those|open|image|picture|photo)\b", re.I)
_CHAR_FIND = re.compile(r"\bfind (?:me )?(?:my|the|a)\b.*\b(?:character|model)\b|\b(?:which|what)\b.*\b(?:characters?|models?)\b.*"
                        r"\b(?:have|open|saved|downloaded|made|got)\b|\bwhat (?:am i|do i have) (?:working on|open)\b", re.I)
_WINDOWS_ASK = re.compile(r"\b(?:what|which|list|show)\b.*\b(?:windows?|apps?|programs?)\b.*\b(?:open|running|up)\b|"
                          r"\b(?:open|running) (?:windows?|apps?|programs?)\b|"
                          r"\bwhat(?:'s| is) (?:in front|on top|focused)\b|\bwhat am i (?:looking at|running)\b", re.I)


_INTERNAL_CRAFT_TOOLS = frozenset({"rig_character", "image_to_character", "review_learned_strategies", "approve_learned_strategy", "reject_learned_strategy"})


def _verified_two_bone(said):
    try:
        from tools import blenderskills
        return blenderskills.wants(said)
    except Exception:
        return False


def _installed_skill_wants(said):
    try:
        from core import skillhost
        return skillhost.wants(said)
    except Exception:
        return False


def _own_skill_tools(said, eligible):
    pick = None
    if _verified_two_bone(said):
        pick = ("bind_two_bone_limb",)                   # a stored verified procedure covers this exact job
    elif _CAN_YOU_RIG.search(said) and not _SPECIFIC_THING.search(said):
        pick = ("rigging_skills", "rigging_lessons")        # "Can you rig a character?" asks about ability, not a job
    elif _RIG_LESSONS.search(said) and not _RIG_DO.search(said):
        pick = ("rigging_lessons", "rigging_skills")
    elif _RIG_ASK.search(said) and not _RIG_DO.search(said):
        pick = ("rigging_skills", "rigging_lessons")
    elif _RIG_DO.search(said):
        pick = ("rig_my_character",)      # one tool: the resolver finds files, takes paths and pictures, and asks itself
    elif _CHAR_FIND.search(said):
        pick = ("find_character", "rig_my_character")
    elif _WINDOWS_ASK.search(said):
        pick = ("list_windows", "observe_pc")
    elif _installed_skill_wants(said):
        pick = ("run_installed_skill",)                  # last, like the chat front door: built-in routes always win over an installed package
    if pick:
        found = [t for n in pick for t in eligible if t["name"] == n]
        return found or None
    return None


WEB_TOOLS = {"search_internet", "research_online", "open_web_page", "find_youtube_tutorials", "youtube_transcript",
             "read_web_pages", "search_web_free", "recall_research", "save_research", "jarvis_research_sources"}


# These are not instructions on their own: they refer back to a request that
# Jarvis has already said it could not identify.  In particular, do not let a
# small local model turn "make corrections too" into corrections to an object
# it invented while trying to be helpful.  This only runs while Brain is
# explicitly waiting for the person to state an intent; clear commands and
# questions still take the usual route.
_UNRESOLVED_CLARIFICATION_FOLLOWUP = re.compile(
    r"^\s*(?:please\s+)?(?:"
    r"(?:make|apply|do)\s+(?:the\s+)?(?:corrections?|changes?|fix(?:es)?)\s*(?:too|then|now)?"
    r"|(?:continue|go\s+on|carry\s+on|do\s+it|fix\s+it|make\s+it\s+(?:better|right))"
    r"|(?:we\s+have\s+)?no\s+request\s+to\s+stop"
    r")\s*[.!?]*\s*$",
    re.I,
)


def unresolved_clarification_followup(text: str) -> bool:
    """Whether ``text`` still lacks the intent Jarvis just asked the person for.

    This is purposefully a small set of deictic follow-ups observed in the
    live voice/phone pipeline, not a general restriction on short messages.
    It is meaningful only when the caller has an outstanding clarification.
    """
    return bool(_UNRESOLVED_CLARIFICATION_FOLLOWUP.fullmatch(_CHANNEL_NOTE.sub("", text)))


# A closing offer, not a question waiting for an answer: "Anything else?", "Anything on your mind?", "How can I help?",
# "What would you like to do next?", "Want me to ...?" (a yes/no offer; a two-word fragment is not a yes).
GENERIC_OFFER = re.compile(r"(?:\banything(?: else| specific| on your mind| i can| you)?\b[^?]*\?|\bhow (?:can|may) i help\b[^?]*\?|"
                            r"\bwhat (?:would|do|else) you (?:like|need|want)\b[^?]*\?|\bwhat can i (?:do|help(?: you)? with)\b[^?]*\?|"
                            r"\b(?:want me to|would you like me to|shall i|should i) (?:open|start|run|launch|check|build|look|help|go ahead|do|"
                            r"pull|show|play|find|search|set|add|make|get|handle|keep|close|tell|describe|explain|walk|read|go)\b[^?]*\?|"
                            r"\bneed (?:anything|a hand|help)\b[^?]*\?|"
                            r"\bhow about you\?|\bhow'?s your (?:day|night|evening)\b[^?]*\?|"
                            r"\bcould you (?:say|tell me) (?:a (?:little|bit) )?more\b[^?]*\?|"   # his own reply to a fragment,
                            r"^\s*what did you need\?)\s*$", re.I)   # then and now ("I'm not sure what you meant by 'quiet pencil', sir. What did you need?")



_LAST_QUESTION = re.compile(r"([^.!?]*\?)\s*$")
_NOT_AN_OFFER = re.compile(r"\bor\b|\b(?:name|call|title|label)\b|\bwhich\b|\beither\b|\bboth\b", re.I)


def generic_offer(text):
    """Does the reply END with a closing offer ("Anything else?", "Want me to tell you more?"), not a real question?
    Only the final sentence counts, and a choice or naming question never does ("Want me to open Blender or Unreal?",
    "What should I call it?": a two-word answer to those is an answer). Independent review 2026-11-10."""
    m = _LAST_QUESTION.search((text or "").strip())
    if not m:
        return False
    last = m.group(1).strip()
    return not _NOT_AN_OFFER.search(last) and bool(GENERIC_OFFER.search(last))


def low_signal_fragment(history, *, allow_after_question=False):
    """True for an isolated, two-word fragment that has no actionable meaning.

    This is deliberately narrower than ``small_talk``.  A phrase such as
    "frozen pencil" is often a Whisper fragment, but sending it to an LLM
    invites an invented story.  Do not suppress it when Jarvis has just asked
    a question: then the same short noun phrase can be a valid answer.
    """
    if not history or history[-1].get("role") != "user":
        return False
    latest = history[-1].get("content")
    if not isinstance(latest, (str, list)):
        return False
    fresh = isinstance(latest, str) or not any(_block(item).get("type") == "tool_result" for item in latest)
    last = _last_assistant_text(history)
    # A closing offer ("Anything on your mind?", "Want me to tell you more?") is not a question this fragment answers:
    # live 2026-11-10 "velvet lantern" after one was offered the PC tools and planned "open Blender".
    if not fresh or (not allow_after_question and "?" in last and not generic_offer(last)):
        return False
    said = last_user_text(history).casefold()
    raw_words = _words(said, hints=False)
    raw_tokens = set(re.findall(r"[a-z]{3,}", said))
    fragment_actions = {"add", "call", "check", "close", "delete", "find", "get", "help", "install", "launch",
                        "list", "open", "pair", "pause", "play", "remember", "remove", "restart", "run", "save",
                        "rig", "rigged", "rigging", "search", "set", "show", "stop", "tell", "time", "turn", "uninstall",
                        "volume", "weather"}
    # Preserve terse but recognisable requests ("rain tomorrow", "music
    # please") without treating every word that happens to occur in a skill
    # description as an instruction ("quiet garden" is not a volume command).
    task_cues = {"rain", "umbrella", "snow", "forecast", "temperature", "timer", "reminder", "music", "song",
                 "screen", "screenshot", "photo", "camera", "directions", "distance", "news", "headlines"}
    return (len(raw_words) == 2 and not (raw_tokens & (fragment_actions | task_cues))
            and not just_asking(said) and not about_them(said) and not _REQUEST.search(said)
            and not small_talk(said, False)       # "Thanks, that's all for now." is a goodbye, not a fragment (voice, 2026-11-10)
            and not any(pattern.search(said) for pattern in _GROUNDING.values()))


# A plain question about this PC ("how much free space is on my C drive?", "what's my IP address?") that a stored,
# proven, read-only playbook already answers.  Offered every tool, the 3B model picked list_windows for disk space, a
# web search for the IP address and the webcam build-checker for "Windows build" (live, 2026-11-05).
_PC_FACT_QUESTION = re.compile(r"^(?:(?:hey\s+)?jarvis[,\s]+)?(?:how\s+(?:much|many|big|large|old|long|full)|what(?:'s|\s+is|\s+are|\s+was)?|"
                               r"which|who|where(?:'s|\s+is)|do\s+i\s+have|is\s+there|tell\s+me|show\s+me|check|give\s+me|list|count)\b", re.I)


# Words that appear in many playbook titles and in ordinary conversation alike; they never decide a match.
_GENERIC_PLAYBOOK_WORDS = frozenset(
    "what whats does have much many am as is set which who my this the are do number current size again answer ask casually "
    "informal dumb question sorry installed".split())


_NOT_A_MACHINE_FACT = re.compile(r"\b(?:best|better|worst|should|recommend\w*|buy|price|prices|cost|costs|cheap\w*|worth|versus|vs|compare\w*|good|top|review\w*|why|how\s+do|how\s+to|how\s+can|for\s+gaming|ever|could|would)\b", re.I)


def learned_playbook(said):
    """The stored playbook a plain PC-fact question clearly asks for, or None.

    Only proven, non-stale, read-only (T0), shell-kind playbooks that need no values from the person.  A match needs at least
    TWO distinctive topic words from the playbook (generic words never count) covering at least 30% of its distinctive words, in a question of at most 14 words;
    one shared word is how "what's the weather today?" once matched "what is today's date"."""
    try:
        from jarvis_pc.learning.playbooks import keywords
        from tools import pcskills
        books = pcskills.skills().playbooks.all()
    except Exception:  # a missing or unreadable store must never break tool selection
        return None
    if len(said.split()) > 14:  # a plain fact question is short; long, multi-part requests keep normal routing
        return None
    if _NOT_A_MACHINE_FACT.search(said):  # advice, opinion, shopping, how-to: not "what is true of THIS PC"
        return None
    query = set(keywords(said))
    best, best_key = None, (0, 0.0)
    for book in books:
        if book.stale or book.kind != "shell" or getattr(book, "tier", "") != "T0" or book.params:
            continue
        distinctive = set(book.keywords) - _GENERIC_PLAYBOOK_WORDS
        hits = query & distinctive
        if len(distinctive) < 2 or len(hits) < 2 or len(hits) / len(distinctive) < 0.3:
            continue
        key = (len(hits), len(hits) / len(distinctive))
        if key > best_key:
            best, best_key = book, key
    return best


def pc_fact_playbook(text):
    """The stored read-only playbook a plain question about THIS PC clearly asks for, or None (see learned_playbook)."""
    said = _CHANNEL_NOTE.sub("", text or "").casefold().strip()      # the phone app's own note must not count as words the person said
    if not _PC_FACT_QUESTION.search(said):
        return None
    return learned_playbook(said)


def _without_pc_tools_if_chat(said, tools):
    """The PC planner already judged this sentence to be talk: the reply may use the web, weather or memory, never the PC
    controls (core.gateway.judged_chat; live 2026-11-08 'I ain't saying I'm rich' -> list_windows -> 'Claude is open')."""
    try:
        from core import gateway
        from tools import registry
        if not gateway.judged_chat(said):
            return tools
        pc = {n for n, t in registry._TOOLS.items() if t["fn"].__module__ in gateway.PC_TOOL_MODULES}
        return [t for t in tools if t.get("name") not in pc]
    except Exception:  # noqa: BLE001  (never lose a turn over tool trimming)
        return tools


# His yes to what Jarvis just said, in his usual words ("go ahead", "sure", "absolutely, go for it", "yeah go ahead and do
# that", "that would be great"): none of them points at a skill; the offer they answer does (see select_tools).
_ACCEPTANCE = re.compile(
    r"\b(?:yes|yeah|yep|yup|sure|ok(?:ay)?|alright|all right|absolutely|definitely|certainly|of course|please(?: do)?|"
    r"go (?:ahead|for it|on)|do (?:it|that|so)|sounds (?:good|great|perfect)|let'?s do (?:it|that)|why not|by all means|"
    r"(?:that|it) (?:would|will|'?d) be (?:great|good|nice|perfect|lovely|helpful|awesome|fine)|"
    r"(?:that|it)'?d be (?:great|good|nice|perfect|lovely|helpful|awesome|fine)|(?:great|good|perfect) idea|"
    r"make it so|i'?d like that|that works)\b", re.I)


def select_tools(history, tools):
    """Bound local prompt size rather than sending all 170 tools on every hello.

    Retain schemas already used in this exchange for valid cross-provider history.
    Selection is lexical routing, not permission; registry checks remain authoritative.
    Small talk gets none (see small_talk).
    """
    if history and history[-1].get("role") == "user" and isinstance(history[-1].get("content"), (str, list)):
        latest = history[-1]["content"]
        fresh = isinstance(latest, str) or not any(_block(i).get("type") == "tool_result" for i in latest)
        if fresh and small_talk(last_user_text(history), "?" in _last_assistant_text(history)):
            return []
        from core import chatturn
        if fresh and chatturn.greeting(last_user_text(history)):
            return []                    # "Hey Jarvis." after a skill: a hello, never the last topic's skills again
        if fresh:
            tools = _without_pc_tools_if_chat(last_user_text(history), tools)
    text, used, theirs = [], set(), []
    for turn in history[-6:]:
        value = turn.get("content", "")
        lines = text if turn.get("role") != "user" else theirs
        if isinstance(value, str):
            lines.append(_CHANNEL_NOTE.sub("", value))
        else:
            for item in value:
                b = _block(item)
                # Not the <background>/<retrieved-memory> Jarvis attaches: thousands of words of memories made
                # nearly every skill "match" whatever was said. Nor the phone app's own channel note: its words
                # ("Jarvis phone app") offered pair_phone, read_screen and open_app with EVERY phone message, and
                # the 3B model, made to call something, paired the person 'frozen pencil' (live, 2026-10-30).
                if b.get("type") == "text" and not b["text"].lstrip().startswith(_INJECTED):
                    lines.append(_CHANNEL_NOTE.sub("", b["text"]))
                if b.get("type") == "tool_use":
                    used.add(b["name"])
    said = last_user_text(history).casefold()
    context = _words(" ".join(text + theirs))
    jarvis_asked_now = _last_assistant_text(history)
    their_named, their_earlier = _words(" ".join(theirs)), _words(" ".join(theirs), hints=False)
    # His own words point at skills; "go ahead", "sure", "that would be great" say yes to Jarvis's last words instead,
    # whether those asked ("Shall I set a reminder?") or offered ("I can try to search the web for you, sir.").
    his = _words(_ACCEPTANCE.sub(" ", said.replace("’", "'")))
    accepted = bool(_ACCEPTANCE.match(_VOCATIVE.sub("", said.replace("’", "'").strip(" \t,.!-")).strip(" ,.!-")))
    question = _words(jarvis_asked_now) if "?" in jarvis_asked_now or accepted else set()
    # The model is offered ONE rigging front door (rig_my_character); the low-level rig/generate tools stay
    # callable by code and scripts but are not choices for a 3B model to confuse with it.
    eligible = [t for t in tools if "input_schema" in t and t["name"] not in _INTERNAL_CRAFT_TOOLS]
    # A concrete cached Legend fact is not a watcher-status request. The 8B
    # model repeatedly selected the no-argument status tool for Axle speed.
    # Offer the existing source readers only for this narrow, read-only intent.
    # A bare, low-signal fragment (live examples: "frozen pencil", "velvet
    # lantern") has no lexical tool hint, question, personal lookup, or action
    # request.  Attaching a large tool list makes small local models choose a
    # plausible-but-unrelated skill.  Keep this narrow: recognised hints,
    # grounded commands, questions, and personal lookups retain their tools.
    own_first = _own_skill_tools(said, eligible)       # "Rig my character" is short but a clear request
    if own_first:
        return own_first
    if low_signal_fragment(history):
        return []
    # Exact offline research recall should use its stored citations, rather than
    # an unrelated similarly named reference tool. This adds no execution power
    # and does not change the owner's selected inference model/provider.
    if (re.search(r'\b(saved|stored|offline|local)\b',said)
        and re.search(r'\b(research|references|tutorials?|sources)\b',said)
        and not re.search(r'\b(save|delete|remove|publish|send)\b',said)):
        readers=[t for t in eligible if t['name']=='jarvis_research_sources']
        if readers:return readers
    if _PAST_TALK.search(said) and _OLDER_TALK.search(said) and just_asking(said):
        readers = [t for t in eligible if t["name"] == "recall_conversations"]
        if readers:
            return readers
    # JARVIS's own rigging skills, lessons and jobs, and "what is open on my PC", have dedicated free skills: offered
    # everything, the 3B model read the status JSON and denied having the skill, and sent window questions to the
    # paid screen-control loop (live, 2026-10-31).
    own = _own_skill_tools(said, eligible)
    if own:
        return own
    # A clear request for the live internet or YouTube gets only the web skills: offered everything, the 3B model
    # answered "look up three Blender rigging tutorials" from the local Blender notes and INVENTED three titles.
    from core import webintent
    want = webintent.kind(last_user_text(history))
    if want in ("search", "youtube", "recall"):
        web = [t for t in eligible if t["name"] in WEB_TOOLS]
        if web:
            return web
    if (re.search(r"\b(apex|legend|upgrade|hotfix|tactical|ultimate|passive)\b",said)
        and re.search(r"abilit|upgrade|hotfix|tactical|ultimate|passive|perk|cooldown|speed",said)
        and not re.search(r"\b(status|watch|monitor|refresh|start|launch(?!\s+speed\b)|download|remember|save)\b",said)):
        from core.gamecoach_meta import LEGENDS
        if any(re.search(r"\b"+re.escape(n.casefold())+r"\b",said) for n in LEGENDS):
            readers=[t for t in eligible if t['name'] in {'apex_legend_intel','apex_patch_evidence_for_legend'}]
            if readers:return readers
    rarity = {}
    for t in eligible:
        for token in {_stem(x) for x in t["name"].split("_")}:
            rarity[token] = rarity.get(token, 0) + 1

    def score(tool):
        name = tool["name"]
        parts = {_stem(x) for x in name.split("_")}
        tokens = _tool_words(name, tool.get("description", ""))
        def match(words):  # a skill's own name counts most, a rare name word more than "list" or "set"
            return sum(10 / rarity[p] ** 0.5 for p in words & parts) + len(words & tokens)
        return 1000 * (name in used) + 2 * match(his) + match(context)

    def pointed(tool):
        """His words point at it: his newest words (by name, description or everyday hint); his earlier lines here by
        the skill's name or two words of its description ("put bread on it too" after "what's on my shopping list?"
        keeps add_note); the question Jarvis just asked him (his "yes, please" answers it); or this exchange used it."""
        name = tool["name"]
        parts = {_stem(x) for x in name.split("_")}
        tokens = _tool_words(name, tool.get("description", ""))
        return (name in used or bool(his & (tokens | parts)) or bool(their_named & parts)
                or len(their_earlier & tokens) >= 2 or bool(question & (tokens | parts)))
    last = history[-1].get("content") if history else None
    mid_task = isinstance(last, list) and any(_block(i).get("type") == "tool_result" for i in last)
    if his and not mid_task:
        # A plain follow-up ("and which size?" after talk of his son's new bike) matched every skill whose description
        # shares a word with the whole chat, Jarvis's replies included: 16 unrelated skills (read_feed,
        # set_household_food, rigging_lessons...), ~2,800 tokens, more than the room left for the conversation itself
        # (measured offline 2026-11-10). Only skills his own words point at are offered now; every skill is still
        # reachable by naming it. With no words of his own ("yes", "go ahead", "that would be great") the chat still
        # decides, as before. So does a question about a reading of the PC that names no skill ("and the ping?"):
        # with no tools at all its reply would stream, unchecked, as a first answer. And so does a round after a tool
        # result (a mission's later rounds too): the next step is often named only by Jarvis's own plan.
        mine = [t for t in eligible if pointed(t)]
        if mine or not _PC_READING_Q.search(said):
            eligible = mine
    ranked = sorted(eligible, key=score, reverse=True)
    return [t for t in ranked[:16] if score(t) > 0]


# Tools a small local model tends to fire at anything (it called open_app('haha') and
# open_app('hey jarvis')). Each must be grounded in the user's own words or the call is
# rejected, which escalates the turn to the next brain tier instead of acting.
_SAVE_CUES = re.compile(r"\b(remember|note|save|keep|store|don'?t forget|write (?:this|that|it) down|jot|record|"
                        r"my [\w']+(?: [\w']+)? (?:is|are|was|were)|i (?:am|was|have|live|work|like|love|hate|prefer|don'?t|"
                        r"can'?t|never|always|usually)|i'?m|we (?:are|have|live|like)|we'?re)\b", re.I)
# Found with llama3.2:3b, the laptop's model: "I approve the $1 limit. Go." set qBittorrent's limits to unlimited,
# "Open blender and delete a cube" ran the media drive's delete (rm -rf) on "cube", "What's my wife's name?" read
# the screen. Each needs its own subject in their words.
_TORRENTS = re.compile(r"\b(torrents?|qbit\w*|q ?bittorrent|magnet|seed\w*|leech\w*|downloads?|downloading|uploads?|"
                       r"uploading|bandwidth|speed limits?)\b", re.I)
_MEDIA_DRIVE = re.compile(r"\b(media|drive|nas|homelab|server|pi|files?|folders?|movies?|films?|shows?|series|seasons?|"
                          r"episodes?|anime|downloads?|photos?|videos?|trailers?|samples?|jellyfin|plex|library)\b|"
                          r"\.\w{2,4}\b", re.I)
_ON_SCREEN = re.compile(r"\b(screens?|screenshot|monitors?|display|window|desktop|pc|computer|see|seeing|look(?:ing)? at|"
                        r"(?:take|have) a look|read|showing|says?|error|message|pop-?up|dialog|menu|button|tab|page|this|"
                        r"here)\b", re.I)     # not a bare "looking": the fragment "Looking on it." took a screenshot
# Words that ask for something from the live internet, or a question the web can answer. ("Google this", "look it
# up", "find the latest", "what's the newest driver" - not 'frozen pencil', which the 3B model would happily search.)
_WANTS_WEB = re.compile(r"\b(google|bing|search|look(?:ing)? (?:\w+ )?up|look for|find|latest|newest|current|recent|news|"
                        r"online|internet|web|browse|research|check|compare|price|cost|download|version|release|update|"
                        r"what(?:'?s| is| are| was| were)|who(?:'?s| is| was)|how (?:do|to|does|much|many|can|long|old)|"
                        r"when|where|which|why|is there|are there|tell me about|anything (?:on|about))\b", re.I)
_GROUNDING = {
    # A website talk ("our branding or our model will be insurance you can trust") started a 3D character job (live 2026-11-15, by voice):
    # making a character needs his words to ask for one, never just the word "model".
    "make_character_from_description": re.compile(
        r"\b(?:make|create|build|generate|design|draw|rig|sculpt|model)\b[^.?!]{0,60}\b(?:character|creature|dragon|humanoid|avatar|monster|mascot|"
        r"figure|robot|animal|npc|hero|villain|3d|rigged)\b", re.I),
    "open_app": re.compile(r"\b(open|launch|start|run|fire\s+up|pull\s+up|bring\s+up|load|boot)\b", re.I),
    # It planned a Costco/Aldi trip for "I want to repaint the garage door this weekend".
    "shopping_route": re.compile(r"\b(shop\w*|stores?|grocer\w*|errands?|route|trip|drive|costco|aldi|commissary|"
                                 r"walmart|target|market|pick\s+up|buy)\b", re.I),
    # "Where does my sister live?" (a remembered fact) was answered with the driving distance to Miami: directions
    # are for getting somewhere, so the words must be about distance, travel or the way there.
    "directions": re.compile(r"\b(far|distance|drive|driving|driv|route|navigat\w*|directions?|get(?:ting)? to|"
                             r"how long|miles?|minutes?|hours?|walk(?:ing)?|transit|bus|train|bike|cycling|map|way to|"
                             r"take me|go(?:ing)? to|head(?:ing)? to|from here|from home|trip|commute|traffic|eta)\b",
                             re.I),
    # "We're thinking of driving down to Miami... don't book anything yet" got a trip plan with invented dates: a trip
    # is planned when they ask for one to be planned, booked or set up.
    "plan_trip": re.compile(r"\b(plan|book|schedule|set\s*up|itinerary|organi[sz]e|arrange|put together|start (?:a|the) trip|"
                            r"new trip|trip plan|create)\b", re.I),
    "trip_add": re.compile(r"\b(trip|itinerary|travel plan|vacation|holiday)\b", re.I),
    # "What's my wife's name?" switched the conversation to Maya: switching needs someone handing over.
    "switch_person": re.compile(r"\b(this is|it'?s me|i'?m back|here|wants? to (?:ask|talk|say|speak|tell)|my turn|"
                                r"switch|speaking|talking now|hand(?:ing)? (?:you )?(?:over|off)|change (?:user|person))\b", re.I),
    # Live 2026-10-30: "Please make corrections too." locked speech recognition to Haitian Creole, a bare phrase sent
    # from the phone app locked it to Kreyòl, and "we have no request to stop." / "Juris." started interpreter mode
    # (English <-> "Haitian Creol"). A language switch is asked for with a speaking/switching word, and the language
    # itself must be one they named (see _unheard_target).
    "set_conversation_language": re.compile(r"\b(speak\w*|spoke|talk\w*|parl\w*|pale|switch\w*|chang\w*|use|lock\w*|set|"
                                            r"go back|back to|only|from now on|understand\w*|recogni[sz]\w*|listen\w*|"
                                            r"hear\w*|mishear\w*|detect\w*|auto\w*|translat\w*|interpret\w*)\b", re.I),
    "start_interpreter": re.compile(r"\b(interpret\w*|translat\w*|tradui\w*|tradwi)\b", re.I),
    # Pairing hands out a code that links a phone to someone's data: only when they ask for a pairing, a code or a
    # phone/app/device to be linked ('frozen pencil' and 'mesye' were offered as the person, see _unheard_target).
    "pair_phone": re.compile(r"\bpair\w*\b|\bcode\b|\b(?:link|connect|set ?up|hook ?up|register|add|enrol+)\b.*"
                             r"\b(?:phone|app|device|telegram|tablet)\b", re.I),
    # A lesson is read before every later task: only when they corrected or complained ("What's the weather like?"
    # once saved "When the user says 'what's the weather like', just call get_weather").
    "learn_lesson": re.compile(r"\b(don'?t|do not|never|always|stop|wrong|mistake|instead|next time|from now on|"
                               r"should(?:n'?t)?|you keep|not like that|not what i|remember (?:to|that)|learn|lesson|"
                               r"annoy\w*|that'?s not|why did you)\b", re.I),
    "forget": re.compile(r"\b(forget|delete|erase|remove|wipe|clear|wrong|outdated|not true|no longer|not anymore)\b", re.I),
    # A taught command is only ever learned from a sentence that teaches one ("when I say X, say Y"), and only
    # dropped by one that asks for that: never from a fact, a reminder or a passing mention of the trigger.
    "learn_command": re.compile(r"\b(?:when(?:ever)?|if|every time|each time|any ?time) i (?:say|tell you|type|ask)\b", re.I),
    "forget_command": re.compile(r"\b(?:forget|delete|remove|drop|disable|un-?learn|stop (?:respond|repl|answer|say|react)\w*)\b", re.I),
    "forget_today": re.compile(r"\b(forget|delete|erase|remove|wipe|clear|scrub)\b", re.I),
    "close_app": re.compile(r"\b(close|quit|exit|kill|shut|end|stop)\b", re.I),
    "activate_eyes": re.compile(r"\b(eyes?|camera|webcam|look|see|watch)\b", re.I),
    "show_lesson_page": re.compile(r"\b(show|open|display|lesson|page|explainer|teach|quiz|diagram|visual|screen)\b", re.I),
    # Saving only with an intent to save: the fragment "issue a new one." was stored as a memory fact word for word,
    # "codecs to go ahead and create a very good" became a lookbook's title.
    "remember": _SAVE_CUES,
    "remember_note": _SAVE_CUES,
    "library_note": re.compile(r"\b(note|save|library|write (?:this|that|it) down|keep|record|remember)\b", re.I),
    "add_note": re.compile(r"\b(add|put|list|buy|need|get|grab|pick up|to-?do|remind|note|idea|shopping|out of)\b", re.I),
    "create_lookbook": re.compile(r"\b(lookbook|look book|outfits?|looks|style|collection|board)\b", re.I),
    # "We're out of milk" was logged as an expense: money has to be in what they said.
    "log_expense": re.compile(r"\$|\d|\b(dollars?|bucks|cents?|spent|spend|paid|pay|cost|costs|bought|expense|bill|"
                              r"charged?|price|receipt|purchase[ds]?)\b", re.I),
    # Logs only when they tell what happened ("Don't Batter Me" was logged as a food: batter).
    "log_food": re.compile(r"\b(ate|eaten|eat|eating|had|having|drank|drink|drinking|log|breakfast|lunch|dinner|"
                           r"snack|meal|calories|kcal|protein|serving)\b", re.I),
    "log_workout": re.compile(r"\b(did|done|ran|run|running|walked|lifted|workout|worked out|exercise|reps?|sets?|"
                              r"miles?|km|push-?ups?|pull-?ups?|squats?|bench|deadlift|log|trained|training|gym|plank)\b", re.I),
    "log_checkin": re.compile(r"\b(feel|feeling|felt|mood|energy|slept|sleep|sore|tired|stressed|motivated|check-?in|"
                              r"log|rate|out of (?:ten|10|five|5))\b", re.I),
    "log_price": re.compile(r"\$|\d|\b(price|prices|cost|costs|dollars?|cents?|on sale|deal|cheaper|log)\b", re.I),
    "log_outfit": re.compile(r"\b(wore|wearing|wear|outfit|dressed|log)\b", re.I),
    "log_aft_test": re.compile(r"\b(aft|test|score|scored|deadlift|plank|push-?ups?|sprint|drag|carry|run|time|points?)\b", re.I),
    # The camera only when they want to be seen ("Looking on it." took a camera picture).
    "look_through_camera": re.compile(r"\b(look|see|camera|webcam|watch|behind me|in front of me|holding|wearing|"
                                      r"how do i look|check (?:this|me|it) out)\b", re.I),
    "what_am_i_holding": re.compile(r"\b(holding|in my hand|what is this|what'?s this|this (?:chip|part|thing|board|"
                                    r"component)|identify)\b", re.I),
    # "You can go to sleep for now" (to Jarvis) put the PC to sleep: the PC, or a power word that only means the PC.
    "power": re.compile(r"\b(pc|computer|desktop|machine|laptop|system|shut ?down|restart|reboot|log ?off|log ?out|"
                        r"sign ?out|lock (?:it|the|my|up)|hibernate)\b", re.I),
    "qbit_add": _TORRENTS,
    "qbit_control": _TORRENTS,
    "qbit_speed_limit": _TORRENTS,
    "delete_item": _MEDIA_DRIVE,
    "move_item": _MEDIA_DRIVE,
    "read_screen": _ON_SCREEN,
    "look_at_screen": _ON_SCREEN,
    "see_pc_screen": _ON_SCREEN,
    "read_clipboard": re.compile(r"\b(clipboard|copied|copy|paste|pasted)\b", re.I),
    # A live internet search or YouTube discovery costs a free-provider call and takes seconds: only for something
    # asked ("google this", "look it up", "find the latest", a question), never for a bare phrase ('frozen pencil').
    "search_internet": _WANTS_WEB,
    "search_web_free": _WANTS_WEB,
    "jarvis_web_search": _WANTS_WEB,
    "research_online": _WANTS_WEB,
    "find_youtube_tutorials": re.compile(r"\b(youtube|tutorials?|videos?|learn|study|teach|how to|find|look|search|recommend|suggest|show me|good|best)\b", re.I),
    "jarvis_learn_youtube": re.compile(r"\b(learn|study|teach|tutorials?|youtube|lesson|course)\b", re.I),
    "save_research": re.compile(r"\b(learn|save|keep|remember|library|store|note|study)\b", re.I),
}

# The local Llama's template ORDERS a function call whenever tools are attached, so a question it should simply answer
# got one anyway (measured on 25 everyday questions: 6 misfires, e.g. "Where am I stationed?" -> set_military_profile,
# "What should I name my new project?" -> start_project). It's therefore always offered this way out too (offered
# last: fewer misfires than first), and picking it means "answer in words": the turn is asked again with no tools.
ANSWER = {"type": "function", "function": {"name": "answer_directly", "description":
          "No tool needed: answer them yourself, in words, from what you know, this conversation or your notes "
          "(names, dates, facts, explanations, advice, opinions, ideas), or ask what they mean when what they said "
          "is unclear or unfinished. Choose this whenever none of the other functions clearly does what they asked for.",
          "parameters": {"type": "object", "properties": {}}}}

# Skills that DO something (change data, settings or what's on screen) rather than look something up. On a plain
# question (just_asking) none of them runs: "Where am I stationed?" must not rewrite the military profile.
# Advice and analysis (style_outfit, review_clip, use_up_food...) stay allowed: "How was my form in that clip?" needs one.
_ACTS = re.compile(r"^(?:set|add|log|start|create|save|update|remove|delete|cancel|stop|switch|forget|remember|learn|"
                   r"make|move|install|uninstall|open|close|restart|repair|enroll|pair|activate|deactivate|show|take|try|"
                   r"write|undo|watch|wake|build|plan_trip|assign|clip|fetch|trip_add|qbit_add|qbit_control|qbit_speed|"
                   r"metube|public_domain_download|jellyfin_rescan|garmin_sync|media_control|volume|power|focus_mode|"
                   r"voice_lock|control|pc_command|blender_create|blender_project|project_log|tutor_(?:add|grade|log|set)|"
                   r"library_note|military_benefits_page|companion)")
_ASKS = re.compile(r"^\W*(?:(?:hey|ok|okay|so|um|uh|jarvis|"
                   r"and|but|oh|well|also|then|alright|now|plus|or)\W+)*(?:what|what's|whats|where|where's|when|who|who's|"
                   r"whose|why|how|which|is|are|was|were|do|does|did|can i|should i|could i|would it|explain|describe|"
                   r"define|tell me (?:about|what|why|how|who|where|when|if)|spell|summari[sz]e|remind me (?:what|who|"
                   r"when|where|how)|any idea|i wonder|i'?m curious)\b", re.I)
# Requests worded as questions: "what about some music?", "can I get the lights on?", "any chance you could...".
_REQUEST = re.compile(r"\b((?<!what )(?<!how )(?<!who )(?<!where )(?<!which )(?:can|could|would|will) you|please|"
                      r"i want you to|i need you to|go ahead|let'?s|why don'?t you|how about|what about|what if (?:you|we)|"
                      r"why not|any chance|(?:can|could|may) i (?:get|have|see|hear)|shall we|mind if|"
                      r"are you able to|is it possible (?:for you )?to|do you (?:mind|think you)|would you mind|"
                      r"make sure|do it|you should)\b", re.I)


# Asking to be TOLD, even when worded as a request: "can you tell me how to turn on pc control" once ran a PC-control
# task, and so did "the cloud budget, how do I do that".
_TELL_ME = re.compile(r"\b(?:can|could|would|will) you (?:please )?(?:tell|explain|remind)(?: (?:me|us))? (?:how|what|why|"
                      r"where|when|who|which|if|whether|about)\b|\bdo you know (?:how|what|why|where|when|who|which|if)\b|"
                      r"\bhow (?:do|can|should|would) (?:i|we)\b", re.I)


def just_asking(said):
    """An information question ("what's my wife's name?", "explain how a heat pump works", "can you tell me why..."),
    not a request to act ("can you open Spotify?", "please add milk", "what about some music?")."""
    if not said:
        return False
    if _TELL_ME.search(said):
        return True
    # "Okay, thanks. Remind me what we were just doing?" is a question in its last sentence: judged by that one
    # (the whole-string match let set_reminder run on it). The first clause is still tried for one-sentence lines.
    sentences = [s for s in _SENTENCE.split(said.strip()) if s.strip()]
    judged = sentences[-1] if len(sentences) > 1 else said
    return bool(_ASKS.match(judged)) and not _REQUEST.search(judged)


# Answering in words, the model INVENTED personal facts it had never been told ("Where did I park?" -> "near the
# commissary", "What's my Wi-Fi password?" -> a made-up password, "Did I log my workout today?" -> "You did"). So a
# question about them is looked up first: only read-only lookups are offered (memory and conversation search always
# among them) and it must use one, then it answers from what came back. Camera/screen captures aren't lookups.
_PERSONAL = re.compile(r"\b(my|mine|our|ours|us|me)\b|\b(?:did|have|am|was|were|will|do) (?:i|we)\b(?<!how do i)"
                       r"(?<!how do we)|\bare we\b|\bthe (camera|lights?|pc|computer|tv|house|car|thermostat|garage|wi-?fi|router|"
                       r"server|homelab|drive|budget)\b", re.I)
_CAPTURE = {"look_through_camera", "what_am_i_holding", "look_at_screen", "see_pc_screen", "read_screen", "read_clipboard"}
# ...except when the question IS about what's in view: "What's behind me?" got "I can see Claude open on the right
# monitor" with no camera frame at all. Those may look (the camera/screen captures are then offered too).
_IN_VIEW = re.compile(r"\b(?:see|look(?:ing)?|behind me|in front of me|holding|wearing|how do i look|in my hand|"
                      r"on (?:my|the) (?:screen|monitor|desk))\b", re.I)
# recall = the long-term FACTS ("His wife is Maya"); recall_memories = the vault of recordings and photos. With only
# the vault offered, "What's my wife's name?" searched it for "Maya" (the answer, from its own notes) and gave up.
_ALWAYS_LOOKUP = ("recall", "recall_memories", "recall_conversations")
_FROM_NOTES = ("(Answer from your notes above if they have it. If they don't, say you don't know and offer to check. "
               "Never make it up.)")
_DID_NOTHING = "(Nothing was done for this. Don't say it was; if they want something done, ask exactly what.)"
# Only when even a lookup couldn't be made. Kept out of every other request: a longer note made small talk ramble.
_HONEST = ("(It's about them and nothing was looked up: if the answer isn't in your notes or this conversation, say "
           "you don't know and offer to check. Never make it up.)")
# Answering in words, a reply was sometimes only its opener: "Checking your plan, sir." for "Go ahead with both" (8B),
# "One sec, sir. Checking your plan." for "Don't Batter Me" (3B). Asked once more with this, before the slow tiers.
_SAY_IT = "(Nothing will be looked up or checked for this. Answer now, or ask what they mean.)"


# "debrief me" got a whole invented gaming session ("You struggled with your aim, and your team didn't quite gel")
# with no tool run: a request for a report on them is a question about them, worded as an order.
_REPORT_REQUEST = re.compile(r"^\W*(?:(?:hey|ok|okay|so|um|uh)\W+)?(?:jarvis\W+)?(?:(?:de)?brief me|catch me up|update me|"
                             r"fill me in|bring me up to (?:speed|date)|give me (?:a |an |the |my )?(?:status|update|"
                             r"summary|rundown|recap|report|debrief|numbers)|status (?:on|of|update|report)|"
                             r"(?:any|what'?s the) (?:news|update|status|progress) on|summari[sz]e my|recap my|"
                             r"how (?:am i|are we) doing (?:on|with)|where (?:am i|are we) (?:at|on|with))\b", re.I)


_DENIES = re.compile(r"\b(?:don'?t|do not|didn'?t|can'?t|cannot) (?:know|have|find|see|recall|remember)|\bnot sure\b|"
                     r"\bno (?:record|information|idea|note)|\bcouldn'?t find\b|\bisn'?t (?:stored|in my notes|something i)|"
                     r"\bnot (?:stored|in my notes|on record)\b", re.I)


def _denies(reply):
    """A worded reply that says it doesn't know."""
    return bool(_DENIES.search(" ".join(b.text for b in reply.content if b.type == "text")))


def _notes_answer(history, said):
    """The notes attached to the question are about what it asks (they share a content word)."""
    notes = retrieved_notes(history)
    return bool(notes) and _shares(said, {w for w in _words(notes, hints=False) if len(w) >= 3})


def retrieved_notes(history):
    """The <retrieved-memory> block the brain attached to the newest question, when it found something."""
    for turn in reversed(history or []):
        if turn.get("role") != "user":
            continue
        value = turn.get("content", "")
        for item in [] if isinstance(value, str) else value:
            text = _block(item).get("text", "") if _block(item).get("type") == "text" else ""
            if text.lstrip().startswith("<retrieved-memory>"):
                body = re.sub(r"</?retrieved-memory>|Historical context only[^\n]*", "", text).strip()
                return body if body and not body.startswith("Nothing remembered") else ""
        return ""
    return ""


def about_them(said):
    """A question about them or their things ("where did I park?", "what's my rank?", "is the camera on?"), not general
    knowledge ("how do I tie a tie?"). A request for a report on them ("debrief me") counts too."""
    return bool(said) and ((just_asking(said) and bool(_PERSONAL.search(said))) or bool(_REPORT_REQUEST.match(said)))


# "What were we just talking about?" counts as about them ("were we"), so it was looked up: recall_memories found
# nothing in the vault and Jarvis said "we didn't have a previous conversation" with the conversation right in
# front of it. The answer to a question about THIS conversation is the messages above (and the <daily-awareness>
# transcript the brain attaches); only older talk ("yesterday", "last week") is the journal's.
_THIS_CHAT_Q = re.compile(r"\b(?:what|who|which)\s+(?:were|was|did|have|are|am)\s+(?:we|i|you)\s+(?:just\s+|all\s+)?"
                          r"(?:talk|say|said|discuss|chat|ask|tell|mention|decid|agree)|"
                          r"\bwhat did (?:i|you) (?:just )?(?:say|ask|tell)|\b(?:repeat|say) (?:that|it|what you (?:just )?said)"
                          r"( again)?\b|\bwhat was that\b|\bcome again\b|\bwhat were we (?:on|doing)\b|"
                          r"\bwhat we were (?:just )?(?:doing|talking|saying|discussing|on)\b", re.I)
_OLDER_TALK = re.compile(r"\b(yesterday|last (?:night|week|month|year|time)|days? ago|weeks? ago|months? ago|"
                         r"on (?:mon|tues|wednes|thurs|fri|satur|sun)day|the other day|before today)\b", re.I)
_THIS_CHAT = "(They mean this very conversation: answer from the messages above, in a sentence or two.)"


def about_this_chat(said):
    """A question about what was said in this conversation, which needs no lookup."""
    return bool(said) and bool(_THIS_CHAT_Q.search(said)) and not _OLDER_TALK.search(said)


# Made-up values in an action (measured on the owner's own past requests): a $5.99 price never said ("...get the big
# one in sam's club" -> log_price), torrent limits of 1000 for "I approve the $1 limit", "He dislikes lactose" saved for
# "Don't batter me", a random YouTube video for "YouTube.". Such a value must come from what they said or what a tool
# returned (never from the model's own words).
_AMOUNT_KEYS = re.compile(r"price|amount|cost|total|kbs|budget|limit|usd|dollars", re.I)
# Skills that SAVE what the model writes into their data: that text must share a word with what they said or a tool
# returned ("Don't batter me" logged a chicken breast; "Go ahead with both" moved /home/user/old_folder).
_WRITES = re.compile(r"^(?:remember|learn_lesson|learn_command|forget_command|library_note|add_|log_|save_|trip_add|plan_trip|start_|project_log|"
                     r"create_lookbook|tutor_add|move_item|set_reminder|set_(?:my_details|military_profile|household_food|"
                     r"fitness_profile|budget|training_plan|benefit_status|project_status))")
_SPECIFIC_URL = re.compile(r"^(?:https?://)?(?:www\.)?([a-z0-9-]+)\.[a-z.]+(/\S*[\w?=&#-]\S*)", re.I)


def heard_in(history):
    """What the user said and the tools returned in this conversation (not the model's own words, nor the
    background notes: a fact copied from those isn't something they asked to save)."""
    out = []
    for turn in history[-12:]:
        value = turn.get("content", "")
        for item in [value] if isinstance(value, str) else value:
            b = {"type": "text", "text": item} if isinstance(item, str) else _block(item)
            if turn.get("role") == "user" and b.get("type") == "text" and not b.get("text", "").lstrip().startswith(_INJECTED):
                out.append(b["text"])
            elif b.get("type") == "tool_result":
                c = b.get("content")
                out.append(c if isinstance(c, str) else json.dumps(c))
    return "\n".join(out)


def _leaves(value, key=""):
    if isinstance(value, dict):
        for k, v in value.items():
            yield from _leaves(v, k)
    elif isinstance(value, list):
        for v in value:
            yield from _leaves(v, key)
    else:
        yield key, value


def _free_text(name, args, skip=()):
    """The string arguments the model wrote itself (not ones picked from a fixed list of choices, nor `skip`)."""
    from tools import registry
    props = next((s["input_schema"]["properties"] for s in registry.schemas() if s["name"] == name), {})
    out = []
    for key, value in args.items():
        if key in skip:
            continue
        if isinstance(value, str) and "enum" not in props.get(key, {}):
            out.append(value)
        elif isinstance(value, (dict, list)):
            out += [leaf for _, leaf in _leaves(value) if isinstance(leaf, str)]
    return out


def _invented(name, args, heard, act=True):
    """The first argument that wasn't said or shown: a link nobody gave (any skill); for actions an amount; for skills
    that save their text, text sharing no word with the conversation. None when everything checks out."""
    low = heard.lower()
    for key, value in _leaves(args):
        if act and isinstance(value, (int, float)) and not isinstance(value, bool) and _AMOUNT_KEYS.search(key) \
                and value not in (0, 1):
            number = f"{value:g}"
            if not re.search(rf"(?<![\d.]){re.escape(number)}(?![\d])", low.replace(",", "")):
                return key
        if isinstance(value, str) and value.strip():
            v = value.strip()
            if _SPECIFIC_URL.match(v) and v.lower().rstrip("/") not in low:
                return key                            # a particular page or video nobody gave (a bare site is fine)
            if "url" in key.lower() and not re.match(r"(?:https?://|www\.)", v, re.I) and v.lower() not in low:
                return key                            # "the link to the movie on your PC" as a URL
    if act and _WRITES.match(name):
        # (A language argument is judged by _unheard_target, by its code: "Kreyol" said, "Haitian Creole" written.)
        words = {w for t in _free_text(name, args, skip=_LANGUAGE_ARGS.get(name, ())) for w in _words(t, hints=False)
                 if len(w) >= 4}
        squashed = re.sub(r"[^a-z0-9]", "", low)
        if words and not (words & _words(heard, hints=False) or any(w in squashed for w in words)):
            return "text"
    return None


# The machinery leaking into speech: "I'll keep my answers plain, without using function calls.", "Since the question
# doesn't require any tool calls, I'll use the answer_directly...". Such a sentence is never spoken.
_META = re.compile(r"function calls?|tool calls?|answer_directly|no tool (?:is )?(?:needed|required)|"
                   r"<background>|retrieved[- ]memory|daily[- ]awareness|the (?:user|owner)'?s? (?:question|request)", re.I)


# The "How that sounds (tone only)" lines of core/personality.md are examples the 9B copies when it has nothing safe to say: "Can you make me a
# humanoid in Blender?" was answered "Running smoothly, sir." on both machines (live 2026-11-15, by voice). An example reply said to a line that
# is nothing like the example's own prompt is a parrot: never said.
_EXAMPLE = re.compile(r'^\s*-\s*"([^"]+)"\s*->\s*"([^"]+)"\s*$', re.M)
_EXAMPLE_PAIRS = []
# The manual's old tone examples (removed from core/personality.md 2026-11-21, wave 3b: the manual now carries the character sheet). The 9B
# learned them by heart over weeks of conversations in its context, so a copy is still caught after the file no longer has them.
_FORMER_EXAMPLES = [("How are you doing?", "Running smoothly, {title}."),
                    ("Why are you here?", "To keep your PC, your plans and your day in order, {title}. Someone has to."),
                    ("You missed closing the window again.", "So I did, {title}. Shall I have another go at closing it?"),
                    ("Thanks, Jarvis.", "Any time, {title}.")]
_PLAIN_WORDS = frozenset("you are the a an i is to my me it and of do can your we he she they this that what how why when where who".split())


def _example_pairs():
    if not _EXAMPLE_PAIRS:
        try:
            from pathlib import Path
            text = (Path(__file__).resolve().parent / "personality.md").read_text(encoding="utf-8", errors="replace")
            found = _EXAMPLE.findall(text)
        except OSError:
            found = []
        for prompt, reply in found + _FORMER_EXAMPLES:
            _EXAMPLE_PAIRS.append((prompt, re.sub(r"[^a-z ]", "", reply.replace("{title}", "").casefold()).strip()))
        if not _EXAMPLE_PAIRS:
            _EXAMPLE_PAIRS.append(("", ""))                                  # nothing to compare: remembered, so the file is read once
    return _EXAMPLE_PAIRS


def parrots_example(sentence, said=""):
    """The sentence is exactly the reply of a personality.md example, said to a line that does not resemble that example's prompt."""
    plain = re.sub(r"\s+", " ", re.sub(r"[^a-z ]", "", (sentence or "").replace("’", "'").replace("{title}", "").casefold())).strip()
    plain = re.sub(r"\b(?:sir|boss|madam|ma am)$", "", plain).strip()
    if not plain:
        return False
    heard = [w for w in re.findall(r"[a-z']+", (said or "").casefold())]
    for prompt, reply in _example_pairs():
        reply = re.sub(r"\b(?:sir|boss|madam|ma am)$", "", reply).strip()
        if not reply or plain != reply:
            continue
        if len(heard) <= 4:
            return False                                                       # a short line may well be the example's own question
        theirs = {w for w in re.findall(r"[a-z']+", prompt.casefold()) if w not in _PLAIN_WORDS}
        return not (theirs & {w for w in heard if w not in _PLAIN_WORDS})      # no word of the example's prompt in his line: a parrot
    return False


def is_meta(sentence):
    """A sentence about how Jarvis works inside (tools, function calls, its notes' tags) rather than to them."""
    return bool(_META.search(sentence))


# The personality asks for a short opener in the same turn as a tool call ("Checking your plan.", "One sec, sir.").
# A reply that looks nothing up opens with one anyway (llama3.2 almost always): "How do I get a stain out?" -> "One
# sec, sir. Checking your plan. You'll need...", and "Where did I park?" got only "One sec, sir. Checking your plan."
# Such an opening is dropped from a reply that can't look anything up.
_LEAD_IN = re.compile(
    r"\W*(?:(?:ok(?:ay)?|alright|all right|sure|right|yes),?\s+)?"
    r"(?:(?:just |give me |gimme )?(?:a|one) (?:sec(?:ond)?|moment|minute|mo)|hold on|hang on|bear with me|on it|"
    r"working on it|coming (?:right )?up|right away|"
    r"let me (?:check|look|see|pull|find|grab|get|think)(?: (?:that|it|this|on that|into (?:it|that)))?|"
    r"(?:checking|pulling|looking|grabbing|fetching|getting|bringing|finding|loading|searching) "
    r"(?:that|it|this|those|these|your|the|my|on|into|up|for)(?: [\w']+){0,2})"
    r"(?: (?:up|out|for you|now|real quick|quickly|first))*(?:,\s*[\w']+|\s+(?:sir|ma'?am|boss|chief))?\W*", re.I)
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def is_lead_in(sentence):
    """A sentence that only announces a lookup ("One sec, sir.", "Checking your plan.") and says nothing else."""
    return bool(_LEAD_IN.fullmatch(sentence.strip()))


def _opening(sentences, unsaid):
    """How many of the first sentences are such openers (or would be dropped anyway)."""
    n = 0
    while n < len(sentences) and (is_lead_in(sentences[n]) or unsaid(sentences[n])):
        n += 1
    return n


# The cloud tier called them "monsieur"/"mesye" in English replies ("The capital of France is Paris, monsieur."), 1 in
# 9 even with the personality spelling it out, and the local 8B said "De rien, mesye" in French. A sentence that
# calls them by a title takes the one of its own language: the language its voice is chosen by.
def in_their_language(sentence, whole=""):
    from audio.language import detect, scores
    from core import config, users
    names = config.titles(users.current())
    called = re.compile(r"(^\s*|,\s*)(" + "|".join(map(re.escape, names.values())) + r")(?=\s*(?:[,.!?;:…]|$))", re.I)
    if not called.search(sentence):
        return sentence
    plain = called.sub(r"\1", sentence)                          # judged without the title itself
    ranked = sorted(scores(plain).values(), reverse=True) + [0.0, 0.0]
    if ranked[0] < 0.15 or ranked[0] - ranked[1] < 0.15:
        # too little or too close to tell from the sentence alone: the whole reply decides (live 2026-11-10 on the
        # phone: "I'm holding steady, mesye. Ready for whatever you need next." - en 0.25 vs ht 0.25 - kept the Kreyol
        # title in an English reply)
        plain = called.sub(r"\1", whole) if whole else plain
        if max(scores(plain).values()) < 0.15:
            return sentence                                       # still too little ("Paris, mesye."): as it is
    right = names.get(detect(plain))
    if not right:
        return sentence                                           # another language (a lesson): left as it is

    def swap(m):
        return m.group(1) + (right[:1].upper() + right[1:] if m.group(2)[:1].isupper() else right)
    return called.sub(swap, sentence)


def _titled(text):
    return "".join(in_their_language(part, text) for part in re.split(r"((?<=[.!?])\s+)", text))


_URL = re.compile(r"https?://[^\s<>()\"'\]]+", re.I)


def _unsourced_urls(reply, evidence):
    """Links in the reply that the evidence never gave (a URL is a claim too; the model may not make one up)."""
    def plain(url):
        return url.rstrip(".,;:!?)").rstrip("/").lower().replace("http://", "https://").replace("://www.", "://")
    given = {plain(u) for u in _URL.findall(evidence or "")}
    return [u for u in _URL.findall(reply or "") if plain(u) not in given]


_FIGURE = re.compile(r"(?<![\w.])(?:[A-Za-z]{1,2}\d{3,}|\d+\.\d+(?:\.\d+)*|\d{3,})(?!\w)")


def _unsourced_numbers(reply, evidence):
    """Figures in the reply (versions, three-digit-plus numbers) that the evidence never gave: a number is a claim too
    (live, laptop: "version R537" for a page the evidence showed as 621.01)."""
    given = set(re.findall(r"\d+(?:\.\d+)*", evidence or ""))
    out = []
    for token in _FIGURE.findall(reply or ""):
        digits = re.sub(r"^[A-Za-z]+", "", token)
        if digits not in given and not all(part in given for part in digits.split(".")):
            out.append(token)
    return out


_GENERIC_WEB = {_w for _w in """headlines headline today news latest sources source pulled google found stories story reports report results result
    search searched article articles online here there their about these those which where would could should think seems appears showing shows
    including various several updates update details detail items item sites site pages page links link top major main world""".split()}


def _evidence_content(evidence):
    """What a live-search result actually SAYS: the words of its titles and of its snippets, the figures in both, and the names of the
    sites that carried it (a site's name is not what it reported)."""
    titles, body, hosts, text = set(), set(), set(), []
    for line in (evidence or "").splitlines():
        stripped = line.strip()
        m = re.match(r"\d+\.\s+(.*)$", stripped)
        if m:
            title, sep, site = m.group(1).rpartition(" \u2014 ")
            if sep:
                hosts |= {_stem(w) for w in re.findall(r"[a-z]{3,}", site.split("(")[0].lower())}
            else:
                title = m.group(1)
            titles |= {_stem(w) for w in re.findall(r"[a-z]{4,}", title.lower())}
            text.append(title)
        elif stripped.startswith("URL:"):
            from urllib.parse import urlsplit
            try:
                hosts |= {_stem(w) for w in re.findall(r"[a-z]{3,}", (urlsplit(stripped[4:].strip()).hostname or "").lower())}
            except ValueError:
                pass
        elif stripped.startswith(('"', "States:")):
            body |= {_stem(w) for w in re.findall(r"[a-z]{4,}", stripped.lower())}
            text.append(stripped)
    return titles, body, hosts, set(re.findall(r"\d[\d,.]*\d", " ".join(text)))


# The honest caveats the evidence block tells the model to give (core/webanswer.verdict): they state no result, and they are the right answer.
_CAVEAT = re.compile(
    r"\b(?:nothing numeric|conflict(?:s|ing)?|disagree\w*|out of date|outdated|unconfirmed|nothing reliable|no reliable|not (?:a )?(?:reliable|confirmed)|won'?t guess|"
    r"only one source|one source only|no second source|only \S+ gives that figure|close but not the same|can'?t compare|different units|"
    r"(?:can'?t|cannot|can not|couldn'?t|could not|unable to) (?:confirm|tell you|compare|find (?:a |an |any |anything |something )?(?:reliable|current)|say)|"
    r"(?:don'?t|do not) have a reliable|results? (?:i found )?(?:are|were|is) (?:all )?old|(?:are|were) (?:all )?old\b)", re.I)


def _web_content_grounded(reply, evidence):
    """Does a reply to live-search evidence carry CONTENT from it: at least two distinct content words (not site names, not words
    like headlines / today / news / sources / pulled) that the titles or snippets use, or a figure copied from them? A reply that only
    names the sources ("I pulled up today's top headlines from Google News, AP, NPR...") states nothing and is not an answer."""
    titles, body, hosts, figs = _evidence_content(evidence)
    heard = (reply or "").replace(chr(8217), "'")
    caveats = [p for p in re.split(r"(?<=[.!?])"+chr(92)+"s+", heard) if _CAVEAT.search(p)]
    groups = {g for g in re.findall(r"\d[\d,.]*\d(?:\s?[-\u2013]\s?\d[\d,.]*\d)?", heard) if set(re.findall(r"\d[\d,.]*\d", g)) & figs}
    if caveats and len(groups) < 2 and not all(_caveat_clean(p, evidence) for p in caveats):
        return False                                          # a caveat that also states a claim ("I can't confirm it, but the Lakers won") is not a caveat
    used = set()
    for w in re.findall(r"[a-z]{4,}", (reply or "").lower().replace("'", "")):
        s = _stem(w)
        if w in _STOP or w in _GENERIC_WEB or s in _GENERIC_WEB:
            continue
        if (s in body) or (s in titles and s not in hosts):         # a site's name only counts when a snippet itself says it
            used.add(s)
    if len(used) >= 2 or bool(set(re.findall(r"\d[\d,.]*\d", reply or "")) & figs):
        return True
    return _honest_caveat(reply, evidence)


_ANNOUNCES = re.compile(r"\bpulled (?:it |them |that )?up\b|\bbrought (?:it |them )?up\b|\bhere(?:'s| is| are)\b|\bi(?:'ve| have) (?:got|grabbed|fetched)\b|"
                        r"\bi found (?:the|some|today|these|several|a few)\b|\b(?:headlines?|stories|news|updates) (?:from|are|is)\b|\b(?:are|is) up\b", re.I)


_CAVEAT_VOCAB = {w for w in """found find finding search searched searching sources source reports report results result figure figures answer answers reliable current
    newest oldest latest older old recent date dated year years month months week weeks ago last over from this that what which who when where why how they them
    their there here then than though although while with without about also only both either neither same different treat approximate approximately close closer
    confirm confirmed unconfirmed verify verified check cross compare numbers number numeric fact facts say says said tell told know knows look looked pulled team
    teams game games score scores winner winners right wrong correct sorry honestly honest afraid unfortunately itself primary second first one two three version
    guess guessing sure certain clear maybe may might could would should will can cannot got get give gave take took any anything something nothing none nor yet
    still ever never available info information data page pages site sites link links well good enough really actually just very much many more most less few
    some such other another each every own price prices cost costs amount value todays apologies things thing reading readings day days high low now like report came come turned show shows showed theyre theres thats january february march april may june july august
    september october november december monday tuesday wednesday thursday friday saturday sunday today yesterday tomorrow tonight night morning evening""".split()}
_FUNCTION_WORDS = {w for w in """the and but for nor yet not can may had has was are were who why how you your our its his her him she they them this that these those with from
    into over than then when what which whom will have been being also just very only both each such more most less sir out per via got get let all any one two off
    does did done doing would should could might must shall there here where while whether about because since until though although dont doesnt didnt cant couldnt
    wont isnt arent wasnt werent hasnt havent hadnt shouldnt wouldnt ill ive youre theyre whats thats""".split()}
_FUTURE = re.compile(r"\bi(?:'ll| will| shall)\b|\blet me\b|\bgive me a (?:moment|second)\b|\bagain later\b|\bshortly\b|\bnext time\b|\bin a (?:moment|bit|while)\b", re.I)


def _caveat_clean(sentence, evidence):
    """Is this sentence made of caveat words only (see _honest_caveat)?"""
    hosts = _evidence_content(evidence)[2]
    vocab = {_stem(w) for w in _CAVEAT_VOCAB}
    if _ANNOUNCES.search(sentence) or _FUTURE.search(sentence) or _unsourced_numbers(sentence, evidence) or _unsourced_urls(sentence, evidence):
        return False
    asked = re.sub(r"(?i)"+chr(92)+"b(?:who|which(?: team| player| side| one)?|whether|if)"+chr(92)+"s+(?:"+chr(92)+"w+"+chr(92)+"s+)?(?:won|wins|win|lost|loses|scored|leads?|leading|ahead)"+chr(92)+"b", " ", sentence)
    rest = _CAVEAT.sub(" ", asked)
    names = {w.lower() for w in re.findall(r"(?<!^)(?<![.!?:] )"+chr(92)+"b[A-Z][A-Za-z]+", rest)}
    for w in re.findall(r"[a-z]{3,}", rest.lower().replace("'", "")):
        if w in _FUNCTION_WORDS or w in names or _stem(w) in vocab or _stem(w) in hosts or w in _CAVEAT_VOCAB:
            continue
        return False
    return True


def _honest_caveat(reply, evidence):
    """A reply that IS the computed caveat ("I found conflicting reports, so I can't tell you which team won", "Sorry sir, the newest is from
    last year, so I can't confirm the current answer"): its FIRST sentence states the caveat (at any position), and EVERY sentence of it is
    made of caveat words only: no announcement, no promise, no figure or link the results never gave, and no claim of its own ("The Lakers
    won, though I can't confirm it" says the Lakers won). A name (a word capitalised mid-sentence, a site) may appear; a claim verb may only
    be asked about ("I can't tell you who won")."""
    text = (reply or "").replace(chr(8217), "'").replace(chr(8216), "'").strip()
    parts = [p for p in re.split(r"(?<=[.!?])"+chr(92)+"s+", text) if p.strip()]
    while len(parts) > 1 and len(parts[0].split()) <= 3:
        parts.pop(0)                                          # "Sorry sir." / "Sir."
    if not parts or not _CAVEAT.search(parts[0]):
        return False
    return all(_caveat_clean(p, evidence) for p in parts)


def _web_evidence(history):
    """The newest round's live-search evidence (core/websearch.evidence_text), when that is what the tools returned."""
    newest = history[-1].get("content") if history else None
    if not isinstance(newest, list):
        return None
    texts = [_block(i).get("content") for i in newest if _block(i).get("type") == "tool_result"]
    from core import websearch
    hits = [t for t in texts if isinstance(t, str) and t.startswith((websearch.MARK_SEARCH, websearch.MARK_VIDEO))]
    return "\n".join(hits) if hits and len(hits) == len(texts) else None


def _short_results(history):
    """The newest round's tool results when every one is a short plain string without error, else None."""
    newest = history[-1].get("content") if history else None
    if not isinstance(newest, list):
        return None
    blocks = [_block(i) for i in newest]
    if not blocks or not all(b.get("type") == "tool_result" for b in blocks):
        return None
    texts = [b.get("content") for b in blocks]
    if any(b.get("is_error") or not isinstance(t, str) or len(t) > 400 for b, t in zip(blocks, texts)):
        return None
    return texts


def _anchor(results, said):
    """The content words and numbers a reply about these results (or this question) would share with them."""
    anchor = set()
    for source in [*results, said or ""]:
        anchor |= {w for w in _words(source, hints=False) if len(w) >= 3} | set(re.findall(r"\d+(?:[.,]\d+)?", source))
    return anchor


def _shares(text, anchor):
    return bool((_words(text, hints=False) | set(re.findall(r"\d+(?:[.,]\d+)?", text))) & anchor)


# A skill reporting that it could NOT do it, in words (tools return such text rather than raise, so is_error is False).
_RESULT_FAILED = re.compile(r"\b(?:couldn'?t|can'?t|cannot|could not|unable to|failed|not found|no such|invalid|"
                            r"already passed|didn'?t work|not (?:available|installed|paired|reachable|supported)|"
                            r"isn'?t (?:reachable|available|responding|installed|paired|running)|unreachable|timed out|"
                            r"rate.?limited|cooling down|circuit)\b", re.I)


_PLAIN_TALK = {"right", "later", "again", "sorry", "afraid", "moment", "shortly", "unfortunately", "currently", "seems",
               "looks", "appears", "check", "trying", "retry", "another", "else", "instead", "meanwhile", "want", "shall",
               "would", "could", "like", "help", "please", "there", "still", "cannot", "unable", "reach", "reachable",
               "service", "available", "unavailable", "problem", "issue", "trouble", "offline", "connection", "minutes",
               "second", "while", "back", "soon", "working", "fix", "fixed", "sure", "think", "know", "today", "tonight"}


def _embellishes(text, results, said):
    """A reply to a FAILED result that adds content words found neither in the result nor in the question nor in plain
    talk about trying again: an invented cause or detail ("down for maintenance", "the server crashed")."""
    allowed = _anchor(results, said) | {_stem(w) for w in _PLAIN_TALK | {"last", "theyre", "older", "oldest", "newest", "came", "turned", "says", "said", "show", "shows"}}
    # Offers and questions may name another way ("Shall I check the forecast page instead?"): judged are the
    # statements about what happened.
    statements = [s for s in _SENTENCE.split(text) if s.strip() and not s.strip().endswith("?")
                  and not re.match(r"\s*(?:shall|would you|want me|do you want|i can|i could|let me|how about|or i)", s, re.I)]
    added = {w for w in _words(" ".join(statements), hints=False) if len(w) >= 5} - allowed
    return bool(added)


def _informs(text, results, said):
    """Does an answer to a question carry something the result said that the question didn't? ("Your shopping list
    includes eggs and milk" does; "You can view the list by saying 'read shopping list'" doesn't.)"""
    found = set()
    for r in results:
        found |= {w for w in _words(r, hints=False) if len(w) >= 3} | set(re.findall(r"\d+(?:[.,]\d+)?", r))
    asked = {w for w in _words(said or "", hints=False)} | set(re.findall(r"\d+(?:[.,]\d+)?", said or ""))
    new = found - asked
    return not new or _shares(text, new)


def _answers(reply, results, said):
    """Does a worded reply speak about the results it follows (or about what they asked)? Judged on content words
    and numbers; a result with no content words of its own ("Done.") can't be contradicted and passes."""
    anchor = _anchor(results, said)
    return not anchor or _shares(" ".join(b.text for b in reply.content if b.type == "text"), anchor)


def _his_message(message):
    """A user message with words of theirs in it, not only blocks Jarvis attaches (a tool's <tool-images> evidence)."""
    return isinstance(message, dict) and message.get("role") == "user" and bool(last_user_text([message]).strip())


def _asked_at(messages):
    """Where the message of his that this request answers sits: the newest one, with nothing after it but the calls
    it led to and their results. None when the last thing said is a worded reply (nothing of his is waiting)."""
    for at in range(len(messages) - 1, -1, -1):
        message = messages[at]
        if _his_message(message):
            return at
        role = message.get("role") if isinstance(message, dict) else None
        if not (role in ("tool", "user") or (role == "assistant" and message.get("tool_calls"))):
            return None
    return None


def _with_note(messages, note, at=-1):
    """The messages with `note` appended to the newest user message, or to the one at `at` (only in this request,
    never in the history)."""
    if not note or not messages:
        return messages
    at %= len(messages)
    last = messages[at]
    parts = last["content"] if isinstance(last.get("content"), list) else [{"type": "text", "text": last.get("content") or ""}]
    return messages[:at] + [dict(last, content=parts + [{"type": "text", "text": note}])] + messages[at + 1:]


def _only_opening(response):
    """A reply that's nothing but such an opener, with no call."""
    text = _content(response)
    if _calls(response) or not isinstance(text, str) or not text.strip():
        return False
    sentences = _SENTENCE.split(text.strip())
    return _opening(sentences, is_meta) == len(sentences)


def _past_the_opening(on_text, nothing_ran, anchor=None, said=None, idle=False):
    """on_text for a reply that can't look anything up, minus its opening "One sec, sir." (parse_reply(answering=True)
    drops the same sentences, so what's already been said still lines up with the reply). With `anchor` (the words
    of a short tool result), nothing is said until a sentence is about that result: a reply that never is gets
    refused whole (see _answers), unspoken. `said`: his words when nothing was measured this turn, so a PC reading
    opening the reply is not said either (unchecked_reading; brain.llm._SpokenAsWritten drops one later on)."""
    held, opened = [""], [False]

    def unsaid(sentence):
        return (is_meta(sentence) or (nothing_ran and claims_done(sentence))
                or bool(said and unchecked_reading(sentence, said)) or (idle and promises_action(sentence)))

    def feed(piece):
        if opened[0]:
            return on_text(piece)
        held[0] += piece
        *done, rest = _SENTENCE.split(held[0])
        kept = done[_opening(done, unsaid):]
        if kept and (anchor is None or any(_shares(s, anchor) for s in kept)):
            opened[0] = True
            on_text(" ".join(kept) + " " + rest)
        else:
            held[0] = " ".join(kept + [rest]) if kept else rest      # not an opener: kept, judged with the next piece
    return feed


# "Done" when nothing was done: refused a made-up call for "I approve the $1 limit", it still said "The budget ... has
# been updated to $1". When no skill ran this turn and they asked for something (not a question), such a sentence is
# never spoken.
_DONE = (r"(?:set|updated|changed|approved|added|saved|logged|turned (?:on|off)|started|stopped|sent|deleted|removed|"
         r"created|scheduled|booked|installed|uninstalled|enabled|disabled|opened|closed|played|paused|muted|locked|"
         r"restarted|shut down|cancelled|canceled|moved|downloaded|uploaded|connected|paired|"
         r"queued|submitted|launched|generated|rigged|drafted|dispatched|kicked off|ordered|checked|scanned|reviewed|verified|"
         # "Got it, sir. I've put a gym session on your calendar for tomorrow at 6 pm." with NO call (sandbox 2026-11-20)
         r"put (?!together|some|up|in |a lot|it (?:simply|another way|this way|plainly|briefly))(?:[\w'-]+\s+){0,5}?"
         r"(?:on|in|into|onto) (?:your|the|my) (?:google )?(?:calendar|schedule|(?:shopping |to-?do )?list|reminders)|"
         r"blocked (?:off|out)|penciled|pencilled|rescheduled|"
         # live 2026-11-20 00:15, no call: "I've spun up my own branch so I can fix things..."; and the drive/stream verbs (wave 3b)
         r"spun up|formatted|wiped|erased|engaged|activated|deactivated|deployed|"
         r"committed|merged|branched|backed up|restored|rebooted|emailed|texted|"
         # "Got it, sir. I've noted your departure time as 12:00." with nothing saved (sandbox 2026-11-21, his trip lines)
         r"noted (?:your|the|down|that (?:your|the))|written down|made a note)")   # "I have queued a mission to build your character" with nothing queued (live 2026-11-16)
_CLAIMED = re.compile(rf"\b(?:i'?ve|i have|i just|i'?ve just|i have just) (?:now |already )?{_DONE}\b|"
                      # "Got it, sir. I stopped everything." with nothing run (live 2026-11-10): the simple past too
                      # (not a question: "Shall I set it for 12:20?" offers, it does not claim)
                      rf"(?<!shall )(?<!should )(?<!can )(?<!could )(?<!may )(?<!will )(?<!would )(?<!did )(?<!do )(?<!if )"
                      rf"\bi (?:now |already )?{_DONE} (?:it|that|this|them|everything|all|the|your|a|an)\b(?![^.!]*\?)|"
                      rf"\b(?:has|have) (?:now |already )?been (?:\w+ )?{_DONE}\b|\b(?:is|are) now\b|"
                      r"\b(?:is|are) currently (?:set|on|off|at|running|playing|paused|muted|enabled|disabled|locked|"
                      r"open|closed)\b|"
                      # a promise to act with nothing behind it: "Turn off the lights" -> "I'll turn off the lights."
                      r"\bi'?ll (?:now |go ahead and |just )?(?:import|launch|load|rig|show|turn|switch|set|open|close|start|stop|play|pause|send|"
                      r"delete|add|log|save|book|schedule|install|uninstall|lock|restart|shut|cancel|move|download|"
                      r"connect|pair|approve|update|change|create|build|draft|prepare|write|put together|"
                      r"format|wipe|erase|kill|engage|activate|deploy|commit|merge|"
                      r"make (?:you|a|an|the))\b|^\s*(?:done|all set|it'?s done)\b|"
                      # live 2026-11-01: "I will now open Blender and create a new scene..." and "I see it on your right monitor." with no tool run
                      r"\bi (?:will|am going to|'?m going to) (?:now |go ahead and |just )?(?:open|create|import|launch|load|start|rig|build|make|"
                      r"set up|turn|switch|play|send|delete|save|install|move|download|"
                      # "I will format your D drive now." with no tool (owner's /goal 2026-11-15, still open on 2026-11-21)
                      r"format|wipe|erase|kill|engage|activate|deploy|commit|merge)\b|"
                      r"\bi'?m (?:now )?(?:opening|creating|importing|launching|loading|"
                      r"starting|rigging|building|formatting|wiping|erasing|deploying)\b|\bi (?:can )?see (?:it|that|the \w+|your \w+)\b[^.]*\b(?:monitor|screen|window|desktop)\b|"
                      # qwen3.5 (2026-11-10) narrates instead of calling: "Setting a timer for ten minutes, sir.", "Adding eggs
                      # and milk to your shopping list, sir.", "Setting volume to 30, sir." with NO call: the action as a
                      # sentence-opening -ing verb is a claim too.
                      r"^\s*(?:(?:ok(?:ay)?|right|sure|certainly|of course|very well|understood|got it|now)\W+(?:sir\W+)?)?"
                      r"(?:setting|adding|opening|closing|turning|switching|starting|stopping|saving|playing|pausing|creating|"
                      r"scheduling|sending|deleting|removing|launching|updating|changing|booking|installing|uninstalling|muting|"
                      r"unmuting|locking|putting|logging|writing|making|moving|downloading|pairing|connecting|restarting|"
                      r"cancell?ing|raising|lowering|bumping|dimming|setting up)\b"
                      # the gerund as the SUBJECT of advice is not a claim: "Restarting your router usually fixes slow wifi",
                      # "Making a starter takes a week" (independent review 2026-11-10)
                      r"(?![^.!?]*\b(?:is|are|was|were|takes?|keeps?|saves?|helps?|fixes|works?|makes?|drains?|means?|can|could|"
                      r"may|might|should|will|would|usually|often|tends?|requires?|needs?)\b)"
                      # ...done TO something, or said to the owner ("Opening hours are nine to five." is not a claim)
                      r"(?: (?:a|an|the|your|my|his|her|their|it|that|this|them|up|down|on|off|volume|brightness|\d)\b"
                      r"|[^.!?]*\b(?:sir|ma'?am|now|for you|right away)\b)"
                      # a status report that ENDS on the action: "Practice session stopped, sir." with no call and nothing
                      # running (live 2026-11-10); "The rain stopped yesterday." goes on, so it is not one
                      r"|^\s*(?:the\s+|your\s+)?[a-z][\w' -]{1,40}?\s+(?:stopped|paused|cancell?ed|started|muted|unmuted|resumed|"
                      r"closed|opened|saved|deleted|removed|turned (?:on|off))"
                      r"(?:,?\s*(?:sir|ma'?am|now|for you))*\s*[.!]?\s*$|"
                      # "Understood, sir: protocol engaged." with no call (live 2026-11-20 23:40). Only a thing Jarvis would switch
                      # (never "The war ended.", "My sister got engaged.": review 2026-11-21)
                      r"(?:^|:\s*)(?:the\s+|your\s+)?(?:[\w'-]+\s+){0,2}(?:protocol|mode|stream|streaming|recording|boost|scan|backup|drive|"
                      r"timer|alarm|reminder|routine)\s+(?:engaged|activated|deactivated|enabled|disabled|ended|formatted|wiped|erased|reset|"
                      r"started|stopped|live)(?:,?\s*(?:sir|now))*\s*[.!]?\s*$|"
                      # "Streaming's on. What do you want to watch?" / "Streaming protocol's ready." to his "Streaming." "Protocol."
                      # with no call (sandbox 2026-11-21, his real lines of 2026-11-20)
                      r"^\W*(?:(?:ok(?:ay)?|right|sure|got it|understood)\W+(?:sir\W+)?)?(?:the\s+|your\s+)?(?:[\w'-]+\s+){0,2}"
                      r"(?:protocol|mode|stream|streaming|recording|boost|backup)(?:'s|\s+is|\s+are)\s+(?:now\s+)?"
                      r"(?:on|ready|live|running|active|engaged|set|up|underway|going)\b", re.I)


def claims_done(sentence):
    """A sentence saying an action was carried out. Curly apostrophes count like straight ones: qwen3.5 wrote "I’ll pair
    your phone now ... enter the code 78, 11, 97" with NO call (an invented code), and "I’ll" slipped past "i'?ll"."""
    return bool(_CLAIMED.search((sentence or "").replace("’", "'").replace("‘", "'")))


# A promise to act with nothing behind it (live 2026-11-10, the one brain): "I'll log that preference for the powdered milk
# there." and "I'll pull up the price book." were said in turns that made NO call, so nothing was logged and no price book came.
# claims_done counts such promises only when he asked for something (nothing_ran), and "pull up" not at all. A reply that makes
# no call, in a turn where no skill ran (even an answer to a question), never promises one. Offers stay: they wait for him
# ("If you like, I'll pull up the price book.", "Say the word and I'll add it.", "Shall I add it?", "I'll add it if you'd
# like."); so does "I'll keep that in mind", and "I'll remember that" / "I'll note that" (review 2026-11-10: the journal keeps
# what he said when the conversation ends; dropping the whole reply made "My son only drinks Nido." end in the failure line).
_PROMISE = re.compile(
    r"\b(?:i'?ll|i will|i'?m going to|i am going to|i'?m gonna|let me)\s+"
    r"(?:(?:now|go ahead and|just|quickly|also|first|then|right away|straight away)\s+)*"
    r"(?:log|add|save|jot|record|track|store|file|put (?!it (?:simply|another way|this way|plainly|briefly))|set|"
    # "I'll handle it with care and precision." / "I'll take care of it." with no call (sandbox 2026-11-21, his "Make it happen.")
    r"handle (?:it|that|this|them|everything|things|the rest)|take care of (?:it|that|this|them|everything)|sort (?:it|that|this) out|"
    r"schedule|book|remind|pull|look (?:up|into|for|it up|that up|them up)|check|find|search|research|fetch|grab|"
    r"get (?:that|this|it|them|those|these|you|the|a|an|your|right|on)|bring up|open(?! with\b)|close|launch|"
    r"turn(?! (?:it |this |that |things )?over\b)|switch|send|text|email|"
    r"message|order|buy|delete|remove|clear|update|change|move|copy|download|install|uninstall|create|build|draft|"
    r"write (?:it|that|this|them) down|prepare|run (?!through)|measure|compare|scan|import|load|rig|lock|restart|shut|cancel|"
    r"connect|pair|approve|mark|queue|generate|render|model|process|compile|produce|make (?!a note\b|note\b)(?:a list|you|the|a|an)|"
    r"(?:start|begin) (?!by (?:asking|explaining|saying|telling|answering)\b))\b", re.I)
# Work in progress that nothing is doing (live 2026-11-15, by voice, a turn with no call): "I'm working on generating the humanoid",
# "Give me a moment while I work on it", "I'll let you know when it's ready". Narrow on purpose: first person + an object he
# can hear (not "I'm working on a project", not "it will take a few minutes to boil"). Judged like a promise: not after an "if".
_PROGRESS = re.compile(
    r"\bi(?:'?m| am) (?:now |currently |still |already )?(?:working on (?:it|that|this)\b(?! (?:the|a|an|my|your|being)\b)|"
    r"working on (?:generating|building|making|creating|rigging|the (?:file|project)|(?:the|a|an|your|my) (?:new )?(?:humanoid |3d )?(?:model|mesh|rig|character|humanoid|scene|avatar)|"
    r"your (?:model|request|character|file|project|order))|"
    r"(?:generating|processing|rendering|compiling|modell?ing|preparing|rigging|building|creating|making) "
    r"(?:(?:a|an|the|your|some|one|new)(?: (?:new|humanoid|character|3d|blender|rigged))* "
    r"(?:character|model|rig|humanoid|mesh|scene|file|project|avatar|animation|video|image|render)s?\b|(?:it|that|this) (?:now|right now|for you)\b))|"
    r"\bgive me a (?:moment|second|sec|minute) (?:while|to) (?:i|work|finish|generate|build)\b|"
    r"\bgive me a (?:moment|second|sec|minute) while (?:the|it|that|this|everything)\b|"
    # "I'm already on it, sir. Generating a new humanoid character with rigging for your project right now." / "I am drawing a humanoid character in
    # T-pose and rigging it locally right now." (live 2026-11-15, the PC and the laptop, no call in either turn)
    r"\bi(?:'m| am) (?:already |now |just )?on it\b|"
    # THE CLASS: with no tool behind the turn, "I am <doing something>" is a claim of work in progress, in any wording the 9B finds ("I am starting
    # a new humanoid character generation now", "I am drawing...", "I am opening..."). What stays: how Jarvis feels or thinks, and "I am not ...".
    r"\bi(?:'m| am) (?:now |currently |already |just |still |really )?(?!(?:listening|thinking|hearing|wondering|hoping|guessing|looking forward|"
    r"working on (?:being|the assumption|my own)|trying to (?:understand|help|follow)|starting to (?:think|see|wonder|understand)|going to (?:say|be|need|ask|have)|about to (?:say|ask)|"
    r"making (?:sure|progress|it clear|a (?:note|point|guess|recommendation|exception|call)|an exception)|"
    r"being|having|saying|asking|telling|speaking|talking|sorry|afraid|here|sure|glad|happy|ready|fine|good|well|able|unable|so|not|"
    r"at your service|standing by|doing (?:fine|well|good|okay|great))\b)\w+ing\b|"
    r"\bone (?:sec|second|moment|minute) while\b|"
    r"(?:^|[.!?]\s+)(?:checking|opening|loading|pulling up|reading|launching|starting|fetching|scanning|searching)\s+(?:on |at |in |through )?"
    r"(?:blender|unreal|shadow|your (?:scene|project|screen|files?|model)|the (?:scene|project|model|rig|mesh|status|files?|character)|that|it)\b|"
    # the stall in any form, and "have it ready" / "report back" (live 2026-11-15 14:19, the laptop: "I'll have it ready for you shortly.",
    # "Give me a moment to get the scene ready.", "I'll report back once the model is ready.")
    r"\bgive me a (?:moment|second|sec|minute)\b(?! to think\b)|"
    # a compound promise: "I'll use a trusted template and save the file to your project folder." (live 2026-11-15 14:56, the laptop)
    r"\bi(?:'ll| will) [^.?!]{0,60}\b(?:and|then) (?:save|create|build|make|generate|write|open|set up|rig|render|import|export)\b|"
    r"\bi(?:'ll| will) (?:have|get) (?:it|that|this|them|everything|(?:the|that|this|your) \w+) (?:ready|done|set up|going)\b|\bi(?:'ll| will) report back\b|"
    r"\bget(?:ting)? the (?:scene|model|character|rig|project) (?:ready|set up)\b|"
    r"(?:^|[.!?]\s+)(?:generating|rigging|building|rendering|modell?ing|creating|drawing|preparing)\s+(?:(?:a|an|the|your)(?: new)?(?: humanoid| 3d| rigged)* "
    r"(?:character|model|rig|humanoid|mesh|scene|avatar|animation)s?\b|(?:it|that)\s+(?:now|right now|locally)\b)|"
    r"\bi(?:'m| am) (?:now |currently |just )?(?:drawing|sculpting|modell?ing) (?:a|an|the|your|new|it|that)\b|"
    # The grammar of a promised RESULT, whoever the subject and whatever the thing ("I will let you know when the humanoid is ready", "It should
    # be ready in a few minutes", "It will take a few minutes to complete"): with no tool behind it nothing is coming. A time of day or a
    # day ("Dinner will be ready at six", "It will be done by Friday") is a plan about the world, not a promise of work.
    r"\blet you know when\b|"
    r"\bi(?:'ll| will) let you know (?:as soon as|once|how it goes|the (?:result|outcome)|what (?:i|happens|comes))\b|"
    r"\b(?:it|that|this) (?:will|'ll) be (?:created|made|generated|built|drawn|rendered|rigged|modell?ed)\b(?! (?:if|once|when|after|by|on|in)\b)|"
    r"\bi(?:'ll| will) keep you (?:posted|updated|informed|in the loop)\b|"
    r"\bi(?:'ll| will) (?:draw|sculpt|sketch|design) (?:it|that|this|a|an|the)\b|"
    r"\b(?:it|that|this)(?:'s| is| was being)? being (?:built|created|made|generated|rigged|drawn|rendered|modell?ed|set up)\b|"
    r"\bi(?:'ll| will) work on (?:it|that|this)\b|"
    r"(?<!\byou )(?<!\bwe )(?<!\byou)(?<!\bwe)\b(?:will|should|'ll|going to)\s+(?:be\s+)?(?:ready|done|complete|completed|finished|available)\b"
    r"(?![^.?!]*\b(?:at|by|on|before|until|tomorrow|tonight|today|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b)|"
    r"\b(?:will|should)\s+take\s+(?:a|about|around|roughly)?\s*(?:few|couple|minute|moment|second|while)\b[^.?!]*\bto\s+(?:complete|finish|build|render|generate|rig|process|load)\b|"
    r"\b(?:run(?:s|ning)|work(?:s|ing)|build(?:s|ing)|render(?:s|ing)|process(?:es|ing)|go(?:es|ing)|happen(?:s|ing)) in the background\b|"
    r"\bwhen (?:it|that)(?:'s| is| has) (?:done|ready|finished|built|rigged)\b|"
    r"\b(?:it|that)(?:'ll| will) be (?:rigged|built|ready|done|finished|posed)\b|\b(?:rigged|built) and ready\b|"
    r"\bi(?:'m| am) checking on (?:the|your) (?:\w+ ){0,2}(?:job|task|build|render|rig)\b|"
    r"\bi(?:'?m| am) (?:now |currently |just )?(?:starting|beginning|about) to (?:generate|build|create|make|render|rig|model|work on|open)\b|"
    r"\bi(?:'?m| am) (?:now |currently )?(?:opening|launching|loading|importing|starting) (?:blender|unreal|the|your|it|that|this)\b|"
    r"\b(?:and |then )?let you know when (?:it|that|they)(?:'s| is| are)? (?:ready|done|finished|complete)\b|"
    r"\b(?:it|that|they) (?:will|should) (?:appear|show up|be (?:ready|done|finished|rigged|there|up)|finish)\b[^.?!]*"
    r"\b(?:shortly|soon|in a moment|in a minute|momentarily)\b|\b(?:it|that) will take a (?:moment|minute|few)\b[^.?!]*\b(?:finish|rig|build|generat|render)|"
    r"\bi'?ll let you know when (?:it'?s|it is|that'?s|they'?re) (?:ready|done|finished|complete)\b", re.I)
# THE CLASS, for the future tense (data/jarvis.log 2026-11-11, all turns with no call, his wish list for Jarvis): "I will fix your
# Jarvis to speak, teach you languages ... and upgrade your UI. I am starting now." / "I will upgrade your Jarvis to match the Tony
# Stark standard". _PROMISE lists the actions on the PC; these are the verbs of making and changing things (software, plans,
# skills), done TO something ("your", "the", "it", ...). Not the verbs a chat reply performs as it speaks (summarize, compare,
# explain, read, rephrase: review 2026-11-21 found a deny-list version dropped those).
_FUTURE_ACT = re.compile(
    r"\bi(?:'ll| will| shall|'m going to| am going to|'m gonna)\s+(?:(?:now|go ahead and|just|also|first|then|right away|straight away|"
    r"personally|definitely|immediately)\s+)*"
    r"(?:fix|upgrade|improve|redesign|rebuild|rewrite|refactor|repair|overhaul|revamp|modify|customi[sz]e|transform|enhance|extend|"
    r"expand|optimi[sz]e|tweak|adjust|polish|patch|implement|integrate|develop|program|code|design|configure|train|teach|"
    r"research|learn|study|investigate|work on|get started on|start on|tackle|finish|complete|redo|clean up|set up|hook up|wire up)"
    r"(?:\s+and\s+[a-z]+)?\s+(?:your|the|my|it|that|this|them|these|those|some|everything|all|every|him|her)\b", re.I)
_OFFER_FIRST = re.compile(r"\b(?:if|when|once|whenever|as soon as|after|before|until|unless|say the word|just say|tell me|"
                          r"let me know|give me|confirm|want|would you like|ready)\b", re.I)
# ...and the condition said after the promise: "I'll add that to your list if you'd like.", "...once you confirm."
_OFFER_AFTER = re.compile(r"\b(?:if you(?:'d| would)? (?:like|want|wish|prefer)|if (?:that's|that is|it's|it is) (?:ok|okay|"
                          r"alright|all right|fine)|once you (?:confirm|say|approve|give)|when(?:ever)? you (?:say|tell me|give)|when(?:ever)? you(?:'re| are)? ready|"
                          r"if you say so|with your (?:ok|okay|go-ahead|permission)|on your (?:word|go|say-so))\b", re.I)


# THE DURABLE RULE (owner /goal 2026-11-15; the 9B kept finding new wordings of fake progress, 15 rounds of phrase lists): when he asks for
# something to be MADE ("Can you make me a humanoid in Blender?") and no tool ran, every sentence of the reply must be a question, an
# admission ("I haven't started that"), or an offer ("if you want, ..."). Any other statement ("It will be created locally.", "Understood.",
# "Yes, sir.") says or implies work that is not happening, so it is not said, whatever its wording.
_ACT_REQUEST = re.compile(
    r"\b(?:make|build|create|generate|rig|model|render|draw|design|animate|write|code|develop|set up|produce|compose)\b[^.?!]{0,60}\b"
    r"(?:humanoid|character|avatar|model|mesh|rig|scene|animation|video|image|picture|website|web ?site|app|program|script|game|3d|blender|unreal|"
    r"project|presentation|spreadsheet|report|document|file|level|texture|material)\b", re.I)
_HONEST_OR_OFFER = re.compile(
    r"\b(?:can'?t|cannot|can not|unable|not able|don'?t|do not|(?<!you )haven'?t|(?<!you )have not|won'?t|no way|not yet|isn'?t|aren'?t|not something|sorry|"
    r"apolog\w*|tell me|say the word|just say|if you (?:want|like|say|prefer|wish)|would you like|shall i|should i|want me to|do you want|"
    r"i need (?:to know|you to|more)|first step|before i)\b", re.I)


# What a make-request is for, grouped by kind ("web site" and "website" are one): a reply about ANOTHER kind is an answer to an earlier request
# (live 2026-11-15: "Make me a presentation about space." -> "I can build you a website, sir, but I need to know your business name").
_THINGS = {
    "website": "website", "web site": "website", "site": "website", "webpage": "website", "web page": "website",
    "presentation": "presentation", "slides": "presentation", "slideshow": "presentation", "deck": "presentation",
    "video": "video", "movie": "video", "animation": "animation", "image": "image", "picture": "image", "logo": "image", "poster": "image",
    "app": "app", "application": "app", "program": "app", "script": "app", "game": "game", "level": "game",
    "spreadsheet": "spreadsheet", "report": "document", "document": "document", "essay": "document", "letter": "document", "file": "document",
    "humanoid": "character", "character": "character", "avatar": "character", "creature": "character", "model": "character", "mesh": "character",
    "rig": "character", "scene": "scene", "texture": "scene", "material": "scene", "project": "project"}
_THING_WORD = re.compile(r"\b(" + "|".join(sorted((re.escape(w) for w in _THINGS), key=len, reverse=True)) + r")s?\b", re.I)


def _things(text) -> set:
    return {_THINGS[m.group(1).lower()] for m in _THING_WORD.finditer(str(text or ""))}


def _protocol_order(said) -> bool:
    """His words order a protocol ("And Streaming Protocol.", "run the house party protocol"): a reply with no tool behind it
    may not say "Got it, sir." (live 2026-11-20 23:48: nothing ran). core.protocols judges the words (no question, no
    negation, no narration)."""
    if "protocol" not in str(said or "").lower():
        return False
    try:
        from core import protocols
        return protocols.sounds_like_order(str(said))
    except Exception:  # noqa: BLE001 - unknown: as before
        return False


def bare_on_action(sentence, said) -> bool:
    """A sentence that is neither a question, an admission nor an offer, in a reply to a request to make something, with no tool behind
    the turn (the caller judges "no tool"); or any sentence about a different kind of thing than the one asked for. See the note above:
    this is the class rule, not a phrase list."""
    text = (sentence or "").replace("\u2019", "'").strip()
    if not text or not said or not (_ACT_REQUEST.search(str(said)) or _protocol_order(said)):
        return False
    asked, named = _things(said), _things(text)
    if named and asked and not (named & asked):
        return True                                              # an answer to an earlier request, said again for this one
    if text.endswith("?"):
        return False
    return not _HONEST_OR_OFFER.search(text)


def promises_action(sentence):
    """A sentence promising an action Jarvis would take ("I'll pull up the price book."), not an offer that waits for him
    (a question, or a promise after "if"/"once"/"say the word", or before "if you'd like"/"once you confirm"). Only said when
    a call stands behind it (see _PROMISE)."""
    text = (sentence or "").replace("’", "'").replace("‘", "'").strip()
    if not text or text.endswith("?"):
        return False
    for pattern in (_PROMISE, _PROGRESS, _FUTURE_ACT):   # each judged on its own: an offer word near one must not hide the other
        m = pattern.search(text)
        if not m:
            continue
        before = re.sub(r"(?i)\bgive me (?:a|one|just a) (?:(?:few|couple of|couple) )?(?:moments?|seconds?|secs?|minutes?)\b", " ", text[:m.start()])   # a stall, not an offer ("give me" is one)
        if not _OFFER_FIRST.search(before) and not _OFFER_AFTER.search(text[m.end():]):
            return True
    return False


# "If he does not know something he looks it up on the internet, always" (owner, 2026-11-14). Two kinds of sentence mean
# the model has nothing to answer from: it says so ("I don't have current information on that"), or it pretends to have
# looked ("I've checked the major news feeds and there is no credible report...", "I'm pulling the latest headlines right
# now": both said that evening with no search run). The Brain holds a draft that OPENS on either, runs a real search and
# answers from what it found (Brain._look_it_up); these two only recognise the sentence.
_UNKNOWN = re.compile(
    r"\bi (?:do ?n'?t|do not) (?:know(?! about you\b)|recall|have (?:any |that |enough |current |live |real-?time |up-to-date |access to |a way to |the ability to )"
    r"|have information)\b|"
    r"\bi(?:'m| am) not (?:sure|certain)(?: (?:who|what|when|where|which|how|why|about|of)\b|\s*[.,;!]|\s*$)|"
    r"\bi(?:'m| am) (?:not (?:aware|able to (?:check|browse|search|look|see|verify|confirm|access))|unable to (?:check|browse|search|look|verify|confirm|access)|unaware)\b|"
    r"\bi (?:can'?t|cannot|can not) (?:browse|access|check|look (?:up|at|into)|search|verify|confirm|pull|read|see (?:the|any|current|live))\b|"
    r"\b(?:no|not any) (?:information|record|data|knowledge|details) (?:on|about|of|regarding)\b|"
    r"\bmy (?:knowledge|training|information)(?: data)? (?:only |is |was |ends|stops|cuts|does)|\bas of my (?:last|latest|most recent)\b|"
    r"\bi have no (?:way|means|access|information|record|idea|knowledge)\b|"
    r"\bi (?:do ?n'?t|do not|haven'?t|have not) (?:have|got|found) (?:(?:the|an?|any|that|this|those|these|his|her|their|its|your|my|our)\s+)?"
    r"(?:[\w'-]+\s+){0,3}?(?:result|results|answer|score|scores|details?|figures?|numbers?|latest|updates?|headlines?|reports?|info|information)\b|"
    r"\bi (?:do ?n'?t|do not|haven'?t|have not) (?:have|got) (?:it|that|them|this|those)\b[^.!?]{0,12}\b(?:handy|on hand|at hand|right now|yet)\b|"
    r"\bfrom that search\b", re.I)
_LOOKED_AT = (r"(?=[^.!?]{0,60}\b(?:news|feeds?|headlines?|web|internet|online|sources?|reports?|outlets?|coverage|sites?|"
              r"articles?|results|updates|wires?|press|forecast|weather|scores?|standings|stocks?|prices?|found (?:nothing|no)|nothing|no (?:credible|reports?|results))\b)")
_LOOKED = re.compile(
    r"\bi(?:'ve| have| just|'ve just| have just)?(?: now| already| just)? (?:checked|searched|looked (?:up|into|at|through|for)|reviewed|scanned|"
    r"read through|gone through|been through|pulled|verified|confirmed|researched|consulted|browsed|scoured)\b" + _LOOKED_AT + r"|"
    r"\bi(?:'ve| have|'ve just| just)? (?:double-?checked|checked|verified it|confirmed it|looked it up|looked into it)(?=[,.:;]| and\b)|"
    r"\bi(?:'ve| have| just)? (?:looked it up|looked into it)\b|"
    r"\bi(?:'m| am)(?: now| currently| just)? (?:pulling|checking|searching|looking (?:up|into|for|at)|reading through|scanning|reviewing|"
    r"gathering|fetching|browsing|researching)\b" + _LOOKED_AT + r"|"
    r"\b(?:give me|just|one) (?:a )?(?:moment|second|sec|minute) (?:to|while i) (?:read|check|pull|look|search|gather|go through|review)\b", re.I)


_HEDGED = re.compile(r"\b(?:i (?:think|believe|guess|reckon|suspect)|probably|likely|maybe|perhaps)\b|"
                     r"\b(?:but|though|however|although)\b[^.!?]*\b(?:it'?s|it is|that'?s|that is|they|he|she|the answer|here'?s)\b", re.I)


def admits_unknown(sentence):
    """A sentence saying he has nothing to answer from ("I don't have current information on that"). Only when that is
    the whole sentence: "I'm not sure, but I think it's Paris" is an answer."""
    text = (sentence or "").replace("’", "'").replace("‘", "'")
    return bool(_UNKNOWN.search(text)) and not _HEDGED.search(text)


_OFFER_LOOKUP = re.compile(r"\b(?:want me to|shall i|should i|would you like me to|do you want me to|may i|can i)\b[^.!?]{0,25}\b"
                           r"(?:pull (?:it|that|this|them|those)? ?up|look (?:it|that|this|them|those)? ?up|look into|search|find out|check|google|dig)", re.I)


def offers_lookup(sentence):
    """An OFFER to look it up ("Want me to pull it up?", "Shall I look it up?"): when no search ran, the search is just done."""
    text = (sentence or "").replace("\u2019", "'").replace("\u2018", "'")
    return bool(_OFFER_LOOKUP.search(text))


def claims_lookup(sentence):
    """A sentence saying he looked something up or is doing so now ("I've checked the news feeds", "I'm pulling the
    headlines"). True of the words alone: whether a search really ran is the caller's knowledge."""
    text = (sentence or "").replace("’", "'").replace("‘", "'").strip()
    return bool(text) and not text.endswith("?") and bool(_LOOKED.search(text))


# "Don't book anything yet." -> plan_trip. An action they just said NOT to take, in a turn that orders nothing else,
# is no call at all: the words are a statement, answered in words.
_NEGATED_ACTION = re.compile(r"\b(?:don'?t|do not|not yet|no need to|never|without|nothing to|not going to|won'?t)\s+"
                             r"(?!forget)(?:\w+\s+){0,2}?(?:book|plan|schedule|add|save|order|buy|send|set|start|create|"
                             r"remember|remind|log|reserve|change|delete|remove|cancel|install|open|play|turn|move|"
                             r"download|share|post)\w*\b", re.I)
_INSISTS = re.compile(r"\b(?:don'?t forget|make sure|remember|be sure) (?:to|you|and)\b", re.I)   # "don't forget to add it"
_ORDER = re.compile(r"(?:^|[.!?;,]\s*)(?:(?:please|also|and|then|just|now|ok|okay|so|hey|jarvis)[\s,]+)*"
                    r"(?:add|set|remind|book|plan|schedule|save|open|play|turn|start|stop|send|log|put|create|make|"
                    r"remember|note|order|buy|reserve|cancel|delete|remove|change|update|write|install|uninstall|show|"
                    r"take|try|call|text|message|search|look|find|check|read|tell|give|get|go|run|launch|pause|skip|"
                    r"switch|close|lock|restart|shut|wake|move|download|clear|forget|learn|teach|pair)\b", re.I)


def says_not_to(said):
    """They said not to do something and asked for nothing else in the same breath ("Don't book anything yet.")."""
    return (bool(said) and bool(_NEGATED_ACTION.search(said)) and not _ORDER.search(said)
            and not _REQUEST.search(said) and not _INSISTS.search(said))


# Live: "Remember that my wife's name is Maya." -> remember(fact="Maya"). A bare name is no fact: it can't be
# recalled by "wife" and can never be corrected. The fact is then their own sentence, less the "remember that" and a
# trailing ", not Maya" (a correction names the old value; the clause kept is the new one).
_FACT_LEAD = re.compile(r"^(?:(?:ok|okay|also|and|so|hey|jarvis|actually|please|sir)[\s,:]+)*(?:(?:a |quick |small )?"
                        r"(?:correction|update|note|reminder)[\s,:]+)?(?:(?:(?:can|could|would) you )?(?:remember|note|save|"
                        r"keep in mind|don'?t forget|write down|jot down)(?:\s+that|\s+this)?[\s,:]*)?", re.I)
_NOT_OLD = re.compile(r",?\s+(?:and )?not\s+(?:the )?[A-Z][\w'-]*(?:\s+[A-Z][\w'-]*)?\s*[.!]?$")


def whole_fact(said):
    """Their sentence as a self-contained fact, or "" when it isn't one ("Remember that." / a bare name)."""
    if said.strip().endswith("?") or just_asking(said):
        return ""                                             # a question is not a fact about them
    text = _FACT_LEAD.sub("", said.strip()).strip()
    text = _NOT_OLD.sub("", text).strip(" .!?,;")
    if len(text.split()) < 3:
        return ""
    return text[:1].upper() + text[1:] + "."


def _repair_fact(args, said, heard=None):
    """A fact to remember must be made of their words: a bare name ("Maya"), or a sentence with words nobody said
    ("Maya is the owner of this PC", invented live for "my wife's name is Maya"), becomes their own sentence."""
    fact = str(args.get("fact", "")).strip()
    source = _fact_words(f"{said} {heard or ''}")
    own = {w for w in _fact_words(fact) if len(w) >= 4}
    whole = whole_fact(said)
    if whole and _contradicts_them(fact, whole):
        return dict(args, fact=whole)        # "...my wife's name is Marie, not Maya" saved as "...is Maya": theirs wins
    from tools import memory
    if whole and memory.drops_a_plan(fact, whole):
        return dict(args, fact=whole)        # "I'm going to live in Denver next month" saved as "I live in Denver.": a plan
    if whole and _slot_of(whole) and _slot_of(fact) != _slot_of(whole):
        # They stated a single-valued fact ("my wife's name is Marie") and the model saved something else entirely
        # (three live runs re-saved the previous turn's sister fact): what they stated is what is remembered.
        return dict(args, fact=whole)
    if len(fact.split()) >= 3 and not fact.istitle() and own <= source:
        return args
    return dict(args, fact=whole) if whole else args


_STOP_STEMS = {_stem(w) for w in _STOP}


def _fact_words(text):
    """Content stems with the stop words removed AFTER stemming ("likes" -> "like" is a stop word on both sides)."""
    return {_stem(w) for w in re.findall(r"[a-z]{3,}", text.lower().replace("'", ""))} - _STOP_STEMS


def _slot_of(text):
    from tools import memory
    m = memory._SLOT_RE.search(str(text).strip())
    return m.group(1).lower() if m else None


def _contradicts_them(fact, whole):
    """The model's fact gives a different value than their own sentence for the same single-valued slot (live: the
    correction "my wife's name is Marie, not Maya" was saved as "My wife's name is Maya" -> "Already noted")."""
    from tools import memory
    return memory.corrects(whole, fact)


# Mode changes whose argument names WHO or WHICH LANGUAGE they are for. That value must come from what they said: a
# language they named (by any of its names, "kreyol" = "Haitian Creole"), a person they named, or themselves. Live
# (2026-10-30) the 3B model filled them from the utterance itself ('frozen pencil' as a person), from the background
# notes ('mesye', the owner's Kreyol title) or from nothing at all ('Kreyòl ayisyen' for a two-word English phrase).
_LANGUAGE_ARGS = {"set_conversation_language": ("language",), "start_interpreter": ("language_a", "language_b")}
_PERSON_ARGS = {"pair_phone": "person"}
_SELF = {"", "me", "myself", "i", "my", "mine", "owner", "the owner", "my phone"}
_AUTO_LANGUAGE = re.compile(r"\b(auto\w*|detect\w*|any language|all languages|normal|whatever|both|unlock\w*|"
                            r"stop locking|either)\b", re.I)
# "pair my wife's phone" -> pair_phone(person="Maya"): a household member they referred to by relation is theirs
# to name, as long as the person resolves to someone Jarvis knows (never a title or a stray phrase).
_KIN = re.compile(r"\b(wife|husband|partner|son|daughter|kids?|child(?:ren)?|boy|girl|mom|mum|mother|dad|father|"
                  r"grand\w+|sister|brother|niece|nephew|cousin|aunt|uncle|madanm|mari|fanm|pitit|manman|papa)\b", re.I)
_TAG_QUESTION = re.compile(r"[,\s]+(?:okay|ok|alright|right|yes|yeah|please|got it|understood|d'accord|dakò|wi)\s*\?$", re.I)


def _plain(text):
    """Lower case without accents: 'Kreyòl' and 'kreyol' are the same word to the ear."""
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFKD", str(text).lower()) if not unicodedata.combining(c))


def _said_name(value, text):
    """The value appears in the text as whole words (accents and case aside)."""
    v = re.sub(r"\s+", " ", _plain(value).strip())
    return bool(v) and bool(re.search(rf"(?<![a-z0-9]){re.escape(v)}(?![a-z0-9])", _plain(text)))


def _unheard_target(name, args, said, heard):
    """For a mode change (_LANGUAGE_ARGS / _PERSON_ARGS): the argument whose language or person they never named,
    or "question" when the words ask about it rather than for it. None when the call is theirs."""
    if name not in _LANGUAGE_ARGS and name not in _PERSON_ARGS:
        return None
    asked = _TAG_QUESTION.sub("", said.rstrip())   # "...speak Spanish now, okay?" is a statement with a tag on it
    if asked.endswith("?") and not _REQUEST.search(asked):
        return "question"                 # "Ou pale kreyòl?" asks whether Jarvis speaks it; it doesn't switch to it
    if name in _LANGUAGE_ARGS:
        from tools import languages
        named = languages.named_in(said)
        for key in _LANGUAGE_ARGS[name]:
            value = str(args.get(key, "")).strip()
            if not value:
                continue
            if value.lower() == "auto":
                if not _AUTO_LANGUAGE.search(said):
                    return key
                continue
            code = languages.code_for(value)
            if code is None:              # not a language Jarvis knows: only if they said those very words
                if not _said_name(value, said):
                    return key
            elif code not in named and not (name == "start_interpreter" and code == "en"):
                return key                # the interpreter's English side may go unsaid; the other side never
    key = _PERSON_ARGS.get(name)
    if key:
        value = str(args.get(key, "")).strip()
        if _plain(value) not in _SELF and not _said_name(value, heard):
            from core import users
            uid = users.find(value)
            if uid is None or not (_said_name(users.get(uid).get("name", ""), heard) or _KIN.search(said)):
                return key
    return None


# An action with no grounding rule of its own still has to be ASKED FOR (see _asked_for). The local template calls a
# function on anything it is given, and a bare phrase sent after a restart ("copper window", in the real-model
# harness) opened the camera's live view: show_camera_view had been offered for the "window" in its description.
# Lookups are never held to this. These are the verbs every skill's name shares; they name no skill in particular.
_GENERIC_PARTS = {_stem(w) for w in ("set add log start create save update remove delete cancel stop switch forget make "
                  "move install uninstall open close restart repair enroll activate deactivate show take try write undo "
                  "watch wake build assign clip fetch control command my the to from on that").split()}


_PICTURES = _CAPTURE - {"read_clipboard"}   # a picture of their screen or room is taken only when asked (same harness:
                                            # 'crimson window' from the phone app read the screen, for the "window")


def _asked_for(name, said, asked="", recent=(), args=None):
    """An action with no grounding rule of its own is theirs when their words name it (its own words or an everyday
    word for it: "louder" is the volume), or answer a question of Jarvis's that named it ("Which stores shall I
    assign?" - "Costco"), or order/request something while the skill is the one in use in this exchange ("turn it
    up" right after the volume was set). An order with no such link is not enough: "Please make corrections too."
    ran build_step, and 'quiet falcon', sent after Jarvis asked what they wanted to know, ran the companion
    (real-model harness). A picture of the screen or room takes a question about it as the asking too."""
    if name not in _PICTURES and (name in _GROUNDING or not _ACTS.match(name)):
        return True                           # lookups, and skills judged by a grounding rule of their own
    parts = {_stem(x) for x in name.split("_")} - _GENERIC_PARTS
    short = re.findall(r"[a-z0-9]+", said.lower())            # "pc", "tv": too short for _words
    if parts & _words(said, hints=False) or parts & set(short):
        return True
    ordered = bool(_ORDER.search(said) or _REQUEST.search(said))
    if parts & _words(said) and (ordered or _hint_command(said)):
        return True                           # an everyday word for the skill, as a command: "louder", "a bit quieter"
    if name in _PICTURES and (ordered or _ASKS.match(said)):
        return True                           # (_ON_SCREEN / the camera words already had to be in what they said)
    if ordered and (name in recent or _vocabulary(name) & _words(said, hints=False) or _names_its_object(name, args, said)):
        return True                           # an order in the skill's own words ("show me what you see"), or
    # Jarvis's question must NAME the skill (no everyday hints: "...you asked about a quiet pencil. Can you give me
    # more context?" followed by "Juris." ran the volume, "quiet" being a volume word).
    return bool(asked and parts & _words(asked, hints=False))


# Words that may surround an everyday hint word in a one-breath command ("a bit louder please", "turn it down").
_HINT_FILLER = {"bit", "little", "more", "less", "lot", "please", "now", "again", "some", "keep", "turn", "make", "put",
                "set", "get", "let", "lets", "sir", "jarvis", "buddy", "that", "this", "the", "and", "you", "can", "could",
                "would", "yes", "okay", "right"}


def _hint_command(said):
    """The utterance is nothing but an everyday hint word and filler ("louder", "mute", "a bit quieter please"), so the
    hint IS the command. Not a phrase the hint word merely sits in: live, 'quiet button' and 'quiet meadow' (random
    phrases) ran the volume because "quiet" is a volume word."""
    tokens = [w for w in re.findall(r"[a-z]+", said.lower().replace("'", "")) if len(w) >= 3]
    return bool(tokens) and all(_stem(w) in _HINT_OF or w in _STOP or w in _HINT_FILLER for w in tokens)


@functools.lru_cache(maxsize=512)
def _vocabulary(name):
    """A skill's own words: its name and its description, less the verbs every skill shares."""
    from tools import registry
    description = next((s.get("description", "") for s in registry.schemas() if s["name"] == name), "")
    return (_tool_words(name, description) | {_stem(x) for x in name.split("_")}) - _GENERIC_PARTS


def _names_its_object(name, args, said):
    """The call is applied to something they named: a word of what they said is in the arguments the model wrote
    ("Open YouTube" -> open_website(url="https://www.youtube.com")). Placeholders and the model's own picks aren't."""
    theirs = {w for w in _words(said, hints=False) if len(w) >= 4}
    for text in _free_text(name, args or {}):
        squashed = re.sub(r"[^a-z0-9]", "", text.lower())
        if any(w in squashed for w in theirs):
            return True
    return False


def used_in(history):
    """The skills already run in this exchange (the last few turns)."""
    out = set()
    for turn in (history or [])[-6:]:
        value = turn.get("content", "")
        for item in [] if isinstance(value, str) else value:
            if _block(item).get("type") == "tool_use":
                out.add(_block(item)["name"])
    return out


def jarvis_asked(history):
    """The question Jarvis put to them just before the newest user turn, or "" when his last words weren't one."""
    text = _last_assistant_text(history or []).rstrip()
    return text if text.endswith("?") else ""


def _justified(name, args, said, heard, asked="", recent=()):
    """A call they asked for: grounded in their words (_GROUNDING; pc_command is checked for what it runs; any other
    action by _asked_for), not an action on a plain question, naming only a language or person they named
    (_unheard_target), and nothing in it made up (_invented). `said`/`heard` None skip those checks."""
    inner = args.get("name") if name == "pc_command" and isinstance(args.get("name"), str) else None
    if said is not None and any(c in _GROUNDING and not _GROUNDING[c].search(said) for c in filter(None, (name, inner))):
        return False
    if said is not None and _ACTS.match(name) and (just_asking(said) or says_not_to(said)):
        return False
    if said is not None and not _asked_for(name, said, asked, recent, args):
        return False
    if said is not None and _unheard_target(name, args, said, heard if heard is not None else said):
        return False
    return heard is None or not _invented(name, args, heard, act=bool(_ACTS.match(name)))


def _calls(response):
    try:
        return response["choices"][0]["message"].get("tool_calls") or []
    except (KeyError, IndexError, TypeError, AttributeError):
        return []


def _content(response):
    try:
        return response["choices"][0]["message"].get("content") or ""
    except (KeyError, IndexError, TypeError, AttributeError):
        return ""


def _without_answer(response):
    """The reply minus any answer_directly call sitting next to a real one (the real call is what they asked for)."""
    calls = _calls(response)
    keep = [c for c in calls if c.get("function", {}).get("name") != ANSWER["function"]["name"]]
    if len(keep) == len(calls):
        return response
    choice = dict(response["choices"][0])
    choice["message"] = dict(choice["message"], tool_calls=keep)
    return dict(response, choices=[choice] + response["choices"][1:])


# "<said-unprompted>" (core.asides): a reminder Jarvis read out is HIS words, never what they asked for (grounding).
# "<recently-elsewhere>" (core.thread.news): what was said on the OTHER machine, never what they asked here.
_INJECTED = ("<background>", "<retrieved-memory>", "<daily-awareness>", _TOOL_IMAGES_TAG, "<said-unprompted>",
             "<recently-elsewhere>")
_CHANNEL_NOTE = re.compile(r"^\s*\[Sent from their Jarvis phone app[^\]]*\]\s*")


def _new_subject_note(history) -> str:
    """The note for a line of plain talk that starts a new subject after an exchange about something else
    (core.chatturn.new_subject), or ''. Never for an order, a request to act or anything about the machine."""
    try:
        from core import chatturn
        said = last_user_text(history)
        newest = history[-1] if history else {}
        if newest.get("role") != "user" or (isinstance(newest.get("content"), list) and any(
                _block(i).get("type") == "tool_result" for i in newest["content"])):
            return ""
        if chatturn.greeting(said) or not chatturn.plain_talk(said) or about_this_chat(said):
            return ""
        return chatturn.FRESH_NOTE if chatturn.new_subject(said, history) else ""
    except Exception:  # noqa: BLE001  (a note never stops a turn)
        return ""


def last_user_text(messages):
    """The newest thing the user actually said (skips tool-result-only turns, and the context blocks
    Jarvis itself attaches — otherwise a shopping list in <background> 'grounds' a shopping_route call)."""
    for turn in reversed(messages or []):
        if turn.get("role") != "user":
            continue
        value = turn.get("content", "")
        texts = [value] if isinstance(value, str) else \
            [_block(i).get("text", "") for i in value if _block(i).get("type") == "text"]
        texts = [_CHANNEL_NOTE.sub("", t) for t in texts if not t.lstrip().startswith(_INJECTED)]
        if any(t.strip() for t in texts):
            return " ".join(t for t in texts if t.strip())
    return ""


def _leaked_tool_call(text: str) -> bool:
    """A tool call the model wrote out as plain text instead of calling it."""
    s = text.strip().strip("`").strip()
    if s.lower().startswith("json"):
        s = s[4:].strip()
    if not s.startswith("{"):
        return False
    from core import offline
    try:
        obj = offline.loads_lenient(s)
    except ValueError:
        # Not even loosely JSON, but shaped like a call: never speak it.
        return bool(re.match(r'\{\s*"name"\s*:\s*"[^"]*"\s*,\s*"(?:parameters|arguments|args)"\s*:', s))
    return isinstance(obj, dict) and "name" in obj and any(k in obj for k in ("parameters", "arguments", "args"))


def _structured(response):
    """A reply to a response_format request: the JSON text itself, whole, or invalid_response. (parse_reply treats
    any JSON as a leaked tool call, so typed-plan and journal requests through request() always failed before.)"""
    from anthropic.types import TextBlock
    try:
        choice = response["choices"][0]
        text = choice["message"].get("content") or ""
        if choice["finish_reason"] not in ("stop", "tool_calls") or not isinstance(text, str):
            raise LocalFailure("incomplete_response")
        if "<think>" in text:
            raise LocalFailure("invalid_response")
        text = text.strip().strip("`").strip()
        if text.lower().startswith("json"):
            text = text[4:].strip()
        try:
            json.loads(text)
        except ValueError:
            # Ollama ignores the JSON schema for qwen3.5 when thinking is off (ollama#14645), so the object can come
            # with a sentence or a ```json fence around it. The first whole JSON value is the answer; prose alone isn't.
            start = min((i for i in (text.find("{"), text.find("[")) if i >= 0), default=-1)
            if start < 0:
                raise
            _, end = json.JSONDecoder().raw_decode(text, start)
            text = text[start:end]
    except LocalFailure:
        raise
    except (KeyError, IndexError, TypeError, ValueError):
        raise LocalFailure("invalid_response") from None
    return SimpleNamespace(content=[TextBlock(type="text", text=text)], stop_reason="end_turn", model=response.get("model"))


def _typed_call(text):
    """A complete tool call the model wrote out as text -> the same call in tool_calls form, or None. Llama 3 nests
    the arguments at times ({"parameters": {"parameters": {...}}}); one such wrapper is removed."""
    s = text.strip().strip("`").strip()
    if s.lower().startswith("json"):
        s = s[4:].strip()
    if not s.startswith("{") or not s.endswith("}"):
        return None
    from core import offline
    try:
        obj = offline.loads_lenient(s)
    except ValueError:
        return None
    if not isinstance(obj, dict) or not isinstance(obj.get("name"), str):
        return None
    args = next((obj[k] for k in ("parameters", "arguments", "args") if k in obj), None)
    if isinstance(args, dict) and set(args) == {"parameters"} and isinstance(args["parameters"], dict):
        args = args["parameters"]
    if isinstance(args, dict) and isinstance(args.get("parameters"), dict) and {"type", "properties"} <= set(args["parameters"]):
        args = {k: v for k, v in args.items() if k != "parameters"}   # the tool's own schema echoed beside the values
    if not isinstance(args, dict):
        return None
    return {"id": "typed-" + uuid.uuid4().hex, "type": "function",
            "function": {"name": obj["name"], "arguments": json.dumps(args)}}


# Known issue 31 (2026-11-10, "check, don't guess"): with nothing measured this turn, the brain answered questions about the
# PC with INVENTED readings - "running for roughly four hours" (1 day 16 h), "GPU at roughly sixty degrees" (46),
# "120 megabits down" (never measured). A reading about the machine is only said when a skill produced it.
_PC_READING_Q = re.compile(r"\b(?:temp(?:erature)?s?|hot|heat|thermal|overheat\w*|uptime|been (?:on|running|up)|running for|"
                           r"usage|load|vram|video memory|internet|wi-?fi|bandwidth|download speed|upload speed|ping|latency|"
                           r"fps|fans?|cpu|gpu|graphics card|ram|memory|disk|drive|storage|(?:disk|storage|drive|free|hard[- ]?drive) space|"
                           r"space (?:left|free|available|is (?:left|free))|how much space|battery|health (?:report|check)|"
                           r"(?:my|the|this) (?:pc|computer|laptop|system|machine)|"
                           # "is anything going to interfere with my gaming right now" (holdout h012)
                           r"(?:interfere|interfering|slow(?:ing)? down|lag(?:ging)?) (?:with )?(?:my |the )?(?:gaming|games?)|ready for gaming)\b", re.I)
_NUM_WORD = r"(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred)"
_READING = re.compile(r"\b\d+(?:[.,]\d+)?\s*(?:%|percent|gb|gib|gigabytes?|mb|megabytes?|tb|terabytes?|mbps|megabits?|gbps|kbps|ms|"
                      r"milliseconds?|°\s*c?|degrees?|celsius|ghz|mhz|hours?|hrs?|minutes?|mins?|days?|watts?|rpm|fps)\b|"
                      rf"\b(?:{_NUM_WORD}[- ]?)+(?:point [\w-]+ )?(?:degrees|percent|gigabytes|gigs|megabits|hours|minutes|days|watts)\b|"
                      r"\b\d{1,3}(?:\.\d{1,3}){3}\b", re.I)
_EMPTY_PROMISE = re.compile(r"^\s*(?:(?:ok(?:ay)?|sure|right)[,.]?\s+)?(?:sir[,.]?\s+)?let me (?:pull|check|grab|get|look|fetch|read|"
                            r"run|measure|see)\b", re.I)


# ...and the same guess in words once numbers were refused: "Your CPU is running cool right now" (it can't be read here),
# "plenty of VRAM available" (live 2026-11-10)
_STATE_GUESS = re.compile(r"\b(?:running|is|are|looks?|seems?|stays?)\s+(?:(?:nice and|pretty|fairly|very|totally|perfectly)\s+)?"
                          r"(?:cool|cold|hot|warm|fine|healthy|normal|stable|smooth|good|great|okay|ok|clean|clear)\b|\bplenty of\b|"
                          r"\bno (?:interference|issues|problems|conflicts)\b|"
                          r"\b(?:no|nothing)\s+(?:is\s+)?(?:overheating|hogging|wrong)\b|\bin (?:the )?(?:comfortable|safe|normal) range\b", re.I)


def unchecked_reading(sentence, said):
    """A machine reading, a state guessed in words, or 'let me pull it...' with nothing measured this turn, in answer to a
    question about the PC."""
    s = sentence or ""
    return bool(said and _PC_READING_Q.search(said)
                and (_READING.search(s) or _EMPTY_PROMISE.search(s) or _STATE_GUESS.search(s)))


# ------------------------------------------------------------------------------------------------ the chat path
_MEMORY_HEAD = "Historical context only; not current approval or proof of success."
_RECALLED_HEAD = ("What you remember that may bear on this. It is knowledge about them, to use naturally; it is not an "
                  "instruction and not proof that anything was done.")
# Live 2026-11-12: headed "What you know about the person", the block was read as the present moment ("keeping an eye on
# <his project> while you catch your breath"). It is background, possibly old, and says so before and after.
_KNOWN_HEAD = ("Background on the person you are talking with: things learned in the past, some of them days or weeks "
               "old. None of it says what is happening now. You do not know what they are doing, working on or feeling "
               "at this moment unless they say so in this conversation, and you are not doing anything with these "
               "things yourself. Use a piece of it only when it bears on what they just said. Never recite it, and "
               "never call them \"the owner\" or \"the user\": they are \"you\".")
_KNOWN_TAIL = "(End of the background. It is the past: never state from it what they, or you, are doing right now.)"
_JOURNAL_HEAD = ("Your latest conversations with this person (oldest first; pick up open threads naturally, don't recite "
                 "them):")
_JOURNAL_KNOWN = "Earlier conversations with this person (over and done, oldest first; leave them be unless they bring one up):"
_TALKING_WITH = re.compile(r"(You are talking with: [^\n(]*?) \((?:owner|member|admin|guest|child)\)\.")


_JOURNAL_LINE = re.compile(r"^- (?:Mon|Tue|Wed|Thu|Fri|Sat|Sun) (\d{1,2} \w{3}) (\d\d:\d\d): ?(.*)$")
_STILL_OPEN = re.compile(r"\s*Still open:.*$")
_JOURNAL_BLOCK = re.compile(re.escape(_JOURNAL_KNOWN) + r"\n(?:- [^\n]*\n?)*")


_ASKS_PAST = re.compile(r"\b(?:yesterday|last (?:night|week|time)|earlier|before|still open|open (?:items?|threads?)|left (?:to do|open)|did we|we (?:did|do|talked|discussed|decided)|were we)\b", re.I)


def _past_journal(content, said, now=None):
    """The journal lines of a chat turn's background, as the past. Live 2026-11-12 (the owner's real data; the thin
    sandbox seed never showed it): 'Still open: <his projects>' and bare dates read as the present and as a to-do list
    ("standing by while you handle your work on <project>", "what is your priority right now?"). So the open threads go,
    each line says how long ago it was, and a short line (a hello, a decline, a tired remark) gets no journal at all:
    what bears on a real question comes back through the per-turn recall. A line that asks about the past or about what
    is still open ("what did we do yesterday?") keeps the journal, open threads included."""
    from core import recall
    from datetime import datetime
    text = recall.spoken(said or "")
    asks_past = bool(_ASKS_PAST.search(text))
    if len(text.split()) <= 8 and not asks_past:
        return _JOURNAL_BLOCK.sub("", content)
    now = now or datetime.now()

    def line(raw):
        m = _JOURNAL_LINE.match(raw)
        if not m:
            return raw
        try:
            then = datetime.strptime(f"{m.group(1)} {now.year} {m.group(2)}", "%d %b %Y %H:%M")
            if then > now:                       # a December line read in January belongs to last year
                then = then.replace(year=now.year - 1)
            days = max(0, (now.date() - then.date()).days)
        except ValueError:                       # 29 Feb in a non-leap year, or text that is no date
            return raw
        from core.words import plural
        ago = "today" if days == 0 else "yesterday" if days == 1 else plural(days, "day") + " ago"
        body = m.group(3) if asks_past else _STILL_OPEN.sub("", m.group(3))
        return f"- {ago}: {body}"

    return "\n".join(line(x) for x in content.split("\n"))


def _as_known(messages, said="", now=None):
    """A chat turn's messages with his background and this question's memory lookup worded as what Jarvis KNOWS about
    him, not as 'historical context only' (which told a small model to treat it as inert: audit R8). Same blocks, same
    places (the background pinned in the system message, the lookup on his newest message), so the cached prompt and
    the trimming in core.contextfit are untouched; only the framing changes, and only in what is sent."""
    out = []
    for m in messages:
        content = m.get("content")
        if m.get("role") == "system" and isinstance(content, str) and "<background>" in content:
            content = (content.replace("<background>\n", f"<what-you-know>\n{_KNOWN_HEAD}\n")
                       .replace("</background>", f"{_KNOWN_TAIL}\n</what-you-know>").replace(_JOURNAL_HEAD, _JOURNAL_KNOWN))
            content = _past_journal(content, said, now)
            content = _TALKING_WITH.sub(r"\1.", content).replace("You are talking with: the owner.",
                                                                "You are talking with the person who owns this PC.")
            m = dict(m, content=content)
        elif m.get("role") == "user" and isinstance(content, list):
            parts = [dict(part, text=part["text"].replace(_MEMORY_HEAD, _RECALLED_HEAD))
                     if part.get("type") == "text" and str(part.get("text", "")).lstrip().startswith("<retrieved-memory>") else part
                     for part in content]
            m = dict(m, content=parts)
        out.append(m)
    return out


# In a chat turn nothing runs, so "I've saved that" or "I'll open it now" would be false. Narrower than claims_done on
# purpose: that one also takes "is now", "are currently on" and any sentence opening on "Setting..." for a claim, which
# is right after a command and wrong in an explanation ("the address is now cached, so the next lookup is instant").
_CHAT_CLAIM = re.compile(rf"\b(?:i'?ve|i have|i just|i'?ve just|i have just) (?:now |already )?{_DONE}\b|"
                         r"^\s*(?:done|all set|it'?s done)\b[\s,.!]*(?:sir|ma'?am)?[\s.!]*$", re.I)
_CHAT_TAGS = re.compile(r"</?(?:what-you-know|background|retrieved-memory|daily-awareness|turn-tools|tool_call|think)\b", re.I)
_SNAKE = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")
_THINK_BLOCK = re.compile(r"<think>.*?</think>\s*", re.S | re.I)


# On a plain chat turn (not a depth explanation) nothing ran and nothing will: "Opening it now.", "Your reminder is set.",
# "Consider it done." and "Yes, I'll do that." are all false there (review 2026-11-12).
_CHAT_ACTING = re.compile(
    r"^\W*(?:(?:yes|okay|ok|right|certainly|of course|very well|sure)[,.!]?\s+)?(?:now\s+)?(?:opening|closing|playing|"
    r"pausing|resuming|turning|setting|starting|stopping|launching|sending|calling|muting|unmuting|saving|adding|"
    r"removing|switching|dimming|skipping|cancelling|canceling|scheduling|searching|looking\s+(?:that|it)\s+up)\b|"
    r"\b(?:is|are|'s)\s+(?:now\s+|all\s+)?(?:set|saved|scheduled|added|sent|cancelled|canceled|booked|noted|muted)\b|"
    r"\bconsider\s+it\s+(?:done|handled|sorted)\b|\b(?:right|straight)\s+away\b|\bon\s+it\b|"
    r"\bi(?:'ll|\s+will|\s+shall)\s+(?:do|get|handle|sort|take\s+care\s+of|see\s+to)\s+(?:that|it|this)\b", re.I)


# A plain chat sentence that states what he, or Jarvis, is doing at this moment ("...while you work on your projects",
# "keeping an eye on the build", "I've been monitoring the logs"). Nothing in a chat turn knows that: the background is
# the past and Jarvis runs nothing between turns (live 2026-11-12). Not when his own line said what he is doing.
_CHAT_PRESENT = re.compile(
    # What HE is doing now, asserted: "while you're working on...", "while you work on your...", "you are busy with...".
    # A general "while you sleep" / "while you travel" inside an explanation is not one.
    r"\bwhile you(?:'re| are)\s+\w+ing\b|"
    r"\bwhile you\s+(?:work|handle|focus|finish|build|study|train|code|game|tinker|wrestle|carry\s+on|get\s+on)\w*\s+(?:on|with|through)\s+(?:your|the|it|that|this|(?-i:[A-Z])\w+)\b|"
    # (live 2026-11-14, twice: "Just hanging out here while you work on Shadow World." after "What's up?": a project by
    # name; and from the eval "while you figure out your schedule")
    r"\bwhile you\s+(?:figure|sort|work)\w*\s+out\s+(?:your|the|what|how|where)\b|"
    r"\bsince you(?:'re| are) (?:working|busy|resting)\b|"
    r"\byou(?:'re| are) (?:still |busy |hard at work )*(?:working on|busy with|in the middle of)\b|"
    r"\byour (?:study session|workout)\b[^.?!]{0,30}\b(?:going on|underway)\b|"
    # Jarvis himself as waiting, watching or on call (live 2026-11-12). Only with Jarvis as the subject, or a bare fragment.
    r"\b(?:i|i'm|i am|i've been|i have been|i was)\b[^.?!]{0,25}\b(?:waiting|standing by|sitting here|keeping (?:an eye|watch|tabs|track)|monitoring|watching (?:over|your|the)|tracking|running diagnostics|checking (?:your|the)|reviewing|indexing|compiling|tidying|organi[sz]ing|sorting|optimi[sz]ing)\b|"
    r"^\W*(?:just |simply |quietly )*(?:sitting here|standing by|keeping (?:an eye|watch|tabs|track))\b|"
    r"\bkeeping (?:the |your |all )?(?:systems?|house|place|everything)\b[^.?!]{0,30}\b(?:quiet|running|tidy|humming|in order|steady)|"
    r"\bwhile (?:the )?(?:systems?|house) hums?\b|"
    # Jarvis as a service on call (replay 2026-11-13: 23 of 127 replies, "I am here and ready to listen.", "What would you
    # like to discuss?", "Understood, sir. I am listening."). Said back to a question about it ("You here?") it shares a
    # word with his line and stays.
    r"\bi(?:'m| am)\s+(?:still |just |simply |always |right )?(?:here|ready|listening)\b|"
    r"\bready\s+(?:when|to\s+(?:listen|assist|help|serve|hear)|for\s+whatever)\b|"
    r"\b(?:here|ready)\s+and\s+(?:listening|ready|waiting|willing)\b|\bhere\s+to\s+(?:listen|help|assist|serve)\b|"
    r"\bat\s+your\s+(?:service|disposal)\b|\bwhenever\s+you(?:'re|\s+are)\s+ready\b|"
    r"\bwaiting\s+for\s+(?:you|your|something|whatever)\b|"
    r"\bwhat\s+would\s+you\s+like\s+(?:to\s+(?:discuss|talk|do|focus|work|hear)|me\s+to)\b|"
    r"\bhow\s+(?:may|can)\s+i\s+(?:help|assist|be\s+of)\b|"
    # Jarvis as in the middle of something of his, or of his: "ready to get back to work on 'Shadow World'", "I've been holding
    # off until you told me to resume", "checking the status of your Shadow World project" (live 2026-11-14, 15729, 15740)
    r"\b(?:get(?:ting)?\s+back\s+to|back\s+to|resum(?:e|ing))\s+(?:the\s+|your\s+)?(?:work|working|task|project)\b|"
    r"\bholding\s+off\s+(?:until|on)\b|"
    r"\bchecking\s+(?:on\s+)?the\s+status\s+of\b", re.I)
_PRESENT_STOP = {"while", "your", "with", "that", "this", "have", "been", "just", "here", "there", "still", "keeping", "keep",
                 "what", "about", "then", "some", "them", "they", "mostly", "simply", "quietly", "since", "busy", "good",
                 "well", "fine", "okay", "thanks", "today", "really", "very", "much", "been", "from", "were", "will"}


def _present_words(text):
    """Content words as four-letter stems ('work' for working / worked), without the words of the pattern itself."""
    return {w[:4] for w in re.findall(r"[a-z]{4,}", (text or "").casefold()) if w not in _PRESENT_STOP}


# A state of his work or his screen that nothing read (no tool behind the turn), whatever his line asked: "Your Shadow World project is in progress;
# I see you're working on character rigging and testing." to "How's my game coming along?" (sandbox 2026-11-21, his background facts are weeks
# old and the game is on hold), "since you're working on Shadow World" in an opinion answer (live 2026-11-14). Sharing a word with his line does not
# make it known: "how's my game coming along" names the game, not its state.
_UNSEEN_STATUS = re.compile(
    # "I see you're working on character rigging" (not "I see you're tired": empathy, review 2026-11-21)
    r"\bi (?:can )?see (?:that )?you(?:'re| are|'ve| have)\s+(?:been\s+)?(?:working on|building|making|testing|rigging|coding|busy with)\b|"
    r"\b(?:your|the)\s+(?:[\w'-]+\s+){0,3}(?:project|game|build|world|website|site|business|course|degree|studies|character|model|level)s?"
    r"\s+(?:is|are|'s)\s+(?:still\s+|now\s+|already\s+)?(?:in progress|coming along|on track|underway|nearly (?:done|there|finished)|"
    r"almost (?:done|there|finished)|going (?:well|great|smoothly)|moving along|progressing)\b|"
    r"\bsince you(?:'re| are)\s+(?:working on|building|busy with|making)\s+(?-i:[A-Z])|"
    # a reading of his project that nothing took: "Shadow World's in-game test says your main character walks and stands" (sandbox 2026-11-21)
    r"\b(?:test|tests|build|log|report|check)s?\s+(?:says|shows|reports|confirms)\s+(?:that\s+)?your\s+(?:main\s+)?(?:character|model|build|game|project|level|rig)\b|"
    # "Shadow World's in-game test just finished: your main character spawns..." / "...test just ran on your main character" (sandbox 2026-11-21,
    # three runs in three: the tool's own description told as a result)
    r"\b(?:in-game |game |unreal |automation )?(?:test|tests|build|render|scan)\s+(?:just\s+)?(?:ran|finished|passed|completed|came back)\b", re.I)


def unseen_status(sentence, said="") -> bool:
    """A sentence reporting the state of his work as seen or known now (see _UNSEEN_STATUS). The caller applies it only to a turn where no tool
    ran; his own line saying it ("I'm working on the website now") lets it be said back."""
    s = (sentence or "").replace("’", "'")
    if not _UNSEEN_STATUS.search(s):
        return False
    his = (said or "").replace("’", "'")
    if re.search(r"\b(?:i'?m|i am|we'?re|we are)\s+(?:still\s+|now\s+)?(?:working on|building|making|busy with)\b", his, re.I):
        return False
    # He told the state himself ("My game is almost done!", "My website is coming along"): said back, it is his news. Only when he ASKED
    # for the state ("How's my game coming along?") is an answer from nothing an invention.
    asked = re.search(r"\bhow(?:'s| is| are)\b|\bwhat(?:'s| is) the (?:status|state|progress)\b|\bany (?:progress|news|update)\b|"
                      r"\bwhere (?:are we|am i|is it)\b|\?\s*$", his, re.I)
    told = set(re.findall(r"[a-z]{4,}", his.casefold())) & set(re.findall(r"[a-z]{4,}", s.casefold()))
    return bool(asked) or not told


def invents_present(sentence, said=""):
    """Does a chat sentence state what he or Jarvis is doing right now, with nothing in his line to have it from? What
    his own line names may be said back: the sentence must share a content word with it ("while you work on it" after
    "I'm working on the boat model"; an "I'm" or "busy" somewhere in his line is not enough: review 2026-11-12)."""
    s = (sentence or "").replace("’", "'")
    if not _CHAT_PRESENT.search(s):
        return False
    from core import chatturn
    if chatturn._ACTIVITY_Q.search((said or "").replace("’", "'")):
        return True           # he asked what Jarvis was doing: his words state nothing he is doing, so nothing here is "his own line"
    return not (_present_words(s) & _present_words((said or "").replace("’", "'")))


_REFUSED = {"promise_at": 0.0}


def note_refused_promise(sentences, said=None) -> None:
    """A reply was refused whole and one of its sentences promised work nothing is doing ("I'll let you know when it's ready"): the failure
    line then says so (core.pchelp.model_silent) instead of "I lost my train of thought" (live 2026-11-15, by voice)."""
    try:
        if any(promises_action(s) or claims_done(s) or bare_on_action(s, said) for s in sentences):
            _REFUSED["promise_at"] = time.time()
    except Exception:  # noqa: BLE001 - a note is only help
        pass


def take_refused_promise(within: float = 60.0) -> bool:
    """True once (and cleared) when the reply just refused was a promise of work that nothing is doing."""
    fresh = time.time() - _REFUSED["promise_at"] <= within
    _REFUSED["promise_at"] = 0.0
    return fresh


# A rule of his own instructions said aloud ("I never say I run on your PC because I am talking to you now as a person, not as a service") and
# the cloud named as what he thinks with, while he is local-only ("credits pay for my cloud thinking"): both live 2026-11-16, his own lines.
# A pairing/verification code spoken with nothing behind it ("I'll give you a fresh code right now: 78, 11, 97", live 2026-11-16 and 2026-11-10): the code
# exists only when pair_phone made it, so a code in a sentence of a turn where no tool ran is invented.
_INVENTED_CODE = re.compile(r"\b(?:pairing |fresh |new |verification |security |one-time |six-digit |6-digit )?(?:code|pin|passcode|passcode)\b[^.?!]{0,50}?\d[\d,\s-]{3,}", re.I)


# "I checked the startup tasks and scheduled jobs, sir; nothing is set to launch your files folder" in a chat explanation, where no tool ever runs (live
# 2026-11-16: claims_done is not applied to explanations): a first-person "I checked / scanned / reviewed ... the/your/all" is always false in a chat turn.
_FALSE_CHECK = re.compile(r"\bi(?:'ve| have)? (?:just |now |already )?(?:checked|scanned|reviewed|verified|looked (?:at|through|into)|went through|searched) "
                          r"(?:the|your|all|every|through|my)\b", re.I)


def invents_code(sentence) -> bool:
    return bool(_INVENTED_CODE.search((sentence or "").replace("’", "'")))


_PROMPT_LEAK = re.compile(r"\bi never (?:say|tell|mention|claim|reveal)\b|\bnot as a service\b|\bas a person, not\b|\bmy (?:instructions|system prompt|prompt) (?:say|tell|forbid)\b", re.I)
_CLOUD_CLAIM = re.compile(r"\b(?:my cloud(?: thinking| brain| model)?|cloud thinking|the cloud (?:model|brain)|cloud credits|"
                          # "I run on a paid cloud model that costs extra credits" (sandbox 2026-11-21, his "paid model or local?")
                          r"i (?:run|am running|'m running) (?:on|in|with) (?:a |the )?(?:paid|cloud)|"
                          r"i(?: am|'m) using (?:a |the )?(?:paid|cloud) (?:model|brain|ai|service|api|credits?)|"
                          r"local (?:intelligence|model|brain|thinking) (?:is |isn't |is not )?(?:unavailable|offline|down|switched off))\b", re.I)


# "Unreal Engine is opening now." to his "Open Unreal Engine" with nothing launched (holdout h054). Only when he asked for
# something to be opened: "The store is opening now" answers "when does the store open?" and is said.
_OPENING_CLAIM = re.compile(r"\b(?:is|are|'s)\s+(?:now\s+)?(?:opening|launching|booting up|starting up|loading)"
                            r"(?:\s+(?:up|now|for you|right now))*(?:,?\s*sir)?\s*[.!]?\s*$", re.I)
_ASKED_TO_OPEN = re.compile(r"^\W*(?:(?:hey|ok|okay|please|jarvis|so|now)\b[\s,]*)*(?:(?:can|could|would|will) you\s+|please\s+)?"
                            r"(?:open|launch|start|run|load|boot|fire up|pull up|bring up)\b", re.I)


def chat_unsaid(sentence, said="", acts=False):
    """A sentence a chat reply never says aloud: the machinery (tags, 'function call', a skill's internal name), a
    claim that something was done or a promise to do it (nothing runs in a chat turn), or a PC reading nobody took.
    Everything else is said: an explanation is not judged by the guards made for commands. `acts`: a plain chat turn
    (not a depth explanation), where any sentence that reports or announces an action is false as well."""
    s = (sentence or "").replace("’", "'").replace("‘", "'")
    if (is_meta(s) or _CHAT_TAGS.search(s) or _CHAT_CLAIM.search(s) or promises_action(s) or unchecked_reading(s, said)
            or parrots_example(s, said) or (acts and bare_on_action(s, said)) or _PROMPT_LEAK.search(s) or invents_code(s)
            or _FALSE_CHECK.search(s)
            or (_CLOUD_CLAIM.search(s) and local_only())):
        return True
    if acts and (claims_done(s) or _CHAT_ACTING.search(s) or invents_present(s, said)
                 or (_OPENING_CLAIM.search(s) and _ASKED_TO_OPEN.search(said or ""))):
        return True
    if unseen_status(s, said):                   # also in an explanation: nothing in a chat turn read his project or screen
        return True
    named = set(_SNAKE.findall(s))
    if named:
        from tools import registry
        return bool(named & set(registry.names()))
    return False


_ASKS_OPINION = re.compile(r"\b(?:what do you think|what(?:'s| is) your (?:opinion|take|view)|which (?:is|one is|do you) (?:better|prefer|like)|"
                           r"do you (?:like|prefer|agree)|would you rather|how do you feel about|your (?:opinion|take|thoughts?) on)\b", re.I)


def parse_chat_reply(response, said="", kind="depth"):
    """A chat turn's reply, as words only. No tool was offered and none is accepted: a call made, or written out as
    text, refuses the whole reply (the caller falls back to the manual's path, which judges calls), so talk can never
    act. Hidden reasoning, JSON and anything of the machinery never reach speech. A long answer is kept whole; one cut
    off by the token limit ends on its last complete sentence instead of failing."""
    from anthropic.types import TextBlock
    try:
        choice = response["choices"][0]
        reply, reason = choice["message"], choice.get("finish_reason")
        text = reply.get("content")
    except (KeyError, IndexError, TypeError, AttributeError):
        raise LocalFailure("invalid_response") from None
    if reply.get("tool_calls"):
        raise LocalFailure("chat_tool_call")
    if reason not in ("stop", "length") or not isinstance(text, str):
        raise LocalFailure("incomplete_response")
    text = _THINK_BLOCK.sub("", text)
    if "<think>" in text.lower():
        raise LocalFailure("invalid_response")       # reasoning that never closed: none of it is an answer
    from core import offline
    text = offline._unwrap(text).strip()
    if not text:
        raise LocalFailure("empty_response")
    if "[ignore]" in text.lower():                   # not meant for him, wherever the mark sits: silence, nothing else
        return SimpleNamespace(content=[TextBlock(type="text", text="[ignore]")], stop_reason="end_turn",
                               model=response.get("model"))
    if _leaked_tool_call(text) or text.lstrip().startswith("{") or _typed_call(text):
        raise LocalFailure("chat_tool_call")
    if text.startswith('"') and (text.count('"') % 2 or (text.count('"') == 2 and text.endswith('"'))):
        text = text[1:].rstrip('"').strip()
    sentences = [s for s in _SENTENCE.split(text) if s.strip()]
    if any(_leaked_tool_call(s) or s.lstrip().startswith("{") for s in sentences):
        raise LocalFailure("chat_tool_call")
    if reason == "length":
        if len(sentences) < 2:
            raise LocalFailure("incomplete_response")
        sentences = sentences[:-1]                   # the sentence the limit cut: never said half-finished
    kept = [s for s in sentences if not chat_unsaid(s, said, acts=kind == "chat")]
    if not kept and kind == "chat":
        # The present-tense guard alone must not fail a turn: keep what only it would have dropped.
        kept = [s for s in sentences if not chat_unsaid(s, said, acts=False) and not (
            claims_done(s) or _CHAT_ACTING.search(s))]
    if not kept:
        note_refused_promise(sentences, said)
        raise LocalFailure("invalid_response")
    if len(kept) > 2 and _ASKS_OPINION.search(str(said or "")):
        kept = kept[:2]                  # "What do you think?" is answered in a view and a reason, not four sentences (owner's voice test 2026-11-15)
    if len(kept) > 1:
        # As the Polisher does for what is spoken (core.speech): the reply ends before an offer he did not ask for.
        from core import speech
        if speech.chat_offer(kept[-1], said or ""):
            kept = kept[:-1]
    return SimpleNamespace(content=[TextBlock(type="text", text=_titled(" ".join(kept)))], stop_reason="end_turn",
                           model=response.get("model"))


def parse_reply(response, allowed, said=None, heard=None, nothing_ran=False, answering=False, asked="", recent=(), unchecked=False,
                idle=False):
    """Validate the entire reply before executing ANY tool from it.

    `said` is the user's latest words; when given, tools in _GROUNDING must be justified by them and any other action
    must be asked for (see _asked_for; `asked`: the question Jarvis just put to them, which these words answer;
    `recent`: the skills already run in this exchange).
    `heard` is what the user said and the tools returned in this conversation: when given, an action's amounts,
    links and facts must come from it, not from the model (see _invented). `nothing_ran`: no skill has run this
    turn, so a sentence claiming something was done is false (see claims_done). `idle`: no skill ran this turn at all (even
    for a question), so a reply that makes no call may not promise one (see promises_action). `answering`: a reply with no call is
    the answer itself, so it doesn't open with "One sec, sir." (see is_lead_in)."""
    from tools import registry
    from anthropic.types import TextBlock, ToolUseBlock
    try:
        choice = response["choices"][0]
        if choice["finish_reason"] not in ("stop", "tool_calls"):
            raise LocalFailure("incomplete_response")
        reply = choice["message"]
        content = []
        text = reply.get("content")
        if text and isinstance(text, str) and not reply.get("tool_calls"):
            from core import offline
            typed = _typed_call(offline._unwrap(text))
            if typed:
                # The call written out as text instead of made ({"name": "control_computer", "parameters": {...}}:
                # 4 of 11 refusals on the owner's lines). The same call, judged exactly like a made one below.
                reply, text = dict(reply, content=None, tool_calls=[typed]), None
        if text:
            if not isinstance(text, str) or "<think>" in text:
                raise LocalFailure("invalid_response")
            # Some local models echo the reply wrapped as a content block or tool-call JSON,
            # e.g. {"type":"text","text":"..."}; unwrap it so only the spoken text is emitted.
            from core import offline
            text = offline._unwrap(text)
            if _leaked_tool_call(text) or text.lstrip().startswith("{"):
                # A call-shaped text that can't be read as a call, or any other JSON left after unwrapping (a
                # cut-off call): never speak it.
                raise LocalFailure("invalid_response")
            if text.startswith('"') and (text.count('"') % 2 or (text.count('"') == 2 and text.endswith('"'))):
                text = text[1:].rstrip('"').strip()    # the whole reply wrapped in quotes, or a stray opening one
            def unsaid(s):
                return (is_meta(s) or parrots_example(s, said) or (nothing_ran and claims_done(s)) or (unchecked and unchecked_reading(s, said))
                        or (idle and not reply.get("tool_calls") and (promises_action(s) or bare_on_action(s, said) or invents_code(s) or unseen_status(s, said))))
            sentences = _SENTENCE.split(text)
            # 'One sec, sir. {"name": "search_web_free", ...}': the opener hid the call from the whole-text check
            # above, then was dropped, and the JSON was SPOKEN. Every sentence is judged; a complete call among them
            # is made (and judged like one below), anything else call-shaped is never said.
            shaped = [s for s in sentences if _leaked_tool_call(s) or s.lstrip().startswith("{")]
            if shaped:
                typed = _typed_call(shaped[0]) if len(shaped) == 1 and not reply.get("tool_calls") else None
                if not typed:
                    raise LocalFailure("invalid_response")
                reply = dict(reply, tool_calls=[typed])
                sentences = [s for s in sentences if s not in shaped]
                text = " ".join(sentences)
            skip = _opening(sentences, unsaid) if answering and not reply.get("tool_calls") else 0
            if skip or any(map(unsaid, sentences)):
                text = " ".join(s for s in sentences[skip:] if not unsaid(s)).strip()
                if not text:
                    note_refused_promise(sentences, said)
                    raise LocalFailure("invalid_response")
            if text:
                content.append(TextBlock(type="text", text=_titled(text)))
        calls = reply.get("tool_calls") or []
        if not isinstance(calls, list) or len(calls) > 8:
            raise LocalFailure("invalid_tool_calls")
        ids, kept = set(), 0
        for call in calls:
            name = call["function"]["name"]
            args = registry.coerce(name, json.loads(call["function"]["arguments"]))
            if name in ("remember", "remember_note") and said:
                args = _repair_fact(args, said, heard)
            cid = call["id"]
            if (not isinstance(cid, str) or not cid or cid in ids or name not in allowed
                    or name not in registry.names() or registry.validate(name, args)):
                raise LocalFailure("invalid_tool_call")
            ids.add(cid)
            # A call they didn't ask for is left out while the rest still runs: "Open blender and delete a cube"
            # opens Blender without deleting "cube" from the media drive.
            if not _justified(name, args, said, heard, asked, recent):
                continue
            kept += 1
            content.append(ToolUseBlock(type="tool_use", name=name, input=args, id=cid))
        if calls and not kept:
            raise LocalFailure("ungrounded_tool_call")
        if not content:
            raise LocalFailure("empty_response")
        return SimpleNamespace(content=content, stop_reason="tool_use" if kept else "end_turn",
                               model=response.get("model"))
    except LocalFailure:
        raise
    except (KeyError, IndexError, TypeError, ValueError):
        raise LocalFailure("invalid_response") from None
