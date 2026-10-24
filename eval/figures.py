"""The headline figure, the reliability diagram, and the latency tail.

The headline figure is one plot: observation tokens per step on x, task success
on y, the six baselines as points, the router as a curve traced by sweeping its
threshold. The claim is visually obvious or it is not there — the router's
curve sits above and to the left of every fixed policy, approaching the oracle.

If that plot shows no separation, there is no paper, and the point of building
this early is to find that out in week 6 rather than month 6.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any, Optional

import matplotlib

matplotlib.use("Agg")  # no display in Docker or CI
import matplotlib.pyplot as plt  # noqa: E402

logger = logging.getLogger(__name__)

BASELINE_STYLE = {
    "always_ax": {"marker": "v", "color": "#2b6cb0", "label": "Always-AX"},
    "always_pixel": {"marker": "^", "color": "#c05621", "label": "Always-pixel"},
    "always_both": {"marker": "s", "color": "#744210", "label": "Always-both"},
    "fixed_ladder": {"marker": "D", "color": "#2f855a", "label": "Fixed ladder (TACTILE order)"},
    "hand_rule": {"marker": "P", "color": "#6b46c1", "label": "Hand rule"},
    "oracle": {"marker": "*", "color": "#b7791f", "label": "Oracle"},
}


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def headline_figure(result: dict[str, Any], out_path: Path, title: str = "") -> Path:
    """Cost–accuracy frontier for one split."""
    figure, axes = plt.subplots(figsize=(7.2, 5.0), dpi=160)

    curve = result.get("router_curve", [])
    if curve:
        ordered = sorted(curve, key=lambda p: p["observation_tokens_per_step"])
        axes.plot(
            [p["observation_tokens_per_step"] for p in ordered],
            [p["step_success_rate"] for p in ordered],
            "-",
            color="#1a202c",
            linewidth=1.8,
            label="GLANCE router (threshold swept)",
            zorder=3,
        )

    pareto = result.get("router_pareto", [])
    if pareto:
        axes.plot(
            [p["observation_tokens_per_step"] for p in pareto],
            [p["step_success_rate"] for p in pareto],
            "o",
            markersize=3,
            color="#1a202c",
            zorder=4,
        )

    randoms = [b for b in result.get("baselines", []) if b["name"].startswith("random@")]
    if randoms:
        ordered_random = sorted(randoms, key=lambda p: p["observation_tokens_per_step"])
        axes.plot(
            [p["observation_tokens_per_step"] for p in ordered_random],
            [p["step_success_rate"] for p in ordered_random],
            "--",
            color="#a0aec0",
            linewidth=1.4,
            label="Random escalation (rate swept)",
            zorder=2,
        )

    for baseline in result.get("baselines", []):
        style = BASELINE_STYLE.get(baseline["name"])
        if style is None:
            continue
        axes.plot(
            baseline["observation_tokens_per_step"],
            baseline["step_success_rate"],
            style["marker"],
            color=style["color"],
            markersize=11 if baseline["name"] == "oracle" else 8,
            label=style["label"],
            zorder=5,
        )

    operating = result.get("operating_point")
    if operating:
        axes.plot(
            operating["observation_tokens_per_step"],
            operating["step_success_rate"],
            "o",
            markerfacecolor="none",
            markeredgecolor="#e53e3e",
            markeredgewidth=2,
            markersize=13,
            label="Router at cost-implied threshold",
            zorder=6,
        )
        ci = (operating.get("success_rate_ci") or {})
        if ci.get("ci_low") is not None:
            axes.vlines(
                operating["observation_tokens_per_step"],
                ci["ci_low"],
                ci["ci_high"],
                color="#e53e3e",
                linewidth=1.2,
                zorder=6,
            )

    axes.set_xlabel("Observation tokens per step")
    axes.set_ylabel("Step success rate")
    split = result.get("split", "")
    axes.set_title(title or f"Cost–accuracy frontier · {split} ({result.get('n_steps', 0)} steps)")
    axes.grid(alpha=0.25, linestyle=":")
    axes.legend(fontsize=7.5, loc="lower right", framealpha=0.95)

    recovered = result.get("oracle_gap_recovered")
    if recovered is not None:
        axes.annotate(
            f"oracle gap recovered: {recovered * 100:.1f}%\n"
            f"escalation rate: {(operating or {}).get('escalation_rate', 0) * 100:.1f}%",
            xy=(0.03, 0.97),
            xycoords="axes fraction",
            va="top",
            fontsize=8,
            bbox=dict(boxstyle="round,pad=0.4", facecolor="#f7fafc", edgecolor="#cbd5e0"),
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    figure.tight_layout()
    figure.savefig(out_path)
    plt.close(figure)
    return out_path


def reliability_diagram(metrics: dict[str, Any], split: str, out_path: Path) -> Optional[Path]:
    """Reliability diagram for the calibrated router. Reviewers will ask."""
    split_metrics = metrics.get(split) or {}
    bins = split_metrics.get("reliability")
    if not bins:
        logger.warning("no reliability data for split %s", split)
        return None

    figure, axes = plt.subplots(figsize=(5.0, 5.0), dpi=160)
    axes.plot([0, 1], [0, 1], ":", color="#a0aec0", label="Perfect calibration")

    populated = [b for b in bins if b.get("count", 0) > 0]
    axes.plot(
        [b["confidence"] for b in populated],
        [b["accuracy"] for b in populated],
        "o-",
        color="#2b6cb0",
        label=f"Calibrated (ECE={split_metrics.get('ece', float('nan')):.4f})",
    )

    raw = (split_metrics.get("raw_uncalibrated") or {}).get("reliability")
    if raw:
        raw_populated = [b for b in raw if b.get("count", 0) > 0]
        axes.plot(
            [b["confidence"] for b in raw_populated],
            [b["accuracy"] for b in raw_populated],
            "s--",
            color="#c05621",
            alpha=0.8,
            label=(
                "Raw scores "
                f"(ECE={(split_metrics.get('raw_uncalibrated') or {}).get('ece', float('nan')):.4f})"
            ),
        )

    axes.set_xlabel("Predicted P(structure-only fails)")
    axes.set_ylabel("Observed failure rate")
    axes.set_title(f"Reliability · {split}")
    axes.set_xlim(0, 1)
    axes.set_ylim(0, 1)
    axes.grid(alpha=0.25, linestyle=":")
    axes.legend(fontsize=8, loc="upper left")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    figure.tight_layout()
    figure.savefig(out_path)
    plt.close(figure)
    return out_path


def latency_tail_figure(result: dict[str, Any], out_path: Path) -> Optional[Path]:
    """p50 against p95, because the tail is the argument.

    A fixed accessibility-first policy has an unbounded worst case: a screenshot
    returned in 54 ms on a page where the AX snapshot failed at 180004 ms. A
    mean would hide that, so p50 and p95 are drawn side by side.
    """
    latency = result.get("latency") or {}
    series = {k: v for k, v in latency.items() if isinstance(v, dict) and v.get("n")}
    if not series:
        return None

    figure, axes = plt.subplots(figsize=(6.4, 4.0), dpi=160)
    names = list(series)
    positions = range(len(names))
    width = 0.38

    axes.bar(
        [p - width / 2 for p in positions],
        [series[n].get("p50") or 0 for n in names],
        width,
        label="p50",
        color="#2b6cb0",
    )
    axes.bar(
        [p + width / 2 for p in positions],
        [series[n].get("p95") or 0 for n in names],
        width,
        label="p95",
        color="#c05621",
    )

    axes.set_xticks(list(positions))
    axes.set_xticklabels(names, fontsize=8)
    axes.set_ylabel("Milliseconds")
    axes.set_title(f"Per-step latency · {result.get('split', '')}")
    axes.set_yscale("log")
    axes.grid(alpha=0.25, linestyle=":", axis="y")
    axes.legend(fontsize=8)

    timeout_rate = latency.get("ax_timeout_rate")
    if timeout_rate is not None:
        axes.annotate(
            f"AX timeout rate: {timeout_rate * 100:.2f}%",
            xy=(0.98, 0.95),
            xycoords="axes fraction",
            ha="right",
            va="top",
            fontsize=8,
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    figure.tight_layout()
    figure.savefig(out_path)
    plt.close(figure)
    return out_path


def importance_figure(importances: list[dict[str, Any]], out_path: Path, top: int = 20) -> Optional[Path]:
    """Feature importance for RQ4."""
    if not importances:
        return None
    top_features = importances[:top][::-1]

    figure, axes = plt.subplots(figsize=(6.4, 0.3 * len(top_features) + 1.2), dpi=160)
    axes.barh(
        [f["feature"] for f in top_features],
        [f["importance"] for f in top_features],
        color="#2f855a",
    )
    axes.set_xlabel(f"Importance ({top_features[0].get('kind', '')})")
    axes.set_title("Which signals carry the predictive weight (RQ4)")
    axes.grid(alpha=0.25, linestyle=":", axis="x")
    axes.tick_params(axis="y", labelsize=8)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    figure.tight_layout()
    figure.savefig(out_path)
    plt.close(figure)
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Emit the paper's figures")
    parser.add_argument("--results", type=Path, default=Path("artifacts/eval/results.json"))
    parser.add_argument("--metrics", type=Path, default=Path("artifacts/router/metrics.json"))
    parser.add_argument("--importances", type=Path, default=Path("artifacts/router/importances.json"))
    parser.add_argument("--out-dir", type=Path, default=Path("paper/figures"))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    written: list[Path] = []

    if args.results.exists():
        results = _load(args.results)
        for split, result in (results.get("splits") or {}).items():
            if not result.get("n_steps"):
                continue
            written.append(
                headline_figure(result, args.out_dir / f"headline_{split}.png")
            )
            latency = latency_tail_figure(result, args.out_dir / f"latency_{split}.png")
            if latency:
                written.append(latency)
    else:
        logger.warning("%s not found; skipping the cost–accuracy figures", args.results)

    if args.metrics.exists():
        metrics = _load(args.metrics)
        for split in ("in_library", "cross_library", "core", "external"):
            path = reliability_diagram(metrics, split, args.out_dir / f"reliability_{split}.png")
            if path:
                written.append(path)
    else:
        logger.warning("%s not found; skipping the reliability diagrams", args.metrics)

    if args.importances.exists():
        path = importance_figure(_load(args.importances), args.out_dir / "importances.png")
        if path:
            written.append(path)

    if not written:
        print("nothing to plot — run eval.run_eval first")
        return 1
    for path in written:
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
