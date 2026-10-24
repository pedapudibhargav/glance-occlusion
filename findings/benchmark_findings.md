# A deterministic occlusion benchmark

**Date:** 2026-10-24 · **Code:** `scripts/record_sites.py`,
`scripts/replay_check.py` · **Raw:** `data/har/manifest.json`,
`data/replay_check.json`

## Why

Every result in this project was measured on live sites, and that was its worst
limitation. Five of 11 sites showed **no overlay** when re-probed minutes after
reporting 17–182 blocked elements. Consent dialogs vary with geography, A/B
split and prior state. Nobody else could rerun any of it.

There is also no public benchmark for this. ComponentBench has `dialog_modal`
components but reports no occlusion analysis; IGG covers occluded targets in
the screenshot-grounding setting, not in a tree serialisation. So the fixture
is itself a contribution, not only a fix.

## Method

Record each site's traffic to HAR with the overlay present, then serve a fresh
context entirely from that file with `not_found="abort"` — a request the
recording does not contain fails rather than quietly reaching the live web,
which would defeat the purpose.

A recording is only used if it reproduces the condition the experiment is
about. `scripts/replay_check.py` requires the overlay to be present on replay
and the blocked count to be within 35% of the live figure, and the A/B runs
only the sites that pass.

## Does replay reproduce the overlay?

| Site | Live blocked | Replay blocked | Delta |
| --- | ---: | ---: | ---: |
| Le Monde | 25 | 25 | 0% |
| Euronews | 171 | 171 | 0% |
| Mirror | 48 | 48 | 0% |
| RTÉ | 36 | 36 | 0% |
| Irish Times | 37 | 37 | 0% |
| Repubblica | 25 | 25 | 0% |
| La Vanguardia | 31 | 31 | 0% |
| ABC | 44 | 44 | 0% |
| Al Jazeera | 11 | 11 | 0% |
| CNN | 36 | 37 | 3% |
| De Standaard | 17 | — | `ERR_FAILED`, excluded |

**10 of 11, nine of them exactly** — including all four sites that were
unstable live (Euronews, Irish Times, La Vanguardia, ABC). The instability was
in the network, not in the page, and recording removes it.

## The defect this produced first, and how it was caught

The first recording captured only each site's landing page. Replaying it, the
A/B returned **0/17 in both arms**. Read as a number that is a clean
refutation of the entire result.

It was a fixture defect. The success condition is a URL change, the
destination pages were never recorded, and `not_found="abort"` aborts a
navigation the HAR does not contain — so the agent could dismiss the overlay,
click the correct link, and still fail.

Three things in the data said so before any conclusion was drawn:

- the improved arm's observation was **2,011 tokens against flat's 3,052**, so
  the overlay was being detected and collapsed correctly;
- every task used the full 6 steps in both arms;
- the arms ran 24 and 17 tasks, because the budget cap hit mid-run.

Confirmed directly: `cnn.har` contained exactly **one** CNN document,
`https://www.cnn.com/`, and `page.goto("https://www.cnn.com/world")` returned
`net::ERR_FAILED`.

The recorder now visits every target path during capture. Replay navigates:
`https://www.cnn.com/world`, `https://www.abc.es/espana/`. 418 MB across 11
recordings, 10 verified.

## State

The fixture is built and verified. **The A/B on it has not been run** — the
$3.00 budget was exhausted at $2.992 by the run that exposed the defect. Until
that run happens, every success-rate number in this project is a live-site
number.
