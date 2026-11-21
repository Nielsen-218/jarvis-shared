# Model-weight training: feasibility roadmap (NOT started, owner-gated)

Status: **nothing here has been run.** No weight of any model on this PC has ever been changed by JARVIS. The learning core reports this
as capability (e): `checkpoints: 0`. Everything the core has shown so far is (a) reading, (b) saved memory, (c) search/parameter tuning
and (d) a trained *selection policy* (a small linear model, not a language model).

## What a weight-level pilot would be

Train a small parameter-efficient adapter (LoRA/QLoRA) for the local coding model, using the Python judge as the reward:
* Data: judged episodes of the learning core (task, prompt, candidate, outcome). Only TRAINING-split episodes whose judge hash matches; sealed exam tasks are excluded by the same `training_episodes()` gate the bandit uses.
* Method options, cheapest first: (1) rejection-sampling fine-tuning (keep the candidates that passed, fine-tune on them), (2) DPO on pass/fail pairs of the same task, (3) a policy-gradient method (GRPO-style) with the judge as the verifier.
* Evaluation: the same sealed held-out tasks, pre/post, paired bootstrap, plus the regression suite and a check that general chat and tool use did not degrade.
* Rollback: adapters are separate files; the base weights are never overwritten; the Ollama model is switched by name.

## Constraints to verify BEFORE any run (none of these has been checked yet)

| Question | How it will be checked | Status |
|---|---|---|
| Does 16 GB VRAM (RTX 5070 Ti) fit a 3B model with QLoRA at the needed sequence length? | measured dry run with a tiny batch, attended | unknown |
| Can Ollama's GGUF models be adapted, or must the HF weights be fetched and re-exported? | read the Ollama and llama.cpp docs for the installed versions | unknown |
| Licence terms of the base model for fine-tuning and local use | read the model card | unknown |
| Is there enough *passing* training data? (the 3B model solves ~60-70% of the small tasks; far fewer on hard ones) | count from stored episodes | partly known |
| Cost | local electricity/time only; no paid API | by design |

## Gates (owner approval required for each)

1. Approve the experiment plan (model, method, data, budget in GPU-hours, hours of day, stop conditions).
2. Approve the dry run (attended, game-aware, STOP file honoured).
3. Approve promotion of any adapter: it would be served under a NEW model name next to the old one; nothing is replaced.

## Risks

Reward hacking (mitigated by the immutable judge and the shrink penalty), overfitting to the small task bank (mitigated by sealed held-out tasks and the paired bootstrap),
catastrophic forgetting (mitigated by a regression battery and a separate adapter), and wasted GPU time (mitigated by a hard budget and attended runs only).
