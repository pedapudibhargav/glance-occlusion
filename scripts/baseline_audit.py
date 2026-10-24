"""The format audit, redone against the baselines agents actually run.

The first version of this audit compared against `Accessibility.getFullAXTree`
on the top document alone, and reported that the AX route misses a consent
button inside a cross-origin iframe on 4 of 4 sites. That is true of the call
it made and false of the systems it was meant to describe: BrowserGym's
`extract_merged_axtree` enumerates frames with `Page.getFrameTree` and requests
a tree per `frameId`. Browser Use goes further and marks occlusion directly,
via CDP paint order — `node.ignored_by_paint_order = True` when a node's rect
is fully inside the union of rects painted above it.

So this measures four things on the same page:

  AX_top    CDP Accessibility.getFullAXTree, top document only (a strawman,
            kept because it is what a naive integration does)
  AX_merged the same call per frame, as BrowserGym does it
  PW        Playwright aria_snapshot(mode="ai"), what Playwright MCP serves
  PAINT     paint-order coverage, the rule Browser Use applies

and asks where each one's answer differs from a hit test, which is the only
ground truth for "would a click land here".
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import sync_playwright  # noqa: E402

from eval.overlay_sites import tolerant_goto  # noqa: E402

STRUCTURE_JS = Path("probe/structure.js").read_text(encoding="utf-8")

SITES = [
    ("bbc", "https://www.bbc.com/news", "Accept and Continue"),
    ("spiegel", "https://www.spiegel.de/", "Consent and continue"),
    ("ft", "https://www.ft.com/", "Accept Cookies"),
    ("skysports", "https://www.skysports.com/", "Accept all"),
    ("cnn", "https://edition.cnn.com/", None),
    ("lemonde", "https://www.lemonde.fr/", None),
    ("euronews", "https://www.euronews.com/", None),
    ("irishtimes", "https://www.irishtimes.com/", None),
]

# AX property names Chrome defines that would express occlusion or inertness
# if they were emitted. Whether getFullAXTree populates them for modal-covered
# nodes is exactly the question, so they are counted rather than assumed.
OCCLUSION_PROPS = ("inertElement", "inertSubtree", "activeModalDialog",
                   "activeAriaModalDialog", "ariaHiddenSubtree", "notRendered",
                   "notVisible", "hidden", "hiddenRoot")


def merged_axtree(session, page) -> dict:
    """BrowserGym's approach: one getFullAXTree per frame, concatenated."""
    tree = session.send("Page.getFrameTree")
    frame_ids: list[str] = []

    def walk(node):
        frame_ids.append(node["frame"]["id"])
        for child in node.get("childFrames", []):
            walk(child)

    walk(tree["frameTree"])
    nodes = []
    unreachable = 0
    for fid in frame_ids:
        try:
            nodes.extend(session.send("Accessibility.getFullAXTree",
                                      {"frameId": fid})["nodes"])
        except Exception:
            unreachable += 1
    return {"nodes": nodes, "frames": len(frame_ids), "unreachable": unreachable}


def paint_order_covered(page) -> dict:
    """Browser Use's rule, reimplemented from its stated logic.

    An element counts as covered when its rect sits entirely inside the union
    of rects painted above it. Transparent or near-transparent elements do not
    contribute to that union — Browser Use skips anything under 0.8 opacity —
    which is where the rule and a hit test should part company.
    """
    return page.evaluate("""() => {
      const els = [...document.querySelectorAll('a[href],button,input,select,textarea,summary,[role=button],[role=link]')]
        .filter(e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; });
      // Paint order is approximated by document order within stacking
      // position, which is what the DOM exposes without CDP.
      const boxes = [];
      for (const e of document.querySelectorAll('*')) {
        const s = getComputedStyle(e);
        if (s.position === 'static') continue;
        if (parseFloat(s.opacity) < 0.8) continue;
        const bg = s.backgroundColor;
        if (!bg || bg === 'transparent' || /rgba\\(.*,\\s*0\\)$/.test(bg)) continue;
        const r = e.getBoundingClientRect();
        if (r.width < 50 || r.height < 50) continue;
        boxes.push({ r, e });
      }
      let covered = 0, hit = 0, both = 0, paintOnly = 0, hitOnly = 0;
      for (const e of els) {
        const r = e.getBoundingClientRect();
        const byPaint = boxes.some(b => !b.e.contains(e) &&
          b.r.left <= r.left && b.r.top <= r.top &&
          b.r.right >= r.right && b.r.bottom >= r.bottom);
        const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
        let byHit = false;
        if (cx >= 0 && cy >= 0 && cx <= innerWidth && cy <= innerHeight) {
          const top = document.elementFromPoint(cx, cy);
          byHit = !!top && top !== e && !e.contains(top) && !top.contains(e);
        }
        if (byPaint) covered++;
        if (byHit) hit++;
        if (byPaint && byHit) both++;
        if (byPaint && !byHit) paintOnly++;
        if (!byPaint && byHit) hitOnly++;
      }
      return { elements: els.length, paint_covered: covered, hit_covered: hit,
               agree: both, paint_only: paintOnly, hit_only: hitOnly };
    }""")


def main() -> int:
    rows: dict[str, dict] = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        for name, url, button in SITES:
            ctx = browser.new_context(viewport={"width": 1280, "height": 720},
                                      locale="en-GB")
            page = ctx.new_page()
            row: dict = {}
            try:
                tolerant_goto(page, url)
                session = ctx.new_cdp_session(page)
                session.send("Accessibility.enable")

                top = session.send("Accessibility.getFullAXTree")
                merged = merged_axtree(session, page)
                snap = page.aria_snapshot(mode="ai")
                page.evaluate(STRUCTURE_JS)
                gl = page.evaluate("() => window.__glanceStructure.build()")
                paint = paint_order_covered(page)

                top_blob, merged_blob = json.dumps(top), json.dumps(merged)
                row.update({
                    "blocked_hit_test": gl["blocked"],
                    "ax_top_nodes": len(top["nodes"]),
                    "ax_merged_nodes": len(merged["nodes"]),
                    "frames": merged["frames"],
                    "frames_unreadable": merged["unreachable"],
                    "paint_vs_hit": paint,
                })
                if button:
                    row["dismiss_in_ax_top"] = button.lower() in top_blob.lower()
                    row["dismiss_in_ax_merged"] = button.lower() in merged_blob.lower()
                    row["dismiss_in_pw"] = button.lower() in snap.lower()

                present = set()
                for node in merged["nodes"]:
                    for prop in node.get("properties", []):
                        if prop.get("name") in OCCLUSION_PROPS:
                            present.add(prop["name"])
                row["ax_occlusion_props_emitted"] = sorted(present)

                rows[name] = row
                print(f'{name:<12} blocked={gl["blocked"]:<4} '
                      f'frames={merged["frames"]:<3} '
                      f'dismiss top/merged/pw: '
                      f'{row.get("dismiss_in_ax_top")}/'
                      f'{row.get("dismiss_in_ax_merged")}/{row.get("dismiss_in_pw")}  '
                      f'paint={paint["paint_covered"]} hit={paint["hit_covered"]} '
                      f'hit_only={paint["hit_only"]} props={row["ax_occlusion_props_emitted"]}')
            except Exception as exc:
                rows[name] = {"error": f"{type(exc).__name__}: {exc}"}
                print(f"{name:<12} ERROR {type(exc).__name__}: {exc}")
            finally:
                ctx.close()
        browser.close()
    Path("data/baseline_audit.json").write_text(json.dumps(rows, indent=2),
                                                encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
