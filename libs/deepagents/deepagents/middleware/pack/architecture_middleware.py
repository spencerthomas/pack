"""Architecture enforcement middleware — checks tool outputs against project rules."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage

logger = logging.getLogger(__name__)

# Tool names that modify files and should trigger architecture checks.
_FILE_MODIFYING_TOOLS = frozenset({
    "write_file",
    "edit_file",
    "apply_patch",
    "create_file",
    "str_replace_editor",
})


@dataclass
class DependencyRule:
    """A directed dependency rule: *source* may depend on *target* but not vice-versa."""

    source: str
    target: str
    description: str = ""


@dataclass
class ArchitectureRules:
    """Parsed representation of a .pack-rules.md file."""

    dependency_chain: list[str] = field(default_factory=list)
    dependency_descriptions: list[str] = field(default_factory=list)
    max_file_lines: int | None = None
    prohibited_patterns: list[tuple[str, str]] = field(default_factory=list)
    # Maps a directory prefix to the set of prefixes it must NOT import from.
    forbidden_imports: dict[str, set[str]] = field(default_factory=dict)

    @property
    def is_empty(self) -> bool:
        return (
            not self.dependency_chain
            and self.max_file_lines is None
            and not self.prohibited_patterns
            and not self.forbidden_imports
        )


def parse_rules(text: str) -> ArchitectureRules:
    """Parse a .pack-rules.md file into structured rules.

    Tolerates malformed content — unparseable sections are skipped with a warning.
    Returns an empty ``ArchitectureRules`` if nothing could be parsed.
    """
    rules = ArchitectureRules()

    # Split into sections by ## headings
    sections: dict[str, str] = {}
    current_heading = ""
    current_lines: list[str] = []

    for line in text.splitlines():
        heading_match = re.match(r"^##\s+(.+)$", line)
        if heading_match:
            if current_heading:
                sections[current_heading] = "\n".join(current_lines)
            current_heading = heading_match.group(1).strip().lower()
            current_lines = []
        else:
            current_lines.append(line)

    if current_heading:
        sections[current_heading] = "\n".join(current_lines)

    # --- Dependency Directions ---
    dep_section = sections.get("dependency directions", "")
    if dep_section:
        try:
            for line in dep_section.splitlines():
                line = line.strip().lstrip("- ")
                if not line:
                    continue
                # Chain format: `types/` -> `repo/` -> `service/` -> `ui/`
                if "->" in line:
                    # Check if it's a chain (multiple ->)
                    parts = [p.strip().strip("`").rstrip("/") for p in line.split("->")]
                    if len(parts) >= 2:
                        rules.dependency_chain = parts
                        rules.dependency_descriptions.append(line)
                # Prose rule: "ui/ must not import from repo/ directly"
                must_not_match = re.match(
                    r"`?(\w+/?)`?\s+must\s+not\s+import\s+from\s+`?(\w+/?)`?",
                    line,
                    re.IGNORECASE,
                )
                if must_not_match:
                    src = must_not_match.group(1).rstrip("/")
                    tgt = must_not_match.group(2).rstrip("/")
                    rules.forbidden_imports.setdefault(src, set()).add(tgt)
                    rules.dependency_descriptions.append(line)
        except Exception:
            logger.warning("Failed to parse dependency directions section", exc_info=True)

    # Build forbidden_imports from dependency chain as well.
    # In a chain A -> B -> C -> D, D cannot import from A or B (only from C).
    # More precisely, each layer can only import from layers to its left (upstream).
    # So if chain is [types, repo, service, ui], ui can import service but not repo or types?
    # Actually the arrow means dependency direction: types -> repo means repo depends on types.
    # So repo can import types, service can import repo and types, ui can import service/repo/types.
    # The forbidden direction is: types must not import from repo/service/ui.
    if rules.dependency_chain:
        chain = rules.dependency_chain
        for i, layer in enumerate(chain):
            # Layer at position i must not import from layers at positions > i
            forbidden = {chain[j] for j in range(i + 1, len(chain))}
            if forbidden:
                existing = rules.forbidden_imports.get(layer, set())
                rules.forbidden_imports[layer] = existing | forbidden

    # --- File Limits ---
    limits_section = sections.get("file limits", "")
    if limits_section:
        try:
            size_match = re.search(r"max\s+file\s+size:\s*(\d+)\s*lines", limits_section, re.IGNORECASE)
            if size_match:
                rules.max_file_lines = int(size_match.group(1))
        except Exception:
            logger.warning("Failed to parse file limits section", exc_info=True)

    # --- Style ---
    style_section = sections.get("style", "")
    if style_section:
        try:
            for line in style_section.splitlines():
                line = line.strip().lstrip("- ")
                if not line:
                    continue
                # "no print statements in production code"
                if re.search(r"no\s+print\s+statements", line, re.IGNORECASE):
                    rules.prohibited_patterns.append(
                        (r"\bprint\s*\(", "print() statement found. Rule: " + line)
                    )
                # "structured logging only"
                if re.search(r"structured\s+logging\s+only", line, re.IGNORECASE):
                    rules.prohibited_patterns.append(
                        (r"\bprint\s*\(", "print() statement found. Rule: " + line)
                    )
        except Exception:
            logger.warning("Failed to parse style section", exc_info=True)

    return rules


def check_violations(
    file_path: str,
    file_content: str,
    rules: ArchitectureRules,
) -> list[str]:
    """Check a file's content against the loaded architecture rules.

    Returns a list of human-readable violation strings.
    """
    violations: list[str] = []
    path_parts = Path(file_path).parts

    # --- Dependency direction checks ---
    if rules.forbidden_imports:
        # Determine which layer this file belongs to
        file_layer: str | None = None
        for layer in rules.forbidden_imports:
            if layer in path_parts or any(p.startswith(layer) for p in path_parts):
                file_layer = layer
                break

        if file_layer and file_layer in rules.forbidden_imports:
            forbidden = rules.forbidden_imports[file_layer]
            # Extract imports
            for line in file_content.splitlines():
                import_match = re.match(
                    r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))", line
                )
                if import_match:
                    module = import_match.group(1) or import_match.group(2)
                    module_parts = module.split(".")
                    for banned in forbidden:
                        if banned in module_parts or any(
                            p.startswith(banned) for p in module_parts
                        ):
                            violations.append(
                                f"Import violation: {file_path} imports from {module}. "
                                f"Rule: {file_layer}/ must not import from {banned}/.\n"
                                f"  REMEDIATION: Use a layer that {file_layer}/ is allowed to depend on instead of direct {banned}/ access."
                            )

    # --- File size checks ---
    if rules.max_file_lines is not None:
        line_count = file_content.count("\n") + (1 if file_content and not file_content.endswith("\n") else 0)
        if line_count > rules.max_file_lines:
            violations.append(
                f"File size: {line_count} lines exceeds limit of {rules.max_file_lines}.\n"
                f"  REMEDIATION: Split into smaller modules."
            )

    # --- Style / prohibited pattern checks ---
    for pattern, message in rules.prohibited_patterns:
        if re.search(pattern, file_content):
            violations.append(f"Style violation: {message}\n  REMEDIATION: Remove prohibited pattern.")

    return violations


def _extract_file_path(tool_name: str, args: dict[str, Any]) -> str | None:
    """Best-effort extraction of the target file path from tool call args."""
    # Different tools use different arg names
    for key in ("path", "file_path", "file", "filename", "file_name"):
        if key in args:
            return str(args[key])
    # str_replace_editor and similar
    if "command" in args and "path" in args:
        return str(args["path"])
    return None


def _extract_file_content(tool_name: str, args: dict[str, Any]) -> str | None:
    """Best-effort extraction of the *full* file content from tool call args.

    Only returns content for tools that provide the complete file body (e.g.
    write_file, create_file).  Partial-edit tools (edit_file, str_replace_editor)
    provide only a diff fragment, so we return None and let the caller fall back
    to reading the file from disk.
    """
    if tool_name in ("edit_file", "str_replace_editor", "apply_patch"):
        # These tools only carry partial content — skip.
        return None
    for key in ("content", "file_text", "text"):
        if key in args:
            return str(args[key])
    return None


class ArchitectureEnforcementMiddleware(AgentMiddleware):
    """Checks file-modifying tool calls against project architecture rules.

    Loads rules from ``.pack-rules.md`` in the working directory on init.
    If no rules file exists the middleware is completely inert (no-op).

    Args:
        working_dir: Path to the project working directory containing
            ``.pack-rules.md``.  Falls back to CWD if not provided.
    """

    def __init__(self, working_dir: str | Path | None = None) -> None:
        self._rules: ArchitectureRules | None = None
        self._working_dir = Path(working_dir) if working_dir else Path.cwd()
        self._rules_path = self._working_dir / ".pack-rules.md"

        if self._rules_path.is_file():
            try:
                text = self._rules_path.read_text(encoding="utf-8")
                self._rules = parse_rules(text)
                if self._rules.is_empty:
                    logger.warning(
                        "Parsed .pack-rules.md but found no actionable rules — middleware inert"
                    )
                    self._rules = None
                else:
                    logger.info("Loaded architecture rules from %s", self._rules_path)
            except Exception:
                logger.warning("Failed to read/parse .pack-rules.md — middleware inert", exc_info=True)
                self._rules = None

    @property
    def active(self) -> bool:
        """Whether the middleware has loaded actionable rules."""
        return self._rules is not None

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        """Check file-modifying tool calls against architecture rules.

        Non-file-modifying tools and all tools when no rules are loaded pass
        through without inspection.
        """
        # Always execute the tool first
        result = await handler(request)

        if self._rules is None:
            return result

        # Extract tool call info
        call = getattr(request, "call", None) or {}
        tool_name = call.get("name", "") if isinstance(call, dict) else ""
        args = call.get("args", {}) if isinstance(call, dict) else {}
        tool_call_id = call.get("id", "") if isinstance(call, dict) else ""

        if tool_name not in _FILE_MODIFYING_TOOLS:
            return result

        file_path = _extract_file_path(tool_name, args)
        if not file_path:
            return result

        # Try to get file content from args; for edits we may need to read the file
        file_content = _extract_file_content(tool_name, args)
        if file_content is None:
            # Try reading the actual file after the tool executed
            full_path = self._working_dir / file_path
            if full_path.is_file():
                try:
                    file_content = full_path.read_text(encoding="utf-8")
                except Exception:
                    logger.debug("Could not read %s for architecture check", full_path)
                    return result
            else:
                return result

        violations = check_violations(file_path, file_content, self._rules)

        if not violations:
            return result

        violation_text = (
            f"ARCHITECTURE VIOLATION in {file_path}:\n"
            + "\n".join(f"- {v}" for v in violations)
        )
        logger.warning(violation_text)

        # Append violation info as a system-level message after the tool result.
        # We return a ToolMessage that includes both the original content and the violation.
        original_content = ""
        if isinstance(result, ToolMessage):
            original_content = result.content
            return ToolMessage(
                content=f"{original_content}\n\n{violation_text}",
                tool_call_id=result.tool_call_id,
            )

        # If result is not a ToolMessage, return as-is (shouldn't happen normally)
        return result
