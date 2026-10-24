"""Feature schema for GLANCE — the single source of truth.

Every feature here is computable without model inference and without a
screenshot, from data the agent already fetches. That is the design
constraint: a signal that needs a screenshot or an LLM call does not belong in
the router, because the router's whole purpose is to decide whether to pay
that cost.

Raw values are stored. Booleans are derived at training time
(``router/features.py``) so thresholds can be revisited without re-collecting.

The schema carries a version hash. Every parquet written by this project
records it, and training refuses to mix versions — see ``schema_hash()`` and
``assert_single_schema()``. Mixing schema versions in one training run is a
silent correctness bug.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, fields, asdict
from typing import Any, Literal, Optional

SCHEMA_VERSION = "1.1.0"

# ---------------------------------------------------------------------------
# Feature groups — the unit of the RQ4 / H5 ablation. Dropping a group means
# dropping exactly these columns, so the ablation is defined by this dict and
# not by a hand-maintained list somewhere else.
# ---------------------------------------------------------------------------

FEATURE_GROUPS: dict[str, tuple[str, ...]] = {
    "occlusion": (
        "hit_is_self",
        "hit_is_descendant_depth",
        "occluder_tag",
        "occluder_is_fixed",
        "occluder_z_index",
        "corner_hit_agreement",
        "paint_order_rank",
    ),
    "geometry": (
        "rect_iou",
        "rect_area_px",
        "rect_in_viewport",
        "rect_min_dim",
        "scroll_offset_delta",
    ),
    "semantic": (
        "ax_role",
        "ax_name_present",
        "ax_name_len",
        "ax_name_is_generic",
        "ax_name_matches_text",
        "ax_state_disabled",
        "ax_state_focusable",
        "ax_state_focused",
        "ax_state_expanded",
        "ax_state_selected",
        "ax_state_checked",
        "ax_state_hidden",
        "ax_state_inert",
        "aria_hidden_ancestor",
        "is_div_with_pointer",
        "has_click_handler_hint",
        "in_shadow_root",
        "in_iframe",
        "sibling_name_collisions",
        # Could the element the observation named be found in the live DOM at
        # all? The cheapest possible trust signal: no screenshot, no model,
        # one querySelector.
        #
        # Measured in the first collection run, this is the DOMINANT failure
        # mode — 24 of 26 structure-only failures in the first 189 rows were
        # an agent acting on a ref that had gone. It is reported separately in
        # the analysis because it risks being near-tautological: an element
        # that cannot be found usually cannot be acted on, so a model given
        # this feature can score well without having learned anything about
        # when a *present* element is untrustworthy. Both analyses are run.
        "target_resolvable",
    ),
    "freshness": (
        "ms_since_last_mutation",
        "ms_since_last_global_mutation",
        "mutation_count_1s",
        "ms_since_navigation",
        "pending_network_requests",
        "has_running_animation",
        "page_settled",
        "snapshot_age_ms",
        "ax_timed_out",
    ),
    "context": (
        "action_type",
        "step_index",
        "prior_failures_this_episode",
        "viewport_w",
        "viewport_h",
        "dpr",
        "modal_open",
        "target_inside_modal",
    ),
}

FEATURE_COLUMNS: tuple[str, ...] = tuple(
    name for group in FEATURE_GROUPS.values() for name in group
)

CATEGORICAL_COLUMNS: tuple[str, ...] = ("ax_role", "occluder_tag", "action_type")

# AX roles are bucketed to keep the categorical cardinality low and stable
# across libraries; anything unlisted becomes "other".
AX_ROLE_BUCKETS: tuple[str, ...] = (
    "button",
    "link",
    "textbox",
    "checkbox",
    "combobox",
    "gridcell",
    "generic",
    "none",
    "other",
)

ACTION_TYPES: tuple[str, ...] = ("click", "type", "select", "drag", "scroll", "other")

# Names that are present but carry no information. ``ax_name_is_generic``
# matches against this stoplist.
GENERIC_NAME_STOPLIST: tuple[str, ...] = (
    "button",
    "link",
    "click here",
    "click",
    "submit",
    "icon",
    "image",
    "img",
    "ok",
    "go",
    "more",
    "menu",
    "open",
    "close",
    "toggle",
    "select",
    "item",
    "option",
    "untitled",
)

# ---------------------------------------------------------------------------
# Columns that must never become model inputs.
#
# The claim is specifically that *structural disagreement signals alone* carry
# the information. If the router can read the task or the element's text it
# becomes a small grounding model and the contribution blurs into existing
# work. These columns are collected for auditing and error analysis only, and
# ``router/features.py`` asserts they never reach the matrix.
# ---------------------------------------------------------------------------

FORBIDDEN_FEATURE_COLUMNS: tuple[str, ...] = (
    "task_goal",
    "element_text",
    "ax_name",
    "action_raw",
    "screenshot_path",
    "screenshot_b64",
    "task_instruction",
    "goal_embedding",
    "text_embedding",
)

# Identity / bookkeeping columns. Carried through the pipeline, never trained on.
METADATA_COLUMNS: tuple[str, ...] = (
    "task_id",
    "step_index",
    "run_id",
    "mode",
    "library",
    "canonical_type",
    "task_template",
    "benchmark_version",
    "schema_version",
    "schema_hash",
    "probe_wall_ms",
    "ts_dispatch",
)

LABEL_COLUMN = "structure_only_succeeded"
ORACLE_LABEL_COLUMN = "escalation_would_help"


@dataclass(slots=True)
class StepFeatures:
    """One probe result: the feature vector for one attempted action.

    Captured at the moment just before the action is dispatched. Optional
    fields are ``None`` when the source could not be read (a timed-out AX call,
    an element in a cross-origin frame); ``None`` is meaningful and is kept
    rather than imputed at collection time.
    """

    # --- identity -----------------------------------------------------------
    task_id: str
    step_index: int
    run_id: str = ""
    mode: str = ""
    library: str = ""
    canonical_type: str = ""
    task_template: str = ""
    benchmark_version: str = "v1"

    # --- occlusion and hit-testability -------------------------------------
    hit_is_self: Optional[bool] = None
    hit_is_descendant_depth: Optional[int] = None
    occluder_tag: Optional[str] = None
    occluder_is_fixed: Optional[bool] = None
    occluder_z_index: Optional[int] = None
    corner_hit_agreement: Optional[float] = None
    paint_order_rank: Optional[int] = None

    # --- geometry agreement -------------------------------------------------
    rect_iou: Optional[float] = None
    rect_area_px: Optional[float] = None
    rect_in_viewport: Optional[float] = None
    rect_min_dim: Optional[float] = None
    scroll_offset_delta: Optional[float] = None

    # --- semantic quality of the structured node ---------------------------
    ax_role: Optional[str] = None
    ax_name_present: Optional[bool] = None
    ax_name_len: Optional[int] = None
    ax_name_is_generic: Optional[bool] = None
    ax_name_matches_text: Optional[float] = None
    ax_state_disabled: Optional[bool] = None
    ax_state_focusable: Optional[bool] = None
    ax_state_focused: Optional[bool] = None
    ax_state_expanded: Optional[bool] = None
    ax_state_selected: Optional[bool] = None
    ax_state_checked: Optional[bool] = None
    ax_state_hidden: Optional[bool] = None
    ax_state_inert: Optional[bool] = None
    aria_hidden_ancestor: Optional[bool] = None
    is_div_with_pointer: Optional[bool] = None
    has_click_handler_hint: Optional[bool] = None
    in_shadow_root: Optional[bool] = None
    in_iframe: Optional[bool] = None
    sibling_name_collisions: Optional[int] = None
    target_resolvable: Optional[bool] = None

    # --- freshness and page settle state -----------------------------------
    ms_since_last_mutation: Optional[float] = None
    ms_since_last_global_mutation: Optional[float] = None
    mutation_count_1s: Optional[int] = None
    ms_since_navigation: Optional[float] = None
    pending_network_requests: Optional[int] = None
    has_running_animation: Optional[bool] = None
    page_settled: Optional[bool] = None
    snapshot_age_ms: Optional[float] = None
    ax_timed_out: bool = False

    # --- context ------------------------------------------------------------
    action_type: str = "other"
    prior_failures_this_episode: int = 0
    viewport_w: Optional[float] = None
    viewport_h: Optional[float] = None
    dpr: Optional[float] = None
    modal_open: Optional[bool] = None
    target_inside_modal: Optional[bool] = None

    # --- audit-only, never trained on (see FORBIDDEN_FEATURE_COLUMNS) ------
    ax_name: Optional[str] = None
    element_text: Optional[str] = None
    action_raw: Optional[str] = None
    selector: Optional[str] = None

    # --- bookkeeping --------------------------------------------------------
    probe_wall_ms: Optional[float] = None
    ts_dispatch: Optional[float] = None
    probe_errors: list[str] = field(default_factory=list)
    schema_version: str = SCHEMA_VERSION
    schema_hash: str = ""

    def __post_init__(self) -> None:
        if not self.schema_hash:
            self.schema_hash = schema_hash()

    def to_row(self) -> dict[str, Any]:
        row = asdict(self)
        row["probe_errors"] = ";".join(self.probe_errors) if self.probe_errors else ""
        return row

    @classmethod
    def from_probe(
        cls,
        task_id: str,
        step_index: int,
        in_page: dict[str, Any],
        cdp: dict[str, Any],
        **meta: Any,
    ) -> "StepFeatures":
        """Merge the in-page result and the CDP result into one record.

        Unknown keys in either dict are dropped rather than raising, so a probe
        script that is ahead of the schema does not break collection. Known
        keys missing from both stay ``None``.
        """
        known = {f.name for f in fields(cls)}
        merged: dict[str, Any] = {}
        for source in (in_page, cdp, meta):
            for key, value in (source or {}).items():
                if key in known:
                    merged[key] = value
        merged["task_id"] = task_id
        merged["step_index"] = step_index
        return cls(**merged)


def schema_hash() -> str:
    """Stable hash over the feature contract.

    Covers the schema version, the ordered feature columns with their declared
    types, the group assignment, and the categorical vocabularies — everything
    that changes the meaning of a stored row. Adding an audit-only column does
    not change the hash; adding, removing, renaming or regrouping a *feature*
    does.
    """
    type_by_name = {f.name: str(f.type) for f in fields(StepFeatures)}
    payload = {
        "schema_version": SCHEMA_VERSION,
        "features": [[name, type_by_name.get(name, "?")] for name in FEATURE_COLUMNS],
        "groups": {g: list(cols) for g, cols in FEATURE_GROUPS.items()},
        "categoricals": list(CATEGORICAL_COLUMNS),
        "ax_roles": list(AX_ROLE_BUCKETS),
        "action_types": list(ACTION_TYPES),
        "label": LABEL_COLUMN,
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()[:16]


def assert_single_schema(hashes) -> str:
    """Fail loudly when one training run spans several schema versions."""
    distinct = sorted({h for h in hashes if h})
    if not distinct:
        raise ValueError("no schema_hash present in the data — refusing to train")
    if len(distinct) > 1:
        raise ValueError(
            "data mixes feature schema versions: "
            + ", ".join(distinct)
            + ". Re-collect or filter to one version; mixing them is a silent "
            "correctness bug."
        )
    return distinct[0]


def bucket_ax_role(role: Optional[str]) -> str:
    if not role:
        return "none"
    normalised = role.strip().lower().replace(" ", "")
    return normalised if normalised in AX_ROLE_BUCKETS else "other"


def is_generic_name(name: Optional[str]) -> bool:
    if not name:
        return False
    return name.strip().lower().strip(" .:*") in GENERIC_NAME_STOPLIST


ActionType = Literal["click", "type", "select", "drag", "scroll", "other"]


def feature_columns(drop_groups: tuple[str, ...] = ()) -> list[str]:
    """Feature columns with whole groups removed — the ablation entry point."""
    unknown = set(drop_groups) - set(FEATURE_GROUPS)
    if unknown:
        raise KeyError(f"unknown feature group(s): {sorted(unknown)}")
    return [
        name
        for group, cols in FEATURE_GROUPS.items()
        if group not in drop_groups
        for name in cols
    ]


if __name__ == "__main__":
    print(json.dumps({
        "schema_version": SCHEMA_VERSION,
        "schema_hash": schema_hash(),
        "n_features": len(FEATURE_COLUMNS),
        "groups": {g: len(c) for g, c in FEATURE_GROUPS.items()},
    }, indent=2))
