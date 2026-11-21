"""One authorization policy for every PC step, keyed by the step's EFFECT, never by which router or model produced it.

The owner's direct instruction is the scope of authority: a step that acts on what the owner named (or on what Jarvis just
observed while doing that task) is an ordinary operation and runs without a second question; a step that would act on
something the owner never mentioned, or whose effect cannot be undone, asks once with the plan in words.

THE OWNER'S RULE (owner, 2026-11-07: "when I say do something there are no rules"): when the OWNER directly tells Jarvis to do
something, that command is the approval, whatever the effect: install, uninstall, delete, overwrite, send, run. Jarvis does not
stop to ask. What still asks is only what is NOT the owner's command: a step aimed at something he never named (the model's own
idea, or text inside a page or a guide), anything asked by someone who is not the owner, and anything after he said "ask me first".

Effects:
  observe      read anything: windows, files, the screen, PC facts, Blender's scene          -> always runs
  reversible   open, focus, navigate, type into the named app, set volume, close the named window -> runs under the owner's
               standing autonomy (core.approvals.pc_autonomous) when it stays in the scope of what they said
  irreversible delete, uninstall, overwrite, install, purge, send, pay, a shell step above T1   -> runs when the OWNER named
               its target; otherwise asks once
  external     anything that leaves the PC with the owner's data                                 -> same as irreversible

Unsaved work: closing a window whose title shows unsaved changes is treated as irreversible (pcmethods reports the title;
the check here only needs the observed titles). Nothing in this module runs anything.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional

OBSERVE, REVERSIBLE, IRREVERSIBLE, EXTERNAL = "observe", "reversible", "irreversible", "external"

EFFECTS: Dict[str, str] = {
    # existing typed tools (core.pcactions.SCHEMAS)
    "get_volume": OBSERVE, "pc_status": OBSERVE, "list_windows": OBSERVE, "process_status": OBSERVE,
    "blender_status": OBSERVE, "list_scene_objects": OBSERVE, "inspect_blender": OBSERVE, "uia_read": OBSERVE,
    "unreal_status": OBSERVE, "shadow_world_check": OBSERVE, "unreal_tests": OBSERVE, "unreal_test_results": OBSERVE,
    "unreal_log": OBSERVE, "unreal_find_asset": OBSERVE,
    "unreal_run_tests": REVERSIBLE,       # runs the game's tests in the owner's open editor; nothing is saved
    "shadow_world_import": REVERSIBLE,    # content + a commit on JARVIS's own branch/worktree; git can undo it
    "shadow_world_retarget": REVERSIBLE,  # same lane
    "shadow_world_game_test": OBSERVE,    # runs the game's automation test; changes nothing
    "read_web_page": OBSERVE, "research_web": OBSERVE, "browse_research": OBSERVE,
    "open_app": REVERSIBLE, "focus_app": REVERSIBLE, "set_volume": REVERSIBLE, "change_volume": REVERSIBLE,
    "open_url": REVERSIBLE, "search_foreground_browser": REVERSIBLE, "scroll_foreground": REVERSIBLE,
    "browser_search": REVERSIBLE, "browse_open": REVERSIBLE, "browse_followup": REVERSIBLE, "media_command": REVERSIBLE,
    "minimize_app": REVERSIBLE, "minimize_foreground": REVERSIBLE,
    "close_app": REVERSIBLE, "close_foreground": REVERSIBLE, "close_browser_tab": REVERSIBLE, "close_site_tab": REVERSIBLE,
    "use_app": REVERSIBLE, "uia_invoke": REVERSIBLE, "uia_set_text": REVERSIBLE,
    "render_preview": IRREVERSIBLE, "export_glb": IRREVERSIBLE,       # they write files at a path the model chose
    "install_program": IRREVERSIBLE, "uninstall_program": IRREVERSIBLE,
    # verbs the gateway plans for (core.gateway.PLANNED, implemented in the next PR)
    "find_project": OBSERVE, "find_files": OBSERVE, "read_screen": OBSERVE, "investigate_slowness": OBSERVE,
    "open_path": REVERSIBLE, "type_text": REVERSIBLE, "run_terminal": REVERSIBLE, "save_document": REVERSIBLE,
}

# Steps whose target must be something the owner named or Jarvis observed for this task: acting on a window, a file or a
# field the owner never mentioned is outside the instruction, however harmless the verb.
_SCOPED = {"minimize_app", "close_app","close_site_tab", "focus_app", "type_text", "use_app", "uia_invoke", "uia_set_text", "open_path",
           "run_terminal", "render_preview", "export_glb"}
_NAME_ARGS = ("app", "process", "site", "path", "name", "command")
_UNSAVED = re.compile(r"^\*|\bunsaved\b|\(modified\)|^●", re.I)
_RISKY_COMMAND = re.compile(r"\b(?:rm|del|erase|rmdir|rd|format|remove-item|ri|mv|move|shutdown|restart|reg\s+(?:delete|add)|diskpart|"
                            r"cipher|takeown|icacls|net\s+user|sudo|install|uninstall|winget|choco|scoop|msiexec|setx|curl|wget|"
                            r"invoke-webrequest|iwr|irm|start-process)\b", re.I)


@dataclass
class Verdict:
    ask: bool
    reasons: List[str] = field(default_factory=list)
    effects: Dict[int, str] = field(default_factory=dict)     # step index -> effect


def effect(step: dict, observed_titles: Iterable[str] = ()) -> str:
    tool, args = step.get("tool", ""), step.get("arguments") or {}
    kind = EFFECTS.get(tool, IRREVERSIBLE)                       # an unknown verb never runs unasked
    if tool == "run_terminal":
        command = str(args.get("command", ""))
        if _RISKY_COMMAND.search(command) or _tier(command) >= 2:
            return IRREVERSIBLE
    if tool == "open_path":
        try:
            from core import pcverbs
            if pcverbs.runs_code(str(args.get("path", ""))):
                return IRREVERSIBLE                              # opening a program or script RUNS it: asks, always
        except Exception:  # noqa: BLE001
            return IRREVERSIBLE
    if tool == "save_document":
        import os
        path = str(args.get("path", "")).strip().strip('"')
        if path and os.path.exists(os.path.expanduser(os.path.expandvars(path))):
            return IRREVERSIBLE                                  # overwriting the owner's file asks first
    if tool.startswith("close") and any(_UNSAVED.search(t or "") for t in observed_titles):
        return IRREVERSIBLE
    return kind


def _tier(command: str) -> int:
    """jarvis_pc's own tier for a shell command (T0 read-only, T1 reversible, T2 ask, T3 never); unknown = ask."""
    try:
        from core import pcverbs
        return pcverbs.terminal_tier(command)
    except Exception:  # noqa: BLE001
        return 2


# Store-name tails nobody says: 'close Crimson Desert' names 'crimson desert enhanced' (live 2026-11-07).
_EDITION = {"enhanced", "edition", "remastered", "remaster", "deluxe", "definitive", "ultimate", "complete",
            "standard", "goty", "anniversary", "director's", "directors", "cut", "launcher"}


def _said(value: str, said: str, observed: Iterable[str]) -> bool:
    """The same thing in another spelling: "paint" covers "mspaint"; an observed name (a found project path) covers itself."""
    v = value.strip().casefold().removesuffix(".exe")
    if not v or v == "<found>":
        return True                   # the path an earlier observation of THIS task will produce (the project the owner named)
    low = said.casefold()
    if v in low:
        return True
    words = [w for w in re.split(r"[\s\-_./\\]+", v) if len(w) >= 4 and w not in _EDITION]
    joined = re.sub(r"[^0-9a-z]+", "", low)            # 'Jarvis acceptance' (as speech recognition wrote it) names JarvisAcceptance
    if words and all(w in low or re.sub(r"[^0-9a-z]+", "", w) in joined for w in words):
        return True
    return any(len(o) >= 4 and (o in v or v in o) for o in (str(x).casefold() for x in observed))


_PATHISH = re.compile(r"[\\/~]|^[a-z]:|\.[a-z0-9]{1,5}$", re.I)


def command_operands(command: str) -> List[str]:
    """The operands a command acts on, as written: its path operands, or with none its last operand (per chain segment;
    later pipeline stages only by path operands)."""
    import shlex
    found: List[str] = []
    for n, segment in enumerate(re.split(r"\|\||&&|[|;&]", command)):
        try:
            tokens = shlex.split(segment, posix=False)
        except ValueError:
            tokens = segment.split()
        operands = [v for v in (t.strip("\"'") for t in tokens[1:])
                    if v and not v.startswith("-") and not re.fullmatch(r"/[a-z?]{1,3}(?::\S*)?", v, re.I) and not re.fullmatch(r"[<>]+", v)]
        paths = [v for v in operands if _PATHISH.search(v)]
        found += paths if paths else (operands[-1:] if n == 0 else [])
    return found


def relative_targets(command: str) -> List[str]:
    """Targets not written as a full path (C:\\..., ~\\..., \\\\server\\...): they would land in the terminal's own folder."""
    return [o for o in command_operands(command) if not re.match(r"^(?:[a-z]:[\\/]|~[\\/]|\\\\|\$env:|%)", o, re.I)]


def command_targets(command: str) -> List[str]:
    """What a shell command acts ON, for the scope check: its path operands (by their last part: 'jarvis-test' in
    'mkdir C:\\Users\\me\\Downloads\\jarvis-test'), or, with no path operand, its last operand. The command word itself
    ('mkdir', 'New-Item') is not a target: what a command may DO is the tier's job (effect()), not the scope check's.
    Later pipeline stages count only by their path operands. No operand at all: the whole command must have been said."""
    targets = [(re.split(r"[\\/]", o.rstrip("\\/"))[-1] or o) if _PATHISH.search(o) else o for o in command_operands(command)]
    return targets or [command]


_CLOSE_THIS = re.compile(r"\b(?:close|quit|exit|kill|shut)\b.{0,20}\b(?:this|it|that|the window|this window|this app|this game)\b", re.I)


def _in_front(app: str) -> bool:
    """Is `app` the application whose window is in front right now?"""
    try:
        from tools import apps, pcops
        front = next((w for w in pcops.windows() if w.get("foreground")), None)
        canonical = apps.resolve_app(app)
        return bool(front and canonical and any(w.get("hwnd") == front.get("hwnd") for w in apps.visible_windows_for_app(canonical)))
    except Exception:  # noqa: BLE001  (unsure: the normal named-target rule decides)
        return False


def in_scope(step: dict, said: str, observed: Iterable[str] = ()) -> bool:
    """May this step run under the owner's instruction `said`? Observe steps and plain opens always; scoped verbs only when
    their target was named by the owner or produced by an earlier observation of this task."""
    tool, args = step.get("tool", ""), step.get("arguments") or {}
    if tool not in _SCOPED:
        return True
    if tool == "close_app" and _CLOSE_THIS.search(said or "") and _in_front(str(args.get("app") or "")):
        return True                   # "close this": the app in front IS the one the owner points at (owner 2026-11-07: "he just does it")
    observed = list(observed)
    for key in _NAME_ARGS:
        value = args.get(key)
        if not (isinstance(value, str) and value.strip()):
            continue
        if key == "app" and tool in _APP_BY_SHORT_NAME:
            if _short_named(value, said):
                continue              # "close Edge" names Microsoft Edge; "close the calc" names Calculator
            if value.strip().casefold() not in (said or "").casefold() and _app_ambiguous(
                    _app_norm(value).removesuffix(" exe"), _app_norm(said)) is True:
                return False          # "close python" with Python 3.12 AND Python Launcher installed: which one? (asked)
        targets = command_targets(value) if key == "command" else [below_home(value)] if key == "path" else [value]
        if not all(_said(t, said, observed) for t in targets):
            return False
    return True


# An app is named by the short name people use for it, not only by its full catalogue name. Measured 2026-11-12: "close
# edge" (Microsoft Edge), "close epic" (Epic Games Launcher), "close the calc", "close vs code" each stopped to ask "acts on
# something you did not name" (about 20 needless confirmations in 430 logged turns). Only for closing, minimizing and
# focusing an app: easy to undo, and a close still needs a close word (said_to_do) and still stops at an unsaved-work prompt.
_APP_BY_SHORT_NAME = {"close_app", "minimize_app", "focus_app"}
_APP_VENDOR = {"microsoft", "windows", "google", "adobe", "the", "app", "apps", "for", "and"}
_APP_ALIASES = {"visual studio code": ("vs code", "vscode", "v s code")}
# First words too general to stand for a whole app name ("close the file" is not File Explorer, "game" is not Game Bar).
_APP_GENERIC = {"file", "files", "game", "games", "task", "control", "command", "video", "music", "photo", "photos", "media",
                "voice", "screen", "sound", "system", "device", "office", "mail", "phone", "your", "note", "notes", "quick",
                "remote", "snipping", "sticky", "character", "disk", "print", "power", "visual", "studio", "code", "chat"}
_APP_SAID_SKIP = {"close", "quit", "exit", "kill", "shut", "open", "hide", "minimize", "minimise", "the", "this", "that", "please",
                  "window", "windows", "app", "apps", "program", "jarvis", "and", "for", "you", "can", "could", "would", "down",
                  "away", "now", "just", "want", "need", "with", "done", "from", "screen", "off"}


def names_app(app: str, said: str) -> bool:
    """Do the owner's words name this installed app the short way? True when most of the distinctive words of its name
    were said (whole, or as a 4+ letter beginning: "calc"), when the first distinctive word of its name was said whole and is
    not a general word ("epic" -> Epic Games Launcher), or by a fixed alias ("vs code"). Never a fuzzy guess: "chat tvt"
    does not name Paint, and "the" names nothing."""
    name, low = _app_norm(app).removesuffix(" exe"), _app_norm(said)
    if not name or not low:
        return False
    if _app_alias(name, low):
        return True
    fit = _app_fit(name, low)
    if fit is None:
        return False
    if fit[0] >= 0.6:
        return True
    distinctive, words = _app_words(name, low)
    first = distinctive[0]
    return len(first) >= 4 and first in words and first not in _APP_GENERIC


def _app_norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s']", " ", (text or "").casefold())).strip()


def _app_alias(name: str, low: str) -> bool:
    return any(re.search(r"\b" + re.escape(alias) + r"\b", low) for alias in _APP_ALIASES.get(name, ()))


def _app_words(name: str, low: str):
    parts = [p for p in name.split() if p]
    return ([p for p in parts if p not in _APP_VENDOR] or parts,
            [w for w in low.split() if len(w) >= 3 and w not in _APP_SAID_SKIP])


def _app_fit(name: str, low: str):
    """How well the said words fit this app's name: (share of its distinctive words covered, how many were said whole), or
    None when none was."""
    distinctive, words = _app_words(name, low)
    whole = {p for p in distinctive if p in words}
    begun = {p for p in distinctive for w in words if len(w) >= 4 and w not in _APP_GENERIC and p.startswith(w)}
    covered = whole | begun
    return (len(covered) / len(distinctive), len(whole)) if covered else None


def _short_named(app: str, said: str) -> bool:
    """names_app, and no OTHER installed app fits the same words as well or better (review 2026-11-12: "close visual studio
    code" authorized Visual Studio 2022, "close chrome" Chrome Remote Desktop, "close steam" SteamVR, "close edge" the Edge
    WebView2 runtime). When only part of the name was said, any other installed app sharing those words makes it ambiguous.
    Without a readable catalogue the siblings cannot be checked, so the short name counts for nothing (he is asked, as before)."""
    if not names_app(app, said):
        return False
    return _app_ambiguous(_app_norm(app).removesuffix(" exe"), _app_norm(said)) is False


def _app_ambiguous(name: str, low: str):
    """True: another installed app fits the said words as well or better (or shares them, when only part of this name was
    said). False: none does. None: the catalogue could not be read or is empty, so nobody can tell."""
    try:
        from core import pcsemantic
        others = {_app_norm(n) for n in pcsemantic._catalogue()} - {name, ""}
    except Exception:  # noqa: BLE001
        return None
    if not others:
        return None
    if _app_alias(name, low):
        return False
    mine = _app_fit(name, low)
    if mine is None:
        return False
    return any(theirs is not None and (mine[0] < 1 or theirs >= mine) for theirs in (_app_fit(o, low) for o in others))


def below_home(path: str) -> str:
    """'C:\\Users\\me\\Downloads' -> 'Downloads': the owner says 'my Downloads folder', never their own profile path. A path
    outside the profile is compared whole."""
    import os
    home = os.path.normcase(os.path.expanduser("~")).rstrip("\\/")
    p = path.strip().strip('"')
    if os.path.normcase(p).startswith(home + os.sep) or os.path.normcase(p).startswith(home + "/"):
        return p[len(home) + 1:]
    return path


def is_owner(uid: Optional[str] = None) -> bool:
    """Is the person giving this instruction the owner (not another member of the household)?"""
    try:
        from core import users
        uid = uid or users.current()
        return users.get(uid).get("role") == "owner" and (uid == "owner" or uid in users.all_users())
    except Exception:  # noqa: BLE001  (unknown: not the owner)
        return False


# What the owner must have SAID TO DO for a step that cannot be undone: naming the thing is not enough ("create a folder called
# x" never authorizes deleting x). Tools by name; shell commands by what their command words do.
_DO = {
    "delete": r"\b(?:delete|remove|erase|wipe|clear|clean|purge|empty|trash|get rid of|rm)\b",
    "uninstall": r"\b(?:uninstall|remove|get rid of|delete)\b",
    "install": r"\b(?:install|set up|setup|get|download|add|reinstall|update|upgrade)\b",
    "download": r"\b(?:download|get|fetch|clone|install|set up|setup|pull)\b",
    "move": r"\b(?:move|rename|put|organi[sz]e|sort)\b",
    "power": r"\b(?:shut ?down|restart|reboot|turn off|power off)\b",
    "system": r"\b(?:registry|permission|owner(?:ship)?|user account|environment variable|encrypt|format|partition)\b",
    "run": r"\b(?:run|launch|start|open|play|execute|set up|setup|build)\b",
    "write": r"\b(?:save|overwrite|replace|write|export|render|update)\b",
    "close": r"\b(?:close|quit|exit|kill|shut|end|stop|dismiss|turn off|get rid of|done with|go away|disappear|"
             r"off (?:my|the) screen)\b",
}
_TOOL_DO = {"uninstall_program": "uninstall", "install_program": "install", "render_preview": "write", "export_glb": "write",
            "save_document": "write", "open_path": "run", "close_app": "close", "close_foreground": "close",
            "close_browser_tab": "close", "close_site_tab": "close"}
_COMMAND_DO = [
    ("delete", r"\b(?:rm|del|erase|rmdir|rd|remove-item|ri)\b"), ("uninstall", r"\b(?:uninstall|msiexec\s+/x)\b"),
    ("install", r"\b(?:install|winget|choco|scoop|msiexec|pip|npm|pnpm|yarn|cargo|conda)\b"),
    ("download", r"\b(?:curl|wget|invoke-webrequest|iwr|irm|git\s+clone)\b"), ("move", r"\b(?:mv|move|ren|rename-item)\b"),
    ("power", r"\b(?:shutdown|restart-computer|stop-computer)\b"),
    ("system", r"\b(?:reg\s+(?:delete|add)|diskpart|format|cipher|takeown|icacls|net\s+user|setx)\b"),
    ("run", r"\b(?:start-process|sudo)\b"),
]


def said_to_do(step: dict, said: str) -> bool:
    """Did the owner's words ask for this KIND of change (not only name its target)?"""
    low = (said or "").casefold()
    tool = step.get("tool", "")
    if tool == "run_terminal":
        command = str((step.get("arguments") or {}).get("command", ""))
        kinds = [k for k, rx in _COMMAND_DO if re.search(rx, command, re.I)]
        return all(re.search(_DO[k], low) for k in kinds) if kinds else True
    kind = _TOOL_DO.get(tool)
    return bool(re.search(_DO[kind], low)) if kind else True


def named(step: dict, said: str, observed: Iterable[str] = ()) -> bool:
    """Every target of the step (app, site, path, program, the full paths in a command) is something the owner said or this
    task observed, AND the owner asked for this kind of change."""
    if not said_to_do(step, said):
        return False
    args = step.get("arguments") or {}
    observed = list(observed)
    for key in _NAME_ARGS:
        value = args.get(key)
        if not (isinstance(value, str) and value.strip()):
            continue
        if key == "command":            # the whole path, not only its last part: 'C:\Windows\x' is not 'my Downloads\x'
            targets = [below_home(o) if _PATHISH.search(o) else o for o in command_operands(value)] or [value]
        else:
            targets = [below_home(value)] if key == "path" else [value]
        if not all(_said(t, said, observed) for t in targets):
            return False
    return in_scope(step, said, observed)


def decide(steps: List[dict], said: str, *, autonomous: bool = True, approved: bool = False,
           observed: Iterable[str] = (), observed_titles: Iterable[str] = (), owner: Optional[bool] = None) -> Verdict:
    """Ask or run, for the whole plan: one answer, the same whatever produced the plan."""
    observed = list(observed)
    owner = is_owner() if owner is None else owner
    verdict = Verdict(ask=False)
    for i, step in enumerate(steps or []):
        kind = effect(step, observed_titles)
        verdict.effects[i] = kind
        if approved:
            continue
        name = step.get("tool", "?")
        if kind in (IRREVERSIBLE, EXTERNAL):
            if owner and autonomous and named(step, said, observed):
                continue                 # the owner told Jarvis to do exactly this: his command is the approval
            verdict.ask = True
            verdict.reasons.append(f"step {i + 1} ({name}) is {kind}" + ("" if owner else " and you are not the owner")
                                   + ("" if not owner or not autonomous else " and acts on something you did not name"))
        elif kind == REVERSIBLE:
            if not autonomous:
                verdict.ask = True
                verdict.reasons.append(f"step {i + 1} ({name}): you asked me to ask first")
            elif _TOOL_DO.get(name) == "close" and not said_to_do(step, said):
                # Live 2026-11-08: "Minimize Claude" closed Claude (no minimize tool; the planner picked close_foreground).
                # A close must be ASKED for: naming the app is not enough.
                verdict.ask = True
                verdict.reasons.append(f"step {i + 1} ({name}) closes something, but you did not ask me to close anything")
            elif not in_scope(step, said, observed):
                verdict.ask = True
                verdict.reasons.append(f"step {i + 1} ({name}) acts on something you did not name")
    return verdict


def allowed_recovery(step: dict, said: str, observed: Iterable[str] = (), owner: Optional[bool] = None) -> bool:
    """What a re-plan may run on its own under the approval already given: observe steps, in-scope reversible ones, and for the
    owner anything aimed at exactly what he named (a broader target, e.g. all of Edge for 'close GitHub', still asks)."""
    kind = effect(step)
    if kind == OBSERVE or (kind == REVERSIBLE and in_scope(step, said, observed)):
        return True
    owner = is_owner() if owner is None else owner
    return bool(owner and kind in (IRREVERSIBLE, EXTERNAL) and named(step, said, observed))
