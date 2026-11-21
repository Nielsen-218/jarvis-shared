// One generation owns local requests, media playback and visual state together.
// Feed main state packets at ingress; local state callbacks must not loop back.
//
// say() is the entry for his own reactions (a poke, a landing, a catch): when the main Jarvis voice is busy, or he
// is still saying his previous line, the reaction WAITS in a short queue and is said once he has been free for a
// moment. Nothing is talked over and nothing is lost in silence; a line that waited too long is dropped with a
// trace record (it would no longer make sense).
export class SpeechSession {
  constructor({ audio, generate, onState = () => {}, onLevel = () => {}, onLine = () => {}, onDrop = () => {},
                now = () => performance.now(), timeoutMs = 30000,
                queueMax = 2, queueAgeMs = 8000, settleMs = 450, gapMs = 180,
                setTimer = (fn, ms) => setTimeout(fn, ms), clearTimer = (id) => clearTimeout(id) } = {}) {
    Object.assign(this, { audio, generate, onState, onLevel, onLine, onDrop, now, timeoutMs,
      queueMax, queueAgeMs, settleMs, gapMs, setTimer, clearTimer });
    this.generation = 0;
    this.mainState = 'idle';
    this.active = false;
    this.trace = [];
    this.cleanup = () => {};
    this.queue = [];              // [{ text, key, at }] reactions waiting for a free moment
    this.freeSince = -Infinity;   // when the main voice last went quiet (never yet)
    this.drainTimer = null;
    this.starting = false;
  }
  record(event) {
    this.trace.push({ event, at_ms: this.now(), generation: this.generation });
    if (this.trace.length > 128) this.trace.shift();
  }
  blocked() { return !['idle', 'sleeping'].includes(this.mainState); }
  setMainState(state) {
    const was = this.blocked();
    this.mainState = state;
    if (state === 'listening') this.clearQueue();      // the owner is talking to him now: an old reaction would be noise
    if (this.blocked()) this.cancel(false);
    else {
      if (was) this.freeSince = this.now();
      this.kick(this.settleMs);
    }
  }
  /** His own reaction: said now when he is free, otherwise queued (newest `key` replaces an older one of the same
   *  key, so a burst of pokes leaves one pending line, not a backlog). -> true | false | 'queued'. */
  async say(text, { key = null } = {}) {
    text = String(text || '');
    if (!text) return false;
    if (!this.blocked() && !this.active && !this.queue.length && this.now() - this.freeSince >= this.settleMs) {
      return this.speak(text);
    }
    if (key !== null) this.queue = this.queue.filter(q => q.key !== key);
    this.queue.push({ text, key, at: this.now() });
    while (this.queue.length > this.queueMax) { const lost = this.queue.shift(); this.record('queue_overflow'); this.onDrop(lost.text, 'overflow'); }
    this.record('queued');
    this.kick(this.blocked() ? this.settleMs : this.gapMs);
    return 'queued';
  }
  /** Forget waiting reactions (all of them, or those with `key`): he was hidden, or the moment has passed. */
  clearQueue(key = null) {
    const before = this.queue.length;
    this.queue = key === null ? [] : this.queue.filter(q => q.key !== key);
    if (before !== this.queue.length) this.record('queue_cleared');
    if (!this.queue.length && this.drainTimer !== null) { this.clearTimer(this.drainTimer); this.drainTimer = null; }
    return before - this.queue.length;
  }
  kick(delayMs) {
    if (!this.queue.length || this.drainTimer !== null) return;
    this.drainTimer = this.setTimer(() => { this.drainTimer = null; this.drain(); }, Math.max(0, delayMs));
  }
  /** Say the oldest reaction that is still fresh, once he is free and the main voice has been quiet a moment. */
  drain() {
    const now = this.now();
    while (this.queue.length && now - this.queue[0].at > this.queueAgeMs) {
      const stale = this.queue.shift();
      this.record('queue_expired');
      this.onDrop(stale.text, 'expired');
    }
    if (!this.queue.length || this.blocked() || this.active) return false;   // busy again: the next free moment kicks
    const wait = this.settleMs - (now - this.freeSince);
    if (wait > 0) { this.kick(wait); return false; }
    const next = this.queue.shift();
    this.record('queue_play');
    this.speak(next.text);
    return true;
  }
  current(token) { return token !== null && this.active && token === this.generation && !this.blocked(); }
  cancel(restore = true) {
    ++this.generation;
    const owned = this.active;
    this.active = false;
    this.cleanup();
    this.cleanup = () => {};
    this.audio.pause();
    this.onLevel(0);
    if (owned && restore && !this.blocked()) this.onState(this.mainState);
    this.record('cancel');
    if (!this.starting) this.kick(this.gapMs);        // he finished (or was stopped): a waiting reaction is next
  }
  begin(state) {
    if (this.blocked()) return null;
    this.starting = true;
    try { this.cancel(); } finally { this.starting = false; }
    const token = this.generation;
    this.active = true;
    let release;
    this.cancelled = new Promise(resolve => { release = () => resolve(null); });
    const timer = setTimeout(() => { if (this.current(token)) this.cancel(); }, this.timeoutMs);
    this.cleanup = () => { clearTimeout(timer); release(); };
    this.record('request_start');
    if (state) this.onState(state);
    return token;
  }
  state(token, value) { if (this.current(token)) this.onState(value); }
  finish(token) { if (this.current(token)) this.cancel(); }
  async speak(text) { return this.run(() => this.generate(text), undefined, text); }
  async play(url, token) { return this.run(() => url, token); }
  async run(resolveUrl, token = undefined, line = null) {
    if (token === undefined) token = this.begin();
    if (!this.current(token)) return false;
    const cancelled = this.cancelled;
    this.record('generate');
    try {
      const url = await Promise.race([Promise.resolve().then(() => this.current(token) ? resolveUrl() : null), cancelled]);
      if (!this.current(token)) return false;
      if (!url) {                                       // no voice came back: the words are shown instead of lost
        this.record('no_audio');
        if (line !== null) this.onLine(line, { silent: true });
        this.finish(token);
        return false;
      }
      const finish = () => this.finish(token);
      // Media events are queued tasks: an old pause may reach the new listener.
      // Inspect the element's CURRENT state before treating it as terminal.
      const handlers = {
        pause: () => { if (this.audio.paused) finish(); },
        ended: () => { if (this.audio.ended) finish(); },
        error: () => { if (this.audio.error) finish(); },
      };
      const cleanup = this.cleanup;
      this.cleanup = () => {
        cleanup();
        for (const [event, handler] of Object.entries(handlers)) this.audio.removeEventListener(event, handler);
      };
      for (const [event, handler] of Object.entries(handlers)) this.audio.addEventListener(event, handler);
      this.audio.src = url;
      this.state(token, 'speaking');
      if (!this.current(token)) return false;
      this.record('play_requested');
      await Promise.race([this.audio.play(), cancelled]);
      if (!this.current(token)) return false;
      this.record('play_started');
      if (line !== null) this.onLine(line, { silent: false });
      return true;
    } catch {
      this.record('play_failed');
      if (line !== null && this.current(token)) this.onLine(line, { silent: true });
      this.finish(token);
      return false;
    }
  }
  dispose() {
    this.queue = [];
    if (this.drainTimer !== null) { this.clearTimer(this.drainTimer); this.drainTimer = null; }
    this.cancel(false);
  }
}
