"""Regression: flaky tests *in* a release, by intersection (S4b).

Why this is an intersection and not a release-scoped statistic
--------------------------------------------------------------
The epic originally specified giving ``flaky_score`` a release column and
recomputing per release. That does not survive contact with the scorer:
``flaky_score_service`` refuses to emit a score below ``MIN_OBSERVATIONS`` (5)
observations of the same test, and only calls confidence "high" at 20+. A
rolling 30-day window clears that easily; one release — a three-day hotfix, a
short RC cycle — frequently does not. Per-release rows would mostly carry
``score=None, confidence="none"``, so the table would multiply in size while
answering "no data" more often than it answered.

The question users actually ask — "which flaky tests are affecting 2.4.0" — is
an intersection: project-wide scores ∩ tests that ran in that release. Every
key for it already existed.

The property that needs guarding
--------------------------------
The result is **mixed-scope**: membership is release-scoped, the score is not.
A filtered list looks release-scoped, so a reader could take ``score`` as "how
flaky during 2.4.0" and be wrong, with nothing in a bare list to correct them.
That is what the ``scope`` block exists for, and most of these tests are about
it rather than about the filtering.
"""
from __future__ import annotations

import inspect

from app.routers import analytics


def _prose(text: str) -> str:
    """Collapse adjacent string literals into readable prose.

    A long message in source is a run of concatenated literals, so any phrase
    spanning a line break is split by ``" "`` boundaries and a naive substring
    check fails on correct text. Strip the quotes and collapse whitespace so
    the assertion reads the sentence the user will see.
    """
    import re

    return re.sub(r"\s+", " ", text.replace('"', "")).strip()


def _code_only(fn) -> str:
    """Source minus the docstring.

    These functions describe the wrong designs by name while explaining why
    they were rejected, so a substring check over raw source matches the
    explanation rather than the code.
    """
    src = inspect.getsource(fn)
    doc = fn.__doc__
    return src.replace(doc, "", 1) if doc else src


# ── The intersection ─────────────────────────────────────────────────────────


def test_flaky_scores_accepts_an_optional_release():
    # VIZ-201: the release arrives through the shared scope dependency.
    from app.services.analytics_scope import analytics_scope

    sig = inspect.signature(analytics_scope(analytics._FLAKY_SCORES))
    assert "release_id" in sig.parameters
    default = sig.parameters["release_id"].default
    assert default is not None and default.default is None, (
        "must be a Query(...) with a None default, never required"
    )


def test_the_release_filter_intersects_rather_than_rescoping():
    """It restricts WHICH scored tests appear, and touches no score.

    A rescope would mean recomputing per release, which is the design this
    slice rejected — so the filter must be a membership predicate over
    fingerprints, not anything that reaches the score itself.
    """
    body = _code_only(analytics.flaky_scores)
    assert "FlakyScore.test_fingerprint.in_(_ran_in_scope(scoped, scope))" in body, (
        "the release filter must select fingerprints, not recompute scores"
    )
    # It reaches the release through the run, which is the only path that
    # exists — flaky_score itself has no release dimension. The predicate is
    # the shared one (``release_filter.release_predicate``) on TestRun. Since
    # VIZ-202 the membership subquery is shared with systemic clusters.
    ran = _code_only(analytics._ran_in_scope)
    assert "_release_predicate(scope.release_arg)" in ran
    assert "select(TestCase.test_fingerprint)" in ran
    from app.core import release_filter

    assert "model.primary_release_id" in inspect.getsource(release_filter.release_predicate)


def test_the_intersection_is_project_scoped_on_both_sides():
    """The subquery must carry the project filter too.

    Without it, a release id from another tenant would still select
    fingerprints — and because fingerprints are not salted per project, a
    collision would surface another project's flaky tests. The outer query's
    project filter would not save it: the IN list is what selects rows.
    """
    body = _code_only(analytics.flaky_scores)
    # The subquery is built for the handler's own pinned project...
    assert "_ran_in_scope(scoped, scope)" in body
    # ...and carries that pin itself.
    assert "TestRun.project_id == project_id" in _code_only(analytics._ran_in_scope), (
        "the fingerprint subquery must be tenant-scoped independently"
    )


def test_the_release_is_guarded_before_it_reaches_the_query():
    """Same per-id guard as S4a — the ratchet cannot see this one either.

    /flaky-scores already carries a required project_id, so the architectural
    authorization ratchet is satisfied by that alone and would pass this route
    with the release entirely unchecked.
    """
    # VIZ-201: the guard is the scope dependency, which resolves EVERY
    # release id before the handler body -- and so the query -- runs.
    body = _code_only(analytics.flaky_scores)
    assert "Depends(analytics_scope(_FLAKY_SCORES))" in body
    assert "release_id = scope.release_arg" in body
    from app.services import analytics_scope

    authorize = inspect.getsource(analytics_scope.authorize_scope)
    assert "resolve_release_query_scopes(db, request.release_ids, user)" in authorize


def test_no_release_means_no_subquery_and_no_change():
    """NFR1 again: the unfiltered path must behave exactly as before.

    The intersection is inside an ``if``, so a caller who passes no release
    gets the same statement the endpoint has always built.
    """
    body = _code_only(analytics.flaky_scores)
    # VIZ-202: a suite filter selects members the same way.
    guard = "if release_id is not None or scope.suite_names:"
    assert guard in body
    guard_at = body.index(guard)
    assert body.index("FlakyScore.test_fingerprint.in_(") > guard_at, (
        "the subquery must be conditional, not always applied"
    )


# ── Mixed scope, stated ──────────────────────────────────────────────────────


def test_the_response_declares_what_each_number_is_scoped_to():
    """The honesty property, and the reason this slice is not just a filter.

    Membership is release-scoped; the score is not. A filtered list looks
    release-scoped, so without this a reader would reasonably take ``score`` as
    "how flaky during 2.4.0" — a wrong reading that nothing else in the payload
    contradicts.
    """
    body = _code_only(analytics.flaky_scores)
    assert '"scope"' in body
    assert '"membership"' in body
    assert '"score": "project_window"' in body, (
        "the payload must say the score is NOT release-scoped"
    )


def test_the_scope_note_appears_only_when_a_release_was_asked_for():
    """No note on the unfiltered path.

    An explanation of a filter nobody applied is noise, and it would also mean
    the unfiltered response changed shape — which NFR1 forbids in substance
    even where the key is additive.
    """
    body = _code_only(analytics.flaky_scores)
    scope = body[body.index('"scope"'):]
    # The release-only note keeps its wording; any other filter gets the
    # shared membership note, which is None when nothing was filtered.
    assert "if release_id and not scope.suite_names else" in scope
    from app.services.analytics_scope import AnalyticsScope

    unfiltered = AnalyticsScope(None, None, (), (), None)
    assert analytics._membership_note(unfiltered, "Scores") == {
        "membership": "project", "note": None,
    }
    release_only = AnalyticsScope(None, None, ("r",), (), None)
    assert analytics._membership_note(release_only, "Scores")["membership"] == "release"
    both = AnalyticsScope(None, None, ("r",), ("s",), None)
    note = analytics._membership_note(both, "Scores")
    assert note["membership"] == "release+suite" and "NOT recomputed" in note["note"]


def test_the_note_explains_the_evidence_floor_not_just_the_mechanic():
    """A reader who knows WHY sees that this is a deliberate design, not a gap.

    "Scores are project-wide" alone reads like an unfinished feature. Naming
    the evidence floor makes it a decision somebody can argue with.
    """
    body = _code_only(analytics.flaky_scores)
    note = _prose(body[body.index('"note"'):])
    assert "evidence floor" in note
    assert "not how flaky they were during it" in note
