"""Tests for the Living Desktop Companion slice (core/companion.py + core/hud.py).

These cover the pure-Python surface — the event contract validator, idle
picker cooldowns, position clamping across multiple monitors, settings
persistence, bounded event buffer, state resolver — and the behavioural glue
in core/hud.py (mode arg, _send validation, no-op without a running HUD).

The renderer in app/companion.html is checked by a page-content assertion
that targets the actual module exports the Python side relies on, not just a
string substring.
"""
import json
import math
from pathlib import Path

import pytest

from core import companion as c
from core import hud


# ---------------------------- validate_event ---------------------------- #

@pytest.mark.parametrize("msg", [
    None, "state", 42, [], {},
    {"type": None}, {"type": "state"}, {"type": "state", "value": 7},
    {"type": "state", "value": "flying"},
    {"type": "level"}, {"type": "level", "value": "abc"},
    {"type": "level", "value": float("nan")},
    {"type": "line", "who": "bot", "text": "hi"},
    {"type": "line", "who": "jarvis"},
    {"type": "line", "who": "jarvis", "text": 123},
    {"type": "idle", "value": "robot"},
    {"type": "position", "value": {"x": "a", "y": 1}},
    {"type": "settings", "value": "nope"},
    {"type": "weird"},
])
def test_validate_event_rejects_bad_input(msg):
    assert c.validate_event(msg) is None


@pytest.mark.parametrize("value, expected", [
    ("idle", "idle"), ("sleeping", "idle"), ("LISTENING", None),
    ("speaking", "speaking"), ("error", "error"),
])
def test_validate_event_state_values(value, expected):
    out = c.validate_event({"type": "state", "value": value})
    if expected is None:
        assert out is None
    else:
        assert out == {"type": "state", "value": expected}


@pytest.mark.parametrize("v, expected", [
    (0, 0.0), (0.5, 0.5), (1.0, 1.0),
    (-1, 0.0), (2.5, 1.0),
    ("0.4", 0.4),                     # str that is castable is allowed
])
def test_validate_event_level_clamps(v, expected):
    out = c.validate_event({"type": "level", "value": v})
    assert out is not None
    assert math.isclose(out["value"], expected, abs_tol=1e-6)


def test_validate_event_passes_through_legacy_types():
    for t in ("badges", "media_clear", "mode", "notify", "quit", "dashboard"):
        out = c.validate_event({"type": t, "x": 1})
        assert out and out["type"] == t and out["x"] == 1


def test_validate_event_truncates_long_lines():
    out = c.validate_event({"type": "line", "who": "jarvis", "text": "x" * 1000})
    assert out and len(out["text"]) == 400


# ---------------------------- resolve_state (full lifecycle) --- #

@pytest.mark.parametrize("current, incoming, expected", [
    # happy path: idle -> listening -> thinking -> speaking -> idle
    ("idle", "listening", "listening"),
    ("listening", "thinking", "thinking"),
    ("thinking", "speaking", "speaking"),
    ("speaking", "idle", "idle"),                 # <-- used to be rejected; must flow
    ("sleeping", "listening", "listening"),       # alias accepted
    # interruption and recovery
    ("speaking", "listening", "listening"),       # user interrupts mid-reply
    ("listening", "muted", "muted"),              # user mutes
    ("muted", "listening", "listening"),          # user unmutes
    ("speaking", "error", "error"),               # TTS crash
    ("error", "idle", "idle"),                    # error cleared
    ("error", "listening", "listening"),          # recover straight into listening
    # garbage is rejected without clobbering current
    ("speaking", "flying", "speaking"),
    ("speaking", None, "speaking"),
    ("speaking", 7, "speaking"),
    # self-transition stays stable
    ("idle", "idle", "idle"),
    ("speaking", "speaking", "speaking"),
])
def test_resolve_state_lifecycle_transitions(current, incoming, expected):
    assert c.resolve_state(current, incoming) == expected


def test_is_ambient_only_covers_idle():
    assert c.is_ambient("idle") and c.is_ambient("sleeping")
    for s in ("listening", "thinking", "speaking", "muted", "error", "working"):
        assert not c.is_ambient(s)


# ---------------------------- IdlePicker --------------------------------- #

def test_idle_picker_respects_cooldowns():
    p = c.IdlePicker()
    # Deterministic PRNG so weight sampling is reproducible
    p._rng.seed(1)
    now = 0.0
    picks = []
    for i in range(80):
        now = i * 0.5
        a = p.choose(now=now)
        if a is not None:
            picks.append((now, a))
    # Breathe (cooldown 1.5s) should not fire twice within 1.5 s
    breaths = [t for t, a in picks if a == "breathe"]
    for i in range(1, len(breaths)):
        assert breaths[i] - breaths[i - 1] >= 1.5 - 1e-9
    # Stretch (big_gesture, 45s cooldown) should respect its own gate
    stretches = [t for t, a in picks if a == "stretch"]
    for i in range(1, len(stretches)):
        assert stretches[i] - stretches[i - 1] >= 45.0 - 1e-9


def test_idle_picker_reduced_motion_blocks_big_gestures():
    p = c.IdlePicker(reduced_motion=True)
    p._rng.seed(0)
    seen = set()
    for i in range(400):
        a = p.choose(now=i * 2.0)
        if a:
            seen.add(a)
    for big in ("weight_shift", "stretch", "wave"):
        assert big not in seen, f"{big} should be suppressed by reduced motion"
    assert "breathe" in seen and "blink" in seen


def test_idle_picker_returns_none_when_all_on_cooldown():
    p = c.IdlePicker()
    # Fire everything once at t=0
    for name in list(c.IDLE_ACTIONS):
        p._last[name] = 0.0
    # 0.1 s later nothing is eligible
    assert p.choose(now=0.1) is None


# ---------------------------- clamp_position ----------------------------- #

PRIMARY = c.Monitor(0, 0, 1920, 1040)
SECOND  = c.Monitor(-1920, -400, 1920, 1080)        # left-of-primary, offset vertically


def test_clamp_position_fits_inside_the_monitor_it_overlaps():
    x, y = c.clamp_position(5000, 5000, 320, 320, [PRIMARY])
    assert 0 <= x <= PRIMARY.width - 320
    assert 0 <= y <= PRIMARY.height - 320


def test_clamp_position_handles_negative_coords_from_second_monitor():
    x, y = c.clamp_position(-1500, -200, 320, 320, [PRIMARY, SECOND])
    assert SECOND.x <= x <= SECOND.right - 320
    assert SECOND.y <= y <= SECOND.bottom - 320


def test_clamp_position_snaps_to_primary_when_monitor_removed():
    # Window remembered on secondary monitor; only primary is present now.
    x, y = c.clamp_position(-1500, -200, 320, 320, [PRIMARY])
    assert 0 <= x <= PRIMARY.width - 320
    assert 0 <= y <= PRIMARY.height - 320


def test_clamp_position_tolerates_garbage_input():
    x, y = c.clamp_position("x", None, 999999, -1, [PRIMARY])  # type: ignore[arg-type]
    assert isinstance(x, int) and isinstance(y, int)
    assert 0 <= x <= PRIMARY.width
    assert 0 <= y <= PRIMARY.height


# ---------------------------- settings I/O ------------------------------- #

def test_safe_settings_clamps_ranges():
    s = c.safe_settings({"quality": "ultra", "scale": 99, "idle_fps": 0, "active_fps": 1000,
                         "dpr_cap": 5.5, "window_size": [1, 999], "reduced_motion": 1, "visible": 0})
    assert s["quality"] == c.DEFAULTS["quality"]      # unknown → default
    assert s["scale"] == 2.0
    assert s["idle_fps"] == 5 and s["active_fps"] == 60
    assert s["dpr_cap"] == 2.0
    assert s["window_size"] == [160, 640]
    assert s["reduced_motion"] is True
    assert s["visible"] is False


def test_safe_settings_falls_back_on_non_dict():
    assert c.safe_settings(None) == c.DEFAULTS
    assert c.safe_settings("oops") == c.DEFAULTS


def test_settings_roundtrip(tmp_path):
    p = tmp_path / "companion-settings.json"
    c.save_settings({"idle_fps": 20, "scale": 1.25}, path=p)
    loaded = c.load_settings(path=p)
    assert loaded["idle_fps"] == 20 and loaded["scale"] == 1.25
    assert loaded["active_fps"] == c.DEFAULTS["active_fps"]      # unmentioned fields default


def test_load_settings_returns_defaults_on_corrupt_file(tmp_path):
    p = tmp_path / "companion-settings.json"
    p.write_text("not { valid json", encoding="utf-8")
    assert c.load_settings(path=p) == c.DEFAULTS


def test_position_roundtrip_and_corruption(tmp_path):
    p = tmp_path / "pos.json"
    assert c.load_position(path=p) is None
    c.save_position(123, -45, path=p)
    assert c.load_position(path=p) == (123, -45)
    p.write_text("{}", encoding="utf-8")
    assert c.load_position(path=p) is None


# ---------------------------- EventBuffer ------------------------------- #

def test_event_buffer_rejects_invalid_and_bounds_size():
    buf = c.EventBuffer(limit=4)
    assert buf.put({"type": "state", "value": "listening"}) is True
    assert buf.put({"type": "notify", "title": "x", "body": "y"}) is True  # legacy allowlisted
    assert buf.put({"type": "weird"}) is False                              # unknown dropped
    assert buf.put({"type": "state", "value": "flying"}) is False
    for _ in range(20):
        buf.put({"type": "line", "who": "sys", "text": "x"})
    assert len(buf) == 4


def test_event_buffer_coalesces_consecutive_levels():
    buf = c.EventBuffer()
    for v in (0.1, 0.2, 0.3, 0.4):
        buf.put({"type": "level", "value": v})
    out = buf.drain()
    assert len(out) == 1 and out[-1] == {"type": "level", "value": 0.4}


def test_event_buffer_does_not_coalesce_level_across_other_events():
    buf = c.EventBuffer()
    buf.put({"type": "level", "value": 0.3})
    buf.put({"type": "state", "value": "idle"})
    buf.put({"type": "level", "value": 0.5})
    assert [e["type"] for e in buf.drain()] == ["level", "state", "level"]


# ---------------------------- core/hud.py mode arg ---------------------- #

def test_hud_mode_name_defaults_to_panel():
    assert hud.mode_name() in ("panel", "companion")


def test_hud_send_drops_bad_level_and_passes_valid(monkeypatch):
    sent = []
    monkeypatch.setattr(hud, "_queue", type("Q", (), {"put_nowait": lambda self, m: sent.append(m)})())
    monkeypatch.setattr(hud, "running", lambda: True)
    hud._last_level = -1.0
    hud.level(float("nan"))      # dropped: NaN
    hud.level("x")               # dropped: non-numeric
    hud.level(0.42)              # accepted
    hud.level(0.421)             # coalesced away (|Δ| < 0.02)
    hud.level(0.6)               # accepted (|Δ| > 0.02)
    # level() also reports what he is doing (the overlay chip): another test may have left a step in core.progress
    assert [m["value"] for m in sent if m.get("type") == "level"] == [0.42, 0.6]


def test_hud_state_sends_normalised_value(monkeypatch):
    sent = []
    monkeypatch.setattr(hud, "_queue", type("Q", (), {"put_nowait": lambda self, m: sent.append(m)})())
    monkeypatch.setattr(hud, "running", lambda: True)
    hud.state("sleeping")                  # alias → idle
    hud.state("listening")
    hud.state("not-a-state")               # dropped
    assert [m["value"] for m in sent] == ["idle", "listening"]


def test_hud_noop_when_not_running(monkeypatch):
    monkeypatch.setattr(hud, "running", lambda: False)
    hud.level(0.5)
    hud.state("speaking")
    # no exception, no queue growth


# ---------------------------- IPC backpressure (R2) --------------------- #

import queue as _queue_mod   # noqa: E402


class _BoundedFakeQueue:
    """Simulates a full mp.Queue so we can prove level is dropped on
    pressure while important events still get through."""

    def __init__(self, limit):
        self.items = []
        self.limit = limit

    def put_nowait(self, m):
        if len(self.items) >= self.limit:
            raise _queue_mod.Full
        self.items.append(m)

    def put(self, m, timeout=None):
        if len(self.items) >= self.limit:
            raise _queue_mod.Full
        self.items.append(m)

    def qsize(self):
        return len(self.items)


def test_level_is_dropped_when_queue_is_full(monkeypatch):
    q = _BoundedFakeQueue(limit=2)
    monkeypatch.setattr(hud, "_queue", q)
    monkeypatch.setattr(hud, "running", lambda: True)
    monkeypatch.setattr(hud, "_dropped_levels", 0, raising=False)
    hud._last_level = -1.0
    # Fill the queue with important traffic
    hud.line("sys", "a")
    hud.line("sys", "b")
    assert q.qsize() == 2
    # Level attempts now fail — none should sneak in and none should raise
    for v in (0.1, 0.3, 0.6, 0.9):
        hud.level(v)
    assert q.qsize() == 2
    assert hud._dropped_levels >= 1
    assert all(m["type"] == "line" for m in q.items)


def test_important_messages_retry_on_pressure_level_does_not(monkeypatch):
    """When put_nowait fails, _send retries the blocking put() for state/line
    (so they wait for the pump to drain) but NOT for level (which drops)."""
    calls = {"nowait": 0, "blocking": 0}

    class RetryTrackingQueue:
        def put_nowait(self, m):
            calls["nowait"] += 1
            raise _queue_mod.Full
        def put(self, m, timeout=None):
            calls["blocking"] += 1
            # Simulate the pump finally draining
            return None

    monkeypatch.setattr(hud, "_queue", RetryTrackingQueue())
    monkeypatch.setattr(hud, "running", lambda: True)
    hud._last_level = -1.0

    hud.state("speaking")        # important → retries blocking put
    hud.line("jarvis", "hi")     # important → retries blocking put
    hud.level(0.5)               # level     → drops, no blocking put

    assert calls["nowait"] == 3
    assert calls["blocking"] == 2, "level must not fall back to blocking put"


def test_stats_reports_bounded_queue_shape():
    s = hud.stats()
    assert set(s) == {"mode", "running", "queue_size", "queue_max", "dropped_levels"}
    assert s["queue_max"] == hud.IPC_MAX


# ---------------------------- Level arbitration (R: BroadcastChannel bug) --- #

def test_panel_quip_source_beats_main_zero_ticks(monkeypatch):
    """R: main.py's steady 0-tick must NOT overwrite a live panel-quip's
    amplitude. core/hud.level(source=...) keeps a source alive for
    _PANEL_QUIP_GRACE_MS after its last non-zero sample."""
    sent = []
    monkeypatch.setattr(hud, "_queue",
                        type("Q", (), {"put_nowait": lambda self, m: sent.append(m)})())
    monkeypatch.setattr(hud, "running", lambda: True)
    hud._last_level = -1.0
    hud._source_last_nonzero_ms.clear()

    hud.level(0.6, source="panel-quip")                  # quip speaks
    hud.level(0.0, source="main")                        # main's zero tick arrives mid-quip — must be dropped
    hud.level(0.4, source="panel-quip")                  # quip continues
    types_sources = [(m["type"], m.get("source"), m["value"]) for m in sent]
    assert ("level", "panel-quip", 0.6) in types_sources
    assert ("level", "panel-quip", 0.4) in types_sources
    assert ("level", "main", 0.0) not in types_sources, "main 0 overwrote the live quip"


def test_main_zero_passes_once_panel_quip_goes_stale(monkeypatch):
    """After the grace window, main's 0 ticks are honored again."""
    import time as _time
    sent = []
    monkeypatch.setattr(hud, "_queue",
                        type("Q", (), {"put_nowait": lambda self, m: sent.append(m)})())
    monkeypatch.setattr(hud, "running", lambda: True)
    hud._last_level = -1.0
    hud._source_last_nonzero_ms.clear()

    # Simulate the quip having spoken a sample a full second ago.
    now_ms = int(_time.monotonic() * 1000)
    hud._source_last_nonzero_ms["panel-quip"] = now_ms - (hud._PANEL_QUIP_GRACE_MS + 100)
    hud._last_level = 0.4
    hud.level(0.0, source="main")                        # now accepted
    assert any(m["type"] == "level" and m["value"] == 0 and m.get("source") == "main" for m in sent)


def test_live_interrupt_from_user_passes_through_both_sources(monkeypatch):
    """Non-zero main samples (real user speech while a quip is still
    playing) must NOT be suppressed — the user interrupting mid-quip is
    exactly when we want the main loop to take over."""
    sent = []
    monkeypatch.setattr(hud, "_queue",
                        type("Q", (), {"put_nowait": lambda self, m: sent.append(m)})())
    monkeypatch.setattr(hud, "running", lambda: True)
    hud._last_level = -1.0
    hud._source_last_nonzero_ms.clear()
    hud.level(0.5, source="panel-quip")
    hud.level(0.7, source="main")                        # user interrupts — allowed
    assert any(m["type"] == "level" and m.get("source") == "main" and m["value"] == 0.7 for m in sent)


def test_validate_event_preserves_source_on_level():
    assert c.validate_event({"type": "level", "value": 0.5, "source": "panel-quip"}) == \
        {"type": "level", "value": 0.5, "source": "panel-quip"}
    assert c.validate_event({"type": "level", "value": 0.5, "source": 123}) == \
        {"type": "level", "value": 0.5}


# ---------------------------- Api.companion_level + integration ---------- #

def test_api_companion_level_routes_to_hud_level(monkeypatch):
    """Pythonic end-to-end: hud/app.py:Api.companion_level pushes through
    core/hud.level with source='panel-quip' and nudges state to speaking
    on first non-zero sample. Verifies without pywebview/webview involved."""
    from hud import app as _app
    calls = {"levels": [], "states": []}
    monkeypatch.setattr(hud, "level", lambda v, source="main": calls["levels"].append((v, source)))
    monkeypatch.setattr(hud, "state", lambda v: calls["states"].append(v))
    monkeypatch.setattr(hud, "_state", {"value": "idle", "since": 0})
    api = _app.Api(mode="companion")

    assert api.companion_level(0.6) is True
    assert ("speaking",) in {(s,) for s in calls["states"]}
    assert (0.6, "panel-quip") in calls["levels"]

    # Zero sample must not re-nudge state.
    calls["states"].clear()
    monkeypatch.setattr(hud, "_state", {"value": "speaking", "since": 0})
    api.companion_level(0.0)
    assert calls["states"] == [], "state must not be touched by a zero sample"


# ---------------------------- --companion launcher flag ---------------- #

def test_launcher_has_companion_flag_without_running_main():
    """main.py exposes --companion as a documented, opt-in flag that wires
    into hud.start(mode='companion'); the default and --no-hud paths are
    byte-for-byte unchanged. The flag's wiring is checked by reading main.py
    rather than invoking main() so this test does not drag in the full
    tool-loading pipeline. The real integration test is in Node + manual
    verification; the handoff status table marks it unverified-live."""
    src = (ROOT / "main.py").read_text(encoding="utf-8")
    # Flag exists and is opt-in (store_true), with help text pointing at the handoff.
    assert '"--companion"' in src and 'action="store_true"' in src
    assert "DEVELOPMENT-HANDOFF.md" in src
    # hud.start now dispatches on the flag; default stays 'panel'.
    assert 'hud.start(mode="companion" if (args.companion or (cfg.companion and not args.no_companion)) else "panel")' in src
    # --no-hud still gates hud.start entirely (companion included).
    assert "args.no_hud" in src
    # The hud.start invocation is inside the "not args.no_hud" branch.
    import re as _re
    guarded = _re.search(r"if not args\.text and not args\.no_hud:\s*\n\s*(?:#[^\n]*\n\s*)*hud\.start\(",
                         src, _re.MULTILINE)
    assert guarded, "hud.start must stay inside the no-hud guard"


# ---------------------------- hud/app.py pump routing ------------------- #

def test_pump_event_classification_covers_the_review_findings():
    """Companion must not swallow panel events (R7). The architecture now
    routes additively: PANEL_LOCAL has the panel-only commands, COMPANION_LOCAL
    has the renderer-only ones, and BOTH_SURFACES carries state/line to both
    windows at once. The legacy PANEL_FORWARDED alias must still name the
    same set so existing regression guards hold."""
    import hud.app as app
    expected_panel = {"mode", "badges", "media", "media_clear", "timers", "notify", "focus", "mini"}
    panel_set = set(getattr(app, "PANEL_LOCAL", app.PANEL_FORWARDED))
    assert panel_set == expected_panel, panel_set
    assert set(app.PANEL_FORWARDED) == expected_panel, "legacy alias must still match"
    # Companion-only events are disjoint from panel-only events.
    comp_local = set(app.COMPANION_LOCAL)
    assert comp_local.isdisjoint(panel_set), (comp_local, panel_set)
    # Level / idle / settings must be among the renderer-only events.
    assert {"level"}.issubset(comp_local | set(getattr(app, "BOTH_SURFACES", frozenset())))
    # State and line reach either companion or both surfaces (not panel-only).
    reach_renderer = comp_local | set(getattr(app, "BOTH_SURFACES", frozenset()))
    assert "state" in reach_renderer and "line" in reach_renderer


# ---------------------------- Partial honesty: DPI/work-area ----------- #

def test_clamp_uses_screen_extents_not_work_areas():
    """R6: core.companion.clamp_position operates on monitor extents from the
    webview Screen list; it does NOT consult the Windows SystemParametersInfo
    work area. The partial-status note in DEVELOPMENT-HANDOFF.md must stay in
    sync with this test — update both together if real work-area support lands."""
    import inspect
    src = inspect.getsource(c.clamp_position)
    assert "work area" in src.lower() or "work_area" in src.lower() or "not implemented" in src.lower(), \
        "clamp_position must document that it is monitor extents, not work-area"


# ---------------------------- renderer / page assertions ---------------- #

ROOT = Path(__file__).resolve().parent.parent


def test_companion_html_bootstraps_against_the_shared_contract():
    page = (ROOT / "app" / "companion.html").read_text(encoding="utf-8")
    # Honest labelling about rig and lip-sync method must survive refactors.
    assert "amplitude" in page.lower()
    assert "phoneme" in page.lower()
    # The window must import the behavior module and expose window.companion
    # with the Python-callable handle/init shape.
    for needle in ("/companion/behavior.js", "window.companion", "handle(msg)",
                   "/vendor/three/three.module.min.js", "pywebviewready",
                   "run2DFallback", "scheduler"):
        assert needle in page, needle
    import re as _re
    assert _re.search(r"\binit\s*\(", page), "init() method missing"
    assert 'canvas id="fallback"' in page or '<canvas id=\"fallback\"' in page


def test_companion_behavior_module_covers_the_contract():
    """Idle actions must mirror Python; the Scheduler and CompanionState
    must be the actual class names the renderer imports."""
    mod = (ROOT / "app" / "companion" / "behavior.js").read_text(encoding="utf-8")
    for needle in ("export class Scheduler", "export class CompanionState",
                   "export class IdlePicker", "export class EventBuffer",
                   "export function validate", "export function resolveState",
                   "export function safeSettings", "LEVEL_STALE_MS"):
        assert needle in mod, needle
    for name in c.IDLE_ACTIONS:
        # The JS IDLE registry uses bare identifier keys.
        assert f"\n  {name}:" in mod, name


def test_character_size_default_and_window_derivation():
    import core.companion as c
    assert c.DEFAULTS["character_px"] == 160 and c.DEFAULTS["idle_fps"] == 20 and c.DEFAULTS["active_fps"] == 30
    assert c.window_for_character(150) == (175, 175)          # 150 px character * measured FIT_MARGIN headroom
    assert c.window_for_character(100) == (117, 117)
    assert c.window_for_character(75) == (87, 87)
    assert c.window_for_character(1) == c.window_for_character(50)
    assert c.window_for_character(9999) == c.window_for_character(400)
    assert c.safe_settings({"character_px": "junk"})["character_px"] == 160
    assert c.safe_settings({"character_px": 5000})["character_px"] == 400


def test_set_character_size_resizes_window_bottom_centred_and_persists(tmp_path, monkeypatch):
    import core.companion as c
    from hud.app import Api
    monkeypatch.setattr(c, "default_settings_path", lambda: tmp_path / "s.json")
    monkeypatch.setattr(c, "default_position_path", lambda: tmp_path / "p.json")

    class Win:                                   # stands in for the pywebview window
        x, y, width, height = 1000, 500, 175, 175
        def resize(self, w, h): self.width, self.height = w, h
        def move(self, x, y): self.x, self.y = x, y

    api = Api(mode="companion"); api._companion = Win()
    monkeypatch.setattr(api, "_enumerate_monitors", lambda: [c.Monitor(0, 0, 1920, 1080)])
    s = api.set_character_size(300)
    assert s["character_px"] == 300 and (api._companion.width, api._companion.height) == c.window_for_character(300)
    w = api._companion
    assert w.x + w.width // 2 == 1000 + 175 // 2 or abs((w.x + w.width // 2) - (1000 + 87)) <= 1   # same centre line
    assert w.y + w.height == 500 + 175                                                              # feet stay put
    assert c.load_settings()["character_px"] == 300                                                  # persisted
    assert api.set_character_size(9999)["character_px"] == 400                                      # clamped
    assert api.set_character_size(150, persist=False)["character_px"] == 150
    assert c.load_settings()["character_px"] == 400                                                  # persist=False leaves disk alone


def test_companion_form_is_keyed_on_a_unique_colour_not_pywebviews_grey():
    """pywebview leaves the WinForms form light grey (#F0F0F0); keying black never matched, so the companion was a
    grey box. The form is repainted KEY_RGB and exactly that colour is keyed out."""
    import hud.app as app
    assert app.KEY_RGB != (0xF0, 0xF0, 0xF0) and max(app.KEY_RGB) <= 8
    calls = []
    orig = app._apply_colorkey_transparency
    app._apply_colorkey_transparency = lambda hwnd, bgr=0: calls.append((hwnd, bgr)) or True
    try:
        class NoNative: native = None
        assert app._make_form_see_through(NoNative(), 123) is False          # no native form: leave the window alone
        assert app._make_form_see_through(NoNative(), 0) is False            # invalid handle
        assert calls == []
    finally:
        app._apply_colorkey_transparency = orig
    src = open(app.__file__, encoding="utf-8").read()
    assert "_make_form_see_through(companion_window, hwnd)" in src


def test_companion_is_off_by_default_and_starts_in_the_top_left_corner(monkeypatch):
    import importlib
    import core.companion as c
    import core.config as config
    assert c.DEFAULT_POSITION == (0, 0)
    assert c.load_position(path=__import__("pathlib").Path("does-not-exist.json")) is None   # unsaved -> default corner
    try:
        monkeypatch.delenv("JARVIS_COMPANION", raising=False)
        assert importlib.reload(config).Config().companion is False   # 2026-11-19: off by default (lagged the PC)
        for on in ("1", "true", "yes", "on"):
            monkeypatch.setenv("JARVIS_COMPANION", on)
            assert importlib.reload(config).Config().companion is True, on
        for off in ("0", "false", "No", "off"):
            monkeypatch.setenv("JARVIS_COMPANION", off)
            assert importlib.reload(config).Config().companion is False, off
    finally:
        monkeypatch.delenv("JARVIS_COMPANION", raising=False)
        importlib.reload(config)
    root = __import__("pathlib").Path(__file__).resolve().parent.parent
    main_src = (root / "main.py").read_text(encoding="utf-8")
    assert "cfg.companion and not args.no_companion" in main_src and "--no-companion" in main_src
    app_src = (root / "hud" / "app.py").read_text(encoding="utf-8")
    assert "load_position() or DEFAULT_POSITION" in app_src


# ---------------------------- smart positioning ---------------------------- #

def _mon():
    import core.companion as c
    return [c.Monitor(0, 0, 1920, 1080)]


def test_auto_place_setting_defaults_on_and_is_boolean():
    import core.companion as c
    assert c.DEFAULTS["auto_place"] is True
    assert c.safe_settings({"auto_place": 0})["auto_place"] is False
    assert c.safe_settings({"auto_place": "yes"})["auto_place"] is True
    assert c.safe_settings({})["auto_place"] is True


def test_resting_candidates_are_corners_inside_the_work_area():
    import core.companion as c
    spots = c.resting_candidates(_mon(), 87, 87, margin=12)
    xy = {(x, y) for (x, y, _) in spots}
    assert (12, 12) in xy                               # top-left, inset by the margin
    assert (1920 - 87 - 12, 12) in xy                   # top-right
    assert (12, 1080 - 87 - 12) in xy                   # bottom-left
    assert (1920 - 87 - 12, 1080 - 87 - 12) in xy       # bottom-right
    for (x, y, _) in spots:                             # every candidate is fully on-screen
        assert 0 <= x <= 1920 - 87 and 0 <= y <= 1080 - 87


def test_plan_stays_put_when_the_spot_is_clear():
    import core.companion as c
    assert c.plan_companion_move(pos=(12, 12), size=(87, 87), monitors=_mon(),
                                 foreground=(900, 900, 400, 150), cursor=(1500, 1000), now=100.0) is None


def test_plan_moves_away_when_a_window_covers_him():
    import core.companion as c
    # a normal (not fullscreen) window lands over the top-left corner; cursor is up there too
    t = c.plan_companion_move(pos=(12, 12), size=(87, 87), monitors=_mon(),
                              foreground=(0, 0, 900, 900), cursor=(100, 100), now=100.0)
    # Living Presence: the nearest clear resting spot (bottom-left), not the corner farthest from the cursor
    assert t == (12, 1080 - 87 - 12)
    assert c._overlap_area(*t, 87, 87, 0, 0, 900, 900) == 0


def test_plan_never_flees_the_cursor_and_avoids_a_maximized_windows_title_bar():
    import core.companion as c
    full = (0, 0, 1920, 1080)
    br = (1920 - 87 - 12, 1080 - 87 - 12)
    # maximized editor, he rests in its quiet bottom corner, the cursor right ON him -> he stays (no fleeing)
    assert c.plan_companion_move(pos=br, size=(87, 87), monitors=_mon(), foreground=full, cursor=(br[0] + 40, br[1] + 40),
                                 now=100.0) is None
    # the same editor, but he sits over its title bar / menus (top-left) -> he moves down, wherever the cursor is
    for cursor in ((30, 30), (1500, 540)):
        t = c.plan_companion_move(pos=(12, 12), size=(87, 87), monitors=_mon(), foreground=full, cursor=cursor, now=100.0)
        assert t is not None and t[1] > 540


def test_plan_respects_cooldown_and_manual_hold():
    import core.companion as c
    covered = dict(pos=(12, 12), size=(87, 87), monitors=_mon(), foreground=(0, 0, 900, 900), cursor=(100, 100))
    assert c.plan_companion_move(now=100.0, last_auto_move=99.5, **covered) is None   # within cooldown
    assert c.plan_companion_move(now=100.0, last_auto_move=90.0, **covered) is not None
    # a passing cursor over a manually-parked companion is respected (no move)...
    cur_only = dict(pos=(12, 12), size=(87, 87), monitors=_mon(), foreground=None, cursor=(30, 30))
    assert c.plan_companion_move(now=100.0, manual_until=200.0, **cur_only) is None
    # ...but a window actually covering his parked spot still relocates him
    assert c.plan_companion_move(now=100.0, manual_until=200.0, **covered) is not None


def test_best_resting_spot_prefers_clear_corners_far_from_the_cursor():
    import core.companion as c
    avoid = [(0, 0, 900, 900)]
    spot = c.best_resting_spot(87, 87, _mon(), avoid, cursor=(100, 100))
    assert c._overlap_any(*spot, 87, 87, avoid) == 0
    assert spot == (1920 - 87 - 12, 1080 - 87 - 12)


def test_move_to_sets_a_manual_hold(monkeypatch, tmp_path):
    import core.companion as c
    from hud.app import Api
    monkeypatch.setattr(c, "default_position_path", lambda: tmp_path / "p.json")

    class Win:
        width = height = 87
        def move(self, x, y): pass

    api = Api(mode="companion"); api._companion = Win()
    monkeypatch.setattr(api, "_enumerate_monitors", lambda: _mon())
    assert api._manual_until == 0.0
    api.move_to(40, 40)
    import time
    assert api._manual_until > time.time() + 10        # ~20 s hold in the future


def test_plan_keeps_him_on_his_current_monitor_when_a_clear_corner_exists_there():
    import core.companion as c
    two = [c.Monitor(0, 0, 1920, 1080), c.Monitor(1920, 0, 1920, 1080)]
    # he's on monitor 0, a normal window covers his top-left corner, cursor up there
    t = c.plan_companion_move(pos=(12, 12), size=(87, 87), monitors=two,
                              foreground=(0, 0, 900, 900), cursor=(100, 100), now=100.0)
    assert t is not None and 0 <= t[0] < 1920 - 87            # stays on monitor 0, not the second screen
    assert c._overlap_area(*t, 87, 87, 0, 0, 900, 900) == 0



# ---------------------------- throw physics (Feature #154) ---------------------------- #

def test_companion_bounds_subtracts_the_window_size_and_picks_the_right_monitor(monkeypatch):
    import hud.app as app
    from hud.app import Api
    import core.companion as c
    monkeypatch.setattr(app, "_work_area_monitors",
                        lambda: [c.Monitor(0, 0, 1920, 1032), c.Monitor(1920, 0, 1920, 1032)])

    class Win:
        width = height = 87
        def move(self, x, y): pass

    api = Api(mode="companion"); api._companion = Win()
    b = api.companion_bounds(10, 10)                       # spans the whole virtual desktop now
    assert b["left"] == 0 and b["top"] == 0
    assert b["right"] == 1920 + 1920 - 87 and b["bottom"] == 1032 - 87   # union of both monitors, size-subtracted
    assert b["floors"] == [{"x0": 0, "x1": 1920 - 87, "y": 1032 - 87},
                           {"x0": 1920, "x1": 1920 + 1920 - 87, "y": 1032 - 87}]   # one floor per monitor
    assert api.companion_bounds.__doc__                    # documented


def test_move_companion_fast_holds_off_autoplacement_and_does_not_persist(monkeypatch, tmp_path):
    import time
    import core.companion as c
    from hud.app import Api
    saved = []
    monkeypatch.setattr(c, "save_position", lambda x, y, path=None: saved.append((x, y)))

    class Win:
        width = height = 87
        moved = None
        def move(self, x, y): self.moved = (x, y)

    api = Api(mode="companion"); api._companion = Win()
    api.move_companion_fast(640, 300)
    assert api._companion.moved == (640, 300)
    assert api._physics_until > time.time() + 0.5          # auto-place will yield
    assert saved == []                                     # fast moves never touch disk
    api.companion_settled(640, 300)
    assert api._physics_until == 0.0 and saved == [(640, 300)]   # settling persists once and releases the hold


def test_a_throw_in_flight_suppresses_auto_placement():
    # plan_companion_move has no knowledge of physics; the hud loop gates on _physics_until.
    # Assert the gate value the loop checks: a future _physics_until means "don't auto-place".
    import time
    from hud.app import Api
    api = Api(mode="companion")
    api._physics_until = time.time() + 2.0
    assert api._physics_until > time.time()                # loop: `if time.time() < api._physics_until: continue`


def test_throw_physics_js_suite_passes():
    import shutil, subprocess, json as _json
    node = shutil.which("node")
    if not node:
        import pytest
        pytest.skip("node not installed")
    root = __import__("pathlib").Path(__file__).resolve().parent.parent
    proc = subprocess.run([node, str(root / "tests" / "node" / "test_throw_physics.mjs")],
                          cwd=str(root), capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, f"node exited {proc.returncode}:\n{proc.stdout}\n{proc.stderr}"
    summary = _json.loads(proc.stdout.strip().splitlines()[-1])
    assert summary["failed"] == 0 and summary["passed"] >= 12, summary


def test_validate_event_accepts_and_clamps_a_throw():
    import core.companion as c
    assert c.validate_event({"type": "throw", "vx": 1400, "vy": -900}) == {"type": "throw", "vx": 1400.0, "vy": -900.0}
    assert c.validate_event({"type": "throw", "vx": 99999, "vy": 0})["vx"] == 6000.0      # clamped
    assert c.validate_event({"type": "throw", "vx": "nan", "vy": 0}) is None               # rejects junk
    assert c.validate_event({"type": "throw"}) == {"type": "throw", "vx": 0.0, "vy": 0.0}  # defaults to no velocity
    from hud.app import COMPANION_LOCAL
    assert "throw" in COMPANION_LOCAL                                                       # routed to the companion


# ---------------------------- app-launch reactions (Feature #151) ---------------------------- #

def test_is_app_window_matches_the_alt_tab_filter():
    import core.companion as c
    ok = dict(visible=True, title="Untitled - Notepad", tool_window=False, has_owner=False, hwnd=10, classname="Notepad")
    assert c.is_app_window(**ok) is True
    assert c.is_app_window(**{**ok, "visible": False}) is False               # hidden
    assert c.is_app_window(**{**ok, "title": "   "}) is False                 # untitled
    assert c.is_app_window(**{**ok, "tool_window": True}) is False            # a tool window (palette/tooltip)
    assert c.is_app_window(**{**ok, "has_owner": True}) is False              # an owned dialog/popup
    assert c.is_app_window(**{**ok, "classname": "Shell_TrayWnd"}) is False   # the taskbar
    assert c.is_app_window(**{**ok, "classname": "Progman"}) is False         # the desktop
    assert c.is_app_window(**{**ok, "exclude_hwnds": {10}}) is False          # one of our own windows


def test_app_reactions_setting_defaults_on_and_is_boolean():
    import core.companion as c
    assert c.DEFAULTS["app_reactions"] is True
    assert c.safe_settings({"app_reactions": 0})["app_reactions"] is False
    assert c.safe_settings({})["app_reactions"] is True


def test_validate_event_accepts_an_appreact():
    import core.companion as c
    assert c.validate_event({"type": "appreact", "target": [800, 400]}) == {"type": "appreact", "target": [800, 400], "comedy": False}
    assert c.validate_event({"type": "appreact", "target": [8, 4], "comedy": True})["comedy"] is True
    assert c.validate_event({"type": "appreact"}) is None                     # needs a target
    assert c.validate_event({"type": "appreact", "target": [1]}) is None      # malformed target
    from hud.app import COMPANION_LOCAL
    assert "appreact" in COMPANION_LOCAL


def test_validate_event_accepts_celebrate_and_it_routes_to_the_companion():
    import core.companion as c
    assert c.validate_event({"type": "celebrate"}) == {"type": "celebrate"}
    from hud.app import COMPANION_LOCAL
    assert "celebrate" in COMPANION_LOCAL


# ---------------------------- Character Studio (Feature #27) ---------------------------- #

def test_studio_profile_roundtrips_a_tint(tmp_path):
    import core.companion_profile as cp
    prof = {"schema_version": 1, "id": "cyan-jarvis", "name": "Cyan Jarvis", "adapter_id": "glb-model-v1",
            "model_url": cp.DEFAULT_MODEL_URL, "rig_url": cp.DEFAULT_RIG_URL,
            "materials": {"shell_light": {"color": "#3a5cff"}}}
    cp.save_profile(prof, root=tmp_path)
    loaded = cp.load_profile("cyan-jarvis", root=tmp_path)
    assert loaded["materials"]["shell_light"]["color"] == "#3a5cff"      # the tint survives save+load
    assert loaded["adapter_id"] == "glb-model-v1" and loaded["model_url"] == cp.DEFAULT_MODEL_URL
    assert "cyan-jarvis" in {p["id"] for p in cp.list_profiles(root=tmp_path)}


def test_studio_api_methods_are_safe_without_a_window():
    from hud.app import Api
    api = Api(mode="companion")                       # no _companion window attached
    assert api._studio is None
    api.companion_retint('{"shell_light": {"color": "#ff0000"}}')   # no-op, must not raise
    api.open_studio()                                  # no server base -> prints + returns, must not raise
    assert api._studio is None


def test_studio_page_exists_and_wires_the_profile_api():
    root = __import__("pathlib").Path(__file__).resolve().parent.parent
    html = (root / "app" / "studio.html").read_text(encoding="utf-8")
    assert "buildAdapterAsync" in html and "retintMaterials" in html
    for needle in ("save_companion_profile", "load_companion_profile", "list_companion_profiles",
                   "set_character_size", "apply_companion_profile"):
        assert needle in html, needle
    panel = (root / "hud" / "index.html").read_text(encoding="utf-8")
    assert "open_studio" in panel and "studioBtn" in panel          # the Customize button opens it


# ---------------------------- drag-and-drop onto him (Feature #152) ---------------------------- #

def test_save_dropped_file_decodes_sanitises_and_writes_to_the_inbox():
    import base64, pathlib
    from hud.app import Api, HERE
    api = Api(mode="companion")
    payload = b"hello jarvis"
    data_url = "data:text/plain;base64," + base64.b64encode(payload).decode()
    saved = api.save_dropped_file("../../evil name!.txt", data_url)
    assert saved is not None
    p = pathlib.Path(saved)
    try:
        assert p.read_bytes() == payload
        from core.companion import data_directory
        assert p.parent == data_directory() / "dropped"          # never escapes the inbox
        assert ".." not in p.name and "/" not in p.name and "!" not in p.name   # sanitised
    finally:
        p.unlink(missing_ok=True)
    assert api.save_dropped_file("x.txt", "not-a-data-url") is None    # rejects junk


def test_companion_page_has_the_drop_catch_handlers():
    root = __import__("pathlib").Path(__file__).resolve().parent.parent
    html = (root / "app" / "companion.html").read_text(encoding="utf-8")
    assert 'addEventListener("drop"' in html and 'addEventListener("dragover"' in html
    assert "runCatch" in html and "save_dropped_file" in html
    # a varied reception (Living Presence): catch / juggle / swing / portal / struggle..., chosen by dropReaction
    assert "dropReaction(meta)" in html and 'phase: "drop"' in html and '(p.phase === "drop" && p.kind === "catch")' in html


def test_validate_event_accepts_a_drop_and_routes_it():
    import core.companion as c
    assert c.validate_event({"type": "drop", "name": "a.png"}) == {"type": "drop", "name": "a.png"}
    assert c.validate_event({"type": "drop"})["name"] == "file"
    from hud.app import COMPANION_LOCAL
    assert "drop" in COMPANION_LOCAL


def test_validate_event_accepts_doze_and_sit_is_a_registered_idle():
    import core.companion as c
    assert c.validate_event({"type": "doze", "on": True}) == {"type": "doze", "on": True}
    assert c.validate_event({"type": "doze"})["on"] is True
    assert c.validate_event({"type": "doze", "on": 0})["on"] is False
    assert "sit" in c.IDLE_ACTIONS and "wander" in c.IDLE_ACTIONS and "dance" in c.IDLE_ACTIONS
    from hud.app import COMPANION_LOCAL
    assert "doze" in COMPANION_LOCAL


def test_seasonal_setting_defaults_on():
    import core.companion as c
    assert c.DEFAULTS["seasonal"] is True
    assert c.safe_settings({"seasonal": 0})["seasonal"] is False


def test_proactive_setting_and_event_route_to_the_companion():
    import core.companion as c
    assert c.DEFAULTS["proactive"] is True
    assert c.safe_settings({"proactive": 0})["proactive"] is False
    assert c.safe_settings({})["proactive"] is True
    # the control event: greeting + break-path flags, with an optional clamped hour
    ev = c.validate_event({"type": "proactive", "greet": 1, "longsession": 0, "hour": 9})
    assert ev == {"type": "proactive", "greet": True, "longsession": False, "hour": 9}
    assert c.validate_event({"type": "proactive"}) == {"type": "proactive", "greet": False, "longsession": False}
    assert c.validate_event({"type": "proactive", "hour": 99})["hour"] == 23
    assert c.validate_event({"type": "proactive", "hour": -5})["hour"] == 0
    assert "hour" not in c.validate_event({"type": "proactive", "hour": "noon"})
    from hud.app import COMPANION_LOCAL
    assert "proactive" in COMPANION_LOCAL


def test_companion_html_wires_proactive_assistance():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    html = (root / "app" / "companion.html").read_text(encoding="utf-8")
    assert "timeGreeting" in html and "shouldSuggestBreak" in html      # both helpers imported and used


def test_a_companion_subtitle_is_spoken_in_his_voice():
    """When he shows a caption of his own words it should be HIM talking, not silent text (owner ask). speakBubble
    shows the bubble and says it via the local `say` TTS, with an anti-overlap guard and quiet-hours restraint for
    the automatic lines."""
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    html = (root / "app" / "companion.html").read_text(encoding="utf-8")
    body = html[html.index("async function speakBubble"):html.index("async function speakBubble") + 1100]
    assert "localSpeech.speak(" in body and "if (!text || proactive) return false" in body   # unasked lines are never said aloud (owner 2026-11-12)
    assert "new SpeechSession" in html and "localSpeech.setMainState(msg.value)" in html   # voice + mouth, night-safe
    # his reactions to what the user just did are voiced
    for marker in ("speakBubble((DROP_LINES", "speakBubble(LAND_LINES", "speakBubble(`Clicks go straight through"):
        assert marker in html, marker
    # the automatic lines are voiced too, but marked proactive (silent in the quiet hours)
    for marker in ("timeGreeting(new Date().getHours()), { proactive: true }", "welcome back, sir.\", { proactive: true }"):
        assert marker in html, marker
    # the sleep ticker is not words — it stays a silent bubble
    assert 'showBubble("z z z' in html
    # a line pushed from the MAIN Jarvis TTS already has audio from that process — the companion must not re-speak it
    line_handler = html[html.index('msg.type === "line"'):html.index('msg.type === "line"') + 200]
    assert "showBubble(msg.text)" in line_handler and "speakBubble(msg.text)" not in line_handler


def test_panel_poke_is_spoken():
    """The round-face panel's poke reply was silent text; it now speaks (both it and the double-poke joke)."""
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    html = (root / "hud" / "index.html").read_text(encoding="utf-8")
    assert "speak(pick(POKES)" in html and "speak(pick(JOKES)" in html


def test_mood_event_validates_and_routes_to_the_companion():
    import core.companion as c
    assert c.MOODS[0] == "neutral" and "annoyed" in c.MOODS and "tired" in c.MOODS
    ev = c.validate_event({"type": "mood", "value": "tired", "amount": 0.8})
    assert ev == {"type": "mood", "value": "tired", "amount": 0.8}
    assert c.validate_event({"type": "mood", "value": "happy"})["amount"] == 0.6   # default strength
    assert c.validate_event({"type": "mood", "value": "happy", "amount": 9})["amount"] == 1.0   # clamped
    assert c.validate_event({"type": "mood", "value": "happy", "amount": -9})["amount"] == 0.0
    assert c.validate_event({"type": "mood", "value": "happy", "amount": "x"})["amount"] == 0.6  # junk → default
    assert c.validate_event({"type": "mood", "value": "neutral"}) is None   # baseline can't be nudged
    assert c.validate_event({"type": "mood", "value": "bogus"}) is None
    assert c.validate_event({"type": "mood"}) is None
    from hud.app import COMPANION_LOCAL
    assert "mood" in COMPANION_LOCAL


def test_companion_html_wires_the_mood_model():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    html = (root / "app" / "companion.html").read_text(encoding="utf-8")
    assert "MoodModel" in html and "mood.bias(now)" in html and "mood.nudge(" in html


def test_is_exclusive_fullscreen_distinguishes_fullscreen_from_maximized():
    import core.companion as c
    mons = [(0, 0, 1920, 1080), (1920, 0, 1920, 1080)]   # FULL monitor bounds (taskbar included)
    # a true full-screen app exactly covers a monitor
    assert c.is_exclusive_fullscreen((0, 0, 1920, 1080), mons) is True
    assert c.is_exclusive_fullscreen((1920, 0, 1920, 1080), mons) is True     # on the second monitor
    assert c.is_exclusive_fullscreen((1, 0, 1919, 1080), mons) is True        # within tolerance
    # a maximized window fills only the work area (taskbar ~48px still shown) -> NOT fullscreen
    assert c.is_exclusive_fullscreen((0, 0, 1920, 1032), mons) is False
    # a normal / small window is never fullscreen
    assert c.is_exclusive_fullscreen((100, 100, 800, 600), mons) is False
    # degenerate inputs are safe
    assert c.is_exclusive_fullscreen(None, mons) is False
    assert c.is_exclusive_fullscreen((0, 0, 1920, 1080), []) is False
    # accepts Monitor objects too (hasattr x/w path)
    from core.companion import Monitor
    assert c.is_exclusive_fullscreen((0, 0, 1920, 1080), [Monitor(0, 0, 1920, 1080)]) is True


def test_hud_app_wires_fullscreen_hide():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    src = (root / "hud" / "app.py").read_text(encoding="utf-8")
    assert "is_exclusive_fullscreen" in src and "_full_monitors" in src and "_fs_hidden" in src


def test_classify_dropped_path_launches_only_shortcuts():
    import core.companion as c
    assert c.classify_dropped_path(r"C:\Users\me\Desktop\Spotify.lnk") == "launch"
    assert c.classify_dropped_path(r"C:\x\bookmark.url") == "launch"
    assert c.classify_dropped_path(r"C:\x\SHORTCUT.LNK") == "launch"            # case-insensitive
    for safe in (r"C:\x\report.pdf", r"C:\x\run.exe", r"C:\x\script.bat", r"C:\x\a.cmd", "", None):
        assert c.classify_dropped_path(safe) == "save"                          # never auto-run exe/bat/cmd
    assert c.DEFAULTS["launch_drops"] is True
    assert c.safe_settings({"launch_drops": 0})["launch_drops"] is False


def test_hud_app_wires_full_path_file_drop():
    """A real drag onto him is taken by WebView2's own drop target (on the child window under the cursor), so the
    old 'decline it and catch WM_DROPFILES on the host' route never fired. The drop now comes through the page with
    each file's full path (pywebview's DOM drop event -> CoreWebView2File.Path)."""
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    src = (root / "hud" / "app.py").read_text(encoding="utf-8")
    assert "_wire_dom_drop(companion_window, api)" in src and 'document.on("drop"' in src
    assert "AllowExternalDrop = False" not in src                         # WebView2 must keep taking drops
    html = (root / "app" / "companion.html").read_text(encoding="utf-8")
    assert "__jarvisFullPathDrops" in html                                 # no double save when the host has the path


def test_dropped_paths_reads_pywebviews_full_paths():
    from hud.app import dropped_paths
    ev = {"type": "drop", "dataTransfer": {"files": [
        {"name": "Spotify.lnk", "pywebviewFullPath": r"C:\Users\me\Desktop\Spotify.lnk"},
        {"name": "notes.txt"},                                            # no path recovered: skipped
        "junk"]}}
    assert dropped_paths(ev) == [r"C:\Users\me\Desktop\Spotify.lnk"]
    assert dropped_paths(None) == [] and dropped_paths({"dataTransfer": None}) == [] and dropped_paths("x") == []


def test_full_path_drop_skips_the_page_reaction_it_already_played(monkeypatch, tmp_path):
    import hud.app as app
    api = app.Api()
    played = []
    api._companion = type("W", (), {"evaluate_js": lambda self, js: played.append(js)})()
    monkeypatch.setattr(app, "HERE", tmp_path / "hud")                   # data/dropped/ lands in tmp
    src = tmp_path / "report.txt"
    src.write_text("hi", encoding="utf-8")
    out = api._handle_native_drops([str(src)], notify_page=False)
    assert out[0]["outcome"].startswith("saved:") and played == []
    api._handle_native_drops([str(src)])
    assert played and "drop" in played[0]


def test_native_drop_probe_recovers_full_paths():
    """The native Win32 drop proof: a real window recovers a dropped file's FULL path via WM_DROPFILES
    + DragQueryFile — what the HTML5 drop route cannot do. Runs the standalone probe."""
    import os
    import subprocess
    import sys
    from pathlib import Path
    if os.name != "nt":
        import pytest; pytest.skip("windows only")
    root = Path(__file__).resolve().parent.parent
    proc = subprocess.run([sys.executable, str(root / "scripts" / "probe_native_drop.py")],
                          capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["ok"] is True and out["captured"] == out["expected"], out


def test_companion_personality_is_voiced_without_subtitle_overlay():
    from pathlib import Path
    html = (Path(__file__).resolve().parents[1] / "app" / "companion.html").read_text(encoding="utf-8")
    assert "#bubble, #bubble.on { display: none !important; }" in html
    body = html[html.index("function playfulCaption"):html.index("function showBubble")]
    assert "speakBubble(" in body and "proactive: true" in body
    assert "FOCUSED.has(userActivity)" in body and "45000" in body
    assert 'companion.mode === "speaking" && !quipEl.paused ? "idle"' in html
    assert "animator?.play(visualMode," in html


def test_old_default_settings_move_to_the_visible_defaults_and_chosen_values_stay(tmp_path):
    """A file that still holds the OLD defaults (75 px, 15 fps) moves to 160 px / 20 fps when read. Any other value is
    the owner's choice and stays, smaller included. Nothing is lifted twice, even if a writer drops defaults_rev."""
    import json
    import core.companion as c

    def load(name, values):
        f = tmp_path / name
        f.write_text(json.dumps(values), encoding="utf-8")
        return c.load_settings(f)
    s = load("old.json", {"character_px": 75, "idle_fps": 15, "active_fps": 30, "quality": "high"})
    assert (s["character_px"], s["idle_fps"], s["active_fps"], s["quality"], s["defaults_rev"]) == (160, 20, 30, "high", 2)
    chosen = load("chosen.json", {"character_px": 50, "idle_fps": 14, "active_fps": 24})
    assert (chosen["character_px"], chosen["idle_fps"], chosen["active_fps"]) == (50, 14, 24)     # deliberate: untouched
    assert load("big.json", {"character_px": 240, "idle_fps": 30})["character_px"] == 240
    assert c.upgrade_settings(c.upgrade_settings({**s})) == s
    norev = {k: v for k, v in s.items() if k != "defaults_rev"}                # an older writer dropped the marker
    again = load("norev.json", norev)
    assert again["character_px"] == 160 and again["idle_fps"] == 20
    assert load("now75.json", {"character_px": 75, "defaults_rev": 2})["character_px"] == 75   # chosen after the change
    assert c.load_settings(tmp_path / "missing.json")["character_px"] == 160
    assert c.safe_settings({"defaults_rev": "junk"})["defaults_rev"] == 1
    assert c.window_for_character(c.DEFAULTS["character_px"]) == tuple(c.DEFAULTS["window_size"])


def test_doing_event_reaches_the_companion_overlay_and_never_blocks_the_voice_loop(monkeypatch):
    """What a tool is busy with (core.progress) goes to the companion as a `doing` event, once per change, polled at
    most four times a second, with a put that never waits; a full queue or a broken progress module costs nothing."""
    import queue
    import core.companion as c
    import core.hud as hud
    from core import progress
    from hud.app import COMPANION_LOCAL
    assert "doing" in COMPANION_LOCAL
    assert c.validate_event({"type": "doing", "value": "  opening   Spotify "}) == {"type": "doing", "value": "opening Spotify"}
    assert c.validate_event({"type": "doing", "value": 5}) is None
    assert len(c.validate_event({"type": "doing", "value": "x" * 500})["value"]) == 120

    class Q:
        def __init__(self):
            self.items, self.full = [], False

        def put_nowait(self, ev):
            if self.full:
                raise queue.Full
            self.items.append(ev)

        def put(self, *a, **k):
            raise AssertionError("a blocking put in the voice loop")
    q = Q()
    clock = [100.0]
    monkeypatch.setattr(hud, "_queue", q)
    monkeypatch.setattr(hud, "running", lambda: True)
    monkeypatch.setattr(hud, "_last_doing", "")
    monkeypatch.setattr(hud, "_doing_poll_at", 0.0)
    monkeypatch.setattr(hud, "_last_level", 0.0)
    monkeypatch.setattr(hud._time, "monotonic", lambda: clock[0])

    def doing():
        return [m for m in q.items if m["type"] == "doing"]
    monkeypatch.setattr(progress, "_now", {"what": "opening Spotify", "since": 1.0, "step": "", "task_start": 1.0})
    q.full = True
    hud.level(0.0)
    assert doing() == [] and hud._last_doing == ""                          # dropped, and NOT remembered as sent
    q.full = False
    clock[0] += 0.1
    hud.level(0.0)
    assert doing() == [], "polled at most every DOING_POLL_S"
    clock[0] += 0.3
    hud.level(0.0)
    clock[0] += 0.3
    hud.level(0.0)
    assert doing() == [{"type": "doing", "value": "opening Spotify"}]      # retried, then sent once
    monkeypatch.setattr(progress, "_now", {"what": "", "since": 0.0, "step": "", "task_start": 0.0})
    clock[0] += 0.3
    hud.level(0.0, source="panel-quip")                                     # only the voice loop's own ticks poll
    assert len(doing()) == 1
    clock[0] += 0.3
    hud.level(0.0)
    assert doing()[-1] == {"type": "doing", "value": ""}
    monkeypatch.setattr(progress, "_now", None)                             # a broken source must not raise
    clock[0] += 0.3
    hud.level(0.0)
