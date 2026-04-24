"""Tests for .harness/config.yaml loader (M1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from deepagents_cli.harness_config import (
    DependencyRule,
    HarnessConfig,
    PackageSpec,
    find_harness_dir,
    load_config,
    policy_from_config,
)
from deepagents_cli.policy import POLICIES, TaskPolicy


# ---------------------------------------------------------------------------
# find_harness_dir
# ---------------------------------------------------------------------------


def test_find_harness_dir_returns_none_when_missing(tmp_path: Path) -> None:
    assert find_harness_dir(tmp_path) is None


def test_find_harness_dir_finds_direct_child(tmp_path: Path) -> None:
    hd = tmp_path / ".harness"
    hd.mkdir()
    assert find_harness_dir(tmp_path) == hd.resolve()


def test_find_harness_dir_walks_up(tmp_path: Path) -> None:
    hd = tmp_path / ".harness"
    hd.mkdir()
    deep = tmp_path / "a" / "b" / "c"
    deep.mkdir(parents=True)
    assert find_harness_dir(deep) == hd.resolve()


# ---------------------------------------------------------------------------
# load_config — empty / missing cases
# ---------------------------------------------------------------------------


def test_load_missing_harness_returns_none(tmp_path: Path) -> None:
    assert load_config(tmp_path / "nonexistent") is None


def test_load_empty_harness_dir_returns_none(tmp_path: Path) -> None:
    (tmp_path / ".harness").mkdir()
    assert load_config(tmp_path / ".harness") is None


def test_load_malformed_yaml_returns_none(tmp_path: Path) -> None:
    hd = tmp_path / ".harness"
    hd.mkdir()
    (hd / "config.yaml").write_text("version: 1\n  bad: - [unterminated\n")
    assert load_config(hd) is None


# ---------------------------------------------------------------------------
# load_config — repo + packages
# ---------------------------------------------------------------------------


def test_load_minimal_config(tmp_path: Path) -> None:
    hd = tmp_path / ".harness"
    hd.mkdir()
    (hd / "config.yaml").write_text(
        "version: 1\n"
        "repo:\n"
        "  name: my-repo\n"
        "  root: /app\n"
    )
    config = load_config(hd)
    assert config is not None
    assert config.version == 1
    assert config.repo_name == "my-repo"
    assert config.repo_root == "/app"


def test_load_parses_packages(tmp_path: Path) -> None:
    hd = tmp_path / ".harness"
    hd.mkdir()
    (hd / "config.yaml").write_text(
        "version: 1\n"
        "packages:\n"
        "  - name: alpha\n"
        "    path: libs/alpha\n"
        "    layer: runtime\n"
        "  - name: beta\n"
        "    path: libs/beta\n"
    )
    config = load_config(hd)
    assert config is not None
    assert len(config.packages) == 2
    assert config.packages[0] == PackageSpec(name="alpha", path="libs/alpha", layer="runtime")
    assert config.packages[1].layer == ""  # missing → default


def test_load_skips_malformed_package_entries(tmp_path: Path) -> None:
    hd = tmp_path / ".harness"
    hd.mkdir()
    (hd / "config.yaml").write_text(
        "packages:\n"
        "  - path: no-name-oops\n"
        "  - name: real\n"
        "    path: libs/real\n"
    )
    config = load_config(hd)
    assert config is not None
    names = [p.name for p in config.packages]
    assert names == ["real"]


# ---------------------------------------------------------------------------
# load_config — dependency rules
# ---------------------------------------------------------------------------


def test_load_parses_dependency_rules(tmp_path: Path) -> None:
    hd = tmp_path / ".harness"
    hd.mkdir()
    (hd / "config.yaml").write_text(
        "dependency_rules:\n"
        "  - from: libs/cli/**\n"
        "    may_import:\n"
        "      - libs/deepagents/**\n"
        "  - from: libs/deepagents/**\n"
        "    may_not_import:\n"
        "      - libs/cli/**\n"
    )
    config = load_config(hd)
    assert config is not None
    assert len(config.dependency_rules) == 2
    assert config.dependency_rules[0] == DependencyRule(
        from_pattern="libs/cli/**",
        may_import=("libs/deepagents/**",),
        may_not_import=(),
    )
    assert config.dependency_rules[1].may_not_import == ("libs/cli/**",)


# ---------------------------------------------------------------------------
# load_config — task_policies overrides
# ---------------------------------------------------------------------------


def test_task_policies_override_existing_preset(tmp_path: Path) -> None:
    hd = tmp_path / ".harness"
    hd.mkdir()
    (hd / "config.yaml").write_text(
        "task_policies:\n"
        "  docs:\n"
        "    allowed_paths: [docs/**, \"**/*.md\", CHANGELOG]\n"
        "    max_files_changed: 3\n"
        "    required_checks: [docs-lint, spell]\n"
    )
    config = load_config(hd)
    assert config is not None
    docs_policy = config.task_policies["docs"]
    assert docs_policy.max_files_changed == 3
    assert "CHANGELOG" in docs_policy.allowed_paths
    assert "docs-lint" in docs_policy.required_checks
    assert "spell" in docs_policy.required_checks
    # Fields not specified keep the base preset values
    base = POLICIES["docs"]
    assert docs_policy.approval_level == base.approval_level


def test_task_policies_new_type_builds_on_fallback(tmp_path: Path) -> None:
    hd = tmp_path / ".harness"
    hd.mkdir()
    (hd / "config.yaml").write_text(
        "task_policies:\n"
        "  data-migration:\n"
        "    allowed_paths: [migrations/**]\n"
        "    require_reviewer: true\n"
        "    approval_level: required\n"
    )
    config = load_config(hd)
    assert config is not None
    new = config.task_policies["data-migration"]
    assert new.task_type == "data-migration"
    assert new.approval_level == "required"
    assert new.require_reviewer is True
    assert new.allowed_paths == ("migrations/**",)


def test_invalid_override_falls_back_to_base(tmp_path: Path) -> None:
    hd = tmp_path / ".harness"
    hd.mkdir()
    (hd / "config.yaml").write_text(
        "task_policies:\n"
        "  docs:\n"
        "    approval_level: whenever-you-feel-like-it\n"  # invalid
    )
    config = load_config(hd)
    assert config is not None
    # Invalid approval_level → fall back to the preset, not crash.
    assert config.task_policies["docs"].approval_level == POLICIES["docs"].approval_level


# ---------------------------------------------------------------------------
# policy_from_config consumer helper
# ---------------------------------------------------------------------------


def test_policy_from_config_with_none_returns_default() -> None:
    default = POLICIES["feature"]
    result = policy_from_config(None, "feature", default=default)
    assert result is default


def test_policy_from_config_uses_override_when_present() -> None:
    override = TaskPolicy(task_type="feature", max_files_changed=99)
    config = HarnessConfig(task_policies={"feature": override})
    result = policy_from_config(config, "feature", default=POLICIES["feature"])
    assert result is override


def test_policy_from_config_falls_back_when_type_not_overridden() -> None:
    override = TaskPolicy(task_type="docs", max_files_changed=3)
    config = HarnessConfig(task_policies={"docs": override})
    default = POLICIES["feature"]
    result = policy_from_config(config, "feature", default=default)
    assert result is default


# ---------------------------------------------------------------------------
# Auto-find behavior on cwd
# ---------------------------------------------------------------------------


def test_load_config_without_arg_uses_cwd_search(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Put a valid config in a parent dir and chdir into a subdir
    root = tmp_path / "repo"
    hd = root / ".harness"
    hd.mkdir(parents=True)
    (hd / "config.yaml").write_text("version: 1\nrepo:\n  name: auto\n")
    sub = root / "work" / "deep"
    sub.mkdir(parents=True)
    monkeypatch.chdir(sub)

    config = load_config()
    assert config is not None
    assert config.repo_name == "auto"
