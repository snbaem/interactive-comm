"""Shared-text discovery and the 'fragments' task. Offline, scripted backend."""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any

import pytest

from icsel.cli import EXIT_SOME_FAILED, main
from icsel.config import ConfigError, ScoringConfig, config_from_dict
from icsel.evaluate import collect_truth, run_eval
from icsel.extract import Block, LetterStructure, extract
from icsel.llm import LLMAssistant, LLMError, register_backend
from icsel.llm.fragments import MAX_DESCRIPTION, MAX_NAME, parse_fragment
from icsel.portfolio import Settings, scan
from icsel.profile import LayoutProfile
from icsel.sharedtext import (
    GAP,
    TextUnit,
    common_text,
    find_shared,
    fragment_payload,
    letter_units,
    name_with_ai,
)
from icsel.synthetic import BOILERPLATE, NEAR_MISS, generate_portfolio

GOOD = json.dumps({"name": "Payment methods", "description": "Says how to pay."})
TEXT = "The quick brown fox jumps over the lazy dog near the old stone bridge at noon"


class NameBackend:
    name = "fake"
    model = "fragments-1"

    def __init__(self, raw: str = GOOD) -> None:
        self.raw = raw
        self.prompts: list[str] = []

    def complete(self, system: str, prompt: str, json_schema: dict[str, Any] | None = None) -> str:
        self.prompts.append(system + "\n" + prompt)
        if self.raw == "fail":
            raise LLMError("boom")
        return self.raw


def _unit(letter: str, text: str, place: str = "body") -> TextUnit:
    return TextUnit(letter, place, text, text)


@pytest.fixture(scope="module")
def portfolio_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("shared-portfolio")
    generate_portfolio(out)
    return out


@pytest.fixture(scope="module")
def units(portfolio_dir: Path, layouts: list[LayoutProfile]) -> list[TextUnit]:
    records = scan(sorted(portfolio_dir.glob("*.pdf")), layouts, Settings(shared_text=True))
    return [u for r in records for u in r.texts]


# ------------------------------------------------------------------ finding
def test_planted_boilerplate_is_found(units: list[TextUnit]) -> None:
    groups = find_shared(units)
    by_text = {g.text: g for g in groups}
    assert len(by_text[BOILERPLATE["privacy"]].letters) == 9
    assert len(by_text[BOILERPLATE["payment"]].letters) == 5
    assert {g.place for g in groups[:2]} == {"header", "footer"}
    assert [g.id for g in groups] == [f"F{n}" for n in range(1, len(groups) + 1)]
    assert [len(g.letters) for g in groups] == sorted(len(g.letters) for g in groups)[::-1]


def test_planted_near_miss_is_not_found(units: list[TextUnit]) -> None:
    assert any(u.text == NEAR_MISS for u in units)  # it is really there
    assert all(u.text != NEAR_MISS for g in find_shared(units) for u in g.units)


def test_the_edited_duplicate_paragraph_is_not_shared(units: list[TextUnit]) -> None:
    """D01 and D02 differ by one word in their first paragraph; the other two are identical."""
    pair = [g for g in find_shared(units) if len(g.letters) == 2]
    assert len(pair) == 2 and all(g.letters[0].endswith("D01-address-confirmation.pdf")
                                  for g in pair)  # fmt: skip


def test_digits_do_not_hide_a_match_and_places_stay_apart() -> None:
    a, b = f"{TEXT} 12", f"{TEXT} 98765"
    assert len(find_shared([_unit("x", a), _unit("y", b)])) == 1
    assert find_shared([_unit("x", a), _unit("y", b, "footer")]) == []


def test_repeats_inside_one_letter_do_not_count() -> None:
    assert find_shared([_unit("x", TEXT), _unit("x", TEXT)]) == []
    (group,) = find_shared([_unit("x", TEXT), _unit("x", TEXT), _unit("y", TEXT)])
    assert group.letters == ["x", "y"] and len(group.units) == 3


def test_settings_control_the_search() -> None:
    near = TEXT.replace("noon", "dusk")
    pair = [_unit("x", TEXT), _unit("y", near)]  # 11 of 13 shingles in common
    (group,) = find_shared(pair)
    assert group.min_similarity == round(11 / 13, 2)
    assert find_shared(pair, ScoringConfig(shared_text_threshold=0.9)) == []
    assert find_shared(pair, ScoringConfig(shared_text_min_letters=3)) == []
    assert find_shared(pair, ScoringConfig(shared_text_min_words=99)) == []


@pytest.mark.parametrize(
    "bad",
    [{"shared_text_threshold": 0}, {"shared_text_min_letters": 1}, {"shared_text_min_words": 0}],
)
def test_settings_are_validated(bad: dict[str, float]) -> None:
    with pytest.raises(ConfigError, match=next(iter(bad))):
        config_from_dict(bad)


def test_order_does_not_matter(units: list[TextUnit]) -> None:
    def view(us: list[TextUnit]) -> list[tuple[str, list[str], float]]:
        return [(g.text, g.letters, g.min_similarity) for g in find_shared(us)]

    assert view(units) == view(units[::-1])


# ------------------------------------------------------------------ benchmark
@pytest.mark.parametrize("folder", ["letters_dir", "portfolio_dir"])
def test_benchmark_recall(
    folder: str, request: pytest.FixtureRequest, layouts: list[LayoutProfile]
) -> None:
    pairs, _ = collect_truth(sorted(request.getfixturevalue(folder).glob("*.pdf")))
    m = run_eval(pairs, layouts).tally.metrics()
    assert m["boilerplate_recall"] == 1.0
    # A few near-identical random paragraphs of the sample letters are grouped as well.
    assert m["boilerplate_precision"] is not None and m["boilerplate_precision"] >= 0.9


def test_body_fields_are_in_the_letters(letters_dir: Path) -> None:
    truth = dict(collect_truth(sorted(letters_dir.glob("L0[1237]*.pdf")))[0])
    for path, entry in truth.items():
        names = {f["name"] for f in entry["fields"]}
        assert names & {"monthlyAmount", "meterReading", "workDate", "debitAmount"}, path


# ------------------------------------------------------------------ AI task
def test_only_common_wording_is_sent() -> None:
    texts = [
        "Your account 0000-0001 moved to 42 Placeholder Lane as Alex asked us to do last week.",
        "Your account 0000-0777 moved to 18 Fictional Road as Jordan asked us to do last week.",
    ]
    common = common_text(texts)
    assert common == f"Your account #-# moved to # {GAP} as {GAP} asked us to do last week."
    assert common_text([texts[0]]) == re.sub(r"\d+", "#", texts[0])


def test_address_lines_are_masked_even_when_every_letter_has_them() -> None:
    letter = LetterStructure("a.pdf", 612, 792, 1, [
        Block(54, 100, 90, 24, ["Alex Sample", "42 Placeholder Lane"], role="address"),
        Block(54, 300, 400, 12, [f"We wrote to Alex Sample about it. {TEXT}"], role="body"),
    ])  # fmt: skip
    both = [u for n in ("a", "b") for u in letter_units(letter, n)]
    (group,) = find_shared(both)
    sent = json.dumps(fragment_payload(group))
    assert "Alex" not in sent and GAP in sent and "Alex Sample" in group.text


def test_no_letter_specific_text_reaches_the_backend(
    portfolio_dir: Path, units: list[TextUnit]
) -> None:
    groups = find_shared(units)
    backend = NameBackend()
    assert name_with_ai(groups, LLMAssistant(backend, tasks=("fragments",))) == ""
    assert len(backend.prompts) == len(groups)
    shared = {u.text for g in groups for u in g.units}
    everything = "\n".join(backend.prompts)
    for path in sorted(portfolio_dir.glob("[PD]*.pdf")):
        assert path.stem not in everything
        for block in extract(path).blocks:
            if block.role == "letterhead" or block.text in shared:
                continue
            assert block.text not in everything, (path.name, block.role)


@pytest.mark.parametrize(
    ("data", "reason"),
    [
        ({"name": "x" * (MAX_NAME + 1), "description": "ok"}, "text too long"),
        ({"name": "ok", "description": "x" * (MAX_DESCRIPTION + 1)}, "text too long"),
        ({"name": "**", "description": "ok"}, "empty text"),
    ],
)
def test_unusable_replies_are_dropped_and_counted(data: dict[str, str], reason: str) -> None:
    assistant = LLMAssistant(NameBackend(json.dumps(data)), tasks=("fragments",))
    assert assistant.name_fragment({"text": "t"}) is None
    assert assistant.drops == {f"fragments: {reason}": 1}


@pytest.mark.parametrize("raw", ["x", "[]", '{"name": "a"}', '{"name": 1, "description": "b"}'])
def test_wrong_shape_is_a_failure(raw: str) -> None:
    with pytest.raises(LLMError):
        parse_fragment(raw, lambda why: None)


def test_failures_and_cap_stop_the_naming(units: list[TextUnit]) -> None:
    groups = find_shared(units)
    backend = NameBackend("fail")
    stopped = name_with_ai(groups, LLMAssistant(backend, tasks=("fragments",)))
    assert "3 failures in a row" in stopped and len(backend.prompts) == 3
    capped = LLMAssistant(NameBackend(), tasks=("fragments",), max_calls=1)
    assert "cap of 1" in name_with_ai(groups, capped)


# ------------------------------------------------------------------ command line
def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def test_cli_rules_only(portfolio_dir: Path, tmp_path: Path) -> None:
    argv = ["portfolio", str(portfolio_dir)]
    assert main([*argv, "--out", str(tmp_path / "off")]) == EXIT_SOME_FAILED  # planted bad files
    assert not (tmp_path / "off" / "shared-text.csv").exists()
    assert "Shared text" not in (tmp_path / "off" / "portfolio.md").read_text(encoding="utf-8")
    main([*argv, "--shared-text", "--out", str(tmp_path / "on")])
    rows = _rows(tmp_path / "on" / "shared-text.csv")
    assert rows[0]["place"] in ("header", "footer") and int(rows[0]["letters"]) == 36
    assert {r["ai_name"] for r in rows} == {""}
    report = (tmp_path / "on" / "portfolio.md").read_text(encoding="utf-8")
    assert "## Shared text (6 candidate(s))" in report and "(AI)" not in report
    assert "`UNVERIFIED`" in report
    off, on = (tmp_path / d / "portfolio.csv" for d in ("off", "on"))
    assert off.read_bytes() == on.read_bytes()


def test_cli_with_ai_names(portfolio_dir: Path, tmp_path: Path) -> None:
    backend = NameBackend()
    register_backend("fake-fragments", lambda env: backend)
    argv = ["portfolio", str(portfolio_dir), "--llm", "fake-fragments", "--ai-tasks", "fragments"]
    main([*argv, "--out", str(tmp_path / "no")])
    assert backend.prompts == []  # not confirmed
    main([*argv, "--yes", "--out", str(tmp_path / "yes")])
    assert len(backend.prompts) == 6
    rows = _rows(tmp_path / "yes" / "shared-text.csv")
    assert {r["ai_name"] for r in rows} == {"Payment methods"}
    report = (tmp_path / "yes" / "portfolio.md").read_text(encoding="utf-8")
    assert "- F1 (AI): Payment methods. Says how to pay." in report
