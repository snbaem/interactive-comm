"""Render a sample PDF: the letter's text placed into a layout's zones.

This is a LOCAL APPROXIMATION for eyeballing fit. It is not output from AEM
Forms Interactive Communications and does not reproduce its fonts, fragments,
data binding or pagination rules.
"""

from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.utils import simpleSplit
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import BaseDocTemplate, Frame, PageTemplate, Paragraph, Spacer

from .extract import LetterStructure
from .profile import LayoutProfile, Zone

FIDELITY_NOTE = (
    "Sample generated locally by icsel - an approximation of layout '{layout}'. "
    "Not output from AEM Forms Interactive Communications."
)

_BODY = ParagraphStyle("body", fontName="Helvetica", fontSize=10, leading=12, spaceAfter=8)


def _draw_zone_text(c: Canvas, page_h: float, zone: Zone, lines: list[str], bold: bool = False,
                    size: float = 10.0) -> None:
    font = "Helvetica-Bold" if bold else "Helvetica"
    wrapped: list[str] = []
    for line in lines:
        wrapped.extend(simpleSplit(line, font, size, zone.width) or [""])
    if zone.max_lines is not None:
        wrapped = wrapped[: zone.max_lines]
    c.setFont(font, size)
    for i, line in enumerate(wrapped):
        c.drawString(zone.x, page_h - zone.y - size * 0.8 - i * (size + 2), line)


def _draw_outline(c: Canvas, page_h: float, zone: Zone) -> None:
    c.saveState()
    c.setStrokeColor(colors.HexColor("#9fb4c7"))
    c.setDash(2, 2)
    c.setLineWidth(0.5)
    c.rect(zone.x, page_h - zone.bottom, zone.width, zone.height)
    c.setFont("Helvetica", 6)
    c.setFillColor(colors.HexColor("#6b8299"))
    c.drawString(zone.x + 2, page_h - zone.y + 2, zone.role)
    c.restoreState()


def render_sample(letter: LetterStructure, layout: LayoutProfile, out: Path,
                  show_zones: bool = True) -> Path:
    """Write a sample PDF of ``letter`` laid out with ``layout``. Returns ``out``."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    page_w, page_h = layout.page_width, layout.page_height
    note = FIDELITY_NOTE.format(layout=layout.id)

    body_zone = layout.zone("body") or Zone("body", 72, 144, page_w - 144, page_h - 216)
    first_frame = Frame(body_zone.x, page_h - body_zone.bottom, body_zone.width, body_zone.height,
                        leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0, id="first")
    later_frame = Frame(body_zone.x, 72, body_zone.width, page_h - 144,
                        leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0, id="later")

    def footer(c: Canvas) -> None:
        c.saveState()
        c.setFont("Helvetica-Oblique", 7)
        c.setFillColor(colors.HexColor("#555555"))
        c.drawCentredString(page_w / 2, 18, note)
        c.restoreState()

    def on_first(c: Canvas, _doc: object) -> None:
        for zone in layout.zones:
            if show_zones:
                _draw_outline(c, page_h, zone)
            if zone.role in ("body", "footer"):
                continue
            blocks = letter.blocks_with(zone.role, page=1)
            if zone.role == "letterhead" and blocks:
                c.setFont("Helvetica-Bold", 14)
                c.drawString(zone.x, page_h - zone.y - 16, blocks[0].text)
                continue
            if blocks:
                _draw_zone_text(c, page_h, zone, blocks[0].lines, bold=blocks[0].bold)
        footer(c)

    def on_later(c: Canvas, _doc: object) -> None:
        footer(c)

    doc = BaseDocTemplate(str(out), pagesize=(page_w, page_h), invariant=1,
                          title=f"icsel sample - {layout.id}", author="icsel (local approximation)")
    doc.addPageTemplates([
        PageTemplate("first", [first_frame], onPage=on_first, autoNextPageTemplate="later"),
        PageTemplate("later", [later_frame], onPage=on_later),
    ])
    story: list = []
    for b in letter.blocks_with("salutation", "body", "closing"):
        # closing/signature lines keep their line breaks; prose is reflowed
        text = "<br/>".join(escape(ln) for ln in b.lines) if b.role == "closing" else escape(b.text)
        story.append(Paragraph(text, _BODY))
    if not story:
        story.append(Spacer(1, 1))
    doc.build(story)
    return out
