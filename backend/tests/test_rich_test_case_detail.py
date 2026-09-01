"""Focused contract tests for the Rich Test Case Detail backend MVP."""
from datetime import datetime, timezone
import json
from types import SimpleNamespace
import uuid
from unittest.mock import AsyncMock, patch

from app.models.schemas import EnrichedTestCaseDetailResponse, ManagedTestCaseCreate
from app.routers.runs import get_enriched_test_case_detail_endpoint, router as runs_router
from app.services.allure_parser import parse_allure_result, parse_allure_zip
from app.services.ingestion import _redact_dict_or_list
from app.services.redaction_service import redact_dict
from app.services.playwright_parser import parse_playwright_json
from app.services.runs_service import _safe_step_parameters, build_enriched_test_case_detail
from app.services.runs_service import get_enriched_test_case_detail


def _allure_result(**overrides):
    payload = {
        "uuid": "allure-uuid-1",
        "historyId": "history-1",
        "testCaseId": "logical-1",
        "name": "login works",
        "fullName": "auth.login works",
        "status": "passed",
        "start": 100,
        "stop": 250,
        "labels": [{"name": "suite", "value": "Auth"}],
        "steps": [{
            "name": "open login",
            "status": "passed",
            "start": 110,
            "stop": 120,
            "steps": [{"name": "nested", "status": "passed"}],
        }],
    }
    payload.update(overrides)
    return payload


def test_allure_sparse_steps_and_source_identity_are_non_fatal():
    parsed = parse_allure_result(
        _allure_result(steps=None), "run-1", "uploads/run-1/0-result.json"
    )

    assert parsed is not None
    assert parsed["steps"] == []
    assert parsed["steps_present"] is False
    assert parsed["source_uuid"] == "allure-uuid-1"
    assert parsed["source_history_id"] == "history-1"
    assert parsed["source_test_case_id"] == "logical-1"
    assert parsed["parser_format"] == "allure"


def test_allure_source_parameters_links_and_malformed_labels_are_bounded():
    parsed = parse_allure_result(
        _allure_result(
            labels=[{"name": "tag", "value": "smoke"}, "bad-label"],
            parameters=[
                {"name": "browser", "value": "chromium", "excludedFromHistory": True},
                "bad-parameter",
                {"name": "", "value": "ignored"},
            ],
            links=[
                {"url": "https://example.test/issue/1", "name": "Issue", "type": "issue"},
                {"name": "missing-url"},
            ],
        ),
        "run-1",
        "uploads/run-1/0-result.json",
    )

    assert parsed["source_labels"] == [{"name": "tag", "value": "smoke"}]
    assert parsed["source_parameters"] == [{
        "name": "browser",
        "value": "chromium",
        "mode": None,
        "excluded_from_history": True,
    }]
    assert parsed["source_links"] == [{
        "url": "https://example.test/issue/1",
        "name": "Issue",
        "type": "issue",
    }]


def test_allure_masked_parameters_and_malformed_optional_objects_fail_closed():
    parsed = parse_allure_result(
        _allure_result(
            statusDetails="not-an-object",
            attachments=None,
            parameters=[{"name": "password", "value": "secret", "mode": "masked"}],
            steps=[{
                "name": "submit",
                "statusDetails": 42,
                "parameters": [{"name": "token", "value": "secret", "mode": "hidden"}],
                "attachments": None,
            }],
        ),
        "run-1",
        "uploads/run-1/0-result.json",
    )

    assert parsed is not None
    assert parsed["source_parameters"][0]["value"] is None
    assert parsed["source_parameters"][0]["masked"] is True
    assert parsed["steps"][0]["parameters"][0]["value"] is None
    assert parsed["attachments"] == []


def test_allure_malformed_core_name_and_status_are_safe():
    assert parse_allure_result(_allure_result(name=None), "run-1", "x") is None
    parsed = parse_allure_result(_allure_result(status=None), "run-1", "x")
    assert parsed is not None
    assert parsed["status"] == "unknown"


def test_allure_malformed_steps_are_non_fatal_and_retry_survivor_keeps_identity():
    malformed = parse_allure_result(
        _allure_result(steps={"not": "a list"}), "run-1", "uploads/run-1/a.json"
    )
    assert malformed is not None
    assert malformed["steps"] == []
    assert malformed["steps_present"] is False

    files = {
        "a-result.json": json.dumps(_allure_result(uuid="attempt-1", status="failed")).encode(),
        "b-result.json": json.dumps(
            _allure_result(uuid="attempt-2", status="passed", start=300, stop=450)
        ).encode(),
    }
    survivors = parse_allure_zip(files, "run-1")

    assert len(survivors) == 1
    assert survivors[0]["source_uuid"] == "attempt-2"
    assert survivors[0]["source_history_id"] == "history-1"
    assert survivors[0]["retry_count"] == 1
    assert survivors[0]["is_flaky"] is True


def test_playwright_sparse_report_has_explicit_empty_steps_and_format():
    payload = {
        "config": {"version": "1.42"},
        "suites": [{
            "title": "login.spec.ts",
            "file": "tests/login.spec.ts",
            "specs": [{
                "title": "login works",
                "line": 12,
                "tests": [{
                    "projectName": "chromium",
                    "status": "passed",
                    "results": [{"status": "passed", "duration": 12}],
                }],
            }],
        }],
    }

    parsed = parse_playwright_json(json.dumps(payload), "run-1")

    assert len(parsed) == 1
    assert parsed[0]["steps"] == []
    assert parsed[0]["steps_present"] is False
    assert parsed[0]["parser_format"] == "playwright"
    assert parsed[0]["parser_version"] == "1.42"


def test_enriched_contract_preserves_flat_fields_and_recursive_steps():
    test_id = uuid.uuid4()
    run_id = uuid.uuid4()
    step_id = uuid.uuid4()
    child_step_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    test_case = SimpleNamespace(
        id=test_id,
        test_run_id=run_id,
        canonical_test_case_id=None,
        test_fingerprint="fingerprint",
        test_name="login works",
        full_name="auth.login works",
        suite_name="Auth",
        class_name="auth.Login",
        package_name="auth",
        status="PASSED",
        duration_ms=120,
        severity=None,
        feature="Authentication",
        story=None,
        epic=None,
        owner=None,
        tags=["smoke"],
        failure_category=None,
        error_message=None,
        minio_s3_prefix="uploads/run-1/",
        has_attachments=False,
        step_count=1,
        steps_present=True,
        retry_count=0,
        is_flaky_run=False,
        stack_trace=None,
        source_uuid="source-1",
        source_history_id="history-1",
        source_test_case_id="logical-1",
        parser_format="allure",
        parser_version=None,
        assigned_to_user_id=None,
        created_at=now,
    )
    tree = {
        "snapshot_source_test_run_id": str(run_id),
        "steps": [{
            "id": step_id,
            "parent_step_id": None,
            "ordinal": 0,
            "depth": 0,
            "name": "open login",
            "keyword": None,
            "status": "PASSED",
            "duration_ms": 12,
            "start_ms": None,
            "assertion_message": None,
            "assertion_trace": None,
            "expected_value": None,
            "actual_value": None,
            "parameters": [],
            "created_at": now,
            "steps": [{
                "id": child_step_id,
                "parent_step_id": step_id,
                "ordinal": 1,
                "depth": 1,
                "name": "assert login",
                "keyword": "expect",
                "status": "PASSED",
                "duration_ms": 4,
                "start_ms": None,
                "assertion_message": None,
                "assertion_trace": None,
                "expected_value": "dashboard",
                "actual_value": "dashboard",
                "parameters": {"field": "route"},
                "created_at": now,
                "steps": [],
                "attachments": [],
            }],
            "attachments": [],
        }],
        "attachments": [],
    }

    response = EnrichedTestCaseDetailResponse.model_validate(
        build_enriched_test_case_detail(test_case, tree)
    )

    assert response.contract_version == "1"
    assert response.contract == "test-case-detail"
    assert response.schema_version == 1
    assert response.test_name == "login works"
    assert response.identity.source_history_id == "history-1"
    assert response.execution.steps_present is True
    assert response.steps[0].name == "open login"
    assert response.steps[0].steps[0].name == "assert login"


def test_enriched_contract_does_not_mislabel_stale_snapshot_for_sparse_case():
    now = datetime.now(timezone.utc)
    test_case = SimpleNamespace(
        id=uuid.uuid4(), test_run_id=uuid.uuid4(), canonical_test_case_id=uuid.uuid4(),
        test_fingerprint="fingerprint", test_name="sparse", full_name=None,
        suite_name=None, class_name=None, package_name=None, status="PASSED",
        duration_ms=1, severity=None, feature=None, story=None, epic=None, owner=None,
        tags=[], failure_category=None, error_message=None, minio_s3_prefix=None,
        has_attachments=False, step_count=0, steps_present=False, retry_count=0,
        is_flaky_run=False, stack_trace=None, source_uuid=None,
        source_history_id=None, source_test_case_id=None, parser_format="junit",
        parser_version=None, created_at=now,
    )
    stale_tree = {"snapshot_source_test_run_id": str(uuid.uuid4()), "steps": [{"name": "old"}], "attachments": []}

    response = build_enriched_test_case_detail(test_case, stale_tree)

    assert response["steps_present"] is False
    assert response["steps"] == []
    assert response["attachments"] == []


def test_sparse_steps_keep_current_attachment_evidence():
    now = datetime.now(timezone.utc)
    test_case = SimpleNamespace(
        id=uuid.uuid4(), test_run_id=uuid.uuid4(), canonical_test_case_id=None,
        test_fingerprint="fingerprint", test_name="attachment-only", full_name=None,
        suite_name=None, class_name=None, package_name=None, status="PASSED",
        duration_ms=1, severity=None, feature=None, story=None, epic=None, owner=None,
        tags=[], failure_category=None, error_message=None, minio_s3_prefix=None,
        has_attachments=True, step_count=0, steps_present=False, retry_count=0,
        is_flaky_run=False, stack_trace=None, source_uuid=None,
        source_history_id=None, source_test_case_id=None, parser_format="junit",
        parser_version=None, created_at=now,
    )
    attachment = {"id": uuid.uuid4(), "name": "screenshot.png", "created_at": now}

    response = build_enriched_test_case_detail(
        test_case,
        {"snapshot_source_test_run_id": str(uuid.uuid4()), "steps": [{"name": "stale"}], "attachments": [attachment]},
    )

    assert response["steps_present"] is False
    assert response["steps"] == []
    assert response["attachments"] == [attachment]


def test_persisted_malformed_source_metadata_is_safe_for_response_schema():
    now = datetime.now(timezone.utc)
    test_case = SimpleNamespace(
        id=uuid.uuid4(), test_run_id=uuid.uuid4(), canonical_test_case_id=None,
        test_fingerprint="fingerprint", test_name="metadata", full_name=None,
        suite_name="Auth", class_name=None, package_name=None, status="PASSED",
        duration_ms=1, severity=None, feature=None, story=None, epic=None, owner=None,
        tags=[], failure_category=None, error_message=None, minio_s3_prefix=None,
        has_attachments=False, step_count=0, steps_present=False, retry_count=0,
        is_flaky_run=False, stack_trace=None, source_uuid=None,
        source_history_id=None, source_test_case_id=None, parser_format="allure",
        parser_version=None, source_parameters=[{"password": "do-not-return", "name": "credential"}],
        source_links=["bad-link", {"url": "https://example.test"}],
        source_labels=[{"name": "suite", "value": "Auth"}, {"name": 4, "value": []}],
        source_extensions=["bad-extension"], assigned_to_user_id=None, created_at=now,
    )

    response = EnrichedTestCaseDetailResponse.model_validate(
        build_enriched_test_case_detail(test_case, {"steps": [], "attachments": []})
    )

    assert response.execution.parameters[0]["password"] == "[REDACTED]"
    assert response.links[0]["url"] == "https://example.test"
    assert response.classification.labels == [{"name": "suite", "value": "Auth"}]
    assert response.extensions == {}


def test_source_metadata_redacts_free_form_list_values_recursively():
    value = ["password=super-secret", {"nested": ["token=secret-value"]}, ["email=user@example.com"]]

    redacted = _redact_dict_or_list(value, redact_dict)

    assert "super-secret" not in str(redacted)
    assert "secret-value" not in str(redacted)
    assert "user@example.com" not in str(redacted)


def test_redaction_replaces_deep_nested_subtrees_instead_of_leaking_them():
    value = {"level": {"level": {"level": {"password": "secret"}}}}
    current = value
    for _ in range(12):
        current = {"level": current}

    redacted = redact_dict(current)

    assert "secret" not in str(redacted)
    assert "[REDACTED]" in str(redacted)


def test_generic_step_parameter_masking_is_enforced_at_write_and_read_boundaries():
    value = [{"name": "token", "value": "secret", "mode": "hidden"}]

    persisted = _redact_dict_or_list(value, redact_dict)
    returned = _safe_step_parameters(persisted)

    assert persisted[0]["value"] is None
    assert returned[0]["value"] is None


def test_authored_steps_and_expected_results_are_optional_and_typed():
    payload = ManagedTestCaseCreate(
        project_id=uuid.uuid4(),
        title="Login smoke test",
        steps=[{"step_number": 1, "action": "Open the login page"}],
    )

    assert payload.steps is not None
    assert payload.steps[0].expected_result is None


def test_authored_parameters_are_optional_and_sensitive_values_are_supported():
    payload = ManagedTestCaseCreate(
        project_id=uuid.uuid4(),
        title="Login parameter test",
        parameters=[{"name": "password", "value": "secret", "masked": True}],
    )

    assert payload.parameters[0].masked is True


def test_rich_detail_service_honors_flag_and_returns_authored_definition():
    now = datetime.now(timezone.utc)
    test_case = SimpleNamespace(
        id=uuid.uuid4(), test_run_id=uuid.uuid4(), canonical_test_case_id=None,
        test_fingerprint="fingerprint", test_name="login works", full_name=None,
        suite_name=None, class_name=None, package_name=None, status="PASSED",
        duration_ms=1, severity=None, feature=None, story=None, epic=None, owner=None,
        tags=[], failure_category=None, error_message=None, minio_s3_prefix=None,
        has_attachments=False, step_count=0, steps_present=False, retry_count=0,
        is_flaky_run=False, stack_trace=None, source_uuid=None,
        source_history_id=None, source_test_case_id=None, parser_format="allure",
        parser_version=None, created_at=now,
    )
    managed = SimpleNamespace(
        id=uuid.uuid4(), version=2, title="login works", description="desc",
        objective="obj", preconditions=None, expected_result="dashboard",
        test_data=None, steps=[{"step_number": 1, "action": "open"}],
        parameters=[{"name": "password", "value": "secret", "masked": True}],
        test_type="functional", priority="high", severity="major", suite_name=None,
        tags=["smoke"],
    )

    class Result:
        def __init__(self, first=None, scalar=None):
            self._first, self._scalar = first, scalar

        def first(self):
            return self._first

        def scalar_one_or_none(self):
            return self._scalar

    db = SimpleNamespace(
        execute=AsyncMock(side_effect=[
            Result(first=(test_case, uuid.uuid4())),
            Result(scalar=managed),
        ])
    )
    with patch("app.services.feature_flags.is_enabled", new=AsyncMock(return_value=True)), \
            patch("app.services.runs_service.get_test_steps_tree", new=AsyncMock(return_value={"steps": [], "attachments": []})):
        payload = __import__("asyncio").run(
            get_enriched_test_case_detail(db, test_case.test_run_id, test_case.id)
        )

    assert payload["definition"]["steps"][0]["action"] == "open"
    assert payload["definition"]["parameters"][0]["value"] is None


def test_rich_detail_route_handler_returns_service_payload():
    run_id, test_id = uuid.uuid4(), uuid.uuid4()
    expected = {"id": test_id, "test_run_id": run_id, "test_name": "smoke"}
    with patch(
        "app.services.runs_service.get_enriched_test_case_detail",
        new=AsyncMock(return_value=expected),
    ):
        payload = __import__("asyncio").run(
            get_enriched_test_case_detail_endpoint(run_id, test_id, object(), None)
        )

    assert payload == expected


def test_rich_detail_endpoint_is_registered_with_versioned_response_model():
    route = next(route for route in runs_router.routes if route.path.endswith("/rich-detail"))

    assert route.methods == {"GET"}
    assert route.response_model is EnrichedTestCaseDetailResponse
