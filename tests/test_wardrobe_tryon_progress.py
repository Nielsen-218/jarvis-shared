"""Progress, reasons and "prepare my closet" for the Wardrobe try-on queue (fakes only)."""
import time

import pytest

from core import pcbridge, users
from tests.test_app import client, pair  # noqa: F401
from tests.test_wardrobe_tryon import REAL_RENDER, _jpeg, _person_photo, clock, closet, renders, wife  # noqa: F401
from tools import fashion, fitting, studio


@pytest.fixture(autouse=True)
def _fresh_prepare_state():
    fitting._prep.clear()
    yield
    fitting._prep.clear()


def _wait_prepared():
    for _ in range(200):
        if fitting.prepare_status("owner")["state"] != "working":
            return
        time.sleep(0.05)


def test_progress_lines_move_the_bar_forward_only(closet, clock, monkeypatch):
    _person_photo()

    def fake_studio(person, owner, ids, notes="", progress=None):
        for line in ("Making room on the graphics card", "Loading the image model", "Drawing, step 4 of 8", "Drawing, step 2 of 8"):
            progress(line)
            clock["now"] += 5
        return None
    monkeypatch.setattr(pcbridge, "local_pc", lambda: True)
    monkeypatch.setattr(studio, "render_wardrobe_tryon", fake_studio)
    monkeypatch.setattr(fitting, "_render", REAL_RENDER)
    seen = []
    real = fitting._progress
    monkeypatch.setattr(fitting, "_progress", lambda job, text: (real(job, text), seen.append((job["phase"], job["progress"]))))
    fitting.enqueue(["tee1"])
    fitting.step()                                     # no path comes back, so the job fails; the progress was recorded
    values = [p for _, p in seen]
    assert values == sorted(values) and seen[-1][0] == "Drawing, step 2 of 8"
    assert 0.6 < values[-1] < 0.7                      # step 4 of 8; the later, lower line did not pull it back


def test_progress_of_understands_the_childs_lines():
    assert fitting.progress_of("Drawing, step 8 of 8") == pytest.approx(0.94)
    assert fitting.progress_of("Loading the image model") < fitting.progress_of("Reading the request")
    assert fitting.progress_of("something new") == -1.0


def test_a_leftout_piece_says_why(closet, renders):
    _person_photo()
    job = fitting.enqueue(["tee1", "jean", "jkt1", "hi1", "cap1"])
    assert "no photo" in job["left_reasons"]["cap1"] and "at most 3" in job["left_reasons"]["hi1"]
    assert "tee1" not in job["left_reasons"]


def test_timeouts_are_reported_in_the_runners_own_words(closet, clock, monkeypatch):
    _person_photo()

    def stuck(job):
        raise RuntimeError("The render stopped responding for 4 minutes and was stopped.")
    monkeypatch.setattr(fitting, "_render", stuck)
    fitting.enqueue(["tee1"])
    assert fitting.step()["detail"] == "The render stopped responding for 4 minutes and was stopped."


def test_prepare_closet_cuts_out_pieces_with_a_photo_and_lists_those_without(closet, monkeypatch):
    monkeypatch.setattr(fitting, "cutout_ready", lambda: True)
    monkeypatch.setattr(fitting, "game_running", lambda: "")
    made = []
    monkeypatch.setattr(fitting, "make_cutout", lambda i: made.append(i) or f"cutouts/{i}.png")
    with users.acting_as("owner"):
        for i in ("tee1", "jean", "hi1", "jkt1"):      # the product shots exist, so no GPU step is needed
            (fashion.fdir() / "photos" / f"{i}-clothes.jpg").write_bytes(_jpeg())
        out = fitting.prepare_closet("owner")
        assert out["total"] == 4 and out["needs_photo"] == ["cap1"]
        _wait_prepared()
    st = fitting.prepare_status("owner")
    assert st["state"] == "done" and sorted(made) == ["hi1", "jean", "jkt1", "tee1"] and "Made 4" in st["detail"]


def test_prepare_closet_waits_for_the_card_during_a_game_and_refuses_without_the_model(closet, monkeypatch):
    with users.acting_as("owner"):
        monkeypatch.setattr(fitting, "cutout_ready", lambda: False)
        with pytest.raises(ValueError, match="isn't installed"):
            fitting.prepare_closet("owner")
        monkeypatch.setattr(fitting, "cutout_ready", lambda: True)
        monkeypatch.setattr(fitting, "game_running", lambda: "Apex Legends")
        gpu_calls = []
        monkeypatch.setattr(studio, "prepare_clothes", lambda *a, **k: gpu_calls.append(a))
        monkeypatch.setattr(fitting, "make_cutout", lambda i: None)
        fitting.prepare_closet("owner")
        _wait_prepared()
    st = fitting.prepare_status("owner")
    assert gpu_calls == [] and st["state"] == "done" and sorted(st["waiting"]) == ["hi1", "jean", "jkt1", "tee1"]
    assert "wait for the graphics card" in st["detail"]


def test_prepare_route(client, closet, monkeypatch):
    h = pair(client)
    monkeypatch.setattr(fitting, "cutout_ready", lambda: False)
    r = client.post("/api/closet/prepare", headers=h, json={})
    assert r.status_code == 400 and "isn't installed" in r.json()["detail"]
    assert client.get("/api/closet/prepare", headers=h).json()["prepare"]["state"] == "idle"
