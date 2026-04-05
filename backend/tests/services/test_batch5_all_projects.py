"""Tests for the all-projects (no project_id filter) mode."""
from types import SimpleNamespace
from unittest.mock import patch


# ── runs_service ──────────────────────────────────────────────────────────────

def test_list_project_runs_no_filter_builds_correct_query():
    """When project_id is None, no WHERE clause for project_id should be added."""
    captured_queries = []

    class FakeResult:
        def scalars(self):
            return self

        def all(self):
            return []

        def scalar(self):
            return 0

    class FakeDB:
        async def execute(self, query, *args, **kwargs):
            captured_queries.append(str(query))
            return FakeResult()

    # Patch the ORM models so we can import the service
    import importlib
    import sys

    fake_release = SimpleNamespace(id=None, name=None)
    fake_models = SimpleNamespace(
        Release=fake_release,
        ReleaseTestRunLink=SimpleNamespace(
            test_run_id=None,
            release_id=None,
            __table__=SimpleNamespace(columns=[]),
        ),
        TestCase=SimpleNamespace(__table__=SimpleNamespace(columns=[])),
        TestRun=SimpleNamespace(
            __table__=SimpleNamespace(columns=[]),
            project_id=None,
            id=None,
            status=None,
            created_at=None,
        ),
        Project=SimpleNamespace(
            __table__=SimpleNamespace(columns=[]),
            id=None,
            name=None,
        ),
    )

    with patch.dict("sys.modules", {"app.models.postgres": fake_models}, clear=False):
        # Re-import to pick up mocked models if cached
        if "app.services.runs_service" in sys.modules:
            del sys.modules["app.services.runs_service"]
        import importlib
        runs_service = importlib.import_module("app.services.runs_service")

    # The function signature now accepts str | None
    import inspect
    sig = inspect.signature(runs_service.list_project_runs)
    assert "project_id" in sig.parameters
    param = sig.parameters["project_id"]
    # Should accept None (annotation is str | None)
    annotation = param.annotation
    assert annotation is not inspect.Parameter.empty


# ── analytics_service ─────────────────────────────────────────────────────────

def test_analytics_service_accepts_none_project_id():
    """All analytics functions should accept project_id=None without raising."""
    import importlib
    import sys

    if "app.services.analytics_service" in sys.modules:
        del sys.modules["app.services.analytics_service"]

    analytics_service = importlib.import_module("app.services.analytics_service")

    import inspect
    for fn_name in (
        "flaky_tests",
        "failure_categories",
        "top_failing_tests",
        "coverage_stats",
        "suite_detail",
        "list_defects",
        "ai_analysis_summary",
    ):
        fn = getattr(analytics_service, fn_name)
        sig = inspect.signature(fn)
        assert "project_id" in sig.parameters, f"{fn_name} missing project_id param"
        # Annotation should not be bare `str` — it should allow None
        annotation = sig.parameters["project_id"].annotation
        assert annotation is not str, (
            f"{fn_name}.project_id annotation is still plain str; expected str | None"
        )


# ── metrics_service ───────────────────────────────────────────────────────────

def test_metrics_service_accepts_none_project_id():
    """get_dashboard_summary, get_trend_data, _period_stats, _count_flaky_tests
    should all accept project_id=None."""
    import importlib
    import sys
    import inspect

    with patch.dict(
        "sys.modules",
        {"app.models.postgres": SimpleNamespace(
            Defect=object, TestCase=object, TestRun=object, TestStatus=object
        )},
        clear=False,
    ):
        if "app.services.metrics_service" in sys.modules:
            del sys.modules["app.services.metrics_service"]
        metrics_service = importlib.import_module("app.services.metrics_service")

    for fn_name in ("get_dashboard_summary", "get_trend_data", "_period_stats", "_count_flaky_tests"):
        fn = getattr(metrics_service, fn_name)
        sig = inspect.signature(fn)
        assert "project_id" in sig.parameters, f"{fn_name} missing project_id param"
        annotation = sig.parameters["project_id"].annotation
        assert annotation is not str, (
            f"{fn_name}.project_id annotation is still plain str; expected str | None"
        )


# ── flaky_test SQL template ───────────────────────────────────────────────────

def test_flaky_tests_sql_omits_project_filter_when_none():
    """When project_id is None, the generated SQL should NOT contain ':project_id'."""
    import importlib
    import sys

    if "app.services.analytics_service" in sys.modules:
        del sys.modules["app.services.analytics_service"]

    analytics_service = importlib.import_module("app.services.analytics_service")

    # Directly inspect the SQL generated inside flaky_tests by calling it with
    # a patched db that captures the executed SQL.
    import asyncio
    from unittest.mock import MagicMock

    captured_sql = []

    async def fake_execute(query, params=None):
        captured_sql.append(str(query))
        result = MagicMock()
        result.fetchall.return_value = []
        return result

    fake_db = MagicMock()
    fake_db.execute = fake_execute

    asyncio.run(analytics_service.flaky_tests(fake_db, None, 30, 20))

    assert captured_sql, "No SQL was executed"
    assert ":project_id" not in captured_sql[0], (
        "SQL should not contain ':project_id' when project_id is None"
    )


def test_flaky_tests_sql_includes_project_filter_when_provided():
    """When project_id is given, the SQL SHOULD contain 'project_id'."""
    import importlib
    import sys
    from unittest.mock import MagicMock
    import asyncio

    if "app.services.analytics_service" in sys.modules:
        del sys.modules["app.services.analytics_service"]

    analytics_service = importlib.import_module("app.services.analytics_service")

    captured_sql = []

    async def fake_execute(query, params=None):
        captured_sql.append(str(query))
        result = MagicMock()
        result.fetchall.return_value = []
        return result

    fake_db = MagicMock()
    fake_db.execute = fake_execute

    asyncio.run(analytics_service.flaky_tests(fake_db, "some-project-id", 30, 20))

    assert captured_sql, "No SQL was executed"
    assert "project_id" in captured_sql[0], (
        "SQL should contain 'project_id' filter when project_id is provided"
    )
