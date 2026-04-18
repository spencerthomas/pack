"""Tests for ArchitectureEnforcementMiddleware."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import ToolMessage

from deepagents.middleware.pack.architecture_middleware import (
    ArchitectureEnforcementMiddleware,
    ArchitectureRules,
    check_violations,
    parse_rules,
)

SAMPLE_RULES = """\
## Dependency Directions
- `types/` -> `repo/` -> `service/` -> `ui/`
- ui/ must not import from repo/ directly

## File Limits
- Max file size: 400 lines

## Style
- Structured logging only (no print statements in production code)
"""


def _make_request(tool_name: str, args: dict, tool_call_id: str = "tc1") -> MagicMock:
    request = MagicMock()
    request.call = {"name": tool_name, "args": args, "id": tool_call_id}
    return request


class TestParseRules:
    def test_parses_dependency_chain(self) -> None:
        rules = parse_rules(SAMPLE_RULES)
        assert rules.dependency_chain == ["types", "repo", "service", "ui"]

    def test_parses_forbidden_imports(self) -> None:
        rules = parse_rules(SAMPLE_RULES)
        # ui/ must not import from repo/ (explicit rule)
        assert "repo" in rules.forbidden_imports.get("ui", set())
        # From chain: types/ must not import from repo/, service/, ui/
        assert "repo" in rules.forbidden_imports.get("types", set())
        assert "service" in rules.forbidden_imports.get("types", set())
        assert "ui" in rules.forbidden_imports.get("types", set())

    def test_parses_file_limits(self) -> None:
        rules = parse_rules(SAMPLE_RULES)
        assert rules.max_file_lines == 400

    def test_parses_style_rules(self) -> None:
        rules = parse_rules(SAMPLE_RULES)
        assert len(rules.prohibited_patterns) > 0
        # Should detect print() as prohibited
        pattern, msg = rules.prohibited_patterns[0]
        assert "print" in pattern

    def test_empty_text_returns_empty_rules(self) -> None:
        rules = parse_rules("")
        assert rules.is_empty

    def test_malformed_sections_dont_crash(self) -> None:
        text = """\
## Dependency Directions
- not a valid chain at all, just random text

## File Limits
- no numbers here

## Style
- something unrecognized
"""
        rules = parse_rules(text)
        # Should not raise; some fields may be empty
        assert rules.max_file_lines is None


class TestCheckViolations:
    def test_import_violation_detected(self) -> None:
        rules = parse_rules(SAMPLE_RULES)
        content = "from repo.database import get_user\n"
        violations = check_violations("src/ui/component.py", content, rules)
        assert len(violations) >= 1
        assert "Import violation" in violations[0]
        assert "repo" in violations[0]

    def test_file_size_violation(self) -> None:
        rules = parse_rules(SAMPLE_RULES)
        content = "\n".join(f"line {i}" for i in range(450))
        violations = check_violations("src/big_file.py", content, rules)
        size_violations = [v for v in violations if "File size" in v]
        assert len(size_violations) == 1
        assert "450" in size_violations[0]
        assert "400" in size_violations[0]

    def test_style_violation_print(self) -> None:
        rules = parse_rules(SAMPLE_RULES)
        content = 'print("debug")\n'
        violations = check_violations("src/service/handler.py", content, rules)
        style_violations = [v for v in violations if "Style violation" in v]
        assert len(style_violations) >= 1

    def test_no_violations_for_compliant_code(self) -> None:
        rules = parse_rules(SAMPLE_RULES)
        content = """\
import logging

logger = logging.getLogger(__name__)

def handle():
    logger.info("handling")
"""
        violations = check_violations("src/service/handler.py", content, rules)
        assert violations == []

    def test_compliant_import_direction(self) -> None:
        """service/ importing from repo/ is allowed by the chain."""
        rules = parse_rules(SAMPLE_RULES)
        content = "from repo.models import User\n"
        violations = check_violations("src/service/handler.py", content, rules)
        import_violations = [v for v in violations if "Import violation" in v]
        assert import_violations == []


class TestMiddlewareInert:
    async def test_no_rules_file_is_inert(self, tmp_path: Path) -> None:
        middleware = ArchitectureEnforcementMiddleware(working_dir=tmp_path)
        assert not middleware.active

        handler = AsyncMock(return_value=ToolMessage(content="ok", tool_call_id="tc1"))
        request = _make_request("write_file", {"path": "foo.py", "content": 'print("hi")'})

        result = await middleware.awrap_tool_call(request, handler)
        assert result.content == "ok"
        handler.assert_called_once()

    async def test_malformed_rules_file_is_inert(self, tmp_path: Path) -> None:
        rules_file = tmp_path / ".pack-rules.md"
        rules_file.write_text("just some random text with no sections\n")

        middleware = ArchitectureEnforcementMiddleware(working_dir=tmp_path)
        assert not middleware.active

        handler = AsyncMock(return_value=ToolMessage(content="ok", tool_call_id="tc1"))
        request = _make_request("write_file", {"path": "foo.py", "content": 'print("hi")'})

        result = await middleware.awrap_tool_call(request, handler)
        assert result.content == "ok"


class TestMiddlewareActive:
    @pytest.fixture()
    def rules_dir(self, tmp_path: Path) -> Path:
        rules_file = tmp_path / ".pack-rules.md"
        rules_file.write_text(SAMPLE_RULES)
        return tmp_path

    async def test_non_file_modifying_tool_bypasses_checks(self, rules_dir: Path) -> None:
        middleware = ArchitectureEnforcementMiddleware(working_dir=rules_dir)
        assert middleware.active

        handler = AsyncMock(return_value=ToolMessage(content="found it", tool_call_id="tc1"))
        request = _make_request("read_file", {"path": "foo.py"})

        result = await middleware.awrap_tool_call(request, handler)
        assert result.content == "found it"

    async def test_dependency_violation_appended_to_result(self, rules_dir: Path) -> None:
        middleware = ArchitectureEnforcementMiddleware(working_dir=rules_dir)

        violating_content = "from repo.database import get_user\n"
        handler = AsyncMock(
            return_value=ToolMessage(content="File written.", tool_call_id="tc1")
        )
        request = _make_request(
            "write_file", {"path": "ui/component.py", "content": violating_content}
        )

        result = await middleware.awrap_tool_call(request, handler)
        assert "ARCHITECTURE VIOLATION" in result.content
        assert "Import violation" in result.content
        assert "repo" in result.content

    async def test_file_size_violation_detected(self, rules_dir: Path) -> None:
        middleware = ArchitectureEnforcementMiddleware(working_dir=rules_dir)

        big_content = "\n".join(f"x = {i}" for i in range(450))
        handler = AsyncMock(
            return_value=ToolMessage(content="File written.", tool_call_id="tc1")
        )
        request = _make_request(
            "write_file", {"path": "src/module.py", "content": big_content}
        )

        result = await middleware.awrap_tool_call(request, handler)
        assert "ARCHITECTURE VIOLATION" in result.content
        assert "File size" in result.content
        assert "450" in result.content

    async def test_compliant_write_passes_through(self, rules_dir: Path) -> None:
        middleware = ArchitectureEnforcementMiddleware(working_dir=rules_dir)

        good_content = "import logging\n\nlogger = logging.getLogger(__name__)\n"
        handler = AsyncMock(
            return_value=ToolMessage(content="File written.", tool_call_id="tc1")
        )
        request = _make_request(
            "write_file", {"path": "service/handler.py", "content": good_content}
        )

        result = await middleware.awrap_tool_call(request, handler)
        assert result.content == "File written."
        assert "VIOLATION" not in result.content

    async def test_edit_file_reads_from_disk_on_missing_content(self, rules_dir: Path) -> None:
        """When tool args don't contain full content, middleware reads the file."""
        # Create the file on disk with a violation
        target = rules_dir / "ui" / "bad.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("from repo.models import User\n" * 5)

        middleware = ArchitectureEnforcementMiddleware(working_dir=rules_dir)

        handler = AsyncMock(
            return_value=ToolMessage(content="Edit applied.", tool_call_id="tc1")
        )
        # edit_file typically has old_str/new_str, not full content
        request = _make_request(
            "edit_file", {"path": "ui/bad.py", "old_str": "x", "new_str": "y"}
        )

        result = await middleware.awrap_tool_call(request, handler)
        assert "ARCHITECTURE VIOLATION" in result.content

    async def test_style_violation_print_statement(self, rules_dir: Path) -> None:
        middleware = ArchitectureEnforcementMiddleware(working_dir=rules_dir)

        content = 'import os\n\ndef run():\n    print("debugging")\n'
        handler = AsyncMock(
            return_value=ToolMessage(content="File written.", tool_call_id="tc1")
        )
        request = _make_request(
            "create_file", {"path": "service/run.py", "content": content}
        )

        result = await middleware.awrap_tool_call(request, handler)
        assert "ARCHITECTURE VIOLATION" in result.content
        assert "Style violation" in result.content

    async def test_handler_always_called(self, rules_dir: Path) -> None:
        """The tool is always executed — violations are advisory, not blocking."""
        middleware = ArchitectureEnforcementMiddleware(working_dir=rules_dir)

        handler = AsyncMock(
            return_value=ToolMessage(content="Done.", tool_call_id="tc1")
        )
        request = _make_request(
            "write_file",
            {"path": "ui/x.py", "content": "from repo.db import x\n"},
        )

        await middleware.awrap_tool_call(request, handler)
        handler.assert_called_once_with(request)
