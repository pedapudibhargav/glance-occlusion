"""End-to-end policy evaluation.

Loads the trained router, scores a test split, runs the six baselines on the
same steps, sweeps the router's threshold, and writes everything the figures
and the paper tables are built from.

Nothing here reports a single operating point on its own. The router's value is
a curve against fixed policies, and a point without its curve is not evidence.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

from probe.schema import LABEL_COLUMN, ORACLE_LABEL_COLUMN, assert_single_schema
from router import baselines
from router.features import build_matrix, make_splits
from router.policy import (
    CostModel,
    oracle_gap_recovered,
    pareto_front,
    sweep_l_fail,
    sweep_thresholds,
)

from .jsonio import write as write_json
from .stats import bootstrap_over_tasks, latency_percentiles, paired_difference

logger = logging.getLogger(__name__)

SPLIT_NAMES = ("in_library", "cross_library", "core", "external")


def load_router(path: Path) -> dict[str, Any]:
    import joblib

    payload = joblib.load(path)
    return payload


def score_frame(frame: pd.DataFrame, payload: dict[str, Any]) -> np.ndarray:
    """Calibrated P(structure-only fails) for every row."""
    card = payload.get("card", {})
    one_hot = card.get("backend") == "logistic"
    drop_groups = tuple(card.get("groups_dropped", ()))
    matrix = build_matrix(frame, drop_groups=drop_groups, one_hot=one_hot)

    expected = card.get("feature_columns")
    if expected and list(matrix.X.columns) != list(expected):
        # Reindex rather than fail: a one-hot column absent from this split
        # would otherwise make the whole evaluation unrunnable.
        missing = [c for c in expected if c not in matrix.X.columns]
        if missing:
            logger.warning("columns missing at scoring time, filled with 0: %s", missing)
        matrix.X = matrix.X.reindex(columns=list(expected), fill_value=0)

    scores = payload["model"].predict_proba(matrix.X)[:, 1]
    calibrator = payload.get("calibrator")
    if calibrator is not None:
        scores = np.clip(calibrator.predict(scores), 0.0, 1.0)
    return scores


def evaluate_split(
    name: str,
    frame: pd.DataFrame,
    payload: dict[str, Any],
    cost: CostModel,
    seed: int = 0,
    resamples: int = 1000,
) -> dict[str, Any]:
    if len(frame) == 0:
        return {"split": name, "n": 0, "note": "split empty"}

    scores = score_frame(frame, payload)
    structure_succeeded = frame[LABEL_COLUMN].astype("boolean").fillna(False).to_numpy(dtype=bool)
    escalation_helps = (
        frame[ORACLE_LABEL_COLUMN].tolist() if ORACLE_LABEL_COLUMN in frame.columns else None
    )
    task_ids = frame["task_id"].astype(str).tolist()

    fixed = baselines.evaluate_all(
        frame=frame,
        structure_succeeded=structure_succeeded,
        escalation_helps=escalation_helps,
        cost=cost,
        task_ids=task_ids,
        seed=seed,
    )
    by_name = {outcome.name: outcome for outcome in fixed}

    curve = sweep_thresholds(
        failure_probability=scores,
        structure_succeeded=structure_succeeded,
        escalation_helps=escalation_helps,
        cost=cost,
        task_ids=task_ids,
    )
    family = sweep_l_fail(
        failure_probability=scores,
        structure_succeeded=structure_succeeded,
        escalation_helps=escalation_helps,
        cost=cost,
        task_ids=task_ids,
    )

    # The operating point implied by the cost model, not a point chosen to look
    # good: the threshold falls out of (c_pixel − c_struct) / L_fail.
    operating_threshold = cost.decision_threshold
    operating = min(curve, key=lambda o: abs((o.threshold or 0.0) - operating_threshold))

    always_ax = by_name["always_ax"]
    oracle_point = by_name["oracle"]
    recovered = oracle_gap_recovered(operating, always_ax, oracle_point)

    escalate_at_operating = scores >= (operating.threshold or operating_threshold)
    router_success = np.where(
        escalate_at_operating,
        structure_succeeded
        | np.asarray(
            [bool(v) if v is not None and v is not np.nan else False for v in (escalation_helps or [False] * len(frame))],
            dtype=bool,
        ),
        structure_succeeded,
    )

    success_ci = bootstrap_over_tasks(
        router_success.astype(float), task_ids, resamples=resamples, seed=seed
    )
    versus_always_ax = paired_difference(
        router_success.astype(float),
        structure_succeeded.astype(float),
        task_ids,
        resamples=resamples,
        seed=seed,
    )

    latency = {
        "probe_wall_ms": latency_percentiles(frame.get("probe_wall_ms", pd.Series(dtype=float))),
        "dispatch_ms": latency_percentiles(frame.get("dispatch_ms", pd.Series(dtype=float))),
        "ax_wall_ms": latency_percentiles(frame.get("ax_wall_ms", pd.Series(dtype=float))),
        "ax_timeout_rate": float(
            frame.get("ax_timed_out", pd.Series(dtype=bool)).fillna(False).mean()
        )
        if "ax_timed_out" in frame.columns
        else None,
    }

    return {
        "split": name,
        "n_steps": int(len(frame)),
        "n_tasks": int(frame["task_id"].nunique()),
        "structure_only_success_rate": float(structure_succeeded.mean()),
        "oracle_label_coverage": (
            float(pd.Series(escalation_helps).notna().mean()) if escalation_helps else 0.0
        ),
        "baselines": [outcome.to_dict() for outcome in fixed],
        "router_curve": [outcome.to_dict() for outcome in curve],
        "router_pareto": [outcome.to_dict() for outcome in pareto_front(curve)],
        "router_cost_rule_family": {
            key: [point.to_dict() for point in points] for key, points in family.items()
        },
        "operating_point": operating.to_dict()
        | {
            "success_rate_ci": success_ci.to_dict(),
            "gain_over_always_ax_ci": versus_always_ax.to_dict(),
        },
        "oracle_gap_recovered": recovered,
        "latency": latency,
        "cost_model": cost.to_dict(),
    }


def headline_sentence(result: dict[str, Any]) -> str:
    """The success sentence from the spec, filled in with real numbers."""
    operating = result.get("operating_point") or {}
    recovered = result.get("oracle_gap_recovered")
    baseline_by_name = {b["name"]: b for b in result.get("baselines", [])}
    always_pixel = baseline_by_name.get("always_pixel", {})

    pixel_tokens = always_pixel.get("observation_tokens_per_step") or 0.0
    router_tokens = operating.get("observation_tokens_per_step") or 0.0
    saved = (1.0 - router_tokens / pixel_tokens) * 100.0 if pixel_tokens else float("nan")

    return (
        f"On {result.get('n_steps', 0)} steps across {result.get('n_tasks', 0)} "
        f"{result.get('split', '')} tasks, GLANCE recovers "
        f"{(recovered * 100.0):.1f}% of the oracle routing gain "
        if recovered is not None
        else f"On {result.get('n_steps', 0)} steps, oracle gain was not measurable "
    ) + (
        f"while escalating to pixels on only "
        f"{(operating.get('escalation_rate') or 0.0) * 100.0:.1f}% of steps, "
        f"cutting observation tokens {saved:.1f}% against an always-screenshot baseline."
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate the router against all six baselines")
    parser.add_argument("--steps", type=Path, default=Path("data/steps.parquet"))
    parser.add_argument("--external", type=Path, default=Path("data/external/steps.parquet"))
    parser.add_argument("--router", type=Path, default=Path("artifacts/router/router.joblib"))
    parser.add_argument("--costs", type=Path, default=Path("data/costs.json"))
    parser.add_argument("--out", type=Path, default=Path("artifacts/eval/results.json"))
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--resamples", type=int, default=1000)
    parser.add_argument("--splits", default=",".join(SPLIT_NAMES))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    for path in (args.steps, args.router):
        if not path.exists():
            print(f"{path} not found", file=sys.stderr)
            return 2

    frame = pd.read_parquet(args.steps)
    assert_single_schema(frame.get("schema_hash", pd.Series(dtype=str)).tolist())
    external = pd.read_parquet(args.external) if args.external.exists() else None

    payload = load_router(args.router)
    cost = CostModel.default(args.costs)
    if not cost.is_measured():
        logger.warning(
            "cost constants are the literature fallback. Measure them in this "
            "harness before putting any token number in the paper."
        )

    splits = make_splits(frame, seed=args.seed, external=external)
    wanted = [s for s in args.splits.split(",") if s]

    results: dict[str, Any] = {
        "cost_model": cost.to_dict(),
        "model_card": payload.get("card", {}),
        "splits": {},
    }
    for name in wanted:
        split_frame = getattr(splits, name, None)
        if split_frame is None:
            logger.warning("no such split: %s", name)
            continue
        results["splits"][name] = evaluate_split(
            name=name,
            frame=split_frame,
            payload=payload,
            cost=cost,
            seed=args.seed,
            resamples=args.resamples,
        )

    write_json(args.out, results)

    for name, result in results["splits"].items():
        if result.get("n_steps"):
            print(f"\n[{name}] {headline_sentence(result)}")
            operating = result["operating_point"]
            print(
                f"  escalation_rate={operating['escalation_rate']:.3f} "
                f"tokens/step={operating['observation_tokens_per_step']:.0f} "
                f"step_success={operating['step_success_rate']:.4f} "
                f"gap_recovered={result['oracle_gap_recovered']}"
            )
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
