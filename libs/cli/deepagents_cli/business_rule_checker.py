"""Business-rule checker — context packs become executable policy.

M5 of the agent-harness roadmap. A context pack can now carry a
``checks.yaml`` file alongside its README + rules.md. The file
declares invariants the harness enforces as part of ``harness check``.

Shape:

.. code-block:: yaml

    invariants:
      - id: no_print_in_middleware
        description: Middleware must log via the logger, not print().
        severity: block
        matcher: absent_regex
        pattern: '\\bprint\\('
        paths:
          - 'libs/cli/deepagents_cli/*_middleware.py'

      - id: every_middleware_has_test
        description: Each middleware file needs a corresponding test_*.py.
        severity: warn
        matcher: file_exists
        target: 'libs/cli/tests/unit_tests/test_{stem}.py'
        paths:
          - 'libs/cli/deepagents_cli/*_middleware.py'

Three matcher types land in this first cut:

- ``regex`` — at least one file in ``paths`` must match ``pattern``.
- ``absent_regex`` — no file in ``paths`` may match ``pattern``.
- ``file_exists`` — for each file in ``paths``, the computed
  ``target`` path must exist. ``{stem}`` / ``{path}`` interpolation
  is supported.

Severity levels:

- ``block`` — any violation flips the check status to ``fail``.
- ``warn`` — surfaced as details but keeps the check status ``pass``.
- ``info`` — logged only; doesn't affect check status.

This is intentionally a small vocabulary. Schema and golden-case
matchers (also in M5 of the review plan) can land as additions to
the ``MatcherType`` enum without changing the integration point.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


SEVERITIES = frozenset({"block", "warn", "info"})
MATCHER_TYPES = frozenset({"regex", "absent_regex", "file_exists"})


CHECKS_FILENAME = "checks.yaml"


# --- Types --------------------------------------------------------------


@dataclass(frozen=True)
class Invariant:
    """One declarative rule from a pack's ``checks.yaml``.

    Attributes:
        id: Stable identifier; violation reports key on this so the
            ratchet can dedupe across runs.
        description: Human-readable explanation, quoted verbatim in
            failure messages.
        severity: ``block | warn | info``. See module docstring.
        matcher: Type of check — see module docstring.
        paths: Glob patterns (repo-relative) the matcher operates on.
            Patterns use the project-standard ``**/*.py`` style and
            are resolved via ``Path.rglob``.
        pattern: Regex for ``regex`` / ``absent_regex`` matchers.
        target: Template for ``file_exists`` matcher. Supports
            ``{stem}`` (file stem) and ``{path}`` (full rel path).
    """

    id: str
    description: str
    severity: str = "block"
    matcher: str = "absent_regex"
    paths: tuple[str, ...] = ()
    pattern: str = ""
    target: str = ""

    def __post_init__(self) -> None:
        if self.severity not in SEVERITIES:
            msg = f"severity must be one of {sorted(SEVERITIES)}, got {self.severity!r}"
            raise ValueError(msg)
        if self.matcher not in MATCHER_TYPES:
            msg = f"matcher must be one of {sorted(MATCHER_TYPES)}, got {self.matcher!r}"
            raise ValueError(msg)


@dataclass(frozen=True)
class InvariantViolation:
    """One failed invariant."""

    invariant_id: str
    severity: str
    description: str
    file: str | None = None
    line: int | None = None
    detail: str = ""


# --- Loading ------------------------------------------------------------


def load_invariants(pack_path: str | Path) -> tuple[Invariant, ...]:
    """Load ``checks.yaml`` from a pack directory.

    Returns an empty tuple when the file is missing, empty, malformed,
    or parses to something other than the expected shape. Logs a
    warning but never raises — a broken checks file should downgrade
    to "no invariants" rather than take the whole harness offline.
    """
    path = Path(pack_path) / CHECKS_FILENAME
    if not path.is_file():
        return ()
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("Could not read %s: %s", path, exc)
        return ()

    try:
        data = _parse_yaml(raw)
    except Exception as exc:  # noqa: BLE001  # malformed YAML is non-fatal
        logger.warning("Malformed %s: %s", path, exc)
        return ()

    if not isinstance(data, dict):
        return ()
    entries = data.get("invariants")
    if not isinstance(entries, list):
        return ()

    out: list[Invariant] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        try:
            out.append(
                Invariant(
                    id=str(entry["id"]),
                    description=str(entry.get("description", "")),
                    severity=str(entry.get("severity", "block")),
                    matcher=str(entry.get("matcher", "absent_regex")),
                    paths=_as_str_tuple(entry.get("paths")),
                    pattern=str(entry.get("pattern", "")),
                    target=str(entry.get("target", "")),
                )
            )
        except (KeyError, ValueError) as exc:
            logger.warning("Skipping malformed invariant in %s: %s", path, exc)
    return tuple(out)


def _parse_yaml(text: str) -> Any:
    """YAML load with a minimal fallback for environments without PyYAML.

    Unlike the harness_config fallback (which handles only flat
    key:value), checks.yaml typically has block-style lists of
    mappings — too elaborate for a naive parser. If PyYAML isn't
    available and the file looks non-trivial, we log and return
    None rather than produce wrong results.
    """
    try:
        import yaml  # type: ignore[import-not-found]

        return yaml.safe_load(text)
    except ImportError:
        logger.warning(
            "PyYAML not importable; checks.yaml requires it for non-trivial "
            "rule sets. Skipping."
        )
        return None


def _as_str_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, list):
        return tuple(str(v) for v in value if isinstance(v, (str, int)))
    if isinstance(value, str):
        return (value,)
    return ()


# --- Running ------------------------------------------------------------


def _iter_matching_files(
    repo_root: Path, globs: tuple[str, ...]
) -> list[Path]:
    """Resolve glob patterns against ``repo_root``.

    Uses ``Path.rglob`` under the hood by walking globs. Each glob is
    relative to ``repo_root``. Deduplicates results so a file matching
    two patterns is scanned once.
    """
    seen: set[Path] = set()
    out: list[Path] = []
    for pattern in globs:
        # Path.glob handles **/* patterns; strip any leading slash.
        normalized = pattern.lstrip("/")
        for match in repo_root.glob(normalized):
            if not match.is_file():
                continue
            if match in seen:
                continue
            seen.add(match)
            out.append(match)
    return out


def _check_regex(
    invariant: Invariant, repo_root: Path
) -> list[InvariantViolation]:
    if not invariant.pattern:
        return [
            InvariantViolation(
                invariant_id=invariant.id,
                severity=invariant.severity,
                description=invariant.description,
                detail="invariant declared a regex matcher but no pattern was set",
            )
        ]
    compiled = re.compile(invariant.pattern)
    files = _iter_matching_files(repo_root, invariant.paths)
    if not files:
        return []
    matched_any = False
    for file in files:
        try:
            text = file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if compiled.search(text):
            matched_any = True
            break
    if matched_any:
        return []
    return [
        InvariantViolation(
            invariant_id=invariant.id,
            severity=invariant.severity,
            description=invariant.description,
            detail=f"no file in {list(invariant.paths)} matched /{invariant.pattern}/",
        )
    ]


def _check_absent_regex(
    invariant: Invariant, repo_root: Path
) -> list[InvariantViolation]:
    if not invariant.pattern:
        return [
            InvariantViolation(
                invariant_id=invariant.id,
                severity=invariant.severity,
                description=invariant.description,
                detail="invariant declared an absent_regex matcher but no pattern",
            )
        ]
    compiled = re.compile(invariant.pattern)
    violations: list[InvariantViolation] = []
    for file in _iter_matching_files(repo_root, invariant.paths):
        try:
            text = file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for match in compiled.finditer(text):
            line_no = text[: match.start()].count("\n") + 1
            rel = str(file.relative_to(repo_root))
            violations.append(
                InvariantViolation(
                    invariant_id=invariant.id,
                    severity=invariant.severity,
                    description=invariant.description,
                    file=rel,
                    line=line_no,
                    detail=f"matched forbidden pattern /{invariant.pattern}/",
                )
            )
            break  # one hit per file is enough
    return violations


def _check_file_exists(
    invariant: Invariant, repo_root: Path
) -> list[InvariantViolation]:
    if not invariant.target:
        return [
            InvariantViolation(
                invariant_id=invariant.id,
                severity=invariant.severity,
                description=invariant.description,
                detail="invariant declared a file_exists matcher but no target template",
            )
        ]
    violations: list[InvariantViolation] = []
    for file in _iter_matching_files(repo_root, invariant.paths):
        rel = str(file.relative_to(repo_root))
        target_str = invariant.target.format(stem=file.stem, path=rel)
        target = repo_root / target_str
        if not target.is_file():
            violations.append(
                InvariantViolation(
                    invariant_id=invariant.id,
                    severity=invariant.severity,
                    description=invariant.description,
                    file=rel,
                    detail=f"expected companion file {target_str!r} does not exist",
                )
            )
    return violations


_MATCHERS: dict[str, Any] = {
    "regex": _check_regex,
    "absent_regex": _check_absent_regex,
    "file_exists": _check_file_exists,
}


def run_invariants(
    invariants: tuple[Invariant, ...],
    repo_root: str | Path,
) -> list[InvariantViolation]:
    """Run every invariant against ``repo_root`` and collect violations.

    Unknown matcher types produce a violation entry naming the rule
    so the author notices the typo rather than silently getting
    "all good."
    """
    root = Path(repo_root).resolve()
    out: list[InvariantViolation] = []
    for invariant in invariants:
        runner = _MATCHERS.get(invariant.matcher)
        if runner is None:
            out.append(
                InvariantViolation(
                    invariant_id=invariant.id,
                    severity=invariant.severity,
                    description=invariant.description,
                    detail=f"unknown matcher type {invariant.matcher!r}",
                )
            )
            continue
        try:
            out.extend(runner(invariant, root))
        except Exception as exc:  # noqa: BLE001  # one bad rule must not kill the run
            logger.warning(
                "Invariant %s raised during check: %s", invariant.id, exc,
                exc_info=True,
            )
            out.append(
                InvariantViolation(
                    invariant_id=invariant.id,
                    severity=invariant.severity,
                    description=invariant.description,
                    detail=f"matcher raised: {exc}",
                )
            )
    return out


# --- harness check integration -----------------------------------------


def run_business_rules(
    repo_root: str | Path,
    *,
    packs_dir: str | Path | None = None,
) -> tuple[str, str, list[InvariantViolation]]:
    """Composite runner for the harness_check pipeline.

    Returns ``(status, summary, violations)`` where status is one of
    ``pass | fail | not_configured``. ``fail`` only when at least one
    ``block``-severity violation fires; warn/info violations keep
    the status at ``pass`` but surface as details.

    Args:
        repo_root: Directory to scan.
        packs_dir: Explicit ``.context-packs/`` root; defaults to
            ``<repo_root>/.context-packs``.
    """
    root = Path(repo_root).resolve()
    packs_root = Path(packs_dir) if packs_dir else root / ".context-packs"
    if not packs_root.is_dir():
        return ("not_configured", "no .context-packs directory", [])

    all_invariants: list[Invariant] = []
    for entry in sorted(packs_root.iterdir()):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        all_invariants.extend(load_invariants(entry))

    if not all_invariants:
        return ("not_configured", "no invariants declared in any pack", [])

    violations = run_invariants(tuple(all_invariants), root)
    blockers = [v for v in violations if v.severity == "block"]
    if blockers:
        return (
            "fail",
            f"{len(blockers)} blocking invariant(s) failed",
            violations,
        )
    warn_count = sum(1 for v in violations if v.severity == "warn")
    info_count = sum(1 for v in violations if v.severity == "info")
    summary = (
        f"{len(all_invariants)} invariants checked, "
        f"0 blocking, {warn_count} warning, {info_count} info"
    )
    return ("pass", summary, violations)


__all__ = [
    "CHECKS_FILENAME",
    "Invariant",
    "InvariantViolation",
    "MATCHER_TYPES",
    "SEVERITIES",
    "load_invariants",
    "run_business_rules",
    "run_invariants",
]
