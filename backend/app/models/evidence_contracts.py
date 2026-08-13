"""Versioned, immutable contracts for decision-grade run evidence."""
from __future__ import annotations

import math
from types import MappingProxyType
from typing import Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator

SHA256_PATTERN = r"^[0-9a-f]{64}$"
RUN_METRIC_VALUE_KEYS = frozenset({
    "total_tests", "passed_tests", "failed_tests", "skipped_tests",
    "broken_tests", "unknown_tests", "pass_rate", "failure_cluster_count",
    "flaky_finding_count", "test_health_finding_count",
})
RUN_METRIC_COUNT_KEYS = RUN_METRIC_VALUE_KEYS - {"pass_rate"}


class FrozenContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DataQualityFlagV1(FrozenContract):
    code: str = Field(min_length=1, max_length=100)
    severity: Literal["warning", "error"] = "warning"
    detail: str = Field(max_length=240)


class MetricWindowV1(FrozenContract):
    kind: Literal["single_run"] = "single_run"
    test_run_id: str = Field(min_length=1)


class RunMetricSnapshotV1(FrozenContract):
    schema_version: Literal[1] = 1
    definition_version: Literal["run_metrics_v1"] = "run_metrics_v1"
    window: MetricWindowV1
    values: Mapping[str, int | float]
    denominators: Mapping[str, int]
    source_fields: Mapping[str, str]
    quality_flags: tuple[DataQualityFlagV1, ...] = ()
    content_sha256: str = Field(pattern=SHA256_PATTERN)

    @field_validator("values", "denominators", "source_fields")
    @classmethod
    def _freeze_mapping(cls, value: Mapping) -> Mapping:
        return MappingProxyType(dict(value))

    @field_serializer("values", "denominators", "source_fields")
    def _serialize_mapping(self, value: Mapping) -> dict:
        return dict(value)

    @model_validator(mode="after")
    def _validate_metric_contract(self) -> "RunMetricSnapshotV1":
        error_codes = {
            item.code for item in self.quality_flags if item.severity == "error"
        }
        if set(self.values) != RUN_METRIC_VALUE_KEYS:
            raise ValueError("metric values must contain the exact run_metrics_v1 keys")
        if set(self.denominators) != {"pass_rate"}:
            raise ValueError("metric denominators must contain only pass_rate")
        if set(self.source_fields) != RUN_METRIC_VALUE_KEYS:
            raise ValueError("metric source_fields must match metric values")
        if any(
            type(self.values[key]) is not int or self.values[key] < 0
            for key in RUN_METRIC_COUNT_KEYS
        ):
            raise ValueError("metric counts must be nonnegative integers")
        pass_rate = self.values["pass_rate"]
        if (
            isinstance(pass_rate, bool)
            or not isinstance(pass_rate, (int, float))
            or not math.isfinite(float(pass_rate))
            or not 0 <= float(pass_rate) <= 100
        ):
            raise ValueError("pass_rate must be finite and between zero and 100")
        executed = sum(
            int(self.values[key])
            for key in ("passed_tests", "failed_tests", "broken_tests")
        )
        if type(self.denominators["pass_rate"]) is not int:
            raise ValueError("pass_rate denominator must be an integer")
        if self.denominators["pass_rate"] != executed:
            raise ValueError("pass_rate denominator must match executed outcomes")
        outcome_total = sum(
            int(self.values[key])
            for key in (
                "passed_tests", "failed_tests", "broken_tests",
                "skipped_tests", "unknown_tests",
            )
        )
        if (
            int(self.values["total_tests"]) != outcome_total
            and "outcome_total_mismatch" not in error_codes
        ):
            raise ValueError("total_tests must match all outcome counts")
        expected_rate = round(
            int(self.values["passed_tests"]) * 100.0 / executed, 4
        ) if executed else 0.0
        if (
            abs(float(pass_rate) - expected_rate) > 0.15
            and "pass_rate_mismatch" not in error_codes
        ):
            raise ValueError("pass_rate must match its declared counts and denominator")
        return self


class EvidenceReferenceV1(FrozenContract):
    schema_version: Literal[1] = 1
    evidence_id: str = Field(pattern=SHA256_PATTERN)
    kind: str = Field(min_length=1, max_length=100)
    source: str = Field(min_length=1, max_length=100)
    scope: Mapping[str, str]
    checksum_sha256: str = Field(pattern=SHA256_PATTERN)
    freshness: Literal["current_run", "historical", "unknown"]
    sensitivity: Literal["internal", "restricted"]
    authorization_status: Literal["pipeline_scoped_unverified"]
    uri_or_ref: str | None = None
    excerpt: str = Field(default="", max_length=500)

    @field_validator("scope")
    @classmethod
    def _freeze_scope(cls, value: Mapping[str, str]) -> Mapping[str, str]:
        return MappingProxyType(dict(value))

    @field_serializer("scope")
    def _serialize_scope(self, value: Mapping[str, str]) -> dict[str, str]:
        return dict(value)



class EvidenceReferenceV2(FrozenContract):
    schema_version: Literal[2] = 2
    artifact_id: str
    evidence_id: str = Field(pattern=SHA256_PATTERN)
    kind: str = Field(min_length=1, max_length=100)
    source: str = Field(min_length=1, max_length=100)
    scope: Mapping[str, str]
    producer_pipeline_run_id: str
    checksum_sha256: str = Field(pattern=SHA256_PATTERN)
    freshness: Literal["current_run", "historical", "unknown"]
    sensitivity: Literal["internal", "restricted"]
    authorization_status: Literal["tenant_run_pipeline_verified"]
    uri_or_ref: None = None
    excerpt: str = Field(default="", max_length=500)

    @field_validator("scope")
    @classmethod
    def _freeze_scope(cls, value: Mapping[str, str]) -> Mapping[str, str]:
        return MappingProxyType(dict(value))

    @field_serializer("scope")
    def _serialize_scope(self, value: Mapping[str, str]) -> dict[str, str]:
        return dict(value)

    @model_validator(mode="after")
    def _validate_authority(self) -> "EvidenceReferenceV2":
        import uuid

        try:
            uuid.UUID(self.artifact_id)
            uuid.UUID(self.producer_pipeline_run_id)
        except ValueError as exc:
            raise ValueError("verified evidence identity must be UUID") from exc
        return self


class RunEvidenceBundleV1(FrozenContract):
    schema_version: Literal[1] = 1
    project_id: str = Field(min_length=1)
    test_run_id: str = Field(min_length=1)
    pipeline_run_id: str = Field(min_length=1)
    build_number: str
    branch: str | None = None
    workflow_type: str
    metric_snapshot: RunMetricSnapshotV1
    failed_test_ids: tuple[str, ...] = Field(default=(), max_length=10_000)
    evidence_refs: tuple[EvidenceReferenceV1, ...] = Field(default=(), max_length=500)
    specialist_payload_sha256: Mapping[str, str]
    quality_flags: tuple[DataQualityFlagV1, ...] = ()
    omitted_evidence_count: int = Field(default=0, ge=0)
    content_sha256: str = Field(pattern=SHA256_PATTERN)

    @field_validator("specialist_payload_sha256")
    @classmethod
    def _freeze_specialist_hashes(cls, value: Mapping[str, str]) -> Mapping[str, str]:
        if any(
            not isinstance(key, str)
            or not isinstance(item, str)
            or len(item) != 64
            or any(char not in "0123456789abcdef" for char in item)
            for key, item in value.items()
        ):
            raise ValueError("specialist payload hashes must be lowercase sha256 values")
        return MappingProxyType(dict(value))

    @field_serializer("specialist_payload_sha256")
    def _serialize_specialist_hashes(
        self, value: Mapping[str, str]
    ) -> dict[str, str]:
        return dict(value)

    @model_validator(mode="after")
    def _validate_bundle_scope(self) -> "RunEvidenceBundleV1":
        if self.metric_snapshot.window.test_run_id != self.test_run_id:
            raise ValueError("metric window must match bundle test_run_id")
        for reference in self.evidence_refs:
            if set(reference.scope) != {"project_id", "test_run_id", "test_case_id"}:
                raise ValueError("evidence scope must contain exact identity keys")
            if (
                reference.scope["project_id"] != self.project_id
                or reference.scope["test_run_id"] != self.test_run_id
                or not reference.scope["test_case_id"]
            ):
                raise ValueError("evidence scope must match bundle identity")
        if any(not item for item in self.failed_test_ids):
            raise ValueError("failed test IDs must be non-empty")
        return self


class RunEvidenceBundleV2(FrozenContract):
    schema_version: Literal[2] = 2
    project_id: str = Field(min_length=1)
    test_run_id: str = Field(min_length=1)
    pipeline_run_id: str = Field(min_length=1)
    build_number: str
    branch: str | None = None
    workflow_type: str
    metric_snapshot: RunMetricSnapshotV1
    failed_test_ids: tuple[str, ...] = Field(default=(), max_length=10_000)
    evidence_refs: tuple[EvidenceReferenceV2, ...] = Field(default=(), max_length=500)
    specialist_payload_sha256: Mapping[str, str]
    quality_flags: tuple[DataQualityFlagV1, ...] = ()
    omitted_evidence_count: int = Field(default=0, ge=0)
    content_sha256: str = Field(pattern=SHA256_PATTERN)

    @field_validator("specialist_payload_sha256")
    @classmethod
    def _freeze_specialist_hashes(cls, value: Mapping[str, str]) -> Mapping[str, str]:
        if any(
            not isinstance(key, str)
            or not isinstance(item, str)
            or len(item) != 64
            or any(char not in "0123456789abcdef" for char in item)
            for key, item in value.items()
        ):
            raise ValueError("specialist payload hashes must be lowercase sha256 values")
        return MappingProxyType(dict(value))

    @field_serializer("specialist_payload_sha256")
    def _serialize_specialist_hashes(self, value: Mapping[str, str]) -> dict[str, str]:
        return dict(value)

    @model_validator(mode="after")
    def _validate_bundle_scope(self) -> "RunEvidenceBundleV2":
        if self.metric_snapshot.window.test_run_id != self.test_run_id:
            raise ValueError("metric window must match bundle test_run_id")
        for reference in self.evidence_refs:
            if set(reference.scope) != {"project_id", "test_run_id", "test_case_id"}:
                raise ValueError("evidence scope must contain exact identity keys")
            if (
                reference.scope["project_id"] != self.project_id
                or reference.scope["test_run_id"] != self.test_run_id
                or not reference.scope["test_case_id"]
                or reference.producer_pipeline_run_id != self.pipeline_run_id
            ):
                raise ValueError("verified evidence scope must match bundle identity")
        if any(not item for item in self.failed_test_ids):
            raise ValueError("failed test IDs must be non-empty")
        return self
