"""Canonical RunEvidenceBundleV1 and deterministic metric snapshot builders."""
from __future__ import annotations

import math
from heapq import nsmallest
from itertools import islice
from copy import deepcopy
from typing import Any

from pydantic import ValidationError

from app.models.evidence_contracts import (
    RunEvidenceBundleV1,
    RunEvidenceBundleV2,
    RunMetricSnapshotV1,
)
from app.services.canonical_json import stable_json_sha256
from app.services.evidence_sanitizer import (
    sanitize_persistence_payload,
    sanitize_reference_text,
)

MAX_EVIDENCE_REFERENCES = 500
MAX_EVIDENCE_SCAN = 2000
MAX_EVIDENCE_EXCERPT_CHARS = 500
MAX_FAILED_TEST_IDS = 10_000
MAX_METRIC_COUNT = 100_000_000
_METRIC_KEYS = (
    "total_tests",
    "passed_tests",
    "failed_tests",
    "skipped_tests",
    "broken_tests",
    "unknown_tests",
)
_SPECIALIST_FIELDS = (
    "anomalies",
    "analyses",
    "failure_clusters",
    "deep_findings",
    "flaky_findings",
    "test_health_findings",
    "contract_findings",
    "log_findings",
    "regression_classification",
    "change_ownership_findings",
    "cluster_investigation_results",
    "release_decision",
)


def _json_hash(value: Any) -> str:
    return stable_json_sha256(value)


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_dict_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _bounded_failed_ids(state: dict[str, Any]) -> tuple[list[str], int, bool]:
    raw = state.get("failed_test_ids")
    if raw is None:
        return [], 0, False
    if not isinstance(raw, (list, tuple)):
        return [], 0, True
    values = [str(item) for item in islice(raw, MAX_FAILED_TEST_IDS + 1)]
    return values[:MAX_FAILED_TEST_IDS], len(raw), False


def _flag(code: str, detail: str, severity: str = "warning") -> dict[str, str]:
    return {"code": code, "severity": severity, "detail": detail[:240]}


def _numeric_metric(
    run_data: dict[str, Any],
    state: dict[str, Any],
    key: str,
    *,
    fallback: int | float = 0,
) -> tuple[int | float, str, list[dict[str, str]]]:
    flags: list[dict[str, str]] = []
    if key in run_data and run_data[key] is not None:
        raw = run_data[key]
        source = f"test_run_data.{key}"
    elif state.get(key) is not None:
        raw = state[key]
        source = f"workflow_state.{key}"
        flags.append(_flag(f"{key}_fallback_used", f"{key} came from workflow state"))
    else:
        raw = fallback
        source = "deterministic_default"
        flags.append(_flag(f"{key}_missing", f"{key} was absent and defaulted to {fallback}"))
    try:
        if key != "pass_rate" and isinstance(raw, float) and not raw.is_integer():
            raise ValueError
        value = float(raw) if key == "pass_rate" else int(raw)
        if isinstance(raw, bool) or not math.isfinite(float(value)):
            raise ValueError
    except (TypeError, ValueError, OverflowError):
        value = float(fallback) if key == "pass_rate" else int(fallback)
        flags.append(_flag(f"{key}_invalid", f"{key} was not a finite number", "error"))
        source = "deterministic_default"
    if value < 0:
        flags.append(_flag(f"{key}_negative", f"{key} was negative and clamped to zero", "error"))
        value = 0.0 if key == "pass_rate" else 0
    if key == "pass_rate" and value > 100:
        flags.append(_flag("pass_rate_out_of_range", "pass_rate exceeded 100", "error"))
        value = 100.0
    if key != "pass_rate" and value > MAX_METRIC_COUNT:
        flags.append(_flag(
            f"{key}_out_of_range",
            f"{key} exceeded the {MAX_METRIC_COUNT} safety limit",
            "error",
        ))
        value = MAX_METRIC_COUNT
    return value, source, flags


def build_run_metric_snapshot(state: dict[str, Any]) -> dict[str, Any]:
    """Create a definition-versioned, single-run metric snapshot."""
    run_data = _as_dict(state.get("test_run_data"))
    values: dict[str, int | float] = {}
    sources: dict[str, str] = {}
    flags: list[dict[str, str]] = []
    for key in _METRIC_KEYS:
        value, source, field_flags = _numeric_metric(run_data, state, key)
        values[key] = value
        sources[key] = source
        flags.extend(field_flags)

    total = int(values["total_tests"])
    passed = int(values["passed_tests"])
    outcome_total = sum(int(values[key]) for key in _METRIC_KEYS if key != "total_tests")
    if total != outcome_total:
        flags.append(_flag(
            "outcome_total_mismatch",
            f"total_tests={total} but all outcome counts sum to {outcome_total}",
            "error",
        ))

    executed = passed + int(values["failed_tests"]) + int(values["broken_tests"])
    supplied_rate, rate_source, rate_flags = _numeric_metric(
        run_data,
        state,
        "pass_rate",
        fallback=(passed * 100.0 / executed if executed else 0.0),
    )
    values["pass_rate"] = round(float(supplied_rate), 4)
    sources["pass_rate"] = rate_source
    flags.extend(rate_flags)
    expected_rate = round(passed * 100.0 / executed, 4) if executed else 0.0
    if abs(float(values["pass_rate"]) - expected_rate) > 0.15:
        flags.append(_flag(
            "pass_rate_mismatch",
            f"pass_rate={values['pass_rate']} but counts imply {expected_rate}",
            "error",
        ))

    failed_ids, raw_failed_count, failed_ids_invalid = _bounded_failed_ids(state)
    failed_outcomes = int(values["failed_tests"]) + int(values["broken_tests"])
    if failed_ids_invalid:
        flags.append(_flag(
            "failed_test_ids_invalid",
            "failed_test_ids must be a list or tuple",
            "error",
        ))
    if raw_failed_count > MAX_FAILED_TEST_IDS:
        flags.append(_flag(
            "failed_test_identity_scan_limit_reached",
            "failed-test identity validation exceeded the bounded scan limit",
            "error",
        ))
    if len(set(failed_ids)) != failed_outcomes:
        flags.append(_flag(
            "failed_test_identity_mismatch",
            f"{len(set(failed_ids))} failed IDs but {failed_outcomes} failed/broken outcomes",
        ))

    specialist_counts = {
        "failure_cluster_count": len(_as_dict_list(state.get("failure_clusters"))),
        "flaky_finding_count": len(_as_dict_list(state.get("flaky_findings"))),
        "test_health_finding_count": len(_as_dict_list(state.get("test_health_findings"))),
    }
    values.update(specialist_counts)
    sources.update({
        "failure_cluster_count": "workflow_state.failure_clusters",
        "flaky_finding_count": "workflow_state.flaky_findings",
        "test_health_finding_count": "workflow_state.test_health_findings",
    })

    payload = {
        "schema_version": 1,
        "definition_version": "run_metrics_v1",
        "window": {"kind": "single_run", "test_run_id": str(state.get("test_run_id") or "")},
        "values": values,
        "denominators": {"pass_rate": executed},
        "source_fields": sources,
        "quality_flags": flags,
    }
    snapshot = RunMetricSnapshotV1(
        **payload,
        content_sha256=_json_hash(payload),
    )
    return snapshot.model_dump(mode="json")


def _evidence_references(
    state: dict[str, Any],
) -> tuple[list[dict[str, Any]], int, int, int, int, int]:
    if "authorized_evidence_artifacts" in state:
        raw_authorized = state.get("authorized_evidence_artifacts")
        authorized = (
            [deepcopy(item) for item in raw_authorized if isinstance(item, dict)]
            if isinstance(raw_authorized, list)
            else []
        )
        analyses = _as_dict(state.get("analyses"))
        candidate_count = sum(
            len(_as_dict(analysis).get("evidence_references") or [])
            if isinstance(_as_dict(analysis).get("evidence_references"), list)
            else 0
            for analysis in analyses.values()
        )
        errors = state.get("evidence_authorization_errors")
        invalid = len(errors) if isinstance(errors, list) else int(bool(errors))
        omitted = max(0, len(authorized) - MAX_EVIDENCE_REFERENCES)
        if candidate_count and not authorized and not invalid:
            invalid = 1
        return (
            authorized[:MAX_EVIDENCE_REFERENCES],
            omitted,
            0,
            0,
            0,
            invalid,
        )
    refs: list[dict[str, Any]] = []
    analyses = _as_dict(state.get("analyses"))
    project_id = str(state.get("project_id") or "")
    test_run_id = str(state.get("test_run_id") or "")
    candidate_count = sum(
        len(_as_dict(analyses[test_case_id]).get("evidence_references") or [])
        if isinstance(_as_dict(analyses[test_case_id]).get("evidence_references"), list)
        else 0
        for test_case_id in analyses
    )
    seen: set[str] = set()
    scanned = 0
    redacted_count = 0
    duplicate_count = 0
    invalid_scope_count = 0
    invalid_reference_count = 0
    known_failed_ids = set(_bounded_failed_ids(state)[0])
    for test_case_id in nsmallest(MAX_EVIDENCE_SCAN, analyses, key=str):
        analysis = _as_dict(analyses[test_case_id])
        raw_references = analysis.get("evidence_references")
        if not isinstance(raw_references, list):
            continue
        for raw in raw_references:
            if scanned >= MAX_EVIDENCE_SCAN:
                break
            scanned += 1
            if not isinstance(raw, dict):
                invalid_reference_count += 1
                continue
            if str(test_case_id) not in known_failed_ids:
                invalid_scope_count += 1
                continue
            raw_source = str(raw.get("source") or "unknown")[:100]
            raw_kind = str(raw.get("kind") or raw.get("type") or "analysis_evidence")[:100]
            uri = raw.get("reference_id") or raw.get("reference") or raw.get("uri")
            raw_uri = str(uri)[:1000] if uri is not None else None
            raw_excerpt = str(raw.get("excerpt") or "")
            source, source_changed, _ = sanitize_reference_text(raw_source, limit=100)
            kind, kind_changed, _ = sanitize_reference_text(raw_kind, limit=100)
            if raw_uri is not None:
                _safe_uri, uri_changed, _ = sanitize_reference_text(raw_uri, limit=1000)
            else:
                uri_changed = False
            excerpt, excerpt_changed, _ = sanitize_reference_text(
                raw_excerpt, limit=MAX_EVIDENCE_EXCERPT_CHARS
            )
            # Inline or external references are not made actionable until the
            # EvidenceArtifact ownership adapter resolves them in the next slice.
            uri_text = None
            if (source, kind, uri_text, excerpt) != (
                raw_source, raw_kind, None, raw_excerpt
            ):
                redacted_count += 1
            elif source_changed or kind_changed or uri_changed or excerpt_changed:
                redacted_count += 1
            canonical = {
                "source": source,
                "kind": kind,
                "uri_or_ref": uri_text,
                "excerpt": excerpt,
            }
            checksum = _json_hash(canonical)
            evidence_id = _json_hash({
                "project_id": project_id,
                "test_run_id": test_run_id,
                "test_case_id": str(test_case_id),
                "checksum": checksum,
            })
            if evidence_id in seen:
                duplicate_count += 1
                continue
            seen.add(evidence_id)
            if len(refs) >= MAX_EVIDENCE_REFERENCES:
                continue
            raw_sensitivity = str(raw.get("sensitivity") or "").strip().lower()
            sensitivity = "internal" if raw_sensitivity == "internal" else "restricted"
            freshness = raw.get("freshness")
            if freshness not in {"current_run", "historical", "unknown"}:
                freshness = "current_run"
            refs.append({
                "schema_version": 1,
                "evidence_id": evidence_id,
                "kind": kind,
                "source": source,
                "scope": {
                    "project_id": project_id,
                    "test_run_id": test_run_id,
                    "test_case_id": str(test_case_id),
                },
                "checksum_sha256": checksum,
                "freshness": freshness,
                "sensitivity": sensitivity,
                "authorization_status": "pipeline_scoped_unverified",
                "uri_or_ref": uri_text,
                "excerpt": excerpt,
            })
        if scanned >= MAX_EVIDENCE_SCAN:
            break
    omitted = max(
        0,
        candidate_count
        - duplicate_count
        - invalid_scope_count
        - invalid_reference_count
        - len(refs),
    )
    return (
        refs,
        omitted,
        redacted_count,
        duplicate_count,
        invalid_scope_count,
        invalid_reference_count,
    )


def build_run_evidence_bundle(state: dict[str, Any]) -> dict[str, Any]:
    """Adapt existing normalized workflow state into RunEvidenceBundleV1."""
    metric_snapshot = build_run_metric_snapshot(state)
    (
        evidence_refs,
        omitted,
        redacted_count,
        duplicate_count,
        invalid_scope_count,
        invalid_reference_count,
    ) = _evidence_references(state)
    quality_flags = deepcopy(metric_snapshot["quality_flags"])
    if omitted:
        quality_flags.append(_flag(
            "evidence_reference_limit_reached",
            f"{omitted} evidence references were omitted after the {MAX_EVIDENCE_REFERENCES} item bound",
        ))
    if redacted_count:
        quality_flags.append(_flag(
            "evidence_content_redacted",
            f"Sensitive patterns were redacted from {redacted_count} evidence references",
        ))
    if duplicate_count:
        quality_flags.append(_flag(
            "duplicate_evidence_references_removed",
            f"{duplicate_count} duplicate evidence references were removed",
        ))
    if invalid_scope_count:
        quality_flags.append(_flag(
            "evidence_scope_invalid",
            f"{invalid_scope_count} references were outside the failed-test identity set",
            "error",
        ))
    if invalid_reference_count:
        quality_flags.append(_flag(
            "evidence_reference_invalid",
            f"{invalid_reference_count} malformed evidence references were ignored",
            "error",
        ))
    if evidence_refs and any(
        item.get("authorization_status") != "tenant_run_pipeline_verified"
        for item in evidence_refs
    ):
        quality_flags.append(_flag(
            "evidence_authorization_pending",
            "Inline evidence is non-actionable until resolved through the tenant-authorized artifact store",
        ))
    for authorization_error in sorted(set(
        str(item) for item in (state.get("evidence_authorization_errors") or [])
    )):
        quality_flags.append(_flag(
            authorization_error,
            "Evidence artifact authorization failed closed",
            "error",
        ))
    if not state.get("baseline_diff") and not state.get("baseline_run_id"):
        quality_flags.append(_flag(
            "historical_baseline_unavailable",
            "No immutable historical baseline was present in workflow state",
        ))
    specialist_hashes: dict[str, str] = {}
    sanitization_omissions = 0
    for field in _SPECIALIST_FIELDS:
        if state.get(field) is None:
            continue
        safe_payload, stats = sanitize_persistence_payload(state[field])
        specialist_hashes[field] = _json_hash(safe_payload)
        sanitization_omissions += stats.omitted_items
    if sanitization_omissions:
        quality_flags.append(_flag(
            "specialist_payload_truncated",
            f"{sanitization_omissions} specialist payload items exceeded persistence bounds",
            "error",
        ))
    failed_ids, raw_failed_count, _failed_ids_invalid = _bounded_failed_ids(state)
    authoritative = "authorized_evidence_artifacts" in state
    payload = {
        "schema_version": 2 if authoritative else 1,
        "project_id": str(state.get("project_id") or ""),
        "test_run_id": str(state.get("test_run_id") or ""),
        "pipeline_run_id": str(state.get("pipeline_run_id") or ""),
        "build_number": str(state.get("build_number") or ""),
        "branch": str(state["branch"]) if state.get("branch") is not None else None,
        "workflow_type": str(state.get("workflow_type") or ""),
        "metric_snapshot": metric_snapshot,
        "failed_test_ids": tuple(dict.fromkeys(failed_ids)),
        "evidence_refs": evidence_refs,
        "specialist_payload_sha256": specialist_hashes,
        "quality_flags": quality_flags,
        "omitted_evidence_count": omitted,
    }
    if raw_failed_count > MAX_FAILED_TEST_IDS:
        payload["quality_flags"].append(_flag(
            "failed_test_identity_limit_reached",
            f"{raw_failed_count - MAX_FAILED_TEST_IDS} failed-test IDs exceeded the persistence bound",
            "error",
        ))
    bundle_type = RunEvidenceBundleV2 if authoritative else RunEvidenceBundleV1
    bundle = bundle_type(**payload, content_sha256=_json_hash(payload))
    return bundle.model_dump(mode="json")


def validate_run_evidence_bundle(bundle: dict[str, Any]) -> list[str]:
    """Validate schema and both nested content fingerprints."""
    failures: list[str] = []
    try:
        bundle_model = (
            RunEvidenceBundleV2
            if bundle.get("schema_version") == 2
            else RunEvidenceBundleV1
        )
        parsed = bundle_model.model_validate(bundle)
    except ValidationError:
        return ["run_evidence_bundle_schema_invalid"]
    normalized = parsed.model_dump(mode="json")
    metric = deepcopy(normalized["metric_snapshot"])
    metric_hash = metric.pop("content_sha256")
    if metric_hash != _json_hash(metric):
        failures.append("metric_snapshot_hash_mismatch")
    observed = normalized.pop("content_sha256")
    if observed != _json_hash(normalized):
        failures.append("run_evidence_bundle_hash_mismatch")
    for reference in normalized["evidence_refs"]:
        canonical = {
            "source": reference["source"],
            "kind": reference["kind"],
            "uri_or_ref": reference["uri_or_ref"],
            "excerpt": reference["excerpt"],
        }
        if reference["checksum_sha256"] != _json_hash(canonical):
            failures.append("evidence_reference_checksum_mismatch")
        expected_id = _json_hash({
            "project_id": normalized["project_id"],
            "test_run_id": normalized["test_run_id"],
            "test_case_id": reference["scope"]["test_case_id"],
            "checksum": reference["checksum_sha256"],
        })
        if reference["evidence_id"] != expected_id:
            failures.append("evidence_reference_identity_mismatch")
        if (
            normalized["schema_version"] == 2
            and reference["producer_pipeline_run_id"] != normalized["pipeline_run_id"]
        ):
            failures.append("evidence_reference_pipeline_mismatch")
    return failures
