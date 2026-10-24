"""Join the two collection passes and write ``data/steps.parquet``.

Inputs
    data/raw/structure/*.jsonl   one row per attempted action, with features
                                 and the primary label
    data/raw/pixel/*.jsonl       the paired pixel pass
    data/raw/counterfactual/*.jsonl  (optional) exact per-step oracle pairs

Output
    data/steps.parquet           the training table

Three things are enforced here rather than trusted downstream: one feature
schema version across the whole table, one label version, and a task template
on every row. The template is what the splits are built on — ComponentBench
instantiates its tasks from 24 templates, so a random row split puts near
duplicates on both sides of the train/test boundary and inflates the numbers.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any, Iterable, Optional

import pandas as pd

from eval.jsonio import write as write_json
from collect.outcome import LABEL_VERSION
from probe.schema import (
    FEATURE_COLUMNS,
    SCHEMA_VERSION,
    schema_hash,
    LABEL_COLUMN,
    ORACLE_LABEL_COLUMN,
    assert_single_schema,
)

logger = logging.getLogger(__name__)

# ComponentBench task ids look like "button-antd-T01" or "data-grid-mui-T14":
# <canonical type>-<library>-<template>. The template is the last segment.
TASK_ID_RE = re.compile(r"^(?P<type>.+)-(?P<library>antd|mui|mantine|external)-(?P<template>[A-Za-z0-9]+)$")


def read_jsonl_dir(path: Path) -> list[dict[str, Any]]:
    """Read every ``*.jsonl`` under ``path`` (file or directory)."""
    if not path.exists():
        return []
    files = [path] if path.is_file() else sorted(path.rglob("*.jsonl"))
    rows: list[dict[str, Any]] = []
    for file in files:
        with file.open(encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    logger.warning("skipping malformed row %s:%d", file, line_no)
    return rows


def parse_task_id(task_id: str) -> dict[str, str]:
    match = TASK_ID_RE.match(task_id or "")
    if not match:
        return {"task_template": "unknown", "library_from_id": "", "type_from_id": ""}
    return {
        "task_template": match.group("template"),
        "library_from_id": match.group("library"),
        "type_from_id": match.group("type"),
    }


def add_template_column(frame: pd.DataFrame) -> pd.DataFrame:
    parsed = frame["task_id"].astype(str).map(parse_task_id).apply(pd.Series)
    for column in ("task_template", "library_from_id", "type_from_id"):
        if column == "task_template":
            # A template already present on the row wins; otherwise derive it.
            existing = frame.get("task_template")
            derived = parsed[column]
            if existing is None:
                frame[column] = derived
            else:
                frame[column] = existing.where(
                    existing.notna() & (existing.astype(str) != ""), derived
                )
        else:
            frame[column] = parsed[column]
    frame["library"] = frame["library"].where(
        frame["library"].notna() & (frame["library"].astype(str) != ""),
        frame["library_from_id"],
    )
    return frame


def backfill_derived_features(frame: pd.DataFrame) -> pd.DataFrame:
    """Derive features that were added after some rows were collected.

    ``target_resolvable`` is recoverable from ``probe_errors``, which every
    row already carries, so rows gathered under an earlier schema do not have
    to be thrown away. The schema hash is restamped to match, because mixing
    hashes in one training run is a correctness bug the trainer refuses.
    """
    frame = frame.copy()
    errors = (
        frame.get("probe_errors", pd.Series("", index=frame.index))
        .fillna("")
        .astype(str)
    )
    unresolved = errors.str.contains("element_not_resolved|resolve_returned_null", regex=True)

    if "target_resolvable" in frame.columns:
        existing = frame["target_resolvable"].astype("boolean")
        frame["target_resolvable"] = existing.fillna(~unresolved)
    else:
        frame["target_resolvable"] = (~unresolved).astype("boolean")

    frame["schema_version"] = SCHEMA_VERSION
    frame["schema_hash"] = schema_hash()
    return frame


def recompute_labels(frame: pd.DataFrame) -> pd.DataFrame:
    """Re-derive the label from the evidence stored on each row.

    Every component of the decision is written out at collection time —
    banner_appeared, state_changed, dialog_changed, landmark_mutated,
    error_reason — so the label is a pure function of recorded evidence and
    can be revised without re-collecting. That matters: the label definition
    is the load-bearing decision in the project, and the first real collection
    run found it wrong in a way that only live data could reveal.

    Rows collected under an older LABEL_VERSION are recomputed under the
    current rule and restamped, so one table never mixes definitions.
    """
    if LABEL_COLUMN not in frame.columns:
        return frame

    evidence = ["banner_appeared", "state_changed", "dialog_changed", "landmark_mutated"]
    if not any(column in frame.columns for column in evidence):
        # Nothing to recompute from; leave the collector's labels alone.
        return frame

    frame = frame.copy()

    def flag(name: str) -> pd.Series:
        if name not in frame.columns:
            return pd.Series(False, index=frame.index)
        return frame[name].astype("boolean").fillna(False)

    progressed = flag("banner_appeared") | flag("state_changed") | flag(
        "dialog_changed"
    ) | flag("landmark_mutated")

    # Only a hit-test mismatch vetoes — see collect/outcome.py for why.
    reason = (
        frame.get("error_reason", pd.Series("", index=frame.index))
        .fillna("")
        .astype(str)
    )
    vetoed = reason.eq("hit_test_mismatch") | reason.str.startswith("dispatch_error")

    previous = frame[LABEL_COLUMN].astype("boolean")
    recomputed = (progressed & ~vetoed).astype("boolean")
    # A row with no evidence recorded at all stays unlabelled rather than
    # becoming a negative.
    unknown = previous.isna() & ~progressed & ~vetoed
    recomputed[unknown] = pd.NA

    changed = int((previous.fillna(False) != recomputed.fillna(False)).sum())
    if changed:
        logger.info(
            "relabelled %d row(s) under label version %s", changed, LABEL_VERSION
        )
    frame[LABEL_COLUMN] = recomputed
    frame["label_version"] = LABEL_VERSION
    return frame


def add_oracle_label(
    structure: pd.DataFrame,
    pixel: pd.DataFrame,
    counterfactual: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """Attach ``escalation_would_help``.

    Two sources, in order of preference.

    Exact, from the counterfactual pass: the same step replayed twice from a
    saved page state, once structure-only and once with a screenshot. The pair
    says directly whether escalation would have helped. ComponentBench tasks
    are deterministic by construction, so replaying a fixed action prefix
    reproduces the state.

    Approximate, from the paired pixel pass: a step whose structure-only
    dispatch failed, in a task the pixel pass completed. This is weaker — the
    pixel run may have taken a different path to the goal, so the step indices
    are not strictly comparable — and it is marked as such in
    ``oracle_source`` so the paper never reports it as the exact oracle.
    """
    structure = structure.copy()
    structure[ORACLE_LABEL_COLUMN] = pd.NA
    structure["oracle_source"] = "none"

    if not pixel.empty:
        pixel_success = (
            pixel.assign(_ok=pixel.get("banner_appeared", False).fillna(False))
            .groupby("task_id")["_ok"]
            .max()
        )
        helped = structure["task_id"].map(pixel_success).fillna(False).astype(bool)
        approximate = (structure[LABEL_COLUMN] == False) & helped  # noqa: E712
        structure.loc[structure[LABEL_COLUMN].notna(), ORACLE_LABEL_COLUMN] = approximate[
            structure[LABEL_COLUMN].notna()
        ]
        structure.loc[structure[LABEL_COLUMN].notna(), "oracle_source"] = "paired_pixel_approx"

    if counterfactual is not None and not counterfactual.empty:
        key = ["task_id", "step_index"]
        exact = counterfactual.set_index(key)
        if ORACLE_LABEL_COLUMN in exact.columns:
            indexed = structure.set_index(key)
            overlap = indexed.index.intersection(exact.index)
            indexed.loc[overlap, ORACLE_LABEL_COLUMN] = exact.loc[overlap, ORACLE_LABEL_COLUMN]
            indexed.loc[overlap, "oracle_source"] = "counterfactual_exact"
            structure = indexed.reset_index()

    return structure


def report(frame: pd.DataFrame) -> dict[str, Any]:
    labelled = frame[frame[LABEL_COLUMN].notna()]
    positives = int((labelled[LABEL_COLUMN] == True).sum())  # noqa: E712
    stats = {
        "rows_total": int(len(frame)),
        "rows_labelled": int(len(labelled)),
        "rows_unlabelled": int(len(frame) - len(labelled)),
        "positive_rate": round(positives / len(labelled), 4) if len(labelled) else None,
        "positives": positives,
        "negatives": int(len(labelled) - positives),
        "tasks": int(frame["task_id"].nunique()),
        "templates": int(frame["task_template"].nunique()),
        "libraries": sorted(frame["library"].dropna().unique().tolist()),
        "ax_timed_out_rows": int(frame.get("ax_timed_out", pd.Series(dtype=bool)).fillna(False).sum()),
        "oracle_sources": frame["oracle_source"].value_counts().to_dict()
        if "oracle_source" in frame
        else {},
    }
    missing_features = [c for c in FEATURE_COLUMNS if c not in frame.columns]
    if missing_features:
        stats["missing_feature_columns"] = missing_features
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description="Join probe passes into data/steps.parquet")
    parser.add_argument("--structure", type=Path, default=Path("data/raw/structure"))
    parser.add_argument("--pixel", type=Path, default=Path("data/raw/pixel"))
    parser.add_argument("--counterfactual", type=Path, default=Path("data/raw/counterfactual"))
    parser.add_argument("--out", type=Path, default=Path("data/steps.parquet"))
    parser.add_argument(
        "--allow-unlabelled",
        action="store_true",
        help="Keep rows whose label could not be determined (default: drop them)",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    structure_rows = read_jsonl_dir(args.structure)
    if not structure_rows:
        print(f"no structure rows under {args.structure}", file=sys.stderr)
        return 2
    structure = pd.DataFrame(structure_rows)
    pixel = pd.DataFrame(read_jsonl_dir(args.pixel))
    counterfactual_rows = read_jsonl_dir(args.counterfactual)
    counterfactual = pd.DataFrame(counterfactual_rows) if counterfactual_rows else None

    # One feature schema, one label definition. Both are correctness gates,
    # not warnings: mixing either silently changes what the model is fitting.
    observed_schemas = sorted(
        set(structure.get("schema_hash", pd.Series(dtype=str)).dropna())
    )
    if len(observed_schemas) > 1:
        logger.info("data spans schema hashes %s; backfilling to the current one", observed_schemas)
    schema = schema_hash()
    label_versions = sorted(set(structure.get("label_version", pd.Series(dtype=str)).dropna()))
    if len(label_versions) > 1:
        logger.info(
            "data spans label versions %s; recomputing all rows under %s",
            label_versions,
            LABEL_VERSION,
        )

    structure = backfill_derived_features(structure)
    structure = recompute_labels(structure)
    structure = add_template_column(structure)
    joined = add_oracle_label(structure, pixel, counterfactual)

    if not args.allow_unlabelled:
        before = len(joined)
        joined = joined[joined[LABEL_COLUMN].notna()].copy()
        dropped = before - len(joined)
        if dropped:
            logger.info("dropped %d unlabelled rows", dropped)

    joined[LABEL_COLUMN] = joined[LABEL_COLUMN].astype("boolean")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    joined.to_parquet(args.out, index=False)

    stats = report(joined) | {"schema_hash": schema, "label_version": label_versions}
    print(json.dumps(stats, indent=2, default=str))

    summary_path = write_json(args.out.with_suffix(".summary.json"), stats)
    print(f"\nwrote {args.out} and {summary_path}")

    if stats["rows_labelled"] == 0:
        print("every row is unlabelled — check the label definition", file=sys.stderr)
        return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
