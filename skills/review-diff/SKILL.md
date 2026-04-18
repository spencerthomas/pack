---
name: review-diff
description: Agent self-review of changes before commit. Check for debug artifacts, missed requirements, style violations
triggers: Before any commit or PR action
allowed-tools: [shell, read_file]
---

# Review Diff

Self-review all staged or pending changes against a checklist of common agent mistakes before allowing a commit or PR.

## Purpose

Agents routinely leave behind debug prints, hardcoded paths, TODO placeholders, commented-out code, and incomplete implementations. Review-diff acts as a pre-commit gate that catches these issues before they reach version control. It is intentionally read-only -- it identifies problems but does not fix them, deferring fixes to `cleanup-pass` or manual action.

## Input / Output

**Input:**
- `diff_source`: How to obtain the diff (`git diff --staged`, `git diff HEAD`, or a patch file)
- `requirements`: Optional list of requirements to verify against

**Output:**
- List of findings, each with: file, line range, category, severity, description
- Categories: `debug-artifact`, `missed-requirement`, `style-violation`, `security-concern`, `incomplete-implementation`
- Overall verdict: `clean` | `needs-cleanup` | `needs-rework`

## Interaction with Other Skills

- **cleanup-pass**: Handles `needs-cleanup` findings (debug prints, dead code)
- **verify-and-iterate**: Should have already passed before review-diff runs
- **plan-and-execute**: Review-diff is called before commit steps in a plan
- **architecture-check**: Complements review-diff with structural/architectural validation

## Example Invocation

```
review-diff:
  diff_source: "git diff --staged"
  requirements:
    - "All API endpoints return proper error codes"
    - "No hardcoded credentials"
```
