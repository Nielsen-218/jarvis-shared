// Node ESM harness for the pure helpers in app/companion/glb-adapter.js
// (bone-name mapping, normalization math, material-slot inference) and the
// adapter's synchronous contract with a stub three namespace. The real GLB
// load path needs WebGL and is covered by Windows visual acceptance, not here.
//   node tests/node/test_glb_adapter.mjs
import {
  ADAPTER_ID, TARGET_HEIGHT_M, sideOf, mapBoneNames, computeNormalization,
  inferMaterialSlot, buildGlbAdapter,
} from "../../app/companion/glb-adapter.js";

let failures = [], passes = 0;
const assert = (c, name, detail = "") => { if (c) passes += 1; else failures.push({ name, detail }); };
const eq = (a, b, name) => assert(JSON.stringify(a) === JSON.stringify(b), name, `got ${JSON.stringify(a)} want ${JSON.stringify(b)}`);

// ---- sideOf ----
eq(sideOf("LeftArm"), "L", "sideOf LeftArm");
eq(sideOf("mixamorig:RightHand"), "R", "sideOf mixamo right");
eq(sideOf("hand.R"), "R", "sideOf dot suffix");
eq(sideOf("arm_l"), "L", "sideOf underscore suffix");
eq(sideOf("Head"), null, "sideOf none");

// ---- mapBoneNames: Mixamo-style humanoid (parent-first order) ----
const mixamo = ["mixamorig:Hips", "mixamorig:Spine", "mixamorig:Spine1", "mixamorig:Neck", "mixamorig:Head",
  "mixamorig:LeftShoulder", "mixamorig:LeftArm", "mixamorig:LeftForeArm", "mixamorig:LeftHand", "mixamorig:LeftHandIndex1",
  "mixamorig:RightShoulder", "mixamorig:RightArm", "mixamorig:RightForeArm", "mixamorig:RightHand", "mixamorig:RightHandThumb1",
  "mixamorig:LeftUpLeg", "mixamorig:LeftLeg", "mixamorig:LeftFoot", "mixamorig:RightUpLeg", "mixamorig:RightLeg", "mixamorig:RightFoot"];
const m = mapBoneNames(mixamo);
eq(m.bones.root, "mixamorig:Hips", "map root=hips");
eq(m.bones.torso, "mixamorig:Spine", "map torso = first spine");
eq(m.bones.head, "mixamorig:Head", "map head");
eq(m.bones.neck, "mixamorig:Neck", "map neck");
eq(m.bones.shoulderL, "mixamorig:LeftShoulder", "map shoulderL");
eq(m.bones.elbowL, "mixamorig:LeftForeArm", "map elbowL = forearm, not arm");
eq(m.bones.wristL, "mixamorig:LeftHand", "map wristL = hand");
eq(m.bones.fingersL, "mixamorig:LeftHandIndex1", "map fingersL");
eq(m.bones.fingersR, "mixamorig:RightHandThumb1", "map fingersR thumb");
eq(m.bones.hipL, "mixamorig:LeftUpLeg", "map hipL = upleg");
eq(m.bones.kneeL, "mixamorig:LeftLeg", "map kneeL");
eq(m.bones.ankleR, "mixamorig:RightFoot", "map ankleR");
assert(m.unmapped.includes("mixamorig:Spine1") && m.unmapped.includes("mixamorig:LeftArm"), "duplicates go to unmapped", JSON.stringify(m.unmapped));

// ---- mapBoneNames: Blender/Rigify style and robot parts ----
const rigify = ["root", "spine", "upper_arm.L", "forearm.L", "hand.L", "thigh.R", "shin.R", "foot.R", "ear_fin.L", "Reactor_Core", "antenna_R"];
const r = mapBoneNames(rigify).bones;
eq(r.root, "root", "rigify root");
eq(r.shoulderL, "upper_arm.L", "rigify shoulder");
eq(r.elbowL, "forearm.L", "rigify elbow");
eq(r.hipR, "thigh.R", "rigify hip");
eq(r.kneeR, "shin.R", "rigify knee");
eq(r.earL, "ear_fin.L", "robot ear fin");
eq(r.earR, "antenna_R", "robot antenna");
eq(r.chestCore, "Reactor_Core", "robot reactor core");
eq(mapBoneNames([]).bones, {}, "empty skeleton");

// ---- computeNormalization ----
const n = computeNormalization({ min: { x: -1, y: 2, z: -0.5 }, max: { x: 1, y: 6, z: 0.5 } });
assert(Math.abs(n.scale - TARGET_HEIGHT_M / 4) < 1e-9, "scale = target/height", n.scale);
assert(Math.abs(n.offset.y + 2 * n.scale) < 1e-9, "feet lifted to y=0", n.offset.y);
eq([n.offset.x, n.offset.z], [0, 0], "centred model has zero x/z offset");
const n2 = computeNormalization({ min: { x: 10, y: 0, z: 0 }, max: { x: 12, y: 1, z: 2 } }, 0.3, 2);
assert(Math.abs(n2.scale - 0.6) < 1e-9, "overall_scale doubles height", n2.scale);
assert(Math.abs(n2.offset.x + 11 * 0.6) < 1e-9, "x re-centred", n2.offset.x);
assert(computeNormalization({ min: { x: 0, y: 0, z: 0 }, max: { x: 0, y: 0, z: 0 } }).scale === TARGET_HEIGHT_M, "degenerate height does not divide by zero");

// ---- inferMaterialSlot ----
eq(inferMaterialSlot("Eye_L_Glow"), "eye_glow", "slot eye");
eq(inferMaterialSlot("MouthScreen"), "mouth_glow", "slot mouth before screen");
eq(inferMaterialSlot("ChestReactor"), "chest_core", "slot chest");
eq(inferMaterialSlot("FaceVisor"), "face_screen", "slot face");
eq(inferMaterialSlot("Foot_Thruster"), "foot_glow", "slot foot");
eq(inferMaterialSlot("EarRing"), "accent_secondary", "slot ear");
eq(inferMaterialSlot("ClawEnergy"), "accent_primary", "slot claw glow");
eq(inferMaterialSlot("Joint_Graphite"), "joint_dark", "slot joint");
eq(inferMaterialSlot("Armor_White"), "shell_light", "slot armor");
eq(inferMaterialSlot(""), "shell_light", "slot default");
// The shipped multi-material robot's slot names must each infer to their own slot.
// REGRESSION: "shell_light" would hit the /light/ (accent) rule BEFORE /shell/, mislabelling the
// whole body as accent_primary — so the shell material is named "shell", which maps correctly.
eq(inferMaterialSlot("shell"), "shell_light", "shipped shell name -> shell_light");
eq(inferMaterialSlot("eye_glow"), "eye_glow", "shipped eye name -> eye_glow");
eq(inferMaterialSlot("chest_core"), "chest_core", "shipped chest name -> chest_core");
eq(inferMaterialSlot("accent_primary"), "accent_primary", "shipped accent name -> accent_primary");
assert(inferMaterialSlot("shell_light") !== "shell_light", "shell_light name is mislabelled (why the shell is named 'shell')");

// ---- buildGlbAdapter sync contract with a stub three namespace ----
class Group { constructor() { this.children = []; this.name = ""; } add(c) { this.children.push(c); } clear() { this.children = []; } traverse(f) { f(this); this.children.forEach((c) => c.traverse ? c.traverse(f) : f(c)); } }
const T = { Group, Box3: class {}, };
const a = buildGlbAdapter(T, { adapter_id: ADAPTER_ID, model_url: "/companion/models/x.glb" });
assert(a.group instanceof Group && a.bones.root === a.group, "adapter exposes group + root bone immediately");
assert(a.ready && typeof a.ready.then === "function", "adapter exposes ready promise");
eq(a.capabilities.loaded, false, "not loaded before ready");
eq(a.capabilities.adapter_id, ADAPTER_ID, "capabilities carry adapter id");
assert(typeof a.dispose === "function", "adapter exposes dispose");
const noUrl = buildGlbAdapter(T, {});
await noUrl.ready.then(() => assert(false, "ready must reject without model_url"), (e) => assert(/model_url/.test(String(e.message)), "ready rejects without model_url", e.message));
// With a url but no loader available in Node, ready rejects (never hangs) and records the error.
await a.ready.catch(() => {});
assert(typeof a.error === "string" && a.error.length > 0, "load failure is recorded on adapter.error", a.error);
a.dispose();
eq(a.group.children.length, 0, "dispose clears the group");

console.log(JSON.stringify({ schema: 1, passed: passes, failed: failures.length, failures }));
process.exit(failures.length ? 1 : 0);
