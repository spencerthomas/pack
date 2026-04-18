"""Pack middleware — connects harness engineering modules into the agent pipeline.

These middleware classes wrap the standalone Pack modules (compaction,
permissions, cost, hooks, memory) and integrate them with LangGraph's
middleware system via `awrap_model_call()` and `awrap_tool_call()`.
"""

from deepagents.middleware.pack.architecture_middleware import ArchitectureEnforcementMiddleware
from deepagents.middleware.pack.agent_dispatch import (
    build_subagent_spec,
    create_teammate_config,
    resolve_agent_profile,
)
from deepagents.middleware.pack.compaction_middleware import CompactionMiddleware
from deepagents.middleware.pack.cost_middleware import CostMiddleware
from deepagents.middleware.pack.hooks_middleware import HooksMiddleware
from deepagents.middleware.pack.memory_middleware import PackMemoryMiddleware
from deepagents.middleware.pack.permission_middleware import PermissionMiddleware
from deepagents.middleware.pack.state import PackState, clear_state, get_state, set_state

__all__ = [
    "ArchitectureEnforcementMiddleware",
    "CompactionMiddleware",
    "CostMiddleware",
    "HooksMiddleware",
    "PackMemoryMiddleware",
    "PackState",
    "PermissionMiddleware",
    "build_subagent_spec",
    "clear_state",
    "create_teammate_config",
    "get_state",
    "resolve_agent_profile",
    "set_state",
]
