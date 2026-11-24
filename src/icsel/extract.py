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
#: Max vertical gap, as a fraction of text height, between lines of a block. 0.75 keeps
#: lines set at 1.5x spacing together, while a blank line (gap >= 1.0) still splits.
LINE_GAP_RATIO = 0.75
#: Blocks starting above this (pt from top) on page 1 count as letterhead.
LETTERHEAD_LIMIT = 100.0
#: Blocks starting this close (pt) to the page bottom count as footer.
FOOTER_MARGIN = 50.0

#: PDF "Creator" set on sample PDFs that icsel renders.
SAMPLE_CREATOR = "icsel sample renderer"
#: Title prefix of icsel sample PDFs (also set by versions before SAMPLE_CREATOR existed).
SAMPLE_TITLE_PREFIX = "icsel sample"
#: Font family icsel uses for its own labels and notes in sample PDFs.
DIAGNOSTIC_FONT = "Courier"
#: In icsel samples, letter text is never smaller than 7 pt; labels are 6-6.5 pt.
_MARK_MAX_SIZE = 6.75


def is_icsel_sample(metadata: dict[str, Any]) -> bool:
    """True if PDF metadata says the file is a sample rendered by icsel."""
    creator = str(metadata.get("Creator", ""))
    title = str(metadata.get("Title", ""))
    return creator == SAMPLE_CREATOR or title.startswith(SAMPLE_TITLE_PREFIX)


def _is_icsel_mark(word: dict[str, Any]) -> bool:
    """A label or note icsel drew on its own sample (not letter content).

    Current samples draw these in Courier. Older samples drew box labels in
    6-6.5 pt Helvetica and the fidelity note in Helvetica-Oblique; icsel never
    draws letter content that small or in an oblique font.
    """
    font = str(word.get("fontname", ""))
    size = float(word.get("size", 99.0))
    return DIAGNOSTIC_FONT in font or "Oblique" in font or size < _MARK_MAX_SIZE


#: Roles that together make up the "body" area of a letter.
BODY_ROLES = ("salutation", "body", "closing")

_MONTHS = (
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?"
    r"|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?"
)
_DATE_RE = re.compile(
    rf"^\s*(\d{{1,2}}\s+{_MONTHS}\s+\d{{4}}|{_MONTHS}\s+\d{{1,2}},?\s+\d{{4}}"
    rf"|\d{{4}}-\d{{2}}-\d{{2}}|\d{{1,2}}[./]\d{{1,2}}[./]\d{{2,4}})\s*$",
    re.IGNORECASE,
)
#: A template placeholder for the letter date, e.g. "[Date]" or "[Letter Date]".
_DATE_PLACEHOLDER_RE = re.compile(r"^\s*\[[^\]]*\bdate\b[^\]]*\]\s*$", re.IGNORECASE)
#: A line that is only a placeholder, e.g. "[Name of Account Holder]".
_PLACEHOLDER_RE = re.compile(r"^\s*\[[^\]]*\]\s*[,.]?\s*$")
_SUBJECT_RE = re.compile(r"^\s*(subject|re|regarding)\s*:", re.IGNORECASE)
_SALUTATION_RE = re.compile(r"^\s*(dear|hello|hi|to whom it may concern)\b", re.IGNORECASE)
_CLOSING_RE = re.compile(
    r"^\s*(kind regards|regards|best regards|sincerely|yours (sincerely|faithfully|truly))\b",
    re.IGNORECASE,
)


class ExtractionError(RuntimeError):
    """Raised when a PDF cannot be read or contains no extractable text."""


class NoTextError(ExtractionError):
    """The PDF opened fine but has no text layer (for example a scanned image)."""


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
    #: Who assigned ``role``: ``"rules"`` or ``"llm"`` (the optional AI step).
    role_source: str = "rules"
    #: Why the AI step chose the role (empty for rule-based roles).
    note: str = ""

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
            "role_source": self.role_source,
            **({"note": self.note} if self.note else {}),
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
        is what a layout's body zone holds, plus any ``unknown`` blocks that sit
        below the header blocks (address, subject, date) and above the end of
        the closing, such as a heading or a table at the top of the body.
        """
        found = self.body_blocks() if role == "body" else self.blocks_with(role, page=1)[:1]
        if not found:
            return None
        x0 = min(b.x for b in found)
        y0 = min(b.y for b in found)
        x1 = max(b.right for b in found)
        y1 = max(b.bottom for b in found)
        lines = [ln for b in found for ln in b.lines]
        return Block(
            x0, y0, x1 - x0, y1 - y0, lines, page=1, bold=all(b.bold for b in found), role=role
        )

    def body_blocks(self) -> list[Block]:
        """Page-1 blocks that make up the body area (see :meth:`region`)."""
        core = self.blocks_with(*BODY_ROLES, page=1)
        if not core:
            return []
        header = self.blocks_with("address", "subject", "date", page=1)
        head_bottom = max((b.bottom for b in header), default=LETTERHEAD_LIMIT)
        end = max(b.bottom for b in core)
        extra = [
            b for b in self.blocks_with("unknown", page=1) if b.y >= head_bottom and b.bottom <= end
        ]
        return core + extra

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
            # A bold first line followed by regular lines stays together: the usual
            # "bold recipient name above the address" pattern. A bold "Subject:" line
            # never absorbs the text under it.
            bold_name_line = (
                blk.bold
                and not s.bold
                and len(blk.lines) == 1
                and not _SUBJECT_RE.match(blk.lines[0])
            )
            if (
                abs(s.x0 - blk.x) <= ALIGN_TOLERANCE
                and -1.0 <= gap <= LINE_GAP_RATIO * max(h, last_h)
                and (s.bold == blk.bold or bold_name_line)
            ):
                target = blk
                break
        if target is None:
            open_blocks.append((Block(s.x0, s.top, s.x1 - s.x0, h, [s.text], page, s.bold), h))
            continue
        target.bold = target.bold and s.bold
        x1 = max(target.right, s.x1)
        target.x = min(target.x, s.x0)
        target.width = x1 - target.x
        target.height = s.bottom - target.y
        target.lines.append(s.text)
        open_blocks = [(b, h if b is target else lh) for b, lh in open_blocks]
    return [b for b, _ in open_blocks]


def _table_cells(blocks: list[Block]) -> set[int]:
    """Ids of blocks that sit in a row with two or more other blocks (table cells).

    Two side-by-side blocks are common in letters (address beside a subject box),
    so only rows of three or more count as a table.
    """
    cells: set[int] = set()
    for b in blocks:
        beside = [
            o
            for o in blocks
            if o is not b and o.page == b.page and o.y < b.bottom - 1 and o.bottom > b.y + 1
        ]
        if len(beside) >= 2:
            cells.add(id(b))
    return cells


def _looks_like_heading(b: Block) -> bool:
    """A short bold or ALL-CAPS line such as "NOTICE OF PAST-DUE ACCOUNT".

    Excludes lone placeholders ("[Name of Account Holder]"), single words and
    lines ending like a greeting or a label (",", ":" or ";").
    """
    text = b.text.strip()
    if b.line_count > 3 or len(re.findall(r"[A-Za-z]{2,}", text)) < 2:
        return False
    if _PLACEHOLDER_RE.match(text) or text.endswith((",", ":", ";")):
        return False
    letters = [c for c in text if c.isalpha()]
    mostly_caps = bool(letters) and sum(c.isupper() for c in letters) / len(letters) > 0.8
    return b.bold or mostly_caps


def _classify(blocks: list[Block], page_h: float) -> None:
    """Assign roles in place. ``blocks`` is in reading order across pages."""
    salutation_seen = False
    closing_seen = False
    address_seen = False
    subject_seen = False
    cells = _table_cells(blocks)
    for b in blocks:
        first = b.lines[0]
        in_table = id(b) in cells
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
        elif in_table:
            b.role = "unknown"  # a date or name inside a table is data, not the letter's date
        elif b.line_count == 1 and (_DATE_RE.match(first) or _DATE_PLACEHOLDER_RE.match(first)):
            b.role = "date"
        elif _SUBJECT_RE.match(first):
            b.role = "subject"
            subject_seen = True
        elif (
            not subject_seen
            and b.page == 1
            and (b.line_count == 1 or address_seen)
            and _looks_like_heading(b)
        ):
            # A heading above the greeting states what the letter is about. Before the
            # address only one-line headings count, so a bold address is not taken.
            b.role = "subject"
            subject_seen = True
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
        structure = LetterStructure(
            str(pdf_path), float(first.width), float(first.height), len(pdf.pages)
        )
        own_sample = is_icsel_sample(pdf.metadata or {})
        for i, page in enumerate(pdf.pages, start=1):
            words = page.extract_words(
                extra_attrs=["fontname", "size"], x_tolerance=1.5, y_tolerance=2
            )
            if own_sample:
                # A sample PDF made by icsel: drop its own labels and notes so a
                # downloaded preview reads back as just the letter.
                words = [w for w in words if not _is_icsel_mark(w)]
            structure.blocks.extend(_blocks(_segments(words), i) if words else [])
    if not structure.blocks:
        raise NoTextError(f"{pdf_path}: no extractable text (scanned or image-only PDF?)")
    structure.blocks.sort(key=lambda b: (b.page, b.y, b.x))
    _classify(structure.blocks, structure.page_height)
    return structure
