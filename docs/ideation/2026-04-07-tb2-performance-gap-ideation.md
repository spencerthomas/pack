---
date: 2026-04-07
topic: tb2-performance-gap
focus: Closing the gap from ~50% to 80%+ on Terminal-Bench 2.0
---

# Ideation: Closing the TB2 Performance Gap (v2 — Grounded in Code Comparison)

## Codebase Context

Pack is a LangGraph-based Python agent with rich middleware (compaction, memory, subagents, skills, permissions, hooks). **However, the Harbor wrapper that runs TB2 disables most of these features.** ForgeCode runs its full system against TB2; we're running a stripped-down version.

### What Pack ALREADY HAS (built, some not wired to Harbor):
- ✅ SubAgentMiddleware + AsyncSubAgentMiddleware (task tool, isolated context)
- ✅ 3-tier CompactionMiddleware (TRIM/COLLAPSE/SUMMARIZE) — needs PACK_ENABLED
- ✅ PackMemoryMiddleware (structured cross-session memory) — disabled in Harbor
- ✅ SkillsMiddleware — disabled in Harbor
- ✅ AgentProfiles with allowed_tools whitelists — not enforced
- ✅ ParallelToolExecutor — built but not wired
- ✅ TodoListMiddleware, DoomLoopDetection, ErrorReflection, EditVerification
- ✅ HookEngine — needs PACK_ENABLED

### What the Harbor wrapper currently does WRONG:
```python
create_cli_agent(
    enable_memory=False,     # ← disables Pack memory
    enable_skills=False,     # ← disables Pack skills
    # PACK_ENABLED not set   # ← disables compaction, cost, hooks
    # no subagents configured
    system_prompt=custom,    # ← overrides Pack's native prompt
)
```

## Ranked Ideas

### Tier 1: Stop Disabling Pack (Configuration — not code)

#### 1. Activate PACK_ENABLED and Stop Overriding Features
**Description:** Set `PACK_ENABLED=1` in the Harbor wrapper, set `enable_memory=True`, `enable_skills=True`. Prepend a minimal Harbor preamble to Pack's native prompt instead of replacing it. This single change activates compaction, memory, hooks, and skills.
**Rationale:** We're benchmarking a lobotomized Pack. ForgeCode runs its full system. This is the #1 reason for the score gap.
**Downsides:** Some features may conflict with sandbox mode. Needs testing.
**Confidence:** 90%
**Complexity:** Low (env var + 3 flag changes)
**Status:** Unexplored

#### 2. Configure Subagents in Harbor Mode
**Description:** Wire SubAgentMiddleware with a planner (read-only tools) and researcher (read-only tools) alongside the main executor agent. The infrastructure exists — it just has zero agents registered in Harbor mode.
**Rationale:** ForgeCode's FORGE/SAGE split is its #1 architectural advantage. Pack HAS this (SubAgentMiddleware + task tool) but configures zero subagents for TB2.
**Downsides:** Subagent coordination adds latency. Needs agent definitions.
**Confidence:** 75%
**Complexity:** Medium (write agent specs, configure in wrapper)
**Status:** Unexplored

### Tier 2: Genuine Architectural Gaps (Code needed)

#### 3. Hard Request Budget with Graceful Wind-Down
**Description:** Add `RequestBudgetMiddleware` that counts model calls and injects "You have used X/100 requests. Prioritize completing the current objective." at 50%, 75%, 90% thresholds. ForgeCode caps at 100 requests per turn; Pack has `recursion_limit: 9999`.
**Rationale:** Runaway agents waste tokens and hit hard limits with no clean exit. Budget awareness lets the agent prioritize and wrap up gracefully. This is one of ForgeCode's key structural advantages.
**Downsides:** May cut off agents that are genuinely making progress on hard tasks.
**Confidence:** 70%
**Complexity:** Medium (new middleware, ~50 lines)
**Status:** Unexplored

#### 4. Enforce Tool Whitelists at SubAgent Dispatch
**Description:** `AgentProfile` already defines `allowed_tools` and `is_tool_allowed()`, but `SubAgentMiddleware` never calls it. Wire this so explore-type subagents can't write files. ForgeCode's SAGE literally cannot write.
**Rationale:** Tool scoping prevents subagents from taking actions outside their role. Narrow tools = lower compound error rate (Droid research finding).
**Downsides:** May need profile definitions per agent type.
**Confidence:** 65%
**Complexity:** Medium (wire existing code)
**Status:** Unexplored

#### 5. Parallel Subagent Execution
**Description:** `ParallelToolExecutor` exists in `execution/parallel.py` with `asyncio.gather`-based scheduling but is never wired into the task tool's execution path. Activate it so multiple subagent calls run concurrently.
**Rationale:** ForgeCode runs task calls via `join_all`. Pack processes them sequentially. On multi-step tasks, this could cut wall time significantly.
**Downsides:** Concurrent subagents may conflict on shared files.
**Confidence:** 55%
**Complexity:** Medium (wire existing code)
**Status:** Unexplored

#### 6. Read-Before-Write Enforcement at Backend Level
**Description:** Track which files have been read in the current session. Block edit/patch operations on files that haven't been read first. ForgeCode enforces this in `tool_executor.rs:45-55`. Pack's docs say it's enforced but the backend doesn't actually track it.
**Rationale:** Prevents blind edits — a common failure mode where the agent patches a file without understanding its current state.
**Downsides:** May block legitimate overwrites of new files.
**Confidence:** 60%
**Complexity:** Low (add a set tracking read files, check on edit)
**Status:** Unexplored

#### 7. Tool Error Budget with Attempt Metadata
**Description:** Track tool failures per turn. After each failure, inject `{"attempts_remaining": N}` into the error message. After `max_tool_failure_per_turn` (default 3), force the agent to change strategy. ForgeCode does this in `orch.rs:285-300`.
**Rationale:** Gives the agent awareness of its error budget. Currently all tool errors look the same to the agent — it doesn't know if it's on its last chance.
**Downsides:** May terminate legitimate retry loops too early.
**Confidence:** 55%
**Complexity:** Low (extend ErrorReflectionMiddleware)
**Status:** Unexplored

### Tier 3: Model and Prompt

#### 8. Model Upgrade (GLM-5 → Sonnet 4)
**Description:** ForgeCode uses claude-sonnet-4 and gets 81.8%. We use glm-5. The model floor matters more than harness quality.
**Rationale:** Every harness improvement amplifies a capable model. A weak model fails despite good scaffolding. Cost: ~$450 for full run.
**Downsides:** 10x cost. Locks to Anthropic.
**Confidence:** 85%
**Complexity:** Low (config change)
**Status:** Unexplored

#### 9. Task Classification + Strategy Routing for Main Agent
**Description:** `detect_agent_type()` and `AgentProfile` exist but only route subagents. Extend to classify the main task and load a category-specific prompt (perf tuning vs artifact reconstruction vs sysadmin).
**Rationale:** ForgeCode's specialized agents serve this purpose. Pack has the machinery but doesn't use it for the primary agent.
**Downsides:** Classification errors apply wrong strategy.
**Confidence:** 60%
**Complexity:** Medium
**Status:** Unexplored

#### 10. "Unlimited Context" Reassurance in Prompt
**Description:** ForgeCode tells the agent: "The conversation has unlimited context through automatic summarization, so do not stop until the objective is fully achieved." Add this to Pack's prompt.
**Rationale:** Agents self-limit when they think context is running out. This one-line addition removes that anxiety.
**Downsides:** None — pure upside.
**Confidence:** 80%
**Complexity:** Trivial (one line)
**Status:** Unexplored

## Rejection Summary

| # | Idea | Reason Rejected |
|---|------|-----------------|
| 1 | Build scratchpad/memory | Already exists (PackMemoryMiddleware) — just not enabled |
| 2 | Build subagent system | Already exists (SubAgentMiddleware) — just not configured |
| 3 | Build compaction | Already exists (3-tier CompactionMiddleware) — needs PACK_ENABLED |
| 4 | Build todo tools | Already exists (TodoListMiddleware) — already wired |
| 5 | Build strategy routing | Already exists (agent_dispatch.py) — not used for main agent |
| 6 | Semantic search | Can't add to Harbor sandbox without custom implementation |
| 7 | Undo tool | Nice-to-have but doesn't address root failure modes |
| 8 | Kill summarizer | Too risky — context overflow is real |
| 9 | Zero-shot no prompt | Untested, high variance |
| 10 | Verify-first harness-enforced | Too complex for the expected gain |

## Key Insight

**The previous ideation round proposed building 3 things that already exist.** The real problem is that the Harbor wrapper disables Pack's features. The highest-ROI work is:

1. **Stop disabling Pack** (Tier 1 — configuration)
2. **Close the genuine architectural gaps** (Tier 2 — request budget, tool whitelists, parallel execution)
3. **Upgrade the model** (Tier 3 — if budget allows)

## Session Log
- 2026-04-07: Initial ideation — 37 candidates, 7 survivors (some redundant with existing capabilities)
- 2026-04-07: v2 — Deep code comparison Pack vs ForgeCode. Re-ideated with grounded gaps. 10 survivors, corrected for existing capabilities. 5 previous ideas rejected as already-built.
