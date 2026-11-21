"""Unreal Engine for the owner (Mark V): what is installed and open, and whether a rigged character is ready for Living
Shadow World. Read-only: nothing here starts Unreal, opens the editor, or writes into a game project (core/unreal)."""
import re
from pathlib import Path

from core import unreal
from tools.registry import tool


def _rigged_folders():
    """Rigged character folders, newest first: the delivered copies (Documents) and the Blender workspace."""
    from tools import charjobs
    roots = []
    try:
        roots.append(charjobs.delivery_root())
    except Exception:  # noqa: BLE001
        pass
    import os
    from core import storage
    for ws in (os.getenv("JARVIS_BLENDER_WORKSPACE"), storage.DATA / "blender-workspace"):   # rigging writes to the first
        if ws and Path(ws) / "rigged" not in roots:
            roots.append(Path(ws) / "rigged")
    found = []
    for r in roots:
        try:
            found += [d for d in Path(r).iterdir() if d.is_dir() and (d / "report.json").is_file()]
        except OSError:
            continue
    return sorted(found, key=lambda d: (d / "report.json").stat().st_mtime, reverse=True)


def _pick(which: str):
    folders = _rigged_folders()
    if not folders:
        return None, "I have no rigged character to check yet. Ask me to rig one first."
    if not which.strip():
        return folders[0], ""
    words = [w for w in which.casefold().replace("-", " ").replace("_", " ").split() if len(w) > 2]
    hits = [d for d in folders if all(w in d.name.casefold().replace("-", " ").replace("_", " ") for w in words)]
    if not hits:
        return None, f"I couldn't find a rigged character called {which}."
    names = []
    for h in hits:                       # newest first; runs of one character share its spoken name
        if spoken_name(h) not in names:
            names.append(spoken_name(h))
    if len(names) == 1:
        return hits[0], ""               # one character (several runs of it: the newest)
    exact = [h for h in hits if spoken_name(h).casefold() == which.strip().casefold()]
    if exact:
        return exact[0], ""
    return None, f"More than one rigged character matches {which}: {', '.join(names[:4])}. Which one?"


def spoken_name(folder) -> str:
    """'Meshy_AI_Shadow_Vanguard_20261-48bf3cfce-20261103-220809' -> 'Meshy AI Shadow Vanguard'."""
    import re
    stem = re.sub(r"--r\d+$", "", folder.name)                                  # a retry folder ends in --rN
    name = stem.rsplit("-", 2)[0].replace("_", " ").replace("-", " ")               # every folder ends in a run stamp
    # and the source file's own stamps and hashes: words with digits are not a name
    return " ".join(w for w in name.split() if not re.search(r"\d", w)) or name


@tool("What Unreal Engine is installed, whether Living Shadow World is open in the editor, and whether its Unreal MCP "
      "server is running. Read-only; never starts Unreal. Use for 'is Unreal open', 'what version of Unreal do I have', "
      "'is the Unreal MCP running', 'Unreal status'.", {}, direct=True)
def unreal_status() -> str:
    return unreal.say_status()


@tool("Is a rigged character ready for Living Shadow World? Grades it against the game's asset rules: the Unreal import, "
      "the mannequin skeleton, pose tests, the vertex budget, recorded source and licence, and whether it was tried in the "
      "game. Read-only. Use for 'is my character ready for Shadow World', 'can I put the vanguard in my game', "
      "'check the rigged character for Unreal'.", {"which": {"type": "string"}}, direct=True)
def shadow_world_check(which: str = "") -> str:
    folder, problem = _pick(which or "")
    if folder is None:
        return problem
    return f"{spoken_name(folder)}: " + unreal.say_grade(unreal.grade_character(folder))


# ------------------------------------------------------------------------------------------------ the open editor (Mark V)
CLOSED = ("Shadow World isn't open in the editor. Open it with the project's start_mcp_editor script and ask me again; "
          "I don't open it myself.")
# Every failure sentence below starts with one of these (core/pcactions turns them into a stop, never a success).
FAILED = ("Unreal couldn't", "The test run didn't finish", "Unreal's asset search didn't work", "The editor is open but",
          "The Unreal MCP server on port 8000")


def _live():
    try:
        return unreal.Live(), ""
    except unreal.EditorClosed as e:
        return None, CLOSED if "isn't open" in str(e) else f"{str(e)[0].upper()}{str(e)[1:]}."


def _short(test: str) -> str:
    return test.rsplit(".", 1)[-1] if "." in test else test


@tool("List Living Shadow World's automation tests (optionally only those whose name contains a word, e.g. 'combat'). "
      "Only while the owner has the editor open; never opens it. Use for 'what tests does Shadow World have', 'list the "
      "combat tests'.", {"contains": {"type": "string"}}, direct=True)
def unreal_tests(contains: str = "") -> str:
    live, why = _live()
    if not live:
        return why
    try:
        tests = unreal.list_tests(live, contains.strip())
    except Exception as e:  # noqa: BLE001
        return f"Unreal couldn't list the tests: {str(e)[:160]}."
    from core.words import plural
    if not tests:
        return "There are no automation tests" + (f" with {contains} in the name." if contains else ".")
    shown = ", ".join(_short(t) for t in tests[:6])
    return f"{plural(len(tests), 'test')}" + (f" with {contains} in the name" if contains else "") + f", for example {shown}."


@tool("Run Living Shadow World's automation tests whose name contains a word (e.g. 'combat') in the owner's open editor, "
      "and say how many passed and which failed. Only while the editor is open; never opens it. At most 50 tests per run. "
      "Use for 'run the combat tests in Shadow World', 'run the anatomy tests in the game'.",
      {"contains": {"type": "string"}}, required=["contains"], slow=True, direct=True)
def unreal_run_tests(contains: str) -> str:
    live, why = _live()
    if not live:
        return why
    if not contains.strip():
        return "Which tests? Say a word from their names, like 'run the combat tests in Shadow World'."
    try:
        out = unreal.run_tests(live, contains.strip())
    except Exception as e:  # noqa: BLE001
        return f"The test run didn't finish: {str(e)[:160]}."
    from core.words import plural
    if out.get("too_many"):
        return f"{plural(out['too_many'], 'test')} match {contains}; I run at most {unreal.MAX_TESTS_PER_RUN} at once. Name a smaller group."
    if not out["ran"]:
        return f"No tests have {contains} in their name."
    got = unreal.test_outcomes(out.get("results"))
    if not (got["passed"] or got["failed"]):
        return f"I ran {plural(out['ran'], 'test')}, but Unreal's results didn't say which passed. Say 'test results' to check."
    text = f"I ran {plural(out['ran'], 'test')}: {len(got['passed'])} passed"
    if got["failed"]:
        text += f", {len(got['failed'])} failed: " + ", ".join(_short(t) for t in got["failed"][:5])
    return text + "."


@tool("The results of the last automation test run in the owner's open Shadow World editor. Use for 'how did the tests "
      "go', 'Shadow World test results'.", {}, direct=True)
def unreal_test_results() -> str:
    live, why = _live()
    if not live:
        return why
    try:
        live.call(unreal.TESTS, "DiscoverTests", {"bForceRediscover": False}, timeout=120)
        got = unreal.test_outcomes(live.call(unreal.TESTS, "GetTestResults", {}, timeout=60))
    except Exception as e:  # noqa: BLE001
        return f"Unreal couldn't give the results: {str(e)[:160]}."
    if not (got["passed"] or got["failed"]):
        return "There are no test results in this editor session yet."
    text = f"{len(got['passed'])} passed"
    if got["failed"]:
        text += f", {len(got['failed'])} failed: " + ", ".join(_short(t) for t in got["failed"][:5])
    return text + "."


@tool("Errors and warnings in the owner's open Shadow World editor's log (newest last), optionally only lines containing "
      "a word. Read-only. Use for 'any errors in Unreal', 'what does the editor log say', 'errors in Shadow World'.",
      {"contains": {"type": "string"}}, direct=True)
def unreal_log(contains: str = "") -> str:
    live, why = _live()
    if not live:
        return why
    import re
    pattern = r"Error|Warning" if not contains.strip() else re.escape(contains.strip())
    try:
        rows = unreal.log_entries(live, pattern, limit=40)
    except Exception as e:  # noqa: BLE001
        return f"Unreal couldn't read its log: {str(e)[:160]}."
    if not rows:
        return "No errors or warnings in this editor session's log." if not contains.strip() else f"Nothing about {contains} in the log."
    errors = [r for r in rows if re.search(r"\bError\b", r)]
    from core.words import plural
    last = re.sub(r"^\[[^\]]*\]\[[^\]]*\]", "", rows[-1]).strip()[:200]
    head = (f"{plural(len(errors), 'error')} and {plural(len(rows) - len(errors), 'warning')} in the last {plural(len(rows), 'line')}"
            if not contains.strip() else f"{plural(len(rows), 'line')} about {contains}")
    return f"{head}. The newest: {last}"


@tool("Find assets in Living Shadow World by meaning (e.g. 'a sword', 'the mannequin', 'stone wall material') with the "
      "editor's semantic search. Only while the editor is open. Use for 'find the sword asset in Shadow World', 'do I have a "
      "wall material in my game'.", {"query": {"type": "string"}}, required=["query"], direct=True)
def unreal_find_asset(query: str) -> str:
    live, why = _live()
    if not live:
        return why
    try:
        found = unreal.search_assets(live, query.strip(), k=5)
    except Exception as e:  # noqa: BLE001
        if re.search(r"\b(?:401|403)\b|embedding", str(e), re.I):
            # Live 2026-11-09: Epic's SemanticSearch plugin needs an online embedding service; with no account or key it
            # answers "Embedding generation failed: HTTP 401". That is the plugin's setup, not a missing asset.
            return ("Unreal's asset search didn't work: its SemanticSearch plugin needs an embedding service account, and "
                    "this editor has none set up. Searching by meaning needs that set up in the plugin's settings first.")
        return f"Unreal's asset search didn't work: {str(e)[:160]}."
    if not found:
        return f"I found nothing like {query} in the project."
    best = "; ".join(f"{f['path'].rsplit('/', 1)[-1].split('.')[0]} ({f['class'] or 'asset'})" for f in found[:3])
    return f"Closest matches for {query}: {best}."



@tool("Put a rigged character into Living Shadow World: imports it (mesh, skeleton, material, textures) into JARVIS's own "
      "branch of the game (feat/jarvis-characters, folder /Game/JARVIS/Characters), checks it from disk, and commits it there "
      "for the game team to merge. Only when it passes the Unreal import, mannequin skeleton, pose tests and vertex budget. "
      "Never changes the owner's working copy, never pushes, never opens the editor. Use for 'put the main character in Shadow "
      "World', 'add my vanguard to the game'.", {"which": {"type": "string"}}, slow=True, direct=True)
def shadow_world_import(which: str = "") -> str:
    folder, problem = _pick(which or "")
    if folder is None:
        return problem
    name = spoken_name(folder)

    def blocking_of(g):
        return [k for k in unreal.MUST_PASS_BEFORE_IMPORT if g["gates"].get(k, {}).get("state") != "pass"]
    grade = unreal.grade_character(folder)
    if blocking_of(grade):
        # The newest run of this character may be a failed retry while an earlier run passes: use the newest that passes.
        for other in _rigged_folders():
            if other != folder and spoken_name(other) == name:
                g = unreal.grade_character(other)
                if not blocking_of(g):
                    folder, grade = other, g
                    break
    if blocking_of(grade):
        return f"I won't put {name} into Shadow World yet: " + unreal.say_grade(grade)
    fbx = folder / "char_rigged.fbx"
    out = unreal.import_character(str(fbx), name)
    if not out.get("ok"):
        return f"I didn't put {name} into Shadow World: {out.get('why') or 'the import failed'}."
    try:
        commit = unreal.commit_character(name)
    except Exception as e:  # noqa: BLE001
        return (f"{name} is imported into my Shadow World branch and checked, but committing it failed ({str(e)[:120]}); "
                "it is there uncommitted.")
    v = out.get("verify") or {}
    said = (f"{name} is in my Shadow World branch, {unreal.CONTENT_ROOT}/{unreal.asset_name(name)}, "
            f"{v.get('verts', 0):,} vertices, {v.get('bones', 0)} bones, " + ("textured" if out.get("textured") else "untextured")
            + f", commit {commit}. Your own copy of the game is untouched.")
    # The whole job (owner, 2026-11-09: "make it so that i can ask"): the game's animations, then the in-game test.
    rt = unreal.retarget_character(name)
    if not rt.get("ok"):
        return said + f" I couldn't give it the game's animations: {rt.get('why')}."
    try:
        unreal.commit_character(name, what="retargeted onto the project's mannequin animations")
    except Exception:  # noqa: BLE001
        pass
    said += f" It has {len(rt.get('anims', []))} of the game's animations."
    return said + " " + _say_game_test(unreal.game_test(name))



def _say_game_test(out: dict) -> str:
    if not out.get("tests"):
        return f"The Shadow World game test didn't run: {out.get('why', 'unknown')}."
    said = []
    for t in out["tests"]:
        if t["state"] == "Success":
            # Said like a person (owner: no robot readouts): the numbers stay in the test report.
            text = " ".join(t["info"])
            height = re.search(r"body height ([\d.]+) cm", text)
            step = re.search(r"moved up to ([\d.]+) cm", text)
            facts = ", ".join(x for x in ("it lands on the floor and stands" if "on ground=1" in text else "",
                                          f"it's {round(float(height.group(1)))} cm tall" if height else "",
                                          "its walk really moves its feet" if step and float(step.group(1)) > 5 else "") if x)
            said.append(f"{t['name']} passed the in-game test" + (f": {facts}" if facts else ""))
        else:
            said.append(f"{t['name']} FAILED the in-game test: " + "; ".join(e.replace("Expected '", "").replace("' to be true.", "")
                                                                             for e in t["errors"][:3]))
    return ". ".join(said) + "."


@tool("Retarget Living Shadow World's mannequin animations (idle, walk, jog, jump, fall, land, three attacks) onto a "
      "character already in JARVIS's Shadow World branch, with Unreal's IK Retargeter, and commit them there. Use for "
      "'retarget the main character for Shadow World', 'give my character the game's animations'.",
      {"which": {"type": "string"}}, slow=True, direct=True)
def shadow_world_retarget(which: str = "") -> str:
    folder, problem = _pick(which or "")
    if folder is None:
        return problem
    name = spoken_name(folder)
    out = unreal.retarget_character(name)
    if not out.get("ok"):
        return f"I didn't retarget {name}: {out.get('why')}."
    if out.get("already"):
        return f"{name} already has the game's animations: {len(out['anims'])} of them."
    try:
        commit = unreal.commit_character(name, what="retargeted onto the project's mannequin animations")
    except Exception as e:  # noqa: BLE001
        return f"{name} is retargeted, but committing it failed ({str(e)[:120]}); it is there uncommitted."
    return (f"{name} now has {len(out['anims'])} of the game's animations, retargeted with Unreal's IK Retargeter; "
            f"{out.get('moving', 0)} of them move the legs. Committed {commit} on my Shadow World branch.")


@tool("Run the in-game test for JARVIS's Shadow World characters (or one): in a real game world the character spawns like "
      "the player, must stand on a floor and walk with its retargeted animation. Use for 'test the main character in "
      "Shadow World', 'does my character work in the game'.", {"which": {"type": "string"}}, slow=True, direct=True)
def shadow_world_game_test(which: str = "") -> str:
    name = ""
    if (which or "").strip():
        folder, problem = _pick(which)
        if folder is None:
            return problem
        name = spoken_name(folder)
    return _say_game_test(unreal.game_test(name))
