"""Two failure-driven additions, measured before being believed.

Both come straight out of `data/residual_failures.md`:

1. On Repubblica the observation correctly listed nine overlay controls and
   the agent clicked "pannello delle preferenze pubblicitarie" — a second
   settings panel — instead of ACCETTA. Announcing the overlay is necessary
   and not sufficient; the agent also has to pick the control that leaves it.
   `rankDismiss` is tested here against the known dismiss text per site, in
   two modes: `generic`, which uses only element type, autofocus and geometry,
   and `lexical`, which adds accept/trap word lists. Reporting both separately
   matters, because a word list is domain knowledge about consent dialogs and
   the generic signals are not.

2. The DOM says which elements are navigation, main content, header or
   footer, and no agent observation carries it. Measured here as coverage (how
   many elements land in a named region) and as collision resolution (how many
   duplicate accessible names a region separates).
"""
from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import sync_playwright  # noqa: E402

from eval.overlay_sites import SITES, tolerant_goto  # noqa: E402

STRUCTURE_JS = Path("probe/structure.js").read_text(encoding="utf-8")

LANDMARK_JS = """() => {
  const s = window.__glanceStructure;
  const plain = s.build({ occlusion: true });
  const grouped = s.build({ occlusion: true, landmarks: true });
  // Region coverage and whether a region separates duplicate names.
  const sel = 'a[href],button,input,select,textarea,summary,[role=button],[role=link]';
  const rows = [];
  for (const el of document.querySelectorAll(sel)) {
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) continue;
    let n = el, mark = 'body', d = 0;
    while (n && n !== document.body && d++ < 30) {
      const tag = (n.tagName || '').toLowerCase();
      const role = n.getAttribute ? (n.getAttribute('role') || '') : '';
      if (tag === 'nav' || role === 'navigation') { mark = 'navigation'; break; }
      if (tag === 'main' || role === 'main') { mark = 'main'; break; }
      if (tag === 'header' || role === 'banner') { mark = 'header'; break; }
      if (tag === 'footer' || role === 'contentinfo') { mark = 'footer'; break; }
      if (tag === 'aside' || role === 'complementary') { mark = 'sidebar'; break; }
      n = n.parentElement;
    }
    const name = (el.getAttribute('aria-label') || el.innerText || '')
      .replace(/\\s+/g, ' ').trim().slice(0, 40);
    rows.push({ name, mark });
  }
  const byName = {};
  for (const r of rows) if (r.name) (byName[r.name] = byName[r.name] || []).push(r);
  let colliding = 0, separated = 0;
  for (const k of Object.keys(byName)) {
    const g = byName[k];
    if (g.length < 2) continue;
    colliding += g.length;
    if (new Set(g.map(x => x.mark)).size > 1) separated += g.length;
  }
  const named = rows.filter(r => r.mark !== 'body').length;
  return {
    elements: rows.length,
    in_named_region: named,
    pct_in_named_region: rows.length ? +(100 * named / rows.length).toFixed(1) : 0,
    colliding_names: colliding,
    collisions_region_separates: separated,
    tokens_plain: Math.round(plain.text.length / 4),
    tokens_grouped: Math.round(grouped.text.length / 4),
  };
}"""


def rank_probe(page, mode: str | None) -> dict:
    return page.evaluate(
        """(mode) => {
             const b = window.__glanceStructure.build(
               mode ? { occlusion: true, rank: mode } : { occlusion: true });
             const lines = b.text.split('\\n');
             const marked = lines.find(l => l.includes('most likely closes the overlay'));
             const overlay = [];
             let inside = false;
             for (const l of lines) {
               if (l.startsWith('Actionable in the overlay:')) { inside = true; continue; }
               if (inside) { if (!l.startsWith('  ')) break; overlay.push(l.trim()); }
             }
             return { picked: marked ? marked.trim() : null,
                      overlay_controls: overlay.length,
                      overlay: overlay };
           }""", mode)


def main() -> int:
    rows: dict[str, dict] = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        for site in SITES:
            ctx = browser.new_context(viewport={"width": 1280, "height": 720},
                                      locale="en-GB")
            page = ctx.new_page()
            try:
                tolerant_goto(page, site.url)
                page.evaluate(STRUCTURE_JS)
                land = page.evaluate(LANDMARK_JS)
                generic = rank_probe(page, "generic")
                lexical = rank_probe(page, "lexical")

                want = site.dismiss_example.lower()
                def hit(res):
                    return bool(res["picked"]) and want in res["picked"].lower()

                row = {
                    "overlay_controls": generic["overlay_controls"],
                    "expected_dismiss": site.dismiss_example,
                    "generic_picked": generic["picked"],
                    "generic_correct": hit(generic),
                    "lexical_picked": lexical["picked"],
                    "lexical_correct": hit(lexical),
                    **land,
                }
                rows[site.key] = row
                print(f'{site.key:<14} ctrls={row["overlay_controls"]:<3} '
                      f'generic={"OK " if row["generic_correct"] else "no "}'
                      f'lexical={"OK " if row["lexical_correct"] else "no "} '
                      f'region={row["pct_in_named_region"]:>5}% '
                      f'collide={row["colliding_names"]:<4} '
                      f'sep={row["collisions_region_separates"]:<4} '
                      f'tok {row["tokens_plain"]}->{row["tokens_grouped"]}')
            except Exception as exc:
                rows[site.key] = {"error": f"{type(exc).__name__}: {exc}"}
                print(f"{site.key:<14} ERROR {type(exc).__name__}")
            finally:
                ctx.close()
        browser.close()

    ok = [r for r in rows.values() if "error" not in r]
    withoverlay = [r for r in ok if r["overlay_controls"]]
    if ok:
        rows["_summary"] = {
            "sites": len(ok),
            "sites_with_overlay_controls": len(withoverlay),
            "generic_correct": sum(1 for r in withoverlay if r["generic_correct"]),
            "lexical_correct": sum(1 for r in withoverlay if r["lexical_correct"]),
            "median_pct_in_named_region": round(
                statistics.median(r["pct_in_named_region"] for r in ok), 1),
            "total_colliding_names": sum(r["colliding_names"] for r in ok),
            "total_collisions_region_separates": sum(
                r["collisions_region_separates"] for r in ok),
            "median_token_change_pct": round(statistics.median(
                100 * (r["tokens_grouped"] - r["tokens_plain"]) / max(r["tokens_plain"], 1)
                for r in ok), 2),
        }
        print("\n", json.dumps(rows["_summary"], indent=2))
    Path("data/rank_landmarks.json").write_text(json.dumps(rows, indent=2),
                                                encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
