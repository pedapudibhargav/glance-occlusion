"""A hard spend cap on the collection run.

Not advisory. When the cap is reached the next API call raises and the run
stops. This exists because ComponentBench runs each task in a child process
and retries on rate limits: a loop in one task could otherwise spend a budget
several times over before anyone noticed.

Three things make it reliable:

  - **Measured, not estimated.** Every response carries
    ``usage.prompt_tokens`` and ``usage.completion_tokens``; those are what is
    counted, so the running total is what the provider will actually bill.
  - **Shared across processes.** The ledger is a file guarded by ``flock``, so
    the parent and every task child see one total. A per-process counter would
    let N children each spend the full budget.
  - **Charged before the call returns.** Usage is recorded as soon as the
    response arrives, so a crash later in the step cannot lose the charge.

The same ledger is the project's own cost measurement. The spec insists every
token number in the paper come from this harness rather than from a published
figure, and this is that instrument.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

DEFAULT_LEDGER = Path("data/spend_ledger.json")


class BudgetExceeded(RuntimeError):
    """The run has spent its cap and must stop."""


@dataclass(frozen=True)
class ModelPrice:
    """USD per million tokens, as published by the provider."""

    input_per_mtok: float
    output_per_mtok: float
    note: str = ""

    def cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        return (
            prompt_tokens * self.input_per_mtok / 1_000_000.0
            + completion_tokens * self.output_per_mtok / 1_000_000.0
        )


# Published list prices. Checked against each provider's own pricing page on
# 2026-10-21; they change, so the ledger records which entry it used and a
# stale entry shows up as a mismatch against the provider's invoice rather
# than silently under-counting.
PRICES: dict[str, ModelPrice] = {
    "gpt-5-mini": ModelPrice(0.25, 2.00),
    # Standard-tier list prices, read from developers.openai.com/api/docs/pricing
    # on 2026-10-23. Added for the second-model run: gpt-5-nano turned out to
    # be a floor effect (0/26 on the flat arm, failing even the control sites),
    # so model-agnosticism needs a model that can do the task, not a weaker one.
    "gpt-5.4-mini": ModelPrice(0.75, 4.50),
    "gpt-5.4-nano": ModelPrice(0.20, 1.25),
    "gpt-5.4": ModelPrice(2.50, 15.00),
    "gpt-5-nano": ModelPrice(0.05, 0.40),
    "gpt-6-luna": ModelPrice(0.10, 0.50),
    "gpt-4.1-nano": ModelPrice(0.10, 0.40),
    # Haiku 5.5 is priced by prompt length: above 100,000 input tokens the
    # rate jumps to $0.50/$2.50. Collection prompts run ~1.4k, so the cheap
    # tier applies; charge_usage checks and switches if that ever changes.
    "claude-haiku-5-5": ModelPrice(0.10, 0.50, note="prompts <=100k tokens"),
    "claude-haiku-5-5-long": ModelPrice(0.50, 2.50, note="prompts >100k tokens"),
    "claude-sonnet-5-5": ModelPrice(2.00, 10.00),
    "claude-opus-5-5": ModelPrice(4.00, 20.00),
}

LONG_PROMPT_THRESHOLD = 100_000


def price_for(model: str, prompt_tokens: int = 0) -> Optional[ModelPrice]:
    key = (model or "").strip()
    if key.startswith("claude-haiku-5-5") and prompt_tokens > LONG_PROMPT_THRESHOLD:
        return PRICES["claude-haiku-5-5-long"]
    if key in PRICES:
        return PRICES[key]
    # Match a dated or suffixed id, e.g. claude-haiku-5-5-20260101.
    for known, price in PRICES.items():
        if key.startswith(known):
            return price
    return None


@dataclass
class Ledger:
    """The running total, read and written under a file lock."""

    path: Path = DEFAULT_LEDGER
    limit_usd: float = 2.0
    model: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def _read(self, handle) -> dict[str, Any]:
        handle.seek(0)
        raw = handle.read()
        if not raw.strip():
            return {
                "spent_usd": 0.0,
                "calls": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "limit_usd": self.limit_usd,
                "model": self.model,
                "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "unpriced_models": [],
            }
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("spend ledger was unreadable; starting a fresh one")
            return {"spent_usd": 0.0, "calls": 0, "prompt_tokens": 0, "completion_tokens": 0}

    def charge(self, model: str, prompt_tokens: int, completion_tokens: int) -> dict[str, Any]:
        """Record one call's usage and return the updated totals.

        Raises :class:`BudgetExceeded` when the cap has been reached. The
        charge is recorded first: the money is already spent, and hiding it
        would make the ledger disagree with the invoice.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        price = price_for(model, prompt_tokens)

        with self.path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                state = self._read(handle)
                if price is None:
                    # An unpriced model must not be treated as free. The run
                    # continues but the model is recorded, and the reconciled
                    # total is flagged as incomplete.
                    unpriced = set(state.get("unpriced_models", []))
                    unpriced.add(model)
                    state["unpriced_models"] = sorted(unpriced)
                else:
                    state["spent_usd"] = round(
                        state.get("spent_usd", 0.0)
                        + price.cost(prompt_tokens, completion_tokens),
                        6,
                    )
                state["calls"] = state.get("calls", 0) + 1
                state["prompt_tokens"] = state.get("prompt_tokens", 0) + prompt_tokens
                state["completion_tokens"] = state.get("completion_tokens", 0) + completion_tokens
                state["limit_usd"] = self.limit_usd
                state["model"] = model
                state["updated_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                if state["calls"]:
                    state["usd_per_call"] = round(state["spent_usd"] / state["calls"], 8)

                handle.seek(0)
                handle.truncate()
                handle.write(json.dumps(state, indent=2))
                handle.flush()
                os.fsync(handle.fileno())
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

        if state["spent_usd"] >= self.limit_usd:
            raise BudgetExceeded(
                f"spend cap reached: ${state['spent_usd']:.4f} of ${self.limit_usd:.2f} "
                f"after {state['calls']} calls "
                f"({state['prompt_tokens']:,} prompt + {state['completion_tokens']:,} "
                f"completion tokens). The run stops here."
            )
        return state

    def read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"spent_usd": 0.0, "calls": 0, "limit_usd": self.limit_usd}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {"spent_usd": 0.0, "calls": 0, "limit_usd": self.limit_usd}

    def remaining(self) -> float:
        return max(0.0, self.limit_usd - self.read().get("spent_usd", 0.0))


_installed = False


def install(limit_usd: Optional[float] = None, ledger_path: Optional[Path] = None) -> bool:
    """Wrap the OpenAI SDK's chat-completions call with the cap.

    Wrapping the SDK rather than ComponentBench's agent means every call site
    is covered — the agent, any retry wrapper, and anything else that happens
    to use the client.
    """
    global _installed
    if _installed:
        return True

    try:
        from openai.resources.chat import completions as chat_completions
    except ImportError:
        logger.warning("openai SDK not importable; spend cap NOT installed")
        return False

    limit = (
        limit_usd
        if limit_usd is not None
        else float(os.environ.get("GLANCE_BUDGET_USD", "2.0"))
    )
    path = ledger_path or Path(os.environ.get("GLANCE_LEDGER", str(DEFAULT_LEDGER)))
    ledger = Ledger(path=path, limit_usd=limit)

    # Refuse to start a run that is already over budget, rather than making
    # one more call and then stopping.
    already = ledger.read().get("spent_usd", 0.0)
    if already >= limit:
        raise BudgetExceeded(
            f"ledger at {path} already records ${already:.4f} of a ${limit:.2f} cap. "
            f"Raise GLANCE_BUDGET_USD or start a new ledger."
        )

    original_create = chat_completions.Completions.create

    def capped_create(self, *args: Any, **kwargs: Any) -> Any:
        response = original_create(self, *args, **kwargs)
        usage = getattr(response, "usage", None)
        if usage is not None:
            state = ledger.charge(
                model=str(kwargs.get("model") or getattr(response, "model", "")),
                prompt_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
                completion_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
            )
            if state["calls"] % 25 == 0:
                logger.info(
                    "spend $%.4f / $%.2f after %d calls",
                    state["spent_usd"],
                    limit,
                    state["calls"],
                )
        return response

    chat_completions.Completions.create = capped_create  # type: ignore[method-assign]
    _installed = True
    logger.info("spend cap installed: $%.2f, ledger %s", limit, path)
    return True
