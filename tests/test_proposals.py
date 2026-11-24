"""New-layout proposals: clustering, draft profiles and the geometry-only AI task."""

from __future__ import annotations

import csv
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from icsel.cli import EXIT_OK, main
from icsel.config import ScoringConfig
from icsel.extract import extract
from icsel.llm import LLMAssistant, LLMError, register_backend
from icsel.llm.propose import MAX_NAME, parse_proposal, plain
from icsel.portfolio import scan
from icsel.profile import LayoutProfile, load_profile
from icsel.proposals import describe_with_ai, proposal_payload, propose
from icsel.synthetic import ADDRESSES, LetterSpec, generate_portfolio, render_letter

GOOD = json.dumps({"name": "Low address", "rationale": "The address sits 1.2 inches lower."})


class ProposeBackend:
    name = "fake"
    model = "propose-1"

    def __init__(self, raw: str = GOOD) -> None:
        self.raw = raw
        self.prompts: list[str] = []

    def complete(self, system: str, prompt: str, json_schema: dict[str, Any] | None = None) -> str:
        self.prompts.append(system + "\n" + prompt)
        if self.raw == "fail":
            raise LLMError("boom")
        return self.raw


@pytest.fixture(scope="module")
def planted(tmp_path_factory: pytest.TempPathFactory, layouts: list[LayoutProfile]) -> Path:
    """Two clusters (3 + 2 letters) away from every layout, one loner, one good letter."""
    folder = tmp_path_factory.mktemp("planted")
    by_id = {lay.id: lay for lay in layouts}
    base = LetterSpec("x", "classic-left", ADDRESSES["short"], "Planted letter", paragraphs=2)

    def add(name: str, **offsets: tuple[float, float]) -> None:
        spec = replace(base, id=name, offsets=offsets)
        render_letter(spec, by_id[spec.layout], folder / f"{name}.pdf")

    for i in range(3):  # address and subject far to the right
        add(f"A{i}", address=(230.0 + 4 * i, 30.0), subject=(200.0, 60.0 + 3 * i))
    for i in range(2):  # date on the left below the address, subject very high
        add(f"B{i}", date=(-330.0, 110.0 + 5 * i), subject=(0.0, -130.0), address=(120.0, 80.0))
    add("C0", address=(150.0, -35.0), subject=(250.0, -90.0), date=(-200.0, 0.0))
    add("G0")
    return folder


def _propose(folder: Path, layouts: list[LayoutProfile], **cfg: Any) -> Any:
    records = scan(sorted(folder.glob("*.pdf")), layouts)
    return propose(records, layouts, ScoringConfig(**cfg))


# ------------------------------------------------------------------ deterministic part
def test_planted_clusters_are_found(planted: Path, layouts: list[LayoutProfile]) -> None:
    proposals, alone = _propose(planted, layouts)
    members = [[r.path.stem for r in p.members] for p in proposals]
    assert members == [["A0", "A1", "A2"], ["B0", "B1"]]
    assert [p.profile.id for p in proposals] == ["proposed-1", "proposed-2"]
    assert [r.path.stem for r in alone] == ["C0"]  # the good letter is not considered at all


def test_drafts_use_medians_and_member_maxima(planted: Path, layouts: list[LayoutProfile]) -> None:
    first = _propose(planted, layouts)[0][0]
    middle = extract(planted / "A1.pdf").region("address")
    zone = first.profile.zone("address")
    assert middle is not None and zone is not None
    assert (zone.x, zone.y) == (round(middle.x, 1), round(middle.y, 1))
    assert zone.max_lines == 3 and zone.required
    assert first.holds == 3


def test_settings_control_clustering(planted: Path, layouts: list[LayoutProfile]) -> None:
    assert _propose(planted, layouts, cluster_distance=0.0)[0] == []
    proposals, alone = _propose(planted, layouts, cluster_min_letters=1)
    assert len(proposals) == 3 and alone == []
    assert len(_propose(planted, layouts, cluster_min_letters=3)[0]) == 1


def test_proposal_ids_avoid_existing_layouts(planted: Path, layouts: list[LayoutProfile]) -> None:
    taken = replace(layouts[0], id="proposed-1")
    records = scan(sorted(planted.glob("*.pdf")), layouts)
    proposals, _ = propose(records, [*layouts, taken])
    assert [p.profile.id for p in proposals] == ["proposed-2", "proposed-3"]


def test_demo_portfolio_gives_one_proposal(tmp_path: Path, layouts: list[LayoutProfile]) -> None:
    """In the demo folder only the two notices are weak in the same way."""
    generate_portfolio(tmp_path)
    proposals, alone = _propose(tmp_path, layouts)
    assert [[r.path.stem for r in p.members] for p in proposals] == [
        ["X01-notice-with-table", "X02-notice-with-table"]
    ]
    assert {r.path.stem for r in alone} == {"N01-eleven-line-address", "W01-subject-too-high"}


def test_cli_writes_valid_profiles_and_views(planted: Path, tmp_path: Path) -> None:
    assert main(["portfolio", str(planted), "--proposals", "--out", str(tmp_path)]) == EXIT_OK
    for n in (1, 2):
        stem = tmp_path / "proposals" / f"proposed-{n}"
        assert load_profile(stem.with_suffix(".json")).id == f"proposed-{n}"
        assert stem.with_suffix(".pdf").read_bytes().startswith(b"%PDF")
        assert stem.with_suffix(".png").read_bytes().startswith(b"\x89PNG")
    report = (tmp_path / "portfolio.md").read_text(encoding="utf-8")
    assert "## Proposed layouts (2)" in report and "proposals/proposed-1.png" in report
    assert "(AI)" not in report


def test_off_by_default(planted: Path, tmp_path: Path) -> None:
    assert main(["portfolio", str(planted), "--out", str(tmp_path)]) == EXIT_OK
    assert "Proposed" not in (tmp_path / "portfolio.md").read_text(encoding="utf-8")
    assert not (tmp_path / "proposals").exists()


# ------------------------------------------------------------------ AI part
def test_no_letter_text_is_sent(planted: Path, layouts: list[LayoutProfile]) -> None:
    proposals, _ = _propose(planted, layouts)
    backend = ProposeBackend()
    assert describe_with_ai(proposals, LLMAssistant(backend, tasks=("propose",))) == ""
    assert len(backend.prompts) == 2
    for p, prompt in zip(proposals, backend.prompts, strict=True):
        assert p.ai == {"name": "Low address", "rationale": json.loads(GOOD)["rationale"],
                        "ai": True}  # fmt: skip
        for record in p.members:
            assert record.path.stem not in prompt and record.path.name not in prompt
            for block in extract(record.path).blocks:
                for line in block.lines:
                    assert line not in prompt
        allowed = {"draft", "letters_in_cluster", "letters_the_draft_holds",
                   "nearest_existing_layout", "draft_minus_nearest_in"}  # fmt: skip
        assert set(proposal_payload(p)) == allowed


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("A **bold** <b>name</b>", "A bold name"),
        ("[click](http://evil.example) here", "click here"),
        ("see https://evil.example/x now", "see now"),
        ("# Heading\n- item | cell `code`", "Heading - item cell code"),
        ("![img](x.png) ok", "img ok"),
        ("<script>alert(1)</script>", "alert(1)"),
        ("bell\x07 and‮ text", "bell and text"),
    ],
)
def test_markup_is_stripped(text: str, expected: str) -> None:
    assert plain(text) == expected


@pytest.mark.parametrize(
    ("data", "reason"),
    [
        ({"name": "x" * (MAX_NAME + 1), "rationale": "ok"}, "text too long"),
        ({"name": "ok", "rationale": "word " * 400}, "text too long"),
        ({"name": "**", "rationale": "ok"}, "empty text"),
        ({"name": "ok", "rationale": "<br>"}, "empty text"),
    ],
)
def test_unusable_text_is_dropped_and_counted(data: dict[str, str], reason: str) -> None:
    drops: list[str] = []
    assert parse_proposal(json.dumps(data), drops.append) is None
    assert drops == [reason]


@pytest.mark.parametrize("raw", ["x", "[]", '{"name": "a"}', '{"name": 1, "rationale": "b"}'])
def test_wrong_shape_is_a_failure(raw: str) -> None:
    with pytest.raises(LLMError):
        parse_proposal(raw, lambda why: None)


def test_cli_with_ai_names(planted: Path, tmp_path: Path) -> None:
    backend = ProposeBackend()
    register_backend("fake-propose", lambda env: backend)
    argv = ["portfolio", str(planted), "--llm", "fake-propose", "--ai-tasks", "propose"]
    main([*argv, "--out", str(tmp_path / "no")])
    assert backend.prompts == []  # not confirmed; the drafts are written anyway
    assert (tmp_path / "no" / "proposals" / "proposed-1.json").exists()
    assert main([*argv, "--yes", "--out", str(tmp_path / "yes")]) == EXIT_OK
    assert len(backend.prompts) == 2
    report = (tmp_path / "yes" / "portfolio.md").read_text(encoding="utf-8")
    assert "- Name (AI): Low address" in report
    saved = load_profile(tmp_path / "yes" / "proposals" / "proposed-1.json")
    assert saved.extra["ai_suggestion"]["ai"] is True and saved.name == "Proposed layout 1"
    with (tmp_path / "yes" / "portfolio.csv").open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert not any("proposed" in r["best_layout"] for r in rows)  # never changes the choice


def test_failures_stop_after_three(planted: Path, layouts: list[LayoutProfile]) -> None:
    proposals, _ = _propose(planted, layouts, cluster_min_letters=1)
    backend = ProposeBackend("fail")
    stopped = describe_with_ai([*proposals, *proposals], LLMAssistant(backend, tasks=("propose",)))
    assert "3 failures in a row" in stopped and len(backend.prompts) == 3
    capped = LLMAssistant(ProposeBackend(), tasks=("propose",), max_calls=1)
    assert "cap of 1" in describe_with_ai(proposals, capped)
