"""What three observation formats actually contain, on the same pages.

Written after a retraction. `scripts/check_pw_frames.py` was first run with
Playwright's *default* aria snapshot and appeared to show that Playwright
cannot see into cross-origin frames. It can: `mode="ai"` — the mode Playwright
MCP serves to agents — emits frame-qualified refs and the BBC consent button
arrives as `button "Accept and Continue" [ref=f4e22]`. The cross-frame problem
is already solved there.

So the question worth measuring is not "can anything see the frame" but "which
of the formats agents are actually given can", and separately whether any of
them represents occlusion at all.

  AX    — CDP Accessibility.getFullAXTree on the top document. The route
          BrowserGym and most AX-tree agent work uses.
  PW    — page.aria_snapshot(mode="ai"). What Playwright MCP serves.
  GL    — probe/structure.js: this project's structured observation.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import sync_playwright  # noqa: E402

from eval.overlay_sites import tolerant_goto  # noqa: E402

STRUCTURE_JS = Path("probe/structure.js").read_text(encoding="utf-8")

SITES = [
    ("bbc", "https://www.bbc.com/news", "Accept and Continue"),
    ("spiegel", "https://www.spiegel.de/", "Consent and continue"),
    ("ft", "https://www.ft.com/", "Accept Cookies"),
    ("skysports", "https://www.skysports.com/", "Accept all"),
    ("cnn", "https://edition.cnn.com/", None),
    ("lemonde", "https://www.lemonde.fr/", None),
]

# Words a format would have to use to tell an agent something is covering the
# page. Checked case-insensitively against the whole serialisation.
OCCLUSION_WORDS = ("blocked", "occluded", "covered", "is on top", "unreachable",
                   "behind the overlay", "intercept")


def main() -> int:
    rows = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        for name, url, button in SITES:
            ctx = browser.new_context(viewport={"width": 1280, "height": 720},
                                      locale="en-GB")
            page = ctx.new_page()
            row: dict[str, object] = {}
            try:
                tolerant_goto(page, url)

                pw_snap = page.aria_snapshot(mode="ai")

                session = ctx.new_cdp_session(page)
                session.send("Accessibility.enable")
                ax = session.send("Accessibility.getFullAXTree")
                ax_blob = json.dumps(ax)

                page.evaluate(STRUCTURE_JS)
                gl = page.evaluate("() => window.__glanceStructure.build()")

                row["blocked_elements"] = gl["blocked"]
                row["overlay_present"] = bool(gl.get("overlay_present"))
                row["ax_nodes"] = len(ax["nodes"])
                for key, blob in (("ax", ax_blob), ("pw", pw_snap), ("gl", gl["text"])):
                    low = blob.lower()
                    row[f"{key}_says_occluded"] = any(w in low for w in OCCLUSION_WORDS)
                    row[f"{key}_chars"] = len(blob)
                    if button:
                        row[f"{key}_has_dismiss_button"] = button.lower() in low
                print(f'{name:<10} blocked={gl["blocked"]:<4} '
                      f'dismiss in ax/pw: {row.get("ax_has_dismiss_button")}/'
                      f'{row.get("pw_has_dismiss_button")}  '
                      f'occlusion in ax/pw/gl: {row["ax_says_occluded"]}/'
                      f'{row["pw_says_occluded"]}/{row["gl_says_occluded"]}')
            except Exception as exc:
                row = {"error": f"{type(exc).__name__}: {exc}"}
                print(f"{name:<10} ERROR {type(exc).__name__}")
            finally:
                rows[name] = row
                ctx.close()
        browser.close()
    Path("data/format_comparison.json").write_text(json.dumps(rows, indent=2),
                                                   encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
