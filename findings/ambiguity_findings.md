# Level 2: selective disambiguation — first measurement

**Date:** 2026-10-23 · 10 live sites · no model called, nothing spent
**Raw:** `data/ambiguity.json` · **Code:** `probe/enrich.js`, `scripts/measure_ambiguity.py`

## The question

An agent is handed a list of elements and must pick one. The accessibility
tree names each element by itself. When several elements share a name — five
buttons called "Edit" — the list is correct and useless, and the agent is
guessing.

Two existing answers are both bad: ship the whole DOM (19,958 tokens median,
16× the tree) to recover context needed for a handful of elements, or ship the
bare tree and accept the guessing.

The proposal measured here: **enrich only what is ambiguous.** Find the
elements whose names collide, attach the shortest piece of surrounding DOM
context that separates them, and leave every uniquely-named element untouched.

## Results

| Measure | Median across 10 sites |
| --- | ---: |
| Interactive elements per page | 210 |
| **Ambiguity rate** — elements sharing a name with another | **18.8%** |
| **Colliding groups resolved** by structural context | **58%** |
| **Token overhead of selective enrichment** | **+4.4%** |

Per site, ambiguity is substantial wherever the page is substantial:

| Site | Elements | Ambiguous | Resolved | Token cost |
| --- | ---: | ---: | ---: | ---: |
| Wikipedia | 910 | 44.0% | 82% | +35.0% |
| Hacker News | 231 | 29.9% | 100% | +95.8% |
| arXiv listing | 566 | 29.7% | 58% | +5.9% |
| GitHub repo | 362 | 27.1% | 77% | +12.7% |
| Playwright docs | 170 | 20.6% | 33% | +2.8% |
| BBC News | 189 | 16.9% | 6% | +1.7% |
| MDN | 253 | 16.2% | 50% | +10.6% |
| gov.uk | 58 | 0.0% | — | 0% |

Three sites returned 2–58 elements (w3.org, pypi, gov.uk). Two of those are
almost certainly a cookie wall or a render failure rather than genuinely tiny
pages, and they drag the median down; they are left in rather than quietly
dropped.

## The cost comparison that matters

| Observation | Median tokens | Relative |
| --- | ---: | ---: |
| Plain accessibility tree | 1,224 | 1.0× |
| **Selective enrichment** | **1,346** | **1.04×** |
| Enrich every element | 1,897 | 1.55× |
| Full DOM | 19,958 | 16.3× |

Enriching everything costs 13× more than enriching only the collisions, for
context that uniquely-named elements did not need. That gap is the whole
argument for selectivity.

## Which context source does the work

Across all colliding groups on all sites:

| Source | Groups separated |
| --- | ---: |
| Nearest preceding heading | 82 |
| Containing row (`tr`, `li`, `[role=row]`) | 27 |
| Section label or its heading | 18 |
| Table column header | 3 |
| Fieldset legend | 0 |
| **No source separated them** | **58** |

Headings carry most of it, rows next. Fieldset legends never fired, which
says something about how rarely they are used correctly rather than about the
idea.

The 58 unresolved groups are the honest limit: roughly 42% of collisions are
between elements that structural context cannot tell apart — repeated
navigation links in a header and a footer, for instance, where the two really
are interchangeable and picking either may be fine.

## What this does and does not show

**Shown:** real pages are substantially ambiguous by accessible name alone,
and most of that ambiguity is resolvable from DOM structure the page already
contains, at a few per cent of extra tokens rather than 16×.

**Not shown:** that resolving it makes an agent more accurate. Ambiguity is a
plausible cause of wrong-element selection — and element selection is the
documented bottleneck on Mind2Web — but this measurement counts collisions, it
does not watch an agent fail on them. That is the next experiment and it needs
tasks whose failure mode is specifically ambiguity.

**Caveat on the token numbers:** these are characters/4 estimates. tiktoken
cannot fetch its encoding file on this network.
