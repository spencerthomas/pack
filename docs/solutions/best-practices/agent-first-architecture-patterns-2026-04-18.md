---
title: Agent-First Architecture Patterns for Coding Agent Harnesses
date: 2026-04-18
category: best-practices
module: sdk
problem_type: best_practice
component: tooling
severity: high
applies_when:
  - Restructuring a coding agent's codebase for agent legibility
  - Adding mechanical enforcement of architectural conventions
  - Scaling from single-agent to multi-agent parallel workflows
  - Moving from documentation-based guidance to tool-enforced constraints
tags: [agent-first, architecture, progressive-disclosure, structural-tests, spec-verification, middleware, harness-engineering]
---

# Agent-First Architecture Patterns for Coding Agent Harnesses

## Context

Pack's AGENTS.md was 359 lines (~20KB) — an encyclopedia that consumed ~5K tokens before the agent even read the task. Dependency directions between domain modules were clean by convention but not enforced. Features were built but never wired (recurring "lobotomized Pack" pattern across 3 ideation sessions). This work applied OpenAI's harness engineering principles to restructure Pack's codebase for agent-first legibility.

The core thesis from the OpenAI blog: agent engineering changes the unit of design from "what is clean for 7 humans" to "what is safe and legible for coordinating hundreds of parallel workers." Without strong boundaries, more agents create more mess faster. With strong boundaries, more agents create more output faster.

## Guidance

### 1. Progressive Disclosure: TOC, Not Encyclopedia

Replace monolithic instruction files with a short table-of-contents (~50 lines) pointing to structured docs. Each doc covers one concern: core beliefs, code quality, CLI patterns, CI/CD, testing. The agent reads the TOC first and drills into specific docs only when the task requires them.

Pack's AGENTS.md went from 359 lines to 47 lines. All content preserved — just distributed across `docs/core-beliefs.md`, `docs/code-quality.md`, `docs/cli-patterns.md`, `docs/ci-cd.md`, `docs/testing.md`, `docs/PATTERNS.md`.

### 2. Mechanical Enforcement via AST-Based Structural Tests

Use Python's `ast` module to parse import statements and enforce dependency directions. Zero runtime overhead — tests only run in CI. Each assertion failure includes a remediation message telling the agent exactly what's wrong and how to fix it.

Rules enforced in Pack:
- Domain modules (compaction, memory, cost, permissions, hooks, execution) must not import from each other
- Domain modules must not import from middleware/
- middleware/pack/ wrappers may only import their corresponding domain module
- providers/ and prompt/ must not import from middleware/

### 3. Spec Files as Ghost Libraries

Write markdown spec files that describe a module precisely enough for CI verification. A test parses the spec and introspects the code — if they diverge, CI fails with "update docs/specs/X.spec.md."

This creates documentation that is both machine-verified (CI checks spec matches code) and machine-usable (agent can understand a module from its spec without reading all the source).

### 4. Feature Wiring Verification

The recurring failure pattern: features are built but never activated. Structural tests verify that every domain module has a corresponding middleware wiring in `_add_pack_middleware()` and that the `PACK_ENABLED` env var gates Pack middleware.

### 5. Advisory Middleware for Architecture Enforcement

The `ArchitectureEnforcementMiddleware` reads `.pack-rules.md` from the target repo and checks file-modifying tool calls against declared rules. Key design choice: it's advisory (non-blocking) — it appends violation messages to the tool result rather than blocking the tool call. The agent gets guidance without losing work.

### 6. Typed Model Client Wrapper

The `PackModelClient` wraps raw `ainvoke()` calls with retry, cost tracking, structured logging, and duration measurement. This is the "narrow wrapper" pattern: hide complexity, encode invariants, create a stable surface for agents.

## Why This Matters

Architecture without mechanical enforcement is decorative. Agents will violate conventions that exist only in documentation — especially under pressure (retries, doom loops, context exhaustion). Mechanical enforcement via structural tests and advisory middleware catches violations at the right moment: CI for dependency directions, runtime for target repo conventions.

The progressive disclosure pattern has an immediate measurable impact: ~5K tokens saved per agent run from the AGENTS.md reduction alone. Over hundreds of runs, this compounds into significant context window savings.

## When to Apply

- When AGENTS.md or CLAUDE.md exceeds ~100 lines
- When dependency directions between modules are clean by convention but not enforced
- When features are built but sometimes not wired into the pipeline
- When agents work on codebases that have architectural conventions the agent should respect
- When scaling from single-agent to multi-agent parallel workflows

## Examples

**Structural test with remediation message:**

```python
def test_no_cross_domain_imports():
    for domain in DOMAIN_MODULES:
        for pyf in py_files(domain_dir):
            for mod in imported_modules(pyf.read_text()):
                for other in DOMAIN_MODULES:
                    if other != domain and f"deepagents.{other}" in mod:
                        violations.append(
                            f"{rel} imports '{mod}'\n"
                            f"  Remediation: {domain}/ must not depend on {other}/."
                        )
    assert not violations, "Cross-domain imports detected:\n" + "\n".join(violations)
```

**Advisory middleware pattern:**

```python
# Always execute the tool first — never block
result = await handler(request)
# Check rules only after successful execution
violations = check_violations(file_path, file_content, rules)
if violations:
    # Append guidance to tool result, don't replace it
    return ToolMessage(
        content=f"{original_content}\n\nARCHITECTURE VIOLATION:\n{violation_text}",
        tool_call_id=result.tool_call_id,
    )
```

## Related

- OpenAI "Harness Engineering" blog post (Feb 2026) by Ryan Lopopolo
- Latent Space interview transcript with spec.md ghost library details
- `docs/ideation/2026-04-18-agent-first-architecture-ideation.md` — full ideation with 14 ranked ideas
- `docs/plans/2026-04-18-001-feat-agent-first-architecture-engine-plan.md` — implementation plan
