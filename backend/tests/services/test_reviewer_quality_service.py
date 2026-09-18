from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import reviewer_quality_service as svc
from app.services.agent_config_service import default_config
from app.services.agent_eval_samples import MutationClass

NOW = datetime(2026, 9, 14, 12, tzinfo=timezone.utc)


def _mutations(
    *,
    per_class: int = 30,
    families: frozenset[int] = frozenset({3, 4}),
) -> list[svc.ReviewerMutationObservation]:
    return [
        svc.ReviewerMutationObservation(
            sample_id=f"{mutation_class.value}-{index}",
            mutation_class=mutation_class,
            detected_families=families,
            observed_at=NOW - timedelta(days=1),
        )
        for mutation_class in svc.SEMANTIC_MUTATION_CLASSES
        for index in range(per_class)
    ]


def _clean(count: int = 30, flagged: int = 0) -> list[svc.ReviewerCleanObservation]:
    return [
        svc.ReviewerCleanObservation(
            sample_id=f"clean-{index}",
            flagged_families=frozenset({3}) if index < flagged else frozenset(),
            observed_at=NOW - timedelta(days=1),
        )
        for index in range(count)
    ]


def _human(count: int = 30, rejected: int = 0) -> list[svc.ReviewerHumanOutcome]:
    return [
        svc.ReviewerHumanOutcome(
            review_id=f"review-{index}",
            reviewer_passed=True,
            human_rejected=index < rejected,
            observed_at=NOW - timedelta(days=1),
        )
        for index in range(count)
    ]


def _evaluate(**kwargs):
    return svc.evaluate_reviewer_quality(
        mutations=kwargs.get("mutations", _mutations()),
        clean=kwargs.get("clean", _clean()),
        human_outcomes=kwargs.get("human_outcomes", _human()),
        now=NOW,
    )


def test_reports_recall_for_every_mutation_class_and_check_family() -> None:
    result = _evaluate()
    rows = result["per_class_family_recall"]

    assert len(rows) == len(MutationClass) * 5
    assert {
        (row["mutation_class"], row["family"]) for row in rows
    } == {(mutation_class.value, family) for mutation_class in MutationClass for family in range(1, 6)}
    assert all("n" in row and "ci_low" in row and "ci_high" in row for row in rows)
    assert result["verdict"] == "pass"


def test_missing_semantic_class_is_insufficient_and_cannot_disable() -> None:
    mutations = [
        item for item in _mutations()
        if item.mutation_class != MutationClass.CORRECT_NUMBERS_WRONG_CONCLUSION
    ]

    result = _evaluate(mutations=mutations)

    assert result["verdict"] == "insufficient_samples"
    assert result["auto_disable_eligible"] is False
    assert "correct_numbers_wrong_conclusion:0/20" in result["reason"]


def test_second_model_can_disable_only_after_thirty_samples_per_semantic_class() -> None:
    no_second_model = frozenset({1, 3})

    below_floor = _evaluate(mutations=_mutations(per_class=29, families=no_second_model))
    at_floor = _evaluate(mutations=_mutations(per_class=30, families=no_second_model))

    assert below_floor["verdict"] == "pass"
    assert below_floor["auto_disable_eligible"] is False
    assert at_floor["deterministic_semantic_recall"] == 1.0
    assert at_floor["second_model_semantic_recall"] == 0.0
    assert at_floor["second_model_recall_delta"] == -1.0
    assert at_floor["auto_disable_eligible"] is True


def test_second_model_stays_enabled_when_it_adds_at_least_point_one_recall() -> None:
    mutations = _mutations(families=frozenset({3, 4}))
    mutations = [
        item.model_copy(
            update={
                "detected_families": (
                    frozenset({3}) if index < 18 else item.detected_families
                )
            }
        )
        for index, item in enumerate(mutations)
    ]

    result = _evaluate(mutations=mutations)

    assert result["second_model_recall_delta"] == pytest.approx(0.8)
    assert result["auto_disable_eligible"] is False


def test_clean_false_flags_and_human_false_omissions_fail_the_gate() -> None:
    result = _evaluate(clean=_clean(flagged=4), human_outcomes=_human(rejected=4))

    assert result["verdict"] == "fail"
    assert result["false_flag_rate"] == pytest.approx(4 / 30)
    assert result["false_omission_rate"] == pytest.approx(4 / 30)
    assert {item["metric"] for item in result["regressions"]} >= {
        "clean_false_flag_rate",
        "human_false_omission_rate",
    }


def test_false_omission_denominator_is_reviewer_passes_only() -> None:
    outcomes = _human(20)
    outcomes.extend([
        svc.ReviewerHumanOutcome(
            review_id=f"reviewer-blocked-{index}",
            reviewer_passed=False,
            human_rejected=True,
            observed_at=NOW - timedelta(days=1),
        )
        for index in range(10)
    ])

    result = _evaluate(human_outcomes=outcomes)

    assert result["human_outcome_count"] == 20
    assert result["false_omission_rate"] == 0.0


def test_observations_outside_the_trailing_window_do_not_count() -> None:
    stale = _mutations(per_class=30)
    stale = [item.model_copy(update={"observed_at": NOW - timedelta(days=31)}) for item in stale]

    result = _evaluate(mutations=stale)

    assert result["verdict"] == "insufficient_samples"
    assert set(result["semantic_sample_counts"].values()) == {0}


@pytest.mark.asyncio
async def test_persistence_records_raw_evidence_and_decision() -> None:
    db = SimpleNamespace(add=lambda row: added.append(row), flush=AsyncMock())
    added: list = []
    result = _evaluate()

    row = await svc.persist_reviewer_quality(
        db,
        project_id=uuid.uuid4(),
        agent_id=svc.REVIEWER_AGENT_ID,
        result=result,
        mutations=_mutations(),
        clean=_clean(),
        human_outcomes=_human(),
        source="observation_batch",
        evaluated_by=uuid.uuid4(),
    )

    assert added == [row]
    assert row.status == "pass"
    assert len(row.mutation_observations) == 90
    assert row.semantic_sample_counts == result["semantic_sample_counts"]
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_auto_disable_uses_the_single_agent_config_writer(monkeypatch) -> None:
    config = default_config(svc.REVIEWER_AGENT_ID)
    config = config.model_copy(update={
        "review": config.review.model_copy(
            update={"auto_reviewer": True, "second_model_check": True}
        )
    })
    row = SimpleNamespace(config_version=7)
    query = SimpleNamespace(scalar_one_or_none=lambda: row)
    db = SimpleNamespace(execute=AsyncMock(return_value=query))
    monkeypatch.setattr(svc, "serialize", lambda *_: {"config": config.model_dump(mode="json")})
    write = AsyncMock(return_value=SimpleNamespace(config_version=8))
    monkeypatch.setattr(svc, "put_config", write)
    result = _evaluate(mutations=_mutations(families=frozenset({1, 3})))

    applied = await svc.disable_second_model_if_eligible(
        db,
        project_id=uuid.uuid4(),
        agent_id=svc.REVIEWER_AGENT_ID,
        result=result,
        updated_by=uuid.uuid4(),
    )

    assert applied is True and result["auto_disable_applied"] is True
    written = write.await_args.args[2]
    assert written.review.second_model_check is False
    assert written.review.auto_reviewer is True
    assert write.await_args.kwargs["expected_version"] == 7
    assert "FOR UPDATE" in str(db.execute.await_args.args[0])


@pytest.mark.asyncio
async def test_auto_disable_loses_a_concurrent_config_update_without_overwriting_it(monkeypatch) -> None:
    config = default_config(svc.REVIEWER_AGENT_ID)
    config = config.model_copy(update={
        "review": config.review.model_copy(update={"second_model_check": True})
    })
    row = SimpleNamespace(config_version=7)
    db = SimpleNamespace(
        execute=AsyncMock(
            return_value=SimpleNamespace(scalar_one_or_none=lambda: row)
        )
    )
    monkeypatch.setattr(
        svc,
        "serialize",
        lambda *_: {"config": config.model_dump(mode="json")},
    )
    monkeypatch.setattr(
        svc,
        "put_config",
        AsyncMock(side_effect=svc.ConfigVersionConflict(7)),
    )
    result = _evaluate(mutations=_mutations(families=frozenset({1, 3})))

    applied = await svc.disable_second_model_if_eligible(
        db,
        project_id=uuid.uuid4(),
        agent_id=svc.REVIEWER_AGENT_ID,
        result=result,
        updated_by=None,
    )

    assert applied is False
    assert result["auto_disable_applied"] is False
    assert "auto_disable_config_version" not in result


@pytest.mark.asyncio
async def test_auto_disable_does_not_read_or_write_config_when_ineligible(monkeypatch) -> None:
    db = SimpleNamespace(execute=AsyncMock())
    write = AsyncMock()
    monkeypatch.setattr(svc, "put_config", write)
    result = _evaluate(mutations=_mutations(per_class=29, families=frozenset({1, 3})))

    assert not await svc.disable_second_model_if_eligible(
        db,
        project_id=uuid.uuid4(),
        agent_id=svc.REVIEWER_AGENT_ID,
        result=result,
        updated_by=None,
    )
    db.execute.assert_not_awaited()
    write.assert_not_awaited()


@pytest.mark.asyncio
async def test_nightly_rollup_uses_only_observation_batches_and_applies_guarded_change(
    monkeypatch,
) -> None:
    project_id = uuid.uuid4()
    config = default_config(svc.REVIEWER_AGENT_ID)
    config = config.model_copy(update={
        "review": config.review.model_copy(
            update={"auto_reviewer": True, "second_model_check": True}
        )
    })
    batch = SimpleNamespace(
        mutation_observations=[item.model_dump(mode="json") for item in _mutations(
            families=frozenset({1, 3})
        )],
        clean_observations=[item.model_dump(mode="json") for item in _clean()],
        human_outcomes=[item.model_dump(mode="json") for item in _human()],
    )

    class _Result:
        def __init__(self, values):
            self.values = values

        def scalars(self):
            return SimpleNamespace(all=lambda: self.values)

        def scalar_one_or_none(self):
            return self.values[0] if self.values else None

    results = iter([
        _Result([project_id]),
        _Result([batch]),
        _Result([SimpleNamespace(config_version=4)]),
    ])
    added: list = []
    db = SimpleNamespace(
        execute=AsyncMock(side_effect=lambda *_: next(results)),
        add=lambda row: added.append(row),
        flush=AsyncMock(),
    )
    monkeypatch.setattr(svc, "serialize", lambda *_: {"config": config.model_dump(mode="json")})
    monkeypatch.setattr(
        svc,
        "put_config",
        AsyncMock(return_value=SimpleNamespace(config_version=5)),
    )
    activity = AsyncMock()
    monkeypatch.setattr("app.services.activity.service.record", activity)

    rows = await svc.run_nightly_reviewer_quality(db, now=NOW)

    assert rows == added and len(rows) == 1
    assert rows[0].source == "scheduled"
    assert rows[0].mutation_observations == []
    assert rows[0].auto_disable_applied is True
    svc.put_config.assert_awaited_once()
    assert [call.kwargs["event_type"] for call in activity.await_args_list] == [
        "ai_eval.reviewer_evaluated",
        "agent_config.updated",
    ]
