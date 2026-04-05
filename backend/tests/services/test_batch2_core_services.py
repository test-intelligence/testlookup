from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import uuid

import pytest

def test_parse_agent_output_falls_back_on_non_json():
    with patch.dict(
        "sys.modules",
        {
            "app.db.mongo": SimpleNamespace(Collections=SimpleNamespace(AI_ANALYSIS_PAYLOADS="ai"), get_mongo_db=lambda: {}),
            "app.tools.fetch_stacktrace": SimpleNamespace(fetch_allure_stacktrace=AsyncMock()),
            "app.tools.fetch_rest_payload": SimpleNamespace(fetch_rest_api_payload=AsyncMock()),
            "app.tools.query_splunk": SimpleNamespace(query_splunk_logs=AsyncMock()),
            "app.tools.check_flakiness": SimpleNamespace(check_test_flakiness=AsyncMock()),
            "app.tools.analyze_ocp": SimpleNamespace(analyze_openshift_pod_events=AsyncMock()),
        },
        clear=False,
    ):
        from app.services.agent import _parse_agent_output
    parsed = _parse_agent_output("not-json")
    assert parsed["failure_category"] == "UNKNOWN"
    assert parsed["requires_human_review"] is True


@pytest.mark.asyncio
async def test_run_triage_agent_fast_path_skips_react():
    store_mock = AsyncMock()
    fast_classify = AsyncMock(return_value={"confidence_score": 99})
    fake_classifier = SimpleNamespace(FastClassifier=SimpleNamespace(classify=fast_classify))
    with patch.dict(
        "sys.modules",
        {
            "app.db.mongo": SimpleNamespace(Collections=SimpleNamespace(AI_ANALYSIS_PAYLOADS="ai"), get_mongo_db=lambda: {}),
            "app.tools.fetch_stacktrace": SimpleNamespace(fetch_allure_stacktrace=AsyncMock()),
            "app.tools.fetch_rest_payload": SimpleNamespace(fetch_rest_api_payload=AsyncMock()),
            "app.tools.query_splunk": SimpleNamespace(query_splunk_logs=AsyncMock()),
            "app.tools.check_flakiness": SimpleNamespace(check_test_flakiness=AsyncMock()),
            "app.tools.analyze_ocp": SimpleNamespace(analyze_openshift_pod_events=AsyncMock()),
            "app.services.training.classifier": fake_classifier,
        },
        clear=False,
    ):
        from app.services.agent import run_triage_agent
        with patch("app.services.agent._store_audit_trail", new=store_mock):
            result = await run_triage_agent("tc1", "testA", error_message="boom")
    assert result["confidence_score"] == 99
    store_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_model_registry_promote_and_status(fake_redis):
    from app.services.model_registry import ModelRegistry
    with patch("app.services.model_registry.get_redis", return_value=fake_redis):
        await ModelRegistry.promote("classifier", "m1", {"accuracy": 0.9})
        active = await ModelRegistry.get_active_model("classifier")
        status = await ModelRegistry.get_all_status()
        await ModelRegistry.retire("classifier")
    assert active == "m1"
    assert status["classifier"]["active_model"] == "m1"


def test_metrics_compute_readiness_thresholds():
    with patch.dict(
        "sys.modules",
        {"app.models.postgres": SimpleNamespace(Defect=object, TestCase=object, TestRun=object, TestStatus=object)},
        clear=False,
    ):
        from app.services import metrics_service
    assert metrics_service._compute_readiness(96, 0, 1) == "GREEN"
    assert metrics_service._compute_readiness(86, 5, 10) == "AMBER"
    assert metrics_service._compute_readiness(70, 10, 10) == "RED"


@pytest.mark.asyncio
async def test_release_linker_link_run_idempotent():
    class FakeLink:
        release_id = object()
        test_run_id = object()

        def __init__(self, **kwargs):
            self.data = kwargs

    def fake_select(*args, **kwargs):
        return SimpleNamespace(where=lambda *a, **k: None)

    class FakeDB:
        def __init__(self):
            self.added = []
            self.exists = None

        async def execute(self, stmt):
            return SimpleNamespace(scalar_one_or_none=lambda: self.exists)

        def add(self, obj):
            self.added.append(obj)

    with patch.dict(
        "sys.modules",
        {"app.models.postgres": SimpleNamespace(Release=object, ReleaseTestRunLink=FakeLink)},
        clear=False,
    ):
        from app.services import release_linker
        db = FakeDB()
        with patch("app.services.release_linker.select", new=fake_select):
            created = await release_linker.link_run_to_release(db, uuid.uuid4(), uuid.uuid4())
        assert created is True
        assert len(db.added) == 1
        db.exists = object()
        with patch("app.services.release_linker.select", new=fake_select):
            created_again = await release_linker.link_run_to_release(db, uuid.uuid4(), uuid.uuid4())
        assert created_again is False


def test_ingestion_make_test_fingerprint_deterministic():
    with patch.dict(
        "sys.modules",
        {
            "app.db.postgres": SimpleNamespace(AsyncSessionLocal=None),
            "app.models.schemas": SimpleNamespace(SentinelFile=object),
            "app.models.postgres": SimpleNamespace(
                LaunchStatus=SimpleNamespace(PASSED="PASSED", FAILED="FAILED", IN_PROGRESS="IN_PROGRESS"),
                TestCase=object,
                TestCaseHistory=object,
                TestRun=object,
                TestStatus=SimpleNamespace(PASSED="passed", FAILED="failed", BROKEN="broken", SKIPPED="skipped", UNKNOWN="unknown"),
            ),
            "app.db.mongo": SimpleNamespace(Collections=SimpleNamespace(RAW_ALLURE_JSON="raw_allure_json"), get_mongo_db=lambda: {}),
            "app.services.testng_parser": SimpleNamespace(parse_testng_xml=lambda *_: []),
        },
        clear=False,
    ):
        from app.services import ingestion
    one = ingestion.make_test_fingerprint("test_login", "A")
    two = ingestion.make_test_fingerprint("test_login", "A")
    three = ingestion.make_test_fingerprint("test_login", "B")
    assert one == two
    assert one != three


@pytest.mark.asyncio
async def test_ingestion_store_raw_allure_batch_falls_back():
    with patch.dict(
        "sys.modules",
        {
            "app.db.postgres": SimpleNamespace(AsyncSessionLocal=None),
            "app.models.schemas": SimpleNamespace(SentinelFile=object),
            "app.models.postgres": SimpleNamespace(
                LaunchStatus=SimpleNamespace(PASSED="PASSED", FAILED="FAILED", IN_PROGRESS="IN_PROGRESS"),
                TestCase=object,
                TestCaseHistory=object,
                TestRun=object,
                TestStatus=SimpleNamespace(PASSED="passed", FAILED="failed", BROKEN="broken", SKIPPED="skipped", UNKNOWN="unknown"),
            ),
            "app.db.mongo": SimpleNamespace(Collections=SimpleNamespace(RAW_ALLURE_JSON="raw_allure_json"), get_mongo_db=lambda: {}),
            "app.services.testng_parser": SimpleNamespace(parse_testng_xml=lambda *_: []),
        },
        clear=False,
    ):
        from app.services import ingestion
        collection = SimpleNamespace(bulk_write=AsyncMock(side_effect=RuntimeError("x")))
        mongo = {"raw_allure_json": collection}
        docs = [({"allure_uuid": "u1", "test_run_id": "r1", "test_name": "t1"}, {"a": 1})]
        fallback = AsyncMock()
        with (
            patch.dict("sys.modules", {"pymongo": SimpleNamespace(UpdateOne=lambda *a, **k: object())}, clear=False),
            patch.object(ingestion, "Collections", SimpleNamespace(RAW_ALLURE_JSON="raw_allure_json")),
            patch.object(ingestion, "get_mongo_db", return_value=mongo),
            patch.object(ingestion, "_store_raw_allure", new=fallback),
        ):
            await ingestion._store_raw_allure_batch(docs)
    fallback.assert_awaited_once()
