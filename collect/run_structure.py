"""Collect structure-only episodes — the rows the router is trained on.

Every action is dispatched against the structured layer alone: the
accessibility tree is in the prompt and the screenshot is not (see
``bootstrap/sitecustomize.py`` for why that needs forcing). Each attempted
action produces one labelled row.

Use a cheap model here. The router is learning about the *page*, not about the
model, and what matters is steps per dollar.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from ._launch import add_common_args, launch


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    add_common_args(parser)
    parser.add_argument(
        "--keep-screenshot",
        action="store_true",
        help=(
            "Leave ComponentBench's screenshot in the prompt. This is NOT a "
            "structure-only run and its rows do not satisfy the label "
            "definition; it exists only to reproduce the benchmark's own "
            "published AX-tree numbers."
        ),
    )
    args = parser.parse_args()

    out = args.out or Path("data/raw/structure/rows.jsonl")
    if args.keep_screenshot:
        print(
            "WARNING: --keep-screenshot leaves pixels in the observation. "
            "These rows are not structure-only."
        )
    return launch(args, mode="ax_tree", out_path=out, structure_only=not args.keep_screenshot)


if __name__ == "__main__":
    raise SystemExit(main())
