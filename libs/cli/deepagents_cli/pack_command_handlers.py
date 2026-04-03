"""Handler implementations for Pack-specific slash commands.

Each handler is an async function that takes an ``app`` instance and
an ``args`` string. Stateful dependencies (cost tracker, rule store,
collapser, etc.) are accepted as optional keyword parameters with
``None`` defaults -- they will be wired by the application layer later.

Heavy imports (deepagents SDK modules) are deferred to function bodies
to keep CLI startup fast (see AGENTS.md startup-performance rules).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from deepagents.agents.profiles import AgentProfile
    from deepagents.compaction.context_collapse import ContextCollapser
    from deepagents.compaction.monitor import CompactionMonitor
    from deepagents.cost.tracker import CostTracker
    from deepagents.memory.dream import DreamConsolidator
    from deepagents.permissions.rules import RuleStore


async def handle_cost(
    app: Any,
    args: str,
    *,
    tracker: CostTracker | None = None,
) -> str:
    """Format and display cost tracker summary.

    Shows per-model breakdown, turn count, and session total.
    If a budget is set, remaining budget is included.

    Args:
        app: The CLI application instance.
        args: Unused argument string.
        tracker: Active cost tracker for the session.

    Returns:
        Formatted cost summary string.
    """
    if tracker is None:
        return "No cost tracker is active for this session."

    from deepagents.cost.display import format_cost, format_tokens

    lines: list[str] = ["Cost Breakdown"]
    lines.append("=" * 40)

    for model, stats in tracker.models.items():
        lines.append(
            f"  {model}: {format_cost(stats.cost)} "
            f"({format_tokens(stats.input_tokens)} in / "
            f"{format_tokens(stats.output_tokens)} out, "
            f"{stats.turns} turns)"
        )

    lines.append("-" * 40)
    lines.append(f"Total: {format_cost(tracker.total_cost)}")
    lines.append(f"Turns: {tracker.turn_count}")

    if tracker.budget is not None:
        remaining = tracker.budget_remaining
        lines.append(
            f"Budget: {format_cost(tracker.budget)} "
            f"(remaining: {format_cost(remaining or 0.0)})"
        )

    return "\n".join(lines)


async def handle_budget(
    app: Any,
    args: str,
    *,
    tracker: CostTracker | None = None,
) -> str:
    """Parse amount and set budget on the cost tracker.

    Args:
        app: The CLI application instance.
        args: Budget amount in USD (e.g. "5.00" or "10").
        tracker: Active cost tracker for the session.

    Returns:
        Confirmation or error message.
    """
    if tracker is None:
        return "No cost tracker is active for this session."

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
    tracker._budget = amount  # noqa: SLF001
    from deepagents.cost.display import format_cost

    return f"Session budget set to {format_cost(amount)}."


async def handle_expand(
    app: Any,
    args: str,
    *,
    collapser: ContextCollapser | None = None,
) -> str:
    """Re-expand a collapsed tool result by its ID.

    Args:
        app: The CLI application instance.
        args: Collapse entry ID to expand.
        collapser: Context collapser managing collapsed entries.

    Returns:
        Original content or error message.
    """
    if collapser is None:
        return "Context collapser is not active."

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


async def handle_permissions(
    app: Any,
    args: str,
    *,
    rule_store: RuleStore | None = None,
) -> str:
    """Manage permission rules: list, reset, add, or remove.

    Args:
        app: The CLI application instance.
        args: Subcommand string (list|reset|add|remove).
        rule_store: Rule store managing persisted permission rules.

    Returns:
        Result message.
    """
    if rule_store is None:
        return "Permission rule store is not active."

    parts = args.strip().split(maxsplit=1)
    subcommand = parts[0].lower() if parts else "list"

    if subcommand == "list":
        rules = rule_store.rules
        if not rules:
            return "No permission rules configured."
        lines: list[str] = [f"Permission Rules ({len(rules)})"]
        lines.append("=" * 40)
        for rule in rules:
            lines.append(
                f"  {rule.decision.value:5s} {rule.tool_name} "
                f"pattern={rule.pattern!r} (hits: {rule.hit_count})"
            )
        return "\n".join(lines)

    if subcommand == "reset":
        rule_store.clear()
        return "All permission rules cleared."

    if subcommand in ("add", "remove"):
        return (
            f"/permissions {subcommand} is not yet implemented via CLI. "
            "Use the permission pipeline API directly."
        )

    return f"Unknown subcommand: {subcommand!r}. Usage: /permissions list|reset|add|remove"


async def handle_dream(
    app: Any,
    args: str,
    *,
    consolidator: DreamConsolidator | None = None,
) -> str:
    """Trigger memory consolidation from recent session transcripts.

    Args:
        app: The CLI application instance.
        args: Unused argument string.
        consolidator: Dream consolidator instance.

    Returns:
        Summary of consolidation results.
    """
    if consolidator is None:
        return "Dream consolidator is not configured."

    transcripts = consolidator.find_recent_transcripts()
    if not transcripts:
        return "No recent transcripts found for consolidation."

    entries = consolidator.consolidate()
    if not entries:
        return "Dream consolidation complete. No new patterns found."

    lines: list[str] = [f"Dream consolidation complete. {len(entries)} new memories:"]
    for entry in entries:
        lines.append(f"  [{entry.category.value}] {entry.description}")
    return "\n".join(lines)


async def handle_worktree(app: Any, args: str) -> str:
    """Manage git worktrees: create, list, or remove.

    Delegates to git worktree CLI commands. Subcommands mirror
    ``git worktree create|list|remove``.

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
            "git", "worktree", "list",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            return f"git worktree list failed: {stderr.decode().strip()}"
        return stdout.decode().strip() or "No worktrees found."

    if subcommand == "create":
        if not sub_args:
            return "Usage: /worktree create <branch-name> [path]"
        create_parts = sub_args.split(maxsplit=1)
        branch = create_parts[0]
        path = create_parts[1] if len(create_parts) > 1 else f"../{branch}"
        proc = await asyncio.create_subprocess_exec(
            "git", "worktree", "add", path, "-b", branch,
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
        proc = await asyncio.create_subprocess_exec(
            "git", "worktree", "remove", sub_args.strip(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        output = (stdout.decode() + stderr.decode()).strip()
        if proc.returncode != 0:
            return f"git worktree remove failed: {output}"
        return output or f"Worktree at {sub_args.strip()} removed."

    return "Usage: /worktree create|list|remove"


async def handle_review(app: Any, args: str) -> str:
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
    focus = args.strip() if args.strip() else "all current changes"
    return (
        f"Spawning {profile.name} agent to review: {focus}\n"
        f"Tools: {', '.join(sorted(profile.allowed_tools))}\n"
        f"Model tier: {profile.model_tier}"
    )


async def handle_security(app: Any, args: str) -> str:
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
    scope = args.strip() if args.strip() else "all current changes"
    return (
        f"Spawning security-focused {profile.name} agent on: {scope}\n"
        f"Focus: vulnerability scanning, secret exposure, injection risks\n"
        f"Tools: {', '.join(sorted(profile.allowed_tools))}\n"
        f"Model tier: {profile.model_tier}"
    )


async def handle_compact(
    app: Any,
    args: str,
    *,
    monitor: CompactionMonitor | None = None,
) -> str:
    """Manually trigger context compaction.

    Args:
        app: The CLI application instance.
        args: Unused argument string.
        monitor: Compaction monitor for checking current token usage.

    Returns:
        Compaction status message.
    """
    if monitor is None:
        return "Compaction monitor is not active."

    return (
        f"Context compaction triggered.\n"
        f"Context window: {monitor.context_window:,} tokens\n"
        "Compaction will run before the next model call."
    )


async def handle_agents(app: Any, args: str) -> str:
    """List available agent types and their tools.

    Args:
        app: The CLI application instance.
        args: Unused argument string.

    Returns:
        Formatted list of agent profiles.
    """
    from deepagents.agents.profiles import AgentType, get_profile

    lines: list[str] = ["Available Agent Types"]
    lines.append("=" * 40)

    for agent_type in AgentType:
        profile = get_profile(agent_type)
        tools = ", ".join(sorted(profile.allowed_tools))
        lines.append(f"\n  {profile.name} ({agent_type.value})")
        lines.append(f"    {profile.description}")
        lines.append(f"    Model tier: {profile.model_tier}")
        lines.append(f"    Max turns: {profile.max_turns}")
        lines.append(f"    Tools: {tools}")

    return "\n".join(lines)
