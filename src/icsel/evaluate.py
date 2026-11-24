"""Benchmark: rules alone against rules plus each AI task (``icsel eval``).

Letters are compared with a ground-truth file (``truth.json``, written next to the
synthetic letters). Every metric is computed for the rules alone and again for the
rules plus one task at a time, so each task has to earn its place:

* **role accuracy**: detected blocks whose role is the true one. A block's true role is
  the one most of its lines were drawn with; blocks whose lines are not in the truth
  file are not scored.
* **field precision and recall**: detected variable fields against the true ones,
  compared by exact text. The rules-only baseline is what the rules already know: the
  date block and each address line. Names are scored separately (``name_accuracy``,
  among correctly found fields).
* **purpose accuracy**: the rules have no purpose tagger, so their baseline is always
  ``other``.
* **layout agreement**: the chosen layout (portfolio tie-break included) is the one the
  letter was built from; for a fits-no-layout letter, that no layout fits.

Model calls come from the assistant. Tokens are reported when the backend exposes
``last_usage``; a cost only when it also exposes ``prices``. Nothing is guessed.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import DEFAULT_CONFIG, ScoringConfig
from .explain import explanation_reasons
from .extract import ExtractionError, LetterStructure, extract
from .llm import Assistant, LLMCallLimit, LLMError, apply_suggestions, fit_facts
from .match import rank, resolve_ties
from .profile import LayoutProfile

TRUTH_FILE = "truth.json"
#: Purpose assumed when nothing tags the letter.
NO_PURPOSE = "other"
#: The AI part of a run stops after this many failed letters in a row.
MAX_FAILURES_IN_A_ROW = 3
#: The metric each task is judged on. Tasks not listed have nothing to score.
TASK_METRIC = {"classify": "role_accuracy", "fields": "field_f1", "purpose": "purpose_accuracy"}
#: Tasks that run once per letter, in the order they are reported.
LETTER_TASKS = ("classify", "fields", "purpose", "explain")


class TruthError(ValueError):
    """The ground-truth file is missing or malformed."""


def load_truth(path: Path) -> dict[str, dict[str, Any]]:
    """Load ``truth.json`` as ``{file name: entry}``.

    Raises:
        TruthError: If the file is missing or not in the expected shape.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {str(e["file"]): e for e in data["letters"]}
    except OSError as exc:
        raise TruthError(f"{path}: cannot read the ground truth ({exc.strerror or exc})") from exc
    except (ValueError, KeyError, TypeError) as exc:
        raise TruthError(f"{path}: not a valid ground-truth file ({exc!r})") from exc


def ratio(part: int, whole: int) -> float | None:
    """``part / whole``, or ``None`` when there is nothing to divide by."""
    return part / whole if whole else None


@dataclass
class Tally:
    """Raw counts of one run; the rates are derived in :meth:`metrics`."""

    letters: int = 0
    blocks: int = 0
    roles_right: int = 0
    blocks_unscored: int = 0
    fields_found: int = 0
    fields_right: int = 0
    fields_true: int = 0
    names_right: int = 0
    purposes: int = 0
    purposes_right: int = 0
    layouts: int = 0
    layouts_right: int = 0

    def metrics(self) -> dict[str, float | None]:
        """Accuracy, precision and recall figures, rounded; ``None`` = not measurable."""
        precision = ratio(self.fields_right, self.fields_found)
        recall = ratio(self.fields_right, self.fields_true)
        f1 = None
        if self.fields_true:
            f1 = 2 * self.fields_right / (self.fields_found + self.fields_true)
        raw = {
            "role_accuracy": ratio(self.roles_right, self.blocks),
            "field_precision": precision,
            "field_recall": recall,
            "field_f1": f1,
            "name_accuracy": ratio(self.names_right, self.fields_right),
            "purpose_accuracy": ratio(self.purposes_right, self.purposes),
            "layout_agreement": ratio(self.layouts_right, self.layouts),
        }
        return {k: None if v is None else round(v, 4) for k, v in raw.items()}


@dataclass
class RunResult:
    """One configuration (rules, or rules plus a task) over all letters."""

    name: str
    tally: Tally
    calls: int = 0
    cache_hits: int = 0
    tokens: dict[str, int] | None = None
    cost: float | None = None
    drops: dict[str, int] = field(default_factory=dict)
    failures: int = 0
    stopped: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Plain-data view. ``tokens`` and ``cost`` appear only when the backend reports them."""
        out: dict[str, Any] = {
            "run": self.name,
            "metrics": self.tally.metrics(),
            "counts": dict(vars(self.tally)),
            "model_calls": self.calls,
            "cache_hits": self.cache_hits,
            "dropped": self.drops,
            "failed_letters": self.failures,
        }
        if self.tokens is not None:
            out["tokens"] = self.tokens
        if self.cost is not None:
            out["cost_estimate"] = round(self.cost, 6)
        if self.stopped:
            out["stopped"] = self.stopped
        return out


def true_role(lines: Sequence[str], by_line: dict[str, str]) -> str | None:
    """Role most of ``lines`` were drawn with, or ``None`` if none is known."""
    votes = Counter(by_line[ln] for ln in lines if ln in by_line)
    return votes.most_common(1)[0][0] if votes else None


def rule_fields(letter: LetterStructure) -> dict[str, str]:
    """Variable text the rules already know: the date and each address line (text -> name)."""
    out: dict[str, str] = {}
    for b in letter.blocks_with("date", page=1)[:1]:
        out[b.text] = "letterDate"
    for b in letter.blocks_with("address", page=1)[:1]:
        for i, line in enumerate(b.lines):
            out[line] = "recipientName" if i == 0 else f"addressLine{i}"
    return out


def _score_letter(
    tally: Tally,
    truth: dict[str, Any],
    letter: LetterStructure,
    layouts: list[LayoutProfile],
    config: ScoringConfig,
    found: dict[str, str],
    purpose: str,
) -> None:
    tally.letters += 1
    by_line = {ln: b["role"] for b in truth.get("blocks", []) for ln in b["lines"]}
    for block in letter.blocks:
        role = true_role(block.lines, by_line)
        if role is None:
            tally.blocks_unscored += 1
            continue
        tally.blocks += 1
        tally.roles_right += role == block.role
    if "fields" in truth:
        true_fields = {f["text"]: f["name"] for f in truth["fields"]}
        hits = found.keys() & true_fields.keys()
        tally.fields_found += len(found)
        tally.fields_true += len(true_fields)
        tally.fields_right += len(hits)
        tally.names_right += sum(found[t].lower() == true_fields[t].lower() for t in hits)
    if "purpose" in truth:
        tally.purposes += 1
        tally.purposes_right += purpose == truth["purpose"]
    if "layout" in truth:
        results = rank(letter, layouts, config)
        best = resolve_ties(letter, layouts, results, config.tie_margin).results[0]
        tally.layouts += 1
        tally.layouts_right += (best.layout_id if best.fits else None) == truth["layout"]


def _run_task(
    task: str,
    assistant: Any,
    letter: LetterStructure,
    layouts: list[LayoutProfile],
    config: ScoringConfig,
) -> dict[str, Any]:
    """Run one task on one letter. ``classify`` updates ``letter`` in place."""
    if task == "classify":
        apply_suggestions(letter, assistant.classify_unknown(letter), assistant.min_confidence)
    elif task == "fields":
        found = assistant.detect_fields(letter)
        return {"fields": {f.text: f.name for f in found if f.kind == "variable"}}
    elif task == "purpose":
        return {"purpose": assistant.tag_purpose(letter)}
    elif task == "explain":
        results = rank(letter, layouts, config)
        if explanation_reasons(letter, layouts, results, config):
            assistant.explain_fit(fit_facts(letter, results))
    return {}


def run_eval(
    letters: Sequence[tuple[Path, dict[str, Any]]],
    layouts: list[LayoutProfile],
    task: str | None = None,
    assistant: Assistant | None = None,
    config: ScoringConfig | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> RunResult:
    """Score the rules (``task=None``) or the rules plus one AI ``task``.

    A letter whose AI call fails is scored with its rule-based result and counted in
    ``failures``. After :data:`MAX_FAILURES_IN_A_ROW` failures, or when the call cap is
    reached, no further calls are made.
    """
    cfg = config or DEFAULT_CONFIG
    out = RunResult("rules" if task is None else f"rules + {task}", Tally())
    ai: Any = assistant if task is not None else None
    before = ai.counters() if ai else None
    in_a_row = 0
    for i, (path, truth) in enumerate(letters, start=1):
        letter = extract(path)
        found, purpose = rule_fields(letter), NO_PURPOSE
        if ai and task and not out.stopped:
            try:
                got = _run_task(task, ai, letter, layouts, cfg)
                # Rules first: what the model adds never replaces a rule-based field.
                found = {**got.get("fields", {}), **found}
                purpose = got.get("purpose", purpose)
                in_a_row = 0
            except LLMCallLimit as exc:
                out.stopped = str(exc)
            except LLMError as exc:
                out.failures += 1
                in_a_row += 1
                if in_a_row >= MAX_FAILURES_IN_A_ROW:
                    out.stopped = f"stopped after {in_a_row} failures in a row (last: {exc})"
        _score_letter(out.tally, truth, letter, layouts, cfg, found, purpose)
        if progress:
            progress(i, len(letters))
    if ai and before:
        after = ai.counters()
        out.calls = after["calls"] - before["calls"]
        out.cache_hits = after["cache_hits"] - before["cache_hits"]
        out.drops = {
            k.split(": ", 1)[1]: n - before["drops"].get(k, 0)
            for k, n in after["drops"].items()
            if k.startswith(f"{task}: ") and n > before["drops"].get(k, 0)
        }
        if after["tokens"] is not None:
            base = before["tokens"] or {}
            out.tokens = {k: n - base.get(k, 0) for k, n in after["tokens"].items()}
            prices = getattr(ai.backend, "prices", None)
            if prices:
                out.cost = sum(
                    out.tokens.get(k, 0) * prices[k] / 1_000_000 for k in prices if k in out.tokens
                )
    return out


def verdict(task: str, rules: Tally, with_task: Tally) -> tuple[bool | None, str]:
    """Whether ``task`` beats the rules on its own metric, with a plain sentence."""
    metric = TASK_METRIC.get(task)
    if metric is None:
        return None, f"`{task}` has no metric here, so it is NOT shown to beat the rules."
    a, b = rules.metrics()[metric], with_task.metrics()[metric]
    if a is None or b is None:
        return None, f"`{task}`: {metric} cannot be measured on these letters."
    label = metric.replace("_", " ")
    if b > a:
        text = f"`{task}` beats the rules: {label} {a:.3f} -> {b:.3f}."
    else:
        how = "is the same as" if b == a else "is WORSE than"
        text = f"`{task}` DOES NOT beat the rules: {label} {how} rules alone ({a:.3f} -> {b:.3f})."
    la, lb = rules.metrics()["layout_agreement"], with_task.metrics()["layout_agreement"]
    if la is not None and lb is not None and lb < la:
        text += f" Layout agreement FELL ({la:.3f} -> {lb:.3f})."
    return b > a, text


def evaluate(
    letters: Sequence[tuple[Path, dict[str, Any]]],
    layouts: list[LayoutProfile],
    assistant: Assistant | None = None,
    config: ScoringConfig | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    """Run the benchmark and return the document written to ``eval.json``."""
    rules = run_eval(letters, layouts, config=config)
    doc: dict[str, Any] = {
        "letters": len(letters),
        "backend": assistant.describe() if assistant else {"enabled": False, "backend": "none"},
        "runs": [rules.to_dict()],
        "verdicts": [],
        "not_scored": [],
    }
    tasks = tuple(getattr(assistant, "tasks", ())) if assistant else ()
    for task in LETTER_TASKS:
        if task not in tasks:
            continue
        run = run_eval(letters, layouts, task, assistant, config, progress)
        beats, text = verdict(task, rules.tally, run.tally)
        if run.failures or run.stopped:
            text += f" Incomplete: {run.failures} failed letter(s)."
            if run.stopped:
                text += f" {run.stopped}."
        doc["runs"].append(run.to_dict())
        doc["verdicts"].append({"task": task, "beats_rules": beats, "text": text})
    doc["not_scored"] = [t for t in tasks if t not in LETTER_TASKS]
    return doc


def _cell(value: float | None) -> str:
    return "-" if value is None else f"{value:.3f}"


def eval_markdown(doc: dict[str, Any]) -> str:
    """The report written to ``eval.md``."""
    backend = doc["backend"]
    out = ["# icsel benchmark", "", f"- Letters: **{doc['letters']}** (synthetic, with truth)"]
    if backend.get("enabled"):
        out.append(f"- Backend: {backend['backend']} / {backend['model']}")
    else:
        out.append("- Backend: none (rules only)")
    out += ["", "## Results", ""]
    out.append(
        "| Run | Role accuracy | Field precision | Field recall | Field names | "
        "Purpose accuracy | Layout agreement | Calls | Cached | Dropped | Failed |"
    )
    out.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for run in doc["runs"]:
        m = run["metrics"]
        keys = ("role_accuracy", "field_precision", "field_recall", "name_accuracy",
                "purpose_accuracy", "layout_agreement")  # fmt: skip
        cells = " | ".join(_cell(m[k]) for k in keys)
        out.append(
            f"| {run['run']} | {cells} | {run['model_calls']} | {run['cache_hits']} | "
            f"{sum(run['dropped'].values())} | {run['failed_letters']} |"
        )
    extra = [r for r in doc["runs"] if "tokens" in r]
    if extra:
        out += ["", "## Tokens", ""]
        for r in extra:
            used = ", ".join(f"{k} {n}" for k, n in r["tokens"].items())
            cost = f"; cost estimate {r['cost_estimate']}" if "cost_estimate" in r else ""
            out.append(f"- {r['run']}: {used}{cost}")
    dropped = [r for r in doc["runs"] if r["dropped"]]
    if dropped:
        out += ["", "## Dropped model output", ""]
        for r in dropped:
            out.append(f"- {r['run']}: " + ", ".join(f"{k} {n}" for k, n in r["dropped"].items()))
    out += ["", "## Does each task beat the rules?", ""]
    if doc["verdicts"]:
        out += [f"- {v['text']}" for v in doc["verdicts"]]
    else:
        out.append("No AI task was run.")
    for task in doc["not_scored"]:
        out.append(f"- `{task}` does not run per letter and is not scored here.")
    out += [
        "",
        "Field baseline: the date and address lines the rules already find. Purpose baseline: "
        f"always `{NO_PURPOSE}`. Fields are compared by exact text.",
    ]
    return "\n".join(out) + "\n"


def collect_truth(
    paths: Sequence[Path], truth_file: Path | None = None
) -> tuple[list[tuple[Path, dict[str, Any]]], int]:
    """Pair letters with their truth entries; also return how many letters were skipped.

    Letters without an entry, and files that cannot be read, are skipped.
    """
    cache: dict[Path, dict[str, dict[str, Any]]] = {}
    pairs: list[tuple[Path, dict[str, Any]]] = []
    skipped = 0
    for path in paths:
        source = truth_file or path.parent / TRUTH_FILE
        if source not in cache:
            cache[source] = load_truth(source)
        entry = cache[source].get(path.name)
        if entry is None:
            skipped += 1
            continue
        try:
            extract(path)
        except ExtractionError:
            skipped += 1
            continue
        pairs.append((path, entry))
    return pairs, skipped
