"""Tests for promote-lesson automation (M6)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from deepagents_cli.promote_lesson import (
    PromotionProposal,
    promote_from_trial,
    propose,
    propose_and_stage,
    stage_proposal,
)
from deepagents_cli.trace_analyzer import TraceInsight


def _insight(**overrides: object) -> TraceInsight:
    defaults: dict[str, object] = {
        "category": "missing_context",
        "confidence": "medium",
        "summary": "summary text",
        "evidence": ("e1", "e2"),
        "proposed_promotion": "do the thing",
    }
    defaults.update(overrides)
    return TraceInsight(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# propose — category dispatch
# ---------------------------------------------------------------------------


def test_propose_missing_context_targets_rules_md() -> None:
    proposal = propose(_insight(category="missing_context"))
    assert proposal.category == "missing_context"
    assert proposal.target_path == ".context-packs/coding-task/rules.md"
    assert "rule" in proposal.title.lower()


def test_propose_missing_rule_points_at_rules_md() -> None:
    proposal = propose(_insight(category="missing_rule", confidence="high"))
    assert proposal.category == "missing_rule"
    assert proposal.target_path == ".context-packs/coding-task/rules.md"
    assert proposal.confidence == "high"


def test_propose_missing_tool_has_no_single_target() -> None:
    proposal = propose(_insight(category="missing_tool"))
    # Scope/tool gaps are architectural — no single file to edit.
    assert proposal.target_path is None
    assert "architectural" in proposal.body.lower() or "decision" in proposal.body.lower()


def test_propose_missing_example_targets_examples_dir() -> None:
    proposal = propose(_insight(category="missing_example"))
    assert proposal.target_path is not None
    assert "examples" in proposal.target_path


def test_propose_model_capability_targets_known_limits_doc() -> None:
    proposal = propose(_insight(category="model_capability_limit"))
    assert proposal.target_path == "docs/harness/known-limits.md"


def test_propose_unknown_category_falls_back() -> None:
    # Can't construct an unknown-category TraceInsight directly since
    # CATEGORIES is validated. Bypass validation by constructing via
    # object.__setattr__ (frozen dataclass guard).
    insight = _insight(category="missing_context")
    object.__setattr__(insight, "category", "newly_invented_bucket")
    proposal = propose(insight)
    assert proposal.category == "newly_invented_bucket"
    assert "Uncategorized" in proposal.title


# ---------------------------------------------------------------------------
# Proposal body shape
# ---------------------------------------------------------------------------


def test_proposal_body_has_standard_sections() -> None:
    proposal = propose(_insight())
    for section in ("## Insight", "## Proposed action", "## Suggested edit", "## Confidence", "## Evidence"):
        assert section in proposal.body


def test_proposal_body_lists_evidence_bullets() -> None:
    proposal = propose(_insight(evidence=("foo", "bar")))
    assert "- foo" in proposal.body
    assert "- bar" in proposal.body


def test_proposal_body_confidence_note_matches_level() -> None:
    high = propose(_insight(confidence="high"))
    low = propose(_insight(confidence="low"))
    assert "auto-apply" in high.body
    assert "triage hint" in low.body


# ---------------------------------------------------------------------------
# stage_proposal
# ---------------------------------------------------------------------------


def test_stage_proposal_creates_pending_promotions_dir(tmp_path: Path) -> None:
    proposal = propose(_insight())
    path = stage_proposal(proposal, harness_dir=tmp_path / ".harness")
    assert path.exists()
    assert path.parent.name == "pending-promotions"


def test_staged_file_contains_header_and_body(tmp_path: Path) -> None:
    proposal = propose(_insight(category="missing_context"))
    path = stage_proposal(
        proposal,
        harness_dir=tmp_path / ".harness",
        trial_id="trial-123",
    )
    text = path.read_text()
    assert "# Promotion proposal" in text
    assert "trial-123" in text
    assert "missing_context" in text
    # The body itself is included
    assert "## Insight" in text


def test_stage_proposal_filename_has_timestamp_category_trial(tmp_path: Path) -> None:
    proposal = propose(_insight(category="missing_rule"))
    path = stage_proposal(
        proposal,
        harness_dir=tmp_path / ".harness",
        trial_id="task-xyz",
    )
    # Expected shape: <timestamp>-missing_rule-task-xyz.md
    assert path.name.endswith("-missing_rule-task-xyz.md")


def test_stage_proposal_sanitizes_trial_id_slashes(tmp_path: Path) -> None:
    proposal = propose(_insight())
    path = stage_proposal(
        proposal,
        harness_dir=tmp_path / ".harness",
        trial_id="runs/abc/xyz",
    )
    # Slashes in the trial id could escape the pending-promotions dir
    # — sanitized to underscores before being used as a filename.
    assert "/" not in path.name
    assert "runs_abc_xyz" in path.name


def test_propose_and_stage_is_single_call_convenience(tmp_path: Path) -> None:
    proposal, staged = propose_and_stage(
        _insight(category="missing_example"),
        harness_dir=tmp_path / ".harness",
        trial_id="t1",
    )
    assert isinstance(proposal, PromotionProposal)
    assert staged.exists()


# ---------------------------------------------------------------------------
# promote_from_trial end-to-end
# ---------------------------------------------------------------------------


def _make_failed_trial(tmp_path: Path, *, kind: str) -> Path:
    """Create a minimal Harbor trial dir that extracts_signals can read."""
    trial = tmp_path / "trial-xyz"
    (trial / "agent").mkdir(parents=True)

    if kind == "arch":
        # arch_rejections > 0 → missing_rule
        (trial / "result.json").write_text(
            json.dumps({"verifier_result": {"rewards": {"reward": 0.0}}})
        )
        traj = {
            "steps": [{"step_id": 1}],
            "final_metrics": {"total_prompt_tokens": 1000, "total_completion_tokens": 200},
        }
        (trial / "agent" / "trajectory.json").write_text(
            json.dumps(traj) + "\nArch-lint violation\n"
        )
    elif kind == "dump":
        # short steps + big completion → missing_example
        (trial / "result.json").write_text(
            json.dumps({"verifier_result": {"rewards": {"reward": 0.0}}})
        )
        traj = {
            "steps": [{"step_id": i} for i in range(3)],
            "final_metrics": {"total_prompt_tokens": 10_000, "total_completion_tokens": 35_000},
        }
        (trial / "agent" / "trajectory.json").write_text(json.dumps(traj))
    elif kind == "hang":
        # tool_call_count=0, no activity → model_capability_limit, low conf
        (trial / "result.json").write_text(
            json.dumps({"verifier_result": {"rewards": {"reward": 0.0}}})
        )
        traj = {
            "steps": [],
            "final_metrics": {"total_prompt_tokens": 0, "total_completion_tokens": 0},
        }
        (trial / "agent" / "trajectory.json").write_text(json.dumps(traj))
    return trial


def test_promote_from_trial_arch_rejection_stages_proposal(tmp_path: Path) -> None:
    trial = _make_failed_trial(tmp_path, kind="arch")
    result = promote_from_trial(trial)
    assert result is not None
    proposal, staged = result
    assert proposal.category == "missing_rule"
    assert staged.exists()
    assert "missing_rule" in staged.name


def test_promote_from_trial_dump_pattern_stages_proposal(tmp_path: Path) -> None:
    trial = _make_failed_trial(tmp_path, kind="dump")
    result = promote_from_trial(trial)
    assert result is not None
    proposal, _ = result
    assert proposal.category == "missing_example"


def test_promote_from_trial_skips_low_conf_model_limit(tmp_path: Path) -> None:
    trial = _make_failed_trial(tmp_path, kind="hang")
    result = promote_from_trial(trial)
    # The hang path is a low-confidence model_capability_limit — we
    # skip it so operator inboxes aren't flooded by provider blips.
    assert result is None


def test_promote_from_trial_missing_dir_returns_none(tmp_path: Path) -> None:
    result = promote_from_trial(tmp_path / "nonexistent")
    assert result is None


def test_promote_from_trial_uses_trial_harness_dir_by_default(tmp_path: Path) -> None:
    trial = _make_failed_trial(tmp_path, kind="arch")
    result = promote_from_trial(trial)
    assert result is not None
    _proposal, staged = result
    # Default: <trial_dir>/.harness/pending-promotions/...
    assert staged.is_relative_to(trial)
    assert staged.parent.parent.name == ".harness"


def test_promote_from_trial_respects_explicit_harness_dir(tmp_path: Path) -> None:
    trial = _make_failed_trial(tmp_path, kind="arch")
    explicit = tmp_path / "custom-harness"
    result = promote_from_trial(trial, harness_dir=explicit)
    assert result is not None
    _proposal, staged = result
    assert staged.is_relative_to(explicit)
