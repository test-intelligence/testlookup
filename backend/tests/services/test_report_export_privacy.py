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
    class _Result:
        def scalar_one_or_none(self):
            return SimpleNamespace(payload=_payload())

    class _Db:
        async def execute(self, _statement):
            return _Result()

    bundle = await build_evidence_bundle(
        _Db(), uuid.uuid4(), uuid.uuid4(), include_pdf=False
    )
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        snapshot = archive.read("intelligence-snapshot.json").decode()
        cluster = archive.read("clusters/c1.json").decode()
    assert "restricted-tool-excerpt" not in snapshot
    assert "nested-restricted-excerpt" not in snapshot
    assert "evidence_references" not in snapshot
    assert "excerpt" not in cluster
