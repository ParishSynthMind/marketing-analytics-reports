"""Render the README images headless with Playwright (no browser window opens).

  python scripts/render_media.py      (needs `pip install playwright` and `playwright install chromium`)

Reads the real output of a mock run (out/mock) and the recorded live run (docs/live-run-2026-11-04.txt):
1. media/dashboard.png      the HTML dashboard as written by `python -m mkt_report run --mock`
2. media/pdf-page1.png      page 1 of the PDF report (rendered with pdftoppm)
3. media/live-run.png       the terminal output of the live Claude summary run
"""
import html
import subprocess
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT, MEDIA = ROOT / "out" / "mock", ROOT / "media"


def terminal_page(text: str) -> str:
    return f"""<html><body style="margin:0;background:#fff">
<div id="wrap" style="display:inline-block;padding:24px;background:#fff">
<div style="background:#1e1e1e;border-radius:10px;padding:18px 22px;max-width:1100px">
<div style="color:#9cdcfe;font:13px Menlo,monospace;margin-bottom:10px">$ python -m mkt_report run --mock</div>
<pre style="margin:0;color:#e6e6e6;font:13px/1.5 Menlo,monospace;white-space:pre-wrap">{html.escape(text)}</pre></div></div></body></html>"""


def main():
    MEDIA.mkdir(exist_ok=True)
    pdf = sorted(OUT.glob("marketing-report-*.pdf"))[-1]
    subprocess.run(["pdftoppm", "-png", "-r", "110", "-f", "1", "-l", "1", "-singlefile", str(pdf), str(MEDIA / "pdf-page1")], check=True)
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": 1180, "height": 900}, device_scale_factor=2)
        page.goto((OUT / "dashboard.html").as_uri())
        page.screenshot(path=str(MEDIA / "dashboard.png"), full_page=False)
        page.set_content(terminal_page((ROOT / "docs" / "live-run-2026-11-04.txt").read_text()))
        page.locator("#wrap").screenshot(path=str(MEDIA / "live-run.png"))
        b.close()
    print("wrote", ", ".join(sorted(x.name for x in MEDIA.glob("*.png"))))


if __name__ == "__main__":
    sys.exit(main())
