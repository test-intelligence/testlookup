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
