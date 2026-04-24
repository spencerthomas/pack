"""System prompt builder with provider-aware cache boundaries.

Assembles the system prompt from modular sections and applies
cache control annotations appropriate for the target LLM provider.
"""

from __future__ import annotations

from typing import Any

from deepagents.prompt.cache_strategy import (
    CacheStrategy,
    DefaultCacheStrategy,
    detect_strategy,
)
from deepagents.prompt.context_pack import ContextPack
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


class SystemPromptBuilder:
    """Assembles system prompt from modular section builders.

    Splits sections into static (cacheable) and dynamic (per-session)
    groups. Static sections include identity, safety guidelines, tool
    usage rules, and style constraints. Dynamic sections include
    environment info, git status, AGENTS.md content, and MCP tool
    definitions.

    The builder applies a provider-aware cache strategy that annotates
    the boundary between static and dynamic sections for providers
    that support explicit prompt caching (e.g., Anthropic).

    Args:
        model_name: Model identifier used to auto-detect cache strategy.
            Ignored when `strategy` is provided explicitly.
        strategy: Override the auto-detected cache strategy.
    """

    def __init__(  # noqa: D107
        self,
        *,
        model_name: str = "",
        strategy: CacheStrategy | None = None,
    ) -> None:
        self._strategy = strategy or (
            detect_strategy(model_name) if model_name else DefaultCacheStrategy()
        )
        self._extra_static: list[PromptSection] = []
        self._extra_dynamic: list[PromptSection] = []

    @property
    def strategy(self) -> CacheStrategy:
        """The active cache strategy."""
        return self._strategy

    def add_static_section(self, content: str) -> None:
        """Append a custom cacheable section.

        Use for content that stays constant across sessions, such as
        AGENTS.md guidelines or custom instructions.

        Args:
            content: The section text to add.
        """
        self._extra_static.append(PromptSection(content=content, cacheable=True))

    def add_dynamic_section(self, content: str) -> None:
        """Append a custom non-cacheable section.

        Use for content that changes each session, such as MCP tool
        definitions or runtime context.

        Args:
            content: The section text to add.
        """
        self._extra_dynamic.append(
            PromptSection(content=content, cacheable=False),
        )

    def add_context_pack(self, pack: ContextPack) -> None:
        """Append a loaded context pack as a cacheable static section.

        Packs provide task-scoped guidance (domain rules, phase-specific
        anti-patterns, golden examples) that rarely changes between
        runs. Rendered with a header naming the pack so the agent can
        tell which guidance is in force.

        Empty packs (no summary and no rules) are silently skipped so
        stub pack directories don't inject empty sections.

        Args:
            pack: A ``ContextPack`` loaded via ``resolve_pack`` or
                ``load_pack``.
        """
        section = context_pack_section(pack.name, pack.summary, pack.rules)
        if not section.content:
            return
        self._extra_static.append(section)

    def build(
        self,
        *,
        cwd: str | None = None,
        os_info: str | None = None,
        branch: str | None = None,
        git_status: str | None = None,
        task_hints: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        """Assemble the full system prompt as annotated content blocks.

        Static sections come first, followed by dynamic sections.
        The cache strategy annotates the boundary between them.

        Args:
            cwd: Current working directory for the environment section.
            os_info: OS description for the environment section.
            branch: Git branch name for the git section.
            git_status: Git status summary for the git section.
            task_hints: Optional classifier-derived hints rendered as a
                dynamic section (phase, domain, complexity, etc.).

        Returns:
            A list of content block dicts suitable for use as the
            system message content in a chat API call.
        """
        sections = self._collect_sections(
            cwd=cwd,
            os_info=os_info,
            branch=branch,
            git_status=git_status,
            task_hints=task_hints,
        )
        return self._strategy.annotate(sections)

    def build_text(
        self,
        *,
        cwd: str | None = None,
        os_info: str | None = None,
        branch: str | None = None,
        git_status: str | None = None,
        task_hints: dict[str, str] | None = None,
    ) -> str:
        """Assemble the full system prompt as a plain text string.

        Convenience method for contexts that don't need cache annotations
        (e.g., logging, testing, providers without cache support).

        Args:
            cwd: Current working directory for the environment section.
            os_info: OS description for the environment section.
            branch: Git branch name for the git section.
            git_status: Git status summary for the git section.
            task_hints: Optional classifier-derived hints rendered as a
                dynamic section.

        Returns:
            The assembled prompt as a single string with sections
            separated by double newlines.
        """
        sections = self._collect_sections(
            cwd=cwd,
            os_info=os_info,
            branch=branch,
            git_status=git_status,
            task_hints=task_hints,
        )
        return "\n\n".join(s.content for s in sections if s.content)

    def _collect_sections(
        self,
        *,
        cwd: str | None,
        os_info: str | None,
        branch: str | None,
        git_status: str | None,
        task_hints: dict[str, str] | None = None,
    ) -> list[PromptSection]:
        """Gather all sections in order: static first, then dynamic.

        Args:
            cwd: Current working directory.
            os_info: OS description.
            branch: Git branch name.
            git_status: Git status summary.
            task_hints: Optional classifier-derived hints.

        Returns:
            Ordered list of all prompt sections.
        """
        sections: list[PromptSection] = [
            identity_section(),
            safety_section(),
            tool_rules_section(),
            style_section(),
            *self._extra_static,
        ]

        if cwd and os_info:
            sections.append(environment_section(cwd, os_info))
        if branch and git_status:
            sections.append(git_section(branch, git_status))

        if task_hints:
            hint_section = task_hints_section(task_hints)
            if hint_section.content:
                sections.append(hint_section)

        sections.extend(self._extra_dynamic)
        return sections
