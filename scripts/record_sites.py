"""Record the overlay sites to HAR so the benchmark stops moving.

The main limitation of every result in this project is that it was measured on
live sites. Five of 11 showed no overlay when re-probed minutes after
reporting 17-182 blocked elements, consent dialogs vary by geography and A/B
split, and nobody else can rerun any of it.

Playwright can record a context's network traffic to HAR and later serve a
context entirely from that file. That makes a run reproducible, shareable, and
independent of whether a site is showing its dialog today. It is also the
thing a reviewer asks for first.

Recording and replay are separate scripts on purpose: `scripts/replay_check.py`
verifies that a replayed page still presents the same overlay before any
result is computed from it.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import sync_playwright  # noqa: E402

from eval.overlay_sites import SITES, tolerant_goto  # noqa: E402

HAR_DIR = Path("data/har")
STRUCTURE_JS = Path("probe/structure.js").read_text(encoding="utf-8")

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")


def main() -> int:
    HAR_DIR.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, dict] = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        for site in SITES:
            har = HAR_DIR / f"{site.key}.har"
            ctx = browser.new_context(
                viewport={"width": 1280, "height": 720}, locale="en-GB",
                user_agent=UA, record_har_path=str(har), record_har_mode="full")
            page = ctx.new_page()
            try:
                tolerant_goto(page, site.url, settle_ms=5000)
                page.evaluate(STRUCTURE_JS)
                live = page.evaluate("() => window.__glanceStructure.build()")
                manifest[site.key] = {
                    "url": site.url,
                    "har": har.name,
                    # The state at record time. Replay has to reproduce this or
                    # the recording is not usable as a fixture.
                    "live_blocked": live["blocked"],
                    "live_overlay": live["overlay_label"],
                    "live_total": live["total"],
                    "targets": [list(t) for t in site.targets],
                }
                # Record the destination pages as well, not just the landing
                # page.
                #
                # The first version recorded only the landing page, and every
                # replayed task then failed in both arms — 0/17 — because the
                # success condition is a URL change and the destination was
                # never in the HAR. Replay aborts a request the recording does
                # not contain, so the agent could dismiss the overlay, click
                # the right link, and still fail. Read as a number it looked
                # like a clean refutation of the whole result.
                recorded = 0
                for _, fragment in site.targets:
                    for candidate in (f"{site.url.rstrip('/')}/{fragment}",
                                      f"{site.url.rstrip('/')}/{fragment}/"):
                        try:
                            page.goto(candidate, wait_until="commit", timeout=20000)
                            page.wait_for_timeout(2500)
                            recorded += 1
                            break
                        except Exception:
                            continue
                manifest[site.key]["targets_recorded"] = recorded
                print(f'{site.key:<14} blocked={live["blocked"]:<4} '
                      f'targets_recorded={recorded}/{len(site.targets)}  '
                      f'overlay={str(live["overlay_label"])[:30]}')
            except Exception as exc:
                manifest[site.key] = {"error": f"{type(exc).__name__}: {exc}"}
                print(f"{site.key:<14} ERROR {type(exc).__name__}")
            finally:
                # Closing the context is what flushes the HAR to disk.
                ctx.close()
        browser.close()
    (HAR_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2),
                                           encoding="utf-8")
    sizes = sum(f.stat().st_size for f in HAR_DIR.glob("*.har"))
    print(f"\n{len(list(HAR_DIR.glob('*.har')))} HAR file(s), {sizes/1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
