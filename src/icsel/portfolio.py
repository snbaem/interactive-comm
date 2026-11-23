"""Portfolio mode: match a whole folder of letters and summarize the set.

``icsel portfolio`` answers "how do our letters spread across our layouts?":

* the best layout per letter, with its score and the runner-up;
* an **ambiguous** flag where two fitting layouts score within a margin of each
  other (the choice is then made by tightest fit, see
  :func:`icsel.match.resolve_ties`);
* the distribution of letters per layout;
* letters that fit **no** layout (candidates for a new layout) and **weak** fits;
* **near-duplicate** letters, grouped (candidates for shared content);
* PDFs that could not be read, or have no text layer, listed instead of aborting
  the run.

Memory stays flat in the number of PDFs: each letter is read, reduced to one small
:class:`LetterRecord` (a handful of numbers plus a set of hashed word shingles of
its body text) and released.

Near-duplicate measure: the Jaccard similarity of the sets of 5-word shingles of
the letter's body paragraphs, after lower-casing and replacing every run of digits
with ``#`` (so account numbers and amounts do not hide a copy). Pairs at or above
the threshold (default 0.8) are linked; linked letters form a group. Comparisons
are exact and pairwise with a size pre-filter, which is comfortable for a few
thousand letters. Letters with fewer than 12 body words are not compared.

The optional AI step is economical here. See :func:`plan_ai` and :func:`run_ai`:
it is only asked about letters that have blocks the rules could not label, and
about letters that are ambiguous, weak or fit no layout, never about every letter.
"""

from __future__ import annotations

import csv
import re
import zlib
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import ScoringConfig
from .extract import ExtractionError, LetterStructure, NoTextError, extract
from .llm import Assistant, LLMError, apply_suggestions, fit_facts
from .match import LOW_FIT_THRESHOLD, TIE_MARGIN, TieBreak, rank, resolve_ties
from .profile import LayoutProfile

#: Default similarity at or above which two letters count as near-duplicates.
DUPLICATE_THRESHOLD = 0.8
#: Words per shingle for the duplicate measure.
SHINGLE_WORDS = 5
#: Letters with fewer body words than this are not compared for duplicates.
MIN_BODY_WORDS = 12
#: The AI step stops after this many failed letters in a row (bad key, no network, ...).
MAX_AI_ERRORS_IN_A_ROW = 3

STATUS_OK = "ok"
STATUS_NO_TEXT = "no_text"
STATUS_ERROR = "error"

FIT_GOOD = "good"
FIT_WEAK = "weak"
FIT_NONE = "none"

CSV_COLUMNS = [
    "file",
    "status",
    "pages",
    "best_layout",
    "score",
    "fit",
    "ambiguous",
    "runner_up",
    "score_gap",
    "tie_break",
    "closest_layout",
    "failures",
    "unknown_blocks",
    "duplicate_group",
    "ai_role_changes",
    "note",
]


@dataclass
class LetterRecord:
    """Everything portfolio mode keeps about one PDF (small by design)."""

    path: Path
    name: str
    status: str = STATUS_OK
    note: str = ""
    pages: int = 0
    best_layout: str = ""
    score: float = 0.0
    fit: str = ""
    ambiguous: bool = False
    runner_up: str = ""
    score_gap: float | None = None
    tie_note: str = ""
    closest_layout: str = ""
    failures: list[str] = field(default_factory=list)
    unknown_blocks: int = 0
    fingerprint: frozenset[int] | None = None
    duplicate_group: str = ""
    ai_changes: list[dict[str, Any]] = field(default_factory=list)
    ai_explanation: str | None = None
    ai_error: str = ""


@dataclass
class DuplicateGroup:
    """Letters whose body text is (nearly) the same."""

    id: str
    members: list[LetterRecord]
    max_similarity: float


@dataclass
class Settings:
    """Thresholds used for one portfolio run (echoed in the report)."""

    min_score: float = LOW_FIT_THRESHOLD
    margin: float = TIE_MARGIN
    duplicate_threshold: float = DUPLICATE_THRESHOLD
    scoring: ScoringConfig | None = None  # None = bundled defaults


# ---------------------------------------------------------------- scanning
def _display_name(path: Path) -> str:
    try:
        return path.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z#]+", re.sub(r"\d+", "#", text.lower()))


def body_fingerprint(letter: LetterStructure) -> frozenset[int] | None:
    """Hashed 5-word shingles of the letter's body paragraphs, or None if too short.

    Salutation, closing and signature are left out (they carry names); digits are
    normalized. See the module docstring for the similarity measure.
    """
    text = " ".join(" ".join(b.lines) for b in letter.blocks if b.role == "body")
    words = _words(text)
    if len(words) < MIN_BODY_WORDS:
        return None
    k = SHINGLE_WORDS
    return frozenset(
        zlib.crc32(" ".join(words[i : i + k]).encode("utf-8")) for i in range(len(words) - k + 1)
    )


def assess(
    record: LetterRecord,
    letter: LetterStructure,
    layouts: list[LayoutProfile],
    settings: Settings,
) -> TieBreak:
    """Rank ``layouts`` for ``letter`` and fill the matching fields of ``record``.

    Returns the tie-break outcome (its ``results`` start with the chosen layout).
    """
    tb = resolve_ties(letter, layouts, rank(letter, layouts, settings.scoring), settings.margin)
    best = tb.results[0]
    record.pages = letter.page_count
    record.unknown_blocks = sum(1 for b in letter.blocks if b.role == "unknown")
    record.score = round(best.score, 3)
    record.ambiguous = tb.ambiguous
    record.tie_note = tb.note
    runner = next((r for r in tb.results[1:] if r.fits), None)
    record.runner_up = runner.layout_id if runner else ""
    record.score_gap = round(abs(best.score - runner.score), 3) if runner else None
    if best.fits:
        record.best_layout = best.layout_id
        record.closest_layout = ""
        record.failures = []
        record.fit = FIT_WEAK if best.score < settings.min_score else FIT_GOOD
    else:
        record.best_layout = ""
        record.closest_layout = best.layout_id
        record.failures = list(best.failures)
        record.fit = FIT_NONE
    return tb


def scan_letter(
    path: Path, layouts: list[LayoutProfile], settings: Settings
) -> tuple[LetterRecord, LetterStructure | None]:
    """Read and match one PDF. Problems become a record with a status, never an exception."""
    record = LetterRecord(path, _display_name(path))
    try:
        letter = extract(path)
    except NoTextError:
        record.status = STATUS_NO_TEXT
        record.note = "no extractable text (scanned or image-only); OCR is out of scope"
        return record, None
    except ExtractionError as exc:
        record.status = STATUS_ERROR
        record.note = str(exc).removeprefix(f"{path}: ")
        return record, None
    except Exception as exc:  # a broken page can raise anything; one bad file must not end the run
        record.status = STATUS_ERROR
        record.note = f"{type(exc).__name__}: {str(exc)[:120]}"
        return record, None
    assess(record, letter, layouts, settings)
    record.fingerprint = body_fingerprint(letter)
    return record, letter


def scan(
    paths: Sequence[Path],
    layouts: list[LayoutProfile],
    settings: Settings | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> list[LetterRecord]:
    """Match every PDF in ``paths``, one at a time, keeping only the small records."""
    settings = settings or Settings()
    records: list[LetterRecord] = []
    for i, path in enumerate(paths, start=1):
        record, _ = scan_letter(path, layouts, settings)
        records.append(record)
        if progress:
            progress(i, len(paths))
    return records


# ---------------------------------------------------------------- duplicates
def _jaccard(a: frozenset[int], b: frozenset[int]) -> float:
    inter = len(a & b)
    return inter / (len(a) + len(b) - inter)


def find_duplicate_groups(
    records: Iterable[LetterRecord], threshold: float = DUPLICATE_THRESHOLD
) -> list[DuplicateGroup]:
    """Group letters whose body similarity is at or above ``threshold``.

    Sets ``duplicate_group`` on every member (``D1``, ``D2``, ... largest group first).
    """
    items = sorted(
        (r for r in records if r.fingerprint), key=lambda r: (len(r.fingerprint or ()), r.name)
    )
    parent = list(range(len(items)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    best: dict[int, float] = {}
    for i, a in enumerate(items):
        fa = a.fingerprint or frozenset()
        for j in range(i + 1, len(items)):
            fb = items[j].fingerprint or frozenset()
            if len(fa) / len(fb) < threshold:
                break  # sorted by size: every later letter is even larger
            sim = _jaccard(fa, fb)
            if sim >= threshold:
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[rj] = ri
                best[i] = max(best.get(i, 0.0), sim)
                best[j] = max(best.get(j, 0.0), sim)
    clusters: dict[int, list[int]] = {}
    for i in range(len(items)):
        clusters.setdefault(find(i), []).append(i)
    multi = [c for c in clusters.values() if len(c) > 1]
    multi.sort(key=lambda c: (-len(c), items[c[0]].name))
    groups = []
    for n, members in enumerate(multi, start=1):
        gid = f"D{n}"
        recs = sorted((items[i] for i in members), key=lambda r: r.name)
        for r in recs:
            r.duplicate_group = gid
        groups.append(DuplicateGroup(gid, recs, round(max(best[i] for i in members), 2)))
    return groups


# ---------------------------------------------------------------- AI step
@dataclass
class AiPlan:
    """Which letters the AI step would be asked about, before any call is made."""

    classify: list[LetterRecord]
    explain: list[LetterRecord]

    @property
    def calls(self) -> int:
        """Upper bound on model calls: one per labeling job plus one per explanation job."""
        return len(self.classify) + len(self.explain)


def plan_ai(records: Iterable[LetterRecord], assistant: Assistant) -> AiPlan:
    """Decide which letters the AI step is worth running on.

    * **Labeling** only for letters with at least one block the rules left ``unknown``.
    * **Explaining** only for letters that are ambiguous, weak or fit no layout.

    Letters that fit one layout cleanly make no model call at all. The explain count is
    an upper bound, because labeling can resolve a weak fit before it is explained.
    """
    tasks = set(getattr(assistant, "tasks", ()))
    ok = [r for r in records if r.status == STATUS_OK]
    classify = [r for r in ok if "classify" in tasks and r.unknown_blocks > 0]
    explain = [r for r in ok if "explain" in tasks and (r.ambiguous or r.fit != FIT_GOOD)]
    return AiPlan(classify, explain)


def run_ai(
    plan: AiPlan,
    layouts: list[LayoutProfile],
    assistant: Assistant,
    settings: Settings | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> str:
    """Run the planned AI jobs; update the records in place.

    Labeling re-ranks the letter (rule-based labels are never overridden, see
    :func:`icsel.llm.apply_suggestions`); explaining stores a short paragraph built from
    scores only. A failing letter records ``ai_error`` and the run continues, except after
    :data:`MAX_AI_ERRORS_IN_A_ROW` failures in a row.

    Returns:
        An empty string, or a message saying why the run stopped early.
    """
    settings = settings or Settings()
    classify_ids = {id(r) for r in plan.classify}
    explain_ids = {id(r) for r in plan.explain}
    ordered: list[LetterRecord] = []
    seen: set[int] = set()
    for r in [*plan.classify, *plan.explain]:
        if id(r) not in seen:
            seen.add(id(r))
            ordered.append(r)
    failures_in_a_row = 0
    for i, record in enumerate(ordered, start=1):
        if progress:
            progress(i, len(ordered))
        try:
            letter = extract(record.path)
        except ExtractionError as exc:
            record.ai_error = f"could not re-read the letter: {exc}"
            continue
        changes = []
        try:
            if id(record) in classify_ids:
                suggestions = assistant.classify_unknown(letter)
                changes = apply_suggestions(letter, suggestions, assistant.min_confidence)
                record.ai_changes = [c.to_dict() for c in changes]
            tb = assess(record, letter, layouts, settings) if changes else None
            if id(record) in explain_ids and (record.ambiguous or record.fit != FIT_GOOD):
                if tb is None:
                    tb = resolve_ties(
                        letter, layouts, rank(letter, layouts, settings.scoring), settings.margin
                    )
                record.ai_explanation = assistant.explain_fit(
                    fit_facts(letter, tb.results, changes)
                )
            failures_in_a_row = 0
        except LLMError as exc:
            record.ai_error = str(exc)
            failures_in_a_row += 1
            if failures_in_a_row >= MAX_AI_ERRORS_IN_A_ROW:
                return (
                    f"AI step stopped after {MAX_AI_ERRORS_IN_A_ROW} failures in a row "
                    f"(last: {exc}). The rest of the report is rule-based."
                )
    return ""


# ---------------------------------------------------------------- reports
def _csv_safe(value: Any) -> Any:
    """Defuse spreadsheet formulas in text cells that start with = + - or @."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@"):
        return "'" + value
    return value


def write_csv(records: Iterable[LetterRecord], path: Path) -> Path:
    """Write one row per PDF to ``path`` (UTF-8 with BOM so Excel opens it correctly)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for r in records:
            row = {
                "file": r.name,
                "status": r.status,
                "pages": r.pages or "",
                "best_layout": r.best_layout,
                "score": r.score if r.status == STATUS_OK else "",
                "fit": r.fit,
                "ambiguous": "yes" if r.ambiguous else "",
                "runner_up": r.runner_up,
                "score_gap": "" if r.score_gap is None else r.score_gap,
                "tie_break": r.tie_note,
                "closest_layout": r.closest_layout,
                "failures": "; ".join(r.failures),
                "unknown_blocks": r.unknown_blocks if r.status == STATUS_OK else "",
                "duplicate_group": r.duplicate_group,
                "ai_role_changes": len(r.ai_changes) if r.ai_changes else "",
                "note": r.note,
            }
            writer.writerow({k: _csv_safe(v) for k, v in row.items()})
    return path


def _md(name: str) -> str:
    return "`" + name.replace("`", "'") + "`"


def write_markdown(
    records: Sequence[LetterRecord],
    groups: Sequence[DuplicateGroup],
    layouts: Sequence[LayoutProfile],
    settings: Settings,
    path: Path,
    ai_summary: dict[str, Any] | None = None,
) -> Path:
    """Write the human-readable portfolio report to ``path``."""
    ok = [r for r in records if r.status == STATUS_OK]
    unreadable = [r for r in records if r.status != STATUS_OK]
    placed = Counter(r.best_layout for r in ok if r.fit != FIT_NONE)
    nofit = [r for r in ok if r.fit == FIT_NONE]
    weak = [r for r in ok if r.fit == FIT_WEAK]
    ambiguous = [r for r in ok if r.ambiguous]

    def share(n: int) -> str:
        return f"{n / len(ok):.0%}" if ok else "-"

    out: list[str] = ["# Letter portfolio report", ""]
    out.append(
        f"- Letters scanned: **{len(records)}** (matched: {len(ok)}, "
        f"unreadable or no text: {len(unreadable)})"
    )
    out.append(f"- Layouts compared: {', '.join(lay.id for lay in layouts)}")
    out.append(
        f"- Settings: weak fit below score {settings.min_score}; near-tie margin "
        f"{settings.margin}; duplicate similarity at or above {settings.duplicate_threshold}"
    )
    out.append("- Every score comes from the rule-based matcher; the sample layouts are synthetic.")
    out += ["", "## Letters per layout", "", "| Layout | Letters | Share |", "|---|---:|---:|"]
    for lay in layouts:
        out.append(f"| {lay.id} | {placed.get(lay.id, 0)} | {share(placed.get(lay.id, 0))} |")
    out.append(f"| (no layout fits) | {len(nofit)} | {share(len(nofit))} |")

    out += ["", f"## No layout fits ({len(nofit)})", ""]
    if nofit:
        out.append("Candidates for a new layout, or for a change to the letter.")
        out.append("")
        for r in nofit:
            why = "; ".join(r.failures) or "hard check failed"
            out.append(f"- {_md(r.name)}: closest is {r.closest_layout}. {why}")
    else:
        out.append("None.")

    out += ["", f"## Weak fit, best score below {settings.min_score} ({len(weak)})", ""]
    if weak:
        for r in weak:
            out.append(f"- {_md(r.name)}: {r.best_layout} ({r.score})")
    else:
        out.append("None.")

    out += [
        "",
        f"## Ambiguous: two or more layouts within {settings.margin} ({len(ambiguous)})",
        "",
    ]
    if ambiguous:
        out.append("Either layout would work. The tightest fit was chosen (fewest unused lines).")
        out.append("")
        for r in ambiguous:
            out.append(f"- {_md(r.name)}: {r.tie_note}")
    else:
        out.append("None.")

    out += ["", f"## Near-duplicate letters ({len(groups)} group(s))", ""]
    if groups:
        out.append(
            "Body text matches at or above the threshold. Candidates for shared content. "
            "Similarity is the Jaccard overlap of 5-word shingles, digits normalized."
        )
        out.append("")
        for g in groups:
            names = ", ".join(_md(m.name) for m in g.members)
            out.append(f"- **{g.id}** (similarity up to {g.max_similarity}): {names}")
    else:
        out.append("None found.")

    out += ["", f"## Unreadable or no text ({len(unreadable)})", ""]
    if unreadable:
        for r in unreadable:
            out.append(f"- {_md(r.name)} [{r.status}]: {r.note}")
    else:
        out.append("None.")

    if ai_summary is not None:
        out += ["", "## AI step", ""]
        out.append(ai_summary["line"])
        if ai_summary.get("stopped"):
            out.append(f"- {ai_summary['stopped']}")
        for r in records:
            if not (r.ai_changes or r.ai_explanation or r.ai_error):
                continue
            out.append("")
            out.append(f"### {_md(r.name)}")
            for c in r.ai_changes:
                out.append(
                    f"- AI labeled block {c['index']} {c['from']} -> {c['to']} "
                    f"({c['confidence']:.2f}): {c['reason']}"
                )
            if r.ai_explanation:
                out.append(f"- Why (AI): {r.ai_explanation}")
            if r.ai_error:
                out.append(f"- AI step warning: {r.ai_error}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return path
