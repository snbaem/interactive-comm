"""Render a sample PDF: the letter's text placed into a layout's zones.

This is a LOCAL APPROXIMATION for eyeballing fit. It is not output from AEM
Forms Interactive Communications and does not reproduce its fonts, fragments,
data binding or pagination rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.utils import simpleSplit
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (
    BaseDocTemplate,
    Flowable,
    Frame,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from .extract import DIAGNOSTIC_FONT, SAMPLE_CREATOR, Block, LetterStructure
from .profile import LayoutProfile, Zone

FIDELITY_NOTE = (
    "Sample generated locally by icsel - an approximation of layout '{layout}'. "
    "Not output from AEM Forms Interactive Communications."
)

_BODY = ParagraphStyle("body", fontName="Helvetica", fontSize=10, leading=12, spaceAfter=8)
_CELL = ParagraphStyle("cell", parent=_BODY, fontSize=8.5, leading=10.5, spaceAfter=0)
_UNPLACED = ParagraphStyle(
    "unplaced", parent=_BODY, backColor=colors.HexColor("#fff1c7"), borderPadding=(2, 3, 2, 3)
)
_SIZE = 10.0
_LEADING = _SIZE + 2
_OVERFLOW = colors.HexColor("#c62828")

#: Page-1 roles whose text is placed into a fixed-size zone (the body flows instead).
FIXED_ROLES = ("date", "address", "subject")


@dataclass
class Overflow:
    """Text that does not fit a zone once wrapped to the zone's width."""

    role: str
    lines: int
    capacity: int

    @property
    def extra(self) -> int:
        """Lines beyond the zone's capacity."""
        return self.lines - self.capacity

    def to_dict(self) -> dict[str, object]:
        """Plain-data view for JSON output."""
        return {
            "role": self.role,
            "lines": self.lines,
            "capacity": self.capacity,
            "extra": self.extra,
        }


def zone_capacity(zone: Zone) -> int:
    """Lines a zone holds: its ``max_lines``, or as many as its height allows."""
    if zone.max_lines is not None:
        return zone.max_lines
    return max(1, int((zone.height + 2) // _LEADING))


def wrap_for_zone(lines: list[str], zone: Zone, bold: bool = False) -> list[str]:
    """Wrap text lines to a zone's width, as the sample renderer does."""
    font = "Helvetica-Bold" if bold else "Helvetica"
    wrapped: list[str] = []
    for line in lines:
        wrapped.extend(simpleSplit(line, font, _SIZE, zone.width) or [""])
    return wrapped


def overflows(letter: LetterStructure, layout: LayoutProfile) -> list[Overflow]:
    """List the fixed zones whose letter text would not fit in ``layout``.

    The text of each block is wrapped to the zone width first, so a subject that
    is too wide shows up as extra lines.
    """
    found: list[Overflow] = []
    for role in FIXED_ROLES:
        zone = layout.zone(role)
        blocks = letter.blocks_with(role, page=1)
        if zone is None or not blocks:
            continue
        wrapped = wrap_for_zone(blocks[0].lines, zone, blocks[0].bold)
        cap = zone_capacity(zone)
        if len(wrapped) > cap:
            found.append(Overflow(role, len(wrapped), cap))
    return found


def _draw_zone_text(
    c: Canvas,
    page_h: float,
    zone: Zone,
    lines: list[str],
    bold: bool = False,
    mark_overflow: bool = True,
) -> None:
    """Draw text into a zone.

    Every line is drawn, even beyond the zone's capacity. With ``mark_overflow``
    the extra lines are red and labeled; without it they look like the rest.
    """
    wrapped = wrap_for_zone(lines, zone, bold)
    cap = zone_capacity(zone)
    c.saveState()
    c.setFont("Helvetica-Bold" if bold else "Helvetica", _SIZE)
    for i, line in enumerate(wrapped):
        if i == cap and mark_overflow:
            c.setFillColor(_OVERFLOW)
        c.drawString(zone.x, page_h - zone.y - _SIZE * 0.8 - i * _LEADING, line)
    if mark_overflow and len(wrapped) > cap:
        extra = len(wrapped) - cap
        c.setFont(DIAGNOSTIC_FONT + "-Bold", 6.5)
        c.setFillColor(_OVERFLOW)
        label = f"{zone.role}: {extra} line{'s' if extra > 1 else ''} over the box ({cap} max)"
        c.drawRightString(zone.right, page_h - zone.bottom - 8, label)
    c.restoreState()


def _draw_outline(c: Canvas, page_h: float, zone: Zone) -> None:
    c.saveState()
    c.setStrokeColor(colors.HexColor("#9fb4c7"))
    c.setDash(2, 2)
    c.setLineWidth(0.5)
    c.rect(zone.x, page_h - zone.bottom, zone.width, zone.height)
    c.setFont(DIAGNOSTIC_FONT, 6)
    c.setFillColor(colors.HexColor("#6b8299"))
    c.drawString(zone.x + 2, page_h - zone.y + 2, zone.role)
    c.restoreState()


#: Roles drawn into a fixed zone of the same role when the layout has one.
ZONED_ROLES = ("letterhead", "date", "address", "subject", "footer")
#: Roles that belong in the body flow.
FLOW_ROLES = ("salutation", "body", "closing")

_UNPLACED_BG = colors.HexColor("#fff1c7")
_TABLE_GRID = colors.HexColor("#c9d1dc")


@dataclass
class Placement:
    """Where each block of a letter goes in one layout.

    Attributes:
        zoned: Page-1 blocks drawn into the zone of their role.
        flow: Every other block, in reading order, poured into the body area.
        unplaced: The blocks in ``flow`` that are not body content: ``unknown``
            blocks outside the body area, extra blocks of a zoned role, or roles
            the layout has no zone for. They are shown, but the layout does not
            account for them.
    """

    zoned: dict[str, list[Block]]
    flow: list[Block]
    unplaced: list[Block]


def plan_placement(letter: LetterStructure, layout: LayoutProfile) -> Placement:
    """Decide which zone (or the body flow) shows each block, so nothing is dropped.

    Only repeated page furniture is left out: letterhead and footer blocks on
    pages after the first.
    """
    zoned: dict[str, list[Block]] = {}
    flow: list[Block] = []
    for b in sorted(letter.blocks, key=lambda b: (b.page, b.y, b.x)):
        furniture = b.role in ("letterhead", "footer")
        if furniture and b.page > 1:
            continue
        if (
            b.page == 1
            and b.role in ZONED_ROLES
            and layout.zone(b.role) is not None
            and (furniture or b.role not in zoned)
        ):
            zoned.setdefault(b.role, []).append(b)
            continue
        flow.append(b)
    # Unknown blocks inside the body area (e.g. a table under the address) are body
    # content; only the rest has no place in this layout.
    in_body = {id(b) for b in letter.body_blocks()}
    unplaced = [b for b in flow if b.role not in FLOW_ROLES and id(b) not in in_body]
    return Placement(zoned, flow, unplaced)


def _rows(blocks: list[Block]) -> list[list[Block]]:
    """Group blocks that sit side by side (overlapping vertically) into rows."""
    rows: list[list[Block]] = []
    for b in blocks:
        last = rows[-1] if rows else None
        if (
            last
            and b.page == last[0].page
            and b.y < max(x.bottom for x in last) - 1
            and b.bottom > min(x.y for x in last) + 1
        ):
            last.append(b)
        else:
            rows.append([b])
    return [sorted(r, key=lambda b: b.x) for r in rows]


def _para_text(b: Block) -> str:
    # Body prose is reflowed to the new width; everything else keeps its line breaks.
    text = escape(b.text) if b.role == "body" else "<br/>".join(escape(ln) for ln in b.lines)
    return f"<b>{text}</b>" if b.bold else text


def _flow_story(placement: Placement, width: float, mark: bool) -> list[Flowable]:
    unplaced = {id(b) for b in placement.unplaced}
    story: list[Flowable] = []
    rows = _rows(placement.flow)
    i = 0
    while i < len(rows):
        row = rows[i]
        if len(row) == 1:
            b = row[0]
            style = _UNPLACED if (mark and id(b) in unplaced) else _BODY
            story.append(Paragraph(_para_text(b), style))
            i += 1
            continue
        # Consecutive side-by-side rows become one table (e.g. an account summary grid).
        table_rows = [row]
        while i + 1 < len(rows) and len(rows[i + 1]) > 1:
            i += 1
            table_rows.append(rows[i])
        i += 1
        ncols = max(len(r) for r in table_rows)
        data = [
            [Paragraph(_para_text(b), _CELL) for b in r] + [""] * (ncols - len(r))
            for r in table_rows
        ]
        table = Table(data, colWidths=[width / ncols] * ncols, hAlign="LEFT")
        style = [
            ("GRID", (0, 0), (-1, -1), 0.4, _TABLE_GRID),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]
        if mark and any(id(b) in unplaced for r in table_rows for b in r):
            style.append(("BACKGROUND", (0, 0), (-1, -1), _UNPLACED_BG))
        table.setStyle(TableStyle(style))
        story += [table, Spacer(1, 8)]
    return story or [Spacer(1, 1)]


def _draw_letterhead(c: Canvas, page_h: float, zone: Zone, blocks: list[Block]) -> None:
    # First line (the sender's name) large and bold; contact lines small. Every line
    # is wrapped to the zone width so long contact lines stay on the page.
    top = zone.y
    lines = [ln for b in blocks for ln in b.lines]
    for n, line in enumerate(lines):
        font, size = ("Helvetica-Bold", 14.0) if n == 0 else ("Helvetica", 8.5)
        c.setFont(font, size)
        for part in simpleSplit(line, font, size, zone.width) or [""]:
            c.drawString(zone.x, page_h - top - size * 0.85, part)
            top += size + 3


def _draw_footer(c: Canvas, page_h: float, zone: Zone, blocks: list[Block]) -> None:
    c.saveState()
    c.setFont("Helvetica", 7)
    text = "   |   ".join(b.text for b in blocks)
    for i, line in enumerate(simpleSplit(text, "Helvetica", 7, zone.width)[:2]):
        c.drawString(zone.x, page_h - zone.y - 7 - i * 9, line)
    c.restoreState()


def render_sample(
    letter: LetterStructure, layout: LayoutProfile, out: Path, show_zones: bool = True
) -> Path:
    """Write a sample PDF of ``letter`` laid out with ``layout``. Returns ``out``.

    All of the letter's content is shown (see :func:`plan_placement`): blocks go
    into the zone of their role, and everything else flows into the body area in
    reading order, with side-by-side blocks kept together as a table.

    With ``show_zones`` (the default) the page is a diagnostic view: zones are
    outlined, text that does not fit its zone is drawn in red with a label, and
    content the layout has no zone for gets an amber background. Without it the
    page is clean: no outlines, labels or highlights, but still all the content.
    """
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    page_w, page_h = layout.page_width, layout.page_height
    note = FIDELITY_NOTE.format(layout=layout.id)
    placement = plan_placement(letter, layout)

    body_zone = layout.zone("body") or Zone("body", 72, 144, page_w - 144, page_h - 216)
    pad = {"leftPadding": 0, "rightPadding": 0, "topPadding": 0, "bottomPadding": 0}
    first_frame = Frame(
        body_zone.x,
        page_h - body_zone.bottom,
        body_zone.width,
        body_zone.height,
        id="first",
        **pad,
    )
    later_frame = Frame(body_zone.x, 72, body_zone.width, page_h - 144, id="later", **pad)

    def fidelity_footer(c: Canvas) -> None:
        c.saveState()
        font = DIAGNOSTIC_FONT + "-Oblique"
        c.setFont(font, 7)
        c.setFillColor(colors.HexColor("#555555"))
        # Courier is wide: wrap so the whole notice stays on the page.
        lines = simpleSplit(note, font, 7, page_w - 72)
        for i, line in enumerate(lines):
            c.drawCentredString(page_w / 2, 4 + 8 * (len(lines) - 1 - i), line)
        c.restoreState()

    def on_first(c: Canvas, _doc: object) -> None:
        for zone in layout.zones:
            if show_zones:
                _draw_outline(c, page_h, zone)
            blocks = placement.zoned.get(zone.role)
            if not blocks or zone.role == "body":
                continue
            if zone.role == "letterhead":
                _draw_letterhead(c, page_h, zone, blocks)
            elif zone.role == "footer":
                _draw_footer(c, page_h, zone, blocks)
            else:
                _draw_zone_text(
                    c, page_h, zone, blocks[0].lines, bold=blocks[0].bold, mark_overflow=show_zones
                )
        fidelity_footer(c)

    def on_later(c: Canvas, _doc: object) -> None:
        fidelity_footer(c)

    doc = BaseDocTemplate(
        str(out),
        pagesize=(page_w, page_h),
        invariant=1,
        title=f"icsel sample - {layout.id}",
        author="icsel (local approximation)",
        creator=SAMPLE_CREATOR,
        subject="Local approximation; not AEM Forms Interactive Communications output",
    )
    doc.addPageTemplates(
        [
            PageTemplate("first", [first_frame], onPage=on_first, autoNextPageTemplate="later"),
            PageTemplate("later", [later_frame], onPage=on_later),
        ]
    )
    doc.build(_flow_story(placement, body_zone.width, mark=show_zones))
    return out
