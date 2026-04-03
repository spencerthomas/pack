"""Unit tests for Pack-specific slash commands and handlers."""

from __future__ import annotations

from unittest.mock import MagicMock

from deepagents_cli.command_registry import BypassTier, SlashCommand
from deepagents_cli.pack_commands import PACK_COMMANDS


class TestPackCommandIntegrity:
    """Validate structural invariants of PACK_COMMANDS."""

    def test_all_entries_are_slash_commands(self) -> None:
        for cmd in PACK_COMMANDS:
            assert isinstance(cmd, SlashCommand)

    def test_names_start_with_slash(self) -> None:
        for cmd in PACK_COMMANDS:
            assert cmd.name.startswith("/"), f"{cmd.name} missing leading slash"

    def test_no_duplicate_names(self) -> None:
        names = [cmd.name for cmd in PACK_COMMANDS]
        assert len(names) == len(set(names)), "Duplicate command names found"

    def test_no_duplicate_aliases(self) -> None:
        all_names: list[str] = []
        for cmd in PACK_COMMANDS:
            all_names.append(cmd.name)
            all_names.extend(cmd.aliases)
        assert len(all_names) == len(set(all_names)), (
            "Duplicate name or alias across entries"
        )

    def test_all_have_descriptions(self) -> None:
        for cmd in PACK_COMMANDS:
            assert cmd.description, f"{cmd.name} has empty description"

    def test_all_have_bypass_tier(self) -> None:
        for cmd in PACK_COMMANDS:
            assert isinstance(cmd.bypass_tier, BypassTier)

    def test_expected_command_count(self) -> None:
        assert len(PACK_COMMANDS) == 10

    def test_expected_commands_present(self) -> None:
        names = {cmd.name for cmd in PACK_COMMANDS}
        expected = {
            "/cost",
            "/budget",
            "/expand",
            "/permissions",
            "/dream",
            "/worktree",
            "/review",
            "/security",
            "/compact",
            "/agents",
        }
        assert names == expected


class TestPackCommandMetadata:
    """Validate specific command metadata."""

    def _find(self, name: str) -> SlashCommand:
        for cmd in PACK_COMMANDS:
            if cmd.name == name:
                return cmd
        msg = f"Command {name} not found"
        raise AssertionError(msg)

    def test_cost_is_side_effect_free(self) -> None:
        assert self._find("/cost").bypass_tier == BypassTier.SIDE_EFFECT_FREE

    def test_budget_is_queued(self) -> None:
        assert self._find("/budget").bypass_tier == BypassTier.QUEUED

    def test_expand_is_side_effect_free(self) -> None:
        assert self._find("/expand").bypass_tier == BypassTier.SIDE_EFFECT_FREE

    def test_agents_is_side_effect_free(self) -> None:
        assert self._find("/agents").bypass_tier == BypassTier.SIDE_EFFECT_FREE

    def test_review_is_queued(self) -> None:
        assert self._find("/review").bypass_tier == BypassTier.QUEUED

    def test_compact_is_queued(self) -> None:
        assert self._find("/compact").bypass_tier == BypassTier.QUEUED

    def test_dream_has_hidden_keywords(self) -> None:
        cmd = self._find("/dream")
        assert "memory" in cmd.hidden_keywords

    def test_worktree_has_hidden_keywords(self) -> None:
        cmd = self._find("/worktree")
        assert "git" in cmd.hidden_keywords


class TestHandlerCost:
    """Tests for the /cost handler."""

    async def test_no_tracker_returns_message(self) -> None:
        from deepagents_cli.pack_command_handlers import handle_cost

        result = await handle_cost(None, "")
        assert "No cost tracker" in result

    async def test_with_tracker_shows_total(self) -> None:
        from deepagents_cli.pack_command_handlers import handle_cost

        tracker = MagicMock()
        tracker.models = {}
        tracker.total_cost = 0.0
        tracker.turn_count = 0
        tracker.budget = None

        result = await handle_cost(None, "", tracker=tracker)
        assert "Cost Breakdown" in result
        assert "Total:" in result

    async def test_with_budget_shows_remaining(self) -> None:
        from deepagents_cli.pack_command_handlers import handle_cost

        tracker = MagicMock()
        tracker.models = {}
        tracker.total_cost = 1.5
        tracker.turn_count = 3
        tracker.budget = 10.0
        tracker.budget_remaining = 8.5

        result = await handle_cost(None, "", tracker=tracker)
        assert "Budget:" in result
        assert "remaining" in result


class TestHandlerBudget:
    """Tests for the /budget handler."""

    async def test_no_tracker_returns_message(self) -> None:
        from deepagents_cli.pack_command_handlers import handle_budget

        result = await handle_budget(None, "")
        assert "No cost tracker" in result

    async def test_empty_args_no_budget(self) -> None:
        from deepagents_cli.pack_command_handlers import handle_budget

        tracker = MagicMock()
        tracker.budget = None
        result = await handle_budget(None, "", tracker=tracker)
        assert "Usage:" in result

    async def test_empty_args_shows_current(self) -> None:
        from deepagents_cli.pack_command_handlers import handle_budget

        tracker = MagicMock()
        tracker.budget = 5.0
        result = await handle_budget(None, "", tracker=tracker)
        assert "Current budget:" in result

    async def test_sets_valid_amount(self) -> None:
        from deepagents_cli.pack_command_handlers import handle_budget

        tracker = MagicMock()
        tracker.budget = None
        result = await handle_budget(None, "10.00", tracker=tracker)
        assert tracker._budget == 10.0  # noqa: RUF069
        assert "budget set to" in result.lower()

    async def test_strips_dollar_sign(self) -> None:
        from deepagents_cli.pack_command_handlers import handle_budget

        tracker = MagicMock()
        tracker.budget = None
        await handle_budget(None, "$5", tracker=tracker)
        assert tracker._budget == 5.0  # noqa: RUF069

    async def test_rejects_invalid_amount(self) -> None:
        from deepagents_cli.pack_command_handlers import handle_budget

        tracker = MagicMock()
        result = await handle_budget(None, "abc", tracker=tracker)
        assert "Invalid amount" in result

    async def test_rejects_negative_amount(self) -> None:
        from deepagents_cli.pack_command_handlers import handle_budget

        tracker = MagicMock()
        result = await handle_budget(None, "-5", tracker=tracker)
        assert "positive" in result.lower()


class TestHandlerExpand:
    """Tests for the /expand handler."""

    async def test_no_collapser_returns_message(self) -> None:
        from deepagents_cli.pack_command_handlers import handle_expand

        result = await handle_expand(None, "")
        assert "not active" in result

    async def test_empty_args_no_entries(self) -> None:
        from deepagents_cli.pack_command_handlers import handle_expand

        collapser = MagicMock()
        collapser.entries = {}
        result = await handle_expand(None, "", collapser=collapser)
        assert "No collapsed entries" in result

    async def test_empty_args_lists_ids(self) -> None:
        from deepagents_cli.pack_command_handlers import handle_expand

        collapser = MagicMock()
        collapser.entries = {"abc123": MagicMock()}
        result = await handle_expand(None, "", collapser=collapser)
        assert "abc123" in result

    async def test_expand_found(self) -> None:
        from deepagents_cli.pack_command_handlers import handle_expand

        collapser = MagicMock()
        collapser.expand.return_value = "original content here"
        result = await handle_expand(None, "abc123", collapser=collapser)
        assert result == "original content here"

    async def test_expand_not_found(self) -> None:
        from deepagents_cli.pack_command_handlers import handle_expand

        collapser = MagicMock()
        collapser.expand.return_value = None
        result = await handle_expand(None, "missing", collapser=collapser)
        assert "No collapsed entry" in result


class TestHandlerPermissions:
    """Tests for the /permissions handler."""

    async def test_no_rule_store_returns_message(self) -> None:
        from deepagents_cli.pack_command_handlers import handle_permissions

        result = await handle_permissions(None, "")
        assert "not active" in result

    async def test_list_empty(self) -> None:
        from deepagents_cli.pack_command_handlers import handle_permissions

        store = MagicMock()
        store.rules = []
        result = await handle_permissions(None, "list", rule_store=store)
        assert "No permission rules" in result

    async def test_list_with_rules(self) -> None:
        from deepagents_cli.pack_command_handlers import handle_permissions

        rule = MagicMock()
        rule.decision.value = "allow"
        rule.tool_name = "execute"
        rule.pattern = ".*"
        rule.hit_count = 3
        store = MagicMock()
        store.rules = [rule]
        result = await handle_permissions(None, "list", rule_store=store)
        assert "Permission Rules" in result
        assert "execute" in result

    async def test_reset_clears(self) -> None:
        from deepagents_cli.pack_command_handlers import handle_permissions

        store = MagicMock()
        result = await handle_permissions(None, "reset", rule_store=store)
        store.clear.assert_called_once()
        assert "cleared" in result.lower()

    async def test_add_not_implemented(self) -> None:
        from deepagents_cli.pack_command_handlers import handle_permissions

        store = MagicMock()
        result = await handle_permissions(None, "add", rule_store=store)
        assert "not yet implemented" in result

    async def test_unknown_subcommand(self) -> None:
        from deepagents_cli.pack_command_handlers import handle_permissions

        store = MagicMock()
        result = await handle_permissions(None, "foo", rule_store=store)
        assert "Unknown subcommand" in result


class TestHandlerDream:
    """Tests for the /dream handler."""

    async def test_no_consolidator_returns_message(self) -> None:
        from deepagents_cli.pack_command_handlers import handle_dream

        result = await handle_dream(None, "")
        assert "not configured" in result

    async def test_no_transcripts(self) -> None:
        from deepagents_cli.pack_command_handlers import handle_dream

        consolidator = MagicMock()
        consolidator.find_recent_transcripts.return_value = []
        result = await handle_dream(None, "", consolidator=consolidator)
        assert "No recent transcripts" in result

    async def test_no_new_patterns(self) -> None:
        from deepagents_cli.pack_command_handlers import handle_dream

        consolidator = MagicMock()
        consolidator.find_recent_transcripts.return_value = ["/tmp/t.md"]
        consolidator.consolidate.return_value = []
        result = await handle_dream(None, "", consolidator=consolidator)
        assert "No new patterns" in result

    async def test_with_entries(self) -> None:
        from deepagents_cli.pack_command_handlers import handle_dream

        entry = MagicMock()
        entry.category.value = "user"
        entry.description = "prefers short variable names"
        consolidator = MagicMock()
        consolidator.find_recent_transcripts.return_value = ["/tmp/t.md"]
        consolidator.consolidate.return_value = [entry]
        result = await handle_dream(None, "", consolidator=consolidator)
        assert "1 new memories" in result
        assert "prefers short variable names" in result


class TestHandlerWorktree:
    """Tests for the /worktree handler."""

    async def test_no_subcommand_shows_usage(self) -> None:
        from deepagents_cli.pack_command_handlers import handle_worktree

        result = await handle_worktree(None, "")
        assert "Usage:" in result

    async def test_create_no_args_shows_usage(self) -> None:
        from deepagents_cli.pack_command_handlers import handle_worktree

        result = await handle_worktree(None, "create")
        assert "Usage:" in result


class TestHandlerCompact:
    """Tests for the /compact handler."""

    async def test_no_monitor_returns_message(self) -> None:
        from deepagents_cli.pack_command_handlers import handle_compact

        result = await handle_compact(None, "")
        assert "not active" in result

    async def test_with_monitor(self) -> None:
        from deepagents_cli.pack_command_handlers import handle_compact

        monitor = MagicMock()
        monitor.context_window = 200_000
        result = await handle_compact(None, "", monitor=monitor)
        assert "compaction triggered" in result.lower()
        assert "200,000" in result


class TestHandlerReview:
    """Tests for the /review handler."""

    async def test_default_focus(self) -> None:
        from deepagents_cli.pack_command_handlers import handle_review

        result = await handle_review(None, "")
        assert "Review" in result
        assert "all current changes" in result

    async def test_custom_focus(self) -> None:
        from deepagents_cli.pack_command_handlers import handle_review

        result = await handle_review(None, "security module")
        assert "security module" in result


class TestHandlerSecurity:
    """Tests for the /security handler."""

    async def test_default_scope(self) -> None:
        from deepagents_cli.pack_command_handlers import handle_security

        result = await handle_security(None, "")
        assert "security" in result.lower()
        assert "all current changes" in result

    async def test_custom_scope(self) -> None:
        from deepagents_cli.pack_command_handlers import handle_security

        result = await handle_security(None, "auth module")
        assert "auth module" in result


class TestHandlerAgents:
    """Tests for the /agents handler."""

    async def test_lists_all_types(self) -> None:
        from deepagents_cli.pack_command_handlers import handle_agents

        result = await handle_agents(None, "")
        assert "Available Agent Types" in result
        assert "Explore" in result
        assert "Review" in result
        assert "General" in result
        assert "Plan" in result
