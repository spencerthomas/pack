"""Tests for Pack middleware wrappers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from langchain_core.messages import HumanMessage, ToolMessage

from deepagents.compaction.context_collapse import ContextCollapser
from deepagents.compaction.monitor import CompactionMonitor
from deepagents.cost.tracker import CostTracker
from deepagents.middleware.pack.compaction_middleware import CompactionMiddleware
from deepagents.middleware.pack.cost_middleware import CostMiddleware, _extract_usage
from deepagents.middleware.pack.permission_middleware import PermissionMiddleware
from deepagents.permissions.classifier import PermissionClassifier
from deepagents.permissions.pipeline import PermissionPipeline
from deepagents.permissions.rules import RuleStore


class TestCostMiddleware:
    def test_extract_usage_from_token_usage(self) -> None:
        response = MagicMock()
        response.response_metadata = {
            "token_usage": {"prompt_tokens": 100, "completion_tokens": 50},
            "model_name": "deepseek/deepseek-chat",
        }
        usage = _extract_usage(response)
        assert usage is not None
        assert usage["input_tokens"] == 100
        assert usage["output_tokens"] == 50

    def test_extract_usage_returns_none_for_missing(self) -> None:
        response = MagicMock(spec=[])
        assert _extract_usage(response) is None

    async def test_tracker_records_on_call(self) -> None:
        tracker = CostTracker()
        middleware = CostMiddleware(tracker, model_name="test-model")

        response = MagicMock()
        response.response_metadata = {
            "token_usage": {"prompt_tokens": 500, "completion_tokens": 100},
            "model_name": "test-model",
        }
        response.message = MagicMock()
        response.message.usage_metadata = None

        handler = AsyncMock(return_value=response)
        request = MagicMock()

        await middleware.awrap_model_call(request, handler)
        assert tracker.total_input_tokens + tracker.total_output_tokens > 0


class TestPermissionMiddleware:
    async def test_allows_safe_tool(self, tmp_path: Path) -> None:
        pipeline = PermissionPipeline(
            rule_store=RuleStore(tmp_path / "rules.json"),
            classifier=PermissionClassifier(),
        )
        middleware = PermissionMiddleware(pipeline)

        handler = AsyncMock(return_value=ToolMessage(content="file contents", tool_call_id="1"))
        request = MagicMock()
        request.call = {"name": "read_file", "args": {"path": "foo.py"}, "id": "1"}

        result = await middleware.awrap_tool_call(request, handler)
        assert result.content == "file contents"
        handler.assert_called_once()

    async def test_denies_dangerous_tool(self, tmp_path: Path) -> None:
        pipeline = PermissionPipeline(
            rule_store=RuleStore(tmp_path / "rules.json"),
            classifier=PermissionClassifier(),
        )
        middleware = PermissionMiddleware(pipeline)

        handler = AsyncMock(return_value=ToolMessage(content="should not reach", tool_call_id="1"))
        request = MagicMock()
        request.call = {"name": "execute", "args": {"command": "rm -rf /"}, "id": "1"}

        result = await middleware.awrap_tool_call(request, handler)
        assert "Permission denied" in result.content
        handler.assert_not_called()

    async def test_auto_approve_bypasses_pipeline(self, tmp_path: Path) -> None:
        pipeline = PermissionPipeline(
            rule_store=RuleStore(tmp_path / "rules.json"),
            classifier=PermissionClassifier(),
        )
        middleware = PermissionMiddleware(pipeline, auto_approve=True)

        handler = AsyncMock(return_value=ToolMessage(content="executed", tool_call_id="1"))
        request = MagicMock()
        request.call = {"name": "execute", "args": {"command": "rm -rf /"}, "id": "1"}

        result = await middleware.awrap_tool_call(request, handler)
        assert result.content == "executed"
        handler.assert_called_once()


class TestCompactionMiddleware:
    async def test_no_compaction_under_threshold(self, tmp_path: Path) -> None:
        monitor = CompactionMonitor(context_window=1_000_000)
        collapser = ContextCollapser(tmp_path)
        middleware = CompactionMiddleware(monitor, collapser)

        request = MagicMock()
        request.state = MagicMock()
        request.state.messages = [HumanMessage(content="short")]
        handler = AsyncMock(return_value="response")

        result = await middleware.awrap_model_call(request, handler)
        assert result == "response"

    async def test_no_messages_passes_through(self, tmp_path: Path) -> None:
        monitor = CompactionMonitor(context_window=100)
        collapser = ContextCollapser(tmp_path)
        middleware = CompactionMiddleware(monitor, collapser)

        request = MagicMock(spec=[])
        handler = AsyncMock(return_value="response")

        result = await middleware.awrap_model_call(request, handler)
        assert result == "response"
