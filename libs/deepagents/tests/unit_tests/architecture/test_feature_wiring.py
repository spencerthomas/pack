"""Feature wiring verification tests.

Reads ``graph.py`` and verifies that every domain module has its
corresponding middleware wired into ``_add_pack_middleware``, and that
the function checks the ``PACK_ENABLED`` environment variable.

Uses string matching on the source text -- no runtime imports needed.
"""

from __future__ import annotations

import re
from pathlib import Path

_GRAPH_PY = (Path(__file__).resolve().parents[3] / "deepagents" / "graph.py").resolve()


def _read_add_pack_middleware_body() -> str:
    """Extract the source text of ``_add_pack_middleware`` from graph.py."""
    source = _GRAPH_PY.read_text()
    # Find the function definition and extract until the next top-level def/class
    match = re.search(
        r"^def _add_pack_middleware\b.*?(?=\n(?:def |class )|\Z)",
        source,
        re.DOTALL | re.MULTILINE,
    )
    assert match, (
        "_add_pack_middleware function not found in graph.py.  "
        "Has it been renamed or removed?"
    )
    return match.group(0)


class TestFeatureWiring:
    """Every domain module must be wired into _add_pack_middleware."""

    # Maps domain module name -> expected middleware class import substring
    _EXPECTED_WIRING: dict[str, str] = {
        "compaction": "CompactionMiddleware",
        "cost": "CostMiddleware",
        "permissions": "PermissionMiddleware",
        "memory": "PackMemoryMiddleware",
        "hooks": "HooksMiddleware",
    }

    def test_all_domains_have_middleware(self) -> None:
        body = _read_add_pack_middleware_body()
        missing: list[str] = []
        for domain, middleware_cls in self._EXPECTED_WIRING.items():
            if middleware_cls not in body:
                missing.append(
                    f"  {domain}/ -> {middleware_cls} not found in "
                    f"_add_pack_middleware.\n"
                    f"    Remediation: import {middleware_cls} from "
                    f"deepagents.middleware.pack and append it to the "
                    f"middleware stack inside _add_pack_middleware()."
                )
        assert not missing, (
            "Domain modules missing middleware wiring in _add_pack_middleware:\n"
            + "\n".join(missing)
        )

    def test_pack_enabled_env_check(self) -> None:
        body = _read_add_pack_middleware_body()
        assert "PACK_ENABLED" in body, (
            "_add_pack_middleware does not check the PACK_ENABLED environment "
            "variable.  The function must gate all middleware registration on "
            "os.environ.get('PACK_ENABLED') so that Pack middleware is only "
            "active when the CLI sets this flag."
        )

    def test_domain_imports_present(self) -> None:
        """Each domain module has at least one import in _add_pack_middleware."""
        body = _read_add_pack_middleware_body()
        missing: list[str] = []
        expected_domain_imports: dict[str, str] = {
            "compaction": "deepagents.compaction",
            "cost": "deepagents.cost",
            "permissions": "deepagents.permissions",
            "memory": "deepagents.memory",
            "hooks": "deepagents.hooks",
        }
        for domain, import_prefix in expected_domain_imports.items():
            if import_prefix not in body:
                missing.append(
                    f"  No import from {import_prefix} in _add_pack_middleware.\n"
                    f"    Remediation: the {domain} domain must be imported and "
                    f"used to construct its middleware instance."
                )
        assert not missing, (
            "Domain module imports missing from _add_pack_middleware:\n"
            + "\n".join(missing)
        )
