"""agent investigations + policies + agent-runs ledger

Revision ID: 0108
Revises: 0107
Create Date: 2026-07-15

Agentic plan Wave B — AI-1 (hypothesis-loop Investigator, shadow mode) and
the AI-3 data core (agent governance ledger + per-project agent policies).
Three tables:

* ``agent_investigations`` — one row per Investigator run against a test
  run. Persists the full InvestigationDetail wire shape: status/mode/
  trigger columns, budget + spend JSONB, the per-hypothesis results
  (hypotheses JSONB list, written incrementally as nodes complete so the
  UI can poll progress), the synthesis verdict, prompt-version snapshot,
  and the cooperative-cancel flag. A **partial unique index on run_id**
  enforces "one active investigation per run" at the database level
  (active = queued/running/synthesizing) — the API's 409 is backed by a
  real constraint, not just a read-then-write check.

* ``agent_policies`` — per-(project, agent) governance: enabled flag,
  mode (shadow|suggest|act), budgets JSONB (max_runs_per_day /
  max_llm_calls_per_run / max_tokens_per_run / max_seconds_per_run) and
  the ``shadow_runs_completed`` promotion counter. A MISSING row resolves
  in code to the default policy (enabled, shadow, 10/30/60000/300).

* ``agent_runs`` — the append-style agent activity ledger (AI-3): one row
  per agent execution with mode/trigger/status, a one-line summary,
  actions proposed vs actions taken (always [] in shadow/suggest),
  token/cost/duration spend, and the prompt-registry digest so any entry
  can be traced to the exact prompt bytes in effect. Compliance packs
  snapshot the rows touching a release's runs.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0108"
down_revision = "0107"
branch_labels = None
depends_on = None

# Investigation statuses considered "active" for the one-active-per-run
# partial unique index. Mirrors AgentInvestigation ACTIVE_STATUSES in
# app/models/postgres.py — keep in sync.
_ACTIVE_STATUSES_SQL = "status IN ('queued', 'running', 'synthesizing')"


def upgrade() -> None:
    op.create_table(
        "agent_investigations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "run_id",
            UUID(as_uuid=True),
            sa.ForeignKey("test_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("mode", sa.String(10), nullable=False, server_default="shadow"),
        sa.Column("triggered_by", sa.String(40), nullable=False, server_default="manual"),
        sa.Column("budget", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("spend", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("hypotheses", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("verdict", JSONB(), nullable=True),
        sa.Column("prompt_versions", JSONB(), nullable=True),
        sa.Column("model_info", JSONB(), nullable=True),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("cancelled_by", sa.String(255), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index(
        "ix_agent_investigations_project_created",
        "agent_investigations",
        ["project_id", "created_at"],
    )
    op.create_index(
        "ix_agent_investigations_run",
        "agent_investigations",
        ["run_id"],
    )
    # One ACTIVE investigation per run — partial unique index.
    op.create_index(
        "uq_agent_investigations_one_active_per_run",
        "agent_investigations",
        ["run_id"],
        unique=True,
        postgresql_where=sa.text(_ACTIVE_STATUSES_SQL),
    )

    op.create_table(
        "agent_policies",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("agent_id", sa.String(50), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("mode", sa.String(10), nullable=False, server_default="shadow"),
        sa.Column("budgets", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("shadow_runs_completed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("promotion_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("project_id", "agent_id", name="uq_agent_policies_project_agent"),
    )

    op.create_table(
        "agent_runs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("agent_id", sa.String(50), nullable=False),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "run_id",
            UUID(as_uuid=True),
            sa.ForeignKey("test_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("mode", sa.String(10), nullable=False, server_default="shadow"),
        sa.Column("trigger", sa.String(40), nullable=False, server_default="manual"),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("actions_proposed", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("actions_taken", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("prompt_registry_digest", sa.String(64), nullable=True),
        sa.Column("details_path", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index(
        "ix_agent_runs_project_agent_created",
        "agent_runs",
        ["project_id", "agent_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_agent_runs_project_agent_created", table_name="agent_runs")
    op.drop_table("agent_runs")
    op.drop_table("agent_policies")
    op.drop_index("uq_agent_investigations_one_active_per_run", table_name="agent_investigations")
    op.drop_index("ix_agent_investigations_run", table_name="agent_investigations")
    op.drop_index("ix_agent_investigations_project_created", table_name="agent_investigations")
    op.drop_table("agent_investigations")
