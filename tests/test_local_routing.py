"""Fast local turns: small talk gets no tools, the right skills get shortlisted, near-miss arguments are repaired,
and the local model is warmed while the user is still talking."""
import json
from types import SimpleNamespace

import pytest

import tools
from core import intelligence
from core.intelligence import parse_reply, select_tools, small_talk
from tools import registry

tools.load_all()
SCHEMAS = registry.schemas()


def _names(history):
    return [t["name"] for t in select_tools(history, SCHEMAS)]


# Real lines from the owner's log that fired random tools (review_clip, get_time, look_at_screen...) and took 8-16 s.
@pytest.mark.parametrize("said", [
    "You like that?", "We'll see you next time.", "How are you?", "What's up?", "Are you here?", "haha", "Bah!",
    "Oh my gosh.", "Oh, nothing, Jarvis. Thank you.", "No, you're good, Travis. Thank you.", "Hey Jarvis, what's up?",
    "Ciao!", "Good morning", "Good job Jarvis, good job.", "I was just testing you.", "Thanks, that's all.",
])
def test_small_talk_is_recognised(said):
    assert small_talk(said) and small_talk(said, asked=True)


@pytest.mark.parametrize("said", [
    # requests, however short or polite
    "Shut down the PC.", "pair my phone", "Open blender.", "What's the weather?", "What time is it?",
    "Now watch this.", "Show me what you see.", "Go ahead.", "Do it.", "Yes.", "Yeah, go ahead.", "Try again",
    "No, it's all good. All right, thank you. Shut down the PC. I'm all done. Good night.",
    "Hey, good job Jarvis, good job. I press enter myself, I will do the next step.",
    "Jarvis, I'm ready.", "What's new?", "No.", "Nope.", "Okay.", "Alright.", "",
])
def test_requests_and_bare_answers_keep_their_tools(said):
    assert not small_talk(said)


def test_after_a_question_thanks_or_okay_may_be_consent():
    assert small_talk("Thank you.") and not small_talk("Thank you.", asked=True)
    assert small_talk("Alright, thank you.") and not small_talk("Alright, thank you.", asked=True)
    assert small_talk("No, thank you.", asked=True)       # a clear decline needs no tool
    assert small_talk("Cool.") and not small_talk("Cool.", asked=True)


def test_small_talk_turn_gets_no_tools_but_a_request_does():
    assert _names([{"role": "user", "content": "You like that?"}]) == []
    asked = [{"role": "user", "content": "hey"},
             {"role": "assistant", "content": "Shall I uninstall Proton VPN as well?"},
             {"role": "user", "content": "Thank you."}]
    assert "uninstall_program" in _names(asked)           # may be consent: the context's tools stay


@pytest.mark.parametrize("said", ["frozen pencil", "velvet lantern", "amber meadow", "quiet garden"])
def test_low_signal_fragments_do_not_offer_unrelated_tools(said):
    assert _names([{"role": "user", "content": said}]) == []


def test_low_signal_guard_preserves_terse_recognised_requests():
    assert not intelligence.low_signal_fragment([{"role": "user", "content": "rain tomorrow"}])
    assert not intelligence.low_signal_fragment([{"role": "user", "content": "music please"}])


def test_low_signal_fragment_is_not_suppressed_after_a_question():
    history = [{"role": "user", "content": "Which colour do you prefer?"},
               {"role": "assistant", "content": "Would you like the velvet lantern or the copper one?"},
               {"role": "user", "content": "velvet lantern"}]
    assert not intelligence.low_signal_fragment(history)
    assert intelligence.low_signal_fragment(history, allow_after_question=True)


@pytest.mark.parametrize("said, skill", [("weather tomorrow", "get_weather"), ("pair my phone", "pair_phone")])
def test_low_signal_guard_keeps_recognised_short_requests(said, skill):
    assert skill in _names([{"role": "user", "content": said}])


def test_tool_result_round_keeps_tools():
    history = [{"role": "user", "content": "thanks"},
               {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "get_time", "input": {}}]},
               {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "10:42"}]}]
    assert "get_time" in _names(history)


@pytest.mark.parametrize("said, skill", [
    ("Is it going to rain today?", "get_weather"), ("Do I need an umbrella tomorrow?", "get_weather"),
    ("How cold is it outside?", "get_weather"), ("What's the weather?", "get_weather"),
    ("Remind me to call mom at 5", "set_reminder"), ("What are my reminders?", "list_reminders"),
    ("How far is Costco from here?", "directions"), ("What's the news today?", "read_feed"),
    ("What's 15 percent of 80?", "engineering_calc"), ("Pause the music", "media_control"),
    ("Turn the volume down", "volume"), ("Add milk to the shopping list", "add_note"),
    ("Uninstall ProtonVPN", "uninstall_program"), ("Shut down the PC", "power"), ("pair my phone", "pair_phone"),
    ("Open Spotify", "open_app"), ("Set a timer for 10 minutes", "set_timer"), ("What time is it?", "get_time"),
    ("What did we talk about yesterday?", "recall_conversations"), ("Turn off eyes", "deactivate_eyes"),
])
def test_everyday_requests_shortlist_the_right_skill(said, skill):
    assert skill in _names([{"role": "user", "content": said}])


def test_background_memories_do_not_pick_the_tools():
    background = ("<background>\nWhat you remember: owner trains with Garmin, logs workouts and nutrition, keeps a "
                  "wardrobe and outfits, watches movies on Jellyfin, plays Apex, studies Kreyol.\n</background>")
    names = _names([{"role": "user", "content": [{"type": "text", "text": background},
                                                 {"type": "text", "text": "Open Spotify"}]}])
    assert "open_app" in names[:3]                         # ties with open_website / open_jarvis_page on "open"
    assert not {"garmin_sync", "log_workout", "list_wardrobe", "jellyfin_search"} & set(names)


def test_a_follow_up_uses_what_jarvis_just_offered():
    history = [{"role": "user", "content": "my disk is full"},
               {"role": "assistant", "content": "Red Dead Redemption takes 120 GB. Shall I uninstall it?"},
               {"role": "user", "content": "Yes, go ahead."}]
    assert "uninstall_program" in _names(history)


# -- near-miss arguments --------------------------------------------------------------------------------------------
def test_coerce_repairs_formatting_near_misses():
    assert registry.coerce("get_weather", {"place": "Miami", "days": "2"}) == {"place": "Miami", "days": 2}
    assert registry.coerce("get_weather", {"days": 3.0}) == {"days": 3}
    out = registry.coerce("set_reminder", {"text": "call mom", "when": "2026-10-23T17:00", "shared": "false"})
    assert out["shared"] is False
    assert registry.coerce("shopping_route", {"stores": "Costco"}) == {"stores": ["Costco"]}
    assert registry.coerce("shopping_route", {"stores": '["Costco", "Aldi"]'}) == {"stores": ["Costco", "Aldi"]}
    assert registry.coerce("directions", {"origin": "a", "destination": "b", "mode": "Driving"})["mode"] == "driving"


def test_coerce_drops_empty_optionals_and_unknown_args_but_never_invents():
    assert registry.coerce("get_weather", {"place": "Paris", "days": None, "units": "metric"}) == {"place": "Paris"}
    assert registry.coerce("get_weather", {"days": "a few"}) == {"days": "a few"}           # left for validate()
    assert registry.validate("get_weather", registry.coerce("get_weather", {"days": "a few"}))
    assert registry.coerce("open_app", {"app": None}) == {"app": None}                       # required: still rejected
    assert registry.validate("open_app", {"app": None})


def test_parse_reply_accepts_a_repaired_call():
    response = {"model": "local", "choices": [{"finish_reason": "tool_calls", "message": {"content": None,
                "tool_calls": [{"id": "c1", "function": {"name": "get_weather",
                                                         "arguments": json.dumps({"place": "Springfield, IL", "days": "2"})}}]}}]}
    block = parse_reply(response, {"get_weather"}, said="what's the weather").content[0]
    assert block.name == "get_weather" and block.input == {"place": "Springfield, IL", "days": 2}


# -- prewarm at the wake word ---------------------------------------------------------------------------------------
class _Opener:
    def __init__(self):
        self.requests = []

    def open(self, request, timeout=None):
        self.requests.append((request.full_url, json.loads(request.data)))

        class R:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self, n=-1):
                return b'{"done": true}'
        return R()


@pytest.fixture
def ollama_fast(monkeypatch):
    opener = _Opener()
    monkeypatch.setenv("JARVIS_INTELLIGENCE", "local-first")
    monkeypatch.setenv("JARVIS_LOCAL_FAST_PROVIDER", "ollama")
    monkeypatch.setenv("JARVIS_LOCAL_FAST_MODEL", "llama3.1:8b")
    monkeypatch.delenv("JARVIS_PC_URL", raising=False)
    monkeypatch.setattr(intelligence, "_ollama_endpoint", lambda: ("http://127.0.0.1:11434/v1", opener))
    monkeypatch.setattr(intelligence, "_last_local", {})
    return opener


def test_prewarm_loads_the_model_with_the_turns_own_prompt_and_context(ollama_fast):
    intelligence.prewarm("SYSTEM PROMPT").join(5)
    (url, body), = [(u, b) for u, b in ollama_fast.requests if u.endswith("/api/chat")]   # (+ a VRAM probe first)
    assert url.endswith("/api/chat") and body["model"] == "llama3.1:8b"
    assert body["messages"] == [{"role": "system", "content": "SYSTEM PROMPT"}]
    assert body["options"]["num_ctx"] == 16384 and body["options"]["num_predict"] == 1   # same context: no reload
    assert body["keep_alive"] == "8h" and body["stream"] is False      # core.warmth: kept for hours (cold first turns were 14 s)
    assert body["tools"] == [intelligence._NO_TOOL]          # the same system header every turn renders


def test_prewarm_skips_when_the_model_just_answered_or_is_not_local(ollama_fast, monkeypatch):
    intelligence.prewarm("S").join(5)
    assert intelligence.prewarm("S") is None                # answered moments ago: loaded and cached
    monkeypatch.setattr(intelligence, "_last_local", {})
    monkeypatch.setenv("JARVIS_INTELLIGENCE", "legacy")
    assert intelligence.prewarm("S") is None
    monkeypatch.setenv("JARVIS_INTELLIGENCE", "local-first")
    monkeypatch.setenv("JARVIS_PC_URL", "http://pc.example:8765")
    from core import pcbridge
    monkeypatch.setattr(pcbridge, "local_pc", lambda: False)
    assert intelligence.prewarm("S") is None                # the laptop: its turns run on the PC's model
    assert len([u for u, _ in ollama_fast.requests if u.endswith("/api/chat")]) == 1


# -- speaking while the local model is still writing ----------------------------------------------------------------
def _ndjson(pieces, calls=None):
    lines = [json.dumps({"message": {"role": "assistant", "content": p}, "done": False}).encode() + b"\n" for p in pieces]
    if calls:
        lines.append(json.dumps({"message": {"role": "assistant", "content": "", "tool_calls": calls},
                                 "done": False}).encode() + b"\n")
    lines.append(json.dumps({"model": "llama3.1:8b", "message": {"role": "assistant", "content": ""}, "done": True,
                             "done_reason": "stop", "prompt_eval_count": 900, "eval_count": 12}).encode() + b"\n")
    return lines


def test_ollama_stream_is_rebuilt_into_the_usual_reply():
    got = []
    final = intelligence._read_ollama_stream(iter(_ndjson(["Hello", " there. How", " are you?"])), got.append,
                                             lambda: False)
    assert got == ["Hello", " there. How", " are you?"]
    reply = intelligence._ollama_reply(final)
    assert reply["choices"][0]["message"]["content"] == "Hello there. How are you?"
    assert reply["choices"][0]["finish_reason"] == "stop" and reply["usage"]["completion_tokens"] == 12
    calls = [{"function": {"name": "get_time", "arguments": {}}}]
    reply = intelligence._ollama_reply(intelligence._read_ollama_stream(iter(_ndjson([], calls)), got.append,
                                                                        lambda: False))
    assert reply["choices"][0]["message"]["tool_calls"][0]["function"]["name"] == "get_time"
    with pytest.raises(intelligence.LocalFailure, match="cancelled"):
        intelligence._read_ollama_stream(iter(_ndjson(["a"])), got.append, lambda: True)


@pytest.fixture
def fake_ollama(ollama_fast, monkeypatch):
    """complete_local against a scripted Ollama: /models lists the model, /api/chat answers `pieces`."""
    sent = []

    class Response:
        def __init__(self, lines):
            self.lines = lines

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def __iter__(self):
            return iter(self.lines)

        def read(self, n=-1):
            return b"".join(self.lines)

    class Opener:
        pieces = ["Of course, sir. ", "Anything else?"]

        def open(self, request, timeout=None):
            url = request if isinstance(request, str) else request.full_url
            if url.endswith("/models"):
                return Response([json.dumps({"data": [{"id": "llama3.1:8b"}]}).encode()])
            body = json.loads(request.data)
            sent.append(body)
            if body["stream"]:
                return Response(_ndjson(self.pieces))
            return Response([json.dumps({"model": "llama3.1:8b", "message": {"role": "assistant",
                             "content": "".join(self.pieces)}, "done": True, "done_reason": "stop"}).encode()])
    monkeypatch.setattr(intelligence, "_ollama_endpoint", lambda: ("http://127.0.0.1:11434/v1", Opener()))
    monkeypatch.setattr(intelligence, "_switch_local_provider", lambda role, cancelled: None)
    monkeypatch.setattr(intelligence, "audit", lambda *a, **k: None)
    return sent


def test_small_talk_streams_but_a_first_round_with_tools_does_not(fake_ollama):
    heard = []
    reply = intelligence.request("fast", "sys", [{"role": "user", "content": "How are you?"}], SCHEMAS,
                                 on_text=heard.append)
    assert fake_ollama[-1]["stream"] is True and fake_ollama[-1]["tools"] == [intelligence._NO_TOOL]
    assert heard == ["Of course, sir. ", "Anything else?"] and reply.content[0].text == "Of course, sir. Anything else?"
    heard.clear()
    intelligence.request("fast", "sys", [{"role": "user", "content": "What's the weather?"}], SCHEMAS,
                         on_text=heard.append)
    assert fake_ollama[-1]["stream"] is False and heard == []
    assert "get_weather" in [t["function"]["name"] for t in fake_ollama[-1]["tools"]]
    assert fake_ollama[-1]["messages"][-1] == {"role": "user", "content": "What's the weather?"}   # unchanged


def test_a_turn_without_tools_renders_like_one_with_them(fake_ollama):
    """Llama 3's template puts a tool note in the system header only when tools are attached: small talk keeps a
    placeholder attached so the header (and Ollama's cached prompt) is the same as on a tool turn, and ends on an
    open assistant turn so the user's message gets no "answer with a function call" block."""
    intelligence.request("fast", "sys", [{"role": "user", "content": "How are you?"}], SCHEMAS)
    body = fake_ollama[-1]
    assert body["tools"] == [intelligence._NO_TOOL]
    assert body["messages"][-1] == {"role": "assistant", "content": ""}
    assert body["messages"][-2]["role"] == "user" and body["messages"][-2]["content"].startswith("How are you?")
    assert body["messages"][-2]["content"].endswith(intelligence._PLAIN)


def test_the_placeholder_is_never_runnable():
    call = {"model": "local", "choices": [{"finish_reason": "tool_calls", "message": {"content": None,
            "tool_calls": [{"id": "c1", "function": {"name": "no_tool", "arguments": "{}"}}]}}]}
    with pytest.raises(intelligence.LocalFailure, match="invalid_tool_call"):
        parse_reply(call, set(), said="how are you")


def test_kept_history_never_gets_the_plain_words_note(fake_ollama):
    history = [{"role": "user", "content": "How are you?"}]
    intelligence.request("fast", "sys", history, SCHEMAS)
    assert history == [{"role": "user", "content": "How are you?"}]


def test_the_answer_after_a_tool_streams(fake_ollama):
    history = [{"role": "user", "content": "What time is it?"},
               {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "get_time", "input": {}}]},
               {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "10:42, of course."}]}]
    heard = []                 # (the scripted reply "Of course, sir." must be about the result to be streamed: _answers)
    intelligence.request("fast", "sys", history, SCHEMAS, on_text=heard.append)
    assert fake_ollama[-1]["stream"] is True and heard


def _brain(monkeypatch, script):
    """A Brain whose local tier replays `script(on_text)` instead of calling a model."""
    from brain import llm
    monkeypatch.setenv("JARVIS_INTELLIGENCE", "local-first")
    b = llm.Brain("claude-opus-5-5", "medium", "You are Jarvis.", uid="owner")
    b._local_role, b._cloud_calls = "fast", 0
    def request(role, system, history, tools, cancelled, on_text=None, **kw):
        if set(kw) - {"chat_system", "on_chat", "talk_only"}:    # the gateway's JSON call and the like: not this tier
            raise TypeError("only the Brain's own turn is replayed here")
        return script(on_text)
    monkeypatch.setattr(intelligence, "request", request)
    monkeypatch.setattr(intelligence, "audit", lambda *a, **k: None)
    return b


def test_brain_speaks_each_sentence_once_and_the_first_before_the_reply_is_done(monkeypatch):
    from anthropic.types import TextBlock
    events = []

    def script(on_text):
        for piece in ["It's 62 degrees", " and sunny, sir. ", "Clear tonight", ", down to 51. ", "Anything else?"]:
            on_text(piece)
        events.append("model finished")
        return SimpleNamespace(content=[TextBlock(type="text", text="It's 62 degrees and sunny, sir. Clear tonight, "
                                                  "down to 51. Anything else?")], stop_reason="end_turn")
    b = _brain(monkeypatch, script)
    b._stream_turn(events.append)
    assert events == ["It's 62 degrees and sunny, sir.", "Clear tonight, down to 51.", "model finished",
                      "Anything else?"]


def test_brain_searches_explicit_local_notes_without_asking_a_model(monkeypatch):
    from tools import registry
    calls, said = [], []
    b = _brain(monkeypatch, lambda on_text: pytest.fail("model should not be called for explicit notes search"))
    monkeypatch.setattr(registry, "run", lambda name, args: (calls.append((name, args)) or ("Saved notes:\nDentist Tuesday.", False)))
    assert b._local_note_request("Search my notes for dentist appointment.", said.append)
    assert calls == [("search_notes", {"query": "dentist appointment"})]
    assert said == ["Saved notes:\nDentist Tuesday."]


def test_brain_clarifies_a_low_signal_fragment_without_asking_a_model(monkeypatch):
    monkeypatch.setenv("JARVIS_CHAT_FIRST", "off")   # the quote-back where there is no chat path (tests/test_chat_first.py)
    b = _brain(monkeypatch, lambda on_text: pytest.fail("model should not be called for an ambiguous fragment"))
    heard = []
    b.ask("frozen pencil", heard.append)
    assert heard == ["I'm not sure what you meant by 'frozen pencil', sir. What did you need?"]   # his words back, not a template
    assert b.messages[-1] == {"role": "assistant", "content": heard[0]}


def test_brain_repeats_its_clarification_for_another_fragment(monkeypatch):
    monkeypatch.setenv("JARVIS_CHAT_FIRST", "off")   # the quote-back where there is no chat path (tests/test_chat_first.py)
    b = _brain(monkeypatch, lambda on_text: pytest.fail("model should not be called for two ambiguous fragments"))
    heard = []
    b.ask("frozen pencil", heard.append)
    b.ask("velvet lantern", heard.append)
    assert heard == ["I'm not sure what you meant by 'frozen pencil', sir. What did you need?",
                     "I'm not sure what you meant by 'velvet lantern', sir. What did you need?"]


def test_brain_does_not_invent_context_for_vague_clarification_followups(monkeypatch):
    monkeypatch.setenv("JARVIS_CHAT_FIRST", "off")   # the quote-back where there is no chat path (tests/test_chat_first.py)
    b = _brain(monkeypatch, lambda on_text: pytest.fail("model should not invent an object after asking for intent"))
    heard = []
    b.ask("frozen pencil", heard.append)
    b.ask("Please make corrections too.", heard.append)
    b.ask("we have no request to stop.", heard.append)
    assert heard == ["I'm not sure what you meant by 'frozen pencil', sir. What did you need?",
                     "I'm not sure what you meant by 'Please make corrections too', sir. What did you need?",
                     "I'm not sure what you meant by 'we have no request to stop', sir. What did you need?"]


def test_brain_allows_a_specific_request_after_an_intent_clarification(monkeypatch):
    monkeypatch.setenv("JARVIS_CHAT_FIRST", "off")   # the quote-back where there is no chat path (tests/test_chat_first.py)
    calls = []
    b = _brain(monkeypatch, lambda on_text: pytest.fail("the dispatch boundary is mocked below"))
    monkeypatch.setattr(b, "_run", lambda emit, start: calls.append("model"))
    heard = []
    b.ask("frozen pencil", heard.append)
    b.ask("Explain why leaves change color.", heard.append)
    assert heard == ["I'm not sure what you meant by 'frozen pencil', sir. What did you need?"]
    assert calls == ["model"]


def test_brain_holds_a_tool_call_typed_as_text(monkeypatch):
    def script(on_text):
        on_text('{"name": "get_time", "parameters": {}}. Then more. ')
        raise intelligence.LocalFailure("invalid_response")
    b = _brain(monkeypatch, script)
    said = []
    from core import usage
    monkeypatch.setattr(usage, "over_budget", lambda: False)
    monkeypatch.setattr(b, "_cloud_turn", lambda emit: said.append("<next tier>") or None)
    b._stream_turn(said.append)
    assert said == ["<next tier>"]                              # nothing of the JSON was said out loud


def test_brain_ends_on_what_it_already_said_when_the_reply_then_fails(monkeypatch):
    def script(on_text):
        on_text("Of course, sir. ")
        on_text('Also {"name": "nope"')
        raise intelligence.LocalFailure("invalid_tool_call")
    b = _brain(monkeypatch, script)
    said = []
    msg = b._stream_turn(said.append)
    assert said == ["Of course, sir."]                          # never repeated by another tier
    assert msg.stop_reason == "end_turn" and msg.content[0].text == "Of course, sir."


# -- escalating must not throw away the warm fast model ---------------------------------------------------------------
class _Server:
    """A loopback server stand-in: answers by URL suffix, records every request; None = unreachable."""

    def __init__(self, routes):
        self.routes, self.seen = routes, []

    def open(self, request, timeout=None):
        import urllib.error
        url = request if isinstance(request, str) else request.full_url
        self.seen.append(url)
        for suffix, body in self.routes.items():
            if url.endswith(suffix):
                if body is None:
                    raise urllib.error.URLError("connection refused")
                return _Opener().open.__func__(_Opener(), type("Q", (), {"full_url": url, "data": b"{}"})()) \
                    if body == "ok" else _Body(body)
        raise urllib.error.URLError("no route")


class _Body:
    def __init__(self, body):
        self.body = json.dumps(body).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, n=-1):
        return self.body


@pytest.mark.parametrize("lm_studio_url, lm_studio_up, unloads", [
    ("http://127.0.0.1:11434/v1", True, False),   # the owner's .env: "LM Studio" is Ollama itself
    ("http://127.0.0.1:1234/v1", False, False),    # LM Studio closed: the advanced request fails anyway
    ("http://127.0.0.1:1234/v1", True, True),      # LM Studio really there: make room for its model
])
def test_advanced_tier_only_unloads_the_fast_model_when_lm_studio_will_use_the_gpu(monkeypatch, lm_studio_url,
                                                                                   lm_studio_up, unloads):
    monkeypatch.setenv("JARVIS_INTELLIGENCE", "local-first")
    monkeypatch.setenv("JARVIS_LOCAL_FAST_PROVIDER", "ollama")
    monkeypatch.setenv("JARVIS_LOCAL_FAST_MODEL", "llama3.1:8b")
    monkeypatch.setenv("JARVIS_LOCAL_ADVANCED_MODEL", intelligence.FAST)
    ollama = _Server({"/api/ps": {"models": [{"name": "llama3.1:8b"}]}, "/api/generate": {"done": True}})
    lm = ollama if lm_studio_url.endswith(":11434/v1") else _Server({"/api/v1/models": {"models": []} if lm_studio_up else None})
    monkeypatch.setattr(intelligence, "_ollama_endpoint", lambda: ("http://127.0.0.1:11434/v1", ollama))
    monkeypatch.setattr(intelligence, "_endpoint", lambda: (lm_studio_url, lm))
    intelligence._switch_local_provider("advanced", lambda: False)
    assert any(u.endswith("/api/generate") for u in ollama.seen) == unloads


# -- "here" means home ----------------------------------------------------------------------------------------------
def test_directions_from_here_start_at_home(monkeypatch):
    from tools import kitchen, travel
    monkeypatch.setattr(kitchen, "_data", lambda: {"household": {"home_address": "12 Oak St, Springfield, IL"}})
    asked = []
    monkeypatch.setattr(travel, "geocode", lambda q, limit=1: asked.append(q) or [])
    out = travel.directions("here", "Costco")
    assert asked == ["12 Oak St, Springfield, IL", "Costco"] and "origin=12+Oak+St" in out
    assert travel._place("Costco") == "Costco" and travel._place("my location") == "12 Oak St, Springfield, IL"


@pytest.fixture
def osm(monkeypatch):
    """Nominatim/OSRM stand-in: bounded searches return two Walmarts/Costcos near the box, routes 10 miles."""
    from tools import travel
    urls = []

    def fake_get(url, timeout=15):
        urls.append(url)
        if "router.project-osrm.org" in url:
            return {"code": "Ok", "routes": [{"distance": 16093.44, "duration": 1260}]}
        if "bounded=1" in url:
            return [{"name": "Far one", "display_name": "Far one, 2 Elm Rd, Decatur, VA", "lat": "37.20", "lon": "-76.50"},
                    {"name": "Near one", "display_name": "Near one, 1 Main St, Springfield, IL", "lat": "37.05", "lon": "-76.36"}]
        return []
    monkeypatch.setattr(travel, "_get", fake_get)
    monkeypatch.setattr(travel, "geocode", lambda q, limit=1: [{"lat": "37.03", "lon": "-76.35",
                                                               "display_name": f"{q}, Virginia, United States"}])
    monkeypatch.setattr(travel, "_last_nominatim", [0.0])
    return urls


def test_find_places_searches_around_the_area_and_gives_real_distances(osm, monkeypatch):
    from tools import travel
    out = travel.find_places("Walmart near Springfield, IL 62701")
    assert "q=Walmart&" in osm[0] and "bounded=1" in osm[0]
    first, second = out.splitlines()
    assert first.startswith("Near one (1.5 miles from Springfield, IL 62701, straight line)") and second.startswith("Far one")
    monkeypatch.setattr(travel, "_home_spot", lambda: (39.80, -89.64))
    assert "miles from home" in travel.find_places("the nearest Walmart")


def test_directions_to_a_store_go_to_the_nearest_one(osm):
    from tools import travel
    out = travel.directions("Springfield, IL", "Costco")
    assert out.startswith("10 miles, about 21 min driving") and "to Near one" in out
    assert "destination=Near+one" in out                    # the link navigates to that very store


def test_directions_need_only_a_destination(osm):
    from tools import travel
    assert registry.validate("directions", {"destination": "Costco"}) is None
    assert registry.validate("directions", {"origin": "here"})          # still needs somewhere to go
    assert "(home to Near one)" in travel.directions(destination="Costco")


def test_weather_always_includes_tomorrow(forecast, monkeypatch):
    from tools import weather
    monkeypatch.setattr(weather, "_home_spot", lambda: (39.80, -89.64, "Home"))
    weather.get_weather("", days=1)
    assert "forecast_days=2" in forecast[-1]


def test_directions_with_no_route_say_so_instead_of_failing(osm, monkeypatch):
    import urllib.error
    from tools import travel
    real = travel._get

    def no_route(url, timeout=15):
        if "router.project-osrm.org" in url:
            raise urllib.error.HTTPError(url, 400, "Bad Request", {}, None)
        return real(url, timeout)
    monkeypatch.setattr(travel, "_get", no_route)
    assert travel.directions("Springfield, IL", "Costco").startswith("No driving route found.")


def test_home_is_looked_up_once(monkeypatch):
    from tools import kitchen, travel
    monkeypatch.setattr(kitchen, "_HOME_SPOTS", {})
    calls = []
    monkeypatch.setattr(travel, "geocode", lambda q, limit=1: calls.append(q) or [{"lat": "39.8", "lon": "-89.6"}])
    household = {"zip": "62701"}
    assert kitchen._home(household) == kitchen._home(household) == (39.8, -89.6, "62701, USA")
    assert calls == ["62701, USA"]


# -- weather defaults to home ---------------------------------------------------------------------------------------
@pytest.fixture
def forecast(monkeypatch):
    from tools import weather
    calls = []

    def fake_get(url):
        calls.append(url)
        return {"current": {"temperature_2m": 17, "apparent_temperature": 16, "weather_code": 0, "wind_speed_10m": 16.1,
                            "precipitation": 0},
                "daily": {"time": ["2026-10-23"], "weather_code": [61], "temperature_2m_max": [19],
                          "temperature_2m_min": [10], "precipitation_probability_max": [70]}}
    monkeypatch.setattr(weather, "_get", fake_get)
    return calls


@pytest.mark.parametrize("place", ["", "home", "user's home city", "the user's location", "my location", "here"])
def test_weather_for_home_uses_the_household_address(forecast, monkeypatch, place):
    from tools import weather
    monkeypatch.setattr(weather, "_home_spot", lambda: (39.80, -89.64, "Home"))
    out = weather.get_weather(place)
    assert "latitude=39.8" in forecast[-1] and "geocoding" not in " ".join(forecast)
    assert out.startswith("Home now: clear, 63°F") and "wind 10 mph" in out and "70% chance of rain" in out


def test_weather_without_a_known_home_asks_instead_of_guessing(forecast, monkeypatch):
    from tools import weather
    monkeypatch.setattr(weather, "_home_spot", lambda: None)
    assert "don't know where home is" in weather.get_weather("")
    assert forecast == []


def test_weather_takes_no_required_arguments():
    assert registry.validate("get_weather", {}) is None


# -- a log line is never worth the turn ---------------------------------------------------------------------------------
def test_a_trace_line_the_console_cannot_encode_does_not_kill_the_reply(monkeypatch):
    """A Windows console in cp1252 raised UnicodeEncodeError on an OCR result's first line; that print ended the whole
    turn with "Something went wrong reaching my servers"."""
    import io
    import sys
    from brain import llm
    narrow = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")
    monkeypatch.setattr(sys, "stdout", narrow)
    llm._log("    -> Screen text: ✓ Save — 日本語")                 # must not raise
    narrow.flush()
    assert b"Save" in narrow.buffer.getvalue()


@pytest.mark.serial   # timing: it waits on the local tiers, which run slow when other tests load the PC
def test_when_no_local_tier_can_phrase_a_short_tool_result_the_result_itself_is_spoken(monkeypatch):
    """"Turn off eyes" -> deactivate_eyes -> "Eyes were already off." -> "Local intelligence is unavailable...": the
    skill had answered; only the model's phrasing failed."""
    def script(on_text):
        raise intelligence.LocalFailure("invalid_response")
    b = _brain(monkeypatch, script)
    monkeypatch.setattr(intelligence, "local_only", lambda: True)
    b.messages = [{"role": "user", "content": "Turn off eyes."},
                  {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "deactivate_eyes", "input": {}}]},
                  {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "Eyes were already off."}]}]
    said = []
    msg = b._stream_turn(said.append)
    assert said == ["Eyes were already off."] and msg.stop_reason == "end_turn"
    # an error result is a friendly sentence too ("The weather service isn't reachable right now."): spoken, without
    # the feedback note meant for the model; a long listing still needs a model: the honest note stays
    b.messages[-1]["content"][0].update(is_error=True, content="The weather service isn't reachable right now. (This get_weather call failed 2 times recently...)")
    said.clear()
    b._stream_turn(said.append)
    assert said == ["The weather service isn't reachable right now."]
    b.messages[-1]["content"][0].update(is_error=False, content="line " * 300 + chr(10) + "more")
    said.clear()
    b._stream_turn(said.append)
    assert said and "say it once more" in said[0] and "unavailable" not in said[0]    # plain words now (core.pchelp)


def _chat(*lines):
    return [{"role": "user" if n % 2 == 0 else "assistant", "content": line} for n, line in enumerate(lines)]


def test_a_plain_follow_up_carries_only_the_skills_his_words_point_at():
    """'and which size?' after two turns about his son's bike was offered 16 skills that only shared a word with the chat
    (Jarvis's replies included): read_feed, set_household_food, rigging_lessons... ~2,800 tokens of tools, more than the
    room left for the conversation itself (measured offline 2026-11-10)."""
    chat = _chat("I'm thinking about getting my son a new bike for Christmas.",
                 "A fine idea, sir. A bike he can grow into for a couple of years is usually the best value.",
                 "He's seven and pretty tall for his age.",
                 "Then he's likely past the smallest frames, sir. Tall seven-year-olds usually ride comfortably on a "
                 "larger wheel.",
                 "and which size?")
    names = _names(chat)
    assert len(names) <= 4, names
    assert not {"set_household_food", "rigging_lessons", "open_in_blender", "set_training_plan", "clip_that"} & set(names)


def test_every_skill_stays_reachable_by_his_words_and_by_the_question_he_answers():
    assert "get_weather" in _names(_chat("Is it going to rain today?", "No rain today, sir; 18 degrees and cloudy.",
                                         "and tomorrow?"))                     # his earlier words name the skill
    assert "set_reminder" in _names(_chat("I have to call the dentist.", "Shall I set a reminder for that?",
                                          "Yes, please, around noon."))       # his yes answers Jarvis's question
    assert "add_note" in _names(_chat("I have to call the dentist.", "Noted, sir.",
                                      "add milk to the shopping list"))       # a new request names its own skill
    used = [{"role": "user", "content": "What's the weather?"},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "w1", "name": "get_weather", "input": {}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "w1", "content": "Sunny, 21 C."}]},
            {"role": "assistant", "content": "Sunny and 21 degrees, sir."},
            {"role": "user", "content": "What about the weekend?"}]
    assert "get_weather" in _names(used)                                       # used in this exchange: kept


# -- review 2026-11-10: his yes to an offer, and a follow-up about a PC reading ------------------------------------------
@pytest.mark.parametrize("offer, wanted", [
    (("I need to call the dentist tomorrow at noon.", "I can set a reminder for noon tomorrow if you like, sir."),
     "set_reminder"),
    (("what's the news on the haitian gangs right now", "I don't have any information on the current news about "
      "Haitian gangs. I can try to search the web for you, sir."), "search_web_free"),
    (("We're out of eggs.", "I can put eggs on your shopping list, sir."), "build_shopping_list"),
])
@pytest.mark.parametrize("yes", ["go ahead", "Sure.", "Yes, go ahead.", "Absolutely, go for it.",
                                 "yeah go ahead and do that", "that would be great"])
def test_his_yes_to_an_offer_keeps_the_skill_jarvis_offered(offer, wanted, yes):
    """An offer said as a statement (no '?'), and a yes in his own words ('go ahead', 'sure'): the filter counted
    'ahead'/'sure' as words of his own and dropped the very skill Jarvis had offered (live log: 'I can try to search the
    web for you, sir.' / 'Yes, go ahead, search it out.'), leaving 'movies_out' or the camera. (The eggs offer gets the
    shopping skills the chat points at, as before this filter: add_note ranking below them is older than it.)"""
    names = _names(_chat(*offer, yes))
    assert wanted in names
    assert not {"what_am_i_holding", "take_avatar_photo"} & set(names)


def test_his_yes_with_words_of_his_own_still_reaches_the_offer():
    chat = _chat("I need to call the dentist tomorrow at noon.", "I can set a reminder for noon tomorrow if you like, sir.",
                 "Yes, around noon is fine.")
    assert "set_reminder" in _names(chat)


@pytest.mark.parametrize("follow_up", ["and the RAM?", "what about the fans?", "is it overheating?", "and the ping?"])
def test_a_follow_up_about_a_pc_reading_keeps_the_skill_that_measures_it(follow_up):
    """'and the RAM?' after 'how's my PC doing?' got no tools at all, so the reply streamed and an invented reading
    ('Your RAM is at 41 percent, sir.') was said before parse_reply could refuse it."""
    names = _names(_chat("how's my PC doing?", "Everything looks normal, sir.", follow_up))
    if follow_up == "and the ping?":
        assert names                                   # no skill measures it: the chat decides, as before the filter
    else:
        assert "get_system_status" in names[:3], names
        assert not {"look_through_camera", "what_am_i_holding", "take_avatar_photo"} & set(names)


def test_a_round_after_a_tool_result_keeps_the_skills_the_task_needs_next():
    """The filter is for his fresh words: in a tool round (and every mission round after the first,
    missionworker.allowed_schemas) the next step's skill is often named only by Jarvis's own plan."""
    history = [{"role": "user", "content": "Get my evening sorted out."},
               {"role": "assistant", "content": [
                   {"type": "text", "text": "I'll check your reminders, then start a timer for the laundry."},
                   {"type": "tool_use", "id": "r1", "name": "list_reminders", "input": {}}]},
               {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "r1", "content": "No reminders."}]}]
    names = _names(history)
    assert "list_timers" in names and "list_reminders" in names         # as before the filter
