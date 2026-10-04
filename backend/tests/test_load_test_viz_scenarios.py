"""Phase D L1: the viz load-test scenarios in ``scripts/load_test_concurrent.py``.

No network: every HTTP exchange goes through ``httpx.MockTransport``. What is
pinned here is what a homelab run cannot check for itself afterwards -- that
each request asked the question it claims to (only parameters the route
declares, repeated parameters in FastAPI's shape), that the viz list never
leaks into the live pytest smoke set, that the shared dev stack is refused,
and that the server-side p95 is computed the way Prometheus computes it.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import urllib.parse
from datetime import date
from pathlib import Path

import httpx
import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import load_test_concurrent as harness  # noqa: E402  (after sys.path insert)

PROJECT_ID = "11111111-1111-1111-1111-111111111111"
RELEASES = [f"00000000-0000-0000-0000-{i:012d}" for i in range(20)]
SUITES = [f"PerfSuite{i:03d}" for i in range(200)]


def _fixtures(**overrides) -> dict:
    f = {
        "project_id": PROJECT_ID,
        "project_slug": "synthetic-perf-dataset",
        "project_name": "Synthetic Perf Dataset",
        "release_ids": list(RELEASES),
        "suite_names": list(SUITES),
        "coverage_suite_key": "perfsuite000",
        "today": date(2026, 10, 4),
        "export_format": "pdf",
    }
    f.update(overrides)
    return f


def _split(path: str) -> tuple[str, list[tuple[str, str]]]:
    route, _, query = path.partition("?")
    return route, urllib.parse.parse_qsl(query, keep_blank_values=True)


# ── Param shapes ────────────────────────────────────────────────────────────


class TestPathBuilders:
    @pytest.mark.parametrize("sc", harness.VIZ_SCENARIOS, ids=lambda sc: sc.operation)
    def test_only_allow_listed_params_warm_and_cold(self, sc):
        f = _fixtures()
        allowed = harness.VIZ_ALLOWED_PARAMS[sc.route]
        for variant in [0, *range(1, 400, 7)]:
            route, pairs = _split(sc.build(f, variant))
            assert route == sc.route
            names = {name for name, _ in pairs}
            assert names <= allowed, f"{sc.operation} sent {names - allowed}"
            assert not any(n.endswith("[]") for n in names)
            assert ("project_id", PROJECT_ID) in pairs

    def test_repeated_params_are_encoded_without_brackets(self):
        path = harness._chart_releases_suites(_fixtures(), 0)
        query = path.split("?", 1)[1]
        assert f"release_id={RELEASES[0]}&release_id={RELEASES[1]}" in query
        assert "suite_name=PerfSuite000&suite_name=PerfSuite001" in query
        assert "%5B" not in query and "[" not in query
        assert "group_by=day&group_by=release" in query

    def test_viz_path_keeps_order_and_repeats(self):
        path = harness.viz_path(
            harness.CHART_DATA,
            [("metric", "pass_rate"), ("group_by", "day"), ("group_by", "suite"),
             ("release_id", "a"), ("release_id", "b"), ("top_n", None)],
        )
        assert path == (
            "/api/v1/analytics/chart-data?metric=pass_rate&group_by=day&group_by=suite"
            "&release_id=a&release_id=b"
        )

    @pytest.mark.parametrize("bad", ["release_id[]", "suite", "limit", "projectId"])
    def test_viz_path_refuses_an_undeclared_param(self, bad):
        with pytest.raises(ValueError):
            harness.viz_path(harness.CHART_DATA, [(bad, "x")])

    def test_viz_path_refuses_an_unknown_route(self):
        with pytest.raises(ValueError):
            harness.viz_path("/api/v1/analytics/not-a-route", [])

    def test_scenario_shapes_match_the_routers(self):
        f = _fixtures()
        by_name = {sc.operation: sc for sc in harness.VIZ_SCENARIOS}

        def q(name: str, variant: int = 0) -> list[tuple[str, str]]:
            return _split(by_name[name].build(f, variant))[1]

        assert ("top_n", "7") in q("viz_chart_day_suite_top7_90d")
        assert ("days", "90") in q("viz_chart_pass_rate_day_90d")
        assert ("metric", "duration_p95") in q("viz_chart_duration_p95_90d")
        assert [v for k, v in q("viz_explorer_discovery") if k == "group_by"] == ["suite", "environment"]
        assert ("include", "edges") in q("viz_failure_groups_edges_30d")
        hm = q("viz_heatmap_test_run_suite")
        assert ("kind", "test_run") in hm and any(k == "suite_name" for k, _ in hm)
        assert "runs" not in dict(q("viz_heatmap_suite_day_90d"))
        rows = dict(q("viz_chart_rows_page1"))
        assert rows["page"] == "1" and rows["bucket_day"] == "2026-10-03"
        assert dict(q("viz_coverage_map_suite"))["suite"] == "perfsuite000"
        assert ("background", "true") in q("viz_export_background")
        assert "background" not in dict(q("viz_export_sync"))

    def test_suite_day_never_exceeds_its_90_day_cap(self):
        sc = next(s for s in harness.VIZ_SCENARIOS if s.operation == "viz_heatmap_suite_day_90d")
        for variant in range(500):
            assert 1 <= int(dict(_split(sc.build(_fixtures(), variant))[1])["days"]) <= 90

    @pytest.mark.parametrize(
        "sc", [s for s in harness.VIZ_SCENARIOS if s.kind == "read"], ids=lambda sc: sc.operation
    )
    def test_cold_keys_are_distinct_and_warm_key_is_not_among_them(self, sc):
        f = _fixtures()
        warm = sc.build(f, 0)
        cold = [sc.build(f, v) for v in range(1, 161)]
        assert warm not in cold
        assert len(set(cold)) == len(cold), f"{sc.operation} repeats a cold key"

    def test_missing_fixtures_skip_rather_than_guess(self):
        sc = next(s for s in harness.VIZ_SCENARIOS if s.operation == "viz_chart_2releases_2suites_90d")
        with pytest.raises(harness.FixtureMissing):
            sc.build(_fixtures(release_ids=[RELEASES[0]]), 0)
        drill = next(s for s in harness.VIZ_SCENARIOS if s.operation == "viz_coverage_map_suite")
        with pytest.raises(harness.FixtureMissing):
            drill.build(_fixtures(coverage_suite_key=None), 0)


class TestBudgets:
    def test_budgets_follow_the_cache_mode(self):
        by_name = {sc.operation: sc for sc in harness.VIZ_SCENARIOS}
        assert by_name["viz_chart_pass_rate_day_90d"].budget_for("cold") == ("chart_data_rate", 500)
        assert by_name["viz_chart_duration_p95_90d"].budget_for("cold") == ("chart_data", 1500)
        assert by_name["viz_heatmap_suite_day_90d"].budget_for("cold") == ("analytics_heatmap", 800)
        assert by_name["viz_chart_rows_page1"].budget_for("cold") == ("chart_rows", 300)
        assert by_name["viz_chart_pass_rate_day_90d"].budget_for("warm") == ("analytics_cached", 50)
        # Outside the analytics read layer: no cached budget to hold it to.
        assert by_name["viz_legacy_metrics_summary"].budget_for("warm") == ("", 0)
        assert by_name["viz_export_sync"].budget_for("-") == ("", 0)


# ── Separation from SCENARIOS ───────────────────────────────────────────────


class TestSeparation:
    def test_viz_scenarios_are_disjoint_from_scenarios(self):
        names = {sc.operation for sc in harness.SCENARIOS}
        viz = {sc.operation for sc in harness.VIZ_SCENARIOS}
        assert not names & viz
        assert harness.RATE_LIMIT_BURST not in names
        assert all(isinstance(sc, harness.Scenario) for sc in harness.SCENARIOS)
        assert not any(isinstance(sc, harness.VizScenario) for sc in harness.SCENARIOS)
        assert not any(
            sc.path_fn({"project_ids": ["p"], "run_ids": ["r"]}).startswith(route)
            for sc in harness.SCENARIOS
            for route in harness.VIZ_ALLOWED_PARAMS
        )

    def test_operation_names_are_unique(self):
        names = [sc.operation for sc in harness.VIZ_SCENARIOS]
        assert len(names) == len(set(names))

    def test_only_selects_and_rejects_unknown_names(self):
        scs, burst = harness.select_viz_scenarios("viz_test_scatter_30d,rate_limit_burst")
        assert [s.operation for s in scs] == ["viz_test_scatter_30d"] and burst
        scs, burst = harness.select_viz_scenarios(None)
        assert len(scs) == len(harness.VIZ_SCENARIOS) and burst
        with pytest.raises(ValueError):
            harness.select_viz_scenarios("viz_nope")


# ── Safety guard ────────────────────────────────────────────────────────────


class TestTargetGuard:
    @pytest.mark.parametrize(
        "url",
        ["http://localhost:8000", "http://127.0.0.1:8000/", "http://[::1]:8000",
         "http://0.0.0.0:8000", "https://localhost:8000", "http://LOCALHOST:8000"],
    )
    def test_the_shared_dev_stack_is_refused_without_the_flag(self, url):
        with pytest.raises(harness.UnsafeTarget):
            harness.check_target(url)
        harness.check_target(url, allow_local_dev_stack=True)

    @pytest.mark.parametrize(
        "url",
        ["http://127.0.0.1:18000", "https://testlookup.local", "http://testlookup.local:8000",
         "http://192.168.1.50:8000"],
    )
    def test_other_targets_are_allowed(self, url):
        harness.check_target(url)

    @pytest.mark.parametrize(
        "url",
        ["postgresql://u:p@db:5432/x", "redis://localhost:6379/0", "http://db.local:5432",
         "http://localhost:6379", "ftp://example.com"],
    )
    def test_non_http_and_datastore_ports_are_refused_even_with_the_flag(self, url):
        with pytest.raises(harness.UnsafeTarget):
            harness.check_target(url, allow_local_dev_stack=True)

    def test_the_cli_refuses_before_any_network(self, monkeypatch, capsys):
        async def boom(*_a, **_k):
            raise AssertionError("the guard must refuse before any request")

        monkeypatch.setattr(harness, "fetch_dev_token", boom)
        monkeypatch.setattr(harness, "resolve_viz_fixtures", boom)
        args = _cli_args("--base-url", "http://localhost:8000")
        assert asyncio.run(harness.cmd_bench(args)) == 2
        assert "shared dev stack" in capsys.readouterr().err

    def test_the_metrics_url_is_guarded_too(self, monkeypatch):
        async def boom(*_a, **_k):
            raise AssertionError("no network")

        monkeypatch.setattr(harness, "fetch_dev_token", boom)
        args = _cli_args("--base-url", "http://127.0.0.1:18000", "--metrics-url", "http://localhost:6379")
        assert asyncio.run(harness.cmd_bench_viz(args)) == 2


def _cli_args(*argv: str) -> argparse.Namespace:
    """``bench --suite viz ...`` through the script's real parser."""
    return harness.build_parser().parse_args(["bench", "--suite", "viz", *argv])


# ── Prometheus diff ─────────────────────────────────────────────────────────

LES = ["0.01", "0.05", "0.1", "0.25", "0.5", "1.0", "2.0", "5.0", "10.0", "+Inf"]
ROUTE = "/api/v1/analytics/chart-data"


def _scrape(miss: list[int], hit: list[int], degraded: float = 0.0, extra: str = "") -> str:
    lines = [
        "# HELP testlookup_analytics_query_duration_seconds Analytics read latency",
        "# TYPE testlookup_analytics_query_duration_seconds histogram",
    ]
    for outcome, cumulative in (("miss", miss), ("hit", hit)):
        for le, c in zip(LES, cumulative):
            lines.append(
                f'testlookup_analytics_query_duration_seconds_bucket{{le="{le}",outcome="{outcome}",'
                f'route="{ROUTE}"}} {float(c)}'
            )
        lines.append(
            f'testlookup_analytics_query_duration_seconds_count{{outcome="{outcome}",route="{ROUTE}"}} '
            f"{float(cumulative[-1])}"
        )
        lines.append(
            f'testlookup_analytics_query_duration_seconds_sum{{outcome="{outcome}",route="{ROUTE}"}} 1.0'
        )
    lines.append(f'testlookup_analytics_read_degraded_total{{reason="cache_timeout"}} {degraded}')
    return "\n".join(lines) + "\n" + extra


class TestPrometheusDiff:
    def test_server_p95_hit_ratio_and_degraded_from_two_scrapes(self):
        before = harness.parse_prometheus_text(_scrape(
            miss=[1, 2, 3, 4, 5, 5, 5, 5, 5, 5], hit=[7] * 10, degraded=2,
        ))
        # 100 new misses: 0 / 10 / 50 / 90 / 100 cumulative across 0.01..0.5 s.
        after = harness.parse_prometheus_text(_scrape(
            miss=[1, 12, 53, 94, 105, 105, 105, 105, 105, 105], hit=[57] * 10, degraded=5,
        ))
        diff = harness.diff_analytics_metrics(before, after)
        route = diff["routes"][ROUTE]
        assert route["miss"]["count"] == 100
        # rank 95 lies in (0.25, 0.5]: 0.25 + 0.25 * (95 - 90) / 10 = 0.375 s
        assert route["miss"]["p95_ms"] == 375.0
        assert route["hit"]["count"] == 50
        assert route["hit"]["p95_ms"] == pytest.approx(9.5)
        assert route["all"]["count"] == 150
        assert route["all"]["hit_ratio"] == pytest.approx(0.333)
        assert route["all"]["timeouts"] == 0 and route["all"]["errors"] == 0
        assert diff["degraded"] == {"cache_timeout": 3}
        assert diff["degraded_total"] == 3

    def test_untouched_series_are_left_out(self):
        text = _scrape(miss=[1] * 10, hit=[2] * 10)
        diff = harness.diff_analytics_metrics(
            harness.parse_prometheus_text(text), harness.parse_prometheus_text(text)
        )
        assert diff["routes"] == {} and diff["degraded_total"] == 0

    def test_a_counter_reset_uses_the_post_reset_value(self):
        before = harness.parse_prometheus_text(_scrape(miss=[50] * 10, hit=[0] * 10))
        after = harness.parse_prometheus_text(_scrape(miss=[0, 4, 4, 4, 4, 4, 4, 4, 4, 4], hit=[0] * 10))
        diff = harness.diff_analytics_metrics(before, after)
        assert diff["routes"][ROUTE]["miss"]["count"] == 4

    def test_histogram_quantile_edges(self):
        inf = float("inf")
        assert harness.histogram_quantile(0.95, []) is None
        assert harness.histogram_quantile(0.95, [(0.1, 0), (inf, 0)]) is None
        # Everything above the last finite bound: the bound, as Prometheus does.
        assert harness.histogram_quantile(0.95, [(0.1, 0), (10.0, 0), (inf, 5)]) == 10.0
        # Bucket order in the input does not matter.
        assert harness.histogram_quantile(0.5, [(inf, 10), (0.1, 10), (0.05, 0)]) == pytest.approx(0.075)

    def test_parser_reads_escaped_labels_and_skips_comments(self):
        samples = harness.parse_prometheus_text(
            '# TYPE x counter\nx_total{a="q\\"uote",b="2"} 3\nbad line here\ny 4.5\n'
        )
        assert samples[("x_total", (("a", 'q\\"uote'), ("b", "2")))] == 3.0
        assert samples[("y", ())] == 4.5


# ── Principals ──────────────────────────────────────────────────────────────


class TestTokens:
    def test_round_robin(self):
        pool = harness.TokenPool(["a", "b", "c"])
        assert [pool.next() for _ in range(7)] == ["a", "b", "c", "a", "b", "c", "a"]
        assert pool.first() == "a" and len(pool) == 3

    def test_empty_pool_is_refused(self):
        with pytest.raises(ValueError):
            harness.TokenPool([])

    def test_tokens_file_skips_blanks_and_comments(self, tmp_path):
        p = tmp_path / "tokens.txt"
        p.write_bytes(b"# load users\n tok-1 \n\ntok-2\n# tok-3\n")
        assert harness.load_tokens_file(str(p)) == ["tok-1", "tok-2"]
        empty = tmp_path / "empty.txt"
        empty.write_bytes(b"\n# nothing\n")
        with pytest.raises(ValueError):
            harness.load_tokens_file(str(empty))

    def test_requests_round_robin_across_principals(self):
        seen: list[str] = []
        paths: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.headers["Authorization"])
            paths.append(str(request.url))
            return httpx.Response(200, json={}, headers={"X-Analytics-Cache": "miss"})

        sc = next(s for s in harness.VIZ_SCENARIOS if s.operation == "viz_chart_pass_rate_day_90d")
        result = asyncio.run(harness.run_viz_cell(
            "http://load.test", harness.TokenPool(["t1", "t2", "t3"]), _fixtures(), sc,
            cache="cold", concurrency=1, iterations=6, warmup=5,
            key_counter=iter(range(1, 100)), transport=httpx.MockTransport(handler),
        ))
        assert seen == ["Bearer t1", "Bearer t2", "Bearer t3"] * 2
        assert len(set(paths)) == 6, "cold mode re-asked a key"
        assert result.n == 6 and result.status_counts == {"200": 6}
        assert result.cache_headers == {"miss": 6}
        assert (result.budget_key, result.budget_p95_ms) == ("chart_data_rate", 500)

    def test_warm_mode_repeats_one_key_after_warmup(self):
        paths: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            paths.append(str(request.url))
            return httpx.Response(429 if len(paths) == 7 else 200, json={})

        sc = next(s for s in harness.VIZ_SCENARIOS if s.operation == "viz_heatmap_suite_day_90d")
        result = asyncio.run(harness.run_viz_cell(
            "http://load.test", harness.TokenPool(["t"]), _fixtures(), sc,
            cache="warm", concurrency=2, iterations=3, warmup=1,
            key_counter=iter(()), transport=httpx.MockTransport(handler),
        ))
        assert len(paths) == 8 and len(set(paths)) == 1
        assert result.n == 6
        assert result.status_counts == {"200": 5, "429": 1}
        assert result.verdict() == "FAIL"  # a 429 is not a pass, however fast
        assert result.budget_key == "analytics_cached"


# ── Rate-limit burst ────────────────────────────────────────────────────────


class TestBurst:
    def test_summarize_burst(self):
        ok = (200, {"content-type": "application/json"}, None)
        limited = (429, {"Retry-After": "42"}, {"code": "rate_limited", "message": "x"})
        result = harness.summarize_burst([ok] * 120 + [limited] * 30, 3.456)
        assert result.sent == 150 and result.rate_limited == 30
        assert result.retry_after_present is True
        assert result.retry_after_values == ["42"]
        assert result.codes == ["rate_limited"]
        assert result.first_429_at == 120
        assert result.elapsed_s == 3.46
        assert result.status_counts == {"200": 120, "429": 30}

    def test_a_429_without_retry_after_is_reported(self):
        result = harness.summarize_burst(
            [(429, {"Retry-After": "5"}, {"code": "rate_limited"}), (429, {}, None)], 1.0
        )
        assert result.retry_after_present is False and result.codes == ["rate_limited"]

    def test_no_429_is_none_not_true(self):
        result = harness.summarize_burst([(200, {}, None)] * 3, 1.0)
        assert result.rate_limited == 0 and result.retry_after_present is None

    def test_burst_against_a_mock_limiter(self):
        count = {"n": 0}
        tokens: set[str] = set()

        def handler(request: httpx.Request) -> httpx.Response:
            count["n"] += 1
            tokens.add(request.headers["Authorization"])
            assert request.url.path == harness.CHART_DATA
            if count["n"] > 120:
                return httpx.Response(429, headers={"Retry-After": "37"},
                                      json={"code": "rate_limited", "message": "Too many"})
            return httpx.Response(200, json={})

        result = asyncio.run(harness.run_rate_limit_burst(
            "http://load.test", "solo", _fixtures(), transport=httpx.MockTransport(handler),
        ))
        assert tokens == {"Bearer solo"}
        assert result.sent == 150 and result.rate_limited == 30
        assert result.retry_after_present is True and result.codes == ["rate_limited"]
        assert result.to_dict()["first_429_at"] == 120


# ── Fixtures, exports, report ───────────────────────────────────────────────


def _api(handler_overrides: dict | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/v1/projects":
            return httpx.Response(200, json=[
                {"id": "other", "slug": "something-else", "name": "Other"},
                {"id": PROJECT_ID, "slug": "synthetic-perf-dataset", "name": "Synthetic"},
            ])
        if path == "/api/v1/releases":
            assert request.url.params["project_id"] == PROJECT_ID
            return httpx.Response(200, json={"items": [
                {"id": "r-few", "test_run_count": 3}, {"id": "r-many", "test_run_count": 300},
            ], "total": 2})
        if path == "/api/v1/suites":
            return httpx.Response(200, json={"items": [{"name": "B"}, {"name": "A"}], "total": 2})
        if path == harness.COVERAGE_MAP:
            return httpx.Response(200, json={"kind": "tree", "nodes": [
                {"id": "root", "parent_id": None}, {"id": "s:perfsuite007", "parent_id": "root"},
            ]})
        raise AssertionError(f"unexpected request {request.url}")

    return httpx.MockTransport(handler)


class TestFixturesAndFlows:
    def test_fixtures_resolve_from_the_api(self):
        async def go():
            async with httpx.AsyncClient(base_url="http://load.test", transport=_api()) as client:
                return await harness.resolve_viz_fixtures(client, "synthetic-perf-dataset")

        f = asyncio.run(go())
        assert f["project_id"] == PROJECT_ID and f["project_name"] == "Synthetic"
        assert f["release_ids"] == ["r-many", "r-few"]
        assert f["suite_names"] == ["A", "B"]
        assert f["coverage_suite_key"] == "perfsuite007"

    def test_an_unknown_slug_is_an_error(self):
        async def go():
            async with httpx.AsyncClient(base_url="http://load.test", transport=_api()) as client:
                return await harness.resolve_viz_fixtures(client, "nope")

        with pytest.raises(RuntimeError, match="no project with slug"):
            asyncio.run(go())

    def test_background_export_is_polled_to_completed(self):
        polls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "POST":
                assert request.url.params["background"] == "true"
                return httpx.Response(202, json={
                    "delivery": "background", "estimated_tests": 9,
                    "export": {"id": "e1", "status": "queued"}, "dispatched": True,
                })
            assert request.url.path == f"{harness.SUMMARY_EXPORTS}/e1"
            polls["n"] += 1
            done = polls["n"] >= 2
            return httpx.Response(200, json={
                "id": "e1", "status": "completed" if done else "running",
                "requested_at": "2026-10-04T10:00:00Z",
                "finished_at": "2026-10-04T10:00:12.5Z" if done else None,
            })

        sc = next(s for s in harness.VIZ_SCENARIOS if s.operation == "viz_export_background")
        cell = asyncio.run(harness.run_export_cell(
            "http://load.test", harness.TokenPool(["t"]), _fixtures(), sc,
            runs=1, poll_interval=0, timeout=30, transport=httpx.MockTransport(handler),
        ))
        flow = cell.flow[0]
        assert flow["final_status"] == "completed" and flow["polls"] == 2
        assert flow["job_s"] == 12.5
        assert cell.status_counts == {"200": 2, "202": 1}

    def test_sync_export_downloads_with_the_same_scope(self):
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            if request.method == "POST":
                return httpx.Response(200, json={"delivery": "download", "estimated_tests": 9})
            return httpx.Response(200, content=b"%PDF-1.7 ...")

        sc = next(s for s in harness.VIZ_SCENARIOS if s.operation == "viz_export_sync")
        cell = asyncio.run(harness.run_export_cell(
            "http://load.test", harness.TokenPool(["t"]), _fixtures(), sc,
            runs=1, poll_interval=0, timeout=30, transport=httpx.MockTransport(handler),
        ))
        assert seen[1].url.path == harness.SUMMARY_PDF
        assert seen[1].url.params["project_id"] == PROJECT_ID and seen[1].url.params["days"] == "7"
        assert cell.flow[0]["final_status"] == "completed" and cell.flow[0]["size_bytes"] == 12

    def test_markdown_has_one_row_per_cell_and_the_burst(self):
        cell = harness.VizCellResult("viz_x", ROUTE, "cold", 4, latencies=[10.0, 20.0],
                                     status_counts={"200": 2}, budget_key="chart_data_rate",
                                     budget_p95_ms=500)
        report = {
            "base_url": "http://load.test", "metrics_url": None, "timestamp": "t",
            "project": {"name": "S", "slug": "s", "id": PROJECT_ID, "releases": 2, "suites": 3},
            "principals": 4, "iterations_per_worker": 10,
            "results": [cell.to_dict()],
            "rate_limit_burst": harness.summarize_burst([(429, {"Retry-After": "1"}, {"code": "rate_limited"})], 1.0).to_dict(),
            "server_totals": None,
        }
        md = harness.render_viz_markdown(report)
        assert "| viz_x | cold | 4 | 2 | 20.0 | 20.0 | 20.0 | 20.0 | - | - |" in md
        assert "chart_data_rate 500 | PASS |" in md
        assert "429s: 1" in md and "rate_limited" in md
        json.dumps(report)  # the JSON writer's input is serialisable
