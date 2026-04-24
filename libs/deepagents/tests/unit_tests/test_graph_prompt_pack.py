"""Tests for graph.py's PACK_ENABLED system-prompt assembly path.

Covers `_build_pack_system_prompt` and `_collect_prompt_context` — the
helpers that route the user system_prompt through `SystemPromptBuilder`
and gather environment context for dynamic sections.
"""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import patch

from langchain_core.messages import SystemMessage

from deepagents.graph import _build_pack_system_prompt, _collect_prompt_context


# ---------------------------------------------------------------------------
# _collect_prompt_context — cheap, defensive helpers
# ---------------------------------------------------------------------------


def test_collect_prompt_context_returns_cwd_and_os() -> None:
    cwd, os_info, _, _ = _collect_prompt_context()
    assert cwd is not None
    assert os_info is not None


def test_collect_prompt_context_handles_missing_git(monkeypatch: Any) -> None:
    # Simulate git being unavailable — should not raise.
    import subprocess

    def _boom(*_args: Any, **_kwargs: Any) -> str:
        raise FileNotFoundError("git not on PATH")

    monkeypatch.setattr(subprocess, "check_output", _boom)
    cwd, os_info, branch, git_status = _collect_prompt_context()
    assert cwd is not None
    assert os_info is not None
    assert branch is None
    assert git_status is None


# ---------------------------------------------------------------------------
# _build_pack_system_prompt — Anthropic path yields SystemMessage with blocks
# ---------------------------------------------------------------------------


def test_anthropic_model_returns_system_message_with_cache_control() -> None:
    result = _build_pack_system_prompt(
        model="anthropic/claude-sonnet-4-6",
        system_prompt="HARBOR_PREAMBLE",
        task_hints=None,
    )
    assert isinstance(result, SystemMessage)
    # content is a list of content blocks
    assert isinstance(result.content, list)
    # Exactly one block carries cache_control = ephemeral
    cached = [b for b in result.content if isinstance(b, dict) and "cache_control" in b]
    assert len(cached) == 1
    assert cached[0]["cache_control"] == {"type": "ephemeral"}


def test_anthropic_path_includes_task_hints_section() -> None:
    hints = {"phase": "fix", "domain": "python", "guidance": "Run tests first."}
    result = _build_pack_system_prompt(
        model="anthropic/claude-sonnet-4-6",
        system_prompt=None,
        task_hints=hints,
    )
    assert isinstance(result, SystemMessage)
    assert isinstance(result.content, list)
    joined = "\n".join(b["text"] for b in result.content if isinstance(b, dict))
    assert "## Task hints" in joined
    assert "phase" in joined
    assert "python" in joined


def test_anthropic_path_without_hints_omits_hints_section() -> None:
    result = _build_pack_system_prompt(
        model="anthropic/claude-sonnet-4-6",
        system_prompt="X",
        task_hints=None,
    )
    assert isinstance(result, SystemMessage)
    joined = "\n".join(
        b["text"] for b in result.content if isinstance(b, dict)  # type: ignore[union-attr]
    )
    assert "## Task hints" not in joined


# ---------------------------------------------------------------------------
# _build_pack_system_prompt — non-Anthropic path yields plain text
# ---------------------------------------------------------------------------


def test_openai_model_returns_plain_text() -> None:
    result = _build_pack_system_prompt(
        model="openai/gpt-4o",
        system_prompt="HARBOR_PREAMBLE",
        task_hints={"phase": "build"},
    )
    assert isinstance(result, str)
    assert "HARBOR_PREAMBLE" in result
    assert "Task hints" in result


def test_openrouter_glm_returns_plain_text() -> None:
    # The benchmark target: OpenRouter GLM-5.1. Strategy falls back to
    # DefaultCacheStrategy → plain text build, no cache_control markers.
    result = _build_pack_system_prompt(
        model="openrouter:z-ai/glm-5.1",
        system_prompt="test prompt",
        task_hints=None,
    )
    assert isinstance(result, str)
    assert "test prompt" in result


# ---------------------------------------------------------------------------
# SystemMessage input passes through intact
# ---------------------------------------------------------------------------


def test_system_message_input_is_unwrapped_and_included() -> None:
    result = _build_pack_system_prompt(
        model="anthropic/claude-sonnet-4-6",
        system_prompt=SystemMessage(content="UPSTREAM_CONTEXT"),
        task_hints=None,
    )
    assert isinstance(result, SystemMessage)
    joined = "\n".join(
        b["text"] for b in result.content if isinstance(b, dict)  # type: ignore[union-attr]
    )
    assert "UPSTREAM_CONTEXT" in joined


def test_none_system_prompt_still_produces_base_sections() -> None:
    result = _build_pack_system_prompt(
        model="anthropic/claude-sonnet-4-6",
        system_prompt=None,
        task_hints=None,
    )
    assert isinstance(result, SystemMessage)
    joined = "\n".join(
        b["text"] for b in result.content if isinstance(b, dict)  # type: ignore[union-attr]
    )
    # At minimum the identity + safety + tool-rules + style sections land
    assert "Deep Agent" in joined
    assert "Core Behavior" in joined


# ---------------------------------------------------------------------------
# PACK_ENABLED env flag integration
# ---------------------------------------------------------------------------


def test_pack_enabled_flag_is_decoupled_from_helper() -> None:
    # Helper does not read PACK_ENABLED — it's always callable. The flag
    # is checked in create_deep_agent to decide whether to call the helper
    # at all. This guarantees unit tests can exercise the helper without
    # leaking env state.
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("PACK_ENABLED", None)
        result = _build_pack_system_prompt(
            model="anthropic/claude-sonnet-4-6",
            system_prompt="x",
            task_hints=None,
        )
        assert isinstance(result, SystemMessage)


# ---------------------------------------------------------------------------
# prompt_env_override — Harbor passes container env, not controller's
# ---------------------------------------------------------------------------


def test_empty_override_dict_skips_auto_collection() -> None:
    # Passing an empty dict explicitly disables auto-collection — no
    # environment or git section renders, even though the calling process
    # has a real cwd and git repo.
    result = _build_pack_system_prompt(
        model="openrouter:z-ai/glm-5.1",
        system_prompt="PREAMBLE",
        task_hints=None,
        prompt_env_override={},
    )
    assert isinstance(result, str)
    assert "## Environment" not in result
    assert "## Git Context" not in result


def test_override_cwd_is_used_instead_of_controller_cwd() -> None:
    # Harbor passes "/app" for the container workdir; the controller's
    # cwd must never leak through.
    result = _build_pack_system_prompt(
        model="openrouter:z-ai/glm-5.1",
        system_prompt="PREAMBLE",
        task_hints=None,
        prompt_env_override={"cwd": "/app", "os_info": "Linux container"},
    )
    assert isinstance(result, str)
    assert "/app" in result
    assert "Linux container" in result
    # No controller-side paths
    assert "/Users/" not in result
    assert "/home/" not in result


def test_override_none_triggers_auto_collection() -> None:
    # None (default) preserves backwards-compat auto-collection — needed
    # for interactive CLI use outside Harbor.
    result = _build_pack_system_prompt(
        model="openrouter:z-ai/glm-5.1",
        system_prompt="PREAMBLE",
        task_hints=None,
        prompt_env_override=None,
    )
    assert isinstance(result, str)
    # cwd is auto-collected — at least an Environment section should render
    assert "## Environment" in result


# ---------------------------------------------------------------------------
# context_pack — Phase B.2 integration
# ---------------------------------------------------------------------------


def test_context_pack_renders_when_supplied() -> None:
    from pathlib import Path as _Path

    from deepagents.prompt import ContextPack

    pack = ContextPack(
        name="test-pack",
        path=_Path("/tmp/test-pack"),
        summary="Pack summary content",
        rules="- Do the thing\n- Don't do the other thing",
    )
    result = _build_pack_system_prompt(
        model="openrouter:z-ai/glm-5.1",
        system_prompt="HARBOR",
        task_hints=None,
        prompt_env_override={},
        context_pack=pack,
    )
    assert isinstance(result, str)
    assert "## Context pack: test-pack" in result
    assert "Pack summary content" in result
    assert "Don't do the other thing" in result


def test_empty_context_pack_adds_no_section() -> None:
    from pathlib import Path as _Path

    from deepagents.prompt import ContextPack

    pack = ContextPack(name="empty", path=_Path("/tmp/empty"))
    result = _build_pack_system_prompt(
        model="openrouter:z-ai/glm-5.1",
        system_prompt="HARBOR",
        task_hints=None,
        prompt_env_override={},
        context_pack=pack,
    )
    assert isinstance(result, str)
    assert "## Context pack" not in result


def test_context_pack_none_is_safe() -> None:
    # Passing None must not crash and must produce normal prompt output.
    result = _build_pack_system_prompt(
        model="openrouter:z-ai/glm-5.1",
        system_prompt="HARBOR",
        task_hints=None,
        prompt_env_override={},
        context_pack=None,
    )
    assert isinstance(result, str)
    assert "HARBOR" in result


def test_context_pack_rides_cache_boundary_on_anthropic() -> None:
    from pathlib import Path as _Path

    from deepagents.prompt import ContextPack

    pack = ContextPack(
        name="cached-pack",
        path=_Path("/tmp/cached-pack"),
        summary="stays stable across runs",
    )
    result = _build_pack_system_prompt(
        model="anthropic/claude-sonnet-4-6",
        system_prompt="HARBOR",
        task_hints=None,
        prompt_env_override={},
        context_pack=pack,
    )
    # On Anthropic we get SystemMessage with content blocks
    assert isinstance(result, SystemMessage)
    assert isinstance(result.content, list)
    cached = [
        b for b in result.content
        if isinstance(b, dict) and "cache_control" in b
    ]
    assert len(cached) == 1
    # The pack block should either be the cached block or come before it —
    # static content always sits in the cacheable portion.
    cached_text = cached[0]["text"]
    pack_idx = next(
        i for i, b in enumerate(result.content)
        if isinstance(b, dict) and "cached-pack" in b.get("text", "")
    )
    cached_idx = next(
        i for i, b in enumerate(result.content)
        if isinstance(b, dict) and b is cached[0]
    )
    assert pack_idx <= cached_idx
