"""Architecture lint — encodes package dependency direction as enforceable rules.

Pack's monorepo has a strict layering:

::

    evals  ── may import from ──►  cli  ── may import from ──►  deepagents

Reverse imports are architectural errors: ``deepagents`` depending on
``cli`` or ``evals`` would invert the dependency, and ``cli`` importing
``evals`` conflates the CLI with its benchmarking wrapper.

This module provides:

1. A pure checker (``check_import``, ``check_file``) that tells you
   whether an import or a whole file violates the rules. No side
   effects, no LLM calls; can run in CI or from inside the reviewer
   sub-agent's critique.
2. An ``ArchLintMiddleware`` that inspects ``write_file`` / ``edit_file``
   tool calls, extracts imports from the new content, and rejects
   violations inline — the same teach-at-failure pattern as scope
   enforcement.

The ruleset is small and deterministic by design. New packages added
to the monorepo just update ``PACKAGE_EDGES`` below; no DSL, no
YAML-driven rule engine.

Phase D.1 of the agent-harness roadmap.
"""

from __future__ import annotations

import ast
import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from langchain.tools.tool_node import ToolCallRequest
    from langgraph.types import Command

logger = logging.getLogger(__name__)


# --- The ruleset --------------------------------------------------------


# Package graph: key package may import from any value in its set.
# Strict direction — if evals wants something in cli, fine; if cli wants
# something in evals, that's the violation we block. Keep this table
# minimal — new packages only get entries when they really land.
#
# This is the **fallback** graph used when no .harness/config.yaml is
# loaded. External repos ship their own dependency_rules through the
# config and override this default via ``edges_from_config``.
PACKAGE_EDGES: dict[str, frozenset[str]] = {
    "deepagents_evals": frozenset({"deepagents_harbor", "deepagents_cli", "deepagents"}),
    "deepagents_harbor": frozenset({"deepagents_cli", "deepagents"}),
    "deepagents_cli": frozenset({"deepagents"}),
    "deepagents": frozenset(),
}
"""Allowed direct imports for each of Pack's first-party packages.

Values are the set of packages the key may depend on. Anything not
in the set (and not itself) is an arch violation.
"""


# The prefixes we actually enforce against. Imports of external
# packages (langchain, pytest, numpy…) are out of scope for this
# linter — that's the package-management layer's job.
_FIRST_PARTY = frozenset(PACKAGE_EDGES)


def edges_from_config(config: object) -> dict[str, frozenset[str]] | None:
    """Compile a ``HarnessConfig`` into a PACKAGE_EDGES-shaped graph.

    Reads ``config.packages`` and ``config.dependency_rules``,
    matches each rule's ``from`` and ``may_import`` / ``may_not_import``
    glob fragments against the declared package paths, and produces
    the per-package edge set the rest of arch-lint already understands.

    Resolution rules:

    1. A rule's ``from`` glob picks an importer package — the unique
       declared package whose ``path`` is contained in the glob's
       prefix. Ambiguous matches log a warning and the rule is
       skipped (don't silently apply to multiple packages).
    2. ``may_import`` adds the matched targets to the importer's
       allow set.
    3. ``may_not_import`` is informational here — arch-lint's model
       is allow-list based, so a forbidden glob narrows the allow
       set to ``everyone EXCEPT this``. We compute that explicitly
       below.
    4. Packages not mentioned in any rule fall back to "may import
       everything declared." This is permissive on purpose: the
       config author explicitly opts into restriction by writing
       a rule.

    Returns ``None`` when ``config`` is ``None`` or has no
    ``dependency_rules`` — caller falls back to the hardcoded
    PACKAGE_EDGES.
    """
    if config is None:
        return None
    packages = getattr(config, "packages", ())
    rules = getattr(config, "dependency_rules", ())
    if not packages or not rules:
        return None

    # Map package name → its declared path so we can match globs.
    pkg_paths: dict[str, str] = {
        p.name: p.path.replace("\\", "/").rstrip("/")
        for p in packages
        if hasattr(p, "name") and hasattr(p, "path")
    }
    pkg_names = list(pkg_paths)

    if not pkg_paths:
        return None

    # Default: every package may import every other package. Each
    # rule narrows from there.
    edges: dict[str, set[str]] = {
        name: set(other for other in pkg_names if other != name)
        for name in pkg_names
    }

    def _match_glob_to_packages(glob: str) -> list[str]:
        """Find every declared package whose path falls under ``glob``."""
        prefix = glob.replace("**", "").rstrip("/*")
        matches = [
            name
            for name, path in pkg_paths.items()
            if path.startswith(prefix) or prefix in path
        ]
        return matches

    for rule in rules:
        sources = _match_glob_to_packages(getattr(rule, "from_pattern", ""))
        if not sources:
            continue
        if len(sources) > 1:
            logger.warning(
                "Arch-lint config: rule from=%r matched multiple packages "
                "(%s); skipping. Tighten the glob to disambiguate.",
                rule.from_pattern,
                sources,
            )
            continue
        importer = sources[0]

        may_targets: set[str] = set()
        for glob in getattr(rule, "may_import", ()):
            may_targets.update(_match_glob_to_packages(glob))
        not_targets: set[str] = set()
        for glob in getattr(rule, "may_not_import", ()):
            not_targets.update(_match_glob_to_packages(glob))

        if may_targets:
            # Explicit allow-list: replace the permissive default.
            edges[importer] = {t for t in may_targets if t != importer}
        if not_targets:
            edges[importer] -= not_targets

    return {name: frozenset(targets) for name, targets in edges.items()}


# --- Types --------------------------------------------------------------


@dataclass(frozen=True)
class ArchViolation:
    """One import that breaks the package direction rules.

    Attributes:
        importer: First-party package the file belongs to
            (e.g. ``"deepagents"``).
        imported: First-party package the import names
            (e.g. ``"deepagents_cli"``).
        import_line: The literal import statement, verbatim.
        line_number: 1-based line in the source file. 0 when not known
            (e.g. when checking a raw import string).
    """

    importer: str
    imported: str
    import_line: str
    line_number: int = 0

    def summary(self) -> str:
        """Human-readable one-line summary for error messages."""
        loc = f" (line {self.line_number})" if self.line_number else ""
        return (
            f"`{self.importer}` may not import from `{self.imported}`"
            f"{loc}: {self.import_line.strip()}"
        )


# --- Path → package resolution ------------------------------------------


_PACKAGE_PATH_MAP: tuple[tuple[str, str], ...] = (
    # Ordered so more specific paths win. Matches are case-sensitive
    # and checked as substrings of the path's forward-slash form.
    ("libs/evals/deepagents_evals/", "deepagents_evals"),
    ("libs/evals/deepagents_harbor/", "deepagents_harbor"),
    ("libs/cli/deepagents_cli/", "deepagents_cli"),
    ("libs/deepagents/deepagents/", "deepagents"),
)


def package_for_path(path: str | None) -> str | None:
    """Return which first-party package ``path`` belongs to, or None.

    Uses a substring check against ``_PACKAGE_PATH_MAP``; absolute vs
    relative paths both resolve because we look for the monorepo-
    relative fragment. Tests and scripts (``libs/*/tests/…``,
    ``libs/*/scripts/…``) return None so they don't get enforced —
    test files legitimately touch implementation details across
    packages.
    """
    if not path:
        return None
    normalized = path.replace("\\", "/")
    for fragment, package in _PACKAGE_PATH_MAP:
        if fragment in normalized:
            # Exclude tests and scripts under the same package root.
            tail = normalized.split(fragment, 1)[1]
            head = tail.split("/", 1)[0] if tail else ""
            if head in {"tests", "test"} or normalized.rsplit("/", 1)[-1].startswith("test_"):
                return None
            return package
    return None


# --- Import extraction --------------------------------------------------


_IMPORT_FROM_RE = re.compile(r"^\s*from\s+([\w.]+)\s+import\s+", re.MULTILINE)
# Keep the name list single-line so we don't swallow code that follows
# a broken import on the next line.
_IMPORT_RE = re.compile(r"^\s*import\s+([\w.,\s]+?)(?:$|#)", re.MULTILINE)


def extract_imports(source: str) -> list[tuple[str, int, str]]:
    """Return ``(module, line_number, raw_line)`` for every top-level import.

    Prefers an AST parse for correctness; falls back to a regex scan
    when the source isn't valid Python (e.g. a partial edit during
    iteration). The regex path only catches top-of-line imports, which
    is fine for lint — nested or inside-function imports are rare in
    Pack's codebase and a later full-parse run will catch them.
    """
    out: list[tuple[str, int, str]] = []
    raw_lines = source.splitlines()

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return _extract_imports_regex(source)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                line = raw_lines[node.lineno - 1] if node.lineno <= len(raw_lines) else ""
                out.append((alias.name, node.lineno, line))
        elif isinstance(node, ast.ImportFrom):
            # node.module can be None for `from . import foo`; skip
            # relative/no-module cases since they can't cross a
            # package boundary.
            if node.module:
                line = raw_lines[node.lineno - 1] if node.lineno <= len(raw_lines) else ""
                out.append((node.module, node.lineno, line))
    return out


def _extract_imports_regex(source: str) -> list[tuple[str, int, str]]:
    """Fallback regex import scanner for unparseable source."""
    out: list[tuple[str, int, str]] = []
    for match in _IMPORT_FROM_RE.finditer(source):
        line_no = source[: match.start()].count("\n") + 1
        raw = source.splitlines()[line_no - 1] if line_no <= source.count("\n") + 1 else ""
        out.append((match.group(1), line_no, raw))
    for match in _IMPORT_RE.finditer(source):
        # `import a, b` → split; each name is checked individually
        line_no = source[: match.start()].count("\n") + 1
        raw = source.splitlines()[line_no - 1] if line_no <= source.count("\n") + 1 else ""
        names = [n.strip() for n in match.group(1).split(",")]
        for name in names:
            if name:
                out.append((name, line_no, raw))
    return out


# --- Checkers -----------------------------------------------------------


def _top_level_package(module: str) -> str | None:
    """Return the first-party package the imported module belongs to."""
    head = module.split(".", 1)[0]
    return head if head in _FIRST_PARTY else None


def check_import(
    importer: str,
    module: str,
    *,
    line: str = "",
    line_number: int = 0,
    edges: dict[str, frozenset[str]] | None = None,
) -> ArchViolation | None:
    """Check one import. Return a violation or None if allowed.

    The ``importer`` is the package the source file belongs to; the
    ``module`` is whatever the ``import`` statement names. External
    imports (not in the active edge map) are always allowed — this
    linter only enforces first-party direction.

    Args:
        edges: Override the hardcoded ``PACKAGE_EDGES`` graph. When
            ``None`` (default) the hardcoded graph applies. Repos
            with their own ``.harness/config.yaml`` derive an edge
            map via :func:`edges_from_config` and pass it here.
    """
    active_edges = edges if edges is not None else PACKAGE_EDGES
    if importer not in active_edges:
        # Unknown importer package: no rules to enforce.
        return None

    first_party = frozenset(active_edges)
    head = module.split(".", 1)[0]
    imported = head if head in first_party else None
    if imported is None or imported == importer:
        return None  # external or self-import — fine

    allowed = active_edges[importer]
    if imported in allowed:
        return None

    return ArchViolation(
        importer=importer,
        imported=imported,
        import_line=line,
        line_number=line_number,
    )


def check_file(
    path: str,
    source: str,
    *,
    edges: dict[str, frozenset[str]] | None = None,
) -> list[ArchViolation]:
    """Check every import in ``source`` against its path-derived package.

    Returns the list of violations. Empty list means clean.
    """
    importer = package_for_path(path)
    if importer is None:
        return []  # test/script or external path — not enforced
    violations: list[ArchViolation] = []
    for module, line_no, raw in extract_imports(source):
        violation = check_import(
            importer,
            module,
            line=raw,
            line_number=line_no,
            edges=edges,
        )
        if violation is not None:
            violations.append(violation)
    return violations


# --- Middleware ---------------------------------------------------------


_WRITE_TOOL_NAMES = frozenset({"write_file", "edit_file"})


class ArchLintMiddleware(AgentMiddleware):
    """Reject writes that would introduce new architectural violations.

    Uses a simple ratchet: violations already on disk are tolerated
    (callers pre-seed them), new violations in the agent's proposed
    content are blocked. This lets the middleware run against a
    codebase that isn't yet fully clean without blocking progress.

    Args:
        existing_violations: Set of ``(importer, imported)`` pairs
            that already exist in the repo and should not be treated
            as new errors. Passed in by the harness from the ratchet.
        violation_recorder: Optional callable invoked on each
            rejection with ``(path, violation)``. Integrates with the
            ratchet when supplied.
        disabled: Hard kill-switch.
    """

    def __init__(
        self,
        *,
        existing_violations: set[tuple[str, str]] | None = None,
        violation_recorder: Callable[[str, ArchViolation], None] | None = None,
        disabled: bool = False,
        edges: dict[str, frozenset[str]] | None = None,
        repo_root: str | None = None,
    ) -> None:
        self.existing_violations = existing_violations or set()
        self.violation_recorder = violation_recorder
        self.disabled = disabled
        # When a config-derived edge map is supplied, use it; otherwise
        # the hardcoded PACKAGE_EDGES governs.
        self.edges = edges
        # Repo root is needed to compose post-edit content for
        # edit_file calls (sharp edge 4). When None, edit_file falls
        # back to the previous "scan new_string only" behaviour.
        self.repo_root = repo_root

    def _check(self, request: ToolCallRequest) -> ToolMessage | None:
        if self.disabled:
            return None
        tool_name = request.tool_call.get("name", "")
        if tool_name not in _WRITE_TOOL_NAMES:
            return None

        args = request.tool_call.get("args") or {}
        path = args.get("path") or args.get("file_path")
        if not isinstance(path, str):
            return None

        source = self._compose_proposed_source(tool_name, path, args)
        if not source:
            return None

        violations = check_file(path, source, edges=self.edges)
        if not violations:
            return None

        fresh = [
            v for v in violations
            if (v.importer, v.imported) not in self.existing_violations
        ]
        if not fresh:
            return None

        if self.violation_recorder:
            for violation in fresh:
                self.violation_recorder(path, violation)

        return _reject(
            tool_call=request.tool_call,
            path=path,
            violations=fresh,
            edges=self.edges,
        )

    def _compose_proposed_source(
        self,
        tool_name: str,
        path: str,
        args: dict[str, Any],
    ) -> str:
        """Return the content arch-lint should scan for this tool call.

        For ``write_file`` the answer is just ``args["content"]``.
        For ``edit_file`` we ideally want the **composed** post-edit
        text — old_string replaced with new_string in the existing
        file — so violations that only manifest in full context get
        caught. When ``repo_root`` is set we read and compose; when
        it isn't, we fall back to scanning ``new_string`` alone (the
        previous behaviour, which the review correctly flagged as a
        blind spot).
        """
        if tool_name == "write_file":
            content = args.get("content")
            return content if isinstance(content, str) else ""

        # edit_file
        new_string = args.get("new_string")
        old_string = args.get("old_string")
        if not isinstance(new_string, str):
            return ""

        if self.repo_root and isinstance(old_string, str):
            try:
                # Resolve path against repo_root if it's not absolute.
                from pathlib import Path

                resolved = (
                    Path(path) if Path(path).is_absolute()
                    else Path(self.repo_root) / path.lstrip("/")
                )
                if resolved.is_file():
                    current = resolved.read_text(encoding="utf-8", errors="replace")
                    if old_string and old_string in current:
                        return current.replace(old_string, new_string)
                    # old_string didn't match — scanning new_string
                    # alone is the safer fallback.
                    return new_string
            except OSError:
                pass

        return new_string

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        rejection = self._check(request)
        if rejection is not None:
            logger.warning(
                "ArchLint: rejected %s on %s with %d fresh violation(s)",
                request.tool_call.get("name"),
                request.tool_call.get("args", {}).get("path"),
                rejection.content.count("\n- "),
            )
            return rejection
        return handler(request)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        rejection = self._check(request)
        if rejection is not None:
            return rejection
        return await handler(request)


def _reject(
    *,
    tool_call: dict[str, Any],
    path: str,
    violations: list[ArchViolation],
    edges: dict[str, frozenset[str]] | None = None,
) -> ToolMessage:
    """Build a teach-at-failure rejection that explains the arch rule.

    Surfaces whichever edge map is active so the agent's suggested
    fix matches the rules actually being enforced. Falls back to
    PACKAGE_EDGES when no override is supplied.
    """
    active = edges if edges is not None else PACKAGE_EDGES
    bullet_lines = "\n".join(f"- {v.summary()}" for v in violations)
    allowed_map = "\n".join(
        f"  - `{src}` may import from: "
        f"{', '.join(sorted(targets)) if targets else '(nothing)'}"
        for src, targets in sorted(active.items())
    )
    content = (
        f"⛔️ Arch-lint violation in `{path}`:\n\n"
        f"{bullet_lines}\n\n"
        "Package direction is fixed — later layers depend on earlier "
        "ones, never the reverse:\n\n"
        f"{allowed_map}\n\n"
        "Either move the importing code into the correct layer, or "
        "invert the dependency (define the API in the lower layer and "
        "have the higher layer implement it). See "
        "`docs/harness/components.md` for the full layering diagram."
    )
    return ToolMessage(
        content=content,
        name=tool_call.get("name", "write_file"),
        tool_call_id=tool_call["id"],
        status="error",
    )


__all__ = [
    "PACKAGE_EDGES",
    "ArchLintMiddleware",
    "ArchViolation",
    "check_file",
    "check_import",
    "extract_imports",
    "package_for_path",
]
