"""Measure the observation token costs in this harness.

Prefer measuring to citing. Every cost number in the paper should come from
this project's own instrumentation: published per-screenshot token figures are
secondary sources and change with every model release, and a reviewer is
entitled to ask where the number came from.

What this measures, from the benchmark's own traces:

    c_struct  tokens the structured observation adds to the prompt
    c_pixel   tokens the screenshot adds to the prompt
    L_fail    tokens wasted by a failed action, including recovery

``L_fail`` is estimated from the traces as the observation cost of the steps
that followed a failed action in the same task. It remains the least certain
constant, which is why the evaluation sweeps it rather than trusting it.
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from .jsonio import write as write_json

logger = logging.getLogger(__name__)

# An image's token cost depends on its pixel dimensions, not on its content.
# This is the general form for Claude-family vision models; it is a starting
# estimate, and a measured prompt-token delta from the harness overrides it.
def image_tokens_estimate(width: int, height: int) -> float:
    return (width * height) / 750.0


def text_tokens_estimate(text: str) -> float:
    """Rough token count without pulling in a tokenizer.

    Only used where the harness did not record real usage. Roughly four
    characters per token for English prose and markup.
    """
    return len(text) / 4.0


def from_benchmark_traces(results_dir: Path) -> dict[str, Any]:
    """Read per-step prompt token usage out of ComponentBench run artefacts.

    The benchmark writes one JSONL of results per run. Where the agent recorded
    usage, the difference between a structured-observation prompt and a
    screenshot prompt on the same task is the measurement wanted here.
    """
    records: list[dict[str, Any]] = []
    for path in sorted(results_dir.rglob("*.jsonl")):
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    if not records:
        return {}

    frame = pd.DataFrame(records)
    usage_columns = [c for c in frame.columns if "token" in c.lower() or "usage" in c.lower()]
    return {
        "n_records": int(len(frame)),
        "usage_columns_found": usage_columns,
        "modes": sorted(frame["mode"].dropna().unique().tolist()) if "mode" in frame else [],
    }


def measure_from_steps(steps_path: Path) -> dict[str, Any]:
    """Estimate ``L_fail`` from the collected step table.

    For each failed action, count how many further steps that task needed.
    Multiplied by the per-step structured cost, that is the waste a failure
    causes — the quantity the decision rule weighs against the screenshot.
    """
    frame = pd.read_parquet(steps_path)
    if "structure_only_succeeded" not in frame.columns:
        return {}

    frame = frame.sort_values(["task_id", "step_index"])
    steps_after_failure: list[int] = []
    for _, task in frame.groupby("task_id"):
        labels = task["structure_only_succeeded"].astype("boolean").fillna(True).to_numpy()
        total = len(labels)
        for position, succeeded in enumerate(labels):
            if not succeeded:
                steps_after_failure.append(total - position - 1)

    if not steps_after_failure:
        return {"recovery_steps_mean": None, "n_failures": 0}

    series = pd.Series(steps_after_failure, dtype=float)
    return {
        "n_failures": int(len(series)),
        "recovery_steps_mean": float(series.mean()),
        "recovery_steps_p50": float(series.median()),
        "recovery_steps_p95": float(series.quantile(0.95)),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure observation token costs")
    parser.add_argument("--steps", type=Path, default=Path("data/steps.parquet"))
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=Path("vendor/ComponentBench/results"),
        help="ComponentBench run artefacts, for recorded prompt token usage",
    )
    parser.add_argument("--out", type=Path, default=Path("data/costs.json"))
    parser.add_argument("--model-id", default="", help="The model these costs were measured for")
    parser.add_argument(
        "--c-struct",
        type=float,
        default=None,
        help="Measured tokens for the structured observation; overrides estimation",
    )
    parser.add_argument(
        "--c-pixel",
        type=float,
        default=None,
        help="Measured tokens for the screenshot; overrides estimation",
    )
    parser.add_argument("--viewport", default="1280x720", help="Screenshot size, for the estimate")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    width, _, height = args.viewport.partition("x")
    estimated_pixel = image_tokens_estimate(int(width), int(height or 720))

    c_struct = args.c_struct
    c_pixel = args.c_pixel if args.c_pixel is not None else estimated_pixel
    source = "measured" if (args.c_struct is not None and args.c_pixel is not None) else "partially_estimated"

    if c_struct is None:
        # Ref-based accessibility snapshots run roughly 200–400 tokens against
        # 3,000–5,000 for a full DOM. Taking the top of that range is the
        # conservative choice: it makes escalation look cheaper, so it does not
        # flatter the router.
        c_struct = 400.0
        source = "partially_estimated"

    recovery = measure_from_steps(args.steps) if args.steps.exists() else {}
    traces = from_benchmark_traces(args.results_dir) if args.results_dir.exists() else {}

    recovery_steps = recovery.get("recovery_steps_mean")
    l_fail = (recovery_steps + 1) * c_struct if recovery_steps is not None else 1200.0

    payload = {
        "c_struct": c_struct,
        "c_pixel": c_pixel,
        "l_fail": l_fail,
        "source": source,
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "model_id": args.model_id,
        "viewport": args.viewport,
        "screenshot_tokens_estimate": estimated_pixel,
        "recovery": recovery,
        "benchmark_traces": traces,
        "notes": (
            "c_struct/c_pixel passed on the command line are measured values. "
            "Anything marked partially_estimated must not be cited as measured "
            "in the paper."
        ),
    }

    write_json(args.out, payload)
    print(json.dumps(payload, indent=2))

    if source != "measured":
        print(
            "\nWARNING: these constants are not fully measured. Pass --c-struct "
            "and --c-pixel from your harness instrumentation before publishing.",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
