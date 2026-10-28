"""Retention holds: place, release, list, and the anti-join clauses every purge and deletion path reads (W22a ACT-05).

**A live hold is ABSOLUTE.** While ``released_at IS NULL AND (expires_at IS
NULL OR expires_at > now)`` nothing it covers is purged or deleted — no route,
flag or role bypasses it; the only way past a hold is an ADMIN releasing it,
with a reason, audited.

**A hold is TRANSITIVE, one hop.** A ``project`` hold (``entity_id IS NULL``)
pins the whole project. A ``release`` hold pins every run linked to the release
(every link, not only primary), every compliance pack of the release, and —
through those runs — their decision reports. A ``run`` hold, and every run a
release hold pins, also pins its BASELINE run (``run_baselines.baseline_run_id``
of that run) — not that baseline's baseline. A ``compliance_pack`` hold pins
that pack only.

**Always an anti-join.** Every clause builder below compiles to ``NOT EXISTS
(…)`` correlated on the candidate's id, never ``NOT IN (subquery)``: a NULL
``entity_id`` (every project hold has one) makes ``x NOT IN (…, NULL)`` NULL
for every row, so a purge filtered that way would silently delete nothing
while reporting success. A NULL candidate id (an evidence row whose run is
gone) is "not held" by every run arm and still caught by the project arm.

**Fail closed.** Nothing here swallows a statement error: a hold read that
fails propagates, and its caller refuses (``citation_blockers`` adds
``HOLDS_UNREADABLE``) or fails (the purge, the routes) rather than proceeding
as "no holds".

**Expiry needs no job.** Every predicate takes ``now`` from its caller, so an
expired hold stops applying on the next read.

Stages only (``flush``); the router owns the commit (``STAGE_ONLY_SERVICES``).
This module never imports the activity ledger: the hold routes record
their ledger rows themselves, literally — otherwise every handler that merely
READS holds (the pack retire / delete routes) would count as "recording" for
the activity-coverage gate and two real gaps would leave its baseline
unrecorded. Log lines carry ids, entity types and counts only — never a
reason, a label or a name.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional, Sequence

import structlog
from sqlalchemy import String, and_, case, cast, exists, func, or_, select
from sqlalchemy.orm import aliased
from sqlalchemy.sql.elements import ColumnElement

from app.models.postgres import (
    CompliancePack,
    Project,
    ProjectActivityEvent,
    Release,
    ReleaseTestRunLink,
    RetentionHold,
    RunBaseline,
    SettingsAuditLog,
    TestRun,
)
from app.services.retention_vocab import (
    HELD_SENTENCE,
    HOLD_ENTITY_TYPES,
    HOLD_ERROR_SENTENCES,
    HOLD_LIST_DEFAULT_LIMIT,
    HOLD_LIST_MAX_LIMIT,
    HOLD_MAX_EXPIRY_DAYS,
    HOLD_REASON_MAX,
    HOLD_REASON_MIN,
    HOLD_STATES,
    PROJECT_HOLD_LABEL,
)

_slog = structlog.get_logger(__name__)

#: ``settings_audit_log.setting_key`` of every place / release row (never purged).
AUDIT_KEY_PREFIX = "retention_hold:"
#: The ``state`` filter of the list route: the three derived states, or every row.
LIST_STATES = (*HOLD_STATES, "all")


# ── Errors (each carries a ``HOLD_ERROR_SENTENCES`` key; the router maps them) ──


class HoldValidationError(ValueError):
    """A request value outside the vocabulary or bounds → 422 with one fixed sentence."""

    def __init__(self, code: str) -> None:
        self.code = code
        self.message = HOLD_ERROR_SENTENCES[code]
        super().__init__(self.message)


class HoldEntityNotFound(LookupError):
    """The held entity is missing OR belongs to another project (one 404, no oracle)."""

    code = "entity_not_found"


class HoldNotFound(LookupError):
    """No hold with that id in this project (another project's hold alike)."""

    code = "hold_not_found"


class HoldNotLive(Exception):
    """The hold was released or expired; it cannot be released (again)."""

    code = "hold_not_live"


# ── The structured hold every refusal and job record carries ─────────────────


@dataclass(frozen=True)
class HoldRef:
    """A live hold as another answer shows it (``HoldRefRead``)."""

    id: uuid.UUID
    entity_type: str
    entity_id: Optional[uuid.UUID]
    entity_label: Optional[str]
    reason: str
    expires_at: Optional[datetime]

    @classmethod
    def from_row(cls, row: Sequence[Any]) -> "HoldRef":
        """From a ``(id, entity_type, entity_id, entity_label, reason, expires_at)`` row."""
        return cls(
            id=row[0],
            entity_type=row[1],
            entity_id=row[2],
            entity_label=row[3],
            reason=row[4],
            expires_at=row[5],
        )

    def sentence(self) -> str:
        """``Held: <reason>`` — the one refusal sentence (409s, criteria blocked[], job error)."""
        return HELD_SENTENCE.format(reason=self.reason)

    def as_json(self) -> dict:
        """The ``HoldRefRead`` keys, JSON-safe (uuids as str, datetimes ISO)."""
        return {
            "hold_id": str(self.id),
            "entity_type": self.entity_type,
            "entity_id": str(self.entity_id) if self.entity_id is not None else None,
            "entity_label": self.entity_label,
            "reason": self.reason,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
        }


#: ``DeletionJob.holds_honoured.run_ids_kept`` is a sample, capped here.
RUN_IDS_KEPT_MAX = 200


def holds_honoured_record(
    holds: Sequence[HoldRef], *, run_ids_kept: Sequence[Any], packs_kept: int = 0
) -> Optional[dict]:
    """The ONE ``DeletionJob.holds_honoured`` shape, for an explicit deletion job.

    ``{"project_held", "holds": [HoldRefRead JSON…], "runs_kept", "packs_kept",
    "run_ids_kept": [≤ 200, sorted]}``; holds de-duplicated by id. ``None`` when
    no hold applied — the column stays NULL, which truthfully says so.
    """
    refs: list[HoldRef] = []
    seen: set[Any] = set()
    for ref in holds:
        if ref.id not in seen:
            seen.add(ref.id)
            refs.append(ref)
    if not refs:
        return None
    run_ids = sorted({str(r) for r in run_ids_kept})
    return {
        "project_held": any(ref.entity_type == "project" for ref in refs),
        "holds": [ref.as_json() for ref in refs],
        "runs_kept": len(run_ids),
        "packs_kept": int(packs_kept),
        "run_ids_kept": run_ids[:RUN_IDS_KEPT_MAX],
    }


def _aware(value: datetime) -> datetime:
    """A naive timestamp (SQLite, some drivers) is UTC; an aware one is converted."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def hold_state(row: Any, *, now: datetime) -> str:
    """``live`` | ``expired`` | ``released`` — derived for ``now``, never stored."""
    if getattr(row, "released_at", None) is not None:
        return "released"
    expires = getattr(row, "expires_at", None)
    if expires is not None and _aware(expires) <= _aware(now):
        return "expired"
    return "live"


# ── The anti-join clause builders (each compiles to NOT EXISTS, never NOT IN) ──
#
# Every arm reads its OWN aliases, so a builder dropped into a statement over
# retention_holds / release_test_run_links / run_baselines / test_runs never
# correlates with the caller's FROM by accident; only the candidate column the
# caller passes (``TestRun.id``, ``EvidenceArtifact.run_id`` …) correlates.


def _live(h: Any, now: datetime) -> ColumnElement[bool]:
    return and_(
        h.released_at.is_(None),
        or_(h.expires_at.is_(None), h.expires_at > now),
    )


def live_clause(now: datetime) -> ColumnElement[bool]:
    """``released_at IS NULL AND (expires_at IS NULL OR expires_at > now)`` on ``retention_holds``."""
    return _live(RetentionHold, now)


def _project_hold_exists(project_id: Any, *, now: datetime) -> ColumnElement[bool]:
    h = aliased(RetentionHold, name="rh_project")
    return exists().where(
        h.project_id == project_id,
        h.entity_type == "project",
        _live(h, now),
    )


def project_not_held(project_id: Any, *, now: datetime) -> ColumnElement[bool]:
    """NOT EXISTS (a live project hold of ``project_id``) — uncorrelated."""
    return ~_project_hold_exists(project_id, now=now)


_RunMatch = Callable[[Any], ColumnElement[bool]]


def _run_hold_arms(
    matches: _RunMatch, project_id: Any, *, now: datetime, tag: str
) -> tuple[ColumnElement[bool], ColumnElement[bool], ColumnElement[bool]]:
    """The three run arms as EXISTS, for the run ``matches(<uuid column>)`` selects.

    (a live run hold on it; a live hold on a release it is linked to; a
    ``run_baselines`` row naming it as the baseline of a run that is itself
    directly held or release-held — one hop).
    """
    def direct_and_release(match: _RunMatch, sub: str) -> tuple[ColumnElement[bool], ColumnElement[bool]]:
        h_run = aliased(RetentionHold, name=f"rh_run_{tag}{sub}")
        h_rel = aliased(RetentionHold, name=f"rh_rel_{tag}{sub}")
        link = aliased(ReleaseTestRunLink, name=f"rh_link_{tag}{sub}")
        direct = exists().where(
            h_run.project_id == project_id,
            h_run.entity_type == "run",
            match(h_run.entity_id),
            _live(h_run, now),
        )
        via_release = exists().where(
            match(link.test_run_id),
            h_rel.project_id == project_id,
            h_rel.entity_type == "release",
            h_rel.entity_id == link.release_id,
            _live(h_rel, now),
        )
        return direct, via_release

    direct, via_release = direct_and_release(matches, "")
    base = aliased(RunBaseline, name=f"rh_base_{tag}")
    b_direct, b_release = direct_and_release(lambda col: col == base.run_id, "b")
    as_baseline = exists().where(matches(base.baseline_run_id), or_(b_direct, b_release))
    return direct, via_release, as_baseline


def run_not_held(run_id_col: Any, project_id: Any, *, now: datetime) -> ColumnElement[bool]:
    """The AND of four anti-joins on a candidate run id.

    1. no live project hold of ``project_id``;
    2. no live run hold with ``entity_id = run_id_col``;
    3. no ``release_test_run_links`` row of ``run_id_col`` whose release has a live hold;
    4. no ``run_baselines`` row with ``baseline_run_id = run_id_col`` whose own
       ``run_id`` is directly held or release-held (ONE hop: the baseline of a
       held run is held, the baseline's baseline is not).

    NULL-safe: a NULL ``run_id_col`` matches no run arm and falls to arm 1.
    """
    direct, via_release, as_baseline = _run_hold_arms(
        lambda col: col == run_id_col, project_id, now=now, tag="c"
    )
    return and_(
        project_not_held(project_id, now=now),
        ~direct,
        ~via_release,
        ~as_baseline,
    )


def pack_not_held(
    pack_id_col: Any, release_id_col: Any, project_id: Any, *, now: datetime
) -> ColumnElement[bool]:
    """No live project hold, no live hold on the pack, no live hold on the pack's release.

    A NULL ``release_id_col`` (the release was deleted) matches no release arm.
    """
    h_pack = aliased(RetentionHold, name="rh_pack")
    h_rel = aliased(RetentionHold, name="rh_pack_rel")
    return and_(
        project_not_held(project_id, now=now),
        ~exists().where(
            h_pack.project_id == project_id,
            h_pack.entity_type == "compliance_pack",
            h_pack.entity_id == pack_id_col,
            _live(h_pack, now),
        ),
        ~exists().where(
            h_rel.project_id == project_id,
            h_rel.entity_type == "release",
            h_rel.entity_id == release_id_col,
            _live(h_rel, now),
        ),
    )


def activity_not_held(project_id: Any, *, now: datetime) -> ColumnElement[bool]:
    """Anti-joins on ``project_activity_events`` rows (correlated on the outer row).

    No live project hold; not a ``run`` row whose ``entity_id`` (text) names a
    held run (directly, through a held release, or as a held run's baseline);
    not a row whose ``release_id`` — or a ``release`` row whose ``entity_id`` —
    names a release with a live hold. The uuid side is cast to text, never the
    text side to uuid (``entity_id`` of other entity types is not a uuid).
    """
    pae = ProjectActivityEvent
    direct, via_release, as_baseline = _run_hold_arms(
        lambda col: and_(pae.entity_type == "run", cast(col, String) == pae.entity_id),
        project_id,
        now=now,
        tag="a",
    )
    h_rel = aliased(RetentionHold, name="rh_act_rel")
    h_rel_entity = aliased(RetentionHold, name="rh_act_rel_entity")
    return and_(
        project_not_held(project_id, now=now),
        ~direct,
        ~via_release,
        ~as_baseline,
        ~exists().where(
            h_rel.project_id == project_id,
            h_rel.entity_type == "release",
            h_rel.entity_id == pae.release_id,
            _live(h_rel, now),
        ),
        ~exists().where(
            pae.entity_type == "release",
            h_rel_entity.project_id == project_id,
            h_rel_entity.entity_type == "release",
            cast(h_rel_entity.entity_id, String) == pae.entity_id,
            _live(h_rel_entity, now),
        ),
    )


# ── Readers (each ONE statement read with ``.all()`` — Row tuples, never
#    ``.mappings()`` / ``.scalars()`` — so the queue / MagicMock fakes of the
#    existing tests answer it with ``[]``) ─────────────────────────────────────


def _hold_columns(h: Any) -> tuple[Any, ...]:
    return (h.id, h.entity_type, h.entity_id, h.entity_label, h.reason, h.expires_at)


def _refs(rows: Any) -> list[HoldRef]:
    out: list[HoldRef] = []
    seen: set[Any] = set()
    for row in rows or []:
        if row[0] in seen:
            continue
        seen.add(row[0])
        out.append(HoldRef.from_row(row))
    return out


async def live_holds(db: Any, project_id: Any, *, now: datetime) -> list[HoldRef]:
    """Every live hold of the project (any entity type), ``placed_at, id`` order."""
    h = RetentionHold
    rows = (
        await db.execute(
            select(*_hold_columns(h))
            .where(h.project_id == project_id, _live(h, now))
            .order_by(h.placed_at, h.id)
        )
    ).all()
    return _refs(rows)


async def any_live_hold(db: Any, *, project_id: Any, now: datetime) -> list[HoldRef]:
    """Project reset's question: every live hold of the project, of every type.

    A ``runs`` reset deletes held runs, a ``full`` reset held releases, and a
    pack would lose its release link — so ANY live hold refuses a reset (D-4).
    """
    return await live_holds(db, project_id, now=now)


def _covers_run(h: Any, r: Any) -> ColumnElement[bool]:
    """Hold ``h`` covers run ``r`` (same project): project / run / release / baseline-of-held."""
    link = aliased(ReleaseTestRunLink, name="rh_cov_link")
    base = aliased(RunBaseline, name="rh_cov_base")
    base_rel = aliased(RunBaseline, name="rh_cov_base_rel")
    link_rel = aliased(ReleaseTestRunLink, name="rh_cov_link_rel")
    return or_(
        h.entity_type == "project",
        and_(
            h.entity_type == "run",
            or_(
                h.entity_id == r.id,
                exists().where(base.baseline_run_id == r.id, base.run_id == h.entity_id),
            ),
        ),
        and_(
            h.entity_type == "release",
            or_(
                exists().where(link.test_run_id == r.id, link.release_id == h.entity_id),
                exists().where(
                    base_rel.baseline_run_id == r.id,
                    link_rel.test_run_id == base_rel.run_id,
                    link_rel.release_id == h.entity_id,
                ),
            ),
        ),
    )


async def holds_covering_runs(
    db: Any, *, project_id: Any, run_ids: Sequence[uuid.UUID], now: datetime
) -> dict[uuid.UUID, list[HoldRef]]:
    """Run id → the live holds covering it; only covered ids are keys.

    ONE statement (rows ``(run_id, id, entity_type, entity_id, entity_label,
    reason, expires_at)``); no statement for an empty list.
    """
    ids = list(dict.fromkeys(run_ids))
    if not ids:
        return {}
    h = aliased(RetentionHold, name="rh")
    r = aliased(TestRun, name="rh_cov_run")
    rows = (
        await db.execute(
            select(r.id, *_hold_columns(h))
            .select_from(r)
            .join(h, and_(h.project_id == r.project_id, _live(h, now), _covers_run(h, r)))
            .where(r.project_id == project_id, r.id.in_(ids))
            .order_by(h.placed_at, h.id)
        )
    ).all()
    grouped: dict[uuid.UUID, list[Any]] = {}
    for row in rows or []:
        grouped.setdefault(row[0], []).append(tuple(row[1:]))
    return {run_id: _refs(hold_rows) for run_id, hold_rows in grouped.items()}


async def holds_covering_run(db: Any, *, run_id: uuid.UUID, now: datetime) -> list[HoldRef]:
    """The live holds covering one run — the project arm reads the run's project in SQL.

    ONE statement; a run that no longer exists is covered by nothing.
    """
    h = aliased(RetentionHold, name="rh")
    r = aliased(TestRun, name="rh_cov_run")
    rows = (
        await db.execute(
            select(*_hold_columns(h))
            .select_from(r)
            .join(h, and_(h.project_id == r.project_id, _live(h, now), _covers_run(h, r)))
            .where(r.id == run_id)
            .order_by(h.placed_at, h.id)
        )
    ).all()
    return _refs(rows)


async def holds_covering_pack(db: Any, *, pack: Any, now: datetime) -> list[HoldRef]:
    """The live holds covering one compliance pack: project ∪ the pack ∪ its release."""
    h = RetentionHold
    arms = [
        h.entity_type == "project",
        and_(h.entity_type == "compliance_pack", h.entity_id == pack.id),
    ]
    release_id = getattr(pack, "release_id", None)
    if release_id is not None:
        arms.append(and_(h.entity_type == "release", h.entity_id == release_id))
    rows = (
        await db.execute(
            select(*_hold_columns(h))
            .where(h.project_id == pack.project_id, _live(h, now), or_(*arms))
            .order_by(h.placed_at, h.id)
        )
    ).all()
    return _refs(rows)


async def holds_covering_packs(
    db: Any,
    *,
    project_id: Any,
    release_id: Optional[uuid.UUID],
    pack_ids: Sequence[uuid.UUID],
    now: datetime,
) -> dict[uuid.UUID, list[HoldRef]]:
    """Pack id → its live holds (project ∪ pack ∪ the release's), for a per-release pack list.

    ONE statement for the whole list (comparisons / ``IN`` only, so it runs on
    any engine); no statement for an empty list. Every pack id is a key (an
    unheld pack maps to ``[]``).
    """
    ids = list(dict.fromkeys(pack_ids))
    if not ids:
        return {}
    h = RetentionHold
    arms = [
        h.entity_type == "project",
        and_(h.entity_type == "compliance_pack", h.entity_id.in_(ids)),
    ]
    if release_id is not None:
        arms.append(and_(h.entity_type == "release", h.entity_id == release_id))
    rows = (
        await db.execute(
            select(*_hold_columns(h))
            .where(h.project_id == project_id, _live(h, now), or_(*arms))
            .order_by(h.placed_at, h.id)
        )
    ).all()
    refs = _refs(rows)
    out: dict[uuid.UUID, list[HoldRef]] = {}
    for pack_id in ids:
        out[pack_id] = [
            ref for ref in refs
            if ref.entity_type in ("project", "release") or ref.entity_id == pack_id
        ]
    return out


async def holds_covering_release(
    db: Any, *, release_id: uuid.UUID, now: datetime
) -> list[HoldRef]:
    """The live holds covering a release: its own and its project's (project read in SQL)."""
    h = RetentionHold
    project_of_release = (
        select(Release.project_id).where(Release.id == release_id).scalar_subquery()
    )
    rows = (
        await db.execute(
            select(*_hold_columns(h))
            .where(
                h.project_id == project_of_release,
                _live(h, now),
                or_(
                    h.entity_type == "project",
                    and_(h.entity_type == "release", h.entity_id == release_id),
                ),
            )
            .order_by(h.placed_at, h.id)
        )
    ).all()
    return _refs(rows)


async def held_run_ids(db: Any, *, project_id: Any, now: datetime) -> set[uuid.UUID]:
    """Every run of the project a live hold pins (any arm, project hold included).

    ONE statement — the negation of :func:`run_not_held`, so the AI over-cap
    sweep and the live-session slugs exclude exactly what the purge excludes.
    """
    r = aliased(TestRun, name="rh_pinned_run")
    rows = (
        await db.execute(
            select(r.id).where(
                r.project_id == project_id,
                ~run_not_held(r.id, project_id, now=now),
            )
        )
    ).all()
    return {row[0] for row in rows or []}


async def pinned_runs_before(
    db: Any, *, project_id: Any, created_before: datetime, now: datetime
) -> dict[uuid.UUID, set[uuid.UUID]]:
    """Hold id → the runs created before ``created_before`` it pins (only holds that pin one).

    A run pinned by two holds appears under both. ONE statement; read BEFORE
    any delete so the purge's ``holds_honoured`` counts what it kept.
    """
    h = aliased(RetentionHold, name="rh")
    r = aliased(TestRun, name="rh_cov_run")
    rows = (
        await db.execute(
            select(h.id, r.id)
            .select_from(h)
            .join(r, r.project_id == h.project_id)
            .where(
                h.project_id == project_id,
                _live(h, now),
                r.created_at < created_before,
                _covers_run(h, r),
            )
        )
    ).all()
    out: dict[uuid.UUID, set[uuid.UUID]] = {}
    for hold_id, run_id in rows or []:
        out.setdefault(hold_id, set()).add(run_id)
    return out


async def pinned_expired_packs(
    db: Any, *, project_id: Any, now: datetime
) -> dict[uuid.UUID, set[uuid.UUID]]:
    """Hold id → the expired packs (``retention_expires_at < now``) it pins. ONE statement."""
    h = aliased(RetentionHold, name="rh")
    p = aliased(CompliancePack, name="rh_pack_row")
    rows = (
        await db.execute(
            select(h.id, p.id)
            .select_from(h)
            .join(p, p.project_id == h.project_id)
            .where(
                h.project_id == project_id,
                _live(h, now),
                p.retention_expires_at < now,
                or_(
                    h.entity_type == "project",
                    and_(h.entity_type == "compliance_pack", h.entity_id == p.id),
                    and_(h.entity_type == "release", h.entity_id == p.release_id),
                ),
            )
        )
    ).all()
    out: dict[uuid.UUID, set[uuid.UUID]] = {}
    for hold_id, pack_id in rows or []:
        out.setdefault(hold_id, set()).add(pack_id)
    return out


# ── Place / release / list ───────────────────────────────────────────────────


def _validated_reason(reason: Optional[str]) -> str:
    text = (reason or "").strip()
    if not (HOLD_REASON_MIN <= len(text) <= HOLD_REASON_MAX):
        raise HoldValidationError("reason_length")
    return text


def _validated_expiry(expires_at: Optional[datetime], now: datetime) -> Optional[datetime]:
    if expires_at is None:
        return None
    expires = _aware(expires_at)
    if expires <= now:
        raise HoldValidationError("expiry_not_future")
    if expires > now + timedelta(days=HOLD_MAX_EXPIRY_DAYS):
        raise HoldValidationError("expiry_too_far")
    return expires


def _display_name(actor: Any) -> Optional[str]:
    """Full name, else the username — never an e-mail (a snapshot shown to every member)."""
    name = getattr(actor, "full_name", None) or getattr(actor, "username", None)
    return str(name)[:200] if name else None


async def _entity_label(
    db: Any, *, project_id: Any, entity_type: str, entity_id: Optional[uuid.UUID]
) -> str:
    """Resolve the entity INSIDE the project, ``FOR SHARE`` (R-1 / D-14), and snapshot its label.

    A miss — absent or another project's — is the same :class:`HoldEntityNotFound`.
    """
    if entity_type == "project":
        return PROJECT_HOLD_LABEL
    stmt: Any
    if entity_type == "release":
        stmt = select(Release.name).where(Release.id == entity_id, Release.project_id == project_id)
    elif entity_type == "run":
        stmt = select(TestRun.build_number).where(
            TestRun.id == entity_id, TestRun.project_id == project_id
        )
    else:
        stmt = select(CompliancePack.generated_at).where(
            CompliancePack.id == entity_id, CompliancePack.project_id == project_id
        )
    row = (await db.execute(stmt.with_for_update(read=True))).first()
    if row is None:
        raise HoldEntityNotFound(entity_type)
    value = row[0]
    if entity_type == "release":
        label = f"release {value}"
    elif entity_type == "run":
        label = f"Build {value}"
    elif value is not None:
        label = "pack of " + value.strftime("%Y-%m-%d")
    else:
        label = "compliance pack"
    return label[:300]


def _audit_fields(hold: RetentionHold) -> dict:
    return {
        "project_id": str(hold.project_id),
        "hold_id": str(hold.id),
        "entity_type": hold.entity_type,
        "entity_id": str(hold.entity_id) if hold.entity_id is not None else None,
        "entity_label": hold.entity_label,
        "reason": hold.reason,
        "expires_at": hold.expires_at.isoformat() if hold.expires_at else None,
    }


async def place_hold(
    db: Any,
    *,
    project_id: uuid.UUID,
    entity_type: str,
    entity_id: Optional[uuid.UUID],
    reason: str,
    expires_at: Optional[datetime],
    actor: Any,
    now: datetime,
) -> RetentionHold:
    """Validate, lock, stage the hold and its ``settings_audit_log`` row, flush.

    Order: entity type; ``entity_id`` NULL iff ``project``; trimmed reason
    3-500; ``expires_at`` in ``(now, now + 3650 d]``. Then the project row and
    the entity row are locked ``FOR SHARE`` — a hold placed while a purge or a
    delete holds its lock waits for it, then finds what is left. Several live
    holds on one entity are allowed (each is released on its own).
    """
    now = _aware(now)
    if entity_type not in HOLD_ENTITY_TYPES:
        raise HoldValidationError("unknown_entity_type")
    if entity_type == "project" and entity_id is not None:
        raise HoldValidationError("entity_id_forbidden")
    if entity_type != "project" and entity_id is None:
        raise HoldValidationError("entity_id_required")
    clean_reason = _validated_reason(reason)
    expires = _validated_expiry(expires_at, now)

    project_row = (
        await db.execute(
            select(Project.id).where(Project.id == project_id).with_for_update(read=True)
        )
    ).first()
    if project_row is None:
        raise HoldEntityNotFound("project")
    label = await _entity_label(
        db, project_id=project_id, entity_type=entity_type, entity_id=entity_id
    )

    actor_id = getattr(actor, "id", None)
    actor_name = _display_name(actor)
    hold = RetentionHold(
        id=uuid.uuid4(),
        project_id=project_id,
        entity_type=entity_type,
        entity_id=entity_id,
        entity_label=label,
        reason=clean_reason,
        expires_at=expires,
        placed_by_id=actor_id,
        placed_by_name=actor_name,
        placed_at=now,
    )
    db.add(hold)
    db.add(
        SettingsAuditLog(
            setting_key=f"{AUDIT_KEY_PREFIX}{project_id}",
            action="hold_placed",
            actor_id=actor_id,
            actor_name=actor_name,
            changed_fields=_audit_fields(hold),
        )
    )
    await db.flush()
    _slog.info(
        "retention_hold_placed",
        project_id=str(project_id),
        hold_id=str(hold.id),
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        has_expiry=expires is not None,
    )
    return hold


async def release_hold(
    db: Any,
    *,
    project_id: uuid.UUID,
    hold_id: uuid.UUID,
    reason: str,
    actor: Any,
    now: datetime,
) -> RetentionHold:
    """Release a LIVE hold of this project with a reason; stage the audit row; flush.

    Loaded ``WHERE id AND project_id … FOR UPDATE`` (another project's hold is
    the same :class:`HoldNotFound`); a released or expired hold is
    :class:`HoldNotLive` — two concurrent releases serialise on the row lock
    and the second sees ``released_at``.
    """
    now = _aware(now)
    hold: Optional[RetentionHold] = (
        await db.execute(
            select(RetentionHold)
            .where(RetentionHold.id == hold_id, RetentionHold.project_id == project_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if hold is None:
        raise HoldNotFound(str(hold_id))
    if hold_state(hold, now=now) != "live":
        raise HoldNotLive(str(hold_id))
    clean_reason = _validated_reason(reason)

    actor_id = getattr(actor, "id", None)
    actor_name = _display_name(actor)
    hold.released_at = now
    hold.released_by_id = actor_id
    hold.released_by_name = actor_name
    hold.release_reason = clean_reason
    db.add(
        SettingsAuditLog(
            setting_key=f"{AUDIT_KEY_PREFIX}{project_id}",
            action="hold_released",
            actor_id=actor_id,
            actor_name=actor_name,
            changed_fields={**_audit_fields(hold), "release_reason": clean_reason},
        )
    )
    await db.flush()
    _slog.info(
        "retention_hold_released",
        project_id=str(project_id),
        hold_id=str(hold.id),
        entity_type=hold.entity_type,
    )
    return hold


def _list_filters(
    *, project_id: Any, entity_type: Optional[str], entity_id: Optional[uuid.UUID]
) -> list[ColumnElement[bool]]:
    if entity_type is not None and entity_type not in HOLD_ENTITY_TYPES:
        raise HoldValidationError("unknown_entity_type")
    h = RetentionHold
    # Every statement carries project_id: another project's entity_id matches nothing.
    where: list[ColumnElement[bool]] = [h.project_id == project_id]
    if entity_type is not None:
        where.append(h.entity_type == entity_type)
    if entity_id is not None:
        where.append(h.entity_id == entity_id)
    return where


def _state_filter(state: str, now: datetime) -> Optional[ColumnElement[bool]]:
    h = RetentionHold
    if state == "live":
        return live_clause(now)
    if state == "expired":
        return and_(h.released_at.is_(None), h.expires_at.is_not(None), h.expires_at <= now)
    if state == "released":
        return h.released_at.is_not(None)
    return None


async def list_holds(
    db: Any,
    *,
    project_id: uuid.UUID,
    state: str = "live",
    entity_type: Optional[str] = None,
    entity_id: Optional[uuid.UUID] = None,
    limit: int = HOLD_LIST_DEFAULT_LIMIT,
    now: datetime,
) -> tuple[list[RetentionHold], int]:
    """The project's holds in a state (``live`` · ``expired`` · ``released`` · ``all``), newest first.

    ``total`` is a COUNT of the same filter, never ``len(items)``. Read-only.
    """
    if state not in LIST_STATES:
        raise HoldValidationError("unknown_state")
    now = _aware(now)
    where = _list_filters(project_id=project_id, entity_type=entity_type, entity_id=entity_id)
    state_where = _state_filter(state, now)
    if state_where is not None:
        where.append(state_where)
    limit = max(1, min(int(limit), HOLD_LIST_MAX_LIMIT))
    h = RetentionHold
    rows = (
        await db.execute(
            select(h).where(*where).order_by(h.placed_at.desc(), h.id.desc()).limit(limit)
        )
    ).scalars().all()
    total = (
        await db.execute(select(func.count()).select_from(h).where(*where))
    ).scalar()
    return list(rows), int(total or 0)


async def count_holds_by_state(
    db: Any,
    *,
    project_id: uuid.UUID,
    entity_type: Optional[str] = None,
    entity_id: Optional[uuid.UUID] = None,
    now: datetime,
) -> dict[str, int]:
    """``{"live", "expired", "released"}`` for the project (entity filters applied, state ignored).

    ONE ``GROUP BY`` of the derived state, every key present (0 when none). The
    CASE is grouped through a subquery: grouping by an expression carrying a
    bind parameter would bind ``now`` twice and Postgres would refuse it.
    """
    now = _aware(now)
    h = RetentionHold
    where = _list_filters(project_id=project_id, entity_type=entity_type, entity_id=entity_id)
    state_expr = case(
        (h.released_at.is_not(None), "released"),
        (and_(h.expires_at.is_not(None), h.expires_at <= now), "expired"),
        else_="live",
    ).label("state")
    sub = select(state_expr).where(*where).subquery()
    rows = (
        await db.execute(select(sub.c.state, func.count()).group_by(sub.c.state))
    ).all()
    counts = {state: 0 for state in HOLD_STATES}
    for state, n in rows or []:
        if state in counts:
            counts[state] = int(n)
    return counts
