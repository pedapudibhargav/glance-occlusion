"""Browser tests for ``probe/inject.js``.

These assertions were first verified by hand against Chrome on
``tests/fixtures/probe_fixture.html``; this file keeps them honest. They need
Playwright and a Chromium install, so they skip where those are absent rather
than failing the whole suite.

The fixture is served over HTTP and the viewport is set explicitly. Both
matter: a ``file://`` page and a background page can both report a zero
viewport, and the probe then reports every feature as unknown — correctly, but
uselessly for a test.
"""

from __future__ import annotations

import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

FIXTURE_DIR = Path(__file__).parent / "fixtures"
INJECT_JS = Path(__file__).parent.parent / "probe" / "inject.js"

playwright_module = pytest.importorskip(
    "playwright.sync_api", reason="playwright is not installed"
)


@pytest.fixture(scope="module")
def fixture_server():
    handler = partial(SimpleHTTPRequestHandler, directory=str(FIXTURE_DIR))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


@pytest.fixture(scope="module")
def browser_context(fixture_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except Exception as exc:  # pragma: no cover - missing browser binary
            pytest.skip(f"chromium unavailable: {exc}")
        context = browser.new_context(viewport={"width": 1280, "height": 720})
        context.add_init_script(INJECT_JS.read_text(encoding="utf-8"))
        yield context
        context.close()
        browser.close()


@pytest.fixture
def page(browser_context, fixture_server):
    """A fresh page per test.

    Function-scoped on purpose. These tests click, toggle and scroll, and the
    probe's own features are state: scroll position decides which elements are
    hit-testable, and the mutation log decides freshness. Sharing one page
    across tests makes later assertions depend on earlier ones — an earlier
    ``scrollIntoView`` puts ``#clean-button`` off screen, and the probe then
    correctly reports it as unknown while the test reads that as a bug.
    """
    page = browser_context.new_page()
    page.goto(f"{fixture_server}/probe_fixture.html", wait_until="load")
    # The fixture positions the occluded button in a requestAnimationFrame
    # callback, so wait for layout to settle before probing.
    page.wait_for_timeout(200)
    yield page
    page.close()


def probe(page, selector: str) -> dict:
    return page.evaluate("(sel) => window.__glance.probe(sel)", selector)


def test_probe_is_installed(page):
    state = page.evaluate("() => ({v: window.__glance.version, s: window.__glance._state()})")
    assert state["v"]
    assert state["s"]["mutation_records"] >= 0


def test_viewport_is_usable(page):
    """Guard the guard: a zero viewport would make every other test vacuous."""
    assert page.evaluate("() => window.innerWidth") == 1280


def test_clean_button_is_hit_testable(page):
    result = probe(page, "#clean-button")
    assert result["hit_is_self"] is True
    assert result["hit_is_descendant_depth"] == 0
    assert result["corner_hit_agreement"] == 1
    assert result["occluder_tag"] is None
    assert result["probe_errors"] == []


def test_cookie_banner_over_button_is_detected(page):
    """The done-condition for the probe: a banner over a button.

    This is the failure mode the whole project exists for — the element is
    present in the structured layer, correctly named, and completely
    unclickable.
    """
    result = probe(page, "#occluded-button")
    assert result["hit_is_self"] is False
    assert result["occluder_tag"]
    assert result["occluder_is_fixed"] is True
    assert result["occluder_z_index"] >= 1


def test_partial_occlusion_is_caught_by_corner_probes(page):
    """A sticky header covering only the top edge.

    A single centre probe can miss this, which is exactly the case that
    produces a mis-click that looks like a success. ``corner_hit_agreement``
    must land strictly between 0 and 1.
    """
    result = probe(page, "#under-header")
    assert 0 < result["corner_hit_agreement"] < 1
    assert result["occluder_is_fixed"] is True


def test_offscreen_element_is_unknown_not_occluded(page):
    """An element scrolled out of view is unknown, never "occluded".

    Returning ``False`` here would teach the router that offscreen means
    occluded, and the two have different remedies: one needs a scroll, the
    other needs pixels.
    """
    result = probe(page, "#offscreen-button")
    assert result["hit_is_self"] is None
    assert "all_probe_points_offscreen" in result["probe_errors"]


def test_sibling_name_collisions(page):
    """Five buttons named "Edit": technically correct, practically useless."""
    result = probe(page, "#collide-button")
    assert result["sibling_name_collisions"] == 4


def test_generic_name_is_flagged(page):
    assert probe(page, "#generic-button")["local_name_is_generic"] is True
    assert probe(page, "#clean-button")["local_name_is_generic"] is False


def test_div_with_pointer_cursor(page):
    assert probe(page, "#div-button")["is_div_with_pointer"] is True
    assert probe(page, "#clean-button")["is_div_with_pointer"] is False


def test_small_target(page):
    assert probe(page, "#tiny-button")["rect_min_dim"] <= 10


def test_shadow_root_element_is_hit_testable(page):
    """Hit testing must descend through open shadow roots.

    ``elementFromPoint`` stops at the shadow boundary, so without descent every
    shadow-hosted control reads as occluded.

    The element must be scrolled into view first. Below the fold it correctly
    reports ``hit_is_self: None`` — that is the offscreen case, not the shadow
    case, and conflating the two would make this test pass for the wrong reason.
    """
    page.evaluate("() => window.__shadowButton.scrollIntoView({block: 'center'})")
    page.wait_for_timeout(50)

    result = page.evaluate("() => window.__glance.probe(window.__shadowButton)")
    assert result["in_shadow_root"] is True
    assert result["hit_is_self"] is True
    assert result["corner_hit_agreement"] == 1


def test_initial_parsing_is_not_counted_as_a_mutation(page):
    """A freshly loaded page must not read as freshly mutated.

    While ``readyState`` is "loading" the parser appends the document's own
    markup, and every element's text node arrives as an added node inside it.
    Counting that would make ``ms_since_last_mutation`` track page age and
    ``page_settled`` false for every early step — a feature correlated with
    ``step_index`` rather than with instability.
    """
    state = page.evaluate("() => window.__glance._state()")
    assert state["ready_state"] != "loading"

    quiet = probe(page, "#clean-button")
    assert quiet["ms_since_last_mutation"] is None
    assert quiet["mutation_count_1s"] == 0

    # mutations_quiet is document-wide, and this fixture does write styles
    # after load from a requestAnimationFrame callback — a real post-load
    # mutation, which the probe should report. Once it is past the 250 ms
    # settle window the document reads as quiet.
    page.wait_for_timeout(350)
    assert probe(page, "#clean-button")["mutations_quiet"] is True


def test_mutation_freshness_tracks_subtrees(page):
    page.click("#mutate-button")
    page.wait_for_timeout(30)

    mutated = probe(page, "#mutation-target")
    assert mutated["ms_since_last_mutation"] is not None
    assert mutated["ms_since_last_mutation"] < 250
    assert mutated["mutation_count_1s"] >= 1
    assert mutated["mutations_quiet"] is False

    # An unrelated element shares the document-wide clock but not the subtree
    # one. If this fails, per-subtree freshness is really global freshness.
    unrelated = probe(page, "#clean-button")
    assert unrelated["ms_since_last_mutation"] is None
    assert unrelated["ms_since_last_global_mutation"] < 250

    page.wait_for_timeout(400)
    settled = probe(page, "#mutation-target")
    assert settled["ms_since_last_mutation"] >= 250
    assert settled["mutations_quiet"] is True


def test_preflight_rejects_occluded_and_missing(page):
    results = page.evaluate(
        """() => ({
            clean: window.__glance.preflight('#clean-button'),
            occluded: window.__glance.preflight('#occluded-button'),
            missing: window.__glance.preflight('#does-not-exist')
        })"""
    )
    assert results["clean"]["ok"] is True
    assert results["occluded"]["ok"] is False
    assert results["occluded"]["reason"] == "hit_test_mismatch"
    assert results["occluded"]["hit_tag"]
    assert results["missing"]["reason"] == "element_not_resolved"


def test_state_snapshot_sees_aria_expanded_flip(page):
    before = page.evaluate("() => window.__glance.stateSnapshot('#expand-button')")
    page.click("#expand-button")
    page.wait_for_timeout(40)
    after = page.evaluate("() => window.__glance.stateSnapshot('#expand-button')")

    assert before["aria_expanded"] == "false"
    assert after["aria_expanded"] == "true"
    assert after["landmark_mutation_t"] is not None


def test_state_snapshot_sees_dialog_open(page):
    before = page.evaluate("() => window.__glance.stateSnapshot('#open-dialog')")
    page.click("#open-dialog")
    page.wait_for_timeout(40)
    after = page.evaluate("() => window.__glance.stateSnapshot('#open-dialog')")

    assert before["dialog_count"] == 0
    assert after["dialog_count"] >= 1
    assert after["modal_open"] is True


def test_probe_is_idempotent(page):
    """Re-injection must not install a second observer or reset history."""
    source = INJECT_JS.read_text(encoding="utf-8")
    before = page.evaluate("() => window.__glance._state().mutation_records")
    page.evaluate(source)
    after = page.evaluate("() => window.__glance._state().mutation_records")
    assert after >= before
    assert page.evaluate("() => window.__glance.version")


def test_probe_is_cheap(page):
    """The probe runs before every action, so it may not be slow.

    Not a hard budget — a CI machine under load will be slower than a
    developer's laptop — but a probe that takes tens of milliseconds per call
    would undermine the cost argument and should fail loudly.
    """
    timings = page.evaluate(
        """() => {
            const out = [];
            for (let i = 0; i < 20; i++) {
                const t = performance.now();
                window.__glance.probe('#clean-button');
                out.push(performance.now() - t);
            }
            return out;
        }"""
    )
    median = sorted(timings)[len(timings) // 2]
    assert median < 25, f"in-page probe median {median:.2f} ms is too slow"
