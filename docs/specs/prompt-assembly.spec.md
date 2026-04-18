# Prompt Assembly Specification

> Source of truth: `libs/deepagents/deepagents/prompt/builder.py` and `prompt/sections.py`

## SystemPromptBuilder API

```python
class SystemPromptBuilder:
    def __init__(self, *, model_name: str = "", strategy: CacheStrategy | None = None) -> None
    @property
    def strategy(self) -> CacheStrategy
    def add_static_section(self, content: str) -> None
    def add_dynamic_section(self, content: str) -> None
    def build(self, *, cwd=None, os_info=None, branch=None, git_status=None) -> list[dict[str, Any]]
    def build_text(self, *, cwd=None, os_info=None, branch=None, git_status=None) -> str
```

Internal method: `_collect_sections()` gathers all sections in order:
1. Static: `identity_section()`, `safety_section()`, `tool_rules_section()`, `style_section()`
2. Extra static sections (added via `add_static_section`)
3. Dynamic: `environment_section()` (if cwd and os_info), `git_section()` (if branch and git_status)
4. Extra dynamic sections (added via `add_dynamic_section`)

## PromptSection Dataclass

```python
@dataclass(frozen=True)
class PromptSection:
    content: str
    cacheable: bool
```

Immutable. `cacheable=True` means the section is static across sessions.

## Section Factories (in `sections.py`)

| Factory Function | Cacheable | Arguments |
|---|---|---|
| `identity_section()` | Yes | none |
| `safety_section()` | Yes | none |
| `tool_rules_section()` | Yes | none |
| `style_section()` | Yes | none |
| `environment_section(cwd, os_info)` | No | cwd: str, os_info: str |
| `git_section(branch, status)` | No | branch: str, status: str |

## CacheStrategy Protocol

```python
@runtime_checkable
class CacheStrategy(Protocol):
    def annotate(self, sections: list[PromptSection]) -> list[dict[str, Any]]: ...
```

### Implementations

| Class | Provider | Behavior |
|---|---|---|
| `AnthropicCacheStrategy` | Anthropic | Adds `cache_control: {"type": "ephemeral"}` on last cacheable section |
| `OpenAICacheStrategy` | OpenAI | No-op (auto-caches prompts > 1024 tokens) |
| `DefaultCacheStrategy` | Fallback | No-op (no caching mechanism) |

### `detect_strategy(model_name)` Factory

Matches model name prefixes:
- `anthropic/`, `claude` -> `AnthropicCacheStrategy`
- `openai/`, `gpt-`, `o1-`, `o3-`, `o4-` -> `OpenAICacheStrategy`
- Everything else -> `DefaultCacheStrategy`

## Mode-Dependent Behavior

### When `PACK_ENABLED` (CLI mode)
- `SystemPromptBuilder` is used
- User `system_prompt` is folded in via `add_static_section()`
- Result via `build_text()` (plain string, no cache annotations in graph.py path)

### When not `PACK_ENABLED` (SDK / Harbor mode)
- Direct string concatenation with `_HarnessProfile.base_system_prompt`
- Falls back to `BASE_AGENT_PROMPT` constant
- Appends `system_prompt_suffix` from profile if present
- Handles `SystemMessage` content blocks for multi-block prompts
