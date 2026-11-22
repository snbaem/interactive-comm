"""Shared fixtures: synthetic letters generated once per test session."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from icsel.profile import LayoutProfile, load_profiles  # noqa: E402
from icsel.synthetic import generate  # noqa: E402

LAYOUTS_DIR = ROOT / "layouts"


@pytest.fixture(scope="session")
def layouts() -> list[LayoutProfile]:
    return load_profiles(LAYOUTS_DIR)


@pytest.fixture(scope="session")
def letters_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("letters")
    generate(LAYOUTS_DIR, out)
    return out


@pytest.fixture(scope="session")
def manifest(letters_dir: Path) -> list[dict]:
    return json.loads((letters_dir / "manifest.json").read_text(encoding="utf-8"))
