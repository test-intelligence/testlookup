"""VIZ-201 / VIZ-210 unit tests: the scope parser, authoriser, SQL builders and
the analytics error contract. The real-database proof is
``tests/integration/test_analytics_scope_postgres.py``."""
from __future__ import annotations

import inspect
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI, HTTPException, Query
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.analytics_errors import (
    AnalyticsQueryError,
    analytics_error_contract,
    analytics_query_error_handler,
    http_exception_handler,
    validation_exception_handler,
)
from app.core.release_filter import UNATTRIBUTED, release_predicate
from app.middleware.telemetry import accepted_request_id
from app.services import analytics_scope as scope_mod
from app.services.analytics_scope import (
    AnalyticsScope,
    ScopePolicy,
    ScopeRequest,
    authorize_scope,
    cache_identity,
    effective_suite_clause,
    parse_days,
    parse_project_id,
    parse_release_ids,
    parse_scope,
    parse_suite_names,
    release_filter_sql,
    row_or_live_label_clause,
    row_or_run_label_sql,
    run_label_match_sql,
    run_touches_suite_sql,
    scoped_text,
    suite_filter_sql,
    suite_keys,
    suite_match_sql,
)

WINDOWED = ScopePolicy(default_days=30, max_days=365)


def _code(fn, *args, **kwargs) -> str:
    with pytest.raises(AnalyticsQueryError) as exc:
        fn(*args, **kwargs)
    assert exc.value.status_code == 422
    return exc.value.code


# ── parsing ──────────────────────────────────────────────────────────────────


class TestProjectId:
    def test_absent_means_all_accessible_projects(self):
        assert parse_project_id(None) is None

    def test_any_uuid_spelling_is_canonicalised(self):
        pid = uuid.uuid4()
        assert parse_project_id(str(pid).upper()) == pid
        assert parse_project_id("{%s}" % pid) == pid
        assert parse_project_id(pid) == pid

    @pytest.mark.parametrize("bad", ["all", "", "undefined", "123", "' OR 1=1--"])
    def test_malformed_is_project_id_format(self, bad):
        assert _code(parse_project_id, bad) == "project_id_format"

    def test_repeated_is_a_422_pointing_at_group_by_project(self):
        with pytest.raises(AnalyticsQueryError) as exc:
            parse_project_id(str(uuid.uuid4()), repeated=2)
        assert exc.value.code == "project_single_valued"
        assert "group_by=project" in exc.value.message
        assert exc.value.param == "project_id"


class TestReleaseIds:
    def test_empty_is_no_parameter(self):
        assert parse_release_ids(None) == () and parse_release_ids([]) == ()

    def test_a_legacy_single_string_still_parses(self):
        rid = str(uuid.uuid4())
        assert parse_release_ids(rid) == (rid,)

    def test_uuid_duplicates_collapse_case_insensitively(self):
        rid = uuid.uuid4()
        assert parse_release_ids([str(rid), str(rid).upper(), str(rid)]) == (str(rid),)

    def test_the_sentinel_normalises_and_mixes_with_ids(self):
        rid = str(uuid.uuid4())
        assert parse_release_ids(["Unattributed", rid, "unattributed"]) == (UNATTRIBUTED, rid)

    @pytest.mark.parametrize("bad", ["", "abc", "unattributed-ish", "12345"])
    def test_malformed_is_release_id_format(self, bad):
        assert _code(parse_release_ids, [str(uuid.uuid4()), bad]) == "release_id_format"

    def test_cap_counts_distinct_ids(self):
        twenty = [str(uuid.uuid4()) for _ in range(20)]
        assert len(parse_release_ids(twenty + [twenty[0].upper()])) == 20
        assert _code(parse_release_ids, twenty + [str(uuid.uuid4())]) == "release_cap"


class TestSuiteNames:
    def test_awkward_names_are_kept_verbatim(self):
        names = ["a,b", "x/y", "Ünïcødé ✓", "<b>bold</b>", "n" * 500]
        assert parse_suite_names(names) == tuple(names)

    def test_length_is_code_points(self):
        assert parse_suite_names(["✓" * 500]) == ("✓" * 500,)
        assert _code(parse_suite_names, ["✓" * 501]) == "suite_name_length"

    def test_empty_value_is_a_422(self):
        assert _code(parse_suite_names, [""]) == "suite_name_length"

    def test_exact_duplicates_collapse_and_the_cap_is_50(self):
        fifty = [f"s{i}" for i in range(50)]
        assert parse_suite_names(fifty + ["s0"]) == tuple(fifty)
        assert _code(parse_suite_names, fifty + ["s50"]) == "suite_cap"


class TestDaysAndWindow:
    @pytest.mark.parametrize("bad", [0, -1, 366, True, "7", 7.0])
    def test_days_outside_range_or_not_an_int(self, bad):
        assert _code(parse_days, bad, max_days=365) == "window_days_range"

    def test_the_route_cap_is_honoured(self):
        assert parse_days(90, max_days=90) == 90
        assert _code(parse_days, 91, max_days=90) == "window_days_range"

    def test_from_to_is_refused_not_ignored(self):
        with pytest.raises(AnalyticsQueryError) as exc:
            parse_scope(policy=WINDOWED, window_from="2026-09-01", window_to="2026-09-02")
        assert exc.value.code == "window_range_unsupported"

    def test_defaults_and_required_project(self):
        parsed = parse_scope(policy=WINDOWED)
        assert parsed == ScopeRequest(None, (), (), 30)
        policy = ScopePolicy(default_days=None, project_required=True, suites=False)
        assert _code(parse_scope, policy=policy) == "missing_parameter"
        # A route without the suite dimension does not read it.
        assert parse_scope(policy=policy, project_id=str(uuid.uuid4()), suite_name=["x"]).suite_names == ()


# ── authorisation ────────────────────────────────────────────────────────────


class TestAuthorize:
    @pytest.mark.asyncio
    async def test_every_release_id_is_authorised(self):
        ids = tuple(str(uuid.uuid4()) for _ in range(3))
        release_check = AsyncMock(side_effect=lambda _db, rids, _u: list(rids))
        with patch("app.core.deps.resolve_project_scope", AsyncMock(return_value=(None, None))), \
             patch("app.core.deps.resolve_release_query_scopes", release_check):
            scope = await authorize_scope(object(), object(), ScopeRequest(None, ids, (), 30), WINDOWED)
        release_check.assert_awaited_once()
        assert tuple(release_check.await_args.args[1]) == ids
        assert scope.release_ids == ids and scope.release_arg == ids

    @pytest.mark.asyncio
    async def test_one_forbidden_id_refuses_the_request(self):
        ok, bad = str(uuid.uuid4()), str(uuid.uuid4())

        async def check(_db, rids, _user):
            if bad in rids:
                raise HTTPException(status_code=403, detail="no")
            return list(rids)

        with patch("app.core.deps.resolve_project_scope", AsyncMock(return_value=(None, None))), \
             patch("app.core.deps.resolve_release_query_scopes", check):
            with pytest.raises(HTTPException) as exc:
                await authorize_scope(object(), object(), ScopeRequest(None, (ok, bad), (), 30), WINDOWED)
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    @pytest.mark.parametrize("kinds", [
        ("ok", "ok", "ok"), ("ok", "forbidden"), ("forbidden", "unknown"),
        ("unknown", "forbidden"), ("ok", "bad", "unknown"), ("unknown", "bad"),
        ("unattributed", "ok", "ok"), ("ok", "ok_dup", "unattributed"),
        ("forbidden",), ("unknown",), ("bad",), (),
    ])
    @pytest.mark.parametrize("admin", [False, True])
    async def test_the_batch_answers_what_the_per_id_check_answers(self, kinds, admin):
        """One IN query and one membership lookup for the whole list, with
        the exact status (and the same first failing id) the sequential
        ``resolve_release_query_scope`` loop gave."""
        from app.core import deps

        mine, theirs = uuid.uuid4(), uuid.uuid4()
        owned, foreign = uuid.uuid4(), uuid.uuid4()
        releases = {owned: mine, foreign: theirs}
        spelled = {
            "ok": str(owned), "ok_dup": str(owned).upper(), "forbidden": str(foreign),
            "unknown": str(uuid.uuid4()), "bad": "not-a-uuid", "unattributed": UNATTRIBUTED,
        }
        ids = [spelled[k] for k in kinds]

        class _Rows:
            def __init__(self, rows):
                self._rows = rows

            def all(self):
                return self._rows

            def scalar_one_or_none(self):
                return self._rows[0] if self._rows else None

        class _Db:
            def __init__(self):
                self.executes = 0

            async def execute(self, stmt):
                self.executes += 1
                compiled = stmt.compile(dialect=postgresql.dialect())
                wanted = next(iter(compiled.params.values()))
                wanted = wanted if isinstance(wanted, list) else [wanted]
                if len(stmt.selected_columns) == 2:
                    assert "IN" in str(compiled), "the batch must be one IN query"
                    return _Rows([(r, releases[r]) for r in wanted if r in releases])
                return _Rows([SimpleNamespace(id=r, project_id=releases[r])
                              for r in wanted if r in releases])

        async def outcome(fn):
            try:
                return ("ok", await fn())
            except HTTPException as exc:
                return (exc.status_code, exc.detail)

        membership = AsyncMock(return_value=None if admin else {mine})
        with patch("app.core.deps.get_accessible_project_ids", membership):
            async def sequential():
                out: list[str] = []
                for rid in ids:
                    got = await deps.resolve_release_query_scope(_Db(), rid, object())
                    if got is not None and got not in out:
                        out.append(got)
                return out

            expected = await outcome(sequential)
            membership.reset_mock()
            db = _Db()
            got = await outcome(lambda: deps.resolve_release_query_scopes(db, ids, object()))
        assert got == expected, (kinds, admin)
        assert db.executes <= 1 and membership.await_count <= 1

    @pytest.mark.asyncio
    async def test_the_project_goes_through_resolve_project_scope(self):
        pid, member_of = uuid.uuid4(), {uuid.uuid4()}
        resolver = AsyncMock(return_value=(None, member_of))
        with patch("app.core.deps.resolve_project_scope", resolver):
            scope = await authorize_scope(object(), "u", ScopeRequest(None, (), ("s",), 7), WINDOWED)
        resolver.assert_awaited_once_with(scope_mod_any(), "u", None)
        assert scope.allowed_project_ids == frozenset(member_of) and scope.suite_arg == "s"
        resolver = AsyncMock(return_value=(pid, None))
        with patch("app.core.deps.resolve_project_scope", resolver):
            scope = await authorize_scope(object(), "u", ScopeRequest(pid, (), (), 7), WINDOWED)
        assert resolver.await_args.args[2] == str(pid) and scope.project == str(pid)

    @pytest.mark.asyncio
    async def test_empty_mode_denies_without_resolving_releases(self):
        empty = ScopePolicy(default_days=7, max_days=90, on_denied="empty")
        mine = uuid.uuid4()
        release_check = AsyncMock()
        with patch("app.core.deps.get_accessible_project_ids", AsyncMock(return_value={mine})), \
             patch("app.core.deps.resolve_release_query_scope", release_check):
            foreign = await authorize_scope(
                object(), object(), ScopeRequest(uuid.uuid4(), (str(uuid.uuid4()),), (), 7), empty
            )
            none = await authorize_scope(object(), object(), ScopeRequest(None, (), (), 7), empty)
            own = await authorize_scope(object(), object(), ScopeRequest(mine, (), (), 7), empty)
        assert foreign.denied and none.denied and not own.denied
        release_check.assert_not_awaited()
        with patch("app.core.deps.get_accessible_project_ids", AsyncMock(return_value=None)):
            admin = await authorize_scope(object(), object(), ScopeRequest(None, (), (), 7), empty)
        assert not admin.denied and admin.project_id is None


def scope_mod_any():
    class _Any:
        def __eq__(self, other):
            return True

    return _Any()


# ── the dependency's declared parameters ─────────────────────────────────────


def _params(policy: ScopePolicy) -> dict:
    return inspect.signature(scope_mod.analytics_scope(policy)).parameters


def test_the_dependency_declares_exactly_what_the_route_honours():
    windowed = _params(WINDOWED)
    assert {"project_id", "release_id", "suite_name", "days", "window_from", "window_to"} <= set(windowed)
    assert windowed["days"].default.default == 30
    scores = _params(ScopePolicy(default_days=None, project_required=True, suites=False))
    assert "suite_name" not in scores and "days" not in scores
    assert scores["project_id"].annotation is str
    metrics = _params(ScopePolicy(default_days=7, max_days=90, on_denied="empty"))
    assert metrics["days"].default.default == 7


# ── SQL builders ─────────────────────────────────────────────────────────────


_LEGACY_EFFECTIVE = (
    "COALESCE(CASE WHEN tr.trigger_source = 'live_stream' "
    "THEN NULLIF(TRIM(tr.primary_suite_name), '') ELSE NULL END, "
    "NULLIF(TRIM(tc.suite_name), ''))"
)


class TestSuiteBuilders:
    def test_keys_normalise_and_drop_blanks(self):
        assert suite_keys(None) == () and suite_keys("  ") == ()
        assert suite_keys(" Checkout ") == ("checkout",)
        assert suite_keys(["A", "a ", "B", " "]) == ("a", "b")

    def test_single_value_sql_is_the_pre_viz201_text(self):
        params: dict = {}
        assert suite_filter_sql(params, "Checkout") == f"AND LOWER({_LEGACY_EFFECTIVE}) = :suite_name"
        assert params == {"suite_name": "checkout"}
        params = {}
        assert suite_match_sql(params, "Checkout") == f"LOWER({_LEGACY_EFFECTIVE}) = :suite_key"
        params = {}
        assert run_label_match_sql(params, "X") == (
            "LOWER(TRIM(COALESCE(tr.primary_suite_name, ''))) = :suite_name"
        )
        params = {}
        assert row_or_run_label_sql(params, "X") == (
            "AND (LOWER(TRIM(tc.suite_name)) = :suite_name "
            "OR LOWER(TRIM(tr.primary_suite_name)) = :suite_name)"
        )

    def test_no_suite_emits_nothing_but_a_drilldown_matches_nothing(self):
        params: dict = {}
        assert suite_filter_sql(params, None) == "" and run_touches_suite_sql(params, " ") == ""
        assert row_or_run_label_sql(params, []) == "" and params == {}
        assert suite_match_sql(params, None).endswith("= :suite_key") and params == {"suite_key": ""}

    def test_several_suites_are_one_expanding_in(self):
        params: dict = {}
        sql = suite_filter_sql(params, ["A", "B"])
        assert sql.endswith("IN :suite_names") and params == {"suite_names": ["a", "b"]}
        clause = scoped_text(f"SELECT 1 FROM test_cases tc JOIN test_runs tr ON TRUE WHERE TRUE {sql}", params)
        assert clause._bindparams["suite_names"].expanding is True
        compiled = str(clause.compile(dialect=postgresql.dialect()))
        assert "POSTCOMPILE_suite_names" in compiled
        # OR within the dimension -- never an AND of two equalities.
        assert " AND LOWER" not in sql.replace("AND LOWER(COALESCE", "", 1)

    def test_scoped_text_is_plain_text_for_one_value(self):
        params: dict = {}
        sql = f"SELECT 1 WHERE TRUE {suite_filter_sql(params, 'A')}"
        assert not scoped_text(sql, params)._bindparams["suite_name"].expanding

    def test_core_clauses_use_equality_for_one_and_in_for_many(self):
        dialect = postgresql.dialect()
        one = str(row_or_live_label_clause("A").compile(dialect=dialect))
        many = str(row_or_live_label_clause(["A", "B"]).compile(dialect=dialect))
        assert " IN " not in one and one.count(" = ") >= 2
        assert many.count(" IN ") == 2
        assert effective_suite_clause(None) is None
        assert " IN " in str(effective_suite_clause(["a", "b"]).compile(dialect=dialect))


class TestReleaseBuilders:
    def test_shapes(self):
        rid, rid2 = str(uuid.uuid4()), str(uuid.uuid4())
        params: dict = {}
        assert release_filter_sql(params, None) == "" and params == {}
        assert release_filter_sql(params, rid) == "AND tr.primary_release_id = :release_id"
        assert params == {"release_id": rid}
        assert release_filter_sql({}, UNATTRIBUTED) == "AND tr.primary_release_id IS NULL"
        assert release_filter_sql({}, (rid,)) == "AND tr.primary_release_id = :release_id"
        params = {}
        assert release_filter_sql(params, (rid, rid2)) == "AND tr.primary_release_id IN :release_ids"
        assert params == {"release_ids": [rid, rid2]}
        params = {}
        assert release_filter_sql(params, (rid, UNATTRIBUTED), table_alias="x") == (
            "AND (x.primary_release_id IN :release_ids OR x.primary_release_id IS NULL)"
        )
        assert params == {"release_ids": [rid]}

    def test_never_null_tolerant(self):
        for arg in (None, str(uuid.uuid4()), UNATTRIBUTED, (str(uuid.uuid4()), UNATTRIBUTED)):
            assert ":release_id IS NULL" not in release_filter_sql({}, arg)

    def test_core_predicate_accepts_a_sequence(self):
        rid, rid2 = uuid.uuid4(), uuid.uuid4()
        dialect = postgresql.dialect()
        assert release_predicate([]) == []
        assert str(release_predicate([str(rid)])[0].compile(dialect=dialect)) == str(
            release_predicate(str(rid))[0].compile(dialect=dialect)
        )
        many = str(release_predicate([str(rid), str(rid2)])[0].compile(dialect=dialect))
        assert " IN " in many
        mixed = str(release_predicate([str(rid), "unattributed"])[0].compile(dialect=dialect))
        assert " IN " in mixed and "IS NULL" in mixed


def test_cache_identity_keeps_single_keys_and_cannot_collide():
    assert cache_identity([]) is None
    assert cache_identity(["checkout"]) == "checkout"
    multi = cache_identity(["b", "a"])
    assert multi == cache_identity(["a", "b"]) and multi.startswith(" ")
    # A normalised single key never starts with a space, so no collision.
    assert suite_keys(multi) != (multi,)


def test_scope_args_are_legacy_shaped_for_one_value():
    scope = AnalyticsScope(None, None, ("r",), ("s",), 7)
    assert scope.release_arg == "r" and scope.suite_arg == "s"
    scope = AnalyticsScope(None, None, (), (), 7)
    assert scope.release_arg is None and scope.suite_arg is None and scope.project is None


# ── the error contract (VIZ-210) ─────────────────────────────────────────────


def _app() -> FastAPI:
    app = FastAPI()
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(AnalyticsQueryError, analytics_query_error_handler)

    @app.get("/marked")
    @analytics_error_contract
    async def marked(n: int = Query(1, ge=1, le=5), fail: str = ""):
        if fail == "404":
            raise HTTPException(status_code=404, detail="Release not found")
        if fail == "rule":
            raise AnalyticsQueryError("release_cap", "too many", param="release_id", allowed={"max": 20})
        return {"n": n}

    @app.get("/plain")
    async def plain(n: int = Query(1, ge=1, le=5), fail: str = ""):
        if fail == "404":
            raise HTTPException(status_code=404, detail="Release not found")
        return {"n": n}

    return app


def test_marked_routes_get_the_contract_body_and_others_keep_fastapis():
    client = TestClient(_app())
    body = client.get("/marked?n=9").json()
    assert body["code"] == "invalid_parameter" and body["param"] == "n"
    assert body["allowed"] == {"max": 5} and body["detail"] == body["message"]
    assert "request_id" in body
    assert client.get("/marked?fail=404").json()["code"] == "not_found"
    rule = client.get("/marked?fail=rule")
    assert rule.status_code == 422 and rule.json()["code"] == "release_cap"
    assert rule.json()["allowed"] == {"max": 20}

    plain = client.get("/plain?n=9").json()
    assert set(plain) == {"detail"} and isinstance(plain["detail"], list)
    assert client.get("/plain?fail=404").json() == {"detail": "Release not found"}


@pytest.mark.parametrize("value,kept", [
    ("abc-123_x.y:z", True),
    ("x" * 128, True),
    ("x" * 129, False),
    ("has space", False),
    ("<script>", False),
    ("line\r\nbreak", False),
    ("", False),
    (None, False),
])
def test_incoming_request_ids_are_validated(value, kept):
    got = accepted_request_id(value)
    if kept:
        assert got == value
    else:
        assert got != value and uuid.UUID(got)


def test_the_unhandled_error_log_carries_the_request_id_the_body_quotes(monkeypatch):
    """The 500 body says "quote the request id"; the log line an operator
    searches must carry that same id, and the trace the body leaves out."""
    from unittest.mock import MagicMock

    from app.core import analytics_errors

    app = FastAPI()
    app.add_exception_handler(Exception, analytics_errors.unhandled_exception_handler)

    @app.get("/boom")
    @analytics_error_contract
    async def boom():
        raise RuntimeError("secret internals")

    @app.middleware("http")
    async def stamp(request, call_next):
        request.state.request_id = request.headers.get("X-Request-ID")
        return await call_next(request)

    fake = MagicMock()
    monkeypatch.setattr(analytics_errors, "logger", fake)
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/boom", headers={"X-Request-ID": "quote-me-42"})
    assert resp.status_code == 500 and resp.json()["request_id"] == "quote-me-42"
    assert "secret internals" not in resp.text

    fake.error.assert_called_once()
    (event,), fields = fake.error.call_args
    assert event == "analytics_unhandled_error"
    assert fields["request_id"] == resp.json()["request_id"]
    assert isinstance(fields["exc_info"], RuntimeError)


def test_extract_error_message_still_has_a_string_detail():
    """The SPA's interceptor reads ``detail`` (apiErrors.extractErrorMessage):
    a string there is shown verbatim, so the contract keeps it."""
    client = TestClient(_app())
    for url in ("/marked?n=9", "/marked?fail=404", "/marked?fail=rule"):
        detail = client.get(url).json()["detail"]
        assert isinstance(detail, str) and detail.strip()


def test_dependency_rejects_a_repeated_project_before_the_database():
    """The FastAPI dependency counts raw ``project_id`` values."""
    dep = scope_mod.analytics_scope(WINDOWED)
    request = SimpleNamespace(query_params=SimpleNamespace(getlist=lambda _k: ["a", "b"]))
    db = AsyncMock()
    import asyncio

    with pytest.raises(AnalyticsQueryError) as exc:
        asyncio.run(dep(request, project_id=str(uuid.uuid4()), db=db, current_user=object()))
    assert exc.value.code == "project_single_valued"
    db.execute.assert_not_called()
