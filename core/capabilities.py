"""The catalogue of DEVELOPER-BUILT primitives a learned procedure may compose.

A primitive is written and reviewed by people; a procedure that composes them is written by JARVIS. Every learned skill
records which primitives it depends on, and the two are always labelled separately (built_by "developer" here, provenance
"jarvis_learned" / "teacher_assisted" on the procedure).

Each provider is one capability object passed to a procedure by name (`def run(browser, params):`). Adding an application
later = adding a provider here plus its implementation; the learner, checker, failure classes, budget and skill format
do not change. Planned providers are listed so the learner can say "I would need X" (a missing-tool request) instead of
trying to fake it with clicks.
"""

PROVIDERS = {
    "browser": {
        "built_by": "developer", "status": "available_in_lab",
        "summary": "The browser's own tabs/windows API: exact tab ids, URLs, hosts, titles, windows; focus, close, open blank.",
        "module": "core.browserbridge", "api": ("windows", "tabs", "tab", "activate", "close", "new_tab"),
        "effects": {"windows": "read", "tabs": "read", "tab": "read", "activate": "reversible", "close": "reversible",
                    "new_tab": "reversible"},
        "verifies_with": "re-read through tabs()/tab(); the lab judge separately reads DevTools ground truth",
    },
    "ui": {
        "built_by": "developer", "status": "available",
        "summary": "Windows UI Automation: any app's windows and controls; read names/values, Invoke and Select. No keyboard/mouse.",
        "module": "core.methodlang", "api": ("windows", "find", "children", "parent", "info", "value", "invoke", "select"),
        "effects": {"invoke": "depends_on_target", "select": "reversible"},
        "verifies_with": "ui.info(id)['exists'] / re-find",
    },
    "util": {
        "built_by": "developer", "status": "available",
        "summary": "Waiting, notes for the trace, and giving up honestly.",
        "module": "core.methodlang", "api": ("wait", "log", "fail"),
        "effects": {},
        "verifies_with": "-",
    },
    # ---- planned: the same contract, not built yet. A procedure may not depend on these; naming one is a tool request.
    "bpy": {"built_by": "developer", "status": "planned",
            "summary": "Blender through bpy (objects, transforms, materials, modifiers, render) via the existing Blender MCP worker.",
            "api": ()},
    "unreal": {"built_by": "developer", "status": "available",
               "summary": "Unreal Engine, read-only (Mark V): installed engines, Living Shadow World open or not, Epic's Unreal MCP "
                          "server tool list when the owner has it running, and a character's grade against the game's asset rules. "
                          "Never starts Unreal and never writes to a game project (core/unreal.py).",
               "module": "core.unreal", "api": ("status", "mcp_tools", "grade_character", "may_run_headless", "list_tests",
                                                 "run_tests", "log_entries", "search_assets"),
               "effects": {}, "verifies_with": "-"},
    "vision": {"built_by": "developer", "status": "planned",
               "summary": "Screenshot + local vision model fallback for UIs that expose no automation tree (find a control by look).",
               "api": ()},
}

USABLE = ("available", "available_in_lab")


def usable(names=None) -> list:
    return [n for n, p in PROVIDERS.items() if p["status"] in USABLE and (names is None or n in names)]


def api(name) -> tuple:
    return tuple(PROVIDERS.get(name, {}).get("api", ()))


def doc(names) -> str:
    """What the learner reads: each usable provider's exact calls, plus the planned ones by name only."""
    from core import browserbridge, methodlang
    parts = []
    for n in names:
        if n == "browser":
            parts.append(browserbridge.BROWSER_DOC)
        elif n == "ui":
            parts.append(methodlang.UI_CAP_DOC)
        elif n == "util":
            parts.append(methodlang.UTIL_DOC)
    planned = [f"{n} ({p['summary']})" for n, p in PROVIDERS.items() if p["status"] == "planned"]
    parts.append("Not available yet (if your plan truly needs one, say so in `needs` instead of working around it): " + "; ".join(planned))
    return "\n\n".join(parts)


def catalogue() -> list:
    return [{"name": n, "built_by": p["built_by"], "status": p["status"], "summary": p["summary"], "api": list(p.get("api", ()))}
            for n, p in PROVIDERS.items()]
