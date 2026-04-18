---
name: architecture-check
description: Validate changes against declared architecture rules in .pack-rules.md
triggers: .pack-rules.md exists in target repository
allowed-tools: [read_file, shell]
---

# Architecture Check

Validate pending changes against the architectural rules and constraints declared in the repository's `.pack-rules.md` file.

## Purpose

Teams encode architectural decisions -- dependency direction, module boundaries, naming conventions, forbidden patterns -- in `.pack-rules.md`. Without active enforcement, agents (and humans) drift from these rules over time. Architecture-check reads the rules file, interprets each rule, and checks the current diff or codebase against them, reporting violations before they are committed.

## Input / Output

**Input:**
- `rules_path`: Path to `.pack-rules.md` (default: repo root)
- `scope`: What to check (`diff` for pending changes, `full` for entire codebase)

**Output:**
- List of violations: rule name, file, line(s), description of violation
- List of rules checked with pass/fail status
- Overall verdict: `compliant` | `violations-found`

## Interaction with Other Skills

- **bootstrap-repo**: Detects `.pack-rules.md` and triggers architecture-check
- **review-diff**: Complements review-diff; review-diff catches code quality issues, architecture-check catches structural violations
- **plan-and-execute**: Architecture-check can be a validation step in the plan
- **cleanup-pass**: Some architecture violations (wrong import paths, forbidden patterns) may be fixable by cleanup-pass

## Example Invocation

```
architecture-check:
  rules_path: "/Users/dev/my-project/.pack-rules.md"
  scope: "diff"
```
