"""Regression: policy resolution merges key by key (S7b).

The defect
----------
``resolve_effective_policy`` picks ONE document and discards the rest — project
if there is one, else system, else hardcoded. Winner takes all.

That breaks the moment a project changes one thing. A project policy setting a
single threshold silently drops every other key the system default carried, and
those keys do NOT fall back to the system default: they fall through to the
hardcoded constants, because the system document is never consulted again. A
team tightening one number inherits a policy they never wrote for everything
else, with nothing reporting the substitution.

S6b adds a third scope, where the same shape would let a phase policy discard a
project's entire document.

What is guarded here
--------------------
Narrower scopes override BY KEY. Siblings survive. Ordered lists are replaced
wholesale rather than concatenated, because a project that removed a rule must
not find it still firing. And every key records which scope supplied it, because
"why did this release fail the gate?" is only answerable if you can say which
threshold applied and where it came from.
"""
from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

from app.services import policy_resolution as pr


class TestMergingKeepsSiblings:
    def test_a_narrower_scope_overrides_only_the_keys_it_names(self):
        merged, _ = pr.merge_documents([
            ("system", {"thresholds": {"go": 90, "no_go": 50}, "hard_caps": {"max_broken": 3}}),
            ("project", {"thresholds": {"go": 95}}),
        ])

        # The whole point. Under winner-takes-all, `no_go` and `hard_caps`
        # vanish and fall through to hardcoded constants nobody chose.
        assert merged["thresholds"] == {"go": 95, "no_go": 50}
        assert merged["hard_caps"] == {"max_broken": 3}

    def test_a_key_absent_everywhere_narrow_survives_from_the_broadest_layer(self):
        merged, _ = pr.merge_documents([
            ("hardcoded", {"thresholds": {"go": 80}, "schema_version": 1}),
            ("system", {"thresholds": {"go": 90}}),
            ("project", {"thresholds": {"go": 95}}),
        ])

        assert merged["schema_version"] == 1
        assert merged["thresholds"]["go"] == 95

    def test_layers_apply_broadest_first(self):
        merged, _ = pr.merge_documents([
            ("hardcoded", {"t": {"x": 1}}),
            ("system", {"t": {"x": 2}}),
            ("project", {"t": {"x": 3}}),
        ])

        # Narrowest wins. Reversing the order would make the hardcoded default
        # beat everything a human configured.
        assert merged["t"]["x"] == 3

    def test_an_empty_layer_changes_nothing(self):
        merged, _ = pr.merge_documents([
            ("system", {"thresholds": {"go": 90}}),
            ("project", {}),
        ])

        # A project with a policy row but an empty document must not blank the
        # system default.
        assert merged["thresholds"] == {"go": 90}


class TestOrderedListsAreReplacedNotConcatenated:
    def test_rules_are_taken_wholesale_from_the_narrowest_scope(self):
        merged, _ = pr.merge_documents([
            ("system", {"rules": [{"id": "a"}, {"id": "b"}]}),
            ("project", {"rules": [{"id": "c"}]}),
        ])

        # Concatenating would apply BOTH, so a project that removed a rule
        # would find it still firing and could never turn one off.
        assert merged["rules"] == [{"id": "c"}]

    def test_a_scope_that_omits_rules_inherits_them(self):
        merged, _ = pr.merge_documents([
            ("system", {"rules": [{"id": "a"}]}),
            ("project", {"thresholds": {"go": 95}}),
        ])

        assert merged["rules"] == [{"id": "a"}]


class TestProvenanceIsRecordedPerKey:
    def test_each_key_records_the_scope_that_supplied_it(self):
        _, sources = pr.merge_documents([
            ("system", {"thresholds": {"go": 90, "no_go": 50}, "hard_caps": {"max": 3}}),
            ("project", {"thresholds": {"go": 95}}),
        ])

        # A single level name would be true of the document and false of most
        # of its contents: here `no_go` and `hard_caps` are the SYSTEM's even
        # though the effective level is "project".
        assert sources["thresholds.go"] == "project"
        assert sources["thresholds.no_go"] == "system"
        assert sources["hard_caps"] == "system"

    def test_provenance_survives_three_layers(self):
        _, sources = pr.merge_documents([
            ("hardcoded", {"t": {"a": 1, "b": 1, "c": 1}}),
            ("system", {"t": {"b": 2, "c": 2}}),
            ("project", {"t": {"c": 3}}),
        ])

        assert sources["t.a"] == "hardcoded"
        assert sources["t.b"] == "system"
        assert sources["t.c"] == "project"


class TestPhaseIsAlreadyASupportedScope:
    def test_a_phase_layer_merges_like_any_other(self):
        # S6b adds a ROW to SCOPES rather than a fourth branch in a chain of
        # if-statements — which is how the resolver being replaced became
        # winner-takes-all in the first place.
        merged, sources = pr.merge_documents([
            ("system", {"t": {"a": 1, "b": 1}}),
            ("project", {"t": {"b": 2}}),
            ("phase", {"t": {"b": 3}}),
        ])

        assert merged["t"] == {"a": 1, "b": 3}
        assert sources["t.b"] == "phase"

    def test_phase_is_declared_narrower_than_project(self):
        assert pr.SCOPES.index("phase") > pr.SCOPES.index("project")
        assert pr.SCOPES.index("project") > pr.SCOPES.index("system")
        assert pr.SCOPES.index("system") > pr.SCOPES.index("hardcoded")


class TestResolutionReadsTheRightRows:
    @staticmethod
    def _session(rows_by_sql):
        class _Result:
            def __init__(self, row):
                self._row = row

            def scalar_one_or_none(self):
                return self._row

        class _Session:
            def __init__(self):
                self.seen = []

            async def execute(self, stmt, *a, **kw):
                sql = " ".join(str(stmt).split())
                self.seen.append(sql)
                for needle, row in rows_by_sql.items():
                    if needle in sql:
                        return _Result(row)
                return _Result(None)

        return _Session()

    def test_the_system_default_is_matched_with_is_null(self):
        session = self._session({})
        asyncio.run(pr.resolve_policy_layers(session, None))

        # `project_id == None` is never true in SQL, so the plain comparison
        # returns no system default and EVERY project silently falls through to
        # hardcoded constants.
        assert any("project_id IS NULL" in s for s in session.seen)

    def test_resolution_is_deterministic_when_two_rows_are_active(self):
        session = self._session({})
        asyncio.run(pr.resolve_policy_layers(session, None))

        # `is_active` has no DB-level single-active guarantee, so a concurrent
        # publish can leave two active rows. Without the ordering the winner is
        # an arbitrary row from an unordered LIMIT 1.
        assert all("ORDER BY" in s and "version DESC" in s for s in session.seen)

    def test_both_scopes_are_consulted_not_just_the_narrowest(self):
        policy = SimpleNamespace(rules={"t": {"a": 1}}, project_id=uuid.uuid4())
        session = self._session({"release_gate_policies": policy})

        asyncio.run(pr.resolve_policy_layers(session, uuid.uuid4()))

        # Two queries: system AND project. A resolver that stopped at the first
        # match is precisely the lossy behaviour being replaced.
        assert len(session.seen) == 2


class TestTheEffectiveDocumentReportsItsOwnDerivation:
    def test_it_returns_the_document_its_sources_and_its_layers(self):
        session = TestResolutionReadsTheRightRows._session({})

        result = asyncio.run(
            pr.resolve_effective_document(session, None, hardcoded={"t": {"a": 1}})
        )

        assert result["document"]["t"]["a"] == 1
        assert result["sources"]["t.a"] == "hardcoded"
        assert result["layers"] == ["hardcoded"]
        assert result["effective_level"] == "hardcoded"

    def test_no_layers_at_all_is_reported_as_hardcoded_not_as_an_error(self):
        session = TestResolutionReadsTheRightRows._session({})

        result = asyncio.run(pr.resolve_effective_document(session, None))

        # A deployment with no policies configured is normal, not broken.
        assert result["effective_level"] == "hardcoded"
        assert result["document"] == {}


class TestWholesaleKeysAreNotHalfInherited:
    def test_a_dict_valued_wholesale_key_is_taken_whole(self):
        merged, sources = pr.merge_documents([
            ("system", {"kind_rules": {"enabled": True, "block_on": ["flaky"], "min": 3}}),
            ("project", {"kind_rules": {"enabled": False}}),
        ])

        # NOT merged. These keys are a set, not independent settings: inheriting
        # `block_on` from the system while the project has disabled the feature
        # produces a combination nobody configured.
        assert merged["kind_rules"] == {"enabled": False}
        assert sources["kind_rules"] == "project"

    def test_a_non_wholesale_dict_still_merges(self):
        # The contrast that makes the distinction real rather than decorative.
        merged, _ = pr.merge_documents([
            ("system", {"thresholds": {"go": 90, "no_go": 50}}),
            ("project", {"thresholds": {"go": 95}}),
        ])

        assert merged["thresholds"] == {"go": 95, "no_go": 50}

    def test_an_ordered_list_is_replaced_whatever_the_constant_says(self):
        # A list never reaches the nested merge, so replacement does not depend
        # on membership of REPLACE_WHOLESALE. Pinned so a future change to the
        # merge cannot start concatenating rules without failing here.
        merged, _ = pr.merge_documents([
            ("system", {"some_list": [1, 2]}),
            ("project", {"some_list": [3]}),
        ])

        assert merged["some_list"] == [3]
