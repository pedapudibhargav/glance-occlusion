"""How common is each expressive gap, and does any deployed format close it?

Occlusion was gap one. This asks whether it is alone or the first of a family:
for each candidate fact the DOM supplies for free, how often does it apply on a
real page, and is it present in the two serialisations agents actually get —
CDP `Accessibility.getFullAXTree` and Playwright `aria_snapshot(mode="ai")`.

Nothing here is assumed. The AX tree, for instance, may well carry a link's
`url` as a node property; that is checked rather than argued.
"""
from __future__ import annotations

import json
import random
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import sync_playwright  # noqa: E402

from eval.overlay_sites import tolerant_goto  # noqa: E402

GAPS_JS = Path("probe/gaps.js").read_text(encoding="utf-8")

SITES = [
    ("cnn", "https://www.cnn.com/"),
    ("lemonde", "https://www.lemonde.fr/"),
    ("bbc", "https://www.bbc.com/news"),
    ("wikipedia", "https://en.wikipedia.org/wiki/Web_browser"),
    ("github", "https://github.com/microsoft/playwright"),
    ("hackernews", "https://news.ycombinator.com/"),
    ("amazon", "https://www.amazon.com/"),
    ("gov_uk", "https://www.gov.uk/"),
    ("stackoverflow", "https://stackoverflow.com/questions"),
    ("euronews", "https://www.euronews.com/"),
    ("irishtimes", "https://www.irishtimes.com/"),
    ("mdn", "https://developer.mozilla.org/en-US/docs/Web/API"),
]


def ax_link_urls(ax: dict) -> int:
    """How many link nodes in the AX tree carry a destination."""
    found = 0
    for node in ax.get("nodes", []):
        role = (node.get("role") or {}).get("value")
        if role != "link":
            continue
        props = {p.get("name"): p.get("value", {}).get("value")
                 for p in node.get("properties", [])}
        if props.get("url"):
            found += 1
    return found


def main() -> int:
    rows: dict[str, dict] = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        for name, url in SITES:
            ctx = browser.new_context(viewport={"width": 1280, "height": 720},
                                      locale="en-GB")
            page = ctx.new_page()
            try:
                tolerant_goto(page, url)
                page.evaluate(GAPS_JS)
                gaps = page.evaluate("() => window.__glanceGaps.build()")

                snap = page.aria_snapshot(mode="ai")
                session = ctx.new_cdp_session(page)
                session.send("Accessibility.enable")
                ax = session.send("Accessibility.getFullAXTree")

                # Does either format carry a link's destination? Sample real
                # hrefs off the page and look for them in each serialisation.
                hrefs = page.evaluate(
                    """() => [...document.querySelectorAll('a[href]')]
                         .map(a => { try { return new URL(a.href).pathname; }
                                     catch (e) { return null; } })
                         .filter(p => p && p.length > 4)""")
                sample = random.Random(0).sample(hrefs, min(15, len(hrefs))) if hrefs else []
                blob = json.dumps(ax)
                gaps["pw_has_href"] = sum(1 for h in sample if h in snap)
                gaps["ax_has_href_string"] = sum(1 for h in sample if h in blob)
                gaps["href_sample"] = len(sample)
                gaps["ax_link_nodes_with_url"] = ax_link_urls(ax)
                # Shadow-DOM reachability: does either format name a control
                # that only exists inside a shadow root?
                shadow_names = [n for n in gaps["sample_shadow"] if n and len(n) > 3]
                gaps["pw_has_shadow_name"] = sum(1 for n in shadow_names if n in snap)
                gaps["ax_has_shadow_name"] = sum(1 for n in shadow_names if n in blob)
                gaps["shadow_name_sample"] = len(shadow_names)

                rows[name] = gaps
                print(f'{name:<14} n={gaps["interactive"]:<5} '
                      f'shadow={gaps["pct"]["in_shadow"]:>5}% '
                      f'inert/hidden={gaps["pct"]["inert_or_hidden"]:>5}% '
                      f'pe-none={gaps["pct"]["pointer_events_none"]:>5}% '
                      f'href in pw/ax: {gaps["pw_has_href"]}/{gaps["ax_has_href_string"]}'
                      f' of {gaps["href_sample"]}')
            except Exception as exc:
                rows[name] = {"error": f"{type(exc).__name__}: {exc}"}
                print(f"{name:<14} ERROR {type(exc).__name__}")
            finally:
                ctx.close()
        browser.close()

    ok = [r for r in rows.values() if "error" not in r]
    if ok:
        med = lambda k: round(statistics.median(r["pct"][k] for r in ok), 1)  # noqa: E731
        rows["_summary"] = {
            "sites": len(ok),
            "median_pct_in_shadow": med("in_shadow"),
            "median_pct_inert_or_hidden": med("inert_or_hidden"),
            "median_pct_pointer_events_none": med("pointer_events_none"),
            "sites_with_any_shadow_control": sum(1 for r in ok if r["in_shadow"]),
            "sites_with_any_inert_or_hidden": sum(
                1 for r in ok if r["inert"] or r["aria_hidden"]),
            "total_href_sampled": sum(r["href_sample"] for r in ok),
            "href_found_in_pw": sum(r["pw_has_href"] for r in ok),
            "href_found_in_ax_blob": sum(r["ax_has_href_string"] for r in ok),
            "ax_link_nodes_with_url": sum(r["ax_link_nodes_with_url"] for r in ok),
            "link_name_collisions": sum(r["link_name_collisions"] for r in ok),
            "collisions_href_separates": sum(r["collisions_href_separates"] for r in ok),
        }
        print("\nsummary:", json.dumps(rows["_summary"], indent=2))
    Path("data/gaps.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
