"""Regression and latency-contract tests for the audited PC-control repairs."""
import time
import sys

import pytest
from types import SimpleNamespace

from core import pcactions, pcbridge, pcledger, pchybrid, pcvision


def test_fast_route_uses_the_installed_app_catalog(monkeypatch):
    from tools import apps
    monkeypatch.setattr(apps, "resolve_app", lambda value: "visual studio code" if value.casefold() == "code" else None)
    plan = pchybrid.simple_plan("Open Code")
    assert plan == {"steps": [{"tool": "open_app", "arguments": {"app": "visual studio code"}}]}


def test_explicit_owner_close_is_the_only_sensitive_fast_path_without_review():
    close = {"steps": [{"tool": "close_app", "arguments": {"app": "calculator"}}]}
    mixed = {"steps": [*close["steps"], {"tool": "uninstall_program", "arguments": {"name": "example"}}]}
    assert pchybrid.direct_owner_close(close, "local_automation") is True
    assert pchybrid.direct_owner_close(close, "local_ai") is False
    assert pchybrid.direct_owner_close(mixed, "local_automation") is False


def test_natural_close_variants_remain_deterministic_and_bounded(monkeypatch):
    from tools import apps
    monkeypatch.setattr(apps, "resolve_app", lambda value: "calculator" if "calculator" in value.casefold() else None)
    for phrase in ("turn off calculator", "I don't want to see calculator", "make calculator go away"):
        assert pchybrid.simple_plan(phrase) == {"steps": [{"tool": "close_app", "arguments": {"app": "calculator"}}]}
    assert pchybrid.simple_plan("close this tab") == {"steps": [{"tool": "close_browser_tab", "arguments": {}}]}
    assert pchybrid.simple_plan("close it") == {"steps": [{"tool": "close_foreground", "arguments": {}}]}


def test_direct_owner_close_passes_its_narrow_approval_to_the_typed_method(monkeypatch):
    monkeypatch.setattr(pchybrid.pcbridge, "require_owner", lambda: "owner")
    monkeypatch.setattr(pchybrid.pcledger, "workflow", lambda task: None)
    monkeypatch.setattr(pchybrid.pcledger, "create", lambda *args, **kwargs: "run-id")
    monkeypatch.setattr(pchybrid.pcledger, "latency", lambda *args, **kwargs: None)
    seen = []
    monkeypatch.setattr(pchybrid.pcmethods, "execute", lambda step, **kwargs: seen.append(kwargs["approved"]) or {
        "verified": True, "detail": "closed"})
    result = pchybrid.run("close calculator")
    assert result["state"] == "completed" and seen == [True]


def test_fast_routes_keep_web_searches_typed_and_bounded():
    # main routes a named-site search to that site's results URL; either way it is one typed, bounded open_url step
    assert pchybrid.simple_plan("Search Google for Blender rigging") == {
        "steps": [{"tool": "open_url", "arguments": {"url": "https://www.google.com/search?q=Blender+rigging"}}]}
    assert pchybrid.simple_plan("Search YouTube for Unreal Engine tutorials") == {
        "steps": [{"tool": "open_url", "arguments": {"url": "https://www.youtube.com/results?search_query=Unreal+Engine+tutorials"}}]}
    plan = pchybrid.simple_plan("Find the official Blender rigging documentation and open it")
    assert plan["steps"][0]["tool"] == "open_url" and plan["steps"][0]["arguments"]["url"].startswith("https://docs.blender.org/")


def test_browser_search_reports_window_not_uninspected_content(monkeypatch):
    monkeypatch.setattr(pcactions, "_open_browser_url", lambda url, cancelled: {"verified": True, "detail": "ready", "url": url,
        "content_verified": False})
    out = pcactions.perform("browser_search", {"site": "youtube", "query": "Unreal Engine tutorials"})
    assert out["verified"] and out["content_verified"] is False
    assert "youtube.com/results?search_query=Unreal+Engine+tutorials" in out["url"]


def test_research_reads_verified_pages_and_retains_source_urls(monkeypatch):
    monkeypatch.setattr("core.websearch.research_question", lambda *args, **kwargs: {
        "provider": "fixture", "sources": [{"title": "Official guide", "url": "https://example.com/guide",
        "excerpt": "Verified public text.", "facts": ["Version: 1"], "provider": "direct"}], "unopened": []})
    result = pcactions.perform("research_web", {"query": "example guide"})
    assert result["verified"] and result["sources"][0]["url"] == "https://example.com/guide"
    assert "Read public research sources" in result["detail"]


def test_read_web_page_requires_readable_question_relevant_content(monkeypatch):
    monkeypatch.setattr("core.websearch.open_page", lambda url: {"title": "Guide", "url": url,
        "text": "A verified guide explains rigging.", "provider": "direct", "source_id": "s1"})
    monkeypatch.setattr("core.websearch._excerpt", lambda text, question: "A verified guide explains rigging.")
    monkeypatch.setattr("core.websearch.key_lines", lambda text: ["Version: 1"])
    result = pcactions.perform("read_web_page", {"url": "https://example.com/guide", "question": "rigging"})
    assert result["verified"] and result["source"]["source_id"] == "s1"


def test_edge_voice_maps_to_the_reviewed_local_kokoro_fallback():
    from audio import tts
    assert tts.kokoro_voice("en-GB-RyanNeural") == "kokoro:bm_lewis"
    assert tts.kokoro_voice("kokoro:bm_george") == "kokoro:bm_george"


def test_local_only_voice_never_waits_for_edge_fallback(monkeypatch):
    from audio import tts
    monkeypatch.setenv("JARVIS_LOCAL_ONLY", "1")
    speaker = tts.Speaker("en-GB-RyanNeural", muted=True)
    calls = []
    monkeypatch.setattr(tts, "kokoro_say", lambda text, voice, rate: calls.append((text, voice, rate)) or "pcm")
    monkeypatch.setattr(speaker, "_say_with", lambda *args: (_ for _ in ()).throw(AssertionError("Edge was tried")))
    assert speaker._synthesize("Calculator is ready.") == "pcm"
    assert calls == [("Calculator is ready.", "kokoro:bm_lewis", "+0%")]


def test_open_app_requires_window_readiness_not_only_a_process(monkeypatch):
    from tools import apps
    monkeypatch.setattr(apps, "resolve_app", lambda value: "slow app")
    monkeypatch.setattr(apps, "launch_and_wait", lambda *args, **kwargs: ("No visible window was verified.", False))
    try:
        pcactions.perform("open_app", {"app": "slow app"})
    except RuntimeError as error:
        assert "visible window" in str(error)
    else:
        raise AssertionError("process-only launch was incorrectly marked verified")


def test_open_app_preserves_real_window_verified_outcome(monkeypatch):
    from tools import apps
    monkeypatch.setattr(apps, "resolve_app", lambda value: "slow app")
    monkeypatch.setattr(apps, "launch_and_wait", lambda *args, **kwargs: ("slow app has a visible application window.", True))
    assert pcactions.perform("open_app", {"app": "slow app"})["verified"]


def test_close_app_verifies_uia_window_disappearance_not_uWP_process(monkeypatch):
    from tools import apps
    monkeypatch.setattr(apps, "resolve_app", lambda value: "calculator")
    monkeypatch.setattr(apps, "close_app", lambda value: "Asked 1 visible window(s) to close.")
    observations = iter([[{"title": "Calculator", "process": "ApplicationFrameHost.exe"}], []])
    monkeypatch.setattr(apps, "visible_windows_for_app", lambda value: next(observations, []))
    result = pcactions.perform("close_app", {"app": "calculator"}, approved=True)
    assert result["verified"] is True
    assert result["detail"] == "Closed calculator."


def test_close_app_does_not_claim_success_when_window_remains(monkeypatch):
    from tools import apps
    monkeypatch.setattr(apps, "resolve_app", lambda value: "calculator")
    monkeypatch.setattr(apps, "close_app", lambda value: "Asked 1 visible window(s) to close.")
    monkeypatch.setattr(apps, "visible_windows_for_app", lambda value: [{"title": "Calculator"}])
    now = [0.0]
    def clock():
        now[0] += .5
        return now[0]
    monkeypatch.setattr(pcactions.time, "monotonic", clock)
    monkeypatch.setattr(pcactions.time, "sleep", lambda seconds: None)
    result = pcactions.perform("close_app", {"app": "calculator"}, approved=True)
    assert result["verified"] is False
    assert "still open" in result["detail"] and result["verified"] is False


def test_uia_focus_fallback_targets_the_verified_window(monkeypatch):
    calls = []
    class Window:
        def set_focus(self):
            calls.append("focused")
    class Desktop:
        def __init__(self, **kwargs):
            calls.append(kwargs)
        def window(self, **kwargs):
            calls.append(kwargs)
            return Window()
    monkeypatch.setitem(sys.modules, "pywinauto", SimpleNamespace(Desktop=Desktop))
    assert pcactions._uia_focus(1234) is True
    assert calls == [{"backend": "uia"}, {"handle": 1234}, "focused"]


def test_uia_result_skips_expensive_full_screen_ocr_and_caches(monkeypatch):
    calls = []
    monkeypatch.setattr(pcvision, "uia_elements", lambda *args: ([{"name": "Save", "type": "Button", "center": [1, 1], "enabled": True}], None))
    monkeypatch.setattr(pcvision, "_grab", lambda *_: calls.append("ocr") or (_ for _ in ()).throw(AssertionError("full OCR")))
    pcvision.clear_cache()
    first = pcvision.read_screen(process="editor.exe")
    second = pcvision.read_screen(process="editor.exe")
    assert first["sources"] == ["uia"] and calls == []
    assert second["cached"] is True and second["age_ms"] >= 0


def test_bridge_reuses_recent_reachability_probe(monkeypatch):
    pcbridge._down = {"until": 0.0, "healthy_until": 0.0}
    calls = []
    class Connection:
        def close(self): pass
    monkeypatch.setattr(pcbridge.socket, "create_connection", lambda *args: calls.append(args) or Connection())
    assert pcbridge._answers("https://pc.example.ts.net")
    assert pcbridge._answers("https://pc.example.ts.net")
    assert len(calls) == 1


def test_latency_summary_reports_observed_p50_and_p95(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_PC_STATE_DIR", str(tmp_path))
    started = time.monotonic() - .012
    pcledger.latency("window_readiness", started)
    summary = pcledger.latency_summary()
    assert summary["window_readiness"]["count"] == 1
    assert summary["window_readiness"]["p50_ms"] >= 10
    assert summary["window_readiness"]["p95_ms"] >= summary["window_readiness"]["p50_ms"]


def test_closed_lm_studio_is_not_reprobed_before_every_local_turn(monkeypatch):
    """A refused loopback port costs ~2 s on Windows; the probe once ran before every Ollama voice turn."""
    from core import intelligence
    calls = []

    class Opener:
        def open(self, url, timeout):
            calls.append(url)
            raise ConnectionRefusedError

    monkeypatch.setattr(intelligence, "_lm_down_until", 0.0)
    opener = Opener()
    assert intelligence._lm_models(opener, "http://127.0.0.1:1234/v1") is None
    assert intelligence._lm_models(opener, "http://127.0.0.1:1234/v1") is None
    assert len(calls) == 1


def test_new_process_with_an_unrelated_visible_window_is_not_ready(monkeypatch):
    """Notepad was reported ready ~300 ms before its window existed because any visible window satisfied the UWP clause."""
    from tools import apps, pcops
    monkeypatch.setattr(apps, "_matching_process_pids", lambda name: {10, 11})
    monkeypatch.setattr(apps, "_visible_window_pids", lambda: {99})
    monkeypatch.setattr(pcops, "windows", lambda: [{"process": "chrome.exe", "title": "Docs", "minimized": False}])
    assert apps._ready("Notepad", {10}) is False
    monkeypatch.setattr(pcops, "windows", lambda: [{"process": "ApplicationFrameHost.exe", "title": "Untitled - Notepad", "minimized": False}])
    assert apps._ready("Notepad", {10}) is True

def test_physical_session_records_only_what_it_observed():
    """No ledger task => NOT_OBSERVED (never passed). A failing independent check or an unheard reply => FAIL."""
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("physical_session_unit", Path(__file__).resolve().parents[1] / "scripts/physical_session.py")
    ps = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ps)

    class World:
        def __init__(self, task_after=None):
            self.now, self.task_after, self.started = 1000.0, task_after, 1000.0
        def clock(self):
            return self.now
        def sleep(self, seconds):
            self.now += seconds
        def history(self):
            if self.task_after is None or self.now < self.started + self.task_after:
                return []
            at = self.started + self.task_after
            return [{"component": "local_automation", "at": at, "tool": "open_app", "method": "native", "verified": True},
                    {"component": "latency", "at": at, "stage": "pc_total", "milliseconds": 310.0}]

    def run(world, check, answer="y", audio=False):
        return ps.run_step({"id": "s", "say": "x", "needs_audio": audio, "check": check}, history=world.history, ask=lambda q: answer,
                           say=lambda text: None, wait=5, sleep=world.sleep, clock=world.clock)

    assert run(World(None), lambda: (True, "ok"))["result"] == "NOT_OBSERVED"
    passed = run(World(1.0), lambda: (True, "visible"), audio=True)
    assert passed["result"] == "PASS" and passed["stage_ms"] == {"pc_total": 310.0} and passed["audible_confirmed_by_owner"] is True
    assert run(World(1.0), lambda: (False, "still visible"))["result"] == "FAIL"
    assert run(World(1.0), lambda: (True, "ok"), answer="n", audio=True)["result"] == "FAIL"


# ---- regressions from the owner's live log (2026-11-02): website phrasing and unhelpful failures ---------------------
def test_website_phrases_route_to_the_website_not_to_an_installed_app(monkeypatch):
    from tools import apps
    monkeypatch.setattr(apps, "resolve_app", lambda value: "git gui" if value.casefold() == "github" else None)
    plan = lambda text: pchybrid.simple_plan(text)["steps"][0]
    assert plan("Open YouTube.") == {"tool": "open_url", "arguments": {"url": "https://www.youtube.com/"}}
    assert plan("Hey Jarvis, open YouTube") == {"tool": "open_url", "arguments": {"url": "https://www.youtube.com/"}}
    assert plan("open github") == {"tool": "open_url", "arguments": {"url": "https://github.com/"}}  # never Git GUI
    assert plan("Open YouTube and look for Blender tutorials.")["arguments"]["url"] == "https://www.youtube.com/results?search_query=Blender+tutorials"
    assert plan("open YouTube and play blender tutorials") == {"tool": "browse_open", "arguments": {"site": "youtube", "query": "blender tutorials"}}
    assert plan("watch rigging basics on youtube") == {"tool": "browse_open", "arguments": {"site": "youtube", "query": "rigging basics"}}
    assert plan("find armature on wikipedia")["arguments"]["url"] == "https://en.wikipedia.org/w/index.php?search=armature"
    assert plan("close YouTube") == {"tool": "close_site_tab", "arguments": {"site": "youtube"}}
    # similarly named applications and ordinary apps keep their own routes
    monkeypatch.setattr(apps, "resolve_app", lambda value: {"calculator": "calculator", "github desktop": "github desktop"}.get(value.casefold()))
    assert pchybrid.simple_plan("open calculator")["steps"][0] == {"tool": "open_app", "arguments": {"app": "calculator"}}
    assert pchybrid.simple_plan("open github desktop")["steps"][0] == {"tool": "open_app", "arguments": {"app": "github desktop"}}


def test_verified_browse_requests_are_checked_before_the_plain_site_router():
    """'search youtube for X and open the first video' must not be swallowed as a plain search for the whole sentence."""
    plan = pchybrid.simple_plan("search YouTube for blender rigging tutorial and open the first video")
    assert plan == {"steps": [{"tool": "browse_open", "arguments": {"site": "youtube", "query": "blender rigging tutorial"}}]}
    assert pchybrid.simple_plan("search google for cats")["steps"][0]["tool"] == "open_url"




def test_user_facing_failure_reasons_surface_but_other_exception_text_never_does(monkeypatch):
    from core import pcmethods
    monkeypatch.setattr(pcmethods.pcledger, "method_order", lambda step, candidates: list(candidates))
    monkeypatch.setattr(pcmethods.pcledger, "method_outcome", lambda *a, **k: None)
    monkeypatch.setattr(pcmethods.pcledger, "usage", lambda *a, **k: None)
    monkeypatch.setattr(pcmethods.pcledger, "latency", lambda *a, **k: None)
    step = {"tool": "close_site_tab", "arguments": {"site": "youtube"}}
    def friendly(*a, **k):
        raise RuntimeError("No browser window is showing YouTube; nothing was closed.")
    monkeypatch.setattr(pcmethods, "invoke", friendly)
    assert pcmethods.execute(step, approved=True)["detail"].startswith("No browser window is showing YouTube; nothing was closed.")
    def internal(*a, **k):
        raise OSError(r"C:\Users\private\secret-path.txt access denied")
    monkeypatch.setattr(pcmethods, "invoke", internal)
    detail = pcmethods.execute(step, approved=True)["detail"]
    assert "secret-path" not in detail and detail.startswith("I couldn't close") and "Known safe" not in detail


def test_relative_and_absolute_volume_phrases_from_the_owners_log():
    plan = lambda text: pchybrid.simple_plan(text)
    delta = lambda text: plan(text)["steps"][0]["arguments"]["delta"]
    assert delta("volume up") == 10 and delta("Volume down") == -10 and delta("turn it down a bit") == -10 and delta("louder") == 10
    assert delta("Put the volume down by 50%.") == -50 and delta("put the volume up by 15 percent") == 15 and delta("lower the volume by 50%") == -50
    assert plan("Put the volume up to 50%.")["steps"][0] == {"tool": "set_volume", "arguments": {"percent": 50}}
    assert plan("set volume to 40")["steps"][0]["arguments"] == {"percent": 40}
    for not_a_command in ("the volume is still at 100", "you have not decreased the volume by 50 percent", "turn the lights down", "volume to 120"):
        assert plan(not_a_command) is None, not_a_command


def test_change_volume_is_relative_clamped_and_verified_by_reading_back(monkeypatch):
    from tools import media

    class Endpoint:
        def __init__(self, level, stuck=False):
            self.level, self.muted, self.stuck = level, 1, stuck
        def GetMasterVolumeLevelScalar(self):
            return self.level / 100
        def SetMasterVolumeLevelScalar(self, value, _):
            if not self.stuck:
                self.level = round(value * 100)
        def GetMute(self):
            return self.muted
        def SetMute(self, value, _):
            self.muted = value

    endpoint = Endpoint(76)
    monkeypatch.setattr(media, "_endpoint", lambda: endpoint)
    assert pcactions.perform("change_volume", {"delta": -50})["detail"] == "Volume went from 76% to 26%."
    assert pcactions.perform("change_volume", {"delta": 100})["percent"] == 100 and endpoint.muted == 0  # raising also unmutes
    assert "already at 100%" in pcactions.perform("change_volume", {"delta": 10})["detail"]
    monkeypatch.setattr(media, "_endpoint", lambda: Endpoint(50, stuck=True))
    with pytest.raises(RuntimeError, match="stayed at 50%"):
        pcactions.perform("change_volume", {"delta": 20})
    for bad in (0, 101, -101):
        with pytest.raises(ValueError):
            pcactions.validate({"steps": [{"tool": "change_volume", "arguments": {"delta": bad}}]})


def test_conversational_filler_before_a_command_is_ignored_but_not_added_meaning(monkeypatch):
    from tools import apps
    monkeypatch.setattr(apps, "resolve_app", lambda value: "calculator" if value.casefold() == "calculator" else None)
    from core import pcactions
    monkeypatch.setattr(pcactions, "foreground_browser", lambda: None)   # not the real desktop's front window
    assert pchybrid.simple_plan("Yes, close calculator.")["steps"][0] == {"tool": "close_app", "arguments": {"app": "calculator"}}
    assert pchybrid.simple_plan("Okay sir, open calculator")["steps"][0]["tool"] == "open_app"
    assert pchybrid.simple_plan("Yes, go ahead, search it out.") is None
    assert pchybrid.simple_plan("Yes.") is None


def test_a_failed_find_project_leaves_the_reason_and_the_name_tried_in_the_ledger(monkeypatch):
    """Audit-pc-control defect 6: failed find_project rows said only 'native'. The ledger is the temp one (conftest)."""
    from core import pcledger, pcmethods
    step = {"tool": "find_project", "arguments": {"name": "Orbital Garden"}}

    def not_found(*a, **k):
        raise RuntimeError("I did not find a project matching 'Orbital Garden' in the usual places.")
    monkeypatch.setattr(pcmethods, "invoke", not_found)
    pcmethods.execute(step, approved=True)
    two = lambda *a, **k: {"verified": False, "detail": "Two projects.", "error_kind": "needs_choice", "candidates": [{}, {}]}   # noqa: E731
    monkeypatch.setattr(pcmethods, "invoke", two)
    pcmethods.execute(step, approved=True)
    rows = [r for r in pcledger.history() if r.get("tool") == "find_project"]
    reasons = [r for r in rows if r.get("error_type") == "RuntimeError"]
    assert reasons and reasons[0]["tried"] == "Orbital Garden" and "did not find a project" in reasons[0]["reason"]
    choice = [r for r in rows if r.get("error_kind") == "needs_choice"]
    assert choice and choice[0]["candidates"] == 2 and choice[0]["tried"] == "Orbital Garden"


def test_other_exception_text_never_reaches_the_ledger_reason(monkeypatch):
    from core import pcledger, pcmethods

    def internal(*a, **k):
        raise OSError(r"C:\Users\private\secret-path.txt access denied")
    monkeypatch.setattr(pcmethods, "invoke", internal)
    pcmethods.execute({"tool": "find_project", "arguments": {"name": "x"}}, approved=True)
    assert all("secret-path" not in str(r) for r in pcledger.history())
