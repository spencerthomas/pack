"""Tests for BudgetObservableMiddleware."""

from __future__ import annotations

import time
from typing import Any, cast
from unittest.mock import Mock

import pytest
from langchain_core.messages import ToolMessage

from deepagents_cli.budget_observable import (
    BudgetObservableMiddleware,
    _format_seconds,
)


def _tool_message(content: str = "output") -> ToolMessage:
    return ToolMessage(content=content, tool_call_id="tc-1")


def _request(tool_name: str = "ls") -> Any:
    req = Mock()
    req.tool_call = {"name": tool_name, "args": {}, "id": "tc-1"}
    return req


# ---------------------------------------------------------------------------
# Format helper
# ---------------------------------------------------------------------------


def test_format_seconds_under_minute() -> None:
    assert _format_seconds(45) == "45s"


def test_format_seconds_exact_minute() -> None:
    assert _format_seconds(60) == "1m 00s"


def test_format_seconds_multi_minute() -> None:
    assert _format_seconds(452) == "7m 32s"


def test_format_seconds_zero_and_negative() -> None:
    assert _format_seconds(0) == "0s"
    assert _format_seconds(-5) == "0s"


# ---------------------------------------------------------------------------
# Constructor validation
# ---------------------------------------------------------------------------


def test_constructor_rejects_zero_budget() -> None:
    with pytest.raises(ValueError, match="total_budget_sec"):
        BudgetObservableMiddleware(total_budget_sec=0)


def test_constructor_rejects_negative_critical() -> None:
    with pytest.raises(ValueError, match="critical_threshold_sec"):
        BudgetObservableMiddleware(critical_threshold_sec=-1)


# ---------------------------------------------------------------------------
# Budget appending
# ---------------------------------------------------------------------------


def test_appends_normal_budget_when_plenty_remaining() -> None:
    m = BudgetObservableMiddleware(total_budget_sec=900, critical_threshold_sec=120)
    result = m._append_budget(_tool_message("file output"))
    assert isinstance(result, ToolMessage)
    text = str(result.content)
    assert "file output" in text
    assert "[budget:" in text
    assert "remaining" in text
    assert "CRITICAL" not in text


def test_appends_critical_marker_near_end() -> None:
    m = BudgetObservableMiddleware(total_budget_sec=900, critical_threshold_sec=120)
    # Simulate having used 800s already so only ~100s remain (below 120 threshold).
    m._started_at = time.monotonic() - 800
    result = m._append_budget(_tool_message("some output"))
    assert isinstance(result, ToolMessage)
    text = str(result.content)
    assert "[BUDGET CRITICAL" in text
    assert "Emit your best-known solution NOW" in text


def test_disabled_middleware_no_ops() -> None:
    m = BudgetObservableMiddleware(disabled=True)
    result = m._append_budget(_tool_message("unchanged"))
    assert isinstance(result, ToolMessage)
    assert str(result.content) == "unchanged"


def test_handles_non_tool_message_unchanged() -> None:
    m = BudgetObservableMiddleware()
    # Anything that's not a ToolMessage passes through unmodified.
    sentinel = Mock()
    result = m._append_budget(sentinel)
    assert result is sentinel


def test_wrap_tool_call_appends_budget_via_handler() -> None:
    m = BudgetObservableMiddleware(total_budget_sec=900)

    def handler(_req: Any) -> ToolMessage:
        return _tool_message("handler output")

    result = m.wrap_tool_call(_request(), handler)
    assert isinstance(result, ToolMessage)
    assert "handler output" in str(result.content)
    assert "[budget:" in str(result.content)


async def test_awrap_tool_call_appends_budget_via_async_handler() -> None:
    m = BudgetObservableMiddleware(total_budget_sec=900)

    async def handler(_req: Any) -> ToolMessage:
        return _tool_message("async output")

    result = await m.awrap_tool_call(_request(), handler)
    assert isinstance(result, ToolMessage)
    assert "async output" in str(result.content)
    assert "[budget:" in str(result.content)


def test_critical_threshold_transition_is_consistent() -> None:
    # At exactly the threshold, we should see CRITICAL (boundary inclusive).
    m = BudgetObservableMiddleware(total_budget_sec=900, critical_threshold_sec=120)
    m._started_at = time.monotonic() - 780  # exactly 120 remaining
    result = m._append_budget(_tool_message("x"))
    assert "CRITICAL" in str(result.content)
