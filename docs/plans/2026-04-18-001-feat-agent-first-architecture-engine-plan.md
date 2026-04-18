---
title: "feat: Agent-First Architecture Engine"
type: feat
status: active
date: 2026-04-18
origin: docs/ideation/2026-04-18-agent-first-architecture-ideation.md
---

# feat: Agent-First Architecture Engine

## Overview

Restructure Pack's codebase to follow agent-first architecture principles from OpenAI's harness engineering approach. The core thesis: agent engineering changes the unit of design from "what is clean for a human team of 7?" to "what is safe and legible for coordinating hundreds of parallel workers." This means progressive disclosure documentation, mechanical enforcement of dependency directions, spec-verified ghost libraries, quality score tables, and skill consolidation.

Two faces: restructure Pack itself as a model of agent-first architecture (dog-food), and build tooling that enforces these patterns on target codebases.

## Problem Frame

Pack's codebase has clean unidirectional dependencies but no mechanical enforcement — the clean separation exists by convention only. AGENTS.md is 359 lines (~20KB), violating progressive disclosure. Features get built but never wired (SubAgentMiddleware has 0 agents registered, PACK_ENABLED never set in Harbor, ParallelToolExecutor built but never invoked). graph.py is 817 lines with complexity suppressions. Two permission systems coexist with ambiguous precedence. System prompt management has two divergent codepaths (CLI builder vs Harbor constant). 1 skill exists where the target is 6-8.

The recurring institutional pattern: **architecture without mechanical enforcement is decorative.** Features exist but aren't activated. Conventions exist but aren't enforced. This plan encodes enforcement directly into CI and middleware.

## Requirements Trace

- R1. AGENTS.md becomes ~50-line TOC with progressive disclosure into structured docs/
- R2. Dependency directions are declared in DOMAIN_RULES.md and mechanically enforced via structural tests
- R3. Spec files ("ghost libraries") exist for key modules with CI verification that specs match code
- R4. Quality score table grades each module across dimensions
- R5. ArchitectureEnforcementMiddleware reads .pack-rules.md and enforces rules on target codebases
- R6. PackModelClient wrapper consolidates raw LLM calls with retry, cost tracking, tracing
- R7. Skills consolidate to 6-8 well-defined skills with specs and observability
- R8. Structural tests assert features are wired, not just built (addresses recurring "lobotomized Pack" pattern)

## Scope Boundaries

- No graph.py decomposition in this plan (separate refactor task)
- No permission system unification (separate plan, declare canonical choice in ARCHITECTURE.md)
- No middleware conditional activation system (separate plan, but new middleware added here must declare activation conditions)
- No changes to LangGraph core or upstream middleware
- Skills are spec'd and scaffolded, not fully implemented (full implementation is separate work per skill)

### Deferred to Separate Tasks

- graph.py decomposition: separate refactor PR
- Permission system canonical decision + migration: separate plan
- Middleware conditional activation framework: separate plan  
- Full skill implementation (each skill gets its own plan): separate plans
- Harbor wrapper reconciliation with SystemPromptBuilder: separate plan

## Context & Research

### Relevant Code and Patterns

- `AGENTS.md` — 359-line encyclopedia to restructure (split along existing section headers)
- `libs/deepagents/deepagents/graph.py` — 817-line god module, middleware composition order
- `libs/deepagents/deepagents/middleware/pack/` — 8 Pack middleware wrappers
- `libs/deepagents/deepagents/prompt/builder.py` — SystemPromptBuilder with section composition
- `libs/deepagents/deepagents/prompt/sections.py` — Section factories (identity, safety, tool_rules, style, environment, git)
- `libs/deepagents/deepagents/agents/profiles.py` — 4 agent type profiles (Explore, Plan, Review, General)
- `scripts/check_imports.py` — Existing import checker (loadability only, no direction enforcement)
- `skills/langsmith-trace-analyzer/SKILL.md` — Existing skill format reference (YAML frontmatter)
- `tests/unit_tests/test_args.py::TestHelpScreenDrift` — Existing drift detection test pattern to extend
- `libs/deepagents/deepagents/middleware/pack/state.py` — PackState singleton

### Institutional Learnings

- **Features built but never wired** is the #1 recurring failure across 3 ideation sessions. Structural tests must verify activation, not just existence.
- **AGENTS.md contains battle-tested patterns** (Textual Content vs Rich Text, App.notify(markup=False), ruff suppression policy) that must be preserved during restructuring, not deleted.
- **Drift detection tests already exist** (TestHelpScreenDrift, test_command_registry). Extend this pattern for architecture enforcement rather than inventing a new approach.
- **System prompt has two codepaths**: CLI uses SystemPromptBuilder, Harbor uses hardcoded constant. Declare this split explicitly rather than pretending it doesn't exist.

## Key Technical Decisions

- **AGENTS.md splits along existing section headers**: Each major section becomes its own doc file. Content is preserved verbatim, not rewritten. The new AGENTS.md is a TOC with one-line descriptions.
- **Structural tests use AST parsing, not regex**: Python's `ast` module parses import statements reliably. Regex-based import detection is fragile.
- **Spec files are markdown with structured sections**: Not JSON/YAML — markdown is agent-readable and human-reviewable. Spec verification tests parse known sections and compare against introspected code.
- **Quality scores are a single markdown table**: Not a database. Agents and humans read/write the same file. CI can validate format but not scores (scores require judgment).
- **.pack-rules.md uses markdown with structured headings**: Same format philosophy as spec files. Agent-readable, human-editable, versionable.
- **New middleware declares activation conditions**: The ArchitectureEnforcementMiddleware only fires when .pack-rules.md exists in the target repo and tool call is file-modifying.
- **Remediation messages in test assertions**: Every structural test failure tells the agent exactly what to fix, following the OpenAI pattern of injecting guidance into agent context.

## Open Questions

### Resolved During Planning

- **Should AGENTS.md content be rewritten or preserved?** Preserved — split along section headers, content moves verbatim. Rewriting risks losing battle-tested patterns.
- **Where do spec verification tests live?** `tests/unit_tests/architecture/` — co-located with other structural tests.
- **Should lint middleware run on every tool call?** No — conditional activation: only when .pack-rules.md exists and tool call modifies files.
- **What's the skill format?** Extend existing SKILL.md format (YAML frontmatter + markdown body) with additional fields for triggers, observability hooks, and spec pointer.

### Deferred to Implementation

- Exact spec file section format will evolve as the first spec (graph-assembly) is written
- Quality score grading criteria may need refinement after initial assessment
- Skill trigger condition syntax may need iteration based on testing

## Output Structure

```
pack/
├── AGENTS.md                              # Restructured to ~50-line TOC
├── ARCHITECTURE.md                        # NEW: Module map + dependency directions
├── docs/
│   ├── core-beliefs.md                    # NEW: Product vision, team, objectives
│   ├── DOMAIN_RULES.md                    # NEW: Dependency directions + boundaries
│   ├── PATTERNS.md                        # NEW: Canonical code patterns
│   ├── code-quality.md                    # NEW: From AGENTS.md quality section
│   ├── cli-patterns.md                    # NEW: From AGENTS.md CLI section
│   ├── ci-cd.md                           # NEW: From AGENTS.md CI section
│   ├── testing.md                         # NEW: From AGENTS.md testing section
│   ├── quality/
│   │   └── quality-scores.md              # NEW: Module × dimension grades
│   ├── specs/
│   │   ├── graph-assembly.spec.md         # NEW: Ghost library for graph.py
│   │   ├── middleware-contract.spec.md    # NEW: Ghost library for middleware
│   │   ├── prompt-assembly.spec.md       # NEW: Ghost library for prompt builder
│   │   └── permission-pipeline.spec.md   # NEW: Ghost library for permissions
│   └── plans/
│       └── 2026-04-18-001-feat-agent-first-architecture-engine-plan.md
├── libs/deepagents/
│   └── deepagents/
│       ├── middleware/pack/
│       │   └── architecture_middleware.py  # NEW: ArchitectureEnforcementMiddleware
│       └── providers/
│           └── model_client.py            # NEW: PackModelClient wrapper
├── tests/unit_tests/
│   └── architecture/
│       ├── __init__.py
│       ├── test_dependency_directions.py  # NEW: Import direction enforcement
│       ├── test_file_size.py              # NEW: Module size limits
│       ├── test_feature_wiring.py         # NEW: Feature activation verification
│       └── test_spec_verification.py      # NEW: Spec ↔ code consistency
└── skills/
    ├── verify-and-iterate/SKILL.md        # NEW: Skill scaffolds
    ├── bootstrap-repo/SKILL.md
    ├── plan-and-execute/SKILL.md
    ├── review-diff/SKILL.md
    ├── cleanup-pass/SKILL.md
    ├── diagnose-failure/SKILL.md
    └── architecture-check/SKILL.md
```

## Implementation Units

### Phase 1: Knowledge Architecture

- [ ] **Unit 1: Restructure AGENTS.md into TOC + distributed docs**

**Goal:** Transform 359-line AGENTS.md into ~50-line TOC pointing to structured docs/ files. Preserve all existing content.

**Requirements:** R1

**Dependencies:** None

**Files:**
- Modify: `AGENTS.md`
- Create: `docs/core-beliefs.md`
- Create: `docs/code-quality.md`
- Create: `docs/cli-patterns.md`
- Create: `docs/ci-cd.md`
- Create: `docs/testing.md`
- Create: `docs/PATTERNS.md`

**Approach:**
- Read current AGENTS.md section headers. Split at each `##` boundary.
- Move Pack design principles (lines 7-13) → `docs/core-beliefs.md`
- Move code quality, testing, security sections → `docs/code-quality.md`, `docs/testing.md`
- Move CLI/Textual patterns, slash commands, model providers → `docs/cli-patterns.md`
- Move CI/CD, release process, PR labeling, partner additions → `docs/ci-cd.md`
- Move commit standards, development tools → `docs/PATTERNS.md`
- New AGENTS.md: project summary (3 lines) + "Where to Look" section with one-line-per-doc pointers + "Key Rules" section (5-6 critical rules that apply everywhere)
- Preserve exact content in destination files — do not rewrite. Only add a title and brief intro sentence to each new file.

**Patterns to follow:**
- OpenAI's AGENTS.md structure (~100 lines, TOC-style)
- Existing AGENTS.md section headers as natural split points

**Test scenarios:**
- Happy path: All content from original AGENTS.md appears in exactly one destination file (grep-verify)
- Edge case: Cross-references between sections update to point to new file locations
- Integration: A text search for any specific pattern from original AGENTS.md (e.g., "App.notify(markup=False)") finds it in the new location

**Verification:**
- `wc -l AGENTS.md` shows ~50 lines
- All original content findable via grep across docs/ files
- No orphaned references

---

- [ ] **Unit 2: Create ARCHITECTURE.md with module map and dependency directions**

**Goal:** Document Pack's module architecture, dependency directions, and middleware composition order as a single reference document.

**Requirements:** R2

**Dependencies:** Unit 1

**Files:**
- Create: `ARCHITECTURE.md`
- Create: `docs/DOMAIN_RULES.md`

**Approach:**
- ARCHITECTURE.md: High-level module map showing all 14 top-level modules under `libs/deepagents/deepagents/`, their roles, and relationships. Include middleware composition order from graph.py. Include package dependency tree (deepagents → cli, acp, evals, repl).
- DOMAIN_RULES.md: Explicit dependency DAG. Declare allowed and forbidden import directions. Document the two permission systems and which is canonical for each context. Document system prompt codepath split (CLI builder vs Harbor constant).
- Use mermaid diagram for the dependency DAG in ARCHITECTURE.md.

**Patterns to follow:**
- OpenAI's ARCHITECTURE.md with domain/layer structure
- Existing graph.py middleware ordering as source of truth

**Test scenarios:**
- Happy path: ARCHITECTURE.md accurately lists all top-level modules (compare against `ls` output)
- Happy path: DOMAIN_RULES.md declares at least one forbidden import direction per domain module
- Edge case: Middleware composition order in ARCHITECTURE.md matches actual order in graph.py

**Verification:**
- A developer (or agent) reading only ARCHITECTURE.md + DOMAIN_RULES.md can correctly predict which modules may import from which
- The permission system ambiguity is explicitly documented with canonical recommendation

---

- [ ] **Unit 3: Create quality score table**

**Goal:** Grade each Pack module across quality dimensions. Provide a machine-readable reference for agents doing improvement work.

**Requirements:** R4

**Dependencies:** Unit 2 (needs module map for completeness)

**Files:**
- Create: `docs/quality/quality-scores.md`

**Approach:**
- Table structure: Module × Dimensions (Types, Tests, Docs, Spec, Boundary Validation)
- Grade each module A-D based on current state (assessed from research findings):
  - permissions: A (well-tested, documented)
  - compaction: B (tested, some docs)
  - cost: C (tested, minimal docs)
  - memory: C (tested, minimal docs)
  - prompt: B (tested, modular)
  - providers: B (tested, typed)
  - middleware/pack: C (minimal tests — 14 total)
  - CLI: D (minimal tests, large app.py)
  - coordination: D (no tests)
  - execution: D (no tests)
- Include grading criteria definitions
- Include "Known Gaps" section listing modules below B grade

**Patterns to follow:**
- OpenAI's QUALITY_SCORE.md markdown table format

**Test scenarios:**
- Happy path: Every module listed in ARCHITECTURE.md has a row in quality-scores.md
- Edge case: New modules added without a quality score row trigger a reminder (deferred to spec verification tests)

**Verification:**
- Table renders correctly in markdown
- Every domain module from ARCHITECTURE.md is represented

---

### Phase 2: Mechanical Enforcement

- [ ] **Unit 4: Dependency direction structural tests**

**Goal:** Write tests that parse import statements and fail when dependency directions are violated. Include remediation messages in assertion failures.

**Requirements:** R2, R8

**Dependencies:** Unit 2 (DOMAIN_RULES.md defines the rules)

**Files:**
- Create: `tests/unit_tests/architecture/__init__.py`
- Create: `tests/unit_tests/architecture/test_dependency_directions.py`

**Approach:**
- Use Python `ast` module to parse all .py files in each domain module
- Extract all import statements (Import and ImportFrom nodes)
- Check each import against the forbidden-directions map from DOMAIN_RULES.md
- Forbidden directions (from research):
  - Domain modules (compaction, memory, cost, permissions, hooks, execution) must NOT import from each other
  - Domain modules must NOT import from middleware/
  - middleware/pack/ wrappers may import their corresponding domain module but not others
  - providers/ must not import from middleware/
  - prompt/ must not import from middleware/
- Every assertion failure includes a remediation message: what violated, why it's wrong, and how to fix it
- Pattern: extend the TestHelpScreenDrift approach

**Patterns to follow:**
- `tests/unit_tests/test_args.py::TestHelpScreenDrift` — existing structural test with clear assertion messages
- `scripts/check_imports.py` — existing import checking (extend, don't replace)

**Test scenarios:**
- Happy path: All current imports pass (verify existing code is clean before enforcing)
- Error path: A deliberately violating import (e.g., compaction importing from memory) triggers assertion with remediation message
- Edge case: Relative imports within a module are allowed
- Edge case: Type-checking-only imports (`if TYPE_CHECKING:`) are still checked (they reveal dependency intent)

**Verification:**
- `pytest tests/unit_tests/architecture/test_dependency_directions.py` passes on current codebase
- Adding a forbidden import to any domain module causes a test failure with a helpful message

---

- [ ] **Unit 5: File size enforcement tests**

**Goal:** Flag modules that exceed size limits, preventing future graph.py-sized accumulations.

**Requirements:** R8

**Dependencies:** Unit 4 (co-located in architecture/ test directory)

**Files:**
- Create: `tests/unit_tests/architecture/test_file_size.py`

**Approach:**
- Scan all .py files under libs/deepagents/deepagents/
- Flag any file exceeding 500 lines (warning) or 800 lines (failure)
- Exempt __init__.py files and test files
- graph.py currently at 817 lines — mark as known exception with TODO to decompose
- Assertion message includes: file path, line count, limit, and suggestion to split

**Patterns to follow:**
- OpenAI's file size limits enforced via custom linters

**Test scenarios:**
- Happy path: All files except known exceptions are under 500 lines
- Edge case: graph.py (817 lines) is exempted with documented reason
- Error path: A new file at 501+ lines triggers warning with decomposition suggestion

**Verification:**
- Test passes on current codebase (with known exemptions)
- Known exemptions are documented in the test file with TODO comments

---

- [ ] **Unit 6: Feature wiring verification tests**

**Goal:** Assert that built features are actually activated. Addresses the recurring "lobotomized Pack" pattern.

**Requirements:** R8

**Dependencies:** Unit 4

**Files:**
- Create: `tests/unit_tests/architecture/test_feature_wiring.py`

**Approach:**
- For each Pack feature module, verify it has a corresponding middleware wiring:
  - compaction/ → CompactionMiddleware is in _add_pack_middleware
  - cost/ → CostMiddleware is in _add_pack_middleware
  - permissions/ → PermissionMiddleware is in _add_pack_middleware
  - memory/ → PackMemoryMiddleware is in _add_pack_middleware
  - hooks/ → HooksMiddleware is in _add_pack_middleware
- Verify PACK_ENABLED is referenced in test configuration
- Verify agent profiles (Explore, Plan, Review, General) are registered and have tool scoping
- Assertion messages explain what's unwired and where to wire it

**Patterns to follow:**
- `test_command_registry.py` — existing metadata consistency tests
- TestHelpScreenDrift pattern

**Test scenarios:**
- Happy path: Every domain module has a corresponding middleware class in middleware/pack/
- Happy path: Every middleware class in middleware/pack/ is referenced in _add_pack_middleware()
- Error path: A new domain module without middleware wiring triggers failure with "create middleware/pack/<name>_middleware.py" guidance
- Integration: Verify the PACK_ENABLED env var gates all Pack middleware (parse _add_pack_middleware for the guard)

**Verification:**
- Test passes on current codebase
- Adding a new domain module without wiring it causes a clear test failure

---

### Phase 3: Spec Files (Ghost Libraries)

- [ ] **Unit 7: Graph assembly spec file**

**Goal:** Write a spec file for graph.py that describes the module precisely enough for CI verification. Proof-of-concept for the spec-driven approach.

**Requirements:** R3

**Dependencies:** Unit 2 (ARCHITECTURE.md provides context)

**Files:**
- Create: `docs/specs/graph-assembly.spec.md`
- Create: `tests/unit_tests/architecture/test_spec_verification.py`

**Approach:**
- Spec file sections: Public API (function signatures), Middleware Composition Order (numbered list), Subagent Processing (steps), System Prompt Assembly (decision tree), Feature Gating (env vars), Known Complexity (noqa suppressions with rationale)
- Spec verification test: Parse spec file sections, introspect graph.py module, compare:
  - All public functions in spec match actual public functions
  - Middleware ordering in spec matches actual ordering in code
  - Environment variable gates in spec match actual env var checks
- Spec sections use markdown headers that the test can reliably parse

**Patterns to follow:**
- OpenAI's spec.md "ghost library" — multi-pass verified specs
- Existing drift detection tests for the verification approach

**Test scenarios:**
- Happy path: Spec accurately describes current graph.py — test passes
- Error path: Adding a new public function to graph.py without updating spec triggers failure with "update docs/specs/graph-assembly.spec.md" message
- Error path: Changing middleware order without updating spec triggers failure
- Edge case: Private/internal functions are not required in spec

**Verification:**
- `pytest tests/unit_tests/architecture/test_spec_verification.py` passes
- Spec file is readable standalone — an agent can understand graph.py's structure from the spec without reading the code

---

- [ ] **Unit 8: Middleware contract spec file**

**Goal:** Document the middleware contract — what middleware must/must not do, the AgentMiddleware protocol, composition rules.

**Requirements:** R3

**Dependencies:** Unit 7 (spec format proven)

**Files:**
- Create: `docs/specs/middleware-contract.spec.md`
- Modify: `tests/unit_tests/architecture/test_spec_verification.py` (add middleware contract tests)

**Approach:**
- Spec sections: AgentMiddleware Protocol (methods to override), Composition Rules (ordering constraints), Pack Middleware Pattern (domain module → middleware wrapper → graph.py), Naming Conventions, State Access (PackState singleton pattern)
- Verification: Check all middleware classes implement the declared protocol methods, check naming conventions match

**Test scenarios:**
- Happy path: All middleware classes in middleware/pack/ subclass AgentMiddleware
- Error path: A middleware class not following naming convention triggers failure
- Integration: Middleware listed in spec matches actual middleware files in middleware/pack/

**Verification:**
- Spec accurately lists all middleware classes and their roles
- Test catches new middleware that doesn't follow the contract

---

- [ ] **Unit 9: Prompt assembly and permission pipeline spec files**

**Goal:** Complete the spec file set for the remaining key modules.

**Requirements:** R3

**Dependencies:** Unit 7

**Files:**
- Create: `docs/specs/prompt-assembly.spec.md`
- Create: `docs/specs/permission-pipeline.spec.md`
- Modify: `tests/unit_tests/architecture/test_spec_verification.py` (add tests for both)

**Approach:**
- Prompt assembly spec: SystemPromptBuilder API, PromptSection dataclass, section factories in sections.py, CacheStrategy protocol and implementations, mode-dependent behavior (CLI vs Harbor)
- Permission pipeline spec: 6-layer pipeline stages, circuit breaker behavior, rule store format, interaction with SDK-level _PermissionMiddleware. Explicitly document the dual-system situation and canonical recommendation.
- Both follow the format established in Unit 7

**Test scenarios:**
- Happy path: Spec sections match introspected module structure
- Error path: New section factory added to sections.py without spec update triggers failure
- Edge case: Permission spec documents both systems without asserting unification (that's a separate plan)

**Verification:**
- Specs are standalone-readable
- Tests pass on current code

---

### Phase 4: Lint Middleware

- [ ] **Unit 10: ArchitectureEnforcementMiddleware implementation**

**Goal:** Build middleware that enforces repo-specific architecture rules on target codebases after file-modifying tool calls.

**Requirements:** R5

**Dependencies:** None (independent of Phases 1-3)

**Files:**
- Create: `libs/deepagents/deepagents/middleware/pack/architecture_middleware.py`
- Modify: `libs/deepagents/deepagents/middleware/pack/__init__.py` (export)
- Create: `tests/unit_tests/test_architecture_middleware.py`

**Approach:**
- Middleware subclasses AgentMiddleware, overrides `awrap_tool_call()`
- On initialization, checks for `.pack-rules.md` in working directory. If absent, middleware is inert (no-op on all calls).
- After file-modifying tool calls (write_file, edit_file, apply_patch, create_file), parses the modified file and checks against loaded rules
- Rule categories: dependency direction (import checks), file size limits, naming conventions, boundary validation requirements
- Violations produce structured messages injected into agent context with remediation
- Rules are parsed from markdown with structured headings (## Dependency Directions, ## Boundaries, ## Style)
- Conditional activation: only fires when .pack-rules.md exists AND tool call modifies files

**Execution note:** Start with a test for the rule parser, then the middleware integration.

**Patterns to follow:**
- `middleware/pack/permission_middleware.py` — existing Pack middleware pattern
- `AgentMiddleware.awrap_tool_call()` protocol
- OpenAI's lint error messages with remediation

**Test scenarios:**
- Happy path: File modification in repo with .pack-rules.md triggers rule checking
- Happy path: No .pack-rules.md means middleware is completely inert
- Error path: Dependency direction violation produces remediation message with rule source and fix suggestion
- Error path: File size violation produces remediation with current size and limit
- Edge case: Non-file-modifying tool calls bypass all checks
- Edge case: Malformed .pack-rules.md logs warning and becomes inert (no crash)
- Integration: Remediation message appears in agent context as system message

**Verification:**
- Middleware passes all tests
- Adding a .pack-rules.md to a test repo and violating a rule produces a clear remediation message

---

### Phase 5: Wrapper Consolidation

- [ ] **Unit 11: PackModelClient wrapper**

**Goal:** Create a typed wrapper around raw LLM calls that adds retry, cost tracking, and tracing. Establish the "paved road" for model access.

**Requirements:** R6

**Dependencies:** None (independent)

**Files:**
- Create: `libs/deepagents/deepagents/providers/model_client.py`
- Create: `tests/unit_tests/providers/test_model_client.py`

**Approach:**
- PackModelClient wraps langchain model.ainvoke() with:
  - Retry with exponential backoff (2s, 4s, 8s) on transient errors (connection, timeout, 5xx)
  - Cost tracking integration (call cost tracker start/end)
  - Structured logging with purpose tag
  - Trace ID attachment for observability
- Typed result: CompletionResult(content, usage, cost, duration)
- Constructor takes model instance + optional cost tracker + optional tracer
- Does NOT replace langchain's model interface — wraps it for Pack-specific concerns
- Migration of existing raw calls (compaction, memory) is a follow-up, not this unit

**Patterns to follow:**
- `providers/base.py` — existing provider config types
- `cost/tracker.py` — existing cost tracking interface
- The "narrow wrapper" pattern from the ideation: hide complexity, encode invariants, create stable surface

**Test scenarios:**
- Happy path: Successful completion returns CompletionResult with content and usage
- Error path: Transient error triggers retry, succeeds on retry, returns result
- Error path: All retries exhausted raises with clear error message
- Error path: Non-transient error (400, 401) raises immediately without retry
- Edge case: No cost tracker provided — cost field is None, no error
- Edge case: No tracer provided — no tracing, no error
- Integration: Cost tracker start/end called with correct purpose tag

**Verification:**
- All test scenarios pass
- Client can be used as drop-in wrapper around any langchain chat model

---

### Phase 6: Skill Consolidation

- [ ] **Unit 12: Define skill specs and scaffold skill directories**

**Goal:** Define 7 skills with spec files, trigger conditions, and directory scaffolds. Skills are scaffolded but not fully implemented.

**Requirements:** R7

**Dependencies:** Unit 1 (AGENTS.md TOC references skills)

**Files:**
- Create: `skills/verify-and-iterate/SKILL.md`
- Create: `skills/bootstrap-repo/SKILL.md`
- Create: `skills/plan-and-execute/SKILL.md`
- Create: `skills/review-diff/SKILL.md`
- Create: `skills/cleanup-pass/SKILL.md`
- Create: `skills/diagnose-failure/SKILL.md`
- Create: `skills/architecture-check/SKILL.md`

**Approach:**
- Each SKILL.md follows the existing format (YAML frontmatter + markdown body) extended with:
  - `triggers:` — conditions that activate this skill
  - `observability:` — what gets logged when this skill runs
  - `spec:` — pointer to corresponding spec file (if any)
- Skill definitions:
  1. **verify-and-iterate**: Run verification command, analyze failures, retry with diagnosis. Triggers: task has acceptance criteria or test command.
  2. **bootstrap-repo**: Read target repo structure, extract conventions, synthesize operating context. Triggers: first interaction with a new repo.
  3. **plan-and-execute**: Create execution plan artifact, track progress, checkpoint. Triggers: complex multi-step task.
  4. **review-diff**: Agent self-review of own changes before commit. Triggers: before any commit/PR action.
  5. **cleanup-pass**: Remove debug prints, dead code, unused imports from diff. Triggers: after verify-and-iterate passes.
  6. **diagnose-failure**: Structured root-cause analysis with hypothesis tracking. Triggers: verify fails 2+ times on same issue.
  7. **architecture-check**: Validate changes against declared architecture rules. Triggers: .pack-rules.md exists in repo.
- Each skill body describes: purpose, input shape, output shape, interaction with other skills, and example invocation

**Patterns to follow:**
- `skills/langsmith-trace-analyzer/SKILL.md` — existing format
- OpenAI's 6-skill constraint with tracing/observability built in

**Test scenarios:**
- Happy path: Each SKILL.md has valid YAML frontmatter with required fields (name, description, allowed-tools, triggers)
- Edge case: Skill trigger conditions don't overlap ambiguously (each trigger maps to exactly one skill)

**Verification:**
- All 7 SKILL.md files parse correctly
- No two skills have identical trigger conditions
- AGENTS.md TOC references the skills directory

---

## System-Wide Impact

- **Documentation surface:** AGENTS.md restructuring changes the primary entry point for all developers and agents. Broken references must be caught.
- **CI pipeline:** New structural tests must be added to CI. They should run fast (AST parsing, not code execution) and not require PACK_ENABLED.
- **Middleware stack:** ArchitectureEnforcementMiddleware adds to the stack but with conditional activation — no impact when .pack-rules.md is absent.
- **Provider interface:** PackModelClient is additive — existing code continues to work. Migration is a follow-up.
- **Skill loading:** New skills must be discoverable by SkillsMiddleware via existing path-based loading.
- **Unchanged invariants:** LangGraph core, upstream middleware, create_deep_agent() signature, CLI interface, Harbor wrapper — all unchanged.

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| AGENTS.md restructuring breaks references in CI, scripts, or docs | Grep for "AGENTS.md" references before and after; update all cross-references |
| Structural tests are too strict and break on valid patterns | Start with confirmed-clean dependency directions; add exemptions mechanism |
| Spec verification tests are too brittle (break on minor code changes) | Verify at the right abstraction level (function signatures, not line numbers) |
| ArchitectureEnforcementMiddleware adds latency to every file write | Conditional activation ensures it only fires when rules file exists |
| Quality scores become stale | Deferred: add a doc-gardening agent task to flag score staleness |

## Sources & References

- **Origin document:** [docs/ideation/2026-04-18-agent-first-architecture-ideation.md](docs/ideation/2026-04-18-agent-first-architecture-ideation.md)
- **OpenAI blog:** "Harness engineering: leveraging Codex in an agent-first world" (Feb 2026)
- **Latent Space transcript:** Ryan Lopopolo interview — spec.md ghost library, core beliefs, 6 skills, progressive disclosure details
- Related code: `AGENTS.md`, `libs/deepagents/deepagents/graph.py`, `scripts/check_imports.py`
- Related ideation: `docs/ideation/2026-04-07-tb2-performance-gap-ideation.md` (lobotomized Pack pattern)
- Related plans: `docs/plans/2026-04-06-001-feat-iterative-task-performance-plan.md` (verify-retry, doom loop)
