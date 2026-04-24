"""``harness check`` — composite verification pipeline.

PR 3 from the review plan. Wraps every check the harness already knows
about into a single command that returns structured results. Designed
for both CI (parse the JSON) and human use (print the summary).

The checks are named and resolved from ``.harness/config.yaml`` when
present (via ``required_checks`` on any policy) or from a safe default
set otherwise. Unknown check names are reported as ``not_configured``
rather than errored so configs don't break when a new check name is
added before the runner supports it.

Supported checks (initial set):

- ``arch-lint`` — runs the in-process arch-lint against the repo's
  package files.
- ``tests`` — subprocess to a repo-configured test command (defaults
  to ``pytest``). Skipped when no test command is found.
- ``lint`` — subprocess to ``ruff check``.
- ``typecheck`` — subprocess to ``ty`` when available (Astral type
  checker that the repo already uses).
- ``docs-lint`` — subprocess to ``ruff check --select D`` scoped to
  the repo's docstrings.

Every check returns a ``CheckResult`` with ``status`` of ``pass``,
``fail``, ``not_configured``, or ``skip``. The composite exits with
``fail`` if any individual check failed.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from deepagents_cli.arch_lint import check_file as arch_check_file
from deepagents_cli.business_rule_checker import run_business_rules

logger = logging.getLogger(__name__)


# --- Types --------------------------------------------------------------


@dataclass(frozen=True)
class CheckResult:
    """One check's verdict.

    Attributes:
        name: The check's registered name (``arch-lint`` etc.).
        status: ``pass | fail | not_configured | skip``.
        summary: One-line human description.
        command: The shell command run, when applicable.
        details: Free-form structured payload (violation list, stderr
            tail, etc.). Kept as ``dict`` so new fields can land without
            changing the shape.
    """

    name: str
    status: str
    summary: str = ""
    command: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status not in _STATUSES:
            allowed = ", ".join(sorted(_STATUSES))
            msg = f"status must be one of [{allowed}], got {self.status!r}"
            raise ValueError(msg)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CheckReport:
    """Composite report from ``harness check``.

    Attributes:
        status: ``pass`` if every check passed, ``fail`` if any failed.
            ``not_configured`` / ``skip`` entries are non-blocking and
            don't flip the overall status.
        checks: Per-check results, in the order they ran.
    """

    status: str
    checks: tuple[CheckResult, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "checks": [c.to_dict() for c in self.checks],
        }

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=True)

    def to_human(self) -> str:
        lines = [f"status: {self.status}", ""]
        for check in self.checks:
            marker = {
                "pass": "✓",
                "fail": "✗",
                "not_configured": "·",
                "skip": "·",
            }[check.status]
            head = f"{marker} {check.name} [{check.status}]"
            if check.summary:
                head += f" — {check.summary}"
            lines.append(head)
            if check.command:
                lines.append(f"    command: {check.command}")
        return "\n".join(lines)


_STATUSES = frozenset({"pass", "fail", "not_configured", "skip"})


# --- Registry ----------------------------------------------------------

# A simple registry so we can register new checks later without rewiring
# the composite runner. Each entry takes the repo root and returns a
# ``CheckResult``. Subprocess-based checks call ``_run_command`` which
# centralises timeout + capture semantics.

_Runner = Any  # callable(repo_root: Path) -> CheckResult


def _run_arch_lint(repo_root: Path) -> CheckResult:
    """In-process arch-lint across every tracked .py file."""
    violations: list[dict[str, Any]] = []
    files_scanned = 0
    for path in _iter_python_files(repo_root):
        files_scanned += 1
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = str(path.relative_to(repo_root))
        for v in arch_check_file(rel, source):
            violations.append(
                {
                    "file": rel,
                    "importer": v.importer,
                    "imported": v.imported,
                    "line": v.line_number,
                    "import_line": v.import_line.strip(),
                }
            )
    if not violations:
        return CheckResult(
            name="arch-lint",
            status="pass",
            summary=f"{files_scanned} files scanned, 0 violations",
            details={"files_scanned": files_scanned, "violations": []},
        )
    return CheckResult(
        name="arch-lint",
        status="fail",
        summary=f"{len(violations)} architecture violation(s)",
        details={"files_scanned": files_scanned, "violations": violations},
    )


def _run_tests(repo_root: Path) -> CheckResult:
    """Run repo tests via pytest under uv when available."""
    pytest_path = shutil.which("pytest")
    uv_path = shutil.which("uv")
    if uv_path is None and pytest_path is None:
        return CheckResult(
            name="tests",
            status="not_configured",
            summary="no pytest or uv found on PATH",
        )
    # Prefer `uv run pytest` for repos that use uv (Pack does). Falls
    # back to bare pytest if uv isn't available.
    if uv_path:
        cmd = [uv_path, "run", "pytest", "-q", "--no-header"]
    else:
        cmd = [pytest_path or "pytest", "-q", "--no-header"]  # type: ignore[list-item]
    return _run_command(name="tests", cwd=repo_root, cmd=cmd, timeout=300)


def _run_ruff_lint(repo_root: Path) -> CheckResult:
    ruff = shutil.which("ruff")
    if ruff is None:
        return CheckResult(
            name="lint",
            status="not_configured",
            summary="ruff not on PATH",
        )
    return _run_command(
        name="lint",
        cwd=repo_root,
        cmd=[ruff, "check", "."],
        timeout=60,
    )


def _run_typecheck(repo_root: Path) -> CheckResult:
    # The repo uses ty (Astral); fall back to mypy if present.
    ty = shutil.which("ty")
    mypy = shutil.which("mypy")
    if ty is not None:
        return _run_command(
            name="typecheck",
            cwd=repo_root,
            cmd=[ty, "check"],
            timeout=180,
        )
    if mypy is not None:
        return _run_command(
            name="typecheck",
            cwd=repo_root,
            cmd=[mypy, "."],
            timeout=180,
        )
    return CheckResult(
        name="typecheck",
        status="not_configured",
        summary="no ty or mypy on PATH",
    )


def _run_business_rules(repo_root: Path) -> CheckResult:
    """Run invariants declared in any ``.context-packs/*/checks.yaml``."""
    status, summary, violations = run_business_rules(repo_root)
    return CheckResult(
        name="business-rules",
        status=status,
        summary=summary,
        details={
            "violations": [
                {
                    "invariant_id": v.invariant_id,
                    "severity": v.severity,
                    "description": v.description,
                    "file": v.file,
                    "line": v.line,
                    "detail": v.detail,
                }
                for v in violations
            ]
        },
    )


def _run_docs_lint(repo_root: Path) -> CheckResult:
    ruff = shutil.which("ruff")
    if ruff is None:
        return CheckResult(
            name="docs-lint",
            status="not_configured",
            summary="ruff not on PATH",
        )
    return _run_command(
        name="docs-lint",
        cwd=repo_root,
        cmd=[ruff, "check", "--select", "D", "."],
        timeout=60,
    )


CHECK_REGISTRY: dict[str, _Runner] = {
    "arch-lint": _run_arch_lint,
    "business-rules": _run_business_rules,
    "tests": _run_tests,
    "lint": _run_ruff_lint,
    "typecheck": _run_typecheck,
    "docs-lint": _run_docs_lint,
}


# --- Command runner ----------------------------------------------------


def _run_command(
    *,
    name: str,
    cwd: Path,
    cmd: list[str],
    timeout: int,
) -> CheckResult:
    cmd_str = " ".join(cmd)
    try:
        proc = subprocess.run(  # noqa: S603  # args are validated, shell disabled
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        return CheckResult(
            name=name,
            status="not_configured",
            summary="executable not found",
            command=cmd_str,
        )
    except subprocess.TimeoutExpired:
        return CheckResult(
            name=name,
            status="fail",
            summary=f"timed out after {timeout}s",
            command=cmd_str,
        )

    if proc.returncode == 0:
        return CheckResult(
            name=name,
            status="pass",
            summary=_tail_summary(proc.stdout) or "ok",
            command=cmd_str,
            details={"returncode": 0},
        )
    return CheckResult(
        name=name,
        status="fail",
        summary=_tail_summary(proc.stdout or proc.stderr) or f"exit {proc.returncode}",
        command=cmd_str,
        details={
            "returncode": proc.returncode,
            "stdout_tail": _tail(proc.stdout, 1500),
            "stderr_tail": _tail(proc.stderr, 1500),
        },
    )


def _tail(text: str, max_chars: int) -> str:
    if not text:
        return ""
    return text if len(text) <= max_chars else text[-max_chars:]


def _tail_summary(text: str) -> str:
    if not text:
        return ""
    last_line = ""
    for line in text.splitlines():
        if line.strip():
            last_line = line.strip()
    return last_line


# --- Discovery helpers --------------------------------------------------


_IGNORE_DIRS = {
    ".git", ".venv", "venv", "env", "__pycache__",
    "node_modules", "dist", "build", "target",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox",
}


def _iter_python_files(root: Path) -> list[Path]:
    out: list[Path] = []
    for path in root.rglob("*.py"):
        if any(p in _IGNORE_DIRS for p in path.parts):
            continue
        out.append(path)
    return out


# --- Composite runner --------------------------------------------------


def run_checks(
    repo_root: str | Path,
    *,
    checks: list[str] | None = None,
) -> CheckReport:
    """Run the requested checks and return a composite report.

    Args:
        repo_root: Directory to run checks against.
        checks: Explicit check names to run. When None, runs every
            check in ``CHECK_REGISTRY``.

    Returns:
        A ``CheckReport``. Overall status is ``fail`` if any included
        check's status is ``fail``; ``pass`` otherwise.
    """
    root = Path(repo_root).resolve()
    names = checks if checks is not None else list(CHECK_REGISTRY)

    results: list[CheckResult] = []
    for name in names:
        runner = CHECK_REGISTRY.get(name)
        if runner is None:
            results.append(
                CheckResult(
                    name=name,
                    status="not_configured",
                    summary="unknown check name",
                )
            )
            continue
        try:
            result = runner(root)
        except Exception as exc:  # noqa: BLE001  # a runner crash shouldn't mask the others
            logger.warning("Check %s raised: %s", name, exc, exc_info=True)
            result = CheckResult(
                name=name,
                status="fail",
                summary=f"runner raised: {exc}",
            )
        results.append(result)

    overall = "fail" if any(r.status == "fail" for r in results) else "pass"
    return CheckReport(status=overall, checks=tuple(results))


__all__ = [
    "CHECK_REGISTRY",
    "CheckReport",
    "CheckResult",
    "run_checks",
]
