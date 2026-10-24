# Paint order misses exactly the overlays that matter

**Date:** 2026-10-23 · 8 live sites · no model called, nothing spent
**Raw:** `data/paint_vs_hit.json`, `data/baseline_audit.json`, `data/blocker_kind.json`
**Code:** `scripts/paint_vs_hit.py`, `scripts/baseline_audit.py`

## Why this had to be measured

Two earlier claims in this project were made against baselines nobody runs,
and both were wrong:

- "The accessibility tree misses the consent button on 4 of 4 sites" was true
  of `Accessibility.getFullAXTree` on the **top document only**. BrowserGym's
  `extract_merged_axtree` enumerates frames via `Page.getFrameTree` and
  requests a tree per `frameId`. Measured that way it finds the button on
  **4 of 4**. Claim withdrawn.
- "No deployed system represents occlusion" was wrong. Browser Use's
  `dom/serializer/paint_order.py` sets `node.ignored_by_paint_order = True`
  when a node's rect lies inside the union of rects painted above it, using
  CDP paint order, skipping contributors below 0.8 opacity. That ships.

So the real question is not whether occlusion is detected anywhere, but
whether the deployed method detects the cases that matter. It does not.

## Result

Paint order compared against `elementFromPoint`, which is ground truth for
"would a click land here". Paint order read from
`DOMSnapshot.captureSnapshot` with `includePaintOrder: true`, not approximated
from document order.

| Site | Covered (hit test) | Found by paint order | Missed | Recall | What blocks |
| --- | ---: | ---: | ---: | ---: | --- |
| BBC | 33 | 0 | **33** | **0.00** | cross-origin iframe |
| Financial Times | 30 | 0 | **30** | **0.00** | cross-origin iframe |
| Spiegel | 27 | 1 | **26** | **0.04** | cross-origin iframe |
| Sky Sports | 42 | 7 | **35** | **0.17** | cross-origin iframe |
| Le Monde | 25 | 21 | 4 | 0.84 | plain DOM |
| Euronews | 141 | 127 | 14 | 0.90 | plain DOM |
| CNN | 36 | 34 | 2 | 0.94 | plain DOM |
| Irish Times | 39 | 37 | 2 | 0.95 | plain DOM |

**146 of 373 covered elements (39%) are missed.** Zero false positives — the
rule is conservative, not noisy.

## The mechanism, and it is exact

The split is not gradual. It is 4 sites near zero and 4 sites near one, and
`data/blocker_kind.json` says why, with no exceptions in 8 sites:

```
bbc         {'blocker_holds_cross_origin_iframe': 33}
spiegel     {'blocker_holds_cross_origin_iframe': 27}
ft          {'blocker_holds_cross_origin_iframe': 30}
skysports   {'blocker_holds_cross_origin_iframe': 42}
cnn         {'blocker_is_plain_dom': 35}
lemonde     {'blocker_is_plain_dom': 25}
euronews    {'blocker_is_plain_dom': 141}
irishtimes  {'blocker_is_plain_dom': 39}
```

**Paint order is computed per document.** When the consent dialog renders its
panel inside a cross-origin iframe, the opaque box that covers the page is not
a node in the top document's snapshot. Nothing in that document is painted
above the links, so nothing is marked covered — while the compositor puts the
frame on top and the click goes to the frame.

A hit test does not have this problem, because `elementFromPoint` reports what
the compositor actually hit, across documents. It costs 3 ms.

This is the failure mode the rule is least able to tolerate: consent dialogs
are the most common full-page overlay on the web, and the major consent
platforms render them in an iframe.

## The accessibility tree cannot express it at all

Chrome's protocol defines property names that would carry this —
`inertSubtree`, `inertElement`, `activeModalDialog`, `activeAriaModalDialog`,
`ariaHiddenSubtree`, `notRendered`, `notVisible`. Across all 8 sites, with
25–141 elements covered on each, `getFullAXTree` emitted **none of them**, on
any node, in any frame.

| | Dismiss button present | Occlusion expressed |
| --- | :-: | :-: |
| `getFullAXTree`, top document | 0 / 4 | 0 / 8 |
| `getFullAXTree`, merged per frame (BrowserGym) | **4 / 4** | 0 / 8 |
| Playwright `aria_snapshot(mode="ai")` | **4 / 4** | 0 / 8 |
| Browser Use paint order | n/a | partial — 0.61 recall, 0.00–0.17 on frame overlays |
| Hit test (this work) | 4 / 4 | 8 / 8 |

## What this means for the paper

The contribution is no longer "nobody detects occlusion". It is sharper:

1. Occlusion is absent from both tree serialisations agents are given, and the
   protocol's own vocabulary for it goes unused.
2. The one deployed detector uses a per-document method that is blind to
   cross-origin overlays — 39% of covered elements across 8 sites, and
   essentially 100% on the four sites where the overlay is an iframe.
3. A hit test closes both gaps for 3 ms, and labelling the result changes agent
   behaviour measurably (`data/falsification_findings.md`).
