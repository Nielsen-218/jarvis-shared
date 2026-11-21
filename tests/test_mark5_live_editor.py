"""Mark V (2026-11-09, owner: "build that"): JARVIS uses only an allowlist of read-only tools from Epic's Unreal MCP server
(tests, log, asset search) and only while the owner has Living Shadow World open in the editor; it never opens the editor."""
import json

import pytest

from core import unreal
from tools import unreal as U

LSW_PROJECT = str(unreal.LSW / "LivingShadowWorld.uproject")
OPEN = [{"pid": 1, "exe": "unrealeditor.exe", "project": LSW_PROJECT}]


class Resp:
    def __init__(self, body):
        self.body = json.dumps(body)
        self.headers = {"Mcp-Session-Id": "s1"}

    def read(self):
        return self.body.encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeEditor:
    """Answers like the UE 5.8 Unreal MCP server: call_tool wraps a toolset tool, results come back as JSON text."""

    def __init__(self, answers=None):
        self.calls = []
        self.answers = answers or {}
        self.project_in_log = True

    def __call__(self, req, timeout=0):
        body = json.loads(req.data)
        if body.get("method") == "initialize":
            return Resp({"jsonrpc": "2.0", "id": 1, "result": {"serverInfo": {}}})
        if body.get("method") == "tools/call":
            a = body["params"]["arguments"]
            if a["tool_name"] == "GetLogEntries" and a["arguments"].get("pattern") == "LivingShadowWorld":
                rows = ["LogInit: Project: LivingShadowWorld.uproject"] if self.project_in_log else []
                return Resp({"jsonrpc": "2.0", "id": body["id"], "result": {"content": [{"type": "text", "text": json.dumps({"returnValue": rows})}]}})
            self.calls.append((body["params"]["name"], a["toolset_name"], a["tool_name"], a["arguments"]))
            value = self.answers.get(a["tool_name"], "ok")
            if isinstance(value, Exception):
                return Resp({"jsonrpc": "2.0", "id": body["id"], "result": {"isError": True, "content": [{"type": "text", "text": str(value)}]}})
            text = json.dumps({"returnValue": value if not isinstance(value, (dict, list)) or a["tool_name"] in ("GetLogEntries", "Search")
                               else json.dumps(value)})
            return Resp({"jsonrpc": "2.0", "id": body["id"], "result": {"content": [{"type": "text", "text": text}]}})
        return Resp({})


@pytest.fixture
def editor(monkeypatch):
    fake = FakeEditor()
    monkeypatch.setattr(unreal.urllib.request, "urlopen", fake)
    monkeypatch.setattr(unreal, "unreal_processes", lambda: OPEN)
    monkeypatch.setattr(unreal, "project", lambda path=None: {"name": "LivingShadowWorld", "path": LSW_PROJECT, "mcp_enabled": True})
    return fake


def test_nothing_happens_unless_the_editor_already_has_shadow_world_open(monkeypatch):
    monkeypatch.setattr(unreal, "unreal_processes", lambda: [])
    monkeypatch.setattr(unreal, "project", lambda path=None: {"name": "LivingShadowWorld", "path": LSW_PROJECT, "mcp_enabled": True})
    monkeypatch.setattr(unreal.urllib.request, "urlopen", lambda *a, **k: pytest.fail("no network call when the editor is closed"))
    for say in (U.unreal_tests(), U.unreal_run_tests("combat"), U.unreal_test_results(), U.unreal_log(), U.unreal_find_asset("sword")):
        assert say == U.CLOSED


def test_only_allowlisted_tools_can_ever_be_called(editor):
    live = unreal.Live()
    for toolset, tool in [("EditorToolset.EditorAppToolset", "StartPIE"), ("ConfigSettingsToolset.ConfigSettingsToolset", "SaveSection"),
                          ("PluginToolset.PluginToolset", "SetPluginEnabled"), ("EditorToolset.LogsToolset", "SetVerbosity"),
                          (unreal.TESTS, "Anything")]:
        with pytest.raises(unreal.NotAllowed):
            live.call(toolset, tool)
    assert editor.calls == []
    T, L, S = unreal.TESTS, unreal.LOGS, unreal.SEARCH
    assert unreal.ALLOWED == frozenset({(T, "DiscoverTests"), (T, "ListTests"), (T, "GetTestStatus"), (T, "GetTestResults"),
                                        (T, "RunTests"), (T, "StopTests"), (L, "GetLogEntries"), (L, "GetLogCategories"),
                                        (S, "Search")})        # exactly these nine, nothing that edits, imports, plays or saves


def test_the_call_shape_and_unwrapping(editor):
    editor.answers["ListTests"] = {"tests": ["LivingShadowWorld.Combat.Lunge", "LivingShadowWorld.Combat.Parry",
                                             "Project.Maps.Combat Arena", "Editor.Combat.Import"], "total": 4, "returned": 4}
    # engine and editor tests are never listed or run: only the game's own
    assert unreal.list_tests(unreal.Live(), "combat") == ["LivingShadowWorld.Combat.Lunge", "LivingShadowWorld.Combat.Parry"]
    assert editor.calls[0][:3] == ("call_tool", unreal.TESTS, "DiscoverTests")
    assert editor.calls[1] == ("call_tool", unreal.TESTS, "ListTests", {"nameFilter": "combat", "tagFilter": "", "limit": 0})


def test_run_tests_says_what_passed_and_failed(editor):
    editor.answers["ListTests"] = {"tests": ["LivingShadowWorld.Combat.Lunge", "LivingShadowWorld.Combat.Parry", "LivingShadowWorld.Combat.Block"]}
    editor.answers["RunTests"] = {"done": True}
    editor.answers["GetTestResults"] = {"tests": [{"name": "LivingShadowWorld.Combat.Lunge", "state": "Success"},
                                                  {"name": "LivingShadowWorld.Combat.Parry", "state": "Fail"},
                                                  {"name": "LivingShadowWorld.Combat.Block", "state": "Success"}]}
    assert U.unreal_run_tests("combat") == "I ran 3 tests: 2 passed, 1 failed: Parry."
    ran = [c for c in editor.calls if c[2] == "RunTests"][0]
    assert ran[3] == {"testNames": ["LivingShadowWorld.Combat.Lunge", "LivingShadowWorld.Combat.Parry", "LivingShadowWorld.Combat.Block"]}


def test_run_tests_needs_a_name_and_refuses_huge_batches(editor):
    assert U.unreal_run_tests("").startswith("Which tests?")
    editor.answers["ListTests"] = {"tests": [f"LivingShadowWorld.T{i}" for i in range(80)]}
    assert "80 tests match all; I run at most 50" in U.unreal_run_tests("all")
    assert not any(c[2] == "RunTests" for c in editor.calls)
    editor.answers["ListTests"] = {"tests": []}
    assert U.unreal_run_tests("dragons") == "No tests have dragons in their name."


def test_results_that_do_not_say_are_not_reported_as_passes(editor):
    editor.answers["ListTests"] = {"tests": ["LivingShadowWorld.A"]}
    editor.answers["GetTestResults"] = {"weird": "shape"}
    assert "didn't say which passed" in U.unreal_run_tests("A")


def test_a_tool_error_is_said(editor):
    editor.answers["DiscoverTests"] = RuntimeError("no automation worker")
    assert U.unreal_tests() == "Unreal couldn't list the tests: no automation worker."


def test_log_and_search(editor):
    editor.answers["GetLogEntries"] = ["[2026.11.09-17.57.10:899][  7]LogTemp: Warning: low memory",
                                       "[2026.11.09-17.57.11:000][  8]LogLSW: Error: AttackSpec missing"]
    assert U.unreal_log() == "1 error and 1 warning in the last 2 lines. The newest: LogLSW: Error: AttackSpec missing"
    call = [c for c in editor.calls if c[2] == "GetLogEntries"][0]
    assert call[3] == {"category": "", "pattern": "Error|Warning", "maxEntries": 40}
    editor.answers["Search"] = [{"path": {"refPath": "/Game/Weapons/SM_Sword.SM_Sword"}, "class": {"refPath": "/Script/Engine.StaticMesh"},
                                 "caption": "a sword"}]
    assert U.unreal_find_asset("sword") == "Closest matches for sword: SM_Sword (StaticMesh)."


@pytest.mark.parametrize("said, tool, args", [
    ("What tests does Shadow World have?", "unreal_tests", {"contains": ""}),
    ("List the combat tests in Shadow World.", "unreal_tests", {"contains": "combat"}),
    ("Run the combat tests in Shadow World.", "unreal_run_tests", {"contains": "combat"}),
    ("Run all the tests in Shadow World.", "unreal_run_tests", {"contains": ""}),
    ("How did the Shadow World tests go?", "unreal_test_results", {}),
    ("How did the tests go?", None, None), ("Any errors in the editor?", None, None),
    ("What does the log say?", None, None), ("Run the unit tests in the editor.", None, None),
    ("Find the invoice in my game folder.", None, None),
    ("Any errors in Unreal?", "unreal_log", {"contains": ""}),
    ("Find a sword in Shadow World.", "unreal_find_asset", {"query": "sword"}),
    ("Run the tests.", None, None), ("Find my keys.", None, None),
])
def test_routing(said, tool, args):
    from core import gateway
    plan = gateway.unreal_request(said)
    if tool is None:
        assert plan is None
        return
    assert plan["steps"] == [{"tool": tool, "arguments": args}]


def test_registered_and_authorised():
    from core import authz, pcactions
    for name in pcactions.UNREAL_TOOLS:
        assert name in pcactions.SCHEMAS
    assert authz.effect({"tool": "unreal_run_tests", "arguments": {"contains": "combat"}}) == authz.REVERSIBLE
    for name in ("unreal_tests", "unreal_test_results", "unreal_log", "unreal_find_asset"):
        assert authz.effect({"tool": name, "arguments": {}}) == authz.OBSERVE
    pcactions.validate({"steps": [{"tool": "unreal_run_tests", "arguments": {"contains": ""}}]})


def test_a_closed_editor_through_the_typed_action_stops_cleanly(monkeypatch):
    from core import pcactions
    monkeypatch.setattr(unreal, "unreal_processes", lambda: [])
    monkeypatch.setattr(unreal, "project", lambda path=None: {"name": "LivingShadowWorld", "path": LSW_PROJECT, "mcp_enabled": True})
    assert pcactions.perform("unreal_log", {"contains": ""}) == {"verified": False, "detail": U.CLOSED, "error_kind": "needs_owner"}


def test_a_background_job_is_not_an_open_editor(monkeypatch):
    monkeypatch.setattr(unreal, "unreal_processes", lambda: [{"pid": 2, "exe": "unrealeditor-cmd.exe", "project": LSW_PROJECT}])
    monkeypatch.setattr(unreal, "project", lambda path=None: {"name": "LivingShadowWorld", "path": LSW_PROJECT, "mcp_enabled": True})
    monkeypatch.setattr(unreal.urllib.request, "urlopen", lambda *a, **k: pytest.fail("no call: only a headless job is running"))
    assert U.unreal_tests() == U.CLOSED


def test_a_server_that_is_not_shadow_worlds_is_refused(editor):
    editor.project_in_log = False                     # another project's editor (or another app) holds port 8000
    out = U.unreal_tests()
    assert out.startswith("The Unreal MCP server on port 8000 didn't show it belongs to Living Shadow World") and editor.calls == []


def test_the_editor_closing_mid_call_is_a_stop_not_a_success(editor, monkeypatch):
    from core import pcactions
    editor.answers["ListTests"] = {"tests": ["LivingShadowWorld.A"]}
    real = editor.__call__

    def dies(req, timeout=0):
        body = json.loads(req.data)
        if body.get("method") == "tools/call" and body["params"]["arguments"]["tool_name"] == "RunTests":
            raise ConnectionResetError("editor closed")
        return real(req, timeout)
    monkeypatch.setattr(unreal.urllib.request, "urlopen", dies)
    out = pcactions.perform("unreal_run_tests", {"contains": "A"})
    assert out["verified"] is False and out["error_kind"] == "needs_owner" and out["detail"].startswith("The test run didn't finish")


def test_a_text_reply_is_not_read_as_letters(editor):
    editor.answers["ListTests"] = "Discovery has not completed"
    assert U.unreal_tests().startswith("Unreal couldn't list the tests: Unreal's test list came back in a shape I don't know")


@pytest.mark.skip(reason="needs docs/unreal (not shipped in the shared repo)")
def test_argument_names_match_the_real_schemas():
    """The schemas JARVIS read from the real UE 5.8.4 editor (docs/unreal/unreal-mcp-catalogue-5.8.json)."""
    import pathlib
    cat = json.loads((pathlib.Path(__file__).resolve().parents[1] / "docs" / "unreal" / "unreal-mcp-catalogue-5.8.json").read_text(encoding="utf-8"))
    schemas = {t["name"]: t.get("inputSchema") or {} for ts in cat["catalogue"].values() if isinstance(ts, dict) for t in ts.get("tools", [])}
    used = {("AutomationTestToolset.AutomationTestToolset", "ListTests"): {"nameFilter", "tagFilter", "limit"},
            ("AutomationTestToolset.AutomationTestToolset", "DiscoverTests"): {"bForceRediscover"},
            ("AutomationTestToolset.AutomationTestToolset", "RunTests"): {"testNames"},
            ("EditorToolset.LogsToolset", "GetLogEntries"): {"category", "pattern", "maxEntries"},
            ("SemanticSearchToolset.SemanticSearchToolset", "Search"): {"query", "classFilter", "pathRegexes", "k"}}
    for (toolset, tool), args in used.items():
        schema = schemas[f"{toolset}.{tool}"]
        props = set((schema.get("properties") or {}).keys())
        assert args <= props, (tool, args - props)
        assert set(schema.get("required") or []) <= args, (tool, "a required argument is not sent")


def test_search_without_an_embedding_account_is_explained(editor):
    # live 2026-11-09: "Embedding generation failed: HTTP 401: {" was read out, cut off
    editor.answers["Search"] = RuntimeError('Embedding generation failed: HTTP 401: {"error": "unauthorized"}')
    out = U.unreal_find_asset("mannequin")
    assert out.startswith("Unreal's asset search didn't work: its SemanticSearch plugin needs an embedding service account")
