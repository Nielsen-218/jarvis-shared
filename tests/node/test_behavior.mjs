// Node ESM harness that exercises the actual renderer logic in
// app/companion/behavior.js (not a Python mirror). Run via:
//   node tests/node/test_behavior.mjs
// It exits 0 on success and prints a JSON summary on the last line.
import {
  SCHEMA, STATES, IDLE, DEFAULTS, LEVEL_STALE_MS, seasonalTheme, timeGreeting, shouldSuggestBreak,
  MOODS, MoodModel, moodExpression,
  validate, safeSettings, resolveState, isAmbient,
  IdlePicker, Scheduler, CompanionState, EventBuffer,
} from "../../app/companion/behavior.js";

let failures = [];
let passes = 0;
function assert(cond, name, detail = "") {
  if (cond) { passes += 1; return; }
  failures.push({ name, detail });
}
function eq(a, b, name) { assert(JSON.stringify(a) === JSON.stringify(b), name, `got ${JSON.stringify(a)} want ${JSON.stringify(b)}`); }

// ---------------- validate + safeSettings ---------------- //
eq(validate(null), null, "validate rejects null");
eq(validate({}), null, "validate rejects empty");
eq(validate({ type: "state", value: "flying" }), null, "validate rejects unknown state");
eq(validate({ type: "state", value: "sleeping" }), { type: "state", value: "idle" }, "validate aliases sleeping");
eq(validate({ type: "level", value: "0.4" }), { type: "level", value: 0.4 }, "validate parses str level");
eq(validate({ type: "level", value: Number.NaN }), null, "validate rejects NaN level");
eq(validate({ type: "level", value: -5 }), { type: "level", value: 0 }, "validate clamps negative level");
eq(validate({ type: "line", who: "bot", text: "x" }), null, "validate rejects unknown line author");
eq(validate({ type: "notify", title: "x", body: "y" }).type, "notify", "validate passes through legacy notify");
eq(validate({ type: "weird" }), null, "validate drops unknown types");

const s = safeSettings({ quality: "ultra", scale: 99, idle_fps: 0, active_fps: 1000,
                        dpr_cap: 5.5, window_size: [1, 999], reduced_motion: true, visible: 0 });
assert(s.quality === DEFAULTS.quality, "safeSettings drops unknown quality");
assert(s.scale === 2.0, "safeSettings clamps scale");
assert(s.idle_fps === 5 && s.active_fps === 60, "safeSettings clamps fps");
assert(s.dpr_cap === 2.0, "safeSettings clamps dpr_cap");
assert(s.window_size[0] === 160 && s.window_size[1] === 640, "safeSettings clamps window_size");
assert(s.reduced_motion === true && s.visible === false, "safeSettings coerces bools");

// ---------------- resolveState — full lifecycle must pass ---------------- //
const lifecycle = [
  ["idle", "listening", "listening"],
  ["listening", "thinking", "thinking"],
  ["thinking", "speaking", "speaking"],
  ["speaking", "idle", "idle"],          // critical: was broken by the sticky resolver
  ["speaking", "listening", "listening"], // user interrupt mid-reply
  ["listening", "muted", "muted"],
  ["muted", "listening", "listening"],
  ["speaking", "error", "error"],
  ["error", "idle", "idle"],
  ["error", "listening", "listening"],
  ["sleeping", "speaking", "speaking"],
  ["speaking", "flying", "speaking"],    // garbage keeps current
  ["speaking", null, "speaking"],
  ["speaking", "speaking", "speaking"],
];
for (const [cur, nxt, want] of lifecycle) {
  eq(resolveState(cur, nxt), want, `resolveState ${cur}->${nxt}`);
}

assert(isAmbient("idle") && isAmbient("sleeping"), "isAmbient covers idle/sleeping");
for (const bad of ["listening", "thinking", "speaking", "muted", "error", "working"]) {
  assert(!isAmbient(bad), `isAmbient rejects ${bad}`);
}

// ---------------- IdlePicker respects cooldowns and reduced motion ---- //
{
  const picks = [];
  const rnd = (() => { let s = 1; return () => (s = (s * 1664525 + 1013904223) % 2 ** 32) / 2 ** 32; })();
  const p = new IdlePicker({ random: rnd });
  for (let i = 0; i < 200; i++) {
    const a = p.choose(i * 500); // every 500 ms
    if (a) picks.push([i * 500, a]);
  }
  const breaths = picks.filter(([, a]) => a === "breathe").map(([t]) => t);
  for (let i = 1; i < breaths.length; i++) {
    assert(breaths[i] - breaths[i - 1] >= 1500, "breathe cooldown >= 1500ms");
  }
  const stretches = picks.filter(([, a]) => a === "stretch").map(([t]) => t);
  for (let i = 1; i < stretches.length; i++) {
    assert(stretches[i] - stretches[i - 1] >= 45000, "stretch cooldown >= 45000ms");
  }
}
{
  const seen = new Set();
  const p = new IdlePicker({ reducedMotion: true });
  for (let i = 0; i < 400; i++) { const a = p.choose(i * 2000); if (a) seen.add(a); }
  for (const big of ["weight_shift", "stretch", "wave"]) {
    assert(!seen.has(big), `reducedMotion suppresses ${big}`);
  }
  assert(seen.has("breathe") && seen.has("blink"), "reducedMotion still allows breathe+blink");
}
{
  const p = new IdlePicker();
  for (const name of Object.keys(IDLE)) p.last[name] = 0;
  eq(p.choose(100), null, "picker returns null when all on cooldown");
}

// ---------------- Scheduler: single loop, hide suspension, duplicate protection ---------- //
{
  const frames = [];
  const scheduled = [];
  const makeRaf = () => {
    let next = 1;
    const q = new Map();
    const request = (fn) => { const id = next++; q.set(id, fn); scheduled.push(id); return id; };
    const cancel = (id) => { q.delete(id); };
    const run = (t) => { for (const [id, fn] of [...q]) { q.delete(id); fn(t); } };
    return { request, cancel, run, pending: () => q.size };
  };
  const raf = makeRaf();
  const sch = new Scheduler({
    onFrame: (t) => frames.push(t),
    requestFrame: raf.request, cancelFrame: raf.cancel,
  });
  sch.start();
  sch.start(); // duplicate — must not spawn a second loop
  assert(raf.pending() === 1, "scheduler never has more than one pending frame after double-start");
  raf.run(16);
  raf.run(33);
  raf.run(50);
  assert(frames.length === 3, `three frames fired, got ${frames.length}`);
  sch.stop();
  assert(raf.pending() === 0, "stop cancels outstanding frame");
  raf.run(70);
  assert(frames.length === 3, "no frames fire after stop");

  // suspend/resume mirrors native hide/show
  sch.start();
  raf.run(100);
  const before = frames.length;
  sch.suspend();
  assert(sch.suspended && !sch.running, "suspend halts the scheduler");
  raf.run(120);
  assert(frames.length === before, "no frames during suspension");
  sch.resume();
  raf.run(140);
  assert(frames.length === before + 1, "resume reinstates the loop");
}

// ---------------- CompanionState: state apply, mouth timeout, mode gates ---------- //
{
  let now = 1000;
  const st = new CompanionState({ now: () => now, levelTimeoutMs: 500 });

  // idle -> listening -> thinking -> speaking (level) -> idle
  for (const [raw, want] of [
    [{ type: "state", value: "listening" }, "listening"],
    [{ type: "state", value: "thinking" }, "thinking"],
    [{ type: "state", value: "speaking" }, "speaking"],
  ]) {
    st.applyEvent(raw); assert(st.mode === want, `mode now ${want}`);
  }

  st.applyEvent({ type: "level", value: 0.6 });
  assert(st.mouthTarget(now) === 0.6, "mouth follows level while speaking");

  // stale audio closes the mouth even if no new events arrive
  now += 300;
  assert(st.mouthTarget(now) === 0.6, "mouth stays open while audio is fresh");
  now += 300;             // total gap > LEVEL_STALE_MS
  assert(st.mouthTarget(now) === 0, "mouth closes after level stale timeout");

  // transition to idle -> mouth closed even with a stale non-zero level
  st.applyEvent({ type: "state", value: "idle" });
  assert(st.mode === "idle" && st.level === 0, "idle resets level");
  assert(st.mouthTarget(now) === 0, "mouth is closed in idle");

  // interrupt scenario: listening while speaking (same as main.py when the user talks over)
  st.applyEvent({ type: "state", value: "speaking" });
  st.applyEvent({ type: "level", value: 0.4 });
  st.applyEvent({ type: "state", value: "listening" });
  assert(st.mode === "listening" && st.level === 0 && st.mouthTarget(now) === 0, "interrupt closes the mouth");

  // updateMouth lerps toward target and reaches 0 after decay
  st.applyEvent({ type: "state", value: "speaking" });
  st.applyEvent({ type: "level", value: 1.0 });
  for (let i = 0; i < 20; i++) st.updateMouth(now, 0.016);
  assert(st.jaw > 0.9, `jaw opens toward level, got ${st.jaw}`);
  // now stall the producer and let updateMouth decay toward the stale timeout target
  now += 700;
  for (let i = 0; i < 60; i++) st.updateMouth(now + i * 16, 0.016);
  assert(st.jaw === 0, `jaw closes after stale timeout, got ${st.jaw}`);
}

// ---------------- EventBuffer bounds and coalescing ------------- //
{
  const buf = new EventBuffer(4);
  assert(buf.put({ type: "state", value: "listening" }) === true, "buffer accepts state");
  assert(buf.put({ type: "notify", title: "x", body: "y" }) === true, "buffer passes legacy");
  assert(buf.put({ type: "weird" }) === false, "buffer rejects unknown");
  assert(buf.put({ type: "state", value: "flying" }) === false, "buffer rejects bad state");
  for (let i = 0; i < 20; i++) buf.put({ type: "line", who: "sys", text: `l${i}` });
  assert(buf.length === 4, `buffer length bounded, got ${buf.length}`);

  const b2 = new EventBuffer();
  for (const v of [0.1, 0.2, 0.3, 0.4]) b2.put({ type: "level", value: v });
  const drained = b2.drain();
  assert(drained.length === 1 && drained[0].value === 0.4, "consecutive levels coalesce");

  const b3 = new EventBuffer();
  b3.put({ type: "level", value: 0.3 });
  b3.put({ type: "state", value: "idle" });
  b3.put({ type: "level", value: 0.5 });
  const types = b3.drain().map(e => e.type);
  eq(types, ["level", "state", "level"], "levels do not coalesce across other events");
}

{
  const d = safeSettings({});
  assert(d.character_px === 160 && d.idle_fps === 20 && d.active_fps === 30, "defaults: 160 px, 20 fps idle, 30 fps active");
  assert(safeSettings({ character_px: 5000 }).character_px === 400, "character_px clamps high");
  assert(safeSettings({ character_px: 3 }).character_px === 50, "character_px clamps low");
}

{
  assert(seasonalTheme(11).accessory === "partyhat" && seasonalTheme(11).greeting.length > 0, "December wears a party hat + greeting");
  assert(seasonalTheme(0).accessory === "halo", "January wears a halo");
  assert(seasonalTheme(9).accessory === "tophat", "October wears a top hat");
  assert(seasonalTheme(4).accessory === "none", "an ordinary month wears nothing");
  const d = safeSettings({}); assert(d.seasonal === true, "seasonal defaults on");
  assert(safeSettings({ seasonal: 0 }).seasonal === false, "seasonal clamps to boolean");
}

// ---------------- Proactive assistance: greeting + break reminder ---------------- //
{
  eq(timeGreeting(7), "Good morning, sir.", "07h greets morning");
  eq(timeGreeting(11), "Good morning, sir.", "11h still morning");
  eq(timeGreeting(12), "Good afternoon, sir.", "noon greets afternoon");
  eq(timeGreeting(16), "Good afternoon, sir.", "16h still afternoon");
  eq(timeGreeting(17), "Good evening, sir.", "17h greets evening");
  eq(timeGreeting(21), "Good evening, sir.", "21h still evening");
  eq(timeGreeting(23), "Working late, sir? Do take care.", "23h late-night");
  eq(timeGreeting(3), "Working late, sir? Do take care.", "03h late-night");
  for (let h = 0; h < 24; h++) assert(timeGreeting(h).length > 0, `every hour greets (${h})`);

  const T = 50 * 60 * 1000;
  assert(shouldSuggestBreak(T, false) === true, "suggests a break at threshold");
  assert(shouldSuggestBreak(T + 1, false) === true, "suggests past threshold");
  assert(shouldSuggestBreak(T - 1, false) === false, "no break just under threshold");
  assert(shouldSuggestBreak(0, false) === false, "no break at the start of a stretch");
  assert(shouldSuggestBreak(T, true) === false, "never suggests twice in one stretch");
  assert(shouldSuggestBreak(999 * T, true) === false, "stays quiet once suggested");
  assert(shouldSuggestBreak(1000, false, 1000) === true, "honours a custom short threshold");

  const d = safeSettings({});
  assert(d.proactive === true, "proactive defaults on");
  assert(safeSettings({ proactive: 0 }).proactive === false, "proactive clamps to boolean");
}

// ---------------- Mood demeanor (body language) ---------------- //
{
  const { applyDemeanor } = await import("../../app/companion/animation.js");
  const bone = () => ({ rotation: { x: 0, y: 0, z: 0 },
                        scale: { x: 1, y: 1, z: 1, multiplyScalar(f) { this.x *= f; this.y *= f; this.z *= f; return this; } } });
  const bones = () => ({ head: bone(), torso: bone(), shoulderL: bone(), shoulderR: bone() });

  // tired slumps: head tips forward (+x), torso leans, arms hang (shoulders drop apart). now=0 so the bob term is 0.
  let b = bones(); applyDemeanor(b, "tired", 1, 0.7, 0);
  assert(b.head.rotation.x > 0.1, "tired tips the head forward (slump)");
  assert(b.torso.rotation.x > 0, "tired leans the torso forward");
  assert(b.shoulderL.rotation.z < 0 && b.shoulderR.rotation.z > 0, "tired drops the shoulders");

  // proud stands tall: head lifts (−x), chest puffs (torso scales up).
  b = bones(); applyDemeanor(b, "proud", 1, 1.1, 0);
  assert(b.head.rotation.x < 0, "proud lifts the head");
  assert(b.torso.scale.x > 1, "proud puffs the chest");

  // intensity scales it; zero intensity or neutral is a no-op.
  const half = bones(); applyDemeanor(half, "tired", 0.5, 0.7, 0);
  assert(Math.abs(half.head.rotation.x - 0.15 * 0.5) < 1e-9, "demeanor scales with intensity");
  const none = bones(); applyDemeanor(none, "tired", 0, 0.7, 0); applyDemeanor(none, "neutral", 1, 1, 0);
  assert(none.head.rotation.x === 0 && none.torso.rotation.x === 0, "zero intensity / neutral does nothing");

  // liveliness bob: an upbeat mood adds a time-varying head bounce (nonzero when sin≠0).
  const t = bones(); applyDemeanor(t, "playful", 1, 1.4, 350);
  const t0 = bones(); applyDemeanor(t0, "playful", 1, 1.4, 0);
  assert(t.head.rotation.x !== t0.head.rotation.x, "liveliness bob animates over time");
}

// ---------------- Mood / emotion model ---------------- //
{
  // A fresh model is neutral.
  let t = 0; const clock = () => t;
  const m = new MoodModel({ halfLifeMs: 1000, now: clock });
  eq(m.dominant(0).mood, "neutral", "fresh mood is neutral");
  assert(m.dominant(0).intensity === 0, "neutral has zero intensity");

  // A nudge makes that mood dominant.
  m.nudge("tired", 0.8, 0);
  eq(m.dominant(0).mood, "tired", "nudge makes tired dominant");
  assert(Math.abs(m.dominant(0).intensity - 0.8) < 1e-9, "intensity reflects the nudge");

  // It decays toward neutral over time (half-life = 1000 ms here).
  t = 1000;
  assert(Math.abs(m.dominant(t).intensity - 0.4) < 1e-6, "one half-life halves intensity");
  t = 5000;                                   // 0.8 * 0.5^5 = 0.025 < 0.15 threshold
  eq(m.dominant(t).mood, "neutral", "decays back to neutral below threshold");

  // The stronger of two moods wins; nudges accumulate and saturate at 1.
  const m2 = new MoodModel({ halfLifeMs: 1e9, now: () => 0 });   // effectively no decay
  m2.nudge("happy", 0.3, 0); m2.nudge("annoyed", 0.5, 0);
  eq(m2.dominant(0).mood, "annoyed", "stronger mood wins");
  m2.nudge("happy", 0.9, 0);
  eq(m2.dominant(0).mood, "happy", "accumulated nudges overtake");
  assert(m2.dominant(0).intensity <= 1, "intensity saturates at 1");

  // neutral / unknown nudges are ignored.
  const m3 = new MoodModel({ now: () => 0 });
  m3.nudge("neutral", 1, 0); m3.nudge("bogus", 1, 0);
  eq(m3.dominant(0).mood, "neutral", "neutral/unknown nudges do nothing");
  assert(MOODS.includes("playful") && MOODS.length === 7, "mood catalogue");

  // bias(): a strong tired mood multiplies tired-leaning idles up and lively ones down.
  const mb = new MoodModel({ halfLifeMs: 1e9, now: () => 0 });
  mb.nudge("tired", 1.0, 0);
  const b = mb.bias(0);
  assert(b("yawn") > 2.5, "tired strongly favours yawn");
  assert(b("sit") > 2.0, "tired favours sit");
  assert(b("dance") < 0.3, "tired suppresses dance");
  assert(Math.abs(b("blink") - 1) < 1e-9, "unlisted idles keep weight 1");
  // a faint mood barely tilts
  const mf = new MoodModel({ halfLifeMs: 1e9, now: () => 0 });
  mf.nudge("tired", 0.2, 0);
  assert(Math.abs(mf.bias(0)("yawn") - (1 + (3 - 1) * 0.2)) < 1e-6, "faint mood tilts proportionally");

  // neutral bias is all-ones.
  const bn = new MoodModel({ now: () => 0 }).bias(0);
  for (const name of Object.keys(IDLE)) assert(bn(name) === 1, `neutral bias is 1 for ${name}`);

  // moodExpression: tired droops the lids; happy clamps toward wide; neutral is flat.
  assert(moodExpression("tired", 1).lid > 0.3, "tired droops the eyelids");
  assert(moodExpression("annoyed", 1).lid > 0, "annoyed narrows the eyelids");
  assert(moodExpression("happy", 1).lid <= 0, "happy does not droop");
  assert(moodExpression("tired", 1).bob < 1, "tired is sluggish");
  assert(moodExpression("playful", 1).bob > 1, "playful is lively");
  eq(moodExpression("neutral", 1).lid, 0, "neutral lid is flat");
  assert(moodExpression("tired", 0).lid === 0, "zero intensity means no expression");
}

// ---------------- IdlePicker honours a mood bias function ---------------- //
{
  // With a bias that zeroes a behaviour, it is never chosen even when off cooldown.
  const p = new IdlePicker({ random: () => 0.0001 });
  const bias = (name) => (name === "breathe" ? 0 : 1);
  const picks = new Set();
  for (let i = 0; i < 50; i++) { const a = p.choose(i * 5000, bias); if (a) picks.add(a); }
  assert(!picks.has("breathe"), "a zero-weight bias removes a behaviour");
  // Backward compatible: no bias function behaves as before.
  const p2 = new IdlePicker();
  assert(typeof p2.choose(0) === "string", "choose() still works without a bias");
}

// ---------------- Summary ---------------- //
const result = { schema: SCHEMA, passed: passes, failed: failures.length, failures };
console.log(JSON.stringify(result));
if (failures.length) process.exit(1);
