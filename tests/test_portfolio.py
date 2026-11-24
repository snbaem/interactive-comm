"""Portfolio mode: scanning a folder, tie-breaks, duplicates, reports and the economical AI step."""

from __future__ import annotations

import csv
import json
import re
import shutil
from pathlib import Path
from typing import Any

import pytest

from icsel.cli import EXIT_OK, EXIT_SOME_FAILED, main
from icsel.extract import Block, LetterStructure, extract
from icsel.llm import LLMAssistant, LLMError, register_backend
from icsel.match import rank, resolve_ties, tightness
from icsel.portfolio import (
    CSV_COLUMNS,
    LetterRecord,
    Settings,
    body_fingerprint,
    find_duplicate_groups,
    plan_ai,
    run_ai,
    scan,
    write_csv,
)
from icsel.profile import LayoutProfile
from icsel.synthetic import generate_portfolio


@pytest.fixture(scope="session")
def portfolio_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("portfolio")
    generate_portfolio(out)
    return out


@pytest.fixture(scope="session")
def manifest(portfolio_dir: Path) -> list[dict[str, str]]:
    return json.loads((portfolio_dir / "manifest.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def records(portfolio_dir: Path, layouts: list[LayoutProfile]) -> dict[str, LetterRecord]:
    paths = sorted(portfolio_dir.glob("*.pdf"))
    recs = scan(paths, layouts, Settings())
    find_duplicate_groups(recs)
    return {r.path.name: r for r in recs}


class FakeBackend:
    """Answers labeling requests (a JSON schema is sent) and explanation requests."""

    name = "fake"
    model = "fake-portfolio-1"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def complete(self, system: str, prompt: str, json_schema: dict[str, Any] | None = None) -> str:
        self.calls.append("classify" if json_schema else "explain")
        if json_schema is None:
            return "Short explanation."
        found = re.search(r'"unknown_indexes":\s*\[(.*?)\]', prompt, re.S)
        indexes = [int(x) for x in (found.group(1) if found else "").split(",") if x.strip()]
        suggestions = [
            {"index": i, "role": "body", "confidence": 0.9, "reason": "table cell"} for i in indexes
        ]
        return json.dumps({"suggestions": suggestions})


class FailingBackend:
    name = "fake"
    model = "failing-1"

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, system: str, prompt: str, json_schema: dict[str, Any] | None = None) -> str:
        self.calls += 1
        raise LLMError("boom")


# ------------------------------------------------------------------ demo data and the planted cases
def test_demo_folder_has_the_planted_problem_files(manifest: list[dict[str, str]]) -> None:
    kinds = {m["kind"] for m in manifest}
    assert kinds == {"letter", "near_tie", "weak", "no_fit", "duplicate", "notice_table",
                     "scanned", "corrupt"}  # fmt: skip
    assert len(manifest) >= 30


def test_every_demo_file_is_classified_as_planted(
    manifest: list[dict[str, str]], records: dict[str, LetterRecord]
) -> None:
    for entry in manifest:
        rec = records[entry["file"]]
        kind = entry["kind"]
        where = entry["file"]
        if kind == "letter":
            assert (rec.status, rec.fit, rec.ambiguous) == ("ok", "good", False), where
            assert rec.best_layout == entry["expected_layout"], where
        elif kind == "near_tie":
            assert rec.ambiguous and rec.best_layout == entry["expected_layout"], where
            assert rec.runner_up == "tall-address" and rec.tie_note, where
        elif kind == "weak":
            assert rec.fit == "weak" and rec.best_layout == entry["expected_layout"], where
        elif kind == "no_fit":
            assert rec.fit == "none" and rec.best_layout == "", where
            assert rec.closest_layout == "tall-address", where
            assert any("address has 11 lines" in f for f in rec.failures), where
        elif kind == "duplicate":
            assert rec.best_layout == entry["expected_layout"] and rec.fit == "good", where
        elif kind == "notice_table":
            assert rec.status == "ok" and rec.unknown_blocks >= 3, where
        elif kind == "scanned":
            assert rec.status == "no_text", where
        elif kind == "corrupt":
            assert rec.status == "error" and rec.note, where


def test_only_the_planted_pair_is_a_duplicate_group(records: dict[str, LetterRecord]) -> None:
    grouped = {name: r.duplicate_group for name, r in records.items() if r.duplicate_group}
    assert set(grouped) == {
        "D01-address-confirmation.pdf",
        "D02-address-confirmation-copy.pdf",
    }
    assert len(set(grouped.values())) == 1


def test_distribution_adds_up(records: dict[str, LetterRecord]) -> None:
    ok = [r for r in records.values() if r.status == "ok"]
    placed = sum(1 for r in ok if r.fit != "none")
    assert placed + sum(1 for r in ok if r.fit == "none") == len(ok)
    assert {r.best_layout for r in ok if r.fit != "none"} == {
        "classic-left",
        "subject-right",
        "tall-address",
    }


# ------------------------------------------------------------------ tie-break
def test_tie_break_picks_tightest_fit_not_highest_score(
    portfolio_dir: Path, layouts: list[LayoutProfile]
) -> None:
    letter = extract(portfolio_dir / "T03-between-classic-and-tall.pdf")
    results = rank(letter, layouts)
    assert results[0].layout_id == "tall-address"  # higher score by a little
    tb = resolve_ties(letter, layouts, results)
    assert tb.ambiguous and tb.applied
    assert set(tb.tied) == {"classic-left", "tall-address"}
    assert tb.results[0].layout_id == "classic-left"
    assert len(tb.results) == len(results)
    assert "tightest fit is classic-left" in tb.note


def test_tightness_counts_unused_capacity(
    portfolio_dir: Path, layouts: list[LayoutProfile]
) -> None:
    letter = extract(portfolio_dir / "T01-between-classic-and-tall.pdf")
    by_id = {lay.id: lay for lay in layouts}
    ranked = {r.layout_id: r for r in rank(letter, layouts)}
    classic = tightness(letter, by_id["classic-left"], ranked["classic-left"])
    tall = tightness(letter, by_id["tall-address"], ranked["tall-address"])
    assert classic[0] == tall[0] == 0
    assert classic[1] < tall[1]


def test_no_tie_break_without_a_near_tie(portfolio_dir: Path, layouts: list[LayoutProfile]) -> None:
    letter = extract(portfolio_dir / "P01-classic-left.pdf")
    results = rank(letter, layouts)
    tb = resolve_ties(letter, layouts, results)
    assert not tb.ambiguous and not tb.applied and tb.note == ""
    assert tb.results == results


def test_margin_zero_turns_the_tie_break_off(
    portfolio_dir: Path, layouts: list[LayoutProfile]
) -> None:
    letter = extract(portfolio_dir / "T02-between-classic-and-tall.pdf")
    results = rank(letter, layouts)
    assert not resolve_ties(letter, layouts, results, margin=0.0).ambiguous
    assert resolve_ties(letter, layouts, results, margin=0.05).ambiguous


def test_failing_layouts_are_never_tie_candidates(
    portfolio_dir: Path, layouts: list[LayoutProfile]
) -> None:
    letter = extract(portfolio_dir / "N01-eleven-line-address.pdf")
    results = rank(letter, layouts)
    tb = resolve_ties(letter, layouts, results, margin=10.0)  # absurd margin, nothing fits
    assert not tb.ambiguous and tb.results == results


# ------------------------------------------------------------------ duplicates
def _letter_with_body(text: str) -> LetterStructure:
    block = Block(72, 300, 400, 40, [text], role="body")
    return LetterStructure("synthetic", 612, 792, 1, [block])


def test_digits_do_not_hide_a_copy() -> None:
    a = _letter_with_body(
        "Your balance of 12.34 is due on 5 March 2026 and reference 000123 applies to it today."
    )
    b = _letter_with_body(
        "Your balance of 98.76 is due on 17 March 2027 and reference 998877 applies to it today."
    )
    assert body_fingerprint(a) == body_fingerprint(b) is not None


def test_very_short_bodies_are_not_compared() -> None:
    assert body_fingerprint(_letter_with_body("Thank you.")) is None


# ------------------------------------------------------------------ CLI and reports
def test_cli_writes_csv_and_markdown(portfolio_dir: Path, tmp_path: Path, capsys) -> None:
    rc = main(["portfolio", str(portfolio_dir), "--out", str(tmp_path)])
    assert rc == EXIT_SOME_FAILED  # the scan and the corrupt file could not be matched
    captured = capsys.readouterr()
    assert "2 of 40 file(s) could not be matched" in captured.err
    with (tmp_path / "portfolio.csv").open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
        assert reader.fieldnames == CSV_COLUMNS
    assert len(rows) == 40
    by_name = {Path(r["file"]).name: r for r in rows}
    assert by_name["T01-between-classic-and-tall.pdf"]["ambiguous"] == "yes"
    assert by_name["N01-eleven-line-address.pdf"]["fit"] == "none"
    assert by_name["Z01-scanned-image-only.pdf"]["status"] == "no_text"
    report = (tmp_path / "portfolio.md").read_text(encoding="utf-8")
    for heading in (
        "## Letters per layout",
        "## No layout fits (1)",
        "## Weak fit",
        "## Ambiguous",
        "## Near-duplicate letters (1 group(s))",
        "## Unreadable or no text (2)",
    ):
        assert heading in report
    assert "## AI step" not in report  # off by default
    assert "| tall-address |" in report and "| (no layout fits) | 1 |" in report


def test_clean_folder_exits_ok(letters_dir: Path, tmp_path: Path) -> None:
    assert main(["portfolio", str(letters_dir), "--out", str(tmp_path)]) == EXIT_OK


def test_recursive_flag(letters_dir: Path, tmp_path: Path) -> None:
    root = tmp_path / "in"
    (root / "sub").mkdir(parents=True)
    shutil.copy(letters_dir / "L01-classic.pdf", root / "a.pdf")
    shutil.copy(letters_dir / "L03-subject-right.pdf", root / "sub" / "b.pdf")
    for flags, expected in (([], 1), (["-r"], 2)):
        out = tmp_path / f"out{expected}"
        assert main(["portfolio", str(root), "--out", str(out), *flags]) == EXIT_OK
        with (out / "portfolio.csv").open(encoding="utf-8-sig", newline="") as fh:
            assert len(list(csv.DictReader(fh))) == expected


def test_spreadsheet_formulas_are_defused(tmp_path: Path) -> None:
    rec = LetterRecord(Path("x.pdf"), "=HYPERLINK(1).pdf", note="+cmd")
    write_csv([rec], tmp_path / "p.csv")
    text = (tmp_path / "p.csv").read_text(encoding="utf-8-sig")
    assert "'=HYPERLINK(1).pdf" in text and "'+cmd" in text


def test_samples_portfolio_command(tmp_path: Path) -> None:
    assert main(["samples", "--portfolio", "--out", str(tmp_path)]) == EXIT_OK
    assert len(list(tmp_path.glob("*.pdf"))) == 40


def test_handles_more_than_a_hundred_pdfs(
    letters_dir: Path, tmp_path: Path, layouts: list[LayoutProfile]
) -> None:
    sources = sorted(letters_dir.glob("*.pdf"))
    for n in range(8):
        for src in sources:
            shutil.copy(src, tmp_path / f"c{n:02d}-{src.name}")
    seen: list[tuple[int, int]] = []
    recs = scan(sorted(tmp_path.glob("*.pdf")), layouts, progress=lambda i, t: seen.append((i, t)))
    total = len(sources) * 8
    assert total > 100
    assert len(recs) == total and all(r.status == "ok" for r in recs)
    assert seen[-1] == (total, total)
    groups = find_duplicate_groups(recs)
    assert sum(len(g.members) for g in groups) == total  # every copy has twins


# ------------------------------------------------------------------ the economical AI step
def test_plan_only_includes_letters_that_need_the_ai(
    records: dict[str, LetterRecord],
) -> None:
    assistant = LLMAssistant(FakeBackend())
    plan = plan_ai(records.values(), assistant)
    assert {r.path.name for r in plan.classify} == {
        "X01-notice-with-table.pdf",
        "X02-notice-with-table.pdf",
    }
    explain = {r.path.name for r in plan.explain}
    assert explain == {
        "T01-between-classic-and-tall.pdf",
        "T02-between-classic-and-tall.pdf",
        "T03-between-classic-and-tall.pdf",
        "T04-between-classic-and-tall.pdf",
        "W01-subject-too-high.pdf",
        "N01-eleven-line-address.pdf",
        "X01-notice-with-table.pdf",
        "X02-notice-with-table.pdf",
    }
    assert plan.calls == 10 < len(records) / 2
    clean = [r for r in records.values() if r.fit == "good" and not r.ambiguous]
    assert clean and all(r not in plan.classify and r not in plan.explain for r in clean)


def test_plan_respects_the_enabled_tasks(records: dict[str, LetterRecord]) -> None:
    only_explain = LLMAssistant(FakeBackend(), tasks=("explain",))
    plan = plan_ai(records.values(), only_explain)
    assert plan.classify == [] and len(plan.explain) == 8


def test_run_ai_updates_records_and_stays_within_the_estimate(
    portfolio_dir: Path, layouts: list[LayoutProfile]
) -> None:
    recs = scan(sorted(portfolio_dir.glob("*.pdf")), layouts)
    backend = FakeBackend()
    assistant = LLMAssistant(backend)
    plan = plan_ai(recs, assistant)
    assert run_ai(plan, layouts, assistant) == ""
    assert backend.calls.count("classify") == 2
    assert len(backend.calls) <= plan.calls
    notice = next(r for r in recs if r.path.name == "X01-notice-with-table.pdf")
    assert notice.ai_changes and notice.ai_explanation == "Short explanation."
    assert notice.unknown_blocks < 3  # relabeled blocks no longer count as unknown
    clean = next(r for r in recs if r.path.name == "P01-classic-left.pdf")
    assert not clean.ai_changes and clean.ai_explanation is None


def test_run_ai_stops_after_repeated_failures(
    portfolio_dir: Path, layouts: list[LayoutProfile]
) -> None:
    recs = scan(sorted(portfolio_dir.glob("*.pdf")), layouts)
    backend = FailingBackend()
    assistant = LLMAssistant(backend)
    stopped = run_ai(plan_ai(recs, assistant), layouts, assistant)
    assert "stopped after 3 failures in a row" in stopped
    assert backend.calls == 3
    assert sum(1 for r in recs if r.ai_error == "boom") == 3


def test_cli_asks_before_calling_and_needs_yes_when_not_interactive(
    portfolio_dir: Path, tmp_path: Path, capsys
) -> None:
    backend = FakeBackend()
    register_backend("fake-portfolio", lambda env: backend)
    args = ["portfolio", str(portfolio_dir), "--llm", "fake-portfolio"]
    main([*args, "--out", str(tmp_path / "no")])  # pytest stdin is not a terminal
    out = capsys.readouterr()
    assert "up to 10 model call(s)" in out.out and "needs --yes" in out.err
    assert backend.calls == []
    assert "Skipped" in (tmp_path / "no" / "portfolio.md").read_text(encoding="utf-8")

    main([*args, "--yes", "--out", str(tmp_path / "yes")])
    assert 2 <= len(backend.calls) <= 10
    report = (tmp_path / "yes" / "portfolio.md").read_text(encoding="utf-8")
    assert "## AI step" in report and "Why (AI): Short explanation." in report
    assert report.index("- Why (rules): ") < report.index("- Why (AI): ")
    assert "AI labeled block" in report


def test_ai_step_with_nothing_to_ask_makes_no_call(
    letters_dir: Path, tmp_path: Path, capsys
) -> None:
    backend = FakeBackend()
    register_backend("fake-quiet", lambda env: backend)
    clean = tmp_path / "clean"  # only the baseline letters: nothing for the AI to label or explain
    clean.mkdir()
    for name in ("L01-classic", "L03-subject-right", "L05-tall-address-8", "L15-address-right"):
        shutil.copy(letters_dir / f"{name}.pdf", clean)
    argv = ["portfolio", str(clean), "--llm", "fake-quiet", "--out", str(tmp_path / "out")]
    assert main(argv) == 0
    assert "up to 0 model call(s)" in capsys.readouterr().out
    assert backend.calls == []
