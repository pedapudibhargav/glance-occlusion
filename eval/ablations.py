"""The five ablations.

1. Feature group — drop each of the five groups, report ΔAUROC. Tests H5.
2. Model — boosted trees against logistic regression against the hand rule.
3. Calibration — raw scores against isotonic.
4. Transfer — train on one library, test on the other two, all six directions.
5. Model-agnosticism — the router against two different agent LLMs.

Ablation 2 is the one to read honestly. If the three-term hand rule does most
of the work, the paper says so and the contribution becomes the feature set,
which still answers the question TACTILE left open. Ablation 5 needs two
collection runs with different agent models, so it is reported from whatever
``run_id``/model values are present rather than run here.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

from probe.schema import LABEL_COLUMN, FEATURE_GROUPS
from router import baselines
from router.features import build_matrix, group_ablation_sets, make_splits
from router.train import fit_router

from .jsonio import write as write_json

logger = logging.getLogger(__name__)

LIBRARIES = ("antd", "mui", "mantine")


def feature_group_ablation(frame: pd.DataFrame, seed: int = 0, backend: str = "auto") -> list[dict[str, Any]]:
    """ΔAUROC per dropped group, on every held-out split."""
    splits = make_splits(frame, seed=seed)
    rows: list[dict[str, Any]] = []
    reference: Optional[dict[str, float]] = None

    for name, drop in group_ablation_sets():
        try:
            result = fit_router(splits, backend=backend, seed=seed, drop_groups=drop)
        except ValueError as exc:
            logger.warning("ablation %s failed: %s", name, exc)
            continue

        aurocs = {
            split: (result["metrics"].get(split) or {}).get("auroc")
            for split in ("in_library", "cross_library", "core")
        }
        if reference is None:
            reference = {k: v for k, v in aurocs.items() if v is not None}

        row: dict[str, Any] = {"ablation": name, "dropped": list(drop)}
        for split, auroc in aurocs.items():
            row[f"auroc_{split}"] = auroc
            base = (reference or {}).get(split)
            row[f"delta_{split}"] = (
                round(auroc - base, 4) if auroc is not None and base is not None else None
            )
        rows.append(row)

    return rows


def h5_verdict(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """H5: occlusion and staleness signals dominate.

    Falsified if removing them costs less than 0.05 AUROC.
    """
    combined = next((r for r in rows if r["ablation"] == "drop_occlusion_and_freshness"), None)
    if combined is None:
        return {"hypothesis": "H5", "verdict": "not evaluated"}

    deltas = [
        combined.get(f"delta_{split}")
        for split in ("in_library", "cross_library", "core")
        if combined.get(f"delta_{split}") is not None
    ]
    worst_drop = min(deltas) if deltas else None
    passed = worst_drop is not None and worst_drop <= -0.05
    return {
        "hypothesis": "H5",
        "threshold_auroc_drop": 0.05,
        "observed_drops": deltas,
        "largest_drop": worst_drop,
        "passed": bool(passed),
        "verdict": (
            "H5 holds: removing occlusion and freshness costs more than 0.05 AUROC"
            if passed
            else "H5 falsified: those groups are not dominant; report the actual "
            "attribution rather than the hypothesis"
        ),
    }


def model_ablation(frame: pd.DataFrame, seed: int = 0) -> list[dict[str, Any]]:
    """Boosted trees against logistic regression against the hand rule."""
    splits = make_splits(frame, seed=seed)
    rows: list[dict[str, Any]] = []

    from router.train import available_backends

    tree_backend = available_backends()[0]
    for backend in (tree_backend, "logistic"):
        try:
            result = fit_router(splits, backend=backend, seed=seed)
        except ValueError as exc:
            logger.warning("model ablation %s failed: %s", backend, exc)
            continue
        rows.append(
            {
                "model": backend,
                **{
                    f"auroc_{split}": (result["metrics"].get(split) or {}).get("auroc")
                    for split in ("in_library", "cross_library", "core")
                },
                **{
                    f"ece_{split}": (result["metrics"].get(split) or {}).get("ece")
                    for split in ("in_library", "cross_library", "core")
                },
                "decision_latency_ms_p95": result["card"].decision_latency_ms_p95,
            }
        )

    # The hand rule is not a probability model, so its AUROC is the AUROC of a
    # single binary decision — reported as such, not dressed up as a score.
    for split in ("in_library", "cross_library", "core"):
        split_frame: pd.DataFrame = getattr(splits, split)
        if len(split_frame) == 0:
            continue
        escalate = baselines.hand_rule(split_frame)
        truth = ~split_frame[LABEL_COLUMN].astype("boolean").fillna(False).to_numpy(dtype=bool)
        if len(np.unique(truth)) < 2:
            continue
        rows.append(
            {
                "model": "hand_rule",
                "split": split,
                f"auroc_{split}": float(roc_auc_score(truth, escalate.astype(float))),
                "note": "binary rule: hit_is_self AND ax_name_present AND page_settled",
            }
        )

    return rows


def calibration_ablation(frame: pd.DataFrame, seed: int = 0, backend: str = "auto") -> list[dict[str, Any]]:
    """Raw scores against isotonic, to show why calibration matters."""
    splits = make_splits(frame, seed=seed)
    rows: list[dict[str, Any]] = []
    try:
        result = fit_router(splits, backend=backend, seed=seed, calibration="isotonic")
    except ValueError as exc:
        logger.warning("calibration ablation failed: %s", exc)
        return rows

    for split in ("in_library", "cross_library", "core"):
        metrics = result["metrics"].get(split) or {}
        raw = metrics.get("raw_uncalibrated") or {}
        rows.append(
            {
                "split": split,
                "ece_isotonic": metrics.get("ece"),
                "ece_raw": raw.get("ece"),
                "brier_isotonic": metrics.get("brier"),
                "brier_raw": raw.get("brier"),
                "auroc_unchanged": metrics.get("auroc") == raw.get("auroc"),
                "note": (
                    "isotonic is monotone, so AUROC is unchanged by construction; "
                    "the gain is in ECE and Brier, which is what the cost threshold needs"
                ),
            }
        )
    return rows


def transfer_ablation(frame: pd.DataFrame, seed: int = 0, backend: str = "auto") -> list[dict[str, Any]]:
    """Train on one library, test on each other — all six directions.

    Tests H4: the router transfers across UI libraries, falsified if held-out
    library AUROC drops more than 0.10 from in-library.
    """
    rows: list[dict[str, Any]] = []
    library = frame.get("library", pd.Series(index=frame.index, dtype=str)).fillna("").astype(str)

    for train_library in LIBRARIES:
        train_frame = frame[library == train_library]
        if len(train_frame) < 200:
            logger.warning("skipping transfer from %s: only %d rows", train_library, len(train_frame))
            continue

        splits = make_splits(
            train_frame.assign(library=train_library),
            seed=seed,
        )
        try:
            result = fit_router(splits, backend=backend, seed=seed)
        except ValueError as exc:
            logger.warning("transfer from %s failed: %s", train_library, exc)
            continue

        in_library_auroc = (result["metrics"].get("in_library") or {}).get("auroc")

        from eval.run_eval import score_frame

        payload = {"model": result["model"], "calibrator": result["calibrator"], "card": result["card"].__dict__}
        for test_library in LIBRARIES:
            if test_library == train_library:
                continue
            test_frame = frame[library == test_library]
            if len(test_frame) == 0:
                continue
            scores = score_frame(test_frame, payload)
            truth = ~test_frame[LABEL_COLUMN].astype("boolean").fillna(False).to_numpy(dtype=bool)
            if len(np.unique(truth)) < 2:
                continue
            auroc = float(roc_auc_score(truth, scores))
            rows.append(
                {
                    "train_library": train_library,
                    "test_library": test_library,
                    "auroc": auroc,
                    "in_library_auroc": in_library_auroc,
                    "delta": round(auroc - in_library_auroc, 4) if in_library_auroc else None,
                    "h4_falsified": (
                        bool(in_library_auroc - auroc > 0.10) if in_library_auroc else None
                    ),
                }
            )
    return rows


def model_agnosticism_report(frame: pd.DataFrame) -> dict[str, Any]:
    """Whether the data supports the model-agnosticism check at all.

    If the routing policy helps two different agent LLMs, the contribution is
    about the page rather than the model, which is the stronger claim. That
    needs two collection runs with different agents; this reports what is
    present rather than pretending to run it.
    """
    runs = sorted(frame.get("run_id", pd.Series(dtype=str)).dropna().unique().tolist())
    return {
        "run_ids_present": runs,
        "n_runs": len(runs),
        "ready": len(runs) >= 2,
        "note": (
            "collect a second structure-only pass with a different agent model "
            "and re-run; one run cannot support the model-agnosticism claim"
        )
        if len(runs) < 2
        else "two or more runs present; compare router gain per run_id",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the five ablations")
    parser.add_argument("--steps", type=Path, default=Path("data/steps.parquet"))
    parser.add_argument("--out", type=Path, default=Path("artifacts/ablations"))
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--backend", default="auto")
    parser.add_argument(
        "--only",
        default="",
        help="comma-separated subset of: features,model,calibration,transfer,agnosticism",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if not args.steps.exists():
        print(f"{args.steps} not found; run collect.join_label first")
        return 2

    frame = pd.read_parquet(args.steps)
    wanted = set(args.only.split(",")) if args.only else {
        "features", "model", "calibration", "transfer", "agnosticism"
    }

    out: dict[str, Any] = {}
    if "features" in wanted:
        rows = feature_group_ablation(frame, args.seed, args.backend)
        out["feature_group_ablation"] = rows
        out["h5"] = h5_verdict(rows)
    if "model" in wanted:
        out["model_ablation"] = model_ablation(frame, args.seed)
    if "calibration" in wanted:
        out["calibration_ablation"] = calibration_ablation(frame, args.seed, args.backend)
    if "transfer" in wanted:
        out["transfer_ablation"] = transfer_ablation(frame, args.seed, args.backend)
    if "agnosticism" in wanted:
        out["model_agnosticism"] = model_agnosticism_report(frame)

    args.out.mkdir(parents=True, exist_ok=True)
    for name, payload in out.items():
        path = write_json(args.out / f"{name}.json", payload)
        print(f"wrote {path}")

        if isinstance(payload, list) and payload:
            table = pd.DataFrame(payload)
            csv_path = args.out / f"{name}.csv"
            table.to_csv(csv_path, index=False)
            print(f"wrote {csv_path}")

    if "h5" in out:
        print("\n" + json.dumps(out["h5"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
