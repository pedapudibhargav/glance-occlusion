# Layering: what the accessibility tree cannot tell an agent

**Date:** 2026-10-23 · 12 live sites · no model called, nothing spent
**Raw:** `data/frame_blindness.json`, `data/structure_measurement.json`
**Code:** `probe/structure.js`, `scripts/measure_frames.py`

## The failure, concretely

On BBC News, loaded as a UK visitor:

```
frame  https://www.bbc.com/news                     194 interactive elements
frame  https://cdn.privacy-mgmt.com/us_pm/index...     5 interactive elements
                                                        buttons: ["Accept and Continue"]

Blocking overlay: div#sp_message_container  position:fixed  z-index:2147483647
                  role=dialog  aria-modal=true  1280x720
Elements it blocks: 33

Dismiss controls in the top document: 0
```

A standard accessibility-tree snapshot covers the top document. It therefore
contains:

- **not** the button that dismisses the dialog — that lives in a cross-origin
  iframe, in a different document
- **no** indication that 33 elements are unreachable — the tree still lists
  them, correctly named, with correct coordinates

So the agent sees what looks like a working page, acts on it, and nothing
happens. There is nothing in its observation that explains the failure and
nothing it can click to fix it. It is stuck, silently, on a top-50 website.

## How common

| | |
| --- | ---: |
| Sites measured | 12 |
| Pages with a blocking overlay (≥3 elements) | **7** |
| ...where the dismiss control is demonstrably only inside a frame | **2** |

The two confirmed cases are BBC ("Accept and Continue") and Spiegel ("Consent
and continue").

**The real figure is probably higher.** Five more pages measured as blocked
with no dismiss control found anywhere — the Guardian (26 blocked), the
Independent (14), CNN (36), Le Monde (25), Reuters. On the Guardian the
blocking overlay is the same Sourcepoint consent container seen on BBC, so its
control is almost certainly in a frame that my probe could not read. From the
agent's point of view an unreadable frame is the same problem, or worse.

I am reporting 2 rather than 7 because 2 is what was positively confirmed.

## What the structured observation does about it

`probe/structure.js` emits the layering explicitly:

```
OVERLAY "SP Consent Message" is on top and blocks 33 element(s) behind it.

PAGE:
  [1] link "Skip to content"
  [35] textbox "Search news, topics and more" disabled empty
  ...

Blocked until the overlay closes: [41] [42] [43] ...
```

This does not by itself let the agent click the button — that still needs
cross-frame traversal, which Playwright can do and a top-document snapshot
cannot. What it does is convert a **silent** failure into a **diagnosable**
one: the agent can now reason "something is covering the page, deal with that
first" rather than clicking into a void and inferring nothing from the result.

## Token cost

| Observation | Median tokens | Overhead |
| --- | ---: | ---: |
| Flat element list | 1,224 | — |
| **Structured (layering + forms + state)** | — | **+3.8%** |

Zero overhead on pages with no overlay and no forms; the cost appears only
where there is something to report. On BBC the structured form is 3% *smaller*
than flat, because 33 blocked elements collapse into a list of ids instead of
33 full lines.

An earlier version cost +37%. The entire difference was a per-element
"offscreen" marker that appeared on 228 of 253 lines on one page and told the
agent nothing it needed — the driver scrolls before clicking regardless.

## Three bugs found while measuring this

Recorded because each produced a plausible-looking wrong answer.

1. **Static containers counted as overlays.** GitHub reported 73 blocked
   elements and Wikipedia reported an overlay called "Culture". Both were
   `position: static` layout wrappers. A static element cannot float above the
   page; filtering to positioned-or-declared blockers took GitHub from 73 to 1.

2. **Ancestor hits counted as occlusion.** When `elementFromPoint` returns
   something the element sits inside, nothing is covering it — the centre point
   landed on padding. This was most of the remaining false positives.

3. **`aria-labelledby` pointing at an iframe.** BBC and the Guardian both
   announced `OVERLAY ""`. Their consent container sets `aria-labelledby` to
   the id of the iframe holding its content; reading that element's `innerText`
   returns the empty string, because the text is in another document. The
   iframe's `title` attribute carries the real name — "SP Consent Message".

## Why this matters for the research

Of everything measured in this project, this is the clearest gap between what
agents are given and what they need, and it is not a question of cost or
latency at all — it is a correctness gap. The observation format in universal
use is structurally incapable of representing "the page is covered, and the
thing covering it is somewhere you cannot see".

Unresolved: whether telling the agent about the layering actually improves its
success rate. That needs tasks on pages with live consent dialogs, where the
success condition is reaching the content behind them.

---

# Cross-frame traversal: RETRACTED as a contribution

> **This section's novelty claim is withdrawn.** Playwright's `mode="ai"`
> snapshot — what Playwright MCP serves to agents — already descends into
> cross-origin iframes and already uses frame-qualified refs. On BBC it emits
> `button "Accept and Continue" [ref=f4e22]`, the same button and the same
> scheme reimplemented below. The engineering works; it is not new. See
> `data/format_comparison_findings.md`. What remains true is that CDP
> `Accessibility.getFullAXTree` misses it on 4 of 4 sites, which is a statement
> about the AX-tree route, not about web agents in general.

# Cross-frame traversal: the invisible case, fixed

**Date:** 2026-10-23 · **Code:** `probe/frames.py`

The gap above — a blocking dialog whose dismiss control lives in a cross-origin
iframe — cannot be closed from inside the page. Script in the top document
cannot reach a cross-origin frame. It can be closed from the driver, which can
evaluate in every frame; Playwright can, an accessibility snapshot of one
document cannot.

`probe/frames.py` enumerates child frames when, and only when, the top document
reports a blocking overlay, and gives each element a frame-qualified id
(`f4e5` = element 5 of frame 4), following the convention Playwright MCP
already uses for frame refs. An action is routed back to the frame its id came
from.

Tested on the four sites that were previously excluded because no observation
could reach their dismiss button:

| Site | Blocked before | Blocked after | Dismissed via |
| --- | ---: | ---: | --- |
| BBC News | 33 | **3** | `[f4e5] button "Accept and Continue"` |
| Spiegel | 27 | **5** | `[f2e1] button "Consent and continue"` |
| Financial Times | 34 | **3** | `[f3e3] button "Accept Cookies"` |
| Sky Sports | 42 | **0** | `[f2e6] button "Accept all"` |

All four cleared. These are pages where every existing observation format
leaves the agent permanently stuck.

## Why frames are only scanned when the page is blocked

A news page carries dozens of advertising and analytics iframes — the Guardian
had 47. Enumerating them on every step would add hundreds of irrelevant
controls for no benefit. Frames earn their tokens exactly when the page is
blocked and the way out may be inside one, which is the same selectivity
principle as enriching only ambiguous elements.

## A bug worth recording

The first version listed `<p>` elements as controls. Consent dialogs put
`tabindex` on their body text for focus management, and the collector treated
anything with a `tabindex` as actionable. On BBC that made a paragraph
beginning "Terms of Use & Privacy" outrank the real "Accept and Continue"
button, so the click landed on text and nothing happened — blocked stayed at
33. Restricting to genuinely actionable tags and roles fixed all four sites at
once.

## Still open

The Guardian remains unsolved. Its readable frame contains a CCPA "Do not sell
or share my personal information" dialog rather than the main consent dialog,
so no dismissal control is found. Whether the real control is in a frame that
cannot be read, or behind a further interaction, is not yet established.
