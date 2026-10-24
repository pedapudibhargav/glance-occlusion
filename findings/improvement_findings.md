# Learning from the failures: what four fixes actually bought

**Date:** 2026-10-23 · `gpt-5-mini` · 26 tasks, 11 sites
**Raw:** `data/ab_overlay_improved.json`, `data/ab_overlay_b2fixed.json`
**Code:** `probe/structure.js` v1.4.0, arms `B2_structured_improved`,
`B3_structured_landmarks`

Each fix came from a named failure in `data/residual_failures.md` rather than
from a guess about what might help.

| Failure | Fix |
| --- | --- |
| Mirror: the observation listed `[345] button "ACCEPT"` and the agent never touched it, spending six steps on blocked page elements | An imperative first line: `FIRST: activate [345] button "ACCEPT" to clear the overlay, then act on the page.` |
| Repubblica: nine overlay controls, the agent chose "pannello delle preferenze pubblicitarie" over ACCETTA | Rank the overlay controls and mark one |
| RTÉ, Al Jazeera: flat solved in one step, structured took four, because the dialog covers part of the page but not the navigation | Parameter-free obstruction test — does the overlay cover anything in a `navigation` or `main` landmark — and a single line with no instruction when it does not |
| BBC, Spiegel, FT, Sky Sports: the overlay is real and nothing in the document closes it | Say so, rather than listing nothing |

## Result

| Arm | Solved | Steps | Observation tokens |
| --- | ---: | ---: | ---: |
| A — flat | 4 / 26 | 5.23 | 3,008 |
| B — structured | 16 / 26 | 4.19 | 2,725 |
| B2 — + four fixes | 18 / 26 | 3.73 | 2,753 |
| **B2 — wording corrected** | **19 / 26** | **3.38** | 2,887 |
| B3 — + landmarks and href | 19 / 26 | 3.69 | **6,378** |

Against flat: **+15/−0, p = 0.00006**, and by site **7 better, 0 worse,
p = 0.0156** — the strongest site-level result in the project.

Against plain structure: **+4/−1, p = 0.375. Not significant.** The four fixes
move 16 to 19 and cut steps 19%, and at n = 26 that is inside noise. Stated
plainly: the failure analysis improved the system, and the improvement is not
yet demonstrated.

## The regression was mine, and it is the most useful thing here

B2's net +2 was a +2 fix and a −2 regression. The regression came from the
fourth fix.

Euronews' blocker is an `ADVERTISEMENT` iframe, not a consent dialog, and the
line read:

> No control in this document closes it — the overlay's own controls are in an
> iframe and are not listed here.

```
B  (without the line):  click('8') -> click('244') -> click('244') -> click('505')   PASS
B2 (with it):           click('8') -> click('243') × 5                                fail
```

The agent read a statement of absence as a statement of impossibility. It
stopped exploring and repeated one element until its budget ran out — the same
behaviour the whole project exists to remove, caused by the observation this
time.

Rewritten to state the same fact without closing the door ("its controls are
not listed here; the page behind it may still respond"), one of the two
Euronews tasks recovers.

**This is the mirror image of the paper's main claim.** The channel that
rescues an agent by telling it what is covered can disable it by telling it
nothing can be done. An observation format is not a neutral description; its
wording changes whether the agent keeps trying. That belongs in the paper as a
finding, not as an implementation note.

## B3 is cut

Landmark grouping plus `href` on every link reaches the same 19/26 at **2.3×
the tokens** (6,378 against 2,753). Landmarks are nearly free (+0.5% measured
separately); `href` is not. They have to be separated, and a destination only
earns its tokens on elements whose names actually collide.

## Still unsolved after all of this

- **Repubblica.** The ranker now correctly emits
  `FIRST: activate [610] button "ACCETTA"`, and the task still fails. Element
  ids shift between page loads, so the directive may name a stale id.
- **Irish Times, Standaard, lemonde-economie.** Failures in every arm, and
  `data/residual_failures.md` shows at least one of them is not an occlusion
  failure at all.
- **Euronews-business.** Still fails even with corrected wording.
