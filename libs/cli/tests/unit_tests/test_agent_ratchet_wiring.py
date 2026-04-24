"""Integration test for M2: ratchet runtime persistence wired into create_cli_agent.

Exercises the wiring contract end-to-end without spinning up a real
agent — constructs the middleware list, triggers a scope + arch
violation, and verifies the ratchet received both via the
violation_recorder callbacks we wired in.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
from langchain_core.messages import ToolMessage

from deepagents_cli.arch_lint import ArchLintMiddleware, ArchViolation
from deepagents_cli.policy import TaskPolicy
from deepagents_cli.ratchet import Ratchet
from deepagents_cli.scope_enforcement import ScopeEnforcementMiddleware


def _write_request(path: str, content: str = "x", tool: str = "write_file") -> Any:
    req = Mock()
    req.tool_call = {
        "name": tool,
        "args": {"path": path, "content": content},
        "id": "tc-1",
    }
    return req


def _ok_handler(_req: Any) -> ToolMessage:
    return ToolMessage(content="written", name="write_file", tool_call_id="tc-1")


# ---------------------------------------------------------------------------
# Scope enforcement → ratchet
# ---------------------------------------------------------------------------


def test_scope_violation_is_persisted_to_ratchet(tmp_path: Path) -> None:
    ratchet = Ratchet(harness_dir=tmp_path / ".harness")

    def record(tool: str, path: str, reason: str) -> None:
        rule = f"scope.{reason.split()[0].lower()}"
        ratchet.record(rule=rule, subject=path, reason=f"{tool}: {reason}")

    policy = TaskPolicy(task_type="docs", allowed_paths=("docs/**",))
    middleware = ScopeEnforcementMiddleware(
        policy=policy,
        violation_recorder=record,
    )

    result = middleware.wrap_tool_call(
        _write_request("/src/naughty.py"), _ok_handler
    )
    assert result.status == "error"

    violations = ratchet.load_violations()
    assert len(violations) == 1
    assert violations[0].subject == "/src/naughty.py"
    assert violations[0].rule.startswith("scope.")


def test_same_scope_violation_second_time_is_not_duplicated(tmp_path: Path) -> None:
    """Ratchet dedups on (rule, subject) — second attempt is a known
    violation, not a new one."""
    ratchet = Ratchet(harness_dir=tmp_path / ".harness")

    def record(tool: str, path: str, reason: str) -> None:
        ratchet.record(
            rule=f"scope.{reason.split()[0].lower()}",
            subject=path,
            reason=reason,
        )

    policy = TaskPolicy(task_type="docs", allowed_paths=("docs/**",))
    middleware = ScopeEnforcementMiddleware(policy=policy, violation_recorder=record)
    req = _write_request("/src/same.py")

    middleware.wrap_tool_call(req, _ok_handler)
    middleware.wrap_tool_call(req, _ok_handler)

    assert len(ratchet.load_violations()) == 1


# ---------------------------------------------------------------------------
# Arch-lint → ratchet
# ---------------------------------------------------------------------------


def test_arch_violation_is_persisted_to_ratchet(tmp_path: Path) -> None:
    ratchet = Ratchet(harness_dir=tmp_path / ".harness")

    def record(path: str, violation: ArchViolation) -> None:
        subject = f"{violation.importer}:{violation.imported}"
        ratchet.record(
            rule="arch.forbidden_import",
            subject=subject,
            reason=f"{path}: {violation.summary()}",
        )

    middleware = ArchLintMiddleware(violation_recorder=record)

    result = middleware.wrap_tool_call(
        _write_request(
            "libs/deepagents/deepagents/foo.py",
            "from deepagents_cli.policy import TaskPolicy\n",
        ),
        _ok_handler,
    )
    assert result.status == "error"

    violations = ratchet.load_violations()
    assert len(violations) == 1
    entry = violations[0]
    assert entry.rule == "arch.forbidden_import"
    assert entry.subject == "deepagents:deepagents_cli"


# ---------------------------------------------------------------------------
# Ratchet seeding tolerates existing violations across runs
# ---------------------------------------------------------------------------


def test_existing_arch_violation_tolerated_on_second_run(tmp_path: Path) -> None:
    ratchet = Ratchet(harness_dir=tmp_path / ".harness")
    # Simulate a first run that recorded the violation as debt.
    ratchet.record(
        rule="arch.forbidden_import",
        subject="deepagents:deepagents_cli",
        reason="seeded debt",
    )

    # Second-run construction reads the existing violations and seeds
    # them into the middleware.
    existing: set[tuple[str, str]] = set()
    for v in ratchet.load_violations():
        if v.rule == "arch.forbidden_import":
            parts = v.subject.split(":", 1)
            if len(parts) == 2:
                existing.add((parts[0], parts[1]))

    middleware = ArchLintMiddleware(existing_violations=existing)

    result = middleware.wrap_tool_call(
        _write_request(
            "libs/deepagents/deepagents/foo.py",
            "from deepagents_cli.policy import TaskPolicy\n",
        ),
        _ok_handler,
    )
    # Tolerated because it was already known — not a new regression.
    assert result.status != "error"


def test_fresh_arch_violation_still_blocks_with_seeded_debt(tmp_path: Path) -> None:
    """Seeding one pair should not silence other violations."""
    existing: set[tuple[str, str]] = {("deepagents", "deepagents_cli")}
    middleware = ArchLintMiddleware(existing_violations=existing)

    # Different violating pair — still blocked.
    result = middleware.wrap_tool_call(
        _write_request(
            "libs/deepagents/deepagents/foo.py",
            "from deepagents_harbor.wrapper import W\n",
        ),
        _ok_handler,
    )
    assert result.status == "error"


# ---------------------------------------------------------------------------
# create_cli_agent signature smoke test
# ---------------------------------------------------------------------------


def test_create_cli_agent_accepts_ratchet_dir_kwarg() -> None:
    """The production call site (Harbor wrapper + tests) relies on the
    new ``ratchet_dir`` kwarg existing. Fast failure surface if we
    rename it or drop it accidentally."""
    import inspect

    from deepagents_cli.agent import create_cli_agent

    sig = inspect.signature(create_cli_agent)
    assert "ratchet_dir" in sig.parameters
    assert "task_policy" in sig.parameters
    assert "context_pack" in sig.parameters
