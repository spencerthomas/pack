# Middleware Contract Specification

> Source of truth: `libs/deepagents/deepagents/middleware/pack/`

## AgentMiddleware Protocol

All middleware extends `langchain.agents.middleware.types.AgentMiddleware`.

Override points:
- `awrap_model_call(request, handler)` -- intercept/modify LLM calls
- `awrap_tool_call(request, handler)` -- intercept/modify tool execution

Both are async. Call `await handler(request)` to proceed through the chain.

## Pack Middleware Pattern

The pattern is: **domain module** produces the logic, **middleware/pack/ wrapper**
adapts it to the AgentMiddleware protocol, **graph.py** assembles it into the stack.

Example flow:
```
deepagents/cost/tracker.py          (domain: CostTracker)
deepagents/middleware/pack/cost_middleware.py  (wrapper: CostMiddleware)
deepagents/graph.py _add_pack_middleware()     (assembly)
```

## Middleware Classes

### `CostMiddleware`
- File: `middleware/pack/cost_middleware.py`
- Overrides: `awrap_model_call`
- Role: Records token usage and costs after every model call
- Domain module: `deepagents.cost.tracker.CostTracker`

### `PermissionMiddleware`
- File: `middleware/pack/permission_middleware.py`
- Overrides: `awrap_tool_call`
- Role: Evaluates tool calls through the multi-layer permission pipeline
- Domain module: `deepagents.permissions.pipeline.PermissionPipeline`
- Returns error ToolMessage for denied calls

### `CompactionMiddleware`
- File: `middleware/pack/compaction_middleware.py`
- Overrides: `awrap_model_call`
- Role: Proactive context compaction (trim, collapse, summarize)
- Domain modules: `CompactionMonitor`, `ContextCollapser`, `SegmentProtocol`

### `PackMemoryMiddleware`
- File: `middleware/pack/memory_middleware.py`
- Overrides: `awrap_model_call`
- Role: Injects structured memories before model calls, extracts new ones after
- Domain modules: `MemoryIndex`, `MemoryExtractor`

### `HooksMiddleware`
- File: `middleware/pack/hooks_middleware.py`
- Overrides: `awrap_model_call`, `awrap_tool_call`
- Role: Fires lifecycle hook events (pre/post model and tool calls)
- Domain module: `deepagents.hooks.engine.HookEngine`

## Support Modules

### `state.py` -- PackState Singleton
- File: `middleware/pack/state.py`
- `PackState` dataclass holds references to all Pack runtime instances
- Module-level singleton via `get_state()` / `set_state()` / `clear_state()`
- Fields: `cost_tracker`, `permission_pipeline`, `collapser`, `compaction_monitor`,
  `hook_engine`, `memory_index`, `parallel_executor`, `data_dir`
- Safe for single-threaded CLI use only

### `agent_dispatch.py` -- Agent Dispatch Utilities
- File: `middleware/pack/agent_dispatch.py`
- `resolve_agent_profile(task_description, agent_type=None)` -- resolves AgentProfile
- `build_subagent_spec(task_description, agent_type=None)` -- builds SubAgent spec
- `create_teammate_config(agent_id, task_description, ...)` -- creates TeammateConfig

### `parallel_middleware.py` -- Parallel Execution
- File: `middleware/pack/parallel_middleware.py`
- Re-exports `ParallelToolExecutor` for PackState storage
- No middleware class (parallel execution requires batching beyond single-call pattern)

## `__init__.py` Exports

```python
__all__ = [
    "CompactionMiddleware",
    "CostMiddleware",
    "HooksMiddleware",
    "PackMemoryMiddleware",
    "PermissionMiddleware",
    "PackState",
    "build_subagent_spec",
    "clear_state",
    "create_teammate_config",
    "get_state",
    "resolve_agent_profile",
    "set_state",
]
```

## Naming Conventions

- Pack middleware classes: `{Domain}Middleware` (e.g., `CostMiddleware`)
- SDK middleware classes: `_{Domain}Middleware` (e.g., `_PermissionMiddleware`) -- underscore prefix for internal/SDK use
- Domain modules: named after the concern (e.g., `cost/tracker.py`, `permissions/pipeline.py`)
- Wrapper modules: `middleware/pack/{domain}_middleware.py`
