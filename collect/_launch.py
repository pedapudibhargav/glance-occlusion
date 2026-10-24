"""Shared launcher for the two collection passes.

``run_structure`` and ``run_pixel`` differ only in the observation mode and the
output directory, so the subprocess plumbing lives here once.

The benchmark is launched as a subprocess rather than imported because its
runner chdirs to its own project root and spawns a child process per task.
Giving the child the GLANCE bootstrap on ``PYTHONPATH`` is what gets the probe
into every one of those processes.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BOOTSTRAP_DIR = REPO_ROOT / "collect" / "bootstrap"
DEFAULT_BENCH_DIR = REPO_ROOT / "vendor" / "ComponentBench"


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--bench-dir",
        type=Path,
        default=Path(os.environ.get("COMPONENTBENCH_DIR", DEFAULT_BENCH_DIR)),
        help="ComponentBench checkout (default: vendor/ComponentBench)",
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("COMPONENTBENCH_URL", "http://127.0.0.1:3002"),
        help="ComponentBench site URL",
    )
    parser.add_argument(
        "--cdp-url",
        default=os.environ.get("GLANCE_CDP_URL", ""),
        help=(
            "Chrome debugging endpoint, e.g. http://127.0.0.1:9222. Without it "
            "the CDP-sourced features (AX node, paint order, pending requests) "
            "are null and the AX timeout guard cannot run."
        ),
    )
    parser.add_argument("--out", type=Path, default=None, help="Output JSONL path")
    parser.add_argument("--run-id", default="", help="Run id recorded on every row")
    parser.add_argument("--model-id", default="", help="Passed through to the benchmark")
    parser.add_argument("--libraries", default="", help="antd,mui,mantine")
    parser.add_argument("--canonical-types", default="")
    parser.add_argument("--task-ids", default="")
    parser.add_argument("--max-tasks", type=int, default=0)
    parser.add_argument("--max-steps", type=int, default=0)
    parser.add_argument("--benchmark-version", default="v1", choices=["v1", "v2"])
    parser.add_argument("--shard-id", type=int, default=0)
    parser.add_argument("--num-shards", type=int, default=1)
    parser.add_argument("--headless", action="store_true", default=True)
    parser.add_argument("--no-headless", dest="headless", action="store_false")
    parser.add_argument(
        "--ax-mode",
        default="partial",
        choices=["partial", "full"],
        help=(
            "'partial' reads only the target's AX node and is what collection "
            "should use. 'full' calls getFullAXTree — the call with the "
            "pathological latency tail; use it for the latency experiment."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the command and the environment, run nothing",
    )


def build_command(args: argparse.Namespace, mode: str) -> list[str]:
    cmd = [
        sys.executable,
        "scripts/run_benchmark.py",
        "--mode",
        mode,
        "--base_url",
        args.base_url,
        "--benchmark_version",
        args.benchmark_version,
        "--shard_id",
        str(args.shard_id),
        "--num_shards",
        str(args.num_shards),
    ]
    if args.headless:
        cmd.append("--headless")
    if args.libraries:
        cmd += ["--libraries", args.libraries]
    if args.canonical_types:
        cmd += ["--canonical_types", args.canonical_types]
    if args.task_ids:
        cmd += ["--task_ids", args.task_ids]
    if args.max_tasks:
        cmd += ["--max_tasks", str(args.max_tasks)]
    if args.max_steps:
        cmd += ["--max_steps", str(args.max_steps)]
    if args.model_id:
        cmd += ["--model_id", args.model_id]
    return cmd


def build_env(args: argparse.Namespace, out_path: Path, structure_only: bool, mode: str) -> dict[str, str]:
    env = dict(os.environ)
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = os.pathsep.join(
        p for p in (str(BOOTSTRAP_DIR), str(REPO_ROOT), existing_pythonpath) if p
    )
    env["GLANCE_COLLECT"] = "1"
    env["GLANCE_REPO_ROOT"] = str(REPO_ROOT)
    env["GLANCE_OUT"] = str(out_path)
    env["GLANCE_CDP_URL"] = args.cdp_url
    env["GLANCE_RUN_ID"] = args.run_id or f"{mode}-{int(time.time())}"
    env["GLANCE_MODE"] = mode
    env["GLANCE_AX_MODE"] = args.ax_mode
    env["GLANCE_STRUCTURE_ONLY"] = "1" if structure_only else "0"
    return env


def launch(args: argparse.Namespace, mode: str, out_path: Path, structure_only: bool) -> int:
    bench_dir: Path = args.bench_dir
    if not (bench_dir / "scripts" / "run_benchmark.py").exists():
        print(
            f"ComponentBench not found at {bench_dir}.\n"
            "Run scripts/setup_componentbench.sh first, or pass --bench-dir.",
            file=sys.stderr,
        )
        return 2

    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = build_command(args, mode)
    env = build_env(args, out_path, structure_only, mode)

    print(f"mode={mode} structure_only={structure_only}")
    print(f"rows → {out_path}")
    print("cmd:", " ".join(cmd))
    if not args.cdp_url:
        print(
            "WARNING: --cdp-url not set. CDP-sourced features will be null and "
            "the AX timeout guard is inactive. Launch Chrome with "
            "--remote-debugging-port=9222 and pass its URL.",
            file=sys.stderr,
        )
    if args.dry_run:
        for key in sorted(k for k in env if k.startswith("GLANCE_")):
            print(f"  {key}={env[key]}")
        return 0

    return subprocess.call(cmd, cwd=str(bench_dir), env=env)
