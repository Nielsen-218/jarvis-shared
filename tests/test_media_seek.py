"""'Fast forward the video by one minute' (live 2026-11-07: 38 s, then 'I can't'): seek through Windows' media session."""
import pytest

from core import mediastate, pcactions, pchybrid


@pytest.mark.parametrize("said,want", [
    ("Hey Jarvis, fast forward the video by one minute.", ("forward", 60)), ("rewind 10 seconds", ("back", 10)),
    ("go back thirty seconds", ("back", 30)), ("fast forward 2 minutes", ("forward", 120)), ("skip ahead", ("forward", 10)),
    ("jump ahead a few minutes", ("forward", 180)), ("move it forward by 90 seconds", ("forward", 90)),
    ("open spotify", None), ("go back to the previous video", None), ("fast forward my career", None)])
def test_spoken_seeks_are_understood(said, want):
    assert mediastate.parse_seek(said) == want


def test_seek_is_an_instant_plan_and_skip_this_song_is_still_next():
    assert pchybrid.simple_plan("fast forward the video by one minute")["steps"] == [
        {"tool": "media_command", "arguments": {"command": "forward:60"}}]
    assert pchybrid.simple_plan("skip this song")["steps"][0]["arguments"]["command"] == "next"


def test_the_seek_command_is_validated():
    pcactions.validate({"steps": [{"tool": "media_command", "arguments": {"command": "back:10"}}]})
    for bad in ("forward:", "forward:-5", "sideways:10", "forward:999999"):
        with pytest.raises(ValueError):
            pcactions.validate({"steps": [{"tool": "media_command", "arguments": {"command": bad}}]})


class Run:
    def __init__(self, out):
        self.stdout = out


def test_seek_reports_where_it_landed_and_says_so_when_the_player_cannot(monkeypatch):
    monkeypatch.setattr(mediastate, "sessions", lambda: [{"app": "MSEdge", "status": "Playing", "title": "video"}])
    calls = []
    monkeypatch.setattr(mediastate.subprocess, "run", lambda args, **k: calls.append(args) or Run("MSEdge|True|95|155"))
    out = pcactions.perform("media_command", {"command": "forward:60"}, approved=True)
    assert out["verified"] and out["detail"] == "Forward 1 minute, now at 2:35." and calls[0][-4:] == ["-Command", "forward", "-Seconds", "60"]
    monkeypatch.setattr(mediastate.subprocess, "run", lambda args, **k: Run("Spotify|unsupported|"))
    with pytest.raises(RuntimeError, match="doesn't let Windows move its position"):
        pcactions.perform("media_command", {"command": "back:10"}, approved=True)
    monkeypatch.setattr(mediastate, "sessions", lambda: [])
    assert not mediastate.seek("forward", 10)["verified"]


def test_a_fact_about_the_pc_is_not_replanned():
    """'Nothing is playing' cost a 2-4 s planner call to say the same thing (live 2026-11-08)."""
    from core import pcagent
    calls = []
    out = pcagent.pursue("fast forward the video", {"steps": [{"tool": "media_command", "arguments": {"command": "forward:60"}}]},
                         execute=lambda step, **k: (_ for _ in ()).throw(RuntimeError("Nothing is registered with Windows as playable media right now.")),
                         replan=lambda *a: calls.append(a) or {"steps": []}, observe=lambda: "")
    assert calls == [] and out["state"] == "stopped" and "Nothing is registered" in out["evidence"][-1]["detail"]
