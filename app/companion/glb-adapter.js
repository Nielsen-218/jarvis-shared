// GLB model adapter for the Living Desktop Companion (adapter_id "glb-model-v1").
//
// Loads a glTF 2.0 binary (profile.model_url), normalizes it to the companion's
// metric frame (feet on y=0, centred on x/z, TARGET_HEIGHT_M tall), maps its
// skeleton onto the renderer's SEMANTIC_BONES by name, and exposes the same
// adapter contract:
//     { group, bones, materials, dispose, capabilities, ready }
// `ready` is a Promise: loading is async, so `group` is returned at once as an
// empty mount and `bones`/`materials` are filled IN PLACE when it resolves.
// If it rejects, the page shows the 2D concept sprite instead.
//
// Top level stays Node-safe (no three imports) so the pure helpers below can be
// unit-tested under tests/node/test_glb_adapter.mjs; the loader is imported
// lazily inside ready().

export const ADAPTER_ID = "glb-model-v1";
export const TARGET_HEIGHT_M = 0.30;   // design spec: 0.30 m total height

/** "LeftArm" / "arm_l" / "mixamorig:RightHand" / "hand.R" -> "L" | "R" | null */
export function sideOf(name) {
  const n = String(name || "").toLowerCase();
  if (/(^|[^a-z])(left|l)([^a-z]|$)|^l[_.-]|[_.-]l$|left/.test(n)) return "L";
  if (/(^|[^a-z])(right|r)([^a-z]|$)|^r[_.-]|[_.-]r$|right/.test(n)) return "R";
  return null;
}

// Ordered: the first rule whose regex matches a (lower-cased, prefix-stripped)
// bone name wins. Specific parts come before the generic limb words so
// "forearm" never lands on "arm" and "lowerleg" never lands on "leg".
const BONE_RULES = [
  ["fingers", /(index|middle|ring(?!s)|pinky|thumb|finger)/, true],
  ["wrist",   /(hand|wrist)/, true],
  ["elbow",   /(forearm|fore_arm|lowerarm|lower_arm|elbow)/, true],
  ["shoulder",/(upperarm|upper_arm|shoulder|clavicle|\barm\b|arm$)/, true],
  ["ankle",   /(foot|ankle|toe)/, true],
  ["hip",     /(thigh|upperleg|upper_leg|upleg|\bhip\b)/, true],
  ["knee",    /(shin|calf|lowerleg|lower_leg|knee|\bleg\b|leg$)/, true],   // Mixamo: UpLeg = thigh, Leg = shin
  ["ear",     /\bear\b|ear[_.-]|[_.-]ear|antenna|fin/, true],
  ["head",    /head/, false],
  ["neck",    /neck/, false],
  ["chestCore", /(core|reactor)/, false],
  ["torso",   /(spine|chest|torso|body)/, false],
  ["root",    /(^root$|hips|pelvis|armature)/, false],
];

/** Pure: skeleton bone names -> { bones: {semantic: name}, unmapped: [names] }.
 *  First match per semantic slot wins (skeleton order = parent-first), so the
 *  topmost spine bone becomes "torso" and the first finger bone "fingersL". */
export function mapBoneNames(names) {
  const bones = {}, unmapped = [];
  for (const raw of names || []) {
    const n = String(raw).replace(/^mixamorig[:_]?/i, "").toLowerCase();
    let hit = null;
    for (const [semantic, rx, sided] of BONE_RULES) {
      if (!rx.test(n)) continue;
      if (sided) {
        const s = sideOf(n);
        if (!s) continue;
        hit = semantic + s;
      } else hit = semantic;
      break;
    }
    if (hit && !(hit in bones)) bones[hit] = raw; else unmapped.push(raw);
  }
  return { bones, unmapped };
}

/** Pure: world-space bounds -> { scale, offset } that make the model
 *  `targetHeight * overallScale` tall, feet on y=0, centred on x/z. */
export function computeNormalization(bounds, targetHeight = TARGET_HEIGHT_M, overallScale = 1) {
  const h = (bounds.max.y - bounds.min.y) || 1;
  const scale = (targetHeight * (overallScale || 1)) / h;
  const cx = (bounds.min.x + bounds.max.x) / 2, cz = (bounds.min.z + bounds.max.z) / 2;
  return { scale, offset: { x: -cx * scale, y: -bounds.min.y * scale, z: -cz * scale } };
}

/** Pure: a mesh/material name -> profile material slot (see materials.js). */
export function inferMaterialSlot(name) {
  const n = String(name || "").toLowerCase();
  if (/eye|iris|pupil/.test(n)) return "eye_glow";
  if (/mouth|jaw|lip|teeth/.test(n)) return "mouth_glow";
  if (/chest|core|reactor|arc/.test(n)) return "chest_core";
  if (/face|screen|visor|display/.test(n)) return "face_screen";
  if (/foot|sole|boot|thruster/.test(n)) return "foot_glow";
  if (/ear|antenna|fin|ring|halo/.test(n)) return "accent_secondary";
  if (/glow|emiss|light|led|accent|neon|energy|claw/.test(n)) return "accent_primary";
  if (/joint|dark|graphite|rubber|cable|hinge|inner/.test(n)) return "joint_dark";
  if (/shell|armor|armour|plate|panel|metal|white|silver/.test(n)) return "shell_light";
  return "shell_light";
}

const SLOT_KEY = {
  shell_light: "shellLight", shell_dark: "shellDark", face_screen: "faceScreen",
  eye_glow: "eyeGlow", mouth_glow: "mouthGlow", accent_primary: "accentPrimary",
  accent_secondary: "accentSecondary", chest_core: "chestCore", joint_dark: "jointDark",
  foot_glow: "footGlow",
};
const GLOW_SLOTS = new Set(["eye_glow", "mouth_glow", "accent_primary", "accent_secondary", "chest_core", "foot_glow"]);
const hexToInt = (h, d) => { const m = String(h || "").match(/^#?([0-9a-f]{6})$/i); return m ? parseInt(m[1], 16) : d; };

/** Build the adapter. `T` is the three.js namespace; `profile.model_url` is a
 *  same-origin path (validated server-side in core/companion_profile.py). */
export function buildGlbAdapter(T, profile = {}) {
  const group = new T.Group(); group.name = "glb-root";
  const bones = { root: group };
  const materials = {};
  const capabilities = {
    adapter_id: ADAPTER_ID, model_url: profile.model_url || null, loaded: false,
    has_face_screen: false, has_mouth_morph: false, supports_live_retint: true, has_voice_bridge: true,
    bones_present: ["root"], unmapped_bones: 0, triangle_count: 0, mesh_count: 0,
    note: "glTF model adapter; bones/materials discovered at load time by name.",
  };
  const adapter = { group, bones, materials, capabilities, loaded: false, error: null, face: null, sync: null };
  adapter.dispose = () => {
    group.traverse((o) => {
      if (o.geometry) o.geometry.dispose?.();
      const ms = Array.isArray(o.material) ? o.material : (o.material ? [o.material] : []);
      for (const m of ms) { for (const k of ["map", "normalMap", "emissiveMap", "roughnessMap", "metalnessMap"]) m[k]?.dispose?.(); m.dispose?.(); }
    });
    group.clear();
  };

  adapter.ready = (async () => {
    const url = profile.model_url;
    if (!url) throw new Error("glb-model-v1 needs profile.model_url");
    const { GLTFLoader } = await import("three/addons/loaders/GLTFLoader.js");
    const gltf = await new Promise((ok, fail) => new GLTFLoader().load(url, ok, undefined, (e) => fail(new Error(`GLB load failed: ${e?.message || e}`))));
    const model = gltf.scene || gltf.scenes?.[0];
    if (!model) throw new Error("GLB has no scene");

    // Optional rig (profile.rig_url): skin the static mesh with a runtime skeleton and add the face overlays.
    let rig = null, skin = null, skinModel, makeProxyBones, syncProxies, applyProportions, buildGlbFace, buildAccessory;
    if (profile.rig_url) {
      const res = await fetch(profile.rig_url);
      if (!res.ok) throw new Error(`rig load failed: HTTP ${res.status}`);
      rig = await res.json();
      ({ skinModel, makeProxyBones, syncProxies, applyProportions } = await import("/companion/robot-skin.js"));
      ({ buildGlbFace } = await import("/companion/glb-face.js"));
      ({ buildAccessory } = await import("/companion/accessories.js"));
      skin = skinModel(T, model, rig);
    }

    // Normalize into the companion frame. Skinned meshes are measured in their
    // bind pose; the wrapper carries the transform so the skeleton stays untouched.
    model.updateMatrixWorld(true);
    const box = new T.Box3().setFromObject(model);
    const norm = computeNormalization({ min: box.min, max: box.max }, TARGET_HEIGHT_M, profile.proportions?.overall_scale ?? 1);
    const wrap = new T.Group(); wrap.name = "glb-normalized";
    wrap.scale.setScalar(norm.scale);
    wrap.position.set(norm.offset.x, norm.offset.y, norm.offset.z);
    if (Number.isFinite(profile.model_rotation_y)) wrap.rotation.y = profile.model_rotation_y * Math.PI / 180;
    wrap.add(model); group.add(wrap);

    // Bones: real skeleton bones first (parent-first order), then named empties.
    const names = [], byName = new Map();
    model.traverse((o) => { if (o.isBone || o.isGroup || o.isObject3D) { if (o.name && !byName.has(o.name)) { byName.set(o.name, o); if (o.isBone) names.push(o.name); } } });
    if (!names.length) model.traverse((o) => { if (o.name && !o.isMesh) names.push(o.name); });
    if (rig) {
      // Rigged: the clips drive proxy bones named like SEMANTIC_BONES; sync() composes them onto the real skeleton.
      const proxies = makeProxyBones(T, rig);
      Object.assign(bones, proxies);
      const face = buildGlbFace(T, rig.face || {}, skin.bones.head, rig.joints.head.pivot);
      adapter.face = face;
      adapter.rig = { spec: rig, real: skin.bones, proxies };
      adapter.sync = () => syncProxies(T, skin.bones, proxies, rig);
      const { buildJetpack } = await import("/companion/jetpack.js");
      const jetpack = buildJetpack(T);
      skin.bones.torso.add(jetpack.group);
      adapter.updateJetpack = jetpack.update;
      capabilities.has_jetpack = true;
      capabilities.has_skeleton = true; capabilities.has_face_overlays = true; capabilities.has_mouth_morph = true;
      // Accessories: a procedural hat/glasses/etc. parented to the head bone (Feature #27).
      adapter.setAccessory = (kind) => {
        if (adapter._accessory) { skin.bones.head.remove(adapter._accessory); adapter._accessory.traverse((o) => { o.geometry?.dispose?.(); o.material?.dispose?.(); }); adapter._accessory = null; }
        const acc = buildAccessory(T, kind, rig.joints.head.pivot);
        if (acc) { skin.bones.head.add(acc); adapter._accessory = acc; }
        capabilities.accessory = kind && kind !== "none" ? kind : null;
      };
      if (profile.accessory) adapter.setAccessory(profile.accessory);
      // Live proportion editing (Phase F — geometry): head size + limb/spine lengths, non-destructive.
      adapter.setProportions = (prop) => applyProportions(skin.bones, rig, prop);
      capabilities.supports_proportions = true;
      if (profile.proportions) adapter.setProportions(profile.proportions);
      const prevDispose = adapter.dispose; adapter.dispose = () => { try { face.dispose(); } catch {} try { adapter.setAccessory("none"); } catch {} prevDispose(); };
    } else {
      const mapped = mapBoneNames(names);
      for (const [semantic, name] of Object.entries(mapped.bones)) bones[semantic] = byName.get(name);
      capabilities.unmapped_bones = mapped.unmapped.length;
      if (gltf.animations?.length) {
        const { attachNativeAnimations } = await import("/companion/native-animation.js");
        attachNativeAnimations(T, adapter, model, gltf.animations);
      }
    }

    // Materials: tag by slot, apply the profile palette to glow slots, expose
    // one material per slot so retintMaterials() works on the GLB too.
    let tris = 0, meshes = 0, morph = null;
    const palette = profile.materials || {};
    model.traverse((o) => {
      if (!o.isMesh) return;
      meshes += 1;
      const g = o.geometry; if (g) tris += (g.index ? g.index.count : g.getAttribute("position")?.count || 0) / 3;
      o.castShadow = false; o.receiveShadow = false; o.frustumCulled = false;
      const dict = o.morphTargetDictionary || {};
      for (const k of Object.keys(dict)) if (!morph && /(jaw|mouth|viseme|aa|open)/i.test(k)) morph = { mesh: o, name: k, index: dict[k] };
      const ms = Array.isArray(o.material) ? o.material : [o.material];
      for (const m of ms) {
        if (!m) continue;
        const slot = inferMaterialSlot(m.name || o.name);
        m.userData.slot = slot;
        const spec = palette[slot] || {};
        if (GLOW_SLOTS.has(slot) && "emissive" in m) {
          const c = hexToInt(spec.color, m.color?.getHex?.() ?? 0x3df0ff);
          m.emissive.setHex(c); m.emissiveIntensity = spec.emissive ?? Math.max(m.emissiveIntensity || 0, 1.2);
        } else if (typeof spec.color === "string" && m.color && !m.map) m.color.setHex(hexToInt(spec.color, m.color.getHex()));   // a baked texture keeps its own colours
        if (slot === "face_screen") capabilities.has_face_screen = true;
        const key = SLOT_KEY[slot]; if (key && !materials[key]) materials[key] = m;
      }
    });
    if (morph) { adapter.morphs = { mouthOpen: morph }; capabilities.has_mouth_morph = true; }

    Object.assign(capabilities, { loaded: true, triangle_count: Math.round(tris), mesh_count: meshes, bones_present: Object.keys(bones).sort() });
    adapter.loaded = true;
    return adapter;
  })().catch((e) => { adapter.error = String(e?.message || e); throw e; });
  adapter.ready.catch(() => {});   // the sync buildAdapter() path may never await it; keep the rejection handled

  return adapter;
}
