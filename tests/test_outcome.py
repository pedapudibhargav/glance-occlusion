"""Tests for the label definition.

The label is the load-bearing decision in the project, so these tests are
written against the spec's three numbered conditions directly. If one of them
fails, the question is whether the definition changed — not whether the test
needs relaxing.
"""

from __future__ import annotations

from collect.outcome import (
    LABEL_VERSION,
    evaluate_outcome,
    is_element_action,
    parse_action,
)


def snapshot(**overrides) -> dict:
    base = {
        "t": 1000.0,
        "element_present": True,
        "banner_present": False,
        "task_done_attr": False,
        "task_done_global": False,
        "dialog_count": 0,
        "modal_open": False,
        "url": "http://localhost:3002/task/button-antd-T01?mode=benchmark",
        "aria_expanded": None,
        "aria_checked": None,
        "aria_selected": None,
        "aria_pressed": None,
        "value": None,
        "checked": None,
        "text": "Save",
        "landmark_text_len": 120,
        "landmark_child_count": 14,
        "landmark_mutation_t": None,
    }
    base.update(overrides)
    return base


# --- condition (1): terminal success ---------------------------------------


def test_banner_appearing_is_success():
    outcome = evaluate_outcome(snapshot(), snapshot(banner_present=True))
    assert outcome.label is True
    assert outcome.banner_appeared is True


def test_banner_already_present_is_not_credited():
    """A banner that was already there is not evidence for *this* step."""
    outcome = evaluate_outcome(
        snapshot(banner_present=True), snapshot(banner_present=True)
    )
    assert outcome.banner_appeared is False


# --- condition (2): state changed consistently with the action -------------


def test_aria_expanded_flip_is_success():
    outcome = evaluate_outcome(
        snapshot(aria_expanded="false"), snapshot(aria_expanded="true")
    )
    assert outcome.label is True
    assert outcome.state_changed is True


def test_value_change_is_success():
    outcome = evaluate_outcome(snapshot(value=""), snapshot(value="typed text"))
    assert outcome.label is True


def test_dialog_opening_is_success():
    outcome = evaluate_outcome(
        snapshot(dialog_count=0, modal_open=False),
        snapshot(dialog_count=1, modal_open=True),
    )
    assert outcome.label is True
    assert outcome.dialog_changed is True


def test_landmark_mutation_is_success():
    outcome = evaluate_outcome(snapshot(), snapshot(landmark_mutation_t=40.0))
    assert outcome.label is True
    assert outcome.landmark_mutated is True


def test_stale_landmark_mutation_is_not_success():
    """A mutation from a previous step must not credit this one."""
    outcome = evaluate_outcome(
        snapshot(), snapshot(landmark_mutation_t=9000.0)
    )
    assert outcome.landmark_mutated is False
    assert outcome.label is False


def test_nothing_changed_is_failure():
    outcome = evaluate_outcome(snapshot(), snapshot())
    assert outcome.label is False
    assert "no_observable_progress" in outcome.reasons


# --- condition (3): no error fired -----------------------------------------


def test_preflight_rejection_forces_failure_even_with_progress():
    """Condition (3) is a veto, not a vote.

    A click that landed on a different element may still have changed the page
    — it hit *something*. That is precisely the mis-click that looks like a
    success, and labelling it 1 would teach the router that occlusion is fine.
    """
    outcome = evaluate_outcome(
        before=snapshot(aria_expanded="false"),
        after=snapshot(aria_expanded="true"),
        preflight={"ok": False, "reason": "hit_test_mismatch", "hit_tag": "div.overlay"},
    )
    assert outcome.label is False
    assert outcome.error_fired is True
    assert outcome.error_reason == "hit_test_mismatch"
    assert outcome.state_changed is True  # recorded, but vetoed


def test_dispatch_error_forces_failure():
    outcome = evaluate_outcome(
        snapshot(), snapshot(banner_present=True), dispatch_error="TimeoutError: locator"
    )
    assert outcome.label is False
    assert outcome.error_fired is True


# --- unlabelled rather than negative --------------------------------------


def test_missing_snapshot_is_unlabelled_not_negative():
    """A collector failure must never be recorded as a page failure.

    Otherwise the feature set ends up predicting collector bugs, which is both
    wrong and invisible in aggregate metrics.
    """
    assert evaluate_outcome({}, snapshot()).label is None
    assert evaluate_outcome(snapshot(), {}).label is None
    assert "missing_snapshot" in evaluate_outcome({}, {}).reasons


def test_label_version_is_recorded():
    assert evaluate_outcome(snapshot(), snapshot()).to_row()["label_version"] == LABEL_VERSION


# --- action parsing --------------------------------------------------------


def test_parse_bid_actions():
    assert parse_action("click('42')") == ("click", "42", "click('42')")
    assert parse_action('fill("17", "hello")')[:2] == ("type", "17")
    assert parse_action("select_option('9', 'Option A')")[:2] == ("select", "9")
    assert parse_action("drag_and_drop('3', '4')")[:2] == ("drag", "3")


def test_parse_coord_actions_have_no_element_target():
    action_type, target, _ = parse_action("mouse_click(640, 360)")
    assert action_type == "click"
    assert target is None


def test_non_element_actions_are_excluded():
    assert is_element_action("click('42')") is True
    assert is_element_action("send_msg_to_user('done')") is False
    assert is_element_action("report_infeasible('DONE')") is False
    assert is_element_action("noop()") is False
    assert is_element_action("") is False


def test_unknown_action_is_other_not_crash():
    assert parse_action("some_future_action('x')")[0] == "other"


# --- the veto is narrow ----------------------------------------------------


def test_offscreen_does_not_veto_a_successful_step():
    """Playwright scrolls an element into view before clicking.

    The first real collection run labelled two solved tasks as failures
    because the target's centre was outside the viewport, even though the
    success banner had fired. A label that calls a success a failure teaches
    the router to escalate on exactly the steps that work.
    """
    outcome = evaluate_outcome(
        before=snapshot(aria_expanded="false"),
        after=snapshot(aria_expanded="true", banner_present=True),
        preflight={"ok": False, "reason": "centre_offscreen"},
    )
    assert outcome.label is True
    assert outcome.error_fired is False
    assert "preflight:centre_offscreen" in outcome.reasons


def test_unresolvable_element_is_not_itself_a_veto():
    """An element the agent named but that is not in the DOM.

    Observed live: the agent clicked bid 124 four times on one task and
    nothing moved. That is a genuine failure, but it is a failure because
    nothing progressed — not because the preflight complained. Leaving the
    decision to the progress test keeps the two reasons separable.
    """
    nothing_happened = evaluate_outcome(
        before=snapshot(),
        after=snapshot(),
        preflight={"ok": False, "reason": "element_not_resolved"},
    )
    assert nothing_happened.label is False
    assert "no_observable_progress" in nothing_happened.reasons

    but_it_worked = evaluate_outcome(
        before=snapshot(),
        after=snapshot(banner_present=True),
        preflight={"ok": False, "reason": "element_not_resolved"},
    )
    assert but_it_worked.label is True


def test_hit_test_mismatch_still_vetoes():
    """The one case the spec names: the click landed on something else."""
    outcome = evaluate_outcome(
        before=snapshot(aria_expanded="false"),
        after=snapshot(aria_expanded="true"),
        preflight={"ok": False, "reason": "hit_test_mismatch", "hit_tag": "div.overlay"},
    )
    assert outcome.label is False
    assert outcome.error_fired is True
