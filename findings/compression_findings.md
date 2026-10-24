# The compression null is refuted: filtering is not neutral, it is harmful

**Date:** 2026-10-24 · `gpt-5-mini` · **40 paired tasks, ComponentBench** · $0.146
**Raw:** `data/ab_compression_n40.json`, `data/raw/ab/B_verified_obs_stats.jsonl`
**Code:** `scripts/ab_observation.py`

## What was claimed before

`PAPER.md` carried compression as a documented negative result: "a verified,
filtered observation is the same size as a standard compact snapshot (3,389 vs
3,406 tokens) and produces the same task success (10/14 vs 10/14)". Registry
row A27 flagged it as the weakest thing in the paper — n = 14, one run, no
significance test, and `summarise_arm` recorded only aggregate counts so no
paired test was even constructible.

## What 40 paired tasks say

| | A — standard AX observation | B — verified / filtered |
| --- | ---: | ---: |
| Solved | **36 / 40** (90%) | **30 / 40** (75%) |
| Steps per task | 1.98 | 2.55 |
| Observation chars, median | 1,607 | **197.5** |

Paired on all 40 tasks: **0 gained, 6 lost. McNemar exact two-sided
p = 0.03125.**

Both arms are confirmed probed (`A_standard.bootstrap_ok` and
`B_verified.bootstrap_ok` both present — see registry A30 for why that check
now exists).

**Both halves of the old claim were wrong.** Filtering does not leave the
observation the same size: it cuts it by **80%**. And it does not leave success
unchanged: it costs 15 percentage points, significantly.

## Which tasks it loses, and why that is the interesting part

| Task type | Tier | Steps, A | Steps, B |
| --- | --- | ---: | ---: |
| `drawer` | L0 | 1 | 6 |
| `text_input` | L1 | 1 | 6 |
| `collapsible_disclosure` | L1 | 1 | 6 |
| `select_custom_single` | L1 | 2 | 6 |
| `notification_center` | L3 | 2 | 6 |
| `data_grid_editable` | L0 | 6 | 6 |

Five of six went from one or two steps to exhausting the six-step budget. The
agent did not get slightly worse; it stopped being able to finish at all.

And the component names say what happened. A drawer, a collapsible disclosure,
a notification centre, a custom select — these are all controls whose contents
are **hidden until revealed**. The filter drops elements that are not reachable
*right now*, which is exactly the set the agent must learn about in order to
reveal them.

Per step, the filter dropped a mean of 2.43 covered elements, 2.42
non-actionable, 1.18 unnamed and 0.49 zero-area; 39 of 88 steps had at least
one covered element removed.

## The ablation, which refutes the attribution below

The "not established" caveat in the next section was tested immediately, with
one extra arm: the same name, actionability and zero-area filtering, with the
**occlusion filter alone switched off** so covered elements stay in the
observation.

| Arm | Solved | vs A standard |
| --- | ---: | --- |
| A — standard AX | 36 / 40 | — |
| B — filtered, covered dropped | 30 / 40 | +0/−6, p = 0.03125 |
| **C — filtered, covered kept** | **28 / 40** | **+0/−8, p = 0.00781** |

C vs B: +0/−2, p = 0.50.

**Keeping the covered elements recovered 0 of the 6 lost tasks.** All six still
exhaust the budget; `text_input` fails on step 1 instead. C's observation is
larger than B's (602.5 vs 478.8 mean chars) and scores slightly worse.

So the occlusion filter is **not** what costs the 6 tasks. The harm belongs to
the filtered serialisation as a whole — the name filter, the actionability
filter, or the reformatting — and localising it needs further one-at-a-time
arms that have not been run.

### What this retracts

The section below was written before the ablation and claimed a symmetric pair:
labelling occlusion helps, deleting it hurts, same fact and opposite signs. The
second half of that pair is **withdrawn.** Deleting covered elements is not
measurably harmful here; something else in the filtered observation is.

What survives is narrower and still useful: **a filtered, reachability-aware
observation costs task success at 80% fewer characters, significantly, in two
independent arms** — and the reason is not the occlusion filtering.

---

## Retracted: the pair this appeared to form with the main result

The same fact — "this element is covered" — under two treatments, measured on
the same kind of agent:

| Treatment | Effect | p |
| --- | --- | --- |
| **Label** it: say the overlay is there and which elements it blocks | 4/26 → 19/26 | **0.00006** |
| **Delete** it: remove covered elements from the observation | 36/40 → 30/40 | **0.03125** |

**This reading did not survive its own ablation** — see the section above.
Keeping covered elements recovered none of the lost tasks, so the −6 cannot be
charged to the occlusion filter, and the symmetry was an artifact of
attributing a bundled treatment to one of its parts.

Kept in place as a record of how the wrong conclusion looked: entirely
plausible, directionally convenient, and one arm away from being disproved.

## What this does not establish

The caveat was right and the ablation settled it against the convenient
reading. What remains unlocalised is **which** filter costs the 6 tasks:
unnamed, non-actionable, or the reformatting itself. That needs one arm per
filter and has not been run.

Second limit: this is ComponentBench, where each task is a single component
and most pages carry no consent overlay. The covered elements being dropped
here are collapsed and hidden UI, not modal dialogs. The two experiments
therefore agree in direction while measuring different populations.

## Consequence for the paper

`PAPER.md` C4 must change. Compression was listed as a dead end that cost
nothing; it is in fact a dead end that costs 15 points. That is a better
negative result, honestly obtained, and it removes the weakest row from the
registry.
