"""Context packs — the Layer-2 unit of task-scoped guidance.

A context pack is a directory under ``.context-packs/<name>/`` that
collects the rules, examples, and scope hints the harness should load
when a matching task runs. The shape is intentionally minimal so packs
stay close to the code they describe:

::

    .context-packs/
        coding-task/
            README.md      # human-readable summary (loaded as static)
            rules.md       # hard constraints (loaded as static)
            pack.yaml      # optional metadata: applicable domains/phases
            examples/      # optional reference patterns (not yet wired)
            allowed-files.yaml  # optional scope override (future)
            required-checks.yaml # optional verifier override (future)

Phase B.1 of the agent-harness roadmap. Only README.md and rules.md are
loaded at this stage; the other files are reserved for later phases but
won't break the loader if present.

The resolver in this module picks a pack based on ``TaskHints`` using a
deterministic priority order: explicit name match, then domain overlap,
then phase overlap, then fallback. This avoids the "load every pack and
hope" anti-pattern.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


# --- Data model ---------------------------------------------------------


@dataclass(frozen=True)
class ContextPack:
    """A loaded context pack.

    Attributes:
        name: Stable identifier (the directory name).
        path: Filesystem location of the pack.
        summary: Content of ``README.md``. Loaded as a cacheable static
            section so the agent sees it every turn without re-rendering.
        rules: Content of ``rules.md``. Same treatment — static.
        domains: Domain tags this pack applies to (empty = applies to
            any domain). Read from optional ``pack.yaml``.
        phases: Phase tags this pack applies to (empty = applies to any
            phase).
    """

    name: str
    path: Path
    summary: str = ""
    rules: str = ""
    domains: tuple[str, ...] = ()
    phases: tuple[str, ...] = ()

    def is_empty(self) -> bool:
        """True when the pack has no loadable content.

        Empty packs are silently skipped by the builder so a stubbed-out
        pack directory doesn't accidentally inject empty sections.
        """
        return not (self.summary.strip() or self.rules.strip())


# --- Loading -----------------------------------------------------------------

_SUMMARY_FILE = "README.md"
_RULES_FILE = "rules.md"
_METADATA_FILE = "pack.yaml"


def _safe_read_text(path: Path) -> str:
    """Read a file's text content, returning '' on any I/O failure."""
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.debug("ContextPack: could not read %s: %s", path, exc)
        return ""


def _parse_metadata(yaml_path: Path) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Parse ``pack.yaml`` into (domains, phases) tuples.

    Deliberately tolerant: unknown keys ignored, missing file fine,
    malformed file logged and falls back to empty tuples. We accept
    either YAML flow-style lists (``[a, b]``) or plain indented lists
    (``- a\n- b``). If PyYAML isn't importable at runtime, we degrade
    to a naive line parser so the pack still works.
    """
    raw = _safe_read_text(yaml_path)
    if not raw.strip():
        return (), ()

    try:
        import yaml  # type: ignore[import-not-found]

        data = yaml.safe_load(raw) or {}
    except ImportError:
        # Minimal fallback parser: look for ``domains:`` / ``phases:``
        # followed by a flow-style list on the same line. Good enough for
        # our own packs; user-authored packs will have yaml available.
        return _parse_metadata_naive(raw)
    except Exception as exc:  # noqa: BLE001  # yaml errors shouldn't brick loading
        logger.warning("ContextPack: malformed %s: %s", yaml_path, exc)
        return (), ()

    if not isinstance(data, dict):
        return (), ()

    def _as_tuple(value: object) -> tuple[str, ...]:
        if isinstance(value, list):
            return tuple(str(v) for v in value if isinstance(v, (str, int)))
        if isinstance(value, str):
            return (value,)
        return ()

    return _as_tuple(data.get("domains")), _as_tuple(data.get("phases"))


def _parse_metadata_naive(text: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Fallback YAML-less parser for pack metadata.

    Looks only for single-line flow-style lists: ``domains: [python, c]``.
    Anything more elaborate (block scalars, anchors) is ignored.
    """
    domains: tuple[str, ...] = ()
    phases: tuple[str, ...] = ()
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("domains:"):
            domains = _extract_flow_list(stripped[len("domains:") :])
        elif stripped.startswith("phases:"):
            phases = _extract_flow_list(stripped[len("phases:") :])
    return domains, phases


def _extract_flow_list(fragment: str) -> tuple[str, ...]:
    """Pull values out of a ``[a, b, c]`` flow-style fragment."""
    fragment = fragment.strip()
    if not (fragment.startswith("[") and fragment.endswith("]")):
        return ()
    body = fragment[1:-1]
    return tuple(item.strip().strip('"').strip("'") for item in body.split(",") if item.strip())


def load_pack(path: str | Path) -> ContextPack | None:
    """Load a single pack from disk.

    Returns ``None`` when the directory doesn't exist or contains no
    loadable content. Callers should treat None as "pack unavailable",
    not as an error — the common case is an unused slot.

    Args:
        path: Path to the pack directory (e.g. ``.context-packs/coding-task/``).
    """
    pack_path = Path(path)
    if not pack_path.is_dir():
        return None

    summary = _safe_read_text(pack_path / _SUMMARY_FILE)
    rules = _safe_read_text(pack_path / _RULES_FILE)
    domains, phases = _parse_metadata(pack_path / _METADATA_FILE)

    pack = ContextPack(
        name=pack_path.name,
        path=pack_path,
        summary=summary,
        rules=rules,
        domains=domains,
        phases=phases,
    )
    if pack.is_empty():
        logger.debug("ContextPack: %s has no content; skipping", pack_path)
        return None
    return pack


def list_packs(packs_dir: str | Path) -> list[ContextPack]:
    """Return every pack found under ``packs_dir``.

    Skips empty packs silently. Never raises on a missing directory —
    an absent ``.context-packs/`` just means no packs, not a failure.
    """
    base = Path(packs_dir)
    if not base.is_dir():
        return []
    out: list[ContextPack] = []
    for entry in sorted(base.iterdir()):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        pack = load_pack(entry)
        if pack is not None:
            out.append(pack)
    return out


# --- Resolver ----------------------------------------------------------------


_FALLBACK_NAME = "coding-task"


def resolve_pack(
    task_hints: dict[str, str] | None,
    packs_dir: str | Path,
) -> ContextPack | None:
    """Pick the most relevant pack for the given task hints.

    Resolution order (first match wins):

    1. Explicit ``pack`` key in hints naming an existing pack.
    2. Domain overlap: the first pack declaring the task's domain.
    3. Phase overlap: the first pack declaring the task's phase.
    4. A generic ``coding-task`` pack if one exists.
    5. ``None`` if nothing matches — prompt builder falls back to no pack.

    The pack name itself does not embed intelligence about the match —
    the tiebreak is the ordering returned by ``list_packs`` (alphabetical).
    For now one generic pack is the whole system; specialisation lands
    once we have real data about which distinctions matter.

    Args:
        task_hints: Classifier output. Optional — None returns the
            fallback.
        packs_dir: Root ``.context-packs/`` directory to search.
    """
    packs = list_packs(packs_dir)
    if not packs:
        return None

    hints = task_hints or {}
    by_name = {p.name: p for p in packs}

    explicit = hints.get("pack")
    if explicit and explicit in by_name:
        return by_name[explicit]

    domain = (hints.get("domain") or "").lower()
    if domain:
        for pack in packs:
            if domain in {d.lower() for d in pack.domains}:
                return pack

    phase = (hints.get("phase") or "").lower()
    if phase:
        for pack in packs:
            if phase in {p.lower() for p in pack.phases}:
                return pack

    if _FALLBACK_NAME in by_name:
        return by_name[_FALLBACK_NAME]

    return None


__all__ = [
    "ContextPack",
    "list_packs",
    "load_pack",
    "resolve_pack",
]
