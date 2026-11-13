"""W31 STRAT-12 — THE export proof against a real PostgreSQL and the REAL local object store (contract §6.3 G-B).

A subject (``zqxw…`` sentinels, non-hex) is seeded into EVERY inventory location the export declares
(``export_as != "none"``, iterated from ``INVENTORY`` — never a copied table), through the real writer where one
exists (``log_access_change``, ``record_activity`` via ``test_management_audit_service.audit_event``,
``settings_audit_service``, the comment service, ``mfa_service`` enrolment, ``refresh_token_service``, the API-key
route, ``sso_service.log_identity_event``) and by a direct insert everywhere else (chat, SSO links, SCIM groups,
product usage, every reference row — a generic row builder that reads the LIVE catalog: NOT NULL columns, CHECK
literals, enum labels, FK parents). A bystander (``yvkq…``) is seeded into the same locations.

Then the REAL runner (``run_export_job``) on the reference K3s install worker's 1 + 1 pool:
1. ``succeeded``; the stored archive verifies (``verify_archive_file``: chain intact, signature valid — signed);
2. EVERY exported location's member has ≥ 1 row of that location (a ``full`` / ``metadata`` row names it in
   ``_location``, a reference row by ``table`` + ``column``, ``profile.json`` for the profile category) —
   declaration is not emission: a location declared exported and never emitted is red;
3. no secret / hash sentinel (key hash, refresh ``jti`` hash, recovery-code digests, the TOTP ciphertext, the raw API
   key, the password hash) and NO bystander e-mail or bystander row anywhere in the archive bytes; the bystander's
   comment is absent from ``comments/``;
4. migration 0253: upgrade → downgrade → upgrade on a THROWAWAY database (created and dropped by this module);
5. (LAST) every row this module seeded — and every row its writers and the export created (jobs before users) —
   is deleted by id, and every touched table is back to its pre-test count (CI shares ONE Postgres database).

Fixture rules: ``dispose_engine_for_loop()`` before the engines are built and after they are disposed; Mongo is
never touched; Redis is never bound (the activity live tail is stubbed — the activity ROW is still written by the
real writer); object storage = ``LocalStorageProvider`` in a temp dir. Locally: ``pg_pytest_w31.sh b …`` on
``df_w31_b`` at 0253.
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import re
import subprocess
import sys
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, insert, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

BACKEND_ROOT = Path(__file__).resolve().parents[2]
SUBJECT = "zqxwsubj"
BYSTANDER = "yvkqbystand"
OWNER = "zqxwparentowner"
SUBJECT_EMAIL = "zqxw.subj@example.org"
BYSTANDER_EMAIL = "yvkq.bystand@example.org"
SUBJECT_ROW = "zqxwrow"          # the text filled into the subject's generic rows
BYSTANDER_ROW = "yvkqrow"        # … the bystander's (must never reach the subject's archive)
PARENT_ROW = "zqxwparent"        # … a parent row the builder had to create (a FK target)


def _dsn() -> str:
    value = os.environ.get("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


# ── the live catalog (what a generic row must carry) ─────────────────────────

@dataclass
class _Column:
    name: str
    nullable: bool
    has_default: bool
    data_type: str
    udt: str
    length: Optional[int]


@dataclass
class _Catalog:
    columns: dict[str, dict[str, _Column]] = field(default_factory=dict)
    checks: dict[str, dict[str, str]] = field(default_factory=dict)       # table → column → an allowed literal
    enums: dict[str, list[str]] = field(default_factory=dict)             # udt name → labels
    fks: dict[str, dict[str, str]] = field(default_factory=dict)          # table → column → referenced table
    pks: dict[str, list[str]] = field(default_factory=dict)


_ANY = re.compile(r"\(?\(?(\w+)\)?(?:::[\w ]+)?\s*=\s*ANY\s*\(\(?ARRAY\[(.*?)\]", re.IGNORECASE)
_EQ = re.compile(r"\(\(?(\w+)\)?(?:::[\w ]+)?\s*=\s*'([^']*)'", re.IGNORECASE)
_LITERAL = re.compile(r"'([^']*)'")


async def _read_catalog(db: Any) -> _Catalog:
    catalog = _Catalog()
    rows = (await db.execute(text(
        "SELECT table_name, column_name, is_nullable, column_default, data_type, udt_name, character_maximum_length "
        "FROM information_schema.columns WHERE table_schema = current_schema()"
    ))).all()
    for table, column, nullable, default, data_type, udt, length in rows:
        catalog.columns.setdefault(table, {})[column] = _Column(
            column, nullable == "YES", default is not None, data_type, udt, length)
    for table, definition in (await db.execute(text(
        "SELECT c.conrelid::regclass::text, pg_get_constraintdef(c.oid) FROM pg_constraint c "
        "JOIN pg_namespace n ON n.oid = c.connamespace WHERE c.contype = 'c' AND n.nspname = current_schema()"
    ))).all():
        found = catalog.checks.setdefault(table.strip('"'), {})
        for column, values in _ANY.findall(definition):
            literal = _LITERAL.search(values)
            if literal:
                found.setdefault(column, literal.group(1))
        for column, literal in _EQ.findall(definition):
            found.setdefault(column, literal)
    for udt, label in (await db.execute(text(
        "SELECT t.typname, e.enumlabel FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid ORDER BY e.enumsortorder"
    ))).all():
        catalog.enums.setdefault(udt, []).append(label)
    for table, column, target in (await db.execute(text(
        "SELECT cl.relname, att.attname, ref.relname FROM pg_constraint con "
        "JOIN pg_class cl ON cl.oid = con.conrelid JOIN pg_class ref ON ref.oid = con.confrelid "
        "JOIN pg_namespace n ON n.oid = cl.relnamespace "
        "JOIN pg_attribute att ON att.attrelid = cl.oid AND att.attnum = con.conkey[1] "
        "WHERE con.contype = 'f' AND n.nspname = current_schema()"
    ))).all():
        catalog.fks.setdefault(table, {})[column] = target
    for table, column in (await db.execute(text(
        "SELECT cl.relname, att.attname FROM pg_index i JOIN pg_class cl ON cl.oid = i.indrelid "
        "JOIN pg_namespace n ON n.oid = cl.relnamespace "
        "JOIN pg_attribute att ON att.attrelid = cl.oid AND att.attnum = ANY(i.indkey) "
        "WHERE i.indisprimary AND n.nspname = current_schema()"
    ))).all():
        catalog.pks.setdefault(table, []).append(column)
    return catalog


@dataclass(frozen=True)
class _Parent:
    """A value the builder resolves to the primary key of a FRESH row of ``table``."""

    table: str


def _table_defaults(now: datetime) -> dict[str, dict[str, Any]]:
    """Values a table's multi-column CHECKs / partial uniques need (the generic fill cannot infer them)."""
    return {
        "sso_configurations": {"provider_type": "OIDC", "oidc_issuer": "https://idp.example.org",
                               "oidc_client_id": "zqxw-client"},
        "agent_action_ledger": {"request_sha256": "a" * 64},
        "decision_report_eval_cycles": {"corpus_sha256": "b" * 64},
        "run_cadences": {"mode": "weekly", "weekdays": 31, "expected_by": time(9, 0)},
        "suite_membership_parity_runs": {"status": "clean", "finished_at": now},
        "project_workflow_defaults": {"workflow_id": "wf.zqxw-test"},
        "release_gate_policies": {"project_id": _Parent("projects"), "is_active": False},
    }


class _Seeder:
    """Generic rows through the ORM ``Table`` (a Core INSERT: Python defaults and types apply), every NOT NULL
    column without a default filled (a CHECK's first literal, an enum's first label, a typed value, a FRESH parent
    row for a FK, the row's owner for a user FK); every primary key recorded so the cleanup deletes exactly what
    was inserted, children first."""

    def __init__(self, catalog: _Catalog, *, now: datetime, users: dict[str, uuid.UUID]) -> None:
        self.catalog = catalog
        self.now = now
        self.users = users
        self.defaults = _table_defaults(now)
        self.inserted: list[tuple[str, dict[str, Any]]] = []
        self.bystander_rows: set[tuple[str, str]] = set()     # (table, primary key) of the bystander's rows

    def _typed(self, table: str, column: _Column, marker: str) -> Any:
        literal = self.catalog.checks.get(table, {}).get(column.name)
        kind = column.data_type
        if literal is not None:
            return int(literal) if kind in ("integer", "bigint", "smallint") else literal
        if kind == "uuid":
            return uuid.uuid4()
        if kind in ("text", "character varying", "character"):
            value = f"{marker}-{uuid.uuid4().hex[:12]}"
            return value[: column.length] if column.length else value
        if kind in ("integer", "bigint", "smallint"):
            return 1
        if kind in ("double precision", "real"):
            return 0.5
        if kind == "numeric":
            return Decimal("1")
        if kind == "boolean":
            return False
        if kind == "timestamp with time zone":
            return self.now
        if kind == "timestamp without time zone":
            return self.now.replace(tzinfo=None)
        if kind == "date":
            return self.now.date()
        if kind.startswith("time"):
            return time(9, 0)
        if kind in ("json", "jsonb"):
            return {}
        if kind == "ARRAY":
            return []
        if kind == "interval":
            return timedelta(0)
        if kind == "bytea":
            return b"zqxw"
        if kind == "inet":
            return "10.0.0.1"
        if kind == "USER-DEFINED" and self.catalog.enums.get(column.udt):
            return self.catalog.enums[column.udt][0]
        raise AssertionError(f"{table}.{column.name}: no generic value for {kind}")

    async def row(self, db: Any, table: str, *, owner: str, marker: str, values: Optional[dict[str, Any]] = None,
                  depth: int = 0) -> dict[str, Any]:
        from app.models.postgres import Base

        assert depth < 8, f"FK chain too deep at {table}"
        orm = Base.metadata.tables[table]
        given = {**{k: v for k, v in self.defaults.get(table, {}).items() if k in orm.c}, **(values or {})}
        for name, value in list(given.items()):
            if isinstance(value, _Parent):
                parent = await self.row(db, value.table, owner="owner", marker=PARENT_ROW, depth=depth + 1)
                given[name] = parent[self.catalog.pks[value.table][0]]
        row: dict[str, Any] = {}
        for name, column in self.catalog.columns[table].items():
            if name in given or name not in orm.c:
                continue
            if column.nullable or column.has_default or orm.c[name].default is not None:
                continue
            target = self.catalog.fks.get(table, {}).get(name)
            if target == "users":
                row[name] = self.users[owner]
            elif target is not None:
                parent = await self.row(db, target, owner="owner", marker=PARENT_ROW, depth=depth + 1)
                row[name] = parent[self.catalog.pks[target][0]]
            else:
                row[name] = self._typed(table, column, marker)
        row.update(given)
        if table == "dismissed_duplicate_pairs" and str(row["case_a_id"]) > str(row["case_b_id"]):
            row["case_a_id"], row["case_b_id"] = row["case_b_id"], row["case_a_id"]     # ck: case_a_id < case_b_id
        keys = self.catalog.pks.get(table) or []
        statement = insert(orm).values(**row).returning(*[orm.c[name] for name in keys])
        try:
            async with db.begin_nested():
                returned = (await db.execute(statement)).mappings().one()
        except Exception as exc:  # noqa: BLE001 - name the table: the generic builder needs a value for it
            raise AssertionError(f"seeding {table} failed: {type(exc).__name__}: {str(exc).splitlines()[0]}") from exc
        pk = dict(returned)
        self.inserted.append((table, pk))
        return {**row, **pk}

    async def cleanup(self, db: Any) -> None:
        from sqlalchemy import and_

        from app.models.postgres import Base

        for table, pk in reversed(self.inserted):
            orm = Base.metadata.tables[table]
            await db.execute(delete(orm).where(and_(*(orm.c[name] == value for name, value in pk.items()))))
        self.inserted.clear()


#: Tables a real writer, the signing step or the export itself may add rows to: their primary keys are read BEFORE
#: the test, and every row that is new afterwards is deleted by id (writers first, users last).
_WRITER_TABLES = (
    "subject_rights_jobs", "test_case_comments", "api_keys", "refresh_token_records", "mfa_recovery_codes",
    "access_audit_logs", "identity_events", "settings_audit_log", "test_case_audit_logs", "project_activity_events",
    "compliance_signing_keys", "secret_refs",
)


async def _keys_of(db: Any, table: str) -> set[Any]:
    from app.models.postgres import Base

    orm = Base.metadata.tables[table]
    (key,) = list(orm.primary_key.columns)
    return {row[0] for row in (await db.execute(select(key))).all()}


async def _count(db: Any, table: str) -> int:
    from app.models.postgres import Base

    return int((await db.execute(select(func.count()).select_from(Base.metadata.tables[table]))).scalar_one())


def _exported() -> list[Any]:
    from app.services.subject_data_inventory import INVENTORY

    return [location for location in INVENTORY if location.export_as != "none"]


#: Exported locations a REAL writer seeds (below); every other exported location gets a generic row.
_BY_WRITER = frozenset({
    "users.*", "mfa_recovery_codes.user_id", "api_keys.user_id", "refresh_token_records.user_id",
    "access_audit_logs.actor_user_id", "access_audit_logs.target_user_id", "identity_events.user_id",
    "identity_events.actor_id", "settings_audit_log.actor_id", "test_case_audit_logs.actor_id",
    "project_activity_events.actor_id", "test_case_comments.author_id",
})


@pytest_asyncio.fixture(loop_scope="module", scope="module")
async def env() -> Any:
    from app.db import postgres as app_postgres

    await app_postgres.dispose_engine_for_loop()
    engine = create_async_engine(_dsn(), pool_size=5, max_overflow=2)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield sessions
    finally:
        await engine.dispose()
        await app_postgres.dispose_engine_for_loop()


async def _users(db: Any, now: datetime) -> SimpleNamespace:
    from app.models.postgres import User, UserRole

    def person(name: str, email: str, full: str, role: UserRole) -> User:
        return User(id=uuid.uuid4(), username=name, email=email, full_name=full, role=role.value, is_active=True,
                    hashed_password="$2b$12$" + "zqxwPWHASHsentinel" * 3, must_change_password=False,
                    is_service_account=False, is_synthetic=False, avatar_color="#336699", last_login_at=now)

    people = SimpleNamespace(
        subject=person(SUBJECT, SUBJECT_EMAIL, "Zqxw Subjperson", UserRole.ADMIN),
        bystander=person(BYSTANDER, BYSTANDER_EMAIL, "Yvkq Bystander", UserRole.QA_LEAD),
        owner=person(OWNER, "zqxw.parentowner@example.org", "Zqxw Parentowner", UserRole.QA_LEAD),
    )
    db.add_all([people.subject, people.bystander, people.owner])
    await db.flush()
    return people


async def _through_the_writers(db: Any, people: SimpleNamespace, seeder: _Seeder) -> dict[str, Any]:
    """The product's own writers seed the subject (and the bystander where a row names both). Returns the raw
    secrets and the stored digests — none of which may reach the archive."""
    import pyotp

    from app.models.postgres import ApiKey, IdentityEventType, MfaRecoveryCode, RefreshTokenRecord, SecretRef
    from app.models.schemas import ApiKeyCreate, TestCaseCommentCreate
    from app.routers.api_keys import create_api_key
    from app.services import mfa_service, refresh_token_service, settings_audit_service, test_management_service
    from app.services.access_audit_service import log_access_change
    from app.services.sso_service import log_identity_event
    from app.services.test_management_audit_service import audit_event

    subject, bystander = people.subject, people.bystander
    await log_access_change(db, "member_added", subject, target_user_id=bystander.id, after_value={"role": "VIEWER"})
    await log_access_change(db, "role_changed", bystander, target_user_id=subject.id, before_value={"role": "VIEWER"},
                            after_value={"role": "ADMIN", "cc": BYSTANDER_EMAIL})
    await log_identity_event(db, IdentityEventType.SSO_LOGIN, user_id=subject.id, actor_id=bystander.id,
                             actor_name=bystander.username, detail={"email": SUBJECT_EMAIL})
    await log_identity_event(db, IdentityEventType.SCIM_USER_UPDATED, user_id=bystander.id, actor_id=subject.id,
                             actor_name=subject.username, detail={"email": BYSTANDER_EMAIL})
    await settings_audit_service.log_settings_change(db, "zqxw.setting", "updated", subject, ["value"])
    raw_refresh = await refresh_token_service.issue_refresh_token(db, subject.id)
    secret, _uri = await mfa_service.start_enrollment(db, subject)
    code = pyotp.TOTP(secret, interval=mfa_service.TOTP_INTERVAL_SECONDS, digits=mfa_service.TOTP_DIGITS).now()
    recovery = await mfa_service.confirm_enrollment(db, subject, code)
    assert recovery, "MFA enrolment did not confirm"
    case = await seeder.row(db, "managed_test_cases", owner="owner", marker=PARENT_ROW)
    mine = await test_management_service.add_test_case_comment(
        db, case["id"], TestCaseCommentCreate(content="zqxw looks right to me @" + BYSTANDER), subject)
    theirs = await test_management_service.add_test_case_comment(
        db, case["id"], TestCaseCommentCreate(content="yvkqcomment from the bystander"), bystander)
    await audit_event(db, "test_case", case["id"], case["project_id"], "updated", subject,
                      old_values={"title": "zqxw before"}, new_values={"title": "zqxw after"})
    await db.commit()
    created = await create_api_key(payload=ApiKeyCreate(name="zqxw ci key"), db=db, current_user=subject)   # commits

    digests: list[str] = []
    for column, where in ((ApiKey.key_hash, ApiKey.user_id == subject.id),
                          (RefreshTokenRecord.jti_hash, RefreshTokenRecord.user_id == subject.id),
                          (MfaRecoveryCode.code_hash, MfaRecoveryCode.user_id == subject.id),
                          (SecretRef.encrypted_value, (SecretRef.scope == mfa_service.TOTP_SECRET_SCOPE)
                           & (SecretRef.key_name == str(subject.id)))):
        found = [value for (value,) in (await db.execute(select(column).where(where))).all() if value]
        assert found, f"the writer stored no {column}"
        digests.extend(found)
    return {
        "secrets": [created.raw_key, raw_refresh, secret, *recovery, *digests, "zqxwPWHASHsentinel"],
        "comment_id": mine.id, "bystander_comment_id": theirs.id, "case": case,
    }


#: A table with exactly one row (its migration inserts it): its user columns are pointed at the subject for the
#: test and restored afterwards (never a second row; the bystander is not seeded there).
_SINGLETONS = {"instance_retention_policy": {"id": 1}}


def _public_pem() -> str:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    return Ed25519PrivateKey.generate().public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()


def _values_for(location: Any, person: Any, now: datetime) -> dict[str, Any]:
    """The row values that put ``person`` into ``location`` (plus what keeps the row harmless)."""
    if location.key == "user_invitations.email":
        values: dict[str, Any] = {"email": person.email, "accepted_user_id": None}
    else:
        assert location.match == "fk" and location.column, f"{location.key}: an exported location without a filter"
        values = {location.column: person.id}
    if location.table == "compliance_signing_keys":       # never the deployment's ACTIVE key
        values.update({"retired_at": now, "public_key_pem": _public_pem()})
    if location.table == "secret_refs":
        values.update({"scope": "zqxw_w31_test", "encrypted_value": "zqxwCIPHERsentinel"})
    if location.key == "run_cadences.paused_by_id":           # ck: the three pause fields together
        values.update({"paused_at": now, "paused_until": now + timedelta(days=1), "paused_reason": "zqxw pause"})
    if location.key == "workflow_definitions.eval_regression_accepted_by":   # ck: an accepted regression is a fail
        values.update({"eval_regression_accepted": True, "eval_verdict": "fail", "eval_coverage": 0.5,
                       "eval_regression_reason": "zqxw accepted", "eval_regression_accepted_at": now,
                       "evaluated_at": now, "eval_gate_run_id": _Parent("ai_eval_gate_runs")})
    return values


async def _generic_rows(db: Any, seeder: _Seeder, people: SimpleNamespace, now: datetime) -> dict[str, Any]:
    """A direct row for the subject AND the bystander in every exported location no writer covered; a child row
    (chat messages) under each. Returns the singleton values to restore."""
    from app.models.postgres import Base

    restore: dict[str, dict[str, Any]] = {}
    for location in _exported():
        if location.key in _BY_WRITER:
            continue
        if location.table in _SINGLETONS:
            orm = Base.metadata.tables[location.table]
            where = [orm.c[name] == value for name, value in _SINGLETONS[location.table].items()]
            current = (await db.execute(select(orm).where(*where))).mappings().one()
            restore.setdefault(location.table, dict(current))
            await db.execute(orm.update().where(*where).values({location.column: people.subject.id}))
            continue
        for owner, person, marker in (("subject", people.subject, SUBJECT_ROW),
                                      ("bystander", people.bystander, BYSTANDER_ROW)):
            row = await seeder.row(db, location.table, owner=owner, marker=marker,
                                   values=_values_for(location, person, now))
            if owner == "bystander":
                seeder.bystander_rows.add((location.table, str(row[seeder.catalog.pks[location.table][0]])))
            for child in location.child_tables:
                fk = next(name for name, target in seeder.catalog.fks.get(child, {}).items()
                          if target == location.table)
                await seeder.row(db, child, owner=owner, marker=marker,
                                 values={fk: row[seeder.catalog.pks[location.table][0]]})
    await db.commit()
    return restore


async def _restore_singletons(db: Any, restore: dict[str, dict[str, Any]]) -> None:
    from app.models.postgres import Base

    for table, original in restore.items():
        orm = Base.metadata.tables[table]
        where = [orm.c[name] == value for name, value in _SINGLETONS[table].items()]
        await db.execute(orm.update().where(*where).values(original))


#: Writer tables whose new rows may be PARENTS of generic rows: removed after the generic rows.
_LATE = ("compliance_signing_keys", "secret_refs")


async def _cleanup(env: Any, world: SimpleNamespace) -> None:
    """Delete by id everything this module created — writer / export rows (jobs first), the generic rows (children
    first), the late writer tables — restore the singletons, then the three users. Idempotent."""
    from app.models.postgres import Base, User

    if world.cleaned:
        return
    async with env() as db:
        for table in [name for name in _WRITER_TABLES if name not in _LATE]:
            new = await _keys_of(db, table) - world.before_keys[table]
            if new:
                (key,) = list(Base.metadata.tables[table].primary_key.columns)
                await db.execute(delete(Base.metadata.tables[table]).where(key.in_(sorted(new, key=str))))
        await world.seeder.cleanup(db)
        for table in _LATE:
            new = await _keys_of(db, table) - world.before_keys[table]
            if new:
                (key,) = list(Base.metadata.tables[table].primary_key.columns)
                await db.execute(delete(Base.metadata.tables[table]).where(key.in_(sorted(new, key=str))))
        await _restore_singletons(db, world.restore)
        await db.execute(delete(User).where(User.id.in_(world.user_ids)))
        await db.commit()
    world.cleaned = True


@pytest_asyncio.fixture(loop_scope="module", scope="module")
async def world(env: Any, tmp_path_factory: pytest.TempPathFactory) -> Any:
    """The subject + the bystander in every exported location, once; everything removed at the end."""
    from app.db import storage as storage_module
    from app.models.postgres import Base

    now = datetime.now(timezone.utc)
    patch = pytest.MonkeyPatch()
    patch.setattr(storage_module.settings, "LOCAL_STORAGE_PATH", str(tmp_path_factory.mktemp("subject-objects")))
    patch.setattr("app.services.activity.live.stage", lambda db, row: None)   # the activity ROW is written; no Redis
    store = storage_module.LocalStorageProvider(default_bucket="artifacts")
    from app.db import mongo as mongo_module
    from app.db import redis_client

    state = SimpleNamespace(cleaned=True, store=store, restore={}, user_ids=[],
                            clients=(redis_client._client, mongo_module._client))
    try:
        async with env() as db:
            catalog = await _read_catalog(db)
            state.before_counts = {table: await _count(db, table) for table in Base.metadata.tables}
            state.before_keys = {table: await _keys_of(db, table) for table in _WRITER_TABLES}
            people = await _users(db, now)
            await db.commit()
            state.cleaned = False
            state.people = people
            state.user_ids = [people.subject.id, people.bystander.id, people.owner.id]
            state.seeder = _Seeder(catalog, now=now, users={
                "subject": people.subject.id, "bystander": people.bystander.id, "owner": people.owner.id})
            state.written = await _through_the_writers(db, people, state.seeder)
            state.restore = await _generic_rows(db, state.seeder, people, now)
        yield state
    finally:
        patch.undo()
        await _cleanup(env, state)


def _worker_pool() -> Any:
    """The reference K3s install worker's pool: 1 + overflow 1 (a bigger pool would hide a third connection)."""
    engine = create_async_engine(_dsn(), pool_size=1, max_overflow=1, pool_timeout=10)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    sessions.engine = engine  # type: ignore[attr-defined]
    return sessions


@pytest_asyncio.fixture(loop_scope="module", scope="module")
async def exported(env: Any, world: Any) -> Any:
    """ONE export of the subject through the REAL runner on the worker's 1 + 1 pool."""
    from app.models.postgres import SubjectRightsJob, User
    from app.services import compliance_signing
    from app.services import subject_rights_job_service as jobs
    from app.services.compliance_pack_service import COMPLIANCE_BUCKET
    from app.worker import subject_rights_runner as runner

    async with env() as db:
        subject = await db.get(User, world.people.subject.id)
        job = await jobs.create_export_job(db, subject=subject, actor=subject, source="self")
        await db.commit()
    small = _worker_pool()
    try:
        status = await runner.run_export_job(job.id, storage=world.store, session_factory=small)
    finally:
        await small.engine.dispose()
    async with env() as db:
        row = await db.get(SubjectRightsJob, job.id)
        keys = await compliance_signing.public_keys(db)
    archive = b""
    if row is not None and row.storage_key:
        archive = b"".join([chunk async for chunk in world.store.stream_object(row.storage_key,
                                                                                bucket=COMPLIANCE_BUCKET)])
    members: dict[str, bytes] = {}
    if archive:
        with zipfile.ZipFile(io.BytesIO(archive)) as zf:
            members = {name: zf.read(name) for name in zf.namelist()}
    return SimpleNamespace(status=status, row=row, keys=keys, archive=archive, members=members)


def _rows(members: dict[str, bytes], prefix: str = "") -> list[dict[str, Any]]:
    return [json.loads(line) for name, body in members.items()
            if name.endswith(".jsonl") and name.startswith(prefix) for line in body.decode("utf-8").splitlines()
            if line]


# ── 1. the archive ──────────────────────────────────────────────────────────

async def test_the_export_succeeds_signed_and_verified(world: Any, exported: Any) -> None:
    from app.services.compliance_pack_verify import verify_archive_file

    assert exported.status == "succeeded", exported.row.error if exported.row else None
    row = exported.row
    assert row.signature == "signed" and row.signing_key_id
    assert row.storage_key == f"subject-exports/{row.finished_at:%Y/%m/%d}/{row.id}.zip"
    assert str(world.people.subject.id) not in row.storage_key
    assert row.expires_at - row.finished_at == timedelta(hours=72)
    verified = verify_archive_file(io.BytesIO(exported.archive), recorded_manifest_sha256=row.manifest_sha256,
                                   public_keys=exported.keys)
    assert (verified["result"], verified["chain"], verified["signature"]) == ("verified", "intact", "valid")
    manifest = json.loads(exported.members["manifest.json"])
    assert manifest["scope"] == "subject" and manifest["project_id"] is None
    assert manifest["subject"] == {"id": str(world.people.subject.id), "username": SUBJECT}
    profile = json.loads(exported.members["profile.json"])
    assert profile["email"] == SUBJECT_EMAIL and profile["mfa"]["enabled"] is True


# ── 2. declaration is not emission ──────────────────────────────────────────

async def test_every_exported_location_has_at_least_one_row(world: Any, exported: Any) -> None:
    emitted: dict[str, int] = {}
    for row in _rows(exported.members):
        key = row.get("_location") or (f"{row['table']}.{row['column']}" if "table" in row else None)
        if key:
            emitted[key] = emitted.get(key, 0) + 1
    profile = json.loads(exported.members["profile.json"])
    if profile.get("id") == str(world.people.subject.id):
        emitted["users.*"] = 1
    if profile.get("mfa", {}).get("recovery_codes_left", 0) > 0:
        emitted["mfa_recovery_codes.user_id"] = profile["mfa"]["recovery_codes_left"]
    missing = sorted(location.key for location in _exported() if not emitted.get(location.key))
    assert missing == [], f"declared exported, never emitted: {missing}"
    categories = {location.category for location in _exported()} - {"profile"}
    assert all(exported.row.counts.get(category, 0) > 0 for category in categories), exported.row.counts
    assert exported.row.counts.get("chat_messages", 0) > 0


# ── 3. no secret, no hash, no other person ──────────────────────────────────

async def test_no_secret_hash_or_bystander_reaches_the_archive(world: Any, exported: Any) -> None:
    bodies = {name: body.decode("utf-8", "replace") for name, body in exported.members.items()}
    for secret in world.written["secrets"]:
        offenders = [name for name, body in bodies.items() if secret in body]
        assert offenders == [], f"a secret / digest reached {offenders}"
    for marker in (BYSTANDER_EMAIL, BYSTANDER_ROW, "yvkqcomment", "zqxwCIPHERsentinel"):
        offenders = [name for name, body in bodies.items() if marker in body]
        assert offenders == [], f"{marker!r} reached {offenders}"
    pointers = {(row["table"], str(row["row_id"])) for row in _rows(exported.members) if "row_id" in row}
    assert len(world.seeder.bystander_rows) > 80 and not pointers & world.seeder.bystander_rows
    comments = _rows(exported.members, "comments/")
    assert [row["id"] for row in comments] == [str(world.written["comment_id"])]
    assert str(world.written["bystander_comment_id"]) not in "".join(bodies.values())
    about_me = [row for row in _rows(exported.members, "audit_access/")
                if row["_location"] == "access_audit_logs.target_user_id"]
    assert about_me and all(row["after_value"].get("cc") == "[REDACTED_EMAIL]" for row in about_me
                            if row["action"] == "role_changed")
    by_me = [row for row in _rows(exported.members, "audit_identity/")
             if row["_location"] == "identity_events.actor_id" and row["event_type"] == "SCIM_USER_UPDATED"]
    assert by_me, _rows(exported.members, "audit_identity/")
    for row in by_me:      # a row the subject ACTED in: the other person's id only; a key named like a secret
        assert row["detail"] == {"email": "[REDACTED]"} and "user_id_display" not in row, row   # (is_secret_name)


# ── 4. migration 0253 on a throwaway database ───────────────────────────────

async def _alembic(dsn: str, direction: str, revision: str) -> None:
    def invoke() -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment["DATABASE_URL"] = dsn
        return subprocess.run([sys.executable, "-m", "alembic", "-c", "alembic.ini", direction, revision],
                              cwd=BACKEND_ROOT, env=environment, capture_output=True, text=True, check=False)

    result = await asyncio.to_thread(invoke)
    assert result.returncode == 0, f"alembic {direction} {revision} failed\n{result.stdout}\n{result.stderr[-4000:]}"


async def _shape(dsn: str) -> tuple[bool, bool, bool]:
    engine = create_async_engine(dsn, pool_size=1, max_overflow=0)
    try:
        async with engine.connect() as conn:
            table = await conn.scalar(text("SELECT to_regclass('subject_rights_jobs') IS NOT NULL"))
            column = await conn.scalar(text(
                "SELECT count(*) > 0 FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND table_name = 'users' AND column_name = 'erased_at'"))
            index = await conn.scalar(text("SELECT to_regclass('ix_users_erased_at') IS NOT NULL"))
            return bool(table), bool(column), bool(index)
    finally:
        await engine.dispose()


async def test_migration_0253_upgrades_downgrades_and_upgrades_again() -> None:
    import asyncpg
    from sqlalchemy.engine import make_url

    source = make_url(_dsn())
    name = f"w31_subject_{uuid.uuid4().hex[:12]}"
    connect = {"user": source.username, "password": source.password, "host": source.host or "localhost",
               "port": source.port or 5432, "database": "postgres"}
    admin = await asyncpg.connect(**connect)
    try:
        await admin.execute(f'CREATE DATABASE "{name}" TEMPLATE template0')
    finally:
        await admin.close()
    dsn = source.set(database=name).render_as_string(hide_password=False)
    try:
        await _alembic(dsn, "upgrade", "head")
        assert await _shape(dsn) == (True, True, True)
        await _alembic(dsn, "downgrade", "0252")
        assert await _shape(dsn) == (False, False, False)
        await _alembic(dsn, "upgrade", "head")
        assert await _shape(dsn) == (True, True, True)
    finally:
        admin = await asyncpg.connect(**connect)
        try:
            await admin.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                                "WHERE datname = $1 AND pid <> pg_backend_pid()", name)
            await admin.execute(f'DROP DATABASE IF EXISTS "{name}"')
        finally:
            await admin.close()


# ── 5. LAST: everything removed, every count back ───────────────────────────

async def test_zz_every_seeded_row_is_removed_and_every_count_is_back(env: Any, world: Any, exported: Any) -> None:
    from app.models.postgres import Base

    assert exported.row is not None
    await _cleanup(env, world)
    async with env() as db:
        after = {table: await _count(db, table) for table in Base.metadata.tables}
    moved = {table: (world.before_counts[table], after[table]) for table in after
             if after[table] != world.before_counts.get(table)}
    assert moved == {}, f"tables not back to their pre-test counts: {moved}"
    await world.store.delete_object(exported.row.storage_key, bucket="compliance-packs")
    from app.db import mongo as mongo_module
    from app.db import redis_client

    # the path bound no Redis / Mongo client (so this module closes none): the next CI module inherits nothing
    assert (redis_client._client, mongo_module._client) == world.clients
