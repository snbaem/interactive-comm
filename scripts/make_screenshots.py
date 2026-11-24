"""Regenerate the images in ``docs/images/`` from synthetic letters.

Usage (from the repository root, after ``pip install -e .``)::

    python scripts/make_screenshots.py [--out docs/images] [--browser PATH]

The layout images come from ``icsel show-layout``. The browser UI and the portfolio report
are captured with a headless Chromium-based browser (Edge, Chrome or Chromium) if one is
installed; without one, those two images are skipped with a message. No package is needed
for that, and nothing leaves this machine: the pages are served on 127.0.0.1.
"""

from __future__ import annotations

import argparse
import contextlib
import html
import os
import re
import shutil
import subprocess
import tempfile
import threading
from collections.abc import Iterator
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

from icsel.cli import main as icsel
from icsel.web import make_server

SAMPLE = "L04-subject-right-3line"
#: Opens the sample letter as soon as the page has loaded its sample list.
CLICK = """<script>
const t = setInterval(() => {
  const el = [...document.querySelectorAll("#samples *")].find(
    e => !e.children.length && e.textContent.includes("%s"));
  if (el) { clearInterval(t); el.click(); }
}, 100);
</script>"""
STYLE = """body{font:15px/1.5 system-ui,sans-serif;margin:32px;max-width:1040px;color:#222}
table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:4px 10px;text-align:left}
li{margin-left:20px}code{background:#f2f2f2;padding:0 3px}h2{margin-top:24px}"""


def find_browser() -> str | None:
    """Path of an installed Chromium-based browser, or ``None``."""
    for name in ("msedge", "google-chrome", "chromium", "chromium-browser", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    for var in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA"):
        for rest in (
            "Microsoft/Edge/Application/msedge.exe",
            "Google/Chrome/Application/chrome.exe",
        ):
            path = Path(os.environ.get(var, "")) / rest
            if os.environ.get(var) and path.is_file():
                return str(path)
    mac = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    return str(mac) if mac.is_file() else None


def capture(browser: str, url: str, out: Path, size: tuple[int, int]) -> None:
    """Save a headless screenshot of ``url`` to ``out``."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as profile:
        subprocess.run(
            [
                browser,
                "--headless=new",
                "--disable-gpu",
                "--no-first-run",
                "--hide-scrollbars",
                "--force-prefers-color-scheme=light",
                f"--user-data-dir={profile}",
                f"--window-size={size[0]},{size[1]}",
                "--virtual-time-budget=20000",
                f"--screenshot={out.resolve()}",
                url,
            ],
            check=True,
            timeout=120,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    if not out.is_file():
        raise RuntimeError(f"{browser} did not write {out}")


@contextlib.contextmanager
def serving(workdir: Path, pages: dict[str, bytes]) -> Iterator[str]:
    """Serve the icsel UI (sample opened automatically) plus ``pages`` on a free port."""
    server: ThreadingHTTPServer = make_server(port=0, workdir=workdir)
    base: Any = server.RequestHandlerClass

    class Handler(base):
        def _get(self) -> None:
            if self.path in pages:
                self._send(HTTPStatus.OK, pages[self.path], "text/html; charset=utf-8")
            else:
                super()._get()

        def _send(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
            if self.path == "/":
                body += (CLICK % SAMPLE).encode("utf-8")
            super()._send(status, body, content_type)

    server.RequestHandlerClass = Handler
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def report_html(markdown: str) -> bytes:
    """Just enough Markdown for ``portfolio.md``: headings, lists, tables, bold and code."""

    def inline(text: str) -> str:
        text = html.escape(re.sub(r"\\([\\`*_|<>\[\]#])", r"\1", text))
        text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
        return re.sub(r"`(.+?)`", r"<code>\1</code>", text)

    out: list[str] = []
    for line in markdown.splitlines():
        if line.startswith("#"):
            level = len(line) - len(line.lstrip("#"))
            out.append(f"<h{level}>{inline(line.lstrip('# '))}</h{level}>")
        elif line.startswith("- "):
            out.append(f"<li>{inline(line[2:])}</li>")
        elif line.startswith("|---"):
            continue
        elif line.startswith("|"):
            cells = re.split(r"(?<!\\)\|", line.strip("|"))
            row = "".join(f"<td>{inline(c.strip())}</td>" for c in cells)
            out.append(f"<table><tr>{row}</tr></table>")
        elif line:
            out.append(f"<p>{inline(line)}</p>")
    body = "\n".join(out).replace("</table>\n<table>", "\n")
    return f"<!doctype html><meta charset=utf-8><style>{STYLE}</style>{body}".encode()


def main() -> int:
    """Write every image that can be produced on this machine."""
    p = argparse.ArgumentParser(description="Regenerate the README images.")
    p.add_argument("--out", type=Path, default=Path("docs") / "images")
    p.add_argument("--browser", help="Chromium-based browser (default: look for Edge or Chrome)")
    args = p.parse_args()
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    browser = args.browser or find_browser()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_name:
        tmp = Path(tmp_name)
        show = ["show-layout", "classic-left", "--format", "png", "--out", str(tmp)]
        icsel(show)
        icsel([*show, "--compare", "subject-right"])
        shutil.copy(tmp / "layout-classic-left.png", out / "layout-view.png")
        shutil.copy(
            tmp / "compare-classic-left-vs-subject-right.png", out / "layout-comparison.png"
        )
        if browser is None:
            print(
                "No Chromium-based browser found: skipped browser-ui.png and portfolio-report.png"
            )
            return 0
        icsel(["samples", "--portfolio", "--out", str(tmp / "portfolio")])
        # Relative names keep this machine's folders out of the report.
        with _chdir(tmp):
            icsel(["portfolio", "portfolio", "--shared-text", "--out", "report"])
        report = (tmp / "report" / "portfolio.md").read_text(encoding="utf-8")
        with serving(tmp / "web", {"/report": report_html(report)}) as url:
            capture(browser, url + "/", out / "browser-ui.png", (1400, 1150))
            capture(browser, url + "/report", out / "portfolio-report.png", (1100, 1000))
    for path in sorted(out.glob("*.png")):
        print(f"Wrote {path} ({path.stat().st_size // 1024} KB)")
    return 0


@contextlib.contextmanager
def _chdir(path: Path) -> Iterator[None]:
    """Work in ``path`` (``contextlib.chdir`` needs Python 3.11)."""
    before = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(before)


if __name__ == "__main__":
    raise SystemExit(main())
