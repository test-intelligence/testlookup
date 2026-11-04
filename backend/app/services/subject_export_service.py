"""One person's data as ONE signed, verified ZIP (W31 STRAT-12, Art. 15 / 20; contract §1.3, §1.4, §2.2).

* :func:`iter_members` walks ``subject_data_inventory.INVENTORY`` on the caller's session:
  ``README.txt``, ``profile.json``, then — category by category, in the order the
  inventory first names them — every location with ``export_as != "none"``: ONE
  streamed ``SELECT`` per location (``yield_per(CURSOR_BATCH_ROWS)``, ordered by the
  primary key, filtered by the subject's id on the location's column — never a Python
  id list) → the §1.3 projection → JSONL parts of ≤ ``PART_ROWS_MAX`` rows / ≤
  ``PART_BYTES_MAX`` bytes, one ``build_archive`` member each (peak memory = one part).
* Projections: ``full`` = every column but the location's ``drop`` set and the hash
  columns, a secret-named column / JSON key → ``[REDACTED]``, the two webhook URLs →
  ``[REDACTED]`` when set; ``metadata`` = the listed columns only; ``reference`` =
  ``{table, column, row_id, project_id, label}``. Another person appears as an id, with
  ``<column>_display`` (full name or username, never an e-mail) only where the subject's
  own row names them; in the audit trails, a row the subject ACTED in carries the other
  person's id only, and an e-mail address that is not the subject's own is replaced.
* :func:`run_subject_export` — ``compliance_pack_service.build_archive`` (manifest LAST,
  the subject branch) into a spool → ``_sign_pack`` → the object store
  (``subject-exports/YYYY/MM/DD/<job_id>.zip``) → ``on_written(key)`` → RE-READ + verify.
* :func:`sweep` (the janitor's body) and :func:`remove_exports_of` (G-C's object phase).

Stage-only (``STAGE_ONLY_SERVICES``): no commit here. Logs carry job / user ids, location
keys, categories, counts, byte totals, codes and error TYPE names — never a row value, a
name, a username, an e-mail, a storage key, a digest or a secret.
"""
from __future__ import annotations

import asyncio
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, AsyncGenerator, Awaitable, Callable, Mapping, Optional

import structlog
from sqlalchemy import Table, and_, func, or_, select, update

from app.services.project_export_scrub import is_secret_name
from app.services.project_export_service import encode_row
from app.services.subject_data_inventory import INVENTORY, PROFILE_CATEGORY, Location
from app.services.subject_rights_vocab import (
    CURSOR_BATCH_ROWS,
    ERASED_DISPLAY,
    ERROR_SENTENCES,
    EXPORT_FORMAT,
    EXPORT_MAX_BYTES,
    JOB_STALE_MINUTES,
    PART_BYTES_MAX,
    PART_ROWS_MAX,
    SCHEMA_VERSION,
)

_slog = structlog.get_logger(__name__)

README_MEMBER = "README.txt"
PROFILE_MEMBER = "profile.json"
REDACTED = "[REDACTED]"
EMAIL_REDACTED = "[REDACTED_EMAIL]"
#: The key every ``full`` / ``metadata`` row carries: the inventory location it came from.
LOCATION_KEY = "_location"
SWEEP_LIMIT = 200

#: Columns whose VALUE is a credential digest or ciphertext: never selected, whatever a location's ``drop`` says.
HASH_COLUMNS = frozenset({
    "key_hash", "jti_hash", "token_hash", "hashed_password", "code_hash", "encrypted_value", "token",
    "request_ip_hash",
})
#: Columns whose value IS a credential (an incoming-webhook URL): ``[REDACTED]`` when set.
WEBHOOK_URL_COLUMNS: Mapping[str, frozenset[str]] = {
    "notification_preferences": frozenset({"slack_webhook_url", "teams_webhook_url"}),
}
#: ``Location.extra`` → (the row's FK column, the joined table, the joined column) — one LEFT JOIN each.
EXTRA_JOINS: Mapping[str, tuple[str, str, str]] = {
    "sso_display_name": ("sso_config_id", "sso_configurations", "display_name"),
    "group_display_name": ("group_id", "scim_groups", "display_name"),
    "project_name": ("project_id", "projects", "name"),
    "test_case_title": ("test_case_id", "managed_test_cases", "title"),
}
#: The five audit trails (``subject_data_inventory.AUDIT_TABLES``) that are exported: the ACTOR's id column and
#: its stored name snapshot. A row the subject did not act in shows the actor as ``{id, display}``.
AUDIT_ACTORS: Mapping[str, tuple[str, Optional[str]]] = {
    "access_audit_logs": ("actor_user_id", "actor_name"),
    "identity_events": ("actor_id", "actor_name"),
    "settings_audit_log": ("actor_id", "actor_name"),
    "test_case_audit_logs": ("actor_id", "actor_name"),
    "project_activity_events": ("actor_id", "actor_name"),
}
_EMAIL = re.compile(r"[A-Za-z0-9._%+'-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")


class SubjectExportFailed(Exception):
    """An export that must end ``failed``: ``code`` is an ``ERROR_SENTENCES`` key, ``params`` its fields
    (type names and reasons only — never a row value)."""

    def __init__(self, code: str, **params: Any) -> None:
        self.code = code
        self.params = params
        self.message = ERROR_SENTENCES[code].format(**params) if params else ERROR_SENTENCES[code]
        super().__init__(self.message)


class ExportObjectsRemaining(Exception):
    """:func:`remove_exports_of` could not remove every stored export of the subject (storage refused):
    the caller's phase is not done and runs again."""

    def __init__(self, remaining: int) -> None:
        super().__init__(f"{remaining} subject export object(s) not removed")
        self.remaining = remaining


@dataclass(frozen=True)
class SubjectExportOutcome:
    """What a verified export produced. ``key`` is for the job row ONLY (never returned, never logged)."""

    key: str
    bytes: int
    file_count: int
    manifest_sha256: str
    archive_sha256: str
    signature: str                       # "signed" | "unsigned"
    signing_key_id: Optional[str]
    counts: dict[str, int] = field(default_factory=dict)
    locations: dict[str, int] = field(default_factory=dict)   # rows per exported inventory location (additive)


# ── the README (§1.3; the retention paragraph is the user guide's, §4.5) ─────────

#: §4.5's retention-basis paragraph, verbatim (the user guide carries the same text).
RETENTION_PARAGRAPH = (
    "Audit records keep the actor's name after an erasure. TestLookup's five audit trails (access changes, "
    "sign-in and provisioning events, configuration changes, test-content changes, and the activity ledger), "
    "release-override trails, legal holds, deletion records, and signed compliance packs are kept under "
    "Art. 17(3)(b) and (e) GDPR — compliance with a legal obligation and the establishment, exercise or "
    "defence of legal claims — for as long as their own retention clock allows (the audit clock of each "
    "project's retention policy; the configuration log and sign-in events have no clock). Within the product, "
    "an erased person is shown as \"Erased user\"; the stored name is visible only on the audit surfaces, to "
    "administrators."
)

README_TEXT = "\n".join((
    "TestLookup personal data export",
    "",
    "This ZIP holds a copy of everything TestLookup holds about ONE person (GDPR Art. 15 access and",
    "Art. 20 portability): their profile, memberships, preferences, sessions and key details, sign-in",
    "links, their comments and reviews, chat history, assignments, and the activity and audit records",
    "where they are the actor (or, for access changes and sign-in events, the person concerned).",
    "",
    "Members:",
    "- profile.json: the account (id, username, e-mail, name, role, status, sign-in facts, two-factor",
    "  status and the number of unused recovery codes).",
    "- <category>/part-NNNN.jsonl: one JSON object per line (sorted keys, UTF-8). Each row names the",
    "  place it came from in `_location` (table.column); a reference row is {table, column, row_id,",
    "  project_id, label}: a record of a project that points at this person, without its content.",
    "  Categories: memberships, sso_identities, scim_groups, sessions, api_keys, notification_preferences,",
    "  notifications, digest_subscriptions, saved_views, ui_dismissals, invitations, chat_sessions,",
    "  chat_messages, comments, reviews, feedback, timeline_notes, assignments, activity, usage,",
    "  audit_access, audit_settings, audit_test_management, audit_identity, references.",
    "- manifest.json: every file's SHA-256 (the checksum chain root); signature.json: its signature.",
    "",
    "Never included: a password, key, token or recovery-code hash, a two-factor secret, a stored secret,",
    "or a webhook URL (shown as [REDACTED]). Other people appear only as an id, with a display name",
    "where this person's own record names them, never with an e-mail address. Text this person wrote is",
    "exported as written.",
    "",
    "What is kept after an erasure, and why:",
    RETENTION_PARAGRAPH,
    "",
    "Verification (tamper-evident checksum chain + Ed25519 signature):",
    "1. For each file listed in manifest.json `files`, compute its SHA-256 and compare it with the",
    "   manifest. No other file may be in the ZIP except signature.json.",
    "2. signature.json: its `manifest_sha256` must equal the SHA-256 of manifest.json. Verify",
    "   `signature` (base64 Ed25519) over the UTF-8 statement",
    "   `testlookup-compliance-pack-v1\\n<pack_id>\\n<manifest_sha256>\\n<key_id>\\n` with the exporting",
    "   deployment's public key for `key_id` (GET /api/v1/compliance-packs/signing-keys).",
    "3. `testlookup compliance-pack verify <zip> --public-key <pem>` does all of it offline.",
    "An archive without signature.json is tamper-evident only.",
    "",
))


# ── projections (§1.3) ───────────────────────────────────────────────────────────

_JSON_DEPTH_MAX = 20


def _table(name: str) -> Table:
    from app.models.postgres import Base

    return Base.metadata.tables[name]


def user_fk_columns(table_name: str) -> tuple[str, ...]:
    """The columns of ``table_name`` with a FK to ``users.id``."""
    return tuple(
        column.name for column in _table(table_name).columns
        if any(fk.column.table.name == "users" for fk in column.foreign_keys)
    )


def exported_columns(location: Location, table_name: Optional[str] = None) -> tuple[str, ...]:
    """The columns ONE location selects from ``table_name`` (default: its own table; a ``child_tables`` entry is
    read ``full``): ``metadata`` → its listed columns only; ``reference`` → the primary key, the location's column
    and ``project_id``; ``full`` → every column but the location's ``drop`` set and ``HASH_COLUMNS``."""
    name = table_name or location.table or ""
    table = _table(name)
    if location.export_as == "metadata" and name == location.table:
        return tuple(column for column in location.columns if column not in HASH_COLUMNS)
    if location.export_as == "reference" and name == location.table:
        names = [column.name for column in table.primary_key.columns]
        for extra in (location.column, "project_id"):
            if extra and extra in table.c and extra not in names:
                names.append(extra)
        return tuple(names)
    dropped = set(HASH_COLUMNS) | (set(location.drop) if name == location.table else set())
    return tuple(column.name for column in table.columns if column.name not in dropped)


def member_name(category: str, number: int) -> str:
    return f"{category}/part-{number:04d}.jsonl"


def redact_secret_keys(value: Any, depth: int = 1) -> Any:
    """A JSON value with the value of every secret-named key (``project_export_scrub.is_secret_name``) →
    ``[REDACTED]``; lists walked in full; a container nested deeper than 20 → ``[REDACTED]``. Keys are kept;
    text is NOT rewritten (the person's own words are exported as written)."""
    if isinstance(value, (dict, list, tuple)) and depth > _JSON_DEPTH_MAX:
        return REDACTED
    if isinstance(value, dict):
        return {
            key: REDACTED if item is not None and is_secret_name(key) else redact_secret_keys(item, depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_secret_keys(item, depth + 1) for item in value]
    return value


def redact_foreign_emails(value: Any, own: frozenset[str], depth: int = 1) -> Any:
    """Every e-mail address in ``value`` (a string, or the strings of a JSON value) that is not one of ``own``
    (lower-cased) → ``[REDACTED_EMAIL]``. Used on the audit trails, whose JSON is written by the system."""
    if isinstance(value, str):
        return _EMAIL.sub(lambda match: match.group(0) if match.group(0).lower() in own else EMAIL_REDACTED, value)
    if isinstance(value, (dict, list, tuple)) and depth > _JSON_DEPTH_MAX:
        return REDACTED
    if isinstance(value, dict):
        return {key: redact_foreign_emails(item, own, depth + 1) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_foreign_emails(item, own, depth + 1) for item in value]
    return value


def display_of(full_name: Optional[str], username: Optional[str], erased_at: Any) -> Optional[str]:
    """Another person's display: full name, else username — "Erased user" once erased; never an e-mail
    address (an e-mail-shaped name is replaced)."""
    if erased_at is not None:
        return ERASED_DISPLAY
    value = full_name or username
    return _EMAIL.sub(EMAIL_REDACTED, value) if value else None


def reference_row(location: Location, raw: Mapping[str, Any]) -> dict[str, Any]:
    """``{table, column, row_id, project_id, label}`` — a project's record that points at the person."""
    table = _table(location.table or "")
    keys = [column.name for column in table.primary_key.columns]
    row_id: Any = raw.get(keys[0]) if len(keys) == 1 else {name: raw.get(name) for name in keys}
    project_id = raw.get("id") if location.table == "projects" else raw.get("project_id")
    return {"table": location.table, "column": location.column, "row_id": row_id, "project_id": project_id,
            "label": location.label}


@dataclass(frozen=True)
class SubjectContext:
    """What a projection needs of the subject: the id, and their own e-mail addresses (lower-cased)."""

    id: uuid.UUID
    own_emails: frozenset[str] = frozenset()


def _project_audit(row: dict[str, Any], table_name: str, subject: SubjectContext,
                   displays: Mapping[Any, Optional[str]]) -> None:
    """An audit-trail row: the actor shown as ``{id, display}`` when it is NOT the subject (the subject is the
    person the row is about) — its stored name snapshot replaced by that display; a row the subject acted in
    keeps every other person as an id only; e-mail addresses other than the subject's own → replaced."""
    actor_column, name_column = AUDIT_ACTORS[table_name]
    actor = row.get(actor_column)
    if actor is not None and actor != subject.id:
        row[f"{actor_column}_display"] = displays.get(actor)
        if name_column and row.get(name_column) is not None:
            row[name_column] = displays.get(actor)
    for column, value in list(row.items()):
        if isinstance(value, (str, dict, list)):
            row[column] = redact_foreign_emails(value, subject.own_emails)


def project_row(location: Location, raw: Mapping[str, Any], *, subject: SubjectContext,
                displays: Optional[Mapping[Any, Optional[str]]] = None,
                table_name: Optional[str] = None) -> dict[str, Any]:
    """ONE exported row of ``location`` (or of its child table ``table_name``), §1.3: ``reference`` → the pointer;
    ``full`` / ``metadata`` → the selected columns with secret-named values and webhook URLs → ``[REDACTED]``,
    JSON walked for secret-named keys, another person's id given ``<column>_display`` (full rows), the audit
    rule (:func:`_project_audit`), and ``_location``. Returns a NEW dict."""
    name = table_name or location.table or ""
    if location.export_as == "reference" and name == location.table:
        return reference_row(location, raw)
    names = displays or {}
    row = dict(raw)
    webhooks = WEBHOOK_URL_COLUMNS.get(name, frozenset())
    for column, value in list(row.items()):
        if value is None or column in location.extra:
            continue
        if column in webhooks or (is_secret_name(column) and not isinstance(value, bool)):
            row[column] = REDACTED
        elif isinstance(value, (dict, list, tuple)):
            row[column] = redact_secret_keys(value)
    if location.export_as == "full" or name != location.table:
        if name in AUDIT_ACTORS:
            _project_audit(row, name, subject, names)
        else:
            for column in user_fk_columns(name):
                other = row.get(column)
                if other is not None and other != subject.id:
                    row[f"{column}_display"] = names.get(other)
    row[LOCATION_KEY] = location.key
    return row


# ── statements ───────────────────────────────────────────────────────────────────

def location_filter(location: Location, table: Table, *, subject_id: uuid.UUID, email: Optional[str]) -> Any:
    """The WHERE of one exported location: ``column = :uid`` (fk); the invitations addressed to the person
    (``lower(email) = lower(:email)`` and not accepted). Any other exported match raises — a location declared
    exported must have a filter (declaration is not emission)."""
    if location.match == "fk" and location.column:
        return table.c[location.column] == subject_id
    if location.key == "user_invitations.email":
        if not email:
            return table.c.id.is_(None)          # matches nothing
        return and_(func.lower(table.c.email) == email.lower(), table.c.accepted_user_id.is_(None))
    raise ValueError(f"{location.key}: no export filter for match {location.match!r}")


def export_plan() -> list[tuple[str, list[list[Location]]]]:
    """``[(category, [unit, …])]``: the member categories in the order the inventory first names them; a unit
    is the locations ONE statement reads — the ``full`` / ``metadata`` fk locations of one table in one category
    share it (access changes by AND to the person: one row once). ``profile`` is ``profile.json``, not here."""
    plan: dict[str, list[list[Location]]] = {}
    for location in INVENTORY:
        if location.export_as == "none" or location.category in (None, PROFILE_CATEGORY):
            continue
        units = plan.setdefault(str(location.category), [])
        shared = None
        if location.export_as in ("full", "metadata") and location.match == "fk":
            shared = next((unit for unit in units if unit[0].table == location.table
                           and unit[0].export_as == location.export_as and unit[0].match == "fk"), None)
        if shared is not None:
            shared.append(location)
        else:
            units.append([location])
    return list(plan.items())


def unit_where(unit: list[Location], table: Table, *, subject_id: uuid.UUID, email: Optional[str]) -> Any:
    clauses = [location_filter(location, table, subject_id=subject_id, email=email) for location in unit]
    return clauses[0] if len(clauses) == 1 else or_(*clauses)


def unit_statement(unit: list[Location], *, subject_id: uuid.UUID, email: Optional[str]) -> Any:
    """ONE streamed ``SELECT`` of a unit's columns (+ one LEFT JOIN per ``extra``), ordered by the primary key."""
    location = unit[0]
    table = _table(location.table or "")
    selected: list[Any] = [table.c[name] for name in exported_columns(location)]
    source: Any = table
    for extra in location.extra:
        fk_column, other_name, other_column = EXTRA_JOINS[extra]
        other = _table(other_name).alias(f"x_{extra}")
        source = source.outerjoin(other, other.c.id == table.c[fk_column])
        selected.append(other.c[other_column].label(extra))
    return (
        select(*selected).select_from(source)
        .where(unit_where(unit, table, subject_id=subject_id, email=email))
        .order_by(*table.primary_key.columns)
        .execution_options(yield_per=CURSOR_BATCH_ROWS)
    )


def child_statement(location: Location, child: str, *, subject_id: uuid.UUID, email: Optional[str]) -> Any:
    """The ``child_tables`` rows of the location's rows (e.g. chat messages): ``fk IN (SELECT id FROM parent
    WHERE <the location's filter>)`` — a subquery, never a Python id list."""
    parent = _table(location.table or "")
    table = _table(child)
    fk_column = next(column.name for column in table.columns
                     if any(fk.column.table.name == parent.name for fk in column.foreign_keys))
    parents = select(parent.c.id).where(location_filter(location, parent, subject_id=subject_id, email=email))
    return (
        select(*[table.c[name] for name in exported_columns(location, child)])
        .where(table.c[fk_column].in_(parents))
        .order_by(*table.primary_key.columns)
        .execution_options(yield_per=CURSOR_BATCH_ROWS)
    )


# ── members ──────────────────────────────────────────────────────────────────────

class _Parts:
    """One category's JSONL parts: a part is closed at ``PART_ROWS_MAX`` rows or before ``PART_BYTES_MAX`` bytes."""

    def __init__(self, category: str) -> None:
        self.category = category
        self.rows = 0
        self.parts = 0
        self._lines: list[bytes] = []
        self._size = 0

    def add(self, line: bytes) -> list[tuple[str, bytes]]:
        out = []
        if self._lines and self._size + len(line) > PART_BYTES_MAX:
            out.append(self._close())
        self._lines.append(line)
        self._size += len(line)
        self.rows += 1
        if len(self._lines) >= PART_ROWS_MAX:
            out.append(self._close())
        return out

    def tail(self) -> Optional[tuple[str, bytes]]:
        return self._close() if self._lines else None

    def _close(self) -> tuple[str, bytes]:
        self.parts += 1
        member = (member_name(self.category, self.parts), b"".join(self._lines))
        self._lines, self._size = [], 0
        return member


async def foreign_displays(db: Any, table_name: str, where: Any, subject_id: uuid.UUID) -> dict[Any, Optional[str]]:
    """``{user id: display}`` of every OTHER person a full location's rows name in a user FK column — ONE
    statement (``id IN (SELECT <fk> FROM <table> WHERE <the location's filter>)`` per FK column), before the
    rows stream. Never an e-mail."""
    columns = [column for column in user_fk_columns(table_name)]
    if not columns:
        return {}
    table, users = _table(table_name), _table("users")
    picks = [users.c.id.in_(select(table.c[column]).where(where, table.c[column].is_not(None))) for column in columns]
    result = await db.execute(
        select(users.c.id, users.c.full_name, users.c.username, users.c.erased_at)
        .where(or_(*picks), users.c.id != subject_id)
    )
    return {row[0]: display_of(row[1], row[2], row[3]) for row in result.all()}


async def own_emails(db: Any, subject_id: uuid.UUID, email: Optional[str]) -> frozenset[str]:
    """The subject's own addresses (lower-cased): the account's and every SSO link's ``external_email``."""
    fed = _table("federated_identities")
    rows = (await db.execute(select(fed.c.external_email).where(fed.c.user_id == subject_id))).all()
    return frozenset(value.lower() for value in [email, *(row[0] for row in rows)] if value)


async def profile_member(db: Any, subject_id: uuid.UUID, locations: dict[str, int]) -> bytes:
    """``profile.json``: the ``users.*`` row (its ``drop`` set and the hash columns removed) with the MFA facts
    under ``mfa`` — ``recovery_codes_left`` counts the unused ``mfa_recovery_codes`` (never a digest)."""
    profile_locations = [loc for loc in INVENTORY if loc.category == PROFILE_CATEGORY and loc.export_as != "none"]
    unknown = [loc.key for loc in profile_locations if loc.key not in ("users.*", "mfa_recovery_codes.user_id")]
    if unknown:
        raise ValueError(f"profile locations without a projection: {unknown}")
    users, codes = _table("users"), _table("mfa_recovery_codes")
    account = next(loc for loc in profile_locations if loc.key == "users.*")
    dropped = set(account.drop) | HASH_COLUMNS
    row = (await db.execute(
        select(*[column for column in users.columns if column.name not in dropped]).where(users.c.id == subject_id)
    )).mappings().one()
    total, unused = (await db.execute(
        select(func.count(), func.count().filter(codes.c.used_at.is_(None))).where(codes.c.user_id == subject_id)
    )).one()
    profile: dict[str, Any] = {key: value for key, value in row.items() if not key.startswith("mfa_")}
    profile["mfa"] = {"enabled": bool(row.get("mfa_enabled")), "enrolled_at": row.get("mfa_enrolled_at"),
                      "recovery_codes_left": int(unused or 0)}
    locations["users.*"] = 1
    locations["mfa_recovery_codes.user_id"] = int(total or 0)
    return encode_row(profile)


async def _stream_rows(db: Any, statement: Any) -> AsyncGenerator[dict[str, Any], None]:
    result = await db.stream(statement)
    try:
        async for mapping in result.mappings():
            yield dict(mapping)
    finally:
        await result.close()


def _count(unit: list[Location], raw: Mapping[str, Any], subject_id: uuid.UUID, locations: dict[str, int]) -> Location:
    """Rows per location (the emission proof): a shared statement's row counts for every location it matches.
    Returns the location the row is labelled with (``_location``): the first one it matches."""
    if len(unit) == 1:
        locations[unit[0].key] = locations.get(unit[0].key, 0) + 1
        return unit[0]
    matched = [location for location in unit if location.column and raw.get(location.column) == subject_id]
    for location in matched:
        locations[location.key] = locations.get(location.key, 0) + 1
    return matched[0] if matched else unit[0]


async def _unit_members(
    db: Any, unit: list[Location], parts: _Parts, *, subject: SubjectContext, email: Optional[str],
    locations: dict[str, int],
) -> AsyncGenerator[tuple[str, bytes], None]:
    location = unit[0]
    for each in unit:
        locations.setdefault(each.key, 0)
    displays: dict[Any, Optional[str]] = {}
    if location.export_as == "full":
        table = _table(location.table or "")
        displays = await foreign_displays(
            db, table.name, unit_where(unit, table, subject_id=subject.id, email=email), subject.id,
        )
    async for raw in _stream_rows(db, unit_statement(unit, subject_id=subject.id, email=email)):
        labelled = _count(unit, raw, subject.id, locations)
        for member in parts.add(encode_row(project_row(labelled, raw, subject=subject, displays=displays))):
            yield member


async def _child_members(
    db: Any, location: Location, child: str, parts: _Parts, *, subject: SubjectContext, email: Optional[str],
) -> AsyncGenerator[tuple[str, bytes], None]:
    statement = child_statement(location, child, subject_id=subject.id, email=email)
    async for raw in _stream_rows(db, statement):
        for member in parts.add(encode_row(project_row(location, raw, subject=subject, table_name=child))):
            yield member


async def iter_members(db: Any, *, subject: Any, stats: dict[str, Any]) -> AsyncGenerator[tuple[str, bytes], None]:
    """Every member but the manifest: ``README.txt``, ``profile.json``, then each category's parts (a location's
    ``child_tables`` right after its category). Fills ``stats["categories"]`` ({category: {rows, parts}}) and
    ``stats["locations"]`` ({location key: rows}) — every exported location gets an entry, 0 included."""
    subject_id = uuid.UUID(str(subject.id))
    email = getattr(subject, "email", None)
    context = SubjectContext(id=subject_id, own_emails=await own_emails(db, subject_id, email))
    locations: dict[str, int] = stats.setdefault("locations", {})
    categories: dict[str, dict[str, int]] = stats.setdefault("categories", {})
    yield README_MEMBER, README_TEXT.encode("utf-8")
    yield PROFILE_MEMBER, await profile_member(db, subject_id, locations)
    for category, units in export_plan():
        parts = _Parts(category)
        for unit in units:
            async for member in _unit_members(db, unit, parts, subject=context, email=email, locations=locations):
                yield member
        tail = parts.tail()
        if tail is not None:
            yield tail
        categories[category] = {"rows": parts.rows, "parts": parts.parts}
        for location in (loc for unit in units for loc in unit if loc.child_tables):
            for child in location.child_tables:
                if child in categories:
                    raise ValueError(f"child member {child!r} collides with a category")
                child_parts = _Parts(child)
                async for member in _child_members(db, location, child, child_parts, subject=context, email=email):
                    yield member
                tail = child_parts.tail()
                if tail is not None:
                    yield tail
                categories[child] = {"rows": child_parts.rows, "parts": child_parts.parts}


def counts_of(stats: Mapping[str, Any]) -> dict[str, int]:
    """Rows per member category (the job's ``counts``), ``profile`` = 1."""
    counts = {"profile": 1}
    counts.update({name: int(entry.get("rows", 0)) for name, entry in (stats.get("categories") or {}).items()})
    return counts


def manifest_extra(*, subject_id: uuid.UUID, username: Optional[str], stats: Mapping[str, Any], source: str,
                   requested_at: datetime) -> dict[str, Any]:
    """The subject branch's manifest keys (§1.3), from what the member iterator recorded."""
    from app.core.config import settings
    from app.services.project_export_service import _public_host

    return {
        "format": EXPORT_FORMAT,
        "schema_version": SCHEMA_VERSION,
        "source": {"app_version": getattr(settings, "APP_VERSION", None), "host": _public_host()},
        "subject": {"id": str(subject_id), "username": username},
        "categories": {name: dict(entry) for name, entry in (stats.get("categories") or {}).items()},
        "requested": {"source": source, "at": requested_at.isoformat()},
    }


# ── build → sign → write → re-read + verify ─────────────────────────────────────

async def _build(db: Any, *, subject: Any, sink: Any, now: datetime, stats: dict[str, Any], source: str,
                 requested_at: datetime) -> Any:
    from app.services import compliance_pack_service as packs

    subject_id = uuid.UUID(str(subject.id))
    username = getattr(subject, "username", None)
    extra: dict[str, Any] = {}
    scope = packs.ExportScope.for_subject(manifest_extra=extra)
    inner = iter_members(db, subject=subject, stats=stats)

    async def members() -> AsyncGenerator[tuple[str, bytes], None]:
        async for item in inner:
            yield item
        # build_archive writes the manifest AFTER the last member: fill the scope's keys now.
        extra.update(manifest_extra(subject_id=subject_id, username=username, stats=stats, source=source,
                                    requested_at=requested_at))

    outer = members()
    try:
        archive = await packs.build_archive(scope, members=outer, sink=sink, generated_at=now,
                                            max_bytes=EXPORT_MAX_BYTES)
    finally:
        # A refusal mid-location (the byte cap) leaves both iterators suspended: close them, so the server-side
        # cursor of the location being read is released before the caller records the failure.
        await outer.aclose()
        await inner.aclose()
    return scope, archive


async def run_subject_export(
    db: Any,
    *,
    job_id: uuid.UUID,
    subject: Any,
    storage: Any,
    now: datetime,
    on_written: Optional[Callable[[str], Awaitable[Any]]] = None,
    sign_db: Any = None,
    source: str = "self",
    requested_at: Optional[datetime] = None,
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
) -> SubjectExportOutcome:
    """Build → sign → write → RE-READ + verify. Raises :class:`SubjectExportFailed`.

    ``db`` only READS (the runner holds it on a read-only snapshot) and is rolled back once every row is in the
    spool; ``sign_db`` (default ``db``) is where ``_sign_pack`` may CREATE the deployment's signing key and where
    the public keys are read — the caller commits it. ``on_written(key)`` runs right after the upload (the runner
    commits the key there, so a crash before verification leaves the object for the janitor). Any failure after
    the upload removes the object, best effort. ``source`` / ``requested_at`` are the job's (the manifest's
    ``requested`` block)."""
    from app.services import compliance_pack_service as packs
    from app.services import compliance_signing
    from app.services.retention_export_service import (
        ExportFailed,
        new_spool,
        remove_archive,
        verify_stored_archive,
    )

    signer = sign_db if sign_db is not None else db
    subject_id = uuid.UUID(str(subject.id))
    stats: dict[str, Any] = {}
    spool = new_spool()
    key: Optional[str] = None
    try:
        try:
            scope, archive = await _build(db, subject=subject, sink=spool, now=now, stats=stats, source=source,
                                          requested_at=requested_at or now)
            await db.rollback()      # end the read-only snapshot: its connection is not needed for sign / write
            spool, signature, signing_key_id, _signing = await packs._sign_pack(
                signer, spool, pack_id=job_id, manifest_sha256=archive.manifest_sha256, actor=None, signed_at=now,
            )
        except packs.ArchiveTooLarge as exc:
            raise SubjectExportFailed("export_too_large") from exc
        except SubjectExportFailed:
            raise
        except Exception as exc:  # noqa: BLE001 - only the type name reaches the job row
            raise SubjectExportFailed("export_build", error_type=type(exc).__name__) from exc
        archive_sha256 = await asyncio.to_thread(packs.sha256_of_file, spool)
        size = spool.seek(0, 2)
        if size > EXPORT_MAX_BYTES:          # signature.json is appended after the builder's cap
            raise SubjectExportFailed("export_too_large")
        key = packs.build_export_key(scope, now, job_id)
        try:
            await storage.put_object_stream(
                key, spool, size=size, content_type="application/zip", bucket=packs.COMPLIANCE_BUCKET,
            )
            if on_written is not None:
                await on_written(key)
        except Exception as exc:  # noqa: BLE001
            raise SubjectExportFailed("export_write", error_type=type(exc).__name__) from exc
        try:
            public_keys = await compliance_signing.public_keys(signer)
        except Exception as exc:  # noqa: BLE001
            raise SubjectExportFailed("export_unverified", reason="signing keys unreadable") from exc
        try:
            await verify_stored_archive(
                storage, key=key, archive_sha256=archive_sha256, manifest_sha256=archive.manifest_sha256,
                public_keys=public_keys, job_id=job_id, signature=signature, signing_key_id=signing_key_id,
                sleep=sleep,
            )
        except ExportFailed as failed:
            raise SubjectExportFailed(
                "export_unverified", reason=str(failed.params.get("reason") or failed.code),
            ) from failed
        except Exception as exc:  # noqa: BLE001 - an unproven archive is a failed export
            raise SubjectExportFailed("export_unverified", reason=type(exc).__name__) from exc
    except SubjectExportFailed as failed:
        if key is not None:
            await remove_archive(storage, key=key)
        _slog.warning("subject_export_failed", job_id=str(job_id), subject_id=str(subject_id), code=failed.code)
        raise
    finally:
        spool.close()

    counts = counts_of(stats)
    _slog.info("subject_export_verified", job_id=str(job_id), subject_id=str(subject_id),
               categories=len(counts), rows=sum(counts.values()), bytes=size, files=archive.file_count)
    return SubjectExportOutcome(
        key=key, bytes=size, file_count=archive.file_count, manifest_sha256=archive.manifest_sha256,
        archive_sha256=archive_sha256, signature="signed" if signature else "unsigned",
        signing_key_id=signing_key_id, counts=counts, locations=dict(stats.get("locations") or {}),
    )


# ── the janitor's body (§2.2 Expiry) and the erasure's object step (E-8) ─────────

async def _remove(storage: Any, key: str) -> bool:
    from app.services.retention_export_service import remove_archive

    return bool(await remove_archive(storage, key=key))


async def sweep(db: Any, storage: Any, *, now: datetime, limit: int = SWEEP_LIMIT) -> dict[str, int]:
    """Stage the janitor's export work on ``db`` (the caller commits); idempotent — every UPDATE is guarded by the
    state it leaves; a storage fault leaves its row for the next run. The ONLY expiry path (no GET expires).

    1. a succeeded export past ``expires_at`` → object removed, ``expired`` (storage key NULL);
    2. the object of a failed / expired export → removed, storage key NULL;
    3. an export ``running`` past ``JOB_STALE_MINUTES`` (its worker died) → ``failed`` ``interrupted``, its
       partial object removed. An erasure is never touched here (the runner re-dispatches it, E-8)."""
    from app.models.postgres import SubjectRightsJob as Job

    out = {"expired": 0, "objects_removed": 0, "interrupted": 0, "remove_failed": 0}
    due = (await db.execute(
        select(Job.id, Job.storage_key)
        .where(Job.kind == "export", Job.status == "succeeded", Job.expires_at.is_not(None), Job.expires_at <= now)
        .order_by(Job.expires_at).limit(limit)
    )).all()
    for job_id, key in due:
        if key and not await _remove(storage, key):
            out["remove_failed"] += 1
            continue
        result = await db.execute(
            update(Job).where(Job.id == job_id, Job.status == "succeeded")
            .values(status="expired", storage_key=None).execution_options(synchronize_session=False)
        )
        out["expired"] += int(result.rowcount or 0)

    doomed = (await db.execute(
        select(Job.id, Job.storage_key)
        .where(Job.kind == "export", Job.status.in_(("failed", "expired")), Job.storage_key.is_not(None))
        .order_by(Job.requested_at).limit(limit)
    )).all()
    for job_id, key in doomed:
        if not await _remove(storage, key):
            out["remove_failed"] += 1
            continue
        await db.execute(update(Job).where(Job.id == job_id, Job.storage_key == key).values(storage_key=None)
                         .execution_options(synchronize_session=False))
        out["objects_removed"] += 1

    stale = (await db.execute(
        select(Job.id, Job.storage_key)
        .where(Job.kind == "export", Job.status == "running", Job.started_at.is_not(None),
               Job.started_at <= now - timedelta(minutes=JOB_STALE_MINUTES))
        .order_by(Job.started_at).limit(limit)
    )).all()
    for job_id, key in stale:
        removed = bool(key) and await _remove(storage, key)
        values: dict[str, Any] = {"status": "failed", "error_code": "interrupted",
                                  "error": ERROR_SENTENCES["interrupted"], "finished_at": now}
        if removed:
            values["storage_key"] = None
        result = await db.execute(update(Job).where(Job.id == job_id, Job.status == "running").values(**values)
                                  .execution_options(synchronize_session=False))
        out["interrupted"] += int(result.rowcount or 0)
    return out


async def remove_exports_of(db: Any, storage: Any, *, subject_id: uuid.UUID) -> int:
    """Every stored export of ``subject_id`` (G-C's object phase, E-8 / E1): its object removed, a succeeded
    export → ``expired``, the storage key NULL. Staged on ``db`` (the caller commits); id-based and idempotent (a
    second run finds nothing). Returns the objects removed; raises :class:`ExportObjectsRemaining` when storage
    refused one (the rows already handled stay staged — the phase runs again)."""
    from app.models.postgres import SubjectRightsJob as Job

    rows = (await db.execute(
        select(Job.id, Job.storage_key, Job.status)
        .where(Job.kind == "export", Job.subject_user_id == subject_id,
               or_(Job.storage_key.is_not(None), Job.status == "succeeded"))
        .order_by(Job.requested_at)
    )).all()
    removed = remaining = 0
    for job_id, key, status in rows:
        if key:
            if not await _remove(storage, key):
                remaining += 1
                continue
            removed += 1
        values: dict[str, Any] = {"storage_key": None}
        if status == "succeeded":
            values["status"] = "expired"
        await db.execute(update(Job).where(Job.id == job_id).values(**values)
                         .execution_options(synchronize_session=False))
    _slog.info("subject_exports_removed", subject_id=str(subject_id), removed=removed, remaining=remaining)
    if remaining:
        raise ExportObjectsRemaining(remaining)
    return removed
