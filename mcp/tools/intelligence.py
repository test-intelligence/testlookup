"""Tools: Run Intelligence — AI-powered test run analysis, and its history
(AIH-07: get_ai_history, diff_ai_versions, compare_ai_with_previous)."""

from __future__ import annotations

import json
import re

import ai_history_format  # type: ignore[import]
import client as api  # type: ignore[import]
import review_notice  # type: ignore[import]

#: ``get_ai_history``: versions listed by default, and the route's cap.
AI_HISTORY_DEFAULT_LIMIT = 20
AI_HISTORY_MAX_LIMIT = 100
#: ``diff_ai_versions`` resolves version numbers against the whole list (the
#: per-run cap keeps at most 100 versions per kind).
AI_HISTORY_RESOLVE_LIMIT = 100
_VERSION = re.compile(r"^\s*v?(\d+)\s*$", re.IGNORECASE)


def _version_number(value: object) -> int | None:
    """``3`` / ``"3"`` / ``"v3"`` -> 3; anything else (incl. 0, bools) -> None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 1 else None
    match = _VERSION.match(str(value)) if isinstance(value, str) else None
    if match and int(match.group(1)) >= 1:
        return int(match.group(1))
    return None


def register(mcp) -> None:  # noqa: ANN001

    @mcp.tool()
    async def get_run_intelligence(run_id: str) -> str:
        """
        Retrieve the unified Run Intelligence snapshot for a test run.

        Returns: executive summary, failure clusters, release decision,
        dimension scores, baseline diff, defect candidates, and provenance.
        """
        data = await api.get(f"/api/v1/runs/{run_id}/intelligence")
        run = data.get("run", {})
        decision = data.get("release_decision")
        summary = data.get("structured_summary", {})

        lines = [
            f"## Run Intelligence — Build {run.get('build_number', '?')}",
            f"**Status:** {run.get('status', '?')}  |  **Pass Rate:** {run.get('pass_rate', 0):.1f}%  |  **Failed:** {run.get('failed_tests', 0)}",
        ]

        if decision:
            lines.append(f"**Release:** {decision.get('recommendation', '?')} (risk: {decision.get('risk_score', '?')}/100)")

        if summary and summary.get("executive_summary"):
            lines.append(f"\n### Executive Summary\n{summary['executive_summary'][:500]}")

        deep = data.get("deep_pipeline_status", {})
        if deep and deep.get("status") != "never_run":
            lines.append(f"\n**Deep Analysis:** {deep.get('status', '?')} ({deep.get('completed_at', 'in progress')})")

        clusters = data.get("failure_clusters", [])
        if clusters:
            lines.append(f"\n### Failure Clusters ({len(clusters)})")
            for c in clusters[:5]:
                lines.append(f"- **{c.get('label', '?')}** — {c.get('size', 0)} tests, {c.get('criticality_level', '?')}")

        # E8.4: the agent must see whether a person has accepted this AI content.
        lines.extend(review_notice.review_lines(data))
        return "\n".join(lines)

    @mcp.tool()
    async def refresh_intelligence(run_id: str) -> str:
        """Force-refresh the cached intelligence snapshot for a run."""
        await api.post(f"/api/v1/runs/{run_id}/intelligence/refresh")
        return f"Intelligence snapshot refreshed for run {run_id}."

    @mcp.tool()
    async def get_run_summary(run_id: str, mode: str = "executive") -> str:
        """
        Get a mode-specific narrative summary for a run.
        Modes: executive, developer, manager.
        """
        data = await api.get(f"/api/v1/runs/{run_id}/summary", params={"mode": mode})
        summary = data.get("executive_summary") or data.get("markdown_report", "No summary available.")
        return "\n".join([f"## {mode.title()} Summary", "", str(summary), *review_notice.review_lines(data)])

    @mcp.tool()
    async def get_ai_history(run_id: str, limit: int = AI_HISTORY_DEFAULT_LIMIT) -> dict:
        """
        The stored versions of ONE run's AI analysis, newest first: when each
        was generated, by which engine (rules / ML / LLM, or mixed) and model,
        whether a fallback was used, its release verdict and risk, and how many
        failing tests it covered. Every completed AI pipeline of the run adds
        a version and keeps the earlier ones. Read-only.

        Returns {ok, summary, history}: `summary` is compact markdown,
        `history` the REST body of GET /api/v1/runs/{run_id}/ai-history
        (kind=analysis_digest). Reading it: root causes and headlines are AI
        text, not facts. An engine change between versions (rules vs LLM) is
        not a product change. A null figure is "not known", never 0. To see
        what changed between two versions use `diff_ai_versions`; to compare
        with the previous run use `compare_ai_with_previous`.

        Errors come back as {ok: false, status_code, detail}: an unknown run,
        or one you cannot read, is a 404; 503 means the AI-history store is
        unavailable.

        Args:
            run_id: Test run UUID (from list_test_runs).
            limit: How many versions to list (1-100, default 20).
        """
        limit = max(1, min(int(limit), AI_HISTORY_MAX_LIMIT))
        try:
            body = await api.get(
                f"/api/v1/runs/{run_id}/ai-history",
                params={"kind": "analysis_digest", "limit": limit},
            )
        except Exception as exc:  # noqa: BLE001 - every failure is a structured result
            return api.error_payload(exc)
        if not isinstance(body, dict):
            return {"ok": False, "error": "unexpected response from the AI history endpoint"}
        summary = "\n".join([ai_history_format.history_markdown(body), *review_notice.review_lines(body)])
        return {"ok": True, "summary": summary, "history": body}

    @mcp.tool()
    async def diff_ai_versions(run_id: str, from_version: int, to_version: int | str = "latest") -> dict:
        """
        What changed between two stored versions of ONE run's AI analysis:
        provenance first (engine, model, fallback), then the release verdict,
        failure categories, failure clusters (added / removed / resized),
        tests whose category, confidence or flakiness changed, and actions.
        Read-only.

        Returns {ok, summary, diff}: `summary` is compact markdown, `diff` the
        REST body of GET /api/v1/runs/{run_id}/ai-history/diff. Reading it:
        when `diff.provenance.changed` is true the two versions were made by
        different engines (rules vs LLM) and category changes may reflect the
        engine, not the product. Root-cause text is AI text; a reworded root
        cause alone is not a material change. `identical: true` means the same
        content.

        Errors come back as {ok: false, ...}: a version number that is not
        stored for this run is {ok: false, error, stored_versions} (no diff is
        attempted); an unknown run is a 404.

        Args:
            run_id: Test run UUID (from list_test_runs).
            from_version: The older version number (1, 2, ... from get_ai_history).
            to_version: The newer version: a number, "latest" (the newest
                stored version, default) or "live" (the run's current data).
        """
        frm = _version_number(from_version)
        to_text = str(to_version).strip().lower() if isinstance(to_version, str) else None
        to_number = None if to_text in ("latest", "live") else _version_number(to_version)
        if frm is None or (to_text not in ("latest", "live") and to_number is None):
            return {
                "ok": False,
                "error": "from_version must be a version number (1, 2, ...); to_version a number, "
                "'latest' or 'live'.",
            }
        try:
            listing = await api.get(
                f"/api/v1/runs/{run_id}/ai-history",
                params={"kind": "analysis_digest", "limit": AI_HISTORY_RESOLVE_LIMIT},
            )
        except Exception as exc:  # noqa: BLE001 - every failure is a structured result
            return api.error_payload(exc)
        ids = {
            v.get("version"): v.get("version_id")
            for v in (listing.get("versions") if isinstance(listing, dict) else None) or []
            if isinstance(v, dict) and isinstance(v.get("version"), int) and v.get("version_id")
        }
        stored = sorted(ids)
        for number in (frm, to_number):
            if number is not None and number not in ids:
                return {
                    "ok": False,
                    "error": f"v{number} is not a stored AI version of this run "
                    f"(stored: {', '.join(f'v{n}' for n in stored) or 'none'}).",
                    "stored_versions": stored,
                }
        to_param = ids[to_number] if to_number is not None else to_text
        try:
            body = await api.get(
                f"/api/v1/runs/{run_id}/ai-history/diff",
                params={"from": ids[frm], "to": to_param},
            )
        except Exception as exc:  # noqa: BLE001 - every failure is a structured result
            return api.error_payload(exc)
        if not isinstance(body, dict):
            return {"ok": False, "error": "unexpected response from the AI diff endpoint"}
        summary = "\n".join([ai_history_format.diff_markdown(body), *review_notice.review_lines(body)])
        return {"ok": True, "summary": summary, "diff": body}

    @mcp.tool()
    async def compare_ai_with_previous(run_id: str, baseline_run_id: str | None = None) -> dict:
        """
        What changed in a run's AI analysis since the previous run of the
        same suite on the same branch (or since `baseline_run_id`): the
        release verdict, new and resolved root causes, tests whose category
        changed, and a summary. Use it for "what changed in the AI analysis
        since the previous checkout run?". Read-only.

        Returns {ok, summary, verdict_regressed, compare}: `summary` is compact
        markdown, `compare` the REST body of GET /api/v1/runs/{run_id}/ai-compare,
        `verdict_regressed` true when the AI verdict got worse, false when it
        did not, null when it cannot be judged. Reading it: the summary is
        labelled "deterministic summary" when no LLM wrote it (offline or
        over the cost cap) and "AI narrative" when one did; root causes are AI
        text. An engine change (rules vs LLM) between the runs is not a
        product change (`diff.provenance.note`). `reason` "baseline_not_analysed"
        or "current_not_analysed" means NOT COMPARED, which is not "no change";
        "no_previous_run" means there is no earlier completed run of the suite
        on the branch.

        Errors come back as {ok: false, status_code, detail}: an unknown run,
        or a baseline in another project, is a 404.

        Args:
            run_id: Test run UUID (from list_test_runs).
            baseline_run_id: Compare with this run instead of the previous run
                of the same suite and branch (same project).
        """
        try:
            body = await api.get(
                f"/api/v1/runs/{run_id}/ai-compare",
                params={"baseline_run_id": baseline_run_id},
            )
        except Exception as exc:  # noqa: BLE001 - every failure is a structured result
            return api.error_payload(exc)
        if not isinstance(body, dict):
            return {"ok": False, "error": "unexpected response from the AI compare endpoint"}
        summary = "\n".join([ai_history_format.compare_markdown(body), *review_notice.review_lines(body)])
        return {
            "ok": True,
            "summary": summary,
            "verdict_regressed": ai_history_format.verdict_regressed(body),
            "compare": body,
        }
