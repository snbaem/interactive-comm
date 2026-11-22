from pathlib import Path

import pdfplumber

from icsel.extract import Block, LetterStructure, extract
from icsel.match import score_layout
from icsel.profile import LayoutProfile
from icsel.render import overflows, plan_placement, render_sample, wrap_for_zone


def _layout(layouts: list[LayoutProfile], layout_id: str) -> LayoutProfile:
    return next(lay for lay in layouts if lay.id == layout_id)


def test_no_overflow_in_the_planted_layout(letters_dir: Path, layouts: list[LayoutProfile]) -> None:
    letter = extract(letters_dir / "L05-tall-address-8.pdf")
    assert overflows(letter, _layout(layouts, "tall-address")) == []


def test_overflow_reported_and_marked(
    letters_dir: Path, layouts: list[LayoutProfile], tmp_path: Path
) -> None:
    letter = extract(letters_dir / "L05-tall-address-8.pdf")
    classic = _layout(layouts, "classic-left")
    found = {o.role: o for o in overflows(letter, classic)}
    assert found["address"].lines == 8 and found["address"].capacity == 6
    assert found["address"].extra == 2

    marked = render_sample(letter, classic, tmp_path / "marked.pdf", show_zones=True)
    clean = render_sample(letter, classic, tmp_path / "clean.pdf", show_zones=False)
    with pdfplumber.open(marked) as pdf:
        marked_text = pdf.pages[0].extract_text()
    with pdfplumber.open(clean) as pdf:
        clean_text = pdf.pages[0].extract_text()
    # The extra address lines are always drawn; only the marked version labels them.
    assert "Account 0000-0008" in marked_text
    assert "2 lines over the box" in marked_text
    assert "Account 0000-0008" in clean_text
    assert "over the box" not in clean_text


def test_wide_text_wraps_before_counting_overflow(
    letters_dir: Path, layouts: list[LayoutProfile]
) -> None:
    letter = extract(letters_dir / "L02-classic-jitter.pdf")  # one 250 pt subject line
    box = _layout(layouts, "subject-right")
    subject_zone = box.zone("subject")
    assert subject_zone is not None
    subject = letter.blocks_with("subject", page=1)[0]
    assert len(wrap_for_zone(subject.lines, subject_zone, subject.bold)) == 2
    assert "subject" not in {o.role for o in overflows(letter, box)}  # 2 lines fit in 4


def _letter_with_extras() -> LetterStructure:
    blocks = [
        Block(54, 40, 150, 16, ["Brightwater Utilities"], role="letterhead", bold=True),
        Block(72, 110, 200, 12, ["NOTICE OF CHANGE"], role="unknown", bold=True),
        Block(
            72,
            144,
            120,
            34,
            ["Alex Sample", "42 Placeholder Lane", "Riverton ZZ 00001"],
            role="address",
        ),
        Block(72, 250, 90, 10, ["Account"], role="unknown"),
        Block(250, 250, 90, 10, ["Amount due"], role="unknown"),
        Block(72, 264, 90, 10, ["0000-0001"], role="unknown"),
        Block(250, 264, 90, 10, ["12.34"], role="unknown"),
        Block(72, 300, 80, 10, ["Dear Customer,"], role="salutation"),
        Block(72, 320, 400, 22, ["First paragraph line one", "and line two."], role="body"),
        Block(72, 360, 60, 10, ["Kind regards,"], role="closing"),
        Block(54, 760, 200, 7, ["Footer text"], role="footer"),
    ]
    return LetterStructure("synthetic", 612, 792, 1, blocks)


def test_plan_places_every_block(layouts: list[LayoutProfile]) -> None:
    letter = _letter_with_extras()
    plan = plan_placement(letter, _layout(layouts, "classic-left"))
    shown = {id(b) for bs in plan.zoned.values() for b in bs} | {id(b) for b in plan.flow}
    assert shown == {id(b) for b in letter.blocks}
    # The heading above the address has no box; the table under it is body content.
    assert {b.lines[0] for b in plan.unplaced} == {"NOTICE OF CHANGE"}


def test_unknown_blocks_and_tables_appear_in_sample(
    layouts: list[LayoutProfile], tmp_path: Path
) -> None:
    out = render_sample(_letter_with_extras(), _layout(layouts, "classic-left"), tmp_path / "s.pdf")
    with pdfplumber.open(out) as pdf:
        page = pdf.pages[0]
        text = page.extract_text()
        tables = page.extract_tables()
    for expected in ("NOTICE OF CHANGE", "Footer text", "Dear Customer,", "and line two."):
        assert expected in text
    assert any(["Account", "Amount due"] in t for t in tables)


def test_downloaded_sample_reads_back_as_the_same_letter(
    letters_dir: Path, manifest: list[dict], layouts: list[LayoutProfile], tmp_path: Path
) -> None:
    """Upload a preview PDF again: icsel's own labels are ignored and the fit stays high."""
    for entry in manifest:
        layout = _layout(layouts, entry["expected_layout"])
        letter = extract(letters_dir / entry["file"])
        sample = render_sample(letter, layout, tmp_path / entry["file"])
        back = extract(sample)
        texts = {ln for b in back.blocks for ln in b.lines}
        assert not texts & {"letterhead", "address", "date", "subject", "body", "footer"}
        assert not any("Sample generated locally" in t for t in texts)
        assert score_layout(back, layout).score > 0.95, entry["file"]


def test_unscored_when_letter_lacks_a_role(layouts: list[LayoutProfile]) -> None:
    letter = _letter_with_extras()  # no date, no subject
    result = score_layout(letter, _layout(layouts, "classic-left"))
    by_role = {r.role: r for r in result.roles}
    assert by_role["date"].score is None and by_role["subject"].score is None
    # The heading and table under the address count towards the body area.
    body = letter.region("body")
    assert body is not None and body.y == 250


def test_old_style_sample_labels_are_ignored(
    letters_dir: Path, layouts: list[LayoutProfile], tmp_path: Path
) -> None:
    """Samples made before labels moved to Courier: 6 pt Helvetica labels, title only."""
    from reportlab.pdfgen import canvas

    layout = _layout(layouts, "tall-address")
    letter = extract(letters_dir / "L05-tall-address-8.pdf")
    pdf = tmp_path / "old.pdf"
    c = canvas.Canvas(str(pdf), pagesize=(612, 792), invariant=1)
    c.setTitle("icsel sample - tall-address")
    for zone in layout.zones:  # the old renderer's 6 pt labels just above each zone
        c.setFont("Helvetica", 6)
        c.drawString(zone.x + 2, 792 - zone.y + 2, zone.role)
    for b in letter.blocks_with("date", "address", "subject", "salutation", "body", page=1):
        c.setFont("Helvetica", 10)
        for i, line in enumerate(b.lines):
            c.drawString(b.x, 792 - b.y - 8 - i * 12, line)
    c.setFont("Helvetica-Oblique", 7)
    c.drawCentredString(306, 18, "Sample generated locally by icsel - an approximation")
    c.showPage()
    c.save()
    back = extract(pdf)
    texts = {ln for b in back.blocks for ln in b.lines}
    assert not texts & {"address", "date", "subject", "body", "footer", "letterhead"}
    assert score_layout(back, layout).score > 0.95


def test_clean_sample_reads_back(
    letters_dir: Path, layouts: list[LayoutProfile], tmp_path: Path
) -> None:
    layout = _layout(layouts, "subject-right")
    letter = extract(letters_dir / "L04-subject-right-3line.pdf")
    clean = render_sample(letter, layout, tmp_path / "clean.pdf", show_zones=False)
    assert score_layout(extract(clean), layout).score > 0.95


def test_preview_puts_heading_and_date_in_their_boxes(
    notice_letter, tmp_path: Path, layouts: list[LayoutProfile]
) -> None:
    letter = extract(notice_letter(tmp_path))
    for layout in layouts:
        plan = plan_placement(letter, layout)
        assert plan.zoned["subject"][0].lines[0] == "NOTICE OF PAST-DUE ACCOUNT", layout.id
        assert plan.zoned["date"][0].lines[0] == "[Date]", layout.id
        assert plan.zoned["address"][0].line_count == 3, layout.id
        assert plan.unplaced == [], layout.id  # the table counts as body content
