// Node harness for the Living Presence interaction model (app/companion/interaction.js) and the activity policy
// (behavior.js activityBias).   node tests/node/test_interaction.mjs
import { GESTURE, Gesture, HoverIntent, Interaction, OWNING, SETTLE_MS, STATES, landingKind, dropReaction, travelPlan,
  HITMASK, maskGrid, coverCells, hitCells, cellColors, maskBits, maskColors } from "../../app/companion/interaction.js";
import { IDLE, activityBias, DEFAULTS, safeSettings } from "../../app/companion/behavior.js";
import { SIZES } from "../../app/companion/menu.js";

let failures = [], passes = 0;
const assert = (c, name, detail = "") => { if (c) passes += 1; else failures.push({ name, detail }); };

// ---------------- Gesture: one outcome per press ----------------
{
  const g = new Gesture();
  g.down(100, 100, 0);
  assert(g.up(100, 100, 90).kind === "click", "a quick stationary press is a click");
  g.down(100, 100, 0);
  assert(g.tick(450) === null, "no hold yet at 450 ms");
  assert(g.up(101, 100, 450).kind === "click", "a slow, deliberate click (450 ms) still opens the menu — not lost like at 250 ms");
  g.down(100, 100, 0);
  assert(g.move(103, 101, 50) === null, "a 3 px wobble is still a press, not a drag");
  assert(g.up(103, 101, 120).kind === "click", "...and releasing it is a click");
}
{
  const g = new Gesture();
  g.down(0, 0, 0);
  assert(g.tick(GESTURE.HOLD_MS - 1) === null, "not held before HOLD_MS");
  assert(g.tick(GESTURE.HOLD_MS) === "hold", "held still for HOLD_MS: picked up");
  assert(g.tick(GESTURE.HOLD_MS + 100) === null, "the hold fires once");
  assert(g.up(0, 0, 900).kind === "set-down", "releasing a held, unmoved companion sets him down (no menu)");
}
{
  const g = new Gesture();
  g.down(0, 0, 0);
  assert(g.move(10, 0, 40) === "drag-start", "past the slop: the drag starts");
  assert(g.move(20, 0, 80) === "drag", "then it's a drag");
  g.move(30, 0, 300); g.move(40, 0, 600);
  const r = g.up(42, 0, 900);
  assert(r.kind === "place", "a slow drag places him", JSON.stringify(r));
  assert(g.tick(2000) === null && !g.active, "nothing pending after release");
}
{
  const g = new Gesture();
  g.down(0, 0, 0);
  g.move(10, 0, 10); g.move(60, -10, 40); g.move(120, -20, 70);
  const r = g.up(150, -25, 85);
  assert(r.kind === "throw" && r.speed >= GESTURE.THROW_SPEED && r.vx > 0 && r.vy < 0, "a flick throws him, with its velocity", JSON.stringify(r));
}
{
  const g = new Gesture();
  g.down(0, 0, 0); g.move(20, 0, 30);
  const c = g.cancel();
  assert(c.kind === "cancel" && c.dragging === true && c.held === true, "lost capture mid-drag ends cleanly, knowing it was a drag");
  assert(!g.active && g.up(0, 0, 50).kind === "none", "no ghost press after a cancel");
  g.down(0, 0, 0);
  assert(g.cancel().dragging === false, "a cancelled plain press wasn't a drag");
}

// ---------------- HoverIntent: approach vs passing through ----------------
{
  const h = new HoverIntent();
  h.enter(0);
  let u = h.update(100);
  assert(!u.hovered && !u.changed, "a cursor passing over him isn't hovering yet");
  u = h.update(GESTURE.HOVER_DWELL_MS + 1);
  assert(u.hovered && u.changed && u.greet, "settled on him: hovered, with one greeting");
  h.leave(400);
  u = h.update(400 + GESTURE.HOVER_LEAVE_MS - 50);
  assert(u.hovered && !u.changed, "a short slip off him keeps the moment (grace)");
  u = h.update(400 + GESTURE.HOVER_LEAVE_MS + 1);
  assert(!u.hovered && u.changed, "gone long enough: the hover ends");
  h.enter(2000);
  u = h.update(2000 + GESTURE.HOVER_DWELL_MS + 1);
  assert(u.hovered && !u.greet, "coming back soon after: no repeated greeting");
  h.leave(3000); h.update(5000);
  h.enter(40000);
  u = h.update(40000 + GESTURE.HOVER_DWELL_MS + 1);
  assert(u.greet, "after the cooldown he greets again");
}

// ---------------- Interaction: one owner, mirrored as a lease ----------------
{
  const leases = [];
  const ix = new Interaction({ onLease: (k, s, ms) => leases.push([k, s, ms]) });
  assert(ix.state === "ambient" && !ix.owning, "starts ambient, owning nothing");
  ix.set("hovered");
  assert(leases.at(-1)[0] === "grant" && leases.at(-1)[1] === "hovered", "hovering takes the lease (no relocation while approached)");
  ix.set("ambient");
  assert(leases.at(-1)[0] === "release" && leases.at(-1)[2] === SETTLE_MS.hovered, "leaving releases it with a settle period");
  ix.set("held");
  ix.set("thrown");
  assert(leases.at(-1)[0] === "grant" && leases.at(-1)[1] === "thrown", "held -> thrown keeps ownership (physics now owns him)");
  ix.rest();
  assert(leases.at(-1)[0] === "release" && leases.at(-1)[2] === SETTLE_MS.thrown && SETTLE_MS.thrown >= 2000,
    "after a throw, placement waits a settle period", JSON.stringify(leases.at(-1)));
  const n = leases.length;
  ix.renew();
  assert(leases.length === n, "renew is silent when nothing owns him");
  ix.set("menu_open"); ix.renew();
  assert(leases.at(-1)[0] === "grant" && leases.at(-1)[1] === "menu_open", "an open menu keeps renewing its lease");
  ix.setBase("work_focus");
  assert(ix.state === "menu_open" && ix.base === "work_focus", "the user's activity doesn't yank him out of an interaction");
  ix.rest();
  assert(ix.state === "work_focus", "...and he rests in work focus afterwards");
  ix.setBase("ambient");
  assert(ix.state === "ambient", "activity change applies at once when idle");
  assert(ix.set("bogus") === false && ix.state === "ambient", "unknown states are refused");
  assert(STATES.length === 14 && ["held", "thrown", "menu_open", "playing", "skill_running", "drop_target", "repositioning"].every((s) => OWNING.has(s)),
    "all 14 interaction states exist; the direct interactions own his position");
  assert(!OWNING.has("ambient") && !OWNING.has("work_focus") && !OWNING.has("curious") && !OWNING.has("docked"),
    "resting states never block placement");
}

// ---------------- Reactions with variety (deterministic randomness) ----------------
{
  const r = (v) => () => v;
  assert(landingKind(2000, 2, r(0.1)) === "faceplant" && landingKind(2000, 2, r(0.9)) === "superhero", "a hard landing: faceplant or superhero");
  assert(landingKind(1000, 1, r(0.1)) === "roll" && landingKind(1000, 1, r(0.7)) === "cartwheel", "a medium landing: roll or cartwheel");
  assert(landingKind(300, 5, r(0.2)) === "dizzy", "lots of bounces: dizzy");
  assert(["popup", "invite", "annoyed", "surprised"].includes(landingKind(300, 1, r(0.5))), "a soft landing: a playful pop-up / invite");
  const kinds = new Set();
  for (let i = 0; i < 400; i++) kinds.add(landingKind(Math.random() * 2500, (Math.random() * 6) | 0));
  assert(kinds.size >= 8, "at least eight different landings occur", [...kinds].join(","));
  assert(dropReaction([{ name: "huge.iso", size: 900 * 1024 * 1024 }]) === "struggle", "a heavy file is a struggle");
  assert(["portal", "kick"].includes(dropReaction([{ name: "Chrome.lnk", size: 2000 }])), "a shortcut goes through a portal or gets kicked");
  const drops = new Set();
  for (let i = 0; i < 400; i++) drops.add(dropReaction([{ name: "photo.png", size: 1000 }]));
  assert(drops.size >= 6, "a normal file gets varied catches", [...drops].join(","));
}

// ---------------- Travel styles ----------------
{
  assert(travelPlan("teleport", 3000).fade === true, "a teleport fades out and in");
  assert(travelPlan("walk", 300).ms <= 2400 && travelPlan("walk", 300).arc === 0, "a walk stays on the ground, bounded in time");
  assert(travelPlan("fly", 1500).arc > travelPlan("hop", 500).arc, "flight arcs higher than a hop");
}

// ---------------- Personality while working ----------------
{
  assert(IDLE.read && IDLE.hologram && !IDLE.read.big && !IDLE.hologram.big, "calm pastimes exist (read, hologram)");
  const coding = activityBias("coding");
  assert(coding("wander") === 0 && coding("fly") === 0 && coding("parkour") === 0 && coding("perch") === 0 && coding("curious") === 0,
    "while coding he never walks across the screen");
  assert(coding("read") >= 2 && coding("hologram") >= 2, "...and leans toward reading / his hologram toy");
  assert(activityBias("video")("dance") === 0 && activityBias("video")("sit") > 1, "during a video: sits, no dancing");
  assert(activityBias("browsing")("fly") === 0 && activityBias("browsing")("wander") > 0, "browsing: may wander, not fly");
  assert(activityBias("other")("dance") === 1 && activityBias("nonsense")("fly") === 1, "otherwise everything is allowed");
  assert(DEFAULTS.work_aware === true && safeSettings({ work_aware: false }).work_aware === false && safeSettings({}).hover_greet === true,
    "work_aware / hover_greet settings round-trip");
}

assert(GESTURE.DOUBLE_MS >= 250 && GESTURE.DOUBLE_MS < GESTURE.HOLD_MS, "a double-click counts once (the menu doesn't open and shut)");

// ---------------- Hit mask: his silhouette, never a rectangle, never peeking out ----------------
{
  // a 60x60 RGBA frame with an opaque 30x40 "body" (x 15..44, y 10..49) and a soft 1-px anti-aliased rim
  const W = 60, H = 60, px = new Uint8Array(W * H * 4);
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
    const inBody = x >= 15 && x < 45 && y >= 10 && y < 50;
    const rim = !inBody && x >= 14 && x < 46 && y >= 9 && y < 51;
    px[(y * W + x) * 4 + 3] = inBody ? 255 : rim ? 120 : 0;
  }
  const cols = 12, rows = 12;                                 // 5 px cells
  const solid = coverCells(px, W, H, cols, rows);
  const on = (cells) => cells.reduce((n, v) => n + v, 0);
  assert(on(solid) === 6 * 8, "only fully covered cells count (6 x 8 inside the body)", String(on(solid)));
  assert(solid[0] === 0 && solid[2 * cols + 3] === 1 && solid[2 * cols + 2] === 0, "the anti-aliased rim and the empty corner are not his");
  const flipped = new Uint8Array(px.length);
  for (let y = 0; y < H; y++) flipped.set(px.subarray((H - 1 - y) * W * 4, (H - y) * W * 4), y * W * 4);
  assert(maskBits(coverCells(flipped, W, H, cols, rows, true), cols, rows).join() === maskBits(solid, cols, rows).join(),
    "WebGL's bottom-up rows read the same silhouette");
  const first = hitCells(solid, null);
  assert(on(first) === 6 * 8, "first sample: his whole (fully covered) silhouette — not a shrunken sliver");
  // he raises an arm: cells only in the newer frame are not clickable yet; cells he left are dropped at once
  const moved = solid.slice(); for (let r = 0; r < 2; r++) moved[r * cols + 9] = 1; moved[7 * cols + 3] = 0;
  const steady = hitCells(moved, solid);
  assert(on(steady) === on(first) - 1 && steady[1 * cols + 9] === 0 && steady[7 * cols + 3] === 0,
    "a moving part never joins the mask until it has stayed put");
  // lots of motion never shrinks the steady part (a click on his body must not fall through to the app behind)
  const lots = new Uint8Array(cols * rows); for (let r = 1; r < 11; r++) for (let c = 1; c < 11; c++) lots[r * cols + c] = 1;
  assert(on(hitCells(lots, solid)) === on(lots.map((v, i) => v & solid[i])), "busy motion: still every steady cell");
  const bits = maskBits(first, cols, rows);
  assert(bits.length === rows && bits.every((s) => s.length === cols && /^[01]+$/.test(s)) && bits[0] === "0".repeat(cols),
    "wire format: one 0/1 string per row, top row first");
  // his colour per cell (12-bit), so a cell that ever peeks out looks like him
  const tinted = px.slice();
  for (let i = 0; i < W * H; i++) if (tinted[i * 4 + 3] === 255) { tinted[i * 4] = 0xf0; tinted[i * 4 + 1] = 0x80; tinted[i * 4 + 2] = 0x10; }
  const cc = cellColors(tinted, W, H, cols, rows, first);
  assert(cc[2 * cols + 3] === 0xf81 && cc[0] === 0, "a cell's colour is his average there; other cells carry none", cc[2 * cols + 3].toString(16));
  const hex = maskColors(cc, cols, rows);
  assert(hex.length === rows && hex[2].length === 3 * cols && hex[2].slice(9, 12) === "f81" && hex[0] === "000".repeat(cols),
    "colours on the wire: 3 hex digits per cell");
  const g = maskGrid(87, 120);                                 // his default size: fine cells, a usable target
  assert(g.cols >= HITMASK.MIN_CELLS && g.cols <= HITMASK.MAX_CELLS && Math.abs(87 / g.cols - HITMASK.CELL_PX) < 1,
    "cells of about CELL_PX", JSON.stringify(g));
  assert(maskGrid(5000, 5000).rows === HITMASK.MAX_CELLS && maskGrid(10, 10).cols === HITMASK.MIN_CELLS, "the grid is bounded");
  assert(on(coverCells(null, 0, 0, 4, 4)) === 0, "no frame: no cells");
}

// ---------------- Menu sizes ----------------
assert(["main", "talk", "skills", "play", "settings"].every((v) => SIZES[v] && SIZES[v][0] > 200 && SIZES[v][1] > 200),
  "every menu view has a size");

console.log(JSON.stringify({ schema: 1, passed: passes, failed: failures.length, failures }));
process.exit(failures.length ? 1 : 0);
