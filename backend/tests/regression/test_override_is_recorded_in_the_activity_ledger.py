"""A release override must reach the activity ledger, not only its own row.

TL-2026-09-19-01-009, measured live. The override itself was always correct and
its in-row audit is good — an immutable ``original_recommendation`` plus an
append-only entry carrying actor, timestamp, before/after and reason.

But it was audited ONLY there. A real override on the homelab wrote zero rows to
``project_activity_events`` and zero to ``access_audit_logs``, while the catalog
had declared an event for exactly this all along::

    _spec("release.decision_overridden", "release", "release",
          "Release decision for {entity_label} overridden to {recommendation}",
          "attempt")

Nothing emitted it. Eleven other routers record activity — including the review
accept path, for a far less consequential act. A QA Lead reversing the product's
own ship recommendation is the action most likely to be asked about later, and
it was the one release action missing from the ledger.
"""
from __future__ import annotations

import inspect


class TestTheCatalogEntryHasAProducer:
    def test_the_override_handler_records_the_declared_event(self):
        from app.routers import release_readiness

        src = inspect.getsource(release_readiness.override_release_decision)
        assert "record_activity" in src, "the override still writes no activity"
        assert '"release.decision_overridden"' in src, (
            "the handler records some other event than the one the catalog declares"
        )
    def test_the_event_is_declared_and_resolvable(self):
        # Use the public accessor rather than the registry container: the
        # producer and the catalog have to agree on the exact name, and
        # ``lookup`` is what the recorder itself calls.
        from app.services.activity.events import lookup

        spec = lookup("release.decision_overridden")
        assert spec is not None
        assert "{recommendation}" in spec.summary_template, (
            "the template no longer interpolates the verdict the producer sends"
        )

    def test_it_carries_the_slot_the_template_interpolates(self):
        # The catalog template is "...overridden to {recommendation}", so a
        # context without that key renders a placeholder into the ledger.
        from app.routers import release_readiness

        src = inspect.getsource(release_readiness.override_release_decision)
        assert '"recommendation": council.recommendation' in src

    def test_it_is_staged_before_the_commit_not_after(self):
        """An override that rolls back must not leave a ledger entry.

        ``record`` stages; the router owns the commit. Recording after the
        commit would put the entry in a different transaction from the write it
        describes.
        """
        from app.routers import release_readiness

        src = inspect.getsource(release_readiness.override_release_decision)
        record_at = src.index("record_activity(")
        commit_at = src.index("await db.commit()")
        assert record_at < commit_at, (
            "the activity record is written after the commit; a rolled-back "
            "override would still be announced"
        )

    def test_a_failing_record_does_not_break_the_committed_override(self):
        # The override is the user's decision and has already been applied in
        # this transaction; a ledger problem must not turn it into a 500.
        from app.routers import release_readiness

        src = inspect.getsource(release_readiness.override_release_decision)
        tail = src[src.index("record_activity("):]
        assert "except Exception" in tail


class TestTheBeforeAndAfterAreRecorded:
    def test_the_entry_names_what_changed(self):
        from app.routers import release_readiness

        src = inspect.getsource(release_readiness.override_release_decision)
        assert "before=" in src and "after=" in src
        assert "changed_fields=" in src
