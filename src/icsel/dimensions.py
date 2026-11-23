"""Measurements of a layout, as plain data (no drawing).

Everything the dimensioned layout view shows is computed here first, in points,
and converted to inches, centimetres or points only when formatted. Distances are
measured from the top-left corner of the page, matching the profile coordinates.

Terms:
    margin: space between the page edge and the nearest zone on that side.
    gap: empty space between two neighbouring zones. A *vertical* gap is measured
        between the bottom of a zone and the top of the nearest zone below it that
        overlaps it horizontally; a *horizontal* gap between the right edge of a zone
        and the left edge of the nearest zone to its right that overlaps it vertically.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .extract import LetterStructure
from .profile import LayoutProfile, Zone
from .render import zone_capacity

UNITS = ("in", "cm", "pt")
_PT_PER_UNIT = {"in": 72.0, "cm": 72.0 / 2.54, "pt": 1.0}
_DECIMALS = {"in": 2, "cm": 2, "pt": 0}
#: Smallest distance (points) worth reporting as a difference.
DIFF_EPSILON = 0.5


def to_units(points: float, units: str = "in") -> float:
    """Convert a length in points to ``units`` (``in``, ``cm`` or ``pt``)."""
    if units not in _PT_PER_UNIT:
        raise ValueError(f"unknown units {units!r}; use one of {', '.join(UNITS)}")
    return points / _PT_PER_UNIT[units]


def format_length(points: float, units: str = "in") -> str:
    """Format a length for display: ``2.0"``, ``5.08 cm`` or ``144 pt``.

    Trailing zeros are trimmed but at least one decimal is kept for inches.
    """
    value = to_units(points, units)
    if units == "pt":
        return f"{round(value):d} pt"
    text = f"{value:.{_DECIMALS[units]}f}".rstrip("0")
    if text.endswith("."):
        text += "0"
    return f'{text}"' if units == "in" else f"{text} cm"


@dataclass
class Gap:
    """Empty space between two neighbouring zones."""

    direction: str  # "vertical" or "horizontal"
    first: str  # role of the upper / left zone
    second: str  # role of the lower / right zone
    points: float

    def to_dict(self) -> dict[str, Any]:
        """Plain-data view (points and inches)."""
        return {
            "direction": self.direction,
            "between": [self.first, self.second],
            "points": round(self.points, 2),
            "inches": round(self.points / 72.0, 3),
        }


@dataclass
class ZoneMeasure:
    """Position and size of one zone, all in points."""

    role: str
    top: float
    left: float
    width: float
    height: float
    right_margin: float
    bottom_margin: float
    capacity: int | None  # lines held (address, subject, date); None for other roles

    def to_dict(self) -> dict[str, Any]:
        """Plain-data view (points and inches)."""
        d: dict[str, Any] = {"role": self.role, "capacity_lines": self.capacity}
        for key in ("top", "left", "width", "height", "right_margin", "bottom_margin"):
            pts = getattr(self, key)
            d[f"{key}_pt"] = round(pts, 2)
            d[f"{key}_in"] = round(pts / 72.0, 3)
        return d


@dataclass
class Margins:
    """Page margins implied by the zones, in points."""

    top: float
    left: float
    right: float
    bottom: float

    def to_dict(self) -> dict[str, Any]:
        """Plain-data view (points and inches)."""
        out: dict[str, Any] = {}
        for key in ("top", "left", "right", "bottom"):
            pts = getattr(self, key)
            out[f"{key}_pt"] = round(pts, 2)
            out[f"{key}_in"] = round(pts / 72.0, 3)
        return out


@dataclass
class Measurements:
    """All measurements of one layout."""

    layout_id: str
    page_width: float
    page_height: float
    margins: Margins
    zones: list[ZoneMeasure]
    gaps: list[Gap] = field(default_factory=list)

    def zone(self, role: str) -> ZoneMeasure | None:
        """Measurements of the first zone with ``role``."""
        return next((z for z in self.zones if z.role == role), None)

    def to_dict(self) -> dict[str, Any]:
        """Plain-data view for JSON output."""
        return {
            "layout_id": self.layout_id,
            "page_pt": [self.page_width, self.page_height],
            "margins": self.margins.to_dict(),
            "zones": [z.to_dict() for z in self.zones],
            "gaps": [g.to_dict() for g in self.gaps],
        }


def _h_overlap(a: Zone, b: Zone) -> bool:
    return min(a.right, b.right) - max(a.x, b.x) > 0


def _v_overlap(a: Zone, b: Zone) -> bool:
    return min(a.bottom, b.bottom) - max(a.y, b.y) > 0


def find_gaps(zones: list[Zone]) -> list[Gap]:
    """Gaps between each zone and its nearest neighbour below and to the right.

    Zones that overlap each other have no gap between them and are skipped.
    """
    gaps: list[Gap] = []
    for a in zones:
        below = [b for b in zones if b is not a and _h_overlap(a, b) and b.y >= a.bottom - 1e-9]
        if below:
            nearest = min(below, key=lambda b: (b.y, b.x))
            gaps.append(Gap("vertical", a.role, nearest.role, nearest.y - a.bottom))
        right = [b for b in zones if b is not a and _v_overlap(a, b) and b.x >= a.right - 1e-9]
        if right:
            nearest = min(right, key=lambda b: (b.x, b.y))
            gaps.append(Gap("horizontal", a.role, nearest.role, nearest.x - a.right))
    return gaps


def measure(layout: LayoutProfile) -> Measurements:
    """Measure margins, zone positions, line capacities and gaps of ``layout``."""
    zones = layout.zones
    margins = Margins(
        top=min(z.y for z in zones),
        left=min(z.x for z in zones),
        right=layout.page_width - max(z.right for z in zones),
        bottom=layout.page_height - max(z.bottom for z in zones),
    )
    measured = [
        ZoneMeasure(
            role=z.role,
            top=z.y,
            left=z.x,
            width=z.width,
            height=z.height,
            right_margin=layout.page_width - z.right,
            bottom_margin=layout.page_height - z.bottom,
            capacity=zone_capacity(z) if z.role in ("address", "subject", "date") else None,
        )
        for z in zones
    ]
    return Measurements(
        layout.id, layout.page_width, layout.page_height, margins, measured, find_gaps(zones)
    )


@dataclass
class Difference:
    """How a zone (or a letter block) sits relative to a reference, in points.

    Positive ``dy`` means lower on the page, positive ``dx`` further right.
    """

    role: str
    dx: float
    dy: float
    dwidth: float
    dheight: float
    subject: str = "layout"  # "layout" or "letter" - what is being compared to the reference

    @property
    def is_zero(self) -> bool:
        """True if nothing differs by more than :data:`DIFF_EPSILON`."""
        return all(abs(v) < DIFF_EPSILON for v in (self.dx, self.dy, self.dwidth, self.dheight))

    def describe(self, units: str = "in", reference: str = "reference") -> str:
        """Plain-language summary, e.g. ``subject 0.3" lower, 1.0" narrower than classic-left``."""
        parts: list[str] = []
        for value, plus, minus in (
            (self.dy, "lower", "higher"),
            (self.dx, "further right", "further left"),
            (self.dwidth, "wider", "narrower"),
            (self.dheight, "taller", "shorter"),
        ):
            if abs(value) >= DIFF_EPSILON:
                parts.append(f"{format_length(abs(value), units)} {plus if value > 0 else minus}")
        if not parts:
            return f"{self.role}: same as {reference}"
        return f"{self.role} {', '.join(parts)} than {reference}"

    def to_dict(self) -> dict[str, Any]:
        """Plain-data view (points)."""
        return {
            "role": self.role,
            "dx_pt": round(self.dx, 2),
            "dy_pt": round(self.dy, 2),
            "dwidth_pt": round(self.dwidth, 2),
            "dheight_pt": round(self.dheight, 2),
        }


def diff_layouts(layout: LayoutProfile, reference: LayoutProfile) -> list[Difference]:
    """Per-role differences of ``layout`` against ``reference`` (zones present in both).

    Roles only in one of the two are not listed here; see :func:`missing_roles`.
    """
    out = []
    for z in layout.zones:
        ref = reference.zone(z.role)
        if ref is None:
            continue
        out.append(
            Difference(z.role, z.x - ref.x, z.y - ref.y, z.width - ref.width, z.height - ref.height)
        )
    return out


def missing_roles(layout: LayoutProfile, reference: LayoutProfile) -> tuple[list[str], list[str]]:
    """Roles only in ``layout`` and roles only in ``reference``."""
    mine = {z.role for z in layout.zones}
    theirs = {z.role for z in reference.zones}
    return sorted(mine - theirs), sorted(theirs - mine)


def letter_vs_layout(letter: LetterStructure, layout: LayoutProfile) -> list[Difference]:
    """Where each letter block sits relative to the matching zone of ``layout``.

    A subject block 22 pt lower than the subject zone gives ``dy = 22``.
    """
    out = []
    for z in layout.zones:
        block = letter.region(z.role)
        if block is None:
            continue
        out.append(
            Difference(
                z.role,
                block.x - z.x,
                block.y - z.y,
                block.width - z.width,
                block.height - z.height,
                subject="letter",
            )
        )
    return out
