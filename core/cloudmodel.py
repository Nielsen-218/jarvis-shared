"""Cloud fallback brain — Google Gemini via its OpenAI-compatible endpoint.

This tier is tried only *after* the local models decline or fail, and *before*
the paid Claude turn, so JARVIS stays local-first → Gemini → Claude. It is
opt-in: with no GEMINI_API_KEY in .env the tier is skipped entirely, leaving the
previous local→Claude behaviour unchanged.

The request/response shape is the same OpenAI chat-completions format the local
adapter already speaks, so a reply flows straight through intelligence.parse_reply
(including its tool-call validation against the registry).
"""
import json
import os
import time
import urllib.error
import urllib.request

# Gemini exposes an OpenAI-compatible surface; only an explicit https endpoint is allowed.
DEFAULT_URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
# Not "gemini-flash-latest": on this key's tier it never answered (2026-10-23: 0 of 25 Jarvis attempts, all 503s,
# timeouts and the cooldown after them; 3 of 3 test calls timed out at 20 s) while gemini-3.5-flash answered 3 of 3
# in ~1 s. JARVIS_GEMINI_MODEL still overrides it.
DEFAULT_MODEL = "gemini-3.5-flash"


def provider() -> str:
    """Which cloud brain answers when the local model cannot: "claude" (the owner's API credit, 2026-11-21: "use the
    API credit as one of Jarvis's brains for harder questions and tasks"), "gemini", or "off"."""
    return os.getenv("JARVIS_CLOUD_FALLBACK", "gemini").lower()


def available():
    """True when the owner has configured a key and not turned the tier off."""
    from core.intelligence import local_only
    if local_only():
        return False
    if provider() == "claude":
        return bool(os.getenv("ANTHROPIC_API_KEY")) or _via_pc()
    return bool(os.getenv("GEMINI_API_KEY")) and provider() != "off"


def _via_pc() -> bool:
    """The laptop (phone turns) asks the PC to make the Claude call (/api/pc/cloud): one key, one budget, on the PC."""
    try:
        from core import pcbridge
        return not pcbridge.local_pc() and bool(os.getenv("JARVIS_PC_URL"))
    except Exception:  # noqa: BLE001
        return False


def _model():
    return os.getenv("JARVIS_GEMINI_MODEL", DEFAULT_MODEL)


# Gemini's free tier regularly answers 503 "high demand" or stalls under load; with a 90 s timeout
# that left a voice turn hanging ~50 s before Claude took over. Keep waits short, retry a fast
# transient error once, and after a capacity failure skip this tier for a while.
TIMEOUT = {"fast": 12, "advanced": 20}
TRANSIENT = (429, 500, 502, 503, 504)
COOLDOWN_SECONDS = 180
_cooldown_until = 0.0


def _trip():
    global _cooldown_until
    _cooldown_until = time.monotonic() + COOLDOWN_SECONDS


def cooling_down() -> bool:
    return time.monotonic() < _cooldown_until


def _normalize(result):
    """Make Gemini's reply satisfy parse_reply: lowercase finish_reason, and give
    every tool call a stable id and JSON-string arguments."""
    for choice in result.get("choices", []):
        if isinstance(choice.get("finish_reason"), str):
            choice["finish_reason"] = choice["finish_reason"].lower()
        message = choice.get("message") or {}
        for i, call in enumerate(message.get("tool_calls") or []):
            if not call.get("id"):
                call["id"] = "gemini-" + str(i)
            fn = call.get("function") or {}
            if not isinstance(fn.get("arguments"), str):
                fn["arguments"] = json.dumps(fn.get("arguments") or {})
    return result


def complete(payload, cancelled=lambda: False):
    """Run one chat completion on Gemini and return the OpenAI-shaped response dict.

    Raises intelligence.LocalFailure (the same fixed diagnostic the local path
    uses) on any problem, so the caller falls through to Claude without leaking
    provider text, credentials or prompts.
    """
    from core.intelligence import LocalFailure, local_only

    if local_only():
        raise LocalFailure("local_only_no_cloud")
    if provider() == "claude":
        return claude_complete(payload, cancelled)

    key = os.getenv("GEMINI_API_KEY")
    if not key:
        raise LocalFailure("cloud_provider_unconfigured")
    if cancelled():
        raise LocalFailure("cancelled")
    url = os.getenv("JARVIS_GEMINI_URL", DEFAULT_URL)
    if not url.startswith("https://"):
        raise LocalFailure("cloud_endpoint_insecure")
    role = payload.get("role")
    body = {"model": _model(), "messages": payload["messages"], "temperature": 0.2,
            "max_tokens": 1536 if role == "fast" else 3072, "stream": False,
            # Disable/limit Gemini "thinking" so the token budget is spent on the
            # spoken reply (otherwise it truncates to finish_reason="length").
            "reasoning_effort": "none" if role == "fast" else "low"}
    if payload.get("tools"):
        body["tools"] = payload["tools"]
    if payload.get("response_format"):
        body["response_format"] = payload["response_format"]
    data = json.dumps(body).encode()
    if len(data) > 6 * 1024 * 1024:
        raise LocalFailure("context_too_large")
    if cooling_down():
        raise LocalFailure("cloud_cooling_down")
    request = urllib.request.Request(url, data=data, headers={
        "Authorization": "Bearer " + key, "Content-Type": "application/json"})
    timeout = TIMEOUT.get(role, TIMEOUT["advanced"])
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read(2000001)
            break
        except urllib.error.HTTPError as error:  # only the status code is kept: no provider text
            if attempt == 1 and error.code in TRANSIENT and not cancelled():
                time.sleep(1.0)
                continue
            if error.code in TRANSIENT:
                _trip()
            raise LocalFailure(f"cloud_http_{error.code}") from None
        except Exception as error:
            reason = getattr(error, "reason", error)
            if isinstance(error, TimeoutError) or isinstance(reason, TimeoutError):
                _trip()
                raise LocalFailure("cloud_timeout") from None
            raise LocalFailure("cloud_request_failed") from None
    if len(raw) > 2000000:
        raise LocalFailure("response_too_large")
    try:
        return _normalize(json.loads(raw))
    except ValueError:
        raise LocalFailure("invalid_response") from None


# ------------------------------------------------------------------------------------------------ Claude (the API credit)
CLAUDE_MODEL = "claude-opus-5-5"
CLAUDE_TIMEOUT = {"chat": 20.0, "plan": 30.0}     # a voice turn: never a minute of silence (one try, no retry)
_UNSUPPORTED = ("maxLength", "minLength", "maxItems", "minItems", "minimum", "maximum", "multipleOf")
_client = None


def claude_model() -> str:
    return os.getenv("JARVIS_CLOUD_MODEL", CLAUDE_MODEL)


def claude_schema(schema):
    """The JSON schema as Claude's structured outputs take it: no length/number constraints, oneOf -> anyOf. The
    caller validates the reply itself (gateway.validate), so dropping the caps loses nothing."""
    if isinstance(schema, list):
        return [claude_schema(s) for s in schema]
    if not isinstance(schema, dict):
        return schema
    out = {}
    for key, value in schema.items():
        if key in _UNSUPPORTED:
            continue
        out["anyOf" if key == "oneOf" else key] = claude_schema(value)
    return out


def _data_url(url: str):
    if not url.startswith("data:") or ";base64," not in url:
        return None
    head, data = url[5:].split(";base64,", 1)
    return {"type": "image", "source": {"type": "base64", "media_type": head, "data": data}}


def to_claude(messages, with_tools=True):
    """The OpenAI-shaped local messages (intelligence.messages_for_local) -> (system text, Claude messages). Without tools
    in the request (a chat answer, a plan), earlier tool calls and results become plain text: the API refuses tool blocks
    a request defines no tools for. In a user turn the tool results come first, as the API requires."""
    system, out = [], []

    def add(role, blocks):
        if not blocks:
            return
        if out and out[-1]["role"] == role:
            out[-1]["content"].extend(blocks)
        else:
            out.append({"role": role, "content": list(blocks)})
    for m in messages or []:
        role, content = m.get("role"), m.get("content")
        if role == "system":
            if isinstance(content, str) and content.strip():
                system.append(content)
            continue
        if role == "tool":
            if with_tools and m.get("tool_call_id"):
                add("user", [{"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": str(content or "")}])
            else:
                add("user", [{"type": "text", "text": f"(tool result: {str(content or '')[:4000]})"}])
            continue
        blocks = []
        if isinstance(content, str):
            if content.strip():
                blocks.append({"type": "text", "text": content})
        elif isinstance(content, list):
            for part in content:
                if part.get("type") == "text" and part.get("text", "").strip():
                    blocks.append({"type": "text", "text": part["text"]})
                elif part.get("type") == "image_url":
                    image = _data_url((part.get("image_url") or {}).get("url", ""))
                    if image:
                        blocks.append(image)
        for call in m.get("tool_calls") or []:
            fn = call.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except ValueError:
                args = {}
            if with_tools and call.get("id") and fn.get("name"):
                blocks.append({"type": "tool_use", "id": call["id"], "name": fn["name"], "input": args})
            else:
                blocks.append({"type": "text", "text": f"(used {fn.get('name', 'a tool')} with {json.dumps(args)[:400]})"})
        add("assistant" if role == "assistant" else "user", blocks)
    for m in out:                                    # results first in a user turn
        if m["role"] == "user":
            m["content"].sort(key=lambda b: 0 if b.get("type") == "tool_result" else 1)
    if out and out[0]["role"] != "user":
        out.insert(0, {"role": "user", "content": [{"type": "text", "text": "(conversation continues)"}]})
    return "\n\n".join(system), out


def _openai_shape(message) -> dict:
    """Claude's reply -> the OpenAI-shaped dict the local parsers read (intelligence.parse_reply / _structured)."""
    text, calls = [], []
    for block in message.content:
        if block.type == "text":
            text.append(block.text)
        elif block.type == "tool_use":
            calls.append({"id": block.id, "type": "function",
                          "function": {"name": block.name, "arguments": json.dumps(block.input)}})
    finish = {"end_turn": "stop", "tool_use": "tool_calls", "max_tokens": "length"}.get(message.stop_reason, "stop")
    row = {"role": "assistant", "content": "".join(text)}
    if calls:
        row["tool_calls"] = calls
    usage = message.usage
    return {"model": message.model, "choices": [{"index": 0, "message": row, "finish_reason": finish}],
            "usage": {"prompt_tokens": usage.input_tokens, "completion_tokens": usage.output_tokens,
                      "cache_read_tokens": getattr(usage, "cache_read_input_tokens", 0) or 0}}


def claude_complete(payload, cancelled=lambda: False):
    """One Claude call for a payload the local path built (chat, a tool turn, or a JSON plan). Returns the OpenAI-shaped
    dict like Gemini's tier. Every call is metered (core.usage) and logged as an API assist (core.apiassist)."""
    from core.intelligence import LocalFailure
    from core import usage
    import anthropic
    global _client
    if _via_pc():
        from core import pcbridge
        if cancelled():
            raise LocalFailure("cancelled")
        try:
            return pcbridge.json_request("/api/pc/cloud", "POST", payload)
        except pcbridge.PCError:
            raise LocalFailure("pc_cloud_unavailable") from None
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise LocalFailure("cloud_provider_unconfigured")
    if usage.over_budget():
        raise LocalFailure("cloud_budget_reached")
    if cancelled():
        raise LocalFailure("cancelled")
    fmt = payload.get("response_format")
    system, messages = to_claude(payload.get("messages"), with_tools=bool(payload.get("tools")) and not fmt)
    if not messages:
        raise LocalFailure("invalid_request")
    fmt = payload.get("response_format")
    kind = "plan" if fmt else "chat"
    body = {"model": claude_model(), "max_tokens": 4096, "messages": messages,
            "output_config": {"effort": os.getenv("JARVIS_CLOUD_EFFORT", "low")},
            "cache_control": {"type": "ephemeral"}}
    if system:
        body["system"] = system
    if fmt:
        schema = ((fmt.get("json_schema") or {}).get("schema")) or {}
        body["output_config"]["format"] = {"type": "json_schema", "schema": claude_schema(schema)}
    elif payload.get("tools"):
        body["tools"] = [{"name": t["function"]["name"], "description": t["function"].get("description", "")[:1000],
                          "input_schema": t["function"].get("parameters") or {"type": "object", "properties": {}}}
                         for t in payload["tools"] if t.get("function", {}).get("name")]
    try:
        if _client is None:
            _client = anthropic.Anthropic(max_retries=0)
        message = _client.with_options(timeout=CLAUDE_TIMEOUT[kind]).beta.messages.create(
            betas=["server-side-fallback-2026-07-01"], fallbacks="default", **body)
    except anthropic.APITimeoutError:
        raise LocalFailure("cloud_timeout") from None
    except anthropic.APIStatusError as error:       # only the status code is kept: no provider text
        raise LocalFailure(f"cloud_http_{error.status_code}") from None
    except anthropic.APIConnectionError:
        raise LocalFailure("cloud_request_failed") from None
    if message.stop_reason == "refusal":
        raise LocalFailure("cloud_refused")
    cost = usage.record(message.model, message.usage, component="cloud_brain_" + kind)
    out = _openai_shape(message)
    out["usd"] = cost
    return out
