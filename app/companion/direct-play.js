import { TARGET_HEIGHT_M } from './glb-adapter.js';

// Attach group beside the normalized robot (same parent/frame). Positions and
// launch velocities are metres and metres/second, +y up. Limb inputs are WORLD
// Vector3s; update their matrices before calling update. No DOM or owned clock.
const H = TARGET_HEIGHT_M, STEP = 1 / 240, GRAVITY = H * 5;
const RADIUS = H * .045, REACH = H * .105, COOLDOWN = .28;
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const finite = (v, fallback = 0) => Number.isFinite(v) ? v : fallback;

export function buildDirectPlay(T, { onScore } = {}) {
  const group = new T.Group();
  group.name = 'direct-play';
  const geometry = new T.SphereGeometry(RADIUS, 20, 12);
  const material = new T.MeshStandardMaterial({ color: 0xffa52b, roughness: .55, metalness: .08 });
  const ball = new T.Mesh(geometry, material);
  ball.name = 'direct-play:ball';
  group.add(ball);
  group.visible = false;
  const position = new T.Vector3(), velocity = new T.Vector3();
  let active = false, disposed = false, kind = null, score = 0;
  let elapsed = 0, accumulator = 0, cooldown = 0, held = null, pending = false;
  let reduced = false, lastContact = null;
  const bounds = { left: -H * 1.4, right: H * 1.4, floor: RADIUS, ceiling: H * 1.8 };

  function sync() { ball.position.copy(position); }
  function reset() {
    velocity.set(0, 0, 0); position.set(0, H * .65, H * .12);
    elapsed = accumulator = cooldown = score = 0;
    held = lastContact = null; pending = false; reduced = false;
    ball.rotation.set(0, 0, 0); sync();
  }
  function launch({ x = 0, y = H * .65, vx = 0, vy = H * 2 } = {}) {
    if (!active || disposed) return false;
    position.set(clamp(finite(x), bounds.left, bounds.right),
      clamp(finite(y, H * .65), bounds.floor, bounds.ceiling), H * .12);
    velocity.set(clamp(finite(vx), -H * 6, H * 6), clamp(finite(vy), -H * 6, H * 6), 0);
    held = null; pending = false; accumulator = 0; sync();
    // Keep the collision cooldown across pointer launches to prevent score spam.
    return true;
  }
  function start(nextKind) {
    if (disposed) return false;
    if (!['catch', 'juggle', 'kick'].includes(nextKind)) throw new RangeError('Unknown direct play kind');
    reset(); kind = nextKind; active = group.visible = true;
    launch({ x: -H * .45, y: H * .65, vx: H * .6, vy: H * 1.5 });
    pending = true; // Aim the practice toss once the first real limb targets arrive.
    return true;
  }
  function localTarget(value) {
    if (!value || ![value.x, value.y, value.z].every(Number.isFinite)) return null;
    const p = group.worldToLocal(new T.Vector3(value.x, value.y, value.z));
    // Reject targets outside the toy's safe play volume.
    return Math.abs(p.x) <= H * 1.4 && p.y >= 0 && p.y <= bounds.ceiling && Math.abs(p.z) <= H ? p : null;
  }
  function award(limb) {
    score++; lastContact = limb;
    // Snapshot is detached so persistence consumers cannot mutate simulation.
    if (typeof onScore === 'function') {
      try { onScore({ kind, score, delta: 1, contact: limb }); } catch { /* storage must not interrupt physics */ }
    }
  }
  function update(dt, { leftHand, rightHand, foot, reducedMotion = false } = {}) {
    if (!active || disposed) return;
    group.updateWorldMatrix(true, false);
    const targets = kind === 'kick' ? [['foot', localTarget(foot)]] :
      [['leftHand', localTarget(leftHand)], ['rightHand', localTarget(rightHand)]];
    reduced = !!reducedMotion;
    // Freeze even the initial toss placement. Resume carries no time debt.
    if (reduced) { accumulator = 0; sync(); return; }
    if (pending) {
      const target = targets.find(([, p]) => p)?.[1];
      if (target) {
        // Ball arrives descending after 0.65s; launch is physically integrated.
        const flight = .65;
        position.copy(target).add(new T.Vector3(-H * .35, H * .3, 0));
        position.x = clamp(position.x, bounds.left, bounds.right);
        position.y = clamp(position.y, bounds.floor, bounds.ceiling);
        velocity.set((target.x - position.x) / flight,
          (target.y - position.y) / flight + .5 * GRAVITY * flight, 0);
        pending = false;
      }
    }
    accumulator += clamp(finite(dt), 0, .1);
    while (accumulator + 1e-12 >= STEP && active) {
      accumulator = Math.max(0, accumulator - STEP); elapsed += STEP;
      cooldown = Math.max(0, cooldown - STEP);
      if (held) {
        const target = targets.find(([name]) => name === held)?.[1];
        if (target) position.copy(target);
        continue;
      }
      velocity.y -= GRAVITY * STEP;
      position.addScaledVector(velocity, STEP);
      if (cooldown === 0) {
        for (const [name, target] of targets) {
          if (!target || position.distanceToSquared(target) > REACH ** 2) continue;
          cooldown = COOLDOWN;
          if (kind === 'catch') { held = name; position.copy(target); velocity.set(0, 0, 0); }
          else {
            position.copy(target); position.y += REACH;
            velocity.set(kind === 'kick' ? (target.x > 0 ? -1 : 1) * H * 1.1 : -target.x * 1.6,
              H * (kind === 'kick' ? 2.4 : 2.1), 0);
          }
          award(name); break;
        }
      }
      if (position.x < bounds.left || position.x > bounds.right) {
        position.x = clamp(position.x, bounds.left, bounds.right); velocity.x *= -.65;
      }
      if (position.y < bounds.floor) {
        position.y = bounds.floor; velocity.y = Math.abs(velocity.y) * .55; velocity.x *= .8;
        if (velocity.y < H * .12) velocity.y = 0;
      } else if (position.y > bounds.ceiling) {
        position.y = bounds.ceiling; velocity.y = -Math.abs(velocity.y) * .55;
      }
      if (!held) ball.rotation.z -= velocity.x * STEP / RADIUS;
    }
    sync();
  }
  function snapshot() {
    return { active, kind, score, elapsed, cooldown, held, reducedMotion: reduced,
      lastContact, position: position.clone(), velocity: velocity.clone(), radius: RADIUS };
  }
  function stop() { active = group.visible = false; kind = null; reset(); }
  function dispose() {
    if (disposed) return;
    stop(); disposed = true; geometry.dispose(); material.dispose(); group.removeFromParent();
  }
  reset();
  return { group, start, launch, update, snapshot, stop, dispose };
}
