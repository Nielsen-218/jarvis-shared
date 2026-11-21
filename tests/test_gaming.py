"""Offline tests for game mode, clips, reviews and trends (no game, no API)."""
import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import numpy as np
import pytest

from tools import gaming, registry


@pytest.fixture(autouse=True)
def no_game():
    gaming.state.update(game=None, started=None)
    yield
    gaming.state.update(game=None, started=None)


def make_clip(path, seconds=6, fps=10):
    import av

    with av.open(str(path), "w") as out:
        st = out.add_stream("mpeg4", rate=fps)
        st.width, st.height, st.pix_fmt = 320, 180, "yuv420p"
        for i in range(seconds * fps):
            img = np.full((180, 320, 3), (i * 4) % 255, np.uint8)
            for p in st.encode(av.VideoFrame.from_ndarray(img, format="rgb24")):
                out.mux(p)
        for p in st.encode(None):
            out.mux(p)
    return path


def test_game_mode_on_and_off(monkeypatch, tmp_path):
    said = []
    monkeypatch.setattr(gaming, "CLIP_DIRS", [tmp_path])
    monkeypatch.setattr(gaming.hud, "hide", lambda: None)
    monkeypatch.setattr(gaming.hud, "show", lambda: None)
    w = gaming.GameWatcher(say=said.append)
    w._enter("Apex Legends")
    assert gaming.game_mode()
    # heavy GPU jobs are refused mid-game, the clip review too
    out, err = registry.run("try_on", {"garment_ids": ["x"], "avatar": "photo", "notes": ""})
    assert err and "Not during a game" in out
    assert "after the session" in gaming.review_clip("latest", "")
    make_clip(tmp_path / "Apex Legends 2026.10.10 - 20.01.mp4")
    gaming.state["started"] = datetime.now() - timedelta(minutes=30)
    w._leave()
    assert not gaming.game_mode()
    assert "1 clip" in said[0] and "debrief" in said[0]
    assert gaming.storage.load(gaming.SESSIONS, [])[0]["minutes"] == 30


def test_frames_are_evenly_sampled(tmp_path):
    clip = make_clip(tmp_path / "c.mp4", seconds=6)
    frames = gaming._frames(clip, max_frames=4)
    assert 3 <= len(frames) <= 4
    assert frames[0][0] == "0:00" and frames[0][1][:2] == b"\xff\xd8"


def test_review_saves_findings_and_trends(monkeypatch, tmp_path):
    import anthropic

    monkeypatch.setattr(gaming, "CLIP_DIRS", [tmp_path])
    make_clip(tmp_path / "fight.mp4")
    review = {"summary": "Solid aim, risky peeks.", "strengths": ["tracking"],
              "weak_points": [{"category": "positioning", "time": "0:03", "what": "wide peek", "fix": "jiggle peek"}],
              "death": "third-partied while healing in the open", "drills": ["peek drills"]}

    class FakeClient:
        def __init__(self, *a, **k):
            self.messages = SimpleNamespace(create=lambda **kw: SimpleNamespace(
                content=[SimpleNamespace(type="text", text=json.dumps(review))],
                usage=SimpleNamespace(input_tokens=1000, output_tokens=200)))

    monkeypatch.setattr(anthropic, "Anthropic", FakeClient)
    monkeypatch.setattr(gaming, "_coach_page", lambda name, r: None)
    out = gaming.review_clip("latest", "")
    assert "wide peek" in out and "third-partied" in out
    out = gaming.gaming_trends()
    assert "1 clip reviewed" in out and "positioning (1)" in out


def test_clip_that_presses_the_instant_replay_hotkey(monkeypatch):
    import keyboard

    pressed = []
    monkeypatch.setattr(keyboard, "send", pressed.append)
    gaming.clip_that()
    assert pressed == ["alt+f10"]
