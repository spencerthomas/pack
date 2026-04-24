# Pack — Agent Harness Roadmap

**Status:** Living document. Last revised 2026-04-24.

## The reframing

Pack is not a coding agent. Pack is the **control loop around coding agents** — the operating system for agent-driven engineering. The coding model is a component. The harness is the product.

Every agent run must satisfy exactly one of:

1. Ship a safe change that passed all required checks.
2. Escalate with a clear reason.
3. Identify a missing rule, doc, test, or tool and promote it into the harness.

That rule turns every run into an improvement loop. The codebase becomes more legible, testable, and agent-operable after each one — instead of degrading into vibe-coded entropy.

## The target architecture — five layers

```
┌─────────────────────────────────────────────┐
│ 5. Learning        docs + tests + rules     │
│                    promoted from failures    │
├─────────────────────────────────────────────┤
│ 4. Verification    tests, lint, arch rules, │
│                    reviewer agent            │
├─────────────────────────────────────────────┤
│ 3. Execution       worktree, sandbox, tools │
│                    (the agent sits here)     │
├─────────────────────────────────────────────┤
│ 2. Context         specs, domain packs,     │
│                    code maps, examples      │
├─────────────────────────────────────────────┤
│ 1. Policy          task type, permissions,  │
│                    approval, scope caps     │
└─────────────────────────────────────────────┘
```

Pack today: Layer 3 mature; Layer 4 partial (tests + `PreCompletionChecklist` + `OutputCeiling`); Layer 2 basic (`SystemPromptBuilder` + `TaskHints`); Layers 1 and 5 absent.

## The canonical control loop

```
business intent
  ↓
task classification         (Layer 1)
  ↓
policy + context pack load  (Layer 1 + 2)
  ↓
codebase discovery          (Layer 2)
  ↓
plan + plan review          (Layer 1 gate)
  ↓
scoped agent execution      (Layer 3, bounded by policy)
  ↓
verification pipeline       (Layer 4)
  ↓
reviewer agent              (Layer 4)
  ↓
human approval if required  (Layer 1 gate)
  ↓
merge
  ↓
lesson promotion            (Layer 5)
  ↓
next run is better
```

## Phases

Six phases, each independently shippable. Phase-A items move the benchmark *and* establish durable structure. Later phases only matter once they have real signal to compound on, so the ordering is enforced: you cannot meaningfully learn (Phase E) without reviewer signal (Phase C), and you cannot usefully promote rules (Addition 3) without ratchet state (Addition 1).

### Phase A — Policy layer + scope enforcement

**Goal:** The agent cannot write outside its task scope.

- **A.1** `TaskPolicy` dataclass + `policy_for(task_hints)` dispatch. 6-8 predefined policies keyed on phase/domain: `docs`, `bugfix`, `feature`, `refactor`, `migration`, `test-generation`, `security-fix`, `unknown`.
- **A.2** `ScopeEnforcementMiddleware` — rejects write_file/edit_file outside `allowed_paths`, enforces `max_files_changed`, emits teach-at-failure messages.
- **A.3** Ratchet substrate — `.harness/violations.json` tracks existing violations (seeded on first run, tolerated forever); new violations get blocked. `.harness/quality-score.json` is the rolling metric.

Ships immediately: closes the real correctness hole that agents can currently write anywhere with no enforced scope.

### Phase B — Context layer + discovery

**Goal:** The agent sees only the context that applies to its task. Brownfield repos can onboard.

- **B.1** `.context-packs/<domain>/` convention (`README.md`, `rules.md`, `examples/`, `allowed-files.yaml`, `required-checks.yaml`).
- **B.2** `context_loader.py` — resolves which pack to load based on `TaskHints.domain` + repo path. Feeds the result into `SystemPromptBuilder.add_context_pack()`.
- **B.3** `harness discover` command — read-only first pass against a repo. Emits `docs/generated/codebase-map.md`, `package-map.md`, `domain-candidates.md`, `risk-areas.md`. Proposes initial context-pack skeletons.

### Phase C — Reviewer sub-agent

**Goal:** Every non-trivial change is critiqued by a second model pass before termination.

- **C.1** `ReviewerSubAgent` with a different system prompt focused on "find what's wrong with this diff." Called after the main agent declares done; its output is fed back as a HumanMessage before termination is allowed.
- **C.2** Structured `ReviewVerdict(status, concerns, required_fixes)` consumed by the policy layer.
- **C.3** Policy-gated: `security-fix` and `migration` policies require a passing verdict; `docs` and `test-generation` do not.

### Phase D — Verification layer v2 (architecture lint + business rules)

**Goal:** Structural violations get blocked inline with teach-at-failure messages, not in post-commit review.

- **D.1** `tools/arch-lint/` with custom rules: forbidden imports, file-size caps, dependency direction, required docstrings for public APIs.
- **D.2** `wrap_tool_call` interception on writes runs arch-lint and rejects inline with specific fix suggestions.
- **D.3** **Ratchet mode**: existing violations allowed (read from A.3 tracker), new violations blocked.
- **D.4** Business-rule checker: domain packs declare invariants; these become checks via `tools/business-rule-checker/`.

### Phase E — Learning layer

**Goal:** Failures become durable artifacts.

- **E.1** Trace analyzer categorizes failing-vs-passing trial deltas into: `missing_context`, `missing_rule`, `missing_tool`, `missing_example`, `model_capability_limit`.
- **E.2** `harness promote-lesson` CLI converts a reviewed failure into an artifact by category:

  | Category | Auto-promoted to | Human gate |
  |----------|------------------|------------|
  | missing context | domain-pack `rules.md` | review before commit |
  | wrong import | arch-lint rule | review |
  | skipped test | required-checks.yaml | review |
  | reinvented helper | `known-utilities.md` | review |
  | behavior mismatch | golden test fixture | review |
  | model capability | `known-limits.md` | manual only |

- **E.3** Governance: every promotion requires human approval; auto-applied fixes are limited to low-risk categories (docs, tests).

### Phase F — Autonomous cleanup

**Goal:** The system compounds.

- Scheduled agents sweep the repo for ratchet violations, propose small cleanup PRs, bump quality score. Blocks on human review.

Only meaningful after D is mature.

## Three cross-cutting additions

These insert into the phases above rather than standing alone.

### Addition 1 — Ratchet (folds into Phase A.3, reused by D)

The ratchet is the mechanism that makes "every run either ships or improves" real. Without it, policy is just noise. `.harness/violations.json` + `.harness/quality-score.json` are the substrate. All later phases consult these files.

### Addition 2 — Discovery / reverse engineering (folds into Phase B.3)

Context packs can't exist without initial generation from the target repo. `harness discover` scans a brownfield codebase and proposes packs; humans curate. Without this, context packs only work greenfield.

### Addition 3 — Lesson promotion automation (folds into Phase E.2)

The pay-off step. Without automated promotion with human gating, the learning layer is just logs.

## What the human owns vs what the harness owns

**Human:** product intent, business rules, architecture principles, risk thresholds, approval policy, taste, tradeoffs, final responsibility.

**Harness:** task classification, context assembly, scope enforcement, test/lint/arch checks, reviewer coordination, lesson promotion proposals.

**Agent (inside harness Layer 3):** discovery, draft implementation, test generation, refactor mechanics, docs updates, PR preparation, first-pass review.

## Dogfooding plan

Pack applies the harness to Pack. The repo has three packages with clear dependency direction: `evals → cli → deepagents`. Encode as an arch-lint rule early. Every new middleware must ship with a test file, a wiring entry in `agent.py`, and a catalog entry in `docs/middleware-catalog.md`. Trajectories from TB2 runs feed `known-failure-modes.md`. This proves the system works on a real (if small) codebase before any external user sees it.

## What not to build yet

- Domain packs for real business logic (valuation, BPO) — premature until there's a production workload.
- Heavy policy YAML schema — hardcoded Python dispatch is fine for 4-6 task types.
- Migration lanes as a first-class concept — structure will emerge from real usage.
- Specialized reviewer sub-agents per domain — one generic first.

## Success criteria

The harness is working when:

1. A new contributor can open a task against Pack, get an auto-selected policy + context pack, and have their change validated without reading the repo first.
2. A failure that happens twice becomes a rule, test, or documentation change — not a third failure.
3. Pack's own quality score only moves up, never down. New violations cannot land; existing violations decrease over time via cleanup agents.
4. The TB2 pass rate plateaus or rises as a side effect, because TB2 becomes a single application of the generic harness.

The TB2 score is a lagging indicator of the harness working, not the goal itself.

## External review — 2026-04-24

An external review scored Pack at **6.8/10** overall, with these reads:

| Area | Score |
|---|---|
| Vision clarity | 9/10 |
| Harness architecture | 8/10 |
| Runtime enforcement | 6.5/10 |
| Business-logic adaptation | 3/10 |
| Brownfield codebase adoption | 3/10 |
| Learning loop | 2.5/10 |
| Product readiness | 4/10 |
| Benchmark proof | 4/10 |

Core critique: Pack has the right architecture and early enforcement but **does not yet complete the compounding loop**. Middleware nudges agent behavior; policy + checks don't yet reshape the repo over time; lessons don't yet become durable artifacts.

The review's plan converted to six milestones and five immediate PRs. After shipping arch-lint, `harness discover`, and the trace analyzer in commit `9c1dcd74`, the remaining high-priority work aligns with:

### M1 — `.harness/` as the declarative control plane

Move policies out of hardcoded Python into `.harness/config.yaml`. A target repo describes its own task policies, dependency rules, required checks, and packages. `policy_for` loads config when present, falls back to the Python defaults otherwise.

### M2 — Finish the ratchet (runtime persistence)

Wire `Ratchet.record` into the agent loop via `ScopeEnforcement` and `ArchLint` violation recorders. Existing violations at run start are loaded and treated as tolerated; new violations block and persist. `.harness/quality-score.json` gets appended to per run.

**Review calls this the single most important milestone.**

### M3 — `harness discover` ✅ (shipped in `9c1dcd74`)

First version lands as a Python function that can be called directly. CLI entry point wrapping it is pending.

### M4 — Architecture lint ✅ (shipped in `9c1dcd74`)

`arch_lint.py` enforces `PACKAGE_EDGES`. Ratchet mode supported via `existing_violations` param. Wiring into `create_cli_agent` is pending so the middleware actually fires in Harbor runs.

### M5 — Domain packs with executable rules

Evolve context packs from (README + rules + pack.yaml) to include `checks.yaml` (invariants), `examples/`, `golden-cases/`, `schemas/`, `known-failure-modes.md`, `allowed-files.yaml`, `required-checks.yaml`. Build `tools/business-rule-checker/` that runs the invariants as static/schema/golden-case checks.

### M6 — Lesson promotion automation

`harness promote-lesson --from-run <id>` takes a `TraceInsight` (Phase E.1 output) and proposes a durable artifact: updated rules.md, new golden test, new arch rule, new business-rule, etc. Human approval gate before any commit.

## Immediate next PRs (merged review + roadmap)

1. **Ratchet runtime persistence** (M2) — wire ScopeEnforcement + ArchLint violation recorders into a live Ratchet. Most-important item per the review.
2. **`.harness/config.yaml` loader** (M1) — declarative repo control plane.
3. **`harness check` unified CLI** — compose all existing checkers into one JSON-emitting command.
4. **Arch-lint wired into `create_cli_agent`** — the middleware exists but isn't active in the agent loop yet.
5. **Diff-aware reviewer** — move reviewer input from "recent messages" to "actual diff + test output + arch-lint output".

## Session decisions referenced

- **2026-04-24 — `c4d46726`** — SystemPromptBuilder wired + TaskHints classifier introduced. Layer 2 foundation.
- **2026-04-24 — `5996de9d`** — Seven harness fixes: controller env leak, Harbor-timeout wiring, OutputCeiling, ProgressiveDisclosure, tool-result enrichment, shell allowlist, LangSmith scaffolding.
- **2026-04-24 — `3cb1b78b`, `a2814fb2`** — Phase A (policy + scope + ratchet substrate).
- **2026-04-24 — `6cfc2aa5`** — Phase B (context packs) + Phase C (reviewer).
- **2026-04-24 — `9c1dcd74`** — Phase D.1 (arch-lint) + B.3 (discover) + E.1 (trace analyzer).
- **2026-04-24 — this roadmap update** — External review integrated; six-milestone plan + five immediate PRs consolidated.
