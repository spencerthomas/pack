"""System prompt builder with provider-aware cache boundaries.

This module assembles the system prompt from modular sections and
applies cache control annotations appropriate for the target LLM
provider.
"""

from deepagents.prompt.builder import SystemPromptBuilder
from deepagents.prompt.cache_strategy import (
    AnthropicCacheStrategy,
    CacheStrategy,
    DefaultCacheStrategy,
    OpenAICacheStrategy,
    detect_strategy,
)
from deepagents.prompt.sections import (
    PromptSection,
    context_pack_section,
    environment_section,
    git_section,
    identity_section,
    safety_section,
    style_section,
    task_hints_section,
    tool_rules_section,
)
from deepagents.prompt.context_pack import (
    ContextPack,
    list_packs,
    load_pack,
    resolve_pack,
)
from deepagents.prompt.task_classifier import TaskHints, classify

__all__ = [
    "AnthropicCacheStrategy",
    "CacheStrategy",
    "ContextPack",
    "DefaultCacheStrategy",
    "OpenAICacheStrategy",
    "PromptSection",
    "SystemPromptBuilder",
    "TaskHints",
    "classify",
    "context_pack_section",
    "detect_strategy",
    "environment_section",
    "git_section",
    "identity_section",
    "list_packs",
    "load_pack",
    "resolve_pack",
    "safety_section",
    "style_section",
    "task_hints_section",
    "tool_rules_section",
]
