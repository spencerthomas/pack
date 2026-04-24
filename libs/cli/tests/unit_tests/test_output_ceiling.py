"""Tests for OutputCeilingMiddleware."""

from __future__ import annotations

from typing import Any
from unittest.mock import Mock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from deepagents_cli.output_ceiling import (
    OutputCeilingMiddleware,
    _count_ai_output_tokens,
    _count_interventions,
    _INTERVENTION_MARKER,
)


def _ai(content: str, usage_tokens: int | None = None) -> AIMessage:
    msg = AIMessage(content=content)
    if usage_tokens is not None:
        msg.usage_metadata = {"output_tokens": usage_tokens, "input_tokens": 0, "total_tokens": usage_tokens}
    return msg


def _state(messages: list[Any]) -> dict[str, Any]:
    return {"messages": messages}


# ---------------------------------------------------------------------------
# _count_ai_output_tokens
# ---------------------------------------------------------------------------


def test_count_from_usage_metadata() -> None:
    messages = [_ai("hi", usage_tokens=100), _ai("there", usage_tokens=200)]
    assert _count_ai_output_tokens(messages) == 300


def test_count_falls_back_to_char_length_divided_by_4() -> None:
    # No usage_metadata: 40 chars -> 10 tokens, 80 chars -> 20 tokens
    messages = [_ai("a" * 40), _ai("b" * 80)]
    assert _count_ai_output_tokens(messages) == 30


def test_count_mixes_usage_and_fallback() -> None:
    messages = [_ai("has usage", usage_tokens=500), _ai("no usage" * 10)]  # 80 chars → 20
    assert _count_ai_output_tokens(messages) == 520


def test_count_ignores_non_ai_messages() -> None:
    messages = [
        HumanMessage(content="user msg"),
        _ai("ai msg", usage_tokens=10),
        ToolMessage(content="tool output", tool_call_id="t1"),
    ]
    assert _count_ai_output_tokens(messages) == 10


def test_count_handles_list_content_blocks() -> None:
    msg = AIMessage(
        content=[
            {"type": "text", "text": "a" * 40},
            {"type": "text", "text": "b" * 40},
        ]
    )
    assert _count_ai_output_tokens([msg]) == 20  # 80 chars / 4 = 20


# ---------------------------------------------------------------------------
# Constructor validation
# ---------------------------------------------------------------------------


def test_rejects_zero_ceiling() -> None:
    with pytest.raises(ValueError, match="soft_ceiling_tokens"):
        OutputCeilingMiddleware(soft_ceiling_tokens=0)


def test_rejects_zero_interventions() -> None:
    with pytest.raises(ValueError, match="max_interventions"):
        OutputCeilingMiddleware(max_interventions=0)


def test_rejects_template_missing_marker() -> None:
    with pytest.raises(ValueError, match="marker"):
        OutputCeilingMiddleware(intervention_template="no marker here {tokens}")


# ---------------------------------------------------------------------------
# Triggering behavior
# ---------------------------------------------------------------------------


def test_under_ceiling_is_noop() -> None:
    m = OutputCeilingMiddleware(soft_ceiling_tokens=10_000)
    state = _state([_ai("short", usage_tokens=500)])
    assert m._maybe_intervene(state) is None


def test_over_ceiling_fires() -> None:
    m = OutputCeilingMiddleware(soft_ceiling_tokens=10_000)
    state = _state([_ai("huge", usage_tokens=15_000)])
    result = m._maybe_intervene(state)
    assert result is not None
    assert result["jump_to"] == "model"
    human = result["messages"][0]
    assert isinstance(human, HumanMessage)
    assert _INTERVENTION_MARKER in human.content
    assert "15,000" in human.content


def test_at_exact_ceiling_fires_boundary_inclusive() -> None:
    m = OutputCeilingMiddleware(soft_ceiling_tokens=10_000)
    state = _state([_ai("exact", usage_tokens=10_000)])
    assert m._maybe_intervene(state) is not None


def test_fires_once_then_stops() -> None:
    m = OutputCeilingMiddleware(soft_ceiling_tokens=5_000, max_interventions=1)
    # First pass: ceiling tripped, intervention injected
    state1 = _state([_ai("big", usage_tokens=10_000)])
    result1 = m._maybe_intervene(state1)
    assert result1 is not None

    # Simulate the intervention landing in state — second pass should no-op
    state2 = _state([
        _ai("big", usage_tokens=10_000),
        result1["messages"][0],
        _ai("more output", usage_tokens=5_000),  # total now 15K, still >= 5K
    ])
    assert m._maybe_intervene(state2) is None


def test_fires_up_to_max_interventions() -> None:
    m = OutputCeilingMiddleware(soft_ceiling_tokens=5_000, max_interventions=2)

    # First intervention
    state1 = _state([_ai("dump", usage_tokens=10_000)])
    r1 = m._maybe_intervene(state1)
    assert r1 is not None

    # Second intervention (after first was injected)
    state2 = _state([
        _ai("dump", usage_tokens=10_000),
        r1["messages"][0],
        _ai("more", usage_tokens=10_000),
    ])
    r2 = m._maybe_intervene(state2)
    assert r2 is not None

    # Third attempt should be blocked
    state3 = _state([
        _ai("dump", usage_tokens=10_000),
        r1["messages"][0],
        _ai("more", usage_tokens=10_000),
        r2["messages"][0],
        _ai("still", usage_tokens=10_000),
    ])
    assert m._maybe_intervene(state3) is None


def test_disabled_is_noop() -> None:
    m = OutputCeilingMiddleware(soft_ceiling_tokens=100, disabled=True)
    state = _state([_ai("anything", usage_tokens=1_000_000)])
    assert m._maybe_intervene(state) is None


def test_intervention_message_includes_token_count() -> None:
    m = OutputCeilingMiddleware(soft_ceiling_tokens=1_000)
    state = _state([_ai("x", usage_tokens=42_000)])
    result = m._maybe_intervene(state)
    assert result is not None
    assert "42,000" in result["messages"][0].content


# ---------------------------------------------------------------------------
# Integration: simulates the write-compressor failure pattern
# ---------------------------------------------------------------------------


def test_single_shot_dump_pattern_catches_at_first_big_message() -> None:
    # write-compressor in run-010 had step 2 = 62K chars, step 3 = 60K
    # restarting the same analysis. Middleware should intervene after
    # step 2 so step 3 never happens.
    m = OutputCeilingMiddleware(soft_ceiling_tokens=20_000)
    first_dump = "x" * (62_000 * 4)  # 62K tokens via fallback
    state = _state([_ai(first_dump)])
    result = m._maybe_intervene(state)
    assert result is not None
    # Agent is told to stop analyzing
    assert "Stop analyzing" in result["messages"][0].content
