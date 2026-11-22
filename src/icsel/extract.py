"""Extract a letter's block structure from a text-based PDF.

The pipeline is deliberately simple and rule-based:

1. read words with positions and font names (pdfplumber);
2. group words into lines, and split a line into *segments* wherever there is
   a wide horizontal gap (so a left address and a right subject box on the same
   baseline stay apart);
3. stack segments into *blocks* when they share a left edge and are separated
   by no more than normal line spacing;
4. classify each block with ordered rules (letterhead, date, subject,
   salutation, address, body, closing, footer, unknown).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pdfplumber

#: Horizontal gap (pt) that splits one text line into separate segments.
SEGMENT_GAP = 18.0
#: Max left-edge difference (pt) for two segments to belong to one block.
ALIGN_TOLERANCE = 12.0
#: Max vertical gap, as a fraction of text height, between lines of a block.
LINE_GAP_RATIO = 0.5
#: Blocks starting above this (pt from top) on page 1 count as letterhead.
LETTERHEAD_LIMIT = 100.0
#: Blocks starting this close (pt) to the page bottom count as footer.
FOOTER_MARGIN = 50.0

#: Roles that together make up the "body" area of a letter.
BODY_ROLES = ("salutation", "body", "closing")

_MONTHS = (
    "January|February|March|April|May|June|July|August|September|October|November|December"
)
_DATE_RE = re.compile(
    rf"^\s*(\d{{1,2}}\s+({_MONTHS})\s+\d{{4}}|({_MONTHS})\s+\d{{1,2}},?\s+\d{{4}}"
    rf"|\d{{4}}-\d{{2}}-\d{{2}}|\d{{1,2}}[./]\d{{1,2}}[./]\d{{2,4}})\s*$",
    re.IGNORECASE,
)
_SUBJECT_RE = re.compile(r"^\s*(subject|re|regarding)\s*:", re.IGNORECASE)
_SALUTATION_RE = re.compile(r"^\s*(dear|hello|hi)\b", re.IGNORECASE)
_CLOSING_RE = re.compile(
    r"^\s*(kind regards|regards|best regards|sincerely|yours (sincerely|faithfully|truly))\b",
    re.IGNORECASE,
)


class ExtractionError(RuntimeError):
    """Raised when a PDF cannot be read or contains no extractable text."""


@dataclass
class Block:
    """A rectangular group of text lines, in points, top-left origin."""

    x: float
    y: float
    width: float
    height: float
    lines: list[str]
    page: int = 1
    bold: bool = False
    role: str = "unknown"

    @property
    def right(self) -> float:
        """X coordinate of the right edge."""
        return self.x + self.width

    @property
    def bottom(self) -> float:
        """Y coordinate of the bottom edge."""
        return self.y + self.height

    @property
    def line_count(self) -> int:
        """Number of text lines in the block."""
        return len(self.lines)

    @property
    def text(self) -> str:
        """Lines joined with spaces."""
        return " ".join(self.lines)

    def to_dict(self) -> dict[str, Any]:
        """Plain-data view for JSON output."""
        return {
            "role": self.role,
            "page": self.page,
            "x": round(self.x, 2),
            "y": round(self.y, 2),
            "width": round(self.width, 2),
            "height": round(self.height, 2),
            "line_count": self.line_count,
            "bold": self.bold,
            "lines": self.lines,
        }


@dataclass
class LetterStructure:
    """All blocks detected in a letter, plus page geometry."""

    source: str
    page_width: float
    page_height: float
    page_count: int
    blocks: list[Block] = field(default_factory=list)

    def blocks_with(self, *roles: str, page: int | None = None) -> list[Block]:
        """Blocks having any of ``roles``, optionally restricted to one page."""
        return [b for b in self.blocks if b.role in roles and (page is None or b.page == page)]

    def region(self, role: str) -> Block | None:
        """Bounding box of a role on page 1, merging blocks where needed.

        ``"body"`` covers salutation, body paragraphs and closing together, which
        is what a layout's body zone holds.
        """
        roles = BODY_ROLES if role == "body" else (role,)
        found = self.blocks_with(*roles, page=1)
        if not found:
            return None
        if role != "body":
            found = found[:1]
        x0 = min(b.x for b in found)
        y0 = min(b.y for b in found)
        x1 = max(b.right for b in found)
        y1 = max(b.bottom for b in found)
        lines = [ln for b in found for ln in b.lines]
        return Block(x0, y0, x1 - x0, y1 - y0, lines, page=1, bold=all(b.bold for b in found),
                     role=role)

    def to_dict(self) -> dict[str, Any]:
        """Plain-data view for JSON output."""
        return {
            "source": self.source,
            "page": {"width": self.page_width, "height": self.page_height, "units": "pt"},
            "page_count": self.page_count,
            "blocks": [b.to_dict() for b in self.blocks],
        }


@dataclass
class _Seg:
    x0: float
    x1: float
    top: float
    bottom: float
    text: str
    bold: bool


def _is_bold(fontname: str) -> bool:
    name = fontname.lower()
    return "bold" in name or "black" in name or "heavy" in name


def _segments(words: list[dict[str, Any]]) -> list[_Seg]:
    words = sorted(words, key=lambda w: (round(w["top"]), w["x0"]))
    lines: list[list[dict[str, Any]]] = []
    for w in words:
        if lines and abs(w["top"] - lines[-1][0]["top"]) <= 2.0:
            lines[-1].append(w)
        else:
            lines.append([w])
    segs: list[_Seg] = []
    for line in lines:
        line.sort(key=lambda w: w["x0"])
        group = [line[0]]
        for w in line[1:]:
            if w["x0"] - group[-1]["x1"] > SEGMENT_GAP:
                segs.append(_make_seg(group))
                group = [w]
            else:
                group.append(w)
        segs.append(_make_seg(group))
    return segs


def _make_seg(words: list[dict[str, Any]]) -> _Seg:
    return _Seg(
        x0=min(w["x0"] for w in words),
        x1=max(w["x1"] for w in words),
        top=min(w["top"] for w in words),
        bottom=max(w["bottom"] for w in words),
        text=" ".join(w["text"] for w in words),
        bold=all(_is_bold(w.get("fontname", "")) for w in words),
    )


def _blocks(segs: list[_Seg], page: int) -> list[Block]:
    open_blocks: list[tuple[Block, float]] = []  # (block, last line height)
    for s in sorted(segs, key=lambda s: (s.top, s.x0)):
        h = s.bottom - s.top
        target = None
        for blk, last_h in open_blocks:
            gap = s.top - blk.bottom
            if (abs(s.x0 - blk.x) <= ALIGN_TOLERANCE and -1.0 <= gap <= LINE_GAP_RATIO * max(h, last_h)
                    and s.bold == blk.bold):
                target = blk
                break
        if target is None:
            open_blocks.append((Block(s.x0, s.top, s.x1 - s.x0, h, [s.text], page, s.bold), h))
            continue
        x1 = max(target.right, s.x1)
        target.x = min(target.x, s.x0)
        target.width = x1 - target.x
        target.height = s.bottom - target.y
        target.lines.append(s.text)
        open_blocks = [(b, h if b is target else lh) for b, lh in open_blocks]
    return [b for b, _ in open_blocks]


def _classify(blocks: list[Block], page_h: float) -> None:
    """Assign roles in place. ``blocks`` is in reading order across pages."""
    salutation_seen = False
    closing_seen = False
    address_seen = False
    for b in blocks:
        first = b.lines[0]
        if b.page == 1 and b.y < LETTERHEAD_LIMIT:
            b.role = "letterhead"
        elif b.y > page_h - FOOTER_MARGIN:
            b.role = "footer"
        elif closing_seen:
            b.role = "closing"  # signature lines after the closing phrase
        elif _CLOSING_RE.match(first):
            b.role = "closing"
            closing_seen = True
        elif salutation_seen:
            b.role = "body"
        elif _SALUTATION_RE.match(first):
            b.role = "salutation"
            salutation_seen = True
        elif b.line_count == 1 and _DATE_RE.match(first):
            b.role = "date"
        elif _SUBJECT_RE.match(first):
            b.role = "subject"
        elif not address_seen and b.line_count >= 2 and b.page == 1:
            b.role = "address"
            address_seen = True
        else:
            b.role = "unknown"


def extract(pdf_path: Path) -> LetterStructure:
    """Read ``pdf_path`` and return its classified block structure.

    Raises :class:`ExtractionError` if the file cannot be opened or has no
    text layer (for example a scanned image).
    """
    pdf_path = Path(pdf_path)
    try:
        pdf = pdfplumber.open(pdf_path)
    except Exception as exc:  # pdfplumber raises several unrelated types
        raise ExtractionError(f"{pdf_path}: cannot open PDF ({exc})") from exc
    with pdf:
        if not pdf.pages:
            raise ExtractionError(f"{pdf_path}: PDF has no pages")
        first = pdf.pages[0]
        structure = LetterStructure(str(pdf_path), float(first.width), float(first.height),
                                    len(pdf.pages))
        for i, page in enumerate(pdf.pages, start=1):
            words = page.extract_words(extra_attrs=["fontname"], x_tolerance=1.5, y_tolerance=2)
            structure.blocks.extend(_blocks(_segments(words), i) if words else [])
    if not structure.blocks:
        raise ExtractionError(f"{pdf_path}: no extractable text (scanned or image-only PDF?)")
    structure.blocks.sort(key=lambda b: (b.page, b.y, b.x))
    _classify(structure.blocks, structure.page_height)
    return structure
