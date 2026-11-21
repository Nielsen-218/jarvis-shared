"""Local, evidence-bound coaching. Non-blocking source, bounded output.

No calls to game memory, hooks, automation, network or external AI services.
The sink may queue speech; 'delivered' does NOT imply sound reached speakers.
"""
from __future__ import annotations
from collections import deque
from dataclasses import dataclass, field
from math import isfinite
from threading import Condition, Thread
from time import monotonic
from typing import Callable

@dataclass(frozen=True)
class Observation:
    kind: str
    captured_at: float
    confidence: float
    evidence: str
    source: str = "pixels"
    context: str = ""

@dataclass(frozen=True)
class Callout:
    kind: str
    text: str
    captured_at: float
    emitted_at: float
    source: str

MESSAGES = {
    "low_health": "Low health. Find cover.",
    "low_shield": "Low shields. Reset behind cover.",
    "low_ammo": "Ammo low. Reset or switch weapons.",
    "zone_warning": "Ring warning visible. Rotate soon.",
    "teammate_down": "Teammate down. Check cover.",
    "break_sightline": "You're exposed and weak. Break line of sight!",
    "avoid_open_revive": "Don't revive in the open. Secure cover first.",
    "avoid_pinch": "Two visible threats. Break the angle; isolate one.",
    "retreat_outnumbered": "Outnumbered. Back off; take a one-on-one angle.",
    "rotate_zone": "Zone is moving. Disengage and rotate.",
    "play_objective": "Time is low. Play objective, not the chase.",
    "reset_shields": "You've got cover. Recharge before re-peeking.",
    "reload_in_cover": "Low ammo. Use cover to reload or switch weapons.",
    "regroup": "You're split. Regroup before you challenge.",
    "convert_knock": "Knock confirmed. Coordinate the push; don't solo chase.",
    "take_height": "High ground is open. Consider taking it.",
    "contested_low_health": "You're low and an enemy's visible. Break contact—reset.",
    "contested_low_shields": "Enemy in sight. Shields low—avoid the re-peek.",
    "contested_low_ammo": "Enemy in sight, low ammo. Make space or switch weapons.",
    "unsafe_revive_threat": "Teammate down, enemy visible. Secure the fight first.",
    "ring_split_regroup": "Ring's moving, squad's split. Regroup as you rotate.",
    "knock_split_discipline": "Knock confirmed, but you're split. Don't solo chase.",
}
PRIORITY = {"low_health": 5, "teammate_down": 4, "zone_warning": 3,
            "low_shield": 2, "low_ammo": 1,
            "break_sightline": 6, "avoid_open_revive": 6, "avoid_pinch": 6,
            "retreat_outnumbered": 5, "rotate_zone": 5, "play_objective": 5,
            "reset_shields": 4, "reload_in_cover": 4, "regroup": 4,
            "convert_knock": 3, "take_height": 2,
    "contested_low_health": 5, "contested_low_shields": 4,
    "contested_low_ammo": 4, "unsafe_revive_threat": 4,
    "ring_split_regroup": 5, "knock_split_discipline": 4}

@dataclass
class CoachConfig:
    max_age_ms: float = 650.0
    min_confidence: float = 0.85
    global_cooldown_s: float = 3.0
    repeated_kind_cooldown_s: float = 12.0
    max_callouts_per_minute: int = 10
    quiet: bool = False

@dataclass
class CoachStats:
    observations: int = 0
    calls: int = 0
    rejected_stale: int = 0
    rejected_unsupported: int = 0
    rejected_weak: int = 0
    suppressed: int = 0
    overwritten: int = 0
    discarded_lower_priority: int = 0
    errors: int = 0
    # Bounded: prevent unbounded memory growth after hours of gameplay.
    latency_ms: deque[float] = field(default_factory=lambda: deque(maxlen=2048))

    def summary(self) -> dict:
        samples = sorted(self.latency_ms)
        def percentile(p: float) -> float | None:
            if not samples:
                return None
            return round(samples[min(len(samples) - 1, int((len(samples)-1)*p))], 3)
        return {
            "observations": self.observations, "callouts_queued": self.calls,
            "stale": self.rejected_stale, "unsupported": self.rejected_unsupported,
            "weak": self.rejected_weak, "suppressed": self.suppressed,
            "overwritten": self.overwritten,
            "discarded_lower_priority": self.discarded_lower_priority,
            "errors": self.errors,
            "p50_event_to_sink_return_ms": percentile(.5),
            "p95_event_to_sink_return_ms": percentile(.95),
            "p99_event_to_sink_return_ms": percentile(.99),
            "measurement_note": "Sink completion, NOT TTS engine start or audible onset",
        }

class Policy:
    """Evidence, time, confidence, cooldown, rate limit. Called from one worker."""
    def __init__(self, config: CoachConfig | None = None,
                 clock: Callable[[], float] = monotonic):
        self.config = config or CoachConfig()
        self.clock = clock
        self.last_global = -float("inf")
        self.last_priority = -1
        self.last_kind: dict[str, float] = {}
        self.recent_calls: deque[float] = deque()
        self.stats = CoachStats()

    def decide(self, obs: Observation) -> Callout | None:
        now = self.clock()
        self.stats.observations += 1
        if (obs.kind not in MESSAGES or not isinstance(obs.evidence, str)
                or not obs.evidence.strip() or obs.source not in {"pixels", "audio", "user"}):
            self.stats.rejected_unsupported += 1
            return None
        if not isfinite(obs.confidence) or obs.confidence < self.config.min_confidence:
            self.stats.rejected_weak += 1
            return None
        if not isfinite(obs.captured_at):
            self.stats.rejected_stale += 1
            return None
        age_ms = (now - obs.captured_at) * 1000
        if age_ms < -30 or age_ms > self.config.max_age_ms:
            self.stats.rejected_stale += 1
            return None
        while self.recent_calls and now - self.recent_calls[0] >= 60:
            self.recent_calls.popleft()
        priority=PRIORITY[obs.kind]
        # A new more urgent warning can preempt the global speech cooldown.
        # Its own kind cooldown still applies, preventing repeated shouting.
        new_urgent=(priority>self.last_priority and priority>=4)
        if (self.config.quiet or
                (not new_urgent and now-self.last_global < self.config.global_cooldown_s) or
                now-self.last_kind.get(obs.kind, -float("inf")) < self.config.repeated_kind_cooldown_s or
                (not new_urgent and len(self.recent_calls) >= self.config.max_callouts_per_minute)):
            self.stats.suppressed += 1
            return None
        self.last_priority=priority
        self.last_global = now
        self.last_kind[obs.kind] = now
        self.recent_calls.append(now)
        return Callout(obs.kind, MESSAGES[obs.kind], obs.captured_at, now, obs.source)

class LiveCoach:
    """Bounded priority-aware pending work; producer never waits for the sink."""
    def __init__(self, sink: Callable[[Callout], None], policy: Policy | None = None,
                 clock: Callable[[], float] = monotonic):
        self.sink, self.policy = sink, policy or Policy()
        self.clock = clock
        self._cv = Condition()
        self._pending: Observation | None = None
        self._running = False
        self._worker: Thread | None = None

    def start(self):
        with self._cv:
            if self._running:
                return
            if self._worker and self._worker.is_alive():
                raise RuntimeError("Previous coach worker is still terminating")
            self._running = True
            self._worker = Thread(target=self._run, daemon=True, name="jarvis-gamecoach")
            self._worker.start()

    def submit(self, observation: Observation) -> bool:
        with self._cv:
            if not self._running:
                return False
            pending = self._pending
            if pending is not None:
                age_ms = (self.clock()-pending.captured_at)*1000
                if (age_ms < self.policy.config.max_age_ms
                        and PRIORITY.get(observation.kind, 0) < PRIORITY.get(pending.kind, 0)):
                    self.policy.stats.discarded_lower_priority += 1
                    return False
                self.policy.stats.overwritten += 1
            self._pending = observation
            self._cv.notify()
            return True

    def _run(self):
        while True:
            with self._cv:
                self._cv.wait_for(lambda: self._pending is not None or not self._running)
                if not self._running:
                    return
                obs, self._pending = self._pending, None
            if obs is None:
                continue
            call = self.policy.decide(obs)
            if call:
                if (self.clock()-call.captured_at)*1000 > self.policy.config.max_age_ms:
                    self.policy.stats.rejected_stale += 1
                    continue
                try:
                    self.sink(call)
                    self.policy.stats.calls += 1
                    self.policy.stats.latency_ms.append((self.clock()-call.captured_at)*1000)
                except Exception:
                    self.policy.stats.errors += 1

    def stop(self, join_timeout: float = 1):
        with self._cv:
            self._running = False
            self._pending = None
            self._cv.notify_all()
        if self._worker:
            self._worker.join(join_timeout)

@dataclass
class AdaptiveSampler:
    fps: int = 6
    min_fps: int = 1
    max_fps: int = 8
    slow_budget_ms: float = 20.0
    stable_steps: int = 0
    failures: int = 0
    unavailable: bool = False
    # Automatic shedding on sustained capture failure. Never silently thrash.
    failure_limit: int = 15

    def feedback(self, capture_analysis_ms: float, frame_time_spike: bool = False,
                 capture_ok: bool = True) -> int:
        if not capture_ok:
            self.failures += 1
            if self.failures >= self.failure_limit:
                self.unavailable = True
        else:
            self.failures = 0
        if frame_time_spike or capture_analysis_ms > self.slow_budget_ms or not capture_ok:
            self.fps = max(self.min_fps, self.fps//2)
            self.stable_steps = 0
        else:
            self.stable_steps += 1
            if self.stable_steps >= 20:
                self.fps = min(self.max_fps, self.fps+1)
                self.stable_steps = 0
        return self.fps
