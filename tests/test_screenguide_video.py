"""'Follow this' on a video reads what the video SAYS (transcript) and what it SHOWS (the frame, local vision model)."""
from PIL import Image

from core import screenguide
from tools import pcops, reach


def _front(monkeypatch, url):
    monkeypatch.setattr(pcops, "windows", lambda: [{"foreground": True, "process": "msedge.exe", "title": "How to - YouTube", "hwnd": 7}])
    monkeypatch.setattr(screenguide, "_browser_url", lambda hwnd: url)


def test_frame_steps_reads_the_frame_and_keeps_only_real_steps():
    seen = {}

    def ask(b64):
        seen["b64"] = b64
        return {"steps": ["winget install jqlang.jq", " ", 5, "Open Settings > Apps"]}

    steps = screenguide.frame_steps(7, grab=lambda h: Image.new("RGB", (3000, 2000)), ask=ask)
    assert steps == ["winget install jqlang.jq", "Open Settings > Apps"] and len(seen["b64"]) > 100


def test_frame_steps_is_empty_when_the_vision_model_is_missing():
    def ask(b64):
        raise RuntimeError("no model")
    assert screenguide.frame_steps(7, grab=lambda h: Image.new("RGB", (10, 10)), ask=ask) == []


def test_a_video_combines_what_it_says_and_what_it_shows(monkeypatch):
    _front(monkeypatch, "https://www.youtube.com/watch?v=abc")
    monkeypatch.setattr(reach, "youtube_transcript", lambda url: "Install jq (3 min)\nTranscript: first open a terminal")
    monkeypatch.setattr(screenguide, "frame_steps", lambda hwnd: ["winget install jqlang.jq"])
    where, text = screenguide.read_instructions()
    assert where == "the YouTube video Install jq (3 min)"
    assert "first open a terminal" in text and "Shown in the video:\nwinget install jqlang.jq" in text


def test_a_video_without_captions_is_followed_from_its_frame(monkeypatch):
    _front(monkeypatch, "https://youtu.be/abc")
    monkeypatch.setattr(reach, "youtube_transcript", lambda url: "No captions for this video.")
    monkeypatch.setattr(screenguide, "frame_steps", lambda hwnd: ["Open Settings > Personalization > Colors", "Choose Dark"])
    where, text = screenguide.read_instructions()
    assert where.startswith("the YouTube video") and "Choose Dark" in text


def test_a_video_with_nothing_to_follow_says_so(monkeypatch):
    _front(monkeypatch, "https://youtu.be/abc")
    monkeypatch.setattr(reach, "youtube_transcript", lambda url: "No captions for this video.")
    monkeypatch.setattr(screenguide, "frame_steps", lambda hwnd: [])
    assert screenguide.read_instructions() == ("", "")
