"""Inline agent input (W23c AGT-11): redaction at rest, the hash, the frozen seed, the exclusions.

``POST /api/v1/agents/{agent_id}/invoke`` with ``inline_input`` (``agent.summary.v1`` only,
anchored to a stored test run of the project — D18) stores the input REDACTED on the
invocation row and runs the agent on it instead of the server-assembled run facts:

* :func:`prepare` validates the raw object against ``InlineSummaryInputV1``, scrubs every
  string (``untrusted_input.scrub``), clips it back under its cap and re-validates; its result
  is ``agent_invocations.input_payload`` and ``input_sha256`` (``stable_json_sha256`` of the
  redacted object — the bytes the model sees and the reviewer reads). The raw payload is never
  stored, logged or hashed into anything persistent.
* :func:`seed_state` turns that FROZEN payload into the graph-state keys the unchanged summary
  stage reads, every string wrapped by ``untrusted_input`` — the one path inline text takes to
  the model. :func:`frozen_inline_seed` re-reads it by ``pipeline_run_id`` on every attempt, so a
  retry / resume replays the same input, never the request.
* :func:`not_inline_pipeline` / :func:`is_inline_plan` keep an inline pipeline's output out of
  every run rollup (contract §2.2 I-8): the run's report review, its latest summary, release /
  decision / intelligence readers, notifications, digests and action proposals.

Services flush, routers commit: nothing here commits, opens a session or logs content.
"""
from __future__ import annotations

import json
import uuid
from typing import Any, Mapping, Optional

from sqlalchemy import ColumnElement, exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.inline_agent_input import InlineSummaryInputV1
from app.models.postgres import AgentInvocation, AgentPipelineRun, TestRun
from app.services.canonical_json import stable_json_sha256
from app.services.inline_input_vocab import (
    EXCERPTS_PER_FAILURE,
    LOG_EXCERPT_MAX,
    ROOT_CAUSE_MAX,
    SUITE_NAME_MAX,
    TEST_NAME_MAX,
    WRAP_LIMITS,
)
from app.services.untrusted_input import excerpt_slices, scrub, wrap_untrusted

#: The workflow-plan key that marks an inline invocation's plan (``agent_planner``) and the
#: ``execution_metadata`` key the pipeline row carries; its value is ``input_sha256``.
PLAN_INPUT_KEY = "invocation_input_sha256"
#: The graph-state key the seed sets; the summary stage's code-only guards read it.
STATE_INPUT_KEY = "_inline_input_sha256"
#: ``evidence_references[].source`` of a seeded excerpt.
EVIDENCE_SOURCE = "inline_input"
#: ``test_run_data.status`` of a seeded state (the facts are the caller's, not the run's).
SEEDED_STATUS = "inline_input"

# Every free-text field and its cap (the stored, scrubbed value is clipped back under it).
_STRING_CAPS: dict[str, int] = {
    "test_name": TEST_NAME_MAX,
    "suite_name": SUITE_NAME_MAX,
    "root_cause": ROOT_CAUSE_MAX,
    "log_excerpt": LOG_EXCERPT_MAX,
}


def canonical_bytes(payload: Mapping[str, Any]) -> bytes:
    """Compact, key-sorted UTF-8 JSON of ``payload`` — what the 413 cap measures (raw, as received)."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _scrub_capped(value: Optional[str], cap: int) -> Optional[str]:
    if value is None:
        return None
    # A placeholder can be longer than what it replaced ("[REDACTED_EMAIL]" for "a@b.io"):
    # clip, then strip as the model would, so the re-validation below cannot fail on a cap.
    return scrub(value)[:cap].strip()


def prepare(raw: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    """``(redacted payload, input_sha256)`` for a raw ``inline_input``.

    Raises ``pydantic.ValidationError`` when ``raw`` is not an ``InlineSummaryInputV1`` (the route
    answers 422 ``inline_input_invalid``). Every string is scrubbed BEFORE anything is stored,
    hashed or seeded; the scrubbed object is validated again and dumped as canonical JSON.
    """
    model = InlineSummaryInputV1.model_validate(raw)
    dumped = model.model_dump(mode="json")
    for failure in dumped["failures"]:
        for field, cap in _STRING_CAPS.items():
            failure[field] = _scrub_capped(failure.get(field), cap)
        if not failure["test_name"]:
            # Scrubbing never empties a name (a placeholder replaces what it removes); kept
            # total anyway so the re-validation's min_length cannot reject a valid request.
            failure["test_name"] = "[REDACTED]"
    redacted = InlineSummaryInputV1.model_validate(dumped).model_dump(mode="json")
    return redacted, stable_json_sha256(redacted)


def _wrapped(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    return wrap_untrusted(text, limit=limit) if text else ""


# Upper bounds of what the summary stage's context adds around the seeded values (its header
# lines, a bullet's brackets and labels, a catalogue entry's id and source, the catalogue's
# header), with a margin. Used ONLY to decide how many excerpt slices to seed: the maximal
# payload (10 failures at every cap) renders ~9 000 characters with every slice, over the
# default 7 788-character budget (``_MAX_CONTEXT_TOKENS`` × ``TRUNCATION_CHARS_PER_TOKEN``), and a
# cut tail would leave the last data block unterminated (contract K-4).
_CONTEXT_FIXED_CHARS = 600
_BULLET_OVERHEAD_CHARS = 60
_ROOT_CAUSE_FALLBACK_CHARS = 60
_CATALOGUE_HEADER_CHARS = 140
_CATALOGUE_ENTRY_OVERHEAD_CHARS = 40


def _default_context_chars() -> int:
    # Lazy: the summary stage imports this module for its state key.
    from app.agents.summary_agent import _MAX_CONTEXT_TOKENS
    from app.services.resilience import TRUNCATION_CHARS_PER_TOKEN

    return int(_MAX_CONTEXT_TOKENS) * int(TRUNCATION_CHARS_PER_TOKEN)


def _fit_excerpts(
    rows: list[dict[str, Any]], slices: list[list[str]], *, header_chars: int, context_chars: int,
) -> list[list[str]]:
    """The slices to seed: round-robin (every failure's first slice, then the second ones), in
    catalogue order, while the estimated context stays inside ``context_chars``."""
    from app.services.evidence_catalogue import CATALOGUE_LIMIT

    used = _CONTEXT_FIXED_CHARS + header_chars + _CATALOGUE_HEADER_CHARS
    for row in rows:
        used += (
            len(row["test_name"]) + len(row["suite_name"] or "")
            + (len(row["root_cause_summary"]) or _ROOT_CAUSE_FALLBACK_CHARS) + _BULLET_OVERHEAD_CHARS
        )
    kept: list[list[str]] = [[] for _ in slices]
    taken = 0
    for depth in range(EXCERPTS_PER_FAILURE):
        for index, candidates in enumerate(slices):
            if depth >= len(candidates) or taken >= CATALOGUE_LIMIT:
                continue
            cost = len(rows[index]["test_name"]) + len(candidates[depth]) + _CATALOGUE_ENTRY_OVERHEAD_CHARS
            if used + cost > context_chars:
                return kept
            used += cost
            kept[index].append(candidates[depth])
            taken += 1
    return kept


def seed_state(
    payload: Mapping[str, Any],
    *,
    build_number: str,
    branch: Optional[str],
    context_chars: Optional[int] = None,
) -> dict[str, Any]:
    """The graph-state keys the summary stage reads, built from a FROZEN (scrubbed) payload.

    Every string is wrapped as a data block cut to the exact cap the unchanged downstream code
    applies (``WRAP_LIMITS``). The run facts are TRUE of the supplied input: ``n`` tests, ``n``
    failed, a 0.0 % pass rate, no invented passes or skips. Excerpt slices are seeded only while
    the estimated context fits ``context_chars`` (default: the summary stage's own budget), so
    the context is never cut through a data block at the default configuration.
    """
    rows: list[dict[str, Any]] = []
    slices: list[list[str]] = []
    for failure in list(payload.get("failures") or []):
        name = _wrapped(failure.get("test_name"), WRAP_LIMITS["test_name"])
        suite = _wrapped(failure.get("suite_name"), WRAP_LIMITS["suite_name"]) or None
        category = str(failure.get("failure_category") or "UNKNOWN")
        rows.append({
            "test_name": name,
            "suite_name": suite,
            "failure_category": category,
            "confidence_score": int(failure.get("confidence_score") or 0),
            "root_cause_summary": _wrapped(failure.get("root_cause"), WRAP_LIMITS["root_cause"]),
            "is_flaky": category == "FLAKY",
        })
        slices.append(excerpt_slices(
            str(failure.get("log_excerpt") or ""), limit=WRAP_LIMITS["excerpt"], parts=EXCERPTS_PER_FAILURE,
        ))
    kept = _fit_excerpts(
        rows,
        slices,
        header_chars=len(str(build_number or "")) + len(str(branch or "")),
        context_chars=_default_context_chars() if context_chars is None else int(context_chars),
    )
    analyses: dict[str, dict[str, Any]] = {}
    for index, (row, excerpts) in enumerate(zip(rows, kept), start=1):
        analyses[f"inline-{index:02d}"] = {
            **row,
            "evidence_references": [
                {"source": EVIDENCE_SOURCE, "kind": "log_excerpt", "excerpt": excerpt} for excerpt in excerpts
            ],
            "meta": {"test_name": row["test_name"], "suite_name": row["suite_name"], "inline": True},
        }
    count = len(analyses)
    return {
        "analyses": analyses,
        "failed_test_ids": list(analyses),
        "test_run_data": {
            "build_number": build_number,
            "branch": branch,
            "total_tests": count,
            "failed_tests": count,
            "pass_rate": 0.0,
            "status": SEEDED_STATUS,
        },
        "total_tests": count,
        "pass_rate": 0.0,
        "branch": branch,
    }


def not_inline_pipeline() -> ColumnElement[bool]:
    """SQL: this ``agent_pipeline_runs`` row is NOT an inline invocation's pipeline.

    Correlated on ``AgentPipelineRun.id``; served by the unique ``ux_agent_invocations_pipeline_run``.
    A stored-subject invocation (``input_sha256 IS NULL``) and every ordinary pipeline pass.
    """
    return ~exists(
        select(1).where(
            AgentInvocation.pipeline_run_id == AgentPipelineRun.id,
            AgentInvocation.input_sha256.is_not(None),
        )
    )


def is_inline_plan(plan: Optional[Mapping[str, Any]]) -> bool:
    """Whether a frozen workflow plan belongs to an inline invocation."""
    return isinstance(plan, Mapping) and PLAN_INPUT_KEY in plan


def is_inline_state(state: Optional[Mapping[str, Any]]) -> bool:
    """Whether a graph state belongs to an inline-input invocation.

    The compiled graph keeps only the keys ``WorkflowState`` declares, so a node reads it from
    the frozen plan the state always carries (``initial_workflow_plan``, rebuilt into
    ``workflow_plan``); the seed's own ``STATE_INPUT_KEY`` counts too (a stage run outside the
    graph).
    """
    if not isinstance(state, Mapping):
        return False
    return (
        bool(state.get(STATE_INPUT_KEY))
        or is_inline_plan(state.get("initial_workflow_plan"))
        or is_inline_plan(state.get("workflow_plan"))
    )


def _pipeline_uuid(pipeline_run_id: Any) -> Optional[uuid.UUID]:
    try:
        return pipeline_run_id if isinstance(pipeline_run_id, uuid.UUID) else uuid.UUID(str(pipeline_run_id))
    except (TypeError, ValueError):
        return None


async def frozen_inline_payload(db: AsyncSession, pipeline_run_id: Any) -> Optional[tuple[dict[str, Any], str]]:
    """``(input_payload, input_sha256)`` of the pipeline's invocation, or ``None`` (not inline)."""
    pipeline_uuid = _pipeline_uuid(pipeline_run_id)
    if pipeline_uuid is None:
        return None
    row = (
        await db.execute(
            select(AgentInvocation.input_payload, AgentInvocation.input_sha256).where(
                AgentInvocation.pipeline_run_id == pipeline_uuid,
                AgentInvocation.input_sha256.is_not(None),
            )
        )
    ).first()
    if row is None or not isinstance(row[0], dict) or not row[1]:
        return None
    return dict(row[0]), str(row[1])


async def frozen_inline_seed(db: AsyncSession, pipeline_run_id: Any) -> Optional[tuple[dict[str, Any], str]]:
    """``(seeded graph-state keys, input_sha256)`` from the FROZEN payload, or ``None``.

    The build number and branch are the ANCHOR run's (read here, through the invocation's own
    ``test_run_id``), so a resume that enters with ``build_number="resume"`` seeds the same state.
    """
    frozen = await frozen_inline_payload(db, pipeline_run_id)
    if frozen is None:
        return None
    payload, input_sha256 = frozen
    anchor = (
        await db.execute(
            select(TestRun.build_number, TestRun.branch)
            .join(AgentInvocation, AgentInvocation.test_run_id == TestRun.id)
            .where(AgentInvocation.pipeline_run_id == _pipeline_uuid(pipeline_run_id))
        )
    ).first()
    build_number = str(anchor[0]) if anchor is not None and anchor[0] is not None else "invocation"
    branch = str(anchor[1]) if anchor is not None and anchor[1] is not None else None
    seeded = seed_state(payload, build_number=build_number, branch=branch)
    seeded[STATE_INPUT_KEY] = input_sha256
    return seeded, input_sha256


async def inline_review_subject(db: AsyncSession, pipeline_run_id: Any) -> Optional[tuple[str, str]]:
    """``(invocation id, input_sha256)`` when the pipeline is an inline invocation's (I-7), else ``None``."""
    pipeline_uuid = _pipeline_uuid(pipeline_run_id)
    if pipeline_uuid is None:
        return None
    row = (
        await db.execute(
            select(AgentInvocation.id, AgentInvocation.input_sha256).where(
                AgentInvocation.pipeline_run_id == pipeline_uuid,
                AgentInvocation.input_sha256.is_not(None),
            )
        )
    ).first()
    if row is None:
        return None
    return str(row[0]), str(row[1])


__all__ = [
    "EVIDENCE_SOURCE",
    "PLAN_INPUT_KEY",
    "SEEDED_STATUS",
    "STATE_INPUT_KEY",
    "canonical_bytes",
    "frozen_inline_payload",
    "frozen_inline_seed",
    "inline_review_subject",
    "is_inline_plan",
    "is_inline_state",
    "not_inline_pipeline",
    "prepare",
    "seed_state",
]
