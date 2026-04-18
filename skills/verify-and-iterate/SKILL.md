---
name: verify-and-iterate
description: Run verification command, analyze failures, retry with structured diagnosis
triggers: Task has acceptance criteria or test command
allowed-tools: [shell, read_file, write_file, edit_file]
---

# Verify and Iterate

Run a verification command (test suite, build, lint, etc.), analyze any failures, apply fixes, and re-verify in a structured loop until acceptance criteria pass or a maximum retry count is reached.

## Purpose

Prevent agents from declaring success without evidence. Every task with a testable outcome should pass through verify-and-iterate before completion. The skill enforces a discipline of run-check-fix cycles with diminishing patience: if the same failure class recurs, escalate to `diagnose-failure` rather than retrying blindly.

## Input / Output

**Input:**
- `command`: The shell command to run (e.g., `pytest tests/`, `npm run build`)
- `max_retries`: Maximum fix-and-retry cycles (default: 3)
- `acceptance`: Optional plain-language description of what "passing" looks like

**Output:**
- Final command exit code and stdout/stderr
- Structured log of each iteration: what failed, what was changed, result
- Status: `passed` | `failed` | `escalated`

## Interaction with Other Skills

- **diagnose-failure**: Escalate here when the same failure repeats 2+ times
- **cleanup-pass**: Run after verify-and-iterate passes, before final commit
- **review-diff**: Run after all iterations complete to self-review accumulated changes
- **plan-and-execute**: Can invoke verify-and-iterate as a checkpoint step

## Example Invocation

```
verify-and-iterate:
  command: "pytest tests/ -x -q"
  max_retries: 3
  acceptance: "All tests pass with zero warnings"
```
