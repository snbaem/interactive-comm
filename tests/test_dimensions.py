"""Measurements, layout view and explanations."""

from __future__ import annotations

from pathlib import Path

import pytest

from icsel.cli import main
from icsel.dimensions import (
    diff_layouts,
    find_gaps,
    format_length,
    letter_vs_layout,
    measure,
    missing_roles,
    to_units,
)
from icsel.explain import explain_ranking
from icsel.extract import Block, LetterStructure
from icsel.layoutview import pdf_to_png, render_comparison, render_layout_view
from icsel.match import rank
from icsel.profile import LayoutProfile, Zone


def _layout(zones: list[Zone], lid: str = "t") -> LayoutProfile:
    return LayoutProfile(lid, lid, 612, 792, zones)


def test_unit_conversion_and_formatting() -> None:
    assert to_units(144, "in") == 2.0
    assert to_units(72, "cm") == pytest.approx(2.54)
    assert to_units(10, "pt") == 10
    assert format_length(144) == '2.0"'
    assert format_length(21.6) == '0.3"'
    assert format_length(144, "cm") == "5.08 cm"
    assert format_length(100, "pt") == "100 pt"
    with pytest.raises(ValueError, match="unknown units"):
        to_units(1, "mm")


def test_margins_and_zone_measures() -> None:
    lay = _layout([Zone("address", 72, 144, 252, 86, 6), Zone("body", 72, 300, 468, 420)])
    m = measure(lay)
    assert (m.margins.top, m.margins.left) == (144, 72)
    assert m.margins.right == 612 - 540
    assert m.margins.bottom == 792 - 720
    a = m.zone("address")
    assert a is not None
    assert (a.top, a.left, a.width, a.height, a.capacity) == (144, 72, 252, 86, 6)
    assert a.right_margin == 612 - 324
    assert m.zone("body") is not None and m.zone("body").capacity is None  # type: ignore[union-attr]


def test_gap_calculation() -> None:
    above = Zone("address", 72, 100, 200, 50)
    below = Zone("body", 72, 170, 400, 300)
    right = Zone("subject", 300, 100, 100, 50)
    gaps = {(g.direction, g.first, g.second): g.points for g in find_gaps([above, below, right])}
    assert gaps[("vertical", "address", "body")] == 20
    assert gaps[("horizontal", "address", "subject")] == 28
    # subject is also above body (they overlap horizontally)
    assert gaps[("vertical", "subject", "body")] == 20


def test_overlapping_zones_have_no_gap() -> None:
    assert find_gaps([Zone("address", 0, 0, 100, 100), Zone("body", 50, 50, 100, 100)]) == []


def test_diff_between_layouts_reads_in_plain_language() -> None:
    ref = _layout([Zone("subject", 72, 252, 468, 30)], "classic")
    other = _layout([Zone("subject", 72, 273.6, 468, 30), Zone("date", 0, 0, 10, 10)], "alt")
    (d,) = diff_layouts(other, ref)
    assert d.dy == pytest.approx(21.6)
    assert d.describe("in", "layout") == 'subject 0.3" lower than layout'
    assert missing_roles(other, ref) == (["date"], [])


def test_letter_vs_layout_offsets() -> None:
    lay = _layout([Zone("subject", 72, 252, 468, 30)])
    letter = LetterStructure(
        "x.pdf", 612, 792, 1, [Block(72, 273.6, 400, 12, ["S"], role="subject")]
    )
    (d,) = letter_vs_layout(letter, lay)
    assert d.subject == "letter"
    assert d.describe("in", "layout").startswith('subject 0.3" lower')
    assert d.to_dict()["dy_pt"] == 21.6


def test_measurement_json_has_points_and_inches(layouts: list[LayoutProfile]) -> None:
    doc = measure(layouts[0]).to_dict()
    z = doc["zones"][0]
    assert z["top_in"] == pytest.approx(z["top_pt"] / 72, abs=0.001)
    assert doc["margins"]["left_in"] > 0
    assert doc["gaps"]


def test_view_renders_pdf_and_png(layouts: list[LayoutProfile], tmp_path: Path) -> None:
    pdf = render_layout_view(layouts[0], tmp_path / "v.pdf", "cm", reference=layouts[1])
    png = pdf_to_png(pdf, tmp_path / "v.png")
    comp = render_comparison(layouts[0], layouts[1], tmp_path / "c.pdf")
    assert pdf.stat().st_size > 1000 and png.read_bytes()[:4] == b"\x89PNG"
    assert comp.stat().st_size > 1000


def test_explanation_mentions_offsets_and_runner_up(layouts: list[LayoutProfile]) -> None:
    lay = next(x for x in layouts if x.id == "classic-left")
    z = lay.zone("subject")
    assert z is not None
    letter = LetterStructure(
        "x.pdf",
        612,
        792,
        1,
        [Block(z.x, z.y + 21.6, z.width - 20, 12, ["Subject"], role="subject", bold=True)],
    )
    results = rank(letter, layouts)
    text = explain_ranking(letter, layouts, results)
    assert "fits best" in text or "does not fit" in text
    assert "Runner-up" in text or "ruled out" in text


def test_cli_show_layout_and_match_dimensions(tmp_path: Path, letters_dir: Path) -> None:
    out = tmp_path / "o"
    assert main(["show-layout", "classic-left", "--out", str(out), "--json", "--units", "pt"]) == 0
    assert (out / "layout-classic-left.pdf").exists() and (out / "layout-classic-left.png").exists()
    assert (out / "layout-classic-left.json").exists()
    assert (
        main(["show-layout", "subject-right", "--compare", "classic-left", "--out", str(out)]) == 0
    )
    assert main(["show-layout", "classic-left", "--diff", "--out", str(out)]) == 2
    pdf = sorted(letters_dir.glob("*.pdf"))[0]
    m_out = tmp_path / "m"
    assert main(["match", str(pdf), "--out", str(m_out), "--dimensions"]) == 0
    stem = pdf.stem
    assert (m_out / f"{stem}.dimensions.png").exists()
    import json

    doc = json.loads((m_out / f"{stem}.result.json").read_text(encoding="utf-8"))
    assert doc["explanation"] and doc["dimensions"]["layout"]["zones"]
