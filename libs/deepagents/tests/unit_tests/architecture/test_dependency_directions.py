"""Architectural dependency direction tests.

These tests enforce the layered dependency rules of the deepagents package
by statically parsing imports with the ``ast`` module.  No runtime imports
of production code are performed -- only the source text is analysed.

Rules enforced
--------------
1. Domain modules (compaction, memory, cost, permissions, hooks, execution)
   must NOT import from each other.
2. Domain modules must NOT import from middleware/.
3. middleware/pack/ wrappers may import their *corresponding* domain module
   only (e.g. compaction_middleware.py -> compaction/).  ``state.py`` is a
   shared registry and is exempt from the single-domain restriction.
4. providers/ must not import from middleware/.
5. prompt/ must not import from middleware/.
"""

from __future__ import annotations

import ast
from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BASE = (Path(__file__).resolve().parents[3] / "deepagents").resolve()

_DOMAIN_MODULES = [
    "compaction",
    "memory",
    "cost",
    "permissions",
    "hooks",
    "execution",
]

# Map middleware/pack wrapper stems to allowed domain imports.
# ``state.py`` aggregates references from all domains -- exempt it.
_MIDDLEWARE_WRAPPER_ALLOWED: dict[str, list[str]] = {
    "compaction_middleware": ["compaction"],
    "cost_middleware": ["cost"],
    "hooks_middleware": ["hooks"],
    "memory_middleware": ["memory"],
    "parallel_middleware": ["execution"],
    "permission_middleware": ["permissions"],
    "agent_dispatch": [],  # orchestrator, no domain imports expected
    "state": _DOMAIN_MODULES,  # shared state registry -- may reference all
}


def _py_files(directory: Path) -> list[Path]:
    """Return all .py files under *directory*, skipping __pycache__."""
    return sorted(
        p
        for p in directory.rglob("*.py")
        if "__pycache__" not in str(p)
    )


def _imported_modules(source: str) -> list[str]:
    """Return a flat list of fully-qualified module strings from import nodes."""
    tree = ast.parse(source)
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.append(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    return modules


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDomainModuleIsolation:
    """Domain modules must not reach across to peer domains or middleware."""

    def test_no_cross_domain_imports(self) -> None:
        """No domain module may import from another domain module."""
        violations: list[str] = []
        for domain in _DOMAIN_MODULES:
            domain_dir = _BASE / domain
            if not domain_dir.is_dir():
                continue
            for pyf in _py_files(domain_dir):
                rel = pyf.relative_to(_BASE)
                for mod in _imported_modules(pyf.read_text()):
                    for other in _DOMAIN_MODULES:
                        if other == domain:
                            continue
                        if f"deepagents.{other}" in mod:
                            violations.append(
                                f"  {rel} imports '{mod}'\n"
                                f"    Remediation: {domain}/ must not depend on {other}/. "
                                f"Extract a shared interface into a common module or "
                                f"pass the dependency via constructor injection."
                            )
        assert not violations, (
            "Cross-domain imports detected -- domain modules must be independent:\n"
            + "\n".join(violations)
        )

    def test_no_middleware_imports_from_domains(self) -> None:
        """Domain modules must not import from middleware/."""
        violations: list[str] = []
        for domain in _DOMAIN_MODULES:
            domain_dir = _BASE / domain
            if not domain_dir.is_dir():
                continue
            for pyf in _py_files(domain_dir):
                rel = pyf.relative_to(_BASE)
                for mod in _imported_modules(pyf.read_text()):
                    if "deepagents.middleware" in mod:
                        violations.append(
                            f"  {rel} imports '{mod}'\n"
                            f"    Remediation: domain modules sit below middleware "
                            f"in the dependency graph.  Move the needed logic into "
                            f"the domain module or a shared utility."
                        )
        assert not violations, (
            "Domain modules must not import from middleware/:\n"
            + "\n".join(violations)
        )


class TestMiddlewareWrapperImports:
    """middleware/pack/ wrappers may only import their corresponding domain."""

    def test_wrapper_imports_correct_domain_only(self) -> None:
        pack_dir = _BASE / "middleware" / "pack"
        violations: list[str] = []
        for pyf in _py_files(pack_dir):
            stem = pyf.stem
            if stem == "__init__":
                continue
            allowed = _MIDDLEWARE_WRAPPER_ALLOWED.get(stem, [])
            rel = pyf.relative_to(_BASE)
            for mod in _imported_modules(pyf.read_text()):
                for domain in _DOMAIN_MODULES:
                    if f"deepagents.{domain}" in mod and domain not in allowed:
                        violations.append(
                            f"  {rel} imports '{mod}' (allowed: {allowed})\n"
                            f"    Remediation: {stem}.py should only import from "
                            f"{allowed or ['(none)']}. Move the cross-cutting "
                            f"concern into a shared utility or the target domain."
                        )
        assert not violations, (
            "middleware/pack/ wrappers importing wrong domain modules:\n"
            + "\n".join(violations)
        )


class TestProviderAndPromptIsolation:
    """providers/ and prompt/ must not depend on middleware/."""

    def test_providers_no_middleware(self) -> None:
        providers_dir = _BASE / "providers"
        if not providers_dir.is_dir():
            return
        violations: list[str] = []
        for pyf in _py_files(providers_dir):
            rel = pyf.relative_to(_BASE)
            for mod in _imported_modules(pyf.read_text()):
                if "deepagents.middleware" in mod:
                    violations.append(
                        f"  {rel} imports '{mod}'\n"
                        f"    Remediation: providers/ must not depend on middleware/. "
                        f"Inject the required behaviour via a protocol or callback."
                    )
        assert not violations, (
            "providers/ importing from middleware/:\n"
            + "\n".join(violations)
        )

    def test_prompt_no_middleware(self) -> None:
        prompt_dir = _BASE / "prompt"
        if not prompt_dir.is_dir():
            return
        violations: list[str] = []
        for pyf in _py_files(prompt_dir):
            rel = pyf.relative_to(_BASE)
            for mod in _imported_modules(pyf.read_text()):
                if "deepagents.middleware" in mod:
                    violations.append(
                        f"  {rel} imports '{mod}'\n"
                        f"    Remediation: prompt/ must not depend on middleware/. "
                        f"Move shared types to a common location."
                    )
        assert not violations, (
            "prompt/ importing from middleware/:\n"
            + "\n".join(violations)
        )
