"""The retention preview must report every class the purge deletes.

Found on the live deployment: the preview returned EIGHT candidate counts
while ``run_purge`` computes TWELVE.

    live response : audit_rows, compliance_packs_expired, event_archive_rows,
                    minio_objects, mongo_docs, provenance_rows, runs,
                    test_cases
    service returns: + evidence_artifact_rows, analysis_cache_entries,
                       memory_entries_expired, search_index_documents

The handler returns the service's dict unchanged. The loss happens in FastAPI:
``response_model=RetentionPreviewResponse`` filters the payload down to the
fields ``RetentionPreviewCandidates`` declares, and anything else is dropped
**silently** — no error, no warning, just a smaller object.

Why it matters: this preview is a dry run an ADMIN authorises an irreversible
cross-store purge from. Four classes of data — evidence artifacts, analysis
caches, agent-memory entries and search-index documents — were deleted by
execute without ever appearing in the dry run.

Same class the loop keeps finding: a value published that no code path
consumes. Here the producer is the service, and the consumer that quietly
drops it is the response contract itself.

**Why the existing coverage missed it.** ``test_retention_purge`` asserts the
key set on ``run_purge``'s return value — the SERVICE — and it passes, because
the service is right. Nothing asserted what survives serialisation. So this
test drives the model, not the function.
"""
from __future__ import annotations

import inspect

import pytest

pytest.importorskip("pydantic")

from app.models.schemas import RetentionPreviewCandidates  # noqa: E402
from app.services import retention_service  # noqa: E402

pytestmark = pytest.mark.regression


def _counted_classes() -> set[str]:
    """The candidate keys ``run_purge`` builds for mode="preview".

    Read from source rather than executed: ``run_purge`` needs a live session
    and external stores, and stubbing those would let the test drift from the
    real dict.
    """
    source = inspect.getsource(retention_service.run_purge)
    body = source.split("candidates = {", 1)[1].split("return {", 1)[0]
    keys = set()
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith('"') and '":' in stripped:
            keys.add(stripped.split('"')[1])
    return keys


def test_every_counted_class_survives_serialisation():
    """The response model must not be narrower than the service."""
    counted = _counted_classes()
    declared = set(RetentionPreviewCandidates.model_fields)

    assert counted, "could not read the candidate keys — the test proved nothing"
    dropped = counted - declared
    assert not dropped, (
        "run_purge counts these classes but the response model does not declare "
        f"them, so FastAPI drops them from the preview silently: {sorted(dropped)}. "
        "An admin authorises an irreversible purge from this dry run."
    )


def test_the_model_does_not_promise_classes_the_purge_never_counts():
    """The mirror direction: a field with no producer renders a permanent 0
    and reads as 'nothing of this kind will be deleted'."""
    counted = _counted_classes()
    declared = set(RetentionPreviewCandidates.model_fields)

    orphans = declared - counted
    assert not orphans, (
        f"the preview promises classes run_purge never counts: {sorted(orphans)} — "
        "each renders a constant 0, which reads as 'none will be deleted'"
    )


def test_the_four_classes_that_were_being_dropped_are_declared():
    """Named explicitly so a future narrowing of the model fails loudly here
    rather than only in the set-difference assertion above."""
    declared = set(RetentionPreviewCandidates.model_fields)
    for field in (
        "evidence_artifact_rows",
        "analysis_cache_entries",
        "memory_entries_expired",
        "search_index_documents",
    ):
        assert field in declared, f"{field} is counted by the purge but not reported"


def test_the_counts_are_integers_not_optional():
    """A nullable count would let 'not measured' and 'zero' look identical in
    the response — the distinction retention needs most."""
    for name, field in RetentionPreviewCandidates.model_fields.items():
        if name == "mongo_docs":
            continue
        assert field.annotation is int, (
            f"{name} should be a plain int; a nullable count makes "
            "'not measured' indistinguishable from 'nothing to delete'"
        )
