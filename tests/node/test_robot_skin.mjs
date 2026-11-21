// Node harness for the concept robot's runtime rig: skin weights, skeleton, proxy-bone sync and face helpers.
// Uses the vendored three.js and the real rigs/robot-rig.json.   node tests/node/test_robot_skin.mjs
import * as T from "../../app/vendor/three/three.module.min.js";
import { readFileSync } from "node:fs";
import { boneOrder, computeSkinWeights, buildRobotSkeleton, makeProxyBones, syncProxies, skinModel, distToSegment,
         applyProportions, normalizeProportions, DEFAULT_PROPORTIONS } from "../../app/companion/robot-skin.js";
import { faceFrame, mouthSize } from "../../app/companion/glb-face.js";

const rig = JSON.parse(readFileSync(new URL("../../app/companion/rigs/robot-rig.json", import.meta.url), "utf8"));
let failures = [], passes = 0;
const assert = (c, name, detail = "") => { if (c) passes += 1; else failures.push({ name, detail }); };
const near = (a, b, e = 1e-4) => Math.abs(a - b) <= e;

// ---- ordering / structure ----
const { order, index } = boneOrder(rig);
assert(order.length === Object.keys(rig.joints).length, "every joint ordered");
assert(order.every((n) => !rig.joints[n].parent || index[rig.joints[n].parent] < index[n]), "parents come before children");
for (const need of ["head", "torso", "shoulderL", "shoulderR", "elbowL", "wristR", "hipL", "kneeR", "ankleL", "earL", "earR", "fingersL"])
  assert(need in rig.joints, `rig has ${need}`);
assert(rig.joints.shoulderL.pivot[0] < 0 && rig.joints.shoulderR.pivot[0] > 0, "left is -X, right is +X");
assert(distToSegment(0, 1, 0, [0, 0, 0], [0, 2, 0]) === 0 && near(distToSegment(1, 3, 0, [0, 0, 0], [0, 2, 0]), Math.SQRT2), "distToSegment");

// ---- skin weights at landmark points ----
const probe = (pts) => {
  const { skinIndex, skinWeight } = computeSkinWeights(new Float32Array(pts.flat()), rig);
  return pts.map((_, i) => {
    const w = {}; for (let k = 0; k < 4; k++) if (skinWeight[i * 4 + k] > 0) w[order[skinIndex[i * 4 + k]]] = skinWeight[i * 4 + k];
    return w;
  });
};
const top = (w) => Object.entries(w).sort((a, b) => b[1] - a[1])[0][0];
const pts = {
  crown: [0, 0.27, 0], eyeL: [-0.0365, 0.198, 0.06], finTipR: [0.09, 0.295, 0], earDiscL: [-0.099, 0.205, 0],
  chest: [0, 0.128, 0.03], waist: [0, 0.085, 0.02], upperArmL: [-0.052, 0.13, 0], forearmR: [0.067, 0.103, 0],
  clawL: [-0.072, 0.066, 0], thighR: [0.031, 0.075, 0.01], shinL: [-0.033, 0.03, 0.01], bootR: [0.033, 0.01, 0.03],
};
const names = Object.keys(pts), W = probe(Object.values(pts));
const got = Object.fromEntries(names.map((n, i) => [n, top(W[i])]));
const want = { crown: "head", eyeL: "head", finTipR: "earR", earDiscL: "earL", chest: "torso", waist: "pelvis", upperArmL: "shoulderL",
  forearmR: "elbowR", clawL: null, thighR: "hipR", shinL: "kneeL", bootR: "ankleR" };
for (const n of names) if (want[n]) assert(got[n] === want[n], `weight owner of ${n}`, `${got[n]} != ${want[n]}`);
assert(["wristL", "fingersL"].includes(got.clawL), "claw is bound to the hand", got.clawL);
W.forEach((w, i) => {
  const sum = Object.values(w).reduce((a, b) => a + b, 0);
  assert(near(sum, 1, 1e-3) && Object.keys(w).length <= 4, `weights of ${names[i]} sum to 1 with <=4 influences`, JSON.stringify(w));
});
// mirror symmetry: (-x) weights are the L/R-swapped (+x) weights
const swap = (n) => n.endsWith("L") ? n.slice(0, -1) + "R" : n.endsWith("R") ? n.slice(0, -1) + "L" : n;
const sym = [[0.05, 0.13, 0.01], [0.07, 0.1, 0], [0.033, 0.03, 0.01], [0.09, 0.28, 0]];
const A = probe(sym), B = probe(sym.map(([x, y, z]) => [-x, y, z]));
A.forEach((a, i) => { const m = Object.fromEntries(Object.entries(a).map(([k, v]) => [swap(k), v]));
  assert(Object.keys(m).every((k) => near(m[k], B[i][k] ?? -1, 1e-3)), `mirror symmetry at sample ${i}`, JSON.stringify([m, B[i]])); });
// neck blend: head weight rises monotonically across the neck band
const ys = [0.146, 0.152, 0.156, 0.162].map((y) => probe([[0, y, 0]])[0].head || 0);
assert(ys.every((v, i) => i === 0 || v >= ys[i - 1]) && ys[0] < 0.2 && ys[3] > 0.99, "head weight ramps over the neck", JSON.stringify(ys));

// ---- skeleton ----
const skel = buildRobotSkeleton(T, rig);
skel.root.updateMatrixWorld(true);
const wp = new T.Vector3();
for (const n of ["head", "shoulderR", "kneeL", "ankleR", "fingersL"]) {
  skel.bones[n].getWorldPosition(wp);
  assert(wp.distanceTo(new T.Vector3(...rig.joints[n].pivot)) < 1e-6, `${n} sits at its pivot in the bind pose`);
}
assert(Object.values(skel.bones).every((b) => b.quaternion.w === 1), "rest rotations are identity");

// ---- proxy sync: the clips' bias is removed, deltas pass through ----
const proxies = makeProxyBones(T, rig), real = skel.bones;
assert(!("root" in proxies) && "head" in proxies && !("pelvis" in proxies), "proxies cover the semantic bones only");
proxies.shoulderL.rotation.z = -0.25;                       // what animation.js idle assigns
proxies.shoulderR.rotation.z = 0.25 + 0.5;
proxies.head.rotation.y = 0.3; proxies.torso.scale.setScalar(1.02);
syncProxies(T, real, proxies, rig);
const e = new T.Euler();
assert(near(e.setFromQuaternion(real.shoulderL.quaternion).z, 0), "idle shoulder bias cancels to the model's own pose");
assert(near(e.setFromQuaternion(real.shoulderR.quaternion).z, 0.5), "extra shoulder rotation passes through");
assert(near(e.setFromQuaternion(real.head.quaternion).y, 0.3) && near(real.torso.scale.x, 1.02), "head rotation and torso scale pass through");

// ---- skinModel on a synthetic mesh: moving the right shoulder bone moves right-arm vertices, not the head ----
const geo = new T.BufferGeometry();
const verts = new Float32Array([0.067, 0.103, 0, 0.068, 0.104, 0.001, 0.066, 0.102, 0, 0, 0.27, 0, 0.001, 0.27, 0, 0, 0.271, 0.001]);
geo.setAttribute("position", new T.BufferAttribute(verts, 3));
const mesh = new T.Mesh(geo, new T.MeshBasicMaterial()); const scene = new T.Group(); scene.add(mesh);
const skinned = skinModel(T, scene, rig);
assert(skinned.skinned.length === 1 && skinned.skinned[0].isSkinnedMesh && skinned.skeleton.bones.length === order.length, "skinModel builds one skinned mesh and the full skeleton");
skinned.bones.shoulderR.rotation.z = 1.0; skinned.bones.shoulderR.updateMatrixWorld(true); scene.updateMatrixWorld(true);
const v = new T.Vector3(); skinned.skinned[0].getVertexPosition?.(0, v);
const head = new T.Vector3(); skinned.skinned[0].getVertexPosition?.(3, head);
assert(v.distanceTo(new T.Vector3(0.067, 0.103, 0)) > 0.01, "right-arm vertex follows the shoulder bone", v.toArray().join());
assert(head.distanceTo(new T.Vector3(0, 0.27, 0)) < 1e-5, "head vertex ignores the shoulder bone", head.toArray().join());

// ---- proportions: live, idempotent limb/head editing ----
{
  const scene2 = new T.Group();
  // a vertex out on the right forearm (near wristR) and one on the lower-right leg (near ankleR)
  const wristR = rig.joints.wristR.pivot, ankleR = rig.joints.ankleR.pivot, headTop = [0, 0.29, 0];
  const vv = new Float32Array([...wristR, ...ankleR, ...headTop]);
  const g2 = new T.BufferGeometry(); g2.setAttribute("position", new T.BufferAttribute(vv, 3));
  const m2 = new T.Mesh(g2, new T.MeshBasicMaterial()); scene2.add(m2);
  const sk = skinModel(T, scene2, rig);
  const origWristX = sk.bones.wristR.position.x, origAnkleY = sk.bones.ankleR.position.y;

  // normalizeProportions clamps and defaults
  assert(JSON.stringify(normalizeProportions({})) === JSON.stringify(DEFAULT_PROPORTIONS), "default proportions are all 1");
  assert(normalizeProportions({ head_scale: 99 }).head_scale === 1.5, "head_scale clamps high");
  assert(normalizeProportions({ leg_length: 0 }).leg_length === 0.6, "leg_length clamps low");
  assert(normalizeProportions({ arm_length: "x" }).arm_length === 1, "non-number falls back to 1");

  // longer arms push the wrist bone further out; longer legs push the ankle bone further down
  applyProportions(sk.bones, rig, { arm_length: 1.5, leg_length: 1.5 });
  assert(Math.abs(sk.bones.wristR.position.x) > Math.abs(origWristX) * 1.4, "arm_length lengthens the forearm bone offset");
  assert(Math.abs(sk.bones.ankleR.position.y) > Math.abs(origAnkleY) * 1.4, "leg_length lengthens the shin bone offset");

  // idempotent: applying again does NOT compound (absolute from the rig, not a running multiply)
  const x1 = sk.bones.wristR.position.x;
  applyProportions(sk.bones, rig, { arm_length: 1.5, leg_length: 1.5 });
  assert(Math.abs(sk.bones.wristR.position.x - x1) < 1e-9, "re-applying the same proportions does not compound");

  // back to 1.0 restores the original offsets exactly
  applyProportions(sk.bones, rig, {});
  assert(Math.abs(sk.bones.wristR.position.x - origWristX) < 1e-9 && Math.abs(sk.bones.ankleR.position.y - origAnkleY) < 1e-9, "proportions reset to natural size");

  // head_scale rides on a base scale that survives syncProxies (it would otherwise reset to the proxy's 1)
  applyProportions(sk.bones, rig, { head_scale: 1.4 });
  assert(Math.abs(sk.bones.head.scale.x - 1.4) < 1e-9, "head_scale sets the head bone scale");
  const px = makeProxyBones(T, rig);          // proxies default to scale 1
  syncProxies(T, sk.bones, px, rig);
  assert(Math.abs(sk.bones.head.scale.x - 1.4) < 1e-9, "head base scale survives the per-frame proxy sync");
  const otherReset = sk.bones.wristR;         // a non-head bone has no baseScale -> stays 1 after sync
  assert(Math.abs(otherReset.scale.x - 1) < 1e-9, "bones without a baseScale sync to 1 as before");
}

// ---- face helpers ----
const f = faceFrame([0.4, -0.2, 0.86]);
const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
assert(near(dot(f.n, f.up), 0, 1e-6) && near(dot(f.n, f.right), 0, 1e-6) && near(dot(f.up, f.right), 0, 1e-6), "face frame is orthogonal");
assert(f.up[1] > 0.9, "face frame up stays roughly world-up", f.up.join());
assert(!mouthSize(0.01).visible && mouthSize(0.5).visible && mouthSize(1).w > mouthSize(0.2).w && mouthSize(1).h > mouthSize(0.2).h, "mouth grows with amplitude and hides when closed");

console.log(JSON.stringify({ schema: 1, passed: passes, failed: failures.length, failures }));
process.exit(failures.length ? 1 : 0);
