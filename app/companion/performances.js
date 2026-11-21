import { TARGET_HEIGHT_M as H } from './glb-adapter.js';

const NAMES = Object.freeze(['rope_swing', 'magnetic_boots', 'parachute', 'hoverboard',
  'ladder', 'balance', 'swim', 'portal', 'shield', 'wings', 'spacecraft', 'armor',
  'hologram', 'cloak', 'magic', 'music', 'draw']);
const DURATION = 8, MAX_POINTS = 512;
const ACTIONS = { rope_swing: 'ledge_hang', magnetic_boots: 'wander', parachute: 'fly',
  hoverboard: 'weight_shift', ladder: 'stretch', balance: 'weight_shift', swim: 'fly',
  portal: 'curious', shield: 'salute', wings: 'fly', spacecraft: 'sit', armor: 'salute',
  hologram: 'hologram', cloak: 'ponder', magic: 'wave', music: 'dance', draw: 'hologram' };
const clamp = (x, a, b) => Math.max(a, Math.min(b, x));
const neutral = () => ({ action: 'idle', position: [0, 0, 0], rotation: [0, 0, 0], scale: 1, opacity: 1 });

/** No renderer, skeleton, timers, audio or storage. Mount group BESIDE the robot
 * under the same .30m frame, not inside the root receiving pose(). Apply the
 * returned pose as an offset from the robot's rest transform, never cumulatively.
 * now is monotonic milliseconds; dt is seconds (bounded fallback for invalid now).
 * Update from the existing render loop. Music is a visual instrument performance.
 */
export function buildPerformances(T) {
  const group = new T.Group(); group.name = 'performances'; group.visible = false;
  const geometries = new Set(), materials = new Set(), cache = new Map();
  let active = false, disposed = false, name = null, startTime = 0, elapsed = 0;
  let reduced = false, currentPose = neutral(), current = null, pointCount = 0;
  let drawing = [];
  let strokeGeometry = null, strokeBuffer = null;
  function material(color, opacity = 1, wireframe = false) {
    const m = new T.MeshStandardMaterial({ color, metalness: .25, roughness: .5,
      transparent: opacity < 1, opacity, depthWrite: opacity === 1,
      side: T.DoubleSide, wireframe });
    materials.add(m); return m;
  }
  const metal = material(0x34465e), accent = material(0x3edcf5), gold = material(0xffb84d);
  const fabric = material(0x735df2), glass = material(0x5de7ff, .3), ghost = material(0x67b8ff, .4, true);
  function mesh(parent, geometry, mat, pos = [0, 0, 0], label = 'prop') {
    geometries.add(geometry);
    const m = new T.Mesh(geometry, mat); m.name = label; m.position.set(...pos); parent.add(m); return m;
  }
  function box(parent, size, pos, mat = metal, label) {
    return mesh(parent, new T.BoxGeometry(...size.map(n => n * H)), mat, pos.map(n => n * H), label);
  }
  function sphere(parent, radius, pos, mat = accent, label) {
    return mesh(parent, new T.SphereGeometry(radius * H, 20, 12), mat, pos.map(n => n * H), label);
  }
  function ring(parent, radius, pos, mat = accent, label) {
    return mesh(parent, new T.TorusGeometry(radius * H, H * .018, 6, 40), mat, pos.map(n => n * H), label);
  }
  function rod(parent, a, b, radius = .012, mat = metal, label = 'strut') {
    const av = new T.Vector3(...a.map(n => n * H)), bv = new T.Vector3(...b.map(n => n * H));
    const direction = bv.clone().sub(av);
    const m = mesh(parent, new T.CylinderGeometry(radius * H, radius * H, direction.length(), 8), mat,
      av.clone().add(bv).multiplyScalar(.5).toArray(), label);
    m.quaternion.setFromUnitVectors(new T.Vector3(0, 1, 0), direction.normalize()); return m;
  }
  function make(mode) {
    const root = new T.Group(), carried = new T.Group(), stage = new T.Group();
    root.name = `performance:${mode}`; carried.name = 'carried-props'; stage.name = 'stage-props';
    root.add(stage, carried); root.visible = false; group.add(root);
    const parts = { root, carried, stage };
    if (mode === 'shield') {
      const shield = sphere(carried, .36, [0, .58, .2], glass, 'shield-dome');
      shield.scale.set(1, 1.2, .55);
      parts.ring = ring(carried, .36, [0, .58, .25], accent, 'shield-rim');
      for (const x of [-.18, 0, .18]) box(carried, [.018, .35, .015], [x, .58, .4], accent, 'shield-grid');
    } else if (mode === 'wings') {
      parts.wings = [];
      for (const side of [-1, 1]) {
        const wing = new T.Group(); wing.position.set(side * H * .15, H * .65, -H * .09);
        carried.add(wing); parts.wings.push(wing);
        for (let i = 0; i < 5; i++) {
          const feather = box(wing, [.36 - i * .035, .065, .025], [side * (.16 + i * .03), -i * .055, 0], i % 2 ? fabric : accent, 'wing-feather');
          feather.rotation.z = side * (.25 + i * .1);
        }
      }
    } else if (mode === 'spacecraft') {
      const hull = sphere(carried, .52, [0, .02, 0], metal, 'spacecraft-hull'); hull.scale.set(1, .22, .7);
      const cockpit = sphere(carried, .29, [0, .15, 0], glass, 'cockpit'); cockpit.scale.set(1, .8, 1);
      parts.ring = ring(carried, .43, [0, .03, 0], accent, 'engine-ring'); parts.ring.rotation.x = Math.PI / 2;
      for (const side of [-1, 1]) {
        box(carried, [.15, .08, .4], [side * .43, 0, -.05], fabric, 'engine-pod');
        const flame = mesh(carried, new T.ConeGeometry(H * .055, H * .25, 10), gold,
          [side * H * .43, 0, -H * .35], 'engine-plume'); flame.rotation.x = -Math.PI / 2;
      }
    } else if (mode === 'hoverboard') {
      box(carried, [.8, .055, .28], [0, -.04, 0], fabric, 'hoverboard-deck');
      for (const side of [-1, 1]) {
        const engine = ring(carried, .1, [side * .28, -.1, 0], accent, 'hover-engine'); engine.rotation.x = Math.PI / 2;
      }
    } else if (mode === 'parachute') {
      mesh(carried, new T.SphereGeometry(H * .65, 24, 12, 0, Math.PI * 2, 0, Math.PI / 2), fabric,
        [0, H * 1.35, 0], 'parachute-canopy');
      for (const x of [-1, 1]) for (const z of [-1, 1])
        rod(carried, [x * .44, 1.35, z * .44], [x * .14, .58, z * .07], .006, accent, 'parachute-cord');
    } else if (mode === 'rope_swing') {
      parts.rope = rod(stage, [0, 1.65, 0], [0, .8, 0], .012, gold, 'swing-rope');
      sphere(stage, .06, [0, 1.65, 0], metal, 'rope-anchor');
      box(carried, [.43, .04, .2], [0, .29, 0], fabric, 'swing-seat');
    } else if (mode === 'magnetic_boots') {
      for (const side of [-1, 1]) {
        box(carried, [.16, .1, .25], [side * .12, .04, .035], metal, 'magnetic-boot');
        box(carried, [.17, .018, .26], [side * .12, -.02, .035], accent, 'magnet-sole');
      }
      box(stage, [1.4, .035, .55], [0, -.06, 0], metal, 'magnetic-track');
      for (let i = -3; i <= 3; i++) box(stage, [.015, .005, .55], [i * .2, -.04, 0], accent, 'track-strip');
    } else if (mode === 'ladder') {
      for (const x of [-.26, .26]) rod(stage, [x, 0, -.1], [x, 1.6, -.1], .025, metal, 'ladder-rail');
      for (let i = 0; i < 9; i++) rod(stage, [-.26, i * .18, -.1], [.26, i * .18, -.1], .018, gold, 'ladder-rung');
    } else if (mode === 'balance') {
      box(stage, [1.5, .045, .07], [0, -.025, 0], gold, 'balance-beam');
      for (const x of [-.6, .6]) box(stage, [.08, .24, .3], [x, -.16, 0], metal, 'beam-support');
      rod(carried, [-.65, .6, .04], [.65, .6, .04], .012, accent, 'balance-pole');
    } else if (mode === 'swim') {
      for (const side of [-1, 1]) box(carried, [.18, .035, .34], [side * .12, .015, .12], fabric, 'swim-flipper');
      parts.bubbles = [];
      for (let i = 0; i < 6; i++) parts.bubbles.push(sphere(stage, .025 + i * .004,
        [.35 + (i % 2) * .1, .1 + i * .15, .05], glass, 'water-bubble'));
    } else if (mode === 'portal') {
      parts.ring = ring(stage, .65, [0, .55, -.12], fabric, 'portal-rim');
      mesh(stage, new T.CircleGeometry(H * .63, 40), ghost, [0, H * .55, -H * .13], 'portal-field');
    } else if (mode === 'armor') {
      box(carried, [.42, .28, .2], [0, .58, 0], metal, 'armor-breastplate');
      box(carried, [.22, .025, .025], [0, .65, .115], accent, 'armor-visor');
      for (const side of [-1, 1]) sphere(carried, .12, [side * .27, .68, 0], gold, 'armor-pauldron');
    } else if (mode === 'hologram' || mode === 'draw') {
      box(stage, [.7, .62, .012], [.65, .65, .13], glass, 'holographic-board');
      for (const x of [.3, 1]) rod(stage, [x, .34, .14], [x, .96, .14], .008, accent, 'board-edge');
      if (mode === 'draw') {
        strokeBuffer = new Float32Array(MAX_POINTS * 3); strokeGeometry = new T.BufferGeometry();
        strokeGeometry.setAttribute('position', new T.BufferAttribute(strokeBuffer, 3));
        strokeGeometry.setDrawRange(0, 0); geometries.add(strokeGeometry);
        const ink = new T.LineBasicMaterial({ color: 0xffd56b }); materials.add(ink);
        const line = new T.Line(strokeGeometry, ink); line.name = 'user-stroke'; line.frustumCulled = false; stage.add(line);
        syncDrawing();
      } else {
        parts.ring = ring(stage, .2, [.65, .65, .15], accent, 'hologram-orbit');
        parts.model = mesh(stage, new T.IcosahedronGeometry(H * .12), ghost,
          [H * .65, H * .65, H * .16], 'hologram-model');
      }
    } else if (mode === 'cloak') {
      mesh(carried, new T.ConeGeometry(H * .27, H * .65, 24, 1, true), fabric,
        [0, H * .42, -H * .08], 'cloak-mantle');
      sphere(carried, .2, [0, .84, -.03], glass, 'cloak-hood');
    } else if (mode === 'magic') {
      rod(carried, [.22, .52, .1], [.4, .85, .1], .012, gold, 'magic-wand');
      parts.orb = sphere(carried, .08, [.4, .85, .1], accent, 'magic-orb');
      parts.ring = ring(stage, .5, [0, .01, 0], fabric, 'magic-circle'); parts.ring.rotation.x = Math.PI / 2;
    } else if (mode === 'music') {
      box(carried, [.5, .08, .23], [0, .45, .18], metal, 'keyboard');
      for (let i = 0; i < 10; i++) box(carried, [.039, .016, .16], [-.22 + i * .048, .5, .2], i % 3 ? accent : gold, 'instrument-key');
      parts.notes = [];
      for (let i = 0; i < 3; i++) {
        const note = new T.Group(); stage.add(note); note.position.set((.4 + i * .15) * H, (.6 + i * .14) * H, 0);
        sphere(note, .035, [0, 0, 0], gold, 'note-head'); rod(note, [.025, 0, 0], [.025, .14, 0], .008, gold, 'note-stem');
        parts.notes.push(note);
      }
    }
    cache.set(mode, parts); return parts;
  }
  function apply() {
    const t = reduced || name === 'draw' ? 0 : elapsed, phase = t * Math.PI * 2 / DURATION;
    const envelope = Math.sin(Math.PI * t / DURATION) ** 2;
    const sway = Math.sin(t * 2.2), p = neutral(); p.action = ACTIONS[name];
    if (!reduced) {
      if (name === 'rope_swing') {
        const angle = .45 * sway * envelope, length = H * 1.65;
        p.position = [length * Math.sin(angle), length * (1 - Math.cos(angle)), 0]; p.rotation[2] = angle;
        // Rope's lower endpoint follows the same rigid rotation as robot y=.8H.
        const a = new T.Vector3(0, H * 1.65, 0);
        const b = new T.Vector3(-H * .8 * Math.sin(angle) + p.position[0], H * .8 * Math.cos(angle) + p.position[1], 0);
        current.rope.position.copy(a).add(b).multiplyScalar(.5);
        current.rope.scale.y = a.distanceTo(b) / (H * .85);
        current.rope.quaternion.setFromUnitVectors(new T.Vector3(0, 1, 0), a.sub(b).normalize());
      } else if (['wings', 'spacecraft', 'hoverboard', 'parachute'].includes(name)) {
        p.position = [H * .3 * Math.sin(phase) * envelope,
          H * (name === 'parachute' ? .65 : .3) * envelope, 0];
        p.rotation[2] = -.08 * sway * envelope;
      } else if (name === 'ladder') p.position[1] = H * .65 * envelope;
      else if (name === 'balance') { p.position[0] = H * .35 * Math.sin(phase); p.rotation[2] = .12 * sway * envelope; }
      else if (name === 'magnetic_boots') p.position[0] = H * .35 * Math.sin(phase) * envelope;
      else if (name === 'swim') { p.position[1] = H * .25 * envelope; p.rotation[0] = -Math.PI * .4 * envelope; }
      else if (name === 'portal') { p.position[2] = -H * .3 * envelope; p.scale = 1 - .65 * envelope; p.opacity = 1 - .85 * envelope; }
      else if (name === 'cloak') p.opacity = 1 - .7 * envelope;
    } else {
      p.action = 'idle';
      if (current.rope) {
        current.rope.position.set(0, H * 1.225, 0);
        current.rope.quaternion.identity(); current.rope.scale.setScalar(1);
      }
    }
    currentPose = p;
    current.carried.position.set(...p.position); current.carried.rotation.set(...p.rotation); current.carried.scale.setScalar(p.scale);
    if (current.wings) current.wings.forEach((wing, i) => { wing.rotation.y = (i ? 1 : -1) * (.3 + (reduced ? 0 : .4 * Math.sin(t * 5))); });
    if (current.ring) current.ring.rotation.z = reduced ? 0 : t * .7;
    if (current.model) current.model.rotation.set(reduced ? 0 : t * .4, reduced ? 0 : t * .8, 0);
    if (current.orb) current.orb.scale.setScalar(reduced ? 1 : 1 + .2 * Math.sin(t * 4));
    if (current.bubbles) current.bubbles.forEach((b, i) => { b.position.y = H * (.1 + ((i * .15 + (reduced ? 0 : t * .15)) % 1)); });
    if (current.notes) current.notes.forEach((note, i) => { note.position.y = H * (.6 + i * .14 + (reduced ? 0 : .08 * Math.sin(t * 3 + i))); });
  }
  function stop() {
    if (current) current.root.visible = false;
    group.visible = active = false; name = null; elapsed = 0; reduced = false; currentPose = neutral(); current = null;
  }
  function start(next, now = 0) {
    if (disposed) return false;
    if (!NAMES.includes(next)) throw new RangeError(`Unknown performance: ${next}`);
    stop(); name = next; current = cache.get(next) || make(next);
    startTime = Number.isFinite(now) ? now : 0;
    group.visible = active = current.root.visible = true; apply(); return true;
  }
  function update(now, dt, reducedMotion = false) {
    if (!active || disposed) return;
    elapsed = Number.isFinite(now) ? Math.max(elapsed, (now - startTime) / 1000) :
      elapsed + (Number.isFinite(dt) ? clamp(dt, 0, .1) : 0);
    if (name !== 'draw' && elapsed >= DURATION) { stop(); return; }
    reduced = !!reducedMotion; apply();
  }
  function pose() { return { ...currentPose, position: [...currentPose.position], rotation: [...currentPose.rotation] }; }
  function writePoint(index, x, y) {
    strokeBuffer[index * 3] = H * (.3 + x * .7);
    strokeBuffer[index * 3 + 1] = H * (.34 + y * .62);
    strokeBuffer[index * 3 + 2] = H * .145;
  }
  function syncDrawing() {
    if (!strokeGeometry) return;
    strokeBuffer.fill(0);
    drawing.forEach(([x, y], index) => writePoint(index, x, y));
    strokeGeometry.attributes.position.needsUpdate = true;
    strokeGeometry.setDrawRange(0, pointCount);
  }
  function getDrawing() { return drawing.map(([x, y]) => [x, y]); }
  function setDrawing(points) {
    // Validate the entire normalized payload before changing data or geometry.
    // Restore is allowed before the draw prop is built, while stopped, or while
    // another performance is active. No GPU allocation is needed for restore.
    if (disposed || !Array.isArray(points) || points.length > MAX_POINTS) return false;
    const next = [];
    for (const pair of points) {
      if (!Array.isArray(pair) || pair.length !== 2 ||
          !Number.isFinite(pair[0]) || !Number.isFinite(pair[1]) ||
          pair[0] < 0 || pair[0] > 1 || pair[1] < 0 || pair[1] > 1) return false;
      next.push([pair[0], pair[1]]);
    }
    drawing = next; pointCount = next.length; syncDrawing(); return true;
  }
  function clearDrawing() {
    if (disposed) return false;
    drawing = []; pointCount = 0; syncDrawing(); return true;
  }
  function addPoint(x, y) {
    // Normalized board coordinates, +y up. One bounded user stroke is kept in
    // memory across performances/stops. Parent owns durable board persistence.
    if (disposed || !active || name !== 'draw' || !Number.isFinite(x) || !Number.isFinite(y) || pointCount >= MAX_POINTS) return false;
    x = clamp(x, 0, 1); y = clamp(y, 0, 1);
    drawing.push([x, y]); writePoint(pointCount++, x, y);
    strokeGeometry.attributes.position.needsUpdate = true; strokeGeometry.setDrawRange(0, pointCount);
    return true;
  }
  function dispose() {
    if (disposed) return;
    stop(); disposed = true;
    geometries.forEach(g => g.dispose()); materials.forEach(m => m.dispose());
    geometries.clear(); materials.clear(); cache.clear(); group.clear(); group.removeFromParent();
    strokeGeometry = strokeBuffer = null;
    drawing = []; pointCount = 0;
  }
  return { group, names: NAMES, start, update, pose, addPoint, getDrawing, setDrawing, clearDrawing, stop, dispose,
    snapshot: () => ({ active, name, elapsed, duration: name === 'draw' ? null : DURATION, reducedMotion: reduced,
      drawingPoints: pointCount, audio: false, disposed, pose: pose() }) };
}
