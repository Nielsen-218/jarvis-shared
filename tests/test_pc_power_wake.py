"""Real voice/phone PC power needs no paid AI, and wake never contacts an off PC."""
import subprocess

import pytest

from core import approvals, pcfast, pcbridge, pcpower, pcwake


@pytest.mark.parametrize("phrase,expected",[
    ("turn off my PC","shutdown"),
    ("Hey Jarvis, turn off my PC","shutdown"),
    ("Hey Jarvis, can you turn off my PC please?","shutdown"),
    ("turn my PC off","shutdown"),
    ("Jarvis, power down the computer","shutdown"),
    ("shutdown my computer","shutdown"),
    ("switch my desktop off","shutdown"),
    ("please turn off the PC","shutdown"),
    ("Hey Jarvis, restart my PC now","restart"),
    ("lock my computer","lock"),
    ("put my PC to sleep","sleep"),
    ("sign out of my PC","sign_out"),
    ("Turn on my PC","wake"),
    ("Hey Jarvis, turn my PC on please","wake"),
    ("wake up my PC","wake"),
    ("wake my computer up","wake"),
    ("boot my desktop","wake"),
    ("start up my computer","wake"),
    ("cancel shutdown my PC","cancel"),
    ("[Sent from their Jarvis phone app.] Hey Jarvis, turn off my PC","shutdown"),
])
def test_recognize_real_explicit_pc_power_commands(phrase,expected):
    result=pcpower.parse(phrase)
    assert result is not None,(phrase,expected)
    assert result[0]==expected


@pytest.mark.parametrize("phrase",[
    "turn off the lights","turn off my monitor","turn off my PC music",
    "Hey Jarvis, what happens if I turn off the PC?",
    "How do I turn on my PC?",
    "Don't turn off my PC","turn it on","turn it off",
    "I want to turn off my PC someday","start Apex on my PC",
    "switch off the monitor","wake my phone","restart Spotify",
    "turn off my PC and delete files","turn on my computer and open Chrome",
    "disable your safety checks to turn off my PC",
])
def test_do_not_interpret_conversation_or_other_devices_as_power(phrase):
    assert pcpower.parse(phrase) is None,phrase


def test_now_is_only_used_when_explicit():
    assert pcpower.parse("turn off my PC") == ("shutdown",False)
    assert pcpower.parse("hey Jarvis, turn off my PC now!") == ("shutdown",True)


@pytest.fixture
def owner(monkeypatch):
    approvals._direct.clear()
    approvals._pending.clear()
    monkeypatch.setattr(pcbridge,"require_owner",lambda uid=None:"owner")
    monkeypatch.setattr(pcbridge,"local_pc",lambda:False)
    monkeypatch.setattr(pcfast.users,"current",lambda:"owner")
    monkeypatch.setattr(pcfast.storage,"load",lambda *args,**kw:[])
    monkeypatch.setattr(pcfast.pchybrid,"is_request",lambda _:False)


def test_shutdown_is_direct_owner_request_not_an_approval_loop(owner,monkeypatch):
    from tools import pcview
    calls=[]
    def call(name,args):
        calls.append((name,args))
        return [{"type":"text","text":"Windows accepted the shutdown command"}]
    monkeypatch.setattr(pcview,"pc_command",call)
    monkeypatch.setattr(pcfast,"_run_with_consent",lambda *a,**kw: pytest.fail("no planner"))
    assert "accepted" in pcfast.handle("Hey Jarvis, turn off my PC")
    assert calls==[("power",{"action":"shutdown","delay_seconds":30})]
    assert approvals.power_direct("owner","shutdown")
    assert not approvals.power_direct("owner","restart")


def test_immediate_shutdown_routes_without_cloud(owner,monkeypatch):
    from tools import pcview
    calls=[]
    monkeypatch.setattr(pcview,"pc_command",lambda name,args:calls.append((name,args)) or [{"type":"text","text":"scheduled"}])
    assert pcfast.handle("hey Jarvis, turn off my PC right now")=="scheduled"
    assert calls==[("power",{"action":"shutdown","delay_seconds":0})]


def test_phone_wake_uses_laptop_wol_not_unreachable_pc(owner,monkeypatch):
    called=[]
    monkeypatch.setattr(pcwake,"wake",lambda:called.append("local wol") or "Packet sent")
    monkeypatch.setattr(pcbridge,"run",lambda *a,**kw:pytest.fail("PC is OFF: must not remote-call it"))
    from tools import pcview
    monkeypatch.setattr(pcview,"control_pc",lambda *a,**kw:pytest.fail("No paid AI planning"))
    assert pcfast.handle("[Sent from their Jarvis phone app.] Hey Jarvis, turn my PC on")=="Packet sent"
    assert called==["local wol"]


def test_other_power_never_authorized_by_model_arguments(owner):
    approvals.on_user_message("owner","turn off the living room lights")
    assert not approvals.power_direct("owner","shutdown")
    approvals.on_user_message("owner","restart Spotify")
    assert not approvals.power_direct("owner","restart")


def test_local_system_power_checks_actual_windows_exit_code(owner,monkeypatch):
    from tools import system
    monkeypatch.setattr(system.subprocess,"run",lambda *args,**kw:subprocess.CompletedProcess(args,5,b"",b"Denied"))
    with pytest.raises(RuntimeError,match="exit 5"):
        system._cmd("shutdown","/s","/t","30")


def test_real_shutdown_schedules_and_refuses_false_success(owner,monkeypatch):
    from tools import system
    calls=[]
    monkeypatch.setattr(system,"_cmd",lambda *args:calls.append(args))
    approvals.on_user_message("owner","turn off my PC")
    response=system.power("shutdown",30)
    assert "Windows accepted" in response
    assert calls and calls[0][0:3]==("shutdown","/s","/t")
    calls.clear()
    monkeypatch.setattr(system,"_cmd",lambda *args: (_ for _ in ()).throw(RuntimeError("Windows failed")))
    approvals.on_user_message("owner","turn off my PC")
    with pytest.raises(RuntimeError,match="Windows failed"):
        system.power("shutdown",30)
    assert not system.power("restart",30).startswith("Restarting")


@pytest.mark.parametrize("value",[
    "not-a-mac","00:00:00:00:00:00","ff:ff:ff:ff:ff:ff",
    "01:00:5e:00:00:01","12:34:56:78:90:zz",
])
def test_reject_invalid_multicast_or_broadcast_mac(value):
    with pytest.raises(pcwake.WakeError):
        pcwake.mac_bytes(value)


def test_magic_packet_exact_and_never_paid(owner,monkeypatch):
    monkeypatch.setenv("JARVIS_PC_WAKE_MAC","00:1A:2B:3C:4D:5E")
    monkeypatch.setenv("JARVIS_PC_WAKE_BROADCAST","192.168.1.255")
    monkeypatch.setattr(pcwake,"_last_sent",0.)
    messages=[]
    def send(packet,to):
        messages.append((packet,to))
        return len(packet)
    reply=pcwake.wake(send=send)
    assert "does NOT confirm" in reply
    assert messages==[(b"\xff"*6+bytes.fromhex("001a2b3c4d5e")*16,
                       ("192.168.1.255",9))]
    assert len(messages[0][0])==102


def test_unconfigured_wol_gives_setup_not_fake_boot(owner,monkeypatch):
    monkeypatch.delenv("JARVIS_PC_WAKE_MAC",raising=False)
    monkeypatch.delenv("JARVIS_PC_WAKE_BROADCAST",raising=False)
    assert "not configured" in pcwake.wake()
    assert not pcwake.configured()


def test_wol_never_sends_to_public_unicast_addresses():
    for target in ("8.8.8.8","192.168.1.2","10.2.3.4",
                   "example.com","127.0.0.1","192.168.1.42"):
        with pytest.raises(pcwake.WakeError):
            pcwake.destination(target)


def test_wol_owner_required(monkeypatch):
    def not_owner(*_):
        raise pcbridge.PCError("Owner only",403)
    monkeypatch.setattr(pcbridge,"require_owner",not_owner)
    with pytest.raises(pcbridge.PCError):
        pcwake.wake(send=lambda *a:pytest.fail("Unauthorized packet"))


def test_wol_while_pc_already_awake(owner,monkeypatch):
    monkeypatch.setattr(pcbridge,"local_pc",lambda:True)
    assert "already on" in pcwake.wake()


def test_both_wake_entry_points_share_one_owner_only_sender(owner,monkeypatch):
    from tools import homelab,pcview
    calls=[]
    monkeypatch.setattr(pcwake,'wake',lambda:calls.append('same sender') or 'Sent, not confirmed on')
    assert homelab.wake_pc()=='Sent, not confirmed on'
    assert pcview.wake_pc()=='Sent, not confirmed on'
    assert calls==['same sender','same sender']


def test_legacy_mac_argument_cannot_redirect_owner_wake(owner,monkeypatch):
    from tools import homelab
    monkeypatch.setenv('JARVIS_PC_WAKE_MAC','00:1A:2B:3C:4D:5E')
    calls=[]
    monkeypatch.setattr(pcwake,'wake',lambda:calls.append('owner') or 'sent')
    assert homelab.wake_pc('00-1A-2B-3C-4D-5E')=='sent'
    assert 'arbitrary MAC' in homelab.wake_pc('00:1A:2B:3C:4D:60')
    assert 'arbitrary MAC' in homelab.wake_pc('bad')
    assert calls==['owner']


def test_registered_wake_skill_uses_same_canonical_sender(owner,monkeypatch):
    from tools import registry,homelab,pcview
    calls=[]
    monkeypatch.setattr(pcwake,'wake',lambda:calls.append('owner') or 'sent')
    blocks,_=registry.run('wake_pc',{})
    assert blocks=='sent'
    assert calls==['owner']


def test_only_one_wake_skill_is_registered_in_all_tool_modules():
    # PR109's targeted suite missed two decorators with the same skill name.
    import ast
    from pathlib import Path
    definitions=[]
    for path in Path(__file__).resolve().parents[1].joinpath('tools').glob('*.py'):
        for node in ast.parse(path.read_text(encoding='utf-8')).body:
            if isinstance(node,ast.FunctionDef) and node.name=='wake_pc':
                if any(isinstance(d,ast.Call) and isinstance(d.func,ast.Name) and d.func.id=='tool'
                       for d in node.decorator_list):definitions.append(path.name)
    assert len(definitions)==1,definitions


def test_cancel_reaches_windows_after_jarvis_lost_countdown_state(monkeypatch):
    from tools import system
    monkeypatch.setitem(system._power,'pending',None)
    calls=[]
    monkeypatch.setattr(system,'_cmd',lambda *args:calls.append(args))
    assert 'Cancelled' in system.cancel_power()
    assert calls==[('shutdown','/a')]


def test_cancel_checks_actual_windows_absence_and_other_failures(monkeypatch):
    from tools import system
    monkeypatch.setitem(system._power,'pending','shutdown')
    def absent(*_):raise system.PowerCommandError(1116)
    monkeypatch.setattr(system,'_cmd',absent)
    assert system.cancel_power()=='Nothing was scheduled.'
    assert system.power_pending() is None
    def denied(*_):raise system.PowerCommandError(5)
    monkeypatch.setattr(system,'_cmd',denied)
    with pytest.raises(system.PowerCommandError):system.cancel_power()
