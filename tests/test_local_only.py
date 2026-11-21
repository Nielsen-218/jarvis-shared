"""Owner local-only preference must fail closed before any cloud request."""
from types import SimpleNamespace
import pytest
from core import intelligence,cloudmodel

def test_persistent_local_only_blocks_configured_cloud(monkeypatch):
    monkeypatch.setattr(intelligence.storage,"load_global",lambda *a:{"local_only":True})
    monkeypatch.setenv("JARVIS_INTELLIGENCE","legacy")
    monkeypatch.setenv("GEMINI_API_KEY","test-placeholder")
    assert intelligence.enabled() and intelligence.local_only()
    assert not cloudmodel.available()
    monkeypatch.setattr(cloudmodel.urllib.request,"urlopen",lambda *a,**k:pytest.fail("No cloud network"))
    with pytest.raises(intelligence.LocalFailure,match="local_only_no_cloud"):
        cloudmodel.complete({})

def test_brain_local_failure_never_constructs_paid_client(monkeypatch):
    pytest.importorskip("anthropic")
    pytest.importorskip("httpx2")
    pytest.importorskip("numpy")
    from brain.llm import Brain
    from threading import Event
    monkeypatch.setattr(intelligence,"local_only",lambda:True)
    monkeypatch.setattr(intelligence,"enabled",lambda:True)
    monkeypatch.setattr(intelligence,"request",lambda *a,**k:(_ for _ in ()).throw(intelligence.LocalFailure("offline")))
    monkeypatch.setattr(intelligence,"audit",lambda *a:None)
    brain=Brain.__new__(Brain)
    brain._local_role="fast"
    brain._cancel=Event()
    brain.messages=[{"role":"user","content":"Explain gravity briefly"}]
    brain.system=[{"text":"Local test"}]
    brain.tools=[]
    said=[]
    reply=brain._stream_turn(said.append)
    assert reply.stop_reason=="end_turn" and "paid" not in said[0].lower() and "cloud" not in said[0].lower() and said[0].strip()
    with pytest.raises(intelligence.LocalFailure):brain._cloud_client()

def test_direct_specialists_cannot_construct_paid_clients(monkeypatch,tmp_path):
    anthropic=pytest.importorskip("anthropic")
    from core import journal,vault
    from tools import fashion
    monkeypatch.setattr(intelligence,"local_only",lambda:True)
    monkeypatch.setattr(anthropic,"Anthropic",lambda *a,**k:pytest.fail("No paid client"))
    assert journal._ask("Summarize this conversation")=={}
    with pytest.raises(intelligence.LocalFailure,match="local_only_no_cloud"):
        vault._client()
    assert vault._describe_photo(tmp_path/"not-read.jpg","image/jpeg","Owner note")=="Owner note"
    labels=vault._label("note","Original evidence only","","2026-10-26T00:00")
    assert labels["summary"]=="Original evidence only" and labels["people"]==""
    assert "disabled" in fashion.generate_outfit()["error"]

def test_local_only_paid_planner_stops_before_budget_prompt(monkeypatch):
    from core import pcplanner
    monkeypatch.setattr(intelligence,"local_only",lambda:True)
    monkeypatch.setattr(pcplanner,"ratecard",lambda:pytest.fail("No budget prompt or paid planning"))
    assert pcplanner.cloud_plan("Open the saved document")["state"]=="stopped"

def test_existing_clip_review_routes_to_existing_local_implementation(monkeypatch):
    pytest.importorskip("psutil")
    from tools import gaming
    monkeypatch.setattr(intelligence,"local_only",lambda:True)
    seen=[]
    def local(game,clip,focus):
        seen.append((game,clip,focus))
        return "Local review result"
    monkeypatch.setattr(gaming,"review_clip_locally",local)
    assert gaming.review_clip("latest","cover")=="Local review result"
    assert seen==[("apex","latest","cover")]
