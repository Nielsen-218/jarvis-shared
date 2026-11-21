// What he SAYS when he is poked (pure JS, no DOM; Node-tested in tests/node/test_pokes.mjs).
//
// A click on him is a poke: he reacts and answers aloud, in the film butler's voice (dry, warm, British
// understatement). The line is chosen from what is going on: how many pokes in a row (his patience wears thin,
// wittily), what just happened to him (thrown, woken, handed a file, just finished answering), what the owner is
// doing (he is brief while they work), his mood, and the time of day. He never says the same line twice running,
// and avoids his last few.

export const POKE = {
  STREAK_WINDOW_MS: 8000,   // a poke this soon after the last one is "again"
  RECENT_MS: 12000,         // "what just happened" stays fresh this long
  MEMORY: 4,                // lines he will not repeat while there is anything else to say
};

// Streak tiers: [minimum pokes in a row, name]. Highest matching wins.
export const TIERS = [[12, "surrender"], [7, "exasperated"], [4, "thin"], [2, "again"], [1, "first"]];

export const LINES = {
  first: [
    "Yes, sir?", "At your service, sir.", "You rang, sir?", "Present and attentive, sir.",
    "How may I be of use, sir?", "Ready when you are, sir.", "I'm listening, sir.", "For you, sir, always.",
    "All systems nominal, sir. Myself included.", "A pleasure to be noticed, sir.", "Standing by, sir.",
    "Sir. What do you require?",
  ],
  morning: [
    "Good morning, sir. I trust you slept.", "Morning, sir. The day is entirely unspoilt so far.",
    "Up and about already, sir? Splendid.", "Good morning, sir. Shall we begin?",
  ],
  afternoon: [
    "Good afternoon, sir. Still at it, I see.", "Afternoon, sir. Might I suggest water at some point?",
    "The afternoon is proceeding on schedule, sir.",
  ],
  evening: [
    "Good evening, sir.", "Evening, sir. A productive day, I hope.", "Good evening, sir. What shall it be?",
  ],
  night: [
    "It is rather late, sir. Not that I am counting.", "Burning the midnight oil again, sir?",
    "Still awake, sir? I shall keep my voice down.", "The sensible hours have ended, sir. I remain regardless.",
  ],
  happy: ["Delighted, sir. Do carry on.", "In excellent spirits, sir, thank you for asking."],
  playful: ["Careful, sir. I bruise easily. Metaphorically.", "Is this a game, sir? I do enjoy winning."],
  tired: ["Awake, sir. Mostly.", "Running on reserves, sir, but running."],
  annoyed: ["Yes, sir. I had noticed you.", "I am, as ever, thrilled, sir."],
  curious: ["Something on your mind, sir?", "You have my full curiosity, sir."],
  proud: ["Still basking in recent success, sir. Do go on."],
  again: [
    "Twice, sir. I did hear you the first time.", "Still here, sir. That has not changed.",
    "Yes, sir, the button works.", "Again, sir? I am flattered.", "I assure you, sir, I am not a doorbell.",
  ],
  thin: [
    "Sir, I am a sophisticated system, not a stress toy.", "My patience is extensive, sir. Not infinite.",
    "If you are testing my durability, sir, the results are in.",
    "I am beginning to suspect this is recreational, sir.", "Shall I schedule these, sir? Every five seconds, say?",
    "One does wonder what the end goal is, sir.",
  ],
  exasperated: [
    "Sir. Please. I have dignity. Somewhere.", "At this rate, sir, I shall need a helmet.",
    "I am logging each of these, sir. For the tribunal.", "Might I interest you in a hobby, sir?",
    "This is why we cannot have nice things, sir.", "Very well, sir. Get it out of your system.",
  ],
  surrender: [
    "I surrender, sir. You have out-poked me.", "I shall simply stand here and take it, then.",
    "Remarkable stamina, sir. Truly.",
  ],
  landed: ["Still recovering from the flight, sir. Gently.", "After that landing, sir, a poke is almost affectionate."],
  woke: ["I was not asleep, sir. I was conserving energy.", "Awake, sir. Fully operational. Nearly."],
  dropped: ["Still holding what you gave me, sir.", "Your delivery is safe with me, sir."],
  spoke: ["Was there something further, sir?", "Anything else, sir?"],
  working: ["I shall be brief, sir. Yes?", "Do not let me interrupt, sir.", "Quietly at your disposal, sir."],
  gaming: ["Eyes on the game, sir. I can wait."],
  video: ["I shan't talk over the film, sir."],
  // A double click asks him to listen; these are said only when the microphone cannot be opened from here.
  double: [
    "Two taps, sir. You have my undivided attention.", "Twice in quick succession, sir. I am all ears.",
    "A double summons, sir. Do go on.",
  ],
};

// The body reaction that goes with each tier (clip names from animation.js) and how the poke moves his mood.
export const REACTIONS = {
  first: { anims: ["double_take", "salute", "wave", "look_around"], mood: ["happy", 0.2] },
  again: { anims: ["double_take", "look_around", "weight_shift"], mood: ["playful", 0.2] },
  thin: { anims: ["ponder", "weight_shift", "double_take"], mood: ["annoyed", 0.25] },
  exasperated: { anims: ["yawn", "ponder", "stretch"], mood: ["annoyed", 0.4] },
  surrender: { anims: ["laugh", "shuffle"], mood: ["playful", 0.5] },
  double: { anims: ["salute"], mood: ["curious", 0.3] },
};

const WORKING = new Set(["coding", "typing", "reading", "meeting"]);

export function allLines() { return Object.values(LINES).flat(); }

export function tierOf(streak) {
  const n = Math.max(1, Number(streak) || 1);
  for (const [min, name] of TIERS) if (n >= min) return name;
  return "first";
}

export function daypart(hour) {
  const h = ((Number(hour) % 24) + 24) % 24;
  return h < 5 ? "night" : h < 12 ? "morning" : h < 17 ? "afternoon" : h < 22 ? "evening" : "night";
}

/**
 * One click or a double click, never both: the first click WAITS for the double-click window, so a double click
 * neither speaks a poke line nor counts toward the streak. click(now) -> "wait" | "double".
 */
export class ClickGate {
  constructor({ windowMs = 400, onSingle = () => {}, onDouble = () => {},
                setTimer = (fn, ms) => setTimeout(fn, ms), clearTimer = (id) => clearTimeout(id) } = {}) {
    Object.assign(this, { windowMs, onSingle, onDouble, setTimer, clearTimer });
    this.timer = null; this.firstAt = -Infinity;
  }
  get pending() { return this.timer !== null; }
  click(now) {
    if (this.timer !== null && now - this.firstAt <= this.windowMs) { this.cancel(); this.onDouble(now); return "double"; }
    this.cancel();
    this.firstAt = now;
    this.timer = this.setTimer(() => { this.timer = null; this.onSingle(now); }, this.windowMs);
    return "wait";
  }
  cancel() { if (this.timer !== null) { this.clearTimer(this.timer); this.timer = null; } }
}

/** Counts pokes in a row and remembers the last few lines, so the caller only reports "he was poked". */
export class PokeTracker {
  constructor(opts = POKE) { this.o = opts; this.streak = 0; this.lastAt = -Infinity; this.said = []; }
  poke(now) {
    this.streak = now - this.lastAt <= this.o.STREAK_WINDOW_MS ? this.streak + 1 : 1;
    this.lastAt = now;
    return this.streak;
  }
  remember(text) { this.said.push(text); if (this.said.length > this.o.MEMORY) this.said.shift(); }
  get last() { return this.said.length ? this.said[this.said.length - 1] : null; }
}

/**
 * The candidate pools for a poke, most specific first: [[categoryName, weight], ...].
 * ctx: { streak, hour, mood: {name, intensity}, recent: "landed"|"woke"|"dropped"|"spoke"|null, activity }
 */
export function pools(ctx = {}) {
  const tier = tierOf(ctx.streak);
  if (tier !== "first") return [[tier, 1]];                 // a streak is the story: nothing else competes
  const out = [];
  if (ctx.recent && LINES[ctx.recent]) out.push([ctx.recent, 6]);
  const act = ctx.activity;
  if (WORKING.has(act)) out.push(["working", 4]);
  else if (act === "gaming" || act === "video") out.push([act, 4]);
  const m = ctx.mood || {};
  if (m.name && m.name !== "neutral" && LINES[m.name] && (m.intensity ?? 1) >= 0.25) out.push([m.name, 3]);
  out.push([daypart(ctx.hour ?? 12), 2], ["first", 3]);
  return out;
}

/**
 * Choose his reply to a poke. Deterministic for a given rng. Never returns `avoid[avoid.length - 1]` (the line he
 * said last) and skips the rest of `avoid` while any other line is available.
 * -> { text, raw, category, tier, anim, mood: [name, amount] }; `raw` is the line as written (remember that one).
 */
export function pickPoke(ctx = {}, { rng = Math.random, avoid = [], title = "sir" } = {}) {
  const tier = tierOf(ctx.streak);
  const last = avoid.length ? avoid[avoid.length - 1] : null;
  const weighted = pools(ctx);
  const build = (blocked) => {
    const bag = [];
    for (const [category, weight] of weighted) {
      for (const text of LINES[category]) if (!blocked.has(text)) bag.push({ text, category, weight: weight / LINES[category].length });
    }
    return bag;
  };
  let bag = build(new Set(avoid));
  if (!bag.length) bag = build(new Set(last ? [last] : []));
  if (!bag.length) bag = LINES.first.filter((text) => text !== last).map((text) => ({ text, category: "first", weight: 1 }));
  const total = bag.reduce((n, b) => n + b.weight, 0);
  let r = rng() * total, chosen = bag[bag.length - 1];
  for (const b of bag) { r -= b.weight; if (r <= 0) { chosen = b; break; } }
  const reaction = REACTIONS[tier] || REACTIONS.first;
  const anim = reaction.anims[Math.min(reaction.anims.length - 1, Math.floor(rng() * reaction.anims.length))];
  return { text: addressAs(chosen.text, title), raw: chosen.text, category: chosen.category, tier, anim, mood: reaction.mood };
}

/** The spoken reaction to a double click when he cannot open the microphone himself. */
export function pickDouble({ rng = Math.random, avoid = [], title = "sir" } = {}) {
  const blocked = new Set(avoid);
  const bag = LINES.double.filter((t) => !blocked.has(t));
  const from = bag.length ? bag : LINES.double;
  const text = from[Math.min(from.length - 1, Math.floor(rng() * from.length))];
  return { text: addressAs(text, title), raw: text, category: "double", tier: "double", anim: REACTIONS.double.anims[0], mood: REACTIONS.double.mood };
}

/** Lines are written with "sir"; another form of address (the app's user title) replaces it. */
export function addressAs(text, title = "sir") {
  const t = String(title || "sir").trim() || "sir";
  if (t === "sir") return text;
  return text.replace(/\b[Ss]ir\b/g, (m) => (m[0] === "S" ? t[0].toUpperCase() + t.slice(1) : t));
}
