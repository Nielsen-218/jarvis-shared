# Creature anatomy understanding (no body templates)

Goal (owner, 2026-10-31): JARVIS looks at a creature neither of us has seen, works out how its body is built, generates a skeleton
from that anatomy, rigs and tests the movements the creature needs, and gets better at it by research and experiment.
Issues followed: #171 (cross-skill recursive self-improvement), #172 (PC autonomy; not touched here).

## How it works (hub/anatomy.py, runs in Blender, numpy only)
1. **Voxelize** the mesh into a sealed solid; **thickness field** by erosion depth.
2. **Membranes** (wings, fins, fans) = tissue thin everywhere locally AND wide in two directions (component PCA). Tubes are thin too but
   wide in one direction; the body shell is excluded because thick tissue lies behind it.
3. **Geodesic extremes**: BFS from the thickest core; limb/wing/tail tips = successive farthest uncovered points; each traced back to the
   body, centred in the tissue cross-section -> a **tree of branches** attached to a **body core** (or to the branch they grow from).
4. **Describe each branch from measurements**: axial or lateral, round or flat, ground contact (a limb that carries the body reaches the
   ground), mirrored partner, drop, share of membrane around it -> roles (axial/head/tail, leg, arm, wing, digit, appendage, drop). A small hook,
   `anatomy_refine_roles`, is the place JARVIS may patch his own understanding.
5. **Anatomy plan** (data): counts, symmetry, which movements the body suggests (walk/crawl, flap, slither, reach, articulate).
6. **Skeleton from the tree**: joints by Douglas-Peucker plus "long enough to bend"; bones named by role; bones that control no tissue are
   removed (evidence from the skin check). Nothing names a species.
7. **Skin** with the existing chain; **judge** = a bend test on EVERY joint (`generic_pose_tests`) + movement tests derived from the plan
   (`run_motion_tests`: gait, flap, undulation) + animation export, re-import round trip, and the real headless Unreal import. Protected from overlays.
8. **Visual second opinion** (`core/dojo_vision.py`): three orthographic views with the skeleton drawn in; qwen3-vl names the creature, counts
   limbs, says what looks wrong, with a confidence; compared to the geometric counts. Never sufficient on its own.
9. **Research first** (`core/anatomy_knowledge.py`): trusted sources only, injection-stripped, quoted as data, stored as
   `retrieved_unverified` cards (typical limb counts, joints, gaits) that feed the investigator and flag count mismatches; offline recall after restart.

## Honest scoreboard (final benchmark of this pass: dev 6/9, held-out 5/11, 20 cases incl. 4 humanoid dev + 4 humanoid held-out)
Humanoids: all pass except Pink Confidence (Red Sweater passes thanks to the strategy JARVIS learned himself).
Creatures (tests passing = joint bends + movement tests):
* development (tuned on): serpent PASS; spider 49/50, crawl marginal; bird 58/74 (was 54/76 before practice); dragon 71/81 (was 66/81).
* **unseen, procedural, written after the algorithm was frozen**: tripod PASS, starfish PASS; crab 52/57; centipede 63/64; hydra 28/30; manta 56/85 -> **2 of 6 fully pass**.
* **made by JARVIS on the real GPU** (description -> FLUX -> vision pick -> Hunyuan3D -> rig; 314 s; real Unreal import PASS): "six-legged crystal tortoise":
  rig found 5 of 6 legs; vision independently disagreed about the legs (correct); 23/31 tests; now corpus case `gen_crystal_tortoise` (local file, not in git).
Pink Confidence stays a flagged standing challenge (`challenge` in corpus.json).

## Learning-engine findings made on the way (all fixed)
Pipeline: mesh shared between strategies re-skinned the winner (zero vertex groups on re-import); black renders (EEVEE switch, no lights).
Acceptance rule, three times: tie-breaks counted as improvements; adding bones lifted the pass ratio; deleting limbs "fixed" failures. Improvement now means
FEWER FAILING TESTS without shrinking the skeleton (>= 80% of the tests), checked through the production path, and the self-audit demotes anything whose
own evidence does not show it.

## Self-written code (the proof the owner asked for), stated plainly
* His local model (qwen3-vl 8B) proposes parameter changes and CODE (`anatomy_refine_roles`); code passes the AST guard, runs in the sandboxed Blender
  subprocess, is judged by the fixed tests, regression-checked through the production path, and then waits as `pending_review`: **code he wrote for himself never
  runs on real rigs until the owner approves it** (`review_learned_strategies`, `approve learned strategy <id>`, `reject ...`). Parameter sets are data and are active once verified.
* **Code patch that passed verification**: bird 54/76 -> 54/75 (one fewer failing test). Its prompt contained my example fixes, which the patch implements, so this
  is NOT unaided invention. I removed the examples afterwards.
* **Unaided runs (no hints, 45 experiments over 3 sessions on dragon, manta, hydra, bird, tortoise)**: 3 verified improvements, all PARAMETER changes found by his model:
  bird `anat_center_iters` 5 (54/75 -> 59/76, 21 -> 17 failing, 11 passing cases still pass); centipede `anat_center_iters` 6 (5 -> 2 failing). Every unaided CODE patch (about 20)
  was refuted or rejected: none improved a measurement. So: self-written parameter discoveries are proven; a self-written code improvement that he found WITHOUT my hint
  is not yet demonstrated, and I will not claim it.
* Awaiting your review: `aa66b1a5` (the bird patch). Demoted by his own audit as non-improvements: 4 strategies (tie-breaks, ratio gaming, limb deletion).

## Not built / open
Skeleton fitting for dense organic scans (Pink); gaits with ground-contact and foot-slip tests; bones from vision when geometry is ambiguous; a creature
taxonomy learned from references beyond typical counts; fresh unseen test supply beyond procedural and GPU-generated creatures; second skill domain on the same
engine (#171 step 4); overnight scheduling stays disabled until resource limits and hours are agreed.
