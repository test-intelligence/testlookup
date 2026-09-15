"""Move Investigator and Fixer policy rows into agent_configs (E4.4).

Revision ID: 0187
Revises: 0186
Create Date: 2026-09-15

The two legacy agents predate the capability registry. Their pinned API data
is kept in the typed ``config.extensions`` document while ``enabled`` and
``mode`` move to their single canonical columns. No index is added to an
existing table. Downgrade recreates the old table and restores both row shapes.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0187"
down_revision = "0186"
branch_labels = None
depends_on = None

_AGENTS = ("investigator", "fixer")


def upgrade() -> None:
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM agent_policies
                WHERE agent_id NOT IN ('investigator', 'fixer')
            ) THEN
                RAISE EXCEPTION 'agent_policies contains an unknown agent_id; refusing lossy E4.4 migration';
            END IF;
        END $$
    """))
    op.execute(sa.text("""
        INSERT INTO agent_configs (
            id, project_id, agent_id, enabled, mode, config,
            config_version, updated_by, created_at, updated_at
        )
        SELECT
            p.id, p.project_id, p.agent_id, p.enabled, p.mode,
            CASE p.agent_id
                WHEN 'investigator' THEN jsonb_build_object(
                    'extensions', jsonb_build_object(
                        'investigator', jsonb_build_object(
                            'budgets', p.budgets,
                            'shadow_runs_completed', p.shadow_runs_completed,
                            'promotion_note', p.promotion_note
                        )
                    )
                )
                ELSE jsonb_build_object(
                    'extensions', jsonb_build_object(
                        'fixer', jsonb_build_object(
                            'runner', COALESCE(p.budgets -> 'runner', '{}'::jsonb),
                            'test_globs', COALESCE(
                                p.budgets -> 'test_globs',
                                '["tests/**", "**/*.spec.*", "**/*.test.*"]'::jsonb
                            ),
                            'budgets', jsonb_build_object(
                                'max_tests_per_run', COALESCE(p.budgets -> 'max_tests_per_run', '3'::jsonb),
                                'max_attempts_per_test', COALESCE(p.budgets -> 'max_attempts_per_test', '2'::jsonb),
                                'validation_reruns', COALESCE(p.budgets -> 'validation_reruns', '5'::jsonb),
                                'max_concurrent_open_prs', COALESCE(
                                    p.budgets -> 'max_concurrent_open_prs', '2'::jsonb
                                )
                            ),
                            'schedule', COALESCE(p.budgets -> 'schedule', '"off"'::jsonb)
                        )
                    )
                )
            END,
            1, NULL, COALESCE(p.created_at, now()), COALESCE(p.updated_at, now())
        FROM agent_policies AS p
        WHERE p.agent_id IN ('investigator', 'fixer')
        ON CONFLICT ON CONSTRAINT uq_agent_configs_project_agent DO UPDATE SET
            enabled = EXCLUDED.enabled,
            mode = EXCLUDED.mode,
            config = agent_configs.config || EXCLUDED.config,
            config_version = agent_configs.config_version + 1,
            updated_at = EXCLUDED.updated_at
    """))
    op.drop_table("agent_policies")


def downgrade() -> None:
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
    op.execute(sa.text("""
        INSERT INTO agent_policies (
            id, project_id, agent_id, enabled, mode, budgets,
            shadow_runs_completed, promotion_note, created_at, updated_at
        )
        SELECT
            c.id, c.project_id, c.agent_id, c.enabled, c.mode,
            CASE c.agent_id
                WHEN 'investigator' THEN COALESCE(
                    c.config #> '{extensions,investigator,budgets}', '{}'::jsonb
                )
                ELSE COALESCE(c.config #> '{extensions,fixer,budgets}', '{}'::jsonb)
                    || jsonb_build_object(
                        'runner', COALESCE(c.config #> '{extensions,fixer,runner}', '{}'::jsonb),
                        'test_globs', COALESCE(
                            c.config #> '{extensions,fixer,test_globs}',
                            '["tests/**", "**/*.spec.*", "**/*.test.*"]'::jsonb
                        ),
                        'schedule', COALESCE(c.config #> '{extensions,fixer,schedule}', '"off"'::jsonb)
                    )
            END,
            CASE c.agent_id
                WHEN 'investigator' THEN COALESCE(
                    (c.config #>> '{extensions,investigator,shadow_runs_completed}')::integer, 0
                )
                ELSE 0
            END,
            CASE c.agent_id
                WHEN 'investigator' THEN c.config #>> '{extensions,investigator,promotion_note}'
                ELSE NULL
            END,
            c.created_at, c.updated_at
        FROM agent_configs AS c
        WHERE c.agent_id IN ('investigator', 'fixer')
    """))
    op.execute(sa.text("DELETE FROM agent_configs WHERE agent_id IN ('investigator', 'fixer')"))
