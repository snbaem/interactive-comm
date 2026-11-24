"""Recognize account statements and label their parts.

A document is treated as a statement only when all of these hold; everything else keeps
the letter rules unchanged:

* a one-line bold or ALL-CAPS title on the upper part of page 1 says "statement";
* there is no greeting ("Dear ...");
* it has a table of at least :data:`MIN_ROWS` rows with three or more columns.

Then the title becomes the ``subject``; "Label: value" lines above the table are split
into the document's ``date`` and ``reference`` details; table cells become ``table``; and
text after the table becomes ``body``.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .extract import Block, LetterStructure

#: Table rows needed before a document counts as a statement.
MIN_ROWS = 4
#: The title must start above this share of the page height.
TITLE_SHARE = 0.4

_TITLE_RE = re.compile(r"\bstatement\b", re.IGNORECASE)
_DETAIL_RE = re.compile(r"^[A-Za-z][A-Za-z #./-]{1,30}:\s*\S")
#: The document's own date. A due date or a period is a detail, not the date.
_DATE_LABEL_RE = re.compile(
    r"^(?:(?:statement|issue|issued|letter|billing|bill)\s+)?date\s*:", re.I
)


def _is_title(b: Block, page_height: float) -> bool:
    letters = [c for c in b.text if c.isalpha()]
    caps = bool(letters) and all(c.isupper() for c in letters)
    return (
        b.page == 1
        and b.line_count == 1
        and b.y < page_height * TITLE_SHARE
        and (b.bold or caps)
        and bool(_TITLE_RE.search(b.text))
    )


def _is_details(b: Block) -> bool:
    return b.page == 1 and all(_DETAIL_RE.match(ln) for ln in b.lines)


def _beside(b: Block, blocks: list[Block]) -> int:
    return sum(
        o is not b and o.page == b.page and o.y < b.bottom - 1 and o.bottom > b.y + 1
        for o in blocks
    )


def _line_blocks(b: Block) -> list[Block]:
    """One block per text line; line positions are spread evenly over the block."""
    from .extract import Block

    step = b.height / b.line_count
    return [
        Block(b.x, b.y + i * step, b.width, step, [line], b.page, b.bold, b.role)
        for i, line in enumerate(b.lines)
    ]


def _table_cells(blocks: list[Block], top: float) -> list[Block]:
    """Cells of tables that start below ``top`` on page 1, or anywhere on later pages."""
    skip = ("letterhead", "footer")
    usable = [b for b in blocks if b.role not in skip and (b.page > 1 or b.y >= top)]
    cells = [b for b in usable if _beside(b, usable) >= 2]
    spans: dict[int, tuple[float, float]] = {}
    for b in cells:
        lo, hi = spans.get(b.page, (b.y, b.bottom))
        spans[b.page] = (min(lo, b.y), max(hi, b.bottom))
    # A row with an empty column still belongs to the table around it.
    return [
        b
        for b in usable
        if b.page in spans and b.y >= spans[b.page][0] - 1 and b.bottom <= spans[b.page][1] + 1
    ]


def _row_count(cells: list[Block]) -> int:
    tops: list[tuple[int, float]] = []
    for line in sorted((ln for c in cells for ln in _line_blocks(c)), key=lambda b: (b.page, b.y)):
        if not tops or tops[-1][0] != line.page or line.y - tops[-1][1] > 4:
            tops.append((line.page, line.y))
    return len(tops)


def _split_details(b: Block) -> list[Block]:
    """Split "Label: value" lines into the document date and reference details."""
    lines = _line_blocks(b)
    has_date = False
    rest: list[Block] = []
    out: list[Block] = []
    for line in lines:
        if not has_date and _DATE_LABEL_RE.match(line.lines[0]):
            has_date = True
            line.role = "date"
            out.append(line)
        else:
            rest.append(line)
    if rest:
        first = rest[0]
        first.height = rest[-1].bottom - first.y
        first.lines = [ln for r in rest for ln in r.lines]
        first.role = "reference"
        out.append(first)
    return out


def label_statement(letter: LetterStructure) -> bool:
    """Relabel ``letter`` in place if it is a statement; return whether it is."""
    blocks = letter.blocks
    if any(b.role == "salutation" for b in blocks):
        return False
    title = next((b for b in blocks if _is_title(b, letter.page_height)), None)
    if title is None:
        return False
    details = [b for b in blocks if b is not title and _is_details(b)]
    limit = letter.page_height * TITLE_SHARE
    details = [b for b in details if b.role not in ("letterhead", "footer") and b.y < limit]
    top = max([title.bottom, *(b.bottom for b in details)])
    cells = _table_cells(blocks, top)
    if _row_count(cells) < MIN_ROWS:
        return False

    letter.kind = "statement"
    for b in blocks:  # the letter rules may have taken something else for the subject
        if b.role == "subject" and b is not title:
            b.role = "unknown"
    title.role = "subject"
    in_table = {id(b) for b in cells}
    in_details = {id(b) for b in details}
    last_cell = {c.page: c.bottom for c in sorted(cells, key=lambda c: c.bottom)}
    out: list[Block] = []
    for b in blocks:
        if id(b) in in_table:
            for line in _line_blocks(b):
                line.role = "table"
                out.append(line)
        elif id(b) in in_details:
            out.extend(_split_details(b))
        else:
            after = b.page > 1 or b.y >= last_cell.get(1, letter.page_height)
            if b.role == "unknown" and after:
                b.role = "body"
            out.append(b)
    if not any(b.role == "address" for b in out):
        # The details block can take the address label when it sits higher on the page.
        table_top = min((c.y for c in cells if c.page == 1), default=letter.page_height)
        for b in out:
            if b.page == 1 and b.role == "unknown" and b.line_count >= 2 and b.bottom <= table_top:
                b.role = "address"
                break
    letter.blocks = sorted(out, key=lambda b: (b.page, b.y, b.x))
    return True
