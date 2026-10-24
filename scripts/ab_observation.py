"""A/B two observation formats on the same tasks, with the same model.

Arm A is the harness's standard accessibility-tree observation — what
BrowserGym, Playwright MCP and CDP all produce: serialise the tree, hand it
over, and find out about staleness when an action fails.

Arm B is the verified list in ``probe/verified_observation.js``: only elements
that exist right now and that a click would actually reach, each with a name
worth choosing by, duplicates disambiguated by context.

Everything else is held fixed — same tasks in the same order, same model, same
reasoning effort, same action space, same step cap. The only variable is what
the model is shown.

What it measures, per arm: task success rate, observation tokens, steps taken,
and spend. The claim being tested is that removing elements the agent could
not have acted on anyway costs nothing in success and saves most of the
observation.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from collect.budget import Ledger, price_for  # noqa: E402
from scripts.gate_run import BENCH_DIR, REPO_ROOT, install_agent_config, load_env, sample_tasks  # noqa: E402


def run_arm(
    arm: str,
    tasks: list[str],
    model: str,
    base_url: str,
    api_key: str,
    budget_usd: float,
    ledger_path: Path,
    max_steps: int,
    base_site: str,
    verified: bool,
    keep_unreachable: bool = False,
) -> dict[str, Any]:
    agent_config = install_agent_config(model)
    marker_path = REPO_ROOT / "data" / "raw" / "ab" / f"{arm}.bootstrap_ok"
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    marker_path.unlink(missing_ok=True)
    rows_path = REPO_ROOT / "data" / "raw" / "ab" / f"{arm}_rows.jsonl"
    stats_path = REPO_ROOT / "data" / "raw" / "ab" / f"{arm}_obs_stats.jsonl"
    rows_path.parent.mkdir(parents=True, exist_ok=True)
    rows_path.unlink(missing_ok=True)
    stats_path.unlink(missing_ok=True)

    env = dict(os.environ)
    env.update(
        {
            "PYTHONPATH": os.pathsep.join(
                [str(REPO_ROOT / "collect" / "bootstrap"), str(REPO_ROOT), env.get("PYTHONPATH", "")]
            ),
            "GLANCE_COLLECT": "1",
            "GLANCE_STRUCTURE_ONLY": "1",
            "GLANCE_REPO_ROOT": str(REPO_ROOT),
            "GLANCE_OUT": str(rows_path),
            "GLANCE_OBS_STATS": str(stats_path),
            "GLANCE_CDP_PORT": "",
            "GLANCE_MODE": "ax_tree",
            # An ABSOLUTE ledger cap, not this run's increment.
            #
            # collect.budget.install() reads GLANCE_BUDGET_USD as the total the
            # ledger may reach and refuses to start when the ledger already
            # exceeds it. Passing the increment (0.40) against a ledger holding
            # $3.17 made it raise at import, which disabled the entire hook —
            # see registry row A30.
            "GLANCE_BUDGET_USD": f"{budget_usd:.4f}",
            "GLANCE_LEDGER": str(ledger_path),
            "GLANCE_BOOTSTRAP_MARKER": str(marker_path),
            "GLANCE_REASONING_EFFORT": "minimal",
            "GLANCE_VERIFIED_OBS": "1" if verified else "0",
            "GLANCE_KEEP_UNREACHABLE": "1" if keep_unreachable else "0",
            "VLLM_BASE_URL": base_url,
            "VLLM_API_KEY": api_key,
        }
    )

    command = [
        sys.executable, "scripts/run_benchmark.py",
        "--mode", "ax_tree",
        "--base_url", base_site,
        "--agent_config", agent_config,
        "--model_id", model,
        "--task_ids", ",".join(tasks),
        "--max_steps", str(max_steps),
        "--headless",
        "--output_dir", str(REPO_ROOT / "data" / "raw" / "ab" / arm),
    ]
    print(f"\n=== arm {arm}: verified_observation={verified}, {len(tasks)} tasks")
    subprocess.call(command, cwd=str(BENCH_DIR), env=env)
    if not marker_path.exists():
        raise SystemExit(
            f"arm {arm}: the GLANCE bootstrap never installed, so this arm ran "
            f"unprobed and its results are not comparable. Check the ledger cap "
            f"(GLANCE_BUDGET_USD is an absolute total, not an increment)."
        )
    return summarise_arm(arm, rows_path, stats_path)


def summarise_arm(arm: str, rows_path: Path, stats_path: Path) -> dict[str, Any]:
    results_dir = REPO_ROOT / "data" / "raw" / "ab" / arm
    successes, attempted = 0, 0
    # Per-task outcomes, keyed by task_id.
    #
    # The earlier version of this script reported only aggregate counts, so
    # "10/14 vs 10/14, compression is empty" had no paired test behind it and
    # no way to get one. Registry row A27 exists because of that. Keeping the
    # outcome per task makes the arms pairable and the null testable.
    per_task: dict[str, dict[str, Any]] = {}
    for path in results_dir.rglob("results.jsonl"):
        for line in path.read_text(errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            attempted += 1
            ok = bool(record.get("reward", 0) and float(record["reward"]) > 0)
            if ok:
                successes += 1
            key = record.get("task_id") or record.get("task_name") or ""
            if key:
                per_task[key] = {
                    "succeeded": ok,
                    "steps": record.get("steps"),
                    "type": record.get("canonical_type"),
                    "tier": record.get("difficulty_tier"),
                }

    rows = []
    if rows_path.exists():
        rows = [json.loads(l) for l in rows_path.read_text().splitlines() if l.strip()]

    stats = []
    if stats_path.exists():
        stats = [json.loads(l) for l in stats_path.read_text().splitlines() if l.strip()]

    def mean(values: list[float]) -> float | None:
        return round(sum(values) / len(values), 1) if values else None

    return {
        "arm": arm,
        "per_task": per_task,
        "tasks_attempted": attempted,
        "tasks_succeeded": successes,
        "task_success_rate": round(successes / attempted, 4) if attempted else None,
        "probed_steps": len(rows),
        "steps_per_task": round(len(rows) / attempted, 2) if attempted else None,
        "observation_chars_mean": mean([s["chars_after"] for s in stats]),
        "observation_chars_before_mean": mean([s["chars_before"] for s in stats]),
        "observation_lines_mean": mean([float(s["lines_after"]) for s in stats]),
        "observation_lines_before_mean": mean([float(s["lines_before"]) for s in stats]),
        "dropped_totals": {
            key: sum(s.get("dropped", {}).get(key, 0) for s in stats)
            for key in ("covered", "unnamed", "zero_area", "no_bid", "non_actionable")
        }
        if stats
        else {},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="A/B the observation format")
    parser.add_argument("--tasks", type=int, default=40)
    parser.add_argument("--max-steps", type=int, default=6)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--budget", type=float, default=None)
    parser.add_argument("--model", default="")
    parser.add_argument("--base-site", default="https://interfacegym.com")
    parser.add_argument("--arms", default="",
                        help="comma-separated arm names; default runs all three")
    parser.add_argument("--ledger", type=Path, default=REPO_ROOT / "data" / "spend_ledger.json")
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "data" / "ab_result.json")
    args = parser.parse_args()

    env = load_env()
    api_key = env.get("OPENAI_API_KEY") or env.get("ANTHROPIC_API_KEY") or ""
    if not api_key:
        print("no API key in .env", file=sys.stderr)
        return 2
    base_url = env.get("GLANCE_BASE_URL", "https://api.openai.com/v1")
    model = args.model or env.get("GLANCE_MODEL", "gpt-5-mini")

    ledger = Ledger(path=args.ledger.resolve(), limit_usd=float("inf"))
    spent_before = ledger.read().get("spent_usd", 0.0)
    # --budget is this run's allowance; the hook's cap is an absolute ledger
    # total. Conflating the two is what silently disabled the whole probe:
    # against a ledger at $3.17, a $0.40 cap made collect.budget.install()
    # raise at import, sitecustomize swallowed it, and both arms then ran
    # unprobed and unmetered. Registry row A30.
    allowance = args.budget if args.budget is not None else float(
        env.get("GLANCE_BUDGET_USD", "2.0"))
    cap = spent_before + allowance

    tasks = sample_tasks(args.tasks, seed=args.seed)
    price = price_for(model)
    print(f"model={model}  tasks={len(tasks)}  allowance=${allowance:.2f}  "
          f"already spent=${spent_before:.3f}  absolute cap=${cap:.2f}")
    if price:
        print(f"  ~${price.cost(3700, 130):.5f}/step at measured prompt sizes")

    results = []
    # C_verified_keep_covered is B with the occlusion filter alone switched
    # off: same name, actionability and zero-area filtering, but covered
    # elements stay in the observation.
    arm_specs = (("A_standard", False, False),
                 ("B_verified", True, False),
                 ("C_verified_keep_covered", True, True))
    if args.arms:
        wanted = {a.strip() for a in args.arms.split(",") if a.strip()}
        arm_specs = tuple(a for a in arm_specs if a[0] in wanted)
    for arm, verified, keep_unreachable in arm_specs:
        results.append(
            run_arm(
                arm=arm,
                tasks=tasks,
                model=model,
                base_url=base_url,
                api_key=api_key,
                budget_usd=cap,
                ledger_path=args.ledger.resolve(),
                max_steps=args.max_steps,
                base_site=args.base_site,
                verified=verified,
                keep_unreachable=keep_unreachable,
            )
        )
        spend = ledger.read().get("spent_usd", 0.0)
        print(f"    spend now ${spend:.3f} of ${cap:.2f}")
        if spend >= cap:
            print("    budget cap reached; stopping")
            break

    report = {
        "model": model,
        "tasks": len(tasks),
        "max_steps": args.max_steps,
        "spent_usd": round(ledger.read().get("spent_usd", 0.0) - spent_before, 4),
        "arms": results,
    }

    if len(results) >= 2:
        a, b = results[0], results[1]
        # Paired comparison on the tasks both arms actually ran.
        shared = sorted(set(a["per_task"]) & set(b["per_task"]))
        only_b = sum(1 for k in shared
                     if b["per_task"][k]["succeeded"] and not a["per_task"][k]["succeeded"])
        only_a = sum(1 for k in shared
                     if a["per_task"][k]["succeeded"] and not b["per_task"][k]["succeeded"])
        n = only_a + only_b
        p_value = (min(1.0, 2 * sum(math.comb(n, k) for k in range(min(only_a, only_b) + 1))
                       / 2 ** n) if n else 1.0)
        report["paired"] = {
            "tasks_both_arms_ran": len(shared),
            "only_B": only_b, "only_A": only_a,
            "mcnemar_exact_two_sided_p": round(p_value, 5),
            # What the comparison can and cannot detect at this n. A null
            # result without this is not a result.
            "note": ("A null here means no detectable difference at this sample "
                     "size, not equivalence."),
        }
        if a["task_success_rate"] is not None and b["task_success_rate"] is not None:
            report["success_delta"] = round(b["task_success_rate"] - a["task_success_rate"], 4)
        if b.get("observation_chars_mean") and b.get("observation_chars_before_mean"):
            report["observation_reduction"] = round(
                1 - b["observation_chars_mean"] / b["observation_chars_before_mean"], 4
            )

    args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("\n" + json.dumps(report, indent=2))
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
