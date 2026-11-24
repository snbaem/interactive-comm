"""Draw a layout with its measurements: the "dimensioned layout view".

One page is drawn to scale with every zone. Dimension lines with arrowheads show
each zone's distance from the top and left page edges (in the gutters, one lane per
distinct value so lines never overlap), the page margins, the gaps between
neighbouring zones, each zone's width and height (when it is large enough to hold the
text) and the line capacity beside the address, subject and date boxes. A legend
panel lists every number in full. Labels are placed with a small collision check that
nudges them clear of zones and of each other.

Optional overlays: a letter's detected blocks (``letter=``), or another layout's
zones (``reference=``, used by the diff view). Output is a PDF; :func:`pdf_to_png`
turns it into a PNG with pdfplumber's renderer (no extra dependency).

The numbers come from :mod:`icsel.dimensions`; this module only draws them.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from reportlab.lib import colors
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen.canvas import Canvas

from .dimensions import (
    Difference,
    diff_layouts,
    format_length,
    letter_vs_layout,
    measure,
    missing_roles,
)
from .extract import LetterStructure
from .profile import LayoutProfile, Zone

ROLE_COLORS = {
    "letterhead": "#6a1b9a",
    "address": "#1565c0",
    "date": "#00838f",
    "subject": "#ef6c00",
    "salutation": "#558b2f",
    "body": "#455a64",
    "closing": "#8d6e63",
    "footer": "#757575",
    "unknown": "#9e9e9e",
}
_DIM = colors.HexColor("#c2185b")
_FONT = "Helvetica"
_SMALL = 7.0
_LANE = 24.0
_FIDELITY = "Measurements are computed from the layout profile (points, top-left origin)."


@dataclass
class _Rect:
    x0: float
    y0: float
    x1: float
    y1: float

    def hits(self, other: _Rect) -> bool:
        return (
            self.x0 < other.x1 and other.x0 < self.x1 and self.y0 < other.y1 and other.y0 < self.y1
        )


class _Frame:
    """Maps page points (top-left origin) to canvas points (bottom-left origin)."""

    def __init__(self, ox: float, oy_top: float, scale: float, canvas_h: float) -> None:
        self.ox, self.oy, self.s, self.h = ox, oy_top, scale, canvas_h

    def x(self, v: float) -> float:
        return self.ox + v * self.s

    def y(self, v: float) -> float:
        return self.h - (self.oy + v * self.s)


class _Placer:
    """Keeps text labels from overlapping each other or the zones."""

    def __init__(self, obstacles: list[_Rect]) -> None:
        self.obstacles = obstacles
        self.placed: list[_Rect] = []

    def place(self, text: str, x: float, y: float, anchor: str = "middle") -> tuple[float, float]:
        """Return a baseline-left position near ``(x, y)``, clear of other labels if possible."""
        w = stringWidth(text, _FONT, _SMALL)
        h = _SMALL + 1
        left = {"middle": x - w / 2, "start": x, "end": x - w}[anchor]
        steps = [(0, 0), (0, h), (0, -h), (w * 0.6, 0), (-w * 0.6, 0), (0, 2 * h), (0, -2 * h)]
        chosen = (left, y)
        for dx, dy in steps:
            cand = _Rect(left + dx, y + dy, left + dx + w, y + dy + h)
            if not any(cand.hits(r) for r in self.placed + self.obstacles):
                chosen = (left + dx, y + dy)
                break
        self.placed.append(_Rect(chosen[0], chosen[1], chosen[0] + w, chosen[1] + h))
        return chosen


def _arrow(c: Canvas, x0: float, y0: float, x1: float, y1: float) -> None:
    """Line with an arrowhead at both ends (heads shrink on very short lines)."""
    length = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
    if length < 1:
        return
    ux, uy = (x1 - x0) / length, (y1 - y0) / length
    head = min(4.5, length / 2.5)
    c.setStrokeColor(_DIM)
    c.setFillColor(_DIM)
    c.setLineWidth(0.6)
    c.line(x0, y0, x1, y1)
    for (px, py), sign in (((x0, y0), 1), ((x1, y1), -1)):
        bx, by = px + sign * ux * head, py + sign * uy * head
        nx, ny = -uy * head * 0.4, ux * head * 0.4
        p = c.beginPath()
        p.moveTo(px, py)
        p.lineTo(bx + nx, by + ny)
        p.lineTo(bx - nx, by - ny)
        p.close()
        c.drawPath(p, stroke=0, fill=1)


def _text(
    c: Canvas, x: float, y: float, text: str, color: object = _DIM, size: float = _SMALL
) -> None:
    w = stringWidth(text, _FONT, size)
    c.setFillColor(colors.white)
    c.setStrokeColor(colors.white)
    c.rect(x - 0.8, y - 1.2, w + 1.6, size + 1.6, stroke=0, fill=1)
    c.setFillColor(color)
    c.setFont(_FONT, size)
    c.drawString(x, y, text)


def _dashed_rect(c: Canvas, f: _Frame, z: Zone, color: str) -> None:
    c.setStrokeColor(colors.HexColor(color))
    c.setLineWidth(0.9)
    c.setDash(3, 2)
    c.rect(f.x(z.x), f.y(z.bottom), z.width * f.s, z.height * f.s, stroke=1, fill=0)
    c.setDash()


def _lanes(values: list[float]) -> dict[float, int]:
    """Lane number per distinct value; smaller values get lanes nearer the page."""
    distinct = sorted({round(v, 1) for v in values})
    return {v: i for i, v in enumerate(distinct)}


def _draw_page(
    c: Canvas,
    f: _Frame,
    layout: LayoutProfile,
    units: str,
    letter: LetterStructure | None = None,
    reference: LayoutProfile | None = None,
) -> None:
    m = measure(layout)
    s = f.s
    c.setFillColor(colors.white)
    c.setStrokeColor(colors.HexColor("#333333"))
    c.setLineWidth(1)
    c.rect(f.x(0), f.y(layout.page_height), layout.page_width * s, layout.page_height * s, fill=1)
    obstacles: list[_Rect] = []
    for z in layout.zones:
        col = colors.HexColor(ROLE_COLORS.get(z.role, "#9e9e9e"))
        c.setFillColor(col)
        c.setFillAlpha(0.13)
        c.setStrokeColor(col)
        c.rect(f.x(z.x), f.y(z.bottom), z.width * s, z.height * s, stroke=0, fill=1)
        c.setFillAlpha(1)
        c.setLineWidth(1)
        c.rect(f.x(z.x), f.y(z.bottom), z.width * s, z.height * s, stroke=1, fill=0)
        obstacles.append(_Rect(f.x(z.x), f.y(z.bottom), f.x(z.right), f.y(z.y)))
        _text_in_zone(c, f, z, col)
    if reference is not None:
        for z in reference.zones:
            _dashed_rect(c, f, z, "#2e7d32")
    if letter is not None:
        for b in letter.blocks:
            if b.page != 1 or b.role in ("unknown",):
                continue
            c.setStrokeColor(colors.HexColor("#c62828"))
            c.setLineWidth(0.9)
            c.setDash(3, 2)
            c.rect(f.x(b.x), f.y(b.bottom), b.width * s, b.height * s, stroke=1, fill=0)
            c.setDash()
    placer = _Placer(obstacles)
    # Distance from the top edge: vertical arrows in lanes left of the page.
    top_lane = _lanes([z.top for z in m.zones])
    for value, lane in top_lane.items():
        lx = f.x(0) - 10 - lane * _LANE
        _arrow(c, lx, f.y(0), lx, f.y(value))
        c.setStrokeColor(colors.HexColor("#c2185b"))
        c.setLineWidth(0.3)
        c.setDash(1, 2)
        zone_left = min(z.x for z in layout.zones if round(z.y, 1) == value)
        c.line(lx, f.y(value), f.x(zone_left), f.y(value))
        c.setDash()
        label = format_length(value, units)
        mid = (f.y(0) + f.y(value)) / 2
        tx, ty = placer.place(label, lx - 2, mid - 3, "end")
        _text(c, tx, ty, label)
    # Distance from the left edge: horizontal arrows in lanes above the page.
    left_lane = _lanes([z.left for z in m.zones])
    for value, lane in left_lane.items():
        ly = f.y(0) + 10 + lane * 12
        _arrow(c, f.x(0), ly, f.x(value), ly)
        c.setStrokeColor(colors.HexColor("#c2185b"))
        c.setLineWidth(0.3)
        c.setDash(1, 2)
        zone_top = min(z.y for z in layout.zones if round(z.x, 1) == value)
        c.line(f.x(value), ly, f.x(value), f.y(zone_top))
        c.setDash()
        label = format_length(value, units)
        tx, ty = placer.place(label, (f.x(0) + f.x(value)) / 2, ly + 1.5)
        _text(c, tx, ty, label)
    # Gaps between neighbouring zones.
    for g in m.gaps:
        a = layout.zone(g.first)
        b = layout.zone(g.second)
        if a is None or b is None or g.points <= 0:
            continue
        label = format_length(g.points, units)
        if g.direction == "vertical":
            x0, x1 = max(a.x, b.x), min(a.right, b.right)
            gx = f.x((x0 + x1) / 2)
            _arrow(c, gx, f.y(a.bottom), gx, f.y(b.y))
            mid = (f.y(a.bottom) + f.y(b.y)) / 2
            tx, ty = placer.place(label, gx + 3, mid - 3, "start")
        else:
            y0, y1 = max(a.y, b.y), min(a.bottom, b.bottom)
            gy = f.y((y0 + y1) / 2)
            _arrow(c, f.x(a.right), gy, f.x(b.x), gy)
            tx, ty = placer.place(label, (f.x(a.right) + f.x(b.x)) / 2, gy + 2)
        _text(c, tx, ty, label)
    # Page margins on the right and at the bottom (top and left are the lane values).
    right_x0 = f.x(layout.page_width - m.margins.right)
    mid_y = f.y(layout.page_height * 0.5)
    if m.margins.right > 0:
        _arrow(c, right_x0, mid_y, f.x(layout.page_width), mid_y)
        label = format_length(m.margins.right, units)
        tx, ty = placer.place(label, (right_x0 + f.x(layout.page_width)) / 2, mid_y + 2)
        _text(c, tx, ty, label)
    if m.margins.bottom > 0:
        bx = f.x(layout.page_width * 0.5)
        _arrow(c, bx, f.y(layout.page_height - m.margins.bottom), bx, f.y(layout.page_height))
        label = format_length(m.margins.bottom, units)
        mid = (f.y(layout.page_height - m.margins.bottom) + f.y(layout.page_height)) / 2
        tx, ty = placer.place(label, bx + 3, mid - 3, "start")
        _text(c, tx, ty, label)
    # Width and height inside zones that have room.
    for z in layout.zones:
        if z.width * s >= 44 and z.height * s >= 26:
            wx0, wx1, wy = f.x(z.x), f.x(z.right), f.y(z.bottom) + 6
            _arrow(c, wx0 + 1, wy, wx1 - 1, wy)
            label = format_length(z.width, units)
            tx, ty = placer.place(label, (wx0 + wx1) / 2, wy + 1.5)
            _text(c, tx, ty, label)
            hx, hy0, hy1 = f.x(z.right) - 6, f.y(z.y), f.y(z.bottom)
            _arrow(c, hx, hy0 - 1, hx, hy1 + 1)
            label = format_length(z.height, units)
            tx, ty = placer.place(label, hx - 2, (hy0 + hy1) / 2 - 3, "end")
            _text(c, tx, ty, label)
    # Line capacity beside the address, subject and date boxes.
    for zm in m.zones:
        if zm.capacity is None:
            continue
        z = next(q for q in layout.zones if q.role == zm.role)
        label = f"{zm.capacity} line{'s' if zm.capacity != 1 else ''}"
        gx = f.x(z.right) + 3
        tx, ty = placer.place(label, gx, f.y(z.y) - _SMALL, "start")
        c.setFillColor(colors.HexColor(ROLE_COLORS.get(z.role, "#555555")))
        c.setFont(_FONT, _SMALL)
        c.drawString(tx, ty, label)


def _text_in_zone(c: Canvas, f: _Frame, z: Zone, col: object) -> None:
    c.setFillColor(col)
    c.setFont(_FONT + "-Bold", _SMALL + 0.5)
    c.drawString(f.x(z.x) + 3, f.y(z.y) - _SMALL - 1.5, z.role)


def _legend_lines(
    layout: LayoutProfile,
    units: str,
    letter: LetterStructure | None,
    reference: LayoutProfile | None,
) -> list[tuple[str, str]]:
    """Legend text as ``(role-or-empty, line)`` pairs."""
    m = measure(layout)
    lines: list[tuple[str, str]] = [("", f"{layout.name} ({layout.id})"), ("", "")]
    fl = lambda v: format_length(v, units)  # noqa: E731
    for z in m.zones:
        lines.append((z.role, z.role))
        lines.append((z.role, f"  {fl(z.top)} from top, {fl(z.left)} from left"))
        size = f"  {fl(z.width)} wide x {fl(z.height)} high"
        if z.capacity is not None:
            size += f", {z.capacity} line{'s' if z.capacity != 1 else ''}"
        lines.append((z.role, size))
    lines.append(("", ""))
    mg = m.margins
    lines.append(("", "Page margins"))
    lines.append(
        (
            "",
            f"  top {fl(mg.top)}, left {fl(mg.left)}, right {fl(mg.right)}, bottom {fl(mg.bottom)}",
        )
    )
    if m.gaps:
        lines.append(("", ""))
        lines.append(("", "Gaps between zones"))
        for g in m.gaps:
            word = "below" if g.direction == "vertical" else "to the right of"
            lines.append(("", f"  {g.second} {fl(g.points)} {word} {g.first}"))
    if reference is not None:
        lines += [("", ""), ("", f"Compared with {reference.id} (green dashes)")]
        lines += [
            ("", "  " + d.describe(units, reference.id))
            for d in diff_layouts(layout, reference)
            if not d.is_zero
        ]
        only_here, only_ref = missing_roles(layout, reference)
        if only_here:
            lines.append(("", f"  only in {layout.id}: {', '.join(only_here)}"))
        if only_ref:
            lines.append(("", f"  only in {reference.id}: {', '.join(only_ref)}"))
    if letter is not None:
        lines += [("", ""), ("", f"Letter {Path(letter.source).name} (red dashes)")]
        diffs = [d for d in letter_vs_layout(letter, layout) if not d.is_zero]
        lines += [("", "  " + d.describe(units, "layout")) for d in diffs] or [
            ("", "  matches the layout")
        ]
    return lines


def _draw_legend(c: Canvas, x: float, top: float, lines: list[tuple[str, str]]) -> None:
    y = top
    for role, text in lines:
        if y < 20:
            break
        col = (
            colors.HexColor(ROLE_COLORS.get(role, "#222222"))
            if role
            else colors.HexColor("#222222")
        )
        bold = (
            bool(text)
            and not text.startswith("  ")
            and bool(role or text in ("Page margins", "Gaps between zones"))
        )
        c.setFillColor(col)
        c.setFont(_FONT + ("-Bold" if bold else ""), 8)
        c.drawString(x, y, text)
        y -= 10.5


def render_layout_view(
    layout: LayoutProfile,
    out_pdf: Path,
    units: str = "in",
    letter: LetterStructure | None = None,
    reference: LayoutProfile | None = None,
    scale: float = 0.62,
) -> Path:
    """Write a one-page PDF showing ``layout`` with every dimension.

    Args:
        layout: The layout to draw.
        out_pdf: Where to write the PDF.
        units: ``in``, ``cm`` or ``pt``.
        letter: Overlay the letter's detected blocks (red dashes) and list how far each
            sits from the matching zone.
        reference: Overlay another layout's zones (green dashes) and list the
            differences (the diff view).
        scale: Drawing scale; 0.62 fits a Letter page in about 380 x 490 points.
    """
    out_pdf = Path(out_pdf)
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    m = measure(layout)
    lanes_left = len(_lanes([z.top for z in m.zones]))
    lanes_top = len(_lanes([z.left for z in m.zones]))
    gutter_l = 14 + lanes_left * _LANE
    gutter_t = 16 + lanes_top * 12
    page_w, page_h = layout.page_width * scale, layout.page_height * scale
    legend = _legend_lines(layout, units, letter, reference)
    legend_w = 360.0
    height = max(gutter_t + page_h + 36, 30 + len(legend) * 10.5)
    width = gutter_l + page_w + 40 + legend_w
    c = Canvas(str(out_pdf), pagesize=(width, height))
    c.setTitle(f"Dimensioned layout: {layout.id}")
    c.setCreator("icsel")
    c.setFont(_FONT, 8)
    f = _Frame(gutter_l, gutter_t, scale, height)
    _draw_page(c, f, layout, units, letter, reference)
    _draw_legend(c, gutter_l + page_w + 40, height - 16, legend)
    c.setFillColor(colors.HexColor("#777777"))
    c.setFont(_FONT, 6.5)
    c.drawString(gutter_l, 10, f"{_FIDELITY} Units: {units}.")
    c.showPage()
    c.save()
    return out_pdf


def render_comparison(
    left: LayoutProfile, right: LayoutProfile, out_pdf: Path, units: str = "in", scale: float = 0.5
) -> Path:
    """Write a one-page landscape PDF with two layouts side by side and their differences."""
    out_pdf = Path(out_pdf)
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    views = []
    for lay in (left, right):
        m = measure(lay)
        views.append(
            (
                lay,
                14 + len(_lanes([z.top for z in m.zones])) * _LANE,
                16 + len(_lanes([z.left for z in m.zones])) * 12,
            )
        )
    diff_lines = [f"{right.id} compared with {left.id}:"]
    diff_lines += [
        "  " + d.describe(units, left.id) for d in diff_layouts(right, left) if not d.is_zero
    ]
    only_right, only_left = missing_roles(right, left)
    if only_right:
        diff_lines.append(f"  only in {right.id}: {', '.join(only_right)}")
    if only_left:
        diff_lines.append(f"  only in {left.id}: {', '.join(only_left)}")
    if len(diff_lines) == 1:
        diff_lines.append("  identical zones")
    page_h = max(lay.page_height for lay, _, _ in views) * scale
    gutter_t = max(t for _, _, t in views)
    height = gutter_t + page_h + 36 + len(diff_lines) * 10.5 + 16
    width = sum(g + lay.page_width * scale + 50 for lay, g, _ in views)
    c = Canvas(str(out_pdf), pagesize=(width, height))
    c.setTitle(f"Layout comparison: {left.id} vs {right.id}")
    c.setCreator("icsel")
    x = 0.0
    for lay, gutter_l, _ in views:
        f = _Frame(x + gutter_l, gutter_t, scale, height)
        _draw_page(c, f, lay, units)
        c.setFillColor(colors.HexColor("#222222"))
        c.setFont(_FONT + "-Bold", 9)
        c.drawString(x + gutter_l, f.y(lay.page_height) - 14, f"{lay.name} ({lay.id})")
        x += gutter_l + lay.page_width * scale + 50
    y = 30 + len(diff_lines) * 10.5
    c.setFont(_FONT, 8)
    for i, line in enumerate(diff_lines):
        c.setFillColor(colors.HexColor("#222222"))
        c.setFont(_FONT + ("-Bold" if i == 0 else ""), 8)
        c.drawString(14, y - i * 10.5, line)
    c.setFillColor(colors.HexColor("#777777"))
    c.setFont(_FONT, 6.5)
    c.drawString(14, 10, f"{_FIDELITY} Units: {units}.")
    c.showPage()
    c.save()
    return out_pdf


def pdf_to_png(pdf: Path, png: Path, resolution: int = 144) -> Path:
    """Render page 1 of ``pdf`` to a PNG with pdfplumber's renderer."""
    import pdfplumber

    png = Path(png)
    with pdfplumber.open(str(pdf)) as doc:
        doc.pages[0].to_image(resolution=resolution).save(str(png))
    return png


__all__ = [
    "Difference",
    "pdf_to_png",
    "render_comparison",
    "render_layout_view",
]
