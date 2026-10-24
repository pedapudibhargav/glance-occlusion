# Occlusion-Aware Observations for Web Agents

Code and a reproducible benchmark for the finding that browser agents cannot
tell a clickable element from a covered one, because no observation format they
are given expresses occlusion.

## The problem, concretely

An agent receives a list of named elements. On a page covered by a consent
dialog, that list contains the covered elements exactly as it contains the
reachable ones. The agent picks the correct target, the click is intercepted,
and nothing in its observation explains why:

```
click('27') -> err:TimeoutError      the correct target
click('27') -> err:TimeoutError
click('27') -> err:TimeoutError      ... six times, budget gone
```

## Headline results

| | Flat list | Occlusion labelled |
| --- | ---: | ---: |
| Solved (26 tasks, 11 live sites) | 4 | **19** |
| McNemar exact two-sided p | | **0.00006** |
| By site | | 7 better, 0 worse, p = 0.0156 |
| Steps per task | 5.23 | **3.38** |
| Observation tokens | 3,008 | **2,887** |

Replicated on a second model (`gpt-5.4-mini`: 4/26 to 16/26, p = 0.00418, 11
flipped tasks shared) and on the deterministic HAR benchmark (2/24 to 11/24,
p = 0.00391).

The labelled observation is *cheaper*: collapsing 33 unreachable elements into
one line of ids costs less than listing all 33 as though they worked.

## What no deployed format expresses

Eight live sites, 25 to 141 elements covered on each:

| Serialisation | Dismiss button present | Occlusion expressed |
| --- | :-: | :-: |
| CDP `Accessibility.getFullAXTree`, top document | 0 / 4 | **0 / 8** |
| the same, merged per frame (BrowserGym's approach) | 4 / 4 | **0 / 8** |
| Playwright `aria_snapshot(mode="ai")` (Playwright MCP) | 4 / 4 | **0 / 8** |

Chrome's protocol defines `inertSubtree`, `inertElement`, `activeModalDialog`,
`activeAriaModalDialog`, `ariaHiddenSubtree`, `notRendered` and `notVisible`.
None was emitted on any node, in any frame, on any of the eight sites.

## Paint order misses the overlays that matter

Browser Use detects occlusion from CDP paint order
(`dom/serializer/paint_order.py`). Compared against `elementFromPoint`, which
is ground truth for whether a click lands:

| Recall | Sites | Blocker |
| ---: | --- | --- |
| 0.00, 0.00, 0.04, 0.17 | BBC, FT, Spiegel, Sky Sports | cross-origin iframe |
| 0.84, 0.90, 0.94, 0.95 | Le Monde, Euronews, CNN, Irish Times | plain DOM |

146 of 373 covered elements missed, zero false positives. Paint order is
computed per document, so a consent panel inside a cross-origin iframe
contributes no covering box to the top document's snapshot while the
compositor still puts it on top. A hit test asks the compositor, which ignores
document boundaries, in 3 ms.

## The benchmark

`scripts/record_sites.py` records each site to HAR; `scripts/replay_check.py`
verifies that a replay still presents the same overlay before any result is
computed from it. Ten of eleven recordings reproduce their overlay, nine with
an identical blocked count — including four sites that showed no overlay when
re-probed live. The instability was in the network, not the page.

The HAR files are 399 MB uncompressed and are archived separately:

```bash
python scripts/fetch_har.py          # downloads and unpacks into data/har/
python scripts/replay_check.py       # verifies the recordings reproduce
```

## Reproducing

```bash
python -m pip install -r requirements.txt
python -m playwright install chromium

python scripts/format_comparison.py        # what each format contains
python scripts/paint_vs_hit.py             # paint order vs hit testing
python scripts/baseline_audit.py           # against the baselines agents run

export OPENAI_API_KEY=...
python scripts/ab_overlay.py --tasks wide --arms A_flat,B2_structured_improved
python scripts/ab_overlay.py --tasks wide --replay --arms A_flat,B2_structured_improved
```

Every run is metered against a hard spend cap (`collect/budget.py`). If the
instrumentation cannot install, the run aborts rather than proceeding unprobed
— an earlier version logged a warning and carried on, which produced an
experiment where both arms were silently identical.

## Negative results, reported

| Direction | Verdict |
| --- | --- |
| Compress the observation | Harmful, not empty. Filtering to reachable, named, actionable elements cuts 80% of the characters and costs 36/40 to 30/40, p = 0.03125 |
| Shrink the prompt to cut latency | Empty. A 72x prompt range costs 26% latency |
| Cross-frame traversal as a contribution | Retracted. Playwright `mode="ai"` already descends into frames; BrowserGym already merges per-frame trees |
| A loop detector as the cheaper fix | Rejected. A generic "that failed, try something else" hint scores 4/17 against a flat baseline's 3/17 |
| Deleting occluded elements is what makes filtering harmful | Retracted by ablation. Keeping them recovered 0 of 6 lost tasks |

`findings/` holds the full record, including thirty corrections to this
project's own claims with the cause and the detector for each.

## Layout

```
probe/structure.js        occlusion, form grouping, state, landmarks, ranking
probe/verified_observation.js   the filtered observation measured in C5
probe/frames.py           cross-frame traversal (superseded by Playwright)
scripts/ab_overlay.py     the four-arm A/B, live or from HAR
scripts/record_sites.py   build the benchmark
scripts/replay_check.py   verify it reproduces before trusting it
collect/budget.py         hard spend cap, enforced in every child process
findings/                 per-experiment write-ups with limitations
data/                     raw results for every number in the paper
```

## Citing

See `CITATION.cff`. The dataset has its own DOI; cite both if you use the
recordings.

## Licence

MIT for the code. The HAR recordings contain third-party web content and are
distributed for research reproducibility only.
