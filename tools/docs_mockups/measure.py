"""Set every annotation's `top` from the built page.

For each `.mitup-annotated` block a page holds, the element carrying data-note="i" is measured in
headless Chrome and the span with data-for="i" gets its vertical centre. Build the site first.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
SCRIPT = """<script>
(() => {
  const out = [];
  document.querySelectorAll('.mitup-annotated').forEach(block => {
    const base = block.getBoundingClientRect().top;
    const tops = {};
    block.querySelectorAll('[data-note]').forEach(el => {
      const r = el.getBoundingClientRect();
      tops[el.dataset.note] = Math.round(r.top + r.height / 2 - base);
    });
    out.push(tops);
  });
  document.body.insertAdjacentHTML('beforeend', '<pre id="mitup-measure">' + JSON.stringify(out) + '</pre>');
})();
</script>"""


def measure(page: Path):
    built = ROOT / "site" / page.relative_to(ROOT / "docs").with_suffix("") / "index.html"
    probe = built.with_name("measure.html")
    probe.write_text(built.read_text().replace("</body>", SCRIPT + "</body>"))
    try:
        dom = subprocess.run(
            [
                CHROME,
                "--headless=new",
                "--disable-gpu",
                "--window-size=1200,4000",
                "--dump-dom",
                probe.resolve().as_uri(),
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    finally:
        probe.unlink()
    blocks = json.loads(re.findall(r'<pre id="mitup-measure">(.*?)</pre>', dom)[-1])
    text = page.read_text()
    parts = text.split('<div class="mitup-annotated">')
    assert len(parts) - 1 == len(blocks), (
        f"{page}: {len(parts) - 1} blocks in the markdown, {len(blocks)} in the built page"
    )
    for i, tops in enumerate(blocks, start=1):
        for index, top in tops.items():
            parts[i] = re.sub(rf'(data-for="{index}" style="top: )-?\d+px', rf"\g<1>{top}px", parts[i])
    page.write_text('<div class="mitup-annotated">'.join(parts))
    print(page.relative_to(ROOT), blocks)


if __name__ == "__main__":
    for arg in sys.argv[1:]:
        measure(Path(arg).resolve())
