"""Tests for ToolResultEnrichmentMiddleware."""

from __future__ import annotations

from typing import Any
from unittest.mock import Mock

from langchain_core.messages import ToolMessage

from deepagents_cli.tool_result_enrichment import (
    ToolResultEnrichmentMiddleware,
    _derive_execute,
    _derive_list_directory,
    _derive_match_count,
    _derive_read_file,
    _fmt_bytes,
)


def _tool_message(content: str, name: str = "read_file") -> ToolMessage:
    return ToolMessage(content=content, name=name, tool_call_id="tc-1")


def _request(name: str, args: dict[str, Any] | None = None) -> Any:
    req = Mock()
    req.tool_call = {"name": name, "args": args or {}, "id": "tc-1"}
    return req


# ---------------------------------------------------------------------------
# _fmt_bytes
# ---------------------------------------------------------------------------


def test_fmt_bytes_small() -> None:
    assert _fmt_bytes(512) == "512B"


def test_fmt_bytes_kb() -> None:
    assert _fmt_bytes(2048) == "2.0KB"


def test_fmt_bytes_mb() -> None:
    assert _fmt_bytes(2 * 1024 * 1024) == "2.0MB"


# ---------------------------------------------------------------------------
# _derive_read_file
# ---------------------------------------------------------------------------


def test_read_file_renders_line_count_and_size() -> None:
    content = "a\nb\nc\n"
    marker = _derive_read_file(content, {"path": "/app/main.py"})
    assert marker is not None
    assert "3 lines" in marker
    assert ".py" in marker


def test_read_file_no_extension() -> None:
    marker = _derive_read_file("hello\n", {"path": "/app/README"})
    assert marker is not None
    assert "README" not in marker  # only extension not path is surfaced
    assert "1 lines" in marker


def test_read_file_empty() -> None:
    marker = _derive_read_file("", {"path": "/app/empty.txt"})
    assert marker is not None
    assert "0 lines" in marker


def test_read_file_no_trailing_newline_counts_last_line() -> None:
    # Content without trailing newline still counts the final line.
    marker = _derive_read_file("one\ntwo", {"path": "f.txt"})
    assert "2 lines" in marker


# ---------------------------------------------------------------------------
# _derive_list_directory
# ---------------------------------------------------------------------------


def test_list_directory_entry_count() -> None:
    content = "main.py\nREADME.md\ntests/\nsrc/\n"
    marker = _derive_list_directory(content)
    assert marker is not None
    assert "4 entries" in marker
    assert "2 subdirs" in marker


def test_list_directory_empty() -> None:
    assert _derive_list_directory("") == "[dir: empty]"


def test_list_directory_no_subdirs() -> None:
    marker = _derive_list_directory("a.txt\nb.txt\n")
    assert marker is not None
    assert "2 entries" in marker
    assert "0 subdirs" in marker


# ---------------------------------------------------------------------------
# _derive_execute
# ---------------------------------------------------------------------------


def test_execute_parses_exit_code() -> None:
    content = "STDOUT:\nhello\nSTDERR:\n\nExit code: 0"
    marker = _derive_execute(content)
    assert marker is not None
    assert "exit=0" in marker


def test_execute_nonzero_exit() -> None:
    content = "STDOUT:\n\nSTDERR:\nerror: no such file\nExit code: 1"
    marker = _derive_execute(content)
    assert marker is not None
    assert "exit=1" in marker


def test_execute_returns_none_on_unrecognized_shape() -> None:
    assert _derive_execute("just some raw output") is None


# ---------------------------------------------------------------------------
# _derive_match_count
# ---------------------------------------------------------------------------


def test_match_count_counts_nonempty_lines() -> None:
    assert _derive_match_count("a\nb\nc\n", "matches") == "[matches: 3]"


def test_match_count_zero() -> None:
    assert _derive_match_count("", "matches") == "[matches: 0]"


# ---------------------------------------------------------------------------
# Middleware integration
# ---------------------------------------------------------------------------


def test_middleware_appends_marker_to_read_file() -> None:
    m = ToolResultEnrichmentMiddleware()
    request = _request("read_file", {"path": "/app/foo.py"})

    def handler(_req: Any) -> ToolMessage:
        return _tool_message("def f(): pass\n", name="read_file")

    result = m.wrap_tool_call(request, handler)
    text = str(result.content)
    assert "def f(): pass" in text
    assert "[file:" in text
    assert ".py" in text


def test_middleware_passes_through_unknown_tool() -> None:
    m = ToolResultEnrichmentMiddleware()
    request = _request("mcp__custom__tool", {})

    def handler(_req: Any) -> ToolMessage:
        return _tool_message("custom output", name="mcp__custom__tool")

    result = m.wrap_tool_call(request, handler)
    assert str(result.content) == "custom output"


def test_disabled_noop() -> None:
    m = ToolResultEnrichmentMiddleware(disabled=True)
    request = _request("read_file", {"path": "/app/foo.py"})

    def handler(_req: Any) -> ToolMessage:
        return _tool_message("content\n", name="read_file")

    result = m.wrap_tool_call(request, handler)
    assert str(result.content) == "content\n"


def test_extra_derivation_merges_with_defaults() -> None:
    def custom(content: str, _args: dict[str, Any]) -> str:
        return f"[custom: {len(content)}]"

    m = ToolResultEnrichmentMiddleware(extra_derivations={"my_tool": custom})
    request = _request("my_tool", {})

    def handler(_req: Any) -> ToolMessage:
        return _tool_message("abcdef", name="my_tool")

    result = m.wrap_tool_call(request, handler)
    assert "[custom: 6]" in str(result.content)


def test_derivation_failure_is_swallowed() -> None:
    def boom(_content: str, _args: dict[str, Any]) -> str:
        raise RuntimeError("oops")

    m = ToolResultEnrichmentMiddleware(extra_derivations={"my_tool": boom})
    request = _request("my_tool", {})

    def handler(_req: Any) -> ToolMessage:
        return _tool_message("raw", name="my_tool")

    # Must not raise — enrichment is best-effort
    result = m.wrap_tool_call(request, handler)
    assert str(result.content) == "raw"


async def test_async_wrap_enriches() -> None:
    m = ToolResultEnrichmentMiddleware()
    request = _request("read_file", {"path": "/app/a.py"})

    async def handler(_req: Any) -> ToolMessage:
        return _tool_message("x = 1\n", name="read_file")

    result = await m.awrap_tool_call(request, handler)
    assert "[file:" in str(result.content)


def test_non_tool_message_passes_through() -> None:
    m = ToolResultEnrichmentMiddleware()
    request = _request("read_file", {"path": "/app/a.py"})
    sentinel = Mock()

    def handler(_req: Any) -> Any:
        return sentinel  # not a ToolMessage

    result = m.wrap_tool_call(request, handler)
    assert result is sentinel
