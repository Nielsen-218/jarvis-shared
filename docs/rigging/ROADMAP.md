# JARVIS character skills: roadmap to real mastery (agreed with the owner 2026-10-31)

Goal: JARVIS finds the character the owner means (open in Blender, a file by name, or a picture), rigs it, tests it,
learns from every run, and only calls a skill "mastered" on evidence. Status legend: DONE / NEXT / LATER.

## 000. CREATURES (docs/rigging/CREATURES.md): template-free anatomy understanding + skeleton generation; unseen creatures 2/6 pass; GPU describe->rig->Unreal works end to end (314 s).

## 00. THE DOJO (see docs/rigging/DOJO.md): JARVIS investigates, experiments, verifies and remembers on his own GPU/CPU. First use: solved
red_sweater himself (13/14 -> 14/14, verified incl. Unreal), failed pink_confidence (and caught/fixed flaws in his own learning).

## 0. Built since: deterministic front door `core/craftintent.py` (no 3B model for craft), background jobs + cancel + GPU guard
(`core/craftjobs.py`), Unreal import gate in every ue5 rig (`hub/ue_check.py`), failure taxonomy + learned remedies + retry budget
(`tools/remedies.py`), run manifests + confirm-only tidy, describe-to-character flow (`tools/makechar.py`, FLUX klein -> vision check -> Hunyuan -> rig).
NOT yet run on the real GPU: the describe-to-character flow end to end.

## 1. Resolver (DONE, PR #162)  `tools/charjobs.py`
* `rig_my_character` / `find_character`: path or picture as given -> model open in Blender -> file by name -> ASK.
* Never guesses between files; asks "Which one do you mean?". Never touches the open Blender session.
* Open + saved -> rigs the file. Open + unsaved edits -> rigs a copy of Blender's own autosave
  (`%TEMP%\<name>_<pid>_autosave.blend`, <= 2 min old) and says so; no autosave -> asks the owner to save.
* Read-only on the owner's files; writes only new files under the Blender workspace.
* Voice routing: `core/intelligence.py::_own_skill_tools` runs BEFORE the low-signal-fragment gate.

## 2. Live open-model path through the GUI worker (NEXT)
* `rig_auto` op exists in `jarvis-hub` (branch `claude/rig-auto-worker`, unmerged: it sits on the control-hub PR #11 lineage).
* Needed: decide how to ship the hub worker, then prove `rig_auto` end to end inside a GUI Blender session
  (currently only unit-tested; the same code ran headless). Until then the autosave route covers unsaved edits.
* Owner's session stays untouched: result goes into a new collection or a new file.

## 3. Benchmark corpus + score + regression gate (BUILT 2026-10-31; first result is sobering)
* `docs/rigging/corpus.json` (9 cases: 3 synthetic, Vanguard + Neon Sentinel = dev, Red Sweater + Pink Confidence + elf +
  ComfyUI_00008 = HELD-OUT) and `scripts/rig_corpus.py` (real Blender, `--unreal` adds the Unreal gate). Exit 1 on a regression
  against `data/rigging/scorecards/latest.json`. Snapshot: `docs/rigging/scorecard-2026-10-31.json`.
* **First result: dev 5/5, held-out 0/4.** After recalibration below: **dev 5/5, held-out 2/4** (snapshot `scorecard-2026-10-31b.json`).
* What the failures were (diagnosed with measurements + renders, not guesses):
  * **elf, comfyui_00008: metric false alarms.** Both deform well by eye. The old rule (p99.9 edge stretch < 2.2) tripped on ~0.1% of
    armpit/seam edges. New rule (`hub/rigging.py::pose_test`): torn-edge fraction (>2.5x) <= 0.15% and p99 < 1.6. Evidence: healthy rigs
    max 0.106% / p99 1.35; visibly torn Pink arms/hips 0.21-0.53% / p99 up to 1.7. The gap is thin (4 characters) and must be re-checked as the
    corpus grows. The spine-twist "far" radius is 0.2H (twist blends the waist-to-hip band). **These two are no longer clean held-out cases:
    I used their numbers to calibrate the metric.**
  * **red_sweater (13/14, wrist.R): real.** Arms hang beside the body with hands at shorts level, below the arm-tip search window, so the right
    hand bone sits ~0.17H too high. Hand and thigh are not separable by simple geometry here (gap ~1% of height).
  * **pink_confidence (5/14): real.** 5.4M-face T-pose; heat weighting fails on the raw/proxy mesh, the voxel-weld proxy then produces torn
    arm/hip skin (torn fraction 0.2-0.5%). Retrying with other densities did not help (tried 40k/20k).
* Retry engine finding: it retried a plainly humanoid character as a quadruped; swap_kind is no longer offered for pose failures.
* NEXT lever for the two real cases: vision-model landmarks (ask the local VLM for hand/foot boxes on an orthographic front render, convert to
  world coordinates, then fit) and a better path for very dense, hair-heavy scans (cage/volumetric weights instead of heat on a voxel weld).
* Observed over the corpus: see `observed_metric_distribution` in the scorecards; thresholds change only with new evidence plus a visual check.

## 4. Candidate tournament (LATER)
Try several riggers and keep whichever scores best on the SAME measured tests:
(a) our fitter (`hub/rigging.py`), (b) Blender Rigify humanoid, (c) UniRig (cloned at `C:\Users\you\blender-avengers\04-rigging-retopo\UniRig`,
MIT, handles arbitrary creatures), (d) retarget onto the UE5 mannequin skeleton so Epic animations can be reused.

## 5. Visual self-review (LATER)
Render fit overlays and let the local vision model check them ("do the bones sit inside the arms and legs?").
Catches fit errors that numeric pose tests miss (a pass was never proof of a good fit).

## 6. Mastery rules (LATER, enforced by `tools/rigging.py` ledger)
A capability is "mastered" only when: >= N distinct models per kind pass with zero manual help, the held-out set passes,
and ground-truth comparison is within tolerance. Any corpus regression revokes the label. Pipeline CODE changes stay
behind tests and owner approval; JARVIS may autonomously tune parameters and record lessons.

## Known gaps (honest list)
* Unreal import: FIXED and gated (one root bone, vertices +10% not x3, mannequin names, animation, skin weights normalised) for
  the synthetic humanoid; the gate has not yet been run across the whole corpus (`rig_corpus.py --unreal`).
* Pose normalisation + visual review of fits (the vision model as arbiter) is the next lever for the held-out failures.
* `jarvis-hub` branch `claude/rig-auto-worker` still holds a stale copy of `rigging.py` and the `rig_auto` op: HELD on purpose;
  `hub/rigging.py` in this repo is the single source of truth and the copy will be dropped when the worker is revisited.
* Fingers only proven on synthetic hands + on the Neon Sentinel; real gloves/boots have no separable fingers/toes (pruned).
* Disconnected-shell recovery only 10/14 pose tests; thigh stump has no cap face; retopology = decimation only.
* Hunyuan texture stage not wired; image-to-3D tested on one image.
