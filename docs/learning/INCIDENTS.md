# Incidents

## 2026-11-01: a live routing probe started a real rigging job

**What happened.** While verifying the wired two-bone skill through the running app (`/api/chat`), the negative probe
"Rig my dragon with exactly two bones and bind it with automatic weights" was correctly NOT taken by the new skill, so it fell through to the
existing general rigger (`rig_my_character`). That resolver found a real character file and started a genuine background rigging job
("rigging char_rigged"). The job was cancelled at once ("cancel the rigging job"); no files were written, the job list was empty afterwards,
and no data was changed.

**Cause.** A *negative routing check* was run as a live conversation. In the live app "not this skill" does not mean "nothing happens": the next
router in line may act. The probe was mine, not a test, and the suite had no guard against real heavy work either.

**Fixes (this commit).**
1. `tests/conftest.py` `no_real_background_work` (autouse): `rigging.rig_character`, `rigging.image_to_character`, `makechar._make` and
   `dojo.Practice.start` raise in every test unless the test fakes them (the normal way) or is marked `@pytest.mark.real_jobs`.
2. `tests/test_no_real_jobs.py`: routing is checked with the pure `craftintent.match` only; a rig request that finds a real file in
   background mode ends as a failed job with the guard message and writes nothing.
3. `pytest.ini` registers the `real_jobs` marker.

**Rules from now on.**
- Check routing with `craftintent.match(text)` (pure) or the selector, never by sending the phrase to the live app.
- A live probe may only use phrases whose every possible route is read-only or is the skill under test. If a phrase could reach a general tool
  (rigging, practice, character generation, installs, anything that writes), do not send it live.
- If one slips through: cancel through the app, then verify with `craftjobs` state and a listing of newly written files.

## 2026-11-01 (second): a live "REAL" probe started a rig job, and `[dry-run]` did not stop PC control

**What happened.** While verifying fixes through the live app, I sent `i need you to rig meshy ai shadow vanguard` expecting an "ambiguous, which one?" list. The local model chose a
unique file this time, so a real rigging job ran (new files only, in the rigged folder). Then `[dry-run] open blender` and `[dry-run] open the ... in blender` **launched Blender**:
`Brain.ask` runs `quick.handle` and `pcfast.handle` (PC control) before the dry-run check, which sat later in the turn. Nothing of the owner's was touched; I closed the one stray
"(Unsaved)" Blender window I caused and left the owner's own Blender alone.

**Causes.** (1) I treated a phrase as safe because a *deterministic* path would ask; the model can route the same words differently. (2) Dry-run was checked after two front doors that execute.

**Fixes.** `dryrun.parse` is now the very first thing in `Brain.ask`; `dryrun.describe` also names PC-control and PC-power requests (classifiers only, no execution);
`tests/test_conversation_fixes.py::test_dry_run_runs_before_quick_answers_and_pc_control` traps `quick.handle`, `pcfast.handle`, the tool registry and the model.

**Rule added.** A live probe is only "REAL" if every route the phrase could take (including the model's) is read-only. Everything else is sent with `[dry-run]`, and a dry-run claim is
itself verified by watching for side effects (processes, files), not assumed.
