"""File size enforcement tests.

Scans all .py files under deepagents/ and flags any that exceed size
thresholds.  Large files are a code-smell indicating a module is doing
too much and should be decomposed.

- Warning threshold : 500 lines  (informational, does not fail)
- Failure threshold : 800 lines  (test fails unless exempted)

Known exemptions are listed in ``_KNOWN_LARGE_FILES``.  Each exemption
carries a TODO comment explaining why it exists and what the target
state looks like.
"""

from __future__ import annotations

import warnings
from pathlib import Path

_BASE = (Path(__file__).resolve().parents[3] / "deepagents").resolve()

_WARNING_THRESHOLD = 500
_FAILURE_THRESHOLD = 800

# Files known to exceed the failure threshold.
# Each entry is the path relative to _BASE.
# TODO: graph.py -- refactor node-building helpers into graph_nodes.py
# TODO: middleware/async_subagents.py -- extract async orchestration primitives
# TODO: middleware/filesystem.py -- split read/write/sandbox helpers
# TODO: middleware/skills.py -- extract skill registry into its own module
# TODO: middleware/summarization.py -- extract strategy classes
# TODO: backends/protocol.py -- extract protocol ABCs into separate files
_KNOWN_LARGE_FILES: set[str] = {
    "graph.py",
    "middleware/async_subagents.py",
    "middleware/filesystem.py",
    "middleware/skills.py",
    "middleware/summarization.py",
    "backends/protocol.py",
}


def _is_exempt(rel_path: Path) -> bool:
    """Return True if the file is structurally exempt (init / test)."""
    name = rel_path.name
    if name == "__init__.py":
        return True
    if name.startswith("test_") or name.endswith("_test.py"):
        return True
    return False


class TestFileSizeEnforcement:
    """No production file should exceed the failure threshold (unless exempted)."""

    def test_no_oversized_files(self) -> None:
        violations: list[str] = []
        for pyf in sorted(_BASE.rglob("*.py")):
            if "__pycache__" in str(pyf):
                continue
            rel = pyf.relative_to(_BASE)
            if _is_exempt(rel):
                continue
            line_count = len(pyf.read_text().splitlines())

            # Informational warning -- does not fail the test
            if _WARNING_THRESHOLD < line_count <= _FAILURE_THRESHOLD:
                warnings.warn(
                    f"{rel} is {line_count} lines (warning threshold: "
                    f"{_WARNING_THRESHOLD}).  Consider decomposing.",
                    stacklevel=1,
                )

            # Hard failure unless in the known-exemptions set
            if line_count > _FAILURE_THRESHOLD:
                rel_str = str(rel)
                if rel_str in _KNOWN_LARGE_FILES:
                    continue  # exempted -- tracked via TODO above
                violations.append(
                    f"  {rel} : {line_count} lines (threshold: {_FAILURE_THRESHOLD})\n"
                    f"    Suggestion: decompose into smaller, focused modules. "
                    f"If this size is justified, add '{rel_str}' to "
                    f"_KNOWN_LARGE_FILES with a TODO explaining the plan."
                )

        assert not violations, (
            "Files exceeding the size threshold:\n" + "\n".join(violations)
        )
