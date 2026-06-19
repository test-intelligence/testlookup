"""FLK-P6 slice 2 — cross-run step-flip computation (pure, no-DB, never-raise).

``flaky_step_analysis`` (FLK-P5) attributes a failure to one step *in the latest
snapshot* — it cannot say whether that step has been *oscillating* pass↔fail,
because ``test_steps`` is a latest-run-only snapshot. FLK-P6 slice 1 (#199) added
``test_step_runs``, which RETAINS one compact outcome row per
``(canonical_test_case_id, source_test_run_id, ordinal)``. This module turns that
retained history into the signal FLK-P5 explicitly deferred: **which step flipped
between runs, how often, and in which direction** — so a step that flickers
across runs reads as *step-level flakiness* (fix/quarantine that one step) rather
than a whole-test verdict.

Pure: no DB, no I/O, no outbound calls; never raises (a malformed run/step
degrades to a neutral contribution). Safe under ``AI_OFFLINE_MODE`` by
construction. The DB read that assembles the per-run window from
``test_step_runs`` — and any surfacing — is a later slice; this slice is the
computation it will call.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping

# A step's per-run outcome is reduced to one of these before comparison; only a
# PASSED<->FAILED transition is a "flip". Everything else (SKIPPED/UNKNOWN/…)
# carries no pass-vs-fail signal, so it is dropped from a step's sequence rather
# than treated as a third state that would manufacture spurious flips.
_PASSED = "PASSED"
_FAILED = "FAILED"
_FAILED_STATUSES = {"FAILED", "BROKEN"}

_NAME_MAX = 200


def _norm_status(value: Any) -> str:
    """Normalise to ``PASSED`` / ``FAILED`` for flip comparison, or ``""`` for a
    status that carries no pass-vs-fail signal. Mirrors the enum-suffix handling
    used elsewhere in FLK (``LaunchStatus.PASSED`` -> ``PASSED``)."""
    try:
        text = str(value).upper().strip()
    except Exception:
        return ""
    text = text.rsplit(".", 1)[-1] if "." in text else text
    if text == _PASSED:
        return _PASSED
    if text in _FAILED_STATUSES:
        return _FAILED
    return ""


def _clip_name(value: Any) -> str:
    try:
        text = str(value or "").strip()
    except Exception:
        return ""
    return text[:_NAME_MAX]


@dataclass(frozen=True)
class StepFlip:
    """One adjacent-run transition of a single step between PASSED and FAILED."""

    ordinal: int
    step_name: str
    from_run_id: str
    to_run_id: str
    from_status: str  # PASSED | FAILED
    to_status: str  # PASSED | FAILED
    direction: str  # "regression" (PASSED->FAILED) | "recovery" (FAILED->PASSED)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class StepFlipSummary:
    """Per-step roll-up across the analysed window — the actionable unit."""

    ordinal: int
    step_name: str
    flip_count: int
    runs_observed: int  # runs in which this step had a PASSED/FAILED outcome
    last_status: str
    is_flaky: bool  # flipped at least once (oscillated pass<->fail)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class StepFlipReport:
    """Result of :func:`compute_step_flips` over a per-run step window."""

    has_step_flip: bool = False
    runs_analyzed: int = 0
    total_flips: int = 0
    flips: tuple[StepFlip, ...] = ()
    flipping_steps: tuple[StepFlipSummary, ...] = ()
    summary: str = ""

    def to_dict(self) -> dict:
        return {
            "has_step_flip": self.has_step_flip,
            "runs_analyzed": self.runs_analyzed,
            "total_flips": self.total_flips,
            "flips": [f.to_dict() for f in self.flips],
            "flipping_steps": [s.to_dict() for s in self.flipping_steps],
            "summary": self.summary,
        }


def _coerce_run(run: Any) -> tuple[str, dict[int, tuple[str, str]]]:
    """Return ``(run_id, {ordinal: (status, name)})`` for one run, keeping only
    the steps that carry a pass-vs-fail signal. Malformed input -> empty."""
    if not isinstance(run, Mapping):
        return "", {}
    try:
        run_id = str(run.get("run_id") or "")
    except Exception:
        run_id = ""
    steps_raw = run.get("steps")
    if not isinstance(steps_raw, Iterable) or isinstance(steps_raw, (str, bytes, Mapping)):
        return run_id, {}
    by_ordinal: dict[int, tuple[str, str]] = {}
    for step in steps_raw:
        if not isinstance(step, Mapping):
            continue
        status = _norm_status(step.get("status"))
        if not status:
            continue
        try:
            ordinal = int(step.get("ordinal"))
        except (TypeError, ValueError):
            continue
        # Last step wins for a duplicated ordinal within one run (defensive —
        # the table's unique (canonical, run, ordinal) should preclude it).
        by_ordinal[ordinal] = (status, _clip_name(step.get("name")))
    return run_id, by_ordinal


def compute_step_flips(runs: Iterable[Mapping[str, Any]]) -> StepFlipReport:
    """Compute cross-run step-flip from a per-run step-outcome window.

    ``runs`` is the per-run window **ordered oldest -> newest**; each item is a
    mapping ``{"run_id": ..., "steps": [{"ordinal": int, "name": str,
    "status": str}, ...]}`` — the shape ``test_step_runs`` yields per source run.
    Steps are matched across runs by ``ordinal`` (the table's grain and the same
    key FLK-P5 attribution uses); a flip is a PASSED<->FAILED change between two
    consecutive runs in which the step had a pass/fail outcome (intervening
    SKIPPED/UNKNOWN runs are bridged, not counted as flips). Never raises.
    """
    try:
        ordered = [_coerce_run(r) for r in runs]
    except Exception:
        return StepFlipReport()

    runs_analyzed = len(ordered)
    if runs_analyzed < 2:
        # A flip needs two comparable runs; nothing to compute (but report the
        # window size so callers can distinguish "no flip" from "no history").
        return StepFlipReport(
            runs_analyzed=runs_analyzed,
            summary=(
                "Not enough run history for cross-run step-flip "
                f"(need >=2 runs, have {runs_analyzed})."
            ),
        )

    # Per-step sequence of (run_id, status), latest non-empty name per ordinal.
    sequences: dict[int, list[tuple[str, str]]] = {}
    latest_name: dict[int, str] = {}
    for run_id, by_ordinal in ordered:
        for ordinal, (status, name) in by_ordinal.items():
            sequences.setdefault(ordinal, []).append((run_id, status))
            if name:
                latest_name[ordinal] = name  # oldest->newest, so last write wins

    flips: list[StepFlip] = []
    summaries: list[StepFlipSummary] = []
    for ordinal in sorted(sequences):
        seq = sequences[ordinal]
        name = latest_name.get(ordinal, "")
        flip_count = 0
        for (prev_run, prev_status), (cur_run, cur_status) in zip(seq, seq[1:]):
            if prev_status == cur_status:
                continue
            flip_count += 1
            flips.append(
                StepFlip(
                    ordinal=ordinal,
                    step_name=name,
                    from_run_id=prev_run,
                    to_run_id=cur_run,
                    from_status=prev_status,
                    to_status=cur_status,
                    direction="regression" if cur_status == _FAILED else "recovery",
                )
            )
        if flip_count:
            summaries.append(
                StepFlipSummary(
                    ordinal=ordinal,
                    step_name=name,
                    flip_count=flip_count,
                    runs_observed=len(seq),
                    last_status=seq[-1][1],
                    is_flaky=True,
                )
            )

    summaries.sort(key=lambda s: (-s.flip_count, s.ordinal))
    total_flips = len(flips)
    has_step_flip = total_flips > 0

    if not has_step_flip:
        summary = f"No cross-run step-flip across {runs_analyzed} runs."
    else:
        top = summaries[0]
        label = top.step_name or f"step #{top.ordinal}"
        plural = "s" if len(summaries) > 1 else ""
        summary = (
            f"Step-level flakiness: '{label}' flipped PASSED<->FAILED "
            f"{top.flip_count}x across {runs_analyzed} runs"
            f" ({len(summaries)} flipping step{plural}, {total_flips} total flips). "
            "Cross-run step-flip points at one oscillating step rather than the "
            "whole test."
        )

    return StepFlipReport(
        has_step_flip=has_step_flip,
        runs_analyzed=runs_analyzed,
        total_flips=total_flips,
        flips=tuple(flips),
        flipping_steps=tuple(summaries),
        summary=summary,
    )
