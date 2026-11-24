"""Statements: detection, labels, matching by kind, and rendering."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from icsel.extract import Block, LetterStructure, extract
from icsel.llm.assistant import CLASSIFY_SCHEMA
from icsel.match import rank
from icsel.profile import LayoutProfile, ProfileError, profile_from_dict
from icsel.render import overflows, plan_placement, render_sample
from icsel.statement import label_statement
from icsel.synthetic import LETTERS, STATEMENTS

NAMES = [s.id for s in STATEMENTS]


def _lines(letter: LetterStructure, role: str) -> list[str]:
    return [ln for b in letter.blocks if b.role == role for ln in b.lines]


def _structure(title: str = "ACCOUNT STATEMENT", rows: int = 6, bold: bool = True,
               details: tuple[str, ...] = ("Statement date: March 3, 2026", "Account: 0000-0001"),
               details_top: float = 110.0) -> LetterStructure:  # fmt: skip
    blocks = [
        Block(54, 40, 150, 14, ["Brightwater Utilities"], bold=True, role="letterhead"),
        Block(400, 42, 150, 12, [title], bold=bold, role="letterhead"),
        Block(400, details_top, 150, 12.0 * len(details), list(details), role="unknown"),
        Block(54, 112, 120, 34, ["Alex Sample", "42 Placeholder Lane"], role="unknown"),
    ]
    for r in range(rows):
        for x in (54, 200, 400):
            blocks.append(Block(x, 250 + r * 18, 40, 9, [f"c{r}"], role="unknown"))
    blocks.append(Block(54, 680, 200, 9, ["Payment is due by March 25."], role="unknown"))
    return LetterStructure("synthetic", 612, 792, 1, blocks)


# ------------------------------------------------------------------ labels
@pytest.mark.parametrize("name", NAMES)
def test_parts_are_labeled(letters_dir: Path, name: str) -> None:
    letter = extract(letters_dir / f"{name}.pdf")
    assert letter.kind == "statement"
    assert _lines(letter, "subject") == ["ACCOUNT STATEMENT"]
    assert _lines(letter, "date") == ["Statement date: March 3, 2026"]
    assert _lines(letter, "reference") == ["Account: 0000-0001", "Period: Feb 1 - Feb 28, 2026"]
    assert _lines(letter, "address")[0] == "Alex Sample"
    assert _lines(letter, "letterhead")[0] == "Brightwater Utilities"
    assert _lines(letter, "body") == [
        "Payment is due by March 25, 2026. Pay online or at any service counter."
    ]
    assert _lines(letter, "unknown") == []


def test_letters_are_never_statements(letters_dir: Path, notice_letter, tmp_path: Path) -> None:
    for spec in LETTERS:
        assert extract(letters_dir / f"{spec.id}.pdf").kind == "letter", spec.id
    assert extract(notice_letter(tmp_path)).kind == "letter"


def test_a_due_date_is_a_detail_not_the_date() -> None:
    letter = _structure(details=("Due date: March 25, 2026", "Account: 0000-0001"))
    assert label_statement(letter)
    assert _lines(letter, "date") == []
    assert _lines(letter, "reference") == ["Due date: March 25, 2026", "Account: 0000-0001"]


def test_details_above_the_address_do_not_take_its_place() -> None:
    letter = _structure(details_top=102.0)
    letter.blocks[2].role = "address"  # what the letter rules do when it comes first
    assert label_statement(letter)
    assert _lines(letter, "address") == ["Alex Sample", "42 Placeholder Lane"]


@pytest.mark.parametrize(
    "letter",
    [
        _structure(title="ACCOUNT SUMMARY"),
        _structure(title="Your statement", bold=False),
        _structure(rows=3),
    ],
)
def test_near_misses_stay_letters(letter: LetterStructure) -> None:
    before = [b.role for b in letter.blocks]
    assert not label_statement(letter) and letter.kind == "letter"
    assert [b.role for b in letter.blocks] == before


def test_a_greeting_means_letter() -> None:
    letter = _structure()
    letter.blocks.append(Block(54, 200, 80, 9, ["Dear Customer,"], role="salutation"))
    assert not label_statement(letter)


# ------------------------------------------------------------------ matching
@pytest.mark.parametrize("name", NAMES)
def test_statement_layout_wins(letters_dir: Path, layouts: list[LayoutProfile], name: str) -> None:
    results = rank(extract(letters_dir / f"{name}.pdf"), layouts)
    assert results[0].layout_id == "statement" and results[0].fits and results[0].score > 0.9
    assert not any(r.fits for r in results[1:])


def test_without_a_statement_layout_nothing_fits(
    letters_dir: Path, layouts: list[LayoutProfile]
) -> None:
    only_letters = [lay for lay in layouts if lay.kind == "letter"]
    results = rank(extract(letters_dir / "S01-statement.pdf"), only_letters)
    assert not any(r.fits for r in results)
    assert "this is a statement; the layout is for letters" in results[0].failures


def test_letters_never_fit_and_rank_the_statement_layout_last(
    letters_dir: Path, layouts: list[LayoutProfile]
) -> None:
    for spec in LETTERS:
        last = rank(extract(letters_dir / f"{spec.id}.pdf"), layouts)[-1]
        assert last.layout_id == "statement" and not last.fits, spec.id


# ------------------------------------------------------------------ rendering
@pytest.mark.parametrize("name", NAMES)
def test_everything_is_placed_and_round_trips(
    letters_dir: Path, layouts: list[LayoutProfile], tmp_path: Path, name: str
) -> None:
    layout = next(lay for lay in layouts if lay.id == "statement")
    letter = extract(letters_dir / f"{name}.pdf")
    assert plan_placement(letter, layout).unplaced == [] and overflows(letter, layout) == []
    for zones in (True, False):
        out = render_sample(letter, layout, tmp_path / f"{zones}.pdf", show_zones=zones)
        again = extract(out)
        assert again.kind == "statement"
        words = " ".join(b.text for b in again.blocks).split()
        for cell in _lines(letter, "table"):
            assert all(w in words for w in cell.split()), cell
        assert rank(again, layouts)[0].layout_id == "statement"


def test_statement_renders_in_a_letter_layout_too(
    letters_dir: Path, layouts: list[LayoutProfile], tmp_path: Path
) -> None:
    layout = next(lay for lay in layouts if lay.id == "classic-left")
    letter = extract(letters_dir / "S02-statement-2page.pdf")
    assert [b.role for b in plan_placement(letter, layout).unplaced] == ["reference"]
    assert render_sample(letter, layout, tmp_path / "x.pdf").stat().st_size > 1000


# ------------------------------------------------------------------ profile and AI
def test_kind_is_validated_and_kept(layouts: list[LayoutProfile]) -> None:
    layout = next(lay for lay in layouts if lay.id == "statement")
    assert profile_from_dict(json.loads(json.dumps(layout.to_dict()))).kind == "statement"
    assert "kind" not in next(lay for lay in layouts if lay.kind == "letter").to_dict()
    with pytest.raises(ProfileError, match="kind"):
        profile_from_dict({**layout.to_dict(), "kind": "memo"})


def test_the_model_cannot_suggest_statement_roles() -> None:
    roles = CLASSIFY_SCHEMA["properties"]["suggestions"]["items"]["properties"]["role"]["enum"]
    assert "table" not in roles and "reference" not in roles
