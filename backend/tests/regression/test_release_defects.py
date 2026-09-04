"""Regression: which defects block a release (S7a).

A defect has TWO relationships to a release, and gating on either one alone is
wrong — in opposite directions at the same time.

``release_id`` is where it was FOUND. Gate on that alone and every INHERITED
defect disappears: a defect found in 2.3.0 and still open does not show up when
you ask 2.4.0 what is blocking it, so the release ships over a known open bug.

``affects_releases`` is which releases it IMPACTS. Gate on that alone and every
untriaged defect disappears, because the column is NULL until somebody asserts
something — and a gate that stops blocking the moment a field is left blank is
not a gate.

So: affects-if-asserted, found-in as the fallback. NULL means "not triaged", not
"harmless".

The second theme is severity. "No open CRITICALs" reads as a strict criterion
and is a lenient one: severity is set by whoever filed the defect, is often
absent on machine-created rows, and a NULL severity is not a low one. Unrated
defects are reported rather than dropped, so the gate cannot be passed by
leaving a field empty.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

from app.services import release_defect_service as svc

REL_CURRENT = "rel-2.4.0"
REL_PREVIOUS = "rel-2.3.0"


def _defect(severity="CRITICAL", found_in=REL_CURRENT, affects=None, title="boom"):
    return SimpleNamespace(
        id=uuid.uuid4(),
        title=title,
        severity=severity,
        release_id=found_in,
        affects_releases=affects,
        resolution_status="OPEN",
    )


class TestAnInheritedDefectStillBlocks:
    def test_a_defect_found_earlier_but_affecting_this_release_blocks_it(self):
        inherited = _defect(found_in=REL_PREVIOUS, affects=[REL_PREVIOUS, REL_CURRENT])

        # The case that a found-in-only gate misses entirely: the release ships
        # over a known open bug because the defect was discovered one release
        # ago.
        assert svc._affects(inherited, REL_CURRENT) is True

    def test_a_defect_found_earlier_and_not_affecting_this_one_does_not(self):
        fixed_before_branch = _defect(found_in=REL_PREVIOUS, affects=[REL_PREVIOUS])

        # The mirror. Gating on found-in plus "still open" would block 2.4.0 for
        # something already fixed before it branched.
        assert svc._affects(fixed_before_branch, REL_CURRENT) is False


class TestAnUntriagedDefectIsNotAHarmlessOne:
    def test_a_defect_with_no_asserted_impact_falls_back_to_where_it_was_found(self):
        untriaged = _defect(found_in=REL_CURRENT, affects=None)

        # Without the fallback, the gate stops blocking the moment somebody
        # forgets to fill in a field.
        assert svc._affects(untriaged, REL_CURRENT) is True

    def test_an_empty_list_is_treated_as_unasserted_not_as_affects_nothing(self):
        # `[]` is what a form submits when nobody picked anything. Reading it as
        # "affects no releases" would silently unblock every release.
        empty = _defect(found_in=REL_CURRENT, affects=[])

        assert svc._affects(empty, REL_CURRENT) is True

    def test_asserted_impact_wins_over_where_it_was_found(self):
        # A human said this defect does not affect the current release. That
        # assertion beats the inference.
        triaged = _defect(found_in=REL_CURRENT, affects=[REL_PREVIOUS])

        assert svc._affects(triaged, REL_CURRENT) is False


class TestSeverityCannotBeGamedByOmission:
    def test_an_unrated_defect_is_reported_not_dropped(self):
        summary = svc.summarise_blocking([_defect(severity=None)])

        # A NULL severity is not a low one. Silently excluding unrated defects
        # lets a gate be passed by leaving a field blank.
        assert summary["blocking_count"] == 0
        assert summary["unrated_count"] == 1
        assert summary["fully_triaged"] is False

    def test_whitespace_is_not_a_severity(self):
        summary = svc.summarise_blocking([_defect(severity="   ")])

        assert summary["unrated_count"] == 1

    def test_severity_matching_is_case_insensitive(self):
        summary = svc.summarise_blocking([_defect(severity="critical")])

        # Severity is free text on a String column, so casing varies by writer.
        # A case-sensitive match would silently stop blocking.
        assert summary["blocking_count"] == 1

    def test_high_blocks_as_well_as_critical(self):
        summary = svc.summarise_blocking([_defect(severity="HIGH", title="checkout 500s")])

        # Only CRITICAL was tested before, so narrowing the blocking set to
        # CRITICAL alone survived the whole suite — a HIGH-severity defect would
        # have stopped blocking with nothing to notice.
        assert summary["blocking_count"] == 1
        assert svc.verdict_contribution(summary)[0] == "NO_GO"

    def test_the_blocking_set_is_exactly_critical_and_high(self):
        # Pinned as a set rather than checked one severity at a time, so adding
        # or removing a level cannot pass unnoticed.
        assert set(svc.BLOCKING_SEVERITIES) == {"CRITICAL", "HIGH"}

    def test_a_low_severity_defect_does_not_block(self):
        summary = svc.summarise_blocking([_defect(severity="LOW")])

        assert summary["blocking_count"] == 0
        assert summary["fully_triaged"] is True


class TestTheVerdictKeepsThreeOutcomesApart:
    def test_a_blocking_defect_is_no_go_with_reasons(self):
        summary = svc.summarise_blocking([_defect(severity="CRITICAL", title="login 500s")])

        verdict, reasons = svc.verdict_contribution(summary)

        assert verdict == "NO_GO"
        assert any("login 500s" in r for r in reasons)

    def test_untriaged_defects_are_not_evaluated_rather_than_go(self):
        summary = svc.summarise_blocking([_defect(severity=None)])

        verdict, reasons = svc.verdict_contribution(summary)

        # "Cannot tell" and "clear" are different claims. Reporting GO here
        # approves a release on defects nobody assessed.
        assert verdict == "NOT_EVALUATED"
        assert reasons and "no severity" in reasons[0]

    def test_untriaged_defects_are_not_blocked_either(self):
        summary = svc.summarise_blocking([_defect(severity=None)])

        verdict, _ = svc.verdict_contribution(summary)

        # And not NO_GO: blocking a release for a defect nobody has rated sends
        # somebody to fix the wrong thing.
        assert verdict != "NO_GO"

    def test_a_known_critical_outranks_untriaged_noise(self):
        summary = svc.summarise_blocking([
            _defect(severity="CRITICAL", title="real"),
            _defect(severity=None, title="unrated"),
        ])

        verdict, _ = svc.verdict_contribution(summary)

        # A known failure is the more actionable fact; reporting NOT_EVALUATED
        # would bury it.
        assert verdict == "NO_GO"

    def test_no_defects_at_all_is_go(self):
        verdict, reasons = svc.verdict_contribution(svc.summarise_blocking([]))

        assert verdict == "GO"
        assert reasons == []


class TestTheQueryIsScopedToTheProject:
    def test_the_query_pins_the_project_and_open_status(self):
        import asyncio

        seen = []

        class _Rows:
            def scalars(self):
                return self

            def all(self):
                return []

        class _Session:
            async def execute(self, stmt, *a, **kw):
                seen.append(" ".join(str(stmt).split()))
                return _Rows()

        asyncio.run(svc.blocking_defects(_Session(), REL_CURRENT, "p1"))
        where = seen[0].split(" WHERE ")[1]

        # `release_id` is already project-scoped, but nothing at the database
        # level stops a defect row referencing another project's release, and a
        # gate is the wrong place to trust that.
        assert "project_id" in where
        assert "resolution_status" in where
        # Both halves of the match must be in the query, or one of the two
        # failure directions returns.
        assert "affects_releases" in where
        assert "release_id" in where


class TestAssertingImpactIsStable:
    def test_release_ids_are_deduplicated_and_sorted(self):
        result = svc.assert_affects(_defect(), ["b", "a", "b"])

        # A column that reorders itself makes every audit diff look like a
        # change.
        assert result == ["a", "b"]

    def test_an_empty_assertion_stores_null_not_an_empty_list(self):
        # `[]` would read as "affects nothing" on the next read, which is the
        # unblock-by-accident case above.
        assert svc.assert_affects(_defect(), []) is None
