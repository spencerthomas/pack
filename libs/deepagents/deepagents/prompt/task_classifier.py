"""Lightweight task classifier for progressive prompt disclosure.

Produces structured hints (phase, domain, complexity) from the raw task
instruction so the `SystemPromptBuilder` can include only the guidance
that applies. Deterministic: keyword + regex, no LLM call, sub-millisecond.

The classifier is intentionally imprecise — it's a signal, not a gate.
Its output feeds a dynamic prompt section, not tool routing, so a
mis-classification manifests as a slightly less targeted prompt rather
than a broken agent run.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# --- Domain detection ----------------------------------------------------

# Ordered: earlier matches win on ambiguous instructions. Values are the
# canonical domain tag plus a short guidance string that renders in the
# task-hints section when that domain fires.
_DOMAIN_PATTERNS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    (
        "python",
        re.compile(
            r"\b(python|pytest|pip|\.py\b|pyproject|numpy|pandas|pytorch|"
            r"tensorflow|django|flask|fastapi)\b",
            re.IGNORECASE,
        ),
        "Prefer pytest for tests; check pyproject.toml or requirements.txt "
        "before installing packages.",
    ),
    (
        "c",
        re.compile(
            r"\b(gcc|clang|make|Makefile|\.c\b|\.h\b|valgrind|"
            r"segfault|gdb|cmake)\b",
            re.IGNORECASE,
        ),
        "Compile with gcc/clang; read the Makefile if present before "
        "writing one. Check for include headers before using libc APIs.",
    ),
    (
        "git",
        re.compile(
            r"\b(git|branch|commit|rebase|merge|cherry-pick|reflog|"
            r"stash|remote|hook)\b",
            re.IGNORECASE,
        ),
        "Work in a safe worktree or branch; avoid destructive operations "
        "(reset --hard, clean -fd) unless the task explicitly requires them.",
    ),
    (
        "shell",
        re.compile(
            r"\b(bash|shell|\.sh\b|zsh|posix|shebang|cron|systemd|"
            r"/etc/|/usr/bin)\b",
            re.IGNORECASE,
        ),
        "Validate with shellcheck mentally before running. Quote variables. "
        "Prefer pipes over intermediate files when the task is read-heavy.",
    ),
    (
        "web",
        re.compile(
            r"\b(html|javascript|\.js\b|\.ts\b|react|node|npm|express|"
            r"css|dom|xss|csrf|cors)\b",
            re.IGNORECASE,
        ),
        "If serving requests, pick a free port first. Sanitize any "
        "HTML rendering. Check package.json for test commands.",
    ),
    (
        "data",
        re.compile(
            r"\b(csv|json|parquet|sqlite|postgres|mysql|sql|dataset|"
            r"dataframe|schema|migration)\b",
            re.IGNORECASE,
        ),
        "Verify column names and types from a sample before writing "
        "transformations. Sanity-check row counts after operations.",
    ),
    (
        "systems",
        re.compile(
            r"\b(kernel|syscall|elf|binary|assembly|register|"
            r"interrupt|mips|x86|arm|qemu|docker)\b",
            re.IGNORECASE,
        ),
        "Inspect binaries with file/readelf/objdump before editing. "
        "Architectures differ — don't assume x86.",
    ),
    (
        "crypto",
        re.compile(
            r"\b(openssl|tls|ssl|cert|x509|aes|rsa|sha|hash|hmac|"
            r"signature|keygen)\b",
            re.IGNORECASE,
        ),
        "Use the task's named algorithm exactly. Don't roll custom "
        "crypto. Verify with openssl command-line when possible.",
    ),
)

# --- Phase detection -----------------------------------------------------

_PHASE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # Order matters: "fix" and "debug" outrank generic "run"
    (
        "fix",
        # "error" alone is too broad (appears in "error frequencies",
        # "error message") so require a fix-action verb.
        re.compile(
            r"\b(fix|bug|failing|debug|repair|broken|resolve|patch)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "build",
        re.compile(
            r"\b(write|implement|create|build|add|generate|make a|"
            r"produce|construct)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "examine",
        re.compile(
            r"\b(analyze|investigate|inspect|review|explain|understand|"
            r"describe|summarize|report)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "test",
        re.compile(
            r"\b(test|verify|validate|assert|check that|ensure|confirm)\b",
            re.IGNORECASE,
        ),
    ),
)

_PHASE_HINTS: dict[str, str] = {
    "fix": (
        "Reproduce the failure first. Locate the defect by narrowing, "
        "not by rewriting. Run tests after every change."
    ),
    "build": (
        "Check for existing scaffolding before starting. Write the "
        "smallest version that can be tested, then iterate."
    ),
    "examine": (
        "Read broadly before writing. Report findings concisely — "
        "don't modify files unless the task asks you to."
    ),
    "test": (
        "Run existing tests first to see current state. Write assertions "
        "that would catch the specific regression, not just smoke tests."
    ),
}

# --- Complexity heuristic ------------------------------------------------

_ITERATIVE_MARKERS = re.compile(
    r"\b(iterate|benchmark|tune|optimize|sweep|parameter|improve until|"
    r"achieve|maximize|minimize|beat|score)\b",
    re.IGNORECASE,
)
_SIMPLE_MARKERS = re.compile(
    r"\b(print|output|return|list|show|display|what is)\b",
    re.IGNORECASE,
)


def _classify_complexity(text: str, *, length: int) -> str:
    """Pick one of simple / iterative / exploratory from text shape."""
    if _ITERATIVE_MARKERS.search(text):
        return "iterative"
    if _SIMPLE_MARKERS.search(text) and length < 400:
        return "simple"
    return "exploratory"


# --- Public API ----------------------------------------------------------


@dataclass(frozen=True)
class TaskHints:
    """Classifier output rendered into the system prompt's task-hints section.

    All fields are optional because classification is best-effort. Callers
    decide which hints to surface; the builder skips empty values.
    """

    phase: str | None = None
    domain: str | None = None
    complexity: str | None = None
    guidance: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, str]:
        """Flatten to the dict shape `SystemPromptBuilder.build` expects.

        Empty/None fields are dropped so the rendered section stays tight.
        Multi-line guidance gets a trailing newline-space indent for list
        rendering.
        """
        out: dict[str, str] = {}
        if self.phase:
            out["phase"] = self.phase
        if self.domain:
            out["domain"] = self.domain
        if self.complexity:
            out["complexity"] = self.complexity
        if self.guidance:
            out["guidance"] = "\n  - " + "\n  - ".join(self.guidance)
        return out


def classify(instruction: str) -> TaskHints:
    """Classify a task instruction into structured hints.

    Deterministic, under 1ms. Always returns a ``TaskHints`` — falls
    back to all-None when signals are absent rather than raising.

    Args:
        instruction: Raw task prompt as received from the benchmark
            harness or user message.

    Returns:
        A ``TaskHints`` whose non-empty fields capture the strongest
        signals found. Empty instructions yield an all-None result.
    """
    text = (instruction or "").strip()
    if not text:
        return TaskHints()

    # Domain: first matching pattern wins
    domain: str | None = None
    guidance: list[str] = []
    for tag, pattern, tip in _DOMAIN_PATTERNS:
        if pattern.search(text):
            domain = tag
            guidance.append(tip)
            break

    # Phase: first matching pattern wins
    phase: str | None = None
    for tag, pattern in _PHASE_PATTERNS:
        if pattern.search(text):
            phase = tag
            guidance.append(_PHASE_HINTS[tag])
            break

    complexity = _classify_complexity(text, length=len(text))

    return TaskHints(
        phase=phase,
        domain=domain,
        complexity=complexity,
        guidance=tuple(guidance),
    )
