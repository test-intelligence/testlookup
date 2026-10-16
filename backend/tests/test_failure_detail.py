"""FAIL-01: per-failure detail on /failures (latest error, stack head, owner, run link).

``failure_detail_service.latest_failure_detail_by_fingerprint`` reads, for the
fingerprints of one top-failing page, the test's newest FAILED/BROKEN row in
ONE project and shapes it: the first error line and the first 20 trace lines,
truncated and redacted server-side; the owner (eligible assignee, else the
project's ownership-rule team, else nobody). ``top_failing_tests`` adds the
keys to every row, with ``detail_available`` saying whether it could (one
project in scope) or not (All Projects).

These are the pure and fake-session halves; ``integration/
test_failure_detail_postgres.py`` proves the statement on real Postgres.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.services import failure_detail_service as fds

PID = uuid.UUID("11111111-2222-4333-8444-555555555555")


# ── a recording session ──────────────────────────────────────────────────────


class _Result:
    def __init__(self, rows=None, scalars=None, scalar=None):
        self._rows = rows or []
        self._scalars = scalars or []
        self._scalar = scalar

    def fetchall(self):
        return [SimpleNamespace(_mapping=row) for row in self._rows]

    def scalars(self):
        return SimpleNamespace(all=lambda: list(self._scalars))

    def scalar_one_or_none(self):
        return self._scalar


class _Session:
    """Answers the detail statement with ``rows``, the rules read with
    ``rules`` and the component-map read with ``component_map``; records every
    statement and its params."""

    def __init__(self, rows, rules=(), component_map=None):
        self.rows, self.rules, self.component_map = rows, list(rules), component_map
        self.calls: list[tuple[str, dict]] = []

    async def execute(self, statement, params=None):
        sql = str(statement)
        self.calls.append((sql, dict(params or {})))
        if "DISTINCT ON (tc.test_fingerprint)" in sql:
            return _Result(rows=self.rows)
        if "service_ownership_rules" in sql:
            return _Result(scalars=self.rules)
        if "component_owner_map" in sql:
            return _Result(scalar=self.component_map)
        raise AssertionError(f"unexpected statement: {sql[:200]}")


def _row(fp="fp-1", **overrides):
    base = {
        "test_fingerprint": fp,
        "test_case_id": uuid.UUID("aaaaaaaa-0000-4000-8000-000000000001"),
        "test_run_id": uuid.UUID("bbbbbbbb-0000-4000-8000-000000000001"),
        "created_at": datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc),
        "error_head": "AssertionError: expected 3 but was 4\n  at line 2",
        "stack_head": "Traceback (most recent call last):\n  File \"x.py\", line 10",
        "assigned_to_user_id": None,
        "suite_name": "Checkout",
        "class_name": "CheckoutTest",
        "package_name": "com.acme",
        "full_name": "com.acme.CheckoutTest.pays",
        "own_label": None,
        "own_owner": None,
        "assignee_name": None,
    }
    base.update(overrides)
    return base


def _rule(match_type="suite_name", pattern="Checkout", team="Payments"):
    return SimpleNamespace(
        id=uuid.uuid4(), match_type=match_type, match_pattern=pattern,
        service_name="svc", team_name=team, team_contact=None,
    )


def _placeholders(sql: str) -> set[str]:
    return set(re.findall(r"(?<!:):([a-z_][a-z0-9_]*)", sql))


# ── 3. truncation and redaction ─────────────────────────────────────────────


def test_the_first_error_line_is_cut_to_300_characters():
    assert fds.first_error_line("x" * 5000) == "x" * 300
    assert fds.first_error_line("first\r\nsecond") == "first"
    assert fds.first_error_line("first\rsecond") == "first"
    # A message that opens with a newline still has a first line.
    assert fds.first_error_line("\n\n  AssertionError: boom  \nmore") == "AssertionError: boom"


@pytest.mark.parametrize("blank", [None, "", "   ", "\n\r\n"])
def test_a_blank_message_is_none_never_an_empty_string(blank):
    assert fds.first_error_line(blank) is None
    assert fds.stack_head(blank) is None


def test_the_stack_head_is_the_first_20_lines_each_cut_to_300():
    trace = "\n".join(f"  frame {i} " + "y" * 400 for i in range(100))
    head = fds.stack_head(trace)
    lines = head.split("\n")
    assert len(lines) == 20
    assert all(len(line) <= 300 for line in lines)
    assert lines[0].startswith("  frame 0 "), "indentation is how a trace reads: kept"
    assert lines[19].startswith("  frame 19 ")


def test_credentials_are_redacted_and_line_numbers_survive():
    trace = (
        "requests.exceptions.HTTPError: 401\n"
        "  sent Authorization: Bearer abc.def.ghi\n"
        "  with password=hunter2\n"
        '  File "client.py", line 1234567890, in call'
    )
    head = fds.stack_head(trace)
    assert "abc.def.ghi" not in head and "hunter2" not in head
    assert "[REDACTED]" in head
    # redact_text's phone heuristic turns this into [REDACTED_PHONE]: the
    # snippet goes through redact_log_message (markers only) instead.
    assert "line 1234567890" in head
    error = fds.first_error_line("login failed: password=hunter2 at line 1234567890")
    assert "hunter2" not in error and "line 1234567890" in error


# ── owner precedence and statement count ────────────────────────────────────


@pytest.mark.asyncio
async def test_an_eligible_assignee_owns_the_row_in_one_statement():
    rows = [_row(f"fp-{i}", assignee_name=f"Priya {i}") for i in range(20)]
    db = _Session(rows, rules=[_rule()])
    out = await fds.latest_failure_detail_by_fingerprint(db, PID, [r["test_fingerprint"] for r in rows], days=30)
    assert len(db.calls) == 1, "every row has an eligible assignee: no rules read"
    assert out["fp-3"]["owner"] == "Priya 3"
    assert out["fp-3"]["owner_source"] == "assignee"
    assert out["fp-3"]["owner_team"] is None


@pytest.mark.asyncio
async def test_rows_without_an_assignee_read_the_rules_in_three_statements():
    rows = [_row(f"fp-{i}") for i in range(20)]
    db = _Session(rows, rules=[_rule()])
    out = await fds.latest_failure_detail_by_fingerprint(db, PID, [r["test_fingerprint"] for r in rows], days=30)
    assert len(db.calls) == 3
    assert (out["fp-0"]["owner"], out["fp-0"]["owner_source"], out["fp-0"]["owner_team"]) == (
        "Payments", "rule", "Payments",
    )


@pytest.mark.asyncio
async def test_the_rule_team_rides_beside_an_assignee_once_the_rules_are_read():
    rows = [_row("fp-a", assignee_name="Priya Nair"), _row("fp-b")]
    db = _Session(rows, rules=[_rule()])
    out = await fds.latest_failure_detail_by_fingerprint(db, PID, ["fp-a", "fp-b"], days=30)
    assert (out["fp-a"]["owner"], out["fp-a"]["owner_source"], out["fp-a"]["owner_team"]) == (
        "Priya Nair", "assignee", "Payments",
    )


@pytest.mark.asyncio
async def test_the_component_map_and_the_owner_label_name_their_source():
    rows = [_row("fp-map", class_name="BillingTest", suite_name="Billing"),
            _row("fp-label", class_name="Other", suite_name="Other", own_owner="Team Label")]
    db = _Session(rows, component_map={"Billing*": {"team": "Billing Team"}})
    out = await fds.latest_failure_detail_by_fingerprint(db, PID, ["fp-map", "fp-label"], days=30)
    assert (out["fp-map"]["owner"], out["fp-map"]["owner_source"]) == ("Billing Team", "component_map")
    assert (out["fp-label"]["owner"], out["fp-label"]["owner_source"]) == ("Team Label", "test_label")


@pytest.mark.asyncio
async def test_nobody_is_none_never_a_placeholder_name():
    db = _Session([_row("fp-1")])
    out = await fds.latest_failure_detail_by_fingerprint(db, PID, ["fp-1"], days=30)
    assert (out["fp-1"]["owner"], out["fp-1"]["owner_source"], out["fp-1"]["owner_team"]) == (None, None, None)


@pytest.mark.asyncio
async def test_no_fingerprint_issues_no_statement():
    db = _Session([])
    assert await fds.latest_failure_detail_by_fingerprint(db, PID, [], days=30) == {}
    assert await fds.latest_failure_detail_by_fingerprint(db, PID, ["", None], days=30) == {}
    assert db.calls == []


@pytest.mark.asyncio
async def test_the_row_is_shaped_for_the_wire():
    db = _Session([_row("fp-1", created_at=datetime(2026, 10, 1, 14, 0))])
    out = (await fds.latest_failure_detail_by_fingerprint(db, PID, ["fp-1"], days=30))["fp-1"]
    assert set(out) == set(fds.DETAIL_KEYS)
    assert out["latest_run_id"] == "bbbbbbbb-0000-4000-8000-000000000001"
    assert out["latest_test_case_id"] == "aaaaaaaa-0000-4000-8000-000000000001"
    assert out["latest_failed_at"] == "2026-10-01T14:00:00+00:00"
    assert out["latest_error"] == "AssertionError: expected 3 but was 4"
    assert out["latest_stack_head"].startswith("Traceback")


# ── 2. the statement is pinned to the project and scoped like the list ──────


@pytest.mark.asyncio
async def test_the_statement_pins_the_project_and_binds_every_placeholder():
    rid = "3f9a5f7e-1c2b-4d6a-9e8f-0a1b2c3d4e5f"
    db = _Session([_row("fp-1")])
    start = datetime(2026, 9, 1, tzinfo=timezone.utc)
    await fds.latest_failure_detail_by_fingerprint(
        db, str(PID), ["fp-1", "fp-2"], days=30, suite_name="Checkout", release_id=rid, period_start=start,
    )
    sql, params = db.calls[0]
    assert "tr.project_id = :detail_project_id" in sql
    assert params["detail_project_id"] == PID
    assert params["period_start"] == start, "the caller's window start, not a second clock"
    assert "primary_release_id" in sql and params["release_id"] == rid
    assert params["suite_name"] == "checkout"
    assert "FAILED" in sql and "BROKEN" in sql
    # Rendered statements expand the fingerprint list; every named bind is bound.
    assert not (_placeholders(sql) - set(params) - {"detail_fingerprints"})


# ── top_failing_tests carries the keys ──────────────────────────────────────


class _TopSession:
    """The top-N statement answers ``items``; the failure-step anchors none;
    the detail statement ``detail``."""

    def __init__(self, items, detail):
        self.items, self.detail = items, detail
        self.calls: list[str] = []

    async def execute(self, statement, params=None):
        sql = str(statement)
        self.calls.append(sql)
        if "DISTINCT ON (tc.test_fingerprint)" in sql:
            return _Result(rows=self.detail)
        if "canonical_test_cases" in sql:
            return SimpleNamespace(all=lambda: [])
        return _Result(rows=self.items)


_TOP_ITEM = {
    "test_fingerprint": "fp-1", "test_name": "t1", "suite_name": "Checkout", "class_name": "C",
    "failure_category": None, "fail_count": 3, "last_failed": None,
    "last_failed_run_id": None, "last_failed_build": None,
}


@pytest.mark.asyncio
async def test_all_projects_rows_say_detail_is_unavailable_and_carry_no_detail():
    from app.services.analytics_service import top_failing_tests

    db = _TopSession([dict(_TOP_ITEM)], [_row("fp-1", assignee_name="Priya")])
    out = await top_failing_tests(db, None, 30, 15)
    item = out["items"][0]
    assert item["detail_available"] is False
    assert all(item[key] is None for key in fds.DETAIL_KEYS)
    assert not any("DISTINCT ON (tc.test_fingerprint)" in sql for sql in db.calls)


@pytest.mark.asyncio
async def test_one_project_rows_gain_the_detail_and_keep_every_existing_key():
    from app.services.analytics_service import top_failing_tests

    db = _TopSession([dict(_TOP_ITEM)], [_row("fp-1", assignee_name="Priya Nair")])
    out = await top_failing_tests(db, str(PID), 30, 15)
    item = out["items"][0]
    assert item["detail_available"] is True
    assert item["owner"] == "Priya Nair" and item["owner_source"] == "assignee"
    assert item["latest_test_case_id"] == "aaaaaaaa-0000-4000-8000-000000000001"
    existing = {"test_fingerprint", "test_name", "suite_name", "class_name", "failure_category",
                "fail_count", "last_failed", "last_failed_run_id", "last_failed_build",
                "failure_kind", "failure_step"}
    assert existing | {"detail_available", *fds.DETAIL_KEYS} == set(item)


@pytest.mark.asyncio
async def test_a_listed_test_without_a_located_row_reads_none_not_a_neighbours_detail():
    from app.services.analytics_service import top_failing_tests

    items = [dict(_TOP_ITEM), dict(_TOP_ITEM, test_fingerprint="fp-2", test_name="t2")]
    db = _TopSession(items, [_row("fp-1", assignee_name="Priya")])
    out = await top_failing_tests(db, str(PID), 30, 15)
    by_fp = {i["test_fingerprint"]: i for i in out["items"]}
    assert by_fp["fp-1"]["owner"] == "Priya"
    assert by_fp["fp-2"]["detail_available"] is True
    assert all(by_fp["fp-2"][key] is None for key in fds.DETAIL_KEYS)
