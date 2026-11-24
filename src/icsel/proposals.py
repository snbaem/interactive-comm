"""Draft layouts for letters no existing layout holds well (``icsel portfolio --proposals``).

Deterministic, no model:

1. Take the weak and no-fit letters of a portfolio run and read where their address,
   subject and date boxes start on page 1.
2. Cluster them. Two letters are close when they have the same page size and the same of
   those boxes, and no box is farther apart than ``cluster_distance`` points (scoring
   settings). Letters are taken in name order; each joins the first cluster in which it is
   close to *every* member, so a cluster cannot drift.
3. Each cluster of at least ``cluster_min_letters`` letters becomes a draft profile: median
   box positions and sizes, and the members' largest line counts. The draft is validated
   like any other profile, and how many members it would actually hold is reported.

The optional AI task ``propose`` adds a name and a rationale. It is sent geometry and
layout ids only (:func:`proposal_payload`); no letter text and no file names.
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median
from typing import Any

from .config import DEFAULT_CONFIG, ScoringConfig
from .dimensions import diff_layouts
from .extract import LetterStructure, extract
from .layoutview import pdf_to_png, render_layout_view
from .llm import LLMCallLimit, LLMError
from .match import CAPACITY_ROLES, score_layout
from .portfolio import FIT_GOOD, MAX_AI_ERRORS_IN_A_ROW, STATUS_OK, LetterRecord
from .profile import LayoutProfile, Zone, profile_from_dict

#: Boxes whose position defines a cluster.
SIGNATURE_ROLES = ("address", "subject", "date")
#: Boxes a draft profile can have.
DRAFT_ROLES = ("letterhead", "date", "address", "subject", "body", "footer")


@dataclass
class Box:
    """Geometry of one box of a letter (points); no text."""

    x: float
    y: float
    width: float
    height: float
    lines: int


@dataclass
class Geometry:
    """Where the page-1 boxes of one letter are."""

    record: LetterRecord
    page_width: float
    page_height: float
    boxes: dict[str, Box]


@dataclass
class Proposal:
    """A draft layout for one cluster of letters."""

    profile: LayoutProfile
    members: list[LetterRecord]
    nearest: str
    nearest_layout: LayoutProfile | None
    holds: int
    path: Path | None = None
    view: Path | None = None
    ai: dict[str, Any] = field(default_factory=dict)
    ai_error: str = ""


def read_geometry(record: LetterRecord, letter: LetterStructure) -> Geometry:
    """Keep only the geometry of ``letter``."""
    boxes = {}
    for role in DRAFT_ROLES:
        r = letter.region(role)
        if r is not None:
            boxes[role] = Box(r.x, r.y, r.width, r.height, r.line_count)
    return Geometry(record, letter.page_width, letter.page_height, boxes)


def distance(a: Geometry, b: Geometry) -> float:
    """Largest distance between the top-left corners of any signature box, in points."""
    if (a.page_width, a.page_height) != (b.page_width, b.page_height):
        return math.inf
    roles = [r for r in SIGNATURE_ROLES if r in a.boxes]
    if not roles or roles != [r for r in SIGNATURE_ROLES if r in b.boxes]:
        return math.inf
    return max(math.hypot(a.boxes[r].x - b.boxes[r].x, a.boxes[r].y - b.boxes[r].y) for r in roles)


def cluster(items: Sequence[Geometry], limit: float) -> list[list[Geometry]]:
    """Group letters whose boxes are all within ``limit`` points of each other."""
    clusters: list[list[Geometry]] = []
    for item in sorted(items, key=lambda g: g.record.name):
        for members in clusters:
            if all(distance(item, m) <= limit for m in members):
                members.append(item)
                break
        else:
            clusters.append([item])
    return clusters


def draft_profile(members: Sequence[Geometry], profile_id: str) -> LayoutProfile:
    """Median boxes of a cluster as a layout profile, validated against the profile schema."""
    page_w, page_h = members[0].page_width, members[0].page_height
    zones = []
    for role in DRAFT_ROLES:
        boxes = [m.boxes[role] for m in members if role in m.boxes]
        if len(boxes) * 2 <= len(members) and len(boxes) < len(members):
            continue  # at most half of the letters have it
        x = max(0.0, round(median(b.x for b in boxes), 1))
        y = max(0.0, round(median(b.y for b in boxes), 1))
        zones.append(
            Zone(
                role,
                x,
                y,
                round(min(median(b.width for b in boxes), page_w - x), 1),
                round(min(median(b.height for b in boxes), page_h - y), 1),
                max(b.lines for b in boxes) if role in CAPACITY_ROLES else None,
                required=role == "address" and len(boxes) == len(members),
            )
        )
    profile = LayoutProfile(
        profile_id,
        f"Proposed layout {profile_id.rsplit('-', 1)[-1]}",
        page_w,
        page_h,
        zones,
        description=f"Draft from {len(members)} letter(s); review before use.",
    )
    return profile_from_dict(profile.to_dict(), source=profile_id)


def nearest_layout(
    profile: LayoutProfile, layouts: Sequence[LayoutProfile]
) -> LayoutProfile | None:
    """Existing layout whose boxes start closest to the draft's (mean over shared roles)."""

    def mean(layout: LayoutProfile) -> float:
        diffs = [d for d in diff_layouts(profile, layout) if d.role in SIGNATURE_ROLES]
        return sum(math.hypot(d.dx, d.dy) for d in diffs) / len(diffs) if diffs else math.inf

    return min(layouts, key=lambda lay: (mean(lay), lay.id), default=None)


def propose(
    records: Sequence[LetterRecord],
    layouts: Sequence[LayoutProfile],
    config: ScoringConfig | None = None,
) -> tuple[list[Proposal], list[LetterRecord]]:
    """Cluster the weak and no-fit letters into draft layouts.

    Returns:
        The proposals, largest cluster first, and the letters that joined no cluster.
    """
    cfg = config or DEFAULT_CONFIG
    poor = [r for r in records if r.status == STATUS_OK and r.fit != FIT_GOOD]
    groups = cluster([read_geometry(r, extract(r.path)) for r in poor], cfg.cluster_distance)
    groups.sort(key=lambda g: (-len(g), g[0].record.name))
    taken = {lay.id for lay in layouts}
    proposals: list[Proposal] = []
    alone: list[LetterRecord] = []
    n = 0
    for members in groups:
        if len(members) < cfg.cluster_min_letters:
            alone.extend(m.record for m in members)
            continue
        n += 1
        while f"proposed-{n}" in taken:
            n += 1
        profile = draft_profile(members, f"proposed-{n}")
        holds = sum(score_layout(extract(m.record.path), profile, cfg).fits for m in members)
        near = nearest_layout(profile, layouts)
        proposals.append(
            Proposal(profile, [m.record for m in members], near.id if near else "", near, holds)
        )
    return proposals, sorted(alone, key=lambda r: r.name)


# ---------------------------------------------------------------- AI part
def _zones(layout: LayoutProfile) -> list[dict[str, Any]]:
    def inch(points: float) -> float:
        return round(points / 72.0, 2)

    return [
        {
            "role": z.role,
            "left_in": inch(z.x),
            "top_in": inch(z.y),
            "width_in": inch(z.width),
            "height_in": inch(z.height),
            "max_lines": z.max_lines,
        }
        for z in layout.zones
    ]


def proposal_payload(proposal: Proposal) -> dict[str, Any]:
    """What the ``propose`` task sends: geometry and layout ids, nothing from the letters."""
    payload: dict[str, Any] = {
        "draft": {"id": proposal.profile.id, "zones": _zones(proposal.profile)},
        "letters_in_cluster": len(proposal.members),
        "letters_the_draft_holds": proposal.holds,
    }
    if proposal.nearest_layout is not None:
        payload["nearest_existing_layout"] = {
            "id": proposal.nearest,
            "zones": _zones(proposal.nearest_layout),
        }
        payload["draft_minus_nearest_in"] = [
            {"role": d.role, "right": round(d.dx / 72, 2), "down": round(d.dy / 72, 2)}
            for d in diff_layouts(proposal.profile, proposal.nearest_layout)
        ]
    return payload


def describe_with_ai(proposals: Sequence[Proposal], assistant: Any) -> str:
    """Ask for a name and rationale per proposal; return why it stopped early, if it did."""
    failures = 0
    for p in proposals:
        try:
            text = assistant.propose_layout(proposal_payload(p))
            p.ai = text or {}
            failures = 0
        except LLMCallLimit as exc:
            return f"AI step stopped: {exc}."
        except LLMError as exc:
            p.ai_error = str(exc)
            failures += 1
            if failures >= MAX_AI_ERRORS_IN_A_ROW:
                return f"Layout naming stopped after {failures} failures in a row (last: {exc})."
    return ""


# ---------------------------------------------------------------- output
def write_proposals(proposals: Sequence[Proposal], folder: Path) -> None:
    """Write each draft profile and its dimensioned view (PDF and PNG) to ``folder``."""
    if proposals:
        folder.mkdir(parents=True, exist_ok=True)
    for p in proposals:
        if p.ai:
            p.profile.extra["ai_suggestion"] = p.ai
        p.path = folder / f"{p.profile.id}.json"
        p.path.write_text(json.dumps(p.profile.to_dict(), indent=2) + "\n", encoding="utf-8")
        pdf = render_layout_view(
            p.profile, folder / f"{p.profile.id}.pdf", reference=p.nearest_layout
        )
        p.view = pdf_to_png(pdf, pdf.with_suffix(".png"))


def proposals_markdown(
    proposals: Sequence[Proposal], alone: Sequence[LetterRecord], config: ScoringConfig | None
) -> list[str]:
    """The "Proposed layouts" section of ``portfolio.md``."""
    cfg = config or DEFAULT_CONFIG

    def names(records: Sequence[LetterRecord]) -> str:
        return ", ".join("`" + r.name.replace("`", "'") + "`" for r in records)

    out = ["", f"## Proposed layouts ({len(proposals)})", ""]
    out.append(
        "Drafts computed by rules from weak and no-fit letters whose address, subject and date "
        f"boxes are within {cfg.cluster_distance:g} pt of each other. Review before use."
    )
    for p in proposals:
        out += ["", f"### {p.profile.id}", ""]
        out.append(f"- Letters: {len(p.members)} ({names(p.members)})")
        out.append(f"- The draft passes the hard checks for {p.holds} of them.")
        if p.nearest:
            out.append(f"- Nearest existing layout: {p.nearest}")
        if p.path:
            out.append(f"- Draft profile: `proposals/{p.path.name}`")
        if p.ai:
            out.append(f"- Name (AI): {p.ai['name']}")
            out.append(f"- Rationale (AI): {p.ai['rationale']}")
        if p.ai_error:
            out.append(f"- AI step warning: {p.ai_error}")
        if p.view:
            out += ["", f"![{p.profile.id} with dimensions](proposals/{p.view.name})"]
    if alone:
        out += ["", "In no cluster: "
                + names(alone)]  # fmt: skip
    return out
