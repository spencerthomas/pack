"""Multi-layer permission pipeline for tool call authorization.

Processes tool calls through 4 layers in order:
1. Rule matching — persisted cross-session rules
2. Risk assessment — static tool risk classification
3. Read-only whitelist — auto-approve known safe tools
4. Classifier — deterministic regex + optional LLM

Denied calls produce tool error results fed back to the model,
so it can adapt its approach.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

from deepagents.permissions.circuit_breaker import CircuitBreaker
from deepagents.permissions.classifier import ClassifierDecision, ClassifierResult, PermissionClassifier
from deepagents.permissions.rules import PermissionRule, RuleDecision, RuleStore


class Decision(str, Enum):
    """Final decision from the permission pipeline."""

    ALLOW = "allow"
    DENY = "deny"
    ASK_USER = "ask_user"
    MANUAL_MODE = "manual_mode"  # Circuit breaker tripped


class RiskLevel(str, Enum):
    """Risk level for a tool category."""

    READ = "read"  # Auto-approve
    WRITE = "write"  # Needs review
    EXECUTE = "execute"  # High risk
    DESTRUCTIVE = "destructive"  # Very high risk


# Tools and their risk levels
_TOOL_RISK: dict[str, RiskLevel] = {
    # Read-only tools — always safe
    "read_file": RiskLevel.READ,
    "ls": RiskLevel.READ,
    "glob": RiskLevel.READ,
    "grep": RiskLevel.READ,
    "web_search": RiskLevel.READ,
    "fetch_url": RiskLevel.READ,
    "write_todos": RiskLevel.READ,
    "compact_conversation": RiskLevel.READ,
    "ask_user": RiskLevel.READ,
    # Write tools — medium risk
    "write_file": RiskLevel.WRITE,
    "edit_file": RiskLevel.WRITE,
    # Execution tools — high risk
    "execute": RiskLevel.EXECUTE,
    "task": RiskLevel.EXECUTE,
    "launch_async_subagent": RiskLevel.EXECUTE,
    "update_async_subagent": RiskLevel.EXECUTE,
    "cancel_async_subagent": RiskLevel.EXECUTE,
}

# Tools that are always auto-approved (Layer 3)
_READ_ONLY_WHITELIST: frozenset[str] = frozenset({
    "read_file", "ls", "glob", "grep", "write_todos", "ask_user",
})


@dataclass
class PipelineResult:
    """Result from the permission pipeline.

    Args:
        decision: Final authorization decision.
        reason: Human-readable explanation.
        layer: Which layer made the decision (1-4, or 0 for circuit breaker).
        classifier_result: Optional classifier output from Layer 4.
    """

    decision: Decision
    reason: str
    layer: int
    classifier_result: ClassifierResult | None = None


class PermissionPipeline:
    """Multi-layer permission pipeline.

    Processes tool calls through 4 layers, with a circuit breaker
    that degrades to manual mode after repeated denials.

    Args:
        rule_store: Persistent rule storage for cross-session learning.
        classifier: Permission classifier for Layer 4.
        circuit_breaker: Circuit breaker for denial tracking.
    """

    def __init__(
        self,
        rule_store: RuleStore,
        classifier: PermissionClassifier | None = None,
        circuit_breaker: CircuitBreaker | None = None,
    ) -> None:
        self._rules = rule_store
        self._classifier = classifier or PermissionClassifier()
        self._breaker = circuit_breaker or CircuitBreaker()

    @property
    def rule_store(self) -> RuleStore:
        """Access the rule store for listing/managing rules."""
        return self._rules

    @property
    def circuit_breaker(self) -> CircuitBreaker:
        """Access the circuit breaker state."""
        return self._breaker

    def evaluate(self, tool_name: str, args: dict[str, Any]) -> PipelineResult:
        """Evaluate a tool call through the permission pipeline.

        Args:
            tool_name: Name of the tool being called.
            args: Tool call arguments.

        Returns:
            Pipeline result with decision, reason, and layer.
        """
        # Circuit breaker check — if tripped, everything goes to manual
        if self._breaker.tripped:
            return PipelineResult(
                decision=Decision.MANUAL_MODE,
                reason="Circuit breaker tripped — all tool calls require manual approval.",
                layer=0,
            )

        # Layer 1: Rule matching
        rule = self._rules.match(tool_name, args)
        if rule is not None:
            if rule.decision == RuleDecision.ALLOW:
                self._breaker.record_approval()
                return PipelineResult(
                    decision=Decision.ALLOW,
                    reason=f"Matched saved rule: {rule.pattern}",
                    layer=1,
                )
            self._breaker.record_denial()
            return PipelineResult(
                decision=Decision.DENY,
                reason=f"Matched saved deny rule: {rule.pattern}",
                layer=1,
            )

        # Layer 2: Risk assessment
        risk = _TOOL_RISK.get(tool_name, RiskLevel.EXECUTE)
        if risk == RiskLevel.READ:
            self._breaker.record_approval()
            return PipelineResult(
                decision=Decision.ALLOW,
                reason=f"Tool '{tool_name}' is classified as read-only.",
                layer=2,
            )

        # Layer 3: Read-only whitelist (redundant with Layer 2 for listed tools,
        # but catches any tool explicitly whitelisted that might not be in risk map)
        if tool_name in _READ_ONLY_WHITELIST:
            self._breaker.record_approval()
            return PipelineResult(
                decision=Decision.ALLOW,
                reason=f"Tool '{tool_name}' is on the read-only whitelist.",
                layer=3,
            )

        # Layer 4: Classifier
        result = self._classifier.classify(tool_name, args)

        if result.decision == ClassifierDecision.ALLOW:
            self._breaker.record_approval()
            return PipelineResult(
                decision=Decision.ALLOW,
                reason=result.reason,
                layer=4,
                classifier_result=result,
            )

        if result.decision == ClassifierDecision.HARD_DENY:
            self._breaker.record_denial()
            return PipelineResult(
                decision=Decision.DENY,
                reason=result.reason,
                layer=4,
                classifier_result=result,
            )

        # Soft deny — ask the user
        return PipelineResult(
            decision=Decision.ASK_USER,
            reason=result.reason,
            layer=4,
            classifier_result=result,
        )

    def learn_from_user(
        self,
        tool_name: str,
        args: dict[str, Any],
        user_allowed: bool,
        *,
        remember: bool = False,
    ) -> None:
        """Record a user's decision and optionally persist as a rule.

        Args:
            tool_name: Tool that was evaluated.
            args: Tool arguments.
            user_allowed: Whether the user approved the call.
            remember: If True, persist the decision as a cross-session rule.
        """
        if user_allowed:
            self._breaker.record_approval()
        else:
            self._breaker.record_denial()

        if remember:
            # Create a rule that matches this exact tool + a general pattern
            pattern = _derive_pattern(tool_name, args)
            self._rules.add(PermissionRule(
                tool_name=tool_name,
                pattern=pattern,
                decision=RuleDecision.ALLOW if user_allowed else RuleDecision.DENY,
            ))

    def format_denial_feedback(self, result: PipelineResult) -> str:
        """Format a denial as a tool error result for model feedback.

        The model receives this as the tool result, allowing it to
        adapt its approach instead of retrying the denied action.

        Args:
            result: The pipeline result that was denied.

        Returns:
            Error message string for the model.
        """
        return (
            f"Permission denied: {result.reason}\n"
            f"The user or safety system blocked this tool call. "
            f"Please try a different approach or ask the user for guidance."
        )


def _derive_pattern(tool_name: str, args: dict[str, Any]) -> str:
    """Derive a regex pattern from tool arguments for rule storage.

    Creates a pattern specific enough to match similar future calls
    without being overly broad.

    Args:
        tool_name: Name of the tool.
        args: Tool call arguments.

    Returns:
        Regex pattern string.
    """
    if tool_name == "execute" and "command" in args:
        # Match the base command (first word)
        command = args["command"].strip()
        base = command.split()[0] if command else ""
        return rf"^.*{re.escape(base)}.*$"

    if tool_name in ("write_file", "edit_file") and "path" in args:
        # Match the file extension pattern
        path = args["path"]
        ext = path.rsplit(".", 1)[-1] if "." in path else ""
        if ext:
            return rf".*\.{re.escape(ext)}.*"
        return rf".*{re.escape(path)}.*"

    # Generic: match the full args string
    return r".*"
