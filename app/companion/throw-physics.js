// Pure 2D throw physics for the companion window (Feature #154 — "pick up and throw J.A.R.V.I.S.").
//
// No DOM, no three.js: just the integrator, so it is unit-tested under Node
// (tests/node/test_throw_physics.mjs). companion.html runs this loop and moves
// the real window; the renderer reads the same state to tumble and land the rig.
//
// Units are CSS pixels and seconds. `x, y` is the window's top-left. `bounds`
// already accounts for the window size, so a resting window satisfies
// left <= x <= right and top <= y <= bottom (right = workRight - w, etc.).

export const DEFAULT_PARAMS = {
  gravity: 2800,        // px/s^2 downward
  restitution: 0.52,    // vertical energy kept per floor bounce
  wallRestitution: 0.6, // horizontal energy kept per wall/ceiling bounce
  friction: 0.78,       // horizontal speed kept per floor contact (ground drag)
  airDrag: 0.06,        // fraction of horizontal speed shed per second in the air
  spinPerVx: 0.016,     // launch spin (rad/s) per px/s of horizontal speed
  spinDecay: 0.72,      // spin kept per floor contact
  restSpeed: 48,        // |v| below which a floor contact is "at rest"
};

/** Initial airborne state from a release at (x, y) with velocity (vx, vy) px/s. */
export function launchState(x, y, vx, vy, params = DEFAULT_PARAMS) {
  return { x, y, vx, vy, angle: 0, spin: -vx * params.spinPerVx, resting: false, onFloor: false, bounces: 0 };
}

/** The floor height (top-left y) at horizontal position x. With `bounds.floors`
 *  (one {x0, x1, y} per monitor) he lands on whichever monitor he is over, so a
 *  throw can cross to another display and land on ITS floor. Falls back to
 *  `bounds.bottom` for a single screen. */
export function floorAt(x, bounds) {
  const f = bounds.floors;
  if (!f || !f.length) return bounds.bottom;
  for (const seg of f) if (x >= seg.x0 && x <= seg.x1) return seg.y;
  let best = f[0], bd = Infinity;                    // between monitors: nearest segment
  for (const seg of f) { const d = Math.min(Math.abs(x - seg.x0), Math.abs(x - seg.x1)); if (d < bd) { bd = d; best = seg; } }
  return best.y;
}

/** Advance one step. Returns a NEW state (never mutates the input). */
export function stepThrow(s, dt, bounds, params = DEFAULT_PARAMS) {
  if (s.resting) return s;
  dt = Math.max(0, Math.min(dt, 1 / 30));            // clamp giant frames (tab stalls) so he can't tunnel through a wall
  let { x, y, vx, vy, angle, spin, bounces } = s;

  vy += params.gravity * dt;
  vx *= Math.max(0, 1 - params.airDrag * dt);
  x += vx * dt;
  y += vy * dt;
  angle += spin * dt;

  let onFloor = false;
  if (x <= bounds.left) { x = bounds.left; vx = Math.abs(vx) * params.wallRestitution; spin = -spin * 0.8; }
  else if (x >= bounds.right) { x = bounds.right; vx = -Math.abs(vx) * params.wallRestitution; spin = -spin * 0.8; }
  if (y <= bounds.top) { y = bounds.top; vy = Math.abs(vy) * params.wallRestitution; }
  const floor = floorAt(x, bounds);
  if (y >= floor) {
    y = floor;
    onFloor = true;
    bounces += 1;
    vy = -vy * params.restitution;
    vx *= params.friction;
    spin *= params.spinDecay;
    if (Math.abs(vy) < params.restSpeed) vy = 0;     // too slow to leave the floor again
  }

  let resting = false;
  if (onFloor && vy === 0 && Math.abs(vx) < params.restSpeed) {
    vx = 0; spin = 0; resting = true;
  }
  return { x, y, vx, vy, angle, spin, resting, onFloor, bounces };
}

/** Peak height reached from (y, vy) under gravity — used only by tests/telemetry. */
export function apexHeight(y, vy, gravity = DEFAULT_PARAMS.gravity) {
  return vy >= 0 ? y : y - (vy * vy) / (2 * gravity);
}

/** Speed of a release, from the last samples [{t, x, y}] (ms, px). Uses the span
 *  of roughly the last `windowMs` so a flick registers but a slow drag does not. */
export function releaseVelocity(samples, windowMs = 90) {
  if (!samples || samples.length < 2) return { vx: 0, vy: 0, speed: 0 };
  const last = samples[samples.length - 1];
  let first = samples[0];
  for (let i = samples.length - 1; i >= 0; i--) {
    if (last.t - samples[i].t <= windowMs) first = samples[i]; else break;
  }
  const dt = (last.t - first.t) / 1000;
  if (dt <= 0) return { vx: 0, vy: 0, speed: 0 };
  const vx = (last.x - first.x) / dt, vy = (last.y - first.y) / dt;
  return { vx, vy, speed: Math.hypot(vx, vy) };
}
