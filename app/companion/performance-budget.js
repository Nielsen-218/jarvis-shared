// Pure runtime caps for the EXISTING renderer/scheduler; owns no timers or loop.
// Feed render + synchronous readPixels cost, with monotonic milliseconds (e.g.
// performance.now()). active=true means active rendering, false means idle.
// While suspended, do not sample, or pass active=null / "suspended". Gaps over
// two seconds reset measurement history without changing the current caps.
// Construct a new budget when owner settings change; never persist its output.
const DPR_FACTORS = [1, 0.8, 0.65, 0.65, 0.65];
const FPS_FACTORS = [1, 1, 1, 0.8, 0.6];
const MAX_GAP_MS = 2000;
const SLOW_MS = 2000;
const HEALTHY_MS = 15000;
const CHANGE_DWELL_MS = 4000;

function ceiling(value, fallback) {
  return typeof value === "number" && Number.isFinite(value) && value > 0 ? value : fallback;
}

export class PerformanceBudget {
  constructor({ baseDpr = 1.5, idleFps = 20, activeFps = 30 } = {}) {
    // Immutable owner ceilings; every returned runtime value stays <= these.
    this._base = Object.freeze({
      dpr: ceiling(baseDpr, 1.5),
      idleFps: ceiling(idleFps, 20),
      activeFps: ceiling(activeFps, 30),
    });
    this._level = 0;
    this._last = null;
    this._lastChange = null;
    this._resetHistory();
  }

  _resetHistory() {
    this._ewma = null;
    this._slowMs = this._healthyMs = 0;
    this._slowSamples = this._healthySamples = 0;
    this._mode = null;
  }

  get state() { return this.snapshot(); }

  snapshot() {
    const b = this._base, i = this._level;
    return {
      dpr: Math.min(b.dpr, Math.max(Math.min(0.5, b.dpr), b.dpr * DPR_FACTORS[i])),
      idleFps: Math.min(b.idleFps, Math.max(Math.min(1, b.idleFps), b.idleFps * FPS_FACTORS[i])),
      activeFps: Math.min(b.activeFps, Math.max(Math.min(1, b.activeFps), b.activeFps * FPS_FACTORS[i])),
      level: i,
    };
  }

  sample(costMs, now, active) {
    if (active !== true && active !== false) {
      this._resetHistory();
      this._last = null;
      this._lastChange = null;
      return this.snapshot();
    }
    if (typeof costMs !== "number" || !Number.isFinite(costMs) || costMs < 0
        || typeof now !== "number" || !Number.isFinite(now) || now < 0) return this.snapshot();

    const delta = this._last === null ? 0 : now - this._last;
    if (this._last !== null && delta === 0) return this.snapshot();
    if (delta < 0) {
      this._last = now;
      this._lastChange = null;
      this._resetHistory();
      return this.snapshot();
    }
    this._last = now;
    if (delta > MAX_GAP_MS || this._mode !== active) {
      this._resetHistory();
      // A clock discontinuity cannot count as healthy/slow render time.
      if (delta > MAX_GAP_MS) this._lastChange = null;
      this._mode = active;
      return this.snapshot();
    }

    // Use original FPS ceilings, not the reduced output: otherwise slowing FPS
    // would itself look healthy and repeatedly undo its own mitigation.
    const fps = active ? this._base.activeFps : this._base.idleFps;
    const targetMs = Math.min(24, 500 / fps);
    const pressure = Math.min(8, costMs / targetMs);
    // A bounded time-aware EWMA; one enormous finite cost cannot poison it.
    const dt = Math.min(delta, 100); // sparse samples cannot manufacture a long streak
    const alpha = 1 - Math.exp(-dt / 500);
    this._ewma = this._ewma === null ? pressure : this._ewma + alpha * (pressure - this._ewma);
    if (this._ewma > 1.15) {
      this._slowMs += dt;
      this._slowSamples++;
      this._healthyMs = this._healthySamples = 0;
    } else if (this._ewma < 0.65) {
      this._healthyMs += dt;
      this._healthySamples++;
      this._slowMs = this._slowSamples = 0;
    } else {
      this._slowMs = this._healthyMs = 0;
      this._slowSamples = this._healthySamples = 0;
    }
    if (this._lastChange !== null && now - this._lastChange < CHANGE_DWELL_MS) return this.snapshot();

    let next = this._level;
    if (this._slowMs >= SLOW_MS && this._slowSamples >= 24) next = Math.min(4, next + 1);
    else if (this._healthyMs >= HEALTHY_MS && this._healthySamples >= 120) next = Math.max(0, next - 1);
    if (next !== this._level) {
      this._level = next;
      this._lastChange = now;
      this._resetHistory();
      this._mode = active;
    }
    return this.snapshot();
  }
}
