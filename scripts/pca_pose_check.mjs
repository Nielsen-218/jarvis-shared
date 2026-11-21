// Pose validator for the Captain America demonstration figures (app/captain.html, between DEMO-TABLES-BEGIN and
// DEMO-ENGINE-END: the tables and the drawing engine, no DOM). For EVERY key and view it samples 20 steps between each
// pair of keyframes and checks: every bone within +-15% of its rest length, no hand, foot or toe below the floor
// (y > GROUND + 0.5), every joint inside the figure's viewBox, and at least one joint moving more than 3 units over the
// cycle (held positions in HOLDS excepted: they only breathe).
//
//   node scripts/pca_pose_check.mjs            prints {"checked", "samples", "failures"}; exit code 1 on any failure
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const BONES = [["p", "s", "torso"], ["s", "n", "neck"], ["n", "h", "head"], ["sA", "eA", "upper"], ["eA", "hA", "fore"], ["sB", "eB", "upper"],
  ["eB", "hB", "fore"], ["pA", "kA", "thigh"], ["kA", "fA", "shin"], ["pB", "kB", "thigh"], ["kB", "fB", "shin"], ["fA", "tA", "foot"], ["fB", "tB", "foot"]];
const STEPS = 20;

export function engine(html) {
  const a = html.indexOf('/* DEMO-TABLES-BEGIN */'), b = html.indexOf('/* DEMO-ENGINE-END */');
  if (a < 0 || b < a) throw new Error('the demonstration engine markers are missing from captain.html');
  const ctx = { console, Math, Object, Array, Set, Number, String, JSON };
  vm.createContext(ctx);
  vm.runInContext(html.slice(a, b) + '\n;globalThis.__E = { POSES, FRONT_VIEW, PHASE_LABELS, HOLDS, BONES, GROUND, JOINTS, demoFor, between, vb };', ctx);
  return ctx.__E;
}

export function validate(html) {
  const E = engine(html), failures = [];
  let checked = 0, samples = 0;
  const views = Object.keys(E.POSES).map(k => [k, 'side']).concat(Object.keys(E.FRONT_VIEW).map(k => [k, 'front']));
  for (const [key, view] of views) {
    const d = E.demoFor(key, view), name = view === 'front' ? key + ':front' : key;
    if (!d) { failures.push(`${name}: no frames or phase labels`); continue; }
    checked++;
    const [vx, vy, vw, vh] = E.vb(key).split(/\s+/).map(Number), hold = E.HOLDS.includes(key);
    const lo = {}, hi = {}, seen = new Set();
    const bad = (msg, kind) => { if (!seen.has(kind || msg)) { seen.add(kind || msg); failures.push(`${name}: ${msg}`); } };   // one line per problem
    d.frames.forEach((A, i) => {
      const B = d.frames[(i + 1) % d.frames.length];
      for (let k = 0; k <= STEPS; k++) {
        const J = E.between(A, B, k / STEPS); samples++;
        for (const [u, v, bone] of BONES) {
          const len = Math.hypot(J[v][0] - J[u][0], J[v][1] - J[u][1]), rest = E.BONES[bone];
          if (Math.abs(len - rest) > rest * .15) bad(`${u}-${v} (${bone}) ${len.toFixed(1)} vs ${rest} near frame ${i}`, `len:${u}-${v}`);
        }
        for (const j of ['fA', 'fB', 'tA', 'tB', 'hA', 'hB'])
          if (J[j][1] > E.GROUND + .5) bad(`${j} below the floor (y ${J[j][1].toFixed(1)}) near frame ${i}`, `floor:${j}`);
        for (const j of E.JOINTS) {
          const [x, y] = J[j];
          if (x < vx || x > vx + vw || y < vy || y > vy + vh) bad(`${j} outside the viewBox (${x.toFixed(1)}, ${y.toFixed(1)}) near frame ${i}`, `box:${j}`);
          lo[j] = lo[j] ? [Math.min(lo[j][0], x), Math.min(lo[j][1], y)] : [x, y];
          hi[j] = hi[j] ? [Math.max(hi[j][0], x), Math.max(hi[j][1], y)] : [x, y];
        }
      }
    });
    const moves = Math.max(...E.JOINTS.map(j => Math.hypot(hi[j][0] - lo[j][0], hi[j][1] - lo[j][1])));
    if (!hold && moves <= 3) bad(`hardly moves (${moves.toFixed(1)} units)`);
  }
  return { checked, samples, failures };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const out = validate(fs.readFileSync(path.join(ROOT, 'app', 'captain.html'), 'utf8'));
  console.log(JSON.stringify(out, null, 1));
  process.exit(out.failures.length ? 1 : 0);
}
