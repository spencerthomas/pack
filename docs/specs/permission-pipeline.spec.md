# Permission Pipeline Specification

> Source of truth: `libs/deepagents/deepagents/permissions/pipeline.py`

## Pipeline Stages

The `PermissionPipeline.evaluate(tool_name, args)` method processes tool calls
through 4 layers in order. First match wins:

| Layer | Name | Description |
|---|---|---|
| 0 | Circuit Breaker | If tripped, returns `MANUAL_MODE` immediately |
| 1 | Rule Matching | Checks persisted cross-session rules (`RuleStore`) |
| 2 | Risk Assessment | Static tool risk classification (`_TOOL_RISK` map) |
| 3 | Read-Only Whitelist | Auto-approves known safe tools (`_READ_ONLY_WHITELIST`) |
| 4 | Classifier | Deterministic regex + optional LLM (`PermissionClassifier`) |

## Decisions

```python
class Decision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    ASK_USER = "ask_user"
    MANUAL_MODE = "manual_mode"  # Circuit breaker tripped
```

## PipelineResult

```python
@dataclass
class PipelineResult:
    decision: Decision
    reason: str
    layer: int              # 0-4
    classifier_result: ClassifierResult | None = None
```

## Circuit Breaker

Source: `deepagents/permissions/circuit_breaker.py`

```python
@dataclass
class CircuitBreaker:
    max_consecutive: int = 3
    max_cumulative: int = 20
```

Methods: `record_denial()`, `record_approval()`, `reset()`

Trips when consecutive denials >= `max_consecutive` OR cumulative denials >= `max_cumulative`.
When tripped, all tool calls route to manual user approval.

## PermissionPipeline Class

```python
class PermissionPipeline:
    def __init__(self, rule_store, classifier=None, circuit_breaker=None)
    def evaluate(self, tool_name: str, args: dict[str, Any]) -> PipelineResult
    def learn_from_user(self, tool_name, args, user_allowed, *, remember=False) -> None
    def format_denial_feedback(self, result: PipelineResult) -> str
    @property
    def rule_store(self) -> RuleStore
    @property
    def circuit_breaker(self) -> CircuitBreaker
```

## The Dual Permission System

Pack has two independent permission middlewares:

### 1. SDK `_PermissionMiddleware` (`deepagents/middleware/permissions.py`)
- Underscore-prefixed (internal/SDK use)
- Enforces `FilesystemPermission` rules (glob-based path patterns)
- Operations: `read`, `write`
- Always last in the middleware stack
- Configured via `permissions` parameter on `create_deep_agent`

### 2. Pack `PermissionMiddleware` (`deepagents/middleware/pack/permission_middleware.py`)
- Part of Pack harness middleware (gated by `PACK_ENABLED`)
- Uses the multi-layer `PermissionPipeline`
- Handles tool-level permissions (not path-level)
- Layers: rule store, risk assessment, whitelist, classifier
- Supports `auto_approve` mode (skips pipeline entirely)

Both can be active simultaneously. SDK permissions restrict filesystem paths;
Pack permissions classify tool calls by risk level.

## Rule Store Format

Source: `deepagents/permissions/rules.py`

Persisted as JSON at `~/.pack/permission_rules.json`:

```json
[
  {
    "tool_name": "execute",
    "pattern": "^.*npm.*$",
    "decision": "allow",
    "created_at": "2025-01-01T00:00:00+00:00",
    "hit_count": 5
  }
]
```

### RuleStore Methods
- `match(tool_name, args)` -- first-match semantics
- `add(rule)` -- append and persist
- `remove(tool_name, pattern)` -- remove by exact match
- `clear()` -- remove all rules

### PermissionRule
- `tool_name: str` -- exact tool name
- `pattern: str` -- regex matched against serialized args (JSON, sorted keys)
- `decision: RuleDecision` (ALLOW | DENY)
- `created_at: datetime`
- `hit_count: int`

## Classifier

Source: `deepagents/permissions/classifier.py`

Two-stage classification:
1. **Stage 1 (deterministic)**: Regex patterns for known dangerous/safe commands
2. **Stage 2 (optional LLM)**: Chain-of-thought re-evaluation for ambiguous cases

Decisions: `ALLOW`, `SOFT_DENY` (show user), `HARD_DENY` (block)

## Risk Levels

```python
class RiskLevel(str, Enum):
    READ = "read"          # Auto-approve
    WRITE = "write"        # Needs review
    EXECUTE = "execute"    # High risk
    DESTRUCTIVE = "destructive"  # Very high risk
```
