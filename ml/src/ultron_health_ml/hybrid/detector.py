"""Rules-first offline replay with one stateful finding per subsystem."""

from dataclasses import dataclass, field

from ultron_health_ml.hybrid.config import CRITICAL_LIMITS, PRIMARY_MODES, Settings
from ultron_health_ml.hybrid.signals import ShiftState, signals


@dataclass
class Evidence:
    first: float | None = None
    last: float | None = None
    count: int = 0

    def reset(self):
        self.first = self.last = None
        self.count = 0

    def advance(self, observed, max_gap):
        if self.last is not None and observed <= self.last:
            return
        if self.last is None or observed - self.last > max_gap:
            self.reset()
            self.first = observed
        self.last = observed
        self.count += 1

    @property
    def duration(self):
        return self.last - self.first if self.count else 0.


@dataclass
class Episode:
    episode_id: str
    subsystem: str
    finding_type: str
    opened_at: float
    last_confirmed_at: float
    severity: str
    evidence: dict = field(default_factory=dict)
    current_state: str = "open"
    closed_at: float | None = None
    recovery_reason: str | None = None
    detector_sources: set[str] = field(default_factory=set)
    critical_at: float | None = None
    distinct_observations: int = 0
    fresh_observations: int = 0
    evidence_duration: float = 0.
    _last_observed: float | None = None


def critical_condition(key, value):
    if value is None or key not in CRITICAL_LIMITS:
        return False
    _, limit, direction = CRITICAL_LIMITS[key]
    return value >= limit if direction == "high" else value < limit


def clear_of_critical(key, value):
    if key == "memory":
        return value <= CRITICAL_LIMITS[key][1] - 2
    if key == "disk_capacity":
        return value >= CRITICAL_LIMITS[key][1] + 2
    return True


class Replay:
    def __init__(self, config: Settings, mode="hybrid", multiplier=1., *, scorer=signals, optional=None):
        if mode not in (*PRIMARY_MODES, "fixed_budget", "mad_budget", "fixed_original", "mad_original"):
            raise ValueError("unknown detector mode")
        self.config, self.mode, self.multiplier = config, mode, multiplier
        self.scorer, self.optional = scorer, optional
        self.open = {}
        self.episodes = []
        self.entry = {key: Evidence() for key in config.subsystems}
        self.recovery = {key: Evidence() for key in config.subsystems}
        self.shift = {key: ShiftState() for key in config.subsystems}
        self.failures = 0
        self.safety_checks = 0
        self.safety_misses = 0
        self.last_time = None
        self.mad_streak = 0

    def _open(self, key, now, severity):
        episode = Episode(f"{key}-{len(self.episodes) + 1}", key, f"{key}_pressure", now, now, severity)
        self.episodes.append(episode)
        self.open[key] = episode
        return episode

    def _confirm(self, episode, now, observed, fresh, evidence):
        episode.detector_sources.update(evidence)
        episode.evidence.update(evidence)
        episode.current_state = "open"
        if fresh and (episode._last_observed is None or observed > episode._last_observed):
            episode.distinct_observations += 1
            episode.fresh_observations += 1
            episode.last_confirmed_at = now
            episode._last_observed = observed

    def _close(self, key, now, reason):
        episode = self.open.pop(key)
        episode.closed_at = now
        episode.current_state = "closed"
        episode.recovery_reason = reason
        self.entry[key].reset()
        self.recovery[key].reset()

    def _legacy_mad(self, row, now):
        scores = {key: abs(value) for key, spec in self.config.subsystems.items()
                  if (value := row.values.get(f"{spec.metric}__robust_z")) is not None}
        if self.last_time is not None and now - self.last_time > 90:
            self.mad_streak = 0
        strongest = max(scores, key=scores.get) if scores else None
        self.mad_streak = self.mad_streak + 1 if strongest and scores[strongest] >= 4 else 0
        return strongest if self.mad_streak >= 2 else None

    def step(self, row):
        now = row.sample_time.timestamp()
        if self.last_time is not None and now <= self.last_time:
            raise ValueError("replay timestamps must strictly increase")
        legacy = self.mode.endswith("_original")
        mad_key = self._legacy_mad(row, now) if self.mode == "mad_original" else None
        availability, flags = {}, set()
        values = row.values
        readings = {}
        for key, spec in self.config.subsystems.items():
            metric = spec.metric
            value = values.get(f"{metric}__current")
            age = values.get(f"{metric}__observation_age_seconds")
            context_ok = not spec.require_unplugged or row.ac_online is False
            valid = value is not None and age is not None and context_ok
            availability[key] = (value is not None and age is not None) if context_ok else (
                False if row.ac_online is None else None)
            fresh = valid and bool(values.get(f"{metric}__fresh"))
            observed = now - age if valid else None
            readings[key] = value, valid, fresh, observed
            if row.sampling_gap or (row.context_changed and key in {"cpu", "battery_drain"}):
                self.entry[key].reset()
                self.recovery[key].reset()
                self.shift[key].reset()
        critical = set()
        if not legacy:
            for key in CRITICAL_LIMITS:
                value, valid, fresh, observed = readings[key]
                if not critical_condition(key, value if valid else None):
                    continue
                self.safety_checks += 1
                episode = self.open.get(key) or self._open(key, now, "critical")
                if episode.critical_at is None:
                    episode.critical_at = now
                episode.severity = "critical"
                self._confirm(episode, now, observed, fresh, {"safety": value})
                self.entry[key].reset()
                self.recovery[key].reset()
                flags.add(key)
                critical.add(key)
        for key, spec in self.config.subsystems.items():
            if key in critical:
                continue
            value, valid, fresh, observed = readings[key]
            episode = self.open.get(key)
            if not valid:
                self.entry[key].reset()
                self.recovery[key].reset()
                self.shift[key].reset()
                if episode and now - episode.last_confirmed_at >= self.config.missing_grace_seconds:
                    episode.current_state = "insufficient_telemetry"
                continue
            scores = {}
            threshold = {"cpu": 85., "memory": 90., "disk_capacity": 10.}.get(key)
            if self.mode.startswith("fixed_") and threshold is not None:
                scores["fixed"] = (1 + (threshold - value) / 100 if key == "disk_capacity"
                                   else value / threshold)
            elif self.mode == "mad_original":
                if mad_key == key:
                    scores["mad"] = 1.
            elif not legacy and (spec.modes or self.mode == "mad_budget"):
                try:
                    computed = self.scorer(row, spec, self.shift[key], self.config, value, observed, fresh)
                    allowed = (spec.modes if self.mode == "hybrid" else
                               ("mad",) if self.mode == "mad_budget" else (self.mode,))
                    scores = {name: score for name, score in computed.items()
                              if name in allowed and (self.mode == "mad_budget" or name in spec.modes)}
                except Exception:
                    self.failures += 1
                    self.entry[key].reset()
                    self.recovery[key].reset()
                    if episode:
                        episode.current_state = "insufficient_telemetry"
                    continue
                if self.optional is not None and self.mode == "hybrid":
                    try:
                        extra = self.optional(key, row)
                        if extra is not None:
                            scores["experimental"] = extra
                    except Exception:
                        self.failures += 1
            sign = -1 if spec.direction == "low" else 1
            ready = sign * value >= sign * spec.attention_level if spec.direction != "absolute" else True
            if key == "cpu" and self.mode in PRIMARY_MODES:
                process = values.get("selected_process_nonresponding_count__current")
                memory = values.get("memory_usage_pct__current")
                ready = ready and ((process is not None and process >= 2) or (memory is not None and memory >= 87))
            if self.mode.endswith("_budget") or legacy:
                ready = True
            if (key == "disk_capacity" and self.mode == "hybrid"
                    and value <= 10):
                scores["low_space"] = self.multiplier
            if self.mode == "hybrid" and key == "memory" and value >= 90:
                scores["high_memory"] = self.multiplier
            cutoff = 1 + (self.multiplier - 1) * .12 if self.mode == "fixed_budget" else self.multiplier
            active = {name: score for name, score in scores.items() if score >= cutoff} if ready else {}
            if legacy:
                if active:
                    flags.add(key)
                    if episode is None:
                        episode = self._open(key, now, "warning")
                    self._confirm(episode, now, observed, fresh, active)
                    episode.last_confirmed_at = now
                elif episode and now - episode.last_confirmed_at > 60:
                    self._close(key, now, "legacy_cooldown")
                continue
            if not fresh:
                if episode and episode.current_state == "open":
                    flags.add(key)
                continue
            entry, recovery = self.entry[key], self.recovery[key]
            if active:
                entry.advance(observed, spec.max_observation_gap)
                recovery.reset()
                if episode is None and entry.count >= spec.min_observations and entry.duration >= spec.min_duration:
                    episode = self._open(key, now, "warning")
                    episode.distinct_observations = entry.count - 1
                    episode.fresh_observations = entry.count - 1
                    episode.evidence_duration = entry.duration
                if episode:
                    self._confirm(episode, now, observed, fresh, active)
            else:
                entry.reset()
                if episode:
                    boundary = cutoff - .04 if self.mode == "fixed_budget" else cutoff * self.config.recovery_fraction
                    below_exit = not ready or (bool(scores) and max(scores.values()) < boundary)
                    if below_exit and clear_of_critical(key, value):
                        recovery.advance(observed, spec.max_observation_gap)
                        if recovery.count >= 2 and recovery.duration >= spec.recovery_seconds:
                            self._close(key, now, "valid_telemetry_recovered")
                    else:
                        recovery.reset()
            if key in self.open and self.open[key].current_state == "open":
                flags.add(key)
        if not legacy:
            for key in CRITICAL_LIMITS:
                if critical_condition(key, row.values.get(f"{self.config.subsystems[key].metric}__current")):
                    if key not in self.open or self.open[key].critical_at is None or key not in flags:
                        self.safety_misses += 1
        self.last_time = now
        return availability, flags
