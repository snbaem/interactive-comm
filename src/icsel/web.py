"""Local browser UI for icsel: ``icsel serve``.

A small single-user web app built on the standard library's ``http.server``,
so it needs no extra dependencies. It listens on ``127.0.0.1`` by default,
makes no outbound network calls, and stores everything it produces under a
local working folder (default ``out/web``).

Endpoints (all JSON unless noted)::

    GET  /                               the single-page UI (HTML)
    GET  /api/info                       version, layouts, sample letters
    POST /api/runs?name=<file.pdf>       body = PDF bytes; match an uploaded letter
    POST /api/runs?sample=<file.pdf>     match one of the generated sample letters
    POST /api/runs/<id>/render?layout=X  render the run's letter in layout X
    GET  /runs/<id>/<file>               PDFs, PNG previews and result JSON of a run
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import shutil
import socketserver
import sys
import threading
import traceback
import uuid
import webbrowser
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import parse_qs, urlsplit

import pdfplumber

from . import __version__
from .extract import ExtractionError, LetterStructure, extract
from .match import LOW_FIT_THRESHOLD, MatchResult, rank
from .profile import LayoutProfile, load_profiles
from .render import overflows, plan_placement, render_sample
from .report import APPROX_NOTE, build_result
from .synthetic import generate

#: First port tried when none is given.
DEFAULT_PORT = 8765
#: How many consecutive ports to try from :data:`DEFAULT_PORT`.
PORT_ATTEMPTS = 20
#: Largest accepted upload, in bytes.
MAX_UPLOAD = 25 * 1024 * 1024
#: Resolution of PNG page previews.
PREVIEW_DPI = 110
#: Pages rendered as previews per PDF.
MAX_PREVIEW_PAGES = 10

_RUN_ID = re.compile(r"^[0-9a-f]{12}$")
_SAFE_FILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".json": "application/json; charset=utf-8",
    ".html": "text/html; charset=utf-8",
}


class WebError(Exception):
    """An error reported to the browser with an HTTP status."""

    def __init__(self, status: HTTPStatus, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass
class Run:
    """One matched letter held by the server."""

    id: str
    name: str
    folder: Path
    letter: LetterStructure
    results: list[MatchResult]


def _safe_part(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", text)[:60] or "x"


def _page_images(pdf: Path, prefix: str) -> list[str]:
    """Render PNG previews next to ``pdf``; return their file names (cached)."""
    names: list[str] = []
    with pdfplumber.open(pdf) as doc:
        for i, page in enumerate(doc.pages[:MAX_PREVIEW_PAGES], start=1):
            name = f"{prefix}-{i}.png"
            target = pdf.parent / name
            if not target.exists():
                page.to_image(resolution=PREVIEW_DPI).save(target)
            names.append(name)
    return names


class App:
    """State and actions behind the HTTP handler. Safe to call from several threads."""

    def __init__(self, layouts: list[LayoutProfile], workdir: Path) -> None:
        self.layouts = layouts
        self.by_id = {lay.id: lay for lay in layouts}
        self.workdir = Path(workdir)
        self.runs: dict[str, Run] = {}
        self._lock = threading.Lock()
        self.samples_dir = self.workdir / "samples"
        manifest = generate(self.samples_dir)
        self.samples: list[dict[str, Any]] = json.loads(manifest.read_text(encoding="utf-8"))

    # ------------------------------------------------------------------ queries
    def info(self) -> dict[str, Any]:
        """Version, layouts (with zones) and the sample letters."""
        return {
            "version": __version__,
            "fidelity": APPROX_NOTE,
            "low_fit_threshold": LOW_FIT_THRESHOLD,
            "layouts": [lay.to_dict() for lay in self.layouts],
            "samples": self.samples,
        }

    def run_file(self, run_id: str, filename: str) -> Path:
        """Resolve a file of a run, refusing anything outside the run folder."""
        if not _RUN_ID.match(run_id) or not _SAFE_FILE.match(filename):
            raise WebError(HTTPStatus.NOT_FOUND, "not found")
        with self._lock:
            run = self.runs.get(run_id)
        if run is None:
            raise WebError(HTTPStatus.NOT_FOUND, "unknown run (the server may have restarted)")
        path = (run.folder / filename).resolve()
        if path.parent != run.folder.resolve() or not path.is_file():
            raise WebError(HTTPStatus.NOT_FOUND, "not found")
        return path

    # ------------------------------------------------------------------ actions
    def create_run(self, name: str, data: bytes | None = None, sample: str | None = None) -> dict:
        """Match an uploaded PDF (``data``) or a sample letter (``sample``)."""
        run_id = uuid.uuid4().hex[:12]
        folder = self.workdir / "runs" / run_id
        folder.mkdir(parents=True, exist_ok=True)
        source = folder / "source.pdf"
        if sample is not None:
            known = {s["file"] for s in self.samples}
            if sample not in known:
                raise WebError(HTTPStatus.NOT_FOUND, f"unknown sample letter: {sample}")
            shutil.copyfile(self.samples_dir / sample, source)
            name = sample
        else:
            if not data:
                raise WebError(HTTPStatus.BAD_REQUEST, "empty upload")
            source.write_bytes(data)
        try:
            letter = extract(source)
        except ExtractionError as exc:
            shutil.rmtree(folder, ignore_errors=True)
            msg = str(exc).replace(str(source), name)
            raise WebError(HTTPStatus.UNPROCESSABLE_ENTITY, msg) from exc
        letter.source = name
        results = rank(letter, self.layouts)
        run = Run(run_id, name, folder, letter, results)
        with self._lock:
            self.runs[run_id] = run
        sample_info = self.render(run_id, results[0].layout_id)
        result_doc = build_result(letter, results, Path(sample_info["file"]))
        (folder / "result.json").write_text(json.dumps(result_doc, indent=2) + "\n", "utf-8")
        return self._payload(run, sample_info)

    def render(self, run_id: str, layout_id: str) -> dict[str, Any]:
        """Render (or reuse) the sample PDF and previews of a run in one layout."""
        if not _RUN_ID.match(run_id):
            raise WebError(HTTPStatus.NOT_FOUND, "not found")
        with self._lock:
            run = self.runs.get(run_id)
        if run is None:
            raise WebError(HTTPStatus.NOT_FOUND, "unknown run (the server may have restarted)")
        layout = self.by_id.get(layout_id)
        if layout is None:
            raise WebError(HTTPStatus.NOT_FOUND, f"unknown layout: {layout_id}")
        stem = f"sample-{_safe_part(layout.id)}"
        pdf = run.folder / f"{stem}.pdf"
        clean = run.folder / f"{stem}-clean.pdf"
        if not pdf.exists():
            render_sample(run.letter, layout, pdf)
        if not clean.exists():
            render_sample(run.letter, layout, clean, show_zones=False)
        base = f"/runs/{run.id}/"
        return {
            "layout": layout.id,
            "file": pdf.name,
            "pdf": base + pdf.name,
            "clean_pdf": base + clean.name,
            "pages": [base + n for n in _page_images(pdf, stem)],
            "overflow": [o.to_dict() for o in overflows(run.letter, layout)],
            "unplaced": [
                {"role": b.role, "page": b.page, "first_line": b.lines[0][:80]}
                for b in plan_placement(run.letter, layout).unplaced
            ],
        }

    def _payload(self, run: Run, sample_info: dict[str, Any]) -> dict[str, Any]:
        base = f"/runs/{run.id}/"
        letter = run.letter.to_dict()
        return {
            "id": run.id,
            "name": run.name,
            "page": letter["page"],
            "page_count": letter["page_count"],
            "blocks": letter["blocks"],
            "best_layout": run.results[0].layout_id,
            "best_fits": run.results[0].fits,
            "ranking": [r.to_dict() for r in run.results],
            "source": {
                "pdf": base + "source.pdf",
                "pages": [base + n for n in _page_images(run.folder / "source.pdf", "source")],
            },
            "sample": {k: v for k, v in sample_info.items() if k != "file"},
            "result_json": base + "result.json",
            "fidelity": APPROX_NOTE,
        }


class _Handler(BaseHTTPRequestHandler):
    app: ClassVar[App]
    server_version = f"icsel/{__version__}"

    # ------------------------------------------------------------- plumbing
    def log_message(self, format: str, *args: Any) -> None:
        sys.stderr.write(f"  {self.command} {self.path} -> {args[1] if len(args) > 1 else ''}\n")

    def _send(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # Never cache: after a server restart an old page must not get stale files.
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, data: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        self._send(status, json.dumps(data).encode("utf-8"), _CONTENT_TYPES[".json"])

    def _guarded(self, action: Any) -> None:
        try:
            action()
        except WebError as exc:
            self._json({"error": exc.message}, exc.status)
        except Exception as exc:  # report, keep serving
            traceback.print_exc()
            self._json({"error": f"internal error: {exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)

    # ------------------------------------------------------------- routes
    def do_GET(self) -> None:
        self._guarded(self._get)

    def do_POST(self) -> None:
        self._guarded(self._post)

    def _get(self) -> None:
        url = urlsplit(self.path)
        parts = [p for p in url.path.split("/") if p]
        if not parts:
            page = Path(__file__).resolve().parent / "data" / "web" / "index.html"
            self._send(HTTPStatus.OK, page.read_bytes(), _CONTENT_TYPES[".html"])
        elif parts == ["api", "info"]:
            self._json(self.app.info())
        elif len(parts) == 3 and parts[0] == "runs":
            path = self.app.run_file(parts[1], parts[2])
            ctype = _CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")
            self._send(HTTPStatus.OK, path.read_bytes(), ctype)
        elif parts == ["favicon.ico"]:
            self._send(HTTPStatus.NO_CONTENT, b"", "text/plain")
        else:
            raise WebError(HTTPStatus.NOT_FOUND, "not found")

    def _post(self) -> None:
        url = urlsplit(self.path)
        parts = [p for p in url.path.split("/") if p]
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        if parts == ["api", "runs"]:
            if "sample" in query:
                self._json(self.app.create_run(query["sample"], sample=query["sample"]))
                return
            name = Path(query.get("name", "upload.pdf")).name or "upload.pdf"
            self._json(self.app.create_run(name, data=self._read_body()))
        elif len(parts) == 4 and parts[:2] == ["api", "runs"] and parts[3] == "render":
            self._json(self.app.render(parts[2], query.get("layout", "")))
        else:
            raise WebError(HTTPStatus.NOT_FOUND, "not found")

    def _read_body(self) -> bytes:
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError as exc:
            raise WebError(HTTPStatus.LENGTH_REQUIRED, "Content-Length header required") from exc
        if length > MAX_UPLOAD:
            raise WebError(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                f"file is larger than {MAX_UPLOAD // (1024 * 1024)} MB",
            )
        return self.rfile.read(length)


class _Server(ThreadingHTTPServer):
    # On Windows, SO_REUSEADDR lets a second process bind a port that is already in
    # use, so a busy port would fail silently. Only enable it elsewhere.
    allow_reuse_address = os.name != "nt"
    daemon_threads = True

    def server_bind(self) -> None:
        # Skip HTTPServer's socket.getfqdn() reverse lookup: no name lookups needed.
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = str(host)
        self.server_port = int(port)


def make_server(
    host: str = "127.0.0.1",
    port: int | None = None,
    layouts: list[LayoutProfile] | None = None,
    workdir: Path = Path("out") / "web",
) -> ThreadingHTTPServer:
    """Create (but do not start) the HTTP server.

    Args:
        host: Address to listen on.
        port: Port to use; ``0`` picks any free port. ``None`` tries
            :data:`DEFAULT_PORT` and the next few ports until one is free.
        layouts: Layout profiles; defaults to the bundled examples.
        workdir: Folder for sample letters, uploads and results.

    Raises:
        OSError: If the port (or every port tried) is in use.
    """
    app = App(layouts if layouts is not None else load_profiles(), workdir)
    handler = type("Handler", (_Handler,), {"app": app})
    if port is not None:
        return _Server((host, port), handler)
    last_error: OSError | None = None
    for candidate in range(DEFAULT_PORT, DEFAULT_PORT + PORT_ATTEMPTS):
        try:
            return _Server((host, candidate), handler)
        except OSError as exc:
            last_error = exc
    raise OSError(
        f"no free port between {DEFAULT_PORT} and {DEFAULT_PORT + PORT_ATTEMPTS - 1}; pass --port"
    ) from last_error


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def serve(
    host: str = "127.0.0.1",
    port: int | None = None,
    layouts: list[LayoutProfile] | None = None,
    workdir: Path = Path("out") / "web",
    open_browser: bool = False,
) -> None:
    """Run the web UI until interrupted (Ctrl+C)."""
    server = make_server(host, port, layouts, workdir)
    actual_port = server.server_address[1]
    shown = "127.0.0.1" if host in ("", "0.0.0.0") else host
    url = f"http://{shown}:{actual_port}/"
    lines = [f"icsel {__version__} web UI running at {url}"]
    if port is None and actual_port != DEFAULT_PORT:
        lines.append(f"(port {DEFAULT_PORT} was busy, so {actual_port} is used instead)")
    lines.append(f"Working folder: {Path(workdir).resolve()}")
    if not _is_loopback(host):
        lines.append("WARNING: listening on a non-loopback address. The UI has no authentication;")
        lines.append("         anyone who can reach this port can upload files and read results.")
    lines += [APPROX_NOTE, "Press Ctrl+C to stop."]
    print("\n".join(lines), flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping.", flush=True)
    finally:
        server.server_close()
