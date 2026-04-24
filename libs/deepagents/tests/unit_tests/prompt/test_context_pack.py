"""Tests for Phase B.1 context-pack loader + resolver."""

from __future__ import annotations

from pathlib import Path

import pytest

from deepagents.prompt.context_pack import (
    ContextPack,
    _extract_flow_list,
    _parse_metadata_naive,
    list_packs,
    load_pack,
    resolve_pack,
)


def _make_pack(
    root: Path,
    name: str,
    *,
    readme: str = "",
    rules: str = "",
    pack_yaml: str | None = None,
) -> Path:
    """Write a pack directory under ``root`` and return the pack path."""
    pack_dir = root / name
    pack_dir.mkdir(parents=True)
    if readme:
        (pack_dir / "README.md").write_text(readme)
    if rules:
        (pack_dir / "rules.md").write_text(rules)
    if pack_yaml is not None:
        (pack_dir / "pack.yaml").write_text(pack_yaml)
    return pack_dir


# ---------------------------------------------------------------------------
# ContextPack value type
# ---------------------------------------------------------------------------


def test_context_pack_is_empty_when_no_content() -> None:
    assert ContextPack(name="x", path=Path("/tmp/x")).is_empty() is True


def test_context_pack_not_empty_when_summary_present() -> None:
    pack = ContextPack(name="x", path=Path("/tmp/x"), summary="hello")
    assert pack.is_empty() is False


def test_context_pack_not_empty_when_rules_present() -> None:
    pack = ContextPack(name="x", path=Path("/tmp/x"), rules="rule 1")
    assert pack.is_empty() is False


def test_context_pack_is_frozen() -> None:
    pack = ContextPack(name="x", path=Path("/tmp/x"), summary="y")
    with pytest.raises(Exception):  # noqa: PT011  # frozen dataclass
        pack.name = "z"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# load_pack
# ---------------------------------------------------------------------------


def test_load_missing_dir_returns_none(tmp_path: Path) -> None:
    assert load_pack(tmp_path / "missing") is None


def test_load_empty_dir_returns_none(tmp_path: Path) -> None:
    (tmp_path / "empty").mkdir()
    assert load_pack(tmp_path / "empty") is None


def test_load_with_only_readme(tmp_path: Path) -> None:
    path = _make_pack(tmp_path, "p", readme="# Summary\nbody")
    pack = load_pack(path)
    assert pack is not None
    assert pack.name == "p"
    assert "Summary" in pack.summary
    assert pack.rules == ""
    assert pack.domains == ()
    assert pack.phases == ()


def test_load_with_readme_and_rules(tmp_path: Path) -> None:
    path = _make_pack(tmp_path, "p", readme="r1", rules="rule content")
    pack = load_pack(path)
    assert pack is not None
    assert pack.summary == "r1"
    assert pack.rules == "rule content"


def test_load_with_pack_yaml_flow_lists(tmp_path: Path) -> None:
    path = _make_pack(
        tmp_path,
        "p",
        readme="r",
        pack_yaml="domains: [python, c]\nphases: [fix, build]\n",
    )
    pack = load_pack(path)
    assert pack is not None
    assert pack.domains == ("python", "c")
    assert pack.phases == ("fix", "build")


def test_load_with_malformed_yaml_ignored(tmp_path: Path) -> None:
    path = _make_pack(
        tmp_path,
        "p",
        readme="r",
        pack_yaml="domains: [unclosed\n",
    )
    pack = load_pack(path)
    assert pack is not None
    # Metadata defaults to empty when the file can't be parsed
    assert pack.domains == ()


def test_load_pack_yaml_without_domains_or_phases(tmp_path: Path) -> None:
    path = _make_pack(tmp_path, "p", readme="r", pack_yaml="name: p\nfoo: bar\n")
    pack = load_pack(path)
    assert pack is not None
    assert pack.domains == ()
    assert pack.phases == ()


# ---------------------------------------------------------------------------
# _parse_metadata_naive fallback (exercised directly)
# ---------------------------------------------------------------------------


def test_naive_parser_handles_inline_flow_lists() -> None:
    domains, phases = _parse_metadata_naive(
        "domains: [a, b]\nphases: [build]\n",
    )
    assert domains == ("a", "b")
    assert phases == ("build",)


def test_naive_parser_ignores_block_style() -> None:
    domains, phases = _parse_metadata_naive("domains:\n  - a\n  - b\n")
    assert domains == ()
    assert phases == ()


def test_extract_flow_list_strips_quotes() -> None:
    assert _extract_flow_list(" ['a', \"b\", c ]") == ("a", "b", "c")


def test_extract_flow_list_non_flow_returns_empty() -> None:
    assert _extract_flow_list("foo") == ()


# ---------------------------------------------------------------------------
# list_packs
# ---------------------------------------------------------------------------


def test_list_packs_empty_dir(tmp_path: Path) -> None:
    assert list_packs(tmp_path / "missing") == []


def test_list_packs_returns_only_non_empty(tmp_path: Path) -> None:
    base = tmp_path / ".context-packs"
    base.mkdir()
    _make_pack(base, "with-content", readme="x")
    (base / "empty").mkdir()
    _make_pack(base, "also-content", rules="y")

    packs = list_packs(base)
    names = {p.name for p in packs}
    assert names == {"with-content", "also-content"}


def test_list_packs_skips_hidden_dirs(tmp_path: Path) -> None:
    base = tmp_path / ".context-packs"
    base.mkdir()
    _make_pack(base, ".private", readme="x")
    _make_pack(base, "public", readme="y")
    names = {p.name for p in list_packs(base)}
    assert names == {"public"}


def test_list_packs_skips_files(tmp_path: Path) -> None:
    base = tmp_path / ".context-packs"
    base.mkdir()
    (base / "stray.md").write_text("not a pack")
    _make_pack(base, "real", readme="x")
    names = {p.name for p in list_packs(base)}
    assert names == {"real"}


# ---------------------------------------------------------------------------
# resolve_pack
# ---------------------------------------------------------------------------


@pytest.fixture
def packs_root(tmp_path: Path) -> Path:
    base = tmp_path / ".context-packs"
    base.mkdir()
    _make_pack(
        base,
        "coding-task",
        readme="generic",
    )
    _make_pack(
        base,
        "python-focused",
        readme="python",
        pack_yaml="domains: [python]\n",
    )
    _make_pack(
        base,
        "fix-phase",
        readme="fix",
        pack_yaml="phases: [fix]\n",
    )
    return base


def test_resolve_none_hints_falls_back(packs_root: Path) -> None:
    pack = resolve_pack(None, packs_root)
    assert pack is not None
    assert pack.name == "coding-task"


def test_resolve_empty_hints_falls_back(packs_root: Path) -> None:
    pack = resolve_pack({}, packs_root)
    assert pack is not None
    assert pack.name == "coding-task"


def test_resolve_explicit_pack_name(packs_root: Path) -> None:
    pack = resolve_pack({"pack": "python-focused"}, packs_root)
    assert pack is not None
    assert pack.name == "python-focused"


def test_resolve_unknown_explicit_name_falls_through_to_domain(packs_root: Path) -> None:
    # Bogus explicit + real domain → domain wins
    hints = {"pack": "does-not-exist", "domain": "python"}
    pack = resolve_pack(hints, packs_root)
    assert pack is not None
    assert pack.name == "python-focused"


def test_resolve_domain_match(packs_root: Path) -> None:
    pack = resolve_pack({"domain": "python"}, packs_root)
    assert pack is not None
    assert pack.name == "python-focused"


def test_resolve_phase_match(packs_root: Path) -> None:
    pack = resolve_pack({"phase": "fix"}, packs_root)
    assert pack is not None
    assert pack.name == "fix-phase"


def test_resolve_domain_beats_phase(packs_root: Path) -> None:
    # When both signals could match, domain wins (documented priority)
    hints = {"domain": "python", "phase": "fix"}
    pack = resolve_pack(hints, packs_root)
    assert pack is not None
    assert pack.name == "python-focused"


def test_resolve_unknown_domain_phase_falls_back(packs_root: Path) -> None:
    hints = {"domain": "unknown-domain", "phase": "unknown-phase"}
    pack = resolve_pack(hints, packs_root)
    assert pack is not None
    assert pack.name == "coding-task"  # fallback name


def test_resolve_no_packs_returns_none(tmp_path: Path) -> None:
    assert resolve_pack({"domain": "python"}, tmp_path / "missing") is None


def test_resolve_no_fallback_returns_none(tmp_path: Path) -> None:
    # Packs exist but none match and no coding-task default
    base = tmp_path / ".context-packs"
    base.mkdir()
    _make_pack(base, "only-pack", readme="x", pack_yaml="domains: [rust]\n")
    assert resolve_pack({"domain": "python"}, base) is None
