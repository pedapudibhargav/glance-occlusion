# Landmarks and dismiss-ranking: two failure-driven additions

**Date:** 2026-10-23 · 11 sites · no model called, nothing spent
**Raw:** `data/rank_landmarks.json` · **Code:** `probe/structure.js`,
`scripts/measure_rank_landmarks.py`

Both come out of `data/residual_failures.md`. Neither is validated against an
agent yet; what follows is what they extract and what it costs.

## 1. Region grouping

The DOM says which part of the page an element is in — `<nav>`, `<main>`,
`<header>`, `<footer>`, `<aside>` and their ARIA equivalents — and no agent
observation carries it. Grouping the element list by nearest enclosing
landmark:

| | Median |
| --- | ---: |
| Elements inside a named region | **98.2%** |
| Token cost of grouping | **+0.5%** |

Across 11 sites, **482 elements share an accessible name with another
element, and the region separates 180 of them — 37%.**

For comparison, the earlier structural-context approach
(`data/ambiguity_findings.md`) resolved 58% of collisions at +4.4% tokens.
Regions are weaker and nearly free. The two are not alternatives: a region
label plus a row or column header would resolve more than either.

One site breaks the pattern: ABC has only 40.9% of elements inside a named
region, so its markup provides almost no landmarks to group by. The method
degrades to the flat list there rather than failing.

### This contradicts recent prior art, deliberately

> **Correction.** This section was written around a quote attributed to
> Region4Web's decomposition prompt — that ARIA landmarks "group content by
> page position, not by purpose". A citation check could not find that text in
> the paper's extracted body; it appears only inside a figure. **The
> "deliberate disagreement with prior art" framing is withdrawn** until the
> figure is read directly. What is verified is only that Region4Web reduces
> observation tokens from 6,437 to 3,671 (-43%) and improves success by 2.3
> percentage points, with an approximately 536K-parameter decomposition model
> and a fine-tuned Qwen3-0.6B.

Region4Web (arXiv 2605.07134) builds purpose-labelled regions and reports
-43% observation tokens with +2.3 percentage points of success. Whether it
argues against ARIA landmarks specifically is unconfirmed.

That argument is about **compression** — using regions to send less. It is
probably right about that, and this project already measured compression as
empty. The claim here is different: landmarks are useful for
**disambiguation**, telling two identically-named links apart, which position
is exactly the right signal for. "World news" in the primary navigation and
"World news" in the footer sitemap differ by position and by nothing else.

No published work groups an agent observation by HTML landmark and ablates it.

## 2. Ranking the control that dismisses an overlay

On Repubblica the observation listed nine overlay controls, correctly, and the
agent clicked "pannello delle preferenze pubblicitarie" — a second settings
panel — instead of ACCETTA. So the overlay section needs a ranking, and the
ranking is implemented in two modes, kept separate on purpose:

- **generic** — element type (a button, not a link; links in consent dialogs
  go to policies), `autofocus`, a declared default action, and area. No
  vocabulary.
- **lexical** — adds an accept-word list (accept/agree/accetta/akkoord/…) and
  a trap-word list (preferences, manage, subscribe, policy, …).

Measured against each site's known dismiss text, on the 6 sites where an
overlay was present at probe time:

| Mode | Correct |
| --- | ---: |
| generic | **2 / 6** |
| lexical | **5 / 6** |

**The word list does the work, and that has to be said plainly.** Generic
structural signals pick the dismissing control on a third of sites. This is
the same weakness this project criticised in its own arm C: a hand-written
vocabulary that happens to fit consent dialogs is domain knowledge, not
structure. The difference is that it is static and task-independent rather
than injected when the failure appears — but it is still a word list, and the
paper should present it as an engineering default with a measured
contribution, not as a structural finding.

The one lexical miss is RTÉ, which lists 6 controls.

## Limitation that affects both measurements

Five of 11 sites showed **no overlay** at probe time (Euronews, Irish Times,
La Vanguardia, ABC, De Standaard), minutes after the same URLs reported 17–182
blocked elements during the A/B run. Consent dialogs vary with geography, A/B
splits and timing. Overlay presence is therefore measured per run and is not
stable across runs, which is why the ranking test covers 6 sites rather than
11.
