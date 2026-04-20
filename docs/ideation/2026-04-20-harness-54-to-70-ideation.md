---
date: 2026-04-20
topic: harness-54-to-70-ideation
focus: Pack TB2 pass rate 54% → 70%+
mode: repo-grounded
---

# Ideation: Push Pack TB2 from 54% to 70%+

## Grounding Context

**Current state:** Pack at 54% on TB2 (44/81 agent-attempted). 22 wrong_solution + 8 AgentTimeout + 6 credit-related failures. Hard task rate 36% vs medium 63%.

**Deployed this session:** CWD fix, OpenRouter SDK timeout patch, max_tokens=16K cap, LangSmith tracing enrichment (task_name/difficulty/category). Reverted: external auto-verification (was net negative).

**Middleware already in Pack (wired via create_cli_agent):**
- `LocalContextMiddleware` — bash-based env discovery, 718 LOC ✓
- `LoopDetectionMiddleware` — per-file edit thrashing (warn at 8, hard at 12) ✓
- `DoomLoopDetectionMiddleware` — repeated identical tool calls, ForgeCode pattern ✓
- `TokenStateMiddleware` — schema-only state registration

**Key external grounding:**
- LangChain harness engineering: 52.8% → 66.5% (+13.7pp) via 5 middleware combined. Pack has 3 of them.
- ForgeCode: 81.8% via Muse/Forge/Sage multi-agent + mandatory reviewer.
- Meta-Harness: 76.4% via automated trace-to-fix loop.
- Reasoning sandwich: always-max scored 12.6pp BELOW balanced allocation.
- One-shot tasks pass at 61-99%, iterative at 0-22%.

## The real gap

If Pack at 54% already has 3-of-5 LangChain middleware, the `+13.7pp` likely concentrated in the two we lack: **PreCompletionChecklist** and **reasoning sandwich**. Budget observability is similarly missing (`TokenStateMiddleware` is schema-only).

## Ranked Ideas

### 1. PreCompletionChecklistMiddleware
**Description:** Middleware that intercepts the model before it can return an AIMessage with no tool_calls (the "done" signal). On first "done" attempt, inject a HumanMessage with a verification checklist ("Have you run the tests? Walked requirements item-by-item? Verified output format?") and force another cycle. Allow real completion after N satisfactory cycles.
**Rationale:** This is the single pattern LangChain credits as anchoring their +13.7pp gain. Directly targets 22 wrong_solution failures. Runs inline (no external verification phase — which is why our earlier auto-verification failed). Low LOC (~150).
**Downsides:** Invasive middleware hook. If agent refuses checklist items, gate doesn't help. Needs careful integration with LangGraph's model-response handling.
**Confidence:** 85%
**Complexity:** Medium
**Status:** Selected for Phase 1

### 2. BudgetObservableMiddleware
**Description:** Append `[budget: 7m 32s remaining, 45K input tokens used]` to every tool result. At 2 minutes remaining, escalate to `[CRITICAL: emit best-so-far now]`. No hard cutoff — just exposes budget to agent reasoning.
**Rationale:** Current `TokenStateMiddleware` is schema-only (CLI status bar, not agent-visible). 8 AgentTimeouts currently produce zero usable output because agent has no budget awareness. Converting timeouts into "submit best-known" = recoverable signal.
**Downsides:** Prompt discipline to make agent actually respond to budget. Minor per-call overhead.
**Confidence:** 75%
**Complexity:** Low
**Status:** Selected for Phase 1

### 3. Live feedback loop hooked into per-task log flow
**Description:** Instead of batched A/B comparison, watch runs live: as each task's trajectory flows, analyze it in real-time, detect failure signatures, reflect on what's happening, feed insights back to the operator. Per-task feedback during runs, not post-mortem analysis.
**Rationale:** Tighter feedback loop than batch A/B. Makes iteration continuous. User preference: "hook directly into the logs as runs flow per task and reflect on the performance."
**Downsides:** More operator discipline to act on live signals. Streaming analysis is more complex than batch.
**Confidence:** 70%
**Complexity:** Medium
**Status:** Selected for Phase 2

### 4. Writer + Critic adversarial pair
**Description:** Separate Writer (GLM-5.1) from Critic (cheaper model). Critic reviews diff + runs tests before submission. Cross-context review breaks miscalibrated self-assessment.
**Rationale:** ForgeCode 81.8% uses role separation. Addresses wrong_solution failures where agent confidently submits wrong code.
**Confidence:** 70%
**Complexity:** Medium-High
**Status:** Deferred — revisit after Phase 1 + feedback loop data

### 5. Model routing by task phase
**Description:** Route planning to Opus/Sonnet-4, implementation to GLM-5.1, search to Haiku. Mirror production pattern (20-80% cost reduction, no accuracy loss).
**Rationale:** Reasoning sandwich evidence — planner quality is the biggest lever.
**Confidence:** 65%
**Complexity:** Medium
**Status:** Deferred

### 6. Auto-subtask fanout on hard tasks
**Description:** Pre-flight classifier detects hard tasks, forks 3 parallel sub-agents with shorter budgets, winner wins.
**Rationale:** Directly targets 36% hard rate via variance reduction.
**Confidence:** 60%
**Complexity:** High
**Status:** Deferred

### 7. Trace-to-fix automated failure mining
**Description:** Nightly job clusters failed LangSmith traces, auto-files failure cards with fix hypotheses.
**Rationale:** Compounds over time; now enabled by this session's tracing enrichment.
**Confidence:** 60%
**Complexity:** Medium-High
**Status:** Deferred — feedback loop (idea #3) is the tighter version

## Rejection Summary

44 ideas rejected — full list at `/var/folders/.../T/compound-engineering/ce-ideate/a4e377cd/raw-candidates.md`. Main rejection patterns:

- Already-shipped-and-wired (LoopDetection, LocalContext, DoomLoopDetection, environmental bootstrapping)
- Too speculative (no system prompt, patch-space MCTS, negative-space scaffolding)
- Duplicates of stronger ideas (multiple multi-agent variants, multiple budget-management variants, multiple test-gate variants)
- Premature (depends on infrastructure not yet built)
- Unclear ROI vs complexity (abstain action, reproducible benchmarks with pass@k)

## Next Actions

**Phase 1 (this session):**
1. Build PreCompletionChecklistMiddleware
2. Build BudgetObservableMiddleware
3. Wire both into create_cli_agent for Harbor mode

**Phase 2 (after Phase 1):**
4. Live feedback loop that hooks into per-task log flow and reflects in real-time

## Session Log
- 2026-04-20: Initial ideation — 51 raw ideas across 6 frames, 7 survivors, 2 selected for Phase 1 build. Discovery: Pack already has 3/5 LangChain middleware; real gap is PreCompletionChecklist + budget observability.
