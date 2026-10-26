"""W16 AGT-13 ``shadow_pair_service`` over scripted sessions (contract §2.2 test 1).

The SQL itself (``count(*) OVER ()``, the UNION ALL source resolution, the golden-match join, the expiry delete)
runs for real in ``tests/integration/test_shadow_pairs_postgres.py``; here each statement is compiled and read
(every filter INSIDE the project) and the Python around it is driven: the page shape, the read-path sanitizer,
the label codes, the golden verdicts, and the expiry window built from a ``now`` made inside each test.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql

from app.models.postgres import AIEvalShadowPair
from app.models.schemas import ShadowPairLabelRequest
from app.services import shadow_pair_service as svc

ROOT = "agent.root_cause_analysis.v1"
SUMMARY = "agent.summary.v1"


class _Result:
    def __init__(self, rows=(), rowcount=0):
        self._rows = list(rows)
        self.rowcount = rowcount

    def all(self):
        return list(self._rows)

    def one(self):
        return self._rows[0]

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None

    def scalars(self):
        return SimpleNamespace(all=lambda: list(self._rows))


class _Session:
    """Answers each ``execute`` / ``scalar`` with the next scripted answer; records every statement."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.statements = []
        self.flushes = 0
        self.refreshed = []

    async def execute(self, statement, *_args, **_kwargs):
        self.statements.append(statement)
        return self.answers.pop(0)

    async def scalar(self, statement):
        self.statements.append(statement)
        return self.answers.pop(0)

    async def flush(self):
        self.flushes += 1

    async def refresh(self, row):
        self.refreshed.append(row)

    async def commit(self):  # pragma: no cover - a service never commits (STAGE_ONLY_SERVICES)
        raise AssertionError("a stage-only service committed")


def _sql(statement) -> str:
    return str(statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


def _pair(*, project_id, agent_id=ROOT, sample_key=None, status="pending", created_at=None, label=None,
          incumbent=None, candidate=None):
    return AIEvalShadowPair(
        id=uuid.uuid4(),
        project_id=project_id,
        agent_id=agent_id,
        sample_key=sample_key or f"root_cause:{uuid.uuid4()}",
        incumbent_tier="llm",
        candidate_tier="slm",
        incumbent_output={"failure_category": "PRODUCT_BUG"} if incumbent is None else incumbent,
        candidate_output={"failure_category": "FLAKY"} if candidate is None else candidate,
        incumbent_tokens=40,
        candidate_tokens=12,
        total_tokens=52,
        label_status=status,
        label=label,
        created_at=created_at or datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc),
    )


# ── list_pairs ──────────────────────────────────────────────────────────────


async def test_the_list_filters_inside_the_project_and_answers_its_totals():
    project = uuid.uuid4()
    case_id, other_case = uuid.uuid4(), uuid.uuid4()
    run_id = uuid.uuid4()
    mine = _pair(project_id=project, sample_key=f"root_cause:{case_id}")
    foreign = _pair(project_id=project, sample_key=f"root_cause:{other_case}")  # its case lives in another project
    summary = _pair(project_id=project, agent_id=SUMMARY, sample_key=f"summary:{run_id}")
    db = _Session(
        _Result([(mine, 7, None), (foreign, 7, None), (summary, 7, "Lena Ortiz")]),
        _Result([(5, True)]),
        # The source statement answers only what the PROJECT filter lets through.
        _Result([("test_case", case_id, "test_checkout_total", run_id, "412"), ("test_run", run_id, None, run_id, "412")]),
    )

    out = await svc.list_pairs(db, project_id=project, agent_id=ROOT, label_status="pending", limit=3, offset=0)

    assert (out.total, out.pending_total, out.live_tier_pairs_seen) == (7, 5, True)
    assert [item.id for item in out.items] == [str(mine.id), str(foreign.id), str(summary.id)]
    page_sql, counts_sql, sources_sql = (_sql(s) for s in db.statements)
    assert f"ai_eval_shadow_pairs.project_id = '{project}'" in page_sql
    assert f"ai_eval_shadow_pairs.agent_id = '{ROOT}'" in page_sql
    assert "ai_eval_shadow_pairs.label_status = 'pending'" in page_sql
    assert "count(*) OVER ()" in page_sql and "LIMIT 3" in page_sql
    assert counts_sql.count(f"ai_eval_shadow_pairs.project_id = '{project}'") == 2 and "EXISTS" in counts_sql
    assert sources_sql.count(f"test_runs.project_id = '{project}'") == 2 and "UNION ALL" in sources_sql
    assert out.items[0].source is not None and out.items[0].source.href == f"/runs/{run_id}?tab=tests"
    assert out.items[0].source.label == "test_checkout_total" and out.items[0].source.build_number == "412"
    assert out.items[1].source is None, "another project's test case is never looked up by id alone"
    assert out.items[2].source.label == "Run #412" and out.items[2].source.href == f"/runs/{run_id}"
    assert out.items[2].labelled_by_name == "Lena Ortiz" and out.items[0].labelled_by_name is None
    assert db.flushes == 0, "a GET writes nothing"


async def test_all_statuses_and_an_empty_project():
    project = uuid.uuid4()
    db = _Session(_Result([]), _Result([(0, False)]))

    out = await svc.list_pairs(db, project_id=project, label_status="all")

    assert (out.items, out.total, out.pending_total, out.live_tier_pairs_seen) == ([], 0, 0, False)
    assert "label_status" not in _sql(db.statements[0]).split("WHERE", 1)[1].split("ORDER BY", 1)[0]
    assert len(db.statements) == 2, "no source statement for an empty page"


async def test_a_page_past_the_end_still_counts_the_filtered_set():
    project = uuid.uuid4()
    db = _Session(_Result([]), 4, _Result([(4, True)]))

    out = await svc.list_pairs(db, project_id=project, limit=25, offset=50)

    assert (out.items, out.total) == ([], 4)
    assert f"ai_eval_shadow_pairs.project_id = '{project}'" in _sql(db.statements[1])


async def test_outputs_are_re_sanitized_on_read_and_pending_rows_carry_their_expiry():
    project = uuid.uuid4()
    created = datetime(2026, 10, 20, 8, 30, tzinfo=timezone.utc)
    stored = _pair(
        project_id=project, sample_key="root_cause:not-a-uuid", created_at=created,
        incumbent={"root_cause_summary": "Login used token=Qw3rTy9Uiop1 from the vault", "failure_category": "PRODUCT_BUG"},
        candidate={"failure_category": "FLAKY", "api_key": "Zx81Lm02Pq"},
    )
    db = _Session(_Result([(stored, 1, None)]), _Result([(1, True)]))

    item = (await svc.list_pairs(db, project_id=project)).items[0]

    assert item.incumbent_output["root_cause_summary"] == "Login used token=[REDACTED] from the vault"
    assert item.candidate_output == {"failure_category": "FLAKY", "api_key": "[REDACTED]"}
    assert item.source is None and len(db.statements) == 2, "an unparseable key resolves to no source"
    assert item.expires_at == (created + timedelta(days=30)).isoformat()
    assert item.agent_label == "Root-cause analysis"


# ── label_pair ──────────────────────────────────────────────────────────────


def _actor():
    return SimpleNamespace(id=uuid.uuid4(), username="lena", full_name="Lena Ortiz")


async def test_a_label_is_written_by_a_conditional_update_with_its_labeller():
    pair = _pair(project_id=uuid.uuid4())
    actor = _actor()
    now = datetime(2026, 10, 25, 10, 0, tzinfo=timezone.utc)
    db = _Session(_Result(rowcount=1))
    body = ShadowPairLabelRequest(verdict="candidate", failure_category="flaky", notes="Mail me at ops-team@example.org")

    out = await svc.label_pair(db, pair=pair, body=body, actor=actor, now=now)

    assert out is pair and db.flushes == 1 and db.refreshed == [pair], "flush, then refresh before serialising"
    update = db.statements[0]
    sql = str(update.compile(dialect=postgresql.dialect()))
    assert sql.startswith("UPDATE ai_eval_shadow_pairs") and "label_status = %(label_status_1)s" in sql
    values = update.compile(dialect=postgresql.dialect()).params
    assert values["label_status_1"] == "pending", "only a still-pending row is taken"
    assert values["id_1"] == pair.id
    assert values["label"] == {
        "verdict": "candidate", "failure_category": "FLAKY", "notes": "Mail me at [REDACTED_EMAIL]", "source": "human",
    }
    assert values["label_status"] == "human_labelled" and values["labelled_by"] == actor.id
    assert values["labelled_at"] == now


@pytest.mark.parametrize("agent_id,status,body,code,http", [
    (ROOT, "human_labelled", {"verdict": "both"}, "shadow_pair_already_labelled", 409),
    (ROOT, "golden_match", {"verdict": "both"}, "shadow_pair_already_labelled", 409),
    (ROOT, "pending", {"verdict": "maybe"}, "verdict_invalid", 422),
    (ROOT, "pending", {"verdict": "both", "failure_category": "NOT_A_CATEGORY"}, "failure_category_invalid", 422),
    (SUMMARY, "pending", {"verdict": "both", "failure_category": "FLAKY"}, "failure_category_not_applicable", 422),
])
async def test_label_refusals_carry_their_code_and_write_nothing(agent_id, status, body, code, http):
    pair = _pair(project_id=uuid.uuid4(), agent_id=agent_id, status=status)
    db = _Session()

    with pytest.raises(svc.ShadowPairLabelError) as exc:
        await svc.label_pair(
            db, pair=pair, body=ShadowPairLabelRequest(**body), actor=_actor(),
            now=datetime(2026, 10, 25, 10, 0, tzinfo=timezone.utc),
        )

    assert str(exc.value) == exc.value.code == code and exc.value.status_code == http
    assert isinstance(exc.value, ValueError)
    assert db.statements == [] and db.flushes == 0
    if code == "shadow_pair_already_labelled":
        assert exc.value.message == f"This pair is already labelled ({status})."


async def test_a_concurrent_label_that_won_the_row_is_a_409():
    pair = _pair(project_id=uuid.uuid4())
    db = _Session(_Result(rowcount=0), "golden_match")

    with pytest.raises(svc.ShadowPairLabelError) as exc:
        await svc.label_pair(
            db, pair=pair, body=ShadowPairLabelRequest(verdict="both"), actor=_actor(),
            now=datetime(2026, 10, 25, 10, 0, tzinfo=timezone.utc),
        )

    assert exc.value.code == "shadow_pair_already_labelled"
    assert exc.value.message == "This pair is already labelled (golden_match)."
    assert db.flushes == 0


# ── golden match ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("incumbent,candidate,verdict", [
    ("PRODUCT_BUG", "FLAKY", "incumbent"),
    ("FLAKY", "product_bug", "candidate"),
    ("PRODUCT_BUG", "PRODUCT_BUG", "both"),
    ("FLAKY", "INFRASTRUCTURE", "neither"),
    (None, None, "neither"),
])
def test_the_golden_verdict_names_the_tier_that_matches_the_human_truth(incumbent, candidate, verdict):
    assert svc.golden_verdict(
        "PRODUCT_BUG", {"failure_category": incumbent}, {"failure_category": candidate}
    ) == verdict


async def test_golden_match_labels_every_row_its_one_statement_reads():
    now = datetime(2026, 10, 25, 3, 50, tzinfo=timezone.utc)
    first = _pair(project_id=uuid.uuid4())
    second = _pair(project_id=uuid.uuid4(), incumbent={"failure_category": "TEST_DATA"},
                   candidate={"failure_category": "TEST_DATA"})
    feedback_a, feedback_b = uuid.uuid4(), uuid.uuid4()
    db = _Session(_Result([(first, "FLAKY", feedback_a), (second, "TEST_DATA", feedback_b)]))

    assert await svc.golden_match_batch(db, now=now, limit=500) == 2

    assert first.label == {"verdict": "candidate", "failure_category": "FLAKY", "source": "feedback",
                           "feedback_id": str(feedback_a)}
    assert second.label["verdict"] == "both"
    assert {first.label_status, second.label_status} == {"golden_match"}
    assert first.labelled_by is None and first.labelled_at == now
    assert db.flushes == 1 and len(db.statements) == 1
    sql = _sql(db.statements[0])
    assert "ai_eval_shadow_pairs.sample_key LIKE 'root_cause:%%'" in sql or "LIKE 'root_cause:%'" in sql
    assert "ai_eval_shadow_pairs.label_status = 'pending'" in sql
    assert "test_runs.project_id = ai_eval_shadow_pairs.project_id" in sql, "the truth must be the pair's project's"
    assert "ai_feedback.rating = 'correct'" in sql and "ai_feedback.corrected_category IS NOT NULL" in sql
    assert "FOR UPDATE OF ai_eval_shadow_pairs SKIP LOCKED" in sql and "LIMIT 500" in sql
    assert "summary:" not in sql, "a summary pair is never auto-labelled"


async def test_golden_match_with_nothing_to_label_writes_nothing():
    db = _Session(_Result([]))
    assert await svc.golden_match_batch(db, now=datetime(2026, 10, 25, 3, 50, tzinfo=timezone.utc)) == 0
    assert db.flushes == 0


# ── expiry and the labelled corpus ─────────────────────────────────────────


async def test_expiry_deletes_pending_pairs_older_than_thirty_days_from_now():
    now = datetime(2026, 10, 25, 2, 50, tzinfo=timezone.utc)
    db = _Session(_Result(rowcount=3))

    assert await svc.expire_unlabelled_batch(db, now=now, limit=1000) == 3

    sql = _sql(db.statements[0])
    cutoff = now - timedelta(days=30)
    assert sql.startswith("DELETE FROM ai_eval_shadow_pairs WHERE ai_eval_shadow_pairs.id IN (SELECT")
    assert "ai_eval_shadow_pairs.label_status = 'pending'" in sql
    assert f"ai_eval_shadow_pairs.created_at < '{cutoff.isoformat(sep=' ')}'" in sql
    assert "LIMIT 1000" in sql
    assert svc.unlabelled_cutoff(now) == cutoff


async def test_the_labelled_corpus_keeps_verdicts_only_and_re_sanitizes():
    project = uuid.uuid4()
    human = _pair(project_id=project, status="human_labelled", label={"verdict": "candidate", "source": "human"},
                  incumbent={"summary": "token=Ab12Cd34Ef56"})
    broken = _pair(project_id=project, status="golden_match", label={"source": "feedback"})
    db = _Session(_Result([human, broken]))

    out = await svc.labelled_pairs_for(
        db, project_id=project, agent_id=ROOT, incumbent_tier="llm", candidate_tier="slm"
    )

    assert [p.pair_id for p in out] == [str(human.id)], "a label without a verdict is no evidence"
    assert out[0].verdict == "candidate" and out[0].label_status == "human_labelled"
    assert out[0].incumbent_output == {"summary": "token=[REDACTED]"}
    sql = _sql(db.statements[0])
    assert f"ai_eval_shadow_pairs.project_id = '{project}'" in sql and "LIMIT 1000" in sql
    assert "label_status IN ('human_labelled', 'golden_match')" in sql
    assert "incumbent_tier = 'llm'" in sql and "candidate_tier = 'slm'" in sql


