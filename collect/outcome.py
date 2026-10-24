"""The label definition.

This is the single most important design decision in the project. Get it wrong
and nothing downstream is meaningful, so it lives in one small module of its
own rather than inline in the collector, and every condition below maps to a
numbered clause of the spec.

    Primary label. For each step, dispatch the action using the structured
    layer only. Immediately after, check three conditions:

      1. The success banner appeared (terminal success for the task), or
      2. The page state changed in a way consistent with the intended action —
         the target element's aria-expanded, aria-checked, value or
         aria-selected flipped, a dialog opened or closed, or the DOM subtree
         under the target's nearest landmark mutated, and
      3. No error condition fired — the dispatch was not rejected by a
         pre-flight hit-test, and the click did not land on a different
         element.

    Label is 1 if (1) or (2) holds and (3) holds. Label is 0 otherwise.

Terminal task success is deliberately *not* the label. It conflates grounding
failure with planning failure: an agent that clicks the right button and then
chooses the wrong next step has not suffered an observation-layer failure, and
labelling it as one would teach the router to predict the agent's planning.
Per-step attribution is what makes the signal learnable.

**Changing anything here invalidates every row collected before the change.**
If you must change it, bump ``LABEL_VERSION``, say so in the commit message,
and re-collect — do not mix label versions in one training run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

LABEL_VERSION = "1.1.0"

# Condition (2) counts a landmark subtree mutation only if it happened within
# this window of the post-action read. Anything older belongs to a previous
# step, not to this dispatch.
LANDMARK_MUTATION_WINDOW_MS = 1500.0

# State attributes whose flip counts as "consistent with the intended action".
STATE_KEYS = (
    "aria_expanded",
    "aria_checked",
    "aria_selected",
    "aria_pressed",
    "value",
    "checked",
)


@dataclass
class Outcome:
    """The label for one attempted action, with its reason recorded.

    ``reasons`` exists so the 100-row hand audit can be done against the
    collector's own reasoning rather than against a bare 0/1. A label you
    cannot explain is a label you cannot audit.
    """

    label: Optional[bool]
    banner_appeared: bool = False
    state_changed: bool = False
    dialog_changed: bool = False
    landmark_mutated: bool = False
    error_fired: bool = False
    error_reason: str = ""
    reasons: list[str] = field(default_factory=list)
    label_version: str = LABEL_VERSION

    def to_row(self) -> dict[str, Any]:
        return {
            "structure_only_succeeded": self.label,
            "banner_appeared": self.banner_appeared,
            "state_changed": self.state_changed,
            "dialog_changed": self.dialog_changed,
            "landmark_mutated": self.landmark_mutated,
            "error_fired": self.error_fired,
            "error_reason": self.error_reason,
            "label_reasons": ";".join(self.reasons),
            "label_version": self.label_version,
        }


def _flipped(before: Any, after: Any) -> bool:
    """True when a state attribute changed in a way the DOM reports.

    ``None`` on both sides is not a flip. ``None`` on one side is: the
    attribute appeared or disappeared, which is a real state change for
    attributes like ``aria-expanded``.
    """
    if before is None and after is None:
        return False
    return before != after


def evaluate_outcome(
    before: dict[str, Any],
    after: dict[str, Any],
    preflight: Optional[dict[str, Any]] = None,
    dispatch_error: str = "",
) -> Outcome:
    """Apply the label definition to one before/after pair.

    ``before`` and ``after`` are ``window.__glance.stateSnapshot()`` results
    taken immediately before and immediately after dispatch. ``preflight`` is
    ``window.__glance.preflight()`` taken before dispatch. ``dispatch_error``
    is whatever the harness raised, empty when the action executed.

    Returns ``Outcome`` with ``label=None`` when the inputs are too incomplete
    to decide — a missing snapshot is an unlabelled row, never a negative one.
    Silently labelling missing data as failure is how a feature set ends up
    predicting collector bugs.
    """
    outcome = Outcome(label=None)

    if not before or not after:
        outcome.reasons.append("missing_snapshot")
        return outcome

    # --- condition (3): no error fired ---------------------------------
    #
    # Only a hit-test mismatch vetoes. The spec's condition (3) is "the
    # dispatch was not rejected by a pre-flight hit-test, and the click did
    # not land on a different element" — one condition about the click going
    # somewhere else, not a general health check on the element.
    #
    # Treating every preflight complaint as a veto produced false negatives in
    # the first real collection run. Two rows read:
    #
    #   preflight:centre_offscreen;banner_appeared;state_flip:aria_expanded
    #
    # The success banner had fired — the task was solved — and the step was
    # still labelled a failure because the element's centre was outside the
    # viewport. Playwright scrolls an element into view before clicking, so
    # offscreen is not a failed dispatch. A label that calls a success a
    # failure teaches the router to escalate on exactly the steps that work.
    #
    # Offscreen is still recorded, both in the reasons and as
    # rect_in_viewport, so the router can learn from it as a signal.
    VETOING_PREFLIGHT_REASONS = {"hit_test_mismatch"}

    if preflight is not None and not preflight.get("ok", True):
        reason = str(preflight.get("reason") or "preflight_rejected")
        outcome.reasons.append(f"preflight:{reason}")
        if reason in VETOING_PREFLIGHT_REASONS:
            outcome.error_fired = True
            outcome.error_reason = reason
    if dispatch_error:
        outcome.error_fired = True
        outcome.error_reason = outcome.error_reason or dispatch_error[:120]
        outcome.reasons.append("dispatch_error")

    # --- condition (1): terminal success -------------------------------
    banner_before = bool(
        before.get("banner_present") or before.get("task_done_attr") or before.get("task_done_global")
    )
    banner_after = bool(
        after.get("banner_present") or after.get("task_done_attr") or after.get("task_done_global")
    )
    outcome.banner_appeared = banner_after and not banner_before
    if outcome.banner_appeared:
        outcome.reasons.append("banner_appeared")

    # --- condition (2): state changed consistently with the action -----
    for key in STATE_KEYS:
        if _flipped(before.get(key), after.get(key)):
            outcome.state_changed = True
            outcome.reasons.append(f"state_flip:{key}")

    if before.get("dialog_count") is not None and after.get("dialog_count") is not None:
        if before["dialog_count"] != after["dialog_count"]:
            outcome.dialog_changed = True
            outcome.reasons.append("dialog_count_changed")
    if _flipped(before.get("modal_open"), after.get("modal_open")):
        outcome.dialog_changed = True
        outcome.reasons.append("modal_toggled")

    landmark_age = after.get("landmark_mutation_t")
    if landmark_age is not None and landmark_age <= LANDMARK_MUTATION_WINDOW_MS:
        outcome.landmark_mutated = True
        outcome.reasons.append("landmark_mutated")
    else:
        # Structural proxies for a subtree change, for the case where the
        # mutation log missed it (a mutation inside a closed shadow root, say).
        for key in ("landmark_text_len", "landmark_child_count"):
            if before.get(key) is not None and after.get(key) is not None:
                if before[key] != after[key]:
                    outcome.landmark_mutated = True
                    outcome.reasons.append(f"landmark_{key}_changed")
                    break

    # A navigation is a page-level change, not evidence that this element
    # responded, but it does mean the step did something. Treated as a state
    # change only when nothing else fired, and recorded as such.
    if before.get("url") and after.get("url") and before["url"] != after["url"]:
        outcome.state_changed = True
        outcome.reasons.append("url_changed")

    progressed = (
        outcome.banner_appeared
        or outcome.state_changed
        or outcome.dialog_changed
        or outcome.landmark_mutated
    )

    outcome.label = bool(progressed and not outcome.error_fired)
    if not progressed:
        outcome.reasons.append("no_observable_progress")
    return outcome


def parse_action(action: str) -> tuple[str, Optional[str], str]:
    """Split a BrowserGym action string into (action_type, target, raw).

    ComponentBench runs two action spaces: ``bid`` for the structured modes,
    where the target is an element id (``click('42')``), and ``coord`` for
    pixel mode (``mouse_click(640, 360)``). The probe needs the element, so
    coordinate actions return ``None`` as the target and are probed by
    hit-testing the coordinate instead.
    """
    raw = (action or "").strip()
    if not raw:
        return "other", None, raw

    name, _, rest = raw.partition("(")
    name = name.strip().lower()
    args = rest.rsplit(")", 1)[0]

    action_type = _ACTION_TYPE_BY_NAME.get(name, "other")

    target: Optional[str] = None
    if name in _BID_ACTIONS:
        first = args.split(",", 1)[0].strip()
        target = first.strip("'\"") or None

    return action_type, target, raw


_ACTION_TYPE_BY_NAME = {
    # bid action space
    "click": "click",
    "dblclick": "click",
    "fill": "type",
    "press": "type",
    "select_option": "select",
    "check": "click",
    "uncheck": "click",
    "hover": "other",
    "focus": "other",
    "clear": "type",
    "drag_and_drop": "drag",
    "scroll": "scroll",
    "upload_file": "other",
    # coord action space
    "mouse_click": "click",
    "mouse_dblclick": "click",
    "mouse_down": "click",
    "mouse_up": "click",
    "mouse_move": "other",
    "mouse_drag_and_drop": "drag",
    "keyboard_type": "type",
    "keyboard_press": "type",
    "keyboard_insert_text": "type",
    # chat / nav / infeasible — not element interactions
    "send_msg_to_user": "other",
    "report_infeasible": "other",
    "goto": "other",
    "go_back": "other",
    "go_forward": "other",
    "noop": "other",
}

_BID_ACTIONS = {
    "click",
    "dblclick",
    "fill",
    "press",
    "select_option",
    "check",
    "uncheck",
    "hover",
    "focus",
    "clear",
    "drag_and_drop",
    "scroll",
    "upload_file",
}

# Actions that do not touch an element at all. Steps consisting only of these
# produce no row: there is nothing for the router to route.
NON_ELEMENT_ACTIONS = {
    "send_msg_to_user",
    "report_infeasible",
    "goto",
    "go_back",
    "go_forward",
    "noop",
}


def is_element_action(action: str) -> bool:
    name = (action or "").strip().partition("(")[0].strip().lower()
    return bool(name) and name not in NON_ELEMENT_ACTIONS
