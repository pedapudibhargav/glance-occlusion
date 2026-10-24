"""Real-page tasks with programmatic success checks.

ComponentBench verifies success with a hidden banner, so no human or model
judges the outcome. The open web has no such banner, and an LLM judge would
put a model inside the measurement loop — the one thing the whole project
avoids. So each task here carries a JavaScript predicate that is true exactly
when the goal has been reached, evaluated on the live page.

Choosing tasks that way is restrictive, and deliberately so. A predicate like
"the URL now contains /Special:Search" or "this disclosure reports
aria-expanded=true" is unambiguous; "the user found what they wanted" is not.

**Everything here is read-only.** Public reference and documentation pages,
reached without an account. The reachable actions are navigation, in-page
disclosure and site search, which is a GET. No task submits data, changes
anything on a server, or touches a credential — and none of these sites offer
that to an anonymous visitor within a handful of clicks.

Pages drift. ``verify_tasks`` checks every precondition before a run and skips
a task whose target has moved, so a site redesign shows up as a skipped task
rather than a silently failed one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class RealTask:
    task_id: str
    url: str
    goal: str
    # True when the goal is reached. Evaluated on the page after each action.
    success_js: str
    # True at the start. Guards against a task that is already complete, and
    # against a page that has changed out from under the task.
    precondition_js: str = "() => true"
    max_steps: int = 5
    notes: str = ""


TASKS: list[RealTask] = [
    # --- in-page disclosure: the classic "is this control reachable" case ---
    RealTask(
        task_id="wikipedia-toc-toggle",
        url="https://en.wikipedia.org/wiki/Web_accessibility",
        goal="Collapse the 'Contents' table-of-contents panel using its toggle button.",
        precondition_js="() => !!document.querySelector('#vector-toc-collapsed-button, .vector-toc-collapse-button, #vector-page-titlebar-toc')",
        success_js="""() => {
            const b = document.querySelector('#vector-toc-collapsed-button, .vector-toc-collapse-button, #vector-page-titlebar-toc button');
            if (b && b.getAttribute('aria-expanded') === 'false') return true;
            const toc = document.querySelector('#vector-toc');
            return !!toc && toc.getBoundingClientRect().width === 0;
        }""",
    ),
    RealTask(
        task_id="wikipedia-lang-menu",
        url="https://en.wikipedia.org/wiki/Web_accessibility",
        goal="Open the language selector so the list of other languages is shown.",
        precondition_js="() => !!document.querySelector('#p-lang-btn, .vector-dropdown#p-lang-btn button, [data-mw-ve-target-container] #p-lang-btn')",
        success_js="""() => {
            const b = document.querySelector('#p-lang-btn button, #p-lang-btn');
            return !!b && b.getAttribute('aria-expanded') === 'true';
        }""",
    ),
    RealTask(
        task_id="wikipedia-search-accessibility",
        url="https://en.wikipedia.org/wiki/Main_Page",
        goal="Use the site search box to search for the term 'screen reader'.",
        precondition_js="() => !!document.querySelector('input[name=search], #searchInput, .cdx-text-input__input')",
        success_js="""() => location.href.toLowerCase().includes('screen') ||
            (location.search + location.pathname).toLowerCase().includes('search')""",
        notes="Site search is a GET; nothing is written.",
    ),
    RealTask(
        task_id="wikipedia-goto-references",
        url="https://en.wikipedia.org/wiki/Web_accessibility",
        goal="Jump to the 'References' section of the article.",
        precondition_js="() => !!document.querySelector('#References, [id=References], a[href=\\\"#References\\\"]')",
        success_js="() => location.hash.toLowerCase().includes('reference')",
    ),

    # --- documentation sites: dense navigation, many lookalike links -------
    RealTask(
        task_id="mdn-open-aria-guides",
        url="https://developer.mozilla.org/en-US/docs/Web/Accessibility",
        goal="Navigate to the ARIA section of the accessibility documentation.",
        precondition_js="() => !location.pathname.toLowerCase().includes('/aria')",
        success_js="() => location.pathname.toLowerCase().includes('aria')",
    ),
    RealTask(
        task_id="mdn-theme-menu",
        url="https://developer.mozilla.org/en-US/docs/Web/Accessibility",
        goal="Open the theme switcher menu (the control for choosing light or dark appearance).",
        precondition_js="() => !!document.querySelector('.theme-switcher-menu, button[id*=theme], [aria-label*=theme i]')",
        success_js="""() => {
            const b = document.querySelector('.theme-switcher-menu button, button[id*=theme], [aria-label*=theme i]');
            if (b && b.getAttribute('aria-expanded') === 'true') return true;
            return !!document.querySelector('.theme-switcher-menu [role=menu], .submenu.show');
        }""",
    ),
    RealTask(
        task_id="playwright-docs-locators",
        url="https://playwright.dev/docs/intro",
        goal="Open the 'Locators' page from the documentation sidebar.",
        precondition_js="() => !location.pathname.includes('locator')",
        success_js="() => location.pathname.toLowerCase().includes('locator')",
    ),
    RealTask(
        task_id="playwright-docs-search",
        url="https://playwright.dev/docs/intro",
        goal="Open the documentation search dialog.",
        precondition_js="() => !!document.querySelector('.DocSearch-Button, button[class*=search i], [aria-label*=search i]')",
        success_js="""() => !!document.querySelector('.DocSearch-Modal, [role=dialog] input[type=search], .DocSearch-Input')""",
    ),

    # --- government and standards pages: plain markup, long link lists ----
    RealTask(
        task_id="govuk-benefits-universal-credit",
        url="https://www.gov.uk/browse/benefits",
        goal="Open the 'Universal Credit' section.",
        precondition_js="() => !location.pathname.toLowerCase().includes('universal-credit')",
        success_js="() => location.pathname.toLowerCase().includes('universal-credit')",
    ),
    RealTask(
        task_id="w3c-wcag-quickref",
        url="https://www.w3.org/WAI/standards-guidelines/wcag/",
        goal="Navigate to the page about WCAG 2 guidelines or the quick reference.",
        precondition_js="() => !location.href.toLowerCase().includes('quickref') && !location.pathname.toLowerCase().includes('wcag2')",
        success_js="() => location.href.toLowerCase().includes('quickref') || location.pathname.toLowerCase().includes('wcag2')",
    ),

    # --- dense application-like pages -------------------------------------
    RealTask(
        task_id="hn-open-newest",
        url="https://news.ycombinator.com/",
        goal="Open the 'new' page showing the newest submissions.",
        precondition_js="() => !location.pathname.includes('newest')",
        success_js="() => location.pathname.includes('newest')",
    ),
    RealTask(
        task_id="hn-open-comments",
        url="https://news.ycombinator.com/",
        goal="Open the comments page (the 'comments' link in the top navigation).",
        precondition_js="() => !location.pathname.includes('newcomments')",
        success_js="() => location.pathname.includes('newcomments')",
    ),
    RealTask(
        task_id="github-playwright-issues",
        url="https://github.com/microsoft/playwright",
        goal="Open the Issues tab of this repository.",
        precondition_js="() => !location.pathname.endsWith('/issues')",
        success_js="() => location.pathname.toLowerCase().includes('/issues')",
    ),
    RealTask(
        task_id="github-playwright-readme-nav",
        url="https://github.com/microsoft/playwright",
        goal="Open the repository's list of releases.",
        precondition_js="() => !location.pathname.includes('releases')",
        success_js="() => location.pathname.toLowerCase().includes('releases')",
    ),
    RealTask(
        task_id="wikipedia-view-history",
        url="https://en.wikipedia.org/wiki/Web_accessibility",
        goal="Open the page's revision history.",
        precondition_js="() => !location.search.includes('action=history')",
        success_js="() => location.search.includes('action=history') || location.pathname.includes('history')",
    ),
]


def verify_tasks(page, tasks: Optional[list[RealTask]] = None) -> dict[str, str]:
    """Check each task's precondition on the live page.

    Returns task_id -> reason for every task that cannot run. Pages drift, and
    a task whose target has moved should be skipped loudly rather than counted
    as a failure for whichever arm happened to meet it.
    """
    skipped: dict[str, str] = {}
    for task in tasks or TASKS:
        try:
            page.goto(task.url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(1200)
        except Exception as exc:
            skipped[task.task_id] = f"navigation failed: {str(exc)[:70]}"
            continue
        try:
            if page.evaluate(task.success_js):
                skipped[task.task_id] = "already satisfied at load"
                continue
            if not page.evaluate(task.precondition_js):
                skipped[task.task_id] = "precondition not met; page likely changed"
        except Exception as exc:
            skipped[task.task_id] = f"predicate error: {str(exc)[:70]}"
    return skipped
