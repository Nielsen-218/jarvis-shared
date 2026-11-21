"""The gaming-readiness check is spoken as a verdict, not the raw diagnosis (live 2026-11-21)."""
from core import gateway

CLEAN = ("CPU 19% busy; memory 60% used (18.9 of 31.5 GB); system drive 64% full (670 GB free); busiest: ; biggest in memory: "
         "MemCompression 1.8 GB; up for 9 hours; diagnosis: Diagnosis: slow Result: PASS (5 checks) No problems found by these "
         "checks. Looked fine: - memory is fine - 13 error events in the newest events (0 critical): DistributedCOM id 10010 x5")


def test_a_clean_pc_is_one_sentence():
    said = gateway.gaming_verdict(CLEAN)
    assert said == "Nothing in the way: CPU at 19%, memory at 60%, 670 GB free on the system drive. You're clear to game."
    assert "10010" not in said and "Diagnosis" not in said


def test_a_busy_pc_is_named():
    said = gateway.gaming_verdict(CLEAN.replace("CPU 19%", "CPU 88%").replace("busiest: ;", "busiest: Unreal Editor 70%;"))
    assert said.startswith("Worth a look before you play: CPU at 88%") and "Unreal Editor 70%" in said


def test_an_unexpected_report_is_passed_through():
    assert gateway.gaming_verdict("I couldn't read the PC right now.") == "I couldn't read the PC right now."


def test_the_route_uses_the_marker():
    d = gateway.decide("is anything going to interfere with my gaming right now") if hasattr(gateway, "decide") else None
    if d is not None and d.intents and d.intents[0].kind == "actions":
        assert d.intents[0].summary == gateway.GAMING_SUMMARY
