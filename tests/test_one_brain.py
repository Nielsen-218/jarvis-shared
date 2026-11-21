"""2026-11-09, owner: "his local brain is small, I'm thinking one good brain". One Ollama model (qwen3.5:9b) now does talk,
tools, planning and vision. These pin the parts that assumed several different models, and qwen3.5's two traps found
live: it thinks unless every request says think:false, and a trailing empty assistant message made it reason out loud."""
import json

import pytest

from core import intelligence, warmth

BRAIN = "qwen3.5:9b"


@pytest.fixture
def one_brain(monkeypatch):
    monkeypatch.setenv("JARVIS_INTELLIGENCE", "local-first")
    monkeypatch.setenv("JARVIS_LOCAL_FAST_PROVIDER", "ollama")
    monkeypatch.setenv("JARVIS_LOCAL_URL", "http://127.0.0.1:11434/v1")   # his .env; a checkout without it differs
    for var in ("JARVIS_LOCAL_FAST_MODEL", "JARVIS_LOCAL_ADVANCED_MODEL", "JARVIS_LOCAL_VISION_MODEL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setitem(warmth._state, "game", False)


def test_the_default_brain_is_one_model_for_every_role(one_brain):
    assert intelligence.model("fast") == intelligence.model("advanced") == intelligence.vision_model() == BRAIN
    assert intelligence.thinks(BRAIN)                     # so every turn sends think:false
    assert warmth.jarvis_models() == [BRAIN]              # listed once, never twice


def test_a_game_keeps_the_one_brain_loaded_for_hours(one_brain, monkeypatch):
    unloaded = []
    monkeypatch.setattr(warmth, "resident", lambda: {BRAIN: 6_000_000_000})
    monkeypatch.setattr(warmth, "unload", lambda name: unloaded.append(name) or True)
    monkeypatch.setattr(warmth, "_note", lambda text: None)
    warmth.game_started()
    assert unloaded == [] and warmth.keep_alive(BRAIN) == warmth.KEEP          # it is the fast model, not "the planner"
    warmth._state["game"] = False


def test_a_learner_that_is_the_brain_is_not_unloaded_after_practice(one_brain, monkeypatch):
    unloaded, loads = [], []
    monkeypatch.setattr(warmth, "resident", lambda: {BRAIN: 1})
    monkeypatch.setattr(warmth, "unload", lambda name: unloaded.append(name) or True)
    monkeypatch.setattr(warmth, "_post", lambda path, body, timeout=120: loads.append(body) or True)
    monkeypatch.setattr(warmth, "practising", lambda: False)
    warmth.handback(BRAIN)
    assert unloaded == []


def test_every_direct_caller_gets_the_brains_settings(one_brain):
    f = warmth.ollama_fields(BRAIN, "2m")
    assert f["think"] is False and f["options"] == {"num_ctx": warmth.NUM_CTX} and f["keep_alive"] == warmth.KEEP
    other = warmth.ollama_fields("llava:7b", "2m")
    assert "think" not in other and other["keep_alive"] == "2m"   # Ollama refuses the field for models that don't think


def test_a_small_talk_turn_on_a_thinking_model_ends_on_the_users_message(one_brain):
    body = intelligence._same_prompt_start({"model": BRAIN, "messages": [{"role": "user", "content": "how are you?"}]})
    assert body["messages"][-1]["role"] == "user" and body["tools"] == [intelligence._NO_TOOL]
    llama = intelligence._same_prompt_start({"model": "llama3.2:3b", "messages": [{"role": "user", "content": "hi"}]})
    assert llama["messages"][-1] == {"role": "assistant", "content": ""}      # Llama 3 keeps its measured layout


def _reply(text):
    return {"model": BRAIN, "choices": [{"finish_reason": "stop", "message": {"content": text}}]}


def test_structured_answers_survive_the_dropped_schema():
    """Ollama ignores the JSON schema for qwen3.5 with thinking off (ollama#14645): the object can come wrapped."""
    for text in ('{"a": 1}', '```json\n{"a": 1}\n```', 'Sure, here it is:\n```json\n{"a": 1}\n``` Done.', 'Plan: {"a": 1} ok'):
        assert json.loads(intelligence._structured(_reply(text)).content[0].text) == {"a": 1}
    for text in ("I can't do that.", '<think>\nx</think>{"a": 1}'):
        with pytest.raises(intelligence.LocalFailure):
            intelligence._structured(_reply(text))


def test_the_lm_studio_path_never_turns_qwen_thinking_on():
    assert intelligence.thinks(BRAIN) and not intelligence.thinks("llama3.1:8b")
    src = open(intelligence.__file__, encoding="utf-8").read()
    assert '"none" if role == "fast" or thinks(chosen) else "low"' in src


def test_the_eyes_ask_the_brain_with_its_settings_and_the_shape_in_words(one_brain, monkeypatch):
    from core import engeyes
    sent = []

    class Resp:
        def __init__(self, data): self.data = data
        def read(self): return self.data
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def urlopen(req, timeout=None):
        sent.append(json.loads(req.data))
        return Resp(json.dumps({"message": {"content": 'Here: {"item": "a fuse", "confidence": 0.9}'}}).encode())
    monkeypatch.setattr(engeyes, "model", lambda: BRAIN)
    monkeypatch.setattr(engeyes.urllib.request, "urlopen", urlopen)
    out = engeyes._vlm([], "What is it?", {"type": "object", "properties": {"item": {"type": "string"}}})
    body, = sent
    assert out == {"item": "a fuse", "confidence": 0.9}
    assert body["think"] is False and body["options"]["num_ctx"] == warmth.NUM_CTX and body["options"]["temperature"] == 0
    assert "Reply with JSON only" in body["messages"][-1]["content"]


def test_the_planner_does_not_ask_the_same_model_twice(one_brain):
    src = open(__import__("core.pcplanner", fromlist=["x"]).__file__, encoding="utf-8").read()
    assert 'if intelligence.model("fast") != intelligence.model("advanced") else ()' in src


def test_a_fresh_request_is_never_the_owners_yes(monkeypatch):
    """Live 2026-11-09: a Notepad plan waited for review and "Add eggs and milk to my shopping list." was read as YES by
    the local model, so the plan ran. A new request is a new request; "close it" still answers "Should I close Notepad?"."""
    from core import approvals, meaning, pcledger
    asked = []
    monkeypatch.setattr(pcledger, "pending", lambda: [{"data": {"task": "add items to shopping list in Notepad"}}])
    monkeypatch.setattr(meaning, "choose", lambda *a, **k: asked.append(a[0]) or "yes")
    approvals._MEANT.clear()
    assert approvals.meant("owner", "Add eggs and milk to my shopping list.") is None
    assert approvals.meant("owner", "Hey Jarvis, open notepad") is None
    assert asked == []                                    # the model is not even asked
    assert approvals.meant("owner", "close it") == "yes" and asked == ["close it"]
    assert approvals.meant("owner", "please do") == "yes"  # the fixed yes list, as before


def test_his_own_assistant_skills_go_to_the_conversation_brain():
    from core import gateway
    for said in ("Add eggs and milk to my shopping list.", "remind me at five to call my mom", "set a timer for 10 minutes",
                 "remember that my sister Marie lives in Miami", "what's the weather tomorrow", "what's on my calendar today"):
        d = gateway.exact(said)
        assert d and d.intents[0].kind == "chat" and d.source == "exact", said
    for said in ("remind me what I named this pc", "open notepad and write my shopping list"):
        d = gateway.exact(said)
        assert not (d and d.intents[0].kind == "chat"), said


def test_a_timer_named_timer_is_not_said_twice(monkeypatch):
    from core import timers
    from tools import system
    monkeypatch.setattr(timers, "add", lambda seconds, label, done: {"label": label})
    assert system.set_timer(600, "timer") == "10 minutes on the timer. I'll tell you when it's done."
    assert system.set_timer(30, "eggs") == "30 seconds on the eggs timer. I'll tell you when it's done."


def test_a_restart_at_night_greets_on_screen_only(monkeypatch):
    import time as _t
    import main
    at = lambda h: _t.struct_time((2026, 11, 10, h, 0, 0, 0, 314, -1))
    monkeypatch.delenv("JARVIS_QUIET_START_HOURS", raising=False)
    assert main.quiet_start(at(23)) and main.quiet_start(at(3)) and main.quiet_start(at(6))
    assert not main.quiet_start(at(7)) and not main.quiet_start(at(14)) and not main.quiet_start(at(21))
    monkeypatch.setenv("JARVIS_QUIET_START_HOURS", "off")
    assert not main.quiet_start(at(3))


def test_readiness_uses_the_real_in_game_test_result(tmp_path, monkeypatch):
    """Live 2026-11-10: "Is my Shadow World character ready?" said the in-game check never ran, hours after it passed."""
    from core import unreal
    monkeypatch.setattr(unreal, "JARVIS_WT", tmp_path)
    folder = tmp_path / "Shadow_world_main_character-20261101-120-20261109-160715"
    assert unreal._in_game_gate(folder)["state"] == "not_checked"
    rep = tmp_path / "Documentation" / "BuildEvidence" / "t1_report"
    rep.mkdir(parents=True)
    test = {"fullTestPath": "LivingShadowWorld.JARVIS.InGame.ShadowWorldMainCharacter", "state": "Success",
            "entries": [{"event": {"type": "Info", "message": "JARVIS: ShadowWorldMainCharacter body height 183.9 cm"}}]}
    (rep / "index.json").write_text(json.dumps({"tests": [test]}), encoding="utf-8")
    gate = unreal._in_game_gate(folder)
    assert gate["state"] == "pass" and "183.9 cm" in gate["why"]
    test["state"] = "Fail"
    test["entries"].append({"event": {"type": "Error", "message": "fell through the floor"}})
    (rep / "index.json").write_text(json.dumps({"tests": [test]}), encoding="utf-8")
    gate = unreal._in_game_gate(folder)
    assert gate["state"] == "fail" and "fell through the floor" in gate["why"]
    other = unreal._in_game_gate(tmp_path / "Shadow_Vanguard-20261101-120000")
    assert other["state"] == "not_checked"                  # one character's pass never counts for another


def test_changed_ollama_options_restart_the_server_once(monkeypatch):
    """OLLAMA_NUM_PARALLEL etc. only apply when `ollama serve` starts; the server Jarvis started is elevated."""
    from core import storage
    for k in [k for k in __import__("os").environ if k.startswith("OLLAMA_")]:
        monkeypatch.delenv(k)
    monkeypatch.setenv("OLLAMA_NUM_PARALLEL", "3")
    monkeypatch.setattr(intelligence.os, "name", "nt")
    monkeypatch.setattr(intelligence, "_probe", lambda url, opener: True)
    monkeypatch.setattr(intelligence, "_ollama_exe", lambda: "ollama.exe")
    monkeypatch.setattr(intelligence.time, "sleep", lambda s: None)
    killed, started = [], []
    run = lambda: intelligence.apply_ollama_settings("http://127.0.0.1:11434/v1", None, kill=lambda: killed.append(1),
                                                     start=lambda exe: started.append(exe))
    assert run() is True and killed == [1] and started == ["ollama.exe"]
    assert storage.load_global(intelligence.OLLAMA_SETTINGS_FILE, None) == {"OLLAMA_NUM_PARALLEL": "3"}
    assert run() is False and killed == [1]                 # same options: never restarted again
    monkeypatch.setenv("OLLAMA_NUM_PARALLEL", "4")
    assert run() is True and killed == [1, 1]


def test_a_web_search_without_a_named_site_uses_the_default_engine():
    from core import gateway
    step = gateway._check_step({"tool": "browse_research", "arguments": {"query": "latest NVIDIA driver", "site": ""}}, None)
    assert step["arguments"]["site"] == "google"
    with pytest.raises(ValueError):
        gateway._check_step({"tool": "browse_research", "arguments": {"query": "", "site": "google"}}, None)  # no query: still refused


def test_the_tool_header_never_changes_so_the_prompt_stays_cached(monkeypatch):
    """Ollama's qwen3.5 renderer puts the tool list before the system text: a different tool set each turn re-read the
    whole ~12k-token prompt (3 s). The header is now always the placeholder; this turn's tools follow the system text."""
    monkeypatch.delenv("JARVIS_TOOLS_IN_TEXT", raising=False)
    def body(tools):
        return intelligence._ollama_body({"model": BRAIN, "temperature": 0.2, "max_tokens": 50, "tools": tools,
            "messages": [{"role": "system", "content": "SYS"}, {"role": "user", "content": "open spotify"}]})
    t = lambda n: {"type": "function", "function": {"name": n, "description": n, "parameters": {"type": "object", "properties": {}}}}
    a, b = body([t("open_app")]), body([t("add_to_list"), t("set_timer")])
    assert a["tools"] == b["tools"] == [intelligence._NO_TOOL]
    assert a["messages"][0] == b["messages"][0] == {"role": "system", "content": "SYS"}        # the cached part is identical
    assert '"name":"open_app"' in a["messages"][-1]["content"] and "<turn-tools>" in b["messages"][-1]["content"]
    assert a["messages"][-1]["content"].startswith("open spotify")
    llama = intelligence._ollama_body({"model": "llama3.2:3b", "temperature": 0.2, "max_tokens": 50, "tools": [t("open_app")],
                                       "messages": [{"role": "user", "content": "open spotify"}]})
    assert llama["tools"][0]["function"]["name"] == "open_app"                                  # other models: as before
    monkeypatch.setenv("JARVIS_TOOLS_IN_TEXT", "off")
    assert body([t("open_app")])["tools"][0]["function"]["name"] == "open_app"


def test_narrating_an_action_without_doing_it_is_a_claim():
    """qwen3.5: "Setting a timer for ten minutes, sir." with no call. Never spoken as if done."""
    c = intelligence.claims_done
    assert c("Setting a timer for ten minutes, sir.") and c("Adding eggs and milk to your shopping list, sir.")
    assert c("Setting volume to 30, sir.") and c("Okay, sir, opening Spotify now.")
    assert not c("Opening hours are nine to five.") and not c("Making pasta takes ten minutes.")
    assert not c("I'm good, sir.") and not c("Checking your list, sir.")


def test_a_two_word_fragment_never_opens_an_app():
    """Live 2026-11-10: "quiet pencil", "paper window", "velvet window" each OPENED PAINT (4 windows) via the gateway."""
    from core import gateway
    asked = []
    for said in ("quiet pencil", "paper window", "velvet window", "hollow window"):
        d = gateway._decide(said, [], lambda: False, ask=lambda *a, **k: asked.append(a) or None)
        assert d and d.intents[0].kind == "chat", said
    assert asked == []                                         # no model was even asked to plan it
    for said in ("gimme paint", "gimme calc", "notepad please"):
        assert not gateway._bare_pair(said, []), said
    assert not gateway._bare_pair("paper window", [{"role": "assistant", "content": "Which one, sir?"}])   # an answer


def test_a_question_about_what_the_screen_looks_like_goes_to_the_eyes():
    from core import gateway
    for said in ("Look at my screen and tell me what you see.", "What am I looking at?",
                 "Take another screenshot and describe just the layout and the colours, not the text."):
        d = gateway.exact(said)
        assert d and d.intents[0].kind == "chat", said
    assert not gateway.visual_request("Take a look at my screen and tell me which apps are open.")


def test_a_fragment_can_never_launch_a_game():
    """'crimson garden' fuzzily names Crimson Desert (installed): a bare pair with no request word is a fragment."""
    from core import gateway
    for said in ("crimson garden", "crimson lantern", "amber meadow"):
        d = gateway._decide(said, [], lambda: False, ask=lambda *a, **k: None)
        assert d and d.intents[0].kind == "chat", said
    for said in ("gimme paint", "bye bye paint", "gimme calc", "kill paint"):     # never swallowed as a fragment
        d = gateway._decide(said, [], lambda: False, ask=lambda *a, **k: None)
        assert not (d and d.intents and d.intents[0].summary == "short fragment"), said


def test_a_browser_in_front_does_not_swallow_requests_about_the_pc(monkeypatch):
    from core import pcactions, pchybrid
    monkeypatch.setattr(pcactions, "foreground_browser", lambda: True)
    for said in ("find my shadow world project and launch it", "show me the storage that i have on my pc",
                 "bring up the thing I use to draw"):
        assert pchybrid.simple_plan(said) is None, said
    assert pchybrid.simple_plan("search for blender rigging tutorials")["steps"][0]["tool"] == "search_foreground_browser"


def test_a_closing_offer_does_not_turn_a_fragment_into_an_answer():
    """Live 2026-11-10: after "...Anything specific you want me to do with them?" the fragment "quiet pencil" counted as
    an answer and opened Paint. A real question ("Which one, sir: the red or the blue file?") still takes an answer."""
    from core import gateway
    def decide(after):
        h = [{"role": "user", "content": "x"}, {"role": "assistant", "content": after}]
        d = gateway._decide("quiet pencil", h, lambda: False, ask=lambda *a, **k: None)
        return d.intents[0].summary if d and d.intents else None
    for offer in ("I see two monitors. Anything specific you want me to do with them?", "I'm good, sir. Anything on your mind?",
                  "Should I open Paint?", "Want me to check for deals on those too?"):
        assert decide(offer) == "short fragment", offer
    for question in ("Which one, sir: the red or the blue file?", "What should I call it?",
                     "Did the Vanguard rigging hold up on your end?"):
        # never a PC action (live: "quiet pencil" after this last one opened Paint again)...
        assert decide(question) == "short fragment", question
        # ...but the conversation brain still reads it as the answer to a real question
        h = [{"role": "user", "content": "x"}, {"role": "assistant", "content": question}, {"role": "user", "content": "quiet pencil"}]
        assert not intelligence.low_signal_fragment(h), question


def test_the_brain_also_treats_a_fragment_after_an_offer_as_a_fragment():
    h = lambda after, said: [{"role": "user", "content": "x"}, {"role": "assistant", "content": after}, {"role": "user", "content": said}]
    assert intelligence.low_signal_fragment(h("I see a dark window. Want me to tell you more about what's open?", "velvet lantern"))
    assert not intelligence.low_signal_fragment(h("Which one, sir: the red or the blue file?", "velvet lantern"))


def test_clear_mode_requests_run_the_real_skill(monkeypatch):
    """qwen3.5: "Understood, sir." without switching the language, and an INVENTED pairing code with no call."""
    from core import quick
    from tools import registry
    ran = []
    monkeypatch.setattr(registry, "run", lambda name, args: ran.append((name, args)) or ("Speech recognition locked to English.", True))
    assert "English" in quick.handle("From now on, only speak English with me.")
    assert ran[-1] == ("set_conversation_language", {"language": "English"})
    quick.handle("Pair my phone, please.")
    assert ran[-1] == ("pair_phone", {"person": "me"})
    assert not quick._PAIR_MINE.match("repair my phone") and not quick._SPEAK_ONLY.match("Can you speak Spanish?")
    assert intelligence.claims_done("I’ll pair your phone now.")       # curly apostrophe


def test_ohms_law_and_a_new_request_with_no_in_it():
    from core import approvals, quick
    assert "25.5 milliamps" in quick.handle("What's 12 volts across 470 ohms, in milliamps?")
    assert "5.0 milliamps" in quick.handle("what current flows with 5 V across 1k ohms")
    assert "12.00 amps" in quick.handle("120 volts across 10 ohms")
    said = "Google this: Python ModuleNotFoundError no module named yaml. How do I fix it?"
    assert approvals._NEW_REQUEST.match(approvals._norm("Google this: x")) is None   # covered by the google/search guard
    assert approvals.meant("owner", said) != "no"


def test_his_characters_go_to_his_character_skills():
    from core import gateway
    assert gateway.assistant_request("Find my vanguard character") and gateway.assistant_request("rig my character")
    assert not gateway.assistant_request("find my shadow world project")


# ---- independent review 2026-11-10: every confirmed finding stays fixed ------------------------------------------------
def test_review_a_refusal_still_cancels_and_takes_back_a_command(monkeypatch):
    from core import approvals, meaning
    monkeypatch.setattr(meaning, "choose", lambda *a, **k: None)
    for no in ("What? No, don't do that.", "Are you sure? No, leave it.", "Look, no. Don't send it.", "How about no.",
               "Why? Cancel that.", "Is that safe? No."):
        approvals._pending.clear()
        approvals.request("u", "k", "delete report.docx")
        approvals.on_user_message("u", no)
        approvals.on_user_message("u", "Okay, thanks")
        assert "u" not in approvals._pending or not approvals._pending["u"]["approved"], no
    for take_back in ("Delete it, no wait, don't!", "Uninstall... no wait, stop, don't uninstall Discord"):
        approvals.on_user_message("o", "Uninstall Discord")
        approvals.on_user_message("o", take_back)
        assert not approvals.direct("o", "uninstall", "discord") and not approvals.direct("o", "delete"), take_back
    assert approvals._fresh_request(approvals._norm("Google this: Python ModuleNotFoundError no module named yaml."))


def test_review_real_questions_and_real_commands_are_not_fragments():
    from core import gateway
    for q in ("Want me to open Blender or Unreal?", "What should I call it?", "Should I play something calm or upbeat?",
              "Which drive should I look on?", "Should I close both of them?"):
        assert not intelligence.generic_offer(q), q
    assert intelligence.generic_offer("Anything else?") and intelligence.generic_offer("Want me to tell you more?")
    for said in ("task manager", "file explorer", "restart blender", "open downloads", "new tab", "microsoft edge"):
        d = gateway._decide(said, [], lambda: False, ask=lambda *a, **k: None)
        assert not (d and d.intents and d.intents[0].summary == "short fragment"), said


def test_review_web_route_leaves_pc_state_named_sites_and_follow_up_actions_alone():
    from core import gateway
    for said in ("what is the current volume", "what's the latest file in my downloads folder", "search youtube for lofi music",
                 "go to youtube and search for lofi music", "find the latest blender version and install it"):
        assert not gateway.web_research_request(said), said
    assert gateway.web_research_request("Search the internet for the latest NVIDIA driver for my RTX 5070 Ti.")


def test_review_advice_is_not_a_false_claim():
    for s in ("Restarting your router usually fixes slow wifi.", "Making a starter takes a week.",
              "Starting with three runs a week works well, sir.", "Restarting the app may help."):
        assert not intelligence.claims_done(s), s
    assert intelligence.claims_done("Setting a timer for ten minutes, sir.")


def test_review_pairing_speaks_only_the_owner_sentence_and_language_is_truthful(monkeypatch):
    from core import quick
    from tools import registry
    out = {"pair_phone": ('Pairing code for Owner: 123456. Say it out loud, reading each digit: "Your pairing code is 1, 2, 3, 4, 5, 6." '
                          "Always speak every digit, never shorten.", False),
           "set_conversation_language": ("Speech recognition isn't running (text mode); nothing to change.", False)}
    monkeypatch.setattr(registry, "run", lambda name, args: out[name])
    said = quick.handle("Pair my phone, please.")
    assert said == "Your pairing code is 1, 2, 3, 4, 5, 6." and "Say it out loud" not in said
    lang = quick.handle("From now on, only speak Spanish with me.")
    assert "nothing to lock" in lang and "replies change" not in lang


def test_review_ohms_law_only_for_one_current():
    from core import quick
    assert quick.handle("What is the power when 12 volts is across 470 ohms") is None
    assert quick.handle("12 volts across 470 ohms and 330 ohms in series") is None
    assert "microamps" in quick.handle("5 volts across 10 megaohms")


def test_review_the_backup_brain_keeps_its_real_tools_header(monkeypatch):
    monkeypatch.delenv("JARVIS_TOOLS_IN_TEXT_MODELS", raising=False)
    t = {"type": "function", "function": {"name": "open_app", "description": "", "parameters": {"type": "object", "properties": {}}}}
    body = intelligence._ollama_body({"model": "gemma4:12b-it-qat", "temperature": 0.2, "max_tokens": 50, "tools": [t],
                                      "messages": [{"role": "user", "content": "open spotify"}]})
    assert body["tools"][0]["function"]["name"] == "open_app"


def test_review_a_pass_never_counts_for_a_newer_rig(tmp_path, monkeypatch):
    from core import unreal
    monkeypatch.setattr(unreal, "JARVIS_WT", tmp_path)
    rep = tmp_path / "Documentation" / "BuildEvidence" / "t1_report"
    rep.mkdir(parents=True)
    test = {"fullTestPath": "LivingShadowWorld.JARVIS.InGame.ShadowWorldMainCharacter", "state": "Success", "entries": []}
    (rep / "index.json").write_text(json.dumps({"tests": [test]}), encoding="utf-8")
    newer = tmp_path / "Shadow_world_main_character-20991231-235959"            # made after the test
    assert unreal._in_game_gate(newer)["state"] == "not_checked"
    test["state"] = "InProcess"
    (rep / "index.json").write_text(json.dumps({"tests": [test]}), encoding="utf-8")
    assert unreal._in_game_gate(tmp_path / "Shadow_world_main_character-20200101-000000")["state"] == "not_checked"


def test_from_the_phone_game_project_questions_are_asked_of_the_pc():
    """Laptop (gateway off): 'Is my Shadow World character ready for the game?' went to the brain, which invented an answer."""
    from core import pcfast
    for said in ("Is my Shadow World character ready for the game?", "Is Unreal open?", "rig my character", "run the game test"):
        assert pcfast._ABOUT_PC.search(said), said
    assert not pcfast._ABOUT_PC.search("How are you?")


def test_a_short_sentence_takes_the_title_of_the_whole_reply(monkeypatch):
    from core import users
    users.set_current("owner")
    out = intelligence._titled("I’m holding steady, mesye. Ready for whatever you need next.")
    assert "mesye" not in out and ", sir." in out


def test_a_goodbye_is_never_a_fragment():
    for s in ("thanks. That's all for now.", "Thanks, that's all for now."):
        assert not intelligence.low_signal_fragment([{"role": "user", "content": s}]), s
    assert intelligence.low_signal_fragment([{"role": "user", "content": "quiet pencil"}])


def test_owner_words_stop_maths_one_word_and_his_skills_never_become_pc_actions():
    """From data/learning/owner-cases.json (his real lines): 'Stop' / 'Cancel.' planned close_app, 'What's 50% of 100?' opened
    Calculator, 'up' changed the volume, 'rig meshy ai shadow vanguard' opened a path, the gaming coach was clicked."""
    from core import gateway
    for said in ("Stop", "Cancel.", "Jarvis, stop.", "Oh shit, go to sleep.", "Cancel the task.", "What's 50% of 100?", "up", "open",
                 "Closed.", "i need you to rig meshy ai shadow vanguard", "I want you to rig this human in my blender.",
                 "Calibrate my Apex Legend Gaming Coach.", "Make my companion hang out on the search bar."):
        d = gateway.exact(said)
        assert d and d.intents[0].kind == "chat", said
    assert not gateway._STOP_TALK.match("Stop the music") and not gateway._STOP_TALK.match("stop the pc control task and open notepad")


def test_a_stopped_or_started_report_with_nothing_run_is_a_claim():
    """Live: 'Stop' -> 'Practice session stopped, sir.' with no call and nothing running (the tool says 'I was not practising')."""
    for s in ("Practice session stopped, sir.", "Got it, sir. Practice session stopped.", "Timer cancelled, sir.", "Music paused."):
        assert intelligence.claims_done(s.split(". ")[-1] if s.startswith("Got it") else s), s
    for s in ("The rain stopped yesterday.", "My heart stopped when I saw it.", "Sales started in 2019."):
        assert not intelligence.claims_done(s), s


def test_i_stopped_everything_is_a_claim():
    assert intelligence.claims_done("I stopped everything.") and intelligence.claims_done("I set a timer.")


def test_known_issue_31_an_unmeasured_pc_reading_is_never_said():
    """Live 2026-11-10: 'running for roughly four hours' (1 day 16 h), 'GPU at roughly sixty degrees' (46), '120 megabits
    down' (never measured) - with no skill run that turn."""
    u = intelligence.unchecked_reading
    assert u("Your PC has been running for roughly four hours and twenty minutes, sir.", "How long has my PC been on?")
    assert u("Your GPU is running at roughly sixty degrees Celsius.", "How hot is my CPU?")
    assert u("Your internet speed is roughly 120 megabits per second down.", "How fast is my internet?")
    assert u("Let me pull the GPU temp from your system now.", "What's my GPU temperature right now?")
    assert not u("Paris, with about 2 million people.", "What's the capital of France?")
    assert not u("About 9 minutes for a hard-boiled egg.", "How long does it take to boil an egg?")
    assert not u("I can't read the CPU temperature on this PC, sir.", "How hot is my CPU?")


def test_the_status_skill_measures_uptime_and_the_graphics_card(monkeypatch):
    from tools import system
    monkeypatch.setattr(system, "_gpu_reading", lambda: {"gpu": "NVIDIA X", "gpu_temperature_c": 46, "vram_free_gb": 4.6})
    s = system.get_system_status()
    assert s["uptime_hours"] > 0 and s["gpu_temperature_c"] == 46 and s["cpu_temperature"] == "not readable on this PC"
    assert s["memory_total_gb"] >= s["memory_used_gb"] > 0


def test_known_issue_31_a_state_guessed_in_words_is_not_said_and_readings_are_measured(monkeypatch):
    u = intelligence.unchecked_reading
    assert u("Your CPU is running cool right now, sir.", "How hot is my CPU?")
    assert u("Your GPU has plenty of VRAM available right now, sir.", "How much VRAM is free?")
    assert not u("I'm running smooth, sir.", "How are you?")                 # not a question about the PC
    from core import quick
    from tools import registry
    import json as _json
    status = {"cpu_percent": 30.0, "memory_used_gb": 24.5, "memory_total_gb": 31.5, "disk_free_gb": 662.0, "uptime_hours": 40.2,
              "cpu_temperature": "not readable on this PC", "gpu": "NVIDIA", "gpu_temperature_c": 43, "gpu_load_percent": 18,
              "vram_free_gb": 4.7, "vram_total_gb": 15.9}
    monkeypatch.setattr(registry, "run", lambda n, a: (_json.dumps(status), False))
    assert quick.handle("How hot is my CPU?") == "Your graphics card is at 43 degrees; I can't read the CPU temperature on this PC, sir."
    assert quick.handle("How long has my PC been on?") == "It has been on for 1 day and 16 hours, sir."
    assert quick.handle("How much VRAM is free?") == "4.7 GB of video memory is free, of 15.9, sir."
    assert "speed test" in quick.handle("How fast is my internet?")
    assert quick.handle("How hot is it outside?") is None and quick.handle("My PC is running hot today.") is None
