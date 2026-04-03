"""Tests for Pack middleware wrappers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from langchain_core.messages import HumanMessage

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
            "token_usage": {
                "prompt_tokens": 100,
                "completion_tokens": 50,
            },
            "model_name": "deepseek/deepseek-chat",
        }
        usage = _extract_usage(response)
        assert usage is not None
        assert usage["input_tokens"] == 100
        assert usage["output_tokens"] == 50
        assert usage["model"] == "deepseek/deepseek-chat"

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
        # Ensure message attribute also has usage_metadata for fallback path
        response.message = MagicMock()
        response.message.usage_metadata = None

        next_fn = AsyncMock(return_value=response)
        request = MagicMock()
        request.messages = [HumanMessage(content="test")]
        config: dict = {}

        await middleware.wrap_model_call(request, config, next=next_fn)
        assert tracker.total_input_tokens + tracker.total_output_tokens > 0


class TestPermissionMiddleware:
    async def test_allows_safe_tool(self, tmp_path: Path) -> None:
        pipeline = PermissionPipeline(
            rule_store=RuleStore(tmp_path / "rules.json"),
            classifier=PermissionClassifier(),
        )
        middleware = PermissionMiddleware(pipeline)

        next_fn = AsyncMock(return_value="file contents")
        config: dict = {}

        result = await middleware.wrap_tool_call("read_file", {"path": "foo.py"}, config, next=next_fn)
        assert result == "file contents"
        next_fn.assert_called_once()

    async def test_denies_dangerous_tool(self, tmp_path: Path) -> None:
        pipeline = PermissionPipeline(
            rule_store=RuleStore(tmp_path / "rules.json"),
            classifier=PermissionClassifier(),
        )
        middleware = PermissionMiddleware(pipeline)

        next_fn = AsyncMock(return_value="should not reach")
        config: dict = {}

        result = await middleware.wrap_tool_call("execute", {"command": "rm -rf /"}, config, next=next_fn)
        assert "Permission denied" in result
        next_fn.assert_not_called()

    async def test_auto_approve_bypasses_pipeline(self, tmp_path: Path) -> None:
        pipeline = PermissionPipeline(
            rule_store=RuleStore(tmp_path / "rules.json"),
            classifier=PermissionClassifier(),
        )
        middleware = PermissionMiddleware(pipeline, auto_approve=True)

        next_fn = AsyncMock(return_value="executed")
        config: dict = {}

        result = await middleware.wrap_tool_call("execute", {"command": "rm -rf /"}, config, next=next_fn)
        assert result == "executed"
        next_fn.assert_called_once()


class TestCompactionMiddleware:
    async def test_no_compaction_under_threshold(self, tmp_path: Path) -> None:
        monitor = CompactionMonitor(context_window=1_000_000)
        collapser = ContextCollapser(tmp_path)
        middleware = CompactionMiddleware(monitor, collapser)

        request = MagicMock()
        request.messages = [HumanMessage(content="short")]
        next_fn = AsyncMock(return_value="response")
        config: dict = {}

        result = await middleware.wrap_model_call(request, config, next=next_fn)
        assert result == "response"

    async def test_no_messages_passes_through(self, tmp_path: Path) -> None:
        monitor = CompactionMonitor(context_window=100)
        collapser = ContextCollapser(tmp_path)
        middleware = CompactionMiddleware(monitor, collapser)

        request = MagicMock(spec=[])
        next_fn = AsyncMock(return_value="response")
        config: dict = {}

        result = await middleware.wrap_model_call(request, config, next=next_fn)
        assert result == "response"
