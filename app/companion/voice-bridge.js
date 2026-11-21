// Voice bridge: real amplitude from in-browser audio playback.
//
// Problem: when the companion plays a quip through an <audio> element
// (api.say() -> URL -> <audio>.play()), the Python-side TTS output_level is
// zero (the main loop's `speaker` isn't the one playing). So the mouth
// would never move during companion quips.
//
// This bridge wraps any HTMLMediaElement in a Web Audio AnalyserNode and
// publishes RMS amplitude via the ``onLevel`` callback. Honest label: this
// is amplitude synchronization, not viseme / phoneme lip sync.
//
// Pure JS logic (AmplitudeMeter.rms) is split out and unit-tested under
// Node — no AudioContext, no headless browser needed. See
// tests/node/test_amplitude.mjs.

const DEFAULT_FFT = 1024;

/** Compute the RMS of an array of unsigned-byte samples (0..255 centered on
 *  128, as returned by AnalyserNode.getByteTimeDomainData). */
export function rmsFromByteTimeDomain(buf) {
  if (!buf || !buf.length) return 0;
  let sum = 0;
  for (let i = 0; i < buf.length; i++) {
    const v = (buf[i] - 128) / 128;
    sum += v * v;
  }
  return Math.sqrt(sum / buf.length);
}

/** Compute RMS of a Float32Array (-1..1). Used in the Node tests to
 *  verify against synthetic PCM, and by the real-time path when the
 *  browser supports getFloatTimeDomainData. */
export function rmsFromFloat(buf) {
  if (!buf || !buf.length) return 0;
  let sum = 0;
  for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
  return Math.sqrt(sum / buf.length);
}

/** Clamp + optional gain so a quiet voice still animates the mouth. */
export function normaliseLevel(rms, gain = 3.0) {
  const v = rms * gain;
  return v < 0 ? 0 : v > 1 ? 1 : v;
}

export class AmplitudeMeter {
  /** `source` is an HTMLMediaElement. `onLevel(value)` receives [0,1] values.
   *  `onEnd()` is invoked when the audio finishes (so the mouth closes). */
  constructor(source, { onLevel, onEnd, fftSize = DEFAULT_FFT, gain = 3.0 } = {}) {
    this.source = source;
    this.onLevel = onLevel || (() => {});
    this.onEnd = onEnd || (() => {});
    this.fftSize = fftSize;
    this.gain = gain;
    this.ctx = null;
    this.analyser = null;
    this.node = null;
    this.buf = null;
    this._raf = 0;
    this._attached = false;
    this._epoch = 0;
    this._bound = {
      play: () => this.start(),
      playing: () => this.start(),
      waiting: () => this.stop(true),
      emptied: () => this.stop(true),
      error: () => { this.stop(true); this.onEnd(); },
      pause: () => this.stop(/*emitZero*/ true),
      ended: () => { this.stop(/*emitZero*/ true); this.onEnd(); },
    };
  }

  attach() {
    if (!this.source || this._attached) return;
    this._attached = true;
    for (const [event, handler] of Object.entries(this._bound)) this.source.addEventListener(event, handler);
  }

  detach() {
    this.stop(true);
    if (!this.source || !this._attached) return;
    for (const [event, handler] of Object.entries(this._bound)) this.source.removeEventListener(event, handler);
    this._attached = false;
  }

  _ensureCtx() {
    if (this.ctx) return;
    const Ctor = window.AudioContext || window.webkitAudioContext;
    if (!Ctor) throw new Error("AudioContext unavailable");
    this.ctx = new Ctor();
    this.node = this.ctx.createMediaElementSource(this.source);
    this.analyser = this.ctx.createAnalyser();
    this.analyser.fftSize = this.fftSize;
    this.node.connect(this.analyser);
    this.analyser.connect(this.ctx.destination);
    this.buf = new Uint8Array(this.analyser.fftSize);
  }

  start() {
    try { this._ensureCtx(); } catch (e) { console.warn("[voice-bridge]", e); return; }
    if (this.ctx.state === "suspended") this.ctx.resume().catch(() => {});
    cancelAnimationFrame(this._raf);
    const epoch = ++this._epoch;
    const tick = () => {
      if (epoch !== this._epoch || !this.analyser) return;
      if (this.source.paused || this.source.ended) { this.stop(true); return; }
      this.analyser.getByteTimeDomainData(this.buf);
      const rms = rmsFromByteTimeDomain(this.buf);
      this.onLevel(normaliseLevel(rms, this.gain));
      this._raf = requestAnimationFrame(tick);
    };
    this._raf = requestAnimationFrame(tick);
  }

  stop(emitZero = false) {
    ++this._epoch;
    cancelAnimationFrame(this._raf);
    this._raf = 0;
    if (emitZero) this.onLevel(0);
  }
}

export const _internals = { DEFAULT_FFT };
