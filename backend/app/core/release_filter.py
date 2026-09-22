"""The release-filter vocabulary shared by the routers and the services.

One sentinel, defined once. The alternative — a literal ``"unattributed"``
inlined at each of the places that has to recognise it — is the shape the
``frontend.all-projects-literal`` gate exists to prevent, and the same class as
the status-enum drift that made ``"quarantined"`` match nothing forever: a
producer and a consumer spelling the same concept differently, with no error
when they disagree, just a filter that quietly matches no rows.

Lives in ``core`` rather than ``services`` so ``core.deps`` can import it
without creating a core -> services dependency.
"""
from __future__ import annotations

#: Asks for runs that no release claims — ``primary_release_id IS NULL``.
#:
#: Every project has an active release and the attribution ladder always lands
#: somewhere, so in a healthy system this bucket is EMPTY. That is what makes it
#: worth having: a run reaches it when the linker failed and swallowed the error,
#: which is otherwise invisible. It is an operational view, not a routine filter.
#:
#: Deliberately neither a UUID nor empty. Empty already means "no filter", and a
#: UUID-shaped sentinel would be indistinguishable from a real release id in a
#: log line, a saved view, or a shared link.
UNATTRIBUTED = "unattributed"


def is_unattributed(release_id: str | None) -> bool:
    """True when the caller asked for the no-release bucket.

    Compares case-insensitively. This value travels through query strings,
    saved-view JSON and shared URLs, where casing is not reliably preserved, and
    a near miss would fall through to being parsed as a UUID and rejected as
    malformed — a 422 for what was really a capitalisation difference.
    """
    return isinstance(release_id, str) and release_id.strip().lower() == UNATTRIBUTED


def release_predicate(release_id, model=None) -> list:
    """The release rule for a SQLAlchemy Core ``.where()``, as a splat-able list.

    ``[]`` when no release is asked for, so the compiled SQL is byte-identical
    to before the axis existed. That is NFR1, expressed the only way a Core
    select can express it — the raw-SQL side achieves the same thing by
    appending an empty fragment, and both exist so the planner keeps using
    ``ix_test_runs_project_release_created`` for the callers who never asked
    for a release.

    Lives here, with the sentinel, because the alternative is what this module's
    own docstring warns about: several surfaces each spelling the same rule,
    with no error when they disagree — just filters that quietly disagree about
    what a release contains. ``summary_report_service`` had its own copy for
    exactly one commit.

    ``model`` defaults to ``TestRun`` and is imported lazily: this module lives
    in ``core`` so that ``core.deps`` can use it without creating a
    core -> models dependency at import time.
    """
    import uuid as _uuid

    # VIZ-201: a sequence of ids is OR within the dimension. Empty = no filter
    # and one item = exactly the single-value predicate below, so a legacy
    # single-value call builds the statement it always built.
    if release_id is not None and not isinstance(release_id, (str, _uuid.UUID)):
        ids = list(dict.fromkeys(str(item) for item in release_id))
        if not ids:
            return []
        if len(ids) == 1:
            return release_predicate(ids[0], model)
        if model is None:
            from app.models.postgres import TestRun

            model = TestRun
        from sqlalchemy import or_

        real = [_uuid.UUID(item) for item in ids if not is_unattributed(item)]
        if len(real) == len(ids):
            return [model.primary_release_id.in_(real)]
        if not real:
            return [model.primary_release_id.is_(None)]
        return [or_(model.primary_release_id.in_(real), model.primary_release_id.is_(None))]

    if release_id is None:
        return []
    if model is None:
        from app.models.postgres import TestRun

        model = TestRun
    if is_unattributed(str(release_id)):
        return [model.primary_release_id.is_(None)]

    return [model.primary_release_id == _uuid.UUID(str(release_id))]
