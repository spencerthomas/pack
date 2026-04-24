"""Append derived-signal suffixes to tool results for fast LLM reasoning.

The raw output of ``read_file``, ``list_directory``, ``execute`` etc.
forces the agent to re-derive size, type, test-runnability, and exit
status on every return. Trajectory analysis shows agents re-read the
same file 3+ times on failing runs because the result shape has no
anchor.

This middleware intercepts ``wrap_tool_call`` and, after the real
handler runs, appends a compact one-line suffix to the tool message
that summarises the result in LLM-friendly terms. Examples:

- ``read_file`` -> ``[file: 42 lines, 1.2KB, .py]``
- ``list_directory`` -> ``[dir: 7 entries, 2 subdirs]``
- ``execute`` -> ``[exit=0, 4 lines stdout, 0 lines stderr]``
- ``glob`` / ``grep`` -> ``[matches: 12]``

Suffixes are additive — they don't replace or modify the existing
content, just append a marker the agent can pattern-match against.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from langchain.tools.tool_node import ToolCallRequest
    from langgraph.types import Command

logger = logging.getLogger(__name__)


def _file_ext(path: str) -> str | None:
    """Return a path's extension lowercased, or None."""
    _, ext = os.path.splitext(path)
    return ext.lower() if ext else None


def _fmt_bytes(n: int) -> str:
    """Human-readable byte size. Keeps suffix to 1 decimal max."""
    if n < 1024:
        return f"{n}B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f}KB"
    return f"{n / (1024 * 1024):.1f}MB"


def _derive_read_file(content: str, args: dict[str, Any]) -> str | None:
    """Signal for read_file: line count, byte size, extension."""
    if not isinstance(content, str):
        return None
    line_count = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
    byte_len = len(content.encode("utf-8", errors="replace"))
    path = args.get("path") or args.get("file_path") or ""
    ext = _file_ext(path) if isinstance(path, str) else None
    parts = [f"{line_count} lines", _fmt_bytes(byte_len)]
    if ext:
        parts.append(ext)
    return f"[file: {', '.join(parts)}]"


def _derive_list_directory(content: str) -> str | None:
    """Signal for ls/list_directory: entry count, subdir count."""
    if not isinstance(content, str):
        return None
    lines = [ln for ln in content.splitlines() if ln.strip()]
    if not lines:
        return "[dir: empty]"
    # Directories typically trail with "/" in our ls formatting. Heuristic,
    # not load-bearing — if misclassified the agent still sees total count.
    subdirs = sum(1 for ln in lines if ln.rstrip().endswith("/"))
    return f"[dir: {len(lines)} entries, {subdirs} subdirs]"


def _derive_execute(content: str) -> str | None:
    """Signal for shell execute: parse exit code + stdout/stderr line counts.

    Best-effort: result formats vary by backend. Looks for common markers
    (``exit_code``, ``exit=`` in-line JSON, explicit STDOUT/STDERR split).
    Returns None if the shape isn't recognised so we don't annotate noise.
    """
    if not isinstance(content, str):
        return None
    text = content.lower()
    exit_code: int | None = None
    # Common patterns: "exit code: 0", "exit=0", "returncode: 0"
    for marker in ("exit code:", "exit=", "returncode:", "exit_code:"):
        idx = text.find(marker)
        if idx != -1:
            tail = text[idx + len(marker) :].lstrip()
            num = ""
            for ch in tail:
                if ch.isdigit() or (ch == "-" and not num):
                    num += ch
                else:
                    break
            if num:
                try:
                    exit_code = int(num)
                except ValueError:
                    pass
                break

    # Line counts from STDOUT / STDERR sections when present
    def _section_lines(label: str) -> int | None:
        low = text.find(label.lower())
        if low == -1:
            return None
        # Take next ~2000 chars after the label for cheap counting
        window = content[low : low + 4000]
        # Strip the label line itself
        newline = window.find("\n")
        if newline == -1:
            return 0
        body = window[newline + 1 :]
        # Stop at the next section marker
        for stop in ("STDERR", "STDOUT", "Exit code", "exit code", "returncode"):
            j = body.find(stop)
            if j != -1:
                body = body[:j]
        return sum(1 for ln in body.splitlines() if ln.strip())

    stdout_lines = _section_lines("STDOUT")
    stderr_lines = _section_lines("STDERR")

    if exit_code is None and stdout_lines is None and stderr_lines is None:
        return None
    parts: list[str] = []
    if exit_code is not None:
        parts.append(f"exit={exit_code}")
    if stdout_lines is not None:
        parts.append(f"{stdout_lines} lines stdout")
    if stderr_lines is not None:
        parts.append(f"{stderr_lines} lines stderr")
    return f"[{', '.join(parts)}]"


def _derive_match_count(content: str, label: str) -> str | None:
    """Signal for glob / grep: just the match count."""
    if not isinstance(content, str):
        return None
    lines = [ln for ln in content.splitlines() if ln.strip()]
    return f"[{label}: {len(lines)}]"


# Dispatch table: tool name -> derivation callable (content, args) -> marker.
# Each derivation returns None when it can't produce a useful signal so the
# middleware silently skips annotation.
_DERIVATIONS: dict[str, Any] = {
    "read_file": lambda content, args: _derive_read_file(content, args),
    "ls": lambda content, _args: _derive_list_directory(content),
    "list_directory": lambda content, _args: _derive_list_directory(content),
    "execute": lambda content, _args: _derive_execute(content),
    "glob": lambda content, _args: _derive_match_count(content, "matches"),
    "grep": lambda content, _args: _derive_match_count(content, "matches"),
}


class ToolResultEnrichmentMiddleware(AgentMiddleware):
    """Append per-tool derived-signal markers to tool results.

    The goal is to reduce the agent's re-derivation work: instead of re-
    parsing a 500-line ``ls`` result to count entries, the agent sees
    ``[dir: 127 entries, 14 subdirs]`` at the tail and can pattern-match.

    Args:
        disabled: Turn off all enrichment; useful for test isolation or
            if a pathological interaction with downstream middleware is
            discovered.
        extra_derivations: Map of tool-name -> callable ``(content, args) ->
            str | None`` to plug custom signals without editing this module.
            User entries win on conflict with defaults.
    """

    def __init__(
        self,
        *,
        disabled: bool = False,
        extra_derivations: dict[str, Any] | None = None,
    ) -> None:
        self.disabled = disabled
        self._derivations: dict[str, Any] = dict(_DERIVATIONS)
        if extra_derivations:
            self._derivations.update(extra_derivations)

    def _enrich(
        self,
        result: ToolMessage | Command[Any],
        request: ToolCallRequest,
    ) -> ToolMessage | Command[Any]:
        if self.disabled or not isinstance(result, ToolMessage):
            return result

        tool_name = request.tool_call["name"]
        derive = self._derivations.get(tool_name)
        if derive is None:
            return result

        args = request.tool_call.get("args") or {}
        try:
            marker = derive(result.content, args)
        except Exception:  # noqa: BLE001  # enrichment must never crash a tool call
            logger.debug("ToolResultEnrichment: derivation failed for %s", tool_name, exc_info=True)
            return result
        if not marker:
            return result

        result.content = f"{result.content}\n\n{marker}"
        return result

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        """Run the tool, then append the derived-signal marker."""
        result = handler(request)
        return self._enrich(result, request)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        """Async version."""
        result = await handler(request)
        return self._enrich(result, request)


__all__ = ["ToolResultEnrichmentMiddleware"]
