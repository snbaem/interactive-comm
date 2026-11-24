"""Score how well a letter's structure fits each layout profile.

Scoring, per role ``r`` in :data:`ROLE_WEIGHTS`::

    closeness_r = max(0, 1 - distance(letter top-left, zone top-left) / POSITION_SCALE)
    overlap_r   = area(letter block inside zone) / area(letter block)
    score_r     = 0.6 * closeness_r + 0.4 * overlap_r

with special cases: letter has the role but the layout has no zone -> 0 (the
layout has nowhere to put it); layout has the zone but the letter lacks the
role -> not scored (an empty optional box is not a misfit; required zones are a
hard check instead). The total is the weighted mean of the scored roles, in 0..1.

Hard checks (any failure means the layout does not fit, whatever the score):

* line capacity: a block has more lines than its zone's ``max_lines``;
* width: a block is wider than its zone by more than :data:`WIDTH_TOLERANCE`;
* kind: a statement only fits a layout made for statements, and a letter only one for
  letters; layouts of the other kind are ranked last;
* required: a zone marked ``required`` has no matching letter block.

Near-ties (:func:`resolve_ties`): when several layouts fit and score within
:data:`TIE_MARGIN` of the best one, the choice is made by tightest fit instead
of by the last decimal of the score: fewest roles without a zone, then fewest
unused lines in the address, subject and date boxes, then the higher score,
then the layout id. :func:`rank` itself is unchanged.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from .config import DEFAULT_CONFIG, ScoringConfig
from .extract import Block, LetterStructure
from .profile import LayoutProfile, Zone

# Defaults, kept as module constants for callers that import them. The matcher itself
# reads a ScoringConfig (see config.py and data/scoring.json) and falls back to these.
ROLE_WEIGHTS: dict[str, float] = DEFAULT_CONFIG.role_weights
POSITION_SCALE = DEFAULT_CONFIG.position_scale  # a block this far (pt) from its zone: 0 closeness
CLOSENESS_SHARE = DEFAULT_CONFIG.closeness_share
WIDTH_TOLERANCE = DEFAULT_CONFIG.width_tolerance  # points
#: Below this best score the UI suggests previewing other layouts.
LOW_FIT_THRESHOLD = DEFAULT_CONFIG.low_fit_threshold
#: Fitting layouts within this score of the best one count as a near-tie.
TIE_MARGIN = DEFAULT_CONFIG.tie_margin
#: Roles whose box capacity (``max_lines``) counts towards "unused capacity".
CAPACITY_ROLES = ("address", "subject", "date")


@dataclass
class RoleScore:
    """Structured score for one role in one layout."""

    role: str
    score: float | None  # None = not scored (the letter has no block for this role)
    dx: float | None = None
    dy: float | None = None
    closeness: float | None = None
    overlap: float | None = None
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Plain-data view for JSON output."""

        def r(v: float | None) -> float | None:
            return None if v is None else round(v, 3)

        return {
            "role": self.role,
            "score": r(self.score),
            "dx": r(self.dx),
            "dy": r(self.dy),
            "closeness": r(self.closeness),
            "overlap": r(self.overlap),
            "note": self.note,
        }


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
        return {
            "layout_id": self.layout_id,
            "layout_name": self.layout_name,
            "score": round(self.score, 4),
            "fits": self.fits,
            "failures": self.failures,
            "roles": [r.to_dict() for r in self.roles],
        }


def _overlap_fraction(block: Block, zone: Zone) -> float:
    ix = max(0.0, min(block.right, zone.right) - max(block.x, zone.x))
    iy = max(0.0, min(block.bottom, zone.bottom) - max(block.y, zone.y))
    area = max(block.width, 1.0) * max(block.height, 1.0)
    return min(1.0, (ix * iy) / area)


def _hard_checks(role: str, block: Block, zone: Zone, tolerance: float) -> list[str]:
    failures = []
    if zone.max_lines is not None and block.line_count > zone.max_lines:
        failures.append(f"{role} has {block.line_count} lines; layout allows {zone.max_lines}")
    if block.width > zone.width + tolerance:
        failures.append(f"{role} is {block.width:.0f} pt wide; zone is {zone.width:.0f} pt")
    return failures


def score_layout(
    letter: LetterStructure, layout: LayoutProfile, config: ScoringConfig | None = None
) -> MatchResult:
    """Score ``letter`` against one ``layout`` (``config`` defaults to the bundled settings)."""
    cfg = config or DEFAULT_CONFIG
    roles: list[RoleScore] = []
    failures: list[str] = []
    if letter.kind != layout.kind:
        failures.append(f"this is a {letter.kind}; the layout is for {layout.kind}s")
    for role in cfg.role_weights:
        zone = layout.zone(role)
        block = letter.region(role)
        if block is None and zone is None:
            continue
        if block is None:
            assert zone is not None
            if zone.required:
                failures.append(f"layout requires a {role} block; none found")
            roles.append(RoleScore(role, None, note="letter has none; not scored"))
            continue
        if zone is None:
            roles.append(RoleScore(role, 0.0, note="layout has no such zone"))
            continue
        dx, dy = block.x - zone.x, block.y - zone.y
        closeness = max(0.0, 1.0 - math.hypot(dx, dy) / cfg.position_scale)
        overlap = _overlap_fraction(block, zone)
        if role != "body":  # body length varies by letter; capacity is checked by page flow
            failures.extend(_hard_checks(role, block, zone, cfg.width_tolerance))
        s = cfg.closeness_share * closeness + (1 - cfg.closeness_share) * overlap
        roles.append(RoleScore(role, s, dx, dy, closeness, overlap))
    scored = [(cfg.role_weights[r.role], r.score) for r in roles if r.score is not None]
    total_w = sum(w for w, _ in scored)
    total = sum(w * sc for w, sc in scored) / total_w if total_w else 0.0
    return MatchResult(layout.id, layout.name, total, not failures, failures, roles)


def rank(
    letter: LetterStructure, layouts: list[LayoutProfile], config: ScoringConfig | None = None
) -> list[MatchResult]:
    """Score every layout; fitting layouts first, then by descending score."""
    results = [score_layout(letter, lay, config) for lay in layouts]
    other_kind = {lay.id for lay in layouts if lay.kind != letter.kind}
    return sorted(
        results, key=lambda r: (not r.fits, r.layout_id in other_kind, -r.score, r.layout_id)
    )


@dataclass
class TieBreak:
    """Outcome of :func:`resolve_ties` for one letter.

    Attributes:
        results: The ranking, with the tie-break winner moved to the front. Equal to the
            input ranking when there was no near-tie.
        tied: Ids of the fitting layouts within the margin of the best score, best
            score first. Empty unless there are at least two.
        applied: True if the winner differs from the highest-scoring layout.
        note: Plain-language reason, empty when ``tied`` is empty.
    """

    results: list[MatchResult]
    tied: list[str] = field(default_factory=list)
    applied: bool = False
    note: str = ""

    @property
    def ambiguous(self) -> bool:
        """True if two or more fitting layouts are within the margin of the best score."""
        return len(self.tied) > 1


def tightness(
    letter: LetterStructure, layout: LayoutProfile, result: MatchResult
) -> tuple[int, int]:
    """How loosely ``layout`` holds ``letter``: ``(roles without a zone, unused lines)``.

    Lower is tighter. Unused lines are the ``max_lines`` capacity left over in the
    address, subject and date boxes; an empty box counts as fully unused.
    """
    gaps = sum(1 for r in result.roles if r.note == "layout has no such zone")
    unused = 0
    for role in CAPACITY_ROLES:
        zone = layout.zone(role)
        if zone is None or zone.max_lines is None:
            continue
        block = letter.region(role)
        used = block.line_count if block is not None else 0
        unused += max(0, zone.max_lines - used)
    return gaps, unused


def resolve_ties(
    letter: LetterStructure,
    layouts: list[LayoutProfile],
    results: list[MatchResult],
    margin: float = TIE_MARGIN,
) -> TieBreak:
    """Break near-ties in a ranking by tightest fit (see the module docstring).

    Args:
        letter: The letter that was scored.
        layouts: The layout profiles used to produce ``results``.
        results: Output of :func:`rank`.
        margin: Score distance from the best fitting layout that still counts as a tie.
    """
    fitting = [r for r in results if r.fits]
    if not fitting:
        return TieBreak(results)
    top = fitting[0].score
    tied = [r for r in fitting if top - r.score <= margin]
    if len(tied) < 2:
        return TieBreak(results)
    by_id = {lay.id: lay for lay in layouts}
    keyed = {r.layout_id: tightness(letter, by_id[r.layout_id], r) for r in tied}
    winner = min(tied, key=lambda r: (*keyed[r.layout_id], -r.score, r.layout_id))
    ordered = [winner] + [r for r in results if r is not winner]
    applied = winner is not tied[0]
    parts = ", ".join(
        f"{r.layout_id} {r.score:.3f} ({keyed[r.layout_id][0]} gaps, "
        f"{keyed[r.layout_id][1]} unused lines)"
        for r in tied
    )
    note = f"near-tie: {parts}; tightest fit is {winner.layout_id}"
    return TieBreak(ordered, [r.layout_id for r in tied], applied, note)
