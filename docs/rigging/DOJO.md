# The dojo: how JARVIS learns Blender by himself

Owner's goal: not a script-follower. Observe -> Create -> Test -> Fail -> Investigate -> Experiment -> Improve -> Verify -> Remember.
This is the foundation for that loop. Code: `core/dojo.py` (loop), `core/dojo_store.py` (memory), `hub/overlay_guard.py` (safety),
`hub/rig_params.py` (knobs), `tools/dojo.py` (voice), `scripts/practice.py` (CLI). Memory lives in `data/dojo/blender/` (never in git).

## The loop, as built
1. **Observe**: baseline run of a failing character through the real engine; an evidence bundle built from measurements only
   (failing tests, stray bones and where they sit, landmarks, skin attempts, density).
2. **Investigate**: recall past lessons with a similar failure signature; one web search for how riggers fix the symptom
   (hints only, marked unverified); the LOCAL model (qwen3-vl 8B via Ollama, free) proposes up to 3 testable hypotheses.
3. **Experiment**: each hypothesis is a parameter set (14 knobs, ranges enforced) or, from round 2, a replacement of ONE allow-listed
   engine function (code overlay). Overlays are AST-checked, run only inside the Blender subprocess, never written to the repo.
4. **Improve/Verify**: kept only if it makes a MEANINGFUL improvement (full pass, a stage fixed, or more pose tests passing; a
   tie-break is not enough) AND, through the exact production path (learned strategies run only after the default fit fails),
   the target still improves and every previously-passing case still passes.
5. **Remember**: every outcome is a lesson: verified, refuted, regressed, rejected (invalid/unsafe), error, demoted. Refuted ideas are not
   re-run in later sessions; rejected parameter names are fed back to the model. Learned strategies carry their evidence and win rate.

## Safety (what protects the working system)
* **The judge is fixed**: pose tests, skin validation, IK tests, export, round trip, UE profile have no knobs and cannot be replaced by an
  overlay (`PROTECTED` in the guard). An experiment cannot improve its score by changing how it is measured.
* Overlays: no files, network, processes, eval/exec, dunder tricks, Blender save/export/script operators, `while True`.
* Production can never get worse: learned strategies run only if the default fit did not fully pass; the best MEASURED result wins.
* Session guards: kill switch (`data/dojo/blender/STOP`, "stop practising"), yields to real rig jobs, stops for games, time and
  experiment budgets, self-audit demotes strategies whose own evidence shows no real improvement.
* Mastery is never self-declared: `mastery()` counts only held-out cases whose failures were never studied (needs >= 5 per kind).

## What happened on first use (2026-10-31)
* **red_sweater (13/14)**: the local model diagnosed a misfit of the arm-axis regression region and tried `arm_reg_lo 1.2, arm_reg_hi 0.85`:
  14/14, verified through the production path, 7 passing cases unharmed; independently re-run through `tools.rigging` incl. real Unreal
  import: PASS. Found in 6 minutes; the cause I had guessed by hand (arm-tip window) was wrong.
* **pink_confidence (6/14)**: NOT solved in 26 minutes / 10 experiments. Findings about the learner itself: it promoted a 6/14 -> 6/14
  "fix" (my acceptance rule counted a tie-break; fixed, and the audit demoted that strategy), and the model repeated a non-existent
  parameter four times (now fed back as banned names). It also never proposed a code patch: the dense-scan skin problem likely needs one.

## Not built yet (honest list)
* **Unfamiliar creatures**: the engine still knows humanoid and quadruped only. Next: data-driven skeleton templates (spine, limb pairs,
  wings, tail, extra heads) + generic per-chain pose tests, so a dragon/spider is a template the dojo can create and test.
* Vision second opinion on renders (qwen3-vl) during investigation/verification; only measurements decide today.
* Describe-to-character and fresh never-seen characters as the held-out supply (the GPU flow is untested end to end).
* Nightly/idle scheduling (manual start only), and extending the same loop to skills other than Blender.
* Overlay (code-patch) path is proven with hand-written overlays and the guard; a model-written patch has not yet succeeded in practice.


## Update 2026-10-31 night: creatures, review gate, issues #171 / #172
* Creature anatomy understanding and template-free skeleton generation: docs/rigging/CREATURES.md. The dojo now practises creatures with research cards (trusted
  sources, injection-stripped), a vision second opinion and `anatomy_refine_roles` as the code extension point.
* **#171 compliance**: code JARVIS writes for himself is `pending_review` (never runs on real rigs without owner approval); improvement means fewer failing tests without
  shrinking the skeleton; judge functions of both engine modules are protected; practice stops for games, yields to real jobs, kill switch, budgets;
  overnight/unattended practice stays DISABLED until the owner agrees resource limits and hours; failures and refuted ideas are retained.
* Mapping to the issue's levels for the Blender skill: learned-about (research cards), attempted/practised (45+ experiments), evaluated (fixed judge + Unreal),
  independently verified (production-path regression checks), deployed (parameter strategies only; code awaits owner), mastered: NO (needs fresh unstudied cases per kind).
* Not yet done for #171: a second skill domain on the same engine; cross-domain adapter extraction (the Blender dojo is the first adapter, `core/dojo*.py`);
  #172 (PC autonomy) untouched in this pass.
