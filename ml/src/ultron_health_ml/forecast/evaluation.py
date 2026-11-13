"""Forward event matching and conservative notification accounting."""

from bisect import bisect_left, bisect_right
from dataclasses import asdict
from statistics import median

import numpy as np

from ultron_health_ml.hybrid.evaluation import wilson


def observable(event, detections):
    index = bisect_left(detections, event.onset)
    return index < len(detections) and detections[index] < event.recovery


def covered(start, end, timeline, finish, gap):
    if end > finish or not timeline:
        return False
    lower = max(0, bisect_right(timeline, start) - 1)
    upper = bisect_right(timeline, end)
    stamps = [start, *[stamp for stamp in timeline[lower:upper] if stamp >= start], end]
    return (timeline[lower] - start <= gap and start - timeline[lower] <= gap
            and all(right - left <= gap for left, right in zip(stamps, stamps[1:])))


def future_label(time, horizon, events):
    """Ground-truth-only join; never called by feature/scoring code."""
    if any(event.onset <= time < event.recovery for event in events):
        return "late", None
    event = next((event for event in events if time < event.onset <= time + horizon), None)
    return ("future", event) if event else ("negative", None)


def score_run(point, target, trace, truth):
    events = [event for event in truth[0] if event.subsystem == point.subsystem]
    workloads = truth[1]
    available = {event.event_id: observable(event, trace["detections"]) for event in events}
    matches, episodes, interactions = {}, [], []
    for episode in trace["episodes"]:
        status, event = future_label(episode.first_prediction, episode.horizon, events)
        lead = None
        if status == "future":
            if not available[event.event_id]:
                status = "unobservable"
            elif episode.cancelled_at is not None and episode.cancelled_at < event.onset:
                status = "cancelled_false"
            elif event.event_id in matches:
                status = "duplicate"
            else:
                status = "confirmed"
                lead = event.onset - episode.first_prediction
                matches[event.event_id] = (episode.forecast_id, lead)
        elif status == "negative":
            if not covered(episode.first_prediction, episode.window_end, trace["usable"], trace["finish"],
                           target.max_gap_seconds):
                status = "unobservable"
            elif episode.cancelled_at is not None:
                status = "cancelled_false"
            else:
                status = "false"
        episodes.append(dict(run=trace["run"], **asdict(episode), evaluation=status,
                             matched_event=event.event_id if event else None, lead_seconds=lead,
                             minimum_useful_met=lead is not None and lead >= target.min_lead_seconds))
        if status in {"cancelled_false", "unobservable", "false"}:
            interactions.append(dict(run=trace["run"], time=episode.first_prediction, interaction=status,
                                     forecast_id=episode.forecast_id, advisory_interruptions=1))
    details = []
    for event in events:
        matched = matches.get(event.event_id)
        details.append(dict(run=trace["run"], **asdict(event), observable=available[event.event_id],
                            forecast_id=matched[0] if matched else None, lead_seconds=matched[1] if matched else None,
                            minimum_useful_met=matched is not None and matched[1] >= target.min_lead_seconds))
    sessions = [dict(run=trace["run"], **session, nuisance=any(
        row["evaluation"] != "confirmed" and session["start"] <= row["first_prediction"] < session["end"]
        for row in episodes)) for session in workloads]
    interactions.extend(dict(run=trace["run"], **row) for row in trace["interactions"])
    interactions.extend(dict(run=trace["run"], time=stamp, interaction="deterministic_critical",
                             forecast_id=next((episode.forecast_id for episode in trace["episodes"]
                                if episode.first_prediction <= stamp <= episode.window_end
                                and (episode.closed_at is None or stamp <= episode.closed_at)), None),
                             advisory_interruptions=0) for stamp in trace["critical"])
    return details, episodes, sessions, interactions


def quantile(values, proportion):
    return float(np.quantile(values, proportion)) if values else None


def evaluate(point, target, traces, labels):
    events, episodes, sessions, interactions = [], [], [], []
    for trace in traces:
        result = score_run(point, target, trace, labels[trace["run"]])
        for collected, rows in zip((events, episodes, sessions, interactions), result, strict=True):
            collected.extend(rows)
    observable_events = [row for row in events if row["observable"]]
    leads = [row["lead_seconds"] for row in observable_events if row["lead_seconds"] is not None]
    useful = sum(lead >= target.min_lead_seconds for lead in leads)
    days = sum(trace["monitored"] for trace in traces) / 86400
    burden = len(episodes) - len(leads)
    nuisance = sum(row["nuisance"] for row in sessions)
    rows = sum(trace["rows"] for trace in traces)

    def ratio(top, bottom):
        return top / bottom if bottom else None

    summary = dict(total_events=len(events), observable_events=len(observable_events), any_ahead=len(leads),
                   useful_lead=useful, event_recall=ratio(len(leads), len(observable_events)),
                   useful_recall=ratio(useful, len(observable_events)),
                   useful_recall_interval_95=wilson(useful, len(observable_events)),
                   episode_precision=ratio(len(leads), len(episodes)),
                   missed_observable=len(observable_events) - len(leads), interruptions=len(episodes),
                   false_or_uncertain=burden, false_per_day=ratio(burden, days), monitored_device_days=days,
                   workload_sessions=len(sessions), nuisance_sessions=nuisance,
                   workload_nuisance=ratio(nuisance, len(sessions)),
                   workload_interval_95=wilson(nuisance, len(sessions)),
                   p25_lead_seconds=quantile(leads, .25), median_lead_seconds=median(leads) if leads else None,
                   p75_lead_seconds=quantile(leads, .75), p95_lead_seconds=quantile(leads, .95),
                   late=sum(row["evaluation"] == "late" for row in episodes),
                   duplicate_forecasts=sum(row["evaluation"] == "duplicate" for row in episodes),
                   cancelled=sum(row["cancelled_at"] is not None for row in episodes),
                   unobservable_forecasts=sum(row["evaluation"] == "unobservable" for row in episodes),
                   raw_candidate_rows=sum(trace["raw"] for trace in traces),
                   raw_candidate_starts=sum(trace["raw_starts"] for trace in traces),
                   repeated_raw_confirmations=sum(trace["suppressed"] for trace in traces),
                   silent_observe_rows=sum(trace["silent"] for trace in traces),
                   insufficient_rate=ratio(sum(trace["unknown"] for trace in traces), rows),
                   detection_notifications_saved=sum(row["interaction"] == "forecast_to_detection" for row in interactions))
    return summary, events, episodes, sessions, interactions


def budget(result, config):
    return (result["false_per_day"] is not None and result["workload_nuisance"] is not None
            and result["false_per_day"] <= config.false_budget_per_day
            and result["workload_nuisance"] <= config.workload_budget)


def valuable(result, config):
    return (result["observable_events"] >= config.min_observable_events
            and (result["useful_recall"] or 0.) >= config.min_useful_recall
            and (result["episode_precision"] or 0.) >= config.min_precision)


def choose(points, development, validation, config):
    def rank(point):
        earlier, final = development[point.name][0], validation[point.name][0]
        within = budget(earlier, config) and budget(final, config)
        value = valuable(earlier, config) and valuable(final, config)
        return (within and value, within,
                -max(0., final["false_per_day"] - config.false_budget_per_day),
                -max(0., final["workload_nuisance"] - config.workload_budget),
                final["useful_recall"] or 0., final["episode_precision"] or 0.,
                final["median_lead_seconds"] or 0., -point.horizon)
    return max(points, key=rank)
