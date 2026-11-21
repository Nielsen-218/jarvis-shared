"""Unreal Engine provider for JARVIS (Mark V, 2026-11-09): what is installed, what is open, and a grade for a character.

Studied first (4 readers): the Living Shadow World team rules (Documentation/TEAM.md) say never open the editor GUI during
team runs, only lsw-builder commits to main, shared files change only additively; the owner's own rule says Unreal / Shadow
World launches are supervised. So this module NEVER starts Unreal and never writes to a game project:

- status()          engine version, the owner's Unreal projects, whether an editor has one open, whether Epic's own MCP
                    server (ModelContextProtocol, enabled in Living Shadow World) is answering on 127.0.0.1:8000
- mcp_tools()       the tool list of that MCP server, ONLY when the owner already started it (read-only: initialize + list)
- may_run_headless  the guard for JARVIS's headless Unreal jobs (hub/ue_check.py imports into a THROWAWAY project): not
                    in a game, not while an editor has that project open, not with 3+ Unreal processes (the team's limit)
- grade_character   a rigged character's report against Shadow World's asset rules (Design/.../ASSET_ANIMATION_PIPELINE.md):
                    which gates pass, which fail, which still need a supervised in-game check. Nothing is marked done
                    because Unreal merely imported it ("No asset gets marked DONE because Unreal displays a thumbnail").
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional

ENGINE_ROOTS = (r"C:\Program Files\Epic Games",)
LSW = Path(os.getenv("JARVIS_LSW_ROOT", r"C:\Users\you\LivingShadowWorld"))
MCP_URL = os.getenv("JARVIS_UNREAL_MCP_URL", "http://127.0.0.1:8000/mcp")
MAX_UNREAL_PROCESSES = 3          # TEAM.md: wait while 3 or more UnrealEditor-Cmd processes run
EDITOR_EXES = ("unrealeditor.exe", "unrealeditor-cmd.exe")
VERTS_BUDGET = 60000
MANNEQUIN_CORE = ("pelvis", "spine_01", "spine_02", "head", "upperarm_l", "lowerarm_l", "hand_l", "upperarm_r", "lowerarm_r",
                  "hand_r", "thigh_l", "calf_l", "foot_l", "thigh_r", "calf_r", "foot_r")   # as hub/ue_gate_inside.py and the UE5 profile              # a gameplay character's base LOD; above it the optimisation gate fails (owner can change)


# ------------------------------------------------------------------------------------------------ what is installed
def engines() -> List[dict]:
    """[{version, root, editor_cmd}] for every UE install found (Build.version is the engine's own record)."""
    found = []
    roots = [os.getenv("JARVIS_UE_ROOT")] if os.getenv("JARVIS_UE_ROOT") else []
    for base in ENGINE_ROOTS:
        try:
            roots += [str(p) for p in Path(base).glob("UE_*")]
        except OSError:
            pass
    for root in roots:
        r = Path(root)
        build = r / "Engine" / "Build" / "Build.version"
        cmd = r / "Engine" / "Binaries" / "Win64" / "UnrealEditor-Cmd.exe"
        try:
            b = json.loads(build.read_text(encoding="utf-8-sig"))
            version = f"{b['MajorVersion']}.{b['MinorVersion']}.{b['PatchVersion']}"
        except (OSError, ValueError, KeyError):
            continue
        found.append({"version": version, "root": str(r), "editor_cmd": str(cmd) if cmd.exists() else ""})
    return sorted(found, key=lambda e: [int(x) for x in e["version"].split(".")], reverse=True)


def read_uproject(path: Path) -> dict:
    """A .uproject as JSON. Living Shadow World's is UTF-16 with a BOM (re-saved by a plugin), others UTF-8."""
    raw = Path(path).read_bytes()
    for enc in ("utf-16", "utf-8-sig") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else ("utf-8-sig", "utf-16"):
        try:
            return json.loads(raw.decode(enc))
        except (UnicodeDecodeError, ValueError):
            continue
    return {}


def project(path: Path = LSW) -> Optional[dict]:
    """{name, path, engine, plugins, mcp_enabled, python_enabled} for one project folder, or None."""
    path = Path(path)
    files = sorted(path.glob("*.uproject")) if path.is_dir() else ([path] if path.suffix == ".uproject" else [])
    if not files:
        return None
    data = read_uproject(files[0])
    plugins = [p.get("Name") for p in data.get("Plugins", []) if isinstance(p, dict) and p.get("Enabled")]
    return {"name": files[0].stem, "path": str(files[0]), "engine": data.get("EngineAssociation", ""), "plugins": plugins,
            "mcp_enabled": "ModelContextProtocol" in plugins, "python_enabled": "PythonScriptPlugin" in plugins}


# ------------------------------------------------------------------------------------------------ what is running
def unreal_processes() -> List[dict]:
    """[{pid, exe, project}] for every Unreal editor process (GUI or -Cmd)."""
    out = []
    try:
        import psutil
        for p in psutil.process_iter(["pid", "name", "cmdline"]):
            name = (p.info.get("name") or "").lower()
            if name not in EDITOR_EXES:
                continue
            cmd = p.info.get("cmdline") or []
            proj = next((c.split("=", 1)[-1].strip('"') for c in cmd if c.lower().strip('"').endswith(".uproject")), "")
            out.append({"pid": p.info["pid"], "exe": name, "project": proj})
    except Exception:  # noqa: BLE001  (cannot look: say nothing is known, callers then refuse to run)
        return [{"pid": 0, "exe": "unknown", "project": ""}]
    return out


def editor_has(project_path: str, procs: Optional[List[dict]] = None) -> bool:
    want = os.path.normcase(os.path.abspath(project_path))
    return any(p["project"] and os.path.normcase(os.path.abspath(p["project"])) == want for p in (procs if procs is not None else unreal_processes()))


def _post(url: str, body: dict, session: str = "", timeout: float = 3.0):
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
               "Origin": "http://127.0.0.1"}            # the plugin accepts only localhost origins
    if session:
        headers["Mcp-Session-Id"] = session
    req = urllib.request.Request(url, json.dumps(body).encode(), headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        text = r.read().decode("utf-8", "replace")
        sid = r.headers.get("Mcp-Session-Id", session)
    # Streamable HTTP may answer as JSON or as server-sent events ("data: {...}")
    for chunk in [text] + [ln[5:].strip() for ln in text.splitlines() if ln.startswith("data:")]:
        try:
            return json.loads(chunk), sid
        except ValueError:
            continue
    return None, sid


def mcp_tools(url: str = MCP_URL, timeout: float = 3.0) -> Optional[List[str]]:
    """Names of the tools Epic's Unreal MCP server offers, or None when it is not running. Read-only: initialize, then
    tools/list. Never starts the editor or the server, never calls a tool."""
    try:
        init, sid = _post(url, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "jarvis", "version": "1"}}}, timeout=timeout)
        if not isinstance(init, dict) or "result" not in init:
            return None
        try:
            _post(url, {"jsonrpc": "2.0", "method": "notifications/initialized"}, sid, timeout=timeout)
        except Exception:  # noqa: BLE001  (a notification may get an empty 202)
            pass
        listed, _ = _post(url, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}, sid, timeout=timeout)
        tools = ((listed or {}).get("result") or {}).get("tools") or []
        return [t.get("name", "") for t in tools if isinstance(t, dict)]
    except Exception:  # noqa: BLE001  (connection refused: the server is not running)
        return None


def status() -> dict:
    eng = engines()
    lsw = project(LSW)
    procs = unreal_processes()
    tools = mcp_tools() if lsw and lsw["mcp_enabled"] else None
    return {"engines": eng, "lsw": lsw, "processes": procs, "lsw_open": bool(lsw and editor_has(lsw["path"], procs)),
            "mcp_running": tools is not None, "mcp_tools": tools or []}


def say_status(s: Optional[dict] = None) -> str:
    s = s or status()
    if not s["engines"]:
        return "I can't find Unreal Engine on this PC."
    parts = [f"Unreal {s['engines'][0]['version']} is installed."]
    lsw = s["lsw"]
    if lsw:
        parts.append(f"{lsw['name']} is " + ("open in the editor" if s["lsw_open"] else "not open") + ".")
        if s["mcp_running"]:
            from core.words import plural
            parts.append(f"Its Unreal MCP server is answering with {plural(len(s['mcp_tools']), 'tool')}.")
        elif lsw["mcp_enabled"]:
            parts.append("Its Unreal MCP server isn't running; it starts with the editor through start_mcp_editor.")
    busy = [p for p in s["processes"] if p["exe"] == "unrealeditor-cmd.exe"]
    if busy:
        from core.words import plural
        parts.append(f"{plural(len(busy), 'background Unreal job')} running.")
    return " ".join(parts)


# ------------------------------------------------------------------------------------------------ the headless guard
def may_run_headless(project_path: str, procs: Optional[List[dict]] = None) -> tuple:
    """(ok, why) for a JARVIS headless Unreal job on `project_path`. Never the Shadow World working copy; never while a
    game runs; never while an editor has that project open; never with MAX_UNREAL_PROCESSES Unreal processes already."""
    target = os.path.normcase(os.path.abspath(project_path))
    lsw_root = os.path.normcase(os.path.abspath(str(LSW)))
    if target == lsw_root or target.startswith(lsw_root + os.sep):
        return False, "that is the Living Shadow World working copy, which only the game team's builder changes"
    try:
        from core import warmth
        if warmth.gaming():
            return False, "a game is running"
    except Exception:  # noqa: BLE001  (cannot tell: do not start Unreal)
        return False, "I couldn't check whether a game is running"
    procs = procs if procs is not None else unreal_processes()
    if any(p["exe"] == "unknown" for p in procs):
        return False, "I couldn't check which Unreal processes are running"
    if editor_has(project_path, procs):
        return False, "an Unreal editor has that project open"
    if len(procs) >= MAX_UNREAL_PROCESSES:
        return False, f"{len(procs)} Unreal processes are already running"
    return True, ""


# ------------------------------------------------------------------------------------------------ grading a character
def _load(folder: Path, name: str) -> dict:
    try:
        return json.loads((folder / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def grade_character(folder) -> Dict[str, object]:
    """A rigged character folder (report.json, manifest.json, provenance.json) against Shadow World's asset gates.
    Each gate: pass | fail | not_checked, with the reason. ready = every gate passes (never true today: the in-game
    functionality gate needs a supervised run in the game, which JARVIS does not start on its own)."""
    folder = Path(folder)
    report, prov = _load(folder, "report.json"), _load(folder, "provenance.json")
    gates: Dict[str, dict] = {}
    if not report:
        return {"ready": False, "gates": {"report": {"state": "fail", "why": "no rig report in that folder"}}, "folder": str(folder)}
    ue = report.get("unreal_gate") or {}
    if not ue:
        gates["unreal_import"] = {"state": "not_checked", "why": "it was never imported into Unreal"}
    elif ue.get("skipped"):
        gates["unreal_import"] = {"state": "not_checked", "why": f"the Unreal check was skipped because {ue['skipped']}"}
    elif ue.get("ok"):
        gates["unreal_import"] = {"state": "pass", "why": f"Unreal imported it: {ue.get('bones')} bones, one root, skin weights clean"}
    else:
        gates["unreal_import"] = {"state": "fail", "why": "Unreal's import check failed" + (f" ({ue.get('error') or ue.get('error_run')})"
                                                                                       if (ue.get('error') or ue.get('error_run')) else "")}
    names = {str(v).casefold() for v in (report.get("bone_name_map") or {}).values()}
    missing = [b for b in MANNEQUIN_CORE if b not in names]
    if not names:
        gates["mannequin_skeleton"] = {"state": "not_checked", "why": "no bone-name map in the report"}
    elif missing:
        gates["mannequin_skeleton"] = {"state": "fail", "why": "missing UE mannequin bones: " + ", ".join(missing[:5])}
    else:
        gates["mannequin_skeleton"] = {"state": "pass", "why": "the core UE mannequin bones are there, so Shadow World's animations can be retargeted"}
    poses = report.get("pose_tests") or []
    ik = report.get("ik_tests") or []
    if poses or ik:
        bad = [t.get("test") or t.get("control") for t in poses + ik if not t.get("pass")]
        gates["deforms"] = ({"state": "pass", "why": f"{len(poses)} pose tests and {len(ik)} IK tests passed"} if not bad
                            else {"state": "fail", "why": "failed: " + ", ".join(str(b) for b in bad[:4])})
    else:
        gates["deforms"] = {"state": "not_checked", "why": "no pose tests in the report"}
    verts, where = (ue.get("verts"), "in Unreal") if ue.get("verts") else ((report.get("reimport") or {}).get("verts_reimported"), "in Blender")
    if verts:
        gates["optimisation"] = ({"state": "pass", "why": f"{verts:,} vertices {where}, within the {VERTS_BUDGET:,} budget"}
                                 if verts <= VERTS_BUDGET else
                                 {"state": "fail", "why": f"{verts:,} vertices {where}, over the {VERTS_BUDGET:,} gameplay budget (it needs a lighter version or LODs)"})
    else:
        gates["optimisation"] = {"state": "not_checked", "why": "the vertex count is unknown"}
    if not prov or not str(prov.get("license") or "").strip():
        gates["provenance"] = {"state": "fail", "why": "no recorded licence: Shadow World needs the source and licence of every character recorded"}
    elif prov.get("restrictions"):
        gates["provenance"] = {"state": "fail", "why": f"recorded, but its licence ({prov.get('license', 'unknown')}) has restrictions to clear first"}
    else:
        gates["provenance"] = {"state": "pass", "why": f"source and licence recorded ({prov.get('license', 'stated')})"}
    gates["in_game"] = _in_game_gate(folder)
    return {"ready": all(g["state"] == "pass" for g in gates.values()), "gates": gates, "folder": str(folder)}


def _in_game_gate(folder: Path) -> dict:
    """The newest REAL result of this character's in-game automation test (LivingShadowWorld.JARVIS.InGame.<Asset>, run in
    JARVIS's worktree by Scripts/lsw.ps1: a begun-play game world, collision, the retargeted walk). Owner 2026-11-10 (live):
    "Is my Shadow World character ready?" said "not checked yet: the in-game check" hours after that test passed."""
    asset = asset_name(re.sub(r"-\d{8}.*$", "", folder.name))
    found = last_game_test(asset)
    if not found:
        return {"state": "not_checked", "why": "its in-game test has not been run yet (say 'run the game test on my character')"}
    when = found["when"]
    stamp = re.search(r"-(\d{8})-(\d{6})$", folder.name)
    try:
        rig_time = (time.mktime(time.strptime(stamp.group(1) + stamp.group(2), "%Y%m%d%H%M%S")) if stamp
                    else (folder / "report.json").stat().st_mtime)
    except (OSError, ValueError):
        rig_time = 0
    if found.get("mtime", 0) and rig_time and found["mtime"] < rig_time:
        # a newer rig of the same character was never tested in the game (review 2026-11-10)
        return {"state": "not_checked", "why": f"its last in-game test ({when}) was before this rig was made; run the game test again"}
    if found["state"] not in ("Success", "Fail", "Failed"):
        return {"state": "not_checked", "why": f"its in-game test did not finish on {when} ({found['state']})"}
    if found["state"] == "Success":
        detail = "; ".join(m.split("JARVIS:", 1)[-1].strip() for m in found["info"][:3])
        return {"state": "pass", "why": f"its in-game test passed on {when}" + (f" ({detail})" if detail else "")}
    return {"state": "fail", "why": f"its in-game test failed on {when}" + (f": {found['errors'][0]}" if found["errors"] else "")}


def last_game_test(asset: str) -> Optional[dict]:
    """{state, when, info, errors} of the newest in-game test report for `asset` in the worktree's BuildEvidence, or None."""
    import glob
    want = (f"LivingShadowWorld.JARVIS.InGame.{asset}".casefold(), f"LivingShadowWorld.JARVIS.{asset}InGame".casefold())
    reports = sorted(glob.glob(str(JARVIS_WT / "Documentation" / "BuildEvidence" / "*_report" / "*.json")),
                     key=lambda p: Path(p).stat().st_mtime, reverse=True)
    for path in reports[:60]:
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        for t in data.get("tests", []):
            if str(t.get("fullTestPath", "")).casefold() in want:
                entries = [e.get("event", {}) for e in t.get("entries", [])]
                return {"state": t.get("state"), "mtime": Path(path).stat().st_mtime,
                        "when": time.strftime("%d %B at %H:%M", time.localtime(Path(path).stat().st_mtime)),
                        "info": [e.get("message", "") for e in entries if "JARVIS:" in e.get("message", "")],
                        "errors": [e.get("message", "") for e in entries if e.get("type") == "Error"]}
    return None


def say_grade(g: Dict[str, object]) -> str:
    gates = g["gates"]
    passed = [k for k, v in gates.items() if v["state"] == "pass"]
    failed = [(k, v["why"]) for k, v in gates.items() if v["state"] == "fail"]
    pending = [k for k, v in gates.items() if v["state"] == "not_checked"]
    words = {"unreal_import": "the Unreal import", "mannequin_skeleton": "the mannequin skeleton", "deforms": "the pose tests",
             "optimisation": "the vertex budget", "provenance": "provenance", "in_game": "the in-game check", "report": "the report"}
    out = []
    if passed:
        out.append("It passes " + ", ".join(words[k] for k in passed) + ".")
    for k, why in failed[:3]:
        out.append(f"It fails {words[k]}: {why}.")
    if pending:
        out.append("Not checked yet: " + ", ".join(words[k] for k in pending) + ".")
    out.append("It's ready for Shadow World." if g["ready"] else "So it isn't ready for Shadow World yet.")
    return " ".join(out)



# ------------------------------------------------------------------------------------------------ the live editor (allowlist)
# Owner, 2026-11-09: "build that" - JARVIS may use the read-only tools of Epic's Unreal MCP server (listing and running the
# game's automation tests, reading the editor log, searching assets) ONLY while the owner has the editor open. Catalogue:
# docs/unreal/UNREAL-MCP-CATALOGUE-5.8.md (25 toolsets, 262 tools; nothing else is ever called).
TESTS = "AutomationTestToolset.AutomationTestToolset"
LOGS = "EditorToolset.LogsToolset"
SEARCH = "SemanticSearchToolset.SemanticSearchToolset"
ALLOWED = frozenset({
    (TESTS, "DiscoverTests"), (TESTS, "ListTests"), (TESTS, "GetTestStatus"), (TESTS, "GetTestResults"),
    (TESTS, "RunTests"), (TESTS, "StopTests"),
    (LOGS, "GetLogEntries"), (LOGS, "GetLogCategories"),
    (SEARCH, "Search"),
})
MAX_TESTS_PER_RUN = 50
GAME_TESTS = "LivingShadowWorld."      # all 18 of the game's tests are registered under this prefix (Source/.../Tests/*.cpp); the
                                       # editor also lists engine/editor tests, some of which load maps into the open editor


class EditorClosed(RuntimeError):
    """Living Shadow World is not open in the editor (JARVIS never opens it)."""


class NotAllowed(PermissionError):
    """A tool outside ALLOWED."""


class Live:
    """One MCP session with the owner's open editor. Every call is checked against ALLOWED."""

    def __init__(self, url: str = MCP_URL, procs: Optional[List[dict]] = None):
        lsw = project(LSW)
        procs = procs if procs is not None else unreal_processes()
        gui = [p for p in procs if p["exe"] == "unrealeditor.exe"]          # a background UnrealEditor-Cmd job is not "open"
        if not lsw or not editor_has(lsw["path"], gui):
            raise EditorClosed("Living Shadow World isn't open in the editor")
        self.url = url
        try:
            init, self.sid = _post(url, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "jarvis", "version": "1"}}})
        except Exception as e:  # noqa: BLE001
            raise EditorClosed("the editor is open but its Unreal MCP server isn't answering") from e
        if not isinstance(init, dict) or "result" not in init:
            raise EditorClosed("the editor is open but its Unreal MCP server isn't answering")
        try:
            _post(url, {"jsonrpc": "2.0", "method": "notifications/initialized"}, self.sid)
        except Exception:  # noqa: BLE001
            pass
        self._id = 10
        # The server's serverInfo is empty (UE 5.8.4), so prove whose it is: its own log names the project it loaded.
        try:
            proof = self.call(LOGS, "GetLogEntries", {"category": "", "pattern": "LivingShadowWorld", "maxEntries": 1}, timeout=15)
        except Exception as e:  # noqa: BLE001
            raise EditorClosed("the Unreal MCP server on port 8000 didn't show it belongs to Living Shadow World") from e
        if not proof:
            raise EditorClosed("the Unreal MCP server on port 8000 didn't show it belongs to Living Shadow World")

    def call(self, toolset: str, tool: str, arguments: Optional[dict] = None, timeout: float = 30.0):
        """The tool's return value (JSON-decoded when it is JSON text). Raises NotAllowed for anything off the list and
        RuntimeError with the server's own message when the tool fails."""
        if (toolset, tool) not in ALLOWED:
            raise NotAllowed(f"{toolset}.{tool} is not one of the tools JARVIS may use")
        self._id += 1
        reply, _ = _post(self.url, {"jsonrpc": "2.0", "id": self._id, "method": "tools/call", "params": {
            "name": "call_tool", "arguments": {"toolset_name": toolset, "tool_name": tool, "arguments": arguments or {}}}},
            self.sid, timeout=timeout)
        if not isinstance(reply, dict):
            raise RuntimeError("Unreal gave no answer")
        if "error" in reply:
            raise RuntimeError(str((reply["error"] or {}).get("message") or reply["error"])[:300])
        result = reply.get("result") or {}
        text = "".join(c.get("text", "") for c in result.get("content", []) if isinstance(c, dict))
        if result.get("isError"):
            raise RuntimeError(text[:300] or "the Unreal tool reported an error")
        value = result.get("structuredContent", text)
        return _unwrap(value)


def _unwrap(value):
    """{"returnValue": "<json>"} -> the decoded inner value; plain text stays text."""
    for _ in range(3):
        if isinstance(value, str):
            try:
                value = json.loads(value)
                continue
            except ValueError:
                return value
        if isinstance(value, dict) and set(value) == {"returnValue"}:
            value = value["returnValue"]
            continue
        return value
    return value


def list_tests(live: "Live", name_filter: str = "") -> List[str]:
    """The GAME's tests (prefix GAME_TESTS) whose path contains `name_filter`; engine and editor tests are never listed."""
    live.call(TESTS, "DiscoverTests", {"bForceRediscover": False}, timeout=120)
    data = live.call(TESTS, "ListTests", {"nameFilter": name_filter, "tagFilter": "", "limit": 0}, timeout=60)
    tests = data.get("tests") if isinstance(data, dict) else data
    if not isinstance(tests, list):
        raise RuntimeError(f"Unreal's test list came back in a shape I don't know ({str(data)[:80]})")
    return [str(t) for t in tests if str(t).startswith(GAME_TESTS)]


def run_tests(live: "Live", name_filter: str, timeout: float = 360.0) -> dict:
    """Run the tests whose path contains `name_filter` (at most MAX_TESTS_PER_RUN), wait for the result."""
    tests = list_tests(live, name_filter)
    if not tests:
        return {"ran": 0, "tests": []}
    if len(tests) > MAX_TESTS_PER_RUN:
        return {"ran": 0, "too_many": len(tests), "tests": tests[:5]}
    summary = live.call(TESTS, "RunTests", {"testNames": tests}, timeout=timeout)
    results = live.call(TESTS, "GetTestResults", {}, timeout=60)
    return {"ran": len(tests), "summary": summary, "results": results, "tests": tests}


def test_outcomes(results) -> Dict[str, List[str]]:
    """GetTestResults -> {'passed': [...], 'failed': [...], 'other': [...]} by test name (shape-tolerant)."""
    if isinstance(results, dict):
        rows = results.get("tests") or results.get("results") or []
    else:
        rows = results or []
    out: Dict[str, List[str]] = {"passed": [], "failed": [], "other": []}
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict):
            continue
        name = str(r.get("name") or r.get("fullPath") or r.get("testName") or r.get("path") or "?")
        state = str(r.get("state") or r.get("result") or r.get("status") or "").lower()
        if state in ("success", "passed", "pass", "succeeded"):
            out["passed"].append(name)
        elif state in ("fail", "failed", "failure", "error"):
            out["failed"].append(name)
        else:
            out["other"].append(name)
    return out


def log_entries(live: "Live", pattern: str = r"Error|Warning", category: str = "", limit: int = 40) -> List[str]:
    rows = live.call(LOGS, "GetLogEntries", {"category": category, "pattern": pattern, "maxEntries": limit}, timeout=30)
    return [str(r) for r in (rows or [])] if isinstance(rows, list) else []


def search_assets(live: "Live", query: str, k: int = 5) -> List[dict]:
    rows = live.call(SEARCH, "Search", {"query": query, "classFilter": [], "pathRegexes": [], "k": k}, timeout=30)
    found = []
    for r in rows if isinstance(rows, list) else []:
        if isinstance(r, dict):
            path = (r.get("path") or {}).get("refPath", "") if isinstance(r.get("path"), dict) else str(r.get("path", ""))
            cls = (r.get("class") or {}).get("refPath", "") if isinstance(r.get("class"), dict) else str(r.get("class", ""))
            found.append({"path": path, "class": cls.rsplit(".", 1)[-1], "caption": str(r.get("caption", ""))[:200]})
    return found



# ------------------------------------------------------------------------------------------------ into the game (JARVIS's lane)
# Owner, 2026-11-09: "yes do it" - bring a character into Shadow World. The game team's rules (Documentation/TEAM.md): only
# lsw-builder commits main, every member works on a feat/* branch in its own worktree, nobody pushes. JARVIS is such a
# member: worktree C:/Users/you/LSW_wt/jarvis, branch feat/jarvis-characters, content only under /Game/JARVIS/.
JARVIS_WT = Path(os.getenv("JARVIS_LSW_WORKTREE", r"C:\Users\you\LSW_wt\jarvis"))
JARVIS_BRANCH = "feat/jarvis-characters"
CONTENT_ROOT = "/Game/JARVIS/Characters"
_IMPORT_SCRIPT = Path(__file__).resolve().parent.parent / "hub" / "ue_import_lsw.py"


def asset_name(name: str) -> str:
    """'Shadow world main character' -> 'ShadowWorldMainCharacter' (letters and digits only, at most 48)."""
    words = re.findall(r"[A-Za-z0-9]+", name or "")
    return ("".join(w[:1].upper() + w[1:] for w in words) or "Character")[:48]


def _git(*args) -> str:
    import subprocess
    p = subprocess.run(["git", "-C", str(JARVIS_WT), *args], capture_output=True, text=True, timeout=60)
    if p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout).strip()[:200])
    return p.stdout.strip()


def lane_ready() -> tuple:
    """(ok, why) for JARVIS's worktree: exists, is on its own branch, and its game code is built."""
    uproject = JARVIS_WT / "LivingShadowWorld.uproject"
    if not uproject.is_file():
        return False, f"JARVIS's Shadow World worktree isn't at {JARVIS_WT}"
    try:
        branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    except Exception as e:  # noqa: BLE001
        return False, f"I couldn't read the worktree's branch ({e})"
    if branch != JARVIS_BRANCH:
        return False, f"the worktree is on {branch}, not {JARVIS_BRANCH}"
    dll = JARVIS_WT / "Binaries" / "Win64" / "UnrealEditor-LivingShadowWorld.dll"
    if not dll.is_file():
        return False, "the worktree's game code isn't built yet (Scripts/lsw.ps1 build)"
    return True, ""


def glb_textures(glb: str, folder: Path, name: str) -> List[dict]:
    """The rig's GLB holds its textures properly (the FBX's embedded ones UE 5.8 cannot read): write the base colour and
    normal map out as PNG files for Unreal. [{file, asset, slot, kind}]; [] when there are none."""
    import struct
    try:
        raw = Path(glb).read_bytes()
        jlen = struct.unpack("<I", raw[12:16])[0]
        doc = json.loads(raw[20:20 + jlen])
        blen = struct.unpack("<I", raw[20 + jlen:24 + jlen])[0]
        binary = raw[28 + jlen:28 + jlen + blen]
    except (OSError, ValueError, struct.error):
        return []
    mats = doc.get("materials") or []
    if not mats:
        return []
    m = mats[0]
    picks = [("color", ((m.get("pbrMetallicRoughness") or {}).get("baseColorTexture") or {}).get("index"), "DiffuseColorMap", "BaseColor"),
             ("normal", (m.get("normalTexture") or {}).get("index"), "NormalMap", "Normal")]
    out = []
    folder.mkdir(parents=True, exist_ok=True)
    for kind, tindex, slot, suffix in picks:
        if tindex is None:
            continue
        try:
            img = doc["images"][doc["textures"][tindex]["source"]]
            view = doc["bufferViews"][img["bufferView"]]
            data = binary[view.get("byteOffset", 0): view.get("byteOffset", 0) + view["byteLength"]]
        except (KeyError, IndexError, TypeError):
            continue
        ext = ".jpg" if img.get("mimeType") == "image/jpeg" else ".png"
        asset = f"T_{name}_{suffix}"
        path = folder / (asset + ext)
        path.write_bytes(data)
        out.append({"file": str(path), "asset": asset, "slot": slot, "kind": kind})
    return out


IMPORT_TIMEOUT_S, VERIFY_TIMEOUT_S = 330, 180      # + git: inside the 600 s step ceiling (core/pcmethods, test_antistuck)


def _fence(what: str) -> None:
    """Stop here if this PC step has passed its time limit (nothing more is changed after the owner was told it stopped)."""
    try:
        from jarvis_pc.guardrails import stepfence
    except Exception:  # noqa: BLE001
        return
    stepfence.fence(what)


def _run_inside(mode: str, fbx: str, dest: str, timeout: int = IMPORT_TIMEOUT_S, runner=None, textures=None) -> dict:
    import subprocess
    import tempfile
    eng = engines()
    if not eng or not eng[0]["editor_cmd"]:
        return {"ok": False, "error": "Unreal Engine isn't installed"}
    tmp = Path(tempfile.mkdtemp(prefix="lswimport_"))
    cfg, res = tmp / "cfg.json", tmp / "out.json"
    cfg.write_text(json.dumps({"mode": mode, "fbx": fbx, "dest": dest, "textures": textures or []}), encoding="utf-8")
    env = dict(os.environ, LSW_IMPORT_CFG=str(cfg), LSW_IMPORT_OUT=str(res))
    cmd = [eng[0]["editor_cmd"], str(JARVIS_WT / "LivingShadowWorld.uproject"), "-run=pythonscript", "-script=" + str(_IMPORT_SCRIPT),
           "-unattended", "-nosplash", "-nullrhi", "-nop4", "-stdout", "-NoSound"]
    _fence(f"Unreal {mode}")
    if runner is not None:
        try:
            runner(cmd, env=env, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": f"Unreal {mode} timed out"}
    else:
        # A Popen registered with the step fence: if the step passes its limit, this Unreal process tree is killed with it.
        proc = subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            from jarvis_pc.guardrails import stepfence
            stepfence.register_process(proc)
        except Exception:  # noqa: BLE001
            pass
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            return {"ok": False, "error": f"Unreal {mode} timed out"}
    if not res.is_file():
        return {"ok": False, "error": f"Unreal produced no {mode} result"}
    return json.loads(res.read_text(encoding="utf-8"))


def import_character(fbx: str, name: str, runner=None) -> dict:
    """Import a rigged character's FBX into JARVIS's worktree, then verify it from disk in a second Unreal process.
    {ok, dest, import, verify, why}. Never touches the owner's working copy or any other team member's worktree."""
    ok, why = lane_ready()
    if not ok:
        return {"ok": False, "why": why}
    ok, why = may_run_headless(str(JARVIS_WT / "LivingShadowWorld.uproject"))
    if not ok:
        return {"ok": False, "why": why}
    if not Path(fbx).is_file():
        return {"ok": False, "why": f"there is no FBX at {fbx}"}
    dest = f"{CONTENT_ROOT}/{asset_name(name)}"
    on_disk = JARVIS_WT / "Content" / "JARVIS" / "Characters" / asset_name(name)
    if on_disk.exists() and any(on_disk.rglob("*.uasset")):
        return {"ok": False, "why": f"{dest} already exists in the worktree; I never overwrite a character", "dest": dest}
    import tempfile
    glb = Path(fbx).with_suffix(".glb")
    textures = glb_textures(str(glb), Path(tempfile.mkdtemp(prefix="lswtex_")), asset_name(name)) if glb.is_file() else []
    imp = _run_inside("import", str(Path(fbx).resolve()), dest, runner=runner, textures=textures)
    if not imp.get("ok"):
        _discard(on_disk)
        return {"ok": False, "why": "the import failed: " + str(imp.get("error") or "")[-200:], "dest": dest, "import": imp}
    _fence("verify the import")
    ver = _run_inside("verify", "", dest, timeout=VERIFY_TIMEOUT_S, runner=runner)
    good = bool(ver.get("ok") and ver.get("verts") and ver.get("bones") and ver.get("has_pelvis"))
    why = "" if good else "it imported, but reloading it from disk didn't show a complete skeletal mesh"
    ours = str((ver.get("textures_bound") or {}).get("DiffuseColorMap", "")).startswith(dest + "/")   # not a parent default
    textured = bool(ver.get("textured") and ours)
    if good and textures and not textured:
        good, why = False, "it imported, but its colour texture isn't connected, so it would look grey in the game"
    if not good:
        _discard(on_disk)
    return {"ok": good, "dest": dest, "import": imp, "verify": ver, "textured": textured, "why": why}


def _discard(folder: Path) -> None:
    """Remove a character folder THIS run created after a failed attempt (it did not exist before: import_character refuses
    an existing one), so the next try is not blocked and nothing half-made stays in the worktree. Only under the lane."""
    import shutil
    try:
        folder = folder.resolve()
        lane = (JARVIS_WT / "Content" / "JARVIS" / "Characters").resolve()
        if folder.parent == lane and folder.is_dir():
            shutil.rmtree(folder)
    except OSError:
        pass



MUST_PASS_BEFORE_IMPORT = ("unreal_import", "mannequin_skeleton", "deforms", "optimisation")


def commit_character(name: str, report_md: str = "", what: str = "imported under /Game/JARVIS, verified from disk") -> str:
    """Commit one character's folder (and its hand-off note) on JARVIS's branch. Never pushes. Returns the commit id."""
    folder = f"Content/JARVIS/Characters/{asset_name(name)}"
    _fence("commit the character")
    if _git("rev-parse", "--abbrev-ref", "HEAD") != JARVIS_BRANCH:
        raise RuntimeError(f"the worktree is no longer on {JARVIS_BRANCH}")
    paths = [folder] + ([report_md] if report_md else [])
    if (JARVIS_WT / "Content" / "JARVIS" / "Retarget").is_dir():
        paths.append("Content/JARVIS/Retarget")                  # the mannequin source IK rig, shared by every retarget
    _git("add", "--", *paths)
    # Only these paths go into the commit, whatever else might be staged (git commit -- <paths>).
    _git("-c", "core.autocrlf=false", "commit", "-q", "--", *paths, "-m",
         f"feat(jarvis): {asset_name(name)} {what}\n\n"
         "Imported headless by JARVIS (Mark V) into its own worktree; no shared files changed; not pushed.")
    return _git("rev-parse", "--short", "HEAD")



# ------------------------------------------------------------------------------------------------ animations + the game test
_RETARGET_SCRIPT = Path(__file__).resolve().parent.parent / "hub" / "ue_retarget_lsw.py"
RETARGET_TIMEOUT_S, GAME_TEST_TIMEOUT_S = 300, 420
RETARGET_ANIMS = tuple("/Game/Characters/Mannequins/Anims/Unarmed/" + a for a in (
    "MM_Idle", "Walk/MF_Unarmed_Walk_Fwd", "Jog/MF_Unarmed_Jog_Fwd", "Jump/MM_Jump", "Jump/MM_Fall_Loop", "Jump/MM_Land",
    "Attack/MM_Attack_01", "Attack/MM_Attack_02", "Attack/MM_Attack_03"))


def _popen_fenced(cmd, env=None, timeout=300, cwd=None) -> bool:
    """Run a child registered with the step fence (killed with its tree if the step passes its limit). False on timeout."""
    import subprocess
    proc = subprocess.Popen(cmd, env=env, cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        from jarvis_pc.guardrails import stepfence
        stepfence.register_process(proc)
    except Exception:  # noqa: BLE001
        pass
    try:
        proc.wait(timeout=timeout)
        return True
    except subprocess.TimeoutExpired:
        proc.kill()
        return False


def _ready_lane() -> tuple:
    ok, why = lane_ready()
    if ok:
        ok, why = may_run_headless(str(JARVIS_WT / "LivingShadowWorld.uproject"))
    return ok, why


def retarget_character(name: str, runner=None) -> dict:
    """Retarget the project's mannequin animations onto an imported JARVIS character (hub/ue_retarget_lsw.py), then commit.
    {ok, anims, why}. Never overwrites: a character that already has animations is reported as done."""
    import tempfile
    ok, why = _ready_lane()
    if not ok:
        return {"ok": False, "why": why}
    asset = asset_name(name)
    folder = JARVIS_WT / "Content" / "JARVIS" / "Characters" / asset
    if not (folder / f"SK_{asset}.uasset").is_file():
        return {"ok": False, "why": f"{asset} isn't in my Shadow World branch yet; put it in first"}
    if (folder / "Anims").is_dir() and any((folder / "Anims").glob("*.uasset")):
        return {"ok": True, "already": True, "anims": sorted(p.stem for p in (folder / "Anims").glob("*.uasset"))}
    tmp = Path(tempfile.mkdtemp(prefix="lswrt_"))
    cfg, res = tmp / "cfg.json", tmp / "out.json"
    dest = f"{CONTENT_ROOT}/{asset}"
    cfg.write_text(json.dumps({"dest": dest, "mesh": f"{dest}/SK_{asset}", "anims": list(RETARGET_ANIMS)}), encoding="utf-8")
    eng = engines()
    cmd = [eng[0]["editor_cmd"] if eng else "", str(JARVIS_WT / "LivingShadowWorld.uproject"), "-run=pythonscript",
           "-script=" + str(_RETARGET_SCRIPT), "-unattended", "-nosplash", "-nullrhi", "-nop4", "-stdout", "-NoSound"]
    env = dict(os.environ, LSW_RT_CFG=str(cfg), LSW_RT_OUT=str(res))
    _fence("retarget animations")
    finished = (runner(cmd, env=env, timeout=RETARGET_TIMEOUT_S) if runner else _popen_fenced(cmd, env=env, timeout=RETARGET_TIMEOUT_S))
    out = json.loads(res.read_text(encoding="utf-8")) if res.is_file() else {"ok": False, "error": "Unreal produced no result"}
    if not finished:
        out = {"ok": False, "error": "the retarget timed out"}
    if not out.get("ok"):
        import shutil
        for sub_dir in ("Anims", "Rig"):                         # only what this attempt made inside the character's folder
            shutil.rmtree(folder / sub_dir, ignore_errors=True)
        return {"ok": False, "why": "the retarget failed: " + str(out.get("error") or "")[-200:]}
    return {"ok": True, "anims": [a["anim"].rsplit("/", 1)[-1] for a in out.get("anims", [])],
            "moving": sum(1 for a in out.get("anims", []) if a.get("legs_move")), "steps": out.get("steps", [])}


def game_test(name: str = "", runner=None) -> dict:
    """Run the in-game automation test (LivingShadowWorld.JARVIS.InGame.<Name>) in JARVIS's worktree with the team's own
    runner (Scripts/lsw.ps1 test). {ok, tests: [{name, state, info, errors}], why}."""
    import glob
    import time as _time
    ok, why = _ready_lane()
    if not ok:
        return {"ok": False, "why": why}
    flt = "LivingShadowWorld.JARVIS.InGame" + (f".{asset_name(name)}" if name else "")
    tag = "jarvisvoice" + _time.strftime("%H%M%S")
    cmd = ["pwsh", "-NoProfile", "-File", str(JARVIS_WT / "Scripts" / "lsw.ps1"), "test", tag, "-Arg", flt]
    _fence("run the game test")
    finished = (runner(cmd, cwd=str(JARVIS_WT), timeout=GAME_TEST_TIMEOUT_S) if runner
                else _popen_fenced(cmd, cwd=str(JARVIS_WT), timeout=GAME_TEST_TIMEOUT_S))
    if not finished:
        return {"ok": False, "why": "the game test timed out"}
    reports = sorted(glob.glob(str(JARVIS_WT / "Documentation" / "BuildEvidence" / f"{tag}_*_report" / "*.json")))
    if not reports:
        return {"ok": False, "why": "the game test produced no report"}
    data = json.loads(Path(reports[-1]).read_text(encoding="utf-8-sig"))
    tests = []
    for t in data.get("tests", []):
        entries = [e.get("event", {}) for e in t.get("entries", [])]
        tests.append({"name": str(t.get("fullTestPath", "")).rsplit(".", 1)[-1], "state": t.get("state"),
                      "info": [e.get("message", "") for e in entries if "JARVIS:" in e.get("message", "")],
                      "errors": [e.get("message", "") for e in entries if e.get("type") == "Error"]})
    if not tests:
        return {"ok": False, "why": f"no in-game test matched {flt} (is the character imported and the test built?)"}
    return {"ok": all(t["state"] == "Success" for t in tests), "tests": tests}
