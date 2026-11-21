// The listening / thinking / speaking / result overlay beside him (pure JS, no DOM; Node-tested in
// tests/node/test_overlay.mjs). companion.html draws the view this model returns.
//
// It is fed by the SAME events the old panel gets from core/hud.py: `state` (listening, thinking, working,
// speaking, idle, muted, error), `line` (who: you / jarvis / sys), the amplitude `level` (microphone loudness
// while he listens, his own voice while he speaks) and `doing` (what a tool is busy with, from core/progress).
//
//   listening  a ring that follows the microphone, and the words he heard, appearing one by one
//   thinking   a calm orbiting arc, the words he heard kept as confirmation, a chip for what he is doing
//   speaking   his reply as a caption that follows his voice
//   result     the whole reply, held a moment after he stops, then gone
//
// Times are milliseconds on one monotonic clock (performance.now()); the caller passes `now` everywhere.

export const OVERLAY = {
  MS_PER_CHAR: 58,          // caption pace; the same figure viseme.js uses, so the caption and his mouth agree
  HEARD_WORD_MS: 40,        // the words he heard appear one by one
  HOLD_MS: 3000,            // the reply stays this long after he stops speaking
  READ_MS_PER_CHAR: 30,     // ...plus reading time for words that had not appeared yet (bounded by READ_MAX_MS)
  READ_MAX_MS: 4000,
  SPEECH_GAP_MS: 700,       // a pause this short between two sentences is still him speaking
  LISTEN_LINGER_MS: 600,    // he stopped listening and nothing followed
  THINK_LINGER_MS: 1600,    // he stopped thinking and no reply followed
  ERROR_MS: 2600,
  PENDING_REPLY_MS: 6000,   // a reply with no voice after this long is shown anyway (speaker off, TTS failed)
  QUIP_HOLD_MS: 1800,       // his own short reactions (a poke) leave sooner than a real answer
  LEVEL_ATTACK_MS: 60, LEVEL_RELEASE_MS: 180,
  LISTEN_MAX_QUIET_MS: 25000,   // watchdog: "listening" with no event at all for this long means the idle event was lost
  SPEAK_MAX_QUIET_MS: 20000,    // ...the same for "speaking" (his voice level changes many times a second while he talks)
  STUCK_MS: 15000,          // idle this long with a chip that never cleared: the overlay leaves anyway
  CHIP_MAX: 44,
};

export const LABELS = {
  listening: "Listening", thinking: "Thinking", working: "Working", speaking: "Jarvis", result: "Jarvis",
  error: "Something went wrong", hidden: "",
};

const PHONE = /^\s*\u{1F4F1}/u;           // lines relayed from the phone app are not this PC's conversation
const BRACKET = /^\s*\[[^\]]*\]\s*/;      // "[ignore] ..." style control prefixes never reach a caption

export function words(text) {
  return String(text || "").replace(BRACKET, "").split(/\s+/).filter(Boolean);
}

/** "opening Spotify" -> "Opening Spotify"; long tool descriptions end in an ellipsis. */
export function chipText(text, max = OVERLAY.CHIP_MAX) {
  let t = String(text || "").replace(/\s+/g, " ").trim().replace(/[.…]+$/, "");
  if (!t) return "";
  t = t[0].toUpperCase() + t.slice(1);
  return t.length > max ? t.slice(0, max - 1).replace(/\s+\S*$/, "") + "…" : t;
}

export class OverlayModel {
  constructor(opts = {}) {
    this.o = { ...OVERLAY, ...opts };
    this.main = "idle";            // the main Jarvis state, as last reported
    this.phase = "hidden";
    this.label = "";
    this.heard = [];               // [{ w, at }] at = clock time the word appears
    this.reply = [];               // [{ w, at }] at = SPEECH-clock time the word appears
    this.replyCursor = 0;          // speech-clock time the next word starts
    this.speechBase = 0;           // speech clock accumulated before the current stretch of speaking
    this.speakingSince = null;     // clock time the current stretch of speaking began
    this.speechEndedAt = null;
    this.pendingSince = null;      // a reply line arrived and he has not started speaking it
    this.hideAt = null;
    this.chip = "";
    this.local = false;            // the caption is one of his own reactions, not the main voice
    this.target = 0; this.smooth = 0; this.levelAt = null;
    this.idleSince = 0;
    this.eventAt = 0;              // the last state / line / level / doing event (the watchdog's clock)
    this.seq = 0;                  // bumps whenever the text is replaced (the view rebuilds its words)
    this.changedAt = 0;
  }

  speechClock(now) { return this.speechBase + (this.speakingSince === null ? 0 : Math.max(0, now - this.speakingSince)); }
  startSpeaking(now) { if (this.speakingSince === null) this.speakingSince = now; this.speechEndedAt = null; this.pendingSince = null; }
  stopSpeaking(now) {
    if (this.speakingSince !== null) { this.speechBase = this.speechClock(now); this.speakingSince = null; }
    this.speechEndedAt = now;
  }
  clearReply() { this.reply = []; this.replyCursor = 0; this.speechBase = 0; this.speakingSince = null; this.speechEndedAt = null; this.pendingSince = null; this.seq += 1; }
  clearHeard() { if (this.heard.length) { this.heard = []; this.seq += 1; } }
  show(phase, now) {
    if (phase !== this.phase) this.changedAt = now;
    this.phase = phase; this.label = LABELS[phase] ?? ""; this.hideAt = null;
  }
  hide(now) { this.show("hidden", now); this.chip = ""; this.local = false; this.pendingSince = null; }

  /** The main Jarvis state changed. */
  state(value, now) {
    this.eventAt = now;
    const v = value === "sleeping" ? "idle" : value;
    const prev = this.main;
    this.main = v;
    if (v === "listening") {
      if (this.phase !== "listening") { this.clearHeard(); this.clearReply(); this.chip = ""; }
      this.local = false;
      this.show("listening", now);
    } else if (v === "thinking" || v === "working") {
      if (prev === "speaking") this.stopSpeaking(now);
      if (this.phase === "speaking" && !this.local) return;       // between two sentences: tick() decides after the pause
      if (this.phase === "hidden" || this.phase === "result" || this.phase === "error" || this.local) { this.clearHeard(); this.clearReply(); }
      this.local = false;
      this.show("thinking", now);
      this.label = LABELS[v];
    } else if (v === "speaking") {
      if (this.local) { this.local = false; this.clearReply(); }   // the main voice takes the caption over
      this.startSpeaking(now);
      this.show("speaking", now);
    } else if (v === "error") {
      this.stopSpeaking(now);
      this.show("error", now);
      this.hideAt = now + this.o.ERROR_MS;
    } else if (v === "muted") {
      this.hide(now);
    } else if (v === "idle") {
      this.idleSince = now;
      if (prev === "speaking") this.stopSpeaking(now);             // tick() settles it once the pause is real
      else if (this.phase === "listening") this.hideAt = now + (this.heard.length ? this.o.THINK_LINGER_MS : this.o.LISTEN_LINGER_MS);
      else if (this.phase === "thinking" && !this.chip) this.hideAt = now + this.o.THINK_LINGER_MS;
    }
  }

  /** A transcript line: what he heard ("you") or what he says ("jarvis"). */
  line(who, text, now) {
    if (typeof text !== "string" || PHONE.test(text)) return;
    this.eventAt = now;
    const ws = words(text);
    if (!ws.length) return;
    if (who === "you") {
      this.heard = ws.map((w, i) => ({ w, at: now + i * this.o.HEARD_WORD_MS }));
      this.seq += 1;
      if (this.phase === "hidden" || this.phase === "result" || this.phase === "error" || this.local) {
        this.local = false; this.clearReply();
        this.show("thinking", now);                               // typed / dictated: there was no listening phase
        this.hideAt = now + this.o.THINK_LINGER_MS * 2;
      } else if (this.hideAt !== null) this.hideAt = Math.max(this.hideAt, now + this.o.THINK_LINGER_MS);
    } else if (who === "jarvis") {
      const fresh = this.local || this.phase === "hidden" || this.phase === "error"
        || (this.phase === "result" && this.main !== "speaking");
      if (fresh) { this.local = false; this.clearReply(); }
      this.appendReply(ws, now);
      if (this.main === "speaking") { this.startSpeaking(now); this.show("speaking", now); }
      else if (this.pendingSince === null) this.pendingSince = now;
      if (this.phase === "thinking" || this.phase === "listening") this.hideAt = null;   // a reply is on its way
    }
  }

  appendReply(ws, now) {
    let cursor = Math.max(this.replyCursor, this.speechClock(now));
    for (const w of ws) { this.reply.push({ w, at: cursor }); cursor += (w.length + 1) * this.o.MS_PER_CHAR; }
    this.replyCursor = cursor;
  }

  /** What a tool is busy with ("opening Spotify"); "" when it finished. */
  doing(text, now) {
    const chip = chipText(text, this.o.CHIP_MAX);
    this.eventAt = now;
    if (chip === this.chip) return;
    this.chip = chip;
    if (chip && (this.phase === "hidden" || this.phase === "result")) { this.show("thinking", now); this.label = LABELS.working; }
    else if (chip && this.phase === "thinking") this.hideAt = null;
    else if (!chip && this.phase === "thinking" && this.main === "idle") this.hideAt = now + this.o.THINK_LINGER_MS;
  }

  /** Loudness 0..1: the microphone while he listens, his voice while he speaks. */
  level(value, now = null) {
    const v = Number(value); this.target = Number.isFinite(v) ? Math.max(0, Math.min(1, v)) : 0;
    if (now !== null) this.eventAt = now;
  }

  /** One of his own reactions (a poke reply). `silent`: no voice came, so the words are simply shown. */
  quip(text, now, { silent = false } = {}) {
    if (this.main !== "idle" || (this.phase !== "hidden" && !this.local && this.phase !== "result")) return false;
    const ws = words(text);
    if (!ws.length) return false;
    this.clearHeard(); this.clearReply(); this.chip = "";
    this.local = true;
    this.appendReply(ws, now);
    if (silent) { this.show("result", now); this.hideAt = now + this.o.QUIP_HOLD_MS + Math.min(this.o.READ_MAX_MS, text.length * this.o.READ_MS_PER_CHAR); }
    else { this.startSpeaking(now); this.show("speaking", now); this.hideAt = now + this.replyCursor + this.o.HOLD_MS; }   // safety net
    return true;
  }
  /** His own reaction finished playing (or was cut off). */
  quipEnd(now) {
    if (!this.local || this.phase !== "speaking") return;
    this.stopSpeaking(now);
    this.show("result", now);
    this.hideAt = now + this.o.QUIP_HOLD_MS;
  }

  /** Advance to `now` and return what to draw. */
  tick(now) {
    // level smoothing: quick to rise, slower to fall
    const dt = this.levelAt === null ? 16 : Math.max(0, Math.min(250, now - this.levelAt));
    this.levelAt = now;
    const tau = this.target > this.smooth ? this.o.LEVEL_ATTACK_MS : this.o.LEVEL_RELEASE_MS;
    this.smooth += (this.target - this.smooth) * (1 - Math.exp(-dt / tau));
    if (this.smooth < 0.004) this.smooth = 0;

    // he stopped speaking and the pause is a real one: show the whole reply and leave, or go back to thinking
    if (!this.local && this.phase === "speaking" && this.main !== "speaking" && this.speechEndedAt !== null
        && now - this.speechEndedAt >= this.o.SPEECH_GAP_MS) {
      if (this.main === "thinking" || this.main === "working") {
        this.show("thinking", now);
        this.label = LABELS[this.main];
      } else {
        const clock = this.speechClock(now);
        const unseen = this.reply.filter((x) => x.at > clock).reduce((n, x) => n + x.w.length + 1, 0);
        const ended = this.speechEndedAt;
        this.show("result", now);
        this.hideAt = ended + this.o.HOLD_MS + Math.min(this.o.READ_MAX_MS, unseen * this.o.READ_MS_PER_CHAR);
      }
    }
    // watchdog: a lost "idle" event must not leave the card up (and his window grown) for ever
    if (!this.local && ((this.phase === "listening" && now - this.eventAt > this.o.LISTEN_MAX_QUIET_MS)
        || (this.phase === "speaking" && this.main === "speaking" && now - this.eventAt > this.o.SPEAK_MAX_QUIET_MS))) {
      this.main = "idle"; this.idleSince = now; this.stopSpeaking(now); this.hide(now);
    }
    // nothing may leave it stuck: idle for a long while with only a stale chip
    if (this.phase === "thinking" && this.main === "idle" && this.hideAt === null && now - this.idleSince > this.o.STUCK_MS) this.hide(now);
    // a reply that never got a voice is shown anyway
    if (this.pendingSince !== null && this.main !== "speaking" && now - this.pendingSince >= this.o.PENDING_REPLY_MS) {
      const chars = this.reply.reduce((n, x) => n + x.w.length + 1, 0);
      this.pendingSince = null;
      this.show("result", now);
      this.hideAt = now + this.o.HOLD_MS + Math.min(this.o.READ_MAX_MS, chars * this.o.READ_MS_PER_CHAR);
    }
    if (this.hideAt !== null && now >= this.hideAt) this.hide(now);
    return this.view(now);
  }

  view(now) {
    const phase = this.phase;
    const clock = this.speechClock(now);
    const all = phase === "result";
    const replyShown = all ? this.reply.length : this.reply.filter((x) => x.at <= clock).length;
    const heardShown = this.heard.filter((x) => x.at <= now).length;
    const showReply = (phase === "speaking" || phase === "result") && this.reply.length > 0;
    return {
      visible: phase !== "hidden",
      phase,
      label: this.label,
      text: showReply ? "reply" : this.heard.length && phase !== "error" ? "heard" : "none",
      words: showReply ? this.reply.map((x) => x.w) : phase === "error" ? [] : this.heard.map((x) => x.w),
      shown: showReply ? replyShown : phase === "error" ? 0 : heardShown,
      chip: phase === "thinking" || phase === "listening" ? this.chip : "",
      busy: phase === "thinking",
      level: phase === "listening" || phase === "speaking" ? this.smooth : 0,
      local: this.local,
      seq: this.seq,
    };
  }
}

/**
 * Where the overlay card goes in the box the host made beside him (CSS px). The box is as tall as he is (or as tall
 * as the card when he is small) and its bottom is level with his feet; the card sits at head-and-chest height.
 * -> { boxW, boxH } to ask the host for, and cardTop(boxH) for the card inside it.
 */
export function overlayBox(robotH, { cardW = 336, cardH = 104, gap = 10 } = {}) {
  const boxH = Math.max(cardH, Math.round(robotH));
  const centre = boxH - robotH + robotH * 0.36;                      // his chest, measured from the box top
  const top = Math.max(0, Math.min(boxH - cardH, Math.round(centre - cardH / 2)));
  return { boxW: cardW + gap, boxH, cardTop: top, cardW, cardH, gap };
}
