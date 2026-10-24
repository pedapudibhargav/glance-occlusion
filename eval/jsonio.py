"""JSON writing that other languages can actually read.

Python's ``json`` emits bare ``NaN``, ``Infinity`` and ``-Infinity`` tokens.
They round-trip in Python and are rejected by every strict parser, including
JavaScript's ``JSON.parse`` — so a metrics file with one empty calibration bin
silently became unreadable to the dashboard, which then rendered "—" for every
number in it while looking perfectly healthy.

Non-finite values are real here: an empty reliability bin has no mean, and an
undefined AUROC is not zero. They are written as ``null``, which is the
JSON-native way to say "no value".
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any


def sanitise(value: Any) -> Any:
    """Recursively replace non-finite floats with None."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {key: sanitise(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitise(item) for item in value]
    return value


def dumps(payload: Any, indent: int = 2) -> str:
    """Strict JSON — raises rather than emitting a token no other parser takes."""
    return json.dumps(sanitise(payload), indent=indent, default=str, allow_nan=False)


def write(path: Path, payload: Any, indent: int = 2) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps(payload, indent=indent), encoding="utf-8")
    return path
