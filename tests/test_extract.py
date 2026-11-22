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
