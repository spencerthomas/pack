---
date: 2026-04-18
topic: agent-first-architecture-engine
focus: Harness engineering concepts from OpenAI blog post + interview transcript → Pack integration
---

# Ideation: Agent-First Architecture Engine

## Codebase Context

Pack is a Python/LangGraph coding agent harness with middleware architecture. Current benchmark: 100% local (19/19), 32% formal Harbor (12/37), vs 81.8% ForgeCode leader. The codebase is tech-split (compaction/, permissions/, cost/) not domain-split. Clean unidirectional dependencies but no mechanical enforcement. AGENTS.md is 20KB (encyclopedia, not TOC). 1 skill exists where the blog team has 6. System prompt is modular via SystemPromptBuilder but still large for sandbox mode.

### Source Material
- OpenAI "Harness Engineering" blog post (Feb 2026) by Ryan Lopopolo
- Latent Space interview transcript with detailed discussion of spec.md, core beliefs, skills architecture, progressive disclosure
- Pack codebase scan: 6 packages, 13+ middleware modules, 817-line graph.py, provider abstractions, 3-tier compaction

### Key Blog Findings
- AGENTS.md as ~100-line TOC pointing to structured docs/ (not encyclopedia)
- Spec.md "ghost library" — multi-pass verified specs that reproduce systems from docs alone
- Only 6 reusable skills with tracing/observability built in
- Custom linters with remediation in error messages (violations become teaching moments)
- Execution plans versioned in repo with progress/decision logs
- End-to-end autonomous loops (6+ hour unattended runs)
- Rigid architectural layers enforced mechanically (Types → Config → Repo → Service → Runtime → UI)
- Agent-to-agent review; humans review only when needed
- "Golden principles" with background garbage collection agents
- "Human taste captured once, enforced continuously"
- Architecture becomes the prompt — reduces agent search space

### Core Thesis
Agent engineering changes the unit of software design from "what is clean for a human team of 7?" to "what is safe and legible for a human team coordinating hundreds of parallel software workers." Without strong boundaries, more agents create more mess faster. With strong boundaries, more agents create more output faster.

## Ranked Ideas

### Tier 1: Transformative

#### 1. Repository-Native Knowledge Architecture with Progressive Disclosure
**Description:** Shrink AGENTS.md from 20KB to ~50-line TOC. Push domain knowledge into structured docs/ with tiered access: core-beliefs.md, ARCHITECTURE.md, design-docs/, specs/ (ghost libraries), quality-scores.md, DOMAIN_RULES.md, PATTERNS.md. Spec files are verified by CI to match actual code. Agent starts with TOC and reads deeper docs on demand.
**Rationale:** The blog's #1 finding. "Give Codex a map, not a 1,000-page instruction manual." Progressive disclosure keeps context lean. Spec verification ensures docs stay accurate. This is the foundation everything else builds on.
**Downsides:** Significant upfront documentation effort. Spec verification tests add maintenance. Requires discipline to keep docs updated (mitigated by CI enforcement + doc-gardening agents).
**Confidence:** 85%
**Complexity:** High
**Status:** Unexplored

#### 2. Execution Plans as First-Class Versioned Artifacts
**Description:** Before writing code, agent emits structured plan (markdown) into working directory. Plan lists steps, files to touch, risks, acceptance criteria. Each retry creates new plan version. Verify-retry checks plan progress. Autonomous intent resolution folds in as the "understand task" phase.
**Rationale:** Directly addresses iterative task failures (0% on optimization tasks). Plans make runs resumable after crashes (fixes 36% API disconnect problem). Creates auditable decision trail.
**Downsides:** Planning step costs tokens/latency on simple tasks. Needs task-difficulty gating. Plan quality depends on model capability.
**Confidence:** 85%
**Complexity:** Medium
**Status:** Unexplored

#### 3. Long-Horizon Autonomous Loop Completion
**Description:** Complete the autonomous loop beyond verify-retry: self-review → feedback handling → regression detection → judgment-based escalation. Target: 6+ hour unattended runs. The harness is the box; the model chooses how to proceed.
**Rationale:** The blog's north star. Current Pack loop: receive task → execute → verify → retry. Missing: post-verify self-review, feedback handling, regression detection, escalation logic. Long-horizon success is the ultimate benchmark.
**Downsides:** Requires most other ideas to be in place first. High complexity. Risk of runaway token spend without proper cost controls.
**Confidence:** 80%
**Complexity:** High
**Status:** Unexplored

### Tier 2: High-Leverage

#### 4. Cross-Run Knowledge Accumulation with Failure Pattern Indexing
**Description:** After each task, distill reusable lessons into persistent project-scoped knowledge base. Index failure patterns with remediation. Session logs feed batch analysis. Retrieve during bootstrap based on project fingerprint similarity.
**Rationale:** Highest compounding effect. Every failure becomes a lesson never repeated. Blog's transcript: "failed builds, PR comments, and test failures all represent signals indicating missing context."
**Downsides:** Knowledge quality depends on distillation. Stale knowledge could mislead. Similarity retrieval adds complexity.
**Confidence:** 75%
**Complexity:** High
**Status:** Unexplored

#### 5. Mechanical Architecture Enforcement via Lint Middleware
**Description:** New middleware that enforces repo-specific architecture rules after file-modifying tool calls. Rules declared in .pack-rules.md (agent-readable markdown). Lint error messages include remediation injected into agent context.
**Rationale:** "Human taste captured once, enforced continuously." Every rule has infinite ROI. Structural tests with remediation messages turn violations into teaching moments for agents.
**Downsides:** Rule authoring burden. Over-constraining can block valid solutions. Needs sensible defaults.
**Confidence:** 75%
**Complexity:** Medium
**Status:** Unexplored

#### 6. Verify-Diagnose-Replan (Structured Diagnosis Between Retries)
**Description:** Mandatory root-cause hypothesis between verify failure and retry. Agent produces written diagnosis, identifies wrong assumption, generates revised plan. If diagnosis matches previous failed diagnosis, force orthogonal approach.
**Rationale:** Upgrades verify-retry from "try again harder" to "understand why, then try differently." Directly addresses iterative task gap.
**Downsides:** Diagnosis costs tokens. Agent may produce shallow diagnoses. Best when integrated with execution plans.
**Confidence:** 75%
**Complexity:** Medium
**Status:** Unexplored

#### 7. Test-First Verify Loop (TDD Inversion)
**Description:** When acceptance criteria exist, write/identify the test first, then enter tight code→test→fail→fix loop. Anchors agent to formal criterion rather than fuzzy "done."
**Rationale:** 100% local vs 32% formal gap suggests Pack "thinks" it's done when it isn't. Test-first anchors to real criterion.
**Downsides:** Not all tasks have pre-writable tests. Test generation quality varies. Adds upfront cost.
**Confidence:** 75%
**Complexity:** Medium
**Status:** Unexplored

### Tier 3: Infrastructure

#### 8. Benchmark Regression CI with Bisection
**Description:** Every harness PR runs smoke benchmark subset. Full suite nightly. Time-series tracking. Regression detection by task category. Bisection identifies which commit caused degradation.
**Rationale:** Can't engineer what you can't measure. Essential feedback loop for harness development.
**Downsides:** CI compute cost. Benchmark flakiness. Maintaining representative smoke suite.
**Confidence:** 80%
**Complexity:** Medium-High
**Status:** Unexplored

#### 9. Garbage Collection Middleware (Post-Task Cleanup)
**Description:** After agent declares "done," lightweight cleanup pass reviews diff: removes debug prints, dead code, commented-out code, overly broad exception handlers. Operates on git diff.
**Rationale:** Core blog concept. Verify-retry produces "fix layering" — each retry adds defensive code. Cleanup pass turns messy-but-working into clean.
**Confidence:** 80%
**Complexity:** Low
**Status:** Unexplored

#### 10. Progress Velocity Detection (Evolved Doom Loop)
**Description:** Add progress signals beyond tool-call identity: test result delta, edit revert detection, context churn rate. Catches "different actions, no progress" stalls before doom loop threshold fires.
**Rationale:** Current doom loop detection catches obvious repetition. Velocity detection catches the subtler case: agent makes different calls each time but isn't making progress.
**Confidence:** 70%
**Complexity:** Low-Medium
**Status:** Unexplored

#### 11. Middleware Conditional Activation
**Description:** Middleware declares activation conditions (task type, tool type, turn count). Harness activates only relevant subset per tool call. Simple tasks skip heavyweight middleware.
**Rationale:** 15+ middleware modules running unconditionally doesn't scale. Conditional activation prevents bloat as new middleware is added.
**Confidence:** 65%
**Complexity:** Medium
**Status:** Unexplored

#### 12. Parallel Hypothesis Exploration
**Description:** On ambiguous tasks, fork 2-3 scoped sub-agents pursuing different hypotheses. First to pass verification wins. Actually cheaper than serial retry in many cases.
**Rationale:** Serial exploration of wrong hypotheses is expensive. Parallel exploration with early termination finds answers faster.
**Confidence:** 65%
**Complexity:** Medium-High
**Status:** Unexplored

### Tactical

#### 13. Batch Script Emission
**Description:** Agent writes multi-step scripts to temp files and executes as single tool call. Token-saving for multi-file operations.
**Confidence:** 60%
**Complexity:** Low
**Status:** Unexplored

#### 14. Agent Self-Diagnostic Trace
**Description:** Post-task structured run summary (turns, stalls, context waste, tool errors) feeds into flywheel memory. Enables self-recursive improvement.
**Confidence:** 70%
**Complexity:** Low
**Status:** Unexplored

## Feature Implementation: Agent-First Architecture Engine

### Layer 1: Repository Knowledge Architecture
- Shrink AGENTS.md to ~50-line TOC
- Create ARCHITECTURE.md, docs/core-beliefs.md, docs/DOMAIN_RULES.md
- Create docs/specs/ with ghost library spec files
- Create docs/quality/quality-scores.md
- Add spec verification tests

### Layer 2: Dependency Direction Enforcement
- Define allowed dependency directions in DOMAIN_RULES.md
- Write structural tests with remediation messages in assertions
- Add file-size check tests
- Add CI job for docs structure validation

### Layer 3: Lint Middleware for Target Codebases
- Implement ArchitectureEnforcementMiddleware
- Define .pack-rules.md format (agent-readable markdown)
- Wire into middleware stack with conditional activation

### Layer 4: Agent-Legible Wrapper Pattern
- Create PackModelClient wrapper (retry, cost tracking, tracing)
- Migrate raw LLM calls to use wrapper
- Add structured logging at wrapper level

### Layer 5: Quality Score Table
- Create quality-scores.md with domain × layer grades
- Define grading criteria
- Agent uses scores for self-assessment during improvement tasks

### Layer 6: Skill Consolidation
- Define 6-8 skill set: verify-and-iterate, bootstrap-repo, plan-and-execute, review-diff, cleanup-pass, diagnose-failure, trace-analyze, architecture-check
- Implement skill specs with observability
- Wire skills with clear trigger conditions

### Implementation Sequence
- Phase 1: Knowledge Architecture (docs restructure) — 1-2 days
- Phase 2: Mechanical Enforcement (structural tests) — 1-2 days
- Phase 3: Spec Files (ghost libraries + verification) — 2-3 days
- Phase 4: Lint Middleware — 1-2 days
- Phase 5: Wrapper Consolidation — 1-2 days
- Phase 6: Skill Consolidation — 2-3 days

## Rejection Summary

| # | Idea | Reason Rejected |
|---|------|-----------------|
| 1 | Middleware pipeline visualization | User skipped; low leverage |
| 2 | Context budget dashboard | Subsumed by progressive disclosure |
| 3 | Self-assembling middleware | Subsumed by conditional activation |
| 4 | Pull-based middleware | LangGraph pipeline is correct model; conditional activation is the right fix |
| 5 | Git checkpoint per edit | Subsumed by execution plan checkpointing |
| 6 | Learned middleware policy | Subsumed by conditional activation |
| 7 | Prompt A/B testing | Subsumed by knowledge architecture |

## Session Log
- 2026-04-18: Initial ideation — 38 generated across 4 frames, 25 after dedup, 14 survived after two filtering passes. Re-evaluation promoted 7 originally-rejected ideas based on user feedback. Feature implementation developed with 6 layers and phased execution sequence.
