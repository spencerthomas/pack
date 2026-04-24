"""Tests for the progressive-disclosure task classifier."""

from __future__ import annotations

from deepagents.prompt.task_classifier import TaskHints, classify

# ---------------------------------------------------------------------------
# Domain detection
# ---------------------------------------------------------------------------


def test_python_task_tagged_python() -> None:
    assert classify("Fix the failing pytest in test_foo.py").domain == "python"


def test_c_task_tagged_c() -> None:
    assert classify("Write a Makefile and compile main.c with gcc").domain == "c"


def test_git_task_tagged_git() -> None:
    assert classify("Rebase feature branch onto main and resolve conflicts").domain == "git"


def test_shell_task_tagged_shell() -> None:
    assert classify("Write a bash script to parse /etc/hosts").domain == "shell"


def test_web_task_tagged_web() -> None:
    assert classify("Sanitize the JavaScript input to prevent XSS").domain == "web"


def test_data_task_tagged_data() -> None:
    assert classify("Load the parquet file and compute row counts by column").domain == "data"


def test_systems_task_tagged_systems() -> None:
    assert classify("Inspect the ELF binary and patch a syscall").domain == "systems"


def test_crypto_task_tagged_crypto() -> None:
    assert classify("Generate an openssl self-signed x509 cert").domain == "crypto"


def test_domain_falls_back_to_none() -> None:
    assert classify("Complete the task as described") .domain is None


# ---------------------------------------------------------------------------
# Phase detection
# ---------------------------------------------------------------------------


def test_fix_phase_detected() -> None:
    assert classify("The test is failing, fix the bug in parse_url").phase == "fix"


def test_build_phase_detected() -> None:
    assert classify("Write a function that returns the median").phase == "build"


def test_examine_phase_detected() -> None:
    assert classify("Analyze the log files and summarize error frequencies").phase == "examine"


def test_test_phase_detected() -> None:
    assert classify("Verify that the migration preserves all row counts").phase == "test"


# ---------------------------------------------------------------------------
# Complexity heuristic
# ---------------------------------------------------------------------------


def test_iterative_marker_flags_iterative() -> None:
    hints = classify("Tune the hyperparameters to maximize accuracy on the dev set")
    assert hints.complexity == "iterative"


def test_short_simple_task_flags_simple() -> None:
    hints = classify("Print the current date in ISO format")
    assert hints.complexity == "simple"


def test_long_task_without_markers_flags_exploratory() -> None:
    # >400 chars with no iterative/simple markers lands as exploratory
    text = "Investigate the cluster. " * 30
    hints = classify(text)
    assert hints.complexity == "exploratory"


# ---------------------------------------------------------------------------
# Guidance composition
# ---------------------------------------------------------------------------


def test_guidance_includes_domain_and_phase_tips() -> None:
    hints = classify("Fix the failing pytest suite")
    assert hints.phase == "fix"
    assert hints.domain == "python"
    assert len(hints.guidance) == 2  # domain tip + phase tip
    # Order matters: domain first, then phase
    assert "pytest" in hints.guidance[0]
    assert "Reproduce" in hints.guidance[1]


def test_guidance_empty_when_no_matches() -> None:
    hints = classify("Unknown unlabeled thing")
    assert hints.guidance == ()


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_empty_instruction_returns_empty_hints() -> None:
    hints = classify("")
    assert hints == TaskHints()


def test_whitespace_only_returns_empty_hints() -> None:
    assert classify("   \n\t   ") == TaskHints()


def test_none_handled_defensively() -> None:
    # The public API types instruction as str, but defensive guard should
    # not blow up on None — important since the caller pulls from a dict.
    assert classify(None) == TaskHints()  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# as_dict()
# ---------------------------------------------------------------------------


def test_as_dict_drops_none_fields() -> None:
    hints = TaskHints(phase="build", domain=None, complexity="simple")
    assert hints.as_dict() == {"phase": "build", "complexity": "simple"}


def test_as_dict_renders_multi_line_guidance() -> None:
    hints = TaskHints(guidance=("a", "b"))
    rendered = hints.as_dict()["guidance"]
    assert rendered.startswith("\n  - a")
    assert "\n  - b" in rendered


def test_as_dict_on_empty_hints_is_empty() -> None:
    assert TaskHints().as_dict() == {}


# ---------------------------------------------------------------------------
# Integration-ish: realistic TB2 instructions
# ---------------------------------------------------------------------------


def test_tb2_fix_c_task() -> None:
    # Representative of tasks like sqlite-with-gcov
    instruction = "Fix the build so SQLite compiles with gcov coverage instrumentation"
    hints = classify(instruction)
    assert hints.phase == "fix"
    assert hints.domain is not None  # matches something — c or systems


def test_tb2_git_task() -> None:
    instruction = "Recover the leaked secret from git history and remove all references"
    hints = classify(instruction)
    assert hints.domain == "git"
    # Phase may be None (recover/remove aren't in patterns); we accept that
    # as a graceful fallback rather than over-classifying.
