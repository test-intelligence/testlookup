"""W05 REL-01: readable release-gate reasons (``services/release_gate_reasons.py``).

The rollup names a blocking test ``"{suite}::{fingerprint} is FAILED"``; every
surface that shows it says ``"test_pay (Checkout) is FAILED"`` instead. These
pin the format (byte for byte the run email's), the pass-through rules, the
one-query batch, and that a fingerprint is only resolved inside the project
that asked (fingerprints are not globally unique).
"""
from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

from sqlalchemy.dialects import postgresql

from app.services import release_gate_reasons as reasons
from app.services.notification import run_report as rr


class _Db:
    """Counts statements and answers each with the scripted rows."""

    def __init__(self, rows):
        self._rows = rows
        self.statements: list[str] = []

    async def execute(self, stmt, *a, **kw):
        self.statements.append(str(stmt.compile(dialect=postgresql.dialect())))
        return SimpleNamespace(all=lambda: list(self._rows))


def test_reason_key_splits_suite_fingerprint_and_status():
    assert reasons.reason_key("Checkout::abc123 is FAILED") == ("Checkout", "abc123", "FAILED")
    assert reasons.reason_key("::abc123 is BROKEN") == ("", "abc123", "BROKEN")
    assert reasons.reason_key("CRITICAL defect open: Totals") == (None, None, "CRITICAL defect open: Totals")


def test_the_email_keeps_its_names_as_aliases():
    assert rr._reason_key is reasons.reason_key
    assert rr._readable_reasons is reasons.readable_reasons


def test_one_project_reads_test_names_and_passes_the_rest_through():
    db = _Db([("abc123", "test_pay")])
    out = asyncio.run(reasons.readable_reasons(
        db, uuid.uuid4(),
        ["Checkout::abc123 is FAILED", "Checkout::zzz is BROKEN", "CRITICAL defect open: Totals"],
    ))
    assert out == ["test_pay (Checkout) is FAILED", "Checkout::zzz is BROKEN", "CRITICAL defect open: Totals"]
    assert len(db.statements) == 1


def test_no_query_when_no_reason_names_a_test():
    db = _Db([])
    out = asyncio.run(reasons.readable_reasons(db, uuid.uuid4(), ["CRITICAL defect open: Totals"]))
    assert out == ["CRITICAL defect open: Totals"]
    assert db.statements == []
    project = uuid.uuid4()
    assert asyncio.run(reasons.display_reasons_by_project(db, {project: ["a defect"]})) == {
        project: ["a defect"]
    }
    assert asyncio.run(reasons.display_reasons_for_release(db, uuid.uuid4(), ["a defect"])) == ["a defect"]
    assert db.statements == []


def test_an_empty_suite_reads_no_suite():
    db = _Db([("fp1", "test_login")])
    out = asyncio.run(reasons.readable_reasons(db, uuid.uuid4(), ["::fp1 is BROKEN"]))
    assert out == ["test_login (no suite) is BROKEN"]


def test_the_batch_is_one_query_keyed_by_project_and_fingerprint():
    a, b = uuid.uuid4(), uuid.uuid4()
    # The same fingerprint in two projects: only B's test is named "test_b".
    db = _Db([(b, "fp-shared", "test_b"), (a, "fp-a", "test_a")])
    out = asyncio.run(reasons.display_reasons_by_project(db, {
        a: ["S::fp-shared is FAILED", "S::fp-a is FAILED", "open defect"],
        b: ["S::fp-shared is FAILED"],
    }))
    assert out[a] == ["S::fp-shared is FAILED", "test_a (S) is FAILED", "open defect"]
    assert out[b] == ["test_b (S) is FAILED"]
    assert len(db.statements) == 1
    sql = " ".join(db.statements[0].split())
    assert "(test_runs.project_id, test_cases.test_fingerprint) IN" in sql
    assert "GROUP BY test_runs.project_id, test_cases.test_fingerprint" in sql


def test_the_release_form_resolves_its_project_inside_the_one_query():
    db = _Db([("abc123", "test_pay")])
    out = asyncio.run(reasons.display_reasons_for_release(
        db, uuid.uuid4(), ["checkout-e2e::abc123 is FAILED"]
    ))
    assert out == ["test_pay (checkout-e2e) is FAILED"]
    assert len(db.statements) == 1
    sql = " ".join(db.statements[0].split())
    assert "test_runs.project_id = (SELECT releases.project_id" in sql
