"""Declarative control plane — loads ``.harness/config.yaml`` overrides.

Phase M1 of the agent-harness roadmap. Target repos describe their own
task policies, dependency rules, and quality gates in a YAML file that
lives next to the ratchet state. Code defaults stay as the floor; the
config acts as a (conservative, typed) overlay.

Shape:

.. code-block:: yaml

    version: 1

    repo:
      name: pack
      root: /app

    packages:
      - name: deepagents
        path: libs/deepagents
        layer: runtime
      - name: cli
        path: libs/cli
        layer: harness

    dependency_rules:
      - from: libs/cli/**
        may_import: [libs/deepagents/**]
      - from: libs/deepagents/**
        may_not_import: [libs/cli/**]

    task_policies:
      docs:
        allowed_paths: [docs/**, "**/*.md"]
        max_files_changed: 10
        required_checks: [docs-lint]

      feature:
        allowed_paths: [libs/**, tests/**, docs/**]
        max_files_changed: 25
        require_plan: true
        require_reviewer: true
        required_checks: [lint, typecheck, tests, arch-lint]

Anything the config doesn't specify keeps the Python default from
``policy.py``. Unknown keys are ignored rather than errored so new
fields can land without breaking older configs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from deepagents_cli.policy import POLICIES, TaskPolicy

logger = logging.getLogger(__name__)


CONFIG_FILENAME = "config.yaml"


# --- Types --------------------------------------------------------------


@dataclass(frozen=True)
class PackageSpec:
    """One first-party package the repo declares."""

    name: str
    path: str
    layer: str = ""


@dataclass(frozen=True)
class DependencyRule:
    """A single direction rule.

    Exactly one of ``may_import`` or ``may_not_import`` should be
    populated. The loader does not enforce that invariant today;
    consumers treat ``may_not_import`` as blocklist and ``may_import``
    as allowlist.
    """

    from_pattern: str
    may_import: tuple[str, ...] = ()
    may_not_import: tuple[str, ...] = ()


@dataclass(frozen=True)
class HarnessConfig:
    """Root of the loaded ``.harness/config.yaml`` tree.

    Loaders always return a fully-typed ``HarnessConfig``. Missing
    fields fall back to safe defaults (empty tuples / None) rather
    than raising so callers can merge partial configs freely.

    Attributes:
        version: Config schema version. 1 is the only value today.
        repo_name: Optional human identifier for the repo.
        repo_root: Optional repo root path the config assumes.
        packages: First-party packages declared by the repo.
        dependency_rules: Declared import direction rules.
        task_policies: Task-type → TaskPolicy overrides. Keys that
            match a known ``POLICIES`` preset override that preset;
            unknown keys produce a new policy on top of the fallback.
    """

    version: int = 1
    repo_name: str | None = None
    repo_root: str | None = None
    packages: tuple[PackageSpec, ...] = ()
    dependency_rules: tuple[DependencyRule, ...] = ()
    task_policies: dict[str, TaskPolicy] = field(default_factory=dict)


# --- Loading ------------------------------------------------------------


def find_harness_dir(start: str | Path | None = None) -> Path | None:
    """Walk up from ``start`` to find a ``.harness/`` directory.

    Returns None when no ``.harness/`` is found between the starting
    path and the filesystem root. Callers use None to decide whether
    to fall back to Python defaults.
    """
    base = Path(start or Path.cwd()).resolve()
    for ancestor in [base, *base.parents]:
        candidate = ancestor / ".harness"
        if candidate.is_dir():
            return candidate
    return None


def load_config(
    harness_dir: str | Path | None = None,
) -> HarnessConfig | None:
    """Load ``.harness/config.yaml`` if present.

    Returns None when the directory doesn't exist or the file is
    missing. Malformed YAML logs a warning and returns None so the
    agent still runs — the operator can fix the config without
    blocking work.

    Args:
        harness_dir: Explicit ``.harness/`` path. When None, walks up
            from ``cwd()`` via :func:`find_harness_dir`.
    """
    resolved: Path | None
    if harness_dir is None:
        resolved = find_harness_dir()
    else:
        resolved = Path(harness_dir)
    if resolved is None or not resolved.is_dir():
        return None

    config_path = resolved / CONFIG_FILENAME
    if not config_path.is_file():
        return None

    try:
        raw = _read_yaml(config_path)
    except Exception as exc:  # noqa: BLE001  # malformed YAML shouldn't kill the run
        logger.warning("Harness config at %s is malformed: %s", config_path, exc)
        return None

    if not isinstance(raw, dict):
        logger.warning("Harness config at %s did not parse as a mapping", config_path)
        return None

    return _parse_config(raw)


def _read_yaml(path: Path) -> Any:
    """Parse YAML from ``path``. Prefers PyYAML; naive fallback otherwise.

    The naive fallback only understands single-line flow-style lists
    and plain key: value scalars — intentionally minimal so a repo
    authoring its first config gets a clear error if it uses block
    scalars without installing PyYAML.
    """
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore[import-not-found]

        return yaml.safe_load(text)
    except ImportError:
        logger.warning(
            "PyYAML not available; falling back to naive parser. "
            "Complex YAML (block scalars, anchors) will not parse."
        )
        return _naive_yaml(text)


def _naive_yaml(text: str) -> dict[str, Any]:
    """Very small YAML-ish parser for the fallback path.

    Supports top-level ``key: value`` and ``key: [a, b]``. Doesn't
    handle nested structures — complex configs require PyYAML.
    """
    out: dict[str, Any] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        value = value.strip()
        if not value:
            continue
        if value.startswith("[") and value.endswith("]"):
            items = [v.strip().strip('"').strip("'") for v in value[1:-1].split(",") if v.strip()]
            out[key.strip()] = items
        else:
            out[key.strip()] = value.strip('"').strip("'")
    return out


# --- Parsing ------------------------------------------------------------


def _parse_config(raw: dict[str, Any]) -> HarnessConfig:
    version = int(raw.get("version", 1))

    repo = raw.get("repo") or {}
    if not isinstance(repo, dict):
        repo = {}

    packages = _parse_packages(raw.get("packages"))
    rules = _parse_dependency_rules(raw.get("dependency_rules"))
    policies = _parse_task_policies(raw.get("task_policies"))

    return HarnessConfig(
        version=version,
        repo_name=_string_or_none(repo.get("name")),
        repo_root=_string_or_none(repo.get("root")),
        packages=packages,
        dependency_rules=rules,
        task_policies=policies,
    )


def _parse_packages(value: Any) -> tuple[PackageSpec, ...]:
    if not isinstance(value, list):
        return ()
    out: list[PackageSpec] = []
    for entry in value:
        if not isinstance(entry, dict):
            continue
        name = _string_or_none(entry.get("name"))
        path = _string_or_none(entry.get("path"))
        if not name or not path:
            continue
        layer = _string_or_none(entry.get("layer")) or ""
        out.append(PackageSpec(name=name, path=path, layer=layer))
    return tuple(out)


def _parse_dependency_rules(value: Any) -> tuple[DependencyRule, ...]:
    if not isinstance(value, list):
        return ()
    out: list[DependencyRule] = []
    for entry in value:
        if not isinstance(entry, dict):
            continue
        src = _string_or_none(entry.get("from"))
        if not src:
            continue
        may = _string_tuple(entry.get("may_import"))
        not_may = _string_tuple(entry.get("may_not_import"))
        out.append(
            DependencyRule(
                from_pattern=src,
                may_import=may,
                may_not_import=not_may,
            )
        )
    return tuple(out)


def _parse_task_policies(value: Any) -> dict[str, TaskPolicy]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, TaskPolicy] = {}
    for task_type, body in value.items():
        if not isinstance(task_type, str) or not isinstance(body, dict):
            continue
        base = POLICIES.get(task_type, POLICIES["unknown"])
        out[task_type] = _apply_policy_overrides(task_type, base, body)
    return out


def _apply_policy_overrides(
    task_type: str,
    base: TaskPolicy,
    overrides: dict[str, Any],
) -> TaskPolicy:
    """Produce a new TaskPolicy with the YAML overrides folded into ``base``.

    Unknown keys are ignored; the goal is forward/backward compat so
    adding a new policy attribute doesn't break older configs.
    """
    allowed = _string_tuple(overrides.get("allowed_paths"), default=base.allowed_paths)
    max_files = _int_or_default(
        overrides.get("max_files_changed"), base.max_files_changed
    )
    require_tests = _bool_or_default(
        overrides.get("require_tests_pass"), base.require_tests_pass
    )
    require_plan = _bool_or_default(
        overrides.get("require_plan"), base.require_plan
    )
    require_reviewer = _bool_or_default(
        overrides.get("require_reviewer"), base.require_reviewer
    )
    approval = _string_or_none(overrides.get("approval_level")) or base.approval_level
    required_checks = _string_tuple(
        overrides.get("required_checks"), default=base.required_checks
    )

    try:
        return TaskPolicy(
            task_type=task_type,
            allowed_paths=allowed,
            max_files_changed=max_files,
            require_tests_pass=require_tests,
            require_plan=require_plan,
            require_reviewer=require_reviewer,
            approval_level=approval,
            required_checks=required_checks,
        )
    except ValueError as exc:
        logger.warning(
            "Invalid overrides for task_type=%s (%s); falling back to base policy",
            task_type,
            exc,
        )
        return base


# --- Small helpers -----------------------------------------------------


def _string_or_none(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value
    return None


def _string_tuple(
    value: Any, *, default: tuple[str, ...] | None = None
) -> tuple[str, ...]:
    if isinstance(value, list):
        return tuple(str(v) for v in value if isinstance(v, (str, int)))
    if isinstance(value, str) and value.strip():
        return (value,)
    return default or ()


def _int_or_default(value: Any, default: int) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


def _bool_or_default(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        low = value.strip().lower()
        if low in {"true", "yes", "1"}:
            return True
        if low in {"false", "no", "0"}:
            return False
    return default


# --- Consumer helper ---------------------------------------------------


def policy_from_config(
    config: HarnessConfig | None,
    task_type: str,
    *,
    default: TaskPolicy,
) -> TaskPolicy:
    """Pick the right policy given config and task_type.

    Config-level override wins; otherwise returns the ``default`` the
    caller already resolved via :func:`policy_for`.
    """
    if config is None:
        return default
    return config.task_policies.get(task_type, default)


__all__ = [
    "CONFIG_FILENAME",
    "DependencyRule",
    "HarnessConfig",
    "PackageSpec",
    "find_harness_dir",
    "load_config",
    "policy_from_config",
]
