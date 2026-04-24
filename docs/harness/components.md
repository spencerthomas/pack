# Pack harness — components reference

**Status:** Current as of commit `6cfc2aa5` (2026-04-24). Companion to
[`docs/roadmap/agent-harness-roadmap.md`](../roadmap/agent-harness-roadmap.md),
which is the forward plan. This doc is the backward-looking catalog of
what exists today.

## Overview

Pack's agent runtime is a stack of LangGraph middleware wrapped around
a single coding model. Each middleware intercepts one of three hook
points — `wrap_model_call` (before the LLM sees the prompt),
`after_model` (after the LLM responds), or `wrap_tool_call` (around
every tool invocation) — and mutates the request or result to enforce
a single concern.

The components below are organized by the roadmap's 5-layer model. Each
entry lists: where the code lives, when it activates, what state it
reads and writes, and where the tests are.

## Layer 1 — Policy

Decides what kind of run is happening and what bounds apply.

### TaskPolicy + policy_for

- **File:** `libs/cli/deepagents_cli/policy.py`
- **Tests:** `libs/cli/tests/unit_tests/test_policy.py` (23 tests)
- **Activates:** always — `Harbor wrapper` calls `policy_for(task_hints)`
  per-trial.
- **Inputs:** `TaskHints` dict from the classifier.
- **Outputs:** frozen `TaskPolicy(task_type, allowed_paths,
  max_files_changed, require_tests_pass, require_plan,
  require_reviewer, approval_level, required_checks)`.
- **Predefined policies:** `docs`, `test-generation`, `bugfix`,
  `feature`, `refactor`, `migration`, `security-fix`, `unknown`
  (conservative fallback).
- **Dispatch:** explicit `task_type` in hints wins; otherwise
  domain-first override (crypto → security-fix) then phase-driven
  (`fix` → bugfix, `build` → feature, `test` → test-generation,
  `examine` → custom scoped policy).

### ScopeEnforcementMiddleware

- **File:** `libs/cli/deepagents_cli/scope_enforcement.py`
- **Tests:** `libs/cli/tests/unit_tests/test_scope_enforcement.py`
  (20 tests)
- **Hook:** `wrap_tool_call`
- **Activates:** non-interactive mode with `task_policy` set
  (Harbor always provides one).
- **Gates:** `write_file`, `edit_file` against `policy.allowed_paths`
  globs. Does not gate `read_file` or `execute` (shell gets its own
  allowlist).
- **Path matching:** `_matches_any` iterates path suffixes and
  expands `**/` patterns so repo-relative globs match absolute
  container paths (`docs/**` matches `/app/docs/foo.md`).
- **Rejection:** returns a teach-at-failure `ToolMessage` with
  `status="error"` naming the rule, the allowed globs, and a
  concrete suggestion.
- **max_files_changed:** tracked per-run; only successful writes
  increment so a single malformed-path rejection doesn't burn the
  budget.

### Ratchet substrate

- **File:** `libs/cli/deepagents_cli/ratchet.py`
- **Tests:** `libs/cli/tests/unit_tests/test_ratchet.py` (17 tests)
- **State:** `.harness/violations.json`, `.harness/quality-score.json`
  at repo root (or trial-dir for per-trial state).
- **API:** `Ratchet.record(rule, subject, reason)` returns
  `(violation, is_new)` where `is_new=False` means the (rule,
  subject) pair is already tracked — tolerated debt, not a new
  regression. `append_snapshot(QualitySnapshot)` adds to a rolling
  history.
- **Runtime wiring:** `create_cli_agent` accepts a `ratchet_dir`
  kwarg; when supplied, `ScopeEnforcementMiddleware` and
  `ArchLintMiddleware` route their rejections into the ratchet.
  Existing violations at run start are loaded as seed state and
  treated as tolerated; new violations persist to disk. Harbor
  points the ratchet at `<trial_dir>/.harness` so per-trial state is
  inspectable in the run archive.
- **Integration test:** `test_agent_ratchet_wiring.py` (6 tests)
  covers scope + arch recording, dedup, and seeding.

### Declarative control plane (`.harness/config.yaml`)

- **File:** `libs/cli/deepagents_cli/harness_config.py`
- **Tests:** `libs/cli/tests/unit_tests/test_harness_config.py`
  (17 tests)
- **Purpose:** move policy overrides from hardcoded Python into a
  repo-level YAML config. Targets M1 of the review plan.
- **Shape:** `version`, `repo (name, root)`, `packages`,
  `dependency_rules`, `task_policies`. Unknown keys ignored for
  forward/backward compat.
- **API:** `find_harness_dir()` walks up from cwd; `load_config()`
  returns a typed `HarnessConfig` or None when the file is missing
  or malformed. `policy_from_config(config, task_type, default)`
  merges a config override on top of a base `TaskPolicy`.
- **YAML:** prefers PyYAML; falls back to a naive single-line-flow
  parser when PyYAML isn't importable so the module works in
  minimal environments.

### `harness check` unified pipeline

- **File:** `libs/cli/deepagents_cli/harness_check.py`
- **Tests:** `libs/cli/tests/unit_tests/test_harness_check.py`
  (14 tests)
- **Purpose:** PR 3 from the review plan. Composes the checks the
  harness already knows about into a single callable that emits
  machine-readable JSON and human-readable text.
- **Registered checks:** `arch-lint` (in-process), `business-rules`
  (in-process invariant runner), `tests` (uv run pytest or bare
  pytest), `lint` (ruff check), `typecheck` (ty then mypy fallback),
  `docs-lint` (ruff --select D).
- **Result shape:** every check produces a `CheckResult(name,
  status, summary, command, details)` with status in
  `{pass, fail, not_configured, skip}`. Unknown check names report
  as `not_configured` rather than erroring.
- **Composite status:** `fail` if any check failed; `pass`
  otherwise. `not_configured` and `skip` are non-blocking.
- **CLI entry point:** callable as `run_checks(repo_root,
  checks=...)`. A thin CLI wrapper can land next without touching
  the composition logic.

### Business-rule checker (executable invariants)

- **File:** `libs/cli/deepagents_cli/business_rule_checker.py`
- **Tests:** `libs/cli/tests/unit_tests/test_business_rule_checker.py`
  (39 tests)
- **Purpose:** M5 of the review plan. Context packs gain a
  `checks.yaml` that declares invariants the harness enforces.
- **Matcher types (initial):** `regex` (at least one file must
  match), `absent_regex` (no file may match), `file_exists` (for
  each file in `paths`, a computed companion `target` must exist —
  supports `{stem}` / `{path}` interpolation).
- **Severity:** `block` flips the check to fail; `warn` and `info`
  surface details without blocking.
- **First dogfood:** `.context-packs/coding-task/checks.yaml`
  carries two invariants — `no_leftover_ics_uploads` (warn) and
  `middleware_has_tests` (info). The file is the template external
  users copy when starting.
- **Integration:** wired into `harness_check` as the
  `business-rules` runner.

### ArchLintMiddleware + `check_file`

- **File:** `libs/cli/deepagents_cli/arch_lint.py`
- **Tests:** `libs/cli/tests/unit_tests/test_arch_lint.py` (39 tests)
- **Hook:** `wrap_tool_call` (runtime enforcement); also callable as
  a pure checker (`check_file(path, source)`) for CI or reviewer use.
- **Enforces:** Pack's package dependency direction via
  `PACKAGE_EDGES`. `evals → cli → deepagents` is the only allowed
  direction; reverse imports are blocked.
- **Path resolution:** `package_for_path(path)` maps filesystem
  paths to package names; tests and scripts are excluded from
  enforcement.
- **Import extraction:** AST-based when source is valid Python;
  regex fallback when source won't parse (e.g. a partial edit).
- **Ratchet mode:** `existing_violations` set passed at construction
  time is tolerated; only new `(importer, imported)` pairs are
  rejected. New violations flow to the `violation_recorder` callback.
- **Teach-at-failure:** rejection message lists the full
  `PACKAGE_EDGES` table and points at this doc for the layering
  rationale.

### harness discover

- **File:** `libs/cli/deepagents_cli/harness_discover.py`
- **Tests:** `libs/cli/tests/unit_tests/test_harness_discover.py`
  (20 tests)
- **Purpose:** one-shot read-only scan of a brownfield repo; emits
  four markdown reports under `docs/generated/` and proposes initial
  context-pack skeletons under `.context-packs/proposed/`.
- **Reports:** `codebase-map.md` (languages, top-level directories),
  `package-map.md` (detected packages + inferred dependency edges),
  `domain-candidates.md` (dirs under `src/`/`packages/`/`libs/` with
  README excerpts), `risk-areas.md` (files over 500 LOC, directories
  without tests).
- **Pack proposal:** top 5 detected packages get skeleton
  `README.md` + `rules.md` with placeholder content. Idempotent —
  existing edits aren't overwritten.
- **No LLM calls:** pure filesystem scan plus regex-based Python
  import extraction. Fast; safe to run repeatedly.
- **API:** `discover(repo_root, write_outputs=True)` returns a
  `DiscoveryResult` dataclass.

## Layer 2 — Context

Determines what the LLM sees on every turn.

### SystemPromptBuilder

- **File:** `libs/deepagents/deepagents/prompt/builder.py`
- **Tests:** `libs/deepagents/tests/unit_tests/prompt/test_prompt_builder.py`
  (35 tests) + `test_graph_prompt_pack.py` (17 tests)
- **Activates:** `PACK_ENABLED=1` env var. Harbor sets this per run.
- **Assembles:** static sections (identity, safety, tool rules,
  style, user-provided preamble, context pack) followed by dynamic
  sections (environment, git, task hints).
- **Cache annotation:** provider-aware via `cache_strategy.py`. On
  Anthropic models, the last cacheable section gets
  `cache_control: {"type": "ephemeral"}`. On OpenAI/OpenRouter/other,
  returns plain text.
- **Task hint override:** `prompt_env_override` kwarg (`dict` or
  `None`) disables auto-collection of cwd/os/git. Harbor passes
  `{"cwd": "/app", ...None}` so controller state doesn't leak into
  the container agent's prompt.

### TaskClassifier

- **File:** `libs/deepagents/deepagents/prompt/task_classifier.py`
- **Tests:** `libs/deepagents/tests/unit_tests/prompt/test_task_classifier.py`
  (26 tests)
- **API:** `classify(instruction) -> TaskHints(phase, domain,
  complexity, guidance)`. Sub-millisecond, deterministic,
  keyword+regex.
- **Domains:** `python, c, git, shell, web, data, systems, crypto`.
- **Phases:** `fix, build, examine, test`.
- **Output:** `TaskHints.as_dict()` flattens to the dict shape
  `SystemPromptBuilder` expects.

### ContextPack + resolver

- **File:** `libs/deepagents/deepagents/prompt/context_pack.py`
- **Tests:** `libs/deepagents/tests/unit_tests/prompt/test_context_pack.py`
  (29 tests)
- **Convention:** `.context-packs/<name>/` at repo root, each with
  `README.md` (summary), `rules.md` (hard constraints), optional
  `pack.yaml` (applicable domains/phases).
- **API:** `load_pack(path)` returns `ContextPack | None` (None for
  missing or empty). `list_packs(dir)` enumerates. `resolve_pack(
  hints, dir)` picks via explicit → domain → phase → `coding-task`
  fallback.
- **First pack:** `.context-packs/coding-task/` — Pack's own
  dogfood. Applies whenever no specialized pack matches.
- **Builder integration:** `SystemPromptBuilder.add_context_pack(
  pack)` renders a cacheable `## Context pack: <name>` section.

### ProgressiveDisclosureMiddleware

- **File:** `libs/cli/deepagents_cli/progressive_disclosure.py`
- **Tests:** `libs/cli/tests/unit_tests/test_progressive_disclosure.py`
  (19 tests)
- **Hook:** `wrap_model_call`
- **Activates:** any request when `task_hints` indicates a coding
  task (phase or domain present).
- **Prunes:** default distractor set = `{fetch_url, web_search,
  compact_conversation}`. Core tools (read/write/edit/glob/grep/
  execute) always preserved.
- **Rationale:** trajectory analysis shows per-step completion
  tokens are the strongest PASS predictor; unused tools in the
  schema inflate reasoning-to-action ratio.

## Layer 3 — Execution

Where the agent actually runs. Pack's existing backend + tool surface.
Documented in the upstream deepagents docs; not re-catalogued here.

## Layer 4 — Verification

Runs checks during and after the main agent's work.

### PreCompletionChecklistMiddleware

- **File:** `libs/cli/deepagents_cli/precompletion_checklist.py`
- **Tests:** `libs/cli/tests/unit_tests/test_precompletion_checklist.py`
  (15 tests)
- **Hook:** `after_model` with `can_jump_to=["model"]`
- **Activates:** non-interactive mode.
- **Fires:** when the most recent `AIMessage` has no `tool_calls`
  (the natural done signal). Injects a structured verification
  checklist and jumps back to the model.
- **Cycles:** `max_cycles=1` by default — one forced verification
  pass before real termination is allowed. Counted statelessly via
  the `[PRECOMPLETION-CHECKLIST]` marker in the message history.

### OutputCeilingMiddleware

- **File:** `libs/cli/deepagents_cli/output_ceiling.py`
- **Tests:** `libs/cli/tests/unit_tests/test_output_ceiling.py`
  (16 tests)
- **Hook:** `after_model` with `can_jump_to=["model"]`
- **Activates:** non-interactive mode.
- **Fires:** when cumulative completion tokens across all
  `AIMessage`s crosses `soft_ceiling_tokens` (default 25,000).
  Injects a "stop analyzing, commit to a concrete solution now"
  nudge.
- **Caps:** `max_interventions=1` — one nudge per run; repeat
  nudges become noise.
- **Token counting:** prefers `AIMessage.usage_metadata.output_tokens`
  with a char/4 heuristic fallback for providers that don't populate
  usage metadata.

### BudgetObservableMiddleware

- **File:** `libs/cli/deepagents_cli/budget_observable.py`
- **Tests:** `libs/cli/tests/unit_tests/test_budget_observable.py`
  (17 tests)
- **Hook:** `wrap_tool_call`
- **Activates:** non-interactive mode.
- **Appends:** to every tool result — `[budget: Xm Ys remaining / Nm
  total]` or, below `critical_threshold_sec`, `[BUDGET CRITICAL: ...]`
  urging best-so-far submission.
- **Clock:** `_started_at` lazily initialized on the first tool call
  so container setup and agent graph assembly don't eat the budget.
- **Harbor override:** `budget_total_sec` kwarg on `create_cli_agent`
  accepts the real per-task timeout; Harbor wrapper computes it via
  `_resolve_agent_timeout(configuration)`.
- **Default:** 1800s (30 min) — conservative enough for most TB2
  tasks when no explicit value is available.

### ReviewerSubAgent + ReviewerMiddleware

- **Files:** `libs/cli/deepagents_cli/reviewer.py`,
  `libs/cli/deepagents_cli/reviewer_middleware.py`
- **Tests:** `libs/cli/tests/unit_tests/test_reviewer.py` (32 tests),
  `test_reviewer_middleware.py` (25 tests)
- **Hook:** `after_model` with `can_jump_to=["model"]`
- **Activates:** `task_policy.require_reviewer == True` (set for
  `bugfix`, `feature`, `refactor`, `migration`, `security-fix`,
  `unknown`). No-ops when policy is None or opts out.
- **Sub-agent:** invokes the model with a dedicated review system
  prompt (find problems, don't extend the work). No tools passed —
  review is advisory.
- **Verdict:** structured `ReviewVerdict(status, summary, concerns,
  required_fixes)` parsed from JSON-fenced model output. Malformed
  output becomes `block` rather than raising.
- **Evidence-based (PR 5):** the middleware assembles a diff
  summary from the main agent's `write_file`/`edit_file` tool
  calls and passes it to the reviewer as structured evidence.
  The reviewer prompt instructs the model to prioritize evidence
  over conversational claims — if the agent said "tests pass" but
  the test output in evidence shows failures, that's request_changes
  at minimum.
- **Loop control:** `approve` → termination proceeds.
  `request_changes`/`block` → verdict injected as HumanMessage,
  jumps back to model. `max_reviews=2` caps total passes.
- **Model resolution:** `_resolve_reviewer_model` in `agent.py`
  reuses the main agent's model; strings resolved via
  `init_chat_model`.

### ToolResultEnrichmentMiddleware

- **File:** `libs/cli/deepagents_cli/tool_result_enrichment.py`
- **Tests:** `libs/cli/tests/unit_tests/test_tool_result_enrichment.py`
  (22 tests)
- **Hook:** `wrap_tool_call`
- **Activates:** always (in non-interactive mode).
- **Appends:** derived-signal markers to tool results:
  - `read_file` → `[file: N lines, size, .ext]`
  - `ls`/`list_directory` → `[dir: N entries, M subdirs]`
  - `execute` → `[exit=X, N stdout, M stderr]`
  - `glob`/`grep` → `[matches: N]`
- **Rationale:** agents re-derive state on every tool return; a
  compact marker lets them pattern-match instead.
- **Extensibility:** `extra_derivations` kwarg lets custom tools
  register their own signal functions.

## Layer 5 — Learning

Trace analyzer and reflection watcher provide the raw signal; the
lesson-promotion automation that turns signal into durable artifacts
is the next phase.

### Promote-lesson automation

- **File:** `libs/cli/deepagents_cli/promote_lesson.py`
- **Tests:** `libs/cli/tests/unit_tests/test_promote_lesson.py`
  (20 tests)
- **Purpose:** M6 of the review plan. Turns a `TraceInsight` into a
  staged artifact proposal under `.harness/pending-promotions/`.
- **Inputs:** a trial directory (or a bare `TraceInsight`) plus
  optional explicit `harness_dir`.
- **Outputs:** `PromotionProposal(category, confidence, title,
  target_path, body, evidence, rationale)` plus a staged markdown
  file at `.harness/pending-promotions/<timestamp>-<category>-<trial>.md`.
- **Category → target:**
  - `missing_context` → append to `coding-task/rules.md`
  - `missing_rule` → rule entry in `coding-task/rules.md` (rule
    already enforced; context just needed to echo it)
  - `missing_tool` → no single target — architectural decision
  - `missing_example` → new file in `coding-task/examples/`
  - `model_capability_limit` → append to `docs/harness/known-limits.md`
- **Never auto-commits:** proposals stage to
  `.harness/pending-promotions/` for human review. Governance stays
  with the operator.
- **CLI entry:** `promote_from_trial(trial_dir)` reads a Harbor
  trial, runs the analyzer, and stages unless the insight is a
  low-confidence `model_capability_limit` (skips to avoid operator
  inbox spam on provider blips).

### Trace analyzer

- **File:** `libs/cli/deepagents_cli/trace_analyzer.py`
- **Tests:** `libs/cli/tests/unit_tests/test_trace_analyzer.py`
  (19 tests)
- **Inputs:** a Harbor trial directory (reads `result.json` +
  `agent/trajectory.json`) plus an optional `ReviewVerdict`.
- **Outputs:** `TraceInsight(category, confidence, summary,
  evidence, proposed_promotion)` where category is one of
  `missing_context`, `missing_rule`, `missing_tool`,
  `missing_example`, `model_capability_limit`.
- **Dispatch order:** architectural rejections beat scope
  rejections beat behavioural signals (single-shot dump, timeout).
  Matches the harness's "fix architecture first" bias.
- **Confidence:** `high` for encoded rule hits (arch rejection),
  `medium` for clear behavioural patterns, `low` for the fallback.
  Low-confidence insights should require human review before
  promotion in Phase E.2.
- **Deterministic:** no LLM calls. Signal extraction tolerates
  missing or malformed trial files by falling back to zero/False
  defaults — always returns a usable insight.

### Live reflection watcher

- **Files:** `benchmarks/tb2/analysis/reflect_on_run.py`,
  `aggregate_reflections.py`
- **Purpose:** polls a Harbor job directory for completed trials;
  classifies each into a failure-mode bucket; writes `reflection.md`
  per trial. `aggregate_reflections.py` rolls up into a single
  `summary.md`.
- **Modes:** deterministic heuristic (fast, no LLM) or Claude CLI
  subprocess (richer, costs a model call per trial).
- **Status:** primitive. Proper Phase E trace analyzer is the
  planned successor.

## How the pieces wire together

For a classified non-interactive run (Harbor or CLI with `-i` off):

```
Harbor wrapper
  classify(instruction) -> TaskHints
  policy_for(hints)     -> TaskPolicy
  resolve_pack(hints, .context-packs/) -> ContextPack
  _resolve_agent_timeout(config) -> budget_total_sec

create_cli_agent(..., task_hints, task_policy, context_pack, budget_total_sec)

create_deep_agent(..., task_hints, context_pack, prompt_env_override)
  PACK_ENABLED branch:
    _build_pack_system_prompt
      SystemPromptBuilder
        add_static_section(user preamble)
        add_context_pack(pack)
        build(cwd, os, git, task_hints) -> list[content block]
      SystemMessage(blocks) on Anthropic; str otherwise

create_agent(model, system_prompt, tools, middleware=[
  ...upstream middleware...
  ScopeEnforcementMiddleware(task_policy),
  EditVerificationMiddleware,
  ReadBeforeWriteMiddleware,
  DoomLoopDetectionMiddleware,
  LoopDetectionMiddleware,
  ErrorReflectionMiddleware,
  RequestBudgetMiddleware,
  PythonSyntaxCheckMiddleware,
  ToolCallLeakDetectionMiddleware,
  PreCompletionChecklistMiddleware,
  ReviewerMiddleware(task_policy),       # when policy.require_reviewer
  OutputCeilingMiddleware,
  ProgressiveDisclosureMiddleware(task_hints),
  ToolResultEnrichmentMiddleware,
  BudgetObservableMiddleware(budget_total_sec),
])
```

## Testing

Run the harness test suites:

```bash
cd libs/deepagents && uv run pytest tests/unit_tests/prompt/ tests/unit_tests/test_graph_prompt_pack.py
cd libs/cli && uv run pytest tests/unit_tests/test_policy.py \
  tests/unit_tests/test_scope_enforcement.py \
  tests/unit_tests/test_ratchet.py \
  tests/unit_tests/test_precompletion_checklist.py \
  tests/unit_tests/test_budget_observable.py \
  tests/unit_tests/test_output_ceiling.py \
  tests/unit_tests/test_progressive_disclosure.py \
  tests/unit_tests/test_tool_result_enrichment.py \
  tests/unit_tests/test_reviewer.py \
  tests/unit_tests/test_reviewer_middleware.py
```

**Counts as of this writing:**

- Phase A (policy + scope + ratchet): 60 tests
- Phase B (context pack + builder integration): 33 tests
- Phase C (reviewer + middleware): 48 tests
- Earlier harness middleware (budget, checklist, output-ceiling,
  progressive disclosure, tool-result enrichment): ~90 tests

Total harness surface coverage: ~230 tests, all passing.

## Roadmap status

- **Phase A** — ✅ policy + scope + ratchet substrate + runtime
  persistence wired into `create_cli_agent`.
- **Phase B.1 + B.2** — ✅ context pack loader + wiring. First pack
  ships at `.context-packs/coding-task/`.
- **Phase B.3** — ✅ `harness discover` scans a repo and emits
  generated reports + proposed context-pack skeletons.
- **Phase C** — ✅ reviewer sub-agent + policy-gated middleware.
  Diff-aware reviewer upgrade (PR 5) pending.
- **Phase D.1** — ✅ arch-lint with ratchet mode, live in the
  agent loop via `create_cli_agent`. Business-rule checker (D.4)
  not yet built.
- **Phase E.1** — ✅ trace analyzer produces structured insights.
  `harness promote-lesson` automation (E.2) still pending.
- **M1** — ✅ `.harness/config.yaml` declarative control plane.
- **M2** — ✅ ratchet runtime persistence wired through Harbor.
- **PR 3** — ✅ `harness check` unified pipeline.
- **PR 5** — ✅ diff-aware reviewer (evidence-based review).
- **M5** — ✅ executable invariants in context packs via
  `business-rule-checker`.
- **M6** — ✅ `promote-lesson` automation with staged proposals.
- **Phase F** — ⏳ autonomous cleanup agents (the only remaining
  roadmap phase).

See [`../roadmap/agent-harness-roadmap.md`](../roadmap/agent-harness-roadmap.md)
for the remaining phases and design notes.

## Related documents

- [`docs/roadmap/agent-harness-roadmap.md`](../roadmap/agent-harness-roadmap.md)
  — the forward plan.
- [`docs/core-beliefs.md`](../core-beliefs.md) — design principles.
- [`.harness/README.md`](../../.harness/README.md) — per-repo state
  convention.
- [`benchmarks/tb2/task_results_over_time.md`](../../benchmarks/tb2/task_results_over_time.md)
  — longitudinal TB2 benchmark results.
