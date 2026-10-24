# Telling the agent about the overlay: the main result

**Date:** 2026-10-23 · `gpt-5-mini` · **17 paired tasks, 3 sites** · $0.097
**Raw:** `data/ab_overlay_n17.json` (also `ab_overlay_clean.json`, n=8)
**Code:** `scripts/ab_overlay.py`, `probe/structure.js`


> **Refined by a later experiment, not superseded.** Two further arms tested
> whether a loop detector substitutes for structure. One, given a hint naming
> overlays and listing dismiss verbs, matched the structured arm (13/17); the
> other, given a hint that only said the action had failed, scored 4/17 — no
> better than plain flat. So the gain below is real and is not reproducible by
> cheap failure detection alone. See `data/falsification_findings.md`.

## Headline, at n=17

| | A — flat | B — structured |
| --- | ---: | ---: |
| Tasks solved | **3 / 17** (17.6%) | **13 / 17** (76.5%) |
| Steps per task | 5.29 | **3.41** |
| Observation tokens | 2,041 | **2,023** (−0.9%) |

Paired: 2 solved by both, **11 only by B, 1 only by A**.
**McNemar exact two-sided p = 0.0064.**

+58.8 percentage points, at slightly fewer tokens.

### The independence caveat, stated plainly

That p-value treats 17 tasks as independent trials. They are not fully
independent: the tasks are spread over **three sites**, and tasks on one site
share the same overlay and the same dismiss button.

Read site by site the result is weaker and more honest:

| Site | Flat | Structured | Overlay obstructs target? |
| --- | ---: | ---: | --- |
| CNN (8 tasks) | 0/8 | **8/8** | yes |
| Le Monde (6 tasks) | 0/6 | **4/6** | yes |
| Al Jazeera (3 tasks) | 3/3 | 2/3 | no — control |

So: two sites where the overlay obstructs the target show a large effect, and
the control site where it does not obstruct shows none (slightly negative).
That is the right pattern, but it is **2 sites with replication, not 17
independent observations**, and the paper must say so. More sites is the single
biggest improvement available.

### Where the structured arm lost

`aljazeera-economy` is the one task the flat arm won:

```
A: click(12) -> click(13) -> click(27) -> click(30)   PASS
B: click(220) -> click(13) × 5                        fail
```

Al Jazeera's dialog blocks 11 elements but not the navigation. The structured
arm was told about an overlay that was not in its way, spent a step dismissing
it, and then got stuck repeating a click. **Announcing an overlay that does not
obstruct the target can cost a step and mislead.** Worth fixing: report not
just that an overlay exists, but whether anything relevant is behind it.

---

## Original n=8 run

**Date:** 2026-10-23 · `gpt-5-mini` · 8 paired tasks, 3 sites · $0.038

Both arms received the same tasks, model, action space and 6-step budget. The
only difference was the observation:

- **A — flat** element list, what BrowserGym, Playwright MCP and CDP produce
- **B — structured**, the overlay announced and blocked elements collapsed to ids

Every task's target sits behind a consent dialog, so the overlay has to be
dealt with first. Sites are the three where the dismiss control is in the top
document; on BBC, the Guardian, Spiegel and the FT it is inside a cross-origin
iframe, where neither arm could click it.

## Result

| | A — flat | B — structured |
| --- | ---: | ---: |
| Tasks solved | **3 / 8** | **7 / 8** |
| Steps per task | 4.50 | **2.88** |
| Observation tokens | 2,000 | **1,974** |

Paired: 3 solved by both, **4 solved only by B, 0 only by A**.
McNemar exact two-sided **p = 0.125** — the direction is perfectly consistent
but eight tasks cannot reach p < 0.05. Reaching it needs roughly 16–20.

**The structured observation is 1.3% smaller.** Announcing the overlay and
collapsing 36 blocked elements into a list of ids costs less than listing them
in full. The accuracy gain here is free.

## The mechanism, directly observed

The same task, both arms:

```
B_structured:  click('680') -> click(680)     dismisses the consent dialog
               click('27')  -> click(27)      then the target.  PASS in 2 steps

A_flat:        click('27') -> err:TimeoutError
               click('27') -> err:TimeoutError
               click('27') -> err:TimeoutError      ... six identical attempts.  FAIL
```

This is the part worth noticing: **arm A picks the right element immediately.**
Element 27 is the correct target in both arms. Its selection is not the
problem. The click times out because the overlay intercepts it, nothing in the
observation says an overlay exists, and so the agent repeats the identical
action until its budget is gone.

The flat observation does not make the agent choose wrongly. It makes the
agent unable to understand why the right choice fails.

## Per task

| Task | A | blocked | A steps | B | blocked | B steps |
| --- | --- | ---: | ---: | --- | ---: | ---: |
| cnn-world | fail | 36 | 6 | **PASS** | 36 | 4 |
| cnn-politics | fail | 34 | 6 | **PASS** | 36 | 2 |
| cnn-business | fail | 34 | 6 | **PASS** | 36 | 2 |
| lemonde-international | fail | 25 | 6 | **PASS** | 25 | 2 |
| lemonde-economie | fail | 25 | 6 | fail | 25 | 6 |
| aljazeera-news | PASS | 11 | 1 | PASS | 11 | 2 |
| aljazeera-sport | PASS | 11 | 1 | PASS | 11 | 2 |
| aljazeera-economy | PASS | 11 | 4 | PASS | 11 | 3 |

Al Jazeera's dialog blocks 11 elements but not the navigation links, so both
arms reach the target and the overlay is irrelevant — correctly, no difference.
The effect appears exactly where blocking actually obstructs the target.

## A confound caught and removed

The first run produced the same +50 points, and part of it was an artifact.
`clear_cookies()` does not clear `localStorage`, where consent state lives. Arm
B dismissed CNN's dialog on its first task, and its next two CNN tasks then
loaded with **no overlay at all** — they inherited the benefit rather than
testing it.

The rerun gives each task a fresh browser context and measures `blocked` in
both arms, so a contaminated task is visible instead of invisible. Every CNN
and Le Monde task now shows the overlay present in both arms, and the result
replicated unchanged.

## What this supports, and what it does not

**Supports:** labelling occlusion in the observation lets an agent recover from
blocked pages it otherwise cannot, at no token cost. Four tasks flipped, none
regressed, and the failure mechanism is visible in the traces rather than
inferred.

**Does not support, yet:** statistical significance. p = 0.125 on eight tasks.
The effect is large and one-directional, which is encouraging, but it needs
roughly twice the tasks before it can be stated as significant.

**Not tested:** the cross-frame case. On BBC, the Guardian, Spiegel and the FT
the dismiss control lives in an iframe that a top-document observation cannot
reach at all. Structure tells the agent it is blocked; it still cannot act.
That needs cross-frame traversal, which Playwright supports and an
accessibility snapshot does not.

## Why this matters relative to everything else measured here

Earlier attempts in this project tried to make the observation *smaller* and
produced nothing — 0.5% token change, no accuracy change. This makes the
observation *more informative* at the same size, and produces a large effect.

The lesson is specific: the opportunity is not compression. It is representing
things the format currently cannot express at all — in this case, that the
page is covered.
