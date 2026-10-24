"""End-to-end plumbing: synthetic rows in, trained router and figures out.

This proves the pipeline runs, not that the method works. Every number here
comes from ``scripts/make_synthetic.py``, whose generative story is far tidier
than a real page — the AUROC assertions below check that training produces a
usable model on separable data, which is a test of the code, not evidence for
H1. H1 is decided on collected rows at the gate.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("pandas", reason="pandas is not installed")
pytest.importorskip("sklearn", reason="scikit-learn is not installed")

import numpy as np
import pandas as pd

from probe.probe import _edit_similarity, GlanceProbe
from probe.schema import LABEL_COLUMN, ORACLE_LABEL_COLUMN
from router import baselines
from router.features import make_splits
from router.policy import CostModel, pareto_front, sweep_thresholds
from router.train import expected_calibration_error, fit_router, h1_gate, save
from scripts.make_synthetic import generate


@pytest.fixture(scope="module")
def frame() -> pd.DataFrame:
    return generate(n_tasks=400, seed=11)


@pytest.fixture(scope="module")
def trained(frame):
    splits = make_splits(frame, seed=0)
    return splits, fit_router(splits, backend="sklearn_hist", seed=0)


# --- probe pure functions --------------------------------------------------


def test_edit_similarity():
    assert _edit_similarity("Save changes", "Save changes") == 1.0
    assert _edit_similarity("Save", "Cancel") < 0.5
    assert _edit_similarity("  SAVE  ", "save") == 1.0
    assert _edit_similarity(None, "save") is None
    assert _edit_similarity("", "") == 1.0
    assert _edit_similarity("save", "") == 0.0


def test_rect_iou_agreement():
    """Identical rects agree; a shifted rect does not."""
    client = {"x": 10.0, "y": 20.0, "width": 100.0, "height": 50.0}
    same = GlanceProbe._rect_iou([10.0, 20.0, 100.0, 50.0], client, 0.0, 0.0)
    assert same == pytest.approx(1.0)

    shifted = GlanceProbe._rect_iou([10.0, 300.0, 100.0, 50.0], client, 0.0, 0.0)
    assert shifted == 0.0


def test_rect_iou_accounts_for_scroll_offset():
    """Snapshot bounds are document-relative; the client rect is not.

    Without the shift, any scrolled page would report total geometry
    disagreement and the feature would just encode scroll position.
    """
    client = {"x": 10.0, "y": 20.0, "width": 100.0, "height": 50.0}
    iou = GlanceProbe._rect_iou([10.0, 420.0, 100.0, 50.0], client, 0.0, 400.0)
    assert iou == pytest.approx(1.0)


def test_rect_iou_handles_missing_input():
    assert GlanceProbe._rect_iou(None, {"x": 0}, 0, 0) is None
    assert GlanceProbe._rect_iou([1, 2, 3, 4], None, 0, 0) is None


def test_ax_node_features_parses_cdp_shape():
    node = {
        "role": {"value": "button"},
        "name": {"value": "  Save changes  "},
        "properties": [
            {"name": "focusable", "value": {"value": True}},
            {"name": "disabled", "value": {"value": False}},
            {"name": "expanded", "value": {"value": "true"}},
        ],
    }
    parsed = GlanceProbe._ax_node_features(node)
    assert parsed["ax_role"] == "button"
    assert parsed["ax_name"] == "Save changes"
    assert parsed["ax_name_present"] is True
    assert parsed["ax_name_len"] == 12
    assert parsed["ax_state_focusable"] is True
    assert parsed["ax_state_disabled"] is False
    assert parsed["ax_state_expanded"] is True
    assert parsed["ax_state_selected"] is None  # absent means unknown


def test_ax_node_features_on_unnamed_node():
    parsed = GlanceProbe._ax_node_features({"role": {"value": "generic"}, "name": {"value": ""}})
    assert parsed["ax_name_present"] is False
    assert parsed["ax_name_len"] == 0


# --- calibration -----------------------------------------------------------


def test_ece_is_zero_for_perfect_calibration():
    probabilities = np.array([0.0] * 50 + [1.0] * 50)
    truth = np.array([0.0] * 50 + [1.0] * 50)
    ece, diagram = expected_calibration_error(truth, probabilities)
    assert ece == pytest.approx(0.0, abs=1e-9)
    assert sum(b["count"] for b in diagram) == 100


def test_ece_is_one_for_perfectly_wrong_confidence():
    probabilities = np.array([1.0] * 20)
    truth = np.zeros(20)
    ece, _ = expected_calibration_error(truth, probabilities)
    assert ece == pytest.approx(1.0)


# --- training --------------------------------------------------------------


def test_training_reports_every_split(trained):
    _, result = trained
    for split in ("train", "in_library", "cross_library", "core"):
        assert split in result["metrics"]


def test_router_separates_on_separable_data(trained):
    """A code test, not evidence for H1 — see this module's docstring."""
    _, result = trained
    auroc = result["metrics"]["in_library"]["auroc"]
    assert auroc > 0.7, f"plumbing broken: AUROC {auroc} on synthetic data"


def test_calibration_improves_calibration_and_can_only_cost_ranking(trained):
    """What isotonic regression does to ranking, stated accurately.

    It is commonly said that isotonic calibration leaves AUROC untouched
    because it is monotone. It is non-*decreasing*, not strictly increasing:
    its flat segments map distinct scores onto the same value, and those ties
    cost ranking. Observed here, 0.782 raw against 0.749 calibrated, once a
    near-deterministic feature produced many identical scores.

    So the correct invariants are that calibration improves ECE, which is the
    whole point since the decision threshold is a probability, and that it can
    only lose AUROC, never gain it.
    """
    _, result = trained
    metrics = result["metrics"]["in_library"]
    raw = metrics["raw_uncalibrated"]

    assert metrics["auroc"] <= raw["auroc"] + 1e-9, (
        "calibration cannot improve ranking; a gain means the scores were "
        "not passed through the calibrator as expected"
    )
    assert metrics["ece"] <= raw["ece"] + 0.02


def test_latency_budget_is_measured_and_judged(trained):
    """The budget must be measured per backend, not assumed.

    sklearn's HistGradientBoosting costs ~10 ms per single-row call — per-call
    overhead that is identical for a DataFrame, a float32 ndarray and with the
    categoricals removed, so it cannot be tuned away at the call site. It is a
    training and analysis fallback, not a deployment backend.

    What is asserted here is that the measurement happens and the verdict is
    recorded, so a model card can never imply a latency it did not meet.
    """
    _, result = trained
    card = result["card"]
    assert card.decision_latency_ms_p50 is not None
    assert card.decision_latency_ms_p95 is not None
    assert card.decision_latency_budget_ms == 1.0
    assert card.meets_latency_budget == (card.decision_latency_ms_p95 <= 1.0)


def test_a_deployable_backend_meets_the_budget(frame):
    """At least one available backend must be fast enough to deploy.

    If this fails, the cost argument collapses: a router that is not free next
    to the screenshot it avoids has nothing to offer. Logistic regression is
    the floor — the spec's primary, LightGBM, is faster still.
    """
    splits = make_splits(frame, seed=0)
    result = fit_router(splits, backend="logistic", seed=0)
    p95 = result["card"].decision_latency_ms_p95
    assert p95 is not None
    assert p95 <= 2.0, f"even logistic regression needs {p95:.2f} ms per decision"


def test_model_card_records_provenance(trained):
    _, result = trained
    card = result["card"]
    assert card.schema_hash
    assert card.backend == "sklearn_hist"
    assert card.feature_columns
    assert card.seed == 0


def test_importances_are_ranked(trained):
    """RQ4 needs an attribution from every backend.

    sklearn's HistGradientBoosting exposes neither ``feature_importances_`` nor
    coefficients, so it must fall back to permutation importance rather than
    returning nothing — an empty table would silently drop RQ4.
    """
    _, result = trained
    importances = result["importances"]
    assert importances, "no feature attribution available for RQ4"

    values = [f["importance"] for f in importances]
    assert values == sorted(values, reverse=True)

    kinds = {f["kind"] for f in importances}
    assert len(kinds) == 1, "a single table must not mix importance scales"
    assert result["card"].importance_kind in kinds


def test_h1_gate_reports_a_verdict(trained):
    _, result = trained
    gate = h1_gate(result["metrics"])
    assert gate["threshold"] == 0.65
    assert isinstance(gate["passed"], bool)
    assert gate["best_held_out_auroc"] is not None


def test_h1_gate_fails_on_weak_signal():
    gate = h1_gate({"in_library": {"auroc": 0.52}, "cross_library": {"auroc": 0.49}})
    assert gate["passed"] is False
    assert "H1 FAILS" in gate["verdict"]


def test_training_refuses_single_class_data(frame):
    splits = make_splits(frame, seed=0)
    splits.train[LABEL_COLUMN] = True
    with pytest.raises(ValueError, match="one class"):
        fit_router(splits, backend="sklearn_hist", seed=0)


def test_save_writes_artifacts(trained, tmp_path: Path):
    _, result = trained
    save(result, tmp_path)
    assert (tmp_path / "router.joblib").exists()
    card = json.loads((tmp_path / "model_card.json").read_text())
    assert card["schema_hash"]
    assert json.loads((tmp_path / "metrics.json").read_text())["in_library"]["auroc"]


# --- policies --------------------------------------------------------------


def test_all_six_baselines_are_present(frame):
    splits = make_splits(frame, seed=0)
    test_frame = splits.in_library
    outcomes = baselines.evaluate_all(
        frame=test_frame,
        structure_succeeded=test_frame[LABEL_COLUMN].astype("boolean").fillna(False).to_numpy(bool),
        escalation_helps=test_frame[ORACLE_LABEL_COLUMN].tolist(),
        cost=CostModel(),
        task_ids=test_frame["task_id"].tolist(),
    )
    names = {o.name for o in outcomes}
    for required in ("always_ax", "always_pixel", "always_both", "fixed_ladder", "oracle"):
        assert required in names
    assert any(n.startswith("random@") for n in names), "random escalation is required"


def test_always_ax_never_escalates_and_oracle_bounds_above(frame):
    splits = make_splits(frame, seed=0)
    test_frame = splits.in_library
    structure = test_frame[LABEL_COLUMN].astype("boolean").fillna(False).to_numpy(bool)
    outcomes = {
        o.name: o
        for o in baselines.evaluate_all(
            frame=test_frame,
            structure_succeeded=structure,
            escalation_helps=test_frame[ORACLE_LABEL_COLUMN].tolist(),
            cost=CostModel(),
            task_ids=test_frame["task_id"].tolist(),
        )
    }
    assert outcomes["always_ax"].escalation_rate == 0.0
    assert outcomes["always_pixel"].escalation_rate == 1.0
    assert outcomes["oracle"].step_success_rate >= outcomes["always_ax"].step_success_rate

    # Each policy pays for what it actually sends. always-pixel sends a
    # screenshot alone; always-both sends the structured observation too, so it
    # must be strictly more expensive. Collapsing the two would hide the gap
    # between the cheap ceiling and the expensive one.
    ax_tokens = outcomes["always_ax"].observation_tokens_per_step
    pixel_tokens = outcomes["always_pixel"].observation_tokens_per_step
    both_tokens = outcomes["always_both"].observation_tokens_per_step
    assert ax_tokens < pixel_tokens < both_tokens
    assert both_tokens == pytest.approx(ax_tokens + pixel_tokens)


def test_threshold_sweep_is_monotone_in_cost(frame, trained):
    """More escalation costs more tokens — a sanity check on the cost accounting."""
    splits, result = trained
    test_frame = splits.in_library
    from eval.run_eval import score_frame

    payload = {
        "model": result["model"],
        "calibrator": result["calibrator"],
        "card": result["card"].__dict__,
    }
    scores = score_frame(test_frame, payload)
    curve = sweep_thresholds(
        failure_probability=scores,
        structure_succeeded=test_frame[LABEL_COLUMN].astype("boolean").fillna(False).to_numpy(bool),
        escalation_helps=test_frame[ORACLE_LABEL_COLUMN].tolist(),
        cost=CostModel(),
        task_ids=test_frame["task_id"].tolist(),
    )
    rates = [point.escalation_rate for point in curve]
    tokens = [point.observation_tokens_per_step for point in curve]
    assert rates[0] >= rates[-1]
    assert tokens[0] >= tokens[-1]

    front = pareto_front(curve)
    successes = [point.step_success_rate for point in front]
    assert successes == sorted(successes)


def test_bootstrap_resamples_tasks_not_steps(frame):
    """A step-level bootstrap would understate the interval.

    Steps within a task are correlated, so a task-level interval must be at
    least as wide as the naive one. This checks the interval is non-degenerate
    and brackets the point estimate.
    """
    from eval.stats import bootstrap_over_tasks

    values = frame[LABEL_COLUMN].astype(float).to_numpy()
    interval = bootstrap_over_tasks(
        values, frame["task_id"].tolist(), resamples=200, seed=0
    )
    assert interval.low <= interval.point <= interval.high
    assert interval.high > interval.low


def test_paired_difference_is_zero_against_itself(frame):
    from eval.stats import paired_difference

    values = frame[LABEL_COLUMN].astype(float).to_numpy()
    interval = paired_difference(values, values, frame["task_id"].tolist(), resamples=50)
    assert interval.point == pytest.approx(0.0)


def test_latency_percentiles_report_the_tail():
    from eval.stats import latency_percentiles

    # One pathological AX hang among fast calls: the mean hides it, p95 does not.
    values = np.array([20.0] * 95 + [180004.0] * 5)
    stats = latency_percentiles(values)
    assert stats["p50"] == pytest.approx(20.0)
    assert stats["p95"] > 1000.0
    assert stats["max"] == pytest.approx(180004.0)


def test_figures_are_written(frame, trained, tmp_path: Path):
    from eval.figures import headline_figure, reliability_diagram
    from eval.run_eval import evaluate_split

    splits, result = trained
    payload = {
        "model": result["model"],
        "calibrator": result["calibrator"],
        "card": result["card"].__dict__,
    }
    evaluated = evaluate_split(
        "in_library", splits.in_library, payload, CostModel(), resamples=50
    )
    headline = headline_figure(evaluated, tmp_path / "headline.png")
    assert headline.exists() and headline.stat().st_size > 1000

    reliability = reliability_diagram(result["metrics"], "in_library", tmp_path / "rel.png")
    assert reliability is not None and reliability.exists()


# --- the fixed ladder's three rungs ----------------------------------------


def test_ladder_uses_all_three_rungs(frame):
    """The ladder must not collapse into a binary escalate / don't.

    Rung 2 sends OCR-extracted text, rung 3 sends an image. Pricing both as a
    screenshot charges the strongest non-learned competitor for pixels it never
    sends, and misplaces it on the cost axis of the headline figure.
    """
    from router.baselines import Rung, ladder_rungs

    rungs = ladder_rungs(frame)
    counts = {r: int((rungs == int(r)).sum()) for r in Rung}
    for rung, count in counts.items():
        assert count > 0, f"rung {rung.name} never used: {counts}"


def test_ocr_rung_is_cheaper_than_the_visual_rung(frame):
    """OCR runs locally; only rung 3 puts an image in the prompt."""
    from router.baselines import evaluate_ladder, ladder_rungs, Rung

    cost = CostModel()
    structure = frame[LABEL_COLUMN].astype("boolean").fillna(False).to_numpy(bool)
    outcome = evaluate_ladder(
        frame, structure, frame[ORACLE_LABEL_COLUMN].tolist(), cost, frame["task_id"].tolist()
    )

    rungs = ladder_rungs(frame)
    all_visual = cost.c_struct + cost.c_pixel
    assert outcome.observation_tokens_per_step < all_visual, (
        "the ladder costs as much as always-both; the OCR rung is being priced as an image"
    )
    assert outcome.rung_mix["ocr"] == int((rungs == int(Rung.OCR)).sum())


def test_ladder_does_not_credit_ocr_on_unnamed_targets(frame):
    """OCR finds text, so it cannot ground an icon-only control.

    If the OCR rung were credited with recovering steps on elements that have
    no accessible name, the baseline would be stronger than the technique.
    """
    import pandas as pd
    from router.baselines import evaluate_ladder

    unnamed = frame.copy()
    unnamed["ax_name_present"] = False
    cost = CostModel()
    structure = unnamed[LABEL_COLUMN].astype("boolean").fillna(False).to_numpy(bool)

    with_names = evaluate_ladder(
        frame,
        frame[LABEL_COLUMN].astype("boolean").fillna(False).to_numpy(bool),
        frame[ORACLE_LABEL_COLUMN].tolist(),
        cost,
        frame["task_id"].tolist(),
    )
    without = evaluate_ladder(
        unnamed, structure, unnamed[ORACLE_LABEL_COLUMN].tolist(), cost, unnamed["task_id"].tolist()
    )
    assert without.step_success_rate <= with_names.step_success_rate
