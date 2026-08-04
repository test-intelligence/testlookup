"""Fixer pipeline stages (Agentic plan AI-2).

Pure-ish stage helpers used by ``workflow.run_fixer_run``: candidate
selection, diagnosis recall, fix generation (registry prompt / offline),
test-code-only glob rejection, draft-PR opening, and the outcome poller.
No agent implementations here (support module — see the quality-gate note in
``runners.py``); everything is a plain async/sync function.

US-15.2 scope note — candidate selection is NOT confidence-gated
----------------------------------------------------------------
``select_candidates`` is deliberately outside the confidence gate
(``services/confidence_gate.py``). Its inputs are entirely deterministic:
active quarantine state, observed flip rate, last-failure recency, and a count
of prior consumed fix attempts. There is no AI conclusion in that decision, so
there is no confidence to gate on — attaching one would mean inventing a
number, which is precisely the dishonesty US-15.1/15.2 exist to remove.

The safety story for Fixer is a different mechanism and already in place: an
attempt budget, a test-code-only glob restriction, an ephemeral sandbox, and a
draft PR that a human must merge. Nothing here acts unsupervised.

To bring Fixer under the gate later you would need an AI-produced confidence
attached to the FIX, not to candidate selection — e.g. the generation stage
emitting a calibrated "this patch resolves the failure" score validated
against merged-vs-abandoned draft PRs. Until such a signal exists and has been
measured, gating here would be theatre.
"""
from __future__ import annotations

import base64
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.fixer.state import FixCandidate
from app.core.config import settings
from app.models.postgres import (
    AgentInvestigation,
    FixAttempt,
    FlakyQuarantineRequest,
)

logger = structlog.get_logger("agents.fixer.pipeline")

# Active-quarantine states the candidate selector considers (mirrors
# flaky_quarantine_service.active_quarantines_for_project).
_ACTIVE_QUARANTINE_STATES = ("quarantined", "recheck_scheduled", "re_quarantined")

# Statuses that count as a consumed attempt against max_attempts_per_test —
# an attempt that reached generation or beyond actually spent budget.
_ATTEMPT_CONSUMED_STATUSES = (
    "generating", "validating", "validated", "failed_validation",
    "pr_opened", "error",
)


# ── Candidate selection ──────────────────────────────────────────────────────


async def select_candidates(
    db: AsyncSession, project_id: uuid.UUID, max_tests: Optional[int] = None,
) -> list[FixCandidate]:
    """Top flaky tests among active quarantines, ordered by flip-rate desc.

    Ordering + the ``max_tests`` cap happen in SQL (flip_rate desc nulls
    last, then most-recent failure), and prior consumed attempts for the
    capped set come from ONE GROUP BY query — no per-row COUNT round-trips.
    The workflow still enforces ``max_attempts_per_test`` from
    ``prior_attempts``.
    """
    from sqlalchemy import func

    stmt = (
        select(FlakyQuarantineRequest)
        .where(
            FlakyQuarantineRequest.project_id == project_id,
            FlakyQuarantineRequest.status.in_(_ACTIVE_QUARANTINE_STATES),
        )
        .order_by(
            FlakyQuarantineRequest.flip_rate.desc().nulls_last(),
            FlakyQuarantineRequest.last_failure_at.desc().nulls_last(),
        )
    )
    if max_tests is not None and max_tests >= 0:
        stmt = stmt.limit(max_tests)
    rows = (await db.execute(stmt)).scalars().all()
    if not rows:
        return []

    fingerprints = [r.test_fingerprint for r in rows]
    prior_by_fp: dict[str, int] = {
        fp: int(count or 0)
        for fp, count in (
            await db.execute(
                select(FixAttempt.test_fingerprint, func.count(FixAttempt.id))
                .where(
                    FixAttempt.project_id == project_id,
                    FixAttempt.test_fingerprint.in_(fingerprints),
                    FixAttempt.status.in_(_ATTEMPT_CONSUMED_STATUSES),
                )
                .group_by(FixAttempt.test_fingerprint)
            )
        ).all()
    }
    return [
        FixCandidate(
            test_fingerprint=r.test_fingerprint,
            test_name=r.test_name,
            suite_name=r.suite_name,
            flip_rate=r.flip_rate,
            flip_window_size=r.flip_window_size,
            prior_attempts=prior_by_fp.get(r.test_fingerprint, 0),
        )
        for r in rows
    ]


# ── Diagnosis (reuse existing signals — never rerun the investigator) ────────


async def gather_diagnosis(
    db: AsyncSession, project_id: uuid.UUID, candidate: FixCandidate,
) -> dict[str, Any]:
    """Assemble a diagnosis for the fix-generation prompt from EXISTING signals:
    the latest completed investigation verdict (if any), memory recall for the
    fingerprint, and the quarantine flip history. Never triggers a new
    investigation."""
    sources: list[str] = []
    lines: list[str] = []

    if candidate.flip_rate is not None:
        lines.append(
            f"Flip history: flip rate {candidate.flip_rate:.0%} over "
            f"{candidate.flip_window_size or '?'} runs."
        )
        sources.append("flip_history")

    # Memory recall (AI-F3 read side) — prior analyses / human corrections.
    try:
        from app.services.memory_recall import (
            format_recall_lines,
            recall_failure_history,
        )

        recall = await recall_failure_history(
            db, project_id,
            test_fingerprint=candidate.test_fingerprint,
            test_name=candidate.test_name,
        )
        if recall.get("has_history"):
            lines.extend(format_recall_lines(recall)[:3])
            sources.append("memory")
    except Exception as exc:  # noqa: BLE001
        logger.debug("fixer_diagnosis_recall_failed", error=str(exc))

    # Latest completed investigation verdict for the project (read-only reuse).
    try:
        inv = (
            await db.execute(
                select(AgentInvestigation)
                .where(
                    AgentInvestigation.project_id == project_id,
                    AgentInvestigation.status == "completed",
                    AgentInvestigation.verdict.isnot(None),
                )
                .order_by(AgentInvestigation.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if inv is not None and inv.verdict:
            narrative = str((inv.verdict or {}).get("narrative") or "").strip()
            if narrative:
                lines.append(f"Latest investigation verdict: {narrative[:400]}")
                sources.append("investigation")
    except Exception as exc:  # noqa: BLE001
        logger.debug("fixer_diagnosis_investigation_failed", error=str(exc))

    if not lines:
        lines.append("No prior diagnosis on record; treat as a fresh flaky-test analysis.")
    return {"text": "\n".join(lines), "sources": sorted(set(sources))}


# ── Fix generation (ONE registry prompt; test-code-only; offline-honest) ─────


async def generate_candidate_patch(
    *,
    candidate: FixCandidate,
    diagnosis: dict[str, Any],
    test_source: str,
    budget: dict[str, int],
) -> dict[str, Any]:
    """Generate a test-code-only unified diff via the ``fixer_generate_patch``
    registry prompt. Returns
    ``{"can_fix": bool, "patch": str|None, "reasoning": str, "tokens": int}``.

    Honest offline behaviour: when ``AI_OFFLINE_MODE`` is set (the default) or
    no LLM is reachable, returns ``can_fix=False`` with no patch — the run
    records the attempt and does nothing (no validation, no PR).
    """
    if settings.AI_OFFLINE_MODE:
        return {
            "can_fix": False, "patch": None, "tokens": 0,
            "reasoning": "AI_OFFLINE_MODE is enabled — no fix generated (shadow diagnostics only).",
        }
    try:
        from app.services.llm_factory import get_llm
        from app.services.prompt_registry import get_prompt_text

        prompt = get_prompt_text("fixer_generate_patch").format(
            test_name=candidate.test_name or candidate.test_fingerprint,
            diagnosis=diagnosis.get("text") or "",
            test_source=test_source or "(test source unavailable)",
        )
        llm = await get_llm(temperature=0.0)
        response = await llm.ainvoke(prompt)
        text = str(getattr(response, "content", "") or "").strip()
        usage = getattr(response, "usage_metadata", None) or {}
        tokens = int(usage.get("total_tokens") or 0) or max(1, len(prompt) // 4)
        parsed = _extract_json(text)
        if not parsed or not parsed.get("can_fix"):
            return {
                "can_fix": False, "patch": None, "tokens": tokens,
                "reasoning": str((parsed or {}).get("reasoning") or "model declined to propose a test-only fix"),
            }
        patch = str(parsed.get("patch") or "").strip()
        return {
            "can_fix": bool(patch),
            "patch": patch or None,
            "tokens": tokens,
            "reasoning": str(parsed.get("reasoning") or ""),
        }
    except Exception as exc:  # noqa: BLE001 — generation failure is honest "no fix"
        # Full exception text goes to the debug log only; the stored (API-
        # readable) reasoning carries just the type name — raw LLM/provider
        # errors can embed endpoint URLs and third-party strings.
        logger.debug("fixer_generation_failed", error=str(exc))
        return {
            "can_fix": False, "patch": None, "tokens": 0,
            "reasoning": f"generation error: {type(exc).__name__}",
        }


def _extract_json(text: str) -> Optional[dict[str, Any]]:
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        obj = json.loads(text[start:end + 1])
        return obj if isinstance(obj, dict) else None
    except (ValueError, TypeError):
        return None


# ── Test-code-only glob rejection (structural — BEFORE any execution) ─────────

_DIFF_TARGET_RE = re.compile(r"^\+\+\+ (?:b/)?(.+?)\s*$", re.MULTILINE)
_DIFF_SOURCE_RE = re.compile(r"^--- (?:a/)?(.+?)\s*$", re.MULTILINE)


def diff_touched_paths(patch: str) -> list[str]:
    """Every repo-relative path a unified diff adds/modifies/deletes."""
    paths: set[str] = set()
    for m in _DIFF_TARGET_RE.finditer(patch or ""):
        p = m.group(1).strip()
        if p and p != "/dev/null":
            paths.add(p)
    for m in _DIFF_SOURCE_RE.finditer(patch or ""):
        p = m.group(1).strip()
        if p and p != "/dev/null":
            paths.add(p)
    return sorted(paths)


def _glob_to_regex(pattern: str) -> str:
    i, n, out = 0, len(pattern), ""
    while i < n:
        c = pattern[i]
        if c == "*":
            if i + 1 < n and pattern[i + 1] == "*":
                if i + 2 < n and pattern[i + 2] == "/":
                    out += "(?:.*/)?"
                    i += 3
                    continue
                out += ".*"
                i += 2
                continue
            out += "[^/]*"
            i += 1
            continue
        if c == "?":
            out += "[^/]"
            i += 1
            continue
        out += re.escape(c)
        i += 1
    return "^" + out + "$"


def glob_match(path: str, pattern: str) -> bool:
    return re.match(_glob_to_regex(pattern), path) is not None


def patch_touches_only_test_globs(
    patch: str, test_globs: list[str],
) -> tuple[bool, list[str]]:
    """Return ``(all_test_code, offending_paths)``. A patch that touches ANY
    path not matching a test glob — or that touches nothing — is rejected."""
    paths = diff_touched_paths(patch)
    if not paths:
        return False, []
    offending = [p for p in paths if not any(glob_match(p, g) for g in (test_globs or []))]
    return (not offending), offending


def summarize_patch(patch: str) -> str:
    paths = diff_touched_paths(patch)
    added = sum(1 for ln in (patch or "").splitlines() if ln.startswith("+") and not ln.startswith("+++"))
    removed = sum(1 for ln in (patch or "").splitlines() if ln.startswith("-") and not ln.startswith("---"))
    files = ", ".join(paths[:5]) + (" …" if len(paths) > 5 else "")
    return f"{len(paths)} file(s) [{files}] +{added}/-{removed}"


# ── Minimal unified-diff applier (for GitHub-side PR file writes) ─────────────


def apply_unified_diff(base_text: str, file_diff: str) -> Optional[str]:
    """Apply a single-file unified diff's hunks to ``base_text``. Returns the
    patched text, or None when a hunk's context does not line up (so the PR
    opener refuses to commit a mangled file)."""
    base_lines = base_text.splitlines(keepends=False)
    out: list[str] = []
    cursor = 0
    hunk_re = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
    lines = file_diff.splitlines()
    idx = 0
    while idx < len(lines):
        line = lines[idx]
        m = hunk_re.match(line)
        if not m:
            idx += 1
            continue
        old_start = int(m.group(1)) - 1
        if old_start < cursor:
            return None
        out.extend(base_lines[cursor:old_start])
        cursor = old_start
        idx += 1
        while idx < len(lines) and not lines[idx].startswith("@@"):
            hl = lines[idx]
            if hl.startswith("\\"):  # "\ No newline at end of file"
                idx += 1
                continue
            tag, content = (hl[:1], hl[1:]) if hl else (" ", "")
            if tag == " ":
                if cursor >= len(base_lines) or base_lines[cursor] != content:
                    return None
                out.append(base_lines[cursor])
                cursor += 1
            elif tag == "-":
                if cursor >= len(base_lines) or base_lines[cursor] != content:
                    return None
                cursor += 1
            elif tag == "+":
                out.append(content)
            idx += 1
    out.extend(base_lines[cursor:])
    trailing = "\n" if base_text.endswith("\n") else ""
    return "\n".join(out) + trailing


def split_per_file_diffs(patch: str) -> dict[str, str]:
    """Split a multi-file unified diff into ``{path: single_file_diff}``."""
    result: dict[str, str] = {}
    current_path: Optional[str] = None
    buf: list[str] = []
    for line in (patch or "").splitlines():
        if line.startswith("+++ "):
            p = line[4:].strip()
            current_path = p[2:] if p.startswith("b/") else p
        if line.startswith("diff --git") and buf and current_path:
            result[current_path] = "\n".join(buf)
            buf = []
            current_path = None
        buf.append(line)
    if current_path and buf:
        result[current_path] = "\n".join(buf)
    return result


# ── Draft-PR opener (suggest mode only; validated only) ───────────────────────

FIX_BRANCH_PREFIX = "testlookup/fix-flaky-"

_GH_HEADERS_ACCEPT = "application/vnd.github+json"


async def fetch_default_branch(
    *, api_base_url: str, repo_owner: str, repo_name: str, pat: str,
) -> Optional[str]:
    """Resolve the repo's real default branch (hoisted from the PR opener so
    the workflow can thread it into ValidationSpec.ref instead of hardcoding
    "main"). Best-effort: None on any failure — callers fall back to "main"
    only when unknown."""
    import httpx

    from app.services.github_checks_service import _ssrf_block_reason

    repo_url = f"{api_base_url.rstrip('/')}/repos/{repo_owner}/{repo_name}"
    if await _ssrf_block_reason(repo_url):
        return None
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(repo_url, headers={
                "Authorization": f"Bearer {pat}",
                "Accept": _GH_HEADERS_ACCEPT,
                "User-Agent": "TestLookup/1.0",
                "X-GitHub-Api-Version": "2022-11-28",
            })
        if resp.status_code != 200:
            return None
        return (resp.json() or {}).get("default_branch") or None
    except Exception as exc:  # noqa: BLE001
        logger.debug("fixer_default_branch_lookup_failed", error=str(exc))
        return None


async def open_draft_pr(
    *,
    api_base_url: str,
    repo_owner: str,
    repo_name: str,
    pat: str,
    candidate: FixCandidate,
    patch: str,
    validation: dict[str, int],
    reasoning: str,
    ledger_deep_link: str,
) -> dict[str, Any]:
    """Open a DRAFT PR carrying the validated test-only fix. Best-effort and
    fully guarded — returns ``{"pr_url","pr_number"}`` on success or
    ``{"skipped"|"error": reason}``. Reuses the GitHub PAT/SSRF/HTTP patterns
    from ``github_checks_service``; never raises."""
    import httpx

    from app.services.github_checks_service import _ssrf_block_reason

    fp12 = candidate.test_fingerprint[:12]
    branch = FIX_BRANCH_PREFIX + fp12
    base = api_base_url.rstrip("/")
    headers = {
        "Authorization": f"Bearer {pat}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "TestLookup/1.0",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    repo_url = f"{base}/repos/{repo_owner}/{repo_name}"
    block = await _ssrf_block_reason(repo_url)
    if block:
        return {"error": f"blocked target: {block}"}

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            repo = await client.get(repo_url, headers=headers)
            if repo.status_code != 200:
                return {"error": f"repo lookup HTTP {repo.status_code}"}
            default_branch = (repo.json() or {}).get("default_branch") or "main"

            ref = await client.get(
                f"{repo_url}/git/ref/heads/{default_branch}", headers=headers,
            )
            if ref.status_code != 200:
                return {"error": f"base ref HTTP {ref.status_code}"}
            base_sha = (ref.json() or {}).get("object", {}).get("sha")
            if not base_sha:
                return {"error": "no base sha"}

            # Create the fix branch (idempotent — reuse if it already exists).
            created = await client.post(
                f"{repo_url}/git/refs", headers=headers,
                json={"ref": f"refs/heads/{branch}", "sha": base_sha},
            )
            if created.status_code not in (200, 201, 422):
                return {"error": f"branch create HTTP {created.status_code}"}

            # Reconstruct + commit each touched (test) file on the branch.
            for path, file_diff in split_per_file_diffs(patch).items():
                content_resp = await client.get(
                    f"{repo_url}/contents/{path}",
                    headers=headers, params={"ref": default_branch},
                )
                if content_resp.status_code != 200:
                    return {"error": f"content GET {path} HTTP {content_resp.status_code}"}
                meta = content_resp.json() or {}
                base_text = base64.b64decode(meta.get("content", "")).decode("utf-8", "replace")
                new_text = apply_unified_diff(base_text, file_diff)
                if new_text is None:
                    return {"error": f"patch context mismatch on {path}"}
                put = await client.put(
                    f"{repo_url}/contents/{path}", headers=headers,
                    json={
                        "message": f"fix(flaky): stabilise {candidate.test_name or path}",
                        "content": base64.b64encode(new_text.encode("utf-8")).decode("ascii"),
                        "sha": meta.get("sha"),
                        "branch": branch,
                    },
                )
                if put.status_code not in (200, 201):
                    return {"error": f"content PUT {path} HTTP {put.status_code}"}

            body = _pr_body(candidate, validation, reasoning, ledger_deep_link)
            title = (
                f"Attempt to fix flaky test {candidate.test_name or candidate.test_fingerprint} "
                f"— validated {validation.get('passed', 0)}/{validation.get('reruns', 0)} reruns"
            )
            pr = await client.post(
                f"{repo_url}/pulls", headers=headers,
                json={
                    "title": title[:250], "head": branch, "base": default_branch,
                    "body": body, "draft": True,
                },
            )
            if pr.status_code not in (200, 201):
                # Parse GitHub's structured error message rather than storing
                # the raw response body (which can carry endpoint URLs and
                # documentation links) into the API-readable reason.
                try:
                    gh_message = str((pr.json() or {}).get("message") or "")[:200]
                except Exception:  # noqa: BLE001
                    gh_message = ""
                return {"error": f"pull create HTTP {pr.status_code}: {gh_message}"}
            pr_json = pr.json() or {}
            return {"pr_url": pr_json.get("html_url"), "pr_number": pr_json.get("number")}
    except Exception as exc:  # noqa: BLE001
        logger.warning("fixer_pr_open_failed", error=str(exc))
        return {"error": str(exc)[:300]}


def _pr_body(
    candidate: FixCandidate, validation: dict[str, int], reasoning: str, ledger_deep_link: str,
) -> str:
    reruns = validation.get("reruns", 0)
    passed = validation.get("passed", 0)
    return (
        f"### TestLookup Fixer — flaky-test stabilisation\n\n"
        f"**Test:** `{candidate.test_name or candidate.test_fingerprint}`\n\n"
        f"**Reasoning:** {reasoning or 'Test-code-only stabilisation of a flaky test.'}\n\n"
        f"**Validation:**\n\n"
        f"| reruns | passed | result |\n|---|---|---|\n"
        f"| {reruns} | {passed} | {'validated ✅' if passed == reruns and reruns else 'partial'} |\n\n"
        f"Ledger: {ledger_deep_link}\n\n"
        f"> This is a **draft** PR generated by TestLookup Fixer. It changes test code only "
        f"and was validated by rerunning the test {reruns} times in an isolated sandbox. "
        f"Review before merging.\n"
    )


# ── Outcome poller (periodic beat) ───────────────────────────────────────────


def outcome_for_pr(merged: bool) -> str:
    """Map a terminal fixer-PR state to a ``record_fix_outcome`` outcome:
    merged → ``fixed``, closed-unmerged → ``not_fixed`` (AI-5 feedback loop)."""
    return "fixed" if merged else "not_fixed"


# Sweep bounds: at most this many open-PR attempts per beat (oldest first so
# nothing starves), persisted in batches so a crash loses at most one batch.
_PR_SWEEP_LIMIT = 200
_PR_SWEEP_BATCH = 20


async def poll_open_fixer_prs() -> dict[str, int]:
    """Poll ``pr_state='open'`` fixer PRs (bounded to the oldest
    ``_PR_SWEEP_LIMIT``); on a terminal GitHub state feed the outcome back
    through ``feedback_service.record_fix_outcome`` (merged → ``fixed``,
    closed-unmerged → ``not_fixed``) and update ``pr_state``. Offline / no
    integration → no-op.

    Three phases so no DB session is ever held across an HTTP call:
    snapshot (read + close) → poll (one shared client, no session) →
    persist (batched commits; ``pr_state`` advances ONLY after
    ``record_fix_outcome`` succeeds so the feedback row is never lost).

    Transaction note: this is a Celery-beat-owned unit of work on its own
    ``AsyncSessionLocal`` — the commits here are worker-owned by design
    (the transaction-boundary ratchet in
    ``tests/test_architectural_transaction_boundaries.py`` governs
    ``app/services/``; this support module is the beat task's outermost
    orchestration layer, the same standing as ``workflow.py``).
    """
    summary = {"checked": 0, "merged": 0, "closed": 0}
    if settings.AI_OFFLINE_MODE:
        return summary

    import httpx

    from app.db.postgres import AsyncSessionLocal
    from app.services import secret_service
    from app.services.feedback_service import record_fix_outcome
    from app.services.github_checks_service import (
        SECRET_SCOPE,
        _secret_key,
        _ssrf_block_reason,
        get_integration,
    )

    # Phase 1 — snapshot the open attempts + per-project credentials, then
    # CLOSE the session before any HTTP leaves the building.
    async with AsyncSessionLocal() as db:
        open_rows = (
            await db.execute(
                select(
                    FixAttempt.id,
                    FixAttempt.project_id,
                    FixAttempt.pr_number,
                    FixAttempt.pr_url,
                    FixAttempt.test_fingerprint,
                )
                .where(
                    FixAttempt.pr_state == "open",
                    FixAttempt.pr_number.isnot(None),
                )
                .order_by(FixAttempt.created_at.asc())
                .limit(_PR_SWEEP_LIMIT)
            )
        ).all()
        creds: dict[uuid.UUID, tuple[str, str, str, str]] = {}
        for project_id in {row.project_id for row in open_rows}:
            integration = await get_integration(db, project_id)
            if integration is None or not integration.enabled:
                continue
            pat = await secret_service.read_secret(db, SECRET_SCOPE, _secret_key(project_id))
            if not pat:
                continue
            creds[project_id] = (
                integration.api_base_url, integration.repo_owner, integration.repo_name, pat,
            )

    # Phase 2 — poll GitHub with NO session open, reusing ONE client.
    terminal: list[tuple[uuid.UUID, uuid.UUID, str, Optional[str], bool]] = []
    async with httpx.AsyncClient(timeout=15.0) as client:
        for row in open_rows:
            cred = creds.get(row.project_id)
            if cred is None:
                continue
            api_base, repo_owner, repo_name, pat = cred
            summary["checked"] += 1
            gh_pr_url = (
                f"{api_base.rstrip('/')}/repos/{repo_owner}/{repo_name}/pulls/{row.pr_number}"
            )
            if await _ssrf_block_reason(gh_pr_url):
                continue
            try:
                resp = await client.get(gh_pr_url, headers={
                    "Authorization": f"Bearer {pat}",
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "TestLookup/1.0",
                })
                if resp.status_code != 200:
                    continue
                pr = resp.json() or {}
            except Exception:  # noqa: BLE001
                continue
            if pr.get("state") != "closed":
                continue  # still open
            terminal.append(
                (row.id, row.project_id, row.test_fingerprint, row.pr_url, bool(pr.get("merged_at"))),
            )

    # Phase 3 — persist, committing per attempt inside per-batch sessions so
    # one bad row (or a crash) can't lose the whole sweep's progress.
    class _Bot:
        id = None

    for start in range(0, len(terminal), _PR_SWEEP_BATCH):
        batch = terminal[start:start + _PR_SWEEP_BATCH]
        async with AsyncSessionLocal() as db:
            for attempt_id, project_id, fingerprint, pr_url, merged in batch:
                attempt = (
                    await db.execute(select(FixAttempt).where(FixAttempt.id == attempt_id))
                ).scalar_one_or_none()
                if attempt is None or attempt.pr_state != "open":
                    continue  # raced with another writer — nothing to do
                try:
                    await record_fix_outcome(
                        db, project_id, fingerprint, outcome_for_pr(merged),
                        reference=pr_url, comment="fixer outcome poll",
                        current_user=_Bot(),
                    )
                except Exception as exc:  # noqa: BLE001 — missing analysis etc.
                    # Leave pr_state='open' so the NEXT sweep retries — never
                    # advance past a lost feedback row. Roll back to keep the
                    # session usable for the rest of the batch (worker-owned
                    # session: rolling back our own transaction is fine).
                    logger.debug("fixer_outcome_record_skipped", error=str(exc))
                    await db.rollback()
                    continue
                attempt.pr_state = "merged" if merged else "closed"
                summary["merged" if merged else "closed"] += 1
                # Worker-owned commit (see docstring transaction note).
                await db.commit()
    return summary


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
