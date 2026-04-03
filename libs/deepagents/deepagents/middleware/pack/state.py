"""Module-level state container for Pack harness middleware instances.

Provides a singleton so CLI slash command handlers can access the
cost tracker, permission pipeline, collapser, and hook engine that
were created during agent initialization.

Note: This uses a module-level global. The CLI is single-threaded,
so this is safe. Do not use in multi-threaded contexts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from deepagents.compaction.context_collapse import ContextCollapser
    from deepagents.compaction.monitor import CompactionMonitor
    from deepagents.cost.tracker import CostTracker
    from deepagents.hooks.engine import HookEngine
    from deepagents.permissions.pipeline import PermissionPipeline


@dataclass
class PackState:
    """Container for Pack harness runtime instances.

    Args:
        cost_tracker: The session's cost tracker.
        permission_pipeline: The session's permission pipeline.
        collapser: The context collapser for verbose tool results.
        compaction_monitor: The context window monitor.
        hook_engine: The hook event engine.
        data_dir: Base directory for Pack data (rules, memories, etc.).
    """

    cost_tracker: CostTracker | None = None
    permission_pipeline: PermissionPipeline | None = None
    collapser: ContextCollapser | None = None
    compaction_monitor: CompactionMonitor | None = None
    hook_engine: HookEngine | None = None
    data_dir: str = ""


_state: PackState | None = None


def get_state() -> PackState | None:
    """Get the current Pack state, or None if not initialized.

    Returns:
        The PackState singleton, or None if Pack middleware hasn't been set up.
    """
    return _state


def set_state(state: PackState) -> None:
    """Set the Pack state singleton.

    Called by `_add_pack_middleware` during agent initialization.

    Args:
        state: The populated state container.
    """
    global _state  # noqa: PLW0603
    _state = state


def clear_state() -> None:
    """Clear the Pack state. Used in tests."""
    global _state  # noqa: PLW0603
    _state = None
