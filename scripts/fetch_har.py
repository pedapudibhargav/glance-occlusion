"""Download and unpack the HAR benchmark.

The recordings are 399 MB uncompressed and 126 MB compressed, which is over
GitHub's per-file limit, so they are archived on Zenodo and fetched on demand.
Set GLANCE_HAR_URL to the archive URL from the Zenodo record.
"""
from __future__ import annotations

import os
import sys
import tarfile
import urllib.request
from pathlib import Path

DEST = Path("data/har")
URL = os.environ.get("GLANCE_HAR_URL", "")


def main() -> int:
    if not URL:
        print("Set GLANCE_HAR_URL to the archive URL from the Zenodo record.",
              file=sys.stderr)
        return 2
    DEST.mkdir(parents=True, exist_ok=True)
    archive = DEST.parent / "glance-occlusion-har-v1.tar.gz"
    if not archive.exists():
        print(f"downloading {URL}")
        urllib.request.urlretrieve(URL, archive)
    print(f"unpacking into {DEST}")
    with tarfile.open(archive) as tar:
        # filter="data" refuses absolute paths and traversal outside the
        # destination; the archive is third-party content once published.
        tar.extractall(DEST.parent, filter="data")
    print("done. Now run: python scripts/replay_check.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
