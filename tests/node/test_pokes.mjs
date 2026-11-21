import assert from 'node:assert/strict';
import { test } from 'node:test';
import { LINES, REACTIONS, PokeTracker, allLines, tierOf, daypart, pools, pickPoke, pickDouble, addressAs } from '../../app/companion/pokes.js';
import { CLIPS } from '../../app/companion/animation.js';

const seeded = (seed = 7) => () => { seed = (seed * 1664525 + 1013904223) >>> 0; return seed / 4294967296; };

test('at least 40 distinct spoken lines, none empty, all short enough to say', () => {
  const lines = allLines();
  assert.ok(lines.length >= 40, `only ${lines.length}`);
  assert.equal(new Set(lines).size, lines.length, 'a line appears twice');
  for (const l of lines) assert.ok(l.length > 3 && l.length < 90, l);
});

test('every reaction clip exists in the animator', () => {
  for (const r of Object.values(REACTIONS)) for (const a of r.anims) assert.ok(CLIPS[a], `missing clip ${a}`);
});

test('streak tiers: patience wears thin as the pokes pile up', () => {
  assert.deepEqual([1, 2, 3, 4, 6, 7, 11, 12, 40].map(tierOf),
    ['first', 'again', 'again', 'thin', 'thin', 'exasperated', 'exasperated', 'surrender', 'surrender']);
  const t = new PokeTracker();
  assert.equal(t.poke(0), 1); assert.equal(t.poke(3000), 2); assert.equal(t.poke(6000), 3);
  assert.equal(t.poke(60000), 1, 'a long pause starts over');
  const p = pickPoke({ streak: 8 }, { rng: seeded() });
  assert.equal(p.tier, 'exasperated'); assert.ok(LINES.exasperated.includes(p.raw));
});

test('never the same line twice in a row, over a long run with every context', () => {
  const rng = seeded(3), t = new PokeTracker();
  let now = 0, prev = null;
  for (let i = 0; i < 600; i++) {
    now += i % 9 === 0 ? 20000 : 1500;
    const p = pickPoke({ streak: t.poke(now), hour: i % 24, mood: { name: ['happy', 'tired', 'neutral'][i % 3], intensity: .6 },
      recent: [null, 'landed', 'spoke'][i % 3], activity: ['other', 'coding', 'gaming'][i % 3] }, { rng, avoid: t.said });
    assert.notEqual(p.raw, prev);
    prev = p.raw; t.remember(p.raw);
  }
  const d1 = pickDouble({ rng, avoid: [] }), d2 = pickDouble({ rng, avoid: [d1.raw] });
  assert.notEqual(d1.raw, d2.raw);
});

test('context picks the pool: time of day, what just happened, what the owner is doing', () => {
  assert.deepEqual([3, 8, 13, 19, 23].map(daypart), ['night', 'morning', 'afternoon', 'evening', 'night']);
  const names = (ctx) => pools(ctx).map(([n]) => n);
  assert.ok(names({ streak: 1, hour: 8 }).includes('morning'));
  assert.equal(names({ streak: 1, recent: 'landed' })[0], 'landed');
  assert.ok(names({ streak: 1, activity: 'coding' }).includes('working'));
  assert.ok(names({ streak: 1, mood: { name: 'tired', intensity: .5 } }).includes('tired'));
  assert.deepEqual(names({ streak: 5, recent: 'landed', hour: 8 }), ['thin']);
});

test('the form of address follows the app', () => {
  assert.equal(addressAs('Yes, sir?', 'sir'), 'Yes, sir?');
  assert.equal(addressAs('Sir. Yes, sir?', 'boss'), 'Boss. Yes, boss?');
  assert.equal(pickPoke({ streak: 1 }, { rng: () => 0.999, title: 'ma\'am' }).text.includes('sir'), false);
});

test('one click or a double click, never both: a double click speaks no poke and counts no streak', async () => {
  const { ClickGate } = await import('../../app/companion/pokes.js');
  const timers = [], log = [], tracker = new PokeTracker();
  const gate = new ClickGate({ windowMs: 400, onSingle: (t) => log.push(['poke', tracker.poke(t)]), onDouble: () => log.push(['listen']),
    setTimer: (fn) => { const id = { fn }; timers.push(id); return id; }, clearTimer: (id) => timers.splice(timers.indexOf(id), 1) });
  const fire = () => { while (timers.length) timers.shift().fn(); };
  assert.equal(gate.click(1000), 'wait'); assert.deepEqual(log, [], 'nothing is said before the window has passed');
  assert.equal(gate.click(1200), 'double'); fire();
  assert.deepEqual(log, [['listen']]); assert.equal(tracker.streak, 0);
  gate.click(5000); fire(); assert.deepEqual(log.at(-1), ['poke', 1]);
  gate.click(6000); fire(); assert.deepEqual(log.at(-1), ['poke', 2], 'separate clicks are separate pokes');
  gate.click(9000); gate.cancel(); fire(); assert.equal(log.length, 3, 'a cancelled click (hidden, menu) says nothing');
  gate.click(20000); gate.click(20300); gate.click(20350); fire();
  assert.deepEqual(log.slice(3), [['listen'], ['poke', 1]], 'a third click starts over');
});
