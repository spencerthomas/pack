---
title: "feat: Wire Pack harness modules into runtime pipeline"
type: feat
status: completed
date: 2026-04-03
origin: docs/plans/2026-04-02-001-feat-deep-agents-harness-upgrade-plan.md
---

# Wire Pack Harness Modules into Runtime Pipeline

## Overview

The Pack harness modules (permissions, cost, compaction, memory, hooks) are built and tested as standalone units, but not fully integrated into the live agent pipeline. This plan connects the remaining wires so all features work end-to-end during real CLI sessions.

## Problem Frame

Testing revealed 5 integration gaps between the standalone modules and the running agent:

1. Permission middleware ignores the CLI's `-y`/auto-approve flag
2. Slash command handlers can't access runtime middleware state (cost tracker, collapser, etc.)
3. Pack's structured memory module (4-category, autoDream) isn't connected to the agent loop
4. Hooks middleware isn't instantiated in `_add_pack_middleware`
5. No state bridge between `graph.py` middleware and CLI's `app.py` for slash command access

## Requirements Trace

- R1. Permission pipeline respects auto-approve flag and blocks dangerous commands in interactive mode
- R2. `/cost`, `/expand`, `/permissions` commands display live data from the running middleware
- R3. Pack memory system loads and saves memories during agent sessions
- R4. Hook definitions from config fire during tool and model calls
- R5. All 1,240 existing tests continue to pass

## Scope Boundaries

- OUT: New harness features — this is purely wiring existing modules
- OUT: TUI changes beyond command handler wiring
- OUT: New tests for features already tested in unit tests

## Key Technical Decisions

- **PackState singleton**: Create a simple module-level container holding references to the cost tracker, permission pipeline, collapser, and hook engine instances created in `_add_pack_middleware`. Slash command handlers import this instead of receiving instances via parameter passing. This avoids threading state through the entire CLI stack.
- **Memory middleware as separate layer**: Don't replace the existing `MemoryMiddleware` (which handles AGENTS.md). Add Pack's structured memory as an additional system prompt section via a new lightweight middleware.
- **Hooks from config.toml**: Load hook definitions from a `[pack.hooks]` section in `config.toml`, not a separate file. Keeps configuration unified.

## Implementation Units

- [ ] **Unit 1: PackState singleton for runtime state sharing**

**Goal:** Create a module-level state container so CLI commands can access middleware instances

**Requirements:** R2

**Dependencies:** None

**Files:**
- Create: `libs/deepagents/deepagents/middleware/pack/state.py`
- Modify: `libs/deepagents/deepagents/middleware/pack/__init__.py`
- Modify: `libs/deepagents/deepagents/graph.py` — `_add_pack_middleware` stores instances in PackState
- Test: `libs/deepagents/tests/unit_tests/test_pack_state.py`

**Approach:**
- `PackState` class with `cost_tracker`, `permission_pipeline`, `collapser`, `hook_engine` attributes
- Module-level `_state: PackState | None` with `get_state() -> PackState` accessor
- `_add_pack_middleware` calls `set_state()` after creating instances
- Thread-safe: use a simple module global (single-threaded CLI process)

**Patterns to follow:**
- Existing `deepagents_cli/config.py` uses module-level settings singleton

**Test scenarios:**
- Happy path: `set_state()` then `get_state()` returns same instances
- Happy path: `get_state()` before `set_state()` returns None
- Edge case: multiple `set_state()` calls overwrite cleanly

**Verification:**
- `get_state()` returns populated `PackState` after agent creation

---

- [ ] **Unit 2: Wire slash commands to PackState**

**Goal:** Connect CLI command handlers to live runtime state

**Requirements:** R2

**Dependencies:** Unit 1

**Files:**
- Modify: `libs/cli/deepagents_cli/app.py` — pass state to handlers
- Modify: `libs/cli/deepagents_cli/pack_command_handlers.py` — use PackState.get_state() instead of optional params

**Approach:**
- Handlers call `from deepagents.middleware.pack.state import get_state` at invocation time (deferred import)
- Each handler gets the relevant instance from state: `get_state().cost_tracker`, etc.
- If state is None (Pack not enabled), handlers return a message like "Pack harness not active"

**Patterns to follow:**
- Existing handlers use deferred imports for startup performance

**Test scenarios:**
- Happy path: `/cost` with active state returns formatted cost data
- Happy path: `/permissions list` with active state returns rules
- Edge case: command called when Pack not enabled returns helpful message

**Verification:**
- `/cost` shows live token counts during an interactive session

---

- [ ] **Unit 3: Permission middleware respects auto-approve**

**Goal:** Pass the CLI's auto-approve flag through to the permission middleware

**Requirements:** R1

**Dependencies:** Unit 1

**Files:**
- Modify: `libs/deepagents/deepagents/graph.py` — `_add_pack_middleware` accepts `auto_approve` param
- Modify: `libs/cli/deepagents_cli/agent.py` — pass auto_approve from CLI config

**Approach:**
- `_add_pack_middleware(stack, *, auto_approve=False)` 
- In `create_cli_agent`, check if `auto_approve` is True and pass it through
- The `PermissionMiddleware` already supports `auto_approve=True` — just need to wire the flag

**Patterns to follow:**
- Existing `auto_approve` flag flow in `create_cli_agent` → `_add_interrupt_on`

**Test scenarios:**
- Happy path: with auto_approve=False, dangerous command is blocked
- Happy path: with auto_approve=True (-y flag), all commands pass through
- Integration: interactive session without -y blocks `rm -rf`, shows denial reason

**Verification:**
- Running without `-y` blocks dangerous commands through the Pack permission pipeline

---

- [ ] **Unit 4: Wire hooks middleware with config loading**

**Goal:** Load hook definitions and instantiate HooksMiddleware in the pipeline

**Requirements:** R4

**Dependencies:** Unit 1

**Files:**
- Modify: `libs/deepagents/deepagents/graph.py` — add HooksMiddleware to `_add_pack_middleware`
- Create: `libs/deepagents/deepagents/hooks/config.py` — load hook definitions from config.toml or hooks.json

**Approach:**
- Check for `~/.pack/hooks.json` (array of hook definition objects)
- If present, parse into `HookDefinition` instances and create `HookEngine`
- Add `HooksMiddleware(engine)` to the stack (first position — wraps everything)
- If no hooks file, skip (no hooks middleware = no overhead)

**Patterns to follow:**
- Existing `libs/cli/deepagents_cli/hooks.py` for config loading pattern

**Test scenarios:**
- Happy path: hooks.json with post_tool_call hook fires after file write
- Edge case: missing hooks.json — no middleware added, no error
- Edge case: malformed hooks.json — warning logged, no middleware added

**Verification:**
- A hook defined in `~/.pack/hooks.json` fires during an agent session

---

- [ ] **Unit 5: Wire structured memory middleware**

**Goal:** Connect Pack's 4-category memory system to the agent loop

**Requirements:** R3

**Dependencies:** Unit 1

**Files:**
- Create: `libs/deepagents/deepagents/middleware/pack/memory_middleware.py`
- Modify: `libs/deepagents/deepagents/graph.py` — add memory middleware to `_add_pack_middleware`
- Test: `libs/deepagents/tests/unit_tests/test_pack_memory_middleware.py`

**Approach:**
- Create `PackMemoryMiddleware` that extends `AgentMiddleware`
- On `awrap_model_call`: load MEMORY.md index and inject relevant memories into system prompt
- Post-response: trigger `MemoryExtractor` to capture new memories (rate-limited)
- Memory directory: `~/.pack/memories/` (from PackState data dir)
- Does NOT replace existing `MemoryMiddleware` (AGENTS.md) — runs alongside it

**Patterns to follow:**
- Existing `MemoryMiddleware` in `libs/deepagents/deepagents/middleware/memory.py`
- Pack's `MemoryIndex` and `MemoryExtractor` classes

**Test scenarios:**
- Happy path: memories from MEMORY.md appear in system prompt
- Happy path: post-response extraction fires after 3 turns
- Edge case: empty memory directory — no injection, no error
- Edge case: extraction rate limit prevents back-to-back extractions

**Verification:**
- Memories saved in one session are available in the next session

## System-Wide Impact

- **Interaction graph**: PackState singleton is created in `_add_pack_middleware` (graph.py) and accessed in CLI handlers (app.py). One-way dependency: CLI reads state, never writes.
- **Error propagation**: All Pack middleware failures are non-blocking — the agent continues without the feature. Handlers return user-friendly messages when state is unavailable.
- **Unchanged invariants**: `create_deep_agent()` API unchanged. Existing `MemoryMiddleware` and `HumanInTheLoopMiddleware` continue to work independently. All 1,240 tests pass.

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| PackState module-level global not thread-safe | CLI is single-threaded; document this limitation |
| Memory middleware adds tokens to every prompt | Rate-limit extraction; keep MEMORY.md index under 200 lines |
| Hooks config loading adds startup latency | Only load if hooks file exists; defer import |
