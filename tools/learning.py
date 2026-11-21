"""Ask Jarvis to study, teach it corrections, and check real indexing progress."""
from tools.registry import tool

@tool(
    "Teach Jarvis to independently index trustworthy reference material about "
    "communication, psychology, computers or Apex. Adds a small known-source "
    "study queue; the background worker runs only while not gaming. "
    "This saves searchable sources locally, NOT new model weights.",
    {"topic":{"type":"string","enum":["communication","psychology","computers","apex"]}},
    direct=True,
)
def jarvis_study_topic(topic:str)->str:
    from core.autolearn import queue_topic
    info=queue_topic(topic)
    return (f"Added {topic} to my local study queue. {info['queued']} sources waiting. "
            "I will index real reading offline, not pretend that merely saving a page "
            "means I've mastered it. Heavy study won't run while you're gaming.")

@tool(
    "The owner corrects Jarvis or teaches a specific reusable insight. Save it "
    "as an owner-provided lesson, not independently verified scientific fact.",
    {"topic":{"type":"string","enum":["communication","psychology","computers","apex"]},
     "lesson":{"type":"string"}},
    direct=True,
)
def teach_jarvis_lesson(topic:str,lesson:str)->str:
    from core.autolearn import teach
    try:
        info=teach(topic,lesson)
    except ValueError as exc:
        return f"Correction not saved: {exc}"
    return f"Saved your correction for {topic}; {info['user_lessons']} owner lessons recorded."

@tool(
    "Show autonomous study progress honestly, including queued sources, "
    "indexed sources, and corrections. Do not pretend model weights changed.",
    direct=True,
)
def jarvis_learning_status()->str:
    from core.autolearn import status
    s=status()
    return (f"Local self-study: {s['indexed_sources']} indexed sources, "
            f"{s['queued']} waiting, {s['user_lessons']} corrections. "
            f"Next: {s['next_source'] or 'none'}. "+s["meaning"])

@tool(
    "Study ONE queued local learning source right now when no game is running. "
    "Network retrieval requires working internet but no paid model, API key "
    "or GPU. Indexing isn't comprehension or model retraining.",
    slow=True,direct=True,
)
def jarvis_study_next_source()->str:
    from core.autolearn import study_once
    r=study_once()
    return ("Local study "+r["result"]+
            (f": {r['title']} ({r['topic']})" if r.get("title") else "")+
            ". No paid inference or model fine-tuning was performed.")


@tool(
    "When the owner explicitly wants Jarvis to FIX HIS OWN voice speed or "
    "autonomous-learning code, run a bounded LOCAL offline coding-model edit in "
    "a disposable Git worktree, compile, run tests and merge only if main is "
    "clean and unchanged. Never use paid APIs, never run during gaming, "
    "never claim restart or audible-latency validation automatically.",
    {"area":{"type":"string","enum":["voice","learning"]},
     "problem":{"type":"string","description":"Specific observed code bug, regression or reproducible limitation"}},
    slow=True,direct=True,
)
def jarvis_fix_own_code(area:str,problem:str)->str:
    import os
    import subprocess
    from core import users,storage
    if users.get(users.current()).get("role")!="owner":
        return "Changing Jarvis source code is available to the household owner only."
    from core.intelligence import FAST
    from scripts.jarvis_self_upgrade import run
    target={"voice":"core/local_stream.py","learning":"core/autolearn.py"}.get(area)
    if not target:
        return "Supported code improvement areas are voice and learning."
    try:
        settings=storage.load_global("intelligence-settings.json",{})
        result=run(problem,area,target,
            os.getenv("JARVIS_SELF_UPGRADE_MODEL",settings.get("self_upgrade_model",FAST)),
            os.getenv("JARVIS_SELF_UPGRADE_URL",settings.get("self_upgrade_url","http://127.0.0.1:1234/v1")),merge=True)
    except (OSError,RuntimeError,ValueError,ImportError,KeyError,
            subprocess.CalledProcessError,subprocess.TimeoutExpired) as exc:
        return ("Local self-repair was blocked; no production changes made. "
                +type(exc).__name__+": "+str(exc)[:120])
    if result["status"]=="locally_merged_tested":
        return ("I tested and merged a source-level change locally at "
                +result["commit"][:12]+". It takes effect after the app reloads. "
                "Live speech speed and behavior remain to be measured.")
    return ("I built a passing code change on "+result["branch"]+
            ", but main moved during tests, so I left the running application unchanged.")


@tool(
    "Study a programming language locally. Queue curated official language docs "
    "for offline indexing; reading alone is not skill mastery or tool installation. "
    "Supports Python, C++, JavaScript, TypeScript, C#, Java, Rust, Go, Lua, "
    "Kotlin, Swift, SQL and Unreal C++.",
    {"language": {"type": "string"}},
    direct=True,
)
def jarvis_study_programming(language: str) -> str:
    from core.autolearn import PROGRAMMING_SOURCES, queue_programming_language
    try:
        state = queue_programming_language(language)
    except ValueError:
        return ("That language has no reviewed source in my local study curriculum yet. "
                "Available: " + ", ".join(PROGRAMMING_SOURCES) +
                ". I won't claim unsupported material was learned.")
    return (f"Queued reviewed {state['language']} documentation for local study; "
            f"{state['queued']} sources pending overall. Indexed notes will be "
            "available offline, but mastering the language still requires practice and tests.")


@tool(
    "Record a reusable programming or Blender procedure in Jarvis's persistent "
    "local notebook. Include precise steps, provenance and a reproducible "
    "acceptance test. Saved entries are unverified until independently tested; "
    "this NEVER runs code, installs packages or modifies Jarvis. Owner only.",
    {"language": {"type": "string"}, "title": {"type": "string"},
     "procedure": {"type": "string"}, "source": {"type": "string"},
     "how_to_test": {"type": "string"}},
    direct=True,
)
def jarvis_save_coding_procedure(language: str, title: str, procedure: str,
                                 source: str, how_to_test: str) -> str:
    from core import users
    if users.get(users.current()).get("role") != "owner":
        return "Only the household owner can add shared executable-workflow notes."
    from core.skill_memory import capture
    try:
        row = capture(language, title, procedure, source, how_to_test)
    except ValueError as exc:
        return "Procedure was not saved: " + str(exc)
    if row.get("duplicate"):
        return f"Already saved locally: {row['language']} / {row['title']}."
    return (f"Saved {row['language']} / {row['title']} revision {row['revision']} "
            "locally for use after restart. Status: captured, NOT independently tested. "
            "The supplied acceptance check still needs to be run in a safe test environment.")


@tool(
    "Search Jarvis's offline learned coding, Blender and engineering procedures "
    "before researching them again. Returns attributed, versioned, UNVERIFIED "
    "notes as reference data, never code execution authorization.",
    {"query": {"type": "string"}},
    direct=True,
)
def jarvis_find_coding_procedure(query: str) -> str:
    from core.skill_memory import search
    try:
        rows = search(query, 3)
    except ValueError as exc:
        return "Procedure search was refused: " + str(exc)
    if not rows:
        return "No local procedure matches. Research and test before adding a candidate."
    return "\n\n".join(
        f"Saved reference (not verified or authorized to run): {r['language']} / "
        f"{r['title']} revision {r['revision']}. Source: {r['source']}. "
        f"Steps: {r['procedure'][:1600]}. Acceptance check: {r['how_to_test'][:400]}."
        for r in rows
    )
