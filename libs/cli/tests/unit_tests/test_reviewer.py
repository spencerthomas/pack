"""Tests for ReviewerSubAgent + ReviewVerdict (Phase C.1 + C.2)."""

from __future__ import annotations

from typing import Any
from unittest.mock import Mock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from deepagents_cli.reviewer import (
    REVIEWER_SYSTEM_PROMPT,
    ReviewerSubAgent,
    ReviewVerdict,
    VERDICT_STATUSES,
    _extract_json,
    _extract_response_text,
    _render_trajectory,
    parse_verdict,
)


# ---------------------------------------------------------------------------
# ReviewVerdict validation + helpers
# ---------------------------------------------------------------------------


def test_verdict_rejects_unknown_status() -> None:
    with pytest.raises(ValueError, match="status"):
        ReviewVerdict(status="wat", summary="x")


def test_verdict_accepts_all_known_statuses() -> None:
    for status in VERDICT_STATUSES:
        v = ReviewVerdict(status=status, summary="s")
        assert v.status == status


def test_feedback_text_includes_status_banner() -> None:
    v = ReviewVerdict(status="request_changes", summary="needs work")
    text = v.as_feedback_text()
    assert "REQUEST_CHANGES" in text
    assert "needs work" in text


def test_feedback_text_lists_concerns_and_fixes() -> None:
    v = ReviewVerdict(
        status="request_changes",
        summary="s",
        concerns=("tests missing", "no error handling"),
        required_fixes=("add pytest test", "wrap in try/except"),
    )
    text = v.as_feedback_text()
    assert "- tests missing" in text
    assert "- no error handling" in text
    assert "- add pytest test" in text
    assert "- wrap in try/except" in text


def test_feedback_text_omits_empty_sections() -> None:
    v = ReviewVerdict(status="approve", summary="lgtm")
    text = v.as_feedback_text()
    assert "Concerns:" not in text
    assert "Required fixes" not in text


# ---------------------------------------------------------------------------
# parse_verdict
# ---------------------------------------------------------------------------


_FENCED_APPROVE = """\
Here's my review:

```json
{
  "status": "approve",
  "summary": "Work looks good.",
  "concerns": [],
  "required_fixes": []
}
```
"""


def test_parse_fenced_json_approve() -> None:
    v = parse_verdict(_FENCED_APPROVE)
    assert v.status == "approve"
    assert v.summary == "Work looks good."
    assert v.concerns == ()
    assert v.required_fixes == ()


def test_parse_fenced_json_request_changes() -> None:
    raw = """```json
{"status":"request_changes","summary":"missing tests","concerns":["a","b"],"required_fixes":["c"]}
```"""
    v = parse_verdict(raw)
    assert v.status == "request_changes"
    assert v.concerns == ("a", "b")
    assert v.required_fixes == ("c",)


def test_parse_naked_json_without_fence() -> None:
    raw = 'Short verdict: {"status": "block", "summary": "off-track"}'
    v = parse_verdict(raw)
    assert v.status == "block"
    assert v.summary == "off-track"


def test_parse_no_json_returns_block() -> None:
    v = parse_verdict("I approve the changes, they look fine.")
    assert v.status == "block"
    assert "did not contain" in v.summary.lower()


def test_parse_malformed_json_returns_block() -> None:
    v = parse_verdict("```json\n{\"status\": \"approve\", bogus\n```")
    assert v.status == "block"


def test_parse_unknown_status_returns_block() -> None:
    raw = '```json\n{"status": "maybe", "summary": "idk"}\n```'
    v = parse_verdict(raw)
    assert v.status == "block"
    assert "unknown status" in v.summary.lower()


def test_parse_non_object_json_returns_block() -> None:
    raw = "```json\n[1, 2, 3]\n```"
    v = parse_verdict(raw)
    assert v.status == "block"


def test_parse_filters_empty_concern_entries() -> None:
    raw = """```json
{"status":"approve","summary":"ok","concerns":["","a",null],"required_fixes":[]}
```"""
    v = parse_verdict(raw)
    # Empty string and None filtered out; "null" would be a string if
    # quoted. Here the None falls through str() to "None" which is
    # truthy, so we get 2 entries.
    assert "" not in v.concerns


# ---------------------------------------------------------------------------
# _extract_json edge cases
# ---------------------------------------------------------------------------


def test_extract_prefers_fenced_block() -> None:
    raw = "{\"fake\": true} prose ```json\n{\"real\": true}\n```"
    assert _extract_json(raw) == '{"real": true}'


def test_extract_handles_multiline_fenced_block() -> None:
    raw = "```json\n{\n  \"status\": \"approve\"\n}\n```"
    extracted = _extract_json(raw)
    assert extracted is not None
    assert "approve" in extracted


def test_extract_returns_none_when_nothing_matches() -> None:
    assert _extract_json("no json at all") is None


# ---------------------------------------------------------------------------
# _render_trajectory
# ---------------------------------------------------------------------------


def test_render_labels_roles() -> None:
    msgs = [
        HumanMessage(content="ask"),
        AIMessage(content="respond"),
    ]
    text = _render_trajectory(msgs)
    assert "[user]" in text
    assert "[agent]" in text


def test_render_handles_list_content_blocks() -> None:
    msg = AIMessage(content=[{"type": "text", "text": "hello"}, {"type": "text", "text": "world"}])
    text = _render_trajectory([msg])
    assert "hello" in text
    assert "world" in text


def test_render_truncates_long_trajectories() -> None:
    # 30 messages of 1000 chars each >> 12k cap → truncation marker appears
    msgs = [AIMessage(content="x" * 1000) for _ in range(30)]
    text = _render_trajectory(msgs, max_chars=5000)
    assert "truncated" in text


# ---------------------------------------------------------------------------
# _extract_response_text
# ---------------------------------------------------------------------------


def test_extract_text_from_string_content() -> None:
    msg = AIMessage(content="plain string")
    assert _extract_response_text(msg) == "plain string"


def test_extract_text_from_list_content() -> None:
    msg = AIMessage(content=[{"type": "text", "text": "a"}, {"type": "text", "text": "b"}])
    assert _extract_response_text(msg) == "a\nb"


# ---------------------------------------------------------------------------
# ReviewerSubAgent integration with mocked model
# ---------------------------------------------------------------------------


def _mock_model(response_text: str) -> Any:
    model = Mock()
    model.invoke.return_value = AIMessage(content=response_text)

    async def ainvoke(_messages: Any) -> AIMessage:
        return AIMessage(content=response_text)

    model.ainvoke = ainvoke
    return model


def test_review_returns_parsed_verdict() -> None:
    model = _mock_model(_FENCED_APPROVE)
    reviewer = ReviewerSubAgent(model=model)
    verdict = reviewer.review(
        task_instruction="Fix the parser bug",
        main_agent_messages=[AIMessage(content="I fixed it.")],
    )
    assert verdict.status == "approve"


def test_review_passes_system_and_user_messages() -> None:
    model = _mock_model(_FENCED_APPROVE)
    reviewer = ReviewerSubAgent(model=model)
    reviewer.review(
        task_instruction="task",
        main_agent_messages=[AIMessage(content="final")],
    )
    model.invoke.assert_called_once()
    args, _kwargs = model.invoke.call_args
    msgs = args[0]
    assert isinstance(msgs[0], SystemMessage)
    assert msgs[0].content == REVIEWER_SYSTEM_PROMPT
    assert isinstance(msgs[1], HumanMessage)
    assert "task" in str(msgs[1].content)
    assert "final" in str(msgs[1].content)


def test_review_instruction_prefix_is_prepended() -> None:
    model = _mock_model(_FENCED_APPROVE)
    reviewer = ReviewerSubAgent(
        model=model,
        instruction_prefix="This is a security-fix task.",
    )
    reviewer.review(
        task_instruction="Replace broken crypto",
        main_agent_messages=[AIMessage(content="done")],
    )
    args, _kwargs = model.invoke.call_args
    user_content = str(args[0][1].content)
    assert "This is a security-fix task." in user_content


def test_review_survives_model_exception() -> None:
    model = Mock()
    model.invoke.side_effect = RuntimeError("network down")
    reviewer = ReviewerSubAgent(model=model)
    verdict = reviewer.review(
        task_instruction="x",
        main_agent_messages=[AIMessage(content="y")],
    )
    assert verdict.status == "block"
    assert "network down" in verdict.summary


async def test_async_review_works() -> None:
    model = _mock_model(_FENCED_APPROVE)
    reviewer = ReviewerSubAgent(model=model)
    verdict = await reviewer.areview(
        task_instruction="Fix the parser",
        main_agent_messages=[AIMessage(content="done")],
    )
    assert verdict.status == "approve"


def test_review_with_custom_system_prompt() -> None:
    model = _mock_model(_FENCED_APPROVE)
    reviewer = ReviewerSubAgent(
        model=model,
        system_prompt="CUSTOM_PROMPT",
    )
    reviewer.review(
        task_instruction="x",
        main_agent_messages=[AIMessage(content="y")],
    )
    args, _kwargs = model.invoke.call_args
    assert args[0][0].content == "CUSTOM_PROMPT"


# ---------------------------------------------------------------------------
# Evidence rendering (PR 5)
# ---------------------------------------------------------------------------


def test_review_without_evidence_omits_evidence_section() -> None:
    model = _mock_model(_FENCED_APPROVE)
    reviewer = ReviewerSubAgent(model=model)
    reviewer.review(
        task_instruction="task",
        main_agent_messages=[AIMessage(content="final")],
    )
    args, _kwargs = model.invoke.call_args
    user_content = str(args[0][1].content)
    # Nothing shaped like an evidence H2 header shows up
    assert "## Diff Summary" not in user_content
    assert "## Test Output" not in user_content


def test_review_with_evidence_renders_each_section() -> None:
    model = _mock_model(_FENCED_APPROVE)
    reviewer = ReviewerSubAgent(model=model)
    reviewer.review(
        task_instruction="task",
        main_agent_messages=[AIMessage(content="final")],
        evidence={
            "diff_summary": "- `/app/a.py` (2 writes)",
            "test_output": "3 passed, 1 failed",
        },
    )
    args, _kwargs = model.invoke.call_args
    user_content = str(args[0][1].content)
    assert "## Diff Summary" in user_content
    assert "`/app/a.py`" in user_content
    assert "## Test Output" in user_content
    assert "1 failed" in user_content


def test_review_empty_evidence_values_are_dropped() -> None:
    model = _mock_model(_FENCED_APPROVE)
    reviewer = ReviewerSubAgent(model=model)
    reviewer.review(
        task_instruction="task",
        main_agent_messages=[AIMessage(content="final")],
        evidence={"diff_summary": "- `/app/a.py`", "test_output": "   "},
    )
    args, _kwargs = model.invoke.call_args
    user_content = str(args[0][1].content)
    assert "## Diff Summary" in user_content
    # Empty test_output is dropped so no header renders
    assert "## Test Output" not in user_content


def test_review_evidence_sections_ordered_by_sorted_key() -> None:
    model = _mock_model(_FENCED_APPROVE)
    reviewer = ReviewerSubAgent(model=model)
    reviewer.review(
        task_instruction="task",
        main_agent_messages=[AIMessage(content="final")],
        evidence={
            "test_output": "all good",
            "arch_lint_output": "clean",
            "diff_summary": "- `/a`",
        },
    )
    args, _kwargs = model.invoke.call_args
    user_content = str(args[0][1].content)
    # Alphabetical sort: arch_lint < diff < test
    idx_arch = user_content.index("Arch Lint Output")
    idx_diff = user_content.index("Diff Summary")
    idx_test = user_content.index("Test Output")
    assert idx_arch < idx_diff < idx_test


async def test_async_review_passes_evidence_through() -> None:
    model = _mock_model(_FENCED_APPROVE)
    reviewer = ReviewerSubAgent(model=model)
    # Record what the async model sees
    captured: dict[str, Any] = {}

    async def capture_ainvoke(messages: Any) -> AIMessage:
        captured["messages"] = messages
        return AIMessage(content=_FENCED_APPROVE)

    model.ainvoke = capture_ainvoke
    await reviewer.areview(
        task_instruction="async task",
        main_agent_messages=[AIMessage(content="final")],
        evidence={"diff_summary": "- `/a.py`"},
    )
    user_content = str(captured["messages"][1].content)
    assert "## Diff Summary" in user_content
    assert "`/a.py`" in user_content
