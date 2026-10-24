# The falsification test: is structure necessary, or just a loop detector?

**Date:** 2026-10-23 · `gpt-5-mini` · 17 paired tasks × 4 arms = 68 runs · $0.251
**Raw:** `data/ab_overlay_3arm.json`, `data/ab_overlay_armD.json`
**Code:** `scripts/ab_overlay.py`

This experiment was designed to break the project's main claim. For one arm it
looked as though it had, and I wrote that up. The fourth arm showed the third
had been handed the answer.

## The claim under test

`data/overlay_ab_findings.md` reported 3/17 → 13/17 from announcing overlays in
the observation, and read that as *structure lets an agent recover from blocked
pages it otherwise cannot*. But the traces showed the flat arm emitting the
same timing-out click six times in a row. That repetition is itself a free
signal. If simply **telling the agent its action repeated** closes the gap, the
fix is a control-flow change — a loop detector — not an observation change, and
the contribution collapses.

So two more arms were run, both flat observation plus a hint injected the
moment the agent repeats an action or receives an error. They differ only in
what the hint says.

## Result

| | A — flat | B — structured | C — flat + **specific** hint | D — flat + **generic** hint |
| --- | ---: | ---: | ---: | ---: |
| Tasks solved | 3 / 17 | 12 / 17 | **13 / 17** | 4 / 17 |
| Steps per task | 5.29 | **3.41** | 4.12 | 5.24 |
| Observation tokens | 2,042 | **1,913** | 1,936 | 2,059 |

Paired, against flat: B +10/−1 (p = 0.0117), C +10/−0 (p = 0.0020),
**D +1/−0 (p = 1.00)**.

B vs C: +0/−1, p = 1.00.  **B vs D: +9/−1, p = 0.0215.**

## Reading it in two steps, because the first reading was wrong

### Step 1 — the loop detector appeared to win

Arm C matched arm B, and I wrote up the conclusion that structure is not
necessary for success, only two steps faster. That conclusion was premature,
and the reason is in arm C's hint:

> "...A dialog or overlay is probably covering the page and intercepting
> clicks. Find and activate the control that dismisses it (for example Accept,
> Agree, Allow or Continue)..."

It names the failure mode and lists the remedy. It is an oracle, hand-written
for the exact experiment it was being evaluated on.

### Step 2 — remove the oracle and it collapses

Arm D runs the identical detector, firing on the identical condition, with the
hint reduced to what a general agent could actually be given:

> "...your last action was repeated and had no effect. Something is preventing
> it from working. Try a different approach."

**4 / 17.** Indistinguishable from plain flat (3/17, p = 1.00), at slightly
*more* tokens and the same number of steps.

The traces show the detector working perfectly and helping not at all:

```
D_flat_plus_generic_hint, cnn-politics

click('27')  -> err:TimeoutError      the correct target
click('27')  -> err:TimeoutError      repeat detected, hint fires
click('525') -> err:TimeoutError      varies, as instructed
click('27')  -> err:TimeoutError
click('21')  -> err:TimeoutError
click('593') -> err:TimeoutError      budget gone
```

The agent does exactly what it was told. It stops repeating and starts varying.
Every variation fails too, because every element it can see is behind the same
overlay, and nothing in its observation mentions an overlay. Varying blindly
over a covered page is not a recovery strategy.

## What this establishes

**Knowing that an action failed is not the useful information. Knowing what
kind of failure it is, is.**

- Detect the loop, say nothing specific → **4/17**. No better than silence.
- Detect the loop, supply the right diagnosis by hand → 13/17.
- Put the diagnosis in the observation, no detector, no hand-written hint,
  no prior knowledge of the failure mode → **12/17**, and two steps faster.

Arms C and B land in the same place because they deliver the same fact. The
difference is that arm C's fact was written by someone who already knew the
answer, and only covers this one failure mode — a different cause of a stuck
page would need a different hint, selected by a mechanism that does not exist.
Arm B derives it from the DOM in 3 ms, on any page, without being told what is
wrong.

So the falsification test failed to falsify, but it did something more useful:
it isolated *which* information carries the effect. Not "you are looping".
**"You are covered."**

## What structure costs, restated

| | vs flat | vs generic detector |
| --- | --- | --- |
| Success | 3/17 → 12/17, p = 0.0117 | 4/17 → 12/17, p = 0.0215 |
| Steps | 5.29 → 3.41 | 5.24 → 3.41 |
| Observation tokens | 2,042 → 1,913 (−6.3%) | 2,059 → 1,913 (−7.1%) |

Cheaper on every axis measured.

## What is untouched either way

The cross-frame question, which turned out not to be a contribution at all —
Playwright's `mode="ai"` snapshot already descends into cross-origin iframes.
See `data/format_comparison_findings.md`.

## A separate wall, shared by all four arms

Four Le Monde tasks failed in every arm. The structured arm dismisses the
dialog successfully and then wanders (`click(3)`, `click(8)`, `click(4)`,
`type('economie')`), so this is not an occlusion failure — it is a navigation
failure on a site whose section links the agent cannot find. Reporting it
because it bounds the result: **the overlay work fixes overlay failures and
nothing else.**
