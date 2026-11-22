from pathlib import Path

import pytest

from icsel.extract import extract
from icsel.match import rank, score_layout
from icsel.profile import LayoutProfile


def test_best_layout_is_planted_layout(letters_dir: Path, manifest: list[dict],
                                       layouts: list[LayoutProfile]) -> None:
    for entry in manifest:
        best = rank(extract(letters_dir / entry["file"]), layouts)[0]
        assert best.layout_id == entry["expected_layout"], entry["file"]
        assert best.fits, entry["file"]


def test_address_capacity_hard_check(letters_dir: Path, layouts: list[LayoutProfile]) -> None:
    letter = extract(letters_dir / "L05-tall-address-8.pdf")
    classic = next(lay for lay in layouts if lay.id == "classic-left")
    result = score_layout(letter, classic)
    assert not result.fits
    assert any("address has 8 lines" in f for f in result.failures)


def test_score_range(letters_dir: Path, layouts: list[LayoutProfile]) -> None:
    letter = extract(letters_dir / "L01-classic.pdf")
    for r in rank(letter, layouts):
        assert 0.0 <= r.score <= 1.0
    assert rank(letter, layouts)[0].score == pytest.approx(1.0, abs=0.01)
