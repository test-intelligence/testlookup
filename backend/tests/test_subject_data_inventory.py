"""W31 STRAT-12: every place that names a user is classified for the export and the erasure (contract §2.1).

The guard of ``services/subject_data_inventory.py``, against the REAL metadata
and source tree (pure: no database), modelled on
``tests/test_project_export_classification.py``:

1. every column with a FK to ``users.id`` is exactly ONE location, and no
   location names a column that does not exist;
2. every name-like column (the §2.1 pattern) is a location, ``NOT_A_PERSON``
   or ``COVERED_BY`` a location of its row;
3. every writer that copies ``<x>.username`` / ``.email`` / ``.full_name`` into
   a persisted target is pinned in ``WRITERS`` and mapped to a location;
4. ``AUDIT_TABLES`` is the protected gate's ``_AUDIT_TABLES`` and only those
   (and the Art. 17(3) legal records) are ``keep_audit``;
5. the vocabulary of every location;
6. the non-ORM table, the Mongo collections and the Chroma families;
7. the pinned counts (a deliberate edit when a location is added);
8. fail-open: the walks find what they must.

A failure NAMES the column: "<table.column> names a user: classify it in
subject_data_inventory.py (export as / erase / reason)".
"""
from __future__ import annotations

import ast
import collections
import importlib.util
import re
import sys
from pathlib import Path
from typing import Iterable

import pytest
from sqlalchemy import Column, ForeignKey, MetaData, String, Table
from sqlalchemy.dialects.postgresql import UUID

from app.db.mongo import Collections
from app.models.postgres import Base
from app.services import chroma_collections, pipeline_event_log, project_export_scrub
from app.services import subject_data_inventory as inv
from app.services import subject_rights_vocab as vocab

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
APP = BACKEND / "app"
VERSIONS = BACKEND / "migrations" / "versions"
INVENTORY_PY = APP / "services" / "subject_data_inventory.py"
#: §2.1's snapshot pattern: a non-FK column whose NAME may hold a person.
PATTERN = re.compile(
    r"_by$|actor|author|username|email|_name$|owner|assignee|reviewer|requester|approver|creator|recipient|"
    r"mention|avatar|^user_|_user_"
)
#: Never exported, whatever the location says (contract §2.1 check 5).
FORBIDDEN_EXPORT = {"key_hash", "token_hash", "jti_hash", "hashed_password", "code_hash", "encrypted_value", "token"}
#: §1.3's archive members (+ "profile" → profile.json).
MEMBERS = {
    "profile", "memberships", "sso_identities", "scim_groups", "sessions", "api_keys", "notification_preferences",
    "notifications", "digest_subscriptions", "saved_views", "ui_dismissals", "invitations", "chat_sessions",
    "chat_messages", "comments", "reviews", "feedback", "timeline_notes", "assignments", "activity", "usage",
    "audit_access", "audit_settings", "audit_test_management", "audit_identity", "references",
}
KEEP_NEEDS_REASON = {"keep_audit", "keep_record", "out_of_scope", "no_personal_data"}


def user_fk_columns(metadata: MetaData) -> set[str]:
    return {
        f"{name}.{column.name}"
        for name, table in metadata.tables.items() for column in table.columns
        for fk in column.foreign_keys if fk.column.table.name == "users" and fk.column.name == "id"
    }


def snapshot_columns(metadata: MetaData) -> set[str]:
    fks = user_fk_columns(metadata)
    return {
        f"{name}.{column.name}"
        for name, table in metadata.tables.items() for column in table.columns
        if PATTERN.search(column.name) and f"{name}.{column.name}" not in fks
    }


def _message(columns: Iterable[str]) -> str:
    return "\n".join(
        f"{column} names a user: classify it in subject_data_inventory.py (export as / erase / reason)"
        for column in sorted(columns)
    )


def unclassified_fks(metadata: MetaData, inventory: Iterable[inv.Location]) -> list[str]:
    keys = {location.key for location in inventory}
    return sorted(user_fk_columns(metadata) - keys)


def unclassified_snapshots(metadata: MetaData, inventory: Iterable[inv.Location]) -> list[str]:
    keys = {location.key for location in inventory}
    covered = keys | set(inv.NOT_A_PERSON) | set(inv.COVERED_BY)
    return sorted(
        column for column in snapshot_columns(metadata)
        if column not in covered and f"{column.split('.', 1)[0]}.*" not in keys
    )


def _copied_metadata() -> MetaData:
    copy = MetaData()
    for table in Base.metadata.sorted_tables:
        table.to_metadata(copy)
    return copy


# ── (1) complete over FKs ────────────────────────────────────────────────────

def test_1_every_user_fk_column_is_classified_exactly_once():
    missing = unclassified_fks(Base.metadata, inv.INVENTORY)
    assert not missing, _message(missing)
    keys = [location.key for location in inv.INVENTORY]
    duplicates = [key for key, n in collections.Counter(keys).items() if n > 1]
    assert not duplicates, f"a location is listed twice: {duplicates}"


def test_1_a_new_fk_to_users_is_red_and_named():
    """Mutation: a fake FK column to users.id on a COPIED metadata."""
    metadata = _copied_metadata()
    Table("zz_new_feature", metadata, Column("id", UUID(as_uuid=True), primary_key=True),
          Column("owner_id", UUID(as_uuid=True), ForeignKey("users.id")))
    missing = unclassified_fks(metadata, inv.INVENTORY)
    assert missing == ["zz_new_feature.owner_id"]
    message = _message(missing)
    assert "zz_new_feature.owner_id" in message and "classify it in subject_data_inventory.py" in message


def test_1_a_dropped_location_is_red_and_named():
    """Mutation: drop one location from the inventory."""
    reduced = [location for location in inv.INVENTORY if location.key != "chat_sessions.user_id"]
    assert unclassified_fks(Base.metadata, reduced) == ["chat_sessions.user_id"]


def test_1_no_location_names_a_column_that_does_not_exist():
    tables = Base.metadata.tables
    for location in inv.INVENTORY:
        if location.store != "postgres":
            assert location.table is None and location.column is None, location.key
            continue
        assert location.table is not None, location.key
        if location.table in inv.NON_ORM_TABLES:
            assert location.key == f"{location.table}.{location.column}"
            continue
        assert location.table in tables, f"{location.key}: no table {location.table}"
        table = tables[location.table]
        if location.column is None:
            assert location.key == f"{location.table}.*", location.key
        else:
            assert location.key == f"{location.table}.{location.column}", location.key
            assert location.column in table.c, f"{location.key}: no such column"
        for name in location.drop + location.columns:
            assert name in table.c, f"{location.key}: drop / columns names {name}, not a column of {table.name}"
        for child in location.child_tables:
            assert child in tables, f"{location.key}: no child table {child}"


# ── (2) complete over name-like (snapshot) columns ───────────────────────────

def test_2_every_name_like_column_is_a_location_or_not_a_person():
    missing = unclassified_snapshots(Base.metadata, inv.INVENTORY)
    assert not missing, _message(missing)


def test_2_a_new_name_snapshot_column_is_red_and_named():
    """Mutation: a ``foo_by_name`` column on a COPIED table."""
    metadata = _copied_metadata()
    metadata.tables["releases"].append_column(Column("signed_off_by_name", String(255)))
    assert unclassified_snapshots(metadata, inv.INVENTORY) == ["releases.signed_off_by_name"]


def test_2_the_exemptions_are_real_columns_with_reasons():
    tables = Base.metadata.tables
    snapshots = snapshot_columns(Base.metadata)
    for column, reason in {**inv.NOT_A_PERSON, **inv.COVERED_BY}.items():
        table, name = column.split(".", 1)
        assert table in tables and name in tables[table].c, f"{column}: not a column (a stale exemption)"
        assert column in snapshots, f"{column}: no longer name-like — drop the stale exemption"
        assert reason.strip(), f"{column}: an exemption needs a reason"
    assert not set(inv.NOT_A_PERSON) & set(inv.COVERED_BY)
    keys = {location.key: location for location in inv.INVENTORY}
    for column, key in inv.COVERED_BY.items():
        assert key in keys, f"{column}: covered by {key}, which is not a location"
        assert keys[key].table == column.split(".", 1)[0], f"{column}: covered by a location of another table"
    assert not set(inv.NOT_A_PERSON) & set(keys), "a column cannot be both a location and NOT_A_PERSON"


# ── (3) writers that copy a username / e-mail / full name into a persisted target ──

PERSON = {"username", "email", "full_name"}
LOG_METHODS = {"debug", "info", "warning", "warn", "error", "exception", "critical", "msg", "log"}
STRING_METHODS = {"lower", "strip", "upper", "casefold"}
CLIPPERS = {"clip", "truncate", "_clip", "_truncate"}


def _models() -> set[str]:
    return {mapper.class_.__name__ for mapper in Base.registry.mappers}


def _snapshot_names() -> set[str]:
    return {column.name for table in Base.metadata.tables.values() for column in table.columns
            if PATTERN.search(column.name)} - PERSON


class _Reads:
    """Does an expression's VALUE carry a person's username / e-mail / full name?"""

    def __init__(self, helpers: set[str]) -> None:
        self.helpers = helpers

    def __call__(self, node: ast.AST | None, tainted: frozenset[str] = frozenset()) -> bool:
        if node is None:
            return False
        if isinstance(node, ast.Attribute):
            return node.attr in PERSON
        if isinstance(node, ast.Name):
            return node.id in tainted
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                if func.id == "getattr" and len(node.args) >= 2:
                    return isinstance(node.args[1], ast.Constant) and node.args[1].value in PERSON
                if func.id == "str" or func.id in CLIPPERS:
                    return bool(node.args) and self(node.args[0], tainted)
                return func.id in self.helpers
            if isinstance(func, ast.Attribute) and func.attr in STRING_METHODS:
                return self(func.value, tainted)
            if isinstance(func, ast.Attribute) and func.attr == "format":
                return any(self(a, tainted) for a in node.args) or any(self(k.value, tainted) for k in node.keywords)
            return False
        if isinstance(node, ast.BoolOp):
            return any(self(v, tainted) for v in node.values)
        if isinstance(node, ast.Subscript):
            return self(node.value, tainted)
        if isinstance(node, ast.IfExp):
            return self(node.body, tainted) or self(node.orelse, tainted)
        if isinstance(node, ast.BinOp):
            return self(node.left, tainted) or self(node.right, tainted)
        if isinstance(node, ast.JoinedStr):
            return any(isinstance(v, ast.FormattedValue) and self(v.value, tainted) for v in node.values)
        return False


def _tainted(fn: ast.AST, reads: _Reads) -> frozenset[str]:
    names: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign) and reads(node.value, frozenset(names)):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
    return frozenset(names)


def scan_source(source: str, *, models: set[str], snapshot_names: set[str]) -> set[str]:
    """The persisted person-copies of ONE module: ``Model.kw`` (a model constructor), ``kw:<name>`` (a
    snapshot-named keyword of any non-log call), ``attr:<name>`` (an assignment to a snapshot-named
    attribute), ``dict:<key>`` (a snapshot-named key of a dict literal). Read-time resolvers are skipped."""
    tree = ast.parse(source)
    functions = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    helpers: set[str] = set()
    reads = _Reads(helpers)
    for fn in functions:   # this module's person-returning helpers (``_display_name(user)``)
        tainted = _tainted(fn, reads)
        if any(isinstance(n, ast.Return) and reads(n.value, tainted) for n in ast.walk(fn)):
            helpers.add(fn.name)
    skipped = {id(n) for fn in functions if fn.name in inv.READ_RESOLVERS for n in ast.walk(fn)}
    found: set[str] = set()
    for scope in [*functions, tree]:
        tainted = _tainted(scope, reads)
        for node in ast.walk(scope):
            if id(node) in skipped:
                continue
            if isinstance(node, ast.Call) and not (isinstance(node.func, ast.Attribute) and node.func.attr in LOG_METHODS):
                name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
                for kw in node.keywords:
                    if kw.arg and reads(kw.value, tainted):
                        if name in models:
                            found.add(f"{name}.{kw.arg}")
                        elif kw.arg in snapshot_names:
                            found.add(f"kw:{kw.arg}")
            if isinstance(node, ast.Assign) and reads(node.value, tainted):
                found.update(f"attr:{t.attr}" for t in node.targets
                             if isinstance(t, ast.Attribute) and (t.attr in snapshot_names or t.attr in PERSON))
            if isinstance(node, ast.Dict):
                for key, value in zip(node.keys, node.values):
                    if (isinstance(key, ast.Constant) and key.value in snapshot_names and value is not None
                            and reads(value, tainted)):
                        found.add(f"dict:{key.value}")
    return found


def scan_app() -> set[tuple[str, str]]:
    models, names = _models(), _snapshot_names()
    found: set[tuple[str, str]] = set()
    for path in sorted(APP.rglob("*.py")):
        relative = path.relative_to(BACKEND).as_posix()
        for target in scan_source(path.read_text(encoding="utf-8"), models=models, snapshot_names=names):
            found.add((relative, target))
    return found


def test_3_every_person_copying_writer_is_pinned_and_classified():
    found = scan_app()
    pinned = set(inv.WRITERS)
    new = sorted(found - pinned)
    assert not new, "a NEW writer copies a username / e-mail / full name into a persisted target — classify " \
                    "where it lands in subject_data_inventory.py (a location, or WRITERS):\n" + \
                    "\n".join(f"  {path}: {target}" for path, target in new)
    stale = sorted(pinned - found)
    assert not stale, f"WRITERS pins writers the scan no longer finds (drop them deliberately): {stale}"
    keys = {location.key for location in inv.INVENTORY}
    for pair, target in inv.WRITERS.items():
        assert target in keys or target.startswith(inv.TRANSIENT), f"{pair} → {target}: not a location"


def test_3_the_scan_sees_each_writer_shape():
    """Mutation: synthetic writers of every shape are found; a log call and a resolver are not."""
    source = (
        "def _display_name(user):\n    return user.full_name or user.username\n"
        "def a(db, user):\n    db.add(ReportShareLink(created_by_name=user.username))\n"
        "def b(row, user):\n    row.dismissed_by_name = _display_name(user)\n"
        "def c(svc, user):\n    actor = getattr(user, 'email', None)\n    svc.request_cancel(cancelled_by=actor)\n"
        "def d(user):\n    return {'actor_name': (user.full_name or '').strip()}\n"
        "def e(log, user):\n    log.info('x', actor_name=user.username)\n"
        "def requester_names(user):\n    return {'actor_name': user.username}\n"
    )
    found = scan_source(source, models=_models(), snapshot_names=_snapshot_names())
    assert found == {"ReportShareLink.created_by_name", "attr:dismissed_by_name", "kw:cancelled_by", "dict:actor_name"}


# ── (4) audit tables ─────────────────────────────────────────────────────────

def _quality_gate():
    """``scripts/quality_gate.py``, loaded read-only (the test_architectural_audit_write_discipline.py pattern)."""
    path = REPO / "scripts" / "quality_gate.py"
    if not path.exists():
        pytest.skip("scripts/quality_gate.py not present")
    spec = importlib.util.spec_from_file_location("quality_gate_subject_inventory", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_4_audit_tables_are_the_gates_and_are_kept():
    assert set(inv.AUDIT_TABLES) == set(_quality_gate()._AUDIT_TABLES)
    assert len(inv.AUDIT_TABLES) == len(set(inv.AUDIT_TABLES))
    for location in inv.INVENTORY:
        if location.table in inv.AUDIT_TABLES:
            assert location.erase == "keep_audit", f"{location.key}: an audit table is never written (the gate)"


def test_4_keep_audit_outside_the_gate_is_an_art_17_3_record():
    for location in inv.INVENTORY:
        if location.erase != "keep_audit" or location.table in inv.AUDIT_TABLES:
            continue
        assert "Art. 17(3)" in location.reason, f"{location.key}: keep_audit needs its Art. 17(3) reason"
        if location.store == "postgres":
            assert location.table in inv.ART17_KEEP_TABLES, location.key
        else:
            assert location.store == "objects", location.key
            assert location.key.removeprefix("objects:") in inv.ART17_KEEP_PREFIXES, location.key


# ── (5) vocabulary ───────────────────────────────────────────────────────────

def test_5_every_location_speaks_the_vocabulary():
    assert set(inv.CATEGORIES) == MEMBERS and inv.PROFILE_CATEGORY in inv.CATEGORIES
    for location in inv.INVENTORY:
        key = location.key
        assert location.store in inv.STORES, key
        assert location.match in inv.MATCHES, key
        assert location.export_as in vocab.EXPORT_AS, key
        assert location.erase in vocab.ERASE_ACTIONS, key
        assert location.label.strip(), f"{key}: the preview needs a label"
        if location.erase in KEEP_NEEDS_REASON:
            assert location.reason.strip(), f"{key}: {location.erase} needs a reason"
        assert (location.category is not None) == (location.export_as != "none"), \
            f"{key}: a category iff the location is exported"
        if location.category is not None:
            assert location.category in inv.CATEGORIES, f"{key}: {location.category} is not an archive member"
        for child in location.child_tables:
            assert child in inv.CATEGORIES, f"{key}: child {child} has no archive member"
        if location.export_as == "metadata":
            assert location.columns, f"{key}: a metadata export lists its columns"
        exported = set(location.columns) | set(location.extra)
        for name in exported:
            assert name not in FORBIDDEN_EXPORT and not project_export_scrub.is_secret_name(name), \
                f"{key}: {name} would export a secret"
        if location.open_item:
            assert location.store == "postgres" and location.erase == "reference", key
        if location.store != "postgres":
            assert location.export_as == "none", f"{key}: only Postgres rows are exported"


def test_5_a_full_export_drops_every_hash_column_of_its_table():
    tables = Base.metadata.tables
    for location in inv.INVENTORY:
        if location.export_as != "full" or location.table not in tables:
            continue
        hashes = {c.name for c in tables[location.table].columns} & FORBIDDEN_EXPORT
        assert hashes <= set(location.drop), f"{location.key}: a full export must drop {sorted(hashes)}"


# ── (6) the non-ORM table and the other stores ───────────────────────────────

_RAW_CREATE = re.compile(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?\"?(\w+)\"?\s*\((.*?)\)\s*[;\"']", re.I | re.S)


def test_6_the_only_non_orm_table_that_names_a_user_is_classified():
    assert set(inv.NON_ORM_TABLES) == {"auth_token_revocations"}
    raw: set[str] = set()
    for path in VERSIONS.glob("*.py"):
        for name, body in _RAW_CREATE.findall(path.read_text(encoding="utf-8")):
            if re.search(r"\buser_id\b", body):
                raw.add(name.lower())
    outside = raw - set(Base.metadata.tables)
    assert outside == set(inv.NON_ORM_TABLES), f"a raw-SQL table names a user and is not classified: {outside}"
    assert "auth_token_revocations" not in Base.metadata.tables
    assert any(location.table == "auth_token_revocations" for location in inv.INVENTORY)


def test_6_the_mongo_and_chroma_locations_cover_every_collection():
    collections_ = {
        value for name, value in vars(Collections).items() if name.isupper() and isinstance(value, str)
    } | {pipeline_event_log._COLLECTION}
    mongo = {location.key.removeprefix("mongo:") for location in inv.INVENTORY if location.store == "mongo"}
    assert mongo == collections_
    families = chroma_collections.COLLECTION_FAMILIES
    assert len(families) == 7, "a new Chroma family: re-read it for personal data and update the chroma location"
    (chroma,) = [location for location in inv.INVENTORY if location.store == "chroma"]
    assert chroma.key == "chroma:*" and str(len(families)) in chroma.reason


# ── (7) the pinned counts (a deliberate edit when a location is added) ───────

def _pg_fk(location: inv.Location) -> bool:
    return location.store == "postgres" and location.key in user_fk_columns(Base.metadata)


def test_7_pinned_counts():
    locations = inv.INVENTORY
    by_store = collections.Counter(location.store for location in locations)
    fk = sum(1 for location in locations if _pg_fk(location))
    counts = (len(locations), fk, by_store["postgres"] - fk, by_store["objects"], by_store["mongo"],
              by_store["redis"], by_store["chroma"], by_store["logs"])
    # W31 (deliberate, against the contract's 167 / 121 / 21 / 8 / 10 / 5 / 1 / 1): + subject_rights_jobs' two FKs;
    # + webhook_deliveries.event_payload, notification_logs.title / .body, service_ownership_rules.team_contact;
    # + redis ml:retrain:last / :lock, testlookup:dedup:notify_owner:* (found by the writer scan and its traces)
    # W36 STRAT-03 (deliberate): + systemic_cluster_epics.created_by, a reference (176 -> 177, FKs 123 -> 124)
    assert counts == (177, 124, 25, 8, 10, 8, 1, 1), (
        f"(locations, postgres_fk, postgres_other, objects, mongo, redis, chroma, logs) = {counts}: a location was "
        "added or moved — classify it in subject_data_inventory.py and update this pin deliberately"
    )
    erase = collections.Counter(location.erase for location in locations)
    assert (erase["delete"], erase["pseudonymise"] + erase["reference"], erase["keep_audit"], erase["keep_record"],
            erase["no_personal_data"], erase["out_of_scope"]) == (21, 106, 24, 7, 13, 6), erase
    assert (erase["pseudonymise"], erase["reference"]) == (11, 95), erase
    exported = sum(1 for location in locations if location.export_as != "none")
    assert (exported, len(locations) - exported) == (123, 54)


# ── (8) fail-open, and the module stays pure data ────────────────────────────

def test_8_the_walks_find_what_they_must():
    assert len(user_fk_columns(Base.metadata)) >= 100
    assert len(snapshot_columns(Base.metadata)) >= 80
    users = Base.metadata.tables["users"]
    assert "username" in users.c and "email" in users.c
    assert len(scan_app()) >= 40, "the writer scan found almost nothing: it is blind, not clean"


def test_8_lookups_and_purity():
    assert inv.by_key("users.*").erase == "pseudonymise"
    with pytest.raises(KeyError):
        inv.by_key("users.zqxw")
    deletes = inv.postgres_locations(erase="delete")
    assert deletes and all(location.store == "postgres" and location.erase == "delete" for location in deletes)
    exported = inv.postgres_locations(export=True)
    assert all(location.export_as != "none" for location in exported) and len(exported) == 123
    tree = ast.parse(INVENTORY_PY.read_text(encoding="utf-8"))
    modules = [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
    modules += [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    assert set(modules) <= {"__future__", "dataclasses", "typing"}, modules
