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
    sig = inspect.signature(analytics.flaky_scores)
    assert "release_id" in sig.parameters
    assert sig.parameters["release_id"].default is not None, (
        "must be a Query(...) with a None default, never required"
    )


def test_the_release_filter_intersects_rather_than_rescoping():
    """It restricts WHICH scored tests appear, and touches no score.

    A rescope would mean recomputing per release, which is the design this
    slice rejected — so the filter must be a membership predicate over
    fingerprints, not anything that reaches the score itself.
    """
    body = _code_only(analytics.flaky_scores)
    assert "FlakyScore.test_fingerprint.in_(" in body, (
        "the release filter must select fingerprints, not recompute scores"
    )
    # It reaches the release through the run, which is the only path that
    # exists — flaky_score itself has no release dimension.
    assert "TestRun.primary_release_id" in body
    assert "TestCase.test_fingerprint" in body


def test_the_intersection_is_project_scoped_on_both_sides():
    """The subquery must carry the project filter too.

    Without it, a release id from another tenant would still select
    fingerprints — and because fingerprints are not salted per project, a
    collision would surface another project's flaky tests. The outer query's
    project filter would not save it: the IN list is what selects rows.
    """
    body = _code_only(analytics.flaky_scores)
    sub = body[body.index("ran_in_release"):body.index("stmt = stmt.where")]
    assert "TestRun.project_id == scoped" in sub, (
        "the fingerprint subquery must be tenant-scoped independently"
    )


def test_the_release_is_guarded_before_it_reaches_the_query():
    """Same per-id guard as S4a — the ratchet cannot see this one either.

    /flaky-scores already carries a required project_id, so the architectural
    authorization ratchet is satisfied by that alone and would pass this route
    with the release entirely unchecked.
    """
    body = _code_only(analytics.flaky_scores)
    assert "resolve_release_query_scope(db, release_id, current_user)" in body
    guard_at = body.index("resolve_release_query_scope")
    use_at = body.index("TestRun.primary_release_id")
    assert guard_at < use_at, "the release must be validated before it is queried"


def test_no_release_means_no_subquery_and_no_change():
    """NFR1 again: the unfiltered path must behave exactly as before.

    The intersection is inside an ``if``, so a caller who passes no release
    gets the same statement the endpoint has always built.
    """
    body = _code_only(analytics.flaky_scores)
    assert "if release_id is not None:" in body
    guard_at = body.index("if release_id is not None:")
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
    assert 'if release_id else None' in scope
    assert '"membership": "release" if release_id else "project"' in scope


def test_the_note_explains_the_evidence_floor_not_just_the_mechanic():
    """A reader who knows WHY sees that this is a deliberate design, not a gap.

    "Scores are project-wide" alone reads like an unfinished feature. Naming
    the evidence floor makes it a decision somebody can argue with.
    """
    body = _code_only(analytics.flaky_scores)
    note = _prose(body[body.index('"note"'):])
    assert "evidence floor" in note
    assert "not how flaky they were during it" in note
