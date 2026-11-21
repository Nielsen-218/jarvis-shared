"""Engineering Eyes (Issue #9): identify, safety, guided projects — with the vision model mocked."""
import base64
import io

import pytest
from PIL import Image

from core import engeyes, engrecipes


def _photo(w=400, h=300) -> str:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (30, 90, 160)).save(buf, "JPEG")
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


@pytest.fixture(autouse=True)
def local(monkeypatch):
    monkeypatch.setattr(engeyes, "read_markings", lambda img: [{"text": "ATMEGA328P", "confidence": 0.9,
                                                               "box": [0.4, 0.4, 0.5, 0.5]}])
    monkeypatch.setattr(engeyes, "model", lambda: "qwen3-vl:8b-instruct")
    monkeypatch.setattr(engeyes, "warm", lambda: None)
    from core import cloudmodel
    monkeypatch.setattr(cloudmodel, "available", lambda: False)   # no cloud unless a test turns it on
    engeyes.control("stop")


def _vlm_returning(*answers):
    calls = []
    queue = list(answers)

    def fake(images, prompt, schema, system=""):
        calls.append({"images": len(images), "prompt": prompt, "schema": schema})
        return queue.pop(0) if len(queue) > 1 else queue[0]
    fake.calls = calls
    return fake


# -- identify ------------------------------------------------------------------------------------
def test_identify_needs_a_photo_and_says_it_only_sees_stills():
    with pytest.raises(engeyes.EngError, match="still photos"):
        engeyes.handle({"action": "identify"})
    with pytest.raises(engeyes.EngError):
        engeyes.handle({"action": "identify", "image": "not-an-image"})


def test_identify_grounds_on_markings_and_normalizes_boxes(monkeypatch):
    fake = _vlm_returning({"item": "Arduino Uno R3", "category": "board", "confidence": "high",
                           "what_it_does": "microcontroller board", "evidence": ["UNO silkscreen"],
                           "box_2d": [50, 100, 950, 900], "parts": [{"label": "USB-B port", "box_2d": [0, 400, 270, 660]},
                                                                   {"label": "junk", "box_2d": [5, 5, 6, 6]}]})
    monkeypatch.setattr(engeyes, "_vlm", fake)
    r = engeyes.identify(_photo(), "What am I holding?")
    assert "ATMEGA328P" in fake.calls[0]["prompt"]                         # OCR markings ground the answer
    assert r["box"] == [0.05, 0.1, 0.95, 0.9]                               # 0-1000 relative -> 0-1
    assert [p["label"] for p in r["parts"]] == ["USB-B port"]               # degenerate box dropped
    assert r["reply"].startswith("That's an Arduino Uno R3.") and "It's a microcontroller board." in r["reply"]


def test_boxes_are_read_as_0_1000_relative_unless_they_can_only_be_pixels():
    assert engeyes._box([200, 150, 400, 300], (400, 300)) == [0.2, 0.15, 0.4, 0.3]      # Qwen's 0-1000 scale
    assert engeyes._box([1200, 300, 2400, 900], (2400, 1800)) == [0.5, 0.1667, 1.0, 0.5]  # > 1000: pixels
    assert engeyes._box([900, 900, 1000, 1000], (100, 100)) == [0.9, 0.9, 1.0, 1.0]       # clamped, kept
    assert engeyes._box(["x"], (100, 100)) is None


def test_low_confidence_admits_it_and_asks_for_a_better_photo(monkeypatch):
    monkeypatch.setattr(engeyes, "_vlm", _vlm_returning({
        "item": "small sensor module", "category": "sensor", "confidence": "low", "what_it_does": "It senses something.",
        "evidence": ["4-pin header"], "box_2d": [100, 100, 600, 600], "uncertain_about": "which sensor it is",
        "better_photo": "Turn it over so I can read the chip markings"}))
    r = engeyes.identify(_photo())
    assert r["reply"].startswith("I'm not certain")
    assert "which sensor it is" in r["reply"] and "chip markings" in r["reply"]


# -- safety --------------------------------------------------------------------------------------
@pytest.mark.parametrize("goal", ["wire a new light switch", "connect my ceiling fan to the breaker",
                                  "use a relay to turn my desk lamp on", "repair the inside of my microwave",
                                  "hook up 240 V to this", "build a tesla coil"])
def test_mains_and_high_voltage_work_is_stopped(goal):
    s = engeyes.assess(goal)
    assert s["level"] == "stop" and "electrician" in s["message"]


def test_identifying_a_mains_item_is_a_note_not_a_stop():
    s = engeyes.assess("What is this?", "phone charger", "INPUT 100-240V~ 50/60Hz", doing_work=False)
    assert s["level"] == "caution" and "mains-powered" in s["message"]


@pytest.mark.parametrize("goal, level", [("charge this 18650 lipo pack", "caution"), ("solder header pins", "caution"),
                                          ("use a relay to switch a 12V DC fan", "none"),
                                          ("blink an LED", "none")])
def test_low_voltage_work_is_allowed_with_cautions(goal, level):
    assert engeyes.assess(goal)["level"] == level


# -- planning ------------------------------------------------------------------------------------
def test_common_builds_use_verified_wiring_not_the_model(monkeypatch):
    monkeypatch.setattr(engeyes, "_vlm", lambda *a, **k: pytest.fail("a verified recipe must not call the model"))
    out = engeyes.start_project("blink an LED on pin 13")
    p = out["project"]
    assert p["source"] == "verified" and p["total"] == 6 and "Verified wiring" in out["reply"]
    assert p["step"]["title"] == "Unplug the Arduino"


@pytest.mark.parametrize("goal, key", [("switch a relay from my arduino", "relay"), ("add an OLED display", "oled"),
                                        ("read a push button", "button"), ("measure distance with an hc-sr04", "ultrasonic")])
def test_recipe_matching_prefers_the_specific_build(goal, key):
    assert engrecipes.match(goal)["key"] == key


def test_other_boards_get_no_uno_recipe():
    assert engrecipes.match("blink an LED on my ESP32") is None
    assert engrecipes.match("add an OLED to my Arduino Mega") is None   # Mega I2C pins differ
    assert engrecipes.match("blink an LED on my Arduino Mega")["key"] == "led"


def test_unknown_builds_use_the_free_cloud_planner_then_local(monkeypatch):
    from core import cloudmodel
    plan = {"title": "I2C LCD", "parts": ["16x2 LCD"], "steps": [
        {"title": "Unplug", "instruction": "Unplug the board.", "check": ["No cable in the USB port"]},
        {"title": "Wire", "instruction": "SDA to A4, SCL to A5.", "check": "A wire runs from SDA to A4"}]}
    monkeypatch.setattr(cloudmodel, "available", lambda: True)
    monkeypatch.setattr(cloudmodel, "cooling_down", lambda: False)
    monkeypatch.setattr(cloudmodel, "complete", lambda payload: {"choices": [{"message": {"content": __import__("json").dumps(plan)}}]})
    out = engeyes.start_project("show text on a 16x2 lcd")
    assert out["project"]["source"] == "cloud" and out["project"]["total"] == 2
    assert engeyes._load_project()["steps"][1]["check"] == ["A wire runs from SDA to A4"]   # str check -> list
    engeyes.control("stop")
    monkeypatch.setattr(cloudmodel, "complete", lambda payload: (_ for _ in ()).throw(RuntimeError("503")))
    monkeypatch.setattr(engeyes, "_vlm", lambda *a, **k: plan)
    assert engeyes.start_project("show text on a 16x2 lcd")["project"]["source"] == "local"


def test_a_plan_that_drifts_into_mains_becomes_the_safety_plan(monkeypatch):
    monkeypatch.setattr(engeyes, "_vlm", lambda *a, **k: {"title": "Lamp", "parts": [], "steps": [
        {"title": "Wire it", "instruction": "Connect the relay output to the 120V wall outlet.", "check": ["done"]}]})
    out = engeyes.start_project("make my thing turn on automatically")
    assert out["project"]["source"] == "safety" and out["project"]["safety"]["level"] == "stop"
    assert all("120V" not in s["instruction"] for s in engeyes._load_project()["steps"])


# -- checking steps --------------------------------------------------------------------------------
def _start_led():
    engeyes.start_project("blink an LED on pin 13")
    engeyes.control("skip")  # -> "Place the LED": one requirement


def test_a_confirmed_yes_advances(monkeypatch):
    _start_led()
    monkeypatch.setattr(engeyes, "_vlm", _vlm_returning(
        {"requirements": [{"requirement": "An LED", "note": "LED in row 10", "met": "yes", "evidence_box_2d": [100, 100, 300, 300]}]},
        {"seen": "a red LED in the breadboard", "met": "yes"}))
    out = engeyes.check_step(_photo())
    assert out["verdict"] == "done" and out["project"]["index"] == 2 and out["reply"].startswith("That looks right.")


def test_a_yes_the_zoomed_look_disputes_is_never_done(monkeypatch):
    _start_led()
    monkeypatch.setattr(engeyes, "_vlm", _vlm_returning(
        {"requirements": [{"requirement": "An LED", "note": "LED there", "met": "yes", "evidence_box_2d": [100, 100, 300, 300]}]},
        {"seen": "a resistor with color bands", "met": "no"}))
    out = engeyes.check_step(_photo())
    assert out["verdict"] == "cant_tell" and out["project"]["index"] == 1
    assert "a resistor with color bands" in out["reply"]


def test_a_yes_without_evidence_is_unclear(monkeypatch):
    _start_led()
    monkeypatch.setattr(engeyes, "_vlm", _vlm_returning({"requirements": [{"requirement": "x", "note": "", "met": "yes"}]}))
    assert engeyes.check_step(_photo())["verdict"] == "cant_tell"


def test_a_missing_part_is_not_yet_with_its_reason(monkeypatch):
    _start_led()
    monkeypatch.setattr(engeyes, "_vlm", _vlm_returning(
        {"requirements": [{"requirement": "An LED", "note": "No LED is visible. Only resistors.", "met": "no"}]}))
    out = engeyes.check_step(_photo())
    assert out["verdict"] == "not_yet" and out["reply"] == "Not quite yet: no LED is visible. Fix that and send another photo."


def test_checks_need_a_project_and_a_photo():
    with pytest.raises(engeyes.EngError, match="no project"):
        engeyes.check_step(_photo())
    with pytest.raises(engeyes.EngError, match="photo"):
        engeyes.handle({"action": "check"})


def test_controls_back_skip_stop_status():
    engeyes.start_project("blink an LED")
    assert engeyes.control("skip")["project"]["index"] == 1
    assert engeyes._load_project()["history"][-1]["status"] == "skipped"     # recorded as unverified
    assert engeyes.control("back")["project"]["index"] == 0
    assert engeyes.handle({"action": "status"})["project"]["title"] == "Blink an LED on pin 13"
    assert engeyes.control("stop")["project"] is None and engeyes._load_project() is None


# -- HTTP API --------------------------------------------------------------------------------------
from tests.test_app import client, pair  # noqa: E402,F401


def test_api_relays_to_the_pc_and_is_owner_only(client, monkeypatch):
    from core import pcbridge, users
    h = pair(client)
    assert client.post("/api/eng", headers=h, json={"action": "identify"}).status_code in (400, 503)
    monkeypatch.setattr(pcbridge, "local_pc", lambda: False)
    sent = {}
    monkeypatch.setattr(pcbridge, "json_request", lambda path, method, body: sent.update(path=path, body=body) or
                        {"reply": "That's an Arduino Uno.", "item": "Arduino Uno"})
    r = client.post("/api/eng", headers=h, json={"action": "identify", "image": _photo(), "speak": False})
    assert r.status_code == 200 and r.json()["item"] == "Arduino Uno" and sent["path"] == "/api/pc/eng"
    assert "speak" not in sent["body"]
    wife = users.add("Marie", {"en": "madame"}, "wife")
    assert client.post("/api/eng", headers=pair(client, wife), json={"action": "status"}).status_code == 403


def test_opening_engineer_mode_warms_the_model(monkeypatch):
    warmed = []
    monkeypatch.setattr(engeyes, "warm", lambda: warmed.append(True))
    engeyes.handle({"action": "status"})
    assert warmed == [True]
