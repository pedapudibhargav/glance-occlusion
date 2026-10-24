"""Collect paired pixel episodes — the escalation outcome and the cost baseline.

Same tasks as the structure-only pass, in pixel mode. These rows answer "would
a screenshot have helped here?" once joined on ``(task_id, step_index)``, and
they supply the always-pixel cost point.

Pixel mode uses the coordinate action space, so an action names no element.
Rows from this pass therefore carry the task-level outcome and the token cost;
the per-element features come from the structure pass.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from ._launch import add_common_args, launch


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    add_common_args(parser)
    args = parser.parse_args()

    out = args.out or Path("data/raw/pixel/rows.jsonl")
    return launch(args, mode="pixel", out_path=out, structure_only=False)


if __name__ == "__main__":
    raise SystemExit(main())
