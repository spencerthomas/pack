"""Pack-specific slash commands.

Defines additional slash commands for Pack features (cost tracking,
permissions, memory, compaction, agents). These are registered separately
from the upstream COMMANDS in command_registry.py so upstream changes
can be merged cleanly.
"""

from __future__ import annotations

from deepagents_cli.command_registry import BypassTier, SlashCommand

PACK_COMMANDS: tuple[SlashCommand, ...] = (
    SlashCommand(
        name="/cost",
        description="Show detailed cost breakdown (per-model, per-turn, session total)",
        bypass_tier=BypassTier.SIDE_EFFECT_FREE,
        hidden_keywords="spending price usage dollars",
    ),
    SlashCommand(
        name="/budget",
        description="Set session spending limit. Usage: /budget <amount>",
        bypass_tier=BypassTier.QUEUED,
        hidden_keywords="limit spending cap",
    ),
    SlashCommand(
        name="/expand",
        description="Re-expand a collapsed tool result. Usage: /expand <id>",
        bypass_tier=BypassTier.SIDE_EFFECT_FREE,
        hidden_keywords="uncollapse restore",
    ),
    SlashCommand(
        name="/permissions",
        description=(
            "Manage permission rules. Usage: /permissions list|reset|add|remove"
        ),
        bypass_tier=BypassTier.QUEUED,
        hidden_keywords="rules allow deny",
    ),
    SlashCommand(
        name="/dream",
        description="Trigger memory consolidation (autoDream)",
        bypass_tier=BypassTier.QUEUED,
        hidden_keywords="consolidate memory sleep",
    ),
    SlashCommand(
        name="/worktree",
        description="Manage git worktrees. Usage: /worktree create|list|remove",
        bypass_tier=BypassTier.QUEUED,
        hidden_keywords="git branch isolate",
    ),
    SlashCommand(
        name="/review",
        description="Spawn a Review agent on current changes",
        bypass_tier=BypassTier.QUEUED,
        hidden_keywords="code audit quality",
    ),
    SlashCommand(
        name="/security",
        description="Spawn a security-focused review agent",
        bypass_tier=BypassTier.QUEUED,
        hidden_keywords="vulnerability audit scan",
    ),
    SlashCommand(
        name="/pack-compact",
        description="Manually trigger context compaction",
        bypass_tier=BypassTier.QUEUED,
        hidden_keywords="compress shrink context",
    ),
    SlashCommand(
        name="/agents",
        description="List available agent types and their tools",
        bypass_tier=BypassTier.SIDE_EFFECT_FREE,
        hidden_keywords="types profiles subagent",
    ),
)
"""Pack-specific slash commands."""
