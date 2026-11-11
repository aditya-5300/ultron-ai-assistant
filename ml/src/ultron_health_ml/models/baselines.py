"""Offline comparators using only current and past-only Phase 3 features."""
import numpy as np


def threshold_baseline(run, settings):
    rules = {"cpu_usage_pct": (settings.cpu_limit_pct, False),
             "memory_usage_pct": (settings.memory_limit_pct, False),
             "disk_free_pct": (settings.disk_free_limit_pct, True)}
    scores = []
    for name, (limit, low) in rules.items():
        column = f"{name}__current"
        if column not in run.frame:
            continue
        values = run.frame[column].to_numpy()
        scores.append(1 + (limit - values) / 100 if low else values / limit)
    maximum = np.fmax.reduce(scores) if scores else np.full(len(run.frame), np.nan)
    return maximum, maximum >= 1


def mad_baseline(run, settings):
    columns = [name for name in run.frame if name.endswith("__robust_z")]
    scores = (np.fmax.reduce(np.abs(run.frame[columns].to_numpy()), axis=1)
              if columns else np.full(len(run.frame), np.nan))
    decisions = np.zeros(len(scores), dtype=bool)
    streak = 0
    for index, score in enumerate(scores):
        if index and (run.times[index] - run.times[index - 1]).total_seconds() > settings.max_gap_seconds:
            streak = 0
        streak = streak + 1 if score >= settings.mad_cutoff else 0
        decisions[index] = streak >= settings.mad_persistence
    return scores, decisions
