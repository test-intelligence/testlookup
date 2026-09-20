import io
import json
import uuid
import zipfile
from types import SimpleNamespace

import pytest

from app.services.evidence_bundle_service import build_evidence_bundle
from app.services.report_export_sanitizer import sanitize_report_export_payload


def _payload():
    return {
        "run": {"build_number": "42"},
        "top_analyses": [{
            "root_cause_summary": "password=hunter2",
            "evidence_references": [{
                "source": "fetch_rest_api_payload",
                "excerpt": "restricted-tool-excerpt",
                "uri_or_ref": "https://logs.invalid?token=secret-value",
            }],
        }],
        "failure_clusters": [{
            "cluster_id": "c1",
            "evidence": [{"excerpt": "nested-restricted-excerpt"}],
        }],
        "defect_candidates": [{
            "cluster_id": "c1",
            "label": "candidate",
            "evidence_bundle": {
                "stack_traces": ["restricted-production-stack-trace"],
                "log_anomalies": ["restricted-production-log-anomaly"],
            },
        }],
        "nested": {
            "response_body": "restricted-response-body",
            "raw_headers": "restricted-raw-headers",
            "steps": ["restricted-step"],
        },
        "release_decision": {"recommendation": "NO_GO"},
        "provenance": {"evidence_count": 1},
    }


def test_export_projection_removes_evidence_and_redacts_remaining_text():
    safe = sanitize_report_export_payload(_payload())
    rendered = json.dumps(safe)
    assert "restricted-tool-excerpt" not in rendered
    assert "nested-restricted-excerpt" not in rendered
    assert "secret-value" not in rendered
    assert "hunter2" not in rendered
    assert "restricted-production-stack-trace" not in rendered
    assert "restricted-production-log-anomaly" not in rendered
    assert "restricted-response-body" not in rendered
    assert "restricted-raw-headers" not in rendered
    assert "restricted-step" not in rendered
    assert "evidence_bundle" not in rendered
    assert "evidence_references" not in rendered
    assert "password=[REDACTED]" in rendered


@pytest.mark.asyncio
async def test_zip_snapshot_never_contains_restricted_evidence():
    project_id = uuid.uuid4()
    run_id = uuid.uuid4()

    class _Result:
        def __init__(self, value):
            self._value = value

        def scalar_one_or_none(self):
            return self._value

    class _Db:
        """Answers the tenant lookup first, then the snapshot read.

        The service used to enforce tenancy inside the snapshot SELECT, which
        a mocked session ignores entirely — this test would have passed even if
        the service had dropped the scope check. The check is explicit now, so
        the fake has to answer it.
        """

        def __init__(self):
            self.calls = 0

        async def execute(self, _statement):
            self.calls += 1
            if self.calls == 1:
                return _Result(project_id)
            # ``get_cached_snapshot`` reads schema_version off the row; a
            # hand-built namespace does not grow a column when the query does.
            from app.services.intelligence_snapshot_service import (
                CURRENT_SCHEMA_VERSION,
            )

            return _Result(
                SimpleNamespace(
                    payload=_payload(),
                    schema_version=CURRENT_SCHEMA_VERSION,
                    stale=False,
                )
            )

    bundle = await build_evidence_bundle(
        _Db(), project_id, run_id, include_pdf=False
    )
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        snapshot = archive.read("intelligence-snapshot.json").decode()
        cluster = archive.read("clusters/c1.json").decode()
    assert "restricted-tool-excerpt" not in snapshot
    assert "nested-restricted-excerpt" not in snapshot
    assert "evidence_references" not in snapshot
    assert "excerpt" not in cluster
