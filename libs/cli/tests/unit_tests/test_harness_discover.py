"""Tests for harness discover (Phase B.3)."""

from __future__ import annotations

from pathlib import Path

import pytest

from deepagents_cli.harness_discover import (
    DiscoveryResult,
    _looks_like_test,
    _top_level_dirs,
    discover,
)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A minimal fake repo with two packages, tests, and risk signals."""
    root = tmp_path / "fake-repo"

    # Package A: ``alpha`` — 3 py files, one large
    pkg_a = root / "src" / "alpha"
    pkg_a.mkdir(parents=True)
    (pkg_a / "__init__.py").write_text("")
    (pkg_a / "core.py").write_text(
        "\n".join(f"line {i}" for i in range(100))
    )
    # Large file to trigger the risk-areas section
    (pkg_a / "huge.py").write_text(
        "\n".join(f"line {i}" for i in range(800))
    )

    # Package B: ``beta`` — imports from alpha
    pkg_b = root / "src" / "beta"
    pkg_b.mkdir(parents=True)
    (pkg_b / "__init__.py").write_text("")
    (pkg_b / "logic.py").write_text(
        "from alpha.core import thing\nimport os\n"
    )

    # README at the top level and in one of the packages
    (root / "README.md").write_text("# Fake repo\n\nTop level.\n")
    (root / "src" / "alpha" / "README.md").write_text(
        "# Alpha package\n\nDoes alpha things."
    )

    # Tests for alpha but not beta
    (root / "tests").mkdir()
    (root / "tests" / "test_alpha_core.py").write_text("def test_x(): pass\n")

    # Ignored directory that should NOT show up in scans
    venv = root / ".venv" / "lib" / "python3.11"
    venv.mkdir(parents=True)
    (venv / "big.py").write_text("x\n" * 10_000)

    return root


# ---------------------------------------------------------------------------
# _looks_like_test
# ---------------------------------------------------------------------------


def test_looks_like_test_tests_dir() -> None:
    assert _looks_like_test(Path("repo/tests/foo.py")) is True


def test_looks_like_test_prefix() -> None:
    assert _looks_like_test(Path("repo/src/test_something.py")) is True


def test_looks_like_test_suffix() -> None:
    assert _looks_like_test(Path("repo/src/thing_test.py")) is True


def test_looks_like_test_js_convention() -> None:
    assert _looks_like_test(Path("repo/src/component.test.ts")) is True


def test_not_a_test() -> None:
    assert _looks_like_test(Path("repo/src/core.py")) is False


# ---------------------------------------------------------------------------
# discover — core behavior
# ---------------------------------------------------------------------------


def test_discover_returns_result_without_writing_when_false(repo: Path) -> None:
    result = discover(repo, write_outputs=False)
    assert isinstance(result, DiscoveryResult)
    # Nothing written to docs/generated or .context-packs
    assert not (repo / "docs" / "generated").exists()
    assert not (repo / ".context-packs").exists()


def test_discover_writes_four_reports_by_default(repo: Path) -> None:
    discover(repo)
    out = repo / "docs" / "generated"
    for name in ("codebase-map.md", "package-map.md", "domain-candidates.md", "risk-areas.md"):
        assert (out / name).is_file(), f"missing {name}"


def test_discover_proposes_pack_skeletons(repo: Path) -> None:
    discover(repo)
    proposed = repo / ".context-packs" / "proposed"
    assert proposed.is_dir()
    alpha = proposed / "alpha"
    assert alpha.is_dir()
    assert (alpha / "README.md").is_file()
    assert (alpha / "rules.md").is_file()
    # Content mentions the stats we detected
    readme = (alpha / "README.md").read_text()
    assert "alpha" in readme
    assert "PROPOSED" in readme


def test_discover_skips_ignored_directories(repo: Path) -> None:
    result = discover(repo, write_outputs=False)
    # The .venv file should not have contributed to the LOC total or
    # to the large-files list.
    assert not any(".venv" in p for p, _ in result.large_files)


def test_discover_counts_languages(repo: Path) -> None:
    result = discover(repo, write_outputs=False)
    assert result.languages.get("python", 0) >= 4
    # markdown files from the READMEs
    assert result.languages.get("markdown", 0) >= 2


def test_discover_detects_packages(repo: Path) -> None:
    result = discover(repo, write_outputs=False)
    names = {p.name for p in result.packages}
    assert "alpha" in names
    assert "beta" in names


def test_discover_detects_package_edges(repo: Path) -> None:
    # beta imports from alpha → edge should surface in the package map
    discover(repo)
    package_map = (repo / "docs" / "generated" / "package-map.md").read_text()
    assert "`beta` → `alpha`" in package_map


def test_discover_large_files_appear_in_risk_report(repo: Path) -> None:
    discover(repo)
    risk = (repo / "docs" / "generated" / "risk-areas.md").read_text()
    assert "huge.py" in risk
    assert "800" in risk or "large" in risk.lower()


def test_discover_flags_directories_without_tests(repo: Path) -> None:
    result = discover(repo, write_outputs=False)
    # `src` gets picked up as having tests because they're inside the
    # separate top-level `tests/` directory. The test checks the
    # mechanism via a scenario where a top-level dir has no tests
    # anywhere inside it.
    # We know `src` has no test-looking files — expect it in the
    # list.
    assert "src" in result.directories_without_tests


def test_discover_codebase_map_mentions_language_table(repo: Path) -> None:
    discover(repo)
    codebase = (repo / "docs" / "generated" / "codebase-map.md").read_text()
    assert "## Languages" in codebase
    assert "python" in codebase


def test_discover_top_level_dirs_in_codebase_map(repo: Path) -> None:
    discover(repo)
    codebase = (repo / "docs" / "generated" / "codebase-map.md").read_text()
    assert "## Top-level directories" in codebase
    assert "`src`" in codebase
    assert "`tests`" in codebase


def test_discover_domain_candidates_includes_readme_excerpt(repo: Path) -> None:
    discover(repo)
    domain = (repo / "docs" / "generated" / "domain-candidates.md").read_text()
    # Both alpha and beta live under src/; both should show up.
    assert "`src/alpha`" in domain
    assert "Does alpha things" in domain
    # beta has no README → should be noted
    assert "`src/beta`" in domain


def test_discover_idempotent_pack_proposal(repo: Path) -> None:
    # Running twice shouldn't overwrite proposed pack content.
    discover(repo)
    alpha_readme = repo / ".context-packs" / "proposed" / "alpha" / "README.md"
    alpha_readme.write_text("# hand-edited\n")
    discover(repo)
    # Second run didn't clobber the edit
    assert alpha_readme.read_text() == "# hand-edited\n"


def test_discover_empty_repo(tmp_path: Path) -> None:
    root = tmp_path / "empty-repo"
    root.mkdir()
    result = discover(root, write_outputs=False)
    assert result.total_files == 0
    assert result.packages == ()


# ---------------------------------------------------------------------------
# _top_level_dirs helper (exercised via private)
# ---------------------------------------------------------------------------


def test_top_level_dirs_sorts_by_loc_desc(tmp_path: Path) -> None:
    root = tmp_path / "r"
    root.mkdir()
    # Big dir
    big = root / "big"
    big.mkdir()
    (big / "f.py").write_text("\n".join(f"l{i}" for i in range(100)))
    # Small dir
    small = root / "small"
    small.mkdir()
    (small / "f.py").write_text("l\n")

    # Reuse internal helper for a focused assertion.
    from deepagents_cli.harness_discover import _walk_files
    files = _walk_files(root)

    dirs = _top_level_dirs(root, files)
    # big should come first
    assert dirs[0][0] == "big"
    assert dirs[0][2] > dirs[1][2]
