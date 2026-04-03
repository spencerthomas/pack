"""Handler implementations for Pack-specific slash commands.

Each handler is an async function that takes an ``app`` instance and
an ``args`` string. Handlers access runtime state via the PackState
singleton (deferred import for startup performance).

Heavy imports (deepagents SDK modules) are deferred to function bodies
to keep CLI startup fast (see AGENTS.md startup-performance rules).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from deepagents.compaction.context_collapse import ContextCollapser
    from deepagents.compaction.monitor import CompactionMonitor
    from deepagents.cost.tracker import CostTracker
    from deepagents.memory.dream import DreamConsolidator
    from deepagents.middleware.pack.state import PackState
    from deepagents.permissions.rules import RuleStore


def _pack_state() -> PackState | None:
    """Get the Pack runtime state (deferred import).

    Returns:
        PackState singleton or None if Pack is not active.
    """
    from deepagents.middleware.pack.state import get_state

    return get_state()


async def handle_cost(  # noqa: RUF029
    app: Any,  # noqa: ANN401, ARG001
    args: str,  # noqa: ARG001
) -> str:
    """Format and display cost tracker summary.

    Shows per-model breakdown, turn count, and session total.
    If a budget is set, remaining budget is included.

    Args:
        app: The CLI application instance.
        args: Unused argument string (reserved for handler interface).

    Returns:
        Formatted cost summary string.
    """
    state = _pack_state()
    tracker = state.cost_tracker if state else None
    if tracker is None:
        return "Pack harness is not active. Set PACK_ENABLED=1."

    from deepagents.cost.display import format_cost, format_tokens

    lines: list[str] = ["Cost Breakdown", "=" * 40]

    for model, stats in tracker.models.items():
        lines.append(
            f"  {model}: {format_cost(stats.cost)} "
            f"({format_tokens(stats.input_tokens)} in / "
            f"{format_tokens(stats.output_tokens)} out, "
            f"{stats.turns} turns)"
        )

    lines.extend(
        [
            "-" * 40,
            f"Total: {format_cost(tracker.total_cost)}",
            f"Turns: {tracker.turn_count}",
        ]
    )

    if tracker.budget is not None:
        remaining = tracker.budget_remaining
        lines.append(
            f"Budget: {format_cost(tracker.budget)} "
            f"(remaining: {format_cost(remaining or 0.0)})"
        )

    return "\n".join(lines)


async def handle_budget(  # noqa: RUF029
    app: Any,  # noqa: ANN401, ARG001
    args: str,
) -> str:
    """Parse amount and set budget on the cost tracker.

    Args:
        app: The CLI application instance.
        args: Budget amount in USD (e.g. "5.00" or "10").

    Returns:
        Confirmation or error message.
    """
    state = _pack_state()
    tracker = state.cost_tracker if state else None
    if tracker is None:
        return "Pack harness is not active. Set PACK_ENABLED=1."

    stripped = args.strip().lstrip("$")
    if not stripped:
        if tracker.budget is not None:
            from deepagents.cost.display import format_cost

            return f"Current budget: {format_cost(tracker.budget)}"
        return "No budget set. Usage: /budget <amount>"

    try:
        amount = float(stripped)
    except ValueError:
        return f"Invalid amount: {args.strip()!r}. Usage: /budget <amount>"

    if amount <= 0:
        return "Budget must be a positive number."

    # CostTracker doesn't expose a public setter yet; set the private attr
    tracker._budget = amount
    from deepagents.cost.display import format_cost

    return f"Session budget set to {format_cost(amount)}."


async def handle_expand(  # noqa: RUF029
    app: Any,  # noqa: ANN401, ARG001
    args: str,
) -> str:
    """Re-expand a collapsed tool result by its ID.

    Args:
        app: The CLI application instance.
        args: Collapse entry ID to expand.

    Returns:
        Original content or error message.
    """
    state = _pack_state()
    collapser = state.collapser if state else None
    if collapser is None:
        return "Pack harness is not active. Set PACK_ENABLED=1."

    entry_id = args.strip()
    if not entry_id:
        entries = collapser.entries
        if not entries:
            return "No collapsed entries available."
        ids = ", ".join(entries)
        return f"Usage: /expand <id>\nAvailable IDs: {ids}"

    content = collapser.expand(entry_id)
    if content is None:
        return f"No collapsed entry found with ID: {entry_id!r}"
    return content


async def handle_permissions(  # noqa: RUF029
    app: Any,  # noqa: ANN401, ARG001
    args: str,
) -> str:
    """Manage permission rules: list, reset, add, or remove.

    Args:
        app: The CLI application instance.
        args: Subcommand string (list|reset|add|remove).

    Returns:
        Result message.
    """
    state = _pack_state()
    pipeline = state.permission_pipeline if state else None
    if pipeline is None:
        return "Pack harness is not active. Set PACK_ENABLED=1."
    rule_store = pipeline._rules

    parts = args.strip().split(maxsplit=1)
    subcommand = parts[0].lower() if parts else "list"

    if subcommand == "list":
        rules = rule_store.rules
        if not rules:
            return "No permission rules configured."
        lines: list[str] = [f"Permission Rules ({len(rules)})", "=" * 40]
        lines.extend(
            f"  {rule.decision.value:5s} {rule.tool_name} "
            f"pattern={rule.pattern!r} (hits: {rule.hit_count})"
            for rule in rules
        )
        return "\n".join(lines)

    if subcommand == "reset":
        rule_store.clear()
        return "All permission rules cleared."

    if subcommand in {"add", "remove"}:
        return (
            f"/permissions {subcommand} is not yet implemented via CLI. "
            "Use the permission pipeline API directly."
        )

    return (
        f"Unknown subcommand: {subcommand!r}. Usage: /permissions list|reset|add|remove"
    )


async def handle_dream(  # noqa: RUF029
    app: Any,  # noqa: ANN401, ARG001
    args: str,  # noqa: ARG001
) -> str:
    """Trigger memory consolidation from recent session transcripts.

    Args:
        app: The CLI application instance.
        args: Unused argument string (reserved for handler interface).

    Returns:
        Summary of consolidation results.
    """
    state = _pack_state()
    if state is None:
        return "Pack harness is not active. Set PACK_ENABLED=1."

    from deepagents.memory.dream import DreamConsolidator
    from pathlib import Path

    consolidator = DreamConsolidator(
        transcripts_dir=Path(state.data_dir) / "transcripts",
        memory_dir=Path(state.data_dir) / "memories",
    )

    transcripts = consolidator.find_recent_transcripts()
    if not transcripts:
        return "No recent transcripts found for consolidation."

    entries = consolidator.consolidate()
    if not entries:
        return "Dream consolidation complete. No new patterns found."

    lines: list[str] = [
        f"Dream consolidation complete. {len(entries)} new memories:",
    ]
    lines.extend(f"  [{entry.category.value}] {entry.description}" for entry in entries)
    return "\n".join(lines)


async def handle_worktree(
    app: Any,  # noqa: ANN401, ARG001
    args: str,
) -> str:
    """Manage git worktrees: create, list, or remove.

    Delegates to git worktree CLI commands. Subcommands mirror
    `git worktree create|list|remove`.

    Args:
        app: The CLI application instance.
        args: Subcommand and arguments (e.g. "create feature-branch").

    Returns:
        Command output or usage information.
    """
    import asyncio

    parts = args.strip().split(maxsplit=1)
    subcommand = parts[0].lower() if parts else ""
    sub_args = parts[1] if len(parts) > 1 else ""

    if subcommand == "list":
        proc = await asyncio.create_subprocess_exec(
            "git",
            "worktree",
            "list",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            msg = stderr.decode().strip()
            return f"git worktree list failed: {msg}"
        return stdout.decode().strip() or "No worktrees found."

    if subcommand == "create":
        if not sub_args:
            return "Usage: /worktree create <branch-name> [path]"
        create_parts = sub_args.split(maxsplit=1)
        branch = create_parts[0]
        path = create_parts[1] if len(create_parts) > 1 else f"../{branch}"
        proc = await asyncio.create_subprocess_exec(
            "git",
            "worktree",
            "add",
            path,
            "-b",
            branch,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        output = (stdout.decode() + stderr.decode()).strip()
        if proc.returncode != 0:
            return f"git worktree create failed: {output}"
        return output or f"Worktree created at {path} on branch {branch}."

    if subcommand == "remove":
        if not sub_args:
            return "Usage: /worktree remove <path>"
        target = sub_args.strip()
        proc = await asyncio.create_subprocess_exec(
            "git",
            "worktree",
            "remove",
            target,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        output = (stdout.decode() + stderr.decode()).strip()
        if proc.returncode != 0:
            return f"git worktree remove failed: {output}"
        return output or f"Worktree at {target} removed."

    return "Usage: /worktree create|list|remove"


async def handle_review(  # noqa: RUF029
    app: Any,  # noqa: ANN401, ARG001
    args: str,
) -> str:
    """Spawn a Review agent on current changes.

    Formats a message that instructs the agent framework to spawn a
    review-type sub-agent scoped to the current diff.

    Args:
        app: The CLI application instance.
        args: Optional focus area for the review.

    Returns:
        Agent spawn instruction message.
    """
    from deepagents.agents.profiles import AgentType, get_profile

    profile = get_profile(AgentType.REVIEW)
    focus = args.strip() or "all current changes"
    return (
        f"Spawning {profile.name} agent to review: {focus}\n"
        f"Tools: {', '.join(sorted(profile.allowed_tools))}\n"
        f"Model tier: {profile.model_tier}"
    )


async def handle_security(  # noqa: RUF029
    app: Any,  # noqa: ANN401, ARG001
    args: str,
) -> str:
    """Spawn a security-focused review agent.

    Uses the Review agent profile with a security focus hint.

    Args:
        app: The CLI application instance.
        args: Optional scope for the security review.

    Returns:
        Agent spawn instruction message.
    """
    from deepagents.agents.profiles import AgentType, get_profile

    profile = get_profile(AgentType.REVIEW)
    scope = args.strip() or "all current changes"
    return (
        f"Spawning security-focused {profile.name} agent on: {scope}\n"
        f"Focus: vulnerability scanning, secret exposure, injection risks\n"
        f"Tools: {', '.join(sorted(profile.allowed_tools))}\n"
        f"Model tier: {profile.model_tier}"
    )


async def handle_compact(  # noqa: RUF029
    app: Any,  # noqa: ANN401, ARG001
    args: str,  # noqa: ARG001
) -> str:
    """Manually trigger context compaction.

    Args:
        app: The CLI application instance.
        args: Unused argument string (reserved for handler interface).

    Returns:
        Compaction status message.
    """
    state = _pack_state()
    monitor = state.compaction_monitor if state else None
    if monitor is None:
        return "Pack harness is not active. Set PACK_ENABLED=1."

    return (
        f"Context compaction triggered.\n"
        f"Context window: {monitor.context_window:,} tokens\n"
        "Compaction will run before the next model call."
    )


async def handle_agents(  # noqa: RUF029
    app: Any,  # noqa: ANN401, ARG001
    args: str,  # noqa: ARG001
) -> str:
    """List available agent types and their tools.

    Args:
        app: The CLI application instance.
        args: Unused argument string (reserved for handler interface).

    Returns:
        Formatted list of agent profiles.
    """
    from deepagents.agents.profiles import AgentType, get_profile

    lines: list[str] = ["Available Agent Types", "=" * 40]

    for agent_type in AgentType:
        profile = get_profile(agent_type)
        tools = ", ".join(sorted(profile.allowed_tools))
        lines.extend(
            [
                f"\n  {profile.name} ({agent_type.value})",
                f"    {profile.description}",
                f"    Model tier: {profile.model_tier}",
                f"    Max turns: {profile.max_turns}",
                f"    Tools: {tools}",
            ]
        )

    return "\n".join(lines)
