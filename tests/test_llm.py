"""Optional AI step. Everything here runs offline with fake backends."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from icsel.extract import Block, LetterStructure, extract
from icsel.llm import (
    LLMAssistant,
    LLMError,
    NoOpAssistant,
    Suggestion,
    apply_suggestions,
    get_assistant,
    register_backend,
)
from icsel.llm.anthropic_backend import FALLBACK_BETA, AnthropicBackend
from icsel.llm.prompts import render_prompt
from icsel.pipeline import analyze
from icsel.profile import LayoutProfile
from icsel.synthetic import ADDRESSES, LetterSpec, render_letter


class FakeBackend:
    """Returns canned replies in order and records every request."""

    name = "fake"
    model = "fake-model-1"

    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)
        self.calls: list[dict[str, Any]] = []

    def complete(self, system: str, prompt: str, json_schema: dict[str, Any] | None = None) -> str:
        self.calls.append({"system": system, "prompt": prompt, "schema": json_schema})
        if not self.replies:
            raise LLMError("no more canned replies")
        return self.replies.pop(0)


def _letter() -> LetterStructure:
    """A page-1 structure with a heading and a placeholder date the rules could not label."""
    blocks = [
        Block(54, 40, 150, 16, ["Brightwater Utilities"], role="letterhead", bold=True),
        Block(72, 110, 200, 12, ["NOTICE OF CHANGE"], role="unknown", bold=True),  # 1
        Block(72, 126, 60, 10, ["[Date]"], role="unknown"),  # 2
        Block(
            72,
            144,
            120,
            34,
            ["Alex Sample", "42 Placeholder Lane", "Riverton ZZ 00001"],
            role="address",
        ),  # 3
        Block(72, 300, 80, 10, ["Dear Customer,"], role="salutation"),
        Block(72, 320, 400, 22, ["Body text line one", "and line two."], role="body"),
        Block(72, 360, 60, 10, ["Kind regards,"], role="closing"),
    ]
    return LetterStructure("synthetic", 612, 792, 1, blocks)


def _suggestions(*items: tuple[int, str, float]) -> str:
    return json.dumps(
        {
            "suggestions": [
                {"index": i, "role": r, "confidence": c, "reason": "test"} for i, r, c in items
            ]
        }
    )


# ------------------------------------------------------------------ defaults and registry
def test_off_by_default() -> None:
    assert isinstance(get_assistant(env={}), NoOpAssistant)
    assert isinstance(get_assistant("none", env={"ICSEL_LLM": "anthropic"}), NoOpAssistant)
    assert get_assistant(env={}).describe()["enabled"] is False


def test_unknown_backend_is_a_clear_error() -> None:
    with pytest.raises(LLMError, match="choose from"):
        get_assistant("nope", env={})


def test_env_selects_registered_backend() -> None:
    register_backend("fake-env", lambda env: FakeBackend([]))
    a = get_assistant(env={"ICSEL_LLM": "fake-env", "ICSEL_LLM_TASKS": "explain"})
    assert a.enabled and a.describe()["tasks"] == ["explain"]


# ------------------------------------------------------------------ guard rails
def test_suggestions_only_fill_gaps() -> None:
    letter = _letter()
    changes = apply_suggestions(
        letter,
        [
            Suggestion(1, "subject", 0.9, "bold heading"),
            Suggestion(2, "date", 0.8, "placeholder date"),
            Suggestion(3, "body", 0.99, "tries to override a rule-based role"),
            Suggestion(0, "address", 0.99, "not unknown"),
            Suggestion(42, "body", 0.99, "index out of range"),
        ],
        min_confidence=0.5,
    )
    assert [(c.index, c.new_role) for c in changes] == [(1, "subject"), (2, "date")]
    assert letter.blocks[1].role == "subject" and letter.blocks[1].role_source == "llm"
    assert letter.blocks[3].role == "address" and letter.blocks[3].role_source == "rules"


def test_single_roles_low_confidence_and_bad_roles() -> None:
    letter = _letter()
    changes = apply_suggestions(
        letter,
        [
            Suggestion(1, "address", 0.95, "a second address is not allowed"),
            Suggestion(2, "subject", 0.3, "too unsure"),
            Suggestion(2, "banana", 0.9, "not a role"),
        ],
        min_confidence=0.5,
    )
    assert changes == []
    assert [b.role for b in letter.blocks[1:3]] == ["unknown", "unknown"]


def test_competing_suggestions_keep_the_most_confident() -> None:
    letter = _letter()
    changes = apply_suggestions(
        letter,
        [Suggestion(1, "subject", 0.6, "a"), Suggestion(2, "subject", 0.9, "b")],
        min_confidence=0.5,
    )
    assert [(c.index, c.new_role) for c in changes] == [(2, "subject")]


# ------------------------------------------------------------------ what is sent
def test_only_unknown_text_is_sent_for_labeling() -> None:
    backend = FakeBackend([_suggestions((1, "subject", 0.9))])
    LLMAssistant(backend).classify_unknown(_letter())
    prompt = backend.calls[0]["prompt"]
    assert "NOTICE OF CHANGE" in prompt and "[Date]" in prompt
    for private in ("Alex Sample", "42 Placeholder Lane", "Dear Customer", "Body text line one"):
        assert private not in prompt
    assert backend.calls[0]["schema"] is not None


def test_no_call_when_nothing_is_unknown() -> None:
    letter = _letter()
    for b in letter.blocks:
        if b.role == "unknown":
            b.role = "body"
    backend = FakeBackend([])
    assert LLMAssistant(backend).classify_unknown(letter) == []
    assert backend.calls == []


def test_explanation_sends_no_letter_text(layouts: list[LayoutProfile]) -> None:
    backend = FakeBackend([_suggestions((1, "subject", 0.9)), "Classic fits best."])
    letter = _letter()
    analysis = analyze(letter, layouts, LLMAssistant(backend))
    explain_prompt = backend.calls[1]["prompt"]
    for b in letter.blocks:
        for line in b.lines:
            assert line not in explain_prompt
    assert analysis.llm["explanation"] == "Classic fits best."
    assert analysis.llm["role_changes"][0]["to"] == "subject"


# ------------------------------------------------------------------ failure handling
def test_bad_reply_does_not_break_matching(layouts: list[LayoutProfile]) -> None:
    backend = FakeBackend(["not json", "Still explained."])
    analysis = analyze(_letter(), layouts, LLMAssistant(backend))
    assert analysis.results  # ranking still produced
    assert analysis.llm["role_changes"] == []
    assert "not the expected JSON" in analysis.llm["errors"][0]
    assert analysis.llm["explanation"] == "Still explained."


def test_prompt_templates_render_and_can_be_overridden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    system, user = render_prompt("classify_blocks", roles="a, b", payload="{}")
    assert "unknown" in system and "{}" in user
    system, user = render_prompt("explain_fit", facts="{}")
    assert user.endswith("{}")
    (tmp_path / "explain_fit.md").write_text(
        "Be brief.\n---USER---\nFacts: $facts", encoding="utf-8"
    )
    monkeypatch.setenv("ICSEL_PROMPTS_DIR", str(tmp_path))
    assert render_prompt("explain_fit", facts="X") == ("Be brief.", "Facts: X")
    (tmp_path / "explain_fit.md").write_text("no marker", encoding="utf-8")
    with pytest.raises(LLMError, match="---USER---"):
        render_prompt("explain_fit", facts="X")


# ------------------------------------------------------------------ end to end
def _letter_without_subject_prefix(tmp_path: Path, layouts: list[LayoutProfile]) -> Path:
    """Subject in plain text without "Subject:", so the rules cannot recognise it."""
    spec = LetterSpec(
        "no-prefix",
        "subject-right",
        ADDRESSES["short"],
        "Planned maintenance next month",
        subject_prefix="",
        subject_bold=False,
    )
    layout = next(lay for lay in layouts if lay.id == "subject-right")
    return render_letter(spec, layout, tmp_path / "no-prefix.pdf")


def test_ai_labels_subject_and_improves_fit(tmp_path: Path, layouts: list[LayoutProfile]) -> None:
    pdf = _letter_without_subject_prefix(tmp_path, layouts)
    rules_only = analyze(extract(pdf), layouts)
    letter = extract(pdf)
    subject_idx = next(
        i for i, b in enumerate(letter.blocks) if b.lines[0].startswith("Planned maintenance")
    )
    assert letter.blocks[subject_idx].role == "unknown"
    backend = FakeBackend([_suggestions((subject_idx, "subject", 0.92)), "Subject box right."])
    with_ai = analyze(letter, layouts, LLMAssistant(backend))
    assert with_ai.results[0].layout_id == "subject-right"

    # Unscored when missing, the subject could not count against the wrong layouts.
    # Labeled, it does: the gap to the runner-up grows.
    def gap(results: list) -> float:
        return results[0].score - results[1].score

    assert gap(with_ai.results) > gap(rules_only.results) + 0.05


def test_cli_match_with_fake_backend(
    tmp_path: Path, layouts: list[LayoutProfile], capsys: pytest.CaptureFixture[str]
) -> None:
    from icsel.cli import main

    pdf = _letter_without_subject_prefix(tmp_path, layouts)
    letter = extract(pdf)
    idx = next(i for i, b in enumerate(letter.blocks) if b.lines[0].startswith("Planned"))
    register_backend(
        "fake-cli", lambda env: FakeBackend([_suggestions((idx, "subject", 0.9)), "Why."])
    )
    assert main(["match", str(pdf), "--out", str(tmp_path / "out"), "--llm", "fake-cli"]) == 0
    out = capsys.readouterr().out
    assert "AI step ON" in out and "-> subject" in out and "Why (AI): Why." in out
    result = json.loads((tmp_path / "out" / "no-prefix.result.json").read_text(encoding="utf-8"))
    assert result["llm"]["enabled"] and result["llm"]["role_changes"][0]["to"] == "subject"
    blocks = result["letter"]["blocks"]
    assert blocks[idx]["role_source"] == "llm"


def test_cli_without_llm_records_it_off(tmp_path: Path, letters_dir: Path) -> None:
    from icsel.cli import main

    assert main(["match", str(letters_dir / "L01-classic.pdf"), "--out", str(tmp_path)]) == 0
    result = json.loads((tmp_path / "L01-classic.result.json").read_text(encoding="utf-8"))
    assert result["llm"] == {
        "enabled": False,
        "backend": "none",
        "model": None,
        "role_changes": [],
        "explanation": None,
    }


# ------------------------------------------------------------------ Anthropic adapter, offline
def test_anthropic_backend_request_shape_with_real_sdk() -> None:
    """Drive the real SDK through a mock HTTP transport: no network, real serialization."""
    anthropic = pytest.importorskip("anthropic")
    httpx2 = pytest.importorskip("httpx2")
    seen: dict[str, Any] = {}

    def handler(request: Any) -> Any:
        seen["url"] = str(request.url)
        seen["beta"] = request.headers.get("anthropic-beta", "")
        seen["body"] = json.loads(request.content)
        reply = seen.pop("next_reply")
        return httpx2.Response(200, json=reply)

    def reply(text: str, stop: str = "end_turn") -> dict[str, Any]:
        return {
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "model": "claude-opus-5-5",
            "content": [{"type": "text", "text": text}],
            "stop_reason": stop,
            "stop_sequence": None,
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }

    client = anthropic.Anthropic(
        api_key="test-key-not-real",
        base_url="http://testserver.invalid",
        http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler)),
        max_retries=0,
    )
    backend = AnthropicBackend(client=client)
    seen["next_reply"] = reply('{"suggestions": []}')
    assert backend.complete("sys", "user", {"type": "object"}) == '{"suggestions": []}'
    body = seen["body"]
    assert seen["url"].endswith("/v1/messages?beta=true") or seen["url"].endswith("/v1/messages")
    assert body["model"] == "claude-opus-5-5"
    assert body["system"] == "sys"
    assert body["output_config"] == {
        "effort": "low",
        "format": {"type": "json_schema", "schema": {"type": "object"}},
    }
    assert body["fallbacks"] == "default" and FALLBACK_BETA in seen["beta"]
    assert "thinking" not in body  # always on for this model; sending "disabled" would be a 400

    seen["next_reply"] = reply("", stop="refusal")
    with pytest.raises(LLMError, match="declined"):
        backend.complete("sys", "user")


def test_anthropic_backend_settings_from_env() -> None:
    class Dummy:
        pass

    with pytest.raises(LLMError, match="ICSEL_LLM_EFFORT"):
        AnthropicBackend(effort="extreme", client=Dummy())
    with pytest.raises(LLMError, match="invalid ICSEL_LLM"):
        AnthropicBackend.from_env({"ICSEL_LLM_MAX_TOKENS": "lots"})
