# Verified observation vs standard accessibility tree — first A/B

**Date:** 2026-10-21 · **Model:** gpt-5-mini · **Tasks:** 36 paired · **Spend:** $0.17
**Raw:** `data/ab_result.json`, `data/raw/ab/`

Both arms ran the same 36 ComponentBench tasks in the same order, with the same
model, reasoning effort, action space and step cap. The only difference was
what the model was shown.

- **Arm A** — the harness's standard accessibility-tree text, which is what
  BrowserGym, Playwright MCP and CDP all produce.
- **Arm B** — `probe/verified_observation.js`: only elements that exist right
  now and that a click would actually reach, each with a name worth choosing
  by, duplicates disambiguated by their row or group.

This is the first run in this project where both arms were genuinely
structure-only. Earlier runs were not — see "What this corrects" below.

## Result

| | Arm A | Arm B | |
| --- | ---: | ---: | --- |
| Task success | 28/36 (77.8%) | 26/36 (72.2%) | **no detectable difference** |
| Observation tokens/step | 347 | 71 | **80% smaller** |
| Full prompt tokens/step | 1,247 | 971 | 22% smaller |
| Steps per task | 2.67 | 3.14 | **+18%** |
| **Total tokens per task** | **3,329** | **3,049** | **8% smaller** |

Paired analysis of the 36 tasks:

```
both succeed: 25    only A: 3    only B: 1    neither: 7
McNemar exact two-sided p = 0.625   (4 discordant pairs)
success delta B − A = −5.6pp,  95% CI [−16.7, +5.6]
```

**The success difference is two tasks.** The interval spans from six points
worse to six points better, so this run rules out only large effects. It is
evidence that filtering does not obviously break the agent. It is not evidence
that filtering is free.

## The headline number is misleading, and the honest one is 8%

The observation shrank 80%. Total tokens per task fell 8%.

Two things eat the difference. Fixed prompt overhead — instructions, goal,
action history — is about 900 tokens a step and does not shrink, so an 80%
cut to the observation is a 22% cut to the prompt. And arm B took **18% more
steps per task**, which multiplies back most of what was saved.

The step increase is the result worth taking seriously. It is the signature of
a filter removing something the agent needed and then spending an extra turn
recovering. With 36 tasks it could equally be noise, but it points at the
failure mode to watch, and it is the reason the end-to-end saving is 8% rather
than 80%.

## What was removed

Per step, arm B dropped 600 elements across the run:

| Reason | Count | Share |
| --- | ---: | ---: |
| not actionable (`generic`, `group`, `StaticText`…) | 220 | 37% |
| covered — a click would hit something else | 201 | 34% |
| zero area | 96 | 16% |
| unnameable even from row context | 83 | 14% |

A third were **covered**: present in the tree, correctly named, and
unclickable. Those are the ones that cost an agent a wasted step under every
current system, because none of them check.

## What this corrects

Earlier runs in this project labelled "structure-only" were not. ComponentBench
puts `benchmark/` on `sys.path`, so `agents.openai_agent` and
`benchmark.agents.openai_agent` are two separate module objects with two
separate classes. The runner imports the first; the patch targeted the second.
It logged success and changed nothing, so the agent kept receiving a screenshot
alongside the tree — consistent with the 3,138 average prompt tokens measured
then, against 1,247 here.

Those 400 collected rows still record whether a dispatched action worked, but
the agent chose each action with pixels available. They cannot support any
claim about acting on the structured layer alone, and are kept marked rather
than reused.

## What this does and does not support

**Supported:** an agent can be shown a verified, filtered element list instead
of a raw accessibility tree, at 80% of the observation size, without an obvious
loss of task success. On this evidence the idea is worth developing.

**Not supported:** that it is free, or that it saves 80% end to end. It saves
8% end to end here, and arm B needed more steps.

**Not tested at all:** whether filtering helps on pages where the tree is
genuinely misleading. These are ComponentBench v1 pages — mostly single, clean
components. The failure this is designed to prevent, an agent acting on an
element it cannot reach, is rare here by construction.

## What would make this conclusive

1. **More tasks.** 36 paired tasks give a ±11pp interval. A few hundred would
   separate "no difference" from "a few points either way".
2. **Find the extra steps.** Diff the trajectories on the tasks where arm B
   took longer and read what it was looking for. If the filter is removing
   something needed, that is fixable and the saving becomes real.
3. **A floor on the filter.** It returned nothing at all on 2 of 18 pages in an
   earlier measurement. It must never emit an empty list.
4. **Harder pages.** ComponentBench Core v2 is specified as dense and
   cluttered — four same-type rows, high clutter — which is where a verified
   observation should separate from a raw one.
