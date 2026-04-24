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
- **Runtime wiring:** contract proven via unit test
  (`test_scope_enforcement_records_via_ratchet`); Harbor runtime
  persistence is deferred pending a per-trial vs repo-level decision.

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
- **Tests:** `libs/cli/tests/unit_tests/test_reviewer.py` (27 tests),
  `test_reviewer_middleware.py` (21 tests)
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

Nascent. Trajectory capture exists; lesson-promotion automation does
not yet.

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

- **Phase A** — ✅ policy + scope + ratchet substrate live. Ratchet
  runtime persistence pending.
- **Phase B.1 + B.2** — ✅ context pack loader + wiring. First pack
  ships at `.context-packs/coding-task/`.
- **Phase B.3** — ⏳ `harness discover` CLI not yet built.
- **Phase C** — ✅ reviewer sub-agent + policy-gated middleware.
- **Phase D** — ⏳ arch-lint, business-rule checker, ratchet-mode
  enforcement.
- **Phase E** — ⏳ lesson-promotion automation.
- **Phase F** — ⏳ autonomous cleanup agents.

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
