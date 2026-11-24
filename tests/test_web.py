"""End-to-end tests of the local web UI over loopback HTTP."""

import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest

from icsel.web import make_server


@pytest.fixture(scope="module")
def base_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    server = make_server(port=0, workdir=tmp_path_factory.mktemp("web"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def _req(url: str, method: str = "GET", data: bytes | None = None) -> tuple[int, bytes, str]:
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/pdf")
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            return res.status, res.read(), res.headers.get("Content-Type", "")
    except urllib.error.HTTPError as err:
        return err.code, err.read(), err.headers.get("Content-Type", "")


def test_index_and_info(base_url: str) -> None:
    status, body, ctype = _req(base_url + "/")
    assert status == 200 and ctype.startswith("text/html")
    assert b"Not an official Adobe project" in body or b"not an official Adobe project" in body
    status, body, _ = _req(base_url + "/api/info")
    info = json.loads(body)
    assert {lay["id"] for lay in info["layouts"]} >= {"classic-left", "subject-right"}
    assert any(s["file"] == "L01-classic.pdf" for s in info["samples"])
    assert "approximation" in info["fidelity"]
    assert 0 < info["low_fit_threshold"] < 1


def test_sample_run_and_render_other_layout(base_url: str) -> None:
    status, body, _ = _req(base_url + "/api/runs?sample=L04-subject-right-3line.pdf", "POST", b"")
    assert status == 200, body
    run = json.loads(body)
    assert run["best_layout"] == "subject-right"
    assert run["sample"]["layout"] == "subject-right"
    status, png, ctype = _req(base_url + run["source"]["pages"][0])
    assert status == 200 and ctype == "image/png" and png.startswith(b"\x89PNG")
    status, result, _ = _req(base_url + run["result_json"])
    assert json.loads(result)["best_layout"] == "subject-right"

    status, body, _ = _req(base_url + f"/api/runs/{run['id']}/render?layout=classic-left", "POST")
    assert status == 200
    other = json.loads(body)
    assert [o["role"] for o in other["overflow"]] == ["subject"]  # 3 lines in a 2-line box
    assert other["unplaced"] == []
    status, pdf, ctype = _req(base_url + other["pdf"])
    assert status == 200 and ctype == "application/pdf" and pdf.startswith(b"%PDF")
    status, clean, ctype = _req(base_url + other["clean_pdf"])
    assert status == 200 and clean.startswith(b"%PDF") and clean != pdf
    with urllib.request.urlopen(base_url + other["pdf"], timeout=30) as res:
        assert res.headers["Cache-Control"] == "no-store"


def test_upload(base_url: str, letters_dir: Path) -> None:
    data = (letters_dir / "L05-tall-address-8.pdf").read_bytes()
    status, body, _ = _req(base_url + "/api/runs?name=my%20letter.pdf", "POST", data)
    assert status == 200, body
    run = json.loads(body)
    assert run["name"] == "my letter.pdf"
    assert run["best_layout"] == "tall-address"


def test_bad_upload_is_reported(base_url: str) -> None:
    status, body, _ = _req(base_url + "/api/runs?name=broken.pdf", "POST", b"not a pdf")
    assert status == 422
    assert "broken.pdf" in json.loads(body)["error"]


def test_unknown_things_are_404(base_url: str) -> None:
    assert _req(base_url + "/api/runs?sample=..%2F..%2Fsecret.pdf", "POST", b"")[0] == 404
    assert _req(base_url + "/runs/000000000000/source.pdf")[0] == 404
    assert _req(base_url + "/runs/..%2F..%2Fpyproject.toml/x")[0] == 404
    assert _req(base_url + "/api/runs/000000000000/render?layout=classic-left", "POST")[0] == 404
    assert _req(base_url + "/nope")[0] == 404


def test_busy_port_is_an_error_not_silently_shared(tmp_path: Path) -> None:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        port = busy.getsockname()[1]
        with pytest.raises(OSError):
            make_server(port=port, workdir=tmp_path)


def test_page_script_parses(tmp_path: Path) -> None:
    """A JavaScript syntax error would silently break the whole UI."""
    import re
    import shutil
    import subprocess

    node = shutil.which("node")
    if node is None:
        pytest.skip("node not installed")
    from icsel import web

    html = (Path(web.__file__).parent / "data" / "web" / "index.html").read_text(encoding="utf-8")
    script = re.search(r"<script>(.*)</script>", html, re.S)
    assert script is not None
    js = tmp_path / "page.js"
    js.write_text(script.group(1), encoding="utf-8")
    result = subprocess.run([node, "--check", str(js)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_upload_note_matches_what_the_ai_step_sends() -> None:
    """The page may only promise that files stay local when the AI step is off."""
    import re
    import shutil
    import subprocess

    from icsel import web

    html = (Path(web.__file__).parent / "data" / "web" / "index.html").read_text(encoding="utf-8")
    assert html.count("Files stay on this computer") == 1  # only inside privacyNote
    func = re.search(r"^function privacyNote\(.*?^}", html, re.S | re.M)
    assert func is not None
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not installed")

    def note(llm: dict) -> str:
        script = f"{func.group(0)}\nconsole.log(privacyNote({json.dumps(llm)}));"
        return subprocess.run(
            [node, "-e", script], capture_output=True, text=True, check=True
        ).stdout.strip()

    on = {"enabled": True, "backend": "fake"}
    assert note({"enabled": False}) == "Files stay on this computer."
    both = note({**on, "tasks": ["classify", "explain"]})
    assert "text the rules cannot label" in both and "sent to fake" in both
    explain = note({**on, "tasks": ["explain"]})
    assert "No letter text is sent" in explain and "sent to fake" in explain
