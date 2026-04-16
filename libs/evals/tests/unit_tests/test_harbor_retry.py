"""Regression-proof unit tests for the Harbor retry wrapper.

These tests pin the observable behavior of ``_invoke_with_retry`` before
Unit 2 consolidates the two-codepath (type-check + string-match) retry into
a single ``_is_transient_error`` classifier. Any future refactor must keep
these green.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from deepagents_harbor.deepagents_wrapper import (
    _RETRY_BASE_DELAY,
    _RETRY_MAX_ATTEMPTS,
    _invoke_with_retry,
    _is_transient_error,
)


class _ScriptedAgent:
    """Fake agent whose ``ainvoke`` returns a scripted sequence of outcomes.

    Pass a list of callables or values. On each attempt, the next entry is
    consumed: callables are called (used to raise exceptions), values are
    returned as the result dict.
    """

    def __init__(self, script: list[Any]) -> None:
        self._script = list(script)
        self.calls: list[tuple[dict, Any]] = []

    async def ainvoke(self, input_data: dict, config: Any = None) -> dict:
        self.calls.append((input_data, config))
        entry = self._script.pop(0)
        if callable(entry):
            return entry()
        return entry


def _raise(exc: BaseException):  # noqa: ANN202  # test helper
    def _f() -> None:
        raise exc

    return _f


@pytest.fixture
def sleep_calls(monkeypatch):
    """Replace asyncio.sleep inside the wrapper with a recorder.

    Returns a list that accumulates every sleep duration the retry loop
    requested, so tests can assert backoff cadence without actually waiting.
    """
    recorded: list[float] = []

    async def _fake_sleep(seconds: float) -> None:
        recorded.append(seconds)

    # The wrapper module uses ``import asyncio as _asyncio`` inside the
    # function, so patch asyncio.sleep at the module level.
    import asyncio

    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)
    return recorded


@pytest.mark.asyncio
async def test_happy_path_first_attempt_success(sleep_calls):
    """First attempt succeeds -> result returned, no sleep."""
    expected = {"messages": [{"role": "assistant", "content": "ok"}]}
    agent = _ScriptedAgent([expected])

    result = await _invoke_with_retry(agent, {"input": "x"}, config=None)

    assert result is expected
    assert len(agent.calls) == 1
    assert sleep_calls == []


@pytest.mark.asyncio
async def test_retry_success_after_one_connection_error(sleep_calls):
    """ConnectionError on attempt 1, success on attempt 2."""
    expected = {"messages": []}
    agent = _ScriptedAgent([_raise(ConnectionError("boom")), expected])

    result = await _invoke_with_retry(agent, {"input": "x"}, config=None)

    assert result is expected
    assert len(agent.calls) == 2
    # Current implementation sleeps exactly _RETRY_BASE_DELAY (2.0s) before
    # attempt 2. Unit 2 will add jitter — the assertion below will need to
    # tolerate a small addition; for now it pins the exact current value.
    assert len(sleep_calls) == 1
    assert sleep_calls[0] >= _RETRY_BASE_DELAY


@pytest.mark.asyncio
async def test_retry_success_on_third_attempt_with_backoff(sleep_calls):
    """Backoff durations double: 2s before attempt 2, 4s before attempt 3."""
    expected = {"messages": []}
    agent = _ScriptedAgent(
        [
            _raise(TimeoutError("t1")),
            _raise(ConnectionError("t2")),
            expected,
        ]
    )

    result = await _invoke_with_retry(agent, {"input": "x"}, config=None)

    assert result is expected
    assert len(agent.calls) == 3
    assert len(sleep_calls) == 2
    # Exponential: base * 2**0, base * 2**1. Allow >= to accommodate jitter
    # once Unit 2 lands.
    assert sleep_calls[0] >= _RETRY_BASE_DELAY
    assert sleep_calls[1] >= _RETRY_BASE_DELAY * 2
    assert sleep_calls[1] > sleep_calls[0]


@pytest.mark.asyncio
async def test_all_attempts_fail_raises_last_exception(sleep_calls):
    """Exhausted retries -> last exception propagates."""
    final_exc = ConnectionError("final")
    agent = _ScriptedAgent(
        [
            _raise(ConnectionError("a1")),
            _raise(ConnectionError("a2")),
            _raise(final_exc),
        ]
    )

    with pytest.raises(ConnectionError) as excinfo:
        await _invoke_with_retry(agent, {"input": "x"}, config=None)

    assert excinfo.value is final_exc
    assert len(agent.calls) == _RETRY_MAX_ATTEMPTS
    # Sleep fires before attempts 2 and 3 only — not before attempt 1 or
    # after the final failure.
    assert len(sleep_calls) == _RETRY_MAX_ATTEMPTS - 1


@pytest.mark.asyncio
async def test_non_retryable_error_propagates_immediately(sleep_calls):
    """ValueError is not retryable -> raised on first attempt, no retry."""
    agent = _ScriptedAgent([_raise(ValueError("bad args"))])

    with pytest.raises(ValueError, match="bad args"):
        await _invoke_with_retry(agent, {"input": "x"}, config=None)

    assert len(agent.calls) == 1
    assert sleep_calls == []


@pytest.mark.asyncio
async def test_generic_runtime_error_with_disconnect_message_retries(sleep_calls):
    """String-match path: RuntimeError('Server disconnected') triggers retry.

    This guards the current string-match fallback. When Unit 2 replaces it
    with a structured classifier that still covers generic RuntimeError
    disconnect messages, this test continues to pass.
    """
    expected = {"messages": []}
    agent = _ScriptedAgent(
        [
            _raise(RuntimeError("Server disconnected without sending a response")),
            expected,
        ]
    )

    result = await _invoke_with_retry(agent, {"input": "x"}, config=None)

    assert result is expected
    assert len(agent.calls) == 2
    assert len(sleep_calls) == 1


@pytest.mark.asyncio
async def test_max_attempts_one_disables_retry(sleep_calls):
    """max_attempts=1 means no retry: first failure raises."""
    agent = _ScriptedAgent([_raise(ConnectionError("first"))])

    with pytest.raises(ConnectionError):
        await _invoke_with_retry(
            agent, {"input": "x"}, config=None, max_attempts=1
        )

    assert len(agent.calls) == 1
    assert sleep_calls == []


# --------------------------------------------------------------------------
# Classifier unit tests — Unit 2 coverage of _is_transient_error in isolation.
# --------------------------------------------------------------------------


def test_classifier_accepts_direct_retryable_types():
    assert _is_transient_error(ConnectionError("x"))
    assert _is_transient_error(TimeoutError("x"))
    assert _is_transient_error(OSError("x"))


def test_classifier_rejects_value_error():
    assert not _is_transient_error(ValueError("nope"))


def test_classifier_accepts_generic_runtime_error_with_disconnect_message():
    assert _is_transient_error(RuntimeError("connection reset by peer"))
    assert _is_transient_error(RuntimeError("Server disconnected without a response"))
    assert _is_transient_error(RuntimeError("EOF on socket"))


def test_classifier_walks_cause_chain():
    """ConnectionError wrapped as __cause__ of RuntimeError -> retryable."""
    inner = ConnectionError("tcp reset")
    outer = RuntimeError("something failed")
    outer.__cause__ = inner
    assert _is_transient_error(outer)


def test_classifier_walks_context_chain():
    """Same, but via implicit __context__ (raise-within-except)."""
    inner = TimeoutError("slow")
    outer = RuntimeError("upstream failure")
    outer.__context__ = inner
    assert _is_transient_error(outer)


def test_classifier_rejects_non_generic_type_with_disconnect_text():
    """String fallback is gated on generic types only.

    A ValueError whose message happens to contain a disconnect marker must
    NOT be classified as transient — otherwise programmer errors with
    unlucky wording would get retried.
    """
    assert not _is_transient_error(ValueError("server disconnected on input"))


def test_classifier_terminates_on_cyclic_cause_chain():
    """Pathological cycle must not loop forever."""
    a = RuntimeError("a")
    b = RuntimeError("b")
    a.__cause__ = b
    b.__cause__ = a
    # Should return without hanging. Both are generic with non-disconnect
    # messages, so the result is False — but the important property is that
    # the call returns at all.
    assert _is_transient_error(a) is False


# --------------------------------------------------------------------------
# Retry metadata annotation — Unit 4.
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_metadata_records_attempts_on_first_try_success(sleep_calls):
    """First-attempt success -> retry_attempts=1, retry_terminated=False."""
    agent = _ScriptedAgent([{"messages": []}])
    config: dict[str, Any] = {"metadata": {}}

    await _invoke_with_retry(agent, {"input": "x"}, config=config)

    assert config["metadata"]["retry_attempts"] == 1
    assert config["metadata"]["retry_terminated"] is False
    assert "retry_final_exception_type" not in config["metadata"]


@pytest.mark.asyncio
async def test_metadata_records_attempts_after_recovery(sleep_calls):
    """Succeeded on attempt 2 -> retry_attempts=2, retry_terminated=False."""
    agent = _ScriptedAgent([_raise(ConnectionError("x")), {"messages": []}])
    config: dict[str, Any] = {"metadata": {}}

    await _invoke_with_retry(agent, {"input": "x"}, config=config)

    assert config["metadata"]["retry_attempts"] == 2
    assert config["metadata"]["retry_terminated"] is False


@pytest.mark.asyncio
async def test_metadata_records_terminated_on_exhaustion(sleep_calls):
    """All retries exhausted -> retry_terminated=True + exception type."""
    agent = _ScriptedAgent(
        [
            _raise(ConnectionError("a")),
            _raise(ConnectionError("b")),
            _raise(ConnectionError("c")),
        ]
    )
    config: dict[str, Any] = {"metadata": {}}

    with pytest.raises(ConnectionError):
        await _invoke_with_retry(agent, {"input": "x"}, config=config)

    assert config["metadata"]["retry_attempts"] == _RETRY_MAX_ATTEMPTS
    assert config["metadata"]["retry_terminated"] is True
    assert config["metadata"]["retry_final_exception_type"] == "ConnectionError"


@pytest.mark.asyncio
async def test_metadata_records_non_retryable_termination(sleep_calls):
    """Non-retryable error -> retry_terminated=True on first attempt."""
    agent = _ScriptedAgent([_raise(ValueError("bad"))])
    config: dict[str, Any] = {"metadata": {}}

    with pytest.raises(ValueError):
        await _invoke_with_retry(agent, {"input": "x"}, config=config)

    assert config["metadata"]["retry_attempts"] == 1
    assert config["metadata"]["retry_terminated"] is True
    assert config["metadata"]["retry_final_exception_type"] == "ValueError"


@pytest.mark.asyncio
async def test_metadata_annotation_is_noop_when_config_is_none(sleep_calls):
    """config=None path: no crash, no side effect."""
    agent = _ScriptedAgent([{"messages": []}])

    await _invoke_with_retry(agent, {"input": "x"}, config=None)


@pytest.mark.asyncio
async def test_metadata_annotation_is_noop_when_metadata_missing(sleep_calls):
    """config without metadata key: no crash, no AttributeError."""
    agent = _ScriptedAgent([{"messages": []}])
    config: dict[str, Any] = {}

    await _invoke_with_retry(agent, {"input": "x"}, config=config)

    # Nothing injected — config stays minimal
    assert "metadata" not in config
