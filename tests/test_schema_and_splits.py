"""Tests for the feature contract, the splits, and the cost rule.

These guard the three mistakes that would silently invalidate results: a
feature schema that drifts between collection runs, a task template that leaks
across the train/test boundary, and a forbidden column reaching the model.
"""

from __future__ import annotations

import pytest

pytest.importorskip("pandas", reason="pandas is not installed")
pytest.importorskip("numpy", reason="numpy is not installed")

import numpy as np
import pandas as pd

from probe.schema import (
    CATEGORICAL_COLUMNS,
    FEATURE_COLUMNS,
    FEATURE_GROUPS,
    FORBIDDEN_FEATURE_COLUMNS,
    LABEL_COLUMN,
    StepFeatures,
    assert_single_schema,
    bucket_ax_role,
    feature_columns,
    is_generic_name,
    schema_hash,
)
from router.features import ForbiddenFeatureError, build_matrix, make_splits
from router.policy import CostModel, decide, oracle_gap_recovered, evaluate_policy

from scripts.make_synthetic import generate


# --- schema ----------------------------------------------------------------


def test_schema_hash_is_stable():
    assert schema_hash() == schema_hash()
    assert len(schema_hash()) == 16


def test_feature_groups_cover_every_feature_exactly_once():
    flat = [name for group in FEATURE_GROUPS.values() for name in group]
    assert len(flat) == len(set(flat)), "a feature appears in two groups"
    assert set(flat) == set(FEATURE_COLUMNS)


def test_every_feature_exists_on_the_dataclass():
    fields = set(StepFeatures.__dataclass_fields__)
    missing = [c for c in FEATURE_COLUMNS if c not in fields]
    assert not missing, f"declared features with no field: {missing}"


def test_forbidden_columns_are_not_features():
    assert not set(FORBIDDEN_FEATURE_COLUMNS) & set(FEATURE_COLUMNS)


def test_dropping_a_group_removes_exactly_its_columns():
    full = set(feature_columns())
    without = set(feature_columns(("occlusion",)))
    assert full - without == set(FEATURE_GROUPS["occlusion"])


def test_unknown_group_raises():
    with pytest.raises(KeyError):
        feature_columns(("not_a_group",))


def test_assert_single_schema_rejects_mixed_versions():
    assert assert_single_schema(["abc123", "abc123"]) == "abc123"
    with pytest.raises(ValueError, match="mixes feature schema versions"):
        assert_single_schema(["abc123", "def456"])
    with pytest.raises(ValueError, match="no schema_hash"):
        assert_single_schema([None, ""])


def test_from_probe_ignores_unknown_keys():
    """A probe script ahead of the schema must not break collection."""
    features = StepFeatures.from_probe(
        task_id="button-antd-T01",
        step_index=3,
        in_page={"hit_is_self": True, "some_future_feature": 1},
        cdp={"ax_role": "button"},
    )
    assert features.hit_is_self is True
    assert features.ax_role == "button"
    assert features.schema_hash == schema_hash()


def test_role_bucketing_and_generic_names():
    assert bucket_ax_role("button") == "button"
    assert bucket_ax_role("StaticText") == "other"
    assert bucket_ax_role(None) == "none"
    assert is_generic_name("Click here") is True
    assert is_generic_name("Submit.") is True
    assert is_generic_name("Save invoice 1042") is False
    assert is_generic_name(None) is False


# --- matrix ----------------------------------------------------------------


@pytest.fixture(scope="module")
def synthetic() -> pd.DataFrame:
    return generate(n_tasks=160, seed=7)


def test_matrix_has_every_feature_column(synthetic):
    matrix = build_matrix(synthetic)
    assert list(matrix.X.columns) == list(FEATURE_COLUMNS)
    assert len(matrix.y) == len(synthetic)


def test_matrix_rejects_forbidden_feature(monkeypatch, synthetic):
    """If a forbidden name is ever added to a group, the matrix must refuse."""
    import probe.schema as schema_module
    import router.features as features_module

    monkeypatch.setitem(schema_module.FEATURE_GROUPS, "leak", ("element_text",))
    frame = synthetic.assign(element_text="Save")
    with pytest.raises(ForbiddenFeatureError):
        features_module.build_matrix(frame)


def test_categoricals_are_categorical(synthetic):
    matrix = build_matrix(synthetic)
    for column in CATEGORICAL_COLUMNS:
        assert str(matrix.X[column].dtype) == "category"


def test_one_hot_removes_categoricals(synthetic):
    matrix = build_matrix(synthetic, one_hot=True)
    assert matrix.categorical == []
    assert all(np.issubdtype(dtype, np.number) for dtype in matrix.X.dtypes)


def test_nulls_are_preserved_not_imputed():
    """A null ``rect_iou`` means the AX side could not be read.

    That is information, so it must reach the model as NaN rather than being
    quietly replaced with a plausible number.
    """
    frame = pd.DataFrame(
        {
            "task_id": ["t-antd-T01"],
            "task_template": ["T01"],
            "library": ["antd"],
            "rect_iou": [None],
            LABEL_COLUMN: [True],
        }
    )
    matrix = build_matrix(frame)
    assert np.isnan(matrix.X["rect_iou"].iloc[0])


# --- splits ----------------------------------------------------------------


def test_splits_do_not_share_templates(synthetic):
    """The headline correctness guard: no template on both sides."""
    splits = make_splits(synthetic, seed=0)
    train = set(splits.train["task_template"])
    calibration = set(splits.calibration["task_template"])
    in_library = set(splits.in_library["task_template"])

    assert not train & in_library
    assert not train & calibration
    assert not calibration & in_library


def test_cross_library_split_excludes_the_training_library(synthetic):
    splits = make_splits(synthetic, seed=0)
    assert set(splits.train["library"]) <= {"antd"}
    assert "antd" not in set(splits.cross_library["library"])


def test_core_split_is_v2_only(synthetic):
    splits = make_splits(synthetic, seed=0)
    if len(splits.core):
        assert set(splits.core["benchmark_version"]) == {"v2"}


def test_splits_are_deterministic(synthetic):
    first = make_splits(synthetic, seed=3)
    second = make_splits(synthetic, seed=3)
    assert set(first.in_library["task_template"]) == set(second.in_library["task_template"])


def test_splits_require_template_column(synthetic):
    with pytest.raises(KeyError, match="task_template"):
        make_splits(synthetic.drop(columns=["task_template"]))


# --- cost rule -------------------------------------------------------------


def test_threshold_falls_out_of_the_cost_model():
    cost = CostModel(c_struct=400.0, c_pixel=1400.0, l_fail=2000.0)
    assert cost.escalation_price == 1000.0
    assert cost.decision_threshold == pytest.approx(0.5)


def test_threshold_is_capped_at_one():
    """When looking costs more than failing, never escalate."""
    cost = CostModel(c_struct=400.0, c_pixel=5000.0, l_fail=100.0)
    assert cost.decision_threshold == 1.0


def test_decide_follows_the_rule():
    cost = CostModel(c_struct=400.0, c_pixel=1400.0, l_fail=2000.0)
    escalate = decide(np.array([0.1, 0.49, 0.51, 0.9]), cost)
    assert escalate.tolist() == [False, False, True, True]


def test_literature_costs_are_marked_unmeasured():
    assert CostModel().is_measured() is False
    assert CostModel(source="measured").is_measured() is True


def test_oracle_gap_is_none_when_oracle_offers_nothing():
    """Guard against manufacturing a headline by dividing by near-zero."""
    always_ax = evaluate_policy(
        "always_ax", np.zeros(4, bool), np.ones(4, bool), None, CostModel()
    )
    oracle = evaluate_policy(
        "oracle", np.zeros(4, bool), np.ones(4, bool), None, CostModel()
    )
    assert oracle_gap_recovered(always_ax, always_ax, oracle) is None


def test_escalation_is_not_credited_where_the_oracle_is_unknown():
    """A policy may not be rewarded for escalating into the unknown."""
    structure = np.array([False, False])
    outcome = evaluate_policy(
        name="always_pixel",
        escalate=np.ones(2, bool),
        structure_succeeded=structure,
        escalation_helps=[None, None],
        cost=CostModel(),
    )
    assert outcome.step_success_rate == 0.0
