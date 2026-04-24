"""Tests for arch-lint (Phase D.1)."""

from __future__ import annotations

from typing import Any
from unittest.mock import Mock

from langchain_core.messages import ToolMessage

from deepagents_cli.arch_lint import (
    PACKAGE_EDGES,
    ArchLintMiddleware,
    ArchViolation,
    check_file,
    check_import,
    extract_imports,
    package_for_path,
)


# ---------------------------------------------------------------------------
# PACKAGE_EDGES sanity
# ---------------------------------------------------------------------------


def test_deepagents_has_no_outbound_edges() -> None:
    # deepagents is the bottom of the stack — it must not depend on
    # anything Pack-owned above it.
    assert PACKAGE_EDGES["deepagents"] == frozenset()


def test_cli_depends_only_on_deepagents() -> None:
    assert PACKAGE_EDGES["deepagents_cli"] == frozenset({"deepagents"})


def test_harbor_depends_on_cli_and_deepagents() -> None:
    assert PACKAGE_EDGES["deepagents_harbor"] == frozenset(
        {"deepagents_cli", "deepagents"}
    )


# ---------------------------------------------------------------------------
# package_for_path
# ---------------------------------------------------------------------------


def test_package_for_path_deepagents() -> None:
    assert package_for_path("libs/deepagents/deepagents/graph.py") == "deepagents"
    assert package_for_path(
        "/Users/c/dev/pack/libs/deepagents/deepagents/prompt/builder.py"
    ) == "deepagents"


def test_package_for_path_cli() -> None:
    assert (
        package_for_path("libs/cli/deepagents_cli/agent.py") == "deepagents_cli"
    )


def test_package_for_path_harbor() -> None:
    assert (
        package_for_path("libs/evals/deepagents_harbor/deepagents_wrapper.py")
        == "deepagents_harbor"
    )


def test_package_for_path_tests_are_excluded() -> None:
    # Test files are legitimately cross-package; the linter ignores them.
    assert package_for_path("libs/cli/tests/unit_tests/test_agent.py") is None
    assert package_for_path("libs/deepagents/tests/unit_tests/test_graph.py") is None


def test_package_for_path_external_returns_none() -> None:
    assert package_for_path("some/random/path.py") is None
    assert package_for_path("") is None
    assert package_for_path(None) is None


# ---------------------------------------------------------------------------
# extract_imports
# ---------------------------------------------------------------------------


def test_extract_simple_import() -> None:
    source = "import foo\nimport bar.baz\n"
    names = [m for m, _, _ in extract_imports(source)]
    assert "foo" in names
    assert "bar.baz" in names


def test_extract_from_import() -> None:
    source = "from alpha.beta import gamma\n"
    results = extract_imports(source)
    assert len(results) == 1
    assert results[0][0] == "alpha.beta"


def test_extract_gives_line_numbers() -> None:
    source = "\n\nimport foo\n"
    results = extract_imports(source)
    assert results[0][1] == 3


def test_extract_handles_relative_imports() -> None:
    # Relative imports (`from . import x`) don't produce a first-party
    # crossing so the extractor can safely skip them.
    source = "from . import foo\nfrom .bar import baz\n"
    results = extract_imports(source)
    # Depending on AST behaviour, module may be None — we filter to
    # assertions about non-none entries only.
    for module, _, _ in results:
        assert module is not None


def test_extract_falls_back_on_syntax_error() -> None:
    # Malformed source → regex fallback still finds top-level imports.
    source = "import foo\ndef broken(:\n"
    names = [m for m, _, _ in extract_imports(source)]
    assert "foo" in names


def test_extract_handles_comma_imports() -> None:
    source = "import foo, bar\n"
    names = [m for m, _, _ in extract_imports(source)]
    assert "foo" in names
    assert "bar" in names


# ---------------------------------------------------------------------------
# check_import
# ---------------------------------------------------------------------------


def test_check_allows_deepagents_from_cli() -> None:
    assert check_import("deepagents_cli", "deepagents.graph") is None


def test_check_allows_deepagents_from_harbor() -> None:
    assert (
        check_import("deepagents_harbor", "deepagents.prompt.builder") is None
    )


def test_check_allows_cli_from_harbor() -> None:
    assert check_import("deepagents_harbor", "deepagents_cli.policy") is None


def test_check_blocks_cli_from_deepagents() -> None:
    v = check_import("deepagents", "deepagents_cli.agent")
    assert v is not None
    assert v.importer == "deepagents"
    assert v.imported == "deepagents_cli"


def test_check_blocks_harbor_from_cli() -> None:
    # CLI should not reach into Harbor — that's a reverse dependency.
    v = check_import("deepagents_cli", "deepagents_harbor.wrapper")
    assert v is not None


def test_check_allows_self_import() -> None:
    assert check_import("deepagents", "deepagents.submodule") is None


def test_check_allows_external_imports() -> None:
    # Third-party packages are out of scope for this linter.
    assert check_import("deepagents", "langchain.agents") is None
    assert check_import("deepagents_cli", "pytest") is None


def test_check_unknown_importer_is_noop() -> None:
    # An importer not in the ruleset (e.g. docs tooling) is ignored
    # entirely; no rules to enforce.
    assert check_import("some_other_pkg", "deepagents") is None


# ---------------------------------------------------------------------------
# check_file
# ---------------------------------------------------------------------------


def test_check_file_clean_cli_source() -> None:
    source = (
        "from deepagents import create_deep_agent\n"
        "import logging\n"
    )
    violations = check_file("libs/cli/deepagents_cli/agent.py", source)
    assert violations == []


def test_check_file_flags_reverse_import_in_deepagents() -> None:
    source = "from deepagents_cli.policy import TaskPolicy\n"
    violations = check_file(
        "libs/deepagents/deepagents/graph.py", source
    )
    assert len(violations) == 1
    assert violations[0].imported == "deepagents_cli"


def test_check_file_ignores_test_paths() -> None:
    # Tests are allowed to import whatever.
    source = "from deepagents_cli.policy import TaskPolicy\n"
    violations = check_file(
        "libs/deepagents/tests/unit_tests/test_graph.py", source
    )
    assert violations == []


def test_check_file_unknown_path_returns_empty() -> None:
    source = "from deepagents_cli import anything\n"
    violations = check_file("some/random/path.py", source)
    assert violations == []


def test_check_file_handles_multiple_violations() -> None:
    source = (
        "from deepagents_cli.policy import TaskPolicy\n"
        "from deepagents_harbor.wrapper import DeepAgentsWrapper\n"
    )
    violations = check_file(
        "libs/deepagents/deepagents/graph.py", source
    )
    assert len(violations) == 2
    imported = {v.imported for v in violations}
    assert imported == {"deepagents_cli", "deepagents_harbor"}


# ---------------------------------------------------------------------------
# ArchViolation.summary
# ---------------------------------------------------------------------------


def test_violation_summary_includes_line_number() -> None:
    v = ArchViolation(
        importer="deepagents",
        imported="deepagents_cli",
        import_line="from deepagents_cli import x",
        line_number=12,
    )
    s = v.summary()
    assert "deepagents" in s
    assert "deepagents_cli" in s
    assert "line 12" in s


def test_violation_summary_omits_line_when_zero() -> None:
    v = ArchViolation(
        importer="deepagents",
        imported="deepagents_cli",
        import_line="from deepagents_cli import x",
        line_number=0,
    )
    assert "line" not in v.summary()


# ---------------------------------------------------------------------------
# ArchLintMiddleware — integration with LangGraph tool-call contract
# ---------------------------------------------------------------------------


def _write_request(path: str, content: str) -> Any:
    req = Mock()
    req.tool_call = {
        "name": "write_file",
        "args": {"path": path, "content": content},
        "id": "tc-1",
    }
    return req


def _edit_request(path: str, new_string: str) -> Any:
    req = Mock()
    req.tool_call = {
        "name": "edit_file",
        "args": {"path": path, "old_string": "old", "new_string": new_string},
        "id": "tc-2",
    }
    return req


def _ok_handler(_req: Any) -> ToolMessage:
    return ToolMessage(content="written", name="write_file", tool_call_id="tc-1")


def test_middleware_allows_clean_write() -> None:
    m = ArchLintMiddleware()
    req = _write_request(
        "libs/cli/deepagents_cli/foo.py",
        "from deepagents.prompt import classify\n",
    )
    result = m.wrap_tool_call(req, _ok_handler)
    assert result.status != "error"


def test_middleware_blocks_reverse_import_in_deepagents() -> None:
    m = ArchLintMiddleware()
    req = _write_request(
        "libs/deepagents/deepagents/foo.py",
        "from deepagents_cli.policy import TaskPolicy\n",
    )
    result = m.wrap_tool_call(req, _ok_handler)
    assert result.status == "error"
    assert "deepagents_cli" in str(result.content)
    assert "package direction" in str(result.content).lower()


def test_middleware_ratchet_tolerates_existing_violation() -> None:
    # (deepagents, deepagents_cli) already known to exist → not blocked
    m = ArchLintMiddleware(
        existing_violations={("deepagents", "deepagents_cli")},
    )
    req = _write_request(
        "libs/deepagents/deepagents/foo.py",
        "from deepagents_cli.policy import TaskPolicy\n",
    )
    result = m.wrap_tool_call(req, _ok_handler)
    assert result.status != "error"


def test_middleware_ratchet_still_blocks_other_violations() -> None:
    # (deepagents, deepagents_cli) tolerated but (deepagents, deepagents_harbor) new
    m = ArchLintMiddleware(
        existing_violations={("deepagents", "deepagents_cli")},
    )
    req = _write_request(
        "libs/deepagents/deepagents/foo.py",
        (
            "from deepagents_cli.policy import TaskPolicy\n"
            "from deepagents_harbor.wrapper import W\n"
        ),
    )
    result = m.wrap_tool_call(req, _ok_handler)
    assert result.status == "error"
    assert "deepagents_harbor" in str(result.content)


def test_middleware_records_violations() -> None:
    recorded: list[tuple[str, ArchViolation]] = []

    def record(path: str, v: ArchViolation) -> None:
        recorded.append((path, v))

    m = ArchLintMiddleware(violation_recorder=record)
    req = _write_request(
        "libs/deepagents/deepagents/foo.py",
        "from deepagents_cli.policy import TaskPolicy\n",
    )
    m.wrap_tool_call(req, _ok_handler)
    assert len(recorded) == 1
    assert recorded[0][1].imported == "deepagents_cli"


def test_middleware_disabled_flag() -> None:
    m = ArchLintMiddleware(disabled=True)
    req = _write_request(
        "libs/deepagents/deepagents/foo.py",
        "from deepagents_cli.policy import TaskPolicy\n",
    )
    result = m.wrap_tool_call(req, _ok_handler)
    assert result.status != "error"  # enforcement skipped


def test_middleware_ignores_non_write_tools() -> None:
    m = ArchLintMiddleware()
    req = Mock()
    req.tool_call = {
        "name": "read_file",
        "args": {"path": "libs/deepagents/deepagents/foo.py"},
        "id": "tc-1",
    }
    handler_called = False

    def handler(_req: Any) -> ToolMessage:
        nonlocal handler_called
        handler_called = True
        return ToolMessage(content="ok", name="read_file", tool_call_id="tc-1")

    m.wrap_tool_call(req, handler)
    assert handler_called is True


def test_middleware_handles_edit_file_new_string() -> None:
    # edit_file only sees the replacement text; we scan that for imports.
    m = ArchLintMiddleware()
    req = _edit_request(
        "libs/deepagents/deepagents/foo.py",
        "from deepagents_cli.policy import TaskPolicy",
    )
    result = m.wrap_tool_call(req, _ok_handler)
    assert result.status == "error"


def test_middleware_ignores_empty_content() -> None:
    # A write with no content can't introduce imports.
    m = ArchLintMiddleware()
    req = _write_request("libs/deepagents/deepagents/foo.py", "")
    result = m.wrap_tool_call(req, _ok_handler)
    assert result.status != "error"


async def test_async_wrap_enforces_arch() -> None:
    m = ArchLintMiddleware()

    async def handler(_req: Any) -> ToolMessage:
        return ToolMessage(content="ok", name="write_file", tool_call_id="tc-1")

    req = _write_request(
        "libs/deepagents/deepagents/foo.py",
        "from deepagents_cli.policy import TaskPolicy\n",
    )
    result = await m.awrap_tool_call(req, handler)
    assert result.status == "error"
