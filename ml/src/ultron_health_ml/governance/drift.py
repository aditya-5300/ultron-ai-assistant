"""Bounded, informational quality and drift summaries for sanitized replay windows."""

from collections import Counter, defaultdict
from statistics import median, quantiles

from ultron_health_ml.contracts.telemetry import TelemetryWindow
from ultron_health_ml.features.config import FeatureConfig
from ultron_health_ml.service.engine import WARMUP_SECONDS
from ultron_health_ml.service.schema import DETECTION_FP, MAX_ROWS, MAX_HISTORY_SECONDS
from .registry import digest

FEATURES = FeatureConfig()
METRICS = ("cpu_usage_pct", "memory_usage_pct", "disk_free_pct", "selected_process_nonresponding_count")
RULES = {"minimum_fresh": 30, "minimum_coverage": 0.5, "missingness_delta": 0.15, "context_delta": 0.20,
         "count_prevalence_delta": 0.15, "shift_iqr": 1.5, "iqr_ratio": 2.5,
         "absolute_floor": {"cpu_usage_pct": 8, "memory_usage_pct": 6, "disk_free_pct": 3},
         "cadence_delta_seconds": 15, "warmup_seconds": WARMUP_SECONDS}


def quality(data):
    source_rows = data.get("records") if isinstance(data, dict) else getattr(data, "records", None)
    if isinstance(source_rows, (list, tuple)) and len(source_rows) > MAX_ROWS:
        raise ValueError("window exceeds governance resource limits")
    window = TelemetryWindow.model_validate(data)
    records = window.records
    duration = (records[-1].sample_time - records[0].sample_time).total_seconds()
    if len(records) > MAX_ROWS or duration > MAX_HISTORY_SECONDS:
        raise ValueError("window exceeds governance resource limits")
    elapsed = [row.elapsed_seconds for row in records[1:]]
    gaps = [index for index, row in enumerate(records) if index and
            row.elapsed_seconds > FEATURES.gap_factor * max(row.nominal_interval_seconds, records[index - 1].nominal_interval_seconds)]
    anchor = records[gaps[-1] if gaps else 0].sample_time
    warmup = (records[-1].sample_time - anchor).total_seconds()
    usable = defaultdict(list)
    contexts = Counter(f"{row.power_plan}:{row.ac_online}" for row in records)
    counts = {metric: Counter() for metric in METRICS}
    unsupported = Counter()
    for row in records:
        unsupported.update(set(row.metrics) - set(METRICS))
        for metric in METRICS:
            reading = row.metrics.get(metric)
            state = reading.quality if reading else "missing"
            if state == "cached" and (row.sample_time - reading.observation_time).total_seconds() >= FEATURES.max_cached_age_seconds:
                state = "stale"
            counts[metric][state] += 1
            if state == "fresh" and row.sample_time >= anchor:
                usable[metric].append(reading.value)
    summaries = {}
    for metric, count in counts.items():
        values = usable[metric]
        row = {state: count[state] for state in ("fresh", "cached", "stale", "missing")}
        row["fresh_fraction"] = count["fresh"] / len(records)
        row["latest_segment_fresh_fraction"] = len(values) / (len(records) - (gaps[-1] if gaps else 0))
        row["unavailable_fraction"] = (count["missing"] + count["stale"]) / len(records)
        row["status"] = "SUFFICIENT_DATA" if len(values) >= RULES["minimum_fresh"] and (
            row["latest_segment_fresh_fraction"] >= RULES["minimum_coverage"]) and warmup >= WARMUP_SECONDS else "INSUFFICIENT_DATA"
        if row["status"] == "SUFFICIENT_DATA":
            if metric.endswith("_count"):
                row["nonzero_fraction"] = sum(value > 0 for value in values) / len(values)
            else:
                lower, _, upper = quantiles(values, n=4, method="inclusive")
                row.update(median=median(values), iqr=upper - lower)
        summaries[metric] = row
    return {"records": len(records), "span_seconds": duration, "latest_segment_seconds": warmup,
            "polling_gaps": len(gaps), "largest_gap_seconds": max(elapsed, default=None),
            "median_cadence_seconds": median(elapsed) if elapsed else None,
            "irregular_intervals": sum(abs(row.elapsed_seconds - row.nominal_interval_seconds) > .25 * row.nominal_interval_seconds
                                      for row in records[1:]),
            "insufficient_feature_history": warmup < FEATURES.baseline_seconds,
            "unsupported_measurements": dict(unsupported), "metrics": summaries,
            "context": {key: count / len(records) for key, count in sorted(contexts.items())}}


def make_reference(window, split, seed):
    if split != "reference":
        raise ValueError("only the declared reference split can be fitted")
    return {"version": "1.0.0", "telemetry_schema": "1.0.0", "feature_fingerprint": DETECTION_FP,
            "provenance": "Phase 9 seeded synthetic reference; no historical holdout used", "seed": seed,
            "dataset_hash": digest(window.model_dump(mode="json")), "rules": RULES, "summary": quality(window)}


def evaluate(data, reference, expected_hash):
    try:
        if (digest(reference) != expected_hash or reference["version"] != "1.0.0"
                or reference["telemetry_schema"] != "1.0.0" or reference["feature_fingerprint"] != DETECTION_FP
                or reference["rules"] != RULES):
            return {"status": "REFERENCE_INCOMPATIBLE"}
        old = reference["summary"]
        if set(old["metrics"]) != set(METRICS):
            return {"status": "REFERENCE_INCOMPATIBLE"}
        for metric, item in old["metrics"].items():
            if item["status"] not in {"SUFFICIENT_DATA", "INSUFFICIENT_DATA"} or not 0 <= item["unavailable_fraction"] <= 1:
                return {"status": "REFERENCE_INCOMPATIBLE"}
            if item["status"] == "SUFFICIENT_DATA":
                if metric.endswith("_count"):
                    if not 0 <= item["nonzero_fraction"] <= 1:
                        return {"status": "REFERENCE_INCOMPATIBLE"}
                elif not 0 <= item["median"] <= 100 or not 0 <= item["iqr"] <= 100:
                    return {"status": "REFERENCE_INCOMPATIBLE"}
        if (not 1 <= old["records"] <= MAX_ROWS or not isinstance(old["context"], dict)
                or not all(0 <= value <= 1 for value in old["context"].values())
                or (old["median_cadence_seconds"] is not None and not 0 < old["median_cadence_seconds"] <= MAX_HISTORY_SECONDS)):
            return {"status": "REFERENCE_INCOMPATIBLE"}
    except (KeyError, ValueError, TypeError):
        return {"status": "REFERENCE_INCOMPATIBLE"}
    try:
        current = quality(data)
    except (TypeError, ValueError):
        return {"status": "SCHEMA_INCOMPATIBLE"}
    assessments = {}
    for metric, row in current["metrics"].items():
        baseline = old["metrics"][metric]
        reasons = []
        if abs(row["unavailable_fraction"] - baseline["unavailable_fraction"]) >= RULES["missingness_delta"]:
            reasons.append("availability_shift")
        if row["status"] != "SUFFICIENT_DATA" or baseline["status"] != "SUFFICIENT_DATA":
            assessments[metric] = {"status": "INSUFFICIENT_DATA", "reasons": reasons}
            continue
        if "nonzero_fraction" in row:
            if abs(row["nonzero_fraction"] - baseline["nonzero_fraction"]) >= RULES["count_prevalence_delta"]:
                reasons.append("nonzero_prevalence_shift")
        else:
            floor = RULES["absolute_floor"][metric]
            if abs(row["median"] - baseline["median"]) >= max(floor, RULES["shift_iqr"] * baseline["iqr"]):
                reasons.append("median_shift")
            if max(row["iqr"], baseline["iqr"]) >= max(floor, RULES["iqr_ratio"] * min(row["iqr"], baseline["iqr"])):
                reasons.append("scale_shift")
        assessments[metric] = {"status": "DRIFT_OBSERVED" if reasons else "NO_DRIFT_DETECTED", "reasons": reasons}
    contexts = set(current["context"]) | set(old["context"])
    distance = sum(abs(current["context"].get(key, 0) - old["context"].get(key, 0)) for key in sorted(contexts)) / 2
    contextual = current["records"] >= RULES["minimum_fresh"] and old["records"] >= RULES["minimum_fresh"]
    cadence = current["median_cadence_seconds"]
    context_reasons = []
    if contextual and distance >= RULES["context_delta"]:
        context_reasons.append("power_context_shift")
    if contextual and cadence is not None and old["median_cadence_seconds"] is not None and (
            abs(cadence - old["median_cadence_seconds"]) >= RULES["cadence_delta_seconds"]):
        context_reasons.append("cadence_shift")
    statuses = {row["status"] for row in assessments.values()}
    status = ("DRIFT_OBSERVED" if "DRIFT_OBSERVED" in statuses or context_reasons else
              "INSUFFICIENT_DATA" if "INSUFFICIENT_DATA" in statuses else "NO_DRIFT_DETECTED")
    return {"status": status, "metrics": assessments, "context_reasons": context_reasons, "quality": current}
