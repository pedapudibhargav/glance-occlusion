"""Strip cookies and credential headers from the HAR recordings.

The recordings are made in a fresh context against public news sites with no
sign-in, so they hold tracking and consent cookies rather than anybody's
credentials. They are still not publishable as captured: a HAR carries every
request and response header verbatim, and republishing `Cookie` and
`Set-Cookie` values serves no research purpose.

Replay does not need them. Playwright matches a recorded entry by method and
URL, not by cookie, and the consent dialogs we study are driven by scripts in
the page rather than by a cookie the recording would replay. That claim is
checked rather than assumed: `scripts/replay_check.py` is re-run after this and
must still report the same covered-element counts.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HAR_DIR = Path("data/har")

# Headers removed wherever they appear, request or response.
DROP = {
    "cookie", "set-cookie", "cookie2", "set-cookie2",
    "authorization", "proxy-authorization",
    "x-api-key", "x-auth-token", "x-csrf-token", "x-xsrf-token",
}


def scrub(entry: dict) -> int:
    removed = 0
    for side in ("request", "response"):
        part = entry.get(side)
        if not isinstance(part, dict):
            continue
        headers = part.get("headers")
        if isinstance(headers, list):
            keep = [h for h in headers if h.get("name", "").lower() not in DROP]
            removed += len(headers) - len(keep)
            part["headers"] = keep
        # HAR also keeps a parsed cookie array alongside the raw headers.
        if part.get("cookies"):
            removed += len(part["cookies"])
            part["cookies"] = []
    return removed


def main() -> int:
    files = sorted(HAR_DIR.glob("*.har"))
    if not files:
        print(f"no .har files in {HAR_DIR}", file=sys.stderr)
        return 2
    total = 0
    for path in files:
        data = json.loads(path.read_text(encoding="utf-8"))
        removed = sum(scrub(e) for e in data["log"]["entries"])
        # Playwright writes a creator/browser block; nothing identifying, but
        # the local page list can carry the operator's own URLs.
        data["log"].pop("comment", None)
        path.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
        total += removed
        print(f"{path.name:<22} removed {removed:>5} cookie/credential field(s)")
    print(f"\ntotal removed: {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
