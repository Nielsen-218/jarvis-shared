# Learning core: what passed, what failed, what is unproven

Branch `claude/learning-core` (not merged, not deployed). Full regression suite: **2264 passed, 5 skipped** (2231 before + 33 new).
Everything below was measured on this PC with the real Windows AppContainer sandbox, real headless Blender 5.2.2, and the local models
(llama3.2:3b, qwen3-vl:8b-instruct) on CPU. Raw episodes are in `data/dojo/<skill>/episodes.jsonl` (not in git).

## 1. One shared core, three skills (PASSED)

* `core/learn.py` knows no skill. Python debugging, Blender creature rigging (the existing dojo) and Blender basics all run on its `Engine`, store, judge-hash, sealing and progress rules.
* The Blender adapter returns the same verdict as the dojo for the same run (`test_adapter_matches_the_dojo_verdict`, real Blender); `dojo.better` now delegates to the shared rule.
* Real sandbox verified first: a candidate that writes outside the lab is denied, a `while True` candidate is stopped by the step limit (`test_sandbox_contains_hostile_and_runaway_candidates`).

## 2. Reinforcement learning (PASSED against the pre-declared baselines, with a caveat)

Python debugging, 56 training tasks (18 functions) and **55 sealed held-out tasks (16 different functions)**; five strategies; the policy was trained
and saved **before any held-out episode existed**. Reward = judge outcome only (full pass, partial, time, invalid action, -1 for shrinking tests).

| held-out, 55 tasks | full passes | mean reward |
|---|---|---|
| learned LinUCB policy | 43 | 0.774 |
| fixed default (`llm_traceback`, declared in advance) | 33 | 0.519 |
| best single action on TRAINING data (`llm_spec`) | 29 | 0.579 |
| random | - | 0.499 |
| oracle (best per task, upper bound) | - | 0.948 |

Learned minus strongest declared baseline: **+0.194, 95% paired-bootstrap CI [0.097, 0.298]** (above zero). The policy reloads identically in separate processes (`replay`, byte-identical output) and as a versioned JSON file (`lin00448-2f75771d2d`).

**Caveat, stated plainly:** the held-out tasks were easier for the mechanical search than the training tasks. "Always use `search`" (chosen after seeing the held-out results, so NOT a valid baseline) scores 0.817 with 45/55 full passes: the learned policy is statistically indistinguishable from it (difference -0.043, CI [-0.159, 0.071]). The policy learned a genuinely useful mix, but this pilot does **not** show it beating a fixed strategy that happens to suit the test set.

Blender creature pilot (real rigs, 6 training creatures x 3 strategies, 2 clean held-out creatures): the same train/evaluate code ran end to end, but both held-out creatures pass under every strategy, so there is no room to improve and only 2 tasks: `improves: false`. No Blender RL claim is made. Most older held-out creatures had already been studied by the dojo (burned), so the adapter now treats them as training material.

## 3. Academy: documentation-first teaching (PARTLY WORKED, honest failures)

* Mechanics tested: trusted-host fetching with injection filtering, failure-driven research (a search on the actual error), repair loop, teach-back prediction recorded before execution, offline recall of a verified lesson, sealed A/B exam, exact sign test, remedial study from drill failures only.
* **Python, "boundary bugs", from docs.python.org:** the first lesson was wrong (its example was inverted) and was **retracted** after I found the verifier too weak; the verifier now checks both versions against the stated spec; the corrected lesson passed on its second attempt. Teach-back prediction was wrong (predicted 0/1, actual 3/6).
  **Sealed A/B exam, 14 tasks: baseline 8/14, with the lesson 7/14, helped 1, hurt 2 (sign test p = 1.0). The lesson did not help; the exam was failed.**
* **Blender, two-bone armature, from docs.blender.org + the official Blender wiki archive cookbook + web search:** Over 20 study attempts across four runs (llama3.2:3b and qwen3-vl:8b on CPU), including searches on the actual errors. **No local model wrote a script that passed the judge, so no lesson was stored and no Blender exam was run.** The judge itself was validated (a teacher reference script passes all 9 checks on Z and X tubes; it is never shown to JARVIS).

## 4. Recursive self-improvement (NOT DEMONSTRATED)

Three cycles (13 proposal attempts) on the Python training tasks that no strategy had solved, with the unseeded-prompt check passing (no repair line in any prompt), a guard, sandbox isolation and a trusted helper library in the last cycle: **0 verified improvements.** Most attempts were syntactically invalid or tried to hard-code one task. All are logged (`rsi_attempt`, `rsi_failed`). The pipeline (propose -> isolate -> verify -> `pending_review`, never run in production) is tested with scripted proposals, not shown with JARVIS's own.
So: **no code improvement written by JARVIS has been verified in this work.** The bird patch `aa66b1a5` from the previous mission is still pending your decision and I recommend leaving it pending.

## 5. Persistence (PASSED)

Episodes, sealed set, lessons, policies and events are plain files under `data/dojo/<skill>/`; tests reload them in a new Store/Engine; the saved policy makes identical choices in separate processes.

## 6. What the progress levels say today

Python: `evaluated` using the fixed default action (60% of held-out tasks); `independently_verified` (78%) when judged on the learned policy's choices; not deployed (needs your approval), not mastered. Blender basics: `attempted`. Blender creatures: unchanged from the dojo scorecards (Pink Confidence still a standing challenge).

## 7. Reporting categories (a-e)

(a) reading: 2 verified lessons (Python), 0 for Blender basics. (b) saved memory: 163 own lessons, 628 episodes. (c) search/tuning: 119 search episodes. **(d) trained policy updates: 2 versions, 1 evaluated on held-out tasks.** **(e) model weights: 0 checkpoints; nothing was trained.** Feasibility plan: `MODEL_TRAINING.md` (not started).

## 8. Where JARVIS still depends on me

Task banks, judges, the helper library, the curriculum sources and all infrastructure are mine. His own contributions measured here are: the learned strategy-selection policy, verified Python lessons, and the (unsuccessful) attempts. Local 3B/8B CPU models are too weak to write Blender scripts from documentation or to invent code repairs; a stronger local model or a vision/video learning step would be the next lever, and any model-weight training needs your approval first.

## 9. Model bench (competitive elimination, spec 929b8ad6e7c9, local only, nothing deleted)

Runner: `scripts/model_bench.py` (Blender board), `scripts/voice_bench.py` (conversation board). Spec pre-registered and hashed in `bench/spec.json`.
Harness validity first: the teacher reference passes the judge on Z, X and Y tubes and an empty script fails. Same prompt, context 8192, temperature 0.7, seeds, 3072-token cap and judge for every model.
Model sizes were read from the official Ollama library pages (qwen3-coder:30b is 19 GB and was NOT tried: it cannot sit in 16 GB VRAM).

**Blender two-bone armature board, Stage A (8 training tasks x 4 samples; zero complete passes = eliminated)**

| model | complete passes | note |
|---|---|---|
| llama3.2:3b (incumbent) | 0/32 | eliminated |
| qwen3-vl:8b-instruct (incumbent) | 0/32 | eliminated |
| qwen2.5-coder:14b | 0/32 | eliminated (1 near miss, not counted) |
| devstral (24B) | 0/19 | eliminated; 30-min cap hit at 5 tok/s, 14.4 of 16 GB in VRAM |
| qwen3:14b | 0/32 | eliminated |
| gemma4:12b-it-qat | 0/32 | eliminated |
| gpt-oss:20b | 3/22 | advanced; 30-min cap hit at about 8 tok/s (VRAM was full: 12.7 GB model + about 3.4 GB already in use) |

**Stage B (gpt-oss:20b, all 24 training tasks, 90-min cap): 0/338 complete passes**, at 136 tok/s once the card was not squeezed. The three Stage A passes did NOT reproduce:
the same task and seed combinations failed in Stage B. Combined, gpt-oss passed 3 of 360 attempts (about 0.8%), and I cannot show those 3 were more than luck or numeric noise under memory pressure.
**Result: no local model passed this task in a way that can be relied on. No model is promoted on the Blender board.**
Failure modes (Stage B): 141+124 attempts built the bones but never bound the mesh ("Limb has no Armature modifier", "vertex groups are []"); the rest used wrong operator keyword arguments (`object`, `apply_as`, `apply`) or called `edit_bones` on a missing armature.
So the common break point is the BIND step and operator arguments, not bone creation. That is where a documentation lesson or a verified building block could help, and it is untested.

**Conversation board (the real brain, 15 checks: routing, exact answers, no leaks)**

| model | checks | first turn | warm median |
|---|---|---|---|
| llama3.2:3b (production) | 15/15 | 4.3 s | 0.3 s |
| qwen3:4b-instruct | 14/15 (missed the shopping-list tool) | 3.9 s | 0.4 s |
| gemma4:e4b-it-q4_K_M | 14/15 (wrong engineering maths) | 11.6 s | 1.2 s |

Production's `llama3.2:3b` is not beaten on routing or speed; the two candidates are eliminated from this board. These times cover the BRAIN only.

**Not done, and why:** speech board (no recorded owner audio exists; synthetic clips would not prove short-command accuracy); the audio-pipeline latency (microphone, speech-to-text, text-to-speech, first sound) needs an attended run with your voice;
the Python board; the Hermes comparison (no model passes the Blender task, so a Hermes versus JARVIS-loop comparison would compare two zeros); llama.cpp versus Ollama speed.

## 10. Verified procedure: two-bone limb binding (stored know-how versus regenerating code)

**Authorship, plainly:** the procedure (`procedures/blender_two_bone_limb_bind.py`) was written by Claude, then verified by the unmodified independent judge. Its episodes are marked `assisted` (teacher). JARVIS's part is to recognise the task, supply the parameters, run it, detect failure and pick a recovery. JARVIS did not invent the binding method.
**Diagnosis first (547 judged attempts from every model):** 543 failed the binding and deformation checks (99%); 394 had no Armature modifier at all; about 245 also got the bones wrong. The teacher reference binds with a context-dependent operator (`parent_set`, particular selection); the stored procedure uses the data API instead (edit bones, `vertex_groups.add`, an Armature modifier), then bends the lower bone 70 degrees and measures the deformed mesh itself.
**Registered only after** passing all 24 training tasks (judge hash `e7ac6bebc65392c7`; holdout never run, judge unchanged). Library: `core/procedures.py` (skill-agnostic; evidence required; source hash re-checked on every load).

| task set | without the skill | with the skill (recognise, run, recover) |
|---|---|---|
| 24 training tasks | llama3.2:3b 0/24; gpt-oss:20b 2/24 (one sample each) | **24/24** |
| 24 fresh tasks "dev" (seed 20260101) | llama3.2:3b 0/24 | 22/24 passed, 2 declined as out of range |
| 24 fresh tasks "dev2" (seed 777, run once after the fix) | llama3.2:3b 0/24 | **18/24 passed, 6 declined as out of range, 0 wrong** |

Without-the-skill figures come from the plain code-block prompt (`scripts/skill_baseline.py`; gpt-oss was stopped at 26 of 72 tasks because it ran slowly with the card nearly full: 2/26). The adapter's own JSON-style `llm_plain` path scored 0/72, but every row was the same syntax artifact, so I do not use it as the baseline. Stage B (section 9) had gpt-oss at 0/338 on the training tasks.
**What the first dev run found:** two short, fat tubes tore at a 70 degree bend even though the skill's own measurement caught it. A radius-to-length sweep (x and z tubes) showed 0.13 passes and 0.15 tears, so version 2 refuses tubes thicker than 0.135 x length (`too_thick`, ask the user) instead of binding badly. That limit was set after seeing "dev", so "dev" is not a clean test; "dev2" is the clean confirmation. Declined tasks are counted as not passed above. In-scope tasks: 24/24 train, 22/22 dev, 18/18 dev2.
**Matching is narrow:** a three-bone version of the task, a dragon/character rig, a Python bug and a vague "make the tube bend" request are all NOT matched (tested, no false match).
**Recovery, tested in real Blender:** a stale object named Rig is detected (`rig_exists`) and the retry with `rename_old` passes; a deliberately bad blend width fails its own measurement (`verification_failed`) and the retry with narrower widths passes; a missing mesh is detected and the procedure asks the user instead of guessing.
**Restart:** a fresh Python process loaded the stored procedure (v2) from disk, re-verified its source hash, recognised the task, and passed a dev task. Both versions stay on disk; nothing was overwritten.
**Scope limits:** straight, axis-aligned tube, exactly two bones, radius up to 0.135 x length. Not characters, creatures, tails, more bones or bent meshes. Python debugging, speech and Hermes evaluations remain unfinished. Tests: 11 new, full suite 2275 passed, 5 skipped.
