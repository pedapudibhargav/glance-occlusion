"""Tests for the spend cap.

The cap guards real money, so these check the properties that matter: that it
counts measured usage, that it stops at the limit, and that concurrent
processes cannot each spend the full budget.
"""

from __future__ import annotations

import json
import multiprocessing
from pathlib import Path

import pytest

from collect.budget import BudgetExceeded, Ledger, ModelPrice, price_for


def test_price_lookup_handles_dated_ids():
    assert price_for("gpt-5-mini").input_per_mtok == 0.25
    assert price_for("claude-haiku-5-5-20260101").input_per_mtok == 0.10
    assert price_for("some-unknown-model") is None


def test_haiku_switches_to_long_prompt_pricing():
    """Haiku 5.5 is priced by prompt length; over 100k the rate jumps 5x."""
    assert price_for("claude-haiku-5-5", prompt_tokens=1_000).input_per_mtok == 0.10
    assert price_for("claude-haiku-5-5", prompt_tokens=150_000).input_per_mtok == 0.50


def test_cost_is_computed_from_measured_usage():
    price = ModelPrice(0.25, 2.00)
    # 1M prompt + 1M completion tokens
    assert price.cost(1_000_000, 1_000_000) == pytest.approx(2.25)


def test_ledger_accumulates(tmp_path: Path):
    ledger = Ledger(path=tmp_path / "ledger.json", limit_usd=100.0)
    ledger.charge("gpt-5-mini", 1_000_000, 0)
    state = ledger.charge("gpt-5-mini", 1_000_000, 0)
    assert state["spent_usd"] == pytest.approx(0.50)
    assert state["calls"] == 2


def test_ledger_raises_at_the_cap(tmp_path: Path):
    ledger = Ledger(path=tmp_path / "ledger.json", limit_usd=0.30)
    ledger.charge("gpt-5-mini", 1_000_000, 0)  # $0.25, under
    with pytest.raises(BudgetExceeded, match="spend cap reached"):
        ledger.charge("gpt-5-mini", 1_000_000, 0)  # $0.50 total, over


def test_the_charge_is_recorded_even_when_it_trips_the_cap(tmp_path: Path):
    """Money already spent must appear in the ledger, not be hidden by the raise."""
    path = tmp_path / "ledger.json"
    ledger = Ledger(path=path, limit_usd=0.10)
    with pytest.raises(BudgetExceeded):
        ledger.charge("gpt-5-mini", 1_000_000, 0)
    assert json.loads(path.read_text())["spent_usd"] == pytest.approx(0.25)


def test_unpriced_model_is_flagged_not_treated_as_free(tmp_path: Path):
    path = tmp_path / "ledger.json"
    ledger = Ledger(path=path, limit_usd=1.0)
    ledger.charge("mystery-model-9", 1_000_000, 1_000_000)
    state = json.loads(path.read_text())
    assert state["spent_usd"] == 0.0
    assert "mystery-model-9" in state["unpriced_models"]


def _charge_once(args):
    path, limit = args
    ledger = Ledger(path=Path(path), limit_usd=limit)
    try:
        ledger.charge("gpt-5-mini", 400_000, 0)  # $0.10 each
        return "ok"
    except BudgetExceeded:
        return "stopped"


def test_concurrent_processes_share_one_budget(tmp_path: Path):
    """ComponentBench runs a child process per task.

    A per-process counter would let every child spend the whole budget. With
    eight children charging $0.10 against a $0.50 cap, the total must land at
    $0.80 worth of attempts but the ledger must stop handing out approvals
    once it crosses the line.
    """
    path = tmp_path / "ledger.json"
    with multiprocessing.Pool(4) as pool:
        results = pool.map(_charge_once, [(str(path), 0.50)] * 8)

    state = json.loads(path.read_text())
    assert state["calls"] == 8
    assert state["spent_usd"] == pytest.approx(0.80)
    assert results.count("stopped") >= 3, (
        "every charge past the cap must raise; per-process counters would let "
        "each child spend the full budget"
    )
