// Skinning for the textured concept robot (rig data: rigs/robot-rig.json, built by scripts/build_companion_rig.py).
//
// The Hunyuan3D model is one static mesh. This turns it into a skinned character at load time:
//   * computeSkinWeights  - pure; per-vertex bone indices/weights from the rig's rules + capsule influences
//   * buildRobotSkeleton  - real three.js Bones with identity rest rotations (pivots only), parent-first
//   * makeProxyBones      - plain Object3Ds named like the SEMANTIC_BONES; the unchanged clip registry in
//                           animation.js drives these, and syncProxies() composes them onto the real bones
//
// Why proxies: animation.js assigns ABSOLUTE rotations written for arms that hang straight down (idle sets
// shoulderL.rotation.z = -0.25). The textured robot's arms already stand in an A-pose, so the real bone gets
// (proxy rotation) - (clip bias) and the model keeps its authored rest pose.
//
// Top level stays Node-safe: three is passed in (`T`) wherever it is needed.

const clamp01 = (x) => Math.max(0, Math.min(1, x));
const smooth = (lo, hi, x) => { const t = clamp01((x - lo) / (hi - lo || 1e-9)); return t * t * (3 - 2 * t); };

/** Distance from point (px,py,pz) to segment a-b. */
export function distToSegment(px, py, pz, a, b) {
  const abx = b[0] - a[0], aby = b[1] - a[1], abz = b[2] - a[2];
  const apx = px - a[0], apy = py - a[1], apz = pz - a[2];
  const len2 = abx * abx + aby * aby + abz * abz;
  const t = len2 > 0 ? clamp01((apx * abx + apy * aby + apz * abz) / len2) : 0;
  const dx = apx - abx * t, dy = apy - aby * t, dz = apz - abz * t;
  return Math.sqrt(dx * dx + dy * dy + dz * dz);
}

/** Bones in parent-first order, plus name -> index. */
export function boneOrder(rig) {
  const names = Object.keys(rig.joints);
  const seen = new Set(), order = [];
  const visit = (n) => {
    if (seen.has(n)) return;
    const p = rig.joints[n].parent; if (p) visit(p);
    seen.add(n); order.push(n);
  };
  names.forEach(visit);
  return { order, index: Object.fromEntries(order.map((n, i) => [n, i])) };
}

/** Pure. positions: Float32Array xyz in the rig's frame. Returns 4 influences per vertex.
 *  Rules first (head / neck / ear fins by position), capsule falloff for the body. */
export function computeSkinWeights(positions, rig) {
  const { order, index } = boneOrder(rig);
  const n = positions.length / 3;
  const skinIndex = new Uint16Array(n * 4), skinWeight = new Float32Array(n * 4);
  const R = rig.head_rules, segs = rig.segments;
  const K = 4;                                        // falloff sharpness: exp(-K (d/r)^2)
  const head = index.head, earL = index.earL, earR = index.earR;
  const acc = new Float64Array(order.length);
  for (let v = 0; v < n; v++) {
    const x = positions[v * 3], y = positions[v * 3 + 1], z = positions[v * 3 + 2], ax = Math.abs(x);
    // body: capsule influences
    acc.fill(0); let sum = 0;
    for (const s of segs) {
      const d = distToSegment(x, y, z, s.a, s.b) / s.r;
      const w = Math.exp(-K * d * d);
      acc[index[s.bone]] += w; sum += w;
    }
    if (sum < 1e-300) {                               // far from every capsule: nearest one wins outright
      let best = 0, bd = Infinity;
      for (const s of segs) { const d = distToSegment(x, y, z, s.a, s.b) / s.r; if (d < bd) { bd = d; best = index[s.bone]; } }
      acc[best] = 1; sum = 1;
    }
    for (let i = 0; i < acc.length; i++) acc[i] /= sum;
    // head over the neck, ear fins over the head
    const tHead = smooth(R.neck_y[0], R.neck_y[1], y) * (1 - smooth(R.neck_max_abs_x[0], R.neck_max_abs_x[1], ax));
    const tEar = smooth(R.ear_abs_x[0], R.ear_abs_x[1], ax) * smooth(R.ear_y[0], R.ear_y[1], y);
    const earIdx = x < 0 ? earL : earR;
    for (let i = 0; i < acc.length; i++) acc[i] *= (1 - tHead) * (1 - tEar);
    acc[head] += tHead * (1 - tEar);
    acc[earIdx] += tEar;
    // keep the 4 strongest, renormalised
    const top = [];
    for (let i = 0; i < acc.length; i++) if (acc[i] > 1e-4) top.push([i, acc[i]]);
    top.sort((p, q) => q[1] - p[1]);
    const keep = top.slice(0, 4); let ks = 0; for (const k of keep) ks += k[1];
    for (let k = 0; k < 4; k++) {
      if (k < keep.length) { skinIndex[v * 4 + k] = keep[k][0]; skinWeight[v * 4 + k] = keep[k][1] / ks; }
    }
  }
  return { skinIndex, skinWeight, order };
}

/** Real bones, parent-first, local positions = pivot - parent pivot, identity rotation. */
export function buildRobotSkeleton(T, rig) {
  const { order } = boneOrder(rig);
  const bones = {};
  for (const name of order) {
    const j = rig.joints[name];
    const b = new T.Bone(); b.name = name;
    const pp = j.parent ? rig.joints[j.parent].pivot : [0, 0, 0];
    b.position.set(j.pivot[0] - pp[0], j.pivot[1] - pp[1], j.pivot[2] - pp[2]);
    if (j.parent) bones[j.parent].add(b);
    bones[name] = b;
  }
  const list = order.map((n) => bones[n]);
  return { bones, list, root: bones[order[0]] };
}

/** Plain Object3Ds with the semantic names the clip registry drives. `root` is NOT proxied (the clips move the
 *  adapter's group directly). */
export function makeProxyBones(T, rig) {
  const proxies = {};
  for (const name of Object.keys(rig.joints)) {
    if (name === "pelvis") continue;
    const p = new T.Object3D(); p.name = `proxy:${name}`; proxies[name] = p;
  }
  return proxies;
}

/** Compose the proxies (driven by animation clips) onto the real bones. Call once per frame after the animator. */
export function syncProxies(T, real, proxies, rig, scratch = {}) {
  const q = scratch.q || (scratch.q = new T.Quaternion()), qb = scratch.qb || (scratch.qb = new T.Quaternion());
  const e = scratch.e || (scratch.e = new T.Euler());
  for (const name in proxies) {
    const rb = real[name], p = proxies[name];
    if (!rb) continue;
    q.setFromEuler(p.rotation);
    const bias = rig.clip_bias && rig.clip_bias[name];
    if (bias) { e.set(bias[0], bias[1], bias[2]); qb.setFromEuler(e).invert(); q.multiply(qb); }
    rb.quaternion.copy(q);
    rb.scale.copy(p.scale);
    const bs = rb.userData && rb.userData.baseScale;     // proportion edit (head size) survives the per-frame sync
    if (bs && bs !== 1) rb.scale.multiplyScalar(bs);
  }
}

/** Default proportions: everything at natural size. */
export const DEFAULT_PROPORTIONS = { head_scale: 1, body_scale: 1, arm_length: 1, leg_length: 1 };

/** Clamp proportions to a sane range so the model can't be turned inside-out. */
export function normalizeProportions(prop = {}) {
  const c = (v, d) => { const n = Number(v); return Number.isFinite(n) ? Math.max(0.6, Math.min(1.5, n)) : d; };
  return {
    head_scale: c(prop.head_scale, 1), body_scale: c(prop.body_scale, 1),
    arm_length: c(prop.arm_length, 1), leg_length: c(prop.leg_length, 1),
  };
}

/**
 * Live, non-destructive proportion editing. Call AFTER skinModel() has bound the mesh (so moving a
 * bone deforms the rest shape relative to the original bind). Idempotent — each bone is set to its
 * ORIGINAL rig offset times the factor, so repeated calls from the Studio don't compound.
 *  - arm_length / leg_length scale the limb bone offsets (bone POSITIONS survive syncProxies).
 *  - body_scale scales the spine (torso/neck) offsets.
 *  - head_scale rides on bone SCALE via userData.baseScale (syncProxies re-applies it each frame).
 * The face overlays + accessories are children of the head bone, so they scale with the head.
 */
export function applyProportions(bones, rig, prop = {}) {
  const p = normalizeProportions(prop);
  const offset = (name) => {
    const j = rig.joints[name]; if (!j) return null;
    const pp = j.parent ? rig.joints[j.parent].pivot : [0, 0, 0];
    return [j.pivot[0] - pp[0], j.pivot[1] - pp[1], j.pivot[2] - pp[2]];
  };
  const setLen = (name, f) => { const b = bones[name], o = offset(name); if (b && o) b.position.set(o[0] * f, o[1] * f, o[2] * f); };
  for (const s of ["L", "R"]) {
    for (const n of ["elbow", "wrist", "fingers"]) setLen(n + s, p.arm_length);
    for (const n of ["knee", "ankle"]) setLen(n + s, p.leg_length);
  }
  for (const n of ["torso", "neck"]) setLen(n, p.body_scale);
  const head = bones.head;
  if (head) { head.userData = head.userData || {}; head.userData.baseScale = p.head_scale; head.scale.setScalar(p.head_scale); }
  return p;
}

/** Turn every plain Mesh under `model` into a SkinnedMesh bound to one shared skeleton.
 *  The geometry is baked into the rig frame first (mesh node transforms applied). */
export function skinModel(T, model, rig) {
  model.updateMatrixWorld(true);
  const skel = buildRobotSkeleton(T, rig);
  skel.root.updateMatrixWorld(true);                     // bone inverses are taken from the rest-pose world matrices
  const skeleton = new T.Skeleton(skel.list);
  const meshes = [];
  model.traverse((o) => { if (o.isMesh && !o.isSkinnedMesh) meshes.push(o); });
  const skinned = [];
  const toModel = new T.Matrix4().copy(model.matrixWorld).invert();
  for (const mesh of meshes) {
    const g = mesh.geometry.clone();
    g.applyMatrix4(new T.Matrix4().multiplyMatrices(toModel, mesh.matrixWorld));
    const pos = g.getAttribute("position");
    const { skinIndex, skinWeight } = computeSkinWeights(pos.array instanceof Float32Array ? pos.array : Float32Array.from(pos.array), rig);
    g.setAttribute("skinIndex", new T.Uint16BufferAttribute(skinIndex, 4));
    g.setAttribute("skinWeight", new T.Float32BufferAttribute(skinWeight, 4));
    const sm = new T.SkinnedMesh(g, mesh.material);
    sm.name = mesh.name || "robot"; sm.frustumCulled = false;
    if (!skinned.length) sm.add(skel.root);              // the skeleton lives under the first mesh (bind matrix = identity)
    sm.bind(skeleton, new T.Matrix4());
    skinned.push(sm);
    mesh.parent && mesh.parent.remove(mesh);
    model.add(sm);
  }
  model.updateMatrixWorld(true);
  return { skinned, skeleton, bones: skel.bones, root: skel.root };
}
