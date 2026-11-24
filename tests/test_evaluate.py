"""The benchmark: ground truth, arithmetic and honest verdicts. Offline, scripted backends."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from icsel.cli import EXIT_OK, EXIT_USAGE, main
from icsel.evaluate import (
    Tally,
    TruthError,
    collect_truth,
    eval_markdown,
    evaluate,
    load_truth,
    run_eval,
    true_role,
    verdict,
)
from icsel.extract import extract
from icsel.llm import LLMAssistant, LLMError, register_backend
from icsel.profile import LayoutProfile
from icsel.synthetic import LETTERS, STATEMENTS, generate_portfolio


@pytest.fixture(scope="module")
def portfolio_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("eval-portfolio")
    generate_portfolio(out)
    return out


@pytest.fixture(scope="module")
def notices(portfolio_dir: Path) -> list[tuple[Path, dict[str, Any]]]:
    """Two notices whose six table cells each are the only blocks the rules cannot label."""
    return collect_truth(sorted(portfolio_dir.glob("X*.pdf")))[0]


class LabelBackend:
    """Gives every unknown block the same role, right or wrong; optionally reports usage."""

    name = "scripted"
    model = "scripted-1"

    def __init__(self, role: str, usage: bool = False, prices: dict | None = None) -> None:
        self.role = role
        self.calls = 0
        self.last_usage = {"input_tokens": 100, "output_tokens": 10} if usage else None
        self.prices = prices

    def complete(self, system: str, prompt: str, json_schema: dict[str, Any] | None = None) -> str:
        self.calls += 1
        if json_schema is None:
            return "Because."
        found = re.search(r'"unknown_indexes":\s*\[(.*?)\]', prompt, re.S)
        indexes = [int(x) for x in (found.group(1) if found else "").split(",") if x.strip()]
        return json.dumps(
            {
                "suggestions": [
                    {"index": i, "role": self.role, "confidence": 0.9, "reason": "r"}
                    for i in indexes
                ]
            }
        )


class BrokenBackend(LabelBackend):
    def complete(self, system: str, prompt: str, json_schema: dict[str, Any] | None = None) -> str:
        self.calls += 1
        raise LLMError("boom")


# ------------------------------------------------------------------ ground truth
def test_truth_describes_every_letter(letters_dir: Path) -> None:
    truth = load_truth(letters_dir / "truth.json")
    assert set(truth) == {f"{s.id}.pdf" for s in [*LETTERS, *STATEMENTS]}
    assert truth["L14-no-layout-fits.pdf"]["layout"] is None
    assert truth["L01-classic.pdf"]["layout"] == "classic-left"
    fields = {f["name"]: f["text"] for f in truth["L02-classic-jitter.pdf"]["fields"]}
    assert fields["accountNumber"] == "0000-0002" and fields["letterDate"] == "March 3, 2026"
    table = {f["text"] for f in truth["L12-table-block.pdf"]["fields"]}
    assert {"$41.20", "Jan - Mar"} <= table and "Service" not in table


def test_truth_fields_are_verbatim_and_uniquely_named(
    letters_dir: Path, portfolio_dir: Path
) -> None:
    for folder in (letters_dir, portfolio_dir):
        for name, entry in load_truth(folder / "truth.json").items():
            letter = extract(folder / name)
            text = "\n".join(b.text for b in letter.blocks)
            if "fields" not in entry:
                continue  # statements
            names = [f["name"] for f in entry["fields"]]
            assert len(names) == len(set(names)), name
            if name.startswith("L14"):
                continue  # its moved address is printed over the body text on purpose
            assert all(f["text"] in text for f in entry["fields"]), name


def test_almost_every_block_can_be_scored(letters_dir: Path, layouts: list[LayoutProfile]) -> None:
    pairs, skipped = collect_truth(sorted(letters_dir.glob("*.pdf")))
    assert len(pairs) == len(LETTERS) + len(STATEMENTS) and skipped == 0
    tally = run_eval(pairs, layouts).tally
    assert tally.blocks_unscored == 0 and tally.blocks > 100


def test_true_role_is_a_majority_vote() -> None:
    by_line = {"a": "body", "b": "body", "c": "closing"}
    assert true_role(["a", "b", "c", "unknown line"], by_line) == "body"
    assert true_role(["nothing known"], by_line) is None


def test_unreadable_files_are_skipped(portfolio_dir: Path) -> None:
    pairs, skipped = collect_truth(sorted(portfolio_dir.glob("*.pdf")))
    assert len(pairs) == 38 and skipped == 2


@pytest.mark.parametrize("content", ["", "{", "[]", '{"letters": [{}]}'])
def test_bad_truth_file_is_a_clear_error(tmp_path: Path, content: str) -> None:
    path = tmp_path / "truth.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(TruthError, match=r"truth\.json"):
        load_truth(path)


# ------------------------------------------------------------------ arithmetic
def test_tally_metrics() -> None:
    t = Tally(blocks=10, roles_right=9, fields_found=4, fields_right=3, fields_true=6,
              names_right=2, purposes=5, purposes_right=1, layouts=2, layouts_right=2)  # fmt: skip
    assert t.metrics() == {
        "role_accuracy": 0.9,
        "field_precision": 0.75,
        "field_recall": 0.5,
        "field_f1": 0.6,  # 2 * 3 / (4 + 6)
        "name_accuracy": 0.6667,
        "purpose_accuracy": 0.2,
        "layout_agreement": 1.0,
    }
    assert set(Tally().metrics().values()) == {None}  # nothing to divide by, nothing invented


def test_right_labels_beat_the_rules_by_exactly_the_unknown_blocks(
    notices: list[tuple[Path, dict[str, Any]]], layouts: list[LayoutProfile]
) -> None:
    rules = run_eval(notices, layouts).tally
    backend = LabelBackend("body")
    run = run_eval(notices, layouts, "classify", LLMAssistant(backend))
    unknown = sum(b.role == "unknown" for p, _ in notices for b in extract(p).blocks)
    assert unknown > 0 and run.calls == backend.calls == 2
    assert run.tally.roles_right == rules.roles_right + unknown
    assert run.tally.blocks == rules.blocks
    beats, text = verdict("classify", rules, run.tally)
    assert beats is True and "beats the rules" in text


def test_wrong_labels_do_not_beat_the_rules_and_the_report_says_so(
    notices: list[tuple[Path, dict[str, Any]]], layouts: list[LayoutProfile]
) -> None:
    assistant = LLMAssistant(LabelBackend("footer"), tasks=("classify", "explain"))
    doc = evaluate(notices, layouts, assistant)
    by_task = {v["task"]: v for v in doc["verdicts"]}
    assert by_task["classify"]["beats_rules"] is False
    assert by_task["explain"]["beats_rules"] is None
    report = eval_markdown(doc)
    assert "`classify` DOES NOT beat the rules" in report
    assert "NOT shown to beat the rules" in report


def test_a_worse_result_is_called_worse() -> None:
    good = Tally(blocks=10, roles_right=9, layouts=10, layouts_right=10)
    bad = Tally(blocks=10, roles_right=5, layouts=10, layouts_right=9)
    beats, text = verdict("classify", good, bad)
    assert beats is False and "is WORSE than" in text and "Layout agreement FELL" in text


def test_failures_fall_back_to_rules_and_stop_after_three(
    portfolio_dir: Path, layouts: list[LayoutProfile]
) -> None:
    pairs, _ = collect_truth(sorted(portfolio_dir.glob("X*.pdf")))
    pairs = pairs * 3  # six letters that all need a call
    backend = BrokenBackend("body")
    run = run_eval(pairs, layouts, "classify", LLMAssistant(backend))
    assert backend.calls == 3 and run.failures == 3 and "3 failures in a row" in run.stopped
    assert run.tally == run_eval(pairs, layouts).tally


def test_call_cap_is_hard(
    notices: list[tuple[Path, dict[str, Any]]], layouts: list[LayoutProfile]
) -> None:
    backend = LabelBackend("body")
    run = run_eval(notices, layouts, "classify", LLMAssistant(backend, max_calls=1))
    assert backend.calls == 1 and "cap of 1" in run.stopped and run.failures == 0


def test_tokens_and_cost_only_when_the_backend_gives_them(
    notices: list[tuple[Path, dict[str, Any]]], layouts: list[LayoutProfile]
) -> None:
    def run(**kw: Any) -> dict[str, Any]:
        return run_eval(
            notices, layouts, "classify", LLMAssistant(LabelBackend("body", **kw))
        ).to_dict()

    plain = run()
    assert "tokens" not in plain and "cost_estimate" not in plain
    tokens = run(usage=True)
    assert tokens["tokens"] == {"input_tokens": 200, "output_tokens": 20}
    assert "cost_estimate" not in tokens
    priced = run(usage=True, prices={"input_tokens": 5.0, "output_tokens": 50.0})
    assert priced["cost_estimate"] == pytest.approx((200 * 5 + 20 * 50) / 1_000_000)


# ------------------------------------------------------------------ CLI
def test_cli_rules_only(letters_dir: Path, tmp_path: Path) -> None:
    assert main(["eval", str(letters_dir), "--out", str(tmp_path)]) == EXIT_OK
    doc = json.loads((tmp_path / "eval.json").read_text(encoding="utf-8"))
    assert [r["run"] for r in doc["runs"]] == ["rules"]
    assert doc["runs"][0]["model_calls"] == 0
    assert doc["runs"][0]["metrics"]["layout_agreement"] == 1.0
    assert "No AI task was run." in (tmp_path / "eval.md").read_text(encoding="utf-8")


def test_cli_with_a_fake_backend_needs_yes(
    letters_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    backend = LabelBackend("body")
    register_backend("fake-eval", lambda env: backend)
    argv = ["eval", str(letters_dir), "--llm", "fake-eval", "--out", str(tmp_path)]
    assert main(argv) == EXIT_OK
    assert backend.calls == 0 and "needs --yes" in capsys.readouterr().err
    assert main([*argv, "--yes"]) == EXIT_OK
    doc = json.loads((tmp_path / "eval.json").read_text(encoding="utf-8"))
    assert [r["run"] for r in doc["runs"]] == ["rules", "rules + classify", "rules + explain"]
    assert backend.calls == sum(r["model_calls"] for r in doc["runs"])


def test_cli_without_truth_is_a_usage_error(
    letters_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "none.json"
    assert main(["eval", str(letters_dir), "--truth", str(missing)]) == EXIT_USAGE
    assert "ground truth" in capsys.readouterr().err


def test_max_calls_must_not_be_negative(letters_dir: Path) -> None:
    with pytest.raises(SystemExit):
        main(["eval", str(letters_dir), "--max-calls", "-1"])
