"""Tests for ReviewerMiddleware (Phase C.3)."""

from __future__ import annotations

from typing import Any
from unittest.mock import Mock

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from deepagents_cli.policy import TaskPolicy
from deepagents_cli.reviewer import ReviewVerdict, ReviewerSubAgent
from deepagents_cli.reviewer_middleware import (
    ReviewerMiddleware,
    _count_reviews,
    _extract_task_instruction,
    _last_ai_declares_done,
    _recent_agent_messages,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _state(messages: list[Any]) -> dict[str, Any]:
    return {"messages": messages}


def _ai(content: str, tool_calls: list[dict[str, Any]] | None = None) -> AIMessage:
    msg = AIMessage(content=content)
    if tool_calls:
        msg.tool_calls = tool_calls
    return msg


def _stub_reviewer(verdict: ReviewVerdict) -> ReviewerSubAgent:
    """Build a ReviewerSubAgent whose review() always returns ``verdict``."""
    reviewer = Mock(spec=ReviewerSubAgent)
    reviewer.review.return_value = verdict

    async def areview(**_kwargs: Any) -> ReviewVerdict:
        return verdict

    reviewer.areview = areview
    return reviewer


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def test_count_reviews_counts_verdict_markers() -> None:
    msgs = [
        HumanMessage(content="task"),
        AIMessage(content="did it"),
        HumanMessage(content="[REVIEWER VERDICT: REQUEST_CHANGES]\n\nfoo"),
        AIMessage(content="fixed"),
        HumanMessage(content="[REVIEWER VERDICT: APPROVE]\n\nok"),
    ]
    assert _count_reviews(msgs) == 2


def test_count_reviews_ignores_non_verdict_humans() -> None:
    msgs = [
        HumanMessage(content="original task"),
        AIMessage(content="did it"),
    ]
    assert _count_reviews(msgs) == 0


def test_last_ai_declares_done_with_no_tool_calls() -> None:
    msgs = [HumanMessage(content="x"), _ai("final")]
    assert _last_ai_declares_done(msgs) is True


def test_last_ai_not_done_when_tool_calls_present() -> None:
    msgs = [
        HumanMessage(content="x"),
        _ai("thinking", tool_calls=[{"name": "read_file", "args": {}, "id": "1"}]),
    ]
    assert _last_ai_declares_done(msgs) is False


def test_last_ai_ignores_trailing_human_messages() -> None:
    # If the conversation ends with a human message, we look back for
    # the most recent AIMessage to determine done-ness.
    msgs = [_ai("final"), HumanMessage(content="hi")]
    assert _last_ai_declares_done(msgs) is True


def test_extract_task_instruction_returns_first_human() -> None:
    msgs = [
        HumanMessage(content="Fix the parser bug"),
        AIMessage(content="ok"),
        HumanMessage(content="[REVIEWER VERDICT: REQUEST_CHANGES]"),
    ]
    assert _extract_task_instruction(msgs) == "Fix the parser bug"


def test_extract_task_instruction_skips_verdict_injections() -> None:
    msgs = [
        HumanMessage(content="[REVIEWER VERDICT: BLOCK]\nnope"),
        HumanMessage(content="real task"),
    ]
    assert _extract_task_instruction(msgs) == "real task"


def test_extract_task_instruction_empty_fallback() -> None:
    assert _extract_task_instruction([]) == ""


def test_recent_agent_messages_returns_tail() -> None:
    msgs = [AIMessage(content=str(i)) for i in range(10)]
    tail = _recent_agent_messages(msgs, n=3)
    assert len(tail) == 3
    assert tail[-1].content == "9"


def test_recent_agent_messages_empty() -> None:
    assert _recent_agent_messages([]) == []


# ---------------------------------------------------------------------------
# Constructor validation
# ---------------------------------------------------------------------------


def test_rejects_zero_max_reviews() -> None:
    with pytest.raises(ValueError, match="max_reviews"):
        ReviewerMiddleware(
            reviewer=Mock(spec=ReviewerSubAgent),
            max_reviews=0,
        )


# ---------------------------------------------------------------------------
# Activation gating
# ---------------------------------------------------------------------------


def test_no_policy_means_noop() -> None:
    reviewer = _stub_reviewer(ReviewVerdict(status="block", summary="x"))
    m = ReviewerMiddleware(reviewer=reviewer, policy=None)
    state = _state([_ai("final")])
    assert m.after_model(state, Mock()) is None
    reviewer.review.assert_not_called()


def test_policy_without_require_reviewer_noops() -> None:
    reviewer = _stub_reviewer(ReviewVerdict(status="block", summary="x"))
    policy = TaskPolicy(task_type="docs", require_reviewer=False)
    m = ReviewerMiddleware(reviewer=reviewer, policy=policy)
    state = _state([_ai("final")])
    assert m.after_model(state, Mock()) is None
    reviewer.review.assert_not_called()


def test_disabled_flag_noops() -> None:
    reviewer = _stub_reviewer(ReviewVerdict(status="request_changes", summary="x"))
    policy = TaskPolicy(task_type="feature", require_reviewer=True)
    m = ReviewerMiddleware(reviewer=reviewer, policy=policy, disabled=True)
    state = _state([_ai("final")])
    assert m.after_model(state, Mock()) is None


def test_not_yet_done_does_not_invoke_reviewer() -> None:
    reviewer = _stub_reviewer(ReviewVerdict(status="approve", summary="x"))
    policy = TaskPolicy(task_type="feature", require_reviewer=True)
    m = ReviewerMiddleware(reviewer=reviewer, policy=policy)

    # Agent is still mid-work: last AI has tool_calls
    state = _state([
        HumanMessage(content="task"),
        _ai("planning", tool_calls=[{"name": "read_file", "args": {}, "id": "1"}]),
    ])
    assert m.after_model(state, Mock()) is None
    reviewer.review.assert_not_called()


# ---------------------------------------------------------------------------
# Verdict branching
# ---------------------------------------------------------------------------


def test_approve_verdict_returns_none() -> None:
    verdict = ReviewVerdict(status="approve", summary="lgtm")
    reviewer = _stub_reviewer(verdict)
    policy = TaskPolicy(task_type="feature", require_reviewer=True)
    m = ReviewerMiddleware(reviewer=reviewer, policy=policy)
    state = _state([
        HumanMessage(content="task"),
        _ai("final"),
    ])
    result = m.after_model(state, Mock())
    assert result is None
    reviewer.review.assert_called_once()


def test_request_changes_injects_feedback_and_jumps() -> None:
    verdict = ReviewVerdict(
        status="request_changes",
        summary="tests missing",
        concerns=("no tests",),
        required_fixes=("add pytest",),
    )
    reviewer = _stub_reviewer(verdict)
    policy = TaskPolicy(task_type="feature", require_reviewer=True)
    m = ReviewerMiddleware(reviewer=reviewer, policy=policy)
    state = _state([
        HumanMessage(content="task"),
        _ai("final"),
    ])
    result = m.after_model(state, Mock())
    assert result is not None
    assert result["jump_to"] == "model"
    injected = result["messages"][0]
    assert isinstance(injected, HumanMessage)
    assert "REQUEST_CHANGES" in str(injected.content)
    assert "no tests" in str(injected.content)
    assert "add pytest" in str(injected.content)


def test_block_verdict_also_injects_and_jumps() -> None:
    verdict = ReviewVerdict(status="block", summary="off-track")
    reviewer = _stub_reviewer(verdict)
    policy = TaskPolicy(task_type="feature", require_reviewer=True)
    m = ReviewerMiddleware(reviewer=reviewer, policy=policy)
    state = _state([
        HumanMessage(content="task"),
        _ai("final"),
    ])
    result = m.after_model(state, Mock())
    assert result is not None
    assert "BLOCK" in str(result["messages"][0].content)


# ---------------------------------------------------------------------------
# max_reviews cap
# ---------------------------------------------------------------------------


def test_max_reviews_enforces_termination() -> None:
    verdict = ReviewVerdict(status="request_changes", summary="still bad")
    reviewer = _stub_reviewer(verdict)
    policy = TaskPolicy(task_type="feature", require_reviewer=True)
    m = ReviewerMiddleware(reviewer=reviewer, policy=policy, max_reviews=2)

    # Simulate two prior verdicts already in the conversation
    state = _state([
        HumanMessage(content="task"),
        _ai("first try"),
        HumanMessage(content="[REVIEWER VERDICT: REQUEST_CHANGES]\n\n1"),
        _ai("second try"),
        HumanMessage(content="[REVIEWER VERDICT: REQUEST_CHANGES]\n\n2"),
        _ai("third try"),
    ])
    # At max_reviews, the reviewer is not called and termination is allowed
    result = m.after_model(state, Mock())
    assert result is None
    reviewer.review.assert_not_called()


def test_first_pass_invokes_when_under_cap() -> None:
    verdict = ReviewVerdict(status="approve", summary="ok")
    reviewer = _stub_reviewer(verdict)
    policy = TaskPolicy(task_type="feature", require_reviewer=True)
    m = ReviewerMiddleware(reviewer=reviewer, policy=policy, max_reviews=2)

    # No prior verdicts
    state = _state([
        HumanMessage(content="task"),
        _ai("final"),
    ])
    m.after_model(state, Mock())
    reviewer.review.assert_called_once()


# ---------------------------------------------------------------------------
# Async path
# ---------------------------------------------------------------------------


async def test_async_after_model_matches_sync_behavior() -> None:
    verdict = ReviewVerdict(status="request_changes", summary="fix it")
    reviewer = _stub_reviewer(verdict)
    policy = TaskPolicy(task_type="feature", require_reviewer=True)
    m = ReviewerMiddleware(reviewer=reviewer, policy=policy)

    state = _state([HumanMessage(content="task"), _ai("done")])
    result = await m.aafter_model(state, Mock())
    assert result is not None
    assert "REQUEST_CHANGES" in str(result["messages"][0].content)


# ---------------------------------------------------------------------------
# Evidence extraction — PR 5
# ---------------------------------------------------------------------------


def _ai_with_tool_calls(content: str, tool_calls: list[dict[str, Any]]) -> AIMessage:
    msg = AIMessage(content=content)
    msg.tool_calls = tool_calls
    return msg


def test_evidence_empty_when_no_writes() -> None:
    reviewer = _stub_reviewer(ReviewVerdict(status="approve", summary="ok"))
    m = ReviewerMiddleware(
        reviewer=reviewer,
        policy=TaskPolicy(task_type="feature", require_reviewer=True),
    )
    evidence = m._assemble_evidence(
        [HumanMessage(content="task"), _ai("just thinking")]
    )
    assert evidence == {}


def test_evidence_includes_diff_summary_from_write_calls() -> None:
    reviewer = _stub_reviewer(ReviewVerdict(status="approve", summary="ok"))
    m = ReviewerMiddleware(
        reviewer=reviewer,
        policy=TaskPolicy(task_type="feature", require_reviewer=True),
    )
    msgs = [
        HumanMessage(content="task"),
        _ai_with_tool_calls(
            "writing",
            [
                {"name": "write_file", "args": {"path": "/app/a.py", "content": "x"}, "id": "1"},
                {"name": "edit_file", "args": {"path": "/app/a.py", "old_string": "x", "new_string": "y"}, "id": "2"},
                {"name": "write_file", "args": {"path": "/app/b.py", "content": "z"}, "id": "3"},
                {"name": "read_file", "args": {"path": "/app/c.py"}, "id": "4"},
            ],
        ),
        _ai("done"),
    ]
    evidence = m._assemble_evidence(msgs)
    assert "diff_summary" in evidence
    assert "`/app/a.py` (2 writes)" in evidence["diff_summary"]
    assert "`/app/b.py` (1 write)" in evidence["diff_summary"]
    # read_file is not a write — excluded
    assert "/app/c.py" not in evidence["diff_summary"]


def test_reviewer_receives_evidence_on_invocation() -> None:
    verdict = ReviewVerdict(status="approve", summary="lgtm")
    reviewer = _stub_reviewer(verdict)
    policy = TaskPolicy(task_type="feature", require_reviewer=True)
    m = ReviewerMiddleware(reviewer=reviewer, policy=policy)

    state = _state(
        [
            HumanMessage(content="task"),
            _ai_with_tool_calls(
                "writing",
                [{"name": "write_file", "args": {"path": "/app/a.py", "content": "x"}, "id": "1"}],
            ),
            _ai("done"),
        ]
    )
    m.after_model(state, Mock())
    # Confirm the reviewer.review kwargs include an evidence dict with
    # diff_summary present. Mock captures kwargs via call_args_list.
    reviewer.review.assert_called_once()
    _args, kwargs = reviewer.review.call_args
    assert "evidence" in kwargs
    assert "diff_summary" in kwargs["evidence"]
    assert "/app/a.py" in kwargs["evidence"]["diff_summary"]


def test_evidence_diff_truncates_at_max_files() -> None:
    from deepagents_cli.reviewer_middleware import _extract_diff_summary

    tool_calls = [
        {"name": "write_file", "args": {"path": f"/app/f{i}.py", "content": "x"}, "id": str(i)}
        for i in range(30)
    ]
    msg = _ai_with_tool_calls("burst", tool_calls)
    summary = _extract_diff_summary([msg], max_files=5)
    assert "f0.py" in summary
    assert "f4.py" in summary
    assert "25 more file(s) not listed" in summary
