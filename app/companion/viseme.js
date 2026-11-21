// Estimated viseme (mouth-shape) lip-sync from spoken text (Feature #13 — phoneme/viseme sync).
//
// HONEST SCOPE: this is text-estimated, not phoneme-timestamped. The TTS pipeline doesn't surface per-phoneme
// timings, so we map the line's letters to a viseme timeline at an estimated speaking rate and play the SHAPES
// (open / wide / round / closed / teeth) timed to it, while playback amplitude still gates overall openness.
// That gives varied, text-driven mouth shapes — distinct from the pure-amplitude fallback — which is what the
// spec asks be kept distinct. Pure + Node-tested (tests/node/test_viseme.mjs).

// Each viseme is a width/height multiplier applied to the mouth overlay.
export const VISEME_SHAPES = {
  rest:   { w: 0.55, h: 0.18 },
  closed: { w: 0.70, h: 0.10 },   // M B P
  open:   { w: 0.80, h: 1.00 },   // AA (open vowels, generic consonants)
  wide:   { w: 1.30, h: 0.50 },   // E I Y (smile)
  round:  { w: 0.50, h: 0.95 },   // O U (pucker)
  teeth:  { w: 0.95, h: 0.28 },   // F V
};

/** Map a letter to a viseme. */
export function letterViseme(ch) {
  if (ch === "a") return "open";
  if ("ey".includes(ch)) return "wide";
  if ("i".includes(ch)) return "wide";
  if ("ou".includes(ch)) return "round";
  if ("mbp".includes(ch)) return "closed";
  if ("fv".includes(ch)) return "teeth";
  if (ch === " " || ch === "\n" || ch === "\t") return "rest";
  if (ch >= "a" && ch <= "z") return "open";     // other consonants: a slight open
  return "rest";                                  // punctuation / digits: brief pause
}

/** Build a viseme timeline for `text` at ~`msPerChar`. Returns { seq:[{v,t0,t1}], total }. */
export function textToVisemes(text, msPerChar = 58) {
  const seq = []; let t = 0;
  for (const ch of String(text || "").toLowerCase()) {
    const v = letterViseme(ch);
    const dur = v === "rest" ? msPerChar * 1.4 : msPerChar;
    seq.push({ v, t0: t, t1: t + dur });
    t += dur;
  }
  return { seq, total: t };
}

/** The viseme active at `ms` into the timeline (rest past the end). */
export function visemeAt(timeline, ms) {
  if (!timeline || !timeline.seq.length || ms < 0 || ms >= timeline.total) return "rest";
  // linear scan is fine (a line is short); seq is ordered and contiguous
  for (let i = 0; i < timeline.seq.length; i++) {
    const s = timeline.seq[i];
    if (ms >= s.t0 && ms < s.t1) return s.v;
  }
  return "rest";
}
