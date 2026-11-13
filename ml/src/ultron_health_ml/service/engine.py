"""Replay approved engines and translate their state into advisory events."""

from datetime import datetime, timezone
import hashlib
from time import monotonic, perf_counter

from ultron_health_ml.features.config import FeatureConfig
from ultron_health_ml.features.engineering import iter_features
from ultron_health_ml.forecast.baselines import FEATURES, History, measurement, scores
from ultron_health_ml.forecast.contract import Actionable, METRICS
from ultron_health_ml.forecast.lifecycle import Lifecycle
from ultron_health_ml.reduced.policy import ReducedReplay, common_signals
from ultron_health_ml.service.schema import InferResponse, Finding

WARMUP_SECONDS = 3600
METRIC = {"cpu": METRICS[0], "disk_capacity": METRICS[2], "process": METRICS[3]}


def utc(value):
    return datetime.fromtimestamp(value, timezone.utc) if value is not None else None


def identity(*parts):
    return hashlib.sha256("|".join(str(part) for part in parts).encode()).hexdigest()


def infer(request, approved, deadline, timings=None):
    config, detector_point, targets, forecast_point, noise = approved
    window = request.telemetry
    detector = ReducedReplay(config, detector_point)
    target = targets.targets["process"]
    tracker = Actionable("process", target)
    history = History("process")
    forecast = Lifecycle(request.replay_id, forecast_point, target, targets.version)
    detection_rows = iter(iter_features(window, FeatureConfig()))
    forecast_rows = iter(iter_features(window, FEATURES)) if request.scope.forecasting else None
    scope = sorted(set(request.scope.detection) | set(request.scope.forecasting))
    owners, events = {}, []
    last = {}

    def emit(key, owner, transition, record, evidence, reason=None):
        finding_id, first, kind, severity, source, horizon = owner
        event_id = identity(finding_id, record.sample_id, transition)
        events.append(Finding(finding_id=finding_id, event_id=event_id, subsystem=key, finding_type=kind,
            transition=transition, state="closed" if transition == "CLOSE" else
            "insufficient_evidence" if kind == "INSUFFICIENT_EVIDENCE" else "active",
            severity=severity, first_at=utc(first), latest_evidence_at=utc(evidence.pop("observed_at")),
            decision_at=record.sample_time, definition_version=targets.version if source != "reduced_hybrid"
            else "phase45c-v1", source=source, evidence=evidence, horizon_seconds=1800 if horizon else None,
            minimum_useful_lead_seconds=240 if horizon else None, target_window_end=utc(horizon),
            reason=reason))

    for record in window.records:
        start = perf_counter()
        if monotonic() >= deadline:
            raise TimeoutError
        row = next(detection_rows)
        phase5 = next(forecast_rows) if forecast_rows is not None else None
        features_done = perf_counter()
        coverage, _ = detector.consume(row, lambda: common_signals(row))
        detection_done = perf_counter()
        if detector.failures:
            raise RuntimeError("detector failed")
        now = record.sample_time.timestamp()
        signal = None
        if phase5 is not None:
            values = {metric: phase5.values.get(f"{metric}__current") for metric in METRICS}
            observed, fresh = measurement(record, phase5, target)
            change = tracker.update(now, values, observed, fresh)
            if fresh:
                history.add(observed, values[target.metric], target.max_gap_seconds)
            signal = scores("process", target, values, tracker, history, noise)["persistence"]
            forecast.step(now, observed, fresh, values[target.metric], signal, change)
        forecast_done = perf_counter()
        warmed = now - window.window_start.timestamp() >= WARMUP_SECONDS
        for key in scope:
            reading = record.metrics.get(METRIC[key])
            value = row.values.get(f"{METRIC[key]}__current")
            available = bool(coverage[key])
            evidence = dict(available=available, reading=value, quality=reading.quality if available else "unavailable",
                            observed_at=reading.observation_time.timestamp() if available else None)
            prior = owners.get(key)
            detection = detector.open.get(key)
            if key not in request.scope.detection:
                detection = None
            process_owner = forecast.owner if key == "process" and phase5 is not None else None
            active_forecast = process_owner is not None and process_owner.state == "early_warning"
            linked_detection = (key == "process" and phase5 is not None and tracker.active
                                and (key in request.scope.detection or prior is not None))
            critical = detection is not None and detection.severity == "critical"
            active_detection = (detection is not None and (warmed or critical)) or (linked_detection and warmed)
            if prior and prior[2] == "DETECTION" and active_forecast:
                active_detection = True
            current = None
            if active_detection or (warmed and active_forecast):
                kind = "DETECTION" if active_detection else "FORECAST"
                severity = ("critical_reference" if critical else "advisory" if active_detection else "early_warning")
                source = ("reduced_hybrid" if active_detection and detection is not None
                          else "process_actionable" if active_detection else "process_persistence")
                if prior:
                    finding_id, first, _, previous_severity, previous_source, horizon = prior
                    if previous_severity == "critical_reference":
                        severity = previous_severity
                    if active_detection and detection is None and previous_source == "reduced_hybrid":
                        source = previous_source
                else:
                    first = (detection.opened_at if active_detection and detection is not None else
                             now if active_detection else process_owner.first_prediction)
                    finding_id = identity(request.replay_id, key, first)
                    horizon = None if active_detection else process_owner.window_end
                current = (finding_id, first, kind, severity, source, horizon)
                if kind == "FORECAST" and signal is not None and signal.sufficient and available:
                    evidence.update(score=signal.score, score_type="uncalibrated_evidence",
                                    independent_confirmations=process_owner.evidence_summary["independent_confirmations"])
                if prior is None:
                    emit(key, current, "OPEN", record, evidence.copy())
                elif prior[2:4] != current[2:4]:
                    emit(key, current, "ESCALATE", record, evidence.copy())
                elif available and evidence["quality"] == "fresh":
                    emit(key, current, "UPDATE", record, evidence.copy())
                owners[key] = current
            elif prior and available and evidence["quality"] == "fresh":
                awaiting_recovery = prior[5] is not None and process_owner is not None
                if not awaiting_recovery:
                    emit(key, prior, "CLOSE", record, evidence.copy(), "fresh_recovery")
                    owners.pop(key)
            if key == "cpu" and all(row.values.get(f"{metric}__current") is None for metric in (METRICS[1], METRICS[3])):
                insufficient = True
                reason = "missing_cpu_context"
            elif not available or not warmed or (key == "process" and signal is not None and not signal.sufficient):
                insufficient = True
                reason = "sensor_unavailable" if not available else "warmup_or_insufficient_history"
            else:
                insufficient, reason = False, None
            if insufficient and last.get(key) != reason:
                owner = owners.get(key)
                info = (owner[0] if owner else identity(request.replay_id, key, "coverage"),
                        owner[1] if owner else now, "INSUFFICIENT_EVIDENCE", "none", "coverage", None)
                emit(key, info, "INSUFFICIENT_EVIDENCE", record, evidence.copy(), reason)
            last[key] = reason
        if timings is not None:
            finish = perf_counter()
            timings.append(((features_done-start)*1000, (detection_done-features_done)*1000,
                            (forecast_done-detection_done)*1000, (finish-forecast_done)*1000))
    return InferResponse(feature_fingerprints=request.feature_fingerprints, request_id=request.request_id,
        replay_id=request.replay_id, as_of=window.as_of, findings=tuple(events),
        process_raw_qualifying_rows=forecast.raw, logical_open_count=sum(event.transition=="OPEN" for event in events))
