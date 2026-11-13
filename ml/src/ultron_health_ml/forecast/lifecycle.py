"""Forecast ownership, evidence-based recovery, and advisory deduplication."""

from dataclasses import dataclass
from math import ceil

from ultron_health_ml.hybrid.detector import Evidence


@dataclass(frozen=True)
class Point:
    subsystem: str
    method: str
    horizon: int
    threshold: float

    @property
    def name(self):
        return f"{self.subsystem}:{self.method}:{self.horizon}:{self.threshold:g}"


@dataclass
class ForecastEpisode:
    forecast_id: str
    subsystem: str
    target_version: str
    first_prediction: float
    last_confirmation: float
    horizon: int
    window_end: float
    score_type: str
    score: float
    estimated_seconds: float | None
    evidence_summary: dict
    state: str = "early_warning"
    detection_at: float | None = None
    cancelled_at: float | None = None
    closed_at: float | None = None
    reason: str | None = None


class Lifecycle:
    def __init__(self, run, point, target, version):
        self.run, self.point, self.target, self.version = run, point, target, version
        self.entry, self.clear = Evidence(), Evidence()
        self.owner = None
        self.detected = False
        self.next_allowed = float("-inf")
        self.episodes = []
        self.interactions = []
        self.raw = self.silent = self.unknown = self.suppressed = self.raw_starts = 0
        self.rows = 0
        self.was_raw = False
        self.state = "normal"

    def step(self, now, observed, fresh, value, signal, transition):
        self.rows += 1
        point, target = self.point, self.target
        owner = self.owner
        if owner and owner.state == "early_warning" and now > owner.window_end:
            owner.state, owner.reason = "expired", "horizon_elapsed"
        if transition == "actionable":
            self.detected = True
            linked = bool(owner and owner.state == "early_warning")
            if linked:
                owner.state = "detected"
                owner.detection_at = now
            self.interactions.append(dict(time=now, interaction="forecast_to_detection" if linked else "detection_only",
                                          forecast_id=owner.forecast_id if linked else None,
                                          advisory_interruptions=0 if linked else 1))
            self.entry.reset()
        elif transition == "recovered":
            self.detected = False
        sufficient = signal.sufficient and value is not None and observed is not None
        candidate = (sufficient and signal.score >= point.threshold
                     and (signal.eta_seconds is None or 0 < signal.eta_seconds <= point.horizon))
        if not sufficient:
            self.unknown += 1
            self.entry.reset()
            self.clear.reset()
            if self.owner is None and not self.detected:
                self.state = "observe"
            return
        observing = not self.detected and self.owner is None and (candidate or signal.score >= point.threshold * .65)
        if observing:
            self.silent += 1
        self.state = (self.owner.state if self.owner else "detected" if self.detected
                      else "observe" if observing else "normal")
        if candidate:
            self.raw += 1
            self.raw_starts += not self.was_raw
        self.was_raw = candidate
        if not fresh:
            return
        if owner:
            if candidate and owner.state == "early_warning":
                owner.last_confirmation = now
                self.clear.reset()
            elif not self.detected:
                normal = value >= target.recovery_boundary if target.direction == "low" else value <= target.recovery_boundary
                if normal and signal.score < point.threshold * .65:
                    self.clear.advance(observed, target.max_gap_seconds)
                    if self.clear.count >= 2 and self.clear.duration >= target.recovery_seconds:
                        if owner.state == "early_warning":
                            owner.state, owner.cancelled_at, owner.reason = "cancelled", now, "fresh_recovery"
                        elif owner.reason is None:
                            owner.reason = "fresh_recovery_after_detection"
                        owner.closed_at = now
                        self.owner = None
                        self.state = "normal"
                        self.next_allowed = now + (600 if point.subsystem == "disk_capacity" else 180)
                        self.entry.reset()
                        self.clear.reset()
                else:
                    self.clear.reset()
            if candidate:
                self.suppressed += 1
            return
        if candidate and not self.detected and now >= self.next_allowed:
            self.entry.advance(observed, target.max_gap_seconds)
            needed = 2 if point.subsystem == "disk_capacity" else 3
            duration = 180 if point.subsystem == "disk_capacity" else 60
            if self.entry.count >= needed and self.entry.duration >= duration:
                episode = ForecastEpisode(f"{self.run}:{point.subsystem}:{len(self.episodes) + 1}", point.subsystem,
                    self.version, now, now, point.horizon, now + point.horizon, f"{point.method}_uncalibrated_score",
                    signal.score, signal.eta_seconds, dict(
                        reading=value, observed_at=observed, target_boundary=target.boundary,
                        independent_confirmations=self.entry.count, confirmation_span_seconds=self.entry.duration,
                    ))
                self.episodes.append(episode)
                self.owner = episode
                self.state = "early_warning"
                self.entry.reset()
        elif not candidate:
            self.entry.reset()
