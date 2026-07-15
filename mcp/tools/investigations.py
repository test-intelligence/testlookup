"""MCP tools for the Investigator agent (Agentic plan AI-5, over AI-1's API).

Wraps the Investigator REST contract (#383) so any MCP-capable agent can run
the hypothesis-loop Investigator end-to-end:

  * ``start_investigation``  — POST /api/v1/runs/{run_id}/investigations
  * ``get_investigation``    — GET  /api/v1/investigations/{id}
  * ``list_investigations``  — GET  /api/v1/projects/{id}/investigations

All results are structured dicts (the write-tool shape from US-14.1):
failures come back as ``{ok: false, status_code, detail}`` carrying the
backend's real reason — the Investigator API uses 403 (agent policy
disabled), 409 (already running), and 429 (daily budget exhausted) as
first-class, actionable answers rather than errors to retry.

The Investigator itself runs in **shadow mode**: it diagnoses and proposes,
it never acts. Starting one is therefore a safe write — it consumes the
project's investigation budget but cannot change any test, quarantine, or
gate state.
"""

from __future__ import annotations

from typing import Any

import client as api  # type: ignore[import]

# Character budget for long free-text fields in get_investigation output
# (hypothesis summaries, evidence lines, the verdict narrative). Full text
# stays available in the UI / raw API; the MCP render favours a compact,
# token-friendly payload.
_MAX_TEXT = 400
_MAX_EVIDENCE_ITEMS = 5


def _truncate(value: Any, limit: int = _MAX_TEXT) -> Any:
    """Truncate long strings with an explicit ellipsis marker; pass through
    everything else untouched."""
    if isinstance(value, str) and len(value) > limit:
        return value[: limit - 1].rstrip() + "…"
    return value


def _compact_hypothesis(hyp: dict) -> dict:
    """Compact-render one hypothesis: keep the structure, cap the prose.

    Evidence entries can be dicts or strings; both are truncated. Only the
    first ``_MAX_EVIDENCE_ITEMS`` are kept, with an honest marker for the
    rest.
    """
    out = dict(hyp)
    out["summary"] = _truncate(out.get("summary"))
    evidence = list(out.get("evidence") or [])
    kept = []
    for item in evidence[:_MAX_EVIDENCE_ITEMS]:
        if isinstance(item, dict):
            kept.append({k: _truncate(v) for k, v in item.items()})
        else:
            kept.append(_truncate(item))
    if len(evidence) > _MAX_EVIDENCE_ITEMS:
        kept.append(f"… {len(evidence) - _MAX_EVIDENCE_ITEMS} more evidence item(s) elided")
    out["evidence"] = kept
    return out


def _compact_detail(detail: dict) -> dict:
    """Compact-render an InvestigationDetail payload for MCP consumption.

    Preserves every key of the pinned wire shape; only long free-text fields
    (hypothesis summaries/evidence, the verdict narrative) are truncated.
    """
    out = dict(detail)
    out["hypotheses"] = [
        _compact_hypothesis(h) if isinstance(h, dict) else h
        for h in (detail.get("hypotheses") or [])
    ]
    verdict = detail.get("verdict")
    if isinstance(verdict, dict):
        verdict = dict(verdict)
        verdict["narrative"] = _truncate(verdict.get("narrative"), 1200)
        out["verdict"] = verdict
    return out


def _clamp_limit(limit: Any, default: int = 20, lo: int = 1, hi: int = 100) -> int:
    """Clamp a caller-supplied limit into the API's accepted range."""
    try:
        value = int(limit)
    except (TypeError, ValueError):
        return default
    return max(lo, min(value, hi))


def register(mcp) -> None:  # noqa: ANN001

    @mcp.tool()
    async def start_investigation(run_id: str) -> dict:
        """
        SIDE EFFECT (safe): start the Investigator agent for a test run.

        The Investigator is SHADOW-MODE ONLY — it weighs five root-cause
        hypotheses (infra, commit onset, environment drift, known-flaky,
        regression) against the run's evidence and produces a verdict with
        proposed actions. It never executes actions itself; acting on the
        verdict (quarantine proposals, defects, assignments) is done by YOU
        via the existing write tools, with the human in the loop.

        Returns {ok, investigation_id} on 202. Structured failures:
          - 403 → the project's agent policy disables the investigator
            (detail says how a QA Lead can enable it),
          - 409 → an investigation is already running for this run
            (detail carries its investigation_id — poll that instead),
          - 429 → the project's max_runs_per_day budget is exhausted today.

        Args:
            run_id: Test run UUID to investigate.
        """
        try:
            data = await api.post(f"/api/v1/runs/{run_id}/investigations")
        except Exception as exc:
            return api.error_payload(exc)
        return {
            "ok": True,
            "action": "investigation_started",
            "investigation_id": (data or {}).get("investigation_id"),
            "run_id": run_id,
            "note": (
                "Shadow-mode investigation queued. Poll get_investigation "
                "until status is terminal, then act on the verdict yourself "
                "via the write tools."
            ),
        }

    @mcp.tool()
    async def get_investigation(investigation_id: str) -> dict:
        """
        Full detail for one investigation: status, mode, budget vs spend,
        the five hypothesis boards (status/confidence/evidence), and — once
        complete — the verdict (primary_cause, confidence, narrative,
        recommended_actions). Long prose fields are compacted; the web UI
        shows the full text.

        Poll this after start_investigation; terminal statuses are
        completed / failed / cancelled.

        Args:
            investigation_id: Investigation UUID (from start_investigation
                or list_investigations).
        """
        try:
            data = await api.get(f"/api/v1/investigations/{investigation_id}")
        except Exception as exc:
            return api.error_payload(exc)
        return {"ok": True, "investigation": _compact_detail(data or {})}

    @mcp.tool()
    async def list_investigations(project_id: str, limit: int = 20) -> dict:
        """
        Recent investigations for a project, newest first — id, run,
        status, primary cause + confidence when complete.

        Args:
            project_id: Project UUID.
            limit: Max entries to return (1-100, default 20).
        """
        try:
            data = await api.get(
                f"/api/v1/projects/{project_id}/investigations",
                params={"limit": _clamp_limit(limit), "offset": 0},
            )
        except Exception as exc:
            return api.error_payload(exc)
        data = data or {}
        return {
            "ok": True,
            "items": data.get("items", []),
            "total": data.get("total", 0),
        }
