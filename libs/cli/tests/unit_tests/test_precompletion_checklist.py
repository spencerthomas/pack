"""Tests for PreCompletionChecklistMiddleware."""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import Mock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from deepagents_cli.precompletion_checklist import (
    PreCompletionChecklistMiddleware,
    _CHECKLIST_MARKER,
    _count_checklist_cycles,
    _last_ai_message_declares_done,
)


def _state(messages: list[Any]) -> Any:
    return cast(Any, {"messages": messages})


def _runtime() -> Any:
    return cast(Any, Mock())


# ---------------------------------------------------------------------------
# Helper tests
# ---------------------------------------------------------------------------


def test_count_checklist_cycles_counts_only_marked_human_messages() -> None:
    messages = [
        HumanMessage(content="just a regular message"),
        HumanMessage(content=f"{_CHECKLIST_MARKER}\nVerify..."),
        AIMessage(content="sure"),
        HumanMessage(content=f"{_CHECKLIST_MARKER}\nVerify again..."),
    ]
    assert _count_checklist_cycles(messages) == 2


def test_count_checklist_cycles_zero_when_no_marker() -> None:
    messages = [
        HumanMessage(content="task description"),
        AIMessage(content="done"),
    ]
    assert _count_checklist_cycles(messages) == 0


def test_last_ai_declares_done_true_when_no_tool_calls() -> None:
    messages = [
        HumanMessage(content="task"),
        AIMessage(content="I'm done.", tool_calls=[]),
    ]
    assert _last_ai_message_declares_done(messages) is True


def test_last_ai_declares_done_false_when_pending_tool_calls() -> None:
    messages = [
        HumanMessage(content="task"),
        AIMessage(
            content="",
            tool_calls=[{"name": "execute", "args": {}, "id": "tc-1"}],
        ),
    ]
    assert _last_ai_message_declares_done(messages) is False


def test_last_ai_declares_done_false_when_no_ai_messages() -> None:
    messages = [HumanMessage(content="task")]
    assert _last_ai_message_declares_done(messages) is False


def test_last_ai_declares_done_checks_most_recent() -> None:
    # Earlier AI without tool_calls, later AI with tool_calls → still mid-work.
    messages = [
        HumanMessage(content="task"),
        AIMessage(content="done 1", tool_calls=[]),
        HumanMessage(content="also do this"),
        AIMessage(content="", tool_calls=[{"name": "ls", "args": {}, "id": "tc-1"}]),
    ]
    assert _last_ai_message_declares_done(messages) is False


# ---------------------------------------------------------------------------
# Middleware tests
# ---------------------------------------------------------------------------


def test_constructor_rejects_zero_cycles() -> None:
    with pytest.raises(ValueError, match="max_cycles"):
        PreCompletionChecklistMiddleware(max_cycles=0)


def test_constructor_rejects_template_without_marker() -> None:
    with pytest.raises(ValueError, match="marker"):
        PreCompletionChecklistMiddleware(checklist_template="no marker here")


def test_constructor_accepts_custom_template_with_marker() -> None:
    tpl = f"{_CHECKLIST_MARKER} my custom checklist"
    m = PreCompletionChecklistMiddleware(checklist_template=tpl)
    assert m.checklist_template == tpl


def test_injects_checklist_on_first_done_signal() -> None:
    m = PreCompletionChecklistMiddleware(max_cycles=1)
    messages = [
        HumanMessage(content="task"),
        AIMessage(content="I'm done.", tool_calls=[]),
    ]
    result = m.after_model(_state(messages), _runtime())
    assert result is not None
    assert result["jump_to"] == "model"
    injected = result["messages"][0]
    assert isinstance(injected, HumanMessage)
    assert _CHECKLIST_MARKER in str(injected.content)


def test_no_injection_when_agent_still_working() -> None:
    m = PreCompletionChecklistMiddleware()
    messages = [
        HumanMessage(content="task"),
        AIMessage(content="", tool_calls=[{"name": "ls", "args": {}, "id": "t"}]),
    ]
    assert m.after_model(_state(messages), _runtime()) is None


def test_allows_done_after_max_cycles_completed() -> None:
    m = PreCompletionChecklistMiddleware(max_cycles=1)
    messages = [
        HumanMessage(content="task"),
        AIMessage(content="first done"),
        HumanMessage(content=f"{_CHECKLIST_MARKER}\nverify..."),
        AIMessage(content="verified, done", tool_calls=[]),
    ]
    assert m.after_model(_state(messages), _runtime()) is None


def test_max_cycles_2_requires_two_verification_passes() -> None:
    m = PreCompletionChecklistMiddleware(max_cycles=2)
    # Only 1 cycle completed, agent declaring done again → must inject again.
    messages = [
        HumanMessage(content="task"),
        AIMessage(content="first done"),
        HumanMessage(content=f"{_CHECKLIST_MARKER}\nverify..."),
        AIMessage(content="second done", tool_calls=[]),
    ]
    result = m.after_model(_state(messages), _runtime())
    assert result is not None
    assert result["jump_to"] == "model"


def test_no_injection_when_no_messages() -> None:
    m = PreCompletionChecklistMiddleware()
    assert m.after_model(_state([]), _runtime()) is None


async def test_async_hook_matches_sync_behavior() -> None:
    m = PreCompletionChecklistMiddleware(max_cycles=1)
    messages = [
        HumanMessage(content="task"),
        AIMessage(content="done", tool_calls=[]),
    ]
    result = await m.aafter_model(_state(messages), _runtime())
    assert result is not None
    assert result["jump_to"] == "model"
