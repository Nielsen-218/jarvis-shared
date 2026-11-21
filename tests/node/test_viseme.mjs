// Node harness for the viseme lip-sync (app/companion/viseme.js).
import { VISEME_SHAPES, letterViseme, textToVisemes, visemeAt } from "../../app/companion/viseme.js";
let failures = [], passes = 0;
const assert = (c, name) => { if (c) passes += 1; else failures.push(name); };

assert(letterViseme("a") === "open", "a is open");
assert(letterViseme("e") === "wide" && letterViseme("i") === "wide", "e/i are wide");
assert(letterViseme("o") === "round" && letterViseme("u") === "round", "o/u are round");
assert(letterViseme("m") === "closed" && letterViseme("p") === "closed", "m/p are closed");
assert(letterViseme("f") === "teeth", "f is teeth");
assert(letterViseme(" ") === "rest", "space is rest");

const tl = textToVisemes("Hello sir");
assert(tl.seq.length === 9 && tl.total > 0, "timeline covers every character");
assert(tl.seq.every((s, i) => i === 0 || s.t0 === tl.seq[i - 1].t1), "segments are contiguous");
const shapes = new Set(tl.seq.map((s) => s.v));
assert(shapes.size >= 3, "a phrase produces several distinct visemes");          // not just one shape
assert(visemeAt(tl, -5) === "rest" && visemeAt(tl, tl.total + 100) === "rest", "out of range -> rest");
assert(visemeAt(tl, 1) === tl.seq[0].v, "visemeAt reads the first segment");
for (const v of Object.values(VISEME_SHAPES)) assert(v.w > 0 && v.h >= 0, "shape has sane multipliers");
assert(VISEME_SHAPES.wide.w > VISEME_SHAPES.round.w && VISEME_SHAPES.round.h > VISEME_SHAPES.closed.h, "wide is wider, round is taller than closed");

console.log(JSON.stringify({ schema: 1, passed: passes, failed: failures.length, failures }));
process.exit(failures.length ? 1 : 0);
