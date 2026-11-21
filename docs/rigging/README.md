# JARVIS character rigging — checkpoint (2026-10-31, Claude Code continuing Codex's Blender mission)

## What Codex built (verified to exist)
| Item | Where | Status |
|---|---|---|
| Isolated Hunyuan3D-2 install + venv | `%LOCALAPPDATA%\Packages\OpenAI.Codex_*\LocalCache\Local\Jarvis3D\{Hunyuan3D-2,hunyuan-venv}` | present, reused (not reinstalled) |
| Official base shape weights | `...\Jarvis3D\hf-cache\hub\models--tencent--Hunyuan3D-2` | present, reused |
| Penguin demo GLB (370k verts / 739k faces) | `...\Jarvis3D\outputs\shape-smoke\demo-shape.glb` | re-measured: **369,722 verts, 739,448 faces, 1 manifold component, no UVs** (matches Codex) |
| Worker lifecycle fix, rig/bind/reference ops | `jarvis-hub/hub/blender_worker.py` (uncommitted on `feat/control-hub`) | preserved, extended |
| Live headless worker | Blender pid 26480, port 9542 | idle (finished or abandoned the bind) |

## Findings about the unfinished auto-weight test
* **It was not a failure.** Full-resolution `ARMATURE_AUTO` on that mesh completes: **49.9 s** (5-bone rig); 60k faces 0.78 s; 20k faces 0.2 s (≈linear). Codex's 10 s *client socket timeout* was the problem. Fixed: `blender_client.LONG_OPS` (190 s, worker waits 180 s).
* **The test mesh was a penguin holding a sign**, so a humanoid rig on it could never validate shoulder/elbow/finger/hip deformation. Real humanoid tests below.
* Root-cause rule recorded as a JARVIS lesson: never skin raw generator output; skin a game-res copy.

## What was built (all new, all in this change)
| Piece | File |
|---|---|
| Rigging engine: clean → classify → game-res decimation → fitted skeleton (53 bones incl. fingers/toes; quadruped template) → skin with repair chain → IK/FK → pose-test verification loop → UE5 names → anatomy manifest → limb sever | `hub/rigging.py` |
| Real-Blender acceptance harness | `hub/rig_acceptance.py`, `hub/rig_recovery_test.py`, `hub/rig_lsw_test.py`, `hub/synth_meshes.py`, `hub/draw_overlay.py` |
| **JARVIS skills** (callable by the brain): `rig_character`, `image_to_character`, `rigging_skills`, `rigging_lessons` | `tools/rigging.py` (registered in `tools.ALL`) |
| Free read-only PC awareness: `observe_pc`, `list_windows`, `wait_for_window` | `tools/pcops.py` |
| Image → GLB on the isolated Hunyuan venv | `scripts/image_to_glb.py` |
| Live-worker op `rig_auto` + MCP tool `rig_character` (approval-gated) + long-op client timeouts | `jarvis-hub/hub/{blender_worker,blender_client,blender_mcp}.py`, copy of `rigging.py` |
| Tests | `tests/test_rigging_skill.py` (incl. a real Blender run), `jarvis-hub/hub/test_rigging_worker.py` |

## Acceptance results (REAL Blender 5.2.2 + RTX 5070 Ti; nothing mocked)
| Subject | Skin | Pose tests | IK/FK | Export glb+fbx | Re-import + animation round-trip |
|---|---|---|---|---|---|
| Meshy "Shadow Vanguard" humanoid (real T-pose character, via JARVIS `rig_character`) | valid | **15/15** (incl. fingers) | 4/4 | PASS | PASS |
| Chibi robot (old mesh) | valid | 14/14 | 4/4 | PASS | PASS |
| Synthetic humanoid with 5-finger hands (**synthetic**) | valid | **16/16** (fingers pass) | 4/4 | PASS | PASS |
| Synthetic quadruped (**synthetic**) | valid | 10/10 | 2/2 | PASS | PASS |
| **Image → Hunyuan3D (GPU, 61 s) → rig, one call to `image_to_character`** | valid | 14/14 | 4/4 | PASS | PASS |

Pose tests measure: moved fraction of the limb's vertices, stray motion elsewhere, edge stretch/compress (candy-wrapper), per joint
(shoulder, elbow, wrist, fingers, hip, knee, ankle, spine, neck). Re-import test compares deformed vertices at animation frame 12
after glTF export/import (0.0 error) and asserts the animation actually deforms the body (25–41 % of height).

### Deliberate-failure recovery (`docs/rigging/recovery.json`)
| Defect | Detected | Repair | Result |
|---|---|---|---|
| A. disconnected overlapping shells | yes (heat weighting invalid ×2) | voxel-weld proxy + weight transfer, landmark re-fit | **partial: 6/14 → 10/14**; forearm/wrist still fail |
| B. wrong landmark override | yes (pose tests 12/16) | re-fit ignoring override | **fully recovered: 16/16** |
| Worker timeout | yes | long-op client timeout | fixed |

### Living Shadow World anatomy (`docs/rigging/lsw_anatomy.json`)
`anatomy_manifest()` emits a limb graph (sever joints, bone chains, locomotion consequences, regeneration stump/order, vital regions).
`split_limb()` severs an arm and a leg into their own mesh + own armature: limb moves 36–42 % of height alone, body displacement 0.0.
**Caveat:** the thigh stump received no cap face (not watertight); regeneration/injury *runtime* behaviour is not implemented, only the data and geometry.

## Passed / failed / untested — honest list
* **Passed (measured):** everything in the tables above.
* **Failed / weak:** disconnected-shell recovery (10/14); fingers on the real Meshy gloves are pruned (no separable fingers at mesh resolution) so finger deformation is only proven on the *synthetic* hands; thigh stump cap.
* **Untested:** Unreal Engine import/retarget of the FBX (UE5 bone names are applied but never imported into UE); live GUI worker `rig_auto` end-to-end (unit-tested for validation only; the same code ran headless); true retopology (only collapse-decimate exists) (QuadriFlow/Instant Meshes not wired); non-humanoid/non-quadruped anatomy (serpent, winged, multi-limb) beyond classification; Hunyuan texture stage (no texture pipeline); stability on many more images; PC *action* upgrades (click/type) — only observation added.
* Nothing is labelled "mastered".

## Known limits / design choices
* Landmark fit is heuristic slice analysis, verified by measured tests; fit overlays are in `*/fit_front_bones.png` for visual review.
* `rig_character` / `image_to_character` run one fixed headless job with no model-written code, writing only into a new unique folder; no paid calls. The live-worker/MCP path still goes through owner approval.
* `rigging.py` exists in two repos (`jarvis/hub` canonical, copy in `jarvis-hub/hub`): keep in sync until the hub is merged.
* The data ledger (`blender/rigging_ledger.json`) lives under JARVIS's global data dir.
