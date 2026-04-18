# Graph Assembly Specification

> Source of truth: `libs/deepagents/deepagents/graph.py`

## Public API

### `create_deep_agent()`

Primary entry point for constructing a fully configured Deep Agent.

```python
def create_deep_agent(
    model: str | BaseChatModel | None = None,
    tools: Sequence[BaseTool | Callable | dict[str, Any]] | None = None,
    *,
    system_prompt: str | SystemMessage | None = None,
    middleware: Sequence[AgentMiddleware] = (),
    subagents: Sequence[SubAgent | CompiledSubAgent | AsyncSubAgent] | None = None,
    skills: list[str] | None = None,
    memory: list[str] | None = None,
    permissions: list[FilesystemPermission] | None = None,
    response_format: ResponseFormat[ResponseT] | type[ResponseT] | dict[str, Any] | None = None,
    context_schema: type[ContextT] | None = None,
    checkpointer: Checkpointer | None = None,
    store: BaseStore | None = None,
    backend: BackendProtocol | BackendFactory | None = None,
    interrupt_on: dict[str, bool | InterruptOnConfig] | None = None,
    debug: bool = False,
    name: str | None = None,
    cache: BaseCache | None = None,
) -> CompiledStateGraph
```

### `get_default_model()`

Returns the default chat model (OpenRouter if key present, else Anthropic claude-sonnet-4-6).

### `_add_pack_middleware()`

Internal function that appends Pack harness middleware to the stack.
Only activates when `PACK_ENABLED` env var is set.

```python
def _add_pack_middleware(
    stack: list[AgentMiddleware[Any, Any, Any]],
    *,
    auto_approve: bool = False,
) -> list[BaseTool]
```

Returns extra tools (git worktree tools, document reader tools) to register.

## Middleware Composition Order (Main Agent)

The main agent middleware stack is built in this order:

1. `TodoListMiddleware`
2. `SkillsMiddleware` (conditional: only if `skills` is provided)
3. `FilesystemMiddleware`
4. `SubAgentMiddleware`
5. `SummarizationMiddleware` (via `create_summarization_middleware`)
6. `PatchToolCallsMiddleware`
7. `AsyncSubAgentMiddleware` (conditional: only if async subagents exist)
8. **User middleware** (from `middleware` parameter)
9. **Pack middleware** (via `_add_pack_middleware`, conditional on `PACK_ENABLED`):
   - 9a. `HooksMiddleware` (inserted at position 0 if hooks.json exists)
   - 9b. `CostMiddleware`
   - 9c. `PermissionMiddleware` (Pack)
   - 9d. `CompactionMiddleware`
   - 9e. `PackMemoryMiddleware`
10. Profile `extra_middleware` (provider-specific)
11. `_ToolExclusionMiddleware` (conditional: if profile has `excluded_tools`)
12. `AnthropicPromptCachingMiddleware` (unconditional; no-ops for non-Anthropic)
13. `MemoryMiddleware` (conditional: if `memory` is provided)
14. `HumanInTheLoopMiddleware` (conditional: if `interrupt_on` is provided)
15. `_PermissionMiddleware` (SDK; conditional: if `permissions` rules are present; always last)

## Feature Gating

Pack middleware activates only when `PACK_ENABLED` env var is truthy.
This prevents Pack from affecting upstream SDK tests or consumers.
The CLI sets `PACK_ENABLED=1` automatically.

## Prompt Assembly

When `PACK_ENABLED`:
- Uses `SystemPromptBuilder` from `deepagents.prompt.builder`
- User `system_prompt` is folded in via `add_static_section()`
- Output via `build_text()`

When not `PACK_ENABLED`:
- Uses `_HarnessProfile.base_system_prompt` (or `BASE_AGENT_PROMPT` fallback)
- Appends `system_prompt_suffix` if present
- Concatenates with user `system_prompt`

## Known Complexity Notes

`create_deep_agent` carries `noqa: C901, PLR0912, PLR0915` suppressions
(high cyclomatic complexity, too many branches, too many statements).
This is intentional -- the function is the single assembly point for
all agent configuration and middleware ordering.

Other noqa suppressions in the module:
- `noqa: E501` on `BASE_AGENT_PROMPT` (long string literal)
- `noqa: BLE001` on exception handlers in `get_default_model` and `_add_pack_middleware`
  (broad exceptions are acceptable for fallback/non-critical paths)

## Helper Functions

- `_resolve_extra_middleware(profile)` -- materializes profile's extra_middleware
- `_harness_profile_for_model(model, spec)` -- looks up HarnessProfile for a model
- `_tool_name(tool)` -- extracts name from any tool type
- `_apply_tool_description_overrides(tools, overrides)` -- copies tools with description rewrites
