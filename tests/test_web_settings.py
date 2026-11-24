"""AI settings of the local web UI. Offline: fake backends only."""

from __future__ import annotations

import http.client
import json
import threading
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest

from icsel.llm import LLMAuthError, LLMError, register_backend, registry
from icsel.web import MAX_SETTINGS, make_server

KEY = "test-key-not-real-123"
JSON = {"Content-Type": "application/json"}


class KeyedBackend:
    """Accepts only :data:`KEY`; ``offline`` makes the check inconclusive."""

    name = "keyed"
    model = "keyed-1"
    offline = False

    def __init__(self, key: str) -> None:
        self.key = key

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> KeyedBackend:
        return cls(env.get("KEYED_API_KEY", ""))

    def check(self) -> None:
        if self.offline:
            raise LLMError("network is down")
        if self.key != KEY:
            raise LLMAuthError("bad key")

    def complete(self, system: str, prompt: str, json_schema: dict[str, Any] | None = None) -> str:
        return '{"suggestions": []}' if json_schema else "Because it fits."


class Client:
    def __init__(self, server: Any) -> None:
        self.server = server
        self.port = server.server_address[1]

    def post(
        self, path: str, body: Any = None, headers: dict[str, str] | None = None
    ) -> tuple[int, dict[str, Any], str]:
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        return self._send("POST", path, data, JSON if headers is None else headers)

    def get(self, path: str) -> tuple[int, dict[str, Any], str]:
        return self._send("GET", path, None, {})

    def _send(
        self, method: str, path: str, data: bytes | None, headers: dict[str, str]
    ) -> tuple[int, dict[str, Any], str]:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        try:
            conn.request(method, path, data, headers)
            res = conn.getresponse()
            text = res.read().decode("utf-8")
            return res.status, json.loads(text), text
        finally:
            conn.close()

    def enabled(self) -> bool:
        return self.get("/api/info")[1]["llm"]["enabled"]


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Client]:
    monkeypatch.setattr(registry, "_FACTORIES", dict(registry._FACTORIES))
    monkeypatch.setattr(registry, "_KEY_VARS", dict(registry._KEY_VARS))
    monkeypatch.delenv("KEYED_API_KEY", raising=False)
    register_backend("keyed", KeyedBackend.from_env, api_key_env="KEYED_API_KEY")
    server = make_server(port=0, workdir=tmp_path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield Client(server)
    server.shutdown()
    server.server_close()


def _on(**extra: Any) -> dict[str, Any]:
    return {"enabled": True, "backend": "keyed", "api_key": KEY, **extra}


def _source(info: dict[str, Any]) -> str:
    return next(p for p in info["ai_settings"]["providers"] if p["name"] == "keyed")["key_source"]


def test_turn_on_run_and_turn_off(client: Client) -> None:
    info = client.get("/api/info")[1]
    assert info["llm"]["enabled"] is False and _source(info) == "none"
    status, info, _ = client.post("/api/settings", _on())
    assert status == 200 and info["warning"] is None and _source(info) == "app"
    assert info["llm"]["tasks"] == ["explain"]  # letter text is opt-in
    _, info, _ = client.post("/api/settings", _on(label_blocks=True))
    assert info["llm"]["tasks"] == ["classify", "explain"]
    _, run, _ = client.post("/api/runs?sample=L01-classic.pdf", b"")
    assert run["llm"]["explanation"] == "Because it fits."

    _, info, _ = client.post("/api/settings", {"enabled": False})
    assert info["llm"]["enabled"] is False and _source(info) == "app"
    _, run, _ = client.post("/api/runs?sample=L01-classic.pdf", b"")
    assert run["llm"]["enabled"] is False
    # The saved key is reused when the field is left blank.
    assert client.post("/api/settings", _on(api_key=""))[0] == 200


def test_key_is_never_returned_logged_or_written(
    client: Client, tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    texts = [
        client.post("/api/settings", _on(label_blocks=True))[2],
        client.get("/api/info")[2],
        client.post("/api/runs?sample=L01-classic.pdf", b"")[2],
        client.post("/api/settings", _on(api_key=KEY + "-wrong"))[2],
    ]
    out, err = capfd.readouterr()
    assert not any(KEY in text for text in [*texts, out, err])
    assert not any(KEY.encode() in p.read_bytes() for p in tmp_path.rglob("*") if p.is_file())


def test_forget_key(client: Client) -> None:
    client.post("/api/settings", _on())
    _, info, _ = client.post("/api/settings", {"enabled": False, "forget_key": True})
    assert info["llm"]["enabled"] is False and _source(info) == "none"
    assert client.post("/api/settings", _on(api_key=""))[0] == 400


def test_rejected_key_changes_nothing(client: Client) -> None:
    status, body, _ = client.post("/api/settings", _on(api_key="wrong"))
    assert status == 400 and body["error"] == "bad key"
    assert not client.enabled() and _source(client.get("/api/info")[1]) == "none"
    client.post("/api/settings", _on())
    assert client.post("/api/settings", _on(api_key="wrong"))[0] == 400
    assert client.enabled()
    assert client.post("/api/settings", _on(api_key=""))[0] == 200  # good key still there


def test_inconclusive_check_turns_on_with_a_warning(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(KeyedBackend, "offline", True)
    status, info, _ = client.post("/api/settings", _on())
    assert status == 200 and "network is down" in info["warning"] and info["llm"]["enabled"]


def test_key_from_environment(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KEYED_API_KEY", KEY)
    assert _source(client.get("/api/info")[1]) == "environment"
    assert client.post("/api/settings", _on(api_key=""))[0] == 200


@pytest.mark.parametrize(
    "body",
    [
        [],
        {},
        {"enabled": "yes"},
        _on(backend="nope"),
        _on(backend="none"),
        _on(api_key=5),
        _on(api_key="has space"),
        _on(api_key="new\nline"),
        _on(api_key="x" * 2000),
        b"{not json",
    ],
)
def test_bad_settings_are_refused(client: Client, body: Any) -> None:
    assert client.post("/api/settings", body)[0] == 400
    assert not client.enabled()


def test_oversized_settings_are_refused(client: Client) -> None:
    headers = {**JSON, "Content-Length": str(MAX_SETTINGS + 1)}
    assert client.post("/api/settings", b"", headers)[0] == 413


@pytest.mark.parametrize(
    ("headers", "status"),
    [
        ({**JSON, "Origin": "http://evil.example"}, 403),
        ({**JSON, "Origin": "null"}, 403),
        ({**JSON, "Host": "evil.example"}, 403),  # DNS rebinding
        ({**JSON, "Host": "evil.example", "Origin": "http://evil.example"}, 403),
        ({"Content-Type": "text/plain"}, 415),  # what a cross-site form could send
    ],
)
def test_other_sites_cannot_change_settings(
    client: Client, headers: dict[str, str], status: int
) -> None:
    assert client.post("/api/settings", _on(), headers)[0] == status
    assert not client.enabled()


def test_other_sites_cannot_start_runs(client: Client) -> None:
    path = "/api/runs?sample=L01-classic.pdf"
    assert client.post(path, b"", {"Origin": "http://evil.example"})[0] == 403
    for name in ("127.0.0.1", "localhost"):
        host = f"{name}:{client.port}"
        assert client.post(path, b"", {"Host": host, "Origin": f"http://{host}"})[0] == 200


def test_settings_are_locked_when_reachable_from_other_computers(tmp_path: Path) -> None:
    server = make_server(host="0.0.0.0", port=0, workdir=tmp_path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = Client(server)
        assert client.get("/api/info")[1]["ai_settings"]["editable"] is False
        assert client.post("/api/settings", _on())[0] == 403
        assert client.post("/api/runs?sample=L01-classic.pdf", b"")[0] == 200
    finally:
        server.shutdown()
        server.server_close()
