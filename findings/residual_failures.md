# Why three sites fail in both arms

**Date:** 2026-10-23 · no model called, nothing spent
**Raw:** `data/ab_overlay_wide8.json` · traces re-read, pages re-probed

In the 8-site run, Mirror (0/2), Irish Times (0/2) and Repubblica (0/1) failed
in both arms. Announcing the overlay did nothing for them. Each has a
different cause, and two of them point at the same missing piece.

## Mirror — the answer was in the observation and went unused

The structured observation said, correctly:

```
OVERLAY "Processing of Your Personal Information" is on top and blocks 48 element(s) behind it.
Actionable in the overlay:
  [341] link "Reach and across our entities"
  [342] link "cookies"
  [343] link "Privacy Notice"
  [344] link "Terms and Conditions"
  [345] button "ACCEPT"
```

The agent's trace never touches `[345]`:

```
click('590') -> click(590) | click('572') -> err:TimeoutError |
click('8') -> click(8) | click('2') -> click(2) | click('2') -> click(2) |
click('Gutter_gutter__kLC93') -> stale
```

This is an agent failure, not an observation failure. Worth stating plainly in
the paper: supplying the fact does not guarantee the fact is used.

## Repubblica — the right fact, buried among nine candidates

```
OVERLAY "powered by I ricavi ottenuti dalla pubblicità personalizzata" is on top
and blocks 25 element(s) behind it. (not declared as a dialog; detected by hit-testing)
Actionable in the overlay:
  [603] link "Cookie Policy"
  [604] button "pannello delle preferenze pubblicitarie"
  [605] button "RIFIUTA E ABBONATI"
  [606] button "ACCETTA"
  [607] link "ACCEDI"
  [608] link "Cookie Policy"
  [609] button "terze parti"
  [610] button "pannello delle preferenze pubblicitarie"
  [611] link "Cookie Policy"
```

The agent clicked `[610]` — "advertising preferences panel" — not `[606]`
ACCETTA. It opened a second-level settings view and never came back.

Nine controls, one of which dismisses, three of which are the same policy
link, two of which open a sub-panel, and one of which (`RIFIUTA E ABBONATI`,
"refuse and subscribe") leads to a paywall. The observation is correct and
unranked.

**This is the next contribution, and it falls straight out of C3.** The
four-arm ablation showed that what helps is the *diagnosis* — "you are
covered" — not the detection. The same logic applies one level down: knowing
there is an overlay is worth less than knowing which control leaves it. The
label is derivable without a model (primary button styling, `autofocus`,
`aria-label`, the dialog's default action, position in the dialog's action
row), and it is absent from every format for the same reason occlusion is.

## Irish Times — the overlay is not reliably there

The A/B recorded `blocked=39` for both arms. Re-probing the same URL minutes
later, `probe/structure.js` reports no overlay at all and the page serialises
straight into navigation links.

So the Irish Times failure is not an occlusion failure, and the run's
`blocked=39` should not be read as one either. Consent dialogs vary with
geography, A/B splits and prior state; `tolerant_goto` and a fresh context
remove the worst of that, but not this. **Stated as a limitation: overlay
presence is measured per run, and is not stable across runs on every site.**

## What this changes

- One of the three is an agent failure with the answer in hand (Mirror).
- One is a ranking failure inside a correct observation (Repubblica).
- One is not an occlusion failure at all (Irish Times).

Only the second is a gap in the observation, and it is the natural next
experiment: mark the dismissing control, not just the overlay.
