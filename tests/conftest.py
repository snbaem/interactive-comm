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


def _make_notice_letter(tmp_path: Path, date_text: str = "[Date]") -> Path:
    """Shaped like a typical collection notice: heading on top, no "Subject:" prefix."""
    from reportlab.pdfgen import canvas

    pdf = tmp_path / "notice.pdf"
    c = canvas.Canvas(str(pdf), pagesize=(612, 792), invariant=1)
    y = 792 - 120

    def line(text: str, font: str = "Helvetica", x: float = 72, step: float = 14) -> None:
        nonlocal y
        c.setFont(font, 10)
        c.drawString(x, y, text)
        y -= step

    line("NOTICE OF PAST-DUE ACCOUNT", "Helvetica-Bold", step=34)
    line(date_text, step=24)
    line("Alex Sample", "Helvetica-Bold", step=12)  # bold name above regular address lines
    line("42 Placeholder Lane", step=12)
    line("Riverton ZZ 00001", step=40)
    for x, text in [(72, "Account"), (220, "Due date"), (360, "Amount")]:
        c.setFont("Helvetica-Bold", 9)
        c.drawString(x, y, text)
    y -= 14
    for x, text in [(72, "0000-0001"), (220, "October 15, 2026"), (360, "12.34")]:
        c.setFont("Helvetica", 9)
        c.drawString(x, y, text)
    y -= 36
    line("Dear Alex Sample,", step=20)
    line("Our records show a past-due balance on your account.")
    c.showPage()
    c.save()
    return pdf


@pytest.fixture
def notice_letter():
    """Factory: ``notice_letter(folder, date_text="[Date]")`` writes a notice-style PDF."""
    return _make_notice_letter
