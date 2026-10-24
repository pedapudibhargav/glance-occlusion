"""Does Playwright's own AI snapshot see into cross-origin frames?

The cross-frame contribution only holds if mainstream observation formats miss
frame content. Playwright's injected snapshot already understands frame-scoped
refs (`refPrefix = 'f' + frameSeq` in the driver bundle), so the question is
whether the snapshot an agent actually receives *includes* child frames or only
the document it was called on.

This answers it by evidence rather than by reading a bundle: take the snapshot
the way a Playwright-MCP-style agent would, then ask whether the string
contains the consent button that the page's own frames are known to hold.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import sync_playwright  # noqa: E402

from eval.overlay_sites import tolerant_goto  # noqa: E402

SITES = {
    "bbc": ("https://www.bbc.com/news", "Accept and Continue"),
    "spiegel": ("https://www.spiegel.de/", "Consent and continue"),
    "ft": ("https://www.ft.com/", "Accept Cookies"),
    "skysports": ("https://www.skysports.com/", "Accept all"),
}


def snapshot(page) -> str:
    """The snapshot Playwright MCP actually serves to an agent.

    `mode="ai"` is the important part. The default mode is a plain aria
    snapshot of one document; "ai" mode is the one that assigns `eN` refs,
    prefixes them per frame (`refPrefix = "f" + frameSeq` in the driver), and —
    since playwright#35396 — emits iframe nodes. Measuring the default mode and
    reporting it as what agents receive would have been wrong.
    """
    return page.aria_snapshot(mode="ai")


def main() -> int:
    out = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        for name, (url, button) in SITES.items():
            context = browser.new_context(
                viewport={"width": 1280, "height": 720}, locale="en-GB")
            page = context.new_page()
            try:
                tolerant_goto(page, url)
                top = snapshot(page)
                in_top = button.lower() in top.lower()
                # The same button, reached by walking frames ourselves.
                in_any_frame = False
                for frame in page.frames:
                    if frame == page.main_frame:
                        continue
                    try:
                        text = frame.locator("body").aria_snapshot(mode="ai")
                    except Exception:
                        continue
                    if button.lower() in text.lower():
                        in_any_frame = True
                        break
                out[name] = {
                    "button": button,
                    "in_top_document_snapshot": in_top,
                    "in_some_child_frame": in_any_frame,
                    "frames": len(page.frames),
                    "top_snapshot_chars": len(top),
                    "top_mentions_iframe": "iframe" in top.lower(),
                }
                print(f"{name:<10} top={in_top!s:<5} frame={in_any_frame!s:<5} "
                      f"frames={len(page.frames)}")
            except Exception as exc:
                out[name] = {"error": f"{type(exc).__name__}: {exc}"}
                print(f"{name:<10} ERROR {type(exc).__name__}")
            finally:
                context.close()
        browser.close()
    Path("data/playwright_frame_check.json").write_text(
        json.dumps(out, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
