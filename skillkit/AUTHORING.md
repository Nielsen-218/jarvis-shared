# Authoring a JARVIS skill (for any developer or AI assistant)

Copy `skillkit/template/` to a new folder named after the skill, then follow `docs/learning/FORMULA.md` and `docs/learning/SKILL-PACKAGE-SPEC.md`.
`skillkit/examples/util.split_bill/` is a complete, passing example.

## Instructions to give an AI assistant (paste as-is)

> Produce a JARVIS skill package for: <what the capability should do>.
> 1. Define the job in one sentence and its limits; write them as `scope`.
> 2. Write `procedure.py`: pure standard-library Python, one entry function, named `SkillError`s for expected failures. No files, network, subprocess, eval, open.
> 3. Write `params` with `min`/`max` on every number (use type `decimal` for money, never a float; `number_words: true` if the owner may say "three"), a `from_text` regex with ONE capture group, and an `ask` question for every required value.
> 4. Write narrow `triggers` (whole words) and `exclude` words for look-alike requests.
> 5. Write `verify.invariants`: rules that must hold for EVERY output and that are checked a different way than the procedure computes it (an independent oracle, not a copy of the code).
> 6. Write `cases/train|dev|holdout.json`: at least 12 execution cases (>=3 holdout, >=2 expecting a named error) and routing cases (>=3 that must match, >=3 that must NOT). Work the expected values out by hand or with a different method than the procedure.
> 7. Run `python manage.py skill pack <folder> <id>-<version>.jskill`, then `python manage.py skill review <file>` and fix every PROBLEM it prints.
> Do not claim it is verified. Only `python manage.py skill install` with the owner's approval can verify it.

## Rules that will get a package rejected
Any permission other than `compute`; imports outside the allowlist; `eval/exec/open/getattr/__import__`; dunder attribute access; files not in the spec; hash mismatches;
an undeclared dependency; fewer cases than the minimum; numeric parameters without ranges; an update that is not a newer version.
