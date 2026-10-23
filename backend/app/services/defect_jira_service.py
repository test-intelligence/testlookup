"""
One-click Jira defect creation + status sync-back (PMF US-6.1 / US-6.2 / US-6.3).

Three surfaces:

* **Prefill** (``build_prefill``) — assembles a Jira-ready summary /
  description server-side from a failure signature (test fingerprint or
  failure-cluster id): failure message + truncated stack trace, occurrence
  history (first/last seen, failing-run count), branch/build context, a
  deep link back to TestLookup, and — when an AI analysis exists for the
  fingerprint (US-2.4 lookup) — a clearly-labelled "Suggested root cause"
  block.

* **Create-or-link** (``create_or_link_issue``) — dedup-first: if an OPEN
  defect already linked to Jira exists for the same
  ``signature_fingerprint``, no new issue is filed — the existing issue
  gets a "recurred in build X" comment (best-effort) and the caller gets
  ``deduplicated=true`` with the existing link. Otherwise the issue is
  created via the Jira REST v3 API using the effective connector config
  (AppSetting overrides → secret service → env settings) and recorded on
  a Defect row (bidirectional link). ``target="webhook"`` emits the
  ``defect.create_requested`` outbound-webhook event with the same
  payload instead of calling Jira (US-6.3).

* **Sync-back** (``sync_external_statuses``) — called from the 15-minute
  Celery beat: mirrors the Jira status of linked, still-OPEN defects onto
  ``jira_status`` / ``external_status_at`` (capped per cycle to respect
  rate limits) and raises ``external_status_conflict`` when Jira reports a
  Done-category status while the signature still produced failures in the
  recent window ("closed in Jira but still failing").

* **Triage + approval (DEF-01 / DEF-06)** — ``get_issue`` validates a key a
  person links, ``transition_issue`` writes a status change back (idempotent
  by state), ``jira_browse_url`` is the only builder of a typed key's link,
  and ``file_issue_for_defect`` files the ticket ON an approved defect through
  the same filing core (``_file_or_reuse``) and claim namespace as the
  one-click create, so the two paths never file one failure twice.

Transaction discipline: every function stages mutations on the injected
session and lets the caller commit — the router handler for the create
path, the Celery task for the sync path. ``AI_OFFLINE_MODE`` is the hard
kill switch above ``JIRA_ENABLED`` (same contract as
``defect_promotion_service``).
"""
from __future__ import annotations

import base64
import hashlib
import re
import time
import uuid as _uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, NoReturn, Optional

import httpx
import structlog
from fastapi import HTTPException
from sqlalchemy import func as sa_func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.http_client import get_http_client
from app.models.postgres import (
    AccessAuditLog,
    AIAnalysis,
    AppSetting,
    Defect,
    FailureCluster,
    TestCase,
    TestRun,
    User,
)
from app.services import tool_call_idempotency as tci
from app.services.defect_status import ACTIVE_STATUSES, parse_jira_key
# W10 INT-01: the ONE precedence rule (a saved None / "" hands a key back to
# the environment; a saved boolean is a value).
from app.services.integration_config_service import effective_value

logger = structlog.get_logger("services.defect_jira")

# Failure statuses that count as an occurrence of the signature.
_FAILING_STATUSES = ("FAILED", "BROKEN")

# US-6.2: how far back "still failing" looks when deciding a conflict.
CONFLICT_LOOKBACK_DAYS = 7

# US-6.2: max linked defects refreshed per sync cycle (rate-limit respect).
SYNC_CAP_PER_CYCLE = 50

# Jira statusCategory keys that mean "closed" on the Jira side.
_DONE_CATEGORY_KEYS = {"done"}

# ── Effective connector config ───────────────────────────────────────────────

_INTEGRATIONS_KEY = "integrations_config"


async def resolve_jira_config(db: AsyncSession) -> dict[str, Any]:
    """Effective Jira connector config.

    Mirrors the resolution order of ``app_settings._load_integrations_config``:
    AppSetting overrides win over env settings; the API token additionally
    checks the secret service (Settings-UI-stored tokens live there, the
    stored AppSetting value is stripped of secrets).

    W10 INT-01: each text / boolean key goes through
    ``integration_config_service.effective_value``, so a saved ``None`` or an
    empty string no longer beats the environment. Same two statements, same
    keys.
    """
    result = await db.execute(
        select(AppSetting).where(AppSetting.key == _INTEGRATIONS_KEY)
    )
    row = result.scalar_one_or_none()
    overrides = dict(row.value) if row is not None and row.value else {}

    token: Optional[str] = None
    try:
        from app.services.secret_service import read_secret

        token = await read_secret(db, _INTEGRATIONS_KEY, "jira_api_token")
    except Exception as exc:  # pragma: no cover — secret store optional in tests
        logger.debug("jira_token_secret_lookup_failed", error=str(exc))

    return {
        "enabled": effective_value(overrides, "jira_enabled", settings.JIRA_ENABLED)[0],
        "domain": effective_value(overrides, "jira_domain", settings.JIRA_DOMAIN)[0],
        "email": effective_value(overrides, "jira_email", settings.JIRA_EMAIL)[0],
        "api_token": token or overrides.get("jira_api_token") or settings.JIRA_API_TOKEN,
        "default_project_key": effective_value(
            overrides, "jira_default_project_key", settings.JIRA_DEFAULT_PROJECT_KEY
        )[0],
    }


def availability_reason(cfg: dict[str, Any]) -> Optional[str]:
    """Why outbound Jira calls are blocked right now (``None`` = allowed).

    ``AI_OFFLINE_MODE`` is the hard kill switch above the integration flag —
    an air-gapped deployment never egresses traffic (same contract as
    ``defect_promotion_service`` / ``webhook_service``).
    """
    if settings.AI_OFFLINE_MODE:
        return "offline_mode"
    if not cfg.get("enabled"):
        return "disabled"
    if not (cfg.get("domain") and cfg.get("email") and cfg.get("api_token")):
        return "not_configured"
    return None


_REASON_DETAIL = {
    "offline_mode": (
        "AI_OFFLINE_MODE is enabled — outbound Jira calls are blocked. "
        "Disable offline mode or use target=\"webhook\" to hand the payload "
        "to your own automation."
    ),
    "disabled": (
        "The Jira integration is disabled (jira_enabled=false). Enable it "
        "under Settings → Integrations, or use target=\"webhook\"."
    ),
    "not_configured": (
        "Jira credentials are incomplete — set the domain, email and API "
        "token under Settings → Integrations, or use target=\"webhook\"."
    ),
}


def _raise_unavailable(reason: str) -> None:
    raise HTTPException(status_code=503, detail=_REASON_DETAIL.get(reason, reason))


# ── Jira HTTP helpers ────────────────────────────────────────────────────────


def _auth_header(cfg: dict[str, Any]) -> str:
    raw = f"{cfg['email']}:{cfg['api_token']}"
    return f"Basic {base64.b64encode(raw.encode()).decode()}"


def _headers(cfg: dict[str, Any]) -> dict[str, str]:
    return {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": _auth_header(cfg),
    }


def _base_url(cfg: dict[str, Any]) -> str:
    return f"https://{cfg['domain']}"


async def _jira_get(cfg: dict[str, Any], path: str, params: Optional[dict] = None):
    client = get_http_client()
    return await client.get(
        f"{_base_url(cfg)}{path}", headers=_headers(cfg), params=params, timeout=15.0,
    )


async def _jira_post(cfg: dict[str, Any], path: str, json: dict):
    client = get_http_client()
    return await client.post(
        f"{_base_url(cfg)}{path}", headers=_headers(cfg), json=json, timeout=15.0,
    )


# ── Signature helpers ────────────────────────────────────────────────────────


def cluster_signature(label: str) -> str:
    """Stable 64-hex signature for a cluster label (fingerprint-shaped so it
    shares the ``signature_fingerprint`` column with test fingerprints)."""
    return hashlib.sha256(f"cluster::{label}".encode("utf-8", errors="ignore")).hexdigest()[:64]


# ── Prefill assembly (US-6.1 step 1) ─────────────────────────────────────────


async def build_prefill(
    db: AsyncSession,
    project_id: str,
    *,
    fingerprint: Optional[str] = None,
    cluster_id: Optional[str] = None,
) -> dict[str, Any]:
    """Assemble the pre-filled Jira payload for a failure signature.

    Read-only — used by the preview endpoint to populate the dialog and by
    the create path as the single source of the payload. Raises 404 when
    the signature matches nothing in the project.
    """
    if not fingerprint and not cluster_id:
        raise HTTPException(
            status_code=422, detail="Provide either 'fingerprint' or 'cluster_id'.",
        )

    pid = _uuid.UUID(str(project_id))

    test_name: Optional[str] = None
    suite_name: Optional[str] = None
    error_message: Optional[str] = None
    stack_trace: Optional[str] = None
    member_count: Optional[int] = None
    latest_run: Optional[TestRun] = None
    signature: str
    label: str

    if fingerprint:
        signature = fingerprint
        row = (
            await db.execute(
                select(TestCase, TestRun)
                .join(TestRun, TestCase.test_run_id == TestRun.id)
                .where(
                    TestRun.project_id == pid,
                    TestCase.test_fingerprint == fingerprint,
                    TestCase.status.in_(_FAILING_STATUSES),
                )
                .order_by(TestCase.created_at.desc())
                .limit(1)
            )
        ).first()
        if row is None:
            raise HTTPException(
                status_code=404,
                detail="No failing test found for this fingerprint in the project.",
            )
        tc, latest_run = row
        test_name = tc.test_name
        suite_name = tc.suite_name
        error_message = tc.error_message
        stack_trace = tc.stack_trace or tc.error_message
        label = tc.test_name
    else:
        cluster_row = (
            await db.execute(
                select(FailureCluster, TestRun)
                .join(TestRun, FailureCluster.test_run_id == TestRun.id)
                .where(
                    TestRun.project_id == pid,
                    FailureCluster.cluster_id == cluster_id,
                )
                .order_by(FailureCluster.created_at.desc())
                .limit(1)
            )
        ).first()
        if cluster_row is None:
            raise HTTPException(
                status_code=404, detail="Failure cluster not found in the project.",
            )
        cluster, latest_run = cluster_row
        label = cluster.label
        signature = cluster_signature(cluster.label)
        error_message = cluster.representative_error
        stack_trace = cluster.representative_error
        member_count = cluster.size

    # Occurrence history — failing executions of the signature across runs.
    first_seen = last_seen = None
    failing_runs = 0
    if fingerprint:
        occ = (
            await db.execute(
                select(
                    sa_func.count(sa_func.distinct(TestCase.test_run_id)),
                    sa_func.min(TestCase.created_at),
                    sa_func.max(TestCase.created_at),
                )
                .join(TestRun, TestCase.test_run_id == TestRun.id)
                .where(
                    TestRun.project_id == pid,
                    TestCase.test_fingerprint == fingerprint,
                    TestCase.status.in_(_FAILING_STATUSES),
                )
            )
        ).first()
        if occ is not None:
            failing_runs = int(occ[0] or 0)
            first_seen = occ[1]
            last_seen = occ[2]
    else:
        failing_runs = 1
        first_seen = last_seen = getattr(latest_run, "created_at", None)

    # AI analysis (US-2.4 lookup reuse) — fingerprint identity only.
    ai_block: Optional[dict[str, Any]] = None
    if fingerprint:
        try:
            from app.services.feedback_service import latest_analysis_for_fingerprint

            found = await latest_analysis_for_fingerprint(db, pid, fingerprint)
        except Exception as exc:
            logger.debug("jira_prefill_analysis_lookup_failed", error=str(exc))
            found = None
        if found and found.get("analysis_id"):
            analysis = (
                await db.execute(
                    select(AIAnalysis).where(AIAnalysis.id == found["analysis_id"])
                )
            ).scalar_one_or_none()
            if analysis is not None and analysis.root_cause_summary:
                ai_block = {
                    "root_cause": analysis.root_cause_summary,
                    "confidence": analysis.confidence_score,
                    "failure_category": found.get("failure_category"),
                }

    branch = getattr(latest_run, "branch", None)
    build_number = getattr(latest_run, "build_number", None)
    ci_run_url = getattr(latest_run, "ci_run_url", None)
    run_id = str(latest_run.id) if latest_run is not None else None

    deep_link = (
        f"{settings.public_base_url}/runs/{run_id}" if run_id
        else f"{settings.public_base_url}/failures"
    )

    # One line: Jira refuses a summary with a newline ("The summary is invalid
    # because it contains newline characters"), and a parametrized test id or
    # a cluster label can carry one (found against the contract fake).
    summary = " ".join(f"[TestLookup] {label}".split())[:255]
    description = _build_description_text(
        label=label,
        suite_name=suite_name,
        error_message=error_message,
        stack_trace=stack_trace,
        first_seen=first_seen,
        last_seen=last_seen,
        failing_runs=failing_runs,
        member_count=member_count,
        branch=branch,
        build_number=build_number,
        ci_run_url=ci_run_url,
        deep_link=deep_link,
        ai_block=ai_block,
    )

    existing = await find_open_linked_defect(db, pid, signature)

    return {
        "signature": signature,
        "summary": summary,
        "description": description,
        "test_name": test_name,
        "suite_name": suite_name,
        "cluster_id": cluster_id,
        "error_message": (error_message or "")[:2000] or None,
        "occurrences": {
            "first_seen": first_seen,
            "last_seen": last_seen,
            "failing_runs": failing_runs,
        },
        "context": {
            "branch": branch,
            "build_number": build_number,
            "ci_run_url": ci_run_url,
        },
        "ai_analysis": ai_block,
        "deep_link": deep_link,
        "latest_run_id": run_id,
        "existing_defect": (
            {
                "defect_id": str(existing.id),
                "jira_key": existing.jira_ticket_id,
                "jira_url": existing.jira_ticket_url,
                "external_status": existing.jira_status,
            }
            if existing is not None
            else None
        ),
    }


def _fmt_dt(value: Any) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M UTC")
    return str(value) if value else "unknown"


def _build_description_text(
    *,
    label: str,
    suite_name: Optional[str],
    error_message: Optional[str],
    stack_trace: Optional[str],
    first_seen: Any,
    last_seen: Any,
    failing_runs: int,
    member_count: Optional[int],
    branch: Optional[str],
    build_number: Optional[str],
    ci_run_url: Optional[str],
    deep_link: str,
    ai_block: Optional[dict[str, Any]],
) -> str:
    """Plain-text description — shown verbatim in the dialog preview and
    converted section-by-section to ADF at create time."""
    parts: list[str] = [f"Automated defect filed from TestLookup for: {label}"]
    if suite_name:
        parts.append(f"Suite: {suite_name}")
    if member_count:
        parts.append(f"Failure cluster of {member_count} related test failures.")

    parts.append("")
    parts.append("Occurrence history:")
    parts.append(f"- First seen: {_fmt_dt(first_seen)}")
    parts.append(f"- Last seen: {_fmt_dt(last_seen)}")
    parts.append(f"- Failing runs: {failing_runs}")

    context_bits = []
    if branch:
        context_bits.append(f"branch {branch}")
    if build_number:
        context_bits.append(f"build {build_number}")
    if context_bits or ci_run_url:
        parts.append("")
        parts.append("Environment / CI context: " + (", ".join(context_bits) or "n/a"))
        if ci_run_url:
            parts.append(f"CI run: {ci_run_url}")

    if error_message:
        parts.append("")
        parts.append("Failure message:")
        parts.append(error_message[:1000])

    if stack_trace and stack_trace != error_message:
        parts.append("")
        parts.append("Stack trace (truncated):")
        parts.append(stack_trace[:3000])

    if ai_block:
        confidence = ai_block.get("confidence")
        conf_label = f"{confidence}%" if confidence is not None else "unknown"
        parts.append("")
        parts.append(
            f"Suggested root cause (AI, confidence {conf_label}): "
            f"{ai_block['root_cause']}"
        )

    parts.append("")
    parts.append(f"View in TestLookup: {deep_link}")
    return "\n".join(parts)


def _adf_from_prefill(prefill: dict[str, Any], extra_comment: Optional[str]) -> dict:
    """Atlassian Document Format description built from the prefill parts."""
    occurrences = prefill["occurrences"]
    context = prefill["context"]
    content: list[dict] = [
        {"type": "paragraph", "content": [{"type": "text", "text": (
            f"Automated defect filed from TestLookup for: {prefill['summary'].removeprefix('[TestLookup] ')}"
        )}]},
        {"type": "heading", "attrs": {"level": 3},
         "content": [{"type": "text", "text": "Occurrence history"}]},
        {"type": "paragraph", "content": [{"type": "text", "text": (
            f"First seen: {_fmt_dt(occurrences['first_seen'])} · "
            f"Last seen: {_fmt_dt(occurrences['last_seen'])} · "
            f"Failing runs: {occurrences['failing_runs']}"
        )}]},
    ]

    context_bits = []
    if context.get("branch"):
        context_bits.append(f"branch {context['branch']}")
    if context.get("build_number"):
        context_bits.append(f"build {context['build_number']}")
    if context.get("ci_run_url"):
        context_bits.append(str(context["ci_run_url"]))
    if context_bits:
        content.append({"type": "paragraph", "content": [
            {"type": "text", "text": "Context: " + ", ".join(context_bits)},
        ]})

    if prefill.get("error_message"):
        content.append({"type": "heading", "attrs": {"level": 3},
                        "content": [{"type": "text", "text": "Failure message"}]})
        content.append({
            "type": "codeBlock", "attrs": {"language": "text"},
            "content": [{"type": "text", "text": str(prefill["error_message"])[:3000]}],
        })

    if prefill.get("ai_analysis"):
        ai = prefill["ai_analysis"]
        confidence = ai.get("confidence")
        conf_label = f"{confidence}%" if confidence is not None else "unknown"
        content.append({
            "type": "panel", "attrs": {"panelType": "info"},
            "content": [{"type": "paragraph", "content": [
                {"type": "text", "text": f"Suggested root cause (AI, confidence {conf_label}): ",
                 "marks": [{"type": "strong"}]},
                {"type": "text", "text": str(ai["root_cause"])[:1500]},
            ]}],
        })

    if extra_comment:
        content.append({"type": "paragraph", "content": [
            {"type": "text", "text": extra_comment[:1500]},
        ]})

    content.append({"type": "paragraph", "content": [
        {"type": "text", "text": "View in TestLookup",
         "marks": [{"type": "link", "attrs": {"href": prefill["deep_link"]}}]},
    ]})
    return {"type": "doc", "version": 1, "content": content}


# ── Dedup (US-6.1 step 2) ────────────────────────────────────────────────────


async def find_open_linked_defect(
    db: AsyncSession, project_id: _uuid.UUID, signature: str,
) -> Optional[Defect]:
    """Open defect already linked to a Jira issue for this signature."""
    result = await db.execute(
        select(Defect)
        .where(
            Defect.project_id == project_id,
            Defect.signature_fingerprint == signature,
            Defect.resolution_status.in_(ACTIVE_STATUSES),
            Defect.jira_ticket_id.isnot(None),
        )
        .order_by(Defect.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _post_recurrence_comment(
    cfg: dict[str, Any], issue_key: str, prefill: dict[str, Any], recurrence_count: int,
) -> bool:
    """Best-effort "recurred in build X" comment on the existing issue."""
    build = prefill["context"].get("build_number") or "unknown"
    body = {
        "body": {
            "type": "doc",
            "version": 1,
            "content": [
                {"type": "paragraph", "content": [
                    {"type": "text",
                     "text": (
                         f"TestLookup: this failure recurred in build {build} "
                         f"(recurrence #{recurrence_count}). "
                     )},
                    {"type": "text", "text": "View in TestLookup",
                     "marks": [{"type": "link",
                                "attrs": {"href": prefill["deep_link"]}}]},
                ]},
            ],
        }
    }
    try:
        resp = await _jira_post(cfg, f"/rest/api/3/issue/{issue_key}/comment", body)
        if resp.status_code >= 400:
            logger.warning(
                "jira_recurrence_comment_failed",
                issue_key=issue_key, status_code=resp.status_code,
            )
            return False
        return True
    except Exception as exc:
        logger.warning(
            "jira_recurrence_comment_error", issue_key=issue_key, error=str(exc),
        )
        return False


# ── Exactly-once filing ──────────────────────────────────────────────────────

# Transport errors raised before the request left this process. Jira cannot
# have created anything, so the claim is recorded failed and may be retried.
# Every other error (read timeout, dropped connection) may follow a create.
_NOT_SENT_ERRORS = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)

_LABEL_PREFIX = "testlookup-sig-"


async def _lock_signature(db: AsyncSession, project_id: _uuid.UUID, signature: str) -> None:
    """Serialize one-click creates for (project, signature) until the caller's
    transaction ends. The router's commit (or the rollback on error) releases it."""
    digest = hashlib.sha256(f"one-click-jira\x1f{project_id}\x1f{signature}".encode()).digest()
    lock_id = int.from_bytes(digest[:8], "big", signed=True)
    await db.execute(text("SELECT pg_advisory_xact_lock(:lock_id)"), {"lock_id": lock_id})


async def _signature_generation(
    db: AsyncSession, project_id: _uuid.UUID, signature: str,
) -> str:
    """Which filing of this signature a create belongs to.

    The newest closed linked defect: once it closes, a recurring failure is a
    new filing with a new claim, rather than a replay of the closed issue.
    """
    row = (
        await db.execute(
            select(Defect.id)
            .where(
                Defect.project_id == project_id,
                Defect.signature_fingerprint == signature,
                Defect.resolution_status.notin_(ACTIVE_STATUSES),
                Defect.jira_ticket_id.isnot(None),
            )
            .order_by(Defect.created_at.desc())
            .limit(1)
        )
    ).first()
    return str(row[0]) if row is not None else "first"


def _signature_label(claim_key: str) -> str:
    """Jira label that finds the issue a claim filed, when its answer was lost."""
    return f"{_LABEL_PREFIX}{hashlib.sha256(claim_key.encode()).hexdigest()[:32]}"


def _issue_result(cfg: dict[str, Any], issue_key: str) -> dict[str, Any]:
    return {"jira_key": issue_key, "jira_url": f"{_base_url(cfg)}/browse/{issue_key}"}


async def _post_issue(cfg: dict[str, Any], fields: dict[str, Any]) -> dict[str, Any]:
    """POST the issue. Raises ``OutcomeUnknown`` whenever Jira may hold it."""
    try:
        resp = await _jira_post(cfg, "/rest/api/3/issue", {"fields": fields})
    except _NOT_SENT_ERRORS as exc:
        raise HTTPException(
            status_code=502, detail=f"Jira request failed: {str(exc)[:300]}",
        )
    except Exception as exc:
        raise tci.OutcomeUnknown(f"{type(exc).__name__}: {str(exc)[:200]}") from exc
    if resp.status_code >= 500:
        raise tci.OutcomeUnknown(f"Jira answered HTTP {resp.status_code}")
    if resp.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail=f"Jira rejected the issue (HTTP {resp.status_code}): {resp.text[:300]}",
        )
    try:
        issue_key = str(resp.json()["key"])
    except Exception as exc:
        raise tci.OutcomeUnknown("Jira accepted the request but returned no issue key") from exc
    return _issue_result(cfg, issue_key)


async def _search_issue_keys_by_label(cfg: dict[str, Any], label: str) -> list[str]:
    """Issue keys carrying ``label``, oldest first. 502 when Jira cannot answer:
    an unanswered search is not evidence either way."""
    detail = "Could not check Jira for an issue filed by an earlier request"
    try:
        resp = await _jira_get(
            cfg, "/rest/api/3/search/jql",
            params={
                "jql": f'labels = "{label}" ORDER BY created ASC',
                "fields": "created",
                "maxResults": 10,
            },
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"{detail}: {str(exc)[:300]}")
    if resp.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"{detail} (HTTP {resp.status_code}).")
    try:
        issues = (resp.json() or {}).get("issues") or []
        return [str(issue["key"]) for issue in issues if issue.get("key")]
    except Exception:
        raise HTTPException(status_code=502, detail=f"{detail}: unreadable search response.")


def _raise_outcome_unknown(label: str, *, cause: Optional[str] = None) -> NoReturn:
    if cause:
        message = (
            f"Jira did not confirm whether it created the issue ({cause}). "
            f"Search Jira for the label {label}. Retrying checks Jira for it again."
        )
    else:
        message = (
            "An earlier request to file this defect never recorded whether Jira "
            f"created the issue, and no issue with the label {label} was found. "
            "Jira search can lag, so search Jira for the label; if the issue is "
            "not there, retry with confirm_not_filed."
        )
    raise HTTPException(
        status_code=409,
        detail={"code": "jira_outcome_unknown", "message": message, "jira_label": label},
    )


@dataclass(frozen=True)
class _Filing:
    """What the filing core did for one signature.

    ``how``: ``deduplicated`` (an ACTIVE linked defect of the signature exists:
    ``existing`` is it, its recurrence was bumped and commented), ``created``
    (this request POSTed the issue), ``replayed`` (an earlier request's claim
    recorded the issue) or ``reconciled`` (found in Jira by the claim label).
    """

    how: str
    issue_key: Optional[str] = None
    issue_url: Optional[str] = None
    existing: Optional[Defect] = None
    commented: bool = False
    other_keys: tuple[str, ...] = ()


async def _file_or_reuse(
    db: AsyncSession,
    pid: _uuid.UUID,
    actor: Any,
    prefill: dict[str, Any],
    cfg: dict[str, Any],
    *,
    jira_project_key: Optional[str],
    issue_type: Optional[str],
    assignee: Optional[str],
    extra_comment: Optional[str],
    confirm_not_filed: bool,
    exclude_defect_id: Optional[_uuid.UUID] = None,
) -> _Filing:
    """The filing core shared by the one-click create and the approval path.

    Lock the signature, deduplicate onto an ACTIVE linked defect, else claim
    the filing in ``agent_action_ledger`` (namespace ``one-click:{pid}`` /
    ``{signature}:{generation}``) and POST the issue exactly once. Both callers
    use the SAME claim namespace, so the one-click path and approving a
    promoted defect can never file one failure twice. Raises the same
    ``HTTPException`` texts as before (502 never-sent / rejected, 409
    ``jira_outcome_unknown``). Stage-only.
    """
    signature = prefill["signature"]

    # Serialize creates for this signature. A double-submit waits here until
    # the first request commits or rolls back, then finds its Defect row in the
    # dedup read below instead of racing it to Jira.
    await _lock_signature(db, pid, signature)

    # Dedup before create — never file a duplicate for a known-open defect.
    existing = await find_open_linked_defect(db, pid, signature)
    if existing is not None and exclude_defect_id is not None and existing.id == exclude_defect_id:
        existing = None
    if existing is not None:
        existing.recurrence_count = int(existing.recurrence_count or 0) + 1
        existing.last_recurrence_at = datetime.now(timezone.utc)
        commented = await _post_recurrence_comment(
            cfg, existing.jira_ticket_id, prefill, existing.recurrence_count,
        )
        await db.flush()
        return _Filing(
            how="deduplicated",
            issue_key=existing.jira_ticket_id,
            issue_url=existing.jira_ticket_url,
            existing=existing,
            commented=commented,
        )

    # The filing is claimed in the ledger before Jira is called, keyed on the
    # signature and its generation, so a request whose outcome was lost (the
    # commit failed, the worker died, the read timed out) is never filed twice.
    generation = await _signature_generation(db, pid, signature)
    scope_id = f"one-click:{pid}"
    subject_id = f"{signature}:{generation}"
    key = tci.tool_call_key(
        tool=tci.JIRA_TICKET_TOOL, scope_id=scope_id, subject_id=subject_id,
    )
    label = _signature_label(key)

    project_key = jira_project_key or cfg["default_project_key"]
    fields: dict[str, Any] = {
        "project": {"key": project_key},
        "summary": prefill["summary"],
        "issuetype": {"name": issue_type or "Bug"},
        "description": _adf_from_prefill(prefill, extra_comment),
        "labels": ["testlookup", "one-click-defect", label],
    }
    if assignee:
        fields["assignee"] = {"accountId": assignee}

    async def _file() -> dict[str, Any]:
        return await _post_issue(cfg, fields)

    async def _run_once() -> tci.ToolCallOutcome:
        try:
            return await tci.run_once(
                project_id=pid,
                tool=tci.JIRA_TICKET_TOOL,
                scope_id=scope_id,
                subject_id=subject_id,
                target_type="failure_signature",
                request_payload={
                    "signature": signature,
                    "generation": generation,
                    "jira_project_key": project_key,
                    "issue_type": issue_type or "Bug",
                    "label": label,
                },
                call=_file,
            )
        except tci.OutcomeUnknown as exc:
            _raise_outcome_unknown(label, cause=str(exc))

    outcome = await _run_once()
    other_keys: list[str] = []
    if outcome.status == "outcome_unknown":
        # An earlier request claimed this filing and never recorded how it
        # ended. Look for the issue by its label before deciding anything.
        found = await _search_issue_keys_by_label(cfg, label)
        if found:
            issue_key, other_keys = found[0], found[1:]
            await tci.record_outcome(
                project_id=pid, key=outcome.key, status="executed",
                result_payload=_issue_result(cfg, issue_key),
            )
            outcome = tci.ToolCallOutcome(
                status="replayed", key=outcome.key, result=_issue_result(cfg, issue_key),
            )
            how = "reconciled"
        elif not confirm_not_filed:
            # Jira search lags behind creation, so "not found" is not proof.
            _raise_outcome_unknown(label)
        else:
            db.add(AccessAuditLog(
                actor_user_id=actor.id,
                actor_name=getattr(actor, "username", None),
                project_id=pid,
                action="jira_defect_confirm_not_filed",
                after_value={"signature": signature, "jira_label": label},
            ))
            await tci.record_outcome(
                project_id=pid, key=outcome.key, status="failed",
                error_code="confirmed_not_filed",
            )
            outcome = await _run_once()
            if outcome.status == "outcome_unknown":  # pragma: no cover — serialized by the lock
                _raise_outcome_unknown(label)
            how = "created" if outcome.status == "executed" else "replayed"
    else:
        how = "created" if outcome.status == "executed" else "replayed"

    issue_key = str(outcome.result.get("jira_key") or "")
    if not issue_key:
        # A claim recorded executed without a key is not something to guess at.
        _raise_outcome_unknown(label)
    issue_url = str(outcome.result.get("jira_url") or f"{_base_url(cfg)}/browse/{issue_key}")
    return _Filing(
        how=how, issue_key=issue_key, issue_url=issue_url, other_keys=tuple(other_keys),
    )


# ── Create-or-link (US-6.1 step 3 + US-6.3 webhook target) ──────────────────


async def create_or_link_issue(
    db: AsyncSession,
    project_id: str,
    actor: User,
    *,
    fingerprint: Optional[str] = None,
    cluster_id: Optional[str] = None,
    issue_type: str = "Bug",
    jira_project_key: Optional[str] = None,
    assignee: Optional[str] = None,
    extra_comment: Optional[str] = None,
    target: str = "jira",
    confirm_not_filed: bool = False,
) -> dict[str, Any]:
    """One-click defect creation. Stage-only — the router handler commits.

    ``target="jira"``  → dedup-or-create against the Jira REST API.
    ``target="webhook"`` → emit ``defect.create_requested`` with the same
    prefilled payload through the customer-managed webhook subsystem.

    At most one Jira issue per signature generation, however the request ends:

    * a transaction-scoped advisory lock serializes requests for one
      signature, so a double-submit waits and then deduplicates;
    * the filing is claimed in ``agent_action_ledger`` (E7.6 ``run_once``)
      before the POST, so a retry after a lost outcome replays the recorded
      issue instead of filing again;
    * a claim whose outcome is unknown is reconciled by searching Jira for
      the issue's ``testlookup-sig-*`` label; when nothing is found the
      request gets a 409 and files only when retried with
      ``confirm_not_filed=True``.
    """
    pid = _uuid.UUID(str(project_id))
    prefill = await build_prefill(
        db, project_id, fingerprint=fingerprint, cluster_id=cluster_id,
    )

    if target == "webhook":
        return await _emit_create_requested(
            db, pid, actor, prefill,
            issue_type=issue_type, extra_comment=extra_comment,
        )

    cfg = await resolve_jira_config(db)
    reason = availability_reason(cfg)
    if reason:
        _raise_unavailable(reason)

    signature = prefill["signature"]
    filing = await _file_or_reuse(
        db, pid, actor, prefill, cfg,
        jira_project_key=jira_project_key,
        issue_type=issue_type,
        assignee=assignee,
        extra_comment=extra_comment,
        confirm_not_filed=confirm_not_filed,
    )
    if filing.existing is not None:
        existing = filing.existing
        return {
            "target": "jira",
            "deduplicated": True,
            "defect_id": str(existing.id),
            "jira_key": existing.jira_ticket_id,
            "jira_url": existing.jira_ticket_url,
            "external_status": existing.jira_status,
            "recurrence_count": existing.recurrence_count,
            "recurrence_comment_posted": filing.commented,
            "message": (
                f"Linked to existing {existing.jira_ticket_id} (recurrence noted)."
            ),
        }
    how = filing.how
    issue_key = filing.issue_key or ""
    issue_url = filing.issue_url or f"{_base_url(cfg)}/browse/{issue_key}"
    other_keys = list(filing.other_keys)

    # Attach to the newest failing TestCase when we have a fingerprint so
    # the defect shows up on /defects (its list JOINs test_cases).
    tc_id: Optional[_uuid.UUID] = None
    if fingerprint:
        tc_row = (
            await db.execute(
                select(TestCase.id)
                .join(TestRun, TestCase.test_run_id == TestRun.id)
                .where(
                    TestRun.project_id == pid,
                    TestCase.test_fingerprint == fingerprint,
                    TestCase.status.in_(_FAILING_STATUSES),
                )
                .order_by(TestCase.created_at.desc())
                .limit(1)
            )
        ).first()
        if tc_row is not None:
            # Partial unique index: one OPEN defect per test_case_id. Leave
            # the link off when an open defect already claims the test case
            # (e.g. an unlinked local defect) — the Jira link matters more
            # than the join.
            open_claim = (
                await db.execute(
                    select(Defect.id).where(
                        Defect.test_case_id == tc_row[0],
                        Defect.resolution_status.in_(ACTIVE_STATUSES),
                    )
                )
            ).first()
            if open_claim is None:
                tc_id = tc_row[0]

    failure_category = (
        (prefill.get("ai_analysis") or {}).get("failure_category") or None
    )
    defect = Defect(
        project_id=pid,
        test_case_id=tc_id,
        cluster_id=cluster_id,
        signature_fingerprint=signature,
        title=prefill["summary"][:255],
        description=prefill["description"],
        jira_ticket_id=issue_key,
        jira_ticket_url=issue_url,
        jira_status="Open",
        external_status_at=datetime.now(timezone.utc),
        failure_category=failure_category,
        resolution_status="OPEN",
        promotion_source="one_click_jira",
        approval_status="executed",
    )
    db.add(defect)
    await db.flush()

    logger.info(
        "jira_defect_created" if how == "created" else "jira_defect_recovered",
        project_id=str(pid), issue_key=issue_key, how=how,
        signature=signature[:16], actor_id=str(actor.id),
    )
    if how == "created":
        message = f"Created {issue_key}."
    elif how == "reconciled":
        message = f"Linked to {issue_key}, filed by an earlier request and found in Jira by its label."
        if other_keys:
            logger.warning(
                "jira_defect_label_duplicates",
                project_id=str(pid), issue_key=issue_key, others=other_keys,
            )
            message += f" {', '.join(other_keys)} carry the same label; close them as duplicates."
    else:
        message = f"Linked to {issue_key}, filed by an earlier request whose result was not saved."
    return {
        "target": "jira",
        "deduplicated": how != "created",
        "defect_id": str(defect.id),
        "jira_key": issue_key,
        "jira_url": issue_url,
        "external_status": "Open",
        "recurrence_count": 0,
        "recurrence_comment_posted": False,
        "message": message,
    }


async def _emit_create_requested(
    db: AsyncSession,
    project_id: _uuid.UUID,
    actor: User,
    prefill: dict[str, Any],
    *,
    issue_type: str,
    extra_comment: Optional[str],
) -> dict[str, Any]:
    """US-6.3 — hand the prefilled payload to customer automation via the
    outbound-webhook subsystem instead of calling Jira."""
    from app.services.webhook_service import _post_allowed, emit_event

    if not await _post_allowed(project_id=project_id, user=actor):
        raise HTTPException(
            status_code=503,
            detail=(
                "Outbound webhooks are unavailable — the 'outbound_webhooks' "
                "feature flag is off or AI_OFFLINE_MODE is enabled."
            ),
        )

    occurrences = prefill["occurrences"]
    payload = {
        "signature": prefill["signature"],
        "summary": prefill["summary"],
        "description": prefill["description"],
        "issue_type": issue_type or "Bug",
        "test_name": prefill.get("test_name"),
        "suite_name": prefill.get("suite_name"),
        "cluster_id": prefill.get("cluster_id"),
        "occurrences": {
            "first_seen": _fmt_dt(occurrences["first_seen"]),
            "last_seen": _fmt_dt(occurrences["last_seen"]),
            "failing_runs": occurrences["failing_runs"],
        },
        "context": prefill["context"],
        "ai_analysis": prefill.get("ai_analysis"),
        "deep_link": prefill["deep_link"],
        "extra_comment": extra_comment,
        "requested_by": getattr(actor, "username", None) or str(actor.id),
    }
    count = await emit_event(
        "defect.create_requested", project_id=project_id, payload=payload,
    )
    return {
        "target": "webhook",
        "deduplicated": False,
        "defect_id": None,
        "jira_key": None,
        "jira_url": None,
        "external_status": None,
        "recurrence_count": 0,
        "recurrence_comment_posted": False,
        "subscriptions_notified": count,
        "message": (
            f"defect.create_requested emitted to {count} subscription(s)."
            if count
            else (
                "No enabled webhook subscription listens for "
                "defect.create_requested in this project — add one under "
                "Settings → Webhooks."
            )
        ),
    }


# ── Triage: link validation + status write-back (DEF-01) ─────────────────────

#: The Jira status CATEGORY each TestLookup status maps to. A write-back moves
#: the issue to the first transition into that category, and is idempotent by
#: state: an issue already in the category is skipped (no ledger claim, D-4).
TARGET_CATEGORY: dict[str, str] = {
    "OPEN": "new",
    "IN_PROGRESS": "indeterminate",
    "RESOLVED": "done",
    "INVALID": "done",
}

# A bare host, optionally with a port: no scheme, no path, no userinfo.
_JIRA_HOST_RE = re.compile(r"^[A-Za-z0-9.-]+(:[0-9]{1,5})?$")


def jira_browse_url(cfg: dict[str, Any], key: str) -> Optional[str]:
    """``https://{configured domain}/browse/{KEY}``, or ``None``.

    The ONLY way a Jira link is built for a key a person typed: no request
    accepts a URL. ``domain`` loses an ``https://`` prefix and a trailing
    ``/``; anything that is still not a bare host (a path, another scheme,
    userinfo) gives ``None``, as does an empty domain or a non-key.
    """
    issue_key = parse_jira_key(key) if isinstance(key, str) else None
    if issue_key is None:
        return None
    domain = str((cfg or {}).get("domain") or "").strip()
    if domain.lower().startswith("https://"):
        domain = domain[len("https://"):]
    domain = domain.rstrip("/")
    if not domain or not _JIRA_HOST_RE.match(domain):
        return None
    return f"https://{domain}/browse/{issue_key}"


async def get_issue(cfg: dict[str, Any], key: str) -> dict[str, Any]:
    """Look an issue up before linking it: ``{key, status, status_category, summary}``.

    404 is the person's mistake (422 ``Jira has no issue {KEY}.``); any other
    HTTP error, or no answer, is a 502 that saves nothing (D-5). The texts name
    the exception CLASS only: no URL, no token.
    """
    try:
        resp = await _jira_get(
            cfg, f"/rest/api/3/issue/{key}", params={"fields": "status,summary"},
        )
    except Exception as exc:
        logger.warning("jira_issue_lookup_failed", issue_key=key, error_type=type(exc).__name__)
        raise HTTPException(status_code=502, detail=f"Could not reach Jira: {type(exc).__name__}")
    if resp.status_code == 404:
        raise HTTPException(status_code=422, detail=f"Jira has no issue {key}.")
    if resp.status_code >= 400:
        raise HTTPException(
            status_code=502, detail=f"Jira answered HTTP {resp.status_code} for {key}.",
        )
    try:
        body = resp.json() or {}
        fields = body.get("fields") or {}
        status_obj = fields.get("status") or {}
        category = ((status_obj.get("statusCategory") or {}).get("key") or "").lower()
        summary = fields.get("summary")
        return {
            "key": str(body.get("key") or key),
            "status": (str(status_obj["name"])[:50] if status_obj.get("name") else None),
            "status_category": category or None,
            "summary": (str(summary) if summary is not None else None),
        }
    except Exception as exc:
        logger.warning("jira_issue_lookup_unreadable", issue_key=key, error_type=type(exc).__name__)
        raise HTTPException(
            status_code=502, detail=f"Jira answered HTTP {resp.status_code} for {key}.",
        )


def _category_of(payload: Any) -> str:
    status_obj = ((payload or {}).get("fields") or {}).get("status") or {}
    return str((status_obj.get("statusCategory") or {}).get("key") or "").lower()


async def transition_issue(cfg: dict[str, Any], key: str, target_status: str) -> dict[str, Any]:
    """Move a linked issue to the category of ``target_status``. NEVER raises.

    ``{"status": "sent" | "skipped" | "failed", "reason": str | None}``. Read
    the issue's category first: already there → ``skipped / already_in_status``
    (so a re-run after a lost answer does nothing); no transition into the
    category → ``skipped / no_transition``; the POST's ``HTTP {code}`` or the
    exception CLASS → ``failed``. Called only after the router's commit.
    """
    try:
        category = TARGET_CATEGORY[target_status]
        current = await _jira_get(cfg, f"/rest/api/3/issue/{key}", params={"fields": "status"})
        if current.status_code >= 400:
            logger.warning(
                "jira_transition_failed", issue_key=key, target=target_status,
                status_code=current.status_code,
            )
            return {"status": "failed", "reason": f"HTTP {current.status_code}"}
        if _category_of(current.json()) == category:
            logger.info(
                "jira_transition_skipped", issue_key=key, target=target_status,
                reason="already_in_status",
            )
            return {"status": "skipped", "reason": "already_in_status"}

        listed = await _jira_get(cfg, f"/rest/api/3/issue/{key}/transitions")
        if listed.status_code >= 400:
            logger.warning(
                "jira_transition_failed", issue_key=key, target=target_status,
                status_code=listed.status_code,
            )
            return {"status": "failed", "reason": f"HTTP {listed.status_code}"}
        transitions = (listed.json() or {}).get("transitions") or []
        chosen = next(
            (
                t for t in transitions
                if str(((t.get("to") or {}).get("statusCategory") or {}).get("key") or "").lower()
                == category
                and t.get("id") is not None
            ),
            None,
        )
        if chosen is None:
            logger.info(
                "jira_transition_skipped", issue_key=key, target=target_status,
                reason="no_transition",
            )
            return {"status": "skipped", "reason": "no_transition"}

        posted = await _jira_post(
            cfg, f"/rest/api/3/issue/{key}/transitions",
            {"transition": {"id": str(chosen["id"])}},
        )
        if posted.status_code >= 400:
            logger.warning(
                "jira_transition_failed", issue_key=key, target=target_status,
                status_code=posted.status_code,
            )
            return {"status": "failed", "reason": f"HTTP {posted.status_code}"}
        logger.info("jira_transition_sent", issue_key=key, target=target_status)
        return {"status": "sent", "reason": None}
    except Exception as exc:  # noqa: BLE001 — the saved status stands whatever Jira does
        logger.warning(
            "jira_transition_failed", issue_key=key, target=target_status,
            error_type=type(exc).__name__,
        )
        return {"status": "failed", "reason": type(exc).__name__}


# ── Approval filing (DEF-06) ─────────────────────────────────────────────────

NO_SIGNATURE_DETAIL = "This defect names no failing test or cluster to file."


async def _prefill_for_defect(db: AsyncSession, defect: Defect) -> Optional[dict[str, Any]]:
    """The filing payload for an EXISTING defect, or ``None`` when it names nothing.

    Signature: the defect's own ``signature_fingerprint``, else its test
    case's ``test_fingerprint`` (read through the run, scoped to the defect's
    project), else the cluster signature ``build_prefill(cluster_id=…)`` gives.
    The ticket text is the prefill's evidence with the defect's own (reviewed)
    title as the summary; its description travels as the extra comment.
    """
    pid = defect.project_id
    fingerprint: Optional[str] = None
    if defect.test_case_id is not None:
        row = (
            await db.execute(
                select(TestCase.test_fingerprint)
                .join(TestRun, TestCase.test_run_id == TestRun.id)
                .where(TestCase.id == defect.test_case_id, TestRun.project_id == pid)
                .limit(1)
            )
        ).first()
        fingerprint = row[0] if row is not None and row[0] else None

    prefill: Optional[dict[str, Any]] = None
    if fingerprint:
        try:
            prefill = await build_prefill(db, str(pid), fingerprint=fingerprint)
        except HTTPException:
            prefill = None
    if prefill is None and defect.cluster_id:
        try:
            prefill = await build_prefill(db, str(pid), cluster_id=defect.cluster_id)
        except HTTPException:
            prefill = None

    signature = defect.signature_fingerprint or fingerprint or (prefill or {}).get("signature")
    if not signature:
        return None
    if prefill is None:
        prefill = {
            "summary": "",
            "description": defect.description or "",
            "error_message": None,
            "occurrences": {"first_seen": None, "last_seen": None, "failing_runs": 0},
            "context": {},
            "ai_analysis": None,
            "deep_link": f"{settings.public_base_url}/defects?defect={defect.id}",
        }
    prefill = {**prefill, "signature": str(signature)[:64]}
    if defect.title:
        prefill["summary"] = " ".join(f"[TestLookup] {defect.title}".split())[:255]
    elif not prefill.get("summary"):
        prefill["summary"] = f"[TestLookup] defect {str(defect.id)[:8]}"
    return prefill


def _filing_detail_text(detail: Any) -> str:
    if isinstance(detail, dict):
        return str(detail.get("message") or detail.get("code") or detail)[:500]
    return str(detail)[:500]


async def file_issue_for_defect(
    db: AsyncSession,
    defect: Defect,
    actor: Any,
    *,
    jira_project_key: Optional[str],
    issue_type: str = "Bug",
) -> dict[str, Any]:
    """File (or link) the Jira issue ON an approved defect. NEVER raises ``HTTPException``.

    Unlike ``create_or_link_issue`` it creates no Defect row: the key lands on
    ``defect`` itself. Returns ``{"status", "reason", "error", "jira_key",
    "jira_url"}`` with ``status``:

    * ``not_filed`` — Jira is unavailable (``reason``: the availability reason);
      no HTTP call is made;
    * ``linked`` — the defect was already linked, or an ACTIVE linked twin of
      the same signature exists (its key, URL and status are copied here,
      ``duplicate_of`` = the twin, the twin's recurrence bumped), or an earlier
      request's claim had filed it;
    * ``filed`` — this call POSTed a new issue;
    * ``failed`` / ``unknown`` — Jira refused it, or did not confirm (the
      ``jira_outcome_unknown`` 409), with the server's text in ``error``.

    Always records ``policy_evaluation["jira_filing"] = {status, reason, at}``
    and flushes. Stage-only: the review route commits (after it has already
    committed the approval itself, so a failing Jira never undoes it).
    """
    now = datetime.now(timezone.utc)
    result: dict[str, Any] = {
        "status": "failed", "reason": None, "error": None, "jira_key": None, "jira_url": None,
    }
    try:
        cfg = await resolve_jira_config(db)
        reason = availability_reason(cfg)
        if reason:
            result = {**result, "status": "not_filed", "reason": reason}
        elif defect.jira_ticket_id:
            # Linked by hand before the approval: nothing to file.
            defect.approval_status = "executed"
            result = {
                **result, "status": "linked",
                "jira_key": defect.jira_ticket_id, "jira_url": defect.jira_ticket_url,
            }
        else:
            prefill = await _prefill_for_defect(db, defect)
            if prefill is None:
                result = {**result, "status": "failed", "error": NO_SIGNATURE_DETAIL}
            else:
                try:
                    filing = await _file_or_reuse(
                        db, defect.project_id, actor, prefill, cfg,
                        jira_project_key=(jira_project_key or "").strip() or None,
                        issue_type=issue_type,
                        assignee=None,
                        extra_comment=(defect.description or None),
                        confirm_not_filed=False,
                        exclude_defect_id=defect.id,
                    )
                except HTTPException as exc:
                    outcome_unknown = (
                        isinstance(exc.detail, dict)
                        and exc.detail.get("code") == "jira_outcome_unknown"
                    )
                    result = {
                        **result,
                        "status": "unknown" if outcome_unknown else "failed",
                        "error": _filing_detail_text(exc.detail),
                    }
                else:
                    if filing.existing is not None:
                        twin = filing.existing
                        defect.jira_ticket_id = twin.jira_ticket_id
                        defect.jira_ticket_url = twin.jira_ticket_url
                        defect.jira_status = twin.jira_status
                        defect.duplicate_of = twin.id
                        status = "linked"
                    else:
                        defect.jira_ticket_id = filing.issue_key
                        defect.jira_ticket_url = jira_browse_url(cfg, filing.issue_key or "")
                        defect.jira_status = "Open"
                        status = "filed" if filing.how == "created" else "linked"
                    defect.external_status_at = now
                    defect.external_status_conflict = False
                    if not defect.signature_fingerprint:
                        defect.signature_fingerprint = prefill["signature"]
                    defect.approval_status = "executed"
                    result = {
                        **result, "status": status,
                        "jira_key": defect.jira_ticket_id, "jira_url": defect.jira_ticket_url,
                    }
    except Exception as exc:  # noqa: BLE001 — the approval is already committed
        logger.warning(
            "jira_filing_on_approval_error",
            defect_id=str(defect.id), error_type=type(exc).__name__,
        )
        result = {**result, "status": "failed", "error": type(exc).__name__}

    defect.policy_evaluation = {
        **(defect.policy_evaluation or {}),
        "jira_filing": {
            "status": result["status"],
            "reason": result["reason"] or result["error"],
            "at": now.isoformat(),
        },
    }
    await db.flush()
    logger.info(
        "jira_filing_on_approval",
        defect_id=str(defect.id), project_id=str(defect.project_id),
        status=result["status"], reason=result["reason"],
        issue_key=result["jira_key"],
        actor_id=str(getattr(actor, "id", None)),
    )
    return result


# ── Metadata for the dialog pickers (US-6.1 step 4) ─────────────────────────

METADATA_TTL_SECONDS = 300

# Module-level cache: Jira projects / issue types are instance-global (the
# connector config is not per-TestLookup-project), so one cache entry
# suffices. Only successful fetches are cached.
_metadata_cache: dict[str, Any] = {"at": 0.0, "data": None}


def _metadata_cache_get() -> Optional[dict[str, Any]]:
    if _metadata_cache["data"] is None:
        return None
    if (time.monotonic() - _metadata_cache["at"]) > METADATA_TTL_SECONDS:
        return None
    return _metadata_cache["data"]


def _metadata_cache_put(data: dict[str, Any]) -> None:
    _metadata_cache["at"] = time.monotonic()
    _metadata_cache["data"] = data


def _metadata_cache_clear() -> None:
    """Test hook."""
    _metadata_cache["at"] = 0.0
    _metadata_cache["data"] = None


async def _webhook_target_available(
    db: AsyncSession, project_id: _uuid.UUID, *, user: Optional[User] = None,
) -> bool:
    """True when the webhook fallback would actually deliver something.

    Asks ``outbound_webhooks`` exactly as ``_emit_create_requested`` will: for
    this project and this user, so the dialog cannot offer a target the
    submit then refuses.
    """
    from app.models.postgres import WebhookSubscription
    from app.services.webhook_service import _post_allowed

    if not await _post_allowed(project_id=project_id, user=user):
        return False
    result = await db.execute(
        select(WebhookSubscription).where(
            WebhookSubscription.project_id == project_id,
            WebhookSubscription.enabled.is_(True),
        )
    )
    for sub in result.scalars().all():
        if "defect.create_requested" in (sub.events or []):
            return True
    return False


async def get_metadata(
    db: AsyncSession, project_id: str, *, user: Optional[User] = None,
) -> dict[str, Any]:
    """Available Jira projects + issue types for the dialog pickers.

    Graceful — never raises for an unreachable/unconfigured Jira; the
    response carries ``available=false`` + a machine-readable ``reason``
    so the UI can disable the action with a tooltip. Successful Jira
    fetches are cached ~5 minutes.
    """
    pid = _uuid.UUID(str(project_id))
    cfg = await resolve_jira_config(db)
    reason = availability_reason(cfg)
    webhook_available = await _webhook_target_available(db, pid, user=user)

    base = {
        "available": False,
        "reason": reason,
        "projects": [],
        "issue_types": [],
        "default_project_key": cfg.get("default_project_key"),
        "webhook_available": webhook_available,
    }
    if reason:
        return base

    cached = _metadata_cache_get()
    if cached is not None:
        return {**base, **cached, "available": True, "reason": None}

    try:
        projects_resp = await _jira_get(
            cfg, "/rest/api/3/project/search", params={"maxResults": 50},
        )
        types_resp = await _jira_get(cfg, "/rest/api/3/issuetype")
        if projects_resp.status_code >= 400 or types_resp.status_code >= 400:
            status = max(projects_resp.status_code, types_resp.status_code)
            return {**base, "reason": f"jira_http_{status}"}
        projects = [
            {"key": p.get("key"), "name": p.get("name")}
            for p in projects_resp.json().get("values", [])
            if p.get("key")
        ]
        issue_types: list[str] = []
        for it in types_resp.json():
            name = it.get("name")
            if name and not it.get("subtask") and name not in issue_types:
                issue_types.append(name)
    except Exception as exc:
        logger.debug("jira_metadata_fetch_failed", error=str(exc))
        return {**base, "reason": "unreachable"}

    fetched = {"projects": projects, "issue_types": issue_types}
    _metadata_cache_put(fetched)
    return {**base, **fetched, "available": True, "reason": None}


# ── Status sync-back (US-6.2) ────────────────────────────────────────────────


async def _signature_still_failing(db: AsyncSession, defect: Defect) -> bool:
    """Any failing execution of the defect's signature in the lookback
    window? Drives the "closed in Jira but still failing" conflict flag."""
    fingerprint = defect.signature_fingerprint
    if not fingerprint and defect.test_case_id is not None:
        fp_row = (
            await db.execute(
                select(TestCase.test_fingerprint).where(TestCase.id == defect.test_case_id)
            )
        ).first()
        fingerprint = fp_row[0] if fp_row else None
    if not fingerprint:
        return False

    cutoff = datetime.now(timezone.utc) - timedelta(days=CONFLICT_LOOKBACK_DAYS)
    row = (
        await db.execute(
            select(TestCase.id)
            .join(TestRun, TestCase.test_run_id == TestRun.id)
            .where(
                TestRun.project_id == defect.project_id,
                TestCase.test_fingerprint == fingerprint,
                TestCase.status.in_(_FAILING_STATUSES),
                TestCase.created_at >= cutoff,
            )
            .limit(1)
        )
    ).first()
    return row is not None


async def sync_external_statuses(
    db: AsyncSession, *, cap: int = SYNC_CAP_PER_CYCLE,
) -> dict[str, Any]:
    """Mirror Jira status onto linked, still-OPEN defects (US-6.2).

    Stage-only — the calling Celery task owns the commit. Capped at
    ``cap`` issues per cycle (oldest-refreshed first) so a large backlog
    never hammers the Jira API; the beat's 15-minute cadence catches the
    rest up over subsequent cycles.
    """
    cfg = await resolve_jira_config(db)
    reason = availability_reason(cfg)
    if reason:
        return {"skipped": reason, "checked": 0, "conflicts": 0}

    rows = (
        await db.execute(
            select(Defect)
            .where(
                Defect.jira_ticket_id.isnot(None),
                Defect.resolution_status.in_(ACTIVE_STATUSES),
            )
            .order_by(Defect.external_status_at.asc().nullsfirst())
            .limit(max(1, cap))
        )
    ).scalars().all()

    checked = 0
    conflicts = 0
    errors = 0
    now = datetime.now(timezone.utc)
    for defect in rows:
        try:
            resp = await _jira_get(
                cfg,
                f"/rest/api/3/issue/{defect.jira_ticket_id}",
                params={"fields": "status"},
            )
        except Exception as exc:
            errors += 1
            logger.debug(
                "jira_status_sync_fetch_error",
                issue_key=defect.jira_ticket_id, error=str(exc),
            )
            continue
        if resp.status_code >= 400:
            errors += 1
            # Still stamp the timestamp so one perpetually-404 issue can't
            # monopolise the cap-limited window every cycle.
            defect.external_status_at = now
            continue

        status_obj = (resp.json().get("fields") or {}).get("status") or {}
        status_name = status_obj.get("name")
        category_key = ((status_obj.get("statusCategory") or {}).get("key") or "").lower()

        defect.jira_status = status_name
        defect.external_status_at = now
        is_conflict = False
        if category_key in _DONE_CATEGORY_KEYS:
            is_conflict = await _signature_still_failing(db, defect)
        defect.external_status_conflict = is_conflict
        if is_conflict:
            conflicts += 1
        checked += 1

    await db.flush()
    logger.info(
        "jira_status_sync_complete",
        checked=checked, conflicts=conflicts, errors=errors, batch=len(rows),
    )
    return {"checked": checked, "conflicts": conflicts, "errors": errors}
