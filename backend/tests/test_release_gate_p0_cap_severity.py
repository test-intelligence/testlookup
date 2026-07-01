"""Regression: the release-gate "P0" hard cap must count the severity the
defect-promotion pipeline actually writes.

Bug (2026-07): the ``max_p0_defects`` hard cap filtered ``Defect.severity ==
"P0"`` (``metrics_service`` readiness + ``release_council_service`` synth/deep
paths), but defects are only ever persisted with CRITICAL/HIGH/MEDIUM/LOW
(``defect_promotion_service._composite_to_severity`` /
``analytics_service._SEVERITY_FROM_PRIORITY``). No row ever matched, so the cap
was dead code — a release with unlimited open CRITICAL defects was never
blocked. The fix centralises the mapping in ``metrics_service.P0_DEFECT_SEVERITY``
+ ``count_open_critical_defects`` and routes every call site through it.

These tests pin the producer↔consumer vocabulary so the cap can't silently
go dead again.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest


def test_p0_cap_severity_is_canonical_critical():
    """The cap counts the same string the promotion pipeline stores."""
    from app.services.metrics_service import P0_DEFECT_SEVERITY

    assert P0_DEFECT_SEVERITY == "CRITICAL"


def test_p0_maps_to_the_severity_the_promotion_pipeline_writes():
    """A P0-priority finding must promote to exactly the severity the cap
    counts. If either side drifts, this fails before the cap goes dead."""
    from app.services.analytics_service import _SEVERITY_FROM_PRIORITY
    from app.services.defect_promotion_service import _composite_to_severity
    from app.services.metrics_service import P0_DEFECT_SEVERITY

    # The UI/policy "P0" priority maps onto the cap's severity...
    assert _SEVERITY_FROM_PRIORITY["P0"] == P0_DEFECT_SEVERITY
    # ...and a top-band composite score promotes to that same severity, so a
    # genuinely-critical finding is counted by the cap.
    assert _composite_to_severity(80.0) == P0_DEFECT_SEVERITY
    # The literal "P0" is never a stored severity value.
    assert "P0" not in set(_SEVERITY_FROM_PRIORITY.values())


@pytest.mark.asyncio
async def test_count_open_critical_defects_filters_critical_and_open():
    """The helper issues a COUNT and returns its scalar; the compiled SQL
    filters on the canonical CRITICAL severity + OPEN status (not "P0")."""
    from app.services.metrics_service import (
        P0_DEFECT_SEVERITY,
        count_open_critical_defects,
    )

    captured = {}

    async def _fake_execute(stmt):
        captured["sql"] = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        res = MagicMock()
        res.scalar = MagicMock(return_value=3)
        return res

    db = AsyncMock()
    db.execute = _fake_execute

    count = await count_open_critical_defects(db, project_id=uuid.uuid4())

    assert count == 3
    sql = captured["sql"]
    assert P0_DEFECT_SEVERITY in sql          # 'CRITICAL' present
    assert "'P0'" not in sql                  # the dead literal is gone
    assert "OPEN" in sql
