"""W23b FAIL-13: the configuration drift of the Changes tab (contract §2.2 D-1, §6.3 G-B).

``run_diff_service.config_drift(run, baseline)`` is PURE: it reads every field
through ``getattr(obj, name, None)``, so plain ``SimpleNamespace`` runs stand in
for ``TestRun`` rows. ``legacy_config_drift`` is ``/baseline-diff``'s list,
pinned equal to the pre-W23b code (copied below as the oracle).
``compute_regression_diff`` runs for real on a session double and grows
``config_drift`` and ``new_failing_tests[].test_fingerprint`` only.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql

from app.models.schemas import ConfigDrift
from app.services import commit_attribution_service, regression_diff_service
from app.services.run_context_vocab import CONTEXT_STATES, DRIFT_ENTRIES_MAX, DRIFT_SECTIONS
from app.services.run_diff_service import config_drift, legacy_config_drift

LOCK_A = "a1" * 32  # sha256-shaped lockfile hashes (64 hex), built here, never secrets
LOCK_B = "b2" * 32


def _ctx(**sections):
    return {"v": 1, **sections}


def _run(build="4480", **fields):
    return SimpleNamespace(id=uuid.uuid4(), build_number=build, **fields)


def _pre_w23b_drift(run, baseline):
    """The pre-W23b body of get_baseline_diff's drift block (run_diff_service.py:255-268), verbatim."""
    config_drift: list[dict] = []
    if run.branch != baseline.branch and (run.branch or baseline.branch):
        config_drift.append({"field": "branch", "old_value": baseline.branch, "new_value": run.branch})
    if run.ocp_namespace != baseline.ocp_namespace and (run.ocp_namespace or baseline.ocp_namespace):
        config_drift.append({
            "field": "ocp_namespace", "old_value": baseline.ocp_namespace, "new_value": run.ocp_namespace,
        })
    return config_drift


def _lines(drift):
    return [(e["section"], e["key"], e["change"], e["old"], e["new"]) for e in drift["entries"]]


def test_the_acceptance_pair_lockfile_then_python():
    base = _run("41", run_context=_ctx(dependency_fingerprints={"poetry.lock": LOCK_A},
                                       runtime={"python": "3.11.8", "pytest": "8.3.2"}))
    run = _run("42", run_context=_ctx(dependency_fingerprints={"poetry.lock": LOCK_B},
                                      runtime={"python": "3.11.9", "pytest": "8.3.2"}))
    drift = config_drift(run, base)
    assert drift["context_state"] == "recorded"
    assert _lines(drift) == [
        ("dependencies", "poetry.lock", "changed", LOCK_A[:12], LOCK_B[:12]),
        ("runtime", "python", "changed", "3.11.8", "3.11.9"),
    ]
    assert (drift["baseline_run_id"], drift["baseline_build_number"]) == (str(base.id), "41")
    assert drift["entries_total"] == 2
    assert ConfigDrift.model_validate(drift).model_dump(mode="json") == drift


def test_added_and_removed_in_every_section_keys_ascending():
    base = _run(run_context=_ctx(
        dependency_fingerprints={"yarn.lock": LOCK_A, "poetry.lock": LOCK_A},
        runtime={"java": "21"},
        image_digests={"app": "sha256:" + "c" * 64},
        env={"TZ": "UTC", "LANG": "C.UTF-8"},
    ), environment="staging", branch="main", ocp_namespace="qa-1")
    run = _run(run_context=_ctx(
        dependency_fingerprints={"poetry.lock": LOCK_A, "go.sum": LOCK_B},
        runtime={"pytest": "8.3.2", "node": "20.11.1"},
        image_digests={"db": "sha256:" + "d" * 64},
        env={"TZ": "Europe/Sentinel", "LANG": "C.UTF-8", "NODE_ENV": "test"},
    ), environment="prod", branch="feature/pay", ocp_namespace=None)
    assert _lines(config_drift(run, base)) == [
        ("dependencies", "go.sum", "added", None, LOCK_B[:12]),
        ("dependencies", "yarn.lock", "removed", LOCK_A[:12], None),
        ("runtime", "java", "removed", "21", None),
        ("runtime", "node", "added", None, "20.11.1"),
        ("runtime", "pytest", "added", None, "8.3.2"),
        ("images", "app", "removed", "sha256:" + "c" * 64, None),
        ("images", "db", "added", None, "sha256:" + "d" * 64),
        ("environment", "environment", "changed", "staging", "prod"),
        ("env", "NODE_ENV", "added", None, "test"),
        ("env", "TZ", "changed", "UTC", "Europe/Sentinel"),
        ("run", "branch", "changed", "main", "feature/pay"),
        ("run", "ocp_namespace", "removed", "qa-1", None),
    ]
    sections = [e["section"] for e in config_drift(run, base)["entries"]]
    assert sections == sorted(sections, key=DRIFT_SECTIONS.index)


def test_branch_and_namespace_drift_shows_without_any_context():
    base = _run(branch="main", ocp_namespace="qa-1", environment="staging")
    run = _run(branch="release/v3", ocp_namespace="qa-2", environment="staging")
    drift = config_drift(run, base)
    assert drift["context_state"] == "both_missing"
    assert _lines(drift) == [
        ("run", "branch", "changed", "main", "release/v3"),
        ("run", "ocp_namespace", "changed", "qa-1", "qa-2"),
    ]


@pytest.mark.parametrize("run_ctx, base_ctx, expected", [
    (True, True, "recorded"), (False, True, "run_missing"), (True, False, "baseline_missing"),
    (False, False, "both_missing"),
])
def test_the_four_context_states_and_context_sections_only_when_recorded(run_ctx, base_ctx, expected):
    ctx_a, ctx_b = _ctx(runtime={"python": "3.11.8"}), _ctx(runtime={"python": "3.11.9"})
    base = _run(run_context=ctx_a if base_ctx else None, environment="staging")
    run = _run(run_context=ctx_b if run_ctx else None, environment="prod")
    drift = config_drift(run, base)
    assert drift["context_state"] == expected and expected in CONTEXT_STATES
    sections = {e["section"] for e in drift["entries"]}
    assert sections == ({"runtime", "environment"} if expected == "recorded" else {"environment"})


def test_a_non_dict_context_is_not_recorded():
    base = _run(run_context="not-a-dict")
    run = _run(run_context=_ctx(runtime={"python": "3.11.9"}))
    assert config_drift(run, base)["context_state"] == "baseline_missing"


def test_empty_string_reads_as_absent():
    base = _run(branch="", environment=None, run_context=_ctx(env={"TZ": ""}, runtime={"python": "3.11.9"}))
    run = _run(branch=None, environment="", run_context=_ctx(env={}, runtime={"python": "3.11.9"}))
    drift = config_drift(run, base)
    assert drift["entries"] == [] and drift["entries_total"] == 0 and drift["context_state"] == "recorded"
    added = config_drift(_run(environment="prod"), _run(environment=""))
    assert _lines(added) == [("environment", "environment", "added", None, "prod")]


def test_equal_hash_prefixes_still_compare_in_full():
    old, new = "e" * 12 + "0" * 52, "e" * 12 + "1" * 52
    drift = config_drift(_run(run_context=_ctx(dependency_fingerprints={"uv.lock": new})),
                         _run(run_context=_ctx(dependency_fingerprints={"uv.lock": old})))
    assert _lines(drift) == [("dependencies", "uv.lock", "changed", "e" * 12, "e" * 12)]


def test_the_entry_cap_keeps_the_full_count():
    many = {f"key{i:03d}": f"v{i}" for i in range(DRIFT_ENTRIES_MAX + 7)}
    drift = config_drift(_run(run_context=_ctx(runtime=many)), _run(run_context=_ctx(runtime={})))
    assert len(drift["entries"]) == DRIFT_ENTRIES_MAX
    assert drift["entries_total"] == DRIFT_ENTRIES_MAX + 7
    assert [e["key"] for e in drift["entries"]][:3] == ["key000", "key001", "key002"]


def test_simplenamespace_runs_without_the_new_attributes():
    bare_run = SimpleNamespace(id=uuid.uuid4(), pass_rate=80.0)
    bare_base = SimpleNamespace(id=uuid.uuid4(), pass_rate=100.0, build_number="4475")
    drift = config_drift(bare_run, bare_base)
    assert (drift["context_state"], drift["entries"], drift["baseline_build_number"]) == ("both_missing", [], "4475")
    assert legacy_config_drift(bare_run, bare_base) == []


@pytest.mark.parametrize("run_fields, base_fields", [
    ({"branch": "main", "ocp_namespace": "qa"}, {"branch": "main", "ocp_namespace": "qa"}),
    ({"branch": "feature/x", "ocp_namespace": "qa"}, {"branch": "main", "ocp_namespace": "qa"}),
    ({"branch": None, "ocp_namespace": "qa-2"}, {"branch": "main", "ocp_namespace": "qa-1"}),
    ({"branch": "", "ocp_namespace": None}, {"branch": None, "ocp_namespace": ""}),
    ({"branch": "main", "ocp_namespace": None}, {"branch": "Main", "ocp_namespace": "prod"}),
    ({"branch": None, "ocp_namespace": None}, {"branch": None, "ocp_namespace": None}),
])
def test_legacy_config_drift_is_the_pre_w23b_list(run_fields, base_fields):
    run, base = SimpleNamespace(**run_fields), SimpleNamespace(**base_fields)
    assert legacy_config_drift(run, base) == _pre_w23b_drift(run, base)


# ── The regression-diff answer grows two things, nothing else ───────────────────


class _Rows:
    def __init__(self, value=None, rows=None):
        self._value, self._rows = value, rows or []

    def scalar_one_or_none(self):
        return self._value

    def all(self):
        return list(self._rows)


class _DiffSession:
    def __init__(self, *, run, baseline, failing):
        self.run, self.baseline, self.failing = run, baseline, failing
        self.added: list = []

    def add(self, obj):  # pragma: no cover - a write would fail the test
        self.added.append(obj)

    async def execute(self, stmt):
        compiled = stmt.compile(dialect=postgresql.dialect())
        sql, params = str(compiled), compiled.params
        if "FROM run_baselines" in sql:
            return _Rows(rows=[])
        if "FROM test_runs" in sql and "test_runs.pass_rate >=" in sql:
            return _Rows(self.baseline)
        if "FROM test_cases" in sql:
            if params["test_run_id_1"] != self.run.id:
                return _Rows(rows=[])
            return _Rows(rows=[SimpleNamespace(test_fingerprint=fp, test_name=f"t-{fp}", suite_name="s")
                               for fp in self.failing])
        raise AssertionError(f"unexpected statement: {sql}")


PRE_W23B_KEYS = {
    "run_id", "baseline_run_id", "baseline_available", "build_number", "baseline_build_number", "pass_rate",
    "baseline_pass_rate", "pass_rate_delta", "new_failing_tests", "new_failing_count", "resolved_count",
    "commit_range", "commit_count", "commit_range_status", "baseline_purged",
}


@pytest.fixture
def _flat_commit_range(monkeypatch):
    async def get_commit_range(_db, _run):
        return {"commits": []}

    async def status(_db, _run, _serialized):
        return SimpleNamespace(model_dump=lambda mode=None: {"reason": "no_commits"})

    monkeypatch.setattr(commit_attribution_service, "get_commit_range", get_commit_range)
    monkeypatch.setattr(regression_diff_service, "commit_range_status", status)


async def test_compute_regression_diff_carries_config_drift_and_fingerprints(_flat_commit_range):
    now = datetime.now(timezone.utc)
    run = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4(), pass_rate=80.0, created_at=now,
                          build_number="4480", branch="feature/pay",
                          run_context=_ctx(runtime={"python": "3.11.9"}))
    baseline = SimpleNamespace(id=uuid.uuid4(), pass_rate=100.0, build_number="4475", branch="main",
                               run_context=_ctx(runtime={"python": "3.11.8"}))
    db = _DiffSession(run=run, baseline=baseline, failing=["fp-sentinel-a", "fp-sentinel-b"])

    diff = await regression_diff_service.compute_regression_diff(run, db)

    assert set(diff) == PRE_W23B_KEYS | {"config_drift"}
    assert diff["new_failing_tests"] == [
        {"test_name": "t-fp-sentinel-a", "suite_name": "s", "test_fingerprint": "fp-sentinel-a"},
        {"test_name": "t-fp-sentinel-b", "suite_name": "s", "test_fingerprint": "fp-sentinel-b"},
    ]
    assert diff["config_drift"] == config_drift(run, baseline)
    assert _lines(diff["config_drift"]) == [
        ("runtime", "python", "changed", "3.11.8", "3.11.9"),
        ("run", "branch", "changed", "main", "feature/pay"),
    ]
    assert diff["config_drift"]["baseline_build_number"] == diff["baseline_build_number"] == "4475"
    assert db.added == []


async def test_the_no_baseline_answer_is_unchanged(_flat_commit_range):
    now = datetime.now(timezone.utc)
    run = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4(), pass_rate=80.0, created_at=now,
                          build_number="1")
    diff = await regression_diff_service.compute_regression_diff(run, _DiffSession(run=run, baseline=None,
                                                                                  failing=[]))
    assert set(diff) == {"run_id", "baseline_run_id", "baseline_available", "message", "baseline_purged"}
