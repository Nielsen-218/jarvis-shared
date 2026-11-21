"""The PC <-> laptop links must not fail silently: a rejected pairing shows up on the dashboard."""
import io
import time
import urllib.error

from core import appserver, pcbridge
from tools import vault


def _http_error(code):
    return urllib.error.HTTPError("https://laptop/api/memory/stats", code, "x", {}, io.BytesIO(b"{}"))


def test_pc_reports_a_laptop_that_rejects_its_token(monkeypatch):
    monkeypatch.setattr(pcbridge, "local_pc", lambda: True)
    monkeypatch.setattr(vault, "_remote", lambda: ("https://laptop", "tok"))

    import urllib.request

    def reject(*a, **k):
        raise _http_error(401)
    monkeypatch.setattr(urllib.request, "urlopen", reject)
    assert any("laptop rejects the PC's token" in p for p in appserver.link_problems())


def test_an_asleep_machine_is_not_a_problem(monkeypatch):
    monkeypatch.setattr(pcbridge, "local_pc", lambda: True)
    monkeypatch.setattr(vault, "_remote", lambda: ("https://laptop", "tok"))
    import urllib.request

    def unreachable(*a, **k):
        raise urllib.error.URLError("timed out")
    monkeypatch.setattr(urllib.request, "urlopen", unreachable)
    assert appserver.link_problems() == []


def test_laptop_reports_a_pc_that_rejects_its_token(monkeypatch):
    monkeypatch.setattr(pcbridge, "local_pc", lambda: False)
    monkeypatch.setenv("JARVIS_PC_URL", "https://pc.example.ts.net")

    def rejected(*a, **k):
        raise pcbridge.PCError("The PC rejected this pairing. Its owner device token needs to be linked again.", 502)
    monkeypatch.setattr(pcbridge, "json_request", rejected)
    assert any("PC rejects the laptop's token" in p for p in appserver.link_problems())


def test_dashboard_never_waits_on_the_check(monkeypatch):
    def slow():
        time.sleep(2)
        return ["late"]
    monkeypatch.setattr(appserver, "link_problems", slow)
    monkeypatch.setitem(appserver._LINKS, "at", 0.0)
    monkeypatch.setitem(appserver._LINKS, "value", [])
    t = time.monotonic()
    assert appserver.link_problems_cached() == [] and time.monotonic() - t < 0.5
