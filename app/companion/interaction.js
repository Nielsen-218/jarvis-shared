// Living Presence Engine — the pointer-side interaction model (pure JS, no DOM; Node-tested).
//
// One controller owns what the user is doing WITH him (hovering, pressing, dragging, throwing, using his menu,
// playing, running a skill) and reports it to the desktop host as a position LEASE: while an interaction owns him,
// the host's auto-placement (core/presence.py) waits, so nothing yanks him out of a drag, a throw or an open menu.
// Interaction states sit beside the emotion/mood and animation systems; they don't replace them.

import { releaseVelocity } from "./throw-physics.js";

export const STATES = ["ambient", "curious", "hovered", "engaged", "menu_open", "held", "thrown", "drop_target",
  "playing", "skill_running", "work_focus", "repositioning", "docked", "suspended"];
// While in one of these, he holds the lease (mirrors core/presence.OWNING_STATES).
export const OWNING = new Set(["hovered", "engaged", "menu_open", "held", "thrown", "drop_target", "playing",
  "skill_running", "repositioning"]);
// After an owning state ends, placement waits this long before judging his new spot (a throw needs to settle).
export const SETTLE_MS = { thrown: 3000, held: 2000, repositioning: 1000, menu_open: 1500, playing: 2000,
  skill_running: 1500, drop_target: 1500, engaged: 800, hovered: 600 };
export const LEASE_TTL_MS = 2500;   // renewed about every second while held

export const GESTURE = {
  TAP_SLOP: 6,            // px of movement that turns a press into a drag
  CLICK_MAX_MS: 600,      // a stationary press released within this is a click (a deliberate, slow click still counts)
  DOUBLE_MS: 400,         // a second click this soon is the same click (a double-click must not open and shut the menu)
  HOLD_MS: 600,           // a stationary press held this long picks him up
  THROW_SPEED: 950,       // px/s release speed that turns a drag into a throw
  HOVER_DWELL_MS: 220,    // the cursor has to settle on him this long to count as approaching him
  HOVER_LEAVE_MS: 450,    // ...and be off him this long before he lets go of the moment
  GREET_COOLDOWN_MS: 25000,
  CURIOUS_RADIUS: 260,    // px from his centre at which he notices the cursor
};

/** Click vs. hold vs. drag vs. flick, from raw pointer samples. Every press ends in exactly one outcome. */
export class Gesture {
  constructor(opts = GESTURE) { this.o = opts; this.p = null; }
  get active() { return !!this.p; }
  get dragging() { return !!(this.p && this.p.moved); }
  down(x, y, t) {
    this.p = { x0: x, y0: y, t0: t, moved: false, held: false, samples: [{ t, x, y }] };
    return "pressed";
  }
  /** "drag-start" once the press moves past the slop, then "drag"; null while still a press. */
  move(x, y, t) {
    const p = this.p;
    if (!p) return null;
    p.samples.push({ t, x, y });
    if (p.samples.length > 10) p.samples.shift();
    if (!p.moved && Math.hypot(x - p.x0, y - p.y0) >= this.o.TAP_SLOP) { p.moved = true; return "drag-start"; }
    return p.moved ? "drag" : null;
  }
  /** "hold" once, when a stationary press has lasted HOLD_MS (he's picked up). */
  tick(t) {
    const p = this.p;
    if (p && !p.moved && !p.held && t - p.t0 >= this.o.HOLD_MS) { p.held = true; return "hold"; }
    return null;
  }
  /** click | set-down (held, never moved) | place (slow drag) | throw (flick, with velocity) | none. */
  up(x, y, t) {
    const p = this.p;
    this.p = null;
    if (!p) return { kind: "none" };
    if (p.moved) {
      p.samples.push({ t, x, y });
      const rv = releaseVelocity(p.samples);
      return rv.speed >= this.o.THROW_SPEED ? { kind: "throw", ...rv } : { kind: "place", ...rv };
    }
    if (p.held || t - p.t0 > this.o.CLICK_MAX_MS) return { kind: "set-down" };
    return { kind: "click" };
  }
  /** Lost pointer capture, window deactivated, an error: end the press cleanly. */
  cancel() {
    const p = this.p;
    this.p = null;
    return { kind: "cancel", dragging: !!(p && p.moved), held: !!(p && (p.held || p.moved)) };
  }
}

/** Approach vs. passing through: the cursor must settle on him to count, and he greets at most once a while. */
export class HoverIntent {
  constructor(opts = GESTURE) { this.o = opts; this.inside = false; this.since = 0; this.leftAt = 0;
    this.hovered = false; this.lastGreet = -Infinity; }
  enter(t) { if (!this.inside) { this.inside = true; this.since = t; } }
  leave(t) { if (this.inside) { this.inside = false; this.leftAt = t; } }
  /** -> { changed, hovered, greet } */
  update(t) {
    let changed = false, greet = false;
    if (!this.hovered && this.inside && t - this.since >= this.o.HOVER_DWELL_MS) {
      this.hovered = true; changed = true;
      if (t - this.lastGreet >= this.o.GREET_COOLDOWN_MS) { greet = true; this.lastGreet = t; }
    } else if (this.hovered && !this.inside && t - this.leftAt >= this.o.HOVER_LEAVE_MS) {
      this.hovered = false; changed = true;
    }
    return { changed, hovered: this.hovered, greet };
  }
  reset() { this.inside = false; this.hovered = false; }
}

/** The single owner of his interaction state. onLease(kind, state, settleMs) mirrors ownership to the host. */
export class Interaction {
  constructor({ onChange = () => {}, onLease = () => {} } = {}) {
    this.state = "ambient";
    this.base = "ambient";         // what he falls back to: ambient | work_focus | docked
    this.onChange = onChange;
    this.onLease = onLease;
    this.since = 0;
  }
  get owning() { return OWNING.has(this.state); }
  is(...names) { return names.includes(this.state); }
  /** Enter `next`. Leaving an owning state releases the lease with its settle time; entering one grants it. */
  set(next, now = 0) {
    if (!STATES.includes(next) || next === this.state) return false;
    const prev = this.state;
    this.state = next; this.since = now;
    if (OWNING.has(prev) && !OWNING.has(next)) this.onLease("release", prev, SETTLE_MS[prev] || 0);
    if (OWNING.has(next)) this.onLease("grant", next, LEASE_TTL_MS);
    this.onChange(next, prev);
    return true;
  }
  /** Back to the resting state for what the user is doing. */
  rest(now = 0) { return this.set(this.base, now); }
  /** The user's activity changed (ambient / work_focus / docked); applied now unless an interaction is live. */
  setBase(base, now = 0) {
    if (!["ambient", "work_focus", "docked"].includes(base)) return;
    this.base = base;
    if (!this.owning && this.state !== "curious" && this.state !== "suspended") this.set(base, now);
  }
  /** Keep the host's lease alive while an interaction lasts (call about once a second). */
  renew() { if (this.owning) this.onLease("grant", this.state, LEASE_TTL_MS); }
}

/** Pure. Which landing reaction a throw earns, from how hard he hit (impact px/s) and how many bounces. */
export function landingKind(impact, bounces, random = Math.random) {
  const r = random();
  if (impact > 1500) return r < 0.4 ? "faceplant" : r < 0.7 ? "dizzy" : "superhero";
  if (impact > 900) return r < 0.35 ? "roll" : r < 0.6 ? "superhero" : r < 0.8 ? "cartwheel" : "annoyed";
  if (bounces > 3) return r < 0.5 ? "dizzy" : "roll";
  return r < 0.3 ? "popup" : r < 0.55 ? "invite" : r < 0.8 ? "annoyed" : "surprised";
}

/** Pure. How he receives a dropped file or shortcut: a heavy file is a struggle, a shortcut goes through a portal
 *  or gets kicked, anything else is caught, juggled, swung at or (rarely) missed. */
export function dropReaction(meta, random = Math.random) {
  const f = (meta && meta[0]) || {};
  const name = String(f.name || "").toLowerCase();
  const r = random();
  if ((f.size || 0) > 50 * 1024 * 1024) return "struggle";
  if (name.endsWith(".lnk") || name.endsWith(".url")) return r < 0.5 ? "portal" : "kick";
  return r < 0.35 ? "catch" : r < 0.55 ? "juggle" : r < 0.7 ? "swing" : r < 0.82 ? "toss" : r < 0.92 ? "finisher" : "miss";
}

/** Pure. The duration (ms) and window-hop arc (px) of each relocation style. */
export function travelPlan(style, distance) {
  if (style === "teleport") return { ms: 700, arc: 0, fade: true };
  if (style === "fly") return { ms: Math.min(2600, 900 + distance * 0.9), arc: 140, fade: false };
  if (style === "hop") return { ms: Math.min(2200, 600 + distance * 1.2), arc: 46, fade: false };
  return { ms: Math.min(2400, 400 + distance * 3.2), arc: 0, fade: false };   // walk: about 300 px/s
}

// ---- Hit mask: his silhouette, as cells the host makes clickable (hud/app.py companion_hitmask) ----
// The mask is painted BEHIND him, each cell in his own colour there, so it should never be seen: only cells he
// covers completely, and only where he was in the previous sample as well (a limb in motion can't strand a cell).
// If a cell does peek out for an instant, it is a ghost of him a quarter-second ago, not a block of foreign colour.
// It is never shrunk further: a click on his body that fell through would land on the app behind him.
export const HITMASK = { EVERY_MS: 250, REFRESH_MS: 2000, CELL_PX: 4, MIN_CELLS: 8, MAX_CELLS: 48, ALPHA_MIN: 240 };

/** Pure. The cell grid for a stage of w x h CSS px. */
export function maskGrid(w, h) {
  const n = (v) => Math.max(HITMASK.MIN_CELLS, Math.min(HITMASK.MAX_CELLS, Math.round((v || 0) / HITMASK.CELL_PX)));
  return { cols: n(w), rows: n(h) };
}

/** Pure. 1 for each cell whose sampled pixels are ALL opaque (RGBA bytes, W x H; flipY for WebGL's bottom-up rows). */
export function coverCells(px, W, H, cols, rows, flipY = false, alphaMin = HITMASK.ALPHA_MIN) {
  const out = new Uint8Array(cols * rows);
  if (!px || !W || !H) return out;
  for (let r = 0; r < rows; r++) {
    const y0 = Math.floor(r * H / rows), y1 = Math.max(y0 + 1, Math.floor((r + 1) * H / rows));
    for (let c = 0; c < cols; c++) {
      const x0 = Math.floor(c * W / cols), x1 = Math.max(x0 + 1, Math.floor((c + 1) * W / cols));
      let solid = 1;
      for (let y = y0; y < y1 && solid; y += 2) {
        const row = flipY ? H - 1 - y : y;
        for (let x = x0; x < x1; x += 2) if (px[(row * W + x) * 4 + 3] < alphaMin) { solid = 0; break; }
      }
      out[r * cols + c] = solid;
    }
  }
  return out;
}

/** Pure. The cells to make clickable from this sample (`now`) and the previous one (`prev`, or null): the steady ones. */
export function hitCells(now, prev) {
  if (!prev || prev.length !== now.length) return now.slice();
  return now.map((v, i) => v & prev[i]);
}

/** Pure. His average colour in each given cell, as 12-bit 0xRGB (0 elsewhere) — what the hidden mask is painted. */
export function cellColors(px, W, H, cols, rows, cells, flipY = false) {
  const out = new Uint16Array(cols * rows);
  for (let r = 0; r < rows; r++) {
    const y0 = Math.floor(r * H / rows), y1 = Math.max(y0 + 1, Math.floor((r + 1) * H / rows));
    for (let c = 0; c < cols; c++) {
      if (!cells[r * cols + c]) continue;
      const x0 = Math.floor(c * W / cols), x1 = Math.max(x0 + 1, Math.floor((c + 1) * W / cols));
      let R = 0, G = 0, B = 0, n = 0;
      for (let y = y0; y < y1; y += 2) {
        const row = flipY ? H - 1 - y : y;
        for (let x = x0; x < x1; x += 2) { const i = (row * W + x) * 4; R += px[i]; G += px[i + 1]; B += px[i + 2]; n++; }
      }
      out[r * cols + c] = n ? ((R / n) >> 4) << 8 | ((G / n) >> 4) << 4 | ((B / n) >> 4) : 0;
    }
  }
  return out;
}

/** Pure. A cell mask as rows of "0"/"1" (the wire format), top row first. */
export function maskBits(cells, cols, rows) {
  const bits = [];
  for (let r = 0; r < rows; r++) {
    let s = "";
    for (let c = 0; c < cols; c++) s += cells[r * cols + c] ? "1" : "0";
    bits.push(s);
  }
  return bits;
}

/** Pure. Cell colours as rows of 3 hex digits per cell (the wire format), top row first. */
export function maskColors(colors, cols, rows) {
  const out = [];
  for (let r = 0; r < rows; r++) {
    let s = "";
    for (let c = 0; c < cols; c++) s += (colors[r * cols + c] || 0).toString(16).padStart(3, "0");
    out.push(s);
  }
  return out;
}
