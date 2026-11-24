"""Plain-language explanation of a ranking, written by rules (no model).

The text only restates numbers the matcher already computed: the best layout's score,
which boxes line up and which are off (in inches), and why the runner-up lost.
"""

from __future__ import annotations

from .config import DEFAULT_CONFIG, ScoringConfig
from .dimensions import format_length
from .extract import LetterStructure
from .match import MatchResult
from .profile import LayoutProfile

#: A box closer than this (points) to its zone counts as lined up.
ALIGNED_WITHIN = 9.0


def _role_phrase(r: object, units: str) -> str | None:
    """One clause about a scored role, or ``None`` when it was not scored."""
    score = getattr(r, "score", None)
    if score is None:
        return None
    role = r.role
    note = getattr(r, "note", "")
    if note == "layout has no such zone":
        return f"the {role} has no box in this layout"
    dx, dy = getattr(r, "dx", 0.0) or 0.0, getattr(r, "dy", 0.0) or 0.0
    if max(abs(dx), abs(dy)) <= ALIGNED_WITHIN:
        return f"the {role} lines up with its box"
    parts = []
    if abs(dy) > ALIGNED_WITHIN:
        parts.append(f"{format_length(abs(dy), units)} {'lower' if dy > 0 else 'higher'}")
    if abs(dx) > ALIGNED_WITHIN:
        parts.append(f"{format_length(abs(dx), units)} {'right' if dx > 0 else 'left'}")
    return f"the {role} sits {' and '.join(parts)} than its box"


def explain_ranking(
    letter: LetterStructure,
    layouts: list[LayoutProfile],
    results: list[MatchResult],
    config: ScoringConfig | None = None,
    units: str = "in",
) -> str:
    """Explain why ``results[0]`` was chosen, in a few sentences."""
    cfg = config or DEFAULT_CONFIG
    best = results[0]
    name = {lay.id: lay.name for lay in layouts}.get(best.layout_id, best.layout_id)
    out: list[str] = []
    if not best.fits:
        out.append(
            f"No layout passes every hard check; {name} ({best.layout_id}) scores highest at "
            f"{best.score:.2f}, but it does not fit: {'; '.join(best.failures)}."
        )
    else:
        out.append(f"{name} ({best.layout_id}) fits best with a score of {best.score:.2f}.")
    clauses = [p for p in (_role_phrase(r, units) for r in best.roles) if p]
    if clauses:
        out.append("Within it, " + "; ".join(clauses) + ".")
    if best.fits and best.score < cfg.low_fit_threshold:
        out.append(
            f"This is a weak fit (below {cfg.low_fit_threshold:.2f}); check the preview "
            "before relying on it."
        )
    others = results[1:]
    if others:
        nxt = others[0]
        if not nxt.fits:
            reason = nxt.failures[0] if nxt.failures else "it fails a hard check"
            out.append(f"{nxt.layout_id} was ruled out: {reason}.")
        else:
            out.append(
                f"Runner-up {nxt.layout_id} scored {nxt.score:.2f}, "
                f"{best.score - nxt.score:.2f} lower."
            )
    return " ".join(out)
