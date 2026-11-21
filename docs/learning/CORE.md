# JARVIS universal learning core (#171)

One engine, many skills. Blender (creature rigging), Blender basics and Python debugging all run on the same code; adding a skill is one
adapter class and one line in `core/learn_skills.py`. PC control (#172) is the planned next adapter.

## Layout

| File | Role |
|---|---|
| `core/learn.py` | The contract (`Adapter`), the episode `Engine`, the shared improvement rule `better`, progress levels, the a-e capability report. Knows no skill. |
| `core/dojo_store.py` | The one memory (extended, not replaced): episodes, sealed exams, versioned policies, lessons, playbook, STOP flag. |
| `core/learn_policy.py` | Reinforcement learning: reward from the judge, LinUCB contextual bandit, versioned policies, held-out evaluation with a paired bootstrap. |
| `core/learn_academy.py` | Research -> Study -> Practice -> Test -> Fix -> Remember: docs-first lessons, teach-back, drills, sealed A/B exam, offline recall. |
| `core/learn_rsi.py` | Self-improvement: diagnose unsolved training tasks, propose code, isolate, verify, keep as `pending_review`. |
| `core/learn_llm.py` | Local model helper (CPU by default, cached, deterministic). |
| `core/learn_blender.py` | Adapter over the existing dojo + rigging engine (creatures). |
| `core/learn_blender_basics.py` + `hub/learn_basics_judge.py` | Adapter + judge for the two-bone armature skill, run in headless Blender. |
| `core/learn_python.py` + `core/learn_pytasks.py` | Adapter + task bank + judge for Python bug fixing, run in the Windows AppContainer sandbox. |
| `scripts/learn.py` | Command line: `status`, `seal`, `matrix`, `academy`, `train`, `eval`, `replay`, `rsi`. |

## Rules the core enforces (each has a test)

* **Immutable judge.** The judge's code hash is stored in every episode; a result is comparable only to results under the same judge; a judge that changes during an episode raises `JudgeChanged`.
* **Sealed exams.** Held-out tasks are sealed before training. A sealed task cannot be run for training, a trained-on task cannot be sealed, and policy training reads only non-sealed training rows.
* **No seeded answers.** Episodes that used teacher hints are `assisted` and never count. The self-improvement prompt is refused if it contains any line that would give a repair away.
* **No reward hacking.** `better` rejects tie-breaks, ratios inflated by adding tests, and "passing" by shrinking what is tested; the RL reward gives -1 for shrinking.
* **Truthful levels.** learned-about, attempted, practised, evaluated, independently verified, deployed, mastered are *computed* from stored episodes. `deployed` needs an `owner_approved` event; `mastered` also needs a second session (a restart).
* **a-e reporting.** (a) reading, (b) saved memory, (c) tuning/search, (d) trained policy updates, (e) model-weight updates are counted separately. (e) is zero: no model weight has ever been changed.
* **Review gate.** Code JARVIS writes for himself is `pending_review`; production never loads it.
* **Safety.** STOP file, gaming check, cancel, CPU-only local model by default, no network except trusted documentation hosts.

## Reinforcement learning (what is and is not claimed)

State = adapter features of the task; action = a strategy; reward = judged outcome (+1 full pass, up to +0.4 partial, minus time, minus invalid action, -1 for shrinking the tests);
policy = LinUCB (a linear model per action, updated from the reward of the action it chose); episodes are one step, `Policy.update` already accepts `next_state`/`done` for a
sequential method. A policy "improves" only if the 95% paired-bootstrap interval of (learned - strongest baseline) on at least 40 sealed tasks is above zero.
Nothing here changes any language-model weights; model-weight RL is a separate, owner-gated track (see ROADMAP in this folder).
