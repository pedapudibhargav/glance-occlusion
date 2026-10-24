"""Does a replayed page still present the same overlay as when recorded?

A HAR is only a usable fixture if replaying it reproduces the condition the
experiment is about. Consent dialogs are the hardest case for this: many are
injected by script from a third-party host, and some decide what to show from
geo headers or a cookie that was never recorded.

So this verifies before trusting. For each recording it serves the context
entirely from the HAR, builds the same observation, and compares the blocked
count and overlay label against what was captured live. A site that does not
reproduce is excluded from the deterministic benchmark rather than quietly
contributing a different condition.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import sync_playwright  # noqa: E402

HAR_DIR = Path("data/har")
STRUCTURE_JS = Path("probe/structure.js").read_text(encoding="utf-8")

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")

# A recording counts as reproducing when the overlay is present and the
# blocked count is within this fraction of the live figure. Page content
# varies between a record and a replay — a carousel lands on a different
# slide, a lazy list renders a different number of cards — so demanding an
# exact match would reject usable fixtures. The overlay has to be present
# either way; that is not a tolerance.
TOLERANCE = 0.35


def main() -> int:
    manifest = json.loads((HAR_DIR / "manifest.json").read_text(encoding="utf-8"))
    out: dict[str, dict] = {}
    usable: list[str] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        for key, rec in manifest.items():
            if "error" in rec:
                out[key] = {"skipped": rec["error"]}
                continue
            ctx = browser.new_context(viewport={"width": 1280, "height": 720},
                                      locale="en-GB", user_agent=UA)
            page = ctx.new_page()
            try:
                # Everything is served from the recording; `not_found="abort"`
                # means a request the HAR does not contain fails rather than
                # silently reaching the live site, which would defeat the point.
                ctx.route_from_har(HAR_DIR / rec["har"], not_found="abort")
                page.goto(rec["url"], wait_until="commit", timeout=30000)
                page.wait_for_timeout(5000)
                page.evaluate(STRUCTURE_JS)
                got = page.evaluate("() => window.__glanceStructure.build()")

                live = rec["live_blocked"]
                delta = abs(got["blocked"] - live) / max(live, 1)
                ok = bool(got["overlay_present"]) and delta <= TOLERANCE
                out[key] = {
                    "live_blocked": live,
                    "replay_blocked": got["blocked"],
                    "delta_fraction": round(delta, 3),
                    "live_overlay": rec["live_overlay"],
                    "replay_overlay": got["overlay_label"],
                    "overlay_present_on_replay": bool(got["overlay_present"]),
                    "usable": ok,
                }
                if ok:
                    usable.append(key)
                print(f'{key:<14} live={live:<4} replay={got["blocked"]:<4} '
                      f'delta={delta:>5.0%}  overlay={bool(got["overlay_present"])!s:<5} '
                      f'{"USABLE" if ok else "excluded"}')
            except Exception as exc:
                out[key] = {"error": f"{type(exc).__name__}: {exc}", "usable": False}
                print(f"{key:<14} ERROR {type(exc).__name__}: {str(exc)[:60]}")
            finally:
                ctx.close()
        browser.close()
    out["_usable"] = usable
    Path("data/replay_check.json").write_text(json.dumps(out, indent=2),
                                              encoding="utf-8")
    print(f"\n{len(usable)}/{len(manifest)} recordings reproduce their overlay: {usable}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
