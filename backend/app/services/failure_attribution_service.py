"""Is this failure yours, flaky, or the environment's?

Phase 4 of ``architecture/TEST_INTELLIGENCE_PLAN.md`` — the flagship, and the
item the rest of the roadmap exists to reach.

## The problem

At Google roughly **84% of pass→fail transitions involve a flaky test**. A raw
transition is therefore a weak signal of a real regression, and a product that
presents every new red as "new failure" produces a false-positive flood that
trains engineers to dismiss the real ones — the cockpit-alarm failure mode.

Everything needed to do better already existed here and was never composed:
flaky scores (Phase 2), systemic co-failure clusters (Phase 3), commit-range
attribution, a last-green baseline, and measured per-project classifier
calibration (Phase 0). This module is the composition.

## How the verdict is reached

Not a cascade of ``if``s. Each input **votes** for at most one verdict with a
strength, and then:

* exactly one verdict supported above threshold  → that verdict;
* two or more supported                          → ``UNCERTAIN`` (they disagree);
* none supported                                 → ``UNCERTAIN`` (thin evidence).

``UNCERTAIN`` is a first-class answer, not a failure to decide. Filling that gap
with the least-bad guess is precisely how a verdict surface loses the trust it
exists to create.

## Precedence, and why

When signals conflict the answer is ``UNCERTAIN`` rather than a ranked winner —
but the *strengths* encode what the evidence supports:

* **Systemic cluster membership is the strongest single signal.** Co-occurring
  failures across otherwise-unrelated tests are strong evidence of shared
  infrastructure rather than one developer's change, and a cluster-level
  environmental cause is *checkable* in a way a per-test code-defect claim is
  not.
* **Commit-range overlap** is direct but noisy: touching a file a test covers
  is suggestive, not conclusive.
* **A high flaky score** argues for flakiness, and never for silence.

## The safeguard

Nothing here suppresses anything. Google's follow-up found that when a
previously stable test turned flaky, roughly **1 in 6 times the cause was a real
production bug**, so ``LIKELY_FLAKY`` must never be read as "safe to ignore" —
and this module offers no way to express that. Confidence exists to *rank* what
a human sees, never to hide it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Optional

import structlog

if TYPE_CHECKING:  # pragma: no cover - typing only
    from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger("services.failure_attribution")


class AttributionVerdict(str, Enum):
    """The four answers. ``UNCERTAIN`` is an answer, not an absence of one."""

    LIKELY_YOUR_CHANGE = "LIKELY_YOUR_CHANGE"
    LIKELY_FLAKY = "LIKELY_FLAKY"
    LIKELY_INFRA = "LIKELY_INFRA"
    UNCERTAIN = "UNCERTAIN"


# Human-facing copy for every verdict. Keyed by the enum so a new member cannot
# be added without a label — the vocabulary-subset defect class that produced
# F-074, F-078 and UAT-002 (a producer grows a state, a consumer silently keeps
# rendering the old set).
VERDICT_LABELS: dict[AttributionVerdict, str] = {
    AttributionVerdict.LIKELY_YOUR_CHANGE: "Likely your change",
    AttributionVerdict.LIKELY_FLAKY: "Likely flaky",
    AttributionVerdict.LIKELY_INFRA: "Likely infrastructure",
    AttributionVerdict.UNCERTAIN: "Uncertain",
}

VERDICT_DESCRIPTIONS: dict[AttributionVerdict, str] = {
    AttributionVerdict.LIKELY_YOUR_CHANGE: (
        "The change under test touches code this test covers, and nothing "
        "suggests flakiness or a shared environmental cause."
    ),
    AttributionVerdict.LIKELY_FLAKY: (
        "This test has a history of flipping without a code change. Worth "
        "checking anyway — a newly-flaky test is sometimes a real bug."
    ),
    AttributionVerdict.LIKELY_INFRA: (
        "This test failed together with others that share no code path, which "
        "points at shared infrastructure rather than any one change."
    ),
    AttributionVerdict.UNCERTAIN: (
        "The available signals disagree or are too thin to attribute this "
        "failure. Shown as-is rather than guessed."
    ),
}

# A vote below this contributes nothing. Above it, a vote is "supported".
SUPPORT_THRESHOLD = 0.5

# Signal strengths. Ordered by how checkable the resulting claim is, not by how
# confident the underlying number looks.
STRENGTH_CLUSTER = 0.85          # co-failure across unrelated tests
STRENGTH_CHANGE_OVERLAP = 0.70   # commit touches covered files
STRENGTH_FLAKY = 0.65            # historical flip behaviour

# A flaky score must clear this before it argues for LIKELY_FLAKY.
FLAKY_SCORE_FLOOR = 0.5

# Commit-range path overlap must clear this before it argues for the change.
CHANGE_OVERLAP_FLOOR = 0.4

# Confidence bands from Phase 2 that are too thin to vote on.
WEAK_CONFIDENCES = {"none", "low"}


@dataclass(frozen=True)
class AttributionInputs:
    """The five signals, exactly as they were when the verdict was composed."""

    # 1. Did this test just turn red, or was it already failing?
    is_new_failure: bool = False
    last_green_run_id: Optional[str] = None
    # 2. Phase 2.
    flaky_score: Optional[float] = None
    flaky_confidence: str = "none"
    # 3. Phase 3.
    cluster_key: Optional[str] = None
    cluster_cause_family: Optional[str] = None
    cluster_size: int = 0
    # 4. commit_attribution_service.
    change_overlap: Optional[float] = None
    changed_files: tuple[str, ...] = ()
    # 5. Phase 0 calibration, via the suppression gate.
    calibration_mode: str = "hint"
    calibration_specificity: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_new_failure": self.is_new_failure,
            "last_green_run_id": self.last_green_run_id,
            "flaky_score": self.flaky_score,
            "flaky_confidence": self.flaky_confidence,
            "cluster_key": self.cluster_key,
            "cluster_cause_family": self.cluster_cause_family,
            "cluster_size": self.cluster_size,
            "change_overlap": self.change_overlap,
            "changed_files": list(self.changed_files),
            "calibration_mode": self.calibration_mode,
            "calibration_specificity": self.calibration_specificity,
        }


@dataclass(frozen=True)
class Attribution:
    """A verdict, its confidence, and every input behind it."""

    verdict: AttributionVerdict
    confidence: float
    rationale: str
    inputs: AttributionInputs
    votes: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict.value,
            "verdict_label": VERDICT_LABELS[self.verdict],
            "verdict_description": VERDICT_DESCRIPTIONS[self.verdict],
            "confidence": self.confidence,
            "rationale": self.rationale,
            "inputs": self.inputs.to_dict(),
            "votes": dict(self.votes),
            # Restated on every payload. A consumer must never be able to read
            # a verdict as permission to hide a failure.
            "policy": (
                "Advisory only. A newly-flaky test is sometimes a real bug, so "
                "no verdict suppresses, hides or auto-closes a failure."
            ),
        }


def _clamp(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if number != number:  # NaN
        return 0.0
    return max(0.0, min(1.0, number))


def compose(inputs: AttributionInputs) -> Attribution:
    """Compose the five signals into one verdict. Pure; never raises."""
    votes: dict[str, float] = {}

    # A test that was already failing is not a new regression to attribute.
    # Saying so is more useful than attributing an ongoing failure to whoever
    # happened to push next.
    if not inputs.is_new_failure:
        return Attribution(
            verdict=AttributionVerdict.UNCERTAIN,
            confidence=0.0,
            rationale=(
                "Not a new failure — this test was already failing before this "
                "run, so there is no transition to attribute."
            ),
            inputs=inputs,
            votes=votes,
        )

    # 3. Systemic cluster membership — the most checkable claim available.
    if inputs.cluster_key and inputs.cluster_size >= 2:
        votes[AttributionVerdict.LIKELY_INFRA.value] = STRENGTH_CLUSTER

    # 4. Commit-range overlap.
    overlap = _clamp(inputs.change_overlap) if inputs.change_overlap is not None else 0.0
    if overlap >= CHANGE_OVERLAP_FLOOR:
        votes[AttributionVerdict.LIKELY_YOUR_CHANGE.value] = STRENGTH_CHANGE_OVERLAP

    # 2. Flaky history — only when the score rests on enough observations.
    # A thin-history score is exactly the fabricated confidence Phase 2 refuses
    # to emit, and it must not sneak back in as a vote here.
    score = _clamp(inputs.flaky_score) if inputs.flaky_score is not None else 0.0
    if score >= FLAKY_SCORE_FLOOR and inputs.flaky_confidence not in WEAK_CONFIDENCES:
        votes[AttributionVerdict.LIKELY_FLAKY.value] = STRENGTH_FLAKY

    supported = {name: strength for name, strength in votes.items()
                 if strength >= SUPPORT_THRESHOLD}

    if len(supported) == 1:
        winner, strength = next(iter(supported.items()))
        verdict = AttributionVerdict(winner)
        rationale = _rationale_for(verdict, inputs)
        confidence = strength
    elif len(supported) > 1:
        verdict = AttributionVerdict.UNCERTAIN
        names = ", ".join(sorted(VERDICT_LABELS[AttributionVerdict(n)].lower()
                                 for n in supported))
        rationale = (
            f"Signals disagree ({names}). Reported as uncertain rather than "
            "resolved to whichever looked strongest."
        )
        confidence = 0.0
    else:
        verdict = AttributionVerdict.UNCERTAIN
        rationale = (
            "No signal was strong enough to attribute this failure: no "
            "co-failure cluster, no meaningful overlap with the changed files, "
            "and no flaky history with enough observations behind it."
        )
        confidence = 0.0

    # 5. Calibration caps the claim. Where the project's classifier measures
    # weak — or has never been measured — the evidence is not good enough to
    # state a conclusion, so the verdict degrades to UNCERTAIN rather than
    # borrowing authority it has not earned.
    if verdict is not AttributionVerdict.UNCERTAIN and inputs.calibration_mode != "advisory":
        return Attribution(
            verdict=AttributionVerdict.UNCERTAIN,
            confidence=0.0,
            rationale=(
                f"{_rationale_for(verdict, inputs)} Reported as uncertain "
                "because this project's failure classifier has not measured "
                "well enough on its own history to state that as a conclusion."
            ),
            inputs=inputs,
            votes=votes,
        )

    return Attribution(
        verdict=verdict,
        confidence=round(confidence, 4),
        rationale=rationale,
        inputs=inputs,
        votes=votes,
    )


def _rationale_for(verdict: AttributionVerdict, inputs: AttributionInputs) -> str:
    """Name the specific evidence, not the category.

    Practitioners reject generic factor-level explanations — they want the
    changed files and the prior failures named.
    """
    if verdict is AttributionVerdict.LIKELY_INFRA:
        cause = (inputs.cluster_cause_family or "unknown").replace("_", " ")
        return (
            f"Failed together with {inputs.cluster_size - 1} other test(s) that "
            f"share no code path (cluster {inputs.cluster_key}, cause: {cause})."
        )
    if verdict is AttributionVerdict.LIKELY_YOUR_CHANGE:
        files = ", ".join(inputs.changed_files[:3]) or "the changed files"
        more = ""
        if len(inputs.changed_files) > 3:
            more = f" (+{len(inputs.changed_files) - 3} more)"
        return (
            f"This run's changes touch code this test covers: {files}{more}."
        )
    if verdict is AttributionVerdict.LIKELY_FLAKY:
        return (
            f"This test has flipped without a code change before "
            f"(flakiness {inputs.flaky_score:.2f}, {inputs.flaky_confidence} "
            "confidence). Still worth a look — a newly-flaky test is sometimes "
            "a real bug."
        )
    return VERDICT_DESCRIPTIONS[AttributionVerdict.UNCERTAIN]


# ── Composition from stored signals ─────────────────────────────────────────
#
# Split from ``compose`` so the verdict logic stays exhaustively testable
# without a database, and so a caller holding the signals already can compose
# without a round trip.


async def attribute_run(
    db: "AsyncSession",
    run_id: Any,
    *,
    limit: int = 500,
) -> list[tuple[Any, Attribution]]:
    """Compose a verdict for each failing test in a run.

    Only FAILING tests get a verdict — there is nothing to attribute about a
    pass, and scoring every green test would be a large read for no signal.

    Returns ``(test_case, Attribution)`` pairs. Project-scoped through the run.
    """
    from sqlalchemy import select

    from app.models.postgres import (
        FlakyClassifierCalibration,
        FlakyScore,
        SystemicFlakeCluster,
        RunCommitRange,
        SystemicFlakeClusterMember,
        TestCase,
        TestRun,
    )
    from app.services.flaky_suppression_gate import decide as gate_decide

    run = (
        await db.execute(select(TestRun).where(TestRun.id == run_id))
    ).scalar_one_or_none()
    if run is None:
        return []

    failing = list(
        (
            await db.execute(
                select(TestCase)
                .where(
                    TestCase.test_run_id == run_id,
                    TestCase.status.in_(["FAILED", "BROKEN"]),
                )
                .limit(limit)
            )
        ).scalars().all()
    )
    if not failing:
        return []

    fingerprints = [tc.test_fingerprint for tc in failing if tc.test_fingerprint]

    # 2. Flaky scores (Phase 2).
    scores = {
        row.test_fingerprint: row
        for row in (
            await db.execute(
                select(FlakyScore).where(
                    FlakyScore.project_id == run.project_id,
                    FlakyScore.test_fingerprint.in_(fingerprints or [""]),
                )
            )
        ).scalars().all()
    }

    # 3. Systemic cluster membership (Phase 3).
    cluster_rows = (
        await db.execute(
            select(SystemicFlakeClusterMember, SystemicFlakeCluster)
            .join(
                SystemicFlakeCluster,
                SystemicFlakeCluster.id == SystemicFlakeClusterMember.cluster_id,
            )
            .where(
                SystemicFlakeCluster.project_id == run.project_id,
                SystemicFlakeClusterMember.test_fingerprint.in_(fingerprints or [""]),
            )
        )
    ).all()
    clusters = {member.test_fingerprint: cluster for member, cluster in cluster_rows}

    # 5. Calibration (Phase 0), one decision for the whole project.
    calibration = (
        await db.execute(
            select(FlakyClassifierCalibration).where(
                FlakyClassifierCalibration.project_id == run.project_id
            )
        )
    ).scalar_one_or_none()
    gate = gate_decide(
        run.project_id,
        specificity=getattr(calibration, "specificity", None),
        sample_count=getattr(calibration, "sample_count", 0) or 0,
    )

    # 1. Was each test green in the previous run of this project?
    previous = (
        await db.execute(
            select(TestRun)
            .where(
                TestRun.project_id == run.project_id,
                TestRun.created_at < run.created_at,
            )
            .order_by(TestRun.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    previously_green: set = set()
    if previous is not None:
        previously_green = {
            fingerprint
            for (fingerprint,) in (
                await db.execute(
                    select(TestCase.test_fingerprint).where(
                        TestCase.test_run_id == previous.id,
                        TestCase.status == "PASSED",
                    )
                )
            ).all()
        }

    # 4. Commit-range overlap for this run, computed once.
    #
    # The changed files are NOT on TestRun — they live in
    # ``run_commit_ranges.commits`` as ``[{sha, author, message, files}]``.
    # Reading a ``run.changed_files`` that does not exist would have made this
    # whole signal dead code that fails silently, which is exactly how the
    # Phase 0 OpenShift branch broke.
    #
    # An empty range means "no overlap evidence", which is different from
    # "evidence of no overlap": with no commits known, this signal simply does
    # not vote, rather than voting against the change.
    changed_files: tuple[str, ...] = ()
    locator = None
    try:
        from app.services.commit_attribution_service import build_locator, path_overlap

        locator = build_locator(failing)
        commit_range = (
            await db.execute(
                select(RunCommitRange).where(RunCommitRange.run_id == run_id)
            )
        ).scalar_one_or_none()
        files: list[str] = []
        for commit in (getattr(commit_range, "commits", None) or []):
            if isinstance(commit, dict):
                for path in (commit.get("files") or []):
                    if isinstance(path, str) and path:
                        files.append(path)
        # De-duplicate while preserving order — a file touched by three commits
        # is one piece of evidence, not three.
        changed_files = tuple(dict.fromkeys(files))
    except Exception:  # noqa: BLE001 — attribution must degrade, never fail
        locator = None

    results: list[tuple[Any, Attribution]] = []
    for case in failing:
        fingerprint = case.test_fingerprint or ""
        score_row = scores.get(fingerprint)
        cluster = clusters.get(fingerprint)

        overlap = None
        if locator is not None and changed_files:
            try:
                overlap = max(
                    (path_overlap(locator, path) for path in changed_files),
                    default=None,
                )
            except Exception:  # noqa: BLE001
                overlap = None

        results.append((
            case,
            compose(AttributionInputs(
                is_new_failure=bool(previous is not None and fingerprint in previously_green),
                last_green_run_id=str(previous.id) if previous is not None else None,
                flaky_score=getattr(score_row, "score", None),
                flaky_confidence=getattr(score_row, "confidence", "none") or "none",
                cluster_key=getattr(cluster, "cluster_key", None),
                cluster_cause_family=getattr(cluster, "cause_family", None),
                cluster_size=int(getattr(cluster, "size", 0) or 0),
                change_overlap=overlap,
                changed_files=changed_files[:20],
                calibration_mode=gate.mode,
                calibration_specificity=gate.specificity,
            )),
        ))
    return results


async def store_attributions(
    db: "AsyncSession",
    project_id: Any,
    run_id: Any,
    results: "list[tuple[Any, Attribution]]",
) -> int:
    """Upsert one verdict per test case. Staged only — the caller commits."""
    from sqlalchemy import select

    from app.models.postgres import FailureAttribution

    written = 0
    for case, attribution in results:
        existing = (
            await db.execute(
                select(FailureAttribution).where(
                    FailureAttribution.test_case_id == case.id
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            existing = FailureAttribution(
                project_id=project_id,
                test_case_id=case.id,
                test_run_id=run_id,
                test_fingerprint=case.test_fingerprint or "",
            )
            db.add(existing)
        existing.verdict = attribution.verdict.value
        existing.confidence = attribution.confidence
        existing.rationale = attribution.rationale[:1000]
        existing.inputs = attribution.inputs.to_dict()
        written += 1
    await db.flush()
    return written
