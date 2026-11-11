"""Join evaluation truth after scoring; aggregate per-run alerts without crossing gaps."""
from collections import Counter
from dataclasses import dataclass

import numpy as np
from sklearn.metrics import average_precision_score, roc_auc_score

from ultron_health_ml.data_generation.scenarios import PROFILES


WORKLOADS = ("BACKUP_WORKLOAD", "COMPILATION_OR_BUILD_WORKLOAD")


def ratio(numerator, denominator):
    return float(numerator / denominator) if denominator else None


@dataclass(frozen=True)
class Truth:
    actual: np.ndarray
    scope: np.ndarray
    workload: np.ndarray


def observability(metrics):
    available = set(metrics)
    return {
        name: "observable" if set(profile[0]) <= available else
        "partially_observable" if set(profile[0]) & available else "not_observable"
        for name, profile in PROFILES.items()
    }


def align_truth(run, labels, visibility):
    indexed = {row.sample_id: row for row in labels.observations}
    if set(indexed) != set(run.ids) or len(indexed) != len(labels.observations):
        raise ValueError("evaluation labels do not match the scored sample IDs")
    rows = [indexed[sample_id] for sample_id in run.ids]
    actual = np.array([row.is_anomaly for row in rows], dtype=bool)
    scope = np.array([not row.is_anomaly or any(
        PROFILES[name][2] and visibility[name] != "not_observable" for name in row.scenario
    ) for row in rows], dtype=bool)
    workload = np.array([any(name in WORKLOADS for name in row.scenario) for row in rows], dtype=bool)
    return Truth(actual, scope, workload)


def row_metrics(actual, predicted, scores=None):
    tp = int(np.sum(actual & predicted))
    fp = int(np.sum(~actual & predicted))
    fn = int(np.sum(actual & ~predicted))
    tn = int(np.sum(~actual & ~predicted))
    result = dict(tp=tp, fp=fp, fn=fn, tn=tn, precision=ratio(tp, tp + fp), recall=ratio(tp, tp + fn),
                  f1=ratio(2 * tp, 2 * tp + fp + fn), fpr=ratio(fp, fp + tn), fnr=ratio(fn, fn + tp),
                  specificity=ratio(tn, tn + fp))
    if scores is not None:
        valid = np.isfinite(scores)
        classes = len(np.unique(actual[valid])) == 2
        result.update(unscored=int((~valid).sum()),
                      average_precision=float(average_precision_score(actual[valid], scores[valid])) if classes else None,
                      roc_auc=float(roc_auc_score(actual[valid], scores[valid])) if classes else None)
    return result


def choose_threshold(scores, truth, settings):
    if not np.isfinite(scores).all() or not truth.actual[truth.scope].any():
        raise ValueError("threshold selection needs finite scores and observable validation anomalies")
    candidates = np.unique(np.r_[np.quantile(scores, np.linspace(0, 1, 101)), np.nextafter(scores.max(), np.inf)])
    best_key = None
    best = None
    for cutoff in candidates:
        decisions = scores >= cutoff
        row = row_metrics(truth.actual[truth.scope], decisions[truth.scope])
        workload_fpr = ratio((decisions & truth.workload & ~truth.actual).sum(),
                             (truth.workload & ~truth.actual).sum())
        objective = ((row["f1"] or 0) - settings.workload_penalty * (workload_fpr or 0)
                     - settings.false_positive_penalty * (row["fpr"] or 0))
        key = (objective, -(workload_fpr or 0), -(row["fpr"] or 0), row["recall"] or 0, float(cutoff))
        if best_key is None or key > best_key:
            best_key = key
            best = dict(threshold=float(cutoff), objective=objective, workload_fpr=workload_fpr, **row)
    return best


def episodes(times, decisions, scope, actual, settings):
    groups = []
    start = last = None
    true = False
    count = 0
    for index, stamp in enumerate(times):
        split = (not scope[index] or (index and
                 (stamp - times[index - 1]).total_seconds() > settings.max_gap_seconds))
        expired = last is not None and (stamp - times[last]).total_seconds() > settings.episode_cooldown_seconds
        if start is not None and (split or (expired and (not decisions[index] or index != last + 1))):
            groups.append(_episode(times, start, last, true, count))
            start = last = None
        if decisions[index] and scope[index]:
            if start is None:
                start, true, count = index, False, 0
            last, true, count = index, true or bool(actual[index]), count + 1
    if start is not None:
        groups.append(_episode(times, start, last, true, count))
    return groups


def _episode(times, start, last, true, count):
    return dict(start=times[start].isoformat(), end=times[last].isoformat(),
                duration_seconds=(times[last] - times[start]).total_seconds(), true_alert=true, flagged_rows=count)


def _duration_stats(groups):
    values = [row["duration_seconds"] for row in groups]
    return dict(mean_duration_seconds=float(np.mean(values)) if values else None,
                median_duration_seconds=float(np.median(values)) if values else None)


def evaluate(scored, label_sets, metrics, settings):
    visibility = observability(metrics)
    all_actual, all_scores, all_decisions, all_scope = [], [], [], []
    grouped, scenarios, workloads = [], [], {name: Counter() for name in WORKLOADS}
    elapsed = 0.0
    for (run, scores, decisions), labels in zip(scored, label_sets, strict=True):
        truth = align_truth(run, labels, visibility)
        all_actual.append(truth.actual)
        all_scores.append(scores)
        all_decisions.append(decisions)
        all_scope.append(truth.scope)
        grouped.extend(dict(run=run.name, context="profile_scope", **row)
                       for row in episodes(run.times, decisions, truth.scope, truth.actual, settings))
        elapsed += (run.times[-1] - run.times[0]).total_seconds()
        for scenario in labels.scenarios:
            name = scenario.scenario_type
            inside = np.asarray((run.times >= scenario.scenario_start) & (run.times < scenario.scenario_end))
            hits = np.flatnonzero(inside & decisions)
            first = run.times[hits[0]] if len(hits) else None
            start, incident = scenario.scenario_start, scenario.incident_time
            trigger = PROFILES[name][3]
            eligible = bool(incident is not None and trigger and trigger[0] in metrics)
            finite = scores[inside & np.isfinite(scores)]
            scenarios.append(dict(run=run.name, scenario_id=scenario.scenario_id, scenario=name,
                                  observability=visibility[name], is_anomaly=PROFILES[name][2],
                                  scenario_start=start.isoformat(), scenario_end=scenario.scenario_end.isoformat(),
                                  incident_time=incident.isoformat() if incident else None, incident_eligible=eligible,
                                  first_alert=first.isoformat() if first is not None else None,
                                  detected=bool(len(hits)) if visibility[name] != "not_observable" and inside.any() else None,
                                  delay_seconds=(first - start).total_seconds() if first is not None else None,
                                  relative_incident_seconds=(first - incident).total_seconds()
                                  if first is not None and eligible else None,
                                  rows=int(inside.sum()), fraction_flagged=ratio(len(hits), inside.sum()),
                                  max_score=float(finite.max()) if len(finite) else None))
            if name in workloads:
                groups = episodes(run.times, decisions, inside & ~truth.actual, truth.actual, settings)
                grouped.extend(dict(run=run.name, context=name, **row) for row in groups)
                workloads[name].update(rows=int((inside & ~truth.actual).sum()),
                                       flagged=int((inside & decisions & ~truth.actual).sum()))
    actual, scores, decisions, scope = map(np.concatenate, (all_actual, all_scores, all_decisions, all_scope))
    primary = [row for row in grouped if row["context"] == "profile_scope"]
    false = sum(not row["true_alert"] for row in primary)
    elapsed_days = elapsed / 86400
    incidents = Counter()
    per_scenario = {}
    for row in scenarios:
        kind = per_scenario.setdefault(row["scenario"], dict(instances=0, detectable_instances=0, alerted=0))
        kind["instances"] += 1
        kind["detectable_instances"] += row["detected"] is not None
        kind["alerted"] += row["detected"] is True
        if row["incident_time"]:
            delta = row["relative_incident_seconds"]
            category = ("unobservable_trigger" if not row["incident_eligible"] else
                        "missed" if delta is None else "before" if delta < 0 else "after" if delta > 0 else "at")
            incidents[category] += 1
    eligible = sum(incidents[name] for name in ("before", "at", "after", "missed"))
    return {
        "profile_scope": row_metrics(actual[scope], decisions[scope], scores[scope]),
        "all_rows": row_metrics(actual, decisions, scores),
        "excluded_positive_rows": int((~scope).sum()),
        "episodes": dict(total=len(primary), false=false, true=len(primary) - false,
                         observed_days=elapsed_days, alerts_per_day=ratio(len(primary), elapsed_days),
                         false_alerts_per_day=ratio(false, elapsed_days), **_duration_stats(primary)),
        "incidents": dict(**{name: incidents[name] for name in ("before", "at", "after", "missed", "unobservable_trigger")},
                          eligible=eligible, detection_rate=ratio(eligible - incidents["missed"], eligible)),
        "workloads": {name: dict(rows=count["rows"], flagged=count["flagged"],
                                 fpr=ratio(count["flagged"], count["rows"]),
                                 episodes=sum(row["context"] == name for row in grouped),
                                 **_duration_stats([row for row in grouped if row["context"] == name]))
                      for name, count in workloads.items()},
        "scenarios": per_scenario,
    }, scenarios, grouped
