"""W25a HYG-04: the parity comparison is the contract's matrix, cell for cell (pure; no database).

* ``classify`` — one case per cell of §2.1 (14 outcomes incl. both "match"
  cells and the "counted, not reported" move), the ``-deleted`` bucket fold,
  the name / first-sighting checks, the priority rule, the NULL-pointer rule,
  an empty window (the reconciler off) → ``retire_pending``;
* ``compare_project`` — the four reads on the caller's session, no write;
* ``streak_days`` — §1.3 (absence is not health; the last run of a day decides);
* ``summarise`` — the caps (20 per reason, 200 projects) and zero-filled counts.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

from app.services import suite_membership_parity as smp
from app.services import suite_parity_vocab as vocab
from app.services.suite_membership_parity import CanonicalRow, LegacyRow, ProjectParity

FP = "fpq-alpha"
FP2 = "fpq-bravo"


def _legacy(suite="Smoke", fp=FP, status="active", name="test_login", cls="LoginTests", first=None, last=None):
    return LegacyRow(suite, fp, status, name, cls, first, last)


def _canon(suite="Smoke", fp=FP, status="active", source="execution", default=False, name="test_login",
           cls="LoginTests", first=None, last=None):
    return CanonicalRow(fp, status, source, suite, default, name, cls, first, last)


def _one(legacy, canonical, seen=(), defaults=()):
    keys, divergences = smp.classify(legacy, canonical, set(seen), set(defaults))
    return keys, [(d.reason, d.suite, d.legacy, d.canonical) for d in divergences]


# ── the matrix, one case per cell ────────────────────────────────────────────

def test_active_active_matches():
    assert _one([_legacy()], [_canon()]) == (1, [])


def test_active_retired_seen_is_retired_but_seen():
    assert _one([_legacy()], [_canon(status="deleted")], seen=[FP]) == (
        1, [("retired_but_seen", "Smoke", "active", "retired")])


def test_active_retired_unseen_is_legacy_stale():
    assert _one([_legacy()], [_canon(status="needs_review")]) == (
        1, [("legacy_stale", "Smoke", "active", "retired")])


def test_active_other_suite_is_moved_suite():
    keys, rows = _one([_legacy()], [_canon(suite="Regression")])
    assert keys == 2   # (Smoke, fp) legacy + (Regression, fp) canonical
    assert rows == [("moved_suite", "Smoke", "active", "other_suite")]


def test_active_absent_is_missing_in_canonical():
    assert _one([_legacy()], []) == (1, [("missing_in_canonical", "Smoke", "active", "absent")])


def test_removed_active_seen_is_deletion_window():
    assert _one([_legacy(status="needs_review")], [_canon()], seen=[FP]) == (
        1, [("deletion_window", "Smoke", "removed", "active")])


def test_removed_active_unseen_is_retire_pending():
    assert _one([_legacy(status="needs_review")], [_canon()]) == (
        1, [("retire_pending", "Smoke", "removed", "active")])


def test_removed_retired_matches():
    assert _one([_legacy(status="deleted")], [_canon(status="deleted")]) == (1, [])


def test_removed_other_suite_is_moved_suite():
    keys, rows = _one([_legacy(suite="Smoke-deleted", status="needs_review")], [_canon(suite="Regression")])
    assert keys == 2
    assert rows == [("moved_suite", "Smoke", "removed", "other_suite")]


def test_removed_absent_is_missing_in_canonical():
    assert _one([_legacy(status="deleted")], []) == (1, [("missing_in_canonical", "Smoke", "removed", "absent")])


def test_a_canonical_key_legacy_holds_elsewhere_is_counted_not_reported():
    keys, rows = _one([_legacy(suite="Smoke")], [_canon(suite="Regression")])
    assert keys == 2
    assert [r for r in rows if r[1] == "Regression"] == []      # the move is reported once, on the legacy key
    assert [r[0] for r in rows] == ["moved_suite"]


@pytest.mark.parametrize("status", ["active", "deleted"])
def test_a_default_suite_case_is_default_suite(status):
    rows = _one([], [_canon(suite="Default Suite (Qx)", default=True, status=status)])[1]
    assert rows == [("default_suite", "Default Suite (Qx)", "absent", "active" if status == "active" else "retired")]


def test_default_suite_also_follows_the_passed_names():
    assert _one([], [_canon(suite="Unsorted")], defaults=["Unsorted"])[1][0][0] == "default_suite"


@pytest.mark.parametrize("source", ["managed", "linked"])
def test_a_catalog_case_is_catalog_only(source):
    assert _one([], [_canon(source=source)])[1] == [("catalog_only", "Smoke", "absent", "active")]


@pytest.mark.parametrize("status", ["active", "needs_review"])
def test_an_execution_case_legacy_never_saw_is_missing_in_legacy(status):
    rows = _one([], [_canon(status=status)])[1]
    assert [r[0] for r in rows] == ["missing_in_legacy"]


# ── the -deleted bucket fold ─────────────────────────────────────────────────

def test_a_deleted_bucket_row_is_removed_in_its_base_suite():
    keys, rows = _one([_legacy(suite="Smoke-deleted", status="needs_review")], [_canon(status="deleted")])
    assert (keys, rows) == (1, [])


def test_an_active_row_and_its_bucket_row_fold_to_active():
    legacy = [_legacy(suite="Smoke-deleted", status="needs_review"), _legacy(suite="Smoke")]
    assert _one(legacy, [_canon()]) == (1, [])
    assert _one(list(reversed(legacy)), [_canon()]) == (1, [])


def test_a_suite_named_like_a_bucket_but_active_is_its_own_suite():
    keys, rows = _one([_legacy(suite="Smoke-deleted", status="active")], [_canon(suite="Smoke-deleted")])
    assert (keys, rows) == (1, [])


# ── matched keys: names, first sighting, priority, NULL pointers ─────────────

def test_names_differ_at_the_same_sighting_is_name_drift():
    run = uuid.uuid4()
    rows = _one([_legacy(name="test_login_v2", last=run)], [_canon(last=run)])[1]
    assert [r[0] for r in rows] == ["name_drift"]


def test_class_name_differs_at_the_same_sighting_is_name_drift():
    run = uuid.uuid4()
    assert _one([_legacy(cls="Other", last=run)], [_canon(last=run)])[1][0][0] == "name_drift"


def test_names_differ_at_different_sightings_is_name_stale():
    rows = _one([_legacy(name="test_login_v2", last=uuid.uuid4())], [_canon(last=uuid.uuid4())])[1]
    assert [r[0] for r in rows] == ["name_stale"]


def test_a_null_last_sighting_never_makes_a_drift():
    rows = _one([_legacy(name="test_login_v2", last=None)], [_canon(last=uuid.uuid4())])[1]
    assert [r[0] for r in rows] == ["name_stale"]


def test_null_and_empty_class_names_are_the_same():
    run = uuid.uuid4()
    assert _one([_legacy(cls=None, last=run)], [_canon(cls="", last=run)]) == (1, [])


def test_first_seen_differs_in_one_suite():
    rows = _one([_legacy(first=uuid.uuid4())], [_canon(first=uuid.uuid4())])[1]
    assert [r[0] for r in rows] == ["first_seen_differs"]


def test_first_seen_differs_in_several_suites_is_expected():
    legacy = [_legacy(first=uuid.uuid4()), _legacy(suite="Nightly")]
    rows = _one(legacy, [_canon(first=uuid.uuid4())])[1]
    assert ("first_seen_multi_suite", "Smoke", "active", "active") in rows
    assert ("moved_suite", "Nightly", "active", "other_suite") in rows


def test_a_null_first_sighting_never_differs():
    assert _one([_legacy(first=None)], [_canon(first=uuid.uuid4())]) == (1, [])
    assert _one([_legacy(first=uuid.uuid4())], [_canon(first=None)]) == (1, [])


def test_priority_the_first_blocking_reason_wins():
    run, first_a, first_b = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    both_blocking = _one([_legacy(name="Xq", last=run, first=first_a)], [_canon(last=run, first=first_b)])[1]
    assert [r[0] for r in both_blocking] == ["name_drift"]
    blocking_beats_expected = _one(
        [_legacy(name="Xq", last=uuid.uuid4(), first=first_a)], [_canon(last=uuid.uuid4(), first=first_b)])[1]
    assert [r[0] for r in blocking_beats_expected] == ["first_seen_differs"]


def test_priority_else_the_first_expected_reason_wins():
    legacy = [_legacy(name="Xq", last=uuid.uuid4(), first=uuid.uuid4()), _legacy(suite="Nightly")]
    rows = _one(legacy, [_canon(last=uuid.uuid4(), first=uuid.uuid4())])[1]
    assert [r[0] for r in rows if r[1] == "Smoke"] == ["name_stale"]


def test_an_empty_window_makes_every_removed_active_key_retire_pending():
    legacy = [_legacy(status="needs_review"), _legacy(fp=FP2, status="needs_review")]
    canonical = [_canon(), _canon(fp=FP2)]
    keys, divergences = smp.classify(legacy, canonical, set(), set())
    assert keys == 2 and {d.reason for d in divergences} == {"retire_pending"}
    assert all(d.blocking for d in divergences)


def test_every_key_lands_in_exactly_one_reason_and_divergences_come_in_key_order():
    pid = uuid.uuid4()
    legacy = [_legacy(suite="Zeta"), _legacy(suite="Alpha", fp=FP2, status="deleted")]
    keys, divergences = smp.classify(legacy, [], set(), set(), project_id=pid)
    assert keys == 2
    assert [(d.suite, d.reason) for d in divergences] == [("Alpha", "missing_in_canonical"),
                                                          ("Zeta", "missing_in_canonical")]
    assert {d.project_id for d in divergences} == {pid}
    assert {d.reason for d in divergences} <= set(vocab.BLOCKING_REASONS) | set(vocab.EXPECTED_REASONS)


# ── compare_project: four reads on the caller's session, nothing else ────────

class _Result:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)


class _ReadOnlySession:
    """Answers C-1..C-4 by the table each statement reads; any write method fails the test."""

    def __init__(self, legacy, canonical, seen, events):
        self.tables: list[str] = []
        self._answers = {"suite_memberships": legacy, "canonical_test_cases": canonical,
                         "test_cases": [(fp,) for fp in seen], "suite_membership_events": events}

    async def execute(self, statement):
        sql = str(statement.compile())
        table = next(t for t in ("suite_membership_events", "suite_memberships", "canonical_test_cases",
                                 "test_cases") if f"FROM {t}" in sql)
        assert "project_id" in sql, f"{table} read without the project scope"
        self.tables.append(table)
        return _Result(self._answers[table])

    def __getattr__(self, name):
        if name in {"add", "add_all", "flush", "commit", "rollback", "delete", "merge"}:
            raise AssertionError(f"compare_project called session.{name}: it only reads")
        raise AttributeError(name)


async def test_compare_project_reads_four_times_and_classifies():
    pid = uuid.uuid4()
    legacy = [tuple(_legacy(status="needs_review"))]
    canonical = [tuple(_canon())]
    session = _ReadOnlySession(legacy, canonical, seen=[FP], events=[("modified", 3), ("restored", 1)])
    result = await smp.compare_project(session, pid, window_runs=5)
    assert session.tables == ["suite_memberships", "canonical_test_cases", "test_cases", "suite_membership_events"]
    assert result.project_id == pid and result.keys == 1
    assert [d.reason for d in result.divergences] == ["deletion_window"]
    assert result.info_counts == {"legacy_modified_events": 3, "legacy_restored_events": 1}


async def test_compare_project_with_the_reconciler_off_reads_no_window():
    session = _ReadOnlySession([tuple(_legacy(status="needs_review"))], [tuple(_canon())], seen=[FP], events=[])
    result = await smp.compare_project(session, uuid.uuid4(), window_runs=0)
    assert "test_cases" not in session.tables and len(session.tables) == 3
    assert [d.reason for d in result.divergences] == ["retire_pending"]
    assert result.info_counts == {"legacy_modified_events": 0, "legacy_restored_events": 0}


async def test_compare_project_files_default_suite_cases_from_the_join():
    default = tuple(_canon(suite="Default Suite (Qx)", default=True))
    session = _ReadOnlySession([], [default], seen=[], events=[])
    result = await smp.compare_project(session, uuid.uuid4(), window_runs=5)
    assert [d.reason for d in result.divergences] == ["default_suite"]


# ── streak_days (§1.3) ───────────────────────────────────────────────────────

def _run(day: date, status: str, hour: int = 6, minute: int = 15):
    return {"status": status, "started_at": datetime(day.year, day.month, day.day, hour, minute, tzinfo=timezone.utc)}


def _today() -> date:
    return datetime.now(timezone.utc).date()


def test_no_runs_is_no_streak():
    assert smp.streak_days([], _today()) == 0


def test_today_not_yet_run_starts_from_yesterday():
    today = _today()
    runs = [_run(today - timedelta(days=d), "clean") for d in (1, 2, 3)]
    assert smp.streak_days(runs, today) == 3
    assert smp.streak_days(runs + [_run(today, "running")], today) == 3   # a running run is not a finished day


def test_today_clean_counts_today():
    today = _today()
    runs = [_run(today - timedelta(days=d), "clean") for d in (0, 1, 2)]
    assert smp.streak_days(runs, today) == 3


def test_a_gap_day_breaks_the_streak():
    today = _today()
    runs = [_run(today - timedelta(days=d), "clean") for d in (1, 2, 4, 5, 6)]
    assert smp.streak_days(runs, today) == 2


@pytest.mark.parametrize("bad", ["failed", "diffs"])
def test_a_failed_or_diffs_day_breaks_the_streak(bad):
    today = _today()
    runs = [_run(today - timedelta(days=1), "clean"), _run(today - timedelta(days=2), bad),
            _run(today - timedelta(days=3), "clean")]
    assert smp.streak_days(runs, today) == 1
    assert smp.streak_days([_run(today, bad)] + runs, today) == 0


def test_the_last_run_of_a_date_decides_it():
    today = _today()
    yesterday = today - timedelta(days=1)
    admin_then_beat = [_run(yesterday, "diffs", hour=3), _run(yesterday, "clean", hour=6)]
    assert smp.streak_days(admin_then_beat, today) == 1
    beat_then_admin = [_run(yesterday, "clean", hour=6), _run(yesterday, "diffs", hour=14)]
    assert smp.streak_days(beat_then_admin, today) == 0
    seven_clicks = [_run(yesterday, "clean", hour=h) for h in range(10, 17)]
    assert smp.streak_days(seven_clicks, today) == 1


def test_seven_and_eight_clean_days():
    today = _today()
    seven = [_run(today - timedelta(days=d), "clean") for d in range(1, 8)]
    assert smp.streak_days(seven, today) == 7 >= vocab.REQUIRED_CLEAN_DAYS
    eight = seven + [_run(today - timedelta(days=8), "clean")]
    assert smp.streak_days(eight, today) == 8
    assert smp.streak_days(seven[:6], today) == 6 < vocab.REQUIRED_CLEAN_DAYS


def test_a_run_late_in_a_utc_day_belongs_to_that_utc_date():
    today = _today()
    yesterday = today - timedelta(days=1)
    late = {"status": "clean", "started_at": datetime(yesterday.year, yesterday.month, yesterday.day, 23, 59,
                                                      tzinfo=timezone(timedelta(hours=-5)))}
    assert smp.streak_days([late], today + timedelta(days=1)) == 1   # 23:59 at UTC-5 is the next UTC date


# ── summarise: zero-filled counts, the caps ──────────────────────────────────

def _div(reason: str, pid: uuid.UUID, n: int) -> smp.Divergence:
    return smp.Divergence(reason=reason, project_id=pid, suite="Smoke", fingerprint=f"fpq-{n:05d}",
                          test_name=f"t{n}", legacy="active", canonical="absent")


def test_counts_name_every_reason_even_at_zero():
    summary = smp.summarise([])
    expected = set(vocab.BLOCKING_REASONS) | set(vocab.EXPECTED_REASONS) | set(vocab.INFO_COUNTS)
    assert set(summary["counts"]) == expected and set(summary["counts"].values()) == {0}
    assert (summary["projects_checked"], summary["keys_compared"]) == (0, 0)
    assert summary["per_project"] == [] and summary["sample"] == []
    assert summary["blocking_diffs"] == 0 and summary["expected_divergences"] == 0


def test_totals_split_blocking_from_expected_and_add_info_counts():
    pid = uuid.uuid4()
    clean = ProjectParity(project_id=uuid.uuid4(), keys=40, divergences=[],
                          info_counts={"legacy_modified_events": 2, "legacy_restored_events": 0})
    mixed = ProjectParity(project_id=pid, keys=10,
                          divergences=[_div("missing_in_canonical", pid, 1), _div("moved_suite", pid, 2),
                                       _div("moved_suite", pid, 3)],
                          info_counts={"legacy_modified_events": 1, "legacy_restored_events": 4})
    summary = smp.summarise([clean, mixed])
    assert (summary["projects_checked"], summary["keys_compared"]) == (2, 50)
    assert (summary["blocking_diffs"], summary["expected_divergences"]) == (1, 2)
    assert summary["counts"]["missing_in_canonical"] == 1 and summary["counts"]["moved_suite"] == 2
    assert summary["counts"]["legacy_modified_events"] == 3 and summary["counts"]["legacy_restored_events"] == 4
    assert summary["per_project"] == [{"project_id": str(pid), "blocking": 1, "expected": 2}]   # clean projects omitted
    assert {row["project_id"] for row in summary["sample"]} == {str(pid)}


def test_the_sample_keeps_twenty_per_reason_in_key_order():
    pid = uuid.uuid4()
    many = [_div("name_stale", pid, n) for n in range(35)] + [_div("retire_pending", pid, n) for n in range(25)]
    summary = smp.summarise([ProjectParity(project_id=pid, keys=60, divergences=many)])
    by_reason: dict[str, list[str]] = {}
    for row in summary["sample"]:
        by_reason.setdefault(row["reason"], []).append(row["fingerprint"])
    assert {r: len(v) for r, v in by_reason.items()} == {"name_stale": 20, "retire_pending": 20}
    assert by_reason["name_stale"] == [f"fpq-{n:05d}" for n in range(20)]
    assert summary["counts"]["name_stale"] == 35 and summary["counts"]["retire_pending"] == 25   # counts are NOT capped
    assert [row["reason"] for row in summary["sample"]][0] == "retire_pending"   # blocking reasons first
    assert set(summary["sample"][0]) == {"reason", "project_id", "suite", "fingerprint", "test_name", "legacy",
                                         "canonical"}


def test_per_project_keeps_two_hundred_most_blocking_first():
    results = []
    for n in range(230):
        pid = uuid.uuid4()
        divs = [_div("retire_pending", pid, i) for i in range(n % 7)] + [_div("legacy_stale", pid, 99)]
        results.append(ProjectParity(project_id=pid, keys=10, divergences=divs))
    summary = smp.summarise(results)
    lines = summary["per_project"]
    assert len(lines) == vocab.PER_PROJECT_MAX
    assert [(line["blocking"], line["expected"]) for line in lines] == sorted(
        ((line["blocking"], line["expected"]) for line in lines), reverse=True)
    assert lines[0]["blocking"] == 6 and lines[-1]["blocking"] <= lines[0]["blocking"]
    assert summary["projects_checked"] == 230 and summary["blocking_diffs"] == sum(n % 7 for n in range(230))
