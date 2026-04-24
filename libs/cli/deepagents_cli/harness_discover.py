"""harness discover — read-only onboarding scan for a brownfield repo.

The first step of Phase B adoption. Walks a target repository and emits
four markdown reports under ``docs/generated/`` plus proposed context-
pack skeletons under ``.context-packs/proposed/``. A human curates the
output before any enforcement turns on.

Outputs:

- ``codebase-map.md``: top-level layout summary with per-directory
  sizes, languages present, and file counts.
- ``package-map.md``: detected first-party packages and inferred
  dependency edges (best-effort — see caveats in the generated doc).
- ``domain-candidates.md``: directory clusters that look like they
  hold domain logic, each with a README excerpt if present.
- ``risk-areas.md``: heat map of large files, files with many imports,
  directories without tests.

No LLM calls. Pure filesystem scan, suffix-based language detection,
regex-based import extraction. Slow repos get the default cap
(``max_files=50000``) so a giant monorepo scan doesn't hang.

Phase B.3 of the agent-harness roadmap.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

# --- Types --------------------------------------------------------------


@dataclass
class _PackageInfo:
    """Detected first-party package plus inferred dependencies."""

    name: str
    path: Path
    file_count: int = 0
    loc: int = 0
    imports: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class DiscoveryResult:
    """Structured output of ``discover()``.

    The dataclass is frozen so callers can cache the result; the
    inner dicts/lists are not deep-frozen but callers should treat
    them as read-only.
    """

    repo_root: Path
    total_files: int
    total_loc: int
    languages: dict[str, int]
    packages: tuple[_PackageInfo, ...]
    top_level_dirs: tuple[tuple[str, int, int], ...]  # (name, files, loc)
    large_files: tuple[tuple[str, int], ...]  # (path, loc), sorted desc
    directories_without_tests: tuple[str, ...]


# --- Config ------------------------------------------------------------

# File extensions we treat as language-bearing. Anything not in here
# counts toward ``other``.
_LANGUAGE_EXTS: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".jsx": "javascript",
    ".go": "go",
    ".rs": "rust",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".hpp": "cpp",
    ".java": "java",
    ".kt": "kotlin",
    ".rb": "ruby",
    ".php": "php",
    ".swift": "swift",
    ".cs": "csharp",
    ".sh": "shell",
    ".bash": "shell",
    ".sql": "sql",
    ".md": "markdown",
    ".rst": "markdown",
}

# Directories we never descend into — build artefacts, vendored code,
# caches. Walking them is wasteful and usually leaks false-positive
# "large files" into the risk report.
_IGNORE_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "env",
        "node_modules",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        "dist",
        "build",
        "target",
        ".next",
        ".svelte-kit",
        "coverage",
        ".coverage",
    }
)

_LARGE_FILE_LOC = 500  # files over this threshold land in risk-areas
_MAX_FILES_SCANNED = 50_000


# --- Scanning ----------------------------------------------------------


def _walk_files(repo_root: Path) -> list[Path]:
    """Enumerate files under ``repo_root`` skipping ignored dirs."""
    out: list[Path] = []
    for path in repo_root.rglob("*"):
        if len(out) >= _MAX_FILES_SCANNED:
            break
        if any(part in _IGNORE_DIRS or part.startswith(".") and part != ".context-packs"
               for part in path.parts if part != repo_root.name):
            continue
        if path.is_file():
            out.append(path)
    return out


def _count_loc(path: Path) -> int:
    """Count non-empty, non-comment lines (cheap, imperfect)."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    return sum(1 for line in text.splitlines() if line.strip())


_PY_IMPORT_RE = re.compile(r"^\s*(?:from|import)\s+([\w.]+)", re.MULTILINE)


def _extract_python_imports(path: Path) -> set[str]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return set()
    names: set[str] = set()
    for match in _PY_IMPORT_RE.finditer(text):
        names.add(match.group(1).split(".", 1)[0])
    return names


# --- Package detection --------------------------------------------------


def _detect_packages(repo_root: Path, files: list[Path]) -> list[_PackageInfo]:
    """Find first-party Python packages by looking for ``__init__.py``.

    Returns packages sorted by file count descending — gives callers a
    rough proxy for which are the meatiest to document first.

    We only consider directories directly under ``<lib-root>/*/`` or
    equivalent. A package nested arbitrarily deep isn't recognized as
    a top-level package for onboarding purposes; that's a reasonable
    simplification for the first pass.
    """
    package_dirs: dict[Path, _PackageInfo] = {}

    for file in files:
        if file.name != "__init__.py":
            continue
        pkg_path = file.parent
        # Walk up to find the shallowest ancestor containing __init__.py
        # directly under a non-package parent — that's the "top-level"
        # of this package family.
        top = pkg_path
        while (top.parent / "__init__.py").exists():
            top = top.parent
        if top in package_dirs:
            continue
        package_dirs[top] = _PackageInfo(name=top.name, path=top)

    # Tally files + imports per package
    for info in package_dirs.values():
        for file in files:
            try:
                file.relative_to(info.path)
            except ValueError:
                continue
            info.file_count += 1
            if file.suffix == ".py":
                info.loc += _count_loc(file)
                info.imports.update(_extract_python_imports(file))

    return sorted(
        package_dirs.values(), key=lambda p: p.file_count, reverse=True
    )


def _package_edges(packages: list[_PackageInfo]) -> dict[str, set[str]]:
    """Infer dependency edges from Python imports.

    Edge A → B exists when package A imports any module named after B.
    Best-effort: ``import foo.bar`` gives edge A → foo even if
    ``foo`` is external. The caller filters to first-party names.
    """
    names = {p.name for p in packages}
    edges: dict[str, set[str]] = defaultdict(set)
    for pkg in packages:
        first_party = pkg.imports & names
        first_party.discard(pkg.name)  # no self-edges
        edges[pkg.name] = first_party
    return edges


# --- Rendering ---------------------------------------------------------


def _render_codebase_map(result: DiscoveryResult) -> str:
    lines = [
        "# Codebase map",
        "",
        f"Generated by `harness discover` against `{result.repo_root}`.",
        "",
        f"**Total files scanned:** {result.total_files:,}  ",
        f"**Total lines of code:** {result.total_loc:,}",
        "",
        "## Languages",
        "",
        "| Language | Files |",
        "|----------|------:|",
    ]
    for lang, count in sorted(
        result.languages.items(), key=lambda kv: -kv[1]
    ):
        lines.append(f"| {lang} | {count:,} |")
    lines.extend(
        [
            "",
            "## Top-level directories",
            "",
            "| Directory | Files | LOC |",
            "|-----------|------:|----:|",
        ]
    )
    for name, files, loc in result.top_level_dirs:
        lines.append(f"| `{name}` | {files:,} | {loc:,} |")
    lines.append("")
    return "\n".join(lines)


def _render_package_map(result: DiscoveryResult, edges: dict[str, set[str]]) -> str:
    lines = [
        "# Package map",
        "",
        "Auto-detected first-party Python packages and their inferred "
        "dependency edges. Edges are best-effort (import-name based); "
        "review and correct before feeding into arch-lint's "
        "`PACKAGE_EDGES` table.",
        "",
        "## Packages",
        "",
        "| Package | Files | LOC | Path |",
        "|---------|------:|----:|------|",
    ]
    for pkg in result.packages:
        try:
            rel = pkg.path.relative_to(result.repo_root)
        except ValueError:
            rel = pkg.path
        lines.append(f"| `{pkg.name}` | {pkg.file_count:,} | {pkg.loc:,} | `{rel}` |")
    lines.extend(["", "## Dependency edges", ""])
    if not any(edges.values()):
        lines.append("_No first-party dependency edges detected._")
    else:
        for src in sorted(edges):
            targets = ", ".join(f"`{t}`" for t in sorted(edges[src]))
            if targets:
                lines.append(f"- `{src}` → {targets}")
    lines.append("")
    return "\n".join(lines)


def _render_domain_candidates(result: DiscoveryResult) -> str:
    """List top-level dirs that look like domain roots.

    Heuristic: directory under ``<repo>/src/``, ``<repo>/packages/``,
    ``<repo>/domains/``, or ``<repo>/libs/*/`` that contains a README.
    No README → it's mentioned but flagged as "no documentation yet".
    """
    candidates: list[tuple[Path, str]] = []
    search_roots = [
        result.repo_root / "src",
        result.repo_root / "packages",
        result.repo_root / "domains",
        result.repo_root / "libs",
    ]
    for root in search_roots:
        if not root.is_dir():
            continue
        for entry in sorted(root.iterdir()):
            if not entry.is_dir():
                continue
            if entry.name.startswith("."):
                continue
            readme = _first_readme(entry)
            excerpt = _excerpt_readme(readme) if readme else ""
            candidates.append((entry, excerpt))

    lines = [
        "# Domain candidates",
        "",
        "Directories that look like domain or feature clusters. Use as "
        "a starting point for `.context-packs/<domain>/` authoring. "
        "Delete the ones that aren't real domains.",
        "",
    ]
    if not candidates:
        lines.append(
            "_No obvious domain-cluster directories found under "
            "`src/`, `packages/`, `domains/`, or `libs/`._"
        )
    for path, excerpt in candidates:
        try:
            rel = path.relative_to(result.repo_root)
        except ValueError:
            rel = path
        lines.append(f"## `{rel}`")
        lines.append("")
        if excerpt:
            lines.append(excerpt)
        else:
            lines.append("_No README found; add one before authoring a pack._")
        lines.append("")
    return "\n".join(lines)


def _render_risk_areas(result: DiscoveryResult) -> str:
    lines = [
        "# Risk areas",
        "",
        "Heat map of structural concerns detected by the scan. These are "
        "not bugs — they're places an agent is likely to get lost or an "
        "edit is likely to touch too much at once.",
        "",
        f"## Large files (>{_LARGE_FILE_LOC} LOC)",
        "",
    ]
    if not result.large_files:
        lines.append("_No files over the size threshold._")
    else:
        lines.append("| File | LOC |")
        lines.append("|------|----:|")
        for path, loc in result.large_files:
            lines.append(f"| `{path}` | {loc:,} |")
    lines.extend(["", "## Directories without tests", ""])
    if not result.directories_without_tests:
        lines.append("_Every top-level directory has at least one test file._")
    else:
        for d in result.directories_without_tests:
            lines.append(f"- `{d}`")
    lines.append("")
    return "\n".join(lines)


# --- Helpers -----------------------------------------------------------


def _first_readme(root: Path) -> Path | None:
    for name in ("README.md", "README.rst", "README", "readme.md"):
        candidate = root / name
        if candidate.is_file():
            return candidate
    return None


def _excerpt_readme(path: Path, max_chars: int = 400) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    stripped = text.strip()
    if len(stripped) <= max_chars:
        return stripped
    return stripped[:max_chars].rstrip() + "\n\n_[excerpted]_"


def _top_level_dirs(
    repo_root: Path, files: list[Path]
) -> list[tuple[str, int, int]]:
    buckets: dict[str, list[int]] = defaultdict(list)
    for f in files:
        try:
            rel = f.relative_to(repo_root)
        except ValueError:
            continue
        parts = rel.parts
        if not parts:
            continue
        top = parts[0]
        buckets[top].append(_count_loc(f) if f.suffix in _LANGUAGE_EXTS else 0)
    return sorted(
        [(name, len(locs), sum(locs)) for name, locs in buckets.items()],
        key=lambda kv: -kv[2],
    )


def _directories_without_tests(
    repo_root: Path, files: list[Path]
) -> list[str]:
    """Find top-level dirs with no test files anywhere inside."""
    buckets: dict[str, bool] = {}
    for f in files:
        try:
            rel = f.relative_to(repo_root)
        except ValueError:
            continue
        if not rel.parts:
            continue
        top = rel.parts[0]
        if top not in buckets:
            buckets[top] = False
        if _looks_like_test(f):
            buckets[top] = True
    return sorted(name for name, has_tests in buckets.items() if not has_tests)


def _looks_like_test(path: Path) -> bool:
    parts = {p.lower() for p in path.parts}
    if "tests" in parts or "test" in parts:
        return True
    name = path.name.lower()
    return name.startswith("test_") or name.endswith("_test.py") or ".test." in name


# --- Pack skeleton proposal ---------------------------------------------


def _propose_pack_skeleton(
    repo_root: Path, packages: list[_PackageInfo]
) -> list[Path]:
    """Create skeletal context-pack directories under ``.context-packs/proposed/``.

    Returns the list of created directories. Safe to call repeatedly —
    existing skeletons are not overwritten.
    """
    proposed_root = repo_root / ".context-packs" / "proposed"
    proposed_root.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []
    for pkg in packages[:5]:  # top 5 packages only; human promotes more later
        pack_dir = proposed_root / pkg.name
        if pack_dir.exists():
            continue
        pack_dir.mkdir(parents=True)
        (pack_dir / "README.md").write_text(
            f"# `{pkg.name}` context pack (PROPOSED)\n\n"
            f"Auto-generated skeleton. Fill in the summary of what this "
            f"package owns, what kinds of tasks run under it, and what "
            f"the agent should know before touching its files.\n\n"
            f"Detected stats: **{pkg.file_count:,} files**, "
            f"**{pkg.loc:,} LOC**.\n"
        )
        (pack_dir / "rules.md").write_text(
            f"# Rules for `{pkg.name}`\n\n"
            f"Replace these with the real constraints — these are "
            f"placeholders so the pack isn't empty.\n\n"
            f"- Keep changes scoped to this package where possible.\n"
            f"- Run this package's tests before declaring done.\n"
            f"- Preserve the public API surface unless the task "
            f"explicitly asks for a breaking change.\n"
        )
        created.append(pack_dir)
    return created


# --- Public entry point -------------------------------------------------


def discover(
    repo_root: str | Path,
    *,
    write_outputs: bool = True,
) -> DiscoveryResult:
    """Scan a repo and optionally write the four generated reports.

    Args:
        repo_root: Directory to scan.
        write_outputs: When True (default), writes markdown files under
            ``<repo_root>/docs/generated/`` and proposed packs under
            ``<repo_root>/.context-packs/proposed/``. When False, the
            function only returns the ``DiscoveryResult`` — useful for
            tests or previewing.

    Returns:
        ``DiscoveryResult`` — even when ``write_outputs=False``. Callers
        can render their own reports or feed the raw data elsewhere.
    """
    root = Path(repo_root).resolve()
    files = _walk_files(root)

    languages: Counter[str] = Counter()
    total_loc = 0
    large_files: list[tuple[str, int]] = []
    for f in files:
        lang = _LANGUAGE_EXTS.get(f.suffix, "other")
        languages[lang] += 1
        if f.suffix in _LANGUAGE_EXTS:
            loc = _count_loc(f)
            total_loc += loc
            if loc > _LARGE_FILE_LOC:
                try:
                    rel = str(f.relative_to(root))
                except ValueError:
                    rel = str(f)
                large_files.append((rel, loc))

    packages = _detect_packages(root, files)
    edges = _package_edges(packages)
    top_dirs = _top_level_dirs(root, files)
    no_tests = _directories_without_tests(root, files)

    result = DiscoveryResult(
        repo_root=root,
        total_files=len(files),
        total_loc=total_loc,
        languages=dict(languages),
        packages=tuple(packages),
        top_level_dirs=tuple(top_dirs),
        large_files=tuple(sorted(large_files, key=lambda kv: -kv[1])[:50]),
        directories_without_tests=tuple(no_tests),
    )

    if write_outputs:
        _write_reports(result, edges)
        _propose_pack_skeleton(root, packages)

    return result


def _write_reports(
    result: DiscoveryResult, edges: dict[str, set[str]]
) -> None:
    """Emit the four report markdown files under ``docs/generated/``."""
    out_dir = result.repo_root / "docs" / "generated"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "codebase-map.md").write_text(_render_codebase_map(result))
    (out_dir / "package-map.md").write_text(_render_package_map(result, edges))
    (out_dir / "domain-candidates.md").write_text(_render_domain_candidates(result))
    (out_dir / "risk-areas.md").write_text(_render_risk_areas(result))


__all__ = [
    "DiscoveryResult",
    "discover",
]
