"""Unit tests for role_actions_service.py — pure functions, no DB required."""
from app.services.role_actions_service import (
    generate_role_actions,
    enrich_with_ownership,
    merge_role_actions,
    ROLES,
)


class TestGenerateRoleActions:
    def test_product_bug_returns_all_roles(self):
        actions = generate_role_actions(failure_category="PRODUCT_BUG")
        for role in ROLES:
            assert role in actions
            assert len(actions[role]) > 0

    def test_unknown_category_uses_default(self):
        actions = generate_role_actions(failure_category="NONEXISTENT_CATEGORY")
        for role in ROLES:
            assert role in actions

    def test_flaky_flag_overrides_category(self):
        actions = generate_role_actions(failure_category="PRODUCT_BUG", is_flaky=True)
        assert "Quarantine" in actions["qa"] or "flaky" in actions["qa"].lower()

    def test_low_confidence_adds_caveat(self):
        actions = generate_role_actions(failure_category="PRODUCT_BUG", confidence_score=30)
        for role in ROLES:
            assert actions[role].startswith("[Low confidence]")

    def test_high_confidence_no_caveat(self):
        actions = generate_role_actions(failure_category="PRODUCT_BUG", confidence_score=80)
        for role in ROLES:
            assert not actions[role].startswith("[Low confidence]")

    def test_zero_confidence_no_caveat(self):
        # confidence_score=0 is default/unknown, should NOT add caveat
        actions = generate_role_actions(failure_category="PRODUCT_BUG", confidence_score=0)
        for role in ROLES:
            assert not actions[role].startswith("[Low confidence]")


class TestEnrichWithOwnership:
    def test_known_component_adds_owner(self):
        base = {"qa": "Review tests.", "developer": "Fix bug."}
        owner_map = {"auth": {"team": "identity", "qa": "alice", "developer": "bob"}}
        result = enrich_with_ownership(base, "auth", owner_map)
        assert "@alice" in result["qa"]
        assert "@bob" in result["developer"]

    def test_unknown_component_uses_default(self):
        base = {"qa": "Review tests."}
        owner_map = {"default": {"team": "platform"}}
        result = enrich_with_ownership(base, "unknown-svc", owner_map)
        assert "[platform]" in result["qa"]

    def test_no_map_returns_unchanged(self):
        base = {"qa": "Review tests."}
        result = enrich_with_ownership(base, "auth", None)
        assert result == base

    def test_empty_map_returns_unchanged(self):
        base = {"qa": "Review tests."}
        result = enrich_with_ownership(base, "auth", {})
        assert result == base

    def test_team_fallback_when_no_role_owner(self):
        base = {"sre": "Check infra."}
        owner_map = {"auth": {"team": "identity"}}
        result = enrich_with_ownership(base, "auth", owner_map)
        assert "[identity]" in result["sre"]


class TestMergeRoleActions:
    def test_later_overrides_earlier(self):
        a = {"qa": "first", "developer": "first"}
        b = {"qa": "second"}
        result = merge_role_actions(a, b)
        assert result["qa"] == "second"
        assert result["developer"] == "first"

    def test_empty_string_does_not_override(self):
        a = {"qa": "good action"}
        b = {"qa": ""}
        result = merge_role_actions(a, b)
        assert result["qa"] == "good action"

    def test_empty_dicts(self):
        result = merge_role_actions({}, {})
        assert result == {}

    def test_three_way_merge(self):
        a = {"qa": "a", "developer": "a"}
        b = {"developer": "b", "sre": "b"}
        c = {"release_manager": "c"}
        result = merge_role_actions(a, b, c)
        assert result["qa"] == "a"
        assert result["developer"] == "b"
        assert result["sre"] == "b"
        assert result["release_manager"] == "c"
