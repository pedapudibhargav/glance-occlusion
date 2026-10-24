# Live probe run — first real measurement

**Date:** 2026-10-20
**Scope:** 92 elements across 17 ComponentBench v1 task pages (antd, mui, mantine),
served from the hosted benchmark at `interfacegym.com`.
**Command:** `python scripts/verify_probe_live.py --tasks "$(cat data/sample_task_ids.txt)"`
**Raw rows:** `data/raw/live_probe.jsonl`

This is the done-condition for task 3 of the spec. It is **not** a result about
H1 — see "What this does not show" at the bottom.

## Task 3 done-condition: met

> *A single probe call on a settled page returns in under 150 ms and all fields
> are non-null.*

| Measure | Value |
| --- | --- |
| Probe latency p50 | **8.5 ms** |
| Probe latency p95 | 21.0 ms |
| Probe latency max | 37.8 ms |
| Probe errors | 0 / 92 |
| CDP connection | attached on every page |
| `ax_timed_out` | 0 / 92 |

Comfortably inside the 150 ms budget, with the full CDP path live:
`Accessibility.getPartialAXTree`, `DOMSnapshot.captureSnapshot` with paint
order, and the `Network`/`Page` event counters.

Five features are always null, and legitimately so: `ax_state_disabled`,
`ax_state_focused`, `ax_state_expanded`, `ax_state_checked`, `ax_state_inert`.
Chrome omits an accessibility property when it does not apply to the node, and
the schema treats absent as `None` meaning *unknown* rather than `False`. That
distinction is deliberate and is working.

## The signals vary on real components

The thing worth knowing before spending money on a collection run: do these
features actually discriminate between elements on real React pages, or does
everything look the same?

| Feature | Distinct values | Range observed |
| --- | --- | --- |
| `corner_hit_agreement` | 5 | 0, 0.2, 0.4, 0.6, 1.0 |
| `hit_is_self` | 2 | True and False |
| `paint_order_rank` | 37 | 0–37 |
| `rect_min_dim` | 23 | 16 px – 120 px |
| `ax_role` | 8 | button, checkbox, combobox, generic, link, none, other, textbox |
| `sibling_name_collisions` | 4 | 0–3 |
| `ax_name_present` | 2 | True and False |
| `ax_name_matches_text` | 6 | 0.0 – 0.61 |
| `is_div_with_pointer` | 2 | True and False |

Partial occlusion is **common**, not exotic. `corner_hit_agreement` lands
strictly between 0 and 1 on 14 of 92 elements — these are cases a single
centre-point hit test would score as perfectly fine, which is precisely the
failure mode that produces a mis-click looking like a success.

## The premise looks sound

Two pages are worth calling out, because they are exactly the regime the paper
argues about.

**`carousel-antd-T12`** — 12 of 14 probed elements are occluded at their centre
*and* carry no accessible name. The accessibility tree lists them; acting on
them blind would be guesswork.

**`data_grid_editable-mui-T06`** — a mix of occluded cells, name collisions up
to 3, and `div`-with-pointer controls carrying no role. Several elements show
`corner_hit_agreement = 0.60` with `is_div_with_pointer = true`: visually
clickable, structurally anonymous, partially covered.

**`virtual_list-mui-T10`** — repeated `corner_hit_agreement` of 0.2 and 0.6 on
buttons, with unnamed checkboxes interleaved.

These are the benchmark's own pages, and the structured layer is visibly
unreliable on them. That supports the premise. It does not yet support the
claim.

## Features that were constant here, and why

`rect_iou` (always 1.0), `page_settled` (always True) and
`pending_network_requests` (always 0) did not vary — an artifact of *how* this
run works, not of the pages. The verifier settles each page for 400 ms, then
marks the observation and probes immediately after, so there is no window for
geometry to drift or for the page to be mid-render.

In real collection those three carry the staleness signal, because the
observation is taken, sent to a model, and acted on hundreds of milliseconds
later. **This run cannot tell us whether they are informative.** The collection
run can.

Also constant for benign reasons: `modal_open` (no modal tasks in this sample),
`in_shadow_root` (none of the three libraries use shadow DOM),
`ax_name_is_generic` (no stoplisted names in this sample).

## What this does not show

**Nothing about H1.** These rows have no labels. The elements were chosen by a
CSS selector sweep, not by an agent deciding what to act on, and no action was
dispatched, so there is no `structure_only_succeeded` and no way to ask whether
the features predict failure.

H1 needs the collection run: an agent acting in structure-only mode across the
2,910 v1 tasks, with the success banner supplying ground truth. That needs an
LLM API key. Until then the gate at task 11 is open.

## Fixed while getting here

Three defects in the probe, all of which would have corrupted collection:

1. **Chrome's DevTools HTTP endpoint 404s on `127.0.0.1` and answers on
   `localhost`** — it validates the Host header. `normalise_debug_url` now
   substitutes it, so passing an IP works.
2. **Chrome rejects a DevTools WebSocket carrying an unapproved `Origin`.** The
   client now suppresses the header entirely, which Chrome accepts, so the
   probe works against a browser someone else launched without needing
   `--remote-allow-origins`.
3. **Tagging elements with `data-testid` to address them does not survive a
   React re-render.** The verifier computes a structural CSS path instead and
   mutates nothing — closer to the real harness, where BrowserGym assigns the
   id itself.
