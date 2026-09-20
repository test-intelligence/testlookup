"""The two most exposed surfaces must not export a superseded verdict.

A shared report link is readable by anyone holding the token, with no login. A
compliance evidence bundle is what an auditor is handed. Both read the run
intelligence snapshot, and both used to read it with a bare
``select(RunIntelligenceSnapshot).where(run_id == ...)``:

* **no ``stale`` predicate** — so after a QA Lead's override or a defect
  promotion marked the row stale, the export kept serving the verdict as it
  stood before;
* **no ``schema_version`` predicate** — so a row at a superseded version was
  served too, which ``get_stale_snapshot`` explicitly refuses because it has the
  WRONG SHAPE: "a payload the current contract says cannot exist".

The evidence bundle additionally fell back to ``{}`` when nothing matched, so a
compliance pack could ship an empty ``release-decision.json`` with nothing
saying it was empty.

Both now go through ``intelligence_snapshot_service.get_or_compute``, which the
module docstring had advertised since it was written without ever defining it.
"""
from __future__ import annotations

import inspect
import uuid

import pytest

RUN = uuid.uuid4()


class TestNeitherExportReadsTheTableDirectly:
    """Source-level, deliberately: the defect WAS the shape of the query.

    A behavioural test that mocks the session cannot see which predicates the
    statement carried — that is precisely how this survived.
    """

    def test_report_composition_uses_the_helper(self):
        from app.services import report_composition_service as svc

        src = inspect.getsource(svc)
        assert "get_or_compute" in src
        assert "select(RunIntelligenceSnapshot)" not in src, (
            "the report still reads the snapshot table directly, so it can "
            "serve a stale or obsolete-schema payload"
        )

    def test_evidence_bundle_uses_the_helper(self):
        from app.services import evidence_bundle_service as svc

        src = inspect.getsource(svc)
        assert "get_or_compute" in src
        assert "select(RunIntelligenceSnapshot)" not in src

    def test_the_bundle_no_longer_ships_an_empty_payload_silently(self):
        from app.services import evidence_bundle_service as svc

        src = inspect.getsource(svc)
        assert "if snapshot else {}" not in src, (
            "a missing snapshot still becomes an empty compliance bundle"
        )

    def test_the_bundle_still_enforces_tenant_scope(self):
        # The old query enforced it by joining TestRun inside the select.
        # get_or_compute keys on run_id alone, so the check has to be explicit.
        from app.services import evidence_bundle_service as svc

        src = inspect.getsource(svc)
        assert "does not belong to project" in src
        assert "TestRun.project_id" in src or "owning_project" in src


class TestGetOrComputeRefusesWhatCannotBeServed:
    @pytest.mark.asyncio
    async def test_a_fresh_snapshot_is_returned_without_recomputing(self, monkeypatch):
        from app.services import intelligence_snapshot_service as svc

        async def _cached(db, run_id):
            return {"fresh": True}

        called = []

        async def _never(*a, **k):
            called.append(1)
            return {}

        monkeypatch.setattr(svc, "get_cached_snapshot", _cached)
        monkeypatch.setattr(
            "app.services.run_intelligence_service.get_run_intelligence", _never
        )
        assert await svc.get_or_compute(None, RUN) == {"fresh": True}
        assert called == [], "a fresh snapshot must not trigger a recompute"

    @pytest.mark.asyncio
    async def test_a_stale_or_obsolete_row_triggers_a_recompute(self, monkeypatch):
        """``get_cached_snapshot`` returns None for BOTH cases.

        It filters ``stale.is_(False)`` and ``schema_version >= CURRENT``, so
        the helper cannot tell the two apart and does not need to: neither is
        servable, and both must be replaced.
        """
        from app.services import intelligence_snapshot_service as svc

        async def _none(db, run_id):
            return None

        async def _recomputed(run_id, db, mongo, **k):
            return {"recomputed": True, "provenance": {"fallback_used": False}}

        saved = {}

        async def _save(db, run_id, payload, fallback_used=False):
            saved["payload"] = payload

        monkeypatch.setattr(svc, "get_cached_snapshot", _none)
        monkeypatch.setattr(svc, "save_snapshot", _save)
        monkeypatch.setattr("app.db.mongo.get_mongo_db", lambda: None)
        monkeypatch.setattr(
            "app.services.run_intelligence_service.get_run_intelligence", _recomputed
        )

        out = await svc.get_or_compute(None, RUN)
        assert out == {"recomputed": True, "provenance": {"fallback_used": False}}

    @pytest.mark.asyncio
    async def test_a_failed_recompute_propagates_rather_than_serving_stale(self, monkeypatch):
        # Falling back to the stale row here would reinstate the defect.
        from app.services import intelligence_snapshot_service as svc

        async def _none(db, run_id):
            return None

        async def _boom(run_id, db, mongo, **k):
            raise RuntimeError("mongo down")

        monkeypatch.setattr(svc, "get_cached_snapshot", _none)
        monkeypatch.setattr("app.db.mongo.get_mongo_db", lambda: None)
        monkeypatch.setattr(
            "app.services.run_intelligence_service.get_run_intelligence", _boom
        )

        with pytest.raises(RuntimeError):
            await svc.get_or_compute(None, RUN)

    @pytest.mark.asyncio
    async def test_a_failed_CACHE_write_does_not_fail_the_export(self, monkeypatch):
        # Caching is best-effort; the payload in hand is already current.
        from app.services import intelligence_snapshot_service as svc

        async def _none(db, run_id):
            return None

        async def _recomputed(run_id, db, mongo, **k):
            return {"recomputed": True}

        async def _save_boom(*a, **k):
            raise RuntimeError("write failed")

        monkeypatch.setattr(svc, "get_cached_snapshot", _none)
        monkeypatch.setattr(svc, "save_snapshot", _save_boom)
        monkeypatch.setattr("app.db.mongo.get_mongo_db", lambda: None)
        monkeypatch.setattr(
            "app.services.run_intelligence_service.get_run_intelligence", _recomputed
        )

        assert await svc.get_or_compute(None, RUN) == {"recomputed": True}


class TestTheModuleDocstringIsNowTrue:
    def test_get_or_compute_exists(self):
        # It was advertised in the module docstring and never defined; both
        # export paths hand-rolled their own query instead.
        from app.services import intelligence_snapshot_service as svc

        assert callable(svc.get_or_compute)
