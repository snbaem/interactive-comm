from pathlib import Path

import pytest

from icsel.extract import ExtractionError, extract


def test_detects_core_blocks(letters_dir: Path) -> None:
    s = extract(letters_dir / "L03-subject-right.pdf")
    address = s.region("address")
    subject = s.region("subject")
    assert address is not None and address.line_count == 3
    assert subject is not None and subject.x == pytest.approx(330, abs=1)
    assert s.region("date") is not None
    assert s.region("body") is not None


def test_multi_page_body(letters_dir: Path) -> None:
    s = extract(letters_dir / "L06-tall-address-2page.pdf")
    assert s.page_count == 2
    assert s.blocks_with("body", page=2)
    assert s.blocks_with("closing", page=2)


def test_non_pdf_raises(tmp_path: Path) -> None:
    bad = tmp_path / "not-a-pdf.pdf"
    bad.write_text("hello", encoding="utf-8")
    with pytest.raises(ExtractionError):
        extract(bad)


def test_one_and_a_half_spacing_and_to_whom(tmp_path: Path) -> None:
    from reportlab.pdfgen import canvas

    pdf = tmp_path / "spaced.pdf"
    c = canvas.Canvas(str(pdf), pagesize=(612, 792), invariant=1)
    c.setFont("Helvetica", 11)
    y = 792 - 200
    c.drawString(72, y, "To whom it may concern,")
    y -= 30
    for line in ["This paragraph is set with", "one and a half line spacing", "throughout."]:
        c.drawString(72, y, line)
        y -= 16.5  # 1.5 x 11 pt
    c.showPage()
    c.save()
    s = extract(pdf)
    assert [b.role for b in s.blocks] == ["salutation", "body"]
    assert s.blocks[1].line_count == 3


def test_date_inside_a_table_row_is_not_the_letter_date(tmp_path: Path) -> None:
    from reportlab.pdfgen import canvas

    pdf = tmp_path / "table.pdf"
    c = canvas.Canvas(str(pdf), pagesize=(612, 792), invariant=1)
    c.setFont("Helvetica", 10)
    top = 792 - 300
    for x, text in [(72, "0000-0001"), (200, "October 15, 2026"), (340, "12.34")]:
        c.drawString(x, top, text)
    c.drawString(72, top - 40, "Dear Customer,")
    c.showPage()
    c.save()
    s = extract(pdf)
    assert not s.blocks_with("date")
    assert [b.role for b in s.blocks if b.y < 320] == ["unknown", "unknown", "unknown"]


def test_heading_date_placeholder_and_bold_name(notice_letter, tmp_path: Path) -> None:
    s = extract(notice_letter(tmp_path))
    by_first = {b.lines[0]: b for b in s.blocks}
    assert by_first["NOTICE OF PAST-DUE ACCOUNT"].role == "subject"
    assert by_first["[Date]"].role == "date"
    address = by_first["Alex Sample"]
    assert address.role == "address" and address.line_count == 3
    # A date inside the account table is a cell, not the letter date.
    cell = next(b for b in s.blocks if "October 15, 2026" in b.lines)
    assert cell.role == "unknown"
    assert [b.lines[0] for b in s.blocks_with("date")] == ["[Date]"]


@pytest.mark.parametrize("text", ["Nov. 22, 2026", "22 Nov 2026", "Sept 3, 2026", "[Letter Date]"])
def test_more_date_forms(notice_letter, tmp_path: Path, text: str) -> None:
    s = extract(notice_letter(tmp_path, date_text=text))
    assert [b.lines[0] for b in s.blocks_with("date")] == [text]


def test_placeholders_and_greetings_are_not_headings(tmp_path: Path) -> None:
    from reportlab.pdfgen import canvas

    pdf = tmp_path / "verify.pdf"
    c = canvas.Canvas(str(pdf), pagesize=(612, 792), invariant=1)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(72, 792 - 150, "[Name of Account Holder]")
    c.drawString(72, 792 - 190, "Warm greetings,")
    c.setFont("Helvetica", 10)
    c.drawString(72, 792 - 230, "Dear Customer,")
    c.showPage()
    c.save()
    assert not extract(pdf).blocks_with("subject")
