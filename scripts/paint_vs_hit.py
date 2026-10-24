"""Paint-order coverage against hit testing, using real CDP paint order.

Browser Use marks a node `ignored_by_paint_order` when its rect sits entirely
inside the union of rects painted above it, skipping contributors below 0.8
opacity or with a transparent background. That is a deployed occlusion signal
and the closest thing to this project's hit test, so the two have to be
compared on the same pages — and compared fairly, which means reading
`DOMSnapshot.captureSnapshot` with `includePaintOrder` rather than guessing
paint order from document order.

Ground truth is `elementFromPoint`: whatever it returns is what a click hits.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import sync_playwright  # noqa: E402

from eval.overlay_sites import tolerant_goto  # noqa: E402

SITES = [
    ("bbc", "https://www.bbc.com/news"),
    ("spiegel", "https://www.spiegel.de/"),
    ("ft", "https://www.ft.com/"),
    ("skysports", "https://www.skysports.com/"),
    ("cnn", "https://edition.cnn.com/"),
    ("lemonde", "https://www.lemonde.fr/"),
    ("euronews", "https://www.euronews.com/"),
    ("irishtimes", "https://www.irishtimes.com/"),
]

# Tag each interactive element so a CDP snapshot node can be matched back to
# the element the hit test was run on.
TAG_JS = """() => {
  const sel = 'a[href],button,input,select,textarea,summary,[role=button],[role=link]';
  let i = 0;
  const out = [];
  for (const e of document.querySelectorAll(sel)) {
    const r = e.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) continue;
    i += 1;
    e.setAttribute('data-glance-po', String(i));
    const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
    let hit = null;
    if (cx >= 0 && cy >= 0 && cx <= innerWidth && cy <= innerHeight) {
      const top = document.elementFromPoint(cx, cy);
      hit = !!top && top !== e && !e.contains(top) && !top.contains(e);
    }
    out.push({ id: i, hit_covered: hit,
               rect: [r.left + scrollX, r.top + scrollY, r.width, r.height] });
  }
  return out;
}"""


def covered_by_paint(snapshot: dict) -> set[int]:
    """Nodes whose rect is inside the union of rects painted above them."""
    doc = snapshot["documents"][0]
    strings = snapshot["strings"]
    layout = doc["layout"]
    nodes = doc["nodes"]
    bounds = layout["bounds"]
    node_index = layout["nodeIndex"]
    paint_orders = layout.get("paintOrders") or []
    styles = layout.get("styles") or []

    attrs = nodes.get("attributes", [])

    def glance_id(dom_index: int):
        row = attrs[dom_index] if dom_index < len(attrs) else []
        for k in range(0, len(row) - 1, 2):
            if strings[row[k]] == "data-glance-po":
                return int(strings[row[k + 1]])
        return None

    # Every laid-out box, with its paint order and whether it is opaque enough
    # to contribute to coverage. Browser Use skips contributors under 0.8.
    boxes = []
    for i, dom_index in enumerate(node_index):
        if i >= len(bounds) or not bounds[i]:
            continue
        order = paint_orders[i] if i < len(paint_orders) else 0
        opaque = True
        if styles and i < len(styles) and styles[i]:
            vals = [strings[s] for s in styles[i] if 0 <= s < len(strings)]
            for v in vals:
                try:
                    if 0 <= float(v) < 0.8:
                        opaque = False
                except ValueError:
                    pass
                if v in ("transparent", "rgba(0, 0, 0, 0)"):
                    opaque = False
        boxes.append({"i": i, "order": order, "b": bounds[i],
                      "opaque": opaque, "gid": glance_id(dom_index)})

    targets = [b for b in boxes if b["gid"] is not None]
    covered: set[int] = set()
    for t in targets:
        x, y, w, h = t["b"]
        for o in boxes:
            if o["order"] <= t["order"] or not o["opaque"]:
                continue
            ox, oy, ow, oh = o["b"]
            if ow < 50 or oh < 50:
                continue
            if ox <= x and oy <= y and ox + ow >= x + w and oy + oh >= y + h:
                covered.add(t["gid"])
                break
    return covered


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
                elements = page.evaluate(TAG_JS)
                session = ctx.new_cdp_session(page)
                snapshot = session.send("DOMSnapshot.captureSnapshot", {
                    "computedStyles": ["opacity", "background-color"],
                    "includePaintOrder": True,
                    "includeDOMRects": True,
                })
                paint = covered_by_paint(snapshot)
                hit = {e["id"] for e in elements if e["hit_covered"]}
                onscreen = {e["id"] for e in elements if e["hit_covered"] is not None}
                paint &= onscreen

                both = len(paint & hit)
                row = {
                    "elements_onscreen": len(onscreen),
                    "hit_covered": len(hit),
                    "paint_covered": len(paint),
                    "agree": both,
                    "missed_by_paint": len(hit - paint),
                    "paint_false_positive": len(paint - hit),
                    "recall_of_paint": round(both / len(hit), 3) if hit else None,
                }
                rows[name] = row
                print(f'{name:<12} onscreen={row["elements_onscreen"]:<4} '
                      f'hit={row["hit_covered"]:<4} paint={row["paint_covered"]:<4} '
                      f'missed_by_paint={row["missed_by_paint"]:<4} '
                      f'false_pos={row["paint_false_positive"]:<4} '
                      f'recall={row["recall_of_paint"]}')
            except Exception as exc:
                rows[name] = {"error": f"{type(exc).__name__}: {exc}"}
                print(f"{name:<12} ERROR {type(exc).__name__}: {exc}")
            finally:
                ctx.close()
        browser.close()
    ok = [r for r in rows.values() if "error" not in r and r["hit_covered"]]
    if ok:
        rows["_summary"] = {
            "sites": len(ok),
            "total_hit_covered": sum(r["hit_covered"] for r in ok),
            "total_missed_by_paint": sum(r["missed_by_paint"] for r in ok),
            "total_paint_false_positive": sum(r["paint_false_positive"] for r in ok),
        }
        print("\n", rows["_summary"])
    Path("data/paint_vs_hit.json").write_text(json.dumps(rows, indent=2),
                                              encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
