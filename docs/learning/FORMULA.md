# The JARVIS teaching formula

**Define → Establish baseline → Diagnose → Implement → Independently test → Register → Route → Execute → Verify → Recover → Retain.**

This is the process that produced the verified two-bone Blender skill (see `EVIDENCE.md`, `CORE.md`), written so any person or AI can repeat it and the
Skill Installer (`core/skillinstall.py`) can enforce its middle steps mechanically. The rule behind every step: **a claim of skill is only as good as an
independent check that could have said no.**

| # | Step | What you do | Where it lives / what enforces it |
|---|---|---|---|
| 1 | **Define** | One job, in one sentence; numeric limits; what it is NOT for. | `scope` in `skill.json` (required, shown to the owner at review) |
| 2 | **Establish baseline** | Measure what JARVIS does today, unaided, on held-out tasks. A skill without a baseline cannot show it helped. | Learning core episodes (`core/learn.py`); for a package, the baseline is "no skill installed" |
| 3 | **Diagnose** | Find *why* the baseline fails (wrong order, missing precondition, bad parameter) before writing anything. | Notes in `docs/`, named errors in `errors` |
| 4 | **Implement** | The smallest procedure that fixes the diagnosed failures, with named errors for expected failures. | `procedure.py` |
| 5 | **Independently test** | A judge that is not the author's code decides pass/fail, on cases the implementation was not tuned to: train / dev / sealed holdout, plus routing cases that must NOT match, plus host fuzzing. | `core/skilljudge.py` (host-owned). A package's own `judge.py` may only veto |
| 6 | **Register** | Only judge evidence (≥12 tasks, all passed, judge hash recorded) can create a registered, hash-checked, versioned skill. | `core/procedures.Library.register` |
| 7 | **Route** | Narrow whole-word triggers plus exclusions, so a look-alike request is never stretched over a skill. | `triggers`, `core/craftintent.py`, `core/skillhost.plan` |
| 8 | **Execute** | Extract parameters from the owner's words; ask for essentials that are missing; never invent. Run through the authorised path only. | `params.ask`, `core/skillsandbox.py` |
| 9 | **Verify** | Re-check the invariants on the real output of every use. Say "verified" only if they hold. | `verify.invariants`, `skillhost.run` |
| 10 | **Recover** | A bounded table: named error → change parameters once, or ask the owner. No open-ended retry loops. | `recovery` |
| 11 | **Retain** | Version, hash, log every use and failure, survive restarts, allow disable and rollback. | `Library` + `events.jsonl` + `skill rollback/disable` |

## Four kinds of "knowing" — never blur them

| Kind | Meaning | Can JARVIS act on it? |
|---|---|---|
| **Unverified knowledge** | Notes, retrieved pages, a model's recollection (`skill_memory`, web research "retrieved_unverified") | No. It may inform a plan, never be announced as capability |
| **Teacher-assisted skill** | A procedure someone else wrote, judged independently (the two-bone bind; every installed package) | Yes, within `scope`. Episodes are marked *assisted*: they never count as JARVIS having learned it unaided |
| **Independently learned skill** | JARVIS found the procedure himself and the judge passed it on a sealed holdout without a teacher | Yes. **Not yet demonstrated for any skill.** No claim of this kind may be made without that evidence |
| **Verified executable capability** | Any skill above that holds a registry entry with judge evidence, a matching hash and a trigger scope | Only these run. This is what the library stores |

## What is proven and what is not
* Proven here: steps 4–11 for teacher-authored skills, in the Blender two-bone procedure (real Blender, independent judge) and in the installer path (`tests/test_skillkit.py`, `scripts/skill_demo.py`).
* Not proven: steps 2–3 performed by JARVIS himself, i.e. JARVIS inventing a skill. The installer gives that future work somewhere safe to land: a self-made package goes through the same quarantine and host judge.
