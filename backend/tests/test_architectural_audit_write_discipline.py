"""
Architectural audit-write-discipline test — pins the ``backend.audit-write-
discipline`` quality gate (``scripts/quality_gate.py``).

Background (US-13.3 compliance audit, finding 3): ``settings_audit_log``,
``access_audit_logs``, ``test_case_audit_logs`` and ``identity_events``
documented themselves as *immutable*, but nothing enforced it — the entire
migration set contains one trigger (the search-vector trigger in ``0001``),
no grants are restricted, and no store is WORM. The models now say
**append-only by application convention**, and this guard is the convention's
enforcement. These tests keep that honest:

  * the current tree is clean (no UPDATE anywhere, no DELETE outside the
    retention purge);
  * each violation shape the guard claims to catch is actually caught —
    a guard that cannot catch the violation is worse than none;
  * the allowlist stays honest: ``services/retention_service.py`` is the only
    module that deletes audit rows, and removing it from the allowlist makes
    the guard fire on exactly those deletes.
"""
from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_APP = REPO_ROOT / "backend" / "app"
RETENTION_SERVICE = BACKEND_APP / "services" / "retention_service.py"


def _load_quality_gate_module():
    path = REPO_ROOT / "scripts" / "quality_gate.py"
    if not path.exists():
        pytest.skip("scripts/quality_gate.py not present")
    spec = importlib.util.spec_from_file_location("quality_gate_audit_guard", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def qg():
    return _load_quality_gate_module()


def _run(qg, tmp_path: Path, source: str) -> list[str]:
    """Run the guard over a synthetic module and return its messages."""
    (tmp_path / "synthetic_service.py").write_text(source, encoding="utf-8")
    return [v.message for v in qg._backend_audit_write_discipline(root=tmp_path)]


# ── The tree is clean ────────────────────────────────────────────────────────


def test_guard_is_registered_with_a_fix_hint(qg):
    guard = qg.GUARD_BY_NAME.get("backend.audit-write-discipline")
    assert guard is not None, "guard must be registered so `make quality-gate` runs it"
    assert guard.fix_hint, "every guard carries an actionable fix hint"


def test_current_tree_has_no_audit_write_violations(qg):
    violations = qg._backend_audit_write_discipline()
    assert violations == [], (
        "audit tables must stay append-only:\n"
        + "\n".join(v.format() for v in violations)
    )


def test_no_baseline_file_exists(qg):
    """This guard ships at zero — there is nothing legacy to tolerate.

    A baseline would quietly re-open the hole the guard exists to close, so
    its absence is part of the contract.
    """
    guard = qg.GUARD_BY_NAME["backend.audit-write-discipline"]
    assert not guard.baseline_path.exists(), (
        f"{guard.baseline_path} exists — an audit-write exception was baselined "
        "instead of fixed or allowlisted with a reason"
    )


# ── Every claimed violation shape is actually caught ─────────────────────────


def test_core_update_construct_is_caught(qg, tmp_path):
    messages = _run(qg, tmp_path, """
from sqlalchemy import update
from app.models.postgres import AccessAuditLog


async def rewrite_history(db):
    await db.execute(
        update(AccessAuditLog)
        .where(AccessAuditLog.action == "role_changed")
        .values(action="redacted")
    )
""")
    assert any("UPDATE against an audit table" in m for m in messages), messages


def test_namespaced_update_construct_is_caught(qg, tmp_path):
    messages = _run(qg, tmp_path, """
import sqlalchemy as sa
from app.models.postgres import SettingsAuditLog


async def rewrite(db):
    await db.execute(sa.update(SettingsAuditLog).values(actor_name="anon"))
""")
    assert any("UPDATE against an audit table" in m for m in messages), messages


def test_orm_bulk_update_chain_is_caught(qg, tmp_path):
    messages = _run(qg, tmp_path, """
from app.models.postgres import TestCaseAuditLog


def scrub(db):
    db.query(TestCaseAuditLog).update({"details": None})
""")
    assert any("UPDATE against an audit table" in m for m in messages), messages


def test_attribute_mutation_on_a_fetched_row_is_caught(qg, tmp_path):
    messages = _run(qg, tmp_path, """
from sqlalchemy import select
from app.models.postgres import IdentityEvent


async def tidy(db, event_id):
    row = (
        await db.execute(select(IdentityEvent).where(IdentityEvent.id == event_id))
    ).scalar_one_or_none()
    row.error_message = None
    await db.flush()
""")
    assert any(
        "attribute assignment on fetched audit row" in m and "error_message" in m
        for m in messages
    ), messages


def test_mutation_of_a_loop_variable_over_fetched_rows_is_caught(qg, tmp_path):
    messages = _run(qg, tmp_path, """
from sqlalchemy import select
from app.models.postgres import SettingsAuditLog


async def scrub(db):
    rows = (await db.execute(select(SettingsAuditLog))).scalars().all()
    for row in rows:
        row.actor_name = "redacted"
""")
    assert any(
        "attribute assignment on fetched audit row" in m and "actor_name" in m
        for m in messages
    ), messages


def test_setattr_on_a_fetched_row_is_caught(qg, tmp_path):
    messages = _run(qg, tmp_path, """
from sqlalchemy import select
from app.models.postgres import AccessAuditLog


async def sneaky(db, field, value):
    row = (await db.execute(select(AccessAuditLog))).scalars().first()
    setattr(row, field, value)
""")
    assert any("setattr on fetched audit row" in m for m in messages), messages


def test_session_delete_of_a_fetched_row_is_caught(qg, tmp_path):
    messages = _run(qg, tmp_path, """
from sqlalchemy import select
from app.models.postgres import TestCaseAuditLog


async def purge_one(db):
    row = (await db.execute(select(TestCaseAuditLog))).scalars().first()
    await db.delete(row)
""")
    assert any("session delete of fetched audit row" in m for m in messages), messages


def test_core_delete_construct_is_caught(qg, tmp_path):
    messages = _run(qg, tmp_path, """
from sqlalchemy import delete
from app.models.postgres import SettingsAuditLog


async def wipe(db):
    await db.execute(delete(SettingsAuditLog))
""")
    assert any("DELETE against an audit table" in m for m in messages), messages


def test_raw_sql_update_and_delete_are_caught(qg, tmp_path):
    messages = _run(qg, tmp_path, """
from sqlalchemy import text


async def raw(db):
    await db.execute(text("UPDATE settings_audit_log SET actor_name = 'x'"))
    await db.execute(text("DELETE FROM access_audit_logs WHERE id = :id"))
    await db.execute(text("TRUNCATE TABLE identity_events"))
""")
    assert any("raw SQL UPDATE of an audit table" in m for m in messages), messages
    assert sum("raw SQL DELETE/TRUNCATE" in m for m in messages) == 2, messages


# ── No false positives on the legitimate write paths ─────────────────────────


def test_inserting_an_audit_row_is_not_a_violation(qg, tmp_path):
    """Building a row and setting its attributes before flush is one INSERT."""
    messages = _run(qg, tmp_path, """
from app.models.postgres import SettingsAuditLog


async def record(db, key, actor):
    row = SettingsAuditLog(setting_key=key, action="updated")
    row.actor_id = actor
    db.add(row)
    await db.flush()
""")
    assert messages == []


def test_reading_audit_rows_is_not_a_violation(qg, tmp_path):
    messages = _run(qg, tmp_path, """
from sqlalchemy import select
from app.models.postgres import AccessAuditLog


async def latest(db):
    rows = (
        await db.execute(select(AccessAuditLog).order_by(AccessAuditLog.created_at))
    ).scalars().all()
    return [row.action for row in rows]
""")
    assert messages == []


def test_same_local_name_in_a_different_function_is_not_confused(qg, tmp_path):
    """Scope isolation — a ``row`` bound to a non-audit model elsewhere in the
    same module must not inherit the audit binding (the real-world shape in
    ``retention_service.upsert_policy``)."""
    messages = _run(qg, tmp_path, """
from sqlalchemy import select
from app.models.postgres import ProjectRetentionPolicy, SettingsAuditLog


async def read_audit(db):
    rows = (await db.execute(select(SettingsAuditLog))).scalars().all()
    for row in rows:
        yield row.action


async def upsert_policy(db, project_id, enabled):
    row = (
        await db.execute(
            select(ProjectRetentionPolicy).where(
                ProjectRetentionPolicy.project_id == project_id
            )
        )
    ).scalar_one_or_none()
    row.enabled = enabled
    await db.flush()
""")
    assert messages == []


# ── The allowlist stays honest ───────────────────────────────────────────────


def test_retention_service_is_the_only_audit_deleter(qg):
    """Drop the allowlist and the guard must fire on the retention purge —
    and on nothing else in ``backend/app/``.

    If this starts failing with extra files, a second deleter appeared and the
    audit-model docstrings ("rows are removed only by the retention purge on
    the audit clock") became false.
    """
    original = set(qg._AUDIT_DELETE_ALLOWLIST)
    try:
        qg._AUDIT_DELETE_ALLOWLIST.clear()
        violations = qg._backend_audit_write_discipline()
    finally:
        qg._AUDIT_DELETE_ALLOWLIST.clear()
        qg._AUDIT_DELETE_ALLOWLIST.update(original)

    assert violations, (
        "removing the allowlist produced no violations — either the retention "
        "purge stopped deleting audit rows (update the model docstrings) or "
        "the guard stopped detecting deletes"
    )
    offenders = {v.file for v in violations}
    assert offenders == {RETENTION_SERVICE}, (
        "only services/retention_service.py may delete audit rows; found: "
        + ", ".join(sorted(str(p) for p in offenders))
    )
    assert all("DELETE against an audit table" in v.message for v in violations)


def test_allowlist_exempts_deletes_only_never_updates(qg, tmp_path):
    """Even the retention service may not rewrite an audit row."""
    source = """
from sqlalchemy import delete, update
from app.models.postgres import AccessAuditLog


async def purge(db, cutoff):
    await db.execute(delete(AccessAuditLog).where(AccessAuditLog.created_at < cutoff))
    await db.execute(update(AccessAuditLog).values(actor_name="anon"))
"""
    path = tmp_path / "synthetic_service.py"
    path.write_text(source, encoding="utf-8")

    # The guard keys its allowlist on the repo-relative posix path, falling
    # back to the absolute one for files outside the repo (basetemp may be
    # either, depending on how pytest was invoked).
    try:
        rel = path.relative_to(qg.REPO_ROOT).as_posix()
    except ValueError:
        rel = path.as_posix()

    original = set(qg._AUDIT_DELETE_ALLOWLIST)
    try:
        qg._AUDIT_DELETE_ALLOWLIST.add(rel)
        messages = [
            v.message for v in qg._backend_audit_write_discipline(root=tmp_path)
        ]
    finally:
        qg._AUDIT_DELETE_ALLOWLIST.clear()
        qg._AUDIT_DELETE_ALLOWLIST.update(original)

    assert not any("DELETE against an audit table" in m for m in messages), messages
    assert any("UPDATE against an audit table" in m for m in messages), messages


def test_allowlist_entry_points_at_a_real_file(qg):
    for rel in qg._AUDIT_DELETE_ALLOWLIST:
        assert (REPO_ROOT / rel).exists(), f"stale allowlist entry: {rel}"


def test_retention_purge_deletes_only_the_project_scoped_audit_tables(qg):
    """The audit-clock deletes are the PROJECT-SCOPED audit tables only:
    ``access_audit_logs``, ``test_case_audit_logs`` and (since epic ACT)
    ``project_activity_events``.

    ``settings_audit_log`` must stay out of it — it holds the purge-audit rows
    themselves, which is what the model docstring promises. ``identity_events``
    stays out because it has no project scope to purge by.
    """
    tree = ast.parse(RETENTION_SERVICE.read_text(encoding="utf-8"))
    deleted: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and qg._audit_call_name(node.func) == "delete"
            and node.args
        ):
            deleted |= qg._audit_referenced_names(node.args[0]) & qg._AUDIT_MODELS
    assert deleted == {
        "AccessAuditLog",
        "TestCaseAuditLog",
        "ProjectActivityEvent",
    }, deleted


# ── The docstrings match the enforcement ─────────────────────────────────────


@pytest.mark.parametrize(
    "model_name",
    [
        "SettingsAuditLog",
        "AccessAuditLog",
        "TestCaseAuditLog",
        "IdentityEvent",
        "ProjectActivityEvent",
    ],
)
def test_audit_model_docstrings_claim_append_only_not_immutable(model_name):
    """The docstrings must describe the guarantee the code actually provides."""
    pytest.importorskip("sqlalchemy")
    from app.models import postgres as models

    doc = getattr(models, model_name).__doc__ or ""
    lowered = doc.lower()
    assert "append-only" in lowered, f"{model_name} docstring lost the real claim"
    assert "not by database enforcement" in lowered, (
        f"{model_name} docstring must name the boundary — nothing in the "
        "database enforces append-only"
    )
    assert "audit-write-discipline" in lowered, (
        f"{model_name} docstring must point at the guard that enforces it"
    )
    assert "immutable" not in lowered, (
        f"{model_name} docstring still claims immutability, which no trigger, "
        "grant or WORM store backs"
    )
