// Living Desktop Companion — behavior layer (pure JS, no DOM).
//
// This module owns everything the renderer needs that is NOT WebGL:
// the versioned event contract, the state resolver, the idle picker,
// the single-RAF scheduler with hide/visibility suspension, and the
// mouth-level state machine (with a hard stale-audio timeout so the
// mouth cannot be left hanging open by a stalled producer).
//
// It mirrors core/companion.py; it does not depend on it. The Python
// side and the JS side are tested independently (see
// tests/test_living_companion.py and tests/node/test_behavior.mjs).

export const SCHEMA = 1;

export const STATES = ["idle", "listening", "thinking", "speaking", "working", "muted", "error"];
export const ALIAS = { sleeping: "idle" };
export const AMBIENT = new Set(["idle"]);

export const IDLE = {
  breathe:      { weight: 10,  cooldown: 1.5,  big: false },
  blink:        { weight:  8,  cooldown: 2.0,  big: false },
  glance:       { weight:  4,  cooldown: 7.0,  big: false },
  weight_shift: { weight:  2,  cooldown: 14.0, big: true  },
  look_around:  { weight:  3,  cooldown: 10.0, big: false },
  ponder:       { weight: 1.5, cooldown: 25.0, big: false },
  stretch:      { weight:  1,  cooldown: 45.0, big: true  },
  yawn:         { weight: 0.8, cooldown: 60.0, big: true  },
  wander:       { weight: 0.6, cooldown: 50.0, big: true  },
  sit:          { weight:   1, cooldown: 40.0, big: true  },
  perch:        { weight: 0.7, cooldown: 70.0, big: true  },
  curious:      { weight: 1.2, cooldown: 30.0, big: true  },
  laugh:        { weight: 0.6, cooldown: 55.0, big: true  },
  fly:          { weight: 0.5, cooldown: 80.0, big: true  },
  parkour:      { weight: 0.5, cooldown: 90.0, big: true  },
  play:         { weight: 0.8, cooldown: 45.0, big: true  },
  wave:         { weight: 0.5, cooldown: 90.0, big: true  },
  dance:        { weight: 0.4, cooldown: 120.0, big: true },
  read:         { weight: 0.7, cooldown: 50.0, big: false },   // a tiny book while the user works
  hologram:     { weight: 0.7, cooldown: 50.0, big: false },   // fiddles with a little holographic toy
  salute: { weight: 0.8, cooldown: 65, big: true },
  double_take: { weight: 1.0, cooldown: 45, big: true },
  shuffle: { weight: 0.9, cooldown: 75, big: true },
};

// Personality while the user works (Living Presence). Each activity (core/presence.classify_activity) limits him
// to calm, in-place pastimes and tilts toward the fitting ones: nothing that walks through the editor while they
// type, nothing big while they watch a video. `null` = everything allowed.
export const ACTIVITY_STYLE = {
  coding:   { allow: ["breathe", "blink", "glance", "look_around", "ponder", "read", "hologram", "sit", "stretch", "yawn", "weight_shift"],
              boost: { read: 3, hologram: 2.5, sit: 2, ponder: 1.5 } },
  typing:   { allow: ["breathe", "blink", "glance", "ponder", "read", "hologram", "sit", "weight_shift"],
              boost: { read: 3, hologram: 2, sit: 2 } },
  reading:  { allow: ["breathe", "blink", "glance", "ponder", "read", "sit", "hologram", "weight_shift"],
              boost: { read: 3, sit: 2 } },
  video:    { allow: ["breathe", "blink", "glance", "sit"], boost: { sit: 3 } },
  meeting:  { allow: ["breathe", "blink", "glance"], boost: {} },
  browsing: { allow: null, deny: ["fly", "parkour"], boost: { look_around: 1.5, curious: 1.3 } },
  gaming:   { allow: ["breathe", "blink"], boost: {} },
};

/** Pure. A weight factor per idle action for the user's current activity (0 = never). */
export function activityBias(activity) {
  const style = ACTIVITY_STYLE[activity];
  if (!style) return () => 1;
  const allow = style.allow ? new Set(style.allow) : null, deny = new Set(style.deny || []);
  return (name) => (deny.has(name) || (allow && !allow.has(name))) ? 0 : (style.boost[name] || 1);
}

export const DEFAULTS = {
  quality: "medium", scale: 1.0, idle_fps: 20, active_fps: 30,
  reduced_motion: false, dpr_cap: 1.5, character_px: 160, window_size: [187, 187], visible: true,
  auto_place: true,   // Python-side smart positioning; mirrored here so settings round-trip
  app_reactions: true, // Python-side app-launch kicks; mirrored here so settings round-trip
  seasonal: true,      // auto seasonal accessory + greeting by the calendar
  proactive: true,     // time-of-day greetings + break reminder after long activity
  launch_drops: true,  // open a shortcut dropped ON him by its real path (native drop, #152)
  work_aware: true,    // calm, in-place behaviour while the user types, codes, reads or watches (Living Presence)
  hover_greet: true,   // a small hello when the cursor settles on him
};

export const MAX_EVENTS = 256;

// A seasonal accessory + greeting by calendar month (0=Jan). Pure, so it is Node-testable; companion.html
// applies it at boot when the "seasonal" setting is on and the profile has no explicit accessory.
const SEASONS = {
  0:  { accessory: "halo",      greeting: "Happy new year, sir." },
  1:  { accessory: "bowtie",    greeting: "" },
  9:  { accessory: "tophat",    greeting: "A spooky evening to you, sir." },
  11: { accessory: "partyhat",  greeting: "Season's greetings, sir." },
};
// A greeting for the hour of day (0-23). Pure, Node-tested.
export function timeGreeting(hour) {
  if (hour >= 5 && hour < 12) return "Good morning, sir.";
  if (hour >= 12 && hour < 17) return "Good afternoon, sir.";
  if (hour >= 17 && hour < 22) return "Good evening, sir.";
  return "Working late, sir? Do take care.";
}

// Proactive break logic: suggest one after `thresholdMs` of continuous activity (once per active stretch).
export function shouldSuggestBreak(activeMs, alreadySuggested, thresholdMs = 50 * 60 * 1000) {
  return !alreadySuggested && activeMs >= thresholdMs;
}

export function seasonalTheme(month) {
  return SEASONS[month] || { accessory: "none", greeting: "" };
}

// -------------------------------------------------------------------------------------------------
// Mood / emotion model (beyond the conversational states). Moods are discrete feelings whose
// intensities decay toward calm over time; the strongest one above a small threshold is "current".
// Mood biases which idle behaviours he leans toward and tilts his expression (droopy when tired,
// narrowed when annoyed). Pure + Node-tested; the renderer nudges it from real events.
// -------------------------------------------------------------------------------------------------
export const MOODS = ["neutral", "happy", "proud", "playful", "curious", "tired", "annoyed"];

// Per-mood idle weight multipliers (absent name = 1.0). This is what makes a mood *do* something:
// a tired companion yawns and sits; a playful one plays and dances; a curious one investigates.
const MOOD_IDLE_BIAS = {
  happy:   { wave: 2.5, laugh: 2.5, dance: 2.0, play: 1.5 },
  proud:   { salute: 3.0, wave: 2.0, dance: 1.8, stretch: 1.5 },
  playful: { shuffle: 3.0, double_take: 2.0, play: 3.0, dance: 2.0, parkour: 1.8, fly: 1.5 },
  curious: { double_take: 2.5, curious: 3.0, look_around: 2.0, glance: 1.5, ponder: 1.5 },
  tired:   { yawn: 3.0, sit: 2.5, perch: 1.5, stretch: 1.4, wander: 0.3, dance: 0.2, parkour: 0.2, fly: 0.2 },
  annoyed: { weight_shift: 2.0, ponder: 1.6, wave: 0.4, dance: 0.3, play: 0.5 },
};

// Per-mood expression hint: `lid` is extra eyelid coverage (positive = droop/narrow; negative clamps
// to wide-open), `bob` scales idle liveliness (>1 livelier, <1 sluggish).
const MOOD_EXPR = {
  neutral: { lid: 0.0,  bob: 1.0 },
  happy:   { lid: -0.05, bob: 1.3 },
  proud:   { lid: -0.05, bob: 1.15 },
  playful: { lid: 0.0,  bob: 1.4 },
  curious: { lid: -0.1, bob: 1.1 },
  tired:   { lid: 0.35, bob: 0.7 },
  annoyed: { lid: 0.2,  bob: 0.95 },
};

/** Pure. Expression hint for a mood at a given intensity (0..1). */
export function moodExpression(mood, intensity = 1) {
  const e = MOOD_EXPR[mood] || MOOD_EXPR.neutral;
  const s = Math.max(0, Math.min(1, intensity));
  return { lid: e.lid * s, bob: 1 + (e.bob - 1) * s };
}

export class MoodModel {
  constructor({ halfLifeMs = 30000, threshold = 0.15,
                now = () => (typeof performance !== "undefined" ? performance.now() : Date.now()) } = {}) {
    this.halfLife = halfLifeMs;
    this.threshold = threshold;
    this._now = now;
    this.m = Object.create(null);     // mood name -> intensity (decaying)
    this.t = now();
  }
  _decayTo(nowMs) {
    const n = typeof nowMs === "number" ? nowMs : this._now();
    const dt = n - this.t;
    if (dt > 0) {
      const k = Math.pow(0.5, dt / this.halfLife);
      for (const name in this.m) { this.m[name] *= k; if (this.m[name] < 0.01) delete this.m[name]; }
      this.t = n;
    }
    return n;
  }
  /** Add `amount` to a mood (decaying everything to `nowMs` first); intensity saturates at 1. */
  nudge(mood, amount = 0.5, nowMs) {
    if (!MOODS.includes(mood) || mood === "neutral") return this;
    const n = this._decayTo(nowMs);
    this.m[mood] = Math.max(0, Math.min(1, (this.m[mood] || 0) + amount));
    this.t = n;
    return this;
  }
  update(nowMs) { this._decayTo(nowMs); return this; }
  /** The strongest mood above threshold, else neutral. */
  dominant(nowMs) {
    this._decayTo(nowMs);
    let best = "neutral", bi = this.threshold;
    for (const name in this.m) { if (this.m[name] > bi) { bi = this.m[name]; best = name; } }
    return { mood: best, intensity: best === "neutral" ? 0 : this.m[best] };
  }
  /** A weight-multiplier function name->factor for the IdlePicker, tilted by the current mood's strength. */
  bias(nowMs) {
    const d = this.dominant(nowMs);
    const table = MOOD_IDLE_BIAS[d.mood] || {};
    const s = d.intensity;
    return (name) => { const target = table[name]; return target === undefined ? 1 : 1 + (target - 1) * s; };
  }
  /** Current expression hint (lid/bob) for the face + idle liveliness. */
  expression(nowMs) {
    const d = this.dominant(nowMs);
    return moodExpression(d.mood, d.intensity);
  }
}

// If the main loop stops sending levels while state is still "speaking"
// (producer stall, dropped packets), close the mouth after this long.
export const LEVEL_STALE_MS = 500;

const LEGACY_TYPES = new Set([
  "badges", "media", "media_clear", "mode", "timers", "notify", "focus",
  "show", "hide", "companion_show", "companion_hide",
  "dashboard", "quit", "companion_toggle", "mini",
]);

export function validate(msg) {
  if (!msg || typeof msg !== "object") return null;
  const t = msg.type;
  if (typeof t !== "string") return null;
  if (t === "state") {
    const v = ALIAS[msg.value] || msg.value;
    return STATES.includes(v) ? { type: "state", value: v } : null;
  }
  if (t === "level") {
    const n = Number(msg.value);
    if (!Number.isFinite(n)) return null;
    return { type: "level", value: Math.max(0, Math.min(1, n)) };
  }
  if (t === "line" && ["you", "jarvis", "sys"].includes(msg.who) && typeof msg.text === "string") {
    return { type: "line", who: msg.who, text: msg.text.slice(0, 400) };
  }
  if (t === "settings" && msg.value && typeof msg.value === "object") {
    return { type: "settings", value: safeSettings(msg.value) };
  }
  if (t === "idle" && IDLE[msg.value]) return { type: "idle", value: msg.value };
  if (LEGACY_TYPES.has(t)) return { ...msg };
  return null;
}

export function safeSettings(raw) {
  const out = { ...DEFAULTS };
  if (!raw || typeof raw !== "object") return out;
  if (["low", "medium", "high"].includes(raw.quality)) out.quality = raw.quality;
  const num = (v, lo, hi, d) => { const n = Number(v); return Number.isFinite(n) ? Math.max(lo, Math.min(hi, n)) : d; };
  out.scale = num(raw.scale, 0.5, 2.0, out.scale);
  out.idle_fps = Math.round(num(raw.idle_fps, 5, 60, out.idle_fps));
  out.active_fps = Math.round(num(raw.active_fps, 10, 60, out.active_fps));
  out.dpr_cap = num(raw.dpr_cap, 1.0, 2.0, out.dpr_cap);
  out.character_px = Math.round(num(raw.character_px, 50, 400, out.character_px));
  out.reduced_motion = !!raw.reduced_motion;
  out.visible = raw.visible === undefined ? out.visible : !!raw.visible;
  out.auto_place = raw.auto_place === undefined ? out.auto_place : !!raw.auto_place;
  out.app_reactions = raw.app_reactions === undefined ? out.app_reactions : !!raw.app_reactions;
  out.seasonal = raw.seasonal === undefined ? out.seasonal : !!raw.seasonal;
  out.proactive = raw.proactive === undefined ? out.proactive : !!raw.proactive;
  out.launch_drops = raw.launch_drops === undefined ? out.launch_drops : !!raw.launch_drops;
  out.work_aware = raw.work_aware === undefined ? out.work_aware : !!raw.work_aware;
  out.hover_greet = raw.hover_greet === undefined ? out.hover_greet : !!raw.hover_greet;
  if (Array.isArray(raw.window_size) && raw.window_size.length === 2) {
    out.window_size = [
      num(raw.window_size[0], 160, 640, out.window_size[0]) | 0,
      num(raw.window_size[1], 160, 640, out.window_size[1]) | 0,
    ];
  }
  return out;
}

// Full-lifecycle state resolver. The producer (main.py) is authoritative;
// any valid incoming state is honored. Garbage falls back to current.
export function resolveState(current, incoming) {
  const cur = ALIAS[current] || current;
  const curValid = STATES.includes(cur) ? cur : "idle";
  const next = ALIAS[incoming] || incoming;
  if (!STATES.includes(next)) return curValid;
  return next;
}

export function isAmbient(state) {
  return AMBIENT.has(ALIAS[state] || state);
}

export class IdlePicker {
  constructor({ reducedMotion = false, random = Math.random } = {}) {
    this.reducedMotion = reducedMotion;
    this.random = random;
    this.last = Object.create(null);
  }
  choose(nowMs, biasFn = null) {
    const items = [];
    for (const name in IDLE) {
      const spec = IDLE[name];
      if (this.reducedMotion && spec.big) continue;
      const last = this.last[name];
      if (last !== undefined && (nowMs - last) / 1000 < spec.cooldown) continue;
      const w = spec.weight * (biasFn ? Math.max(0, biasFn(name)) : 1);   // mood can tilt the odds
      if (w <= 0) continue;
      items.push([name, w]);
    }
    if (!items.length) return null;
    const total = items.reduce((s, [, w]) => s + w, 0);
    let r = this.random() * total;
    for (const [name, w] of items) {
      r -= w;
      if (r <= 0) { this.last[name] = nowMs; return name; }
    }
    const name = items[items.length - 1][0];
    this.last[name] = nowMs;
    return name;
  }
  reset() { this.last = Object.create(null); }
}

/**
 * Single-RAF scheduler. start() is idempotent (no duplicate loops);
 * stop() cancels any outstanding frame. suspend()/resume() mirror
 * window hide/show so a hidden companion stops rendering entirely —
 * CSS visibility alone is not enough.
 */
export class Scheduler {
  constructor({ onFrame, requestFrame, cancelFrame, now = () => Date.now() }) {
    this.onFrame = onFrame;
    this.request = requestFrame;
    this.cancel = cancelFrame;
    this.now = now;
    this._raf = 0;
    this._suspended = false;
    this._frames = 0;
    this._lastTick = 0;
  }
  get running() { return this._raf !== 0; }
  get suspended() { return this._suspended; }
  get frames() { return this._frames; }
  start() {
    if (this._raf !== 0 || this._suspended) return;
    const tick = (t) => {
      if (this._raf === 0) return;    // stopped between frames
      this._frames += 1;
      this._lastTick = t;
      this.onFrame(t);
      this._raf = this.request(tick);
    };
    this._raf = this.request(tick);
  }
  stop() {
    if (this._raf === 0) return;
    this.cancel(this._raf);
    this._raf = 0;
  }
  suspend() {
    if (this._suspended) return;
    this._suspended = true;
    this.stop();
  }
  resume() {
    if (!this._suspended) return;
    this._suspended = false;
    this.start();
  }
}

/**
 * Owns the renderer-visible state: mode, level, mouth target, and the
 * stale-audio timeout. applyEvent returns true when an event mutated
 * state (so the renderer can decide to re-render), false otherwise.
 */
export class CompanionState {
  constructor({ now = () => (typeof performance !== "undefined" ? performance.now() : Date.now()),
                levelTimeoutMs = LEVEL_STALE_MS } = {}) {
    this._now = now;
    this.levelTimeoutMs = levelTimeoutMs;
    this.mode = "idle";
    this.level = 0;
    this.lastLevelAt = 0;
    this.jaw = 0;
    this.active = false;
    this.settings = { ...DEFAULTS };
    this.reducedMotion = false;
  }
  applyEvent(evRaw) {
    const ev = validate(evRaw);
    if (!ev) return false;
    if (ev.type === "state") {
      const next = resolveState(this.mode, ev.value);
      const changed = next !== this.mode;
      this.mode = next;
      this.active = !isAmbient(this.mode);
      if (this.mode !== "speaking") this.level = 0;
      return changed;
    }
    if (ev.type === "level") {
      this.level = ev.value;
      this.lastLevelAt = this._now();
      return true;
    }
    if (ev.type === "settings") {
      this.settings = safeSettings({ ...this.settings, ...ev.value });
      this.reducedMotion = this.settings.reduced_motion;
      return true;
    }
    return true;
  }
  /**
   * Target jaw opening in [0, 1]. Hard rules:
   *  - mouth is CLOSED outside "speaking" (silence, cancel, mute, error)
   *  - mouth is CLOSED after LEVEL_STALE_MS without a level update
   *  - otherwise it follows the latest clamped level
   * This is amplitude sync (per core/companion.py's docstring), not phoneme sync.
   */
  mouthTarget(nowMs) {
    if (this.mode !== "speaking") return 0;
    const n = typeof nowMs === "number" ? nowMs : this._now();
    if (this.lastLevelAt && n - this.lastLevelAt > this.levelTimeoutMs) return 0;
    return this.level;
  }
  /** Smoothly lerp jaw toward mouthTarget; call this once per frame. */
  updateMouth(nowMs, dt = 1 / 60) {
    const target = this.mouthTarget(nowMs);
    const speed = Math.min(1, dt * 18);
    this.jaw += (target - this.jaw) * speed;
    if (this.jaw < 0.005) this.jaw = 0;
    return this.jaw;
  }
}

/** Bounded ring buffer. Drops the oldest when overflowing; coalesces
 *  consecutive level events to the latest value. Mirrors
 *  core/companion.py:EventBuffer. */
export class EventBuffer {
  constructor(limit = MAX_EVENTS) { this.limit = limit; this._q = []; }
  put(evRaw) {
    const ev = validate(evRaw);
    if (!ev) return false;
    if (ev.type === "level" && this._q.length && this._q[this._q.length - 1].type === "level") {
      this._q[this._q.length - 1] = ev;
      return true;
    }
    this._q.push(ev);
    if (this._q.length > this.limit) this._q.splice(0, this._q.length - this.limit);
    return true;
  }
  drain() { const out = this._q; this._q = []; return out; }
  get length() { return this._q.length; }
}
