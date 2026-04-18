---
name: cleanup-pass
description: Remove debug prints, dead code, unused imports, commented-out code from recent changes
triggers: After verify-and-iterate passes, before final commit
allowed-tools: [read_file, edit_file, shell]
---

# Cleanup Pass

Sweep recently changed files to remove development artifacts that should not be committed: debug prints, dead code, unused imports, and commented-out blocks.

## Purpose

During iterative development, agents accumulate debugging scaffolding -- console.log statements, print() calls, commented-out alternatives, unused imports added during exploration. Cleanup-pass removes these systematically so the final commit contains only intentional production code.

## Input / Output

**Input:**
- `files`: List of files to clean (default: files in `git diff --name-only`)
- `patterns`: Additional patterns to search for beyond defaults

**Output:**
- List of removals: file, line(s), what was removed, why
- Count of changes per category (debug prints, dead code, unused imports, comments)
- Re-run of verification command to confirm cleanup did not break anything

## Interaction with Other Skills

- **verify-and-iterate**: Must pass before cleanup-pass runs; cleanup-pass re-verifies after changes
- **review-diff**: May identify cleanup items that this skill then resolves
- **plan-and-execute**: Cleanup-pass is typically the second-to-last step in a plan

## Example Invocation

```
cleanup-pass:
  files: ["src/auth.py", "src/handlers.py", "tests/test_auth.py"]
  patterns: ["console.log", "debugger", "# HACK"]
```
