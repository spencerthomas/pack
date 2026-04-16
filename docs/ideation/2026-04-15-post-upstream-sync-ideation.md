---
date: 2026-04-15
topic: post-upstream-sync
focus: What's next for Pack after the 169-commit upstream sync + feature-branch ports
---

# Ideation: Post Upstream-Sync Next Moves

## Codebase Context

Pack is a Python monorepo (uv/ruff/pytest) — fork of langchain-ai/deepagents at spencerthomas/pack, benchmarking on Terminal-Bench 2.0. Three main libraries: `deepagents` (SDK), `cli` (Textual terminal UI), `evals` (Harbor benchmark runner). Plus `acp`, `repl`, `partners/`.

**Just shipped via `chore/upstream-sync`:** 169-commit upstream merge plus manual ports from Vivek's feature branches. Doom-loop middleware, stateless file-edit loop detection, error reflection, HARBOR_PREAMBLE with 3-phase workflow + pivot rules + discipline rules, langsmith-trace-analyzer skill, summarization token cap (100k), `truncate_execute_output` helper, Rich markup escape fixes, filesystem permissions middleware, MCP tool deterministic sort, fail-fast on missing credentials, O(1) MessageStore, user-scoped memory, harness profiles refactor. 1471 deepagents tests pass, 0 merge regressions (all 37 CLI failures are pre-existing).

**Known gaps identified in the code audit + past learnings:**

- OpenRouter transient disconnect retry is **middleware-level only** — never wraps `ainvoke()` in `DeepAgentsWrapper.run()`. 36% of Harbor failures are "Server disconnected without sending a response" crashes with no trajectory captured.
- `PACK_ENABLED` still not set in Harbor mode — compaction, memory, skills, hooks silently no-op during TB2 benchmark runs.
- `SubAgentMiddleware` is wired but **zero subagents are registered** in Harbor mode. The task tool has no targets.
- `AgentProfile.allowed_tools` / `is_tool_allowed()` exist but `SubAgentMiddleware` never calls them — tool whitelists unenforced.
- `ParallelToolExecutor` exists in `execution/parallel.py` but is never invoked from the task-tool path.
- Verify-retry loop works in ad-hoc CLI mode but is not wired into Harbor.
- Two coexisting permission systems post-merge: `deepagents/permissions/` (rules pipeline) and `deepagents/middleware/permissions.py` (upstream middleware). Ambiguous precedence.
- Pre-existing drift: 26 `test_pack_commands.py` failures (kwarg-injection vs `_pack_state()` singleton), 3 help-body drift tests (Pack's own commands never added to `/help` body string).
- No regression benchmark — no way to catch "this PR dropped TB2 by 8%".
- Every TB2 job produces a trajectory directory; no automated analysis pipeline exists.

**Past learnings consulted:**
- `docs/ideation/2026-04-07-tb2-performance-gap-ideation.md` — prior ideation; Tier 1 items have mostly shipped.
- `docs/plans/forgecode-analysis.md` — ForgeCode (81.8% TB2 leader) differentiators.
- `docs/plans/tb2-harbor-run-feedback.md` — LangSmith cluster analysis and OpenRouter disconnect data point.
- `docs/plans/2026-04-06-001-feat-iterative-task-performance-plan.md` (completed) — doom loop, error reflection, retry, slim prompt plan.

## Ranked Ideas

### 1. Wrap `ainvoke()` in Harbor with transient-error retry

**Description:** Wrap the outer `ainvoke()` call in `DeepAgentsWrapper.run()` (`libs/evals/deepagents_harbor/deepagents_wrapper.py`) with exponential-backoff retry (2s/4s/8s, 3 attempts) on connection errors, stream disconnects, and 5xx. Only retry transient errors where no partial work was committed — avoid replaying partially-executed tool calls. Preserve the checkpointer state so retry resumes from the last checkpoint, not from scratch. Also capture stream chunks accumulated before the disconnect and flush to LangSmith/disk on exception so post-mortem analysis has something to chew on.

**Rationale:** 36% of Harbor failures are OpenRouter "Server disconnected without sending a response" crashes — the single largest identified gap between ad-hoc (100%) and Harbor (32%) scores. Middleware-layer retry never fires because the disconnect aborts the LangGraph stream before middleware runs. Wrapping `ainvoke()` is the only place to catch it.

**Downsides:** Must be careful not to double-execute tools on retry. Needs either transaction-aware state or idempotency markers. Risk of masking real provider problems with aggressive retries.

**Confidence:** 90%
**Complexity:** Medium (~100 lines + tests)
**Status:** Unexplored

---

### 2. Activate Pack's full stack in Harbor mode

**Description:** Three coupled changes to `libs/evals/deepagents_harbor/deepagents_wrapper.py`:
1. Set `PACK_ENABLED=1` by default in Harbor — activates compaction, memory, skills, hooks middleware that exists but is disabled.
2. Register a default Harbor subagent roster: `researcher` (read-only: `ls`, `read_file`, `grep`, `glob`), `verifier` (read + `execute` but no writes), `editor` (full write access). Main agent delegates via the `task` tool.
3. Call `AgentProfile.is_tool_allowed()` inside `SubAgentMiddleware` before exposing tools to each subagent — enforces the whitelists that already exist on the profile objects.

**Rationale:** "Benchmarking a lobotomized Pack" thesis from prior ideation — Pack has built compaction, memory, hooks, skills, subagents, profiles, tool whitelists, but the Harbor wrapper disables all of them. ForgeCode runs its full system at 81.8%; we run a stripped Pack at 32%. Specialization + delegation (ForgeCode's FORGE→SAGE→MUSE pattern) is the #1 architectural advantage.

**Downsides:** Subagent coordination adds latency and may conflict with single-agent assumptions in Harbor's scoring. Some middleware (e.g. HITL prompts) may misbehave in non-interactive mode. Requires careful per-agent tool curation. Higher risk than idea #1.

**Confidence:** 75%
**Complexity:** Medium-high (config + 3 subagent specs + middleware wiring + regression testing)
**Status:** Unexplored

---

### 3. TB2 regression benchmark gate in CI

**Description:** Two-tier benchmark in CI. (a) **PR gate**: 5-10 representative TB2 tasks (one per failure cluster from LangSmith) run on every PR targeting `main`, posting a pass-rate delta vs baseline as a GitHub check. Hard block if regression > 2%. (b) **Nightly full**: 32-task suite against `main`, committed as structured JSON to a `docs/bench-history/` branch. Graph pass-rate over time. Uses Docker Harbor to match production.

**Rationale:** Without a regression gate, every prompt/middleware edit is a coin flip. We've seen silent regressions happen (Vivek's prompting branch rebuilt prompts from scratch; our HARBOR_PREAMBLE edit could regress things we haven't measured). A smoke subset per PR becomes a ratchet: every improvement that passes raises the floor. The history branch also gives the team a durable timeline to debug "when did we regress on X-type tasks."

**Downsides:** Costs real LLM inference per PR (mitigated by selecting fast, cheap tasks for the smoke subset — <$2/run). CI time adds ~5-10 min per PR. Docker-in-Docker setup has operational edges. Flakes will degrade trust if the gate is too tight.

**Confidence:** 80%
**Complexity:** Medium-high (CI workflow + task selection + baseline + delta logic + Docker Harbor in CI)
**Status:** Unexplored

---

### 4. Trajectory auto-analyzer + failure playbook

**Description:** Post-run pipeline attached to every Harbor job. After `libs/evals/jobs/<run>/` completes, an analyzer script parses trajectories and extracts failure signatures: tool-loop patterns (beyond what the middleware already catches), context blowouts, prompt misfires, provider-specific error classes, "agent gave up early" signals, identical-tool-call sequences. Output: structured `failures.jsonl` per run plus aggregated `docs/benchmarks/failure-clusters.md` updated on nightly runs. Each new cluster opens a GitHub issue with sample trajectories attached. Extract the existing manual process in `skills/langsmith-trace-analyzer/` into an always-on script.

**Rationale:** Today every TB2 run is analyzed manually by a human reading trajectories. Automated extraction means the 100th run is strictly more valuable than the 1st — clusters sharpen, patterns stabilize, blind spots surface. This is the compounding asset: every future contributor inherits the pattern library. Pairs with idea #3 — the regression gate surfaces "did we regress?" while this surfaces "what did we regress on, and why." Directly extends the trace-analyzer skill we just ported.

**Downsides:** Requires schema design for failure signatures (gets it wrong, retooling later is painful). The playbook only compounds if someone actually maintains the issue/cluster loop. Can generate noise if clustering thresholds are too loose.

**Confidence:** 70%
**Complexity:** High (parser + classifier + clusterer + GitHub integration + doc pipeline)
**Status:** Unexplored

---

### 5. Collapse the two permission systems

**Description:** Pack currently runs both `deepagents/permissions/` (Pack's rules pipeline with classifier + circuit breaker + rule store) and `deepagents/middleware/permissions.py` (upstream's `_PermissionMiddleware` with `FilesystemPermission` rules) simultaneously after the merge. Pick the upstream middleware as canonical (less surface area, better-maintained, already wired by upstream graph assembly). Rewrite Pack's rules pipeline as a thin adapter that emits `FilesystemPermission` instances. Delete the duplicated `classifier.py`, `circuit_breaker.py`, `pipeline.py` from `deepagents/permissions/` (keep `rules.py` as the adapter). Delete `middleware/pack/permission_middleware.py` if it exists.

**Rationale:** Two permission systems means permission-related bugs during TB2 runs are untraceable — a denied tool could be either system's fault. Removing code (not adding) improves the signal. Upstream's system is the long-term bet; Pack's pipeline pre-dated it. The merge resolution split Pack's pipeline tests into `test_permissions_pipeline.py` specifically because both exist — this makes the separation permanent, which is the wrong direction.

**Downsides:** Pack's classifier/circuit-breaker may have features the upstream middleware doesn't. Have to audit what Pack's pipeline does that upstream doesn't before deleting. Risk of losing a safety rail the team relies on.

**Confidence:** 65%
**Complexity:** Medium (audit + adapter + delete)
**Status:** Unexplored

---

### 6. Auto-generate `/help` body from the `COMMANDS` tuple

**Description:** Replace the hand-written `/help` body string in `libs/cli/deepagents_cli/app.py:~2629` with a one-liner that iterates the `COMMANDS` tuple from `libs/cli/deepagents_cli/command_registry.py`, grouping by category and rendering name + description. Delete the 3 `test_help_body_lists_all_commands` failures by construction (the test becomes "iterate COMMANDS, compare to rendered body" — always green). Also regenerates automatically when Pack adds commands.

**Rationale:** Pure deletion. Fixes 3 pre-existing test failures by removing the drift source rather than chasing it. Every future slash-command addition is free. Low-risk, low-complexity, high-quality-of-life.

**Downsides:** Minor: the hand-written help body might have ordering, emphasis, or grouping that a naive auto-generator loses. Mitigate by including category/order metadata in `COMMANDS` itself.

**Confidence:** 90%
**Complexity:** Low (~30 lines)
**Status:** Unexplored

## Rejection Summary

| # | Idea | Reason Rejected |
|---|------|-----------------|
| R1 | Route task tool through `ParallelToolExecutor` | Depends on #2 shipping first; concurrent file edits risky |
| R2 | Lazy-import SDK via PEP 562 `__getattr__` | Developer QoL, doesn't move TB2 — dev-only churn |
| R3 | Bisect test-pollution source in CI | Nice-to-have; 8 affected tests all pass in isolation |
| R4 | Delete `HARBOR_PREAMBLE` prompt layer | Just invested in it — regression risk outweighs simplification |
| R5 | Remove custom OpenRouter provider | Has unique OpenAI-compat base URL path; deleting loses flexibility |
| R6 | Auto-wire pack_commands handlers to singleton | Fixing 26 tests not TB2-moving; separate cleanup task |
| R7 | Merge three loop detectors | They target different failure modes (tight same-tool vs slow file-edit); merging loses coverage |
| R8 | Fold EditVerification + ReadBeforeWrite + SyntaxCheck | Collapse reduces per-middleware observability in traces |
| R9 | Delete ShellAllowList + ToolCallLeakDetection | They do specific checks permission rules express awkwardly |
| R10 | Infer Anthropic caching from model id | Minor simplification, low impact |
| R11 | Collapse Memory + PackMemory middlewares | Risky — different semantics; needs audit before merging |
| R12 | Flatten 3-tier compaction | Structure encodes size-dependent strategy; flattening hurts clarity |
| R13 | Delete sync SubAgentMiddleware | Async-as-superset claim unverified — risky |
| R14 | Automated upstream sync bot | Valuable but we just synced; cost exceeds near-term ROI |
| R15 | Declarative profile → middleware DSL | Too vague / big architectural shift — better as ce:brainstorm |
| R16 | Prompt A/B harness | Depends on #3 shipping first |
| R17 | Trajectory replay / deterministic mock mode | Depends on #4 shipping first |
| R18 | Provider adapter conformance suite | Large investment, diffuse payoff relative to #3+#4 |
| R19 | Leaderboard dashboard | Cosmetic without #3 and A/B harness in place first |
| R20 | Middleware composition DSL + introspection | Same bucket as R15 |
| R21 | Contributor skill pack for Pack-dev | No immediate pain; low urgency |

## Session Log
- 2026-04-15: Initial ideation — 33 candidates generated across 3 frames (pain/friction, removal/inversion, leverage/compounding); 6 survived; proceeding directly to `ce:plan` on idea #1 (OpenRouter wrapper retry) per user's loop instruction.
