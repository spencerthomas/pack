"""Individual section builders for the system prompt.

Each function returns a `PromptSection` with content and a flag indicating
whether the section is safe to cache (static across sessions).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PromptSection:
    """A single section of the assembled system prompt.

    Attributes:
        content: The text content for this section.
        cacheable: Whether this section is static and safe to cache
            across sessions. Dynamic sections (git status, environment)
            change per invocation and must not be cached.
    """

    content: str
    cacheable: bool


def identity_section() -> PromptSection:
    """Build the agent identity section.

    Returns:
        A cacheable section describing who the agent is.
    """
    content = (
        "You are a Deep Agent, an AI assistant that helps users "
        "accomplish tasks using tools. You respond with text and tool "
        "calls. The user can see your responses and tool outputs in "
        "real time."
    )
    return PromptSection(content=content, cacheable=True)


def safety_section() -> PromptSection:
    """Build the operational safety guidelines section.

    Returns:
        A cacheable section with safety and objectivity rules.
    """
    content = """\
## Core Behavior

- Be concise and direct. Don't over-explain unless asked.
- NEVER add unnecessary preamble ("Sure!", "Great question!", "I'll now...").
- Don't say "I'll now do X" -- just do it.
- If the request is ambiguous, ask questions before acting.
- If asked how to approach something, explain first, then act.

## Professional Objectivity

- Prioritize accuracy over validating the user's beliefs.
- Disagree respectfully when the user is incorrect.
- Avoid unnecessary superlatives, praise, or emotional validation."""
    return PromptSection(content=content, cacheable=True)


def tool_rules_section() -> PromptSection:
    """Build the tool usage rules section.

    Instructs the agent to prefer dedicated tools over shell commands.

    Returns:
        A cacheable section with tool preference rules.
    """
    content = """\
## Tool Usage

IMPORTANT: Use specialized tools instead of shell commands:

- `read_file` over `cat`/`head`/`tail`
- `edit_file` over `sed`/`awk`
- `write_file` over `echo`/heredoc
- `grep` tool over shell `grep`/`rg`
- `glob` over shell `find`/`ls`

When performing multiple independent operations, make all tool calls \
in a single response -- don't make sequential calls when parallel is possible."""
    return PromptSection(content=content, cacheable=True)


def style_section() -> PromptSection:
    """Build the output formatting and style constraints section.

    Returns:
        A cacheable section with task execution and style rules.
    """
    content = """\
## Doing Tasks

When the user asks you to do something:

1. **Understand first** -- read relevant files, check existing patterns.
2. **Act** -- implement the solution. Work quickly but accurately.
3. **Verify** -- check your work against what was asked, not against your \
own output. Your first attempt is rarely correct -- iterate.

Keep working until the task is fully complete. Don't stop partway and \
explain what you would do -- just do it. Only yield back to the user \
when the task is done or you're genuinely blocked.

## Progress Updates

For longer tasks, provide brief progress updates at reasonable intervals \
-- a concise sentence recapping what you've done and what's next."""
    return PromptSection(content=content, cacheable=True)


def environment_section(cwd: str, os_info: str) -> PromptSection:
    """Build the dynamic environment context section.

    Args:
        cwd: Current working directory path.
        os_info: Operating system description (e.g., `Linux 6.1 x86_64`).

    Returns:
        A non-cacheable section with runtime environment details.
    """
    content = f"""\
## Environment

- Working directory: {cwd}
- Operating system: {os_info}"""
    return PromptSection(content=content, cacheable=False)


def git_section(branch: str, status: str) -> PromptSection:
    """Build the dynamic git context section.

    Args:
        branch: Current git branch name.
        status: Summary of `git status` output (e.g., `clean`,
            `3 modified, 1 untracked`).

    Returns:
        A non-cacheable section with current git state.
    """
    content = f"""\
## Git Context

- Branch: {branch}
- Status: {status}"""
    return PromptSection(content=content, cacheable=False)


def context_pack_section(pack_name: str, summary: str, rules: str) -> PromptSection:
    """Build a cacheable section from a loaded context pack's content.

    Packs are stable per-repo guidance; the summary and rules rarely
    change between runs, so they belong in the static (cached) portion
    of the prompt. Wraps the content with a short header naming the
    pack so the agent can see which rule set is in force.

    Args:
        pack_name: The pack's identifier (directory name).
        summary: Content of ``README.md``. May be empty.
        rules: Content of ``rules.md``. May be empty.

    Returns:
        A cacheable section. If both summary and rules are empty the
        section content is empty too; callers filter those out before
        rendering.
    """
    pieces: list[str] = [f"## Context pack: {pack_name}"]
    if summary.strip():
        pieces.append(summary.strip())
    if rules.strip():
        pieces.append("### Rules\n\n" + rules.strip())
    # If we only have the header and no content, collapse to empty.
    if len(pieces) == 1:
        return PromptSection(content="", cacheable=True)
    return PromptSection(content="\n\n".join(pieces), cacheable=True)


def task_hints_section(hints: dict[str, str]) -> PromptSection:
    """Build a dynamic section of task-specific guidance.

    Callers pass the output of a task classifier (phase, domain, etc.)
    and this renders targeted hints the agent can use to shape its
    approach without bloating the prompt for unrelated tasks.

    Args:
        hints: Flat dict of hint keys to values. Unknown keys render
            under a generic "Task hints" heading so new classifier
            categories don't require edits here.

    Returns:
        A non-cacheable section. Empty hints produce an empty-content
        section callers can filter out.
    """
    if not hints:
        return PromptSection(content="", cacheable=False)

    lines = ["## Task hints"]
    for key, value in hints.items():
        if value:
            lines.append(f"- **{key}:** {value}")
    content = "\n".join(lines)
    return PromptSection(content=content, cacheable=False)
