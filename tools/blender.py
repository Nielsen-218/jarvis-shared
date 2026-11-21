"""Offline Blender reference and reviewed starter recipes. No network or bpy execution."""
import json
import re
import uuid

from core import storage
from core.config import ROOT
from tools.registry import tool

HANDBOOK = ROOT / "knowledge" / "blender" / "handbook.json"
RECIPES = ROOT / "knowledge" / "blender" / "recipes"
STOP = {"a", "an", "and", "the", "my", "i", "to", "in", "how", "do", "on", "for", "of", "with", "blender", "make", "create", "please"}
ALIASES = {"tshirt": "clothing", "t-shirt": "clothing", "sneaker": "clothing", "shoes": "clothing", "outfit": "clothing",
    "skin": "characters", "person": "characters", "human": "characters", "sims": "characters", "inzoi": "characters",
    "room": "lighting", "house": "lighting", "robot": "hard_surface", "bottle": "hard_surface",
    "spinning": "animation", "rotate": "animation", "stl": "printing", "pink": "troubleshooting"}


def handbook():
    return json.loads(HANDBOOK.read_text(encoding="utf-8"))


def search(query, limit=4):
    if not isinstance(query, str) or not query.strip() or len(query) > 2000:
        raise ValueError("Provide Blender keywords or a goal of at most 2000 characters.")
    words = set(re.findall(r"[a-z0-9_-]+", query.casefold())) - STOP
    extra = {ALIASES[w] for w in words if w in ALIASES}
    scored = []
    for card in handbook()["cards"]:
        title = set(re.findall(r"[a-z0-9_-]+", card["title"].casefold() + " " + card["tags"]))
        body = set(re.findall(r"[a-z0-9_-]+", card["body"].casefold()))
        score = 4 * len(words & title) + len(words & body) + (6 if card["id"] in extra else 0)
        if card["id"] in words:
            score += 6
        if score:
            scored.append((score, card))
    return [card for _, card in sorted(scored, key=lambda x: -x[0])[:max(1, min(int(limit), 5))]]


@tool("Search JARVIS's bundled OFFLINE Blender handbook before searching the web: modeling, anatomy, clothes, hair, materials, rigging, animation, nodes, rendering, export, Python and troubleshooting. Returns original notes with official source links and version scope; zero API calls.",
    {"query": {"type": "string"}, "max_results": {"type": "integer"}}, required=["query"])
def blender_reference(query: str, max_results: int = 4) -> str:
    book = handbook()
    cards = search(query, max_results)
    if not cards:
        return "No local reference matches. Do not invent an answer; clarify the topic or request a version-specific lookup."
    return json.dumps({"target_version": book["target_version"], "reviewed": book["reviewed"], "notice": book["notice"], "references": cards})


@tool("Create a local Blender project blueprint and quality checklist from the bundled handbook, without AI or network calls. This prepares reference guidance, NOT execution or a spending/approval grant.",
    {"goal": {"type": "string"}})
def blender_project_plan(goal: str) -> str:
    cards = search(goal)
    if not cards:
        return "No supported reference matches that goal. Clarify the Blender deliverable before planning."
    return json.dumps({"goal": goal, "kind": "reference_blueprint_not_executed",
        "brief": ["Establish dimensions, visual references, realism target, final format and device budget.",
            "Check the installed Blender version and available typed tools before claiming a capability."],
        "phases": [{"topic": c["id"], "guidance": c["body"], "source": c["source"]} for c in cards],
        "verification": ["Inspect silhouette/proportions from multiple views before fine detail.",
            "Check actual dimensions, normals, materials and deformation where relevant.",
            "Render a cheap preview and inspect it; successful code is not visual approval.",
            "Export a new file and reopen it in the target application; verify textures, scale and playback."],
        "execution": "Use existing local functions/MCP and verified workflows. The current narrow Blender worker supports status/list/render/export, not arbitrary modeling Python. Starter scripts are reviewable artifacts for manual or separately approved execution. Preserve files and approval gates; do not invent tools or silently spend on a cloud loop."})


@tool("Create a private, reviewable Blender Python starter script from a bundled trusted template: product_studio, room_blockout or turntable. Also saves a README. Does NOT run Blender, change its open scene, render, download assets or grant permission to execute code.",
    {"recipe": {"type": "string", "enum": ["product_studio", "room_blockout", "turntable"]}})
def blender_create_starter(recipe: str) -> str:
    if recipe not in ("product_studio", "room_blockout", "turntable"):
        raise ValueError("Unknown bundled recipe.")
    source = RECIPES / "starter.py"
    # Unique directories; never overwrite a saved project, and no user-supplied paths/code.
    relative = f"blender/projects/{uuid.uuid4().hex}"
    script = storage.path(relative + "/build.py")
    script.write_text(source.read_text(encoding="utf-8").replace('"__JARVIS_RECIPE__"', repr(recipe)), encoding="utf-8")
    readme = storage.path(relative + "/README.txt")
    readme.write_text("JARVIS reviewed starter: " + recipe + "\n"
        "This is a basic editable scene, not a finished professional asset. No downloaded assets.\n"
        "Review build.py. Run in a separate Blender background process, not over your working scene:\n"
        'blender --background --factory-startup --python-exit-code 1 --python "' + str(script) + '"\n'
        "Outputs starter.blend beside the script; existing output is refused.\n"
        "Optional: add -- --preview for a small CPU preview. Review the render before accepting quality.\n"
        "No arbitrary script execution is added to the existing MCP.\n", encoding="utf-8")
    return json.dumps({"state": "prepared_not_executed", "recipe": recipe, "script": str(script),
        "readme": str(readme), "relative_script": relative + "/build.py", "relative_readme": relative + "/README.txt",
        "output": str(script.parent / "starter.blend"), "asset_license": "Original repository code; no external assets or models."})
