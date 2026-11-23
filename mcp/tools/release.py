"""Tool: check_release_readiness.

The verdict is the server's. ``GET /api/v1/metrics/summary`` already decides
GREEN / AMBER / RED from the project's active release-gate policy (pass-rate
bands plus hard caps) and says which hard caps fired; ``GET
/api/v1/releases/{id}/gate`` returns a release's stored GO / NO_GO /
NOT_EVALUATED decision with its blocking reasons. This tool renders those. It
used to apply its own literal thresholds to KPI values it read from a key the
API does not send, so every pass rate read 0% and every answer was a blocker.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx

import client as api  # type: ignore[import]
import release_resolver  # type: ignore[import]
from tools.metrics import (  # type: ignore[import]
    DEFAULT_WINDOW_DAYS,
    PENDING_TEXT,
    SUMMARY_KPIS,
    describe_downgrade,
    kpi_value,
    readiness_of,
)

_GATE_ICON = {"GO": "🟢", "CONDITIONAL_GO": "🟡", "NO_GO": "🔴", "NOT_EVALUATED": "⚪"}
#: The project signal's icons (the same as ``tools.metrics``'s heading uses).
_READINESS_ICON = {"GREEN": "🟢", "AMBER": "🟡", "RED": "🔴"}


def _never_evaluated(response: httpx.Response) -> bool:
    """Whether a gate 404 is "this release has no stored verdict", not "no such release"."""
    try:
        detail = response.json().get("detail")
    except Exception:  # noqa: BLE001 - a body that is not JSON is not that answer
        return False
    return isinstance(detail, str) and "not been evaluated" in detail.lower()


def _reason_text(reason: Any) -> str:
    if isinstance(reason, dict):
        for key in ("reason", "message", "detail", "text"):
            if isinstance(reason.get(key), str):
                return reason[key]
    return str(reason)


async def _release_gate(release_id: str) -> str:
    try:
        release_id = str(uuid.UUID(str(release_id).strip()))
    except ValueError:
        return f"`{release_id}` is not a release UUID."
    try:
        gate = await api.get(api.path("/api/v1/releases/{release_id}/gate", release_id=release_id))
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return (
                f"## ⚪ Release Gate: **never evaluated**\n\n"
                f"Release `{release_id}` has no recorded gate decision yet (or does not exist). "
                f"A QA lead records one with POST /api/v1/releases/{release_id}/gate/evaluate."
            )
        raise

    verdict = str(gate.get("verdict") or "UNKNOWN")
    reasons = [_reason_text(r) for r in gate.get("blocking_reasons") or []]
    scorecard = gate.get("scorecard") or {}

    lines = [
        f"## {_GATE_ICON.get(verdict, '⚪')} Release Gate: **{verdict}**",
        f"*Stored decision for release `{release_id}`"
        + (f", decided {gate['decided_at']}" if gate.get("decided_at") else "")
        + " — a snapshot, not a live re-evaluation.*",
    ]
    if scorecard:
        lines += [
            "",
            "| Evidence | Value |",
            "|----------|-------|",
            f"| Tests with evidence | {scorecard.get('evidence_count', 0)} of {scorecard.get('denominator', 0)} |",
            f"| Runs | {scorecard.get('run_count', 0)} |",
            f"| Measured (above the evidence floor) | {'yes' if scorecard.get('measured') else 'no'} |",
        ]
    if reasons:
        heading = "### ❌ Blocking reasons" if verdict == "NO_GO" else "### Reasons"
        lines += ["", heading]
        lines += [f"- {r}" for r in reasons]
    elif verdict == "GO":
        lines += ["", "No blocking reasons were recorded."]
    return "\n".join(lines)


def register(mcp) -> None:  # noqa: ANN001

    @mcp.tool()
    async def get_release_gate(release: str, project: str | None = None, evaluate: bool = False) -> dict:
        """
        A release's gate verdict (GO / NO_GO / NOT_EVALUATED; the release
        gate has no conditional verdict) as data: verdict, pass rate, evidence, blockers and when it was decided.
        Reads the stored decision the release page shows; ``evaluate=True``
        records a fresh one first (QA lead or a project API key; it is appended
        to the release's gate history).

        Args:
            release: Release id, or its exact name / version (case-insensitive).
                A name several releases share is an error listing them with
                their ids: never a guess.
            project: Optional project id or name, to narrow a release name.
            evaluate: Record a new verdict before answering (default False).

        Returns ``{ok: true, release_id, evaluated: false}`` when the release
        has never been evaluated, so "not looked at" is never read as a verdict.
        """
        try:
            pid = await release_resolver.resolve_project(project)
            rid = await release_resolver.resolve_release(release, pid)
        except release_resolver.UnresolvedName as exc:
            return release_resolver.unresolved_payload(exc)
        except Exception as exc:
            return api.error_payload(exc)
        try:
            if evaluate:
                gate = await api.post(api.path("/api/v1/releases/{release_id}/gate/evaluate", release_id=rid))
            else:
                gate = await api.get(api.path("/api/v1/releases/{release_id}/gate", release_id=rid))
        except httpx.HTTPStatusError as exc:
            # TL-QA-24: the gate route has two 404s. Only "not evaluated yet" is
            # an answer; "Release not found" (or one the caller cannot read) is
            # an error, and used to read as a release awaiting its first verdict.
            if exc.response.status_code == 404 and not evaluate and _never_evaluated(exc.response):
                return {"ok": True, "release_id": rid, "evaluated": False,
                        "detail": "This release has not been evaluated yet; evaluate=True records a verdict (QA lead)."}
            return api.error_payload(exc)
        except Exception as exc:
            return api.error_payload(exc)
        gate = gate or {}
        card = gate.get("scorecard") or {}
        reasons = gate.get("blocking_reasons_display") or gate.get("blocking_reasons") or []
        return {
            "ok": True,
            "release_id": rid,
            "evaluated": True,
            "verdict": gate.get("verdict"),
            "recorded": gate.get("recorded"),
            "decided_at": gate.get("decided_at"),
            "pass_rate": card.get("pass_rate"),
            "evidence_count": card.get("evidence_count"),
            "run_count": card.get("run_count"),
            "measured": card.get("measured"),
            "blockers": [_reason_text(r) for r in reasons],
            "blocking_total": gate.get("blocking_total", len(reasons)),
        }

    @mcp.tool()
    async def check_release_readiness(
        project_id: str,
        days: int = DEFAULT_WINDOW_DAYS,
        release_id: str | None = None,
    ) -> str:
        """
        Is this project ready to release? Without ``release_id``: the stored
        gate verdict of each in-progress release of the project (GO / NO_GO /
        NOT_EVALUATED, led by any NO_GO), then the project's windowed
        readiness signal (GREEN / AMBER / RED, the /overview page's, decided
        by the server from the release-gate policy) with the hard caps that
        fired. The project signal is not a release verdict. "Pending" means no
        executions in the window, not RED.

        With ``release_id``, returns that release's stored gate decision
        (GO / NO_GO / NOT_EVALUATED) and its blocking reasons instead.

        Args:
            project_id: Project UUID.
            days: Analysis window for the project signal (1-90, default 30).
            release_id: Optional release UUID; reads the release's own gate decision.
        """
        if release_id:
            return await _release_gate(release_id)

        data: dict[str, Any] = await api.get(
            api.path("/api/v1/metrics/summary"),
            params={"project_id": project_id, "days": days},
        ) or {}

        if not any(key in data for key in SUMMARY_KPIS):
            return (
                f"No readiness data was returned for project {project_id} "
                f"(no access to it, or no data in the last {days} days)."
            )

        readiness = readiness_of(data)
        band = data.get("release_readiness_band")
        downgrades = [d for d in data.get("release_readiness_downgrades") or [] if isinstance(d, str)]
        pass_rate = kpi_value(data, "avg_pass_rate_7d")
        total_executions = int(kpi_value(data, "total_executions_7d"))

        # D-120: the release answer is the release's stored gate, not the
        # project's windowed signal. Read the in-progress releases first so a
        # NO_GO release leads instead of hiding under an AMBER project.
        active, active_error = await _active_release_gates(project_id)
        no_go = [r for r in active if r["verdict"] == "NO_GO"]

        lines: list[str] = []
        for r in no_go:
            lines += [
                f"## 🔴 Active release {r['name']}: **NO_GO**, {_blockers_text(r['blocking_total'])}",
                "*The release's stored gate decision"
                + (f", decided {r['decided_at']}" if r.get("decided_at") else "")
                + ".*",
            ]
            lines += [f"- {reason}" for reason in r["reasons"]]
            lines.append("")
        if no_go:
            lines.append(f"### {_project_heading(data, days)}")
        else:
            lines.append(f"## {_project_heading(data, days)}")
        lines += [
            (
                f"*Project signal over the last {days} days — {total_executions} test execution(s); "
                "not a release verdict*"
            ),
            "",
            "### Key Metrics",
            "| Metric | Value |",
            "|--------|-------|",
            f"| Avg Pass Rate | **{pass_rate:.1f}%** |",
            f"| Active Defects | {int(kpi_value(data, 'active_defects'))} |",
            f"| Flaky Tests | {int(kpi_value(data, 'flaky_test_count'))} |",
            f"| New Failures (24h) | {int(kpi_value(data, 'new_failures_24h'))} |",
            f"| Total Executions ({days}d) | {total_executions} |",
        ]

        lines += ["", "### How this verdict was decided"]
        if readiness is None:
            lines.append(f"{PENDING_TEXT}: readiness is assessed once runs land in this window.")
        elif isinstance(band, str) and band:
            lines.append(
                f"The project's release-gate policy puts a {pass_rate:.1f}% pass rate in the "
                f"**{band}** band"
                + ("; a hard cap that fires forces RED." if downgrades else ".")
            )
        else:
            lines.append(
                "No release-gate policy is active for this project, so the server's "
                "built-in readiness rule decided."
            )

        if downgrades:
            lines += ["", "### ❌ Blockers (policy hard caps that fired)"]
            lines += [f"- {describe_downgrade(d)}" for d in downgrades]

        others = [r for r in active if r["verdict"] != "NO_GO"]
        if others or active_error:
            lines += ["", "### Active releases"]
            for r in others:
                if r["verdict"] is None:
                    lines.append(f"- {r['name']}: gate not evaluated yet")
                else:
                    lines.append(
                        f"- {r['name']}: {_GATE_ICON.get(r['verdict'], '⚪')} **{r['verdict']}**"
                        + (f", {_blockers_text(r['blocking_total'])}" if r["blocking_total"] else "")
                    )
            if active_error:
                lines.append(f"- The project's in-progress releases could not be read ({active_error}).")

        lines += ["", "### Recommendation"]
        if no_go:
            names = ", ".join(r["name"] for r in no_go)
            lines.append(
                f"🔴 **No-Go** — the stored gate of {names} says NO_GO. Resolve its blockers, "
                "then record a fresh verdict with `get_release_gate(evaluate=True)`."
            )
        elif readiness is None:
            lines.append("⚪ **No verdict yet** — run the suites for this window first.")
        elif readiness == "GREEN":
            lines.append("✅ **Go** — the server's readiness verdict is GREEN.")
        elif readiness == "AMBER":
            # D-120: AMBER is a project signal, not a release recommendation.
            lines.append(
                "🟡 **AMBER — review first.** The project's recent results sit below the "
                "GREEN band. This is not a release verdict: read the release's own gate "
                "with `get_release_gate` before deciding."
            )
        else:
            lines.append(
                "🔴 **No-Go** — resolve the blockers above before releasing. "
                "Run `get_top_failing_tests` and `get_defects` for details."
            )

        return "\n".join(lines)


def _blockers_text(count: int) -> str:
    return f"{count} blocker{'' if count == 1 else 's'}"


def _project_heading(data: dict, days: int) -> str:
    """D-120: "Project readiness (last N days)", never "Release Readiness":
    the windowed project signal is not a release's verdict."""
    readiness = readiness_of(data)
    label = f"Project readiness (last {days} days)"
    if readiness is None:
        return f"⚪ {label}: **{PENDING_TEXT}**"
    band = data.get("release_readiness_band")
    band_text = f" (policy band: {band})" if isinstance(band, str) and band else ""
    return f"{_READINESS_ICON.get(readiness, '⚪')} {label}: **{readiness}**{band_text}"


async def _active_release_gates(project_id: str) -> tuple[list[dict], str | None]:
    """The project's in-progress releases with their stored gate summary.

    ``GET /api/v1/releases?status=in_progress`` carries each release's last
    recorded gate (``gate``: verdict, blocking reasons and total, decided_at;
    ``null`` when never evaluated). A failure to read them is reported, never
    allowed to sink the project answer.
    """
    try:
        body = await api.get(
            api.path("/api/v1/releases"),
            params={"project_id": project_id, "status": "in_progress"},
        )
    except Exception as exc:  # noqa: BLE001 - reported in the answer
        status = getattr(getattr(exc, "response", None), "status_code", None)
        return [], f"HTTP {status}" if status else type(exc).__name__
    items = body.get("items") if isinstance(body, dict) else body
    out: list[dict] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        gate = item.get("gate") if isinstance(item.get("gate"), dict) else None
        reasons = [_reason_text(r) for r in (gate or {}).get("blocking_reasons") or []]
        total = (gate or {}).get("blocking_total")
        out.append({
            "name": str(item.get("name") or item.get("version") or item.get("id") or "?"),
            "verdict": str(gate["verdict"]) if gate and gate.get("verdict") else None,
            "reasons": reasons,
            "blocking_total": int(total) if isinstance(total, int) else len(reasons),
            "decided_at": (gate or {}).get("decided_at"),
        })
    return out, None
