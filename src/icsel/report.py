"""The result document written for each matched letter (shared by the CLI and web UI)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .extract import LetterStructure
from .match import MatchResult

APPROX_NOTE = (
    "Note: the sample PDF is a local approximation for checking fit. "
    "It is not AEM Forms Interactive Communications output."
)


def build_result(
    letter: LetterStructure,
    results: list[MatchResult],
    sample_pdf: Path | None,
    llm: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble the JSON result for one letter.

    Args:
        letter: Detected structure of the letter.
        results: Ranking from :func:`icsel.match.rank` (best first).
        sample_pdf: Path of the rendered sample PDF, if one was written.
        llm: What the optional AI step did (see :func:`icsel.pipeline.analyze`).
    """
    best = results[0]
    return {
        "letter": letter.to_dict(),
        "best_layout": best.layout_id,
        "best_fits": best.fits,
        "ranking": [r.to_dict() for r in results],
        "sample_pdf": sample_pdf.as_posix() if sample_pdf else None,
        "fidelity": APPROX_NOTE,
        "llm": llm or {"enabled": False},
    }
