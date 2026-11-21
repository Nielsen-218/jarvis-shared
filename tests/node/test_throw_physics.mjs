// Node harness for the pure throw physics (app/companion/throw-physics.js).
//   node tests/node/test_throw_physics.mjs
import { DEFAULT_PARAMS, launchState, stepThrow, apexHeight, releaseVelocity, floorAt } from "../../app/companion/throw-physics.js";

let failures = [], passes = 0;
const assert = (c, name, detail = "") => { if (c) passes += 1; else failures.push({ name, detail }); };
const near = (a, b, e = 1e-6) => Math.abs(a - b) <= e;

// window 87px, work area 1920x1032 (taskbar), so top-left bounds subtract the size.
const B = { left: 0, top: 0, right: 1920 - 87, bottom: 1032 - 87 };

// --- gravity accelerates downward ---
let s = launchState(500, 200, 0, 0);
const s1 = stepThrow(s, 1 / 60, B);
assert(s1.vy > 0 && s1.y > 200, "gravity pulls down", `vy=${s1.vy} y=${s1.y}`);
assert(s1.x === 500, "no horizontal drift without vx");

// --- launch spin follows horizontal velocity ---
assert(launchState(0, 0, 1000, 0).spin < 0, "rightward throw tumbles forward (negative z-spin)");
assert(launchState(0, 0, -1000, 0).spin > 0, "leftward throw tumbles the other way");

// --- a full throw lands, bounces with decreasing energy, and comes to rest in-bounds ---
function simulate(x, y, vx, vy, maxSteps = 4000) {
  let st = launchState(x, y, vx, vy);
  const apexes = [];
  let prevVy = st.vy;
  for (let i = 0; i < maxSteps && !st.resting; i++) {
    const before = st;
    st = stepThrow(st, 1 / 120, B);
    // record an apex each time vy crosses from negative to positive (top of an arc)
    if (before.vy < 0 && st.vy >= 0) apexes.push(before.y);
    prevVy = st.vy;
  }
  return { st, apexes };
}
const { st, apexes } = simulate(100, 300, 1400, -1200);
assert(st.resting, "the throw eventually comes to rest", JSON.stringify(st));
assert(st.y === B.bottom, "it rests on the floor", `y=${st.y} floor=${B.bottom}`);
assert(st.x >= B.left && st.x <= B.right, "it rests within the horizontal bounds", `x=${st.x}`);
assert(near(st.vx, 0) && near(st.vy, 0) && near(st.spin, 0), "velocities zeroed at rest");
if (apexes.length >= 2) {
  // later apexes are lower on screen (larger y) => each bounce loses height
  assert(apexes[1] > apexes[0], "each bounce reaches a lower peak", JSON.stringify(apexes.slice(0, 3)));
}

// --- walls reflect and keep him on screen ---
let w = launchState(B.right - 2, 400, 3000, -200);   // hurled hard into the right wall
for (let i = 0; i < 20; i++) w = stepThrow(w, 1 / 120, B);
assert(w.x <= B.right && w.x >= B.left, "a hard throw into a wall stays on screen", `x=${w.x}`);

// --- clamps a giant frame (no tunnelling through the floor) ---
let g = stepThrow(launchState(500, B.bottom - 5, 0, 5000), 5.0, B);
assert(g.y <= B.bottom, "a huge dt does not tunnel through the floor", `y=${g.y}`);

// --- apexHeight + releaseVelocity helpers ---
assert(apexHeight(1000, 0) === 1000, "apex of a falling object is its current height");
assert(apexHeight(1000, -600) < 1000, "apex of a rising object is above it");
const rv = releaseVelocity([{ t: 0, x: 0, y: 0 }, { t: 50, x: 50, y: -30 }, { t: 100, x: 120, y: -80 }]);
assert(rv.speed > 0 && rv.vx > 0 && rv.vy < 0, "release velocity points up-right for an up-right flick", JSON.stringify(rv));
assert(releaseVelocity([{ t: 0, x: 0, y: 0 }]).speed === 0, "a single sample has no velocity");

// --- floorAt + throws between monitors ---
assert(floorAt(500, B) === B.bottom, "floorAt falls back to bounds.bottom without floors");
const TWO = { left: 0, top: 0, right: 3840 - 87, bottom: 1032 - 87,
  floors: [{ x0: 0, x1: 1920 - 87, y: 1032 - 87 }, { x0: 1920, x1: 3840 - 87, y: 600 - 87 }] };  // monitor 2 has a higher floor
assert(floorAt(100, TWO) === 1032 - 87, "floorAt picks monitor 0's floor on the left");
assert(floorAt(2500, TWO) === 600 - 87, "floorAt picks monitor 1's floor on the right");
{
  // hurled hard to the right from monitor 0 -> crosses onto monitor 1 and rests on ITS floor
  let st = launchState(200, 400, 3200, -300);
  for (let i = 0; i < 4000 && !st.resting; i++) st = stepThrow(st, 1 / 120, TWO);
  assert(st.resting && st.x > 1920, "a hard throw crosses to the second monitor", `x=${st.x}`);
  assert(st.y === 600 - 87, "and lands on the second monitor's floor", `y=${st.y}`);
}

console.log(JSON.stringify({ schema: 1, passed: passes, failed: failures.length, failures }));
process.exit(failures.length ? 1 : 0);
