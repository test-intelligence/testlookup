"""CODEOWNERS import + path-owner resolution (Epic 8 US-8.3 / US-8.4).

This module turns a GitHub ``CODEOWNERS`` file into ``path`` rows in the
existing ``service_ownership_rules`` table — **no new table, no migration**.
A CODEOWNERS line ``glob  @owner1 @owner2`` becomes one rule:

    match_type    = "path"
    match_pattern = <glob, gitignore→fnmatch normalised>
    service_name  = "CODEOWNERS"          ← provenance marker (see below)
    team_name     = <primary owner handle, e.g. "@org/team" or "@alice">
    team_contact  = <full space-joined owner list>
    priority      = <file order; LATER lines get HIGHER priority>

Provenance
----------
Imported rows are tagged with ``service_name == "CODEOWNERS"`` (the ``created_by``
column is a ``users.id`` FK and can't hold a sentinel string). A re-import
deletes exactly the rows carrying that marker for the project and re-inserts —
so hand-authored rules (any other ``service_name``) survive untouched.

Last-match-wins
---------------
GitHub's CODEOWNERS resolves to the **last** matching line. The ownership
resolver (``ownership_resolver_service.resolve_test_ownership``) iterates rules
in ``priority DESC`` order and returns the first match, so encoding later file
lines as higher priorities reproduces GitHub's semantics exactly.

Acquisition paths
-----------------
* **fetch** — pull the file over the GitHub Contents API, reusing the
  ``github_checks_service`` connector (PAT via ``secret_service``, SSRF egress
  guard, ``async_retry``, ``AI_OFFLINE_MODE`` hard gate + ``github_checks``
  feature flag). Never raises.
* **text** — the raw CODEOWNERS body posted in the request (air-gapped /
  paste-upload). No egress.

Transaction discipline: the import helper ``db.add`` / ``db.delete`` / ``flush``
and returns; the **router owns the single commit**.
"""
from __future__ import annotations

import base64
import fnmatch
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx
import structlog
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import (
    ServiceOwnershipRule,
    TestCase,
    TestRun,
    User,
)

logger = structlog.get_logger(__name__)

# Provenance marker written to ``service_name`` on every imported row. A
# re-import scopes its "replace" to exactly these rows.
CODEOWNERS_SERVICE = "CODEOWNERS"

# Candidate paths GitHub honours for a CODEOWNERS file, in resolution order.
_CODEOWNERS_PATHS = (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS")

# Column caps mirror the ORM (``match_pattern`` String(500), ``team_name``
# String(255), ``team_contact`` String(500)).
_PATTERN_CAP = 500
_TEAM_NAME_CAP = 255
_TEAM_CONTACT_CAP = 500


# ── Parser ──────────────────────────────────────────────────────────────────


@dataclass
class CodeownersEntry:
    """One effective CODEOWNERS line: the original pattern, the fnmatch-ready
    glob, and its owner tokens (in file order; first is the primary owner)."""

    pattern: str
    glob: str
    owners: list[str] = field(default_factory=list)


def _pattern_to_glob(pattern: str) -> str:
    """Normalise a gitignore-style CODEOWNERS pattern to an ``fnmatch`` glob
    matched against a repo-relative path.

    * A leading ``/`` anchors to the repo root — the paths we match are
      already repo-relative, so we strip it.
    * A trailing ``/`` means "everything under this directory" — append ``**``.
    * Python ``fnmatch`` treats ``*`` as spanning ``/`` (it has no path-segment
      semantics), so a bare ``*.py`` already matches ``src/a.py`` and
      ``src/api/**`` matches nested files — no further rewriting needed.
    """
    p = (pattern or "").strip()
    if p.startswith("/"):
        p = p[1:]
    if p.endswith("/"):
        p = p + "**"
    return p


def _looks_like_owner(token: str) -> bool:
    """A CODEOWNERS owner is ``@user`` / ``@org/team`` or a bare email."""
    if token.startswith("@") and len(token) > 1:
        return True
    return "@" in token and "." in token.split("@")[-1]


def parse_codeowners(text: str) -> list[CodeownersEntry]:
    """Parse a CODEOWNERS body into ordered entries.

    * ``#`` comments and blank lines are ignored (a ``#`` beginning a line is a
      comment; inline trailing comments are NOT stripped — GitHub treats ``#``
      inside a line literally, and so do we).
    * Each remaining line is ``pattern owner...``. A line with a pattern but
      **no owners** clears ownership in GitHub; we can't represent "no owner"
      as a rule, so such lines are dropped (they never assign anyone).
    * Order is preserved so the caller can encode last-match-wins via priority.
    """
    entries: list[CodeownersEntry] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        pattern = parts[0]
        owners = [tok for tok in parts[1:] if _looks_like_owner(tok)]
        if not owners:
            # Pattern with no valid owner token → nothing to assign.
            continue
        entries.append(
            CodeownersEntry(
                pattern=pattern,
                glob=_pattern_to_glob(pattern),
                owners=owners,
            )
        )
    return entries


# ── Import (idempotent, CODEOWNERS-scoped replace) ──────────────────────────


async def import_codeowners_rules(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    text: str,
    actor_id: Optional[uuid.UUID],
) -> dict[str, Any]:
    """Replace the project's CODEOWNERS-sourced ``path`` rules from ``text``.

    Deletes only rows tagged ``service_name == "CODEOWNERS"`` (hand-authored
    rules survive), then inserts one rule per parsed entry. ``priority`` is the
    file-order index so later lines win (GitHub last-match-wins).

    Stages the writes and returns a summary; the caller commits.
    """
    entries = parse_codeowners(text)

    # Scope the replace to CODEOWNERS-sourced rows for this project only.
    existing = await db.execute(
        select(ServiceOwnershipRule).where(
            ServiceOwnershipRule.project_id == project_id,
            ServiceOwnershipRule.service_name == CODEOWNERS_SERVICE,
        )
    )
    replaced = 0
    for old in existing.scalars().all():
        await db.delete(old)
        replaced += 1

    created = 0
    for idx, entry in enumerate(entries):
        primary = entry.owners[0]
        rule = ServiceOwnershipRule(
            project_id=project_id,
            match_type="path",
            match_pattern=entry.glob[:_PATTERN_CAP],
            service_name=CODEOWNERS_SERVICE,
            team_name=primary[:_TEAM_NAME_CAP],
            team_contact=(" ".join(entry.owners))[:_TEAM_CONTACT_CAP],
            # Later file lines → higher priority → win under the resolver's
            # priority-DESC iteration (last-match-wins).
            priority=idx,
            created_by=actor_id,
        )
        db.add(rule)
        created += 1

    await db.flush()

    logger.info(
        "codeowners_imported",
        project_id=str(project_id),
        parsed_entries=len(entries),
        rules_created=created,
        rules_replaced=replaced,
    )
    return {
        "imported": len(entries),
        "rules_created": created,
        "rules_replaced": replaced,
        "coverage": {"path_rules": created},
    }


# ── Fetch over the GitHub Contents API ──────────────────────────────────────


async def fetch_codeowners_text(
    db: AsyncSession, project_id: uuid.UUID,
) -> tuple[Optional[str], str]:
    """Fetch the repo's CODEOWNERS file. Returns ``(text | None, detail)``.

    Reuses ``github_checks_service`` for the connector plumbing: the compound
    ``AI_OFFLINE_MODE`` + ``github_checks`` gate, the per-project integration
    row, the PAT from ``secret_service``, the SSRF egress guard, and
    ``async_retry``. Never raises — every failure returns ``(None, reason)`` so
    the endpoint can 422/409 cleanly instead of 500ing.
    """
    from app.services import github_checks_service as gh

    if not await gh._post_allowed(db):
        return None, "offline_or_flag_off"

    integration = await gh.get_integration(db, project_id)
    if integration is None or not integration.enabled:
        return None, "integration_disabled_or_missing"

    from app.services import secret_service

    pat = await secret_service.read_secret(
        db, gh.SECRET_SCOPE, gh._secret_key(project_id),
    )
    if not pat:
        return None, "no_pat_configured"

    api_base = integration.api_base_url.rstrip("/")
    headers = {
        "Authorization": f"Bearer {pat}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "TestLookup/1.0",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    from app.services.resilience import async_retry

    last_detail = "not_found"
    for rel_path in _CODEOWNERS_PATHS:
        url = (
            f"{api_base}/repos/{integration.repo_owner}/"
            f"{integration.repo_name}/contents/{rel_path}"
        )
        block = await gh._ssrf_block_reason(url)
        if block:
            return None, f"blocked_unsafe_target:{block}"

        async def _do_get(target: str = url) -> httpx.Response:
            async with httpx.AsyncClient(timeout=15.0) as client:
                return await client.get(target, headers=headers)

        try:
            resp: httpx.Response = await async_retry(
                _do_get,
                max_retries=3,
                base_delay=1.0,
                operation_name="codeowners_fetch",
            )
        except Exception as exc:  # noqa: BLE001 — never raise to the endpoint
            logger.warning(
                "codeowners_fetch_failed",
                project_id=str(project_id),
                path=rel_path,
                error=str(exc),
            )
            last_detail = f"error:{type(exc).__name__}"
            continue

        if resp.status_code == 404:
            last_detail = "not_found"
            continue
        if resp.status_code != 200:
            last_detail = f"http_{resp.status_code}"
            continue

        body = resp.json() if resp.content else {}
        content_b64 = body.get("content") or ""
        try:
            decoded = base64.b64decode(content_b64).decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            last_detail = "decode_error"
            continue
        logger.info(
            "codeowners_fetched",
            project_id=str(project_id),
            path=rel_path,
            bytes=len(decoded),
        )
        return decoded, f"fetched:{rel_path}"

    return None, last_detail


# ── Path-owner resolution (US-8.4) ──────────────────────────────────────────
#
# Shared by the assignment service (assign time) and the /my-failures inbox
# (read-time reason derivation). Kept here so the two surfaces agree on which
# rule matches a path and how an owner handle resolves to a User.


async def load_path_rules(
    db: AsyncSession, project_id: uuid.UUID,
) -> list[ServiceOwnershipRule]:
    """Active ``path`` ownership rules for the project, priority DESC (so a
    first-match iteration honours priority / last-match-wins)."""
    result = await db.execute(
        select(ServiceOwnershipRule)
        .where(
            ServiceOwnershipRule.project_id == project_id,
            ServiceOwnershipRule.match_type == "path",
            ServiceOwnershipRule.is_active.is_(True),
        )
        .order_by(
            ServiceOwnershipRule.priority.desc(),
            ServiceOwnershipRule.created_at,
        )
    )
    return list(result.scalars().all())


def match_path_rule(
    path: str, rules: list[ServiceOwnershipRule],
) -> Optional[ServiceOwnershipRule]:
    """First rule whose glob matches ``path`` (rules pre-sorted priority DESC).

    Uses the same ``fnmatch`` matching as ``ownership_resolver_service`` so the
    inbox reason and the assignment never disagree.
    """
    if not path:
        return None
    target = path.lower()
    for rule in rules:
        if fnmatch.fnmatch(target, (rule.match_pattern or "").lower()):
            return rule
    return None


def owner_handle_for_rule(rule: ServiceOwnershipRule) -> Optional[str]:
    """The single-user owner handle a rule resolves to, or ``None``.

    ``@org/team`` team handles have no single user → ``None`` (the caller falls
    through to the next precedence; we never assign to a stranger). ``@alice``
    and bare-email owners resolve; the leading ``@`` is stripped.
    """
    handle = (rule.team_name or "").strip()
    if handle.startswith("@"):
        handle = handle[1:]
    if not handle or "/" in handle:
        return None
    return handle


async def resolve_handles_to_users(
    db: AsyncSession, handles: set[str],
) -> dict[str, uuid.UUID]:
    """Map ``@handle``/email → ``users.id`` by ``username`` or ``email``
    (case-insensitive). Keyed by the lowercased handle. Unmatched handles are
    simply absent — the caller treats absence as "unresolvable → skip"."""
    lowered = {h.lower() for h in handles if h}
    if not lowered:
        return {}
    rows = (
        await db.execute(
            select(User.id, User.username, User.email).where(
                or_(
                    func.lower(User.username).in_(lowered),
                    func.lower(User.email).in_(lowered),
                )
            )
        )
    ).all()
    mapping: dict[str, uuid.UUID] = {}
    for r in rows:
        if r.username and r.username.lower() in lowered:
            mapping.setdefault(r.username.lower(), r.id)
        if r.email and r.email.lower() in lowered:
            mapping.setdefault(r.email.lower(), r.id)
    return mapping


def assignment_reason(rule: ServiceOwnershipRule) -> str:
    """Compact human reason for the inbox, e.g. ``via CODEOWNERS: src/api/**``.
    Non-CODEOWNERS path rules read ``via path rule: <pattern>``."""
    source = (
        "CODEOWNERS" if rule.service_name == CODEOWNERS_SERVICE else "path rule"
    )
    return f"via {source}: {rule.match_pattern}"


def locate_failure_path(
    stack_trace: Optional[str], error_message: Optional[str],
) -> Optional[str]:
    """Repo-relative failure path from a stack trace / error message, or
    ``None``. Reuses ``github_checks_service.locate_in_trace`` (Python + JS/TS;
    Java deliberately unlocated) — a non-locatable failure has no path and must
    fall through to the QA-lead pool."""
    from app.services.github_checks_service import locate_in_trace

    loc = locate_in_trace(stack_trace) or locate_in_trace(error_message)
    return loc[0] if loc else None


async def codeowners_reasons_for_rows(
    db: AsyncSession, rows: list[Any],
) -> dict[uuid.UUID, str]:
    """Best-effort read-time reason map for inbox rows.

    ``rows`` need ``.id``, ``.project_id``, ``.assigned_to_user_id``,
    ``.stack_trace``, ``.error_message``. A row gets a reason only when its
    located path matches a path rule whose resolved owner **equals the row's
    current assignee** — i.e. the row was (or would have been) assigned via
    path ownership. Rows spanning multiple projects are grouped so rules load
    once per project. Never raises.
    """
    reasons: dict[uuid.UUID, str] = {}
    by_project: dict[uuid.UUID, list[Any]] = {}
    for row in rows:
        pid = getattr(row, "project_id", None)
        if pid is None or getattr(row, "assigned_to_user_id", None) is None:
            continue
        by_project.setdefault(pid, []).append(row)

    for pid, prows in by_project.items():
        try:
            path_rules = await load_path_rules(db, pid)
            if not path_rules:
                continue
            handles = {
                h
                for h in (owner_handle_for_rule(r) for r in path_rules)
                if h
            }
            handle_map = await resolve_handles_to_users(db, handles)
            for row in prows:
                path = locate_failure_path(
                    getattr(row, "stack_trace", None),
                    getattr(row, "error_message", None),
                )
                if not path:
                    continue
                rule = match_path_rule(path, path_rules)
                if rule is None:
                    continue
                handle = owner_handle_for_rule(rule)
                if not handle:
                    continue
                uid = handle_map.get(handle.lower())
                if uid is not None and uid == row.assigned_to_user_id:
                    reasons[row.id] = assignment_reason(rule)
        except Exception as exc:  # noqa: BLE001 — inbox must not 500 on this
            logger.debug(
                "codeowners_reason_derivation_failed",
                project_id=str(pid),
                error=str(exc),
            )
            continue
    return reasons


# ── Coverage (US-8.3) ───────────────────────────────────────────────────────


async def compute_coverage(
    db: AsyncSession,
    project_id: uuid.UUID,
    *,
    lookback_days: int = 30,
    sample_cap: int = 500,
) -> dict[str, Any]:
    """Fraction of recent failing-test locations covered by a ``path`` rule.

    Samples the most recent ``sample_cap`` FAILED/BROKEN test cases within
    ``lookback_days``, derives each one's repo-relative path, and counts how
    many match some active path rule. ``coverage_pct`` is over the *locatable*
    failures (Java / non-locatable failures have no path to cover and would
    otherwise drag the number down misleadingly)."""
    path_rules = await load_path_rules(db, project_id)
    codeowners_rules = sum(
        1 for r in path_rules if r.service_name == CODEOWNERS_SERVICE
    )
    summary = {
        "path_rules": len(path_rules),
        "codeowners_rules": codeowners_rules,
        "sampled": 0,
        "located": 0,
        "matched": 0,
        "coverage_pct": 0.0,
        "lookback_days": lookback_days,
    }
    if not path_rules:
        return summary

    period_start = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    rows = (
        await db.execute(
            select(TestCase.stack_trace, TestCase.error_message)
            .join(TestRun, TestRun.id == TestCase.test_run_id)
            .where(
                TestRun.project_id == project_id,
                TestCase.status.in_(("FAILED", "BROKEN")),
                TestCase.created_at >= period_start,
            )
            .order_by(TestCase.created_at.desc())
            .limit(sample_cap)
        )
    ).all()

    located = 0
    matched = 0
    for r in rows:
        path = locate_failure_path(r.stack_trace, r.error_message)
        if not path:
            continue
        located += 1
        if match_path_rule(path, path_rules) is not None:
            matched += 1

    summary["sampled"] = len(rows)
    summary["located"] = located
    summary["matched"] = matched
    summary["coverage_pct"] = round((matched / located) * 100, 1) if located else 0.0
    return summary
