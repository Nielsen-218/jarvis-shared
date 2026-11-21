// Node harness for the accessory factory (app/companion/accessories.js) against vendored three.
import * as T from "../../app/vendor/three/three.module.min.js";
import { ACCESSORIES, buildAccessory } from "../../app/companion/accessories.js";

let failures = [], passes = 0;
const assert = (c, name) => { if (c) passes += 1; else failures.push(name); };

assert(ACCESSORIES.includes("none") && ACCESSORIES.includes("tophat") && ACCESSORIES.length >= 6, "accessory list");
assert(buildAccessory(T, "none") === null, "none -> null");
assert(buildAccessory(T, "bogus") === null, "unknown -> null");
for (const kind of ACCESSORIES.filter((k) => k !== "none")) {
  const g = buildAccessory(T, kind, [0, 0.158, 0]);
  let meshes = 0; g.traverse((o) => { if (o.isMesh) meshes += 1; });
  assert(g && g.isGroup && meshes >= 1, kind + " builds a group with geometry");
  assert(g.name === "accessory:" + kind, kind + " is named");
}
// hats sit above the head pivot; the bowtie sits below (neck/chest)
assert(buildAccessory(T, "tophat").position.y > 0, "tophat rides above the head bone");
assert(buildAccessory(T, "bowtie").position.y < 0, "bowtie sits at the neck/chest");

// Jetpack uses real vendored Three geometry; flames stop immediately on landing/reduced motion.
const { buildJetpack, exhaustState } = await import("../../app/companion/jetpack.js");
const { CLIPS, buildAnimator } = await import("../../app/companion/animation.js");
const jet = buildJetpack(T);
assert(new T.Box3().setFromObject(jet.group).min.z < 0, "pack sits behind the torso");
const plumes = jet.group.children.filter(o => o.isGroup);
assert(plumes.length === 2 && plumes.every(o => !o.visible), "two dormant thrusters");
jet.update(1000, true, false);
assert(plumes.every(o => o.visible && o.scale.y > 0), "flight ignites both plumes");
jet.update(1100, true, true);
assert(plumes.every(o => !o.visible), "reduced motion suppresses exhaust");
jet.update(1200, false, false);
assert(plumes.every(o => !o.visible), "landing extinguishes exhaust");
for (let now = 0; now < 5000; now += 17) {
  const e = exhaustState(now, true);
  assert(e.length >= .7 && e.length <= 1 && e.width >= .84 && e.width <= 1, "bounded exhaust pulse");
}
const bones = Object.fromEntries(["root", "torso", "head", "shoulderL", "shoulderR", "elbowR", "hipL", "hipR"].map(n => [n, new T.Group()]));
for (const action of ["salute", "double_take", "shuffle"]) {
  const animator = buildAnimator(bones);
  animator.play("idle", 1500, action, 1000);
  assert(Object.values(bones).every(b => [...b.rotation.toArray().slice(0, 3)].every(Number.isFinite)), action + " has finite poses");
  const plain = buildAnimator(bones, { reducedMotion: true });
  plain.play("idle", 1500, action, 1000);
  assert(bones.hipL.rotation.x === 0 && bones.shoulderR.rotation.x === 0, action + " suppressed with reduced motion");
  assert(CLIPS[action].duration > 1.2, action + " gets time to complete");
}

// Exercise the actual native-window glide with deterministic frames, including cancellation.
const { readFileSync } = await import("node:fs");
const page = readFileSync(new URL("../../app/companion.html", import.meta.url), "utf8");
const glideCode = page.slice(page.indexOf("async function glide("), page.indexOf("// ---------------- Relocation"));
let clock = 0, moves = [];
const glide = new Function("window", "performance", "requestAnimationFrame", glideCode + "; return glide;")(
  { screenX: 0, screenY: 0, pywebview: { api: { move_companion_fast: async (x,y) => moves.push([x,y]) } } },
  { now: () => clock }, cb => { clock += 100; cb(clock); });
assert(await glide(100, 50, 250, 0, () => false) === false && moves.length === 0, "cancelled flight never moves the window");
assert(await glide(100, 50, 250) === true && moves.at(-1)[0] === 100 && moves.at(-1)[1] === 50, "flight arrives exactly at target");

console.log(JSON.stringify({ schema: 1, passed: passes, failed: failures.length, failures }));
process.exit(failures.length ? 1 : 0);
