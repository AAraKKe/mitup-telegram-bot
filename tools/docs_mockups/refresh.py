"""Regenerate every generated showcase of the user guide: uv run python tools/docs_mockups/refresh.py

Dumps the screens, fills the mock slots, builds the site, measures the annotations and builds again.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import dump_screens  # noqa: E402
import measure  # noqa: E402
import pages  # noqa: E402


def build():
    subprocess.run(["uv", "run", "mb", "docs", "build"], cwd=HERE.parents[1], check=True, capture_output=True)


if __name__ == "__main__":
    dump_screens.main()
    pages.fill_all()
    build()
    for page in pages.PAGES:
        measure.measure(page)
    build()
    print("showcases refreshed")
