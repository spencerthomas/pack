"""Tests for partial trajectory capture on retry exhaustion.

Exercises ``DeepAgentsWrapper._save_trajectory`` with failure paths to
verify every TB2 trial produces a ``trajectory.json`` in its job directory
— even when the agent invocation dies before returning a result.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from deepagents_harbor.deepagents_wrapper import (
    DeepAgentsWrapper,
    _build_failure_info,
    _FailureInfo,
)


@pytest.fixture
def fake_environment(tmp_path: Path):
    """Minimal BaseEnvironment-like object for _save_trajectory."""
    return SimpleNamespace(session_id="harbor-test-session")


@pytest.fixture
def wrapper(tmp_path: Path, monkeypatch):
    """DeepAgentsWrapper with logs_dir pointed at a tmp path, no real model init."""
    instance = DeepAgentsWrapper.__new__(DeepAgentsWrapper)
    instance._model_name = "test-model"
    instance._model = MagicMock()
    instance._use_cli_agent = True
    instance._instruction_to_example_id = {}
    instance.logs_dir = tmp_path
    return instance


def test_save_trajectory_success_path_no_failure_field(wrapper, fake_environment):
    """Happy path: trajectory.json written without extra.failure block."""
    result = {"messages": []}

    wrapper._save_trajectory(fake_environment, "task", result, infra_meta=None)

    trajectory_path = wrapper.logs_dir / "trajectory.json"
    assert trajectory_path.exists()
    data = json.loads(trajectory_path.read_text())
    assert data.get("extra") in (None, {}) or "failure" not in data.get("extra", {})


def test_save_trajectory_with_none_result_and_failure_emits_failed_status(
    wrapper, fake_environment
):
    """Terminal failure: result=None + failure -> extra.status=failed + user step."""
    failure = _FailureInfo(
        reason="retry_exhausted",
        exception_type="ConnectionError",
        attempts=3,
        final_exception_repr="ConnectionError('server disconnected')",
    )

    wrapper._save_trajectory(
        fake_environment, "my task", result=None, infra_meta=None, failure=failure
    )

    trajectory_path = wrapper.logs_dir / "trajectory.json"
    assert trajectory_path.exists()
    data = json.loads(trajectory_path.read_text())
    assert data["extra"]["status"] == "failed"
    assert data["extra"]["failure"]["reason"] == "retry_exhausted"
    assert data["extra"]["failure"]["exception_type"] == "ConnectionError"
    assert data["extra"]["failure"]["attempts"] == 3
    # User instruction must be present as a step even when invocation died
    assert data["steps"][0]["source"] == "user"
    assert data["steps"][0]["message"] == "my task"


def test_save_trajectory_with_partial_result_and_failure(wrapper, fake_environment):
    """Mid-stream failure: result has some messages, failure block also set."""
    from langchain_core.messages import AIMessage

    result = {"messages": [AIMessage(content="partial progress")]}
    failure = _FailureInfo(
        reason="retry_exhausted",
        exception_type="TimeoutError",
        attempts=3,
        final_exception_repr="TimeoutError('slow')",
    )

    wrapper._save_trajectory(
        fake_environment, "task", result=result, infra_meta=None, failure=failure
    )

    data = json.loads((wrapper.logs_dir / "trajectory.json").read_text())
    assert data["extra"]["status"] == "failed"
    assert data["extra"]["failure"]["attempts"] == 3


def test_build_failure_info_truncates_long_repr():
    """Exception reprs >2048 chars are truncated to keep JSON compact."""
    # Python truncates the message in repr; build an exception with a
    # genuinely long args tuple.
    long_msg = "x" * 5000
    exc = RuntimeError(long_msg)

    info = _build_failure_info(exc, attempts=3, reason="retry_exhausted")

    assert info.exception_type == "RuntimeError"
    assert info.attempts == 3
    assert info.reason == "retry_exhausted"
    assert len(info.final_exception_repr) <= 2048
    assert info.final_exception_repr.endswith("...")


def test_build_failure_info_short_repr_unmodified():
    """Short exception reprs are not truncated."""
    exc = ConnectionError("boom")

    info = _build_failure_info(exc, attempts=1, reason="non_retryable")

    assert "boom" in info.final_exception_repr
    assert not info.final_exception_repr.endswith("...")


def test_save_trajectory_handles_empty_messages_dict_with_failure(
    wrapper, fake_environment
):
    """``result`` can be an empty dict (no 'messages' key) without crashing."""
    failure = _FailureInfo(
        reason="non_retryable",
        exception_type="ValueError",
        attempts=1,
        final_exception_repr="ValueError('bad')",
    )

    # Should not raise — the trajectory builder handles missing/empty messages.
    wrapper._save_trajectory(
        fake_environment, "task", result={}, infra_meta=None, failure=failure
    )

    data = json.loads((wrapper.logs_dir / "trajectory.json").read_text())
    assert data["extra"]["failure"]["exception_type"] == "ValueError"
