"""Event matching, user-visible alert budgets, and secondary row diagnostics."""

from dataclasses import dataclass, field
from math import log, sqrt
from statistics import median

import numpy as np

from ultron_health_ml.hybrid.config import CRITICAL_LIMITS, WORKLOADS
from ultron_health_ml.hybrid.detector import critical_condition


def ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def wilson(successes, total):
    if not total:
        return None
    center = successes / total
    squared = 1.96 ** 2
    offset = 1 + squared / total
    radius = 1.96 * sqrt((center * (1 - center) + squared / (4 * total)) / total)
    return [(center + squared / (2 * total) - radius) / offset,
            (center + squared / (2 * total) + radius) / offset]


@dataclass
class Trace:
    name: str
    times: list
    flags: list
    available: list
    critical_observed: dict
    episodes: list
    monitored_seconds: float
    safety_checks: int
    safety_misses: int
    failures: int
    timings_ms: list = field(default_factory=list)


def match_events(trace, events):
    matched = set()
    details = []
    for event in sorted(events, key=lambda item: (item.event_start, item.event_id)):
        if event.actionable_onset is None:
            continue
        candidates = sorted((notice, index) for index, episode in enumerate(trace.episodes)
                            if episode.subsystem == event.subsystem and index not in matched
                            for notice in (episode.opened_at, episode.critical_at)
                            if notice is not None and event.actionable_onset <= notice < event.event_end)
        detected = candidates[0] if candidates else None
        if detected:
            matched.add(detected[1])
        expected_critical = next((stamp for stamp in trace.critical_observed.get(event.subsystem, ())
                                  if event.incident_time is not None and event.incident_time <= stamp < event.event_end),
                                 None)
        actual_critical = min((episode.critical_at for episode in trace.episodes
                               if episode.subsystem == event.subsystem and episode.critical_at is not None
                               and event.actionable_onset <= episode.critical_at < event.event_end), default=None)
        episode = trace.episodes[detected[1]] if detected else None
        details.append(dict(run=trace.name, event_id=event.event_id, kind=event.kind,
                            subsystem=event.subsystem, severity=event.severity, event_start=event.event_start,
                            actionable_onset=event.actionable_onset, event_end=event.event_end,
                            incident_time=event.incident_time, recovery_time=event.event_end,
                            recovery_deadline=event.recovery_time,
                            observable_metrics=";".join(event.observable_metrics), detected=detected is not None,
                            detected_at=detected[0] if detected else None,
                            delay_seconds=detected[0] - event.actionable_onset if detected else None,
                            episode_id=episode.episode_id if episode else None,
                            recovery_delay_seconds=episode.closed_at - event.event_end
                            if episode and episode.closed_at is not None else None,
                            expected_critical_at=expected_critical, actual_critical_at=actual_critical,
                            critical_unobservable=event.incident_time is not None and expected_critical is None,
                            critical_missed=expected_critical is not None and actual_critical is None,
                            critical_delayed=expected_critical is not None and actual_critical is not None
                            and actual_critical > expected_critical))
    return details, matched


def evaluate(traces, events_by_run, config):
    event_rows, episode_rows, exposure = [], [], []
    for trace in traces:
        events = events_by_run[trace.name]
        details, matched = match_events(trace, events)
        event_rows.extend(details)
        episodes = []
        last_closed = {}
        for index, episode in enumerate(trace.episodes):
            prior = last_closed.get(episode.subsystem)
            noncritical_notice = episode.critical_at != episode.opened_at
            noncritical_matched = any(event.actionable_onset is not None and event.subsystem == episode.subsystem
                                      and event.actionable_onset <= episode.opened_at < event.event_end
                                      for event in events)
            false = noncritical_notice and not noncritical_matched
            workload = next((event.kind for event in events if event.kind in WORKLOADS
                             and event.event_start <= episode.opened_at < event.event_end), None)
            episodes.append(dict(run=trace.name, episode_id=episode.episode_id, subsystem=episode.subsystem,
                                 finding_type=episode.finding_type, opened_at=episode.opened_at,
                                 last_confirmed_at=episode.last_confirmed_at, severity=episode.severity,
                                 evidence=episode.evidence, current_state=episode.current_state,
                                 closed_at=episode.closed_at, recovery_reason=episode.recovery_reason,
                                 detector_sources=";".join(sorted(episode.detector_sources)),
                                 critical_at=episode.critical_at, matched=index in matched,
                                 false_noncritical=false, workload=workload,
                                 duplicate_advisory=noncritical_matched and noncritical_notice and index not in matched,
                                 flap=prior is not None and 0 <= episode.opened_at - prior <= config.flap_window_seconds,
                                 duration_seconds=(episode.closed_at if episode.closed_at is not None
                                                   else trace.times[-1]) - episode.opened_at,
                                 distinct_observations=episode.distinct_observations,
                                 fresh_observations=episode.fresh_observations,
                                 evidence_duration=episode.evidence_duration))
            if episode.closed_at is not None:
                last_closed[episode.subsystem] = episode.closed_at
        episode_rows.extend(episodes)
        exposure.extend(dict(run=trace.name, workload=event.kind,
                             nuisance=any(row["false_noncritical"] and
                                          event.event_start <= row["opened_at"] < event.event_end for row in episodes))
                        for event in events if event.kind in WORKLOADS)

    def aggregate(subsystem=None):
        events = [row for row in event_rows if subsystem is None or row["subsystem"] == subsystem]
        episodes = [row for row in episode_rows if subsystem is None or row["subsystem"] == subsystem]
        delays = [row["delay_seconds"] for row in events if row["detected"]]
        recoveries = [row["recovery_delay_seconds"] for row in events if row["recovery_delay_seconds"] is not None]
        days = sum(trace.monitored_seconds for trace in traces) / 86400
        false = sum(row["false_noncritical"] for row in episodes)
        availability = [value for trace in traces for row in trace.available for key, value in row.items()
                        if (subsystem is None or key == subsystem) and value is not None]
        detected = len(delays)
        return dict(events=len(events), detected=detected, event_recall=ratio(detected, len(events)),
                    recall_interval_95=wilson(detected, len(events)), false_noncritical_episodes=false,
                    monitored_device_days=days, false_episodes_per_day=ratio(false, days),
                    zero_false_upper_95=(-log(.05) / days) if not false and days else None,
                    median_delay_seconds=median(delays) if delays else None,
                    p95_delay_seconds=float(np.quantile(delays, .95)) if delays else None,
                    critical_expected=sum(row["expected_critical_at"] is not None for row in events),
                    critical_missed=sum(row["critical_missed"] for row in events),
                    critical_delayed=sum(row["critical_delayed"] for row in events),
                    critical_unobservable=sum(row["critical_unobservable"] for row in events),
                    flap_count=sum(row["flap"] for row in episodes),
                    duplicate_advisories=sum(row["duplicate_advisory"] for row in episodes),
                    median_recovery_delay_seconds=median(recoveries) if recoveries else None,
                    median_episode_duration_seconds=median(row["duration_seconds"] for row in episodes) if episodes else None,
                    insufficient_telemetry_rate=ratio(availability.count(False), len(availability)),
                    insufficient_rows=availability.count(False), eligible_subsystem_rows=len(availability))

    summary = aggregate()
    misses = sum(trace.safety_misses for trace in traces)
    successes = sum(row["nuisance"] for row in exposure)
    workload = {kind: dict(sessions=len(group), nuisance=sum(row["nuisance"] for row in group),
                           nuisance_rate=ratio(sum(row["nuisance"] for row in group), len(group)))
                for kind in sorted(WORKLOADS) if (group := [row for row in exposure if row["workload"] == kind])}
    tp = fp = fn = tn = 0
    for trace in traces:
        events = [event for event in events_by_run[trace.name] if event.actionable_onset is not None]
        for stamp, flag in zip(trace.times, trace.flags, strict=True):
            actual = any(event.actionable_onset <= stamp < event.event_end for event in events)
            predicted = bool(flag)
            tp += actual and predicted
            fp += not actual and predicted
            fn += actual and not predicted
            tn += not actual and not predicted
    summary.update(workload_sessions=len(exposure), nuisance_sessions=successes,
                   workload_nuisance_rate=ratio(successes, len(exposure)),
                   workload_interval_95=wilson(successes, len(exposure)), workload_by_kind=workload,
                   safety_checks=sum(trace.safety_checks for trace in traces), safety_misses=misses,
                   statistical_failures=sum(trace.failures for trace in traces),
                   row_precision=ratio(tp, tp + fp), row_recall=ratio(tp, tp + fn),
                   row_f1=ratio(2 * tp, 2 * tp + fp + fn), tp=tp, fp=fp, fn=fn, tn=tn)
    return summary, event_rows, episode_rows, {key: aggregate(key) for key in config.subsystems}, exposure


def choose_candidate(candidates, config):
    def key(item):
        multiplier, result = item
        rate = result["false_episodes_per_day"]
        nuisance = result["workload_nuisance_rate"]
        recall = result["event_recall"]
        eligible = rate is not None and nuisance is not None and recall is not None
        within = eligible and rate <= config.false_budget_per_day and nuisance <= config.workload_budget
        delay = result["median_delay_seconds"]
        burden = max(0., rate - config.false_budget_per_day) if rate is not None else float("inf")
        workload = max(0., nuisance - config.workload_budget) if nuisance is not None else float("inf")
        return (within, -burden, -workload, recall if recall is not None else -1.,
                -delay if delay is not None else -float("inf"), multiplier)
    return max(candidates, key=key)[0]


def compare(hybrid, baseline, config):
    hybrid_rate = hybrid["false_episodes_per_day"]
    base_rate = baseline["false_episodes_per_day"]
    comparable = (hybrid_rate is not None and base_rate is not None
                  and abs(hybrid_rate - base_rate) <= config.budget_match_tolerance)
    base_recall = baseline["event_recall"]
    recall_gain = hybrid["event_recall"] - base_recall if (
        hybrid["event_recall"] is not None and base_recall is not None) else None
    base_delay = baseline["median_delay_seconds"]
    hybrid_delay = hybrid["median_delay_seconds"]
    delay_gain = ratio(base_delay - hybrid_delay, base_delay) if base_delay and hybrid_delay is not None else None
    meets = comparable and recall_gain is not None and (
        recall_gain >= .10 - 1e-12 or (delay_gain is not None and delay_gain >= .20 and recall_gain >= 0))
    return dict(comparable=comparable, baseline_false_episodes_per_day=base_rate, hybrid_false_episodes_per_day=hybrid_rate,
                recall_gain=recall_gain, median_delay_reduction=delay_gain, meets_incremental_gate=meets)
