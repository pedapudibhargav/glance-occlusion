# Artifact registry

Every claim this project made that turned out to be wrong, what caused it, what
caught it, and — the column that matters for a paper about this — **how well
the correction itself is verified.**

The second paper rests on this table, so the table has to survive the same
scrutiny it applies to everything else. A correction supported by one quick
check is not stronger than the claim it replaced.

Verification levels:

- **V3** — the artifact was reproduced, and the corrected measurement was run
  again independently. Highest confidence.
- **V2** — the cause was identified in code or data and the corrected
  measurement was run once.
- **V1** — the cause is argued from a code read or a single observation; not
  reproduced. **Needs work before publication.**

---

## A. Claims about the observation

| # | Claim made | What was actually true | Cause | Caught by | Level |
| --- | --- | --- | --- | --- | :-: |
| A1 | "80% smaller observation, 41% smaller prompt" | 0.5% | `MAX_ELEMENTS = 200` truncating the back half of every page in DOM order; GitHub's Releases link is element 411 | Checking whether a known target appeared in the output at all | **V3** |
| A2 | "Stale refs 5 → 0" | Parser bug, not an improvement | Model emitted `click('[71]')`; executor looked for `bid="[71]"` | Reading traces | V2 |
| A11 | Structured observation costs +37% tokens | +3.8% | A per-element `offscreen` marker on 228 of 253 lines, which the driver makes irrelevant by scrolling before it clicks | Attributing the overhead line by line | **V3** |
| A9 | GitHub has 73 blocked elements; Wikipedia announces an overlay called "Culture" | GitHub has 1; Wikipedia has none | `position: static` layout wrappers counted as overlays, and ancestor hit-test results counted as occlusion | Inspecting the named blocker | **V3** |
| A10 | `OVERLAY ""` on BBC and the Guardian | The overlay is "SP Consent Message" | `aria-labelledby` pointing at the id of the *iframe* holding the dialog, whose `innerText` is empty from the parent document | Reading the attribute chain | **V3** |
| A12 | Four consent sites have no reachable dismiss control | All four do | Consent dialogs put `tabindex` on body text; a `<p>` beginning "Terms of Use & Privacy" outranked "Accept and Continue" | Restricting to actionable tags and roles, then re-running | **V3** |

## B. Claims about novelty

| # | Claim made | What was actually true | Cause | Caught by | Level |
| --- | --- | --- | --- | --- | :-: |
| A14 | Cross-frame traversal is new; no page observation can reach a consent button in a cross-origin iframe | Playwright's `aria_snapshot(mode="ai")` already descends into frames and emits `button "Accept and Continue" [ref=f4e22]` | The verifying check called `aria_snapshot()` in its **default** mode, which snapshots one document | Re-running in `mode="ai"` after a source pointer to playwright#35396 | **V3** |
| A15 | `Accessibility.getFullAXTree` misses the dismiss button on 4 of 4 sites | True of the top document only. BrowserGym enumerates `Page.getFrameTree` and requests a tree per `frameId`; merged, it finds the button 4 of 4 | Measuring a baseline nobody runs | Reading `browsergym/core/observation.py`, then implementing the merge and re-measuring | **V3** |
| A16 | No deployed system represents occlusion | Browser Use ships it: `dom/serializer/paint_order.py` sets `ignored_by_paint_order` from CDP paint order | Not checking deployed code before claiming absence | Reading the source | **V2** — the rule was reimplemented from its source and compared; Browser Use was **not run as a system**. This is the registry's weakest headline entry. |

## C. Claims about the intervention

| # | Claim made | What was actually true | Cause | Caught by | Level |
| --- | --- | --- | --- | --- | :-: |
| A8 | Labelling overlays gives +50 points | The effect is real but that run was contaminated | `clear_cookies()` does not clear `localStorage`, where consent state lives; the structured arm dismissed CNN's dialog on task 1 and its next two CNN tasks loaded with no overlay | Measuring `blocked` in both arms, and giving each task a fresh browser context | **V3** |
| A17 | Structure is *necessary* to recover from a blocked page | Then: a loop detector matched it (13/17 vs 12/17), so "not necessary". Then: that detector's hint named the failure mode and listed the remedy. A generic hint scores 4/17 against flat's 3/17 | A hand-written hint evaluated on the failure it was written for | Running the same detector with a generic hint (arm D) | **V3** — a double correction; both directions were measured |

## D. Claims from the earlier modelling phase

| # | Claim made | What was actually true | Cause | Caught by | Level |
| --- | --- | --- | --- | --- | :-: |
| A4 | Failure predictor AUROC 0.999 | Meaningless | The training split contained 2 failures | Inspecting class balance | V2 |
| A5 | AUROC 1.000 | The model learned component identity, not grounding difficulty | 77 failure rows from 14 tasks, 12 of them `alpha_slider` — a bid click cannot drag a slider, so this is an action-space failure | Grouping failures by component | V2 |
| A6 | AUROC 1.000, with an ablation to prove it was not the tautological feature | The ablation did not ablate | `target_resolvable` was tautological, and blanking that column changed nothing because every other feature was null on exactly those rows | Checking what the ablated rows contained | V2 |
| A7 | A structure-only collection run | Every episode still had screenshots | The runner imports `agents.openai_agent`; the patch was applied to `benchmark.agents.openai_agent` — two distinct module objects, so the patch logged success and changed nothing | Patching both paths and returning a count | **V3** |

## E. Claims about cost and latency — **the weakest section**

| # | Claim made | What was actually true | Cause | Caught by | Level |
| --- | --- | --- | --- | --- | :-: |
| A3 | A 32× prompt increase costs +11% latency | +26% over a 72× range | Prompt caching: five *identical* prompts per size | A reader pointing it out; re-measured with randomised prompts at `cached_tokens: 0` | **V3** |
| A26 | *resolved — see appendix* — "the round trip is ~900 ms and observation extraction is 23 ms, about 2% of a step" | A step is 1,859 ms: model 887 ms (47.7%), browser action 928 ms (49.9%), extraction 44 ms (2.4%) | The figure counted the model call and the extraction and omitted the browser action and settle time | Instrumenting all three phases and saving them in the record | **V3** |
| A27 | *resolved, and refuted* — "compression is empty: 3,389 vs 3,406 tokens, 10/14 vs 10/14" | **Both halves were wrong.** At n = 40 paired, filtering cuts the observation 80% (median 1,607 → 197.5 chars) and costs 36/40 → 30/40, 0 gained / 6 lost, McNemar p = 0.03125 | n = 14, one run, and `summarise_arm` recorded only aggregate counts so no paired test was constructible | Re-running at n = 40 after adding per-task outcomes and a McNemar test to the script | **V3** |

## F. Infrastructure failures that produced wrong numbers

Recorded because each one silently corrupted a result rather than erroring.

| # | Symptom | Cause | Level |
| --- | --- | --- | :-: |
| A13 | 18 of 24 candidate sites "failed" to load | Immediate redirects raising "Navigation is interrupted by another navigation"; `wait_until="commit"` plus a settle wait recovered 5 usable sites | **V3** |
| A18 | A second model scored 0/26 on both arms — readable as a clean refutation | Every call returned `400 Unsupported value: 'reasoning_effort' does not support 'minimal' with this model`. **The tell was `$0.000` spent, not the score** | **V3** |
| A19 | A second model scored 0/26 → 3/26 | Floor effect: `gpt-5-nano` also scored 0/2 on the control sites that `gpt-5-mini` passes on the *flat* arm. Model agnosticism needs a model that can do the task | **V3** |
| A20 | Dashboard showed "—" for every metric | Python wrote `NaN` into JSON; `JSON.parse` throws | V2 |
| A21 | A chart rendered nothing, with correct axes and legend | An unanchored `results/` in `.gitignore` matched `dashboard/app/results/` at any depth, so Tailwind — which honours gitignore — never generated `h-[420px]`, and the container had zero height | V2 |
| A24 | Every episode died while recording step stats | `tiktoken` raised `SSLCertVerificationError` fetching its BPE file through TLS interception | V2 |

---

## What this table says about the second paper

25 entries. **18 at V2 or above, 4 at V3 in the headline rows, and 3 at V1.**

The three V1 rows are the problem, and two of them are load-bearing for the
*first* paper:

1. **A26** — the step-latency breakdown. The paper still contains a figure that
   was computed from two of a step's three phases. Instrumentation is in place;
   the measurement has not been run.
2. **A27** — the compression null result. n = 14, one run, no test. The first
   paper uses it to justify abandoning compression, and the second paper would
   present it as a documented negative result. Neither is safe at n = 14.
3. **A16** — Browser Use's rule was reimplemented from its source rather than
   run. The comparison may be unfair to it in ways a reimplementation hides.

**Until those three are raised, the second paper cannot claim what it is for.**
A paper whose subject is measurement discipline is held to its own standard,
and three of its own rows do not meet it.

---

## Appendix: entries added after the registry opened

| # | Claim made | What was actually true | Cause | Caught by | Level |
| --- | --- | --- | --- | --- | :-: |
| A26 | *resolved* — "the round trip is ~900 ms and extraction is 23 ms, about 2% of a step" | A step is **1,859 ms** on average: model 887 ms (47.7%), browser action 928 ms (49.9%), extraction 44 ms (2.4%). The extraction share was right; half the step was missing | The figure measured the model call and the extraction only | Instrumenting all three phases and saving them in the record | **V3** |
| A28 | An overlay whose dismiss control is unreachable should be reported as such | True, but the wording matters more than the fact. "No control in this document closes it" made the agent abandon the page: Euronews went from PASS in 4 steps to five identical timing-out clicks | Stating an absence terminally | Comparing the same arm with and without the line, then rewording and re-running | **V3** |

Also discovered while resolving A26, and new rather than a correction: an
intercepted click costs **3,390 ms on average and 4,004 ms at the median**,
against **215 ms mean / 87 ms median** for one that lands. A 46× difference at
the median. Occlusion blindness therefore costs wall-clock twice — extra model
calls, and a four-second browser stall on each blocked click.

**Registry status: 27 entries, 3 at V1.** A26 moved from V1 to V3. The two
remaining V1 rows are A27 (the compression null, n = 14, no significance test)
and A16 (Browser Use's rule reimplemented from source rather than run as a
system). Both are still blockers for the second paper.

| # | Claim made | What was actually true | Cause | Caught by | Level |
| --- | --- | --- | --- | --- | :-: |
| A29 | A deterministic replay of the benchmark gives 0/17 in both arms | A fixture defect. Only landing pages were recorded; the success condition is a URL change, and `not_found="abort"` aborts a navigation the HAR lacks, so a correct click still failed | Recording the wrong thing | The observation token counts (2,011 improved vs 3,052 flat) showed the overlay was being handled correctly, so the failure could not be the intervention. Confirmed by finding exactly one document in `cnn.har` and reproducing `net::ERR_FAILED` on `/world` | **V3** |

**Registry status: 29 entries, 3 at V1** (A27 compression null, A16 Browser Use
reimplemented, and A29's successor state — the corrected fixture is verified to
replay but the A/B on it is unrun).
