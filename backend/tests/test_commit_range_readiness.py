"""Commit-range supply + TIA readiness — 2026-08-04 slice.

Covers the three defects that kept ``run_commit_ranges`` near-empty (and
therefore kept an Epic-10 test-impact corpus from accumulating), plus the
metric that makes the go/no-go measurable:

* **supplied base persisted** — ``store_supplied_range`` used to hard-code
  ``base_commit=None``, so a supplied range's boundary was unreconstructible.
  Both wire shapes (bare list / ``{base, head, commits}``) are exercised, and
  supplied-wins precedence must be untouched.
* **fallback base anchor** — a fully-green baseline is no longer required;
  ``resolve_base_anchor`` degrades to the last completed prior run and
  records WHICH anchor it used. The label must never over-state the anchor.
* **file-fetch cap** — now settings-driven, clamped, default pinned at 25.
* **TIA readiness** — zero / sparse / adequate corpora, each with the honest
  ``available:false`` + ``insufficient_data_reason`` pattern borrowed from
  ``value_metrics_service.resolve_availability``.
* migration 0116 chain + a real downgrade.

No DB egress: the service's own read/write helpers are patched, mirroring
``test_commit_attribution_service.py``.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import commit_attribution_service as svc

PROJECT_ID = uuid.uuid4()
RUN_ID = uuid.uuid4()


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
        end_time=datetime(2026, 8, 4, tzinfo=timezone.utc),
        created_at=datetime(2026, 8, 4, tzinfo=timezone.utc),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _upsert_kwargs(upsert):
    return upsert.await_args.kwargs


# ── 1. Supplied base is persisted (both wire shapes) ─────────────────────────


@pytest.mark.asyncio
async def test_supplied_object_shape_persists_base_and_head():
    """The regression: a supplied range with a base must NOT store base=None."""
    payload = {
        "base": "b" * 40,
        "head": "c" * 40,
        "commits": [{"sha": "a" * 40, "files": ["svc/pay.py"]}],
    }
    with patch.object(svc, "_upsert_range", AsyncMock(return_value="row")) as upsert:
        out = await svc.store_supplied_range(MagicMock(), _run(), payload)
    kwargs = _upsert_kwargs(upsert)
    assert out == "row"
    assert kwargs["base_commit"] == "b" * 40
    assert kwargs["head_commit"] == "c" * 40
    assert kwargs["source"] == svc.SOURCE_SUPPLIED
    assert kwargs["base_source"] == svc.BASE_SOURCE_SUPPLIED


@pytest.mark.asyncio
async def test_supplied_accepts_alias_spellings():
    """``base_commit``/``head_commit`` and ``from_commit``/``to_commit`` are
    accepted so a caller does not have to guess our spelling."""
    for base_key, head_key in (
        ("base_commit", "head_commit"),
        ("from_commit", "to_commit"),
    ):
        payload = {
            base_key: "b" * 40,
            head_key: "c" * 40,
            "commits": [{"sha": "a" * 40, "files": ["x.py"]}],
        }
        with patch.object(svc, "_upsert_range", AsyncMock()) as upsert:
            await svc.store_supplied_range(MagicMock(), _run(), payload)
        assert _upsert_kwargs(upsert)["base_commit"] == "b" * 40
        assert _upsert_kwargs(upsert)["head_commit"] == "c" * 40


@pytest.mark.asyncio
async def test_supplied_bare_list_still_works_and_is_honest():
    """Back-compat: the legacy list has no boundary, so we say so rather than
    inventing one."""
    with patch.object(svc, "_upsert_range", AsyncMock()) as upsert:
        await svc.store_supplied_range(
            MagicMock(), _run(), [{"sha": "a" * 40, "files": ["x.py"]}],
        )
    kwargs = _upsert_kwargs(upsert)
    assert kwargs["base_commit"] is None
    assert kwargs["base_source"] == svc.BASE_SOURCE_UNAVAILABLE
    # head still falls back to the run's own commit.
    assert kwargs["head_commit"] == "h" * 40


@pytest.mark.asyncio
async def test_supplied_base_equal_to_head_is_not_an_anchor():
    """base == head bounds an empty range — not a usable anchor."""
    payload = {"base": "c" * 40, "head": "c" * 40, "commits": [{"sha": "a" * 40}]}
    with patch.object(svc, "_upsert_range", AsyncMock()) as upsert:
        await svc.store_supplied_range(MagicMock(), _run(), payload)
    kwargs = _upsert_kwargs(upsert)
    assert kwargs["base_commit"] is None
    assert kwargs["base_source"] == svc.BASE_SOURCE_UNAVAILABLE


@pytest.mark.asyncio
async def test_supplied_empty_commits_object_is_noop():
    out = await svc.store_supplied_range(
        MagicMock(), _run(), {"base": "b" * 40, "commits": []},
    )
    assert out is None


@pytest.mark.asyncio
async def test_supplied_still_wins_over_connector():
    """Precedence ratchet: an existing supplied row is never re-resolved."""
    existing = SimpleNamespace(
        source=svc.SOURCE_SUPPLIED,
        base_source=svc.BASE_SOURCE_SUPPLIED,
        commits=[{"sha": "a" * 40, "files": ["x.py"]}],
    )
    with patch.object(svc, "AsyncSessionLocal", _fake_session_local(_run())), \
         patch.object(svc, "_get_row", AsyncMock(return_value=existing)), \
         patch.object(svc, "_fetch_connector_range", AsyncMock()) as fetch:
        out = await svc.resolve_commit_range(MagicMock(), RUN_ID)
    assert out["skipped"] == "already_resolved"
    fetch.assert_not_called()


def test_normalize_supplied_payload_shapes():
    assert svc.normalize_supplied_payload(None) == (None, None, [])
    assert svc.normalize_supplied_payload("nope") == (None, None, [])
    assert svc.normalize_supplied_payload([]) == (None, None, [])
    base, head, commits = svc.normalize_supplied_payload(
        {"base": " " + "b" * 40 + " ", "head": None, "commits": [{"sha": "a" * 40}]}
    )
    assert base == "b" * 40 and head is None and len(commits) == 1


def test_normalize_supplied_payload_accepts_pydantic_model():
    from app.models.schemas import SuppliedCommit, SuppliedCommitRange

    model = SuppliedCommitRange(
        base="b" * 40,
        head="c" * 40,
        commits=[SuppliedCommit(sha="a" * 40, files=["x.py"])],
    )
    base, head, commits = svc.normalize_supplied_payload(model)
    assert base == "b" * 40
    assert head == "c" * 40
    assert commits[0]["files"] == ["x.py"]


def test_ingest_schema_accepts_both_commit_range_shapes():
    """Back-compat ratchet: the bare list must still validate."""
    from app.models.schemas import IngestPayload, SuppliedCommitRange

    common = dict(
        project_id=str(PROJECT_ID),
        build_number="b1",
        results=[{"test_name": "t", "status": "PASSED"}],
    )
    as_list = IngestPayload(**common, commit_range=[{"sha": "a" * 40}])
    assert isinstance(as_list.commit_range, list)
    as_obj = IngestPayload(
        **common,
        commit_range={"base": "b" * 40, "commits": [{"sha": "a" * 40}]},
    )
    assert isinstance(as_obj.commit_range, SuppliedCommitRange)
    assert as_obj.commit_range.base == "b" * 40
    assert IngestPayload(**common).commit_range is None


def test_live_session_metadata_preserves_the_range_object():
    """The live path stashes the range in ``extra_metadata``; the object shape
    must survive whole (iterating it as a list would keep only the keys)."""
    from app.models.schemas import LiveSessionCreate
    from app.services.stream_service import _with_ci_context

    payload = LiveSessionCreate(
        project_id=str(PROJECT_ID),
        client_name="ci",
        commit_range={"base": "b" * 40, "commits": [{"sha": "a" * 40}]},
    )
    merged = _with_ci_context({}, payload)
    assert merged["commit_range"]["base"] == "b" * 40
    assert merged["commit_range"]["commits"][0]["sha"] == "a" * 40
    # …and the legacy list shape still round-trips as a list.
    legacy = LiveSessionCreate(
        project_id=str(PROJECT_ID), client_name="ci",
        commit_range=[{"sha": "a" * 40}],
    )
    assert isinstance(_with_ci_context({}, legacy)["commit_range"], list)


# ── 2. Fallback base anchor + the label never over-states it ─────────────────


@pytest.mark.asyncio
async def test_anchor_prefers_green_baseline():
    green = _run(id=uuid.uuid4(), commit_hash="g" * 40, failed_tests=0)
    with patch.object(svc, "_last_green_run", AsyncMock(return_value=green)), \
         patch.object(svc, "_last_completed_run", AsyncMock()) as fallback:
        row, label = await svc.resolve_base_anchor(MagicMock(), _run())
    assert row is green
    assert label == svc.BASE_SOURCE_GREEN
    fallback.assert_not_called()


@pytest.mark.asyncio
async def test_anchor_falls_back_to_last_completed_when_nothing_is_green():
    """The dead end this slice fixes: a project whose runs always fail used to
    resolve ``unavailable`` forever."""
    failing = _run(id=uuid.uuid4(), commit_hash="f" * 40, failed_tests=7)
    with patch.object(svc, "_last_green_run", AsyncMock(return_value=None)), \
         patch.object(svc, "_last_completed_run", AsyncMock(return_value=failing)):
        row, label = await svc.resolve_base_anchor(MagicMock(), _run())
    assert row is failing
    assert label == svc.BASE_SOURCE_LAST_COMPLETED
    # And it is explicitly NOT treated as a strong anchor.
    assert label not in svc.STRONG_BASE_SOURCES


@pytest.mark.asyncio
async def test_anchor_unavailable_when_no_prior_run():
    with patch.object(svc, "_last_green_run", AsyncMock(return_value=None)), \
         patch.object(svc, "_last_completed_run", AsyncMock(return_value=None)):
        row, label = await svc.resolve_base_anchor(MagicMock(), _run())
    assert row is None
    assert label == svc.BASE_SOURCE_UNAVAILABLE


@pytest.mark.asyncio
async def test_resolve_persists_weak_anchor_label_end_to_end():
    failing = _run(id=uuid.uuid4(), commit_hash="f" * 40, failed_tests=7)
    with patch.object(svc, "AsyncSessionLocal", _fake_session_local(_run())), \
         patch.object(svc, "_get_row", AsyncMock(return_value=None)), \
         patch.object(svc, "_post_allowed", AsyncMock(return_value=True)), \
         patch.object(svc, "_last_green_run", AsyncMock(return_value=None)), \
         patch.object(svc, "_last_completed_run", AsyncMock(return_value=failing)), \
         patch.object(svc, "_connector_target", AsyncMock(return_value=_target())), \
         patch.object(
             svc, "_fetch_connector_range",
             AsyncMock(return_value=[{"sha": "a" * 40, "files": ["x.py"]}]),
         ), \
         patch.object(svc, "_upsert_range", AsyncMock()) as upsert:
        out = await svc.resolve_commit_range(MagicMock(), RUN_ID)
    assert out["source"] == svc.SOURCE_CONNECTOR
    assert out["base_source"] == svc.BASE_SOURCE_LAST_COMPLETED
    assert _upsert_kwargs(upsert)["base_source"] == svc.BASE_SOURCE_LAST_COMPLETED
    assert _upsert_kwargs(upsert)["base_commit"] == "f" * 40


@pytest.mark.asyncio
async def test_resolve_unavailable_row_still_records_no_anchor():
    """An absent resolution stays an explicit unavailable row — never a
    fabricated-but-plausible range."""
    with patch.object(svc, "AsyncSessionLocal", _fake_session_local(_run())), \
         patch.object(svc, "_get_row", AsyncMock(return_value=None)), \
         patch.object(svc, "_post_allowed", AsyncMock(return_value=False)), \
         patch.object(svc, "_upsert_range", AsyncMock()) as upsert:
        out = await svc.resolve_commit_range(MagicMock(), RUN_ID)
    assert out == {
        "source": svc.SOURCE_UNAVAILABLE,
        "base_source": svc.BASE_SOURCE_UNAVAILABLE,
    }
    assert _upsert_kwargs(upsert)["commits"] == []


@pytest.mark.asyncio
async def test_upsert_downgrades_a_label_with_no_base():
    """The label can never be wrong: no base ref → ``unavailable``, whatever
    the caller claimed."""
    db = MagicMock()
    db.execute = AsyncMock()
    with patch.object(svc, "_get_row", AsyncMock(return_value="row")):
        await svc._upsert_range(
            db, _run(),
            base_commit=None, head_commit="h" * 40, base_run_id=None,
            source=svc.SOURCE_CONNECTOR, commits=[],
            base_source=svc.BASE_SOURCE_GREEN,
        )
    assert _stmt_params(db)["base_source"] == svc.BASE_SOURCE_UNAVAILABLE


@pytest.mark.asyncio
async def test_upsert_rejects_unknown_label():
    db = MagicMock()
    db.execute = AsyncMock()
    with patch.object(svc, "_get_row", AsyncMock(return_value="row")):
        await svc._upsert_range(
            db, _run(),
            base_commit="b" * 40, head_commit="h" * 40, base_run_id=None,
            source=svc.SOURCE_CONNECTOR, commits=[],
            base_source="totally-made-up",
        )
    assert _stmt_params(db)["base_source"] == svc.BASE_SOURCE_UNAVAILABLE


def test_anchor_predicates_exclude_later_runs():
    """An anchor must PREDATE the run, else the compare is inverted."""
    preds = svc._anchor_predicates(_run())
    rendered = " ".join(str(p) for p in preds)
    assert "test_runs.project_id" in rendered
    assert "coalesce" in rendered.lower()  # the end_time/created_at pivot clause


def test_serialize_marks_weak_anchor():
    run = _run()
    row = SimpleNamespace(
        source=svc.SOURCE_CONNECTOR,
        base_source=svc.BASE_SOURCE_LAST_COMPLETED,
        base_commit="f" * 40,
        head_commit="h" * 40,
        base_run_id=None,
        commits=[{"sha": "a" * 40, "files": ["x.py"]}],
        resolved_at=datetime.now(timezone.utc),
    )
    out = svc._serialize_range(row, run)
    assert out["base_source"] == svc.BASE_SOURCE_LAST_COMPLETED
    assert out["base_anchor_is_strong"] is False


def test_serialize_pre_0116_row_defaults_to_unavailable_label():
    """Rows written before the column existed must not read as strong."""
    run = _run()
    row = SimpleNamespace(
        source=svc.SOURCE_SUPPLIED,
        base_commit=None,
        head_commit="h" * 40,
        base_run_id=None,
        commits=[{"sha": "a" * 40, "files": ["x.py"]}],
        resolved_at=None,
    )
    out = svc._serialize_range(row, run)
    assert out["base_source"] == svc.BASE_SOURCE_UNAVAILABLE
    assert out["base_anchor_is_strong"] is False


# ── 3. The 25-commit file-fetch cap decision, pinned ─────────────────────────


def test_file_fetch_limit_default_is_25():
    """Decision record: the DEFAULT stays 25 (ranking's rate-limit budget);
    it is configurable so a TIA-corpus deployment can raise it knowingly."""
    assert svc._DEFAULT_COMMIT_FILE_FETCHES == 25
    assert svc._file_fetch_limit() == 25


def test_file_fetch_limit_is_configurable_and_clamped():
    from app.core.config import settings

    for raw, expected in ((50, 50), (0, 1), (-3, 1), (10_000, svc._MAX_COMMITS), ("x", 25)):
        with patch.object(settings, "COMMIT_RANGE_FILE_FETCH_LIMIT", raw):
            assert svc._file_fetch_limit() == expected


# ── 4. TIA readiness: zero / sparse / adequate ──────────────────────────────


def _range_row(*, source=svc.SOURCE_CONNECTOR, base_source=svc.BASE_SOURCE_GREEN,
               files=("svc/a.py",), resolved_at=None, commits=None):
    if commits is None:
        commits = [{"sha": "a" * 40, "files": list(files)}]
    return SimpleNamespace(
        source=source,
        base_source=base_source,
        commits=commits,
        resolved_at=resolved_at or datetime.now(timezone.utc),
    )


def _adequate_rows():
    now = datetime.now(timezone.utc)
    return [
        _range_row(
            files=[f"svc/mod_{i}.py"],
            resolved_at=now - timedelta(days=i),
        )
        for i in range(svc.TIA_MIN_USABLE_RUNS)
    ]


def test_readiness_zero_data_is_honest():
    out = svc.compute_tia_readiness([])
    assert out["available"] is False
    assert out["insufficient_data_reason"] == (
        "no commit ranges have been resolved for this project yet"
    )
    assert out["usable_runs"] == 0
    assert out["file_detail_coverage"] == 0.0


def test_readiness_all_unavailable_rows_names_the_cause():
    rows = [
        _range_row(source=svc.SOURCE_UNAVAILABLE,
                   base_source=svc.BASE_SOURCE_UNAVAILABLE, commits=[])
        for _ in range(5)
    ]
    out = svc.compute_tia_readiness(rows)
    assert out["available"] is False
    assert "none of the 5 resolved ranges contain any commits" in out["insufficient_data_reason"]
    assert out["runs_with_range"] == 0
    assert out["source_breakdown"] == {svc.SOURCE_UNAVAILABLE: 5}


def test_readiness_ranges_without_files_are_not_corpus():
    """The 25-commit-cap consequence, measured: SHAs with no files train
    nothing."""
    rows = [_range_row(commits=[{"sha": "a" * 40, "files": []}]) for _ in range(4)]
    out = svc.compute_tia_readiness(rows)
    assert out["available"] is False
    assert out["runs_with_range"] == 4
    assert out["usable_runs"] == 0
    assert "none carry per-commit changed files" in out["insufficient_data_reason"]
    assert out["commits_total"] == 4
    assert out["commits_with_files"] == 0


def test_readiness_sparse_reports_the_shortfall():
    rows = [_range_row(files=[f"svc/m{i}.py"]) for i in range(3)]
    out = svc.compute_tia_readiness(rows)
    assert out["available"] is False
    assert out["insufficient_data_reason"] == (
        f"only 3 of 3 runs carry a usable commit range (need {svc.TIA_MIN_USABLE_RUNS})"
    )
    assert out["distinct_paths"] == 3


def test_readiness_enough_runs_but_no_history_span():
    now = datetime.now(timezone.utc)
    rows = [
        _range_row(files=[f"svc/m{i}.py"], resolved_at=now - timedelta(minutes=i))
        for i in range(svc.TIA_MIN_USABLE_RUNS)
    ]
    out = svc.compute_tia_readiness(rows)
    assert out["available"] is False
    assert "span" in out["insufficient_data_reason"]
    assert out["history_days"] < svc.TIA_MIN_HISTORY_DAYS


def test_readiness_enough_runs_and_span_but_too_few_paths():
    now = datetime.now(timezone.utc)
    rows = [
        _range_row(files=["svc/only.py"], resolved_at=now - timedelta(days=i))
        for i in range(svc.TIA_MIN_USABLE_RUNS)
    ]
    out = svc.compute_tia_readiness(rows)
    assert out["available"] is False
    assert out["distinct_paths"] == 1
    assert "distinct changed paths" in out["insufficient_data_reason"]


def test_readiness_adequate_corpus_is_available():
    out = svc.compute_tia_readiness(_adequate_rows())
    assert out["available"] is True
    assert out["insufficient_data_reason"] is None
    assert out["usable_runs"] == svc.TIA_MIN_USABLE_RUNS
    assert out["distinct_paths"] == svc.TIA_MIN_USABLE_RUNS
    assert out["history_days"] >= svc.TIA_MIN_HISTORY_DAYS
    assert out["file_detail_coverage"] == 1.0
    # Thresholds are published so a caller can disagree with them.
    assert out["thresholds"]["min_usable_runs"] == svc.TIA_MIN_USABLE_RUNS


def test_readiness_reports_anchor_strength_mix():
    rows = _adequate_rows()
    rows[0].base_source = svc.BASE_SOURCE_LAST_COMPLETED
    rows[1].base_source = svc.BASE_SOURCE_SUPPLIED
    out = svc.compute_tia_readiness(rows)
    assert out["runs_with_strong_base_anchor"] == svc.TIA_MIN_USABLE_RUNS - 1
    assert out["base_source_breakdown"][svc.BASE_SOURCE_LAST_COMPLETED] == 1
    assert out["base_source_breakdown"][svc.BASE_SOURCE_SUPPLIED] == 1


def test_readiness_flags_a_capped_scan():
    rows = [_range_row(files=[f"svc/m{i}.py"]) for i in range(5)]
    out = svc.compute_tia_readiness(rows, scan_cap=5)
    assert out["scan_capped"] is True
    assert out["scan_cap"] == 5


def test_readiness_tolerates_junk_rows():
    rows = [
        _range_row(base_source="who-knows", commits=["not-a-dict", {"sha": "a", "files": ["x.py"]}]),
        _range_row(resolved_at="not-a-datetime", files=["y.py"]),
    ]
    out = svc.compute_tia_readiness(rows)
    assert out["base_source_breakdown"][svc.BASE_SOURCE_UNAVAILABLE] == 1
    assert out["usable_runs"] == 2
    # A row whose timestamp is unusable simply contributes no span.
    assert out["history_days"] >= 0.0


@pytest.mark.asyncio
async def test_get_tia_readiness_is_project_scoped():
    """Never a fleet average: the query must filter on the project id."""
    db = MagicMock()
    result = MagicMock()
    result.all = MagicMock(return_value=_adequate_rows())
    db.execute = AsyncMock(return_value=result)

    out = await svc.get_tia_readiness(db, PROJECT_ID, days=45)
    assert out["project_id"] == str(PROJECT_ID)
    assert out["window_days"] == 45
    assert out["available"] is True
    sql = str(db.execute.await_args.args[0])
    assert "run_commit_ranges.project_id" in sql
    assert "run_commit_ranges.resolved_at" in sql


# ── 5. Migration 0116 contract ──────────────────────────────────────────────


def test_migration_0116_contract():
    import importlib
    import inspect

    mod = importlib.import_module("migrations.versions.0116_run_commit_range_base_source")
    assert mod.revision == "0116"
    assert mod.down_revision == "0115"
    up = inspect.getsource(mod.upgrade)
    down = inspect.getsource(mod.downgrade)
    assert "base_source" in up
    assert "ix_run_commit_ranges_project_resolved" in up
    # Real downgrade: drops the column AND restores the old index.
    assert "drop_column" in down and "base_source" in down
    assert "ix_run_commit_ranges_project" in down
    # Backfill must not invent an anchor for a row that never had a base.
    assert "base_commit IS NOT NULL" in up


def test_orm_matches_migration_0116():
    from app.models.postgres import RunCommitRange

    col = RunCommitRange.__table__.c.base_source
    assert col.nullable is False
    assert col.server_default.arg == "unavailable"
    indexes = {ix.name for ix in RunCommitRange.__table__.indexes}
    assert "ix_run_commit_ranges_project_resolved" in indexes
    assert "ix_run_commit_ranges_project" not in indexes


# ── helpers ─────────────────────────────────────────────────────────────────


def _target():
    return svc._ConnectorTarget(api_base="https://api.github.com", repo="acme/webapp", pat="p")


def _stmt_params(db):
    from sqlalchemy.dialects import postgresql

    stmt = db.execute.await_args.args[0]
    return stmt.compile(dialect=postgresql.dialect()).params


def _fake_session_local(run):
    read_db = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=run)
    read_db.execute = AsyncMock(return_value=result)

    class _SessionLocal:
        def __call__(self):
            return self

        async def __aenter__(self):
            return read_db

        async def __aexit__(self, *exc):
            return False

    return _SessionLocal()
