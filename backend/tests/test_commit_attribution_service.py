"""Tests for ``services.commit_attribution_service`` — Epic 8 US-8.1/US-8.2.

Covers:

* the deterministic suspect-ranking heuristic (path overlap dominates,
  recency breaks ties, rationale is inspectable, no-overlap degrades to a
  recency ordering, author-prior nudge),
* locator derivation from stack traces + class/package names,
* supplied-commit normalization + bounds,
* commit deep-link derivation (github.com + GHE + no-repo),
* the range read model's honest ``available`` flag,
* the connector fetch (compare + per-commit files, repo-mismatch, no-PAT,
  SSRF block) with the GitHub HTTP layer mocked at ``svc._gh_get``,
* the resolve-and-store orchestration (supplied wins, connector, unavailable),
* the migration 0110 contract.

DB egress is avoided by patching the service's own read/write helpers and a
fake ``AsyncSessionLocal`` — mirroring ``test_github_pr_comment.py``.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import commit_attribution_service as svc


PROJECT_ID = uuid.uuid4()
RUN_ID = uuid.uuid4()


# ── Fakes ────────────────────────────────────────────────────────────────────


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data
        self.text = text
        self.content = b"x" if json_data is not None else b""

    def json(self):
        return self._json


def _tc(**overrides):
    base = dict(
        id=uuid.uuid4(),
        test_run_id=RUN_ID,
        test_name="test_charge_declines",
        class_name=None,
        package_name=None,
        stack_trace=None,
        error_message=None,
        status="FAILED",
        test_fingerprint="fp1",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _run(**overrides):
    base = dict(
        id=RUN_ID,
        project_id=PROJECT_ID,
        commit_hash="h" * 40,
        ci_repo="acme/webapp",
        branch="main",
        failed_tests=1,
        broken_tests=0,
        status="COMPLETED",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _commit(sha, files, author="Al", message="msg"):
    return {"sha": sha, "author": author, "message": message, "files": files, "committed_at": None}


# ── Locator derivation ───────────────────────────────────────────────────────


def test_build_locator_from_trace():
    loc = svc.build_locator([
        _tc(stack_trace='File "services/payments/test_charge.py", line 10')
    ])
    assert "charge" in loc.stems  # test-affix stripped
    assert "payments" in loc.module_tokens
    assert loc.has_signal


def test_build_locator_from_package_and_class():
    loc = svc.build_locator([
        _tc(class_name="com.acme.payments.ChargeTest", package_name="com.acme.payments")
    ])
    assert "payments" in loc.module_tokens
    assert "charge" in loc.stems  # ChargeTest → charge
    # noise tokens dropped
    assert "com" not in loc.module_tokens


def test_build_locator_no_signal():
    loc = svc.build_locator([_tc(test_name="")])
    # test_name empty, no trace/class/package → no usable signal
    assert not loc.has_signal


# ── Path overlap ─────────────────────────────────────────────────────────────


def test_path_overlap_stem_match_is_strongest():
    loc = svc.build_locator([_tc(stack_trace='File "services/payments/test_charge.py", line 3')])
    assert svc.path_overlap(loc, "services/payments/charge.py") == 1.0
    assert svc.path_overlap(loc, "docs/README.md") == 0.0


def test_path_overlap_directory_partial():
    loc = svc.build_locator([_tc(stack_trace='File "services/payments/test_charge.py", line 3')])
    # same directory, different subject file → module-token/dir overlap (>0, <1)
    score = svc.path_overlap(loc, "services/payments/refund.py")
    assert 0.0 < score < 1.0


# ── Scoring matrix ───────────────────────────────────────────────────────────


def _payments_loc():
    return svc.build_locator([_tc(stack_trace='File "services/payments/test_charge.py", line 3')])


def test_score_path_overlap_beats_recency():
    loc = _payments_loc()
    # b is the NEWEST but touches unrelated files; a is older but overlaps.
    commits = [
        _commit("a" * 40, ["services/payments/charge.py"]),
        _commit("b" * 40, ["docs/README.md"]),
    ]
    ranked = svc.score_commits(commits, loc)
    assert ranked[0]["sha"] == "a" * 40  # overlap wins over recency
    assert ranked[0]["score"] > ranked[1]["score"]


def test_score_recency_breaks_overlap_tie():
    loc = _payments_loc()
    # Both overlap identically; the newer (later in list) must rank first.
    commits = [
        _commit("a" * 40, ["services/payments/charge.py"]),
        _commit("c" * 40, ["services/payments/charge.py"]),
    ]
    ranked = svc.score_commits(commits, loc)
    assert ranked[0]["sha"] == "c" * 40
    assert ranked[0]["rationale"]["recency_rank"] == 1


def test_score_rationale_is_inspectable():
    loc = _payments_loc()
    commits = [_commit("a" * 40, ["services/payments/charge.py", "docs/x.md"])]
    ranked = svc.score_commits(commits, loc)
    r = ranked[0]["rationale"]
    assert r["overlapping_files"] == ["services/payments/charge.py"]
    assert r["overlap_score"] == 1.0
    assert r["recency_rank"] == 1
    assert r["changed_file_count"] == 2


def test_score_no_overlap_degrades_to_recency():
    loc = _payments_loc()
    commits = [
        _commit("a" * 40, ["docs/a.md"]),
        _commit("b" * 40, ["docs/b.md"]),
    ]
    ranked = svc.score_commits(commits, loc)
    # No overlap anywhere → newest first, all rationale honest about zero overlap.
    assert ranked[0]["sha"] == "b" * 40
    assert all(x["rationale"]["overlap_score"] == 0.0 for x in ranked)


def test_score_author_prior_nudge():
    loc = _payments_loc()
    # Al overlaps twice → author_touched_module_before True on both Al commits.
    commits = [
        _commit("a" * 40, ["services/payments/charge.py"], author="Al"),
        _commit("b" * 40, ["services/payments/refund.py"], author="Al"),
        _commit("c" * 40, ["services/payments/charge.py"], author="Bo"),
    ]
    ranked = svc.score_commits(commits, loc)
    al = [x for x in ranked if x["author"] == "Al"]
    assert all(x["rationale"]["author_touched_module_before"] for x in al)


def test_score_empty_range():
    assert svc.score_commits([], _payments_loc()) == []


def test_score_is_deterministic():
    loc = _payments_loc()
    commits = [
        _commit("a" * 40, ["services/payments/charge.py"]),
        _commit("b" * 40, ["docs/README.md"]),
        _commit("c" * 40, ["services/payments/charge.py"]),
    ]
    r1 = svc.score_commits(list(commits), loc)
    r2 = svc.score_commits(list(commits), loc)
    assert [x["sha"] for x in r1] == [x["sha"] for x in r2]


# ── Supplied normalization ───────────────────────────────────────────────────


def test_normalize_supplied_range_shapes_and_bounds():
    raw = [
        {"sha": "a" * 40, "author": "Al", "message": "l1\nl2", "files": ["x.py"], "committed_at": "2026-01-01"},
        {"author": "no sha"},  # dropped
        {"sha": "b" * 40},
    ]
    out = svc.normalize_supplied_range(raw)
    assert [c["sha"] for c in out] == ["a" * 40, "b" * 40]
    assert out[0]["message"] == "l1"  # first line only
    assert out[1]["files"] == []


def test_normalize_supplied_range_caps_length():
    raw = [{"sha": f"{i:040x}"} for i in range(svc._MAX_COMMITS + 20)]
    out = svc.normalize_supplied_range(raw)
    assert len(out) == svc._MAX_COMMITS


def test_normalize_supplied_range_non_list():
    assert svc.normalize_supplied_range("nope") == []


# ── Deep-link derivation ─────────────────────────────────────────────────────


def test_commit_url_github_com():
    url = svc._commit_html_url("acme/webapp", "https://api.github.com", "s" * 40)
    assert url == f"https://github.com/acme/webapp/commit/{'s' * 40}"


def test_commit_url_ghe():
    url = svc._commit_html_url("acme/webapp", "https://ghe.corp/api/v3", "s" * 40)
    assert url == f"https://ghe.corp/acme/webapp/commit/{'s' * 40}"


def test_commit_url_none_without_repo():
    assert svc._commit_html_url(None, "https://api.github.com", "s" * 40) is None


# ── Read model honesty ───────────────────────────────────────────────────────


def test_serialize_range_unavailable():
    run = _run()
    out = svc._serialize_range(None, run)
    assert out["available"] is False
    assert out["source"] == svc.SOURCE_UNAVAILABLE
    assert out["commits"] == []
    assert out["head_commit"] == run.commit_hash


def test_serialize_range_available_adds_urls():
    run = _run()
    row = SimpleNamespace(
        source=svc.SOURCE_SUPPLIED,
        base_commit=None,
        head_commit="h" * 40,
        base_run_id=None,
        commits=[_commit("a" * 40, ["x.py"])],
        resolved_at=None,
    )
    out = svc._serialize_range(row, run)
    assert out["available"] is True
    assert out["commits"][0]["commit_url"].endswith(f"/commit/{'a' * 40}")


# ── rank_suspects honest empty ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rank_suspects_no_range_is_honest():
    run = _run()
    with patch.object(svc, "_get_row", AsyncMock(return_value=None)):
        out = await svc.rank_suspects(MagicMock(), run, cluster_id="cl_001")
    assert out["available"] is False
    assert out["reason"] == "no_commit_range"
    assert out["suspects"] == []
    assert "not culprits" in out["caveat"]


@pytest.mark.asyncio
async def test_rank_suspects_ranks_with_range():
    run = _run()
    row = SimpleNamespace(
        source=svc.SOURCE_CONNECTOR,
        base_commit="b" * 40,
        head_commit="h" * 40,
        commits=[
            _commit("a" * 40, ["services/payments/charge.py"]),
            _commit("z" * 40, ["docs/x.md"]),
        ],
    )
    tcs = [_tc(stack_trace='File "services/payments/test_charge.py", line 3')]
    with patch.object(svc, "_get_row", AsyncMock(return_value=row)), \
         patch.object(svc, "_target_test_cases", AsyncMock(return_value=tcs)):
        out = await svc.rank_suspects(MagicMock(), run, fingerprint="fp1")
    assert out["available"] is True
    assert out["suspects"][0]["sha"] == "a" * 40
    assert out["suspects"][0]["commit_url"].endswith(f"/commit/{'a' * 40}")


# ── Connector fetch ──────────────────────────────────────────────────────────


def _integration(**overrides):
    base = dict(
        enabled=True,
        repo_owner="acme",
        repo_name="webapp",
        api_base_url="https://api.github.com",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.mark.asyncio
async def test_fetch_connector_range_success():
    run = _run()
    compare_body = {
        "commits": [
            {"sha": "a" * 40, "commit": {"author": {"name": "Al", "date": "2026-01-01"}, "message": "fix"}},
        ]
    }
    detail_body = {"files": [{"filename": "services/payments/charge.py"}]}

    async def fake_gh_get(url, headers, params=None):
        if "/compare/" in url:
            return FakeResponse(200, compare_body)
        return FakeResponse(200, detail_body)

    with patch.object(svc, "get_integration", AsyncMock(return_value=_integration())), \
         patch.object(svc, "_ssrf_block_reason", AsyncMock(return_value=None)), \
         patch.object(svc, "_gh_get", side_effect=fake_gh_get), \
         patch("app.services.secret_service.read_secret", AsyncMock(return_value="pat")):
        commits = await svc._fetch_connector_range(MagicMock(), run, "b" * 40, "h" * 40)
    assert commits is not None
    assert commits[0]["sha"] == "a" * 40
    assert commits[0]["files"] == ["services/payments/charge.py"]


@pytest.mark.asyncio
async def test_fetch_connector_range_repo_mismatch():
    run = _run(ci_repo="other/repo")
    with patch.object(svc, "get_integration", AsyncMock(return_value=_integration())):
        commits = await svc._fetch_connector_range(MagicMock(), run, "b" * 40, "h" * 40)
    assert commits is None


@pytest.mark.asyncio
async def test_fetch_connector_range_no_pat():
    run = _run()
    with patch.object(svc, "get_integration", AsyncMock(return_value=_integration())), \
         patch("app.services.secret_service.read_secret", AsyncMock(return_value=None)):
        commits = await svc._fetch_connector_range(MagicMock(), run, "b" * 40, "h" * 40)
    assert commits is None


@pytest.mark.asyncio
async def test_fetch_connector_range_ssrf_block():
    run = _run()
    with patch.object(svc, "get_integration", AsyncMock(return_value=_integration())), \
         patch.object(svc, "_ssrf_block_reason", AsyncMock(return_value="blocked_target:127.0.0.1")), \
         patch("app.services.secret_service.read_secret", AsyncMock(return_value="pat")):
        commits = await svc._fetch_connector_range(MagicMock(), run, "b" * 40, "h" * 40)
    assert commits is None


# ── resolve orchestration (stage-only; caller owns the commit) ───────────────


def _db_returning(run):
    """Fake AsyncSession whose ``execute`` yields ``run`` (the initial load)."""
    db = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=run)
    db.execute = AsyncMock(return_value=result)
    return db


@pytest.mark.asyncio
async def test_resolve_supplied_wins_over_connector():
    run = _run()
    existing = SimpleNamespace(source=svc.SOURCE_SUPPLIED, commits=[_commit("a" * 40, ["x"])])
    with patch.object(svc, "_get_row", AsyncMock(return_value=existing)), \
         patch.object(svc, "_fetch_connector_range", AsyncMock()) as fetch:
        out = await svc.resolve_commit_range(_db_returning(run), RUN_ID)
    assert out["skipped"] == "already_resolved"
    fetch.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_connector_path():
    run = _run()
    base_run = _run(id=uuid.uuid4(), commit_hash="b" * 40)
    with patch.object(svc, "_get_row", AsyncMock(return_value=None)), \
         patch.object(svc, "_post_allowed", AsyncMock(return_value=True)), \
         patch.object(svc, "_last_green_run", AsyncMock(return_value=base_run)), \
         patch.object(svc, "_fetch_connector_range", AsyncMock(return_value=[_commit("a" * 40, ["x.py"])])), \
         patch.object(svc, "_upsert_range", AsyncMock()) as upsert:
        out = await svc.resolve_commit_range(_db_returning(run), RUN_ID)
    assert out["source"] == svc.SOURCE_CONNECTOR
    assert out["commit_count"] == 1
    assert upsert.await_args.kwargs["source"] == svc.SOURCE_CONNECTOR


@pytest.mark.asyncio
async def test_resolve_unavailable_when_offline_no_base():
    run = _run()
    with patch.object(svc, "_get_row", AsyncMock(return_value=None)), \
         patch.object(svc, "_post_allowed", AsyncMock(return_value=False)), \
         patch.object(svc, "_upsert_range", AsyncMock()) as upsert:
        out = await svc.resolve_commit_range(_db_returning(run), RUN_ID)
    assert out["source"] == svc.SOURCE_UNAVAILABLE
    assert upsert.await_args.kwargs["source"] == svc.SOURCE_UNAVAILABLE


@pytest.mark.asyncio
async def test_store_supplied_range_stages_row():
    run = _run()
    db = MagicMock()
    with patch.object(svc, "_upsert_range", AsyncMock(return_value="row")) as upsert:
        out = await svc.store_supplied_range(db, run, [{"sha": "a" * 40, "files": ["x.py"]}])
    assert out == "row"
    assert upsert.await_args.kwargs["source"] == svc.SOURCE_SUPPLIED


@pytest.mark.asyncio
async def test_store_supplied_range_empty_is_noop():
    out = await svc.store_supplied_range(MagicMock(), _run(), [])
    assert out is None


# ── Migration 0110 contract ──────────────────────────────────────────────────


def test_migration_0110_contract():
    import importlib

    mod = importlib.import_module("migrations.versions.0110_run_commit_ranges")
    assert mod.revision == "0110"
    assert mod.down_revision == "0109"
    assert callable(mod.upgrade)
    assert callable(mod.downgrade)
