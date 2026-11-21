"""Side-effect-free routing checks, including against the LIVE chat.

Start a message with `[dry-run]` (or `dry run:`) and JARVIS answers only with what he WOULD do: the tool the deterministic front doors and the
skill selector would pick, and the parameters. Nothing is executed, no model is asked, no search, file, Blender job or paid call is made.
The environment variable JARVIS_DRY_RUN=1 does the same for a whole process: tools.registry.run refuses to run any tool.
A spoofed prefix can only make a turn SAFER (it removes actions), so it needs no authentication."""
import os
import re

_PREFIX = re.compile(r"^\s*(?P<chan>\[(?!dry[- ]?run\])[^\]]*\]\s*)?(?:\[dry[- ]?run\]|dry[- ]?run\s*:)\s*", re.I)


def parse(text):
    """-> (text without the marker, is_dry_run)."""
    m = _PREFIX.match(text or "")
    if not m:
        return text, False
    return (m.group("chan") or "") + text[m.end():], True


def active():
    return os.environ.get("JARVIS_DRY_RUN") == "1"


def describe(text):
    """What would happen to this request, decided by the same pure routing code the live turn uses. Executes nothing."""
    from core import craftintent, intelligence, skillhost
    from tools import registry
    t = craftintent.clean(text)
    try:                      # these two front doors run BEFORE the brain on a real turn, so they are described first (both only classify)
        from core import pchybrid, pcpower
        if pcpower.parse(t):
            return "DRY RUN: nothing was executed. This is a PC power command, which JARVIS handles directly before any model."
        if pchybrid.is_request(t):
            return ("DRY RUN: nothing was executed. This is a PC-control request (open/launch/close an app), which JARVIS handles directly "
                    "ahead of the skills and the model.")
    except Exception:         # noqa: BLE001 - describing must never fail the turn
        pass
    hit = craftintent.match(t)
    if hit and hit[0] == "run_installed_skill":
        plan = skillhost.plan(t)
        if plan["kind"] == "ask":
            return f"DRY RUN: nothing was executed. The installed skill {plan['id']} matches, but I would first ask: {plan['question']}"
        return f"DRY RUN: nothing was executed. This would run the installed skill {plan.get('id')} with {plan.get('params')}."
    if hit:
        name, args = hit
        return (f"DRY RUN: nothing was executed. This would go to the tool {name} with {args}"
                f"{' (a slow job)' if registry.is_slow(name) else ''}.")
    own = intelligence._own_skill_tools(t, registry.schemas())
    if own:
        return f"DRY RUN: nothing was executed. The model-side selector would offer {[x['name'] for x in own]}."
    return ("DRY RUN: nothing was executed. No skill or craft front door claims this, so it would go to normal conversation "
            "(which may use web search, notes or a model; that part is not simulated).")
