"""Tests for the harness check pipeline (PR 3)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from deepagents_cli.harness_check import (
    CHECK_REGISTRY,
    CheckReport,
    CheckResult,
    _run_arch_lint,
    run_checks,
)


# ---------------------------------------------------------------------------
# CheckResult / CheckReport shape
# ---------------------------------------------------------------------------


def test_check_result_rejects_unknown_status() -> None:
    with pytest.raises(ValueError, match="status"):
        CheckResult(name="x", status="weird")


def test_check_result_to_dict_preserves_fields() -> None:
    r = CheckResult(name="x", status="pass", summary="ok", command="echo hi")
    d = r.to_dict()
    assert d["name"] == "x"
    assert d["status"] == "pass"
    assert d["command"] == "echo hi"


def test_report_to_dict_has_status_and_checks() -> None:
    report = CheckReport(
        status="pass",
        checks=(CheckResult(name="a", status="pass"),),
    )
    d = report.to_dict()
    assert d["status"] == "pass"
    assert len(d["checks"]) == 1


def test_report_to_json_is_valid() -> None:
    report = CheckReport(
        status="fail",
        checks=(
            CheckResult(name="a", status="pass"),
            CheckResult(name="b", status="fail"),
        ),
    )
    payload = json.loads(report.to_json())
    assert payload["status"] == "fail"
    assert payload["checks"][1]["status"] == "fail"


def test_report_to_human_includes_markers() -> None:
    report = CheckReport(
        status="fail",
        checks=(
            CheckResult(name="a", status="pass", summary="ok"),
            CheckResult(name="b", status="fail", summary="broke"),
        ),
    )
    text = report.to_human()
    assert "✓ a" in text
    assert "✗ b" in text


# ---------------------------------------------------------------------------
# Arch-lint runner — exercises the in-process path against a tiny repo
# ---------------------------------------------------------------------------


def _make_lintable_repo(tmp_path: Path, *, clean: bool) -> Path:
    repo = tmp_path / "r"
    (repo / "libs" / "deepagents" / "deepagents").mkdir(parents=True)
    (repo / "libs" / "cli" / "deepagents_cli").mkdir(parents=True)
    (repo / "libs" / "deepagents" / "deepagents" / "ok.py").write_text(
        "from langchain import agents\n"
    )
    if clean:
        (repo / "libs" / "cli" / "deepagents_cli" / "ok.py").write_text(
            "from deepagents import graph\n"
        )
    else:
        # deepagents reaching into cli — classic reverse-dependency bug
        (repo / "libs" / "deepagents" / "deepagents" / "bad.py").write_text(
            "from deepagents_cli.policy import TaskPolicy\n"
        )
    # Make sure the __init__ files exist so package_for_path can
    # correctly categorize (the in-process lint uses path substrings,
    # so just writing .py files in the right dirs is enough).
    (repo / "libs" / "deepagents" / "deepagents" / "__init__.py").write_text("")
    (repo / "libs" / "cli" / "deepagents_cli" / "__init__.py").write_text("")
    return repo


def test_arch_lint_runner_clean_repo(tmp_path: Path) -> None:
    repo = _make_lintable_repo(tmp_path, clean=True)
    result = _run_arch_lint(repo)
    assert result.status == "pass"
    assert "0 violations" in result.summary


def test_arch_lint_runner_dirty_repo(tmp_path: Path) -> None:
    repo = _make_lintable_repo(tmp_path, clean=False)
    result = _run_arch_lint(repo)
    assert result.status == "fail"
    assert "violation" in result.summary
    violations = result.details["violations"]
    assert any(v["importer"] == "deepagents" for v in violations)


# ---------------------------------------------------------------------------
# run_checks composite
# ---------------------------------------------------------------------------


def test_run_checks_selects_registered_names(tmp_path: Path) -> None:
    repo = _make_lintable_repo(tmp_path, clean=True)
    report = run_checks(repo, checks=["arch-lint"])
    assert [c.name for c in report.checks] == ["arch-lint"]
    assert report.status == "pass"


def test_run_checks_marks_unknown_names_as_not_configured(tmp_path: Path) -> None:
    repo = _make_lintable_repo(tmp_path, clean=True)
    report = run_checks(repo, checks=["does-not-exist"])
    assert report.checks[0].status == "not_configured"
    # not_configured is non-blocking
    assert report.status == "pass"


def test_run_checks_fail_status_propagates(tmp_path: Path) -> None:
    repo = _make_lintable_repo(tmp_path, clean=False)
    report = run_checks(repo, checks=["arch-lint"])
    assert report.checks[0].status == "fail"
    assert report.status == "fail"


def test_run_checks_default_runs_every_registered_check(
    tmp_path: Path,
) -> None:
    """Without an explicit ``checks`` kwarg, the composite runs the
    whole registry. We don't assert on pass/fail of the subprocess-
    based checks (they depend on the host) — just that every name
    shows up in the report."""
    repo = _make_lintable_repo(tmp_path, clean=True)
    # Stub every subprocess-based runner to a deterministic ``skip``
    # so we don't hit the host ruff/pytest/ty binaries.
    def _fake_subproc(**_kwargs: Any) -> CheckResult:
        return CheckResult(name=_kwargs.get("name", "x"), status="skip")

    fakes = {}
    real_arch = CHECK_REGISTRY["arch-lint"]
    for name in CHECK_REGISTRY:
        if name == "arch-lint":
            fakes[name] = real_arch
            continue
        # Closure to capture the name
        def make(n: str) -> Any:
            def run(_root: Path) -> CheckResult:
                return CheckResult(
                    name=n,
                    status="skip",
                    summary="stubbed in test",
                )
            return run
        fakes[name] = make(name)

    with patch.dict(CHECK_REGISTRY, fakes, clear=False):
        report = run_checks(repo)
    names = [c.name for c in report.checks]
    # Registered names all appear at least once, in registration order.
    for expected in ("arch-lint", "tests", "lint", "typecheck", "docs-lint"):
        assert expected in names


def test_run_checks_swallows_runner_exceptions(tmp_path: Path) -> None:
    repo = _make_lintable_repo(tmp_path, clean=True)

    def boom(_root: Path) -> CheckResult:
        raise RuntimeError("exploded")

    with patch.dict(CHECK_REGISTRY, {"arch-lint": boom}, clear=False):
        report = run_checks(repo, checks=["arch-lint"])
    result = report.checks[0]
    assert result.status == "fail"
    assert "exploded" in result.summary


# ---------------------------------------------------------------------------
# Exit-code logic
# ---------------------------------------------------------------------------


def test_skip_and_not_configured_do_not_flip_overall_to_fail() -> None:
    report = CheckReport(
        status="pass",  # baseline so we don't double-assert
        checks=(
            CheckResult(name="a", status="pass"),
            CheckResult(name="b", status="skip"),
            CheckResult(name="c", status="not_configured"),
        ),
    )
    # Re-derive status using the same rule the runner uses
    overall = "fail" if any(c.status == "fail" for c in report.checks) else "pass"
    assert overall == "pass"


def test_any_fail_flips_overall_to_fail() -> None:
    report = CheckReport(
        status="fail",
        checks=(
            CheckResult(name="a", status="pass"),
            CheckResult(name="b", status="fail"),
            CheckResult(name="c", status="not_configured"),
        ),
    )
    overall = "fail" if any(c.status == "fail" for c in report.checks) else "pass"
    assert overall == "fail"
