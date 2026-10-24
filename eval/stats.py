"""Bootstrap confidence intervals, resampled over tasks.

Resampling is over tasks, not over steps. Steps within a task are correlated —
one bad grounding decision produces several failed steps — so a step-level
bootstrap understates the interval and manufactures significance.

Comparisons against baselines are paired on the same task set, which is what
makes "the router beats always-AX by X points" a statement about the policy
rather than about which tasks each policy happened to draw.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, Sequence

import numpy as np

DEFAULT_RESAMPLES = 1000


@dataclass
class Interval:
    point: float
    low: float
    high: float
    level: float = 0.95
    resamples: int = DEFAULT_RESAMPLES

    def to_dict(self) -> dict[str, float]:
        return {
            "point": self.point,
            "ci_low": self.low,
            "ci_high": self.high,
            "level": self.level,
            "resamples": self.resamples,
        }

    def __str__(self) -> str:
        return f"{self.point:.4f} [{self.low:.4f}, {self.high:.4f}]"


def _task_index(task_ids: Sequence[str]) -> tuple[list[str], dict[str, np.ndarray]]:
    unique: list[str] = []
    positions: dict[str, list[int]] = {}
    for position, task in enumerate(task_ids):
        if task not in positions:
            positions[task] = []
            unique.append(task)
        positions[task].append(position)
    return unique, {task: np.asarray(idx, dtype=int) for task, idx in positions.items()}


def bootstrap_over_tasks(
    values: np.ndarray,
    task_ids: Sequence[str],
    statistic: Callable[[np.ndarray], float] = np.mean,
    resamples: int = DEFAULT_RESAMPLES,
    level: float = 0.95,
    seed: int = 0,
) -> Interval:
    """CI for a per-step statistic, resampling whole tasks with replacement."""
    values = np.asarray(values, dtype=float)
    if len(values) == 0:
        return Interval(float("nan"), float("nan"), float("nan"), level, resamples)

    unique, positions = _task_index(task_ids)
    if len(unique) < 2:
        point = float(statistic(values))
        return Interval(point, point, point, level, resamples)

    generator = np.random.default_rng(seed)
    point = float(statistic(values))
    draws = np.empty(resamples, dtype=float)

    for i in range(resamples):
        chosen = generator.integers(0, len(unique), size=len(unique))
        index = np.concatenate([positions[unique[c]] for c in chosen])
        draws[i] = statistic(values[index])

    alpha = (1.0 - level) / 2.0
    return Interval(
        point=point,
        low=float(np.quantile(draws, alpha)),
        high=float(np.quantile(draws, 1.0 - alpha)),
        level=level,
        resamples=resamples,
    )


def paired_difference(
    a: np.ndarray,
    b: np.ndarray,
    task_ids: Sequence[str],
    resamples: int = DEFAULT_RESAMPLES,
    level: float = 0.95,
    seed: int = 0,
) -> Interval:
    """CI for mean(a) − mean(b) on the same steps, resampled over tasks.

    Both policies are evaluated on identical steps, so the difference is taken
    per step before resampling. An interval excluding zero is the paired claim;
    an interval straddling it is reported as straddling it.
    """
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    if a.shape != b.shape:
        raise ValueError(f"paired comparison needs equal shapes, got {a.shape} and {b.shape}")
    return bootstrap_over_tasks(a - b, task_ids, np.mean, resamples, level, seed)


def bootstrap_auroc(
    y_true: np.ndarray,
    scores: np.ndarray,
    task_ids: Sequence[str],
    resamples: int = DEFAULT_RESAMPLES,
    level: float = 0.95,
    seed: int = 0,
) -> Interval:
    """CI for AUROC. Resamples that end up single-class are skipped."""
    from sklearn.metrics import roc_auc_score

    y_true = np.asarray(y_true, dtype=bool)
    scores = np.asarray(scores, dtype=float)
    unique, positions = _task_index(task_ids)
    generator = np.random.default_rng(seed)

    if len(np.unique(y_true)) < 2:
        return Interval(float("nan"), float("nan"), float("nan"), level, resamples)

    point = float(roc_auc_score(y_true, scores))
    draws: list[float] = []
    for _ in range(resamples):
        chosen = generator.integers(0, len(unique), size=len(unique))
        index = np.concatenate([positions[unique[c]] for c in chosen])
        sampled = y_true[index]
        if len(np.unique(sampled)) < 2:
            continue
        draws.append(float(roc_auc_score(sampled, scores[index])))

    if not draws:
        return Interval(point, float("nan"), float("nan"), level, resamples)

    alpha = (1.0 - level) / 2.0
    return Interval(
        point=point,
        low=float(np.quantile(draws, alpha)),
        high=float(np.quantile(draws, 1.0 - alpha)),
        level=level,
        resamples=len(draws),
    )


def latency_percentiles(values: np.ndarray) -> dict[str, Optional[float]]:
    """p50 and p95, reported separately.

    p95 is not decoration. The whole argument for a learned router over a fixed
    accessibility-first ladder rests on the tail, where ``getFullAXTree`` can
    hang while a screenshot returns in milliseconds. A mean hides exactly the
    thing the paper claims to fix.
    """
    values = np.asarray([v for v in values if v is not None and np.isfinite(v)], dtype=float)
    if len(values) == 0:
        return {"p50": None, "p95": None, "p99": None, "max": None, "mean": None, "n": 0}
    return {
        "p50": float(np.percentile(values, 50)),
        "p95": float(np.percentile(values, 95)),
        "p99": float(np.percentile(values, 99)),
        "max": float(values.max()),
        "mean": float(values.mean()),
        "n": int(len(values)),
    }
