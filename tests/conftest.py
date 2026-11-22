"""Shared fixtures. Synthetic letters are generated once per test session."""

from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest

from icsel.profile import LayoutProfile, default_layouts_dir, load_profiles
from icsel.synthetic import generate


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail any test that tries to open a network connection."""

    def guard(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("network access is not allowed in tests")

    monkeypatch.setattr(socket.socket, "connect", guard)
    monkeypatch.setattr(socket, "create_connection", guard)


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
