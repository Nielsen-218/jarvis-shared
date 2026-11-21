import assert from 'node:assert/strict';
import { PerformanceBudget } from '../../app/companion/performance-budget.js';

let passed = 0;
function test(name, fn) {
  try { fn(); passed++; }
  catch (e) { console.error(name); throw e; }
}
function driver(options = {}, active = true, step = 50) {
  const budget = new PerformanceBudget(options);
  let now = 0;
  return { budget, get now() { return now; },
    feed(cost, frames = 1) {
      let state;
      for (let i = 0; i < frames; i++) state = budget.sample(cost, now += step, active);
      return state;
    },
  };
}

test('defaults and detached snapshots', () => {
  const b = new PerformanceBudget();
  assert.deepEqual(b.snapshot(), {dpr: 1.5, idleFps: 20, activeFps: 30, level: 0});
  const snap = b.state; snap.dpr = 100; snap.level = 4;
  assert.equal(b.state.level, 0); assert.equal(b.state.dpr, 1.5);
});

test('a few slow frames and a single giant cost never degrade', () => {
  const d = driver(); d.feed(2, 100);
  d.feed(1000, 5); d.feed(Number.MAX_VALUE); d.feed(2, 100);
  assert.equal(d.budget.state.level, 0);
});

test('sustained pressure reduces DPR twice before FPS', () => {
  const d = driver(), transitions = [];
  let previous = 0;
  for (let i = 0; i < 400; i++) {
    const s = d.feed(100);
    if (s.level !== previous) transitions.push(s);
    previous = s.level;
  }
  assert.deepEqual(transitions.map(s => s.level), [1, 2, 3, 4]);
  for (const s of transitions.slice(0, 2)) {
    assert.ok(s.dpr < 1.5); assert.equal(s.idleFps, 20); assert.equal(s.activeFps, 30);
  }
  assert.equal(transitions[2].dpr, transitions[1].dpr);
  assert.ok(transitions[2].activeFps < 30);
  assert.ok(transitions[3].activeFps < transitions[2].activeFps);
});

test('long health recovers one stage at a time to owner ceilings', () => {
  const d = driver(); d.feed(100, 400);
  assert.equal(d.budget.state.level, 4);
  d.feed(1, 100); assert.equal(d.budget.state.level, 4);
  const recovery = []; let prev = 4;
  for (let i = 0; i < 1500; i++) {
    const s = d.feed(1);
    if (s.level !== prev) recovery.push(s.level);
    prev = s.level;
  }
  assert.deepEqual(recovery, [3, 2, 1, 0]);
  assert.deepEqual(d.budget.snapshot(), {dpr: 1.5, idleFps: 20, activeFps: 30, level: 0});
});

test('threshold noise does not thrash', () => {
  const d = driver();
  for (let i = 0; i < 1000; i++) d.feed(i % 2 ? 17 : 15);
  assert.equal(d.budget.state.level, 0);
});

test('nonfinite, invalid, duplicate and backward samples are ignored', () => {
  const d = driver(); d.feed(2, 10); const before = d.budget.state;
  for (const cost of [NaN, Infinity, -Infinity, -1, null, undefined, '100'])
    assert.deepEqual(d.budget.sample(cost, d.now + 10, true), before);
  for (const time of [NaN, Infinity, -Infinity, -1, null, undefined, '100'])
    assert.deepEqual(d.budget.sample(100, time, true), before);
  assert.deepEqual(d.budget.sample(100, d.now, true), before);
  assert.deepEqual(d.budget.sample(100, d.now - 1, true), before);
  d.feed(2, 100); assert.deepEqual(d.budget.state, before);
});

test('suspension and clock gaps cannot degrade or recover', () => {
  const d = driver(); d.feed(100, 400); const before = d.budget.state;
  assert.deepEqual(d.budget.sample(100, d.now + 1000000, true), before);
  assert.deepEqual(d.budget.sample(0, d.now + 2000000, false), before);
  for (const mode of [null, undefined, 'suspended'])
    assert.deepEqual(d.budget.sample(100, 3000000, mode), before);
  assert.deepEqual(d.budget.sample(1, 4000000, true), before);
});

test('idle false is valid, and original settings remain maxima', () => {
  const options = Object.freeze({baseDpr: 1, idleFps: 10, activeFps: 24});
  const d = driver(options, false, 100);
  d.feed(100, 250); assert.equal(d.budget.state.level, 4);
  const s = d.budget.state;
  assert.ok(s.dpr <= 1 && s.dpr >= .5);
  assert.ok(s.idleFps <= 10 && s.activeFps <= 24);
  assert.deepEqual(options, {baseDpr: 1, idleFps: 10, activeFps: 24});
});

test('low ceilings are never raised and invalid options have finite defaults', () => {
  const d = driver({baseDpr: .25, idleFps: .5, activeFps: .75}); d.feed(1000, 400);
  const s = d.budget.state;
  assert.ok(s.dpr <= .25 && s.idleFps <= .5 && s.activeFps <= .75);
  assert.deepEqual(new PerformanceBudget({baseDpr: NaN, idleFps: 0, activeFps: Infinity}).state,
    {dpr: 1.5, idleFps: 20, activeFps: 30, level: 0});
});

test('mode switching discards incompatible pressure history', () => {
  const b = new PerformanceBudget();
  for (let i = 1; i <= 1000; i++) b.sample(20, i * 50, i % 2 === 0);
  assert.equal(b.state.level, 0);
});

test('sparse samples cannot manufacture sustained pressure', () => {
  const d = driver({}, true, 1900); d.feed(100, 10);
  assert.equal(d.budget.state.level, 0);
});

test('expensive periodic readback can reduce the runtime budget', () => {
  const d = driver();
  for (let i = 0; i < 300; i++) d.feed(i % 4 === 0 ? 100 : 2);
  assert.ok(d.budget.state.level > 0);
});

test('huge clock followed by ordinary timestamps does not wedge sampling', () => {
  const d = driver(); d.feed(2, 50);
  d.budget.sample(100, 1e12, true);
  d.feed(100, 300);
  assert.ok(d.budget.state.level > 0);
});

test('changes respect dwell and EWMA stays bounded under finite extremes', () => {
  const d = driver(); let lastChange = null, previous = 0;
  for (let i = 0; i < 1000; i++) {
    const s = d.feed(Number.MAX_VALUE);
    assert.ok(d.budget._ewma === null || (d.budget._ewma >= 0 && d.budget._ewma <= 8));
    if (s.level !== previous) {
      if (lastChange !== null) assert.ok(d.now - lastChange >= 4000);
      lastChange = d.now;
      assert.equal(s.level, previous + 1);
    }
    previous = s.level;
  }
  assert.equal(d.budget.state.level, 4);
});

console.log(JSON.stringify({performance_budget_passed: passed, performance_budget_failed: 0}));
