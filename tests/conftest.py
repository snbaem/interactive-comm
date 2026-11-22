"""Shared fixtures. Synthetic letters are generated once per test session."""

from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest

from icsel.profile import LayoutProfile, default_layouts_dir, load_profiles
from icsel.synthetic import generate

_LOOPBACK = {"127.0.0.1", "::1", "localhost"}
_real_connect = socket.socket.connect
_real_create_connection = socket.create_connection


def _is_loopback(address: object) -> bool:
    return isinstance(address, tuple) and bool(address) and str(address[0]) in _LOOPBACK


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail any test that opens a network connection, except to this machine (loopback)."""

    def guard_connect(sock: socket.socket, address: object) -> None:
        if not _is_loopback(address):
            raise RuntimeError(f"network access is not allowed in tests: {address!r}")
        _real_connect(sock, address)

    def guard_create(address: object, *args: object, **kwargs: object) -> socket.socket:
        if not _is_loopback(address):
            raise RuntimeError(f"network access is not allowed in tests: {address!r}")
        return _real_create_connection(address, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", guard_connect)
    monkeypatch.setattr(socket, "create_connection", guard_create)


@pytest.fixture(scope="session")
def layouts_dir() -> Path:
    return default_layouts_dir()


@pytest.fixture(scope="session")
def layouts(layouts_dir: Path) -> list[LayoutProfile]:
    return load_profiles(layouts_dir)


@pytest.fixture(scope="session")
def letters_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("letters")
    generate(out)
    return out


@pytest.fixture(scope="session")
def manifest(letters_dir: Path) -> list[dict]:
    return json.loads((letters_dir / "manifest.json").read_text(encoding="utf-8"))
