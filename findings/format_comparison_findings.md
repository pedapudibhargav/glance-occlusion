# Retraction, and what the formats actually contain

**Date:** 2026-10-23 · 6 live sites · no model called, nothing spent
**Raw:** `data/format_comparison.json` · **Code:** `scripts/format_comparison.py`

## The retraction

`data/layering_findings.md` claimed that cross-frame traversal "cleared four
sites nothing could previously reach", on the grounds that a consent button
inside a cross-origin iframe is invisible to any page observation. The first
version of the check appeared to confirm it: Playwright's `aria_snapshot()` on
BBC contains no "Accept and Continue".

**That check used the wrong mode and the claim is wrong.**

Playwright's snapshot takes a `mode` argument. The default is a plain ARIA
snapshot of one document. `mode="ai"` — the mode Playwright MCP serves to
agents — assigns refs, prefixes them per frame, and since
[playwright#35396](https://github.com/microsoft/playwright/pull/35396) descends
into iframes. On BBC it yields, verbatim:

```
- button "Accept and Continue" [ref=f4e22] [cursor=pointer]
```

That is the same button, with the same frame-qualified reference scheme — down
to the same frame index — that `probe/frames.py` reimplemented. **Playwright
MCP already solves the cross-frame case.** The traversal work in this project
is a reimplementation of existing functionality, not a contribution, and the
"4 sites cleared" table is withdrawn as a novelty claim.

I found this by checking an assumption that was load-bearing for the strongest
remaining part of the paper. It should have been checked before the code was
written.

## What the three formats contain, measured

- **AX** — CDP `Accessibility.getFullAXTree` on the top document. The route
  BrowserGym and most accessibility-tree agent work uses.
- **PW** — `page.aria_snapshot(mode="ai")`. What Playwright MCP serves.
- **GL** — `probe/structure.js`, this project's structured observation.

| Site | Blocked elements | Dismiss button in AX | in PW | Occlusion stated: AX / PW / GL |
| --- | ---: | :-: | :-: | :-: |
| BBC | 33 | ✗ | **✓** | ✗ / ✗ / **✓** |
| Spiegel | 27 | ✗ | **✓** | ✗ / ✗ / **✓** |
| Financial Times | 32 | ✗ | **✓** | ✗ / ✗ / **✓** |
| Sky Sports | 42 | ✗ | **✓** | ✗ / ✗ / **✓** |
| CNN | 38 | — | — | ✗ / ✗ / **✓** |
| Le Monde | 25 | — | — | ✗ / ✗ / **✓** |

Two separate results, and they point in opposite directions.

### 1. Frame blindness is an AX-tree problem, not a web-agent problem

`Accessibility.getFullAXTree` returned the dismiss button on **0 of 4** sites;
the Playwright AI snapshot returned it on **4 of 4**. The hole is real, but it
belongs to one observation route — the one a large part of the published agent
literature is built on — and the fix already exists in a widely used tool. The
honest form of this result is a **format comparison**, not a new technique.

### 2. Occlusion is unrepresented in *both* mainstream formats

**0 of 6** for the AX tree. **0 of 6** for Playwright's AI snapshot.

Playwright appeared to score 1/6 on CNN. That was a false positive: the word
"interceptor" in a headline about a Danish F-16. Checked and discarded.

So on all six pages, between 25 and 42 elements are covered by a dialog and
cannot be clicked, and **neither format says so**. Both list the covered
elements exactly as they list reachable ones. An agent receiving either
observation has no way to distinguish an element it can click from one it
cannot, which is precisely the condition that produces six identical
timing-out clicks in `data/overlay_ab_findings.md`.

This is what survives as a gap in the state of the art, and it is narrower and
better evidenced than what was claimed before: **not "agents cannot reach the
button" — they can — but "agents are not told the page is covered".**
