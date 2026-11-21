import assert from 'node:assert/strict';
import * as T from '../../app/vendor/three/three.module.min.js';
import { buildPerformances } from '../../app/companion/performances.js';
import { TARGET_HEIGHT_M as H } from '../../app/companion/glb-adapter.js';
import { CLIPS } from '../../app/companion/animation.js';

let passed = 0;
function test(name, fn) { fn(); passed++; console.log(name); }
// BufferGeometry stores Float32 positions; allow its rounding at metre scale.
const close = (a, b) => assert.ok(Math.abs(a - b) < 1e-7, `${a} != ${b}`);
const find = (p, label) => p.group.getObjectByName(label);
const visibleCount = p => p.group.children.filter(c => c.visible).length;
const expected = ['rope_swing', 'magnetic_boots', 'parachute', 'hoverboard', 'ladder', 'balance',
  'swim', 'portal', 'shield', 'wings', 'spacecraft', 'armor', 'hologram', 'cloak', 'magic', 'music', 'draw'];

test('all 17 modes have real finite Three geometry, valid existing pose actions, correct lifetimes', () => {
  const p = buildPerformances(T);
  assert.deepEqual(p.names, expected); assert.equal(p.group.visible, false);
  for (const name of expected) {
    assert.equal(p.start(name, 1000), true);
    assert.equal(visibleCount(p), 1); assert.equal(p.group.visible, true);
    let meshes = 0;
    p.group.getObjectByName(`performance:${name}`).traverse(o => {
      assert.equal(o.isSkinnedMesh || o.isBone || false, false, 'never clones a robot/skeleton');
      if (!o.isMesh) return;
      meshes++; assert.ok(o.geometry.isBufferGeometry);
      assert.ok(o.material.isMaterial);
      const a = o.geometry.attributes.position.array;
      for (const n of a) assert.ok(Number.isFinite(n));
    });
    assert.ok(meshes > 0, name);
    for (const ms of [1000, 1234, 2800, 5000, 8999]) {
      p.update(ms, .016, false);
      const pose = p.pose(); assert.ok(CLIPS[pose.action], `${name} uses existing clip`);
      for (const n of [...pose.position, ...pose.rotation, pose.scale, pose.opacity]) assert.ok(Number.isFinite(n), name);
      assert.ok(pose.position.every(n => Math.abs(n) <= H));
      assert.ok(pose.scale > 0 && pose.scale <= 1);
    }
    p.update(9000, .016, false);
    if (name === 'draw') {
      assert.equal(p.snapshot().active, true); assert.equal(p.snapshot().duration, null); p.stop();
    }
    assert.equal(p.snapshot().active, false); assert.equal(p.group.visible, false);
    assert.equal(visibleCount(p), 0);
    assert.deepEqual(p.pose(), { action: 'idle', position: [0, 0, 0], rotation: [0, 0, 0], scale: 1, opacity: 1 });
  }
  p.dispose();
});

test('six core modes visibly mount their distinct props and follow returned root offsets', () => {
  const p = buildPerformances(T);
  const labels = { shield: 'shield-dome', wings: 'wing-feather', spacecraft: 'spacecraft-hull',
    hoverboard: 'hoverboard-deck', parachute: 'parachute-canopy', rope_swing: 'swing-seat' };
  for (const [name, label] of Object.entries(labels)) {
    p.start(name, 0); p.update(2200, 1 / 60);
    const prop = find(p, label); assert.ok(prop?.isMesh, name);
    assert.equal(prop.parent.parent.visible || prop.parent.parent.parent?.visible, true);
    const carried = p.group.getObjectByName(`performance:${name}`).getObjectByName('carried-props');
    assert.deepEqual(carried.position.toArray(), p.pose().position);
    assert.deepEqual(carried.rotation.toArray().slice(0, 3), p.pose().rotation);
    close(carried.scale.x, p.pose().scale);
    const bounds = new T.Box3().setFromObject(p.group.getObjectByName(`performance:${name}`));
    assert.ok(bounds.getSize(new T.Vector3()).length() > H * .2, name);
  }
  p.dispose();
});

test('rope remains anchored and reaches moving robot hand height', () => {
  const p = buildPerformances(T); p.start('rope_swing', 0);
  for (const ms of [100, 1200, 3000, 6000]) {
    p.update(ms, .016);
    const rope = find(p, 'swing-rope'); p.group.updateMatrixWorld(true);
    const length = H * .85;
    const upper = rope.localToWorld(new T.Vector3(0, length / 2, 0));
    const lower = rope.localToWorld(new T.Vector3(0, -length / 2, 0));
    close(upper.x, 0); close(upper.y, H * 1.65);
    const root = p.pose(); const hand = new T.Vector3(0, H * .8, 0);
    hand.applyEuler(new T.Euler(...root.rotation)).add(new T.Vector3(...root.position));
    close(lower.x, hand.x); close(lower.y, hand.y); close(upper.distanceTo(lower), length);
  }
  p.dispose();
});

test('wings flap, hologram rotates, bubbles and notes move, no audio claimed', () => {
  const p = buildPerformances(T);
  p.start('wings', 0); p.update(1000, .016);
  const wing = find(p, 'wing-feather').parent, before = wing.rotation.y;
  p.update(1400, .016); assert.notEqual(wing.rotation.y, before);
  for (const [mode, label] of [['hologram', 'hologram-model'], ['swim', 'water-bubble'], ['music', 'note-head']]) {
    p.start(mode, 0); p.update(1000, .016); p.group.updateMatrixWorld(true);
    const a = find(p, label).matrixWorld.clone();
    p.update(1300, .016); p.group.updateMatrixWorld(true);
    assert.notDeepEqual(find(p, label).matrixWorld, a);
  }
  assert.equal(p.snapshot().audio, false); p.dispose();
});

test('reduced motion returns neutral pose and stable effects then expires normally', () => {
  const p = buildPerformances(T);
  for (const mode of expected) {
    p.start(mode, 0); p.update(1500, .016);
    p.update(2000, .016, true); p.group.updateMatrixWorld(true);
    const initial = [];
    p.group.getObjectByName(`performance:${mode}`).traverse(o => initial.push(o.matrixWorld.toArray()));
    const pose = p.pose();
    assert.deepEqual(pose, { action: 'idle', position: [0, 0, 0], rotation: [0, 0, 0], scale: 1, opacity: 1 });
    p.update(5000, .016, true); p.group.updateMatrixWorld(true);
    const later = []; p.group.getObjectByName(`performance:${mode}`).traverse(o => later.push(o.matrixWorld.toArray()));
    assert.deepEqual(initial, later, mode); assert.equal(p.snapshot().reducedMotion, true);
    p.update(8000, .016, true); assert.equal(p.snapshot().active, mode === 'draw'); p.stop();
  }
  p.dispose();
});

test('holographic drawing consumes actual bounded input and survives mode changes in memory', () => {
  const p = buildPerformances(T);
  assert.equal(p.addPoint(.1, .2), false); p.start('draw', 0);
  assert.equal(p.addPoint(NaN, 1), false);
  assert.equal(p.addPoint(0, 0), true); assert.equal(p.addPoint(1, 1), true);
  const line = find(p, 'user-stroke'); assert.ok(line.isLine);
  assert.equal(line.geometry.drawRange.count, 2);
  const position = line.geometry.attributes.position;
  assert.ok(position.version > 0);
  close(position.getX(0), H * .3); close(position.getY(0), H * .34);
  close(position.getX(1), H); close(position.getY(1), H * .96);
  p.stop(); p.start('shield', 100); assert.equal(p.addPoint(0, 0), false);
  p.start('draw', 200); assert.equal(p.snapshot().drawingPoints, 2);
  assert.equal(find(p, 'user-stroke'), line);
  for (let i = 2; i < 512; i++) assert.equal(p.addPoint(i / 512, i / 512), true);
  assert.equal(p.addPoint(0, 0), false); assert.equal(line.geometry.drawRange.count, 512);
  p.dispose(); assert.equal(p.addPoint(.5, .5), false);
});

test('timelines are deterministic, monotonic, restartable and interruptible', () => {
  const a = buildPerformances(T), b = buildPerformances(T);
  a.start('parachute', 100); b.start('parachute', 100);
  for (let ms = 200; ms <= 2100; ms += 100) a.update(ms, .1);
  b.update(2100, 2); assert.deepEqual(a.snapshot(), b.snapshot());
  const before = a.pose(); a.update(0, -.1); assert.deepEqual(a.pose(), before);
  a.update(NaN, Infinity); assert.deepEqual(a.pose(), before);
  a.update(NaN, 999); close(a.snapshot().elapsed, 2.1);
  a.stop(); const stopped = a.snapshot(); a.update(5000, 1); assert.deepEqual(a.snapshot(), stopped);
  a.start('shield', 3000); assert.equal(a.snapshot().elapsed, 0); assert.equal(visibleCount(a), 1);
  a.start('wings', 3100); assert.equal(visibleCount(a), 1); assert.equal(find(a, 'performance:shield').visible, false);
  assert.throws(() => a.start('missing', 3200), RangeError); assert.equal(a.snapshot().name, 'wings');
  const pose = a.pose(); pose.position[0] = 99; assert.notEqual(a.pose().position[0], 99);
  a.update(1e10, .016); assert.equal(a.snapshot().active, false);
  a.dispose(); b.dispose();
});

test('drawing roundtrip is exact, detached and restores before lazy geometry exists', () => {
  const p = buildPerformances(T);
  const input = [[0, 0], [.123456789, .987654321], [1, 1]];
  const saved = structuredClone(input);
  assert.deepEqual(p.getDrawing(), []); assert.equal(p.clearDrawing(), true);
  assert.equal(p.setDrawing(input), true);
  assert.equal(p.group.children.length, 0, 'restore does not build props or a second character');
  input[0][0] = .5; assert.deepEqual(p.getDrawing(), saved);
  const exported = p.getDrawing(); exported[1][0] = 0; exported.push([.1, .2]);
  assert.deepEqual(p.getDrawing(), saved);
  assert.equal(p.snapshot().drawingPoints, 3);
  assert.equal(Object.values(p.snapshot()).some(Array.isArray), false, 'snapshot does not expose drawing payload');
  p.start('draw', 0);
  const g = find(p, 'user-stroke').geometry;
  assert.equal(g.drawRange.count, 3);
  close(g.attributes.position.getX(1), H * (.3 + saved[1][0] * .7));
  close(g.attributes.position.getY(1), H * (.34 + saved[1][1] * .62));
  assert.equal(p.addPoint(-1, 2), true);
  assert.deepEqual(p.getDrawing().at(-1), [0, 1], 'pointer additions retain bounded clamping');
  p.stop(); p.start('shield', 1);
  assert.equal(p.setDrawing([[.5, .25]]), true);
  p.start('draw', 2); assert.equal(g.drawRange.count, 1);
  assert.equal(p.clearDrawing(), true); assert.equal(g.drawRange.count, 0);
  assert.ok(g.attributes.position.array.every(n => n === 0));
  assert.deepEqual(p.getDrawing(), []); assert.equal(p.snapshot().drawingPoints, 0);
  assert.equal(p.addPoint(.1, .2), true); assert.equal(g.drawRange.count, 1);
  p.dispose(); assert.deepEqual(p.getDrawing(), []);
  assert.equal(p.setDrawing([]), false); assert.equal(p.clearDrawing(), false);
});

test('malformed or oversized drawing payloads never mutate data or geometry', () => {
  const p = buildPerformances(T); p.start('draw', 0); p.addPoint(.1, .2);
  const g = find(p, 'user-stroke').geometry;
  const before = p.getDrawing(), buffer = g.attributes.position.array.slice();
  const version = g.attributes.position.version;
  const bad = [null, {}, 'drawing', new Float32Array([0, 1]), [null], [[0]], [[0, 1, 2]],
    [[NaN, 0]], [[0, Infinity]], [[0, -Infinity]], [['0', 1]], [[false, 1]],
    [[-0.01, 0]], [[0, 1.01]], new Array(2), [new Array(2)],
    [[.2, .3], [.4, NaN]], Array.from({ length: 513 }, () => [0, 0])];
  for (const payload of bad) {
    assert.equal(p.setDrawing(payload), false);
    assert.deepEqual(p.getDrawing(), before);
    assert.deepEqual(g.attributes.position.array, buffer);
    assert.equal(g.attributes.position.version, version); assert.equal(g.drawRange.count, 1);
  }
  const full = Array.from({ length: 512 }, (_, i) => [i / 511, 1 - i / 511]);
  assert.equal(p.setDrawing(full), true); assert.deepEqual(p.getDrawing(), full);
  assert.equal(g.drawRange.count, 512); assert.equal(p.addPoint(0, 0), false);
  assert.equal(p.setDrawing([]), true); assert.equal(g.drawRange.count, 0);
  p.dispose();
});

test('drawing stays open across long frames and reduced motion until explicitly interrupted', () => {
  const p = buildPerformances(T); p.start('draw', 1234); p.addPoint(.2, .3);
  for (const now of [9234, 61234, 1e10]) {
    p.update(now, .016, false); assert.equal(p.snapshot().active, true);
    assert.equal(p.snapshot().duration, null); assert.equal(p.group.visible, true);
    p.update(now + 1, .016, true); assert.equal(p.snapshot().active, true);
    assert.deepEqual(p.pose().position, [0, 0, 0]);
  }
  p.stop(); assert.equal(p.group.visible, false);
  assert.deepEqual(p.getDrawing(), [[.2, .3]]);
  p.start('draw', 0); p.start('wings', 100);
  assert.equal(find(p, 'performance:draw').visible, false);
  assert.equal(p.snapshot().duration, 8); p.update(8100, .016); assert.equal(p.snapshot().active, false);
  p.dispose();
});

test('every cached resource disposed exactly once; restarting reuses geometry', () => {
  const p = buildPerformances(T), parent = new T.Group(); parent.add(p.group);
  for (const mode of p.names) p.start(mode, 0);
  const geometries = new Set(), materials = new Set();
  p.group.traverse(o => { if (o.geometry) geometries.add(o.geometry); if (o.material) materials.add(o.material); });
  const children = p.group.children.length;
  for (let i = 0; i < 100; i++) { p.start('shield', 0); p.stop(); }
  assert.equal(p.group.children.length, children);
  let geometryDisposals = 0, materialDisposals = 0;
  geometries.forEach(g => g.addEventListener('dispose', () => geometryDisposals++));
  materials.forEach(m => m.addEventListener('dispose', () => materialDisposals++));
  p.dispose(); p.dispose();
  assert.equal(geometryDisposals, geometries.size); assert.equal(materialDisposals, materials.size);
  assert.equal(p.group.parent, null); assert.equal(p.group.children.length, 0);
  assert.equal(p.start('shield', 0), false); assert.equal(p.snapshot().disposed, true);
});

console.log(JSON.stringify({ schema: 1, passed, failed: 0 }));
