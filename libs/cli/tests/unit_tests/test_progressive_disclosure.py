"""Tests for ProgressiveDisclosureMiddleware."""

from __future__ import annotations

from typing import Any
from unittest.mock import Mock

from deepagents_cli.progressive_disclosure import (
    DEFAULT_DISTRACTOR_TOOLS,
    ProgressiveDisclosureMiddleware,
    _tool_name,
)


def _tool(name: str) -> Any:
    t = Mock()
    t.name = name
    return t


def _request(tools: list[Any]) -> Any:
    req = Mock()
    req.tools = tools
    override_captured: dict[str, Any] = {}

    def _override(**kwargs: Any) -> Any:
        override_captured.update(kwargs)
        new_req = Mock()
        new_req.tools = kwargs.get("tools", req.tools)
        new_req._override_captured = override_captured
        return new_req

    req.override = _override
    req._override_captured = override_captured
    return req


# ---------------------------------------------------------------------------
# _tool_name helper
# ---------------------------------------------------------------------------


def test_tool_name_from_attribute() -> None:
    t = Mock()
    t.name = "read_file"
    assert _tool_name(t) == "read_file"


def test_tool_name_from_dict() -> None:
    assert _tool_name({"name": "glob", "description": "x"}) == "glob"


def test_tool_name_missing_returns_none() -> None:
    assert _tool_name(object()) is None
    assert _tool_name({}) is None


# ---------------------------------------------------------------------------
# _should_filter gating
# ---------------------------------------------------------------------------


def test_no_hints_means_noop() -> None:
    m = ProgressiveDisclosureMiddleware()
    assert m._should_filter() is False


def test_empty_hints_means_noop() -> None:
    m = ProgressiveDisclosureMiddleware(task_hints={})
    assert m._should_filter() is False


def test_hints_with_phase_triggers_filter() -> None:
    m = ProgressiveDisclosureMiddleware(task_hints={"phase": "build"})
    assert m._should_filter() is True


def test_hints_with_domain_triggers_filter() -> None:
    m = ProgressiveDisclosureMiddleware(task_hints={"domain": "python"})
    assert m._should_filter() is True


def test_hints_with_only_complexity_does_not_trigger() -> None:
    # Complexity alone is too weak a signal to prune tools.
    m = ProgressiveDisclosureMiddleware(task_hints={"complexity": "simple"})
    assert m._should_filter() is False


def test_disabled_flag_blocks_filter() -> None:
    m = ProgressiveDisclosureMiddleware(
        task_hints={"phase": "build"}, disabled=True
    )
    assert m._should_filter() is False


# ---------------------------------------------------------------------------
# _filter_tools behavior
# ---------------------------------------------------------------------------


def test_drops_default_distractors() -> None:
    m = ProgressiveDisclosureMiddleware(task_hints={"phase": "build"})
    tools = [_tool("read_file"), _tool("fetch_url"), _tool("web_search"), _tool("execute")]
    kept, dropped = m._filter_tools(tools)
    assert {t.name for t in kept} == {"read_file", "execute"}
    assert set(dropped) == {"fetch_url", "web_search"}


def test_keeps_unknown_tools() -> None:
    m = ProgressiveDisclosureMiddleware(task_hints={"phase": "fix"})
    tools = [_tool("mcp__custom__do_something"), _tool("read_file")]
    kept, dropped = m._filter_tools(tools)
    assert len(kept) == 2
    assert dropped == []


def test_custom_distractor_list() -> None:
    m = ProgressiveDisclosureMiddleware(
        task_hints={"phase": "examine"},
        distractor_tools=frozenset({"read_file"}),
    )
    tools = [_tool("read_file"), _tool("fetch_url")]
    kept, dropped = m._filter_tools(tools)
    assert [t.name for t in kept] == ["fetch_url"]
    assert dropped == ["read_file"]


def test_dict_tools_filtered_too() -> None:
    m = ProgressiveDisclosureMiddleware(task_hints={"phase": "build"})
    tools = [
        {"name": "read_file", "description": "x"},
        {"name": "fetch_url", "description": "y"},
    ]
    kept, dropped = m._filter_tools(tools)
    assert [t["name"] for t in kept] == ["read_file"]
    assert dropped == ["fetch_url"]


# ---------------------------------------------------------------------------
# wrap_model_call integration
# ---------------------------------------------------------------------------


def test_wrap_passes_through_when_noop() -> None:
    m = ProgressiveDisclosureMiddleware()  # no hints
    call_count = 0
    received_req: Any = None

    def handler(req: Any) -> Any:
        nonlocal call_count, received_req
        call_count += 1
        received_req = req
        return "response"

    req = _request([_tool("read_file"), _tool("fetch_url")])
    result = m.wrap_model_call(req, handler)
    assert result == "response"
    assert call_count == 1
    # Request passed through unchanged — no override called
    assert received_req is req


def test_wrap_filters_when_hints_present() -> None:
    m = ProgressiveDisclosureMiddleware(task_hints={"phase": "build", "domain": "python"})
    received_tools: list[Any] = []

    def handler(req: Any) -> Any:
        received_tools.extend(req.tools)
        return "response"

    req = _request([_tool("read_file"), _tool("web_search"), _tool("execute")])
    result = m.wrap_model_call(req, handler)
    assert result == "response"
    received_names = {t.name for t in received_tools}
    assert received_names == {"read_file", "execute"}


def test_wrap_skips_override_when_no_distractors() -> None:
    m = ProgressiveDisclosureMiddleware(task_hints={"phase": "fix"})
    received_reqs: list[Any] = []

    def handler(req: Any) -> Any:
        received_reqs.append(req)
        return "ok"

    req = _request([_tool("read_file"), _tool("edit_file"), _tool("execute")])
    m.wrap_model_call(req, handler)
    # Same request object (not overridden) because no filtering needed
    assert received_reqs[0] is req


async def test_async_wrap_filters() -> None:
    m = ProgressiveDisclosureMiddleware(task_hints={"phase": "build"})
    received_tools: list[Any] = []

    async def handler(req: Any) -> Any:
        received_tools.extend(req.tools)
        return "async-response"

    req = _request([_tool("read_file"), _tool("fetch_url")])
    result = await m.awrap_model_call(req, handler)
    assert result == "async-response"
    assert [t.name for t in received_tools] == ["read_file"]


# ---------------------------------------------------------------------------
# Default distractor set content
# ---------------------------------------------------------------------------


def test_default_distractors_include_external_research() -> None:
    assert "fetch_url" in DEFAULT_DISTRACTOR_TOOLS
    assert "web_search" in DEFAULT_DISTRACTOR_TOOLS


def test_default_distractors_preserve_core_tools() -> None:
    # Defensive: the default must NEVER include core coding tools.
    for core in ("read_file", "write_file", "edit_file", "execute", "glob", "grep"):
        assert core not in DEFAULT_DISTRACTOR_TOOLS
