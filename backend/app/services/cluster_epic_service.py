"""Suggest-only Jira epics for recurring systemic failure clusters (W36 STRAT-03, owner decision D30).

A person on a project that opted in (``projects.cluster_epic_suggestions``,
default off) may file ONE Jira epic for a recurring systemic cluster. Nothing
here runs on its own: no Celery task, no beat entry, no webhook target -- Jira
is written only by the person's POST (``routers/defect_jira.py``).

* **Recurring** = ``co_failure_runs >= EPIC_MIN_CO_FAILURE_RUNS`` (the runs in the
  cluster's window in which every member failed). The clusters keep no
  night-by-night history, so this is the honest meaning of "recurs".
* **Identity** = the cluster's ``membership_key`` -- the only identity that
  survives the nightly delete + re-insert, for an UNCHANGED member set. A
  member joining or leaving is a new key and so no link (OD-4: no overlap
  matching). The link lives in ``systemic_cluster_epics``; never a ``Defect``
  row (it would count in /defects, the KPIs and the release gate).
* **Filing** reuses ``defect_jira_service._file_or_reuse`` (advisory lock ->
  ledger claim -> POST -> label reconcile) in its own claim namespace
  ``cluster-epic:{pid}``, with the signature ``sha256("systemic-epic::" +
  membership_key)``; a 409 ``jira_outcome_unknown`` and ``confirm_not_filed``
  behave exactly as the one-click defect create.

Stage-only: no commit, no rollback here; the router commits.
"""
from __future__ import annotations

import hashlib
import uuid as _uuid
from typing import Any, Optional

import structlog
from fastapi import HTTPException
from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.postgres import (
    Project,
    SystemicClusterEpic,
    SystemicFlakeCluster,
    SystemicFlakeClusterMember,
    User,
)
from app.services import defect_jira_service as jira
from app.services.activity.service import ActorRef, record as record_activity

logger = structlog.get_logger(__name__)

#: A cluster qualifies for an epic when every member failed together in at least this many runs.
EPIC_MIN_CO_FAILURE_RUNS = 3
EPIC_ISSUE_TYPE = "Epic"
EPIC_SCOPE_PREFIX = "cluster-epic"                      # the ledger claim namespace: cluster-epic:{pid}
EPIC_LABELS: tuple[str, ...] = ("testlookup", "systemic-cluster-epic")
EPIC_TARGET_TYPE = "systemic_cluster"
SUMMARY_PREFIX = "[TestLookup] Recurring failure cluster: "
SUMMARY_MAX = 255
MEMBER_NAMES_SHOWN = 50
#: The member names an epic description carries stop at this many characters (Jira caps a description at
#: 32,767): the first 50 names fit unless the names are very long; the rest is "and N more".
MEMBER_NAMES_MAX_CHARS = 10_000
OPENING_PREFIX = "Recurring failure cluster detected by TestLookup: "
CLOSING_TEMPLATE = "Filed from TestLookup by {filer} — nothing is filed automatically."
UNKNOWN_FILER = "a TestLookup user"
EVENT_CREATED = "cluster.epic_created"

OPT_IN_OFF_DETAIL = "Epic suggestions are off for this project"
PROJECT_NOT_FOUND_DETAIL = "Project not found"
CLUSTER_GONE_DETAIL = (
    "This failure cluster is no longer in the project's clusters: its members changed or it was not found "
    "in the latest nightly clustering."
)
NOT_RECURRING_DETAIL = {
    "code": "not_recurring",
    "message": (
        f"This cluster has not recurred enough for an epic: its tests failed together in fewer than "
        f"{EPIC_MIN_CO_FAILURE_RUNS} runs."
    ),
}
NO_EPIC_TYPE_DETAIL = {
    "code": "no_epic_issue_type",
    "message": "Jira offers no Epic issue type, so an epic cannot be filed.",
}
METADATA_UNREADABLE_DETAIL = "Could not read Jira's issue types"
#: The human sentence for each ``availability_reason`` (the status route's ``reason``).
REASON_SENTENCES = {
    "offline_mode": "Offline mode is on: TestLookup makes no calls to Jira.",
    "disabled": "The Jira integration is turned off in Settings › Integrations.",
    "not_configured": "Jira is not set up: add the site, e-mail and API token in Settings › Integrations.",
}


def epic_signature(membership_key: str) -> str:
    """The filing signature of a cluster's epic (its lock, its claim and its ``testlookup-sig-*`` label)."""
    return hashlib.sha256(f"systemic-epic::{membership_key}".encode("utf-8")).hexdigest()[:64]


def epic_summary(label: str) -> str:
    """``[TestLookup] Recurring failure cluster: <label>`` on one line, at most 255 characters."""
    return " ".join(f"{SUMMARY_PREFIX}{label}".split())[:SUMMARY_MAX]


def _pid(project_id: Any) -> _uuid.UUID:
    return project_id if isinstance(project_id, _uuid.UUID) else _uuid.UUID(str(project_id))


def _deep_link() -> str:
    return f"{settings.public_base_url}/failures?tab=groups"


def _epic_parts(cluster: SystemicFlakeCluster, names: list[str], *, filer: Optional[str]) -> dict[str, Any]:
    """The epic description's content, ONCE, in reading order — rendered to ADF for Jira
    (:func:`_epic_adf`) and to plain text for the preview (:func:`_epic_text`), so what the person sees
    is what Jira gets. Members: the first 50 names, fewer when they would pass
    ``MEMBER_NAMES_MAX_CHARS``, then "and N more"."""
    cohesion = f"{cluster.cohesion:.2f}" if cluster.cohesion is not None else "unknown"
    shown: list[str] = []
    budget = MEMBER_NAMES_MAX_CHARS
    for name in names[:MEMBER_NAMES_SHOWN]:
        if len(name) > budget:
            break
        shown.append(name)
        budget -= len(name)
    more = len(names) - len(shown)
    return {
        "opening": OPENING_PREFIX + " ".join(str(cluster.label or "").split()),
        "facts": [
            f"Cause family: {cluster.cause_family or 'unknown'}",
            f"Co-failure: the tests failed together in {cluster.co_failure_runs} runs",
            f"Cohesion: {cohesion}",
            f"Window: the last {cluster.window_days} days",
        ],
        "members_heading": f"Member tests ({len(names)}):",
        "members": shown,
        "more": f"and {more} more" if more > 0 else None,
        "link_label": "View in TestLookup: ",
        "link": _deep_link(),
        "closing": CLOSING_TEMPLATE.format(filer=filer or UNKNOWN_FILER),
    }


def _epic_text(parts: dict[str, Any]) -> str:
    """The preview's ``description_text``: the same content as :func:`_epic_adf`, in the same order."""
    lines = [parts["opening"], ""]
    lines += [f"- {fact}" for fact in parts["facts"]]
    lines += ["", parts["members_heading"]]
    lines += [f"- {name}" for name in parts["members"]]
    if parts["more"]:
        lines.append(parts["more"])
    lines += ["", parts["link_label"] + parts["link"], "", parts["closing"]]
    return "\n".join(lines)


def _text(value: str, marks: Optional[list[dict[str, Any]]] = None) -> dict[str, Any]:
    node: dict[str, Any] = {"type": "text", "text": value}
    if marks:
        node["marks"] = marks
    return node


def _paragraph(*nodes: dict[str, Any]) -> dict[str, Any]:
    return {"type": "paragraph", "content": list(nodes)}


def _bullets(items: list[str]) -> dict[str, Any]:
    return {"type": "bulletList", "content": [{"type": "listItem", "content": [_paragraph(_text(i))]} for i in items]}


def _epic_adf(parts: dict[str, Any]) -> dict[str, Any]:
    """The epic's own Atlassian Document — no heading, no defect wording (the node shapes of
    ``defect_jira_service._adf_from_prefill``: paragraphs of text nodes, a ``link`` mark)."""
    content: list[dict[str, Any]] = [_paragraph(_text(parts["opening"])), _bullets(parts["facts"])]
    content.append(_paragraph(_text(parts["members_heading"], [{"type": "strong"}])))
    if parts["members"]:
        content.append(_bullets(parts["members"]))
    if parts["more"]:
        content.append(_paragraph(_text(parts["more"])))
    content.append(_paragraph(
        _text(parts["link_label"]), _text(parts["link"], [{"type": "link", "attrs": {"href": parts["link"]}}]),
    ))
    content.append(_paragraph(_text(parts["closing"])))
    return {"type": "doc", "version": 1, "content": content}


def _epic_type_name(issue_types: list[Any]) -> Optional[str]:
    """Jira's own spelling of the Epic issue type, or None when it offers none."""
    for name in issue_types:
        if isinstance(name, str) and name.strip().casefold() == EPIC_ISSUE_TYPE.casefold():
            return name
    return None


# ── reads ────────────────────────────────────────────────────────────────────


async def _project_settings(db: AsyncSession, pid: _uuid.UUID) -> tuple[bool, Optional[str]]:
    """``(cluster_epic_suggestions, jira_project_key)`` of the project; 404 when it does not exist."""
    row = (
        await db.execute(
            select(Project.cluster_epic_suggestions, Project.jira_project_key).where(Project.id == pid)
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail=PROJECT_NOT_FOUND_DETAIL)
    return bool(row[0]), (row[1] or None)


async def _current_cluster(
    db: AsyncSession, pid: _uuid.UUID, membership_key: str,
) -> Optional[SystemicFlakeCluster]:
    """Tonight's cluster with this exact member set (newest first: the key is non-unique by design)."""
    return (
        await db.execute(
            select(SystemicFlakeCluster)
            .where(
                SystemicFlakeCluster.project_id == pid,
                SystemicFlakeCluster.membership_key == membership_key,
            )
            .order_by(SystemicFlakeCluster.computed_at.desc(), SystemicFlakeCluster.cluster_key)
            .limit(1)
        )
    ).scalars().first()


async def _members(db: AsyncSession, cluster_id: _uuid.UUID) -> list[tuple[str, Optional[str]]]:
    """``(test_fingerprint, test_name)`` of the cluster's members, by name then fingerprint."""
    rows = (
        await db.execute(
            select(SystemicFlakeClusterMember.test_fingerprint, SystemicFlakeClusterMember.test_name)
            .where(SystemicFlakeClusterMember.cluster_id == cluster_id)
            .order_by(SystemicFlakeClusterMember.test_name, SystemicFlakeClusterMember.test_fingerprint)
        )
    ).all()
    return [(str(r[0]), r[1]) for r in rows]


async def _stored_link(db: AsyncSession, pid: _uuid.UUID, membership_key: str) -> Optional[SystemicClusterEpic]:
    return (
        await db.execute(
            select(SystemicClusterEpic).where(
                SystemicClusterEpic.project_id == pid,
                SystemicClusterEpic.membership_key == membership_key,
            )
        )
    ).scalars().first()


async def _current_links(db: AsyncSession, pid: _uuid.UUID) -> list[Any]:
    """``(link, full_name, username, erased_at)`` for each epic whose member set is one of the project's
    CURRENT clusters, newest first (a link whose cluster changed members is not listed)."""
    current = exists().where(
        SystemicFlakeCluster.project_id == SystemicClusterEpic.project_id,
        SystemicFlakeCluster.membership_key == SystemicClusterEpic.membership_key,
    )
    return list(
        (
            await db.execute(
                select(SystemicClusterEpic, User.full_name, User.username, User.erased_at)
                .outerjoin(User, User.id == SystemicClusterEpic.created_by)
                .where(SystemicClusterEpic.project_id == pid, current)
                .order_by(SystemicClusterEpic.created_at.desc(), SystemicClusterEpic.id)
            )
        ).all()
    )


async def get_epic_status(db: AsyncSession, project_id: Any, *, user: Optional[User] = None) -> dict[str, Any]:
    """The cluster list's ONE status read: the opt-in, Jira's availability, whether Jira offers an Epic
    issue type, and the epics filed for the project's current clusters. Reads only. No Jira call when
    Jira is unavailable (offline / disabled / not configured) or the project has not opted in
    (``epic_type_available`` is then null); otherwise the issue types come from ``get_metadata`` and its
    5-minute cache (null again when Jira's metadata cannot be read)."""
    from app.services.subject_erasure_service import erased_display

    pid = _pid(project_id)
    enabled, _project_key = await _project_settings(db, pid)
    cfg = await jira.resolve_jira_config(db)
    reason = jira.availability_reason(cfg)
    epic_type_available: Optional[bool] = None
    if enabled and reason is None:
        metadata = await jira.get_metadata(db, str(pid), user=user)
        if metadata.get("available"):
            epic_type_available = _epic_type_name(list(metadata.get("issue_types") or [])) is not None
    links = []
    for link, full_name, username, erased_at in await _current_links(db, pid):
        resolved = full_name or username
        links.append({
            "membership_key": link.membership_key,
            "jira_key": link.jira_key,
            "jira_url": link.jira_url,
            "cluster_label": link.cluster_label,
            "created_at": link.created_at,
            "created_by_name": erased_display(erased_at=erased_at, name=resolved),
        })
    return {
        "enabled": enabled,
        "available": reason is None,
        "reason": REASON_SENTENCES.get(reason, reason) if reason else None,
        "epic_type_available": epic_type_available,
        "min_co_failure_runs": EPIC_MIN_CO_FAILURE_RUNS,
        "links": links,
    }


# ── the prefill (preview + create) ───────────────────────────────────────────


async def build_epic_prefill(
    db: AsyncSession, project_id: Any, membership_key: str, *, filer: Optional[str] = None,
) -> dict[str, Any]:
    """The epic's payload for the cluster ``membership_key`` names TODAY: the summary, the epic's own
    description as an ADF document (``adf``, sent to Jira) and as plain text (``description``, the
    preview) — one content, two renderings — and the cluster facts. ``filer`` is the username the
    closing line names. Read-only. 404 when no current cluster has this member set."""
    pid = _pid(project_id)
    cluster = await _current_cluster(db, pid, membership_key)
    if cluster is None:
        raise HTTPException(status_code=404, detail=CLUSTER_GONE_DETAIL)
    members = await _members(db, cluster.id)
    names = [name or fingerprint for fingerprint, name in members]
    parts = _epic_parts(cluster, names, filer=filer)
    return {
        "signature": epic_signature(membership_key),
        "membership_key": membership_key,
        "summary": epic_summary(cluster.label),
        "description": _epic_text(parts),
        "adf": _epic_adf(parts),
        "cluster_label": cluster.label,
        "cause_family": cluster.cause_family,
        "member_count": len(members) or int(cluster.size or 0),
        "member_fingerprints": sorted(fingerprint for fingerprint, _name in members),
        "co_failure_runs": int(cluster.co_failure_runs or 0),
        "cohesion": cluster.cohesion,
        "window_days": int(cluster.window_days or 0),
        "deep_link": parts["link"],
    }


def _require_recurring(prefill: dict[str, Any]) -> None:
    if int(prefill["co_failure_runs"]) < EPIC_MIN_CO_FAILURE_RUNS:
        raise HTTPException(status_code=422, detail=NOT_RECURRING_DETAIL)


async def preview_epic(
    db: AsyncSession, project_id: Any, membership_key: str, *, actor: Any = None,
) -> dict[str, Any]:
    """The preview route's answer (read-only): 404 gone, 422 ``not_recurring``. ``description_text`` is
    the epic description exactly as the POST would send it for this ``actor``."""
    pid = _pid(project_id)
    _enabled, project_key = await _project_settings(db, pid)
    prefill = await build_epic_prefill(db, pid, membership_key, filer=getattr(actor, "username", None))
    _require_recurring(prefill)
    cfg = await jira.resolve_jira_config(db)
    return {
        "membership_key": membership_key,
        "summary": prefill["summary"],
        "description_text": prefill["description"],
        "labels": list(EPIC_LABELS),
        "jira_project_key": project_key or cfg.get("default_project_key") or None,
        "member_count": prefill["member_count"],
        "co_failure_runs": prefill["co_failure_runs"],
        "cluster_label": prefill["cluster_label"],
    }


# ── the create (a person's POST; the router commits) ─────────────────────────


async def _require_epic_type(db: AsyncSession, pid: _uuid.UUID, actor: Any) -> str:
    """Jira's Epic issue-type name from the metadata path (cached ~5 min). 422 when Jira offers none;
    502 when Jira is configured but its issue types cannot be read."""
    metadata = await jira.get_metadata(db, str(pid), user=actor)
    if not metadata.get("available"):
        raise HTTPException(
            status_code=502, detail=f"{METADATA_UNREADABLE_DETAIL} ({metadata.get('reason') or 'unknown'}).",
        )
    name = _epic_type_name(list(metadata.get("issue_types") or []))
    if name is None:
        raise HTTPException(status_code=422, detail=NO_EPIC_TYPE_DETAIL)
    return name


def _link_result(link: SystemicClusterEpic, *, created: bool) -> dict[str, Any]:
    return {
        "membership_key": link.membership_key,
        "jira_key": link.jira_key,
        "jira_url": link.jira_url,
        "created": created,
    }


async def create_epic_for_cluster(
    db: AsyncSession,
    project_id: Any,
    actor: Any,
    membership_key: str,
    *,
    jira_project_key: Optional[str] = None,
    summary: Optional[str] = None,
    confirm_not_filed: bool = False,
) -> dict[str, Any]:
    """File ONE Jira epic for a recurring cluster, or return the one already filed. Stage-only.

    Checks, in this order: the opt-in (403), Jira's availability (503, the defect create's sentences),
    the cluster (404), recurrence (422 ``not_recurring``), an Epic issue type (422
    ``no_epic_issue_type``), a stored link (returned, ``created: false``, nothing sent). Then the shared
    filing core in the ``cluster-epic:{pid}`` namespace (409 ``jira_outcome_unknown`` /
    ``confirm_not_filed`` as the defect create), the link row and the ``cluster.epic_created`` activity.
    No ``Defect`` row. The Jira project key: the request's, else the project's, else the configured default.
    """
    pid = _pid(project_id)
    enabled, project_key = await _project_settings(db, pid)
    if not enabled:
        raise HTTPException(status_code=403, detail=OPT_IN_OFF_DETAIL)
    cfg = await jira.resolve_jira_config(db)
    reason = jira.availability_reason(cfg)
    if reason:
        jira._raise_unavailable(reason)
    prefill = await build_epic_prefill(db, pid, membership_key, filer=getattr(actor, "username", None))
    _require_recurring(prefill)
    issue_type = await _require_epic_type(db, pid, actor)
    stored = await _stored_link(db, pid, membership_key)
    if stored is not None:
        return _link_result(stored, created=False)

    if summary and summary.strip():
        prefill = {**prefill, "summary": " ".join(summary.split())[:SUMMARY_MAX]}
    filing = await jira._file_or_reuse(
        db, pid, actor, prefill, cfg,
        jira_project_key=(jira_project_key or "").strip() or project_key or cfg.get("default_project_key"),
        issue_type=issue_type,
        assignee=None,
        extra_comment=None,
        confirm_not_filed=confirm_not_filed,
        scope_prefix=EPIC_SCOPE_PREFIX,
        labels=EPIC_LABELS,
        target_type=EPIC_TARGET_TYPE,
        description=prefill["adf"],
    )
    # A concurrent request for this cluster held the signature lock inside _file_or_reuse and committed
    # its link first; this request's claim replayed that issue. Answer with the stored link.
    stored = await _stored_link(db, pid, membership_key)
    if stored is not None:
        return _link_result(stored, created=False)

    link = SystemicClusterEpic(
        project_id=pid,
        membership_key=membership_key,
        jira_key=str(filing.issue_key or ""),
        jira_url=filing.issue_url,
        cluster_label=prefill["cluster_label"],
        member_fingerprints=prefill["member_fingerprints"],
        created_by=getattr(actor, "id", None),
    )
    db.add(link)
    await db.flush()
    await record_activity(
        db,
        project_id=pid,
        event_type=EVENT_CREATED,
        actor=ActorRef.from_user(actor),
        entity_id=membership_key,
        entity_label=prefill["cluster_label"],
        entity_type="systemic_cluster",
        context={
            "ticket_key": link.jira_key,
            "member_count": prefill["member_count"],
            "co_failure_runs": prefill["co_failure_runs"],
        },
    )
    logger.info(
        "cluster_epic_filed",
        project_id=str(pid), issue_key=link.jira_key, how=filing.how, membership_key=membership_key,
    )
    return _link_result(link, created=True)
