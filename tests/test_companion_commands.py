from core import companion as c, hud
from tools.interface import companion


def test_reuses_single_existing_companion_tool(monkeypatch):
    called=[]
    monkeypatch.setattr(hud,"companion_action",lambda action:called.append(action) or True)
    assert "Asked" in companion("home") and called==["home"]


def test_robot_command_validated_and_not_raw_script():
    assert c.validate_event({"type":"companion_action","action":"taskbar"}) == {"type":"companion_action","action":"taskbar"}
    assert c.validate_event({"type":"companion_action","action":"shell(code)"}) is None


def test_voice_fast_path_does_not_need_model(monkeypatch):
    actions=[]
    monkeypatch.setattr(hud,"companion_action",lambda action:actions.append(action) or True)
    assert hud.own_screen("sit on my taskbar") and actions==["taskbar"]
    assert hud.own_screen("show me your bedroom") and actions[-1]=="home"


def test_unrelated_personal_room_and_flight_are_not_robot_commands(monkeypatch):
    monkeypatch.setattr(hud,"companion_action",lambda action:(_ for _ in ()).throw(AssertionError("unexpected")))
    assert hud.own_screen("how do I decorate my bedroom") is None
    assert hud.own_screen("find a flight home") is None
