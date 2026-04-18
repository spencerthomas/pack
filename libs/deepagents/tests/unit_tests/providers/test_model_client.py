"""Unit tests for PackModelClient."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage

from deepagents.cost.tracker import CostTracker
from deepagents.providers.model_client import CompletionResult, PackModelClient


def _make_ai_message(
    content: str = "Hello, world!",
    usage: dict[str, int] | None = None,
) -> AIMessage:
    """Create an AIMessage with optional usage metadata."""
    kwargs: dict = {"content": content}
    if usage is not None:
        kwargs["usage_metadata"] = usage
    return AIMessage(**kwargs)


def _make_mock_model(
    response: AIMessage | None = None,
    side_effect: Exception | list | None = None,
) -> MagicMock:
    """Create a mock BaseChatModel with an async ainvoke."""
    model = MagicMock()
    model.model_name = "test-model"
    if side_effect is not None:
        model.ainvoke = AsyncMock(side_effect=side_effect)
    else:
        if response is None:
            response = _make_ai_message()
        model.ainvoke = AsyncMock(return_value=response)
    return model


class TestSuccessfulCompletion:
    """Successful completion returns CompletionResult with content."""

    @pytest.mark.asyncio
    async def test_returns_completion_result(self) -> None:
        """Basic successful call returns CompletionResult with content."""
        model = _make_mock_model(_make_ai_message("test response"))
        client = PackModelClient(model)

        result = await client.complete("hello")

        assert isinstance(result, CompletionResult)
        assert result.content == "test response"
        model.ainvoke.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_usage_extracted(self) -> None:
        """Usage metadata from the response is passed through."""
        usage = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
        model = _make_mock_model(_make_ai_message("ok", usage=usage))
        client = PackModelClient(model)

        result = await client.complete("hello")

        assert result.usage is not None
        assert result.usage["input_tokens"] == 10
        assert result.usage["output_tokens"] == 5

    @pytest.mark.asyncio
    async def test_no_usage_metadata(self) -> None:
        """When model returns no usage metadata, usage is None."""
        model = _make_mock_model(_make_ai_message("ok"))
        client = PackModelClient(model)

        result = await client.complete("hello")

        assert result.usage is None


class TestRetryOnTransientError:
    """Transient error triggers retry, succeeds on second attempt."""

    @pytest.mark.asyncio
    async def test_retry_on_connection_error(self) -> None:
        """ConnectionError triggers retry, second attempt succeeds."""
        model = _make_mock_model(
            side_effect=[
                ConnectionError("connection reset"),
                _make_ai_message("recovered"),
            ],
        )
        client = PackModelClient(model, max_retries=3)

        with patch("deepagents.providers.model_client.asyncio.sleep", new_callable=AsyncMock):
            result = await client.complete("hello")

        assert result.content == "recovered"
        assert model.ainvoke.await_count == 2

    @pytest.mark.asyncio
    async def test_retry_on_timeout_error(self) -> None:
        """TimeoutError triggers retry."""
        model = _make_mock_model(
            side_effect=[
                TimeoutError("timed out"),
                _make_ai_message("ok"),
            ],
        )
        client = PackModelClient(model, max_retries=3)

        with patch("deepagents.providers.model_client.asyncio.sleep", new_callable=AsyncMock):
            result = await client.complete("hello")

        assert result.content == "ok"

    @pytest.mark.asyncio
    async def test_retry_on_5xx_error(self) -> None:
        """Error with 500-like message triggers retry."""
        model = _make_mock_model(
            side_effect=[
                Exception("Server error 503 service unavailable"),
                _make_ai_message("ok"),
            ],
        )
        client = PackModelClient(model, max_retries=3)

        with patch("deepagents.providers.model_client.asyncio.sleep", new_callable=AsyncMock):
            result = await client.complete("hello")

        assert result.content == "ok"


class TestRetriesExhausted:
    """All retries exhausted raises with clear error."""

    @pytest.mark.asyncio
    async def test_all_retries_exhausted_raises(self) -> None:
        """After max_retries transient failures, raises the error."""
        model = _make_mock_model(
            side_effect=[
                ConnectionError("fail 1"),
                ConnectionError("fail 2"),
                ConnectionError("fail 3"),
            ],
        )
        client = PackModelClient(model, max_retries=3)

        with (
            patch("deepagents.providers.model_client.asyncio.sleep", new_callable=AsyncMock),
            pytest.raises(ConnectionError, match="retries exhausted"),
        ):
            await client.complete("hello")

        assert model.ainvoke.await_count == 3

    @pytest.mark.asyncio
    async def test_single_retry_exhausted(self) -> None:
        """max_retries=1 means exactly one attempt, no retry."""
        model = _make_mock_model(side_effect=ConnectionError("fail"))
        client = PackModelClient(model, max_retries=1)

        with pytest.raises(ConnectionError, match="retries exhausted"):
            await client.complete("hello")

        assert model.ainvoke.await_count == 1


class TestNonTransientError:
    """Non-transient error raises immediately without retry."""

    @pytest.mark.asyncio
    async def test_value_error_raises_immediately(self) -> None:
        """ValueError is not transient and should not be retried."""
        model = _make_mock_model(side_effect=ValueError("bad input"))
        client = PackModelClient(model, max_retries=3)

        with pytest.raises(ValueError, match="bad input"):
            await client.complete("hello")

        assert model.ainvoke.await_count == 1

    @pytest.mark.asyncio
    async def test_key_error_raises_immediately(self) -> None:
        """KeyError is not transient."""
        model = _make_mock_model(side_effect=KeyError("missing"))
        client = PackModelClient(model, max_retries=3)

        with pytest.raises(KeyError):
            await client.complete("hello")

        assert model.ainvoke.await_count == 1


class TestCostTracking:
    """Cost tracker integration."""

    @pytest.mark.asyncio
    async def test_no_cost_tracker_cost_is_none(self) -> None:
        """Without a CostTracker, cost field is None."""
        model = _make_mock_model(
            _make_ai_message("ok", usage={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}),
        )
        client = PackModelClient(model)

        result = await client.complete("hello")

        assert result.cost is None

    @pytest.mark.asyncio
    async def test_cost_tracker_records_turn(self) -> None:
        """With a CostTracker, cost is populated from record_turn."""
        usage = {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150}
        model = _make_mock_model(_make_ai_message("ok", usage=usage))

        tracker = MagicMock(spec=CostTracker)
        turn_usage = MagicMock()
        turn_usage.cost = 0.0042
        tracker.record_turn.return_value = turn_usage

        client = PackModelClient(model, cost_tracker=tracker)
        result = await client.complete("hello")

        assert result.cost == 0.0042
        tracker.record_turn.assert_called_once_with(
            model="test-model",
            input_tokens=100,
            output_tokens=50,
            cached_tokens=0,
        )

    @pytest.mark.asyncio
    async def test_cost_tracker_with_no_usage(self) -> None:
        """With CostTracker but no usage metadata, cost is None."""
        model = _make_mock_model(_make_ai_message("ok"))
        tracker = MagicMock(spec=CostTracker)
        client = PackModelClient(model, cost_tracker=tracker)

        result = await client.complete("hello")

        assert result.cost is None
        tracker.record_turn.assert_not_called()


class TestDurationTracking:
    """Duration is always populated."""

    @pytest.mark.asyncio
    async def test_duration_populated(self) -> None:
        """duration_ms should always be a positive number."""
        model = _make_mock_model()
        client = PackModelClient(model)

        result = await client.complete("hello")

        assert result.duration_ms is not None
        assert result.duration_ms >= 0

    @pytest.mark.asyncio
    async def test_duration_populated_on_retry(self) -> None:
        """Duration reflects the successful attempt after retries."""
        model = _make_mock_model(
            side_effect=[
                ConnectionError("fail"),
                _make_ai_message("ok"),
            ],
        )
        client = PackModelClient(model, max_retries=3)

        with patch("deepagents.providers.model_client.asyncio.sleep", new_callable=AsyncMock):
            result = await client.complete("hello")

        assert result.duration_ms is not None
        assert result.duration_ms >= 0


class TestPurposeTag:
    """Purpose tag is passed through to logging."""

    @pytest.mark.asyncio
    async def test_purpose_in_log(self) -> None:
        """Purpose tag appears in log output."""
        model = _make_mock_model()
        client = PackModelClient(model)

        with patch("deepagents.providers.model_client.logger") as mock_logger:
            await client.complete("hello", purpose="compaction")

        # Check that purpose was used in at least one log call.
        all_call_args = []
        for call in mock_logger.debug.call_args_list + mock_logger.info.call_args_list:
            all_call_args.extend(str(a) for a in call.args)
        assert any("compaction" in arg for arg in all_call_args)

    @pytest.mark.asyncio
    async def test_default_purpose(self) -> None:
        """Default purpose is 'general'."""
        model = _make_mock_model()
        client = PackModelClient(model)

        with patch("deepagents.providers.model_client.logger") as mock_logger:
            await client.complete("hello")

        all_call_args = []
        for call in mock_logger.debug.call_args_list + mock_logger.info.call_args_list:
            all_call_args.extend(str(a) for a in call.args)
        assert any("general" in arg for arg in all_call_args)
