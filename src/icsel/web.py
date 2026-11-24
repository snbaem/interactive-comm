"""Local browser UI for icsel: ``icsel serve``.

A small single-user web app built on the standard library's ``http.server``,
so it needs no extra dependencies. It listens on ``127.0.0.1`` by default and
stores everything it produces under a local working folder (default ``out/web``).
It makes no outbound network calls unless the optional AI step is turned on.

An API key typed into the AI settings is kept in memory only: it is never written
to disk, logged, or sent back to the browser. AI settings can be changed only while
the server listens on a loopback address, and requests that change anything must
come from the UI's own page.

Endpoints (all JSON unless noted)::

    GET  /                               the single-page UI (HTML)
    GET  /api/info                       version, layouts, sample letters
    POST /api/settings                   body = JSON; turn the AI step on or off
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
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import parse_qs, urlsplit

import pdfplumber

from . import __version__
from .explain import explain_ranking
from .extract import ExtractionError, LetterStructure, extract
from .llm import (
    Assistant,
    LLMAuthError,
    LLMError,
    NoOpAssistant,
    api_key_variable,
    available_backends,
    get_assistant,
)
from .match import LOW_FIT_THRESHOLD, MatchResult
from .pipeline import analyze
from .profile import LayoutProfile, load_profiles
from .render import overflows, plan_placement, render_sample
from .report import APPROX_NOTE, build_result, dimensions_doc
from .synthetic import generate

#: First port tried when none is given.
DEFAULT_PORT = 8765
#: How many consecutive ports to try from :data:`DEFAULT_PORT`.
PORT_ATTEMPTS = 20
#: Largest accepted upload, in bytes.
MAX_UPLOAD = 25 * 1024 * 1024
#: Largest accepted settings request, in bytes.
MAX_SETTINGS = 16 * 1024
#: Longest accepted API key.
MAX_KEY = 1024
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
    llm: dict[str, Any] = field(default_factory=dict)


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

    def __init__(
        self,
        layouts: list[LayoutProfile],
        workdir: Path,
        assistant: Assistant | None = None,
        settings_editable: bool = True,
    ) -> None:
        self.assistant: Assistant = assistant or NoOpAssistant()
        self.settings_editable = settings_editable
        self._keys: dict[str, str] = {}  # backend -> API key; memory only, never returned
        self._settings_lock = threading.Lock()
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
            **self.ai_info(),
            "layouts": [lay.to_dict() for lay in self.layouts],
            "samples": self.samples,
        }

    def ai_info(self) -> dict[str, Any]:
        """State of the AI step and what its settings allow. Never contains a key."""
        providers = []
        for name in available_backends():
            if name == "none":
                continue
            variable = api_key_variable(name)
            if name in self._keys:
                source = "app"
            elif variable and os.environ.get(variable):
                source = "environment"
            else:
                source = "none"
            providers.append({"name": name, "uses_key": bool(variable), "key_source": source})
        return {
            "llm": self.assistant.describe(),
            "ai_settings": {"editable": self.settings_editable, "providers": providers},
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
    def update_settings(self, data: Any) -> dict[str, Any]:
        """Turn the AI step on or off, and set or forget an API key.

        ``data`` keys: ``enabled`` (bool, required), ``backend``, ``api_key`` (empty keeps
        the current one), ``label_blocks`` (bool; allows sending text of unlabeled blocks)
        and ``forget_key`` (bool; also turns the AI step off). Nothing changes on error.
        """
        if not self.settings_editable:
            raise WebError(
                HTTPStatus.FORBIDDEN,
                "AI settings can be changed only when icsel listens on this computer only",
            )
        if not isinstance(data, dict) or not isinstance(data.get("enabled"), bool):
            raise WebError(HTTPStatus.BAD_REQUEST, "expected a JSON object with 'enabled'")
        with self._settings_lock:
            if data.get("forget_key") is True:
                self._keys.clear()
                self.assistant = NoOpAssistant()
                return self.ai_info()
            if not data["enabled"]:
                self.assistant = NoOpAssistant()
                return self.ai_info()
            return self._enable_ai(data)

    def _enable_ai(self, data: dict[str, Any]) -> dict[str, Any]:
        backend = str(data.get("backend", "")).strip().lower()
        if backend == "none" or backend not in available_backends():
            raise WebError(HTTPStatus.BAD_REQUEST, "unknown AI provider")
        variable = api_key_variable(backend)
        key = data.get("api_key") or ""
        if not isinstance(key, str):
            raise WebError(HTTPStatus.BAD_REQUEST, "the API key must be text")
        key = key.strip()
        # Printable ASCII without spaces: anything else cannot go in an HTTP header.
        if key and (len(key) > MAX_KEY or not re.fullmatch(r"[!-~]+", key)):
            raise WebError(HTTPStatus.BAD_REQUEST, "that does not look like an API key")
        key = key or self._keys.get(backend, "")
        tasks = "classify,explain" if data.get("label_blocks") is True else "explain"
        env = {**os.environ, "ICSEL_LLM_TASKS": tasks}
        if variable and key:
            env[variable] = key
        try:
            assistant = get_assistant(backend, env)
        except LLMError as exc:
            raise WebError(HTTPStatus.BAD_REQUEST, str(exc)) from exc
        warning = None
        check = getattr(assistant, "check", None)
        try:
            if check is not None:
                check()
        except LLMAuthError as exc:
            raise WebError(HTTPStatus.BAD_REQUEST, str(exc)) from exc
        except LLMError as exc:
            warning = f"AI is on, but the key could not be checked: {exc}"
        if variable and key:
            self._keys[backend] = key
        self.assistant = assistant
        return {**self.ai_info(), "warning": warning}

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
        analysis = analyze(letter, self.layouts, self.assistant)
        results = analysis.results
        run = Run(run_id, name, folder, letter, results, analysis.llm)
        with self._lock:
            self.runs[run_id] = run
        sample_info = self.render(run_id, results[0].layout_id)
        result_doc = build_result(
            letter,
            results,
            Path(sample_info["file"]),
            analysis.llm,
            explanation=explain_ranking(letter, self.layouts, results),
            dimensions=dimensions_doc(letter, self.by_id[results[0].layout_id]),
        )
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
            "llm": run.llm,
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
        self._check_same_site()
        url = urlsplit(self.path)
        parts = [p for p in url.path.split("/") if p]
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        if parts == ["api", "runs"]:
            if "sample" in query:
                self._json(self.app.create_run(query["sample"], sample=query["sample"]))
                return
            name = Path(query.get("name", "upload.pdf")).name or "upload.pdf"
            self._json(self.app.create_run(name, data=self._read_body()))
        elif parts == ["api", "settings"]:
            self._json(self.app.update_settings(self._read_json()))
        elif len(parts) == 4 and parts[:2] == ["api", "runs"] and parts[3] == "render":
            self._json(self.app.render(parts[2], query.get("layout", "")))
        else:
            raise WebError(HTTPStatus.NOT_FOUND, "not found")

    def _check_same_site(self) -> None:
        """Refuse requests sent by other web pages (CSRF and DNS rebinding).

        The server has no login, so without this any page open in the browser could
        change settings or spend the user's API credit.
        """
        host = self.headers.get("Host", "")
        origin = self.headers.get("Origin")
        if origin is not None and origin.lower() != f"http://{host.lower()}":
            raise WebError(HTTPStatus.FORBIDDEN, "request from another site refused")
        try:
            name = urlsplit(f"//{host}").hostname or ""
        except ValueError:
            name = ""
        if self.app.settings_editable and not _is_loopback(name):
            raise WebError(HTTPStatus.FORBIDDEN, "unexpected Host header")

    def _read_json(self) -> Any:
        # A JSON content type cannot be sent cross-site without a CORS preflight.
        if self.headers.get_content_type() != "application/json":
            raise WebError(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "expected application/json")
        try:
            return json.loads(self._read_body(MAX_SETTINGS))
        except ValueError as exc:
            raise WebError(HTTPStatus.BAD_REQUEST, "request body is not valid JSON") from exc

    def _read_body(self, limit: int = MAX_UPLOAD) -> bytes:
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError as exc:
            raise WebError(HTTPStatus.LENGTH_REQUIRED, "Content-Length header required") from exc
        if length < 0 or length > limit:
            size = f"{limit // (1024 * 1024)} MB" if limit == MAX_UPLOAD else f"{limit} bytes"
            raise WebError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, f"request is larger than {size}")
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
    assistant: Assistant | None = None,
) -> ThreadingHTTPServer:
    """Create (but do not start) the HTTP server.

    Args:
        host: Address to listen on.
        port: Port to use; ``0`` picks any free port. ``None`` tries
            :data:`DEFAULT_PORT` and the next few ports until one is free.
        layouts: Layout profiles; defaults to the bundled examples.
        workdir: Folder for sample letters, uploads and results.
        assistant: Optional AI step (default: off).

    Raises:
        OSError: If the port (or every port tried) is in use.
    """
    app = App(
        layouts if layouts is not None else load_profiles(),
        workdir,
        assistant,
        settings_editable=_is_loopback(host),
    )
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
    assistant: Assistant | None = None,
) -> None:
    """Run the web UI until interrupted (Ctrl+C)."""
    server = make_server(host, port, layouts, workdir, assistant)
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
