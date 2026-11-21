"""Tool registry: decorate a function with @tool and Jarvis can call it.

The API allows at most 20 "strict" tools (arguments guaranteed to match the
schema), so strict is opt-in for the tools where a malformed call would do real
harm. Every other call is checked here before it runs (validate()).
"""
import ast
import json
import re
import threading
from contextlib import contextmanager
from typing import Callable

_TOOLS: dict[str, dict] = {}
_TYPES = {"string": str, "integer": int, "number": (int, float), "boolean": bool,
          "array": list, "object": dict}


def tool(description: str, properties: dict | None = None, required: list[str] | None = None,
         slow: bool = False, strict: bool = False, direct: bool = False):
    """Register a function as a Claude tool. Its name becomes the tool name.

    slow=True: Jarvis says he's working on it while the tool runs.
    strict=True: schema-guaranteed arguments (max 20 tools; use for risky actions).
    direct=True: the result is already the spoken answer; when a turn calls only direct tools, Jarvis says
    their results as they are instead of asking the model to re-phrase them.
    """
    properties = properties or {}

    def decorator(fn: Callable) -> Callable:
        schema = {
            "name": fn.__name__,
            "description": description,
            "input_schema": {
                "type": "object",
                "properties": properties,
                "required": required if required is not None else list(properties),
                "additionalProperties": False,
            },
        }
        if strict and properties:
            schema["strict"] = True
        _TOOLS[fn.__name__] = {"fn": fn, "slow": slow, "direct": direct, "schema": schema}
        return fn

    return decorator


def schemas() -> list[dict]:
    return [t["schema"] for t in _TOOLS.values()]


def names() -> list[str]:
    return list(_TOOLS)


def is_slow(name: str) -> bool:
    return bool(_TOOLS.get(name, {}).get("slow"))


def is_direct(name: str) -> bool:
    return bool(_TOOLS.get(name, {}).get("direct"))


def _type_ok(value, type_name: str) -> bool:
    expected = _TYPES.get(type_name)
    if expected is None:
        return True
    if isinstance(value, bool) and type_name in ("integer", "number"):
        return False                                    # True/False are ints in Python, never numbers here
    if type_name == "integer" and isinstance(value, float):
        return value.is_integer()                       # 3.0 from a model is still a whole number
    return isinstance(value, expected)


def _check(value, spec: dict, path: str) -> str | None:
    """Validate one value against its schema, recursing into arrays and objects. The first problem wins,
    reported with a precise path (e.g. days[0].meals[1].meal) so the model can fix exactly that argument."""
    t = spec.get("type")
    if t and not _type_ok(value, t):
        return f"'{path}' must be of type {t}"
    if "enum" in spec and value not in spec["enum"]:
        return f"'{path}' must be one of: {', '.join(map(str, spec['enum']))}"
    if t == "array" and isinstance(spec.get("items"), dict):
        for i, item in enumerate(value):
            problem = _check(item, spec["items"], f"{path}[{i}]")
            if problem:
                return problem
    if t == "object" and isinstance(spec.get("properties"), dict):
        props = spec["properties"]
        missing = [k for k in spec.get("required", []) if k not in value]
        if missing:
            return f"'{path}' is missing: {', '.join(missing)}"
        for key, sub in value.items():
            if key in props:
                problem = _check(sub, props[key], f"{path}.{key}")
                if problem:
                    return problem
    return None


_TRUE, _FALSE = {"true", "yes", "on", "1"}, {"false", "no", "off", "0"}
_NOTHING = {"null", "none", "nil", "undefined", "n/a"}       # a model's spelling of "no value" for an optional argument
_SAME = {"skip": "next", "forward": "next", "back": "previous", "prev": "previous", "resume": "play", "unpause": "play",
         "unmute": "mute"}   # everyday words for an allowed option, used only when that option exists


def _coerce(value, spec: dict):
    """One argument's near-miss repaired to its declared type; anything else comes back unchanged for validate()."""
    t = spec.get("type")
    if isinstance(value, str):
        s = value.strip()
        if t == "integer" and re.fullmatch(r"[-+]?\d+(?:\.0*)?", s):
            return int(s.split(".")[0])
        if t == "number" and re.fullmatch(r"[-+]?(?:\d+\.?\d*|\.\d+)", s):
            return float(s) if "." in s else int(s)
        if t == "boolean" and s.lower() in _TRUE | _FALSE:
            return s.lower() in _TRUE
        if t in ("array", "object") and s[:1] in "[{":
            try:
                return _coerce(json.loads(s), spec)
            except ValueError:
                try:
                    return _coerce(ast.literal_eval(s), spec)    # Python's quotes: "['blue jacket']" (llama3.2)
                except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
                    return value
        if "enum" in spec and value not in spec["enum"]:
            key = re.sub(r"[\s-]+", "_", s.lower())
            options = [e for e in spec["enum"] if isinstance(e, str)]
            if _SAME.get(key) and any(_SAME[key] in e.lower().split("_") for e in options):
                key = _SAME[key]
            exact = [e for e in options if e.lower() == key]
            # "pause" for play_pause: one word of exactly one allowed value ("Pause the music" failed on this)
            part = [e for e in options if key in e.lower().split("_")]
            return (exact or (part if len(part) == 1 else [value]))[0]
    if t == "integer" and isinstance(value, float) and value.is_integer():
        return int(value)
    if t == "string" and isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    items = spec.get("items") if isinstance(spec.get("items"), dict) else None
    if t == "array" and items is not None:
        if isinstance(value, list):
            return [_coerce(v, items) for v in value]
        one = _coerce(value, items)
        return [one] if _type_ok(one, items.get("type", "")) else value   # "Costco" for ["Costco"]
    if t == "object" and isinstance(value, dict) and isinstance(spec.get("properties"), dict):
        return {k: _coerce(v, spec["properties"][k]) if k in spec["properties"] else v for k, v in value.items()}
    return value


def coerce(name: str, args):
    """Repair the formatting near-misses small local models make in otherwise-correct calls, before validate():
    "2" for 2 (get_weather days), "false" for false (set_reminder shared), one string for a list, an enum in the
    wrong case, a null or empty optional argument, an argument the skill doesn't have. Each used to fail the
    whole call and send a simple request up to the slow cloud tiers. Never invents a value."""
    entry = _TOOLS.get(name)
    if entry is None or not isinstance(args, dict):
        return args
    schema = entry["schema"]["input_schema"]
    props, required = schema["properties"], set(schema["required"])
    out = {}
    try:
        for key, value in args.items():
            if key not in props:
                continue
            # llama3.2 wrote the WORD for "nothing": read_screen(app='null', find_text='null') then looked for the
            # text "null" on screen. Only for an optional argument; a required one is never invented or dropped.
            empty = value is None or (isinstance(value, str) and value.strip().lower() in _NOTHING)
            if key not in required and (empty or (value == "" and props[key].get("type") != "string")):
                continue                                # the skill's own default applies
            out[key] = _coerce(value, props[key])
    except Exception:
        return args     # a repair must never break a call: validate() judges the original
    return out


def validate(name: str, args: dict) -> str | None:
    """Return a problem description if args don't match the tool's schema, else None.

    Checks every level, not just the top: a skill that takes an array of objects (a meal plan, a training
    plan, vocabulary words) would otherwise accept e.g. ["Monday"], STORE it, and every later read of that
    data would crash ('str' object has no attribute 'get'). Rejecting it here keeps bad data out of storage
    and tells the model exactly which argument to fix."""
    schema = _TOOLS[name]["schema"]["input_schema"]
    if not isinstance(args, dict):
        return "arguments must be an object"
    props = schema["properties"]
    missing = [k for k in schema["required"] if k not in args]
    if missing:
        return f"missing argument(s): {', '.join(missing)}"
    unknown = [k for k in args if k not in props]
    if unknown:
        return f"unknown argument(s): {', '.join(unknown)}"
    for key, value in args.items():
        problem = _check(value, props[key], key)
        if problem:
            return problem
    return None


# Who a skill talks to, so an outage is reported as "Jellyfin on the Pi isn't reachable" rather than
# "URLError: <urlopen error [Errno 11001] getaddrinfo failed>". Unlisted skills fall back to their module.
_SERVICE = {
    "get_weather": "the weather service",
    "directions": "the maps service", "find_places": "the maps service", "shopping_route": "the maps service",
    "flight_search": "the flight search",
    "jellyfin_overview": "Jellyfin on the Pi", "jellyfin_search": "Jellyfin on the Pi", "jellyfin_rescan": "Jellyfin on the Pi",
    "metube_download": "MeTube on the Pi",
    "public_domain_search": "the Internet Archive", "public_domain_download": "the Internet Archive",
    "read_feed": "that news feed", "read_web_pages": "that website", "search_web_free": "the web search",
    "youtube_transcript": "YouTube", "movies_out": "the movie listings", "movie_info": "the movie database",
    "garmin_sync": "Garmin Connect", "grocery_deals": "the store deals service", "gaming_trends": "the gaming news feed",
    "add_to_library": "that source",
}
_MODULE_SERVICE = {"qbittorrent": "qBittorrent", "homelab": "the homelab Pi", "pcview": "the desktop PC",
                   "vault": "the memory vault server", "reach": "that website"}


def _service(name: str) -> str:
    if name in _SERVICE:
        return _SERVICE[name]
    module = _TOOLS.get(name, {}).get("fn").__module__.replace("tools.", "") if name in _TOOLS else ""
    return _MODULE_SERVICE.get(module, "the service it needs")


def _http_status(e) -> int | None:
    code = getattr(e, "code", None)                                     # urllib.error.HTTPError
    if code is None and getattr(e, "response", None) is not None:       # requests.exceptions.HTTPError
        code = getattr(e.response, "status_code", None)
    return code if isinstance(code, int) else None


def _is_outage(e: BaseException) -> bool:
    """A real connectivity failure (refused, unreachable, DNS, timeout, dropped) — not a programming error.
    A urllib URLError counts only when it wraps a socket/OS error: 'unknown url type' is a bug, not an outage."""
    import http.client
    import socket
    import urllib.error
    if isinstance(e, (ConnectionError, TimeoutError, socket.gaierror, socket.herror, http.client.HTTPException)):
        return True
    if isinstance(e, urllib.error.URLError) and not isinstance(e, urllib.error.HTTPError):
        return isinstance(e.reason, (OSError, TimeoutError))
    try:
        import requests
        if isinstance(e, (requests.exceptions.ConnectionError, requests.exceptions.Timeout)):
            return True
    except ImportError:
        pass
    return False


# Helpers that shell out (ssh to the Pi, mcporter/Exa, winget...) report a failed process as RuntimeError(stderr).
# Recognise connectivity and credential failures by that text, so they're explained like any other outage.
_NET_TEXT = __import__("re").compile(
    r"connection (refused|timed out|reset|closed)|timed out|could not resolve|name or service not known|"
    r"no route to host|network is unreachable|host is down|getaddrinfo|temporary failure in name resolution|"
    r"failed to connect|unable to connect|connect to host|ECONNREFUSED|ETIMEDOUT|ENOTFOUND", __import__("re").I)
_AUTH_TEXT = __import__("re").compile(
    r"permission denied \(publickey|authentication failed|unauthori[sz]ed|invalid api key|401 unauthorized|"
    r"host key verification failed", __import__("re").I)


def _friendly_error(name: str, e: Exception) -> str:
    """Turn an expected failure of a skill's dependency into an honest, actionable message. Anything else keeps
    its exception type, so genuine bugs stay visible in the logs instead of being dressed up as outages."""
    svc = _service(name)
    code = _http_status(e)
    if code is not None:
        if code in (401, 403):
            return f"{svc} refused the request (HTTP {code}) — its login or API key needs fixing. Tell the user plainly."
        if code == 404:
            return f"{svc} couldn't find that (HTTP 404)."
        if code == 429:
            return f"{svc} is rate-limiting requests (HTTP 429) — wait a little and try again."
        if code >= 500:
            return f"{svc} is having problems on its end (HTTP {code}) — try again later."
        return f"{svc} returned an error (HTTP {code})."
    # Capitalise generic phrases ("the maps service" -> "The …") but never a proper name ("qBittorrent", "MeTube").
    lead = svc[0].upper() + svc[1:] if svc.split(" ", 1)[0] in ("the", "that") else svc
    text = str(e)
    if isinstance(e, (RuntimeError, OSError)) and _AUTH_TEXT.search(text):
        return (f"{lead} rejected Jarvis's login or key ({text.strip()[-160:]}). Its credentials need fixing — "
                "tell the user plainly; retrying won't help.")
    if _is_outage(e) or (isinstance(e, (RuntimeError, OSError)) and _NET_TEXT.search(text)):
        detail = getattr(e, "reason", None) or e
        return (f"{lead} isn't reachable right now — it may be offline, or the network is down "
                f"({type(detail).__name__}: {str(detail).strip()[-160:]}). Say so plainly and offer to try again "
                "later; don't guess an answer.")
    return f"{type(e).__name__}: {e}"


_guard = threading.local()


@contextmanager
def guarded(fn):
    """Inside this block, on THIS thread, every tool call first asks fn(name, args): None runs it; a string is returned as
    the tool's (error) result and the tool body never runs. Used by the mission worker (Mark VI) so a step that changes
    something parks for the owner's approval instead of running unattended."""
    prev = getattr(_guard, "fn", None)
    _guard.fn = fn
    try:
        yield
    finally:
        _guard.fn = prev


def run(name: str, args: dict) -> tuple[str | list, bool]:
    """Execute a tool. Returns (result, is_error); result is text, or a list of
    content blocks when the tool returns images."""
    entry = _TOOLS.get(name)
    if entry is None:
        return f"Unknown tool: {name}", True
    from core import dryrun
    if dryrun.active():
        return f"Dry run: {name} was NOT executed.", False
    if entry["slow"]:
        from tools import gaming

        if gaming.game_mode() and name != "review_clip":
            return ("Not during a game: that job uses the GPU and would cost you frames. Offer to do it "
                    "after the session."), True
    problem = validate(name, args)
    if problem:
        return f"Invalid call to {name}: {problem}. Fix the arguments and try again.", True
    # Mark VI: a mission thread runs under a guard that decides, per call, whether this tool may run unattended. Thread-
    # local on purpose: the owner's own voice turns (other threads) are never affected (core/missionpolicy.make_guard).
    guard = getattr(_guard, "fn", None)
    if guard is not None:
        held = guard(name, args)
        if held is not None:
            return held, True
    from core import turntrace
    try:
        with turntrace.timed("tool", name):          # the turn's ledger: which skill took the seconds
            result = entry["fn"](**args)
        if isinstance(result, (str, list)):
            return result, False
        return json.dumps(result, ensure_ascii=False), False
    except Exception as e:
        return _friendly_error(name, e), True
