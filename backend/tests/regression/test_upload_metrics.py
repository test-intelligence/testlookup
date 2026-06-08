"""MRU-15/17: upload metrics + the manual_upload rollout-flag migration."""
from __future__ import annotations

import importlib.util as _u
import inspect
import os

_HERE = os.path.dirname(__file__)
_MIG = os.path.join(_HERE, "..", "..", "migrations", "versions", "0092_manual_upload_feature_flag.py")


def test_migration_0092_chains_and_has_downgrade():
    spec = _u.spec_from_file_location("m0092", _MIG)
    m = _u.module_from_spec(spec)
    spec.loader.exec_module(m)
    assert m.revision == "0092"
    assert m.down_revision == "0091"  # chains off the ingestion_source migration
    assert "manual_upload" in inspect.getsource(m.upgrade)
    assert "enabled_global" in inspect.getsource(m.upgrade)
    # Real downgrade body (quality-gate database.downgrade-implemented).
    assert "DELETE FROM feature_flags" in inspect.getsource(m.downgrade)


def test_upload_metrics_exist_with_expected_labels():
    from app.core.metrics import (
        upload_failures_total,
        upload_processing_seconds,
        uploads_total,
    )

    # The documented label sets increment without raising.
    uploads_total.labels(state="succeeded", format="junit").inc()
    uploads_total.labels(state="failed", format="archive").inc()
    upload_failures_total.labels(code="parse_error").inc()
    upload_failures_total.labels(code="empty_report").inc()
    upload_failures_total.labels(code="infra_error").inc()
    upload_processing_seconds.observe(1.5)
