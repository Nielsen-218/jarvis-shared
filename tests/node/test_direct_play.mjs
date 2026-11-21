import assert from 'node:assert/strict';
import * as T from '../../app/vendor/three/three.module.min.js';
import { buildDirectPlay } from '../../app/companion/direct-play.js';
import { TARGET_HEIGHT_M as H } from '../../app/companion/glb-adapter.js';

let passed = 0;
function test(name, fn) { fn(); passed++; console.log(name); }
const near = (a, b) => assert.ok(Math.abs(a - b) < 1e-10, `${a} != ${b}`);
const hand = new T.Vector3(0, H * .6, H * .12);
const foot = new T.Vector3(0, H * .08, H * .12);
const frames = (play, n, targets = {}) => { for (let i = 0; i < n; i++) play.update(1 / 120, targets); };

test('real mesh follows deterministic fixed-step trajectory', () => {
  const a = buildDirectPlay(T), b = buildDirectPlay(T);
  for (const p of [a, b]) { p.start('catch'); p.launch({ x: -.1, y: .2, vx: .1, vy: .2 }); }
  frames(a, 24);
  for (let i = 0; i < 12; i++) b.update(1 / 60);
  assert.deepEqual(a.snapshot(), b.snapshot());
  const s = a.snapshot();
  near(s.position.x, -.08);
  near(s.position.y, .2 + .2 * .2 - H * 5 * (1 / 240) ** 2 * 48 * 49 / 2);
  assert.ok(a.group.isGroup && a.group.children[0].isMesh);
  assert.ok(a.group.children[0].geometry.isBufferGeometry);
  assert.deepEqual(a.group.children[0].position, s.position);
  s.position.x = 99; assert.notEqual(a.snapshot().position.x, 99);
});

test('practice toss catches a real world hand once and follows it', () => {
  const events = [], p = buildDirectPlay(T, { onScore: e => events.push(e) });
  p.start('catch'); frames(p, 100, { leftHand: hand });
  assert.equal(p.snapshot().score, 1); assert.equal(events.length, 1);
  assert.deepEqual(events[0], { kind: 'catch', score: 1, delta: 1, contact: 'leftHand' });
  const moved = hand.clone().add(new T.Vector3(.03, 0, 0));
  frames(p, 100, { leftHand: moved });
  assert.deepEqual(p.snapshot().position, moved); assert.equal(p.snapshot().score, 1);
});

test('juggle rebounds and collision cooldown survives repeated pointer launch', () => {
  const p = buildDirectPlay(T); p.start('juggle');
  const hit = () => { p.launch({ x: hand.x, y: hand.y, vx: 0, vy: 0 }); p.update(1 / 240, { rightHand: hand }); };
  hit(); assert.equal(p.snapshot().score, 1); assert.ok(p.snapshot().velocity.y > 0);
  for (let i = 0; i < 30; i++) hit();
  assert.equal(p.snapshot().score, 1);
  frames(p, 40); hit(); assert.equal(p.snapshot().score, 2);
  p.start('juggle'); frames(p, 400, { leftHand: hand });
  assert.ok(p.snapshot().score >= 2, 'automatic practice allows repeated juggling');
});

test('kick uses foot only and produces an upward sideways rebound', () => {
  const p = buildDirectPlay(T); p.start('kick');
  p.launch({ x: foot.x, y: foot.y, vx: 0, vy: -.1 });
  p.update(1 / 240, { leftHand: foot }); assert.equal(p.snapshot().score, 0);
  p.update(1 / 240, { foot });
  assert.equal(p.snapshot().score, 1); assert.equal(p.snapshot().lastContact, 'foot');
  assert.ok(p.snapshot().velocity.x > 0 && p.snapshot().velocity.y > 0);
  p.start('kick'); frames(p, 100, { foot }); assert.ok(p.snapshot().score >= 1);
});

test('world targets respect translated rotated scaled parent', () => {
  const parent = new T.Group(), p = buildDirectPlay(T);
  parent.position.set(2, 3, -1); parent.rotation.z = .3; parent.scale.setScalar(2);
  parent.add(p.group); parent.updateMatrixWorld(true);
  const world = p.group.localToWorld(hand.clone());
  p.start('catch'); p.launch({ x: hand.x, y: hand.y });
  p.update(1 / 240, { rightHand: world });
  assert.equal(p.snapshot().held, 'rightHand'); near(p.snapshot().position.y, hand.y);
});

test('reduced motion freezes flight and scoring without elapsed debt', () => {
  const practice = buildDirectPlay(T); practice.start('juggle');
  const initial = practice.snapshot().position;
  practice.update(.1, { leftHand: hand, reducedMotion: true });
  assert.deepEqual(practice.snapshot().position, initial);
  assert.equal(practice.snapshot().score, 0);
  const p = buildDirectPlay(T); p.start('catch'); p.launch({ x: 0, y: hand.y });
  const before = p.snapshot();
  frames(p, 100, { leftHand: hand, reducedMotion: true });
  assert.deepEqual(p.snapshot().position, before.position);
  assert.equal(p.snapshot().elapsed, 0); assert.equal(p.snapshot().score, 0);
  near(p.group.children[0].rotation.z, 0);
  p.update(1 / 240, { leftHand: hand }); assert.equal(p.snapshot().score, 1);
});

test('bounded invalid dt and inputs; long frames have bounded work', () => {
  const p = buildDirectPlay(T); p.start('kick');
  p.launch({ x: Infinity, y: NaN, vx: 1e20, vy: -1e20 });
  for (const dt of [NaN, Infinity, -1, 0]) p.update(dt);
  assert.equal(p.snapshot().elapsed, 0);
  p.update(999); near(p.snapshot().elapsed, .1);
  frames(p, 4000, { foot: new T.Vector3(NaN, 0, 0) });
  const s = p.snapshot(); assert.ok(Math.abs(s.position.x) <= H * 1.4);
  assert.ok(s.position.y >= s.radius && s.position.y <= H * 1.8);
  assert.equal(s.score, 0);
  const q = buildDirectPlay(T); q.start('catch');
  q.launch({ x: 0, y: hand.y });
  q.update(1 / 240, { leftHand: new T.Vector3(0, hand.y, 1) });
  assert.equal(q.snapshot().score, 0, 'depth-separated limb cannot catch');
});

test('stop resets, restart is clean, dispose is idempotent', () => {
  const p = buildDirectPlay(T, { onScore() { throw Error('storage offline'); } });
  p.start('catch'); p.launch({ x: 0, y: hand.y }); p.update(1 / 240, { leftHand: hand });
  assert.equal(p.snapshot().score, 1);
  p.stop(); const stopped = p.snapshot(); frames(p, 10, { leftHand: hand });
  assert.deepEqual(p.snapshot(), stopped); assert.equal(stopped.score, 0);
  assert.equal(stopped.active, false); assert.equal(stopped.held, null);
  assert.equal(p.group.visible, false); assert.equal(p.launch({}), false);
  p.start('juggle'); assert.equal(p.snapshot().score, 0); assert.equal(p.group.visible, true);
  const parent = new T.Group(); parent.add(p.group);
  let resources = 0;
  p.group.children[0].geometry.addEventListener('dispose', () => resources++);
  p.group.children[0].material.addEventListener('dispose', () => resources++);
  p.dispose(); p.dispose(); assert.equal(resources, 2); assert.equal(p.group.parent, null);
  assert.equal(p.start('catch'), false);
});

console.log(JSON.stringify({ schema: 1, passed, failed: 0 }));
