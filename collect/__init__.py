"""Data collection: probe the dispatch path, label the outcome, join the passes."""

from .outcome import LABEL_VERSION, Outcome, evaluate_outcome, is_element_action, parse_action

__all__ = [
    "LABEL_VERSION",
    "Outcome",
    "evaluate_outcome",
    "is_element_action",
    "parse_action",
]
