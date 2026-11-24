"""Field detection, the text opt-in, the reply cache and the call cap. Offline fake backends."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import pytest

from icsel.cli import EXIT_OK, EXIT_USAGE, main
from icsel.evaluate import collect_truth, evaluate, run_eval
from icsel.extract import Block, LetterStructure
from icsel.llm import LLMAssistant, LLMCallLimit, LLMError, ReplyCache, get_assistant
from icsel.llm import register_backend as register
from icsel.llm.fields import letter_blocks, parse_fields
from icsel.profile import LayoutProfile


def item(name: Any, text: Any, kind: Any = "variable", confidence: Any = 0.9) -> dict[str, Any]:
    return {"name": name, "text": text, "kind": kind, "confidence": confidence}


class FieldBackend:
    """Replies with fixed items, or with items derived from the blocks it was sent."""

    name = "fake"
    model = "fields-1"

    def __init__(self, items: Any = None, raw: str | None = None) -> None:
        self.items, self.raw = items, raw
        self.prompts: list[str] = []
        self.schemas: list[Any] = []

    def complete(self, system: str, prompt: str, json_schema: dict[str, Any] | None = None) -> str:
        self.prompts.append(prompt)
        self.schemas.append(json_schema)
        if self.raw is not None:
            return self.raw
        items = self.items
        if callable(items):
            start, end = prompt.index("["), prompt.rindex("]")
            items = items(json.loads(prompt[start : end + 1]))
        return json.dumps({"fields": items})


def _letter() -> LetterStructure:
    return LetterStructure(
        "synthetic",
        612,
        792,
        1,
        [
            Block(54, 40, 150, 16, ["Brightwater Utilities"], role="letterhead"),
            Block(400, 110, 60, 10, ["March 3, 2026"], role="date"),
            Block(72, 144, 120, 34, ["Alex Sample", "42 Placeholder Lane"], role="address"),
            Block(72, 320, 400, 22, ["Your account 0000-0001", "is in credit."], role="body"),
            Block(54, 760, 300, 8, ["100 Example Way"], role="footer"),
        ],
    )


def _assistant(backend: Any, **kw: Any) -> LLMAssistant:
    return LLMAssistant(backend, tasks=("fields",), send_text=("fields",), **kw)


# ------------------------------------------------------------------ opt-in and data sent
def test_fields_needs_its_own_switch() -> None:
    with pytest.raises(LLMError, match="--fields-send-text"):
        LLMAssistant(FieldBackend([]), tasks=("fields",))
    register("fake-fields-env", lambda env: FieldBackend([]))
    env = {"ICSEL_LLM_TASKS": "fields"}
    with pytest.raises(LLMError, match="ICSEL_LLM_FIELDS_SEND_TEXT"):
        get_assistant("fake-fields-env", env)
    assert get_assistant("fake-fields-env", {**env, "ICSEL_LLM_FIELDS_SEND_TEXT": "1"}).enabled
    for off in ("0", "no", ""):
        with pytest.raises(LLMError):
            get_assistant("fake-fields-env", {**env, "ICSEL_LLM_FIELDS_SEND_TEXT": off})


def test_default_tasks_are_unchanged() -> None:
    register("fake-fields-default", lambda env: FieldBackend([]))
    assert get_assistant("fake-fields-default", {}).tasks == ("classify", "explain")


def test_letterhead_and_footer_are_not_sent() -> None:
    backend = FieldBackend([])
    _assistant(backend).detect_fields(_letter())
    assert "Alex Sample 42 Placeholder Lane" in backend.prompts[0]
    assert "Brightwater" not in backend.prompts[0] and "Example Way" not in backend.prompts[0]


def test_long_letters_are_capped_at_whole_blocks() -> None:
    blocks = [Block(0, i, 10, 10, ["x" * 5000], role="body") for i in range(4)]
    assert len(letter_blocks(LetterStructure("s", 612, 792, 1, blocks))) == 2


def test_nothing_to_send_makes_no_call() -> None:
    backend = FieldBackend([])
    assert _assistant(backend).detect_fields(LetterStructure("s", 612, 792, 1, [])) == []
    assert backend.prompts == []


# ------------------------------------------------------------------ validation
@pytest.mark.parametrize(
    ("bad", "reason"),
    [
        (item("x", "not in the letter"), "text is not in the letter"),
        (item("x", "alex sample"), "text is not in the letter"),  # case matters
        (item("x", "Sample Your"), "text is not in the letter"),  # never across blocks
        (item("x", "March 3, 2026 Alex"), "text is not in the letter"),
        (item("x", "  "), "text is not in the letter"),
        (item("Not Valid", "0000-0001"), "bad name"),
        (item("x_y", "0000-0001"), "bad name"),
        (item("", "0000-0001"), "bad name"),
        (item("x", "0000-0001", "maybe"), "not the expected schema"),
        (item("x", "0000-0001", confidence=2), "not the expected schema"),
        (item("x", "0000-0001", confidence=True), "not the expected schema"),
        (item(1, "0000-0001"), "not the expected schema"),
        (item("x", ["0000-0001"]), "not the expected schema"),
        ({"name": "x"}, "not the expected schema"),
        ({**item("x", "0000-0001"), "extra": 1}, "not the expected schema"),
        ("text", "not the expected schema"),
        (item("x", "0000-0001", confidence=0.1), "low confidence"),
        (item("letterDate", "0000-0001"), "duplicate name"),
        (item("other", "March 3, 2026"), "duplicate text"),
    ],
)
def test_bad_items_are_dropped_and_counted(bad: Any, reason: str) -> None:
    good = item("letterDate", "March 3, 2026")
    assistant = _assistant(FieldBackend([good, bad]))
    found = assistant.detect_fields(_letter())
    assert [f.name for f in found] == ["letterDate"]
    assert assistant.drops == {f"fields: {reason}": 1}


def test_good_items_are_flagged_as_ai_and_fixed_spans_lose_their_name() -> None:
    backend = FieldBackend([item("accountNumber", "0000-0001"), item("x", "is in credit", "fixed")])
    found = [f.to_dict() for f in _assistant(backend).detect_fields(_letter())]
    assert all(f["ai"] is True for f in found)
    assert [(f["name"], f["kind"]) for f in found] == [("accountNumber", "variable"), ("", "fixed")]
    assert backend.schemas[0]["additionalProperties"] is False


@pytest.mark.parametrize("raw", ["nope", "[]", '{"fields": 1}', '{"fields": [], "x": 1}', "{}"])
def test_a_reply_of_the_wrong_shape_is_a_failure(raw: str) -> None:
    with pytest.raises(LLMError, match="model reply"):
        parse_fields(raw, ["a"], 0.5, lambda why: None)


# ------------------------------------------------------------------ cache and cap
def test_second_run_comes_from_the_cache(tmp_path: Path) -> None:
    backend = FieldBackend([item("letterDate", "March 3, 2026"), item("x", "nope")])
    first = _assistant(backend, cache=ReplyCache(tmp_path))
    second = _assistant(backend, cache=ReplyCache(tmp_path), max_calls=0)
    assert first.detect_fields(_letter()) == second.detect_fields(_letter())
    assert len(backend.prompts) == 1 and (second.calls, second.cache_hits) == (0, 1)
    assert second.drops == first.drops  # cached replies are validated again


def test_cache_key_covers_model_prompt_and_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = FieldBackend([])
    cache = ReplyCache(tmp_path / "c")
    ask = lambda letter: _assistant(backend, cache=cache).detect_fields(letter)  # noqa: E731
    ask(_letter())
    other = _letter()
    other.blocks[1].lines = ["March 4, 2026"]
    ask(other)
    backend.model = "fields-2"
    ask(_letter())
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "detect_fields.md").write_text("New.\n---USER---\n$blocks", encoding="utf-8")
    monkeypatch.setenv("ICSEL_PROMPTS_DIR", str(prompts))
    ask(_letter())
    assert len(backend.prompts) == 4
    ask(_letter())
    assert len(backend.prompts) == 4


def test_failures_are_never_cached(tmp_path: Path) -> None:
    backend = FieldBackend(raw="not json")
    for _ in range(2):
        with pytest.raises(LLMError):
            _assistant(backend, cache=ReplyCache(tmp_path)).detect_fields(_letter())
    assert len(backend.prompts) == 2 and list(tmp_path.iterdir()) == []


def test_damaged_cache_entry_is_asked_again(tmp_path: Path) -> None:
    backend = FieldBackend([item("letterDate", "March 3, 2026")])
    _assistant(backend, cache=ReplyCache(tmp_path)).detect_fields(_letter())
    entry = next(tmp_path.iterdir())
    for damage in ("{", '{"reply": 5}', '{"reply": "not json"}'):
        entry.write_text(damage, encoding="utf-8")
        assert len(_assistant(backend, cache=ReplyCache(tmp_path)).detect_fields(_letter())) == 1
    assert len(backend.prompts) == 4


def test_call_cap() -> None:
    assistant = _assistant(FieldBackend([]), max_calls=1)
    assistant.detect_fields(_letter())
    with pytest.raises(LLMCallLimit, match="cap of 1"):
        assistant.detect_fields(_letter())


# ------------------------------------------------------------------ benchmark
def _perfect(truth: dict[str, Any]) -> Any:
    def reply(blocks: list[str]) -> list[dict[str, Any]]:
        for entry in truth.values():
            texts = {f["text"] for f in entry["fields"]}
            lines = {ln for b in entry["blocks"] if b["role"] == "subject" for ln in b["lines"]}
            if all(any(t in b for b in blocks) for t in texts | lines):
                return [item(f["name"], f["text"]) for f in entry["fields"]]
        return []

    return reply


def test_eval_field_scores(letters_dir: Path, layouts: list[LayoutProfile]) -> None:
    pairs, _ = collect_truth(
        [letters_dir / f"{n}.pdf" for n in ("L02-classic-jitter", "L12-table-block")]
    )
    rules = run_eval(pairs, layouts).tally
    truth = {p.name: e for p, e in pairs}
    good = run_eval(pairs, layouts, "fields", _assistant(FieldBackend(_perfect(truth))))
    m = good.tally.metrics()
    assert m["field_precision"] == m["field_recall"] == m["name_accuracy"] == 1.0
    assert rules.metrics()["field_recall"] < 1.0

    # Wrong model: "Dear Customer," is fixed text. One false positive per letter, nothing found.
    wrong = run_eval(
        pairs, layouts, "fields", _assistant(FieldBackend([item("x", "Dear Customer,")]))
    )
    assert wrong.tally.fields_found == rules.fields_found + 2
    assert wrong.tally.fields_right == rules.fields_right
    doc = evaluate(pairs, layouts, _assistant(FieldBackend([item("x", "Dear Customer,")])))
    assert doc["verdicts"][0]["beats_rules"] is False and "WORSE" in doc["verdicts"][0]["text"]
    made_up = evaluate(pairs, layouts, _assistant(FieldBackend([item("x", "made up")])))
    assert made_up["runs"][1]["dropped"] == {"text is not in the letter": 2}


# ------------------------------------------------------------------ CLI
def test_match_writes_a_fields_section(letters_dir: Path, tmp_path: Path) -> None:
    backend = FieldBackend([item("letterDate", "March 3, 2026"), item("x", "made up")])
    register("fake-fields-match", lambda env: backend)
    pdf = letters_dir / "L01-classic.pdf"
    argv = ["match", str(pdf), "--out", str(tmp_path), "--llm", "fake-fields-match"]
    assert main([*argv, "--ai-tasks", "fields"]) == EXIT_USAGE  # no text opt-in
    assert backend.prompts == []
    assert main([*argv, "--ai-tasks", "fields", "--fields-send-text"]) == EXIT_OK
    doc = json.loads((tmp_path / "L01-classic.result.json").read_text(encoding="utf-8"))
    assert doc["fields"]["result"][0]["text"] == "March 3, 2026"
    assert doc["fields"]["source"] == "llm"
    assert doc["fields"]["dropped"] == {"text is not in the letter": 1}
    assert "fields" not in doc["llm"]
    assert main(["match", str(pdf), "--out", str(tmp_path)]) == EXIT_OK
    plain = json.loads((tmp_path / "L01-classic.result.json").read_text(encoding="utf-8"))
    assert "fields" not in plain


def test_portfolio_writes_fields_csv(
    letters_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def reply(blocks: list[str]) -> list[dict[str, Any]]:
        date = [item("letterDate", b) for b in blocks if b == "March 3, 2026"]
        return [*date, item("recipientName", next(b for b in blocks if "ZZ" in b).split(" ZZ")[0])]

    backend = FieldBackend(reply)
    register("fake-fields-po", lambda env: backend)
    argv = ["portfolio", str(letters_dir), "--llm", "fake-fields-po", "--yes"]
    argv += ["--ai-tasks", "fields", "--fields-send-text"]
    assert main([*argv, "--out", str(tmp_path / "a")]) == EXIT_OK
    assert "LETTER TEXT" in capsys.readouterr().out
    assert len(backend.prompts) == 17
    with (tmp_path / "a" / "fields.csv").open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    dates = [r for r in rows if r["field"] == "letterDate"]
    assert len(dates) == 14 and all(r["probably_fixed"] == "yes" for r in dates)
    assert all(r["source"] == "ai" for r in rows)
    report = (tmp_path / "a" / "portfolio.md").read_text(encoding="utf-8")
    assert "## Variable fields (AI" in report and "| letterDate | 14 |" in report
    assert "dropped by validation: none" in report
    # Same letters again: everything is in the cache.
    assert main([*argv, "--out", str(tmp_path / "b")]) == EXIT_OK
    assert len(backend.prompts) == 17
    assert main([*argv, "--no-ai-cache", "--max-calls", "4", "--out", str(tmp_path / "c")]) == 0
    assert len(backend.prompts) == 21
    assert "cap of 4" in (tmp_path / "c" / "portfolio.md").read_text(encoding="utf-8")


def test_serve_refuses_tasks_its_page_does_not_describe(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    register("fake-fields-serve", lambda env: FieldBackend([]))
    monkeypatch.setenv("ICSEL_LLM_TASKS", "fields")
    monkeypatch.setenv("ICSEL_LLM_FIELDS_SEND_TEXT", "1")
    assert main(["serve", "--llm", "fake-fields-serve"]) == EXIT_USAGE
    assert "does not support" in capsys.readouterr().err
