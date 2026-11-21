// Offline synthetic PCM verification for the Web-Audio voice bridge.
//
// The AmplitudeMeter class in app/companion/voice-bridge.js can only
// be instantiated against an HTMLMediaElement + AudioContext (which Node
// doesn't have), but its pure RMS helpers CAN be tested here. We feed
// synthetic PCM (sine, DC, silence) and verify the math matches a
// hand-computed answer. That's enough to prove the quip-amplitude path
// isn't fabricating a signal; the AudioContext wiring is exercised by
// Codex visually at the harness.
import {
  rmsFromByteTimeDomain, rmsFromFloat, normaliseLevel, _internals,
} from "../../app/companion/voice-bridge.js";

let fails = [];
let passes = 0;
function assert(cond, name, detail = "") {
  if (cond) passes += 1;
  else fails.push({ name, detail });
}
function approx(a, b, eps = 1e-3) { return Math.abs(a - b) < eps; }

// ----- Silence --------------------------------------------------
assert(rmsFromByteTimeDomain(new Uint8Array(1024).fill(128)) === 0,
       "silence (byte 128) -> 0");
assert(rmsFromFloat(new Float32Array(1024)) === 0,
       "silence (float 0) -> 0");

// ----- Pure 1kHz sine @ 44.1kHz, amplitude 0.5 -> expected RMS ~0.3536 ---
{
  const N = 4096;
  const buf = new Float32Array(N);
  for (let i = 0; i < N; i++) buf[i] = 0.5 * Math.sin(2 * Math.PI * 1000 * i / 44100);
  const rms = rmsFromFloat(buf);
  assert(approx(rms, 0.5 / Math.sqrt(2), 2e-3),
         "sine amplitude 0.5 -> RMS ~0.354", `rms=${rms}`);
}

// ----- Byte-time-domain equivalent of the above sine ------------
{
  const N = 4096;
  const buf = new Uint8Array(N);
  for (let i = 0; i < N; i++) {
    const s = 0.5 * Math.sin(2 * Math.PI * 1000 * i / 44100);
    buf[i] = Math.round(128 + s * 128);
  }
  const rms = rmsFromByteTimeDomain(buf);
  assert(approx(rms, 0.5 / Math.sqrt(2), 5e-3),
         "sine (byte-domain) -> RMS ~0.354", `rms=${rms}`);
}

// ----- Full-scale square wave: RMS = 1 --------------------------
{
  const N = 1024;
  const buf = new Float32Array(N);
  for (let i = 0; i < N; i++) buf[i] = (i % 2 === 0) ? 1 : -1;
  assert(approx(rmsFromFloat(buf), 1.0, 1e-6), "square +/-1 -> RMS 1");
}

// ----- Clamping / gain --------------------------------------------
assert(normaliseLevel(0) === 0, "level 0 -> 0");
assert(approx(normaliseLevel(0.2, 3.0), 0.6, 1e-9), "gain applies");
assert(normaliseLevel(0.5, 3.0) === 1.0, "clamps at 1");
assert(normaliseLevel(-5) === 0, "negative clamps at 0");

// ----- Internals surface stable ---------------------------------
assert(typeof _internals.DEFAULT_FFT === "number", "DEFAULT_FFT exported");

// -----  Integration: synthetic PCM "WAV" -> CompanionState.jaw ----
// This is the end-to-end contract behavior.js relies on for in-browser
// quip playback: feed real amplitude into applyEvent, watch jaw open,
// then stop feeding and watch the stale timeout close it. No live audio,
// no paid service — just the pure JS state machine driven by RMS values
// computed from synthetic PCM via the same voice-bridge helpers.
import { CompanionState, LEVEL_STALE_MS } from "../../app/companion/behavior.js";

{
  // "WAV" = a 200ms burst of 1kHz sine at amplitude 0.6, followed by silence.
  const sr = 24000;
  const burstN = Math.floor(0.2 * sr);
  const silenceN = Math.floor(0.3 * sr);
  const burst = new Float32Array(burstN);
  for (let i = 0; i < burstN; i++) burst[i] = 0.6 * Math.sin(2 * Math.PI * 1000 * i / sr);
  const silence = new Float32Array(silenceN);

  // Fake clock we drive the state with.
  let simNow = 0;
  const st = new CompanionState({ now: () => simNow, levelTimeoutMs: LEVEL_STALE_MS });
  st.applyEvent({ type: "state", value: "speaking" });

  // Chop the burst into frames and feed per-frame RMS into applyEvent.
  const frameMs = 40;                                      // matches the voice-bridge interval
  const frameSamples = Math.floor(sr * frameMs / 1000);
  for (let i = 0; i < burstN; i += frameSamples) {
    const slice = burst.subarray(i, i + frameSamples);
    const rms = rmsFromFloat(slice);
    const level = normaliseLevel(rms, 3.0);
    st.applyEvent({ type: "level", value: level });
    st.updateMouth(simNow, 0.04);
    simNow += frameMs;
  }
  const jawDuringBurst = st.jaw;
  assert(jawDuringBurst > 0.4,
         `jaw opens during real-PCM burst (jaw=${jawDuringBurst})`);

  // Stop feeding levels; let the stale timeout close the mouth.
  simNow += LEVEL_STALE_MS + 50;
  for (let i = 0; i < 20; i++) {
    st.updateMouth(simNow + i * 20, 0.02);
  }
  const jawAfterSilence = st.jaw;
  assert(jawAfterSilence < 0.05,
         `jaw closes after producer stall (jaw=${jawAfterSilence})`);

  // Interrupt scenario: level 0 + state leaves speaking must zero the jaw
  // over a short decay (CompanionState.updateMouth lerps toward target).
  st.applyEvent({ type: "state", value: "speaking" });
  st.applyEvent({ type: "level", value: 0.7 });
  for (let i = 0; i < 6; i++) st.updateMouth(simNow + i * 20, 0.02);
  assert(st.jaw > 0.2, `jaw re-opens after resuming speaking (${st.jaw})`);
  st.applyEvent({ type: "state", value: "idle" });
  for (let i = 0; i < 30; i++) st.updateMouth(simNow + 200 + i * 20, 0.02);
  assert(st.jaw < 0.05, `jaw closes after state leaves speaking (${st.jaw})`);
}

// ----- Report ----------------------------------------------------
// Field names match tests/test_companion_behavior_js.py:test_js_amplitude_suite_passes.
console.log(JSON.stringify({
  amplitude_passed: passes,
  amplitude_failed: fails.length,
  failures: fails,
}));
if (fails.length) process.exit(1);
