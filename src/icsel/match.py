"""Score how well a letter's structure fits each layout profile.

Scoring, per role ``r`` in :data:`ROLE_WEIGHTS`::

    closeness_r = max(0, 1 - distance(letter top-left, zone top-left) / POSITION_SCALE)
    overlap_r   = area(letter block inside zone) / area(letter block)
    score_r     = 0.6 * closeness_r + 0.4 * overlap_r

with special cases: letter has the role but the layout has no zone -> 0;
layout has the zone but the letter lacks the role -> :data:`MISSING_ROLE_SCORE`.
The total is the weighted mean of ``score_r``, in 0..1.

Hard checks (any failure means the layout does not fit, whatever the score):

* line capacity: a block has more lines than its zone's ``max_lines``;
* width: a block is wider than its zone by more than :data:`WIDTH_TOLERANCE`;
* required: a zone marked ``required`` has no matching letter block.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from .extract import Block, LetterStructure
from .profile import LayoutProfile, Zone

ROLE_WEIGHTS: dict[str, float] = {"address": 3.0, "subject": 3.0, "date": 1.0, "body": 2.0}
POSITION_SCALE = 72.0  # points; a block one inch away from its zone gets zero closeness
CLOSENESS_SHARE = 0.6
MISSING_ROLE_SCORE = 0.5
WIDTH_TOLERANCE = 18.0  # points


@dataclass
class RoleScore:
    """Structured score for one role in one layout."""

    role: str
    score: float
    dx: float | None = None
    dy: float | None = None
    closeness: float | None = None
    overlap: float | None = None
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Plain-data view for JSON output."""
        def r(v: float | None) -> float | None:
            return None if v is None else round(v, 3)
        return {"role": self.role, "score": r(self.score), "dx": r(self.dx), "dy": r(self.dy),
                "closeness": r(self.closeness), "overlap": r(self.overlap), "note": self.note}


@dataclass
class MatchResult:
    """How one letter fits one layout."""

    layout_id: str
    layout_name: str
    score: float
    fits: bool
    failures: list[str] = field(default_factory=list)
    roles: list[RoleScore] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Plain-data view for JSON output."""
        return {"layout_id": self.layout_id, "layout_name": self.layout_name,
                "score": round(self.score, 4), "fits": self.fits, "failures": self.failures,
                "roles": [r.to_dict() for r in self.roles]}


def _overlap_fraction(block: Block, zone: Zone) -> float:
    ix = max(0.0, min(block.right, zone.right) - max(block.x, zone.x))
    iy = max(0.0, min(block.bottom, zone.bottom) - max(block.y, zone.y))
    area = max(block.width, 1.0) * max(block.height, 1.0)
    return min(1.0, (ix * iy) / area)


def _hard_checks(role: str, block: Block, zone: Zone) -> list[str]:
    failures = []
    if zone.max_lines is not None and block.line_count > zone.max_lines:
        failures.append(f"{role} has {block.line_count} lines; layout allows {zone.max_lines}")
    if block.width > zone.width + WIDTH_TOLERANCE:
        failures.append(f"{role} is {block.width:.0f} pt wide; zone is {zone.width:.0f} pt")
    return failures


def score_layout(letter: LetterStructure, layout: LayoutProfile) -> MatchResult:
    """Score ``letter`` against one ``layout``."""
    roles: list[RoleScore] = []
    failures: list[str] = []
    for role in ROLE_WEIGHTS:
        zone = layout.zone(role)
        block = letter.region(role)
        if block is None and zone is None:
            continue
        if block is None:
            assert zone is not None
            if zone.required:
                failures.append(f"layout requires a {role} block; none found")
            roles.append(RoleScore(role, MISSING_ROLE_SCORE, note="letter has no such block"))
            continue
        if zone is None:
            roles.append(RoleScore(role, 0.0, note="layout has no such zone"))
            continue
        dx, dy = block.x - zone.x, block.y - zone.y
        closeness = max(0.0, 1.0 - math.hypot(dx, dy) / POSITION_SCALE)
        overlap = _overlap_fraction(block, zone)
        if role != "body":  # body length varies by letter; capacity is checked by page flow
            failures.extend(_hard_checks(role, block, zone))
        s = CLOSENESS_SHARE * closeness + (1 - CLOSENESS_SHARE) * overlap
        roles.append(RoleScore(role, s, dx, dy, closeness, overlap))
    total_w = sum(ROLE_WEIGHTS[r.role] for r in roles) or 1.0
    total = sum(ROLE_WEIGHTS[r.role] * r.score for r in roles) / total_w
    return MatchResult(layout.id, layout.name, total, not failures, failures, roles)


def rank(letter: LetterStructure, layouts: list[LayoutProfile]) -> list[MatchResult]:
    """Score every layout; fitting layouts first, then by descending score."""
    results = [score_layout(letter, lay) for lay in layouts]
    return sorted(results, key=lambda r: (not r.fits, -r.score, r.layout_id))
