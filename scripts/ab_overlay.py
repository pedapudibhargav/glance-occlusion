"""Does telling the agent about the overlay help it get past the overlay?

The layering measurement showed that a blocking consent dialog is invisible to
a standard accessibility snapshot: the blocked elements are still listed,
correctly named, and nothing says they are unreachable. This tests whether
saying so changes what the agent achieves.

Both arms get the same tasks, model, action space and step budget. The only
difference is the observation:

  A  flat element list — what BrowserGym, Playwright MCP and CDP all produce
  B  structured — the overlay announced, blocked elements collapsed to ids

Every task's target sits *behind* the overlay, so the agent has to deal with
the overlay before it can succeed. Arm A has no way to know that from its
observation; arm B is told.

Sites are limited to the three where the dismiss control is in the top
document. On BBC, the Guardian, Spiegel and the FT the control lives in a
cross-origin iframe, so neither arm could click it and the comparison would
measure nothing.

Success is a URL check — unambiguous, and no model judges it.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from collect.budget import BudgetExceeded, Ledger  # noqa: E402
from eval.overlay_sites import SITES as WIDE_SITES, tolerant_goto  # noqa: E402
from scripts.gate_run import REPO_ROOT, load_env  # noqa: E402


def wide_tasks() -> list["OverlayTask"]:
    """One task per navigation target across the 8-site table.

    The 17 tasks below sit on three sites, which is the single biggest
    weakness of the main result: each task is an independent trial of "can the
    agent get past this overlay", but the overlay repeats. This set trades
    tasks-per-site for sites, so the same comparison can be read as site
    independence rather than replication.
    """
    out: list[OverlayTask] = []
    for site in WIDE_SITES:
        for label, fragment in site.targets:
            out.append(OverlayTask(
                f"{site.key}-{label.lower().replace(' ', '')}",
                site.url,
                f"Open the {label} section.",
                f"() => location.pathname.toLowerCase().includes({fragment!r})"))
    return out


@dataclass(frozen=True)
class OverlayTask:
    task_id: str
    url: str
    goal: str
    success_js: str
    max_steps: int = 6


TASKS = [
    # CNN and Le Monde are where the overlay actually obstructs the target.
    # Al Jazeera's dialog blocks 11 elements but not its navigation, so it acts
    # as a control: if the structured arm "wins" there too, something other
    # than the overlay is driving the result.
    #
    # Limitation stated plainly: these are many tasks across three sites, not
    # many sites. Each task gets a fresh browser context, so each is an
    # independent trial of "can the agent get past this overlay", but the
    # overlay itself repeats. A reviewer should read this as 3 sites with
    # replication, not as 20 independent sites.
    OverlayTask("cnn-world", "https://www.cnn.com/", "Open the World news section.",
                "() => location.pathname.toLowerCase().includes('/world')"),
    OverlayTask("cnn-politics", "https://www.cnn.com/", "Open the Politics section.",
                "() => location.pathname.toLowerCase().includes('politic')"),
    OverlayTask("cnn-business", "https://www.cnn.com/", "Open the Business section.",
                "() => location.pathname.toLowerCase().includes('business')"),
    OverlayTask("cnn-health", "https://www.cnn.com/", "Open the Health section.",
                "() => location.pathname.toLowerCase().includes('health')"),
    OverlayTask("cnn-travel", "https://www.cnn.com/", "Open the Travel section.",
                "() => location.pathname.toLowerCase().includes('travel')"),
    OverlayTask("cnn-science", "https://www.cnn.com/", "Open the Science section.",
                "() => location.pathname.toLowerCase().includes('science')"),
    OverlayTask("cnn-climate", "https://www.cnn.com/", "Open the Climate section.",
                "() => location.pathname.toLowerCase().includes('climate')"),
    OverlayTask("cnn-entertainment", "https://www.cnn.com/", "Open the Entertainment section.",
                "() => location.pathname.toLowerCase().includes('entertainment')"),
    OverlayTask("lemonde-international", "https://www.lemonde.fr/", "Open the International section.",
                "() => location.pathname.toLowerCase().includes('international')"),
    OverlayTask("lemonde-economie", "https://www.lemonde.fr/", "Open the Economie section.",
                "() => location.pathname.toLowerCase().includes('economie')"),
    OverlayTask("lemonde-politique", "https://www.lemonde.fr/", "Open the Politique section.",
                "() => location.pathname.toLowerCase().includes('politique')"),
    OverlayTask("lemonde-culture", "https://www.lemonde.fr/", "Open the Culture section.",
                "() => location.pathname.toLowerCase().includes('culture')"),
    OverlayTask("lemonde-sport", "https://www.lemonde.fr/", "Open the Sport section.",
                "() => location.pathname.toLowerCase().includes('sport')"),
    OverlayTask("lemonde-planete", "https://www.lemonde.fr/", "Open the Planete section.",
                "() => location.pathname.toLowerCase().includes('planete')"),
    # Controls: the overlay is present but does not obstruct the target.
    OverlayTask("aljazeera-news", "https://www.aljazeera.com/", "Open the News section.",
                "() => location.pathname.toLowerCase().includes('news')"),
    OverlayTask("aljazeera-sport", "https://www.aljazeera.com/", "Open the Sport section.",
                "() => location.pathname.toLowerCase().includes('sport')"),
    OverlayTask("aljazeera-economy", "https://www.aljazeera.com/", "Open the Economy section.",
                "() => location.pathname.toLowerCase().includes('econom')"),
]

STAMP_BIDS = """
() => {
  let i = 0;
  document.querySelectorAll(
    'a[href],button,input,select,textarea,summary,[role],[tabindex],[onclick]'
  ).forEach(e => e.setAttribute('bid', String(++i)));
  return i;
}
"""

SYSTEM_PROMPT = """You are a browser agent. You are shown the interactive elements of a page, each with an id in brackets.

Reply with EXACTLY ONE action and nothing else:
  click('<id>')
  type('<id>', 'text')
  press('<id>', 'Enter')
  scroll('down')

If an overlay or dialog is covering the page, deal with it before anything else."""

ACTION_RE = re.compile(r"\b(click|type|press|scroll)\s*\(\s*'([^']*)'\s*(?:,\s*'([^']*)'\s*)?\)")


def observation(page: Any, structured: bool, structure_src: str,
                opts: dict[str, Any] | None = None) -> dict[str, Any]:
    page.evaluate(STAMP_BIDS)
    page.evaluate(structure_src)
    if structured:
        built = page.evaluate("(o) => window.__glanceStructure.build(o)", opts)
        return {"text": built["text"], "blocked": built["blocked"],
                "overlay": built["overlay_label"],
                "obstructive": built.get("overlay_obstructive")}
    flat = page.evaluate("() => window.__glanceStructure.flat()")
    return {"text": flat["text"], "blocked": None, "overlay": None,
            "obstructive": None}


def execute(page: Any, reply: str) -> str:
    match = ACTION_RE.search(reply)
    if not match:
        return f"unparsed:{reply[:40]}"
    verb, first, second = match.group(1), match.group(2).strip().strip("[]"), match.group(3)
    try:
        if verb == "scroll":
            page.mouse.wheel(0, 600)
            return "scroll"
        locator = page.locator(f'[bid="{first}"]').first
        if locator.count() == 0:
            return f"stale:{first}"
        if verb == "click":
            locator.click(timeout=4000)
            return f"click({first})"
        if verb == "type":
            locator.fill(second or "", timeout=4000)
            return f"type({first})"
        if verb == "press":
            locator.press(second or "Enter", timeout=4000)
            return f"press({first})"
    except Exception as exc:
        return f"err:{type(exc).__name__}"
    return "noop"


# The hint arm C injects after it notices a repeated, ineffective action.
#
# This is the cheapest possible alternative to structuring the observation: do
# not tell the agent what is on the page, just tell it that what it tried did
# not work and why that might be. If this closes most of the gap, the
# contribution narrows from "observations need structure" to "agents need a
# loop detector", so it is tested directly rather than argued about.
#
# Note that arm A already sees "-> err:TimeoutError" in its action history and
# repeats anyway, so arm C is deliberately the strong version: an explicit
# instruction, not merely evidence.
LOOP_HINT = (
    "NOTE: your last action was repeated and had no effect. A dialog or overlay "
    "is probably covering the page and intercepting clicks. Find and activate "
    "the control that dismisses it (for example Accept, Agree, Allow or "
    "Continue) before trying the target again."
)


# Arm D's hint, the fair version of arm C's.
#
# Arm C's hint names the failure mode and the remedy: it says "overlay" and it
# lists "Accept, Agree, Allow, Continue". That is an oracle. A real agent does
# not know in advance which of a dozen failure modes it is hitting, so a hint
# that already contains the answer measures the value of the answer, not the
# value of loop detection. Arm D says only that the action did not work.
GENERIC_HINT = (
    "NOTE: your last action was repeated and had no effect. Something is "
    "preventing it from working. Try a different approach."
)


def reasoning_effort_for(model: str) -> str:
    """The lowest reasoning setting each model accepts.

    `minimal` is what gpt-5-mini takes and it cut cost 6.4x, so it is the
    setting every arm has used. gpt-5.4-mini rejects it outright —
    "Unsupported value: 'reasoning_effort' does not support 'minimal' with
    this model" — and a whole 26-task run died on step 1 with a
    BadRequestError before this was noticed.

    The value differs per model, so cross-model numbers are not strictly
    comparable. That does not matter for what the second model is for: both
    arms within a run share the setting, so the A/B contrast is clean.
    """
    return "none" if model.startswith("gpt-5.4") or model.startswith("gpt-5.5") else "minimal"


def run_task(page, client, model, task, structured, structure_src, ledger,
             hint_text: str = "", opts: dict[str, Any] | None = None) -> dict[str, Any]:
    # tolerant_goto, not a plain goto: most of these sites redirect on first
    # load (geo, consent, A/B), which cancels our navigation and raises
    # "Navigation is interrupted by another navigation". 18 of 24 candidate
    # sites looked like hard failures before this was fixed.
    tolerant_goto(page, task.url)

    history: list[str] = []
    obs_tokens: list[int] = []
    # Where a step's wall-clock actually goes.
    #
    # An earlier claim in this project — "the round trip is ~900 ms and
    # observation extraction is 23 ms, about 2% of a step" — measured the model
    # call and the extraction, and quietly left out the time the browser spends
    # executing the action and settling afterwards. Skim (arXiv 2605.16565)
    # reports browser action time larger than inference time per step, so the
    # omission may be most of the step. Measured here rather than assumed.
    timing: list[dict[str, float]] = []
    blocked_seen = None
    succeeded = False
    steps = 0

    for step in range(task.max_steps):
        steps = step + 1
        t_observe = time.perf_counter()
        obs = observation(page, structured, structure_src, opts)
        observe_ms = (time.perf_counter() - t_observe) * 1000
        obs_tokens.append(round(len(obs["text"]) / 4))
        if blocked_seen is None:
            # Measured for both arms, even though only the structured arm is
            # shown it. Without this the flat arm reports None and a
            # contaminated task cannot be told from a genuine one.
            probe = page.evaluate("() => window.__glanceStructure.build()")
            blocked_seen = probe["blocked"]

        recent = "\n".join(f"- {h}" for h in history[-4:]) or "- (nothing yet)"

        # Repeat detection: the same action proposed twice with no effect.
        hint = ""
        if hint_text and len(history) >= 2:
            last_two = [h.split(" -> ")[0] for h in history[-2:]]
            ineffective = any(
                mark in history[-1] for mark in ("err:", "noop", "stale:", "unparsed:")
            )
            if last_two[0] == last_two[1] or ineffective:
                hint = f"\n{hint_text}\n"

        user = (f"GOAL: {task.goal}\n\nACTIONS SO FAR:\n{recent}\n{hint}\n"
                f"PAGE:\n{obs['text'][:14000]}\n\nYour single next action:")
        t_model = time.perf_counter()
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "system", "content": SYSTEM_PROMPT},
                          {"role": "user", "content": user}],
                max_completion_tokens=120,
                reasoning_effort=reasoning_effort_for(model))
        except Exception as exc:
            history.append(f"model error: {type(exc).__name__}")
            break

        model_ms = (time.perf_counter() - t_model) * 1000
        usage = response.usage
        ledger.charge(model, int(usage.prompt_tokens), int(usage.completion_tokens))
        reply = response.choices[0].message.content or ""
        t_act = time.perf_counter()
        outcome = execute(page, reply)
        action_ms = (time.perf_counter() - t_act) * 1000
        timing.append({"observe_ms": round(observe_ms, 1),
                       "model_ms": round(model_ms, 1),
                       "action_ms": round(action_ms, 1)})
        history.append(f"{reply.strip()[:34]} -> {outcome}")
        page.wait_for_timeout(1500)

        try:
            if page.evaluate(task.success_js):
                succeeded = True
                break
        except Exception:
            pass

    return {
        "task_id": task.task_id, "succeeded": succeeded, "steps": steps,
        "blocked_at_start": blocked_seen,
        "observation_tokens_mean": round(statistics.mean(obs_tokens), 1) if obs_tokens else None,
        # The step's wall clock, split three ways. Collected since the last run
        # and then left out of the record, which is why the paper still carries
        # an uncorrected "~900 ms round trip, 23 ms extraction" — a figure that
        # counted the model call and the extraction and ignored the time the
        # browser spends executing the action and settling.
        "timing": timing,
        "step_ms_mean": {
            k: round(statistics.mean(t[k] for t in timing), 1) for k in
            ("observe_ms", "model_ms", "action_ms")
        } if timing else None,
        "history": history,
    }


# (name, structured observation, hint on a detected repeat, build options)
#
# B2 is B plus the four changes that came out of the failure analysis in
# data/residual_failures.md, so the pair isolates what that analysis bought:
# an imperative first step, a ranked dismiss control, a one-line form for an
# overlay that is not in the way, and an explicit note when the way out is in
# an iframe.
ARMS = (
    ("A_flat", False, "", None),
    ("B_structured", True, "", None),
    ("C_flat_plus_loop_hint", False, LOOP_HINT, None),
    ("D_flat_plus_generic_hint", False, GENERIC_HINT, None),
    ("B2_structured_improved", True, "",
     {"rank": "lexical", "directive": True, "obstruction": True}),
    ("B3_structured_landmarks", True, "",
     {"rank": "lexical", "directive": True, "obstruction": True,
      "landmarks": True, "href": True}),
)
ARM_NAMES = tuple(a[0] for a in ARMS)


def main() -> int:
    parser = argparse.ArgumentParser(description="A/B structured vs flat on overlay pages")
    parser.add_argument("--budget", type=float, default=0.35)
    parser.add_argument("--max-steps", type=int, default=6)
    parser.add_argument("--model", default="")
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "data" / "ab_overlay.json")
    parser.add_argument("--ledger", type=Path, default=REPO_ROOT / "data" / "spend_ledger.json")
    parser.add_argument("--arms", default=",".join(ARM_NAMES),
                        help="comma-separated arm names to run")
    parser.add_argument("--replay", action="store_true",
                        help="serve pages from data/har instead of the live web")
    parser.add_argument("--tasks", choices=("deep", "wide"), default="deep",
                        help="deep: many tasks on 3 sites; wide: one per target on 8")
    args = parser.parse_args()
    tasks = TASKS if args.tasks == "deep" else wide_tasks()
    replay_set: set[str] = set()
    if args.replay:
        # Only recordings that were verified to reproduce their overlay are
        # replayed; scripts/replay_check.py decides that, not this script.
        check = json.loads((REPO_ROOT / "data" / "replay_check.json")
                           .read_text(encoding="utf-8"))
        replay_set = set(check.get("_usable", []))
        tasks = [t for t in tasks if t.task_id.split("-")[0] in replay_set]
        print(f"replay: {len(tasks)} task(s) on {len(replay_set)} verified recording(s)")
    selected = {a.strip() for a in args.arms.split(",") if a.strip()}
    unknown = selected - set(ARM_NAMES)
    if unknown:
        parser.error(f"unknown arm(s): {sorted(unknown)}")

    env = load_env()
    api_key = env.get("OPENAI_API_KEY", "")
    if not api_key:
        print("no OPENAI_API_KEY in .env", file=sys.stderr)
        return 2
    model = args.model or env.get("GLANCE_MODEL", "gpt-5-mini")

    import openai
    client = openai.OpenAI(api_key=api_key, base_url=env.get("GLANCE_BASE_URL"))

    base = Ledger(path=args.ledger.resolve(), limit_usd=float("inf"))
    spent_before = base.read().get("spent_usd", 0.0)
    ledger = Ledger(path=args.ledger.resolve(), limit_usd=spent_before + args.budget)

    structure_src = (REPO_ROOT / "probe" / "structure.js").read_text()
    from playwright.sync_api import sync_playwright

    results: dict[str, list[dict[str, Any]]] = {
        name: [] for name in ARM_NAMES}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = None
        page = None
        try:
            for arm, structured, hint_text, opts in ARMS:
                if arm not in selected:
                    continue
                print(f"\n=== {arm}")
                for task in tasks:
                    task = OverlayTask(task.task_id, task.url, task.goal,
                                       task.success_js, args.max_steps)
                    # A fresh context per task, not just cleared cookies.
                    #
                    # clear_cookies() leaves localStorage intact, and consent
                    # state lives there. In the first run, arm B dismissed
                    # CNN's dialog on its first task and the next two CNN tasks
                    # then loaded with no overlay at all — they inherited the
                    # benefit instead of testing it, and inflated the result
                    # from 2 genuine wins to a reported +50 points.
                    if page is not None:
                        page.close()
                    if context is not None:
                        context.close()
                    context = browser.new_context(
                        viewport={"width": 1280, "height": 720}, locale="en-GB",
                        user_agent=("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                                    "Chrome/140.0.0.0 Safari/537.36"))
                    page = context.new_page()
                    if args.replay:
                        har = REPO_ROOT / "data" / "har" / f'{task.task_id.split("-")[0]}.har'
                        # not_found="abort" so a request the recording lacks
                        # fails instead of quietly reaching the live site.
                        context.route_from_har(har, not_found="abort")
                    try:
                        record = run_task(page, client, model, task, structured,
                                          structure_src, ledger,
                                          hint_text=hint_text, opts=opts)
                    except BudgetExceeded as exc:
                        print(f"  budget cap: {exc}")
                        raise
                    results[arm].append(record)
                    print(f"  {'PASS' if record['succeeded'] else 'fail'}  "
                          f"{task.task_id:<24} steps={record['steps']} "
                          f"obs={record['observation_tokens_mean']:>6} "
                          f"blocked={record['blocked_at_start']}")
        except BudgetExceeded:
            pass
        finally:
            context.close()
            browser.close()

    def summarise(arm: str) -> dict[str, Any]:
        rows = results[arm]
        if not rows:
            return {"arm": arm, "tasks": 0}
        return {
            "arm": arm, "tasks": len(rows),
            "succeeded": sum(1 for r in rows if r["succeeded"]),
            "success_rate": round(sum(1 for r in rows if r["succeeded"]) / len(rows), 4),
            "steps_mean": round(statistics.mean(r["steps"] for r in rows), 2),
            "observation_tokens_mean": round(
                statistics.mean(r["observation_tokens_mean"] or 0 for r in rows), 1),
        }

    report = {"model": model, "replay": bool(args.replay),
              "arms": [summarise(n) for n in ARM_NAMES],
              "spent_usd": round(base.read().get("spent_usd", 0.0) - spent_before, 4),
              "per_task": results}

    a, b = report["arms"][0], report["arms"][1]
    if a.get("tasks") and b.get("tasks"):
        report["success_delta"] = round(b["success_rate"] - a["success_rate"], 4)
        paired = {r["task_id"]: r["succeeded"] for r in results["A_flat"]}
        both = sum(1 for r in results["B_structured"]
                   if r["succeeded"] and paired.get(r["task_id"]))
        only_b = sum(1 for r in results["B_structured"]
                     if r["succeeded"] and not paired.get(r["task_id"]))
        only_a = sum(1 for tid, ok in paired.items() if ok and
                     not next((r["succeeded"] for r in results["B_structured"]
                               if r["task_id"] == tid), False))
        report["paired"] = {"both": both, "only_A": only_a, "only_B": only_b}

    args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("\n" + json.dumps({k: v for k, v in report.items() if k != "per_task"}, indent=2))
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
