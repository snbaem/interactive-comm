"""Purpose tagging: the list, what is sent, validation and reports. Offline fake backends."""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any

import pytest

from icsel.cli import EXIT_OK, EXIT_USAGE, main
from icsel.evaluate import collect_truth, evaluate, load_truth, run_eval
from icsel.extract import extract
from icsel.llm import LLMAssistant, LLMError, load_purposes, register_backend
from icsel.llm.purpose import MAX_PARAGRAPH, purpose_payload
from icsel.profile import LayoutProfile


class PurposeBackend:
    """Answers from a subject -> purpose table, or always the same reply."""

    name = "fake"
    model = "purpose-1"

    def __init__(self, reply: str | None = None, table: dict[str, str] | None = None) -> None:
        self.reply, self.table = reply, table or {}
        self.prompts: list[str] = []

    def complete(self, system: str, prompt: str, json_schema: dict[str, Any] | None = None) -> str:
        self.prompts.append(prompt)
        if self.reply is not None:
            return self.reply
        found = re.search(r'"subject": "(.*?)"', prompt)
        return json.dumps({"purpose": self.table.get(found.group(1) if found else "", "other")})


def _assistant(backend: Any, **kw: Any) -> LLMAssistant:
    return LLMAssistant(backend, tasks=("purpose",), send_text=("purpose",), **kw)


# ------------------------------------------------------------------ the list
def test_bundled_list() -> None:
    assert load_purposes() == (
        "billing-notice", "confirmation", "statement", "reminder", "policy-update", "other",
    )  # fmt: skip


def test_every_true_purpose_is_in_the_list(letters_dir: Path) -> None:
    truth = load_truth(letters_dir / "truth.json")
    assert {e["purpose"] for e in truth.values()} <= set(load_purposes())


def test_custom_list_gets_other(tmp_path: Path) -> None:
    path = tmp_path / "p.json"
    path.write_text('{"purposes": ["welcome"]}', encoding="utf-8")
    assert load_purposes(path) == ("welcome", "other")


@pytest.mark.parametrize(
    "text",
    ["{", "[]", "{}", '{"purposes": []}', '{"purposes": ["Not Ok"]}', '{"purposes": [1]}',
     '{"purposes": ["a", "a"]}', '{"purposes": "a"}'],
)  # fmt: skip
def test_bad_list_is_a_clear_error(tmp_path: Path, text: str) -> None:
    path = tmp_path / "p.json"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(LLMError, match=r"p\.json"):
        load_purposes(path)


# ------------------------------------------------------------------ data sent
def test_needs_its_own_switch() -> None:
    with pytest.raises(LLMError, match="--purpose-send-text"):
        LLMAssistant(PurposeBackend(), tasks=("purpose",))
    with pytest.raises(LLMError, match="--purpose-send-text"):
        LLMAssistant(PurposeBackend(), tasks=("purpose",), send_text=("fields",))


def test_only_subject_and_first_paragraph_are_sent(letters_dir: Path) -> None:
    letter = extract(letters_dir / "L02-classic-jitter.pdf")
    bodies = [b.text for b in letter.blocks if b.role == "body"]
    backend = PurposeBackend()
    _assistant(backend).tag_purpose(letter)
    sent = backend.prompts[0]
    assert bodies[0] in sent and "Meter reading review" in sent
    for b in letter.blocks:
        if b.role in ("address", "date", "salutation", "closing", "footer"):
            assert b.lines[0] not in sent
    assert not any(b in sent for b in bodies[1:])


def test_paragraph_is_capped(letters_dir: Path) -> None:
    letter = extract(letters_dir / "L01-classic.pdf")
    next(b for b in letter.blocks if b.role == "body").lines = ["x" * 5000]
    assert len(purpose_payload(letter)["first_paragraph"]) == MAX_PARAGRAPH


def test_nothing_to_send_makes_no_call(letters_dir: Path) -> None:
    letter = extract(letters_dir / "L01-classic.pdf")
    letter.blocks = [b for b in letter.blocks if b.role == "address"]
    backend = PurposeBackend()
    assert _assistant(backend).tag_purpose(letter) == "other" and backend.prompts == []


# ------------------------------------------------------------------ validation
@pytest.mark.parametrize(
    "reply",
    ['{"purpose": "invented"}', '{"purpose": "Statement"}', '{"purpose": ["statement"]}',
     '{"purpose": "statement", "x": 1}', '"statement"', "{}"],
)  # fmt: skip
def test_anything_outside_the_list_becomes_other_and_is_counted(
    letters_dir: Path, reply: str
) -> None:
    assistant = _assistant(PurposeBackend(reply))
    assert assistant.tag_purpose(extract(letters_dir / "L01-classic.pdf")) == "other"
    assert assistant.drops == {"purpose: not in the list": 1}


def test_unparseable_reply_is_a_failure(letters_dir: Path) -> None:
    with pytest.raises(LLMError):
        _assistant(PurposeBackend("nope")).tag_purpose(extract(letters_dir / "L01-classic.pdf"))


# ------------------------------------------------------------------ benchmark
def test_eval_purpose_scores(letters_dir: Path, layouts: list[LayoutProfile]) -> None:
    pairs, _ = collect_truth(sorted(letters_dir.glob("*.pdf")))
    rules = run_eval(pairs, layouts).tally
    table = {}
    for path, entry in pairs:
        subject = extract(path).region("subject")
        table[subject.text if subject else ""] = entry["purpose"]
    good = run_eval(pairs, layouts, "purpose", _assistant(PurposeBackend(table=table)))
    assert good.tally.purposes_right == good.tally.purposes
    assert good.tally.purposes_right > rules.purposes_right
    wrong = evaluate(pairs, layouts, _assistant(PurposeBackend('{"purpose": "reminder"}')))
    assert wrong["runs"][1]["metrics"]["purpose_accuracy"] == 0.0
    assert wrong["verdicts"][0]["beats_rules"] is False


def test_a_constant_answer_does_not_count_as_beating_the_rules(
    letters_dir: Path, layouts: list[LayoutProfile]
) -> None:
    pairs, _ = collect_truth(sorted(letters_dir.glob("*.pdf")))
    doc = evaluate(pairs, layouts, _assistant(PurposeBackend('{"purpose": "statement"}')))
    rules, run = (r["metrics"]["purpose_accuracy"] for r in doc["runs"])
    assert run > rules  # better than always saying "other" ...
    assert doc["verdicts"][0]["beats_rules"] is False  # ... but not better than a constant
    assert "most common purpose" in doc["verdicts"][0]["text"]


# ------------------------------------------------------------------ CLI
def test_portfolio_column_and_table(letters_dir: Path, tmp_path: Path) -> None:
    backend = PurposeBackend('{"purpose": "statement"}')
    register_backend("fake-purpose", lambda env: backend)
    argv = ["portfolio", str(letters_dir), "--llm", "fake-purpose", "--ai-tasks", "purpose"]
    assert main([*argv, "--yes", "--out", str(tmp_path)]) == EXIT_USAGE  # no text opt-in
    argv += ["--purpose-send-text", "--yes"]
    assert main([*argv, "--out", str(tmp_path)]) == EXIT_OK
    with (tmp_path / "portfolio.csv").open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert {r["purpose"] for r in rows} == {"statement"}
    report = (tmp_path / "portfolio.md").read_text(encoding="utf-8")
    assert "## Purpose by layout (AI, 15 letter(s) tagged)" in report
    assert re.search(r"\| statement \|.* 15 \|", report)


def test_rules_only_portfolio_has_an_empty_purpose_column(
    letters_dir: Path, tmp_path: Path
) -> None:
    assert main(["portfolio", str(letters_dir), "--out", str(tmp_path)]) == EXIT_OK
    with (tmp_path / "portfolio.csv").open(encoding="utf-8-sig", newline="") as fh:
        assert {r["purpose"] for r in csv.DictReader(fh)} == {""}
    assert "Purpose" not in (tmp_path / "portfolio.md").read_text(encoding="utf-8")


def test_match_result_section_and_custom_list(letters_dir: Path, tmp_path: Path) -> None:
    purposes = tmp_path / "p.json"
    purposes.write_text('{"purposes": ["welcome"]}', encoding="utf-8")
    backend = PurposeBackend('{"purpose": "welcome"}')
    register_backend("fake-purpose-match", lambda env: backend)
    argv = ["match", str(letters_dir / "L01-classic.pdf"), "--llm", "fake-purpose-match"]
    argv += ["--ai-tasks", "purpose", "--purpose-send-text", "--purposes", str(purposes)]
    assert main([*argv, "--out", str(tmp_path)]) == EXIT_OK
    doc = json.loads((tmp_path / "L01-classic.result.json").read_text(encoding="utf-8"))
    assert doc["purpose"] == {"result": "welcome", "dropped": {}, "source": "llm"}
    assert "welcome, other" in backend.prompts[0]
