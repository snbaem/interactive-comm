"""The result document written for each matched letter (shared by the CLI and web UI)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .dimensions import letter_vs_layout, measure
from .extract import LetterStructure
from .match import MatchResult
from .profile import LayoutProfile

APPROX_NOTE = (
    "Note: the sample PDF is a local approximation for checking fit. "
    "It is not AEM Forms Interactive Communications output."
)


def build_result(
    letter: LetterStructure,
    results: list[MatchResult],
    sample_pdf: Path | None,
    llm: dict[str, Any] | None = None,
    explanation: str | None = None,
    dimensions: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble the JSON result for one letter.

    Args:
        letter: Detected structure of the letter.
        results: Ranking from :func:`icsel.match.rank` (best first).
        sample_pdf: Path of the rendered sample PDF, if one was written.
        llm: What the optional AI step did (see :func:`icsel.pipeline.analyze`).
        explanation: Rule-based plain-language explanation (see :mod:`icsel.explain`).
        dimensions: Measurements of the best layout and the letter's offsets from it.
    """
    best = results[0]
    doc: dict[str, Any] = {
        "letter": letter.to_dict(),
        "best_layout": best.layout_id,
        "best_fits": best.fits,
        "ranking": [r.to_dict() for r in results],
        "sample_pdf": sample_pdf.as_posix() if sample_pdf else None,
        "fidelity": APPROX_NOTE,
        "llm": llm or {"enabled": False},
    }
    if explanation is not None:
        doc["explanation"] = explanation
    if dimensions is not None:
        doc["dimensions"] = dimensions
    return doc


def dimensions_doc(letter: LetterStructure, layout: LayoutProfile) -> dict[str, Any]:
    """Measurements of ``layout`` plus how far each letter block is from its zone (points)."""
    return {
        "layout": measure(layout).to_dict(),
        "letter_vs_layout": [d.to_dict() for d in letter_vs_layout(letter, layout)],
    }
