"""seed the ask_ai_chat feature flag (enabled)

Revision ID: 0100
Revises: 0099
Create Date: 2026-07-09

PMF backlog US-2.1: the Ask-AI chat page has existed fully built since the
chat router landed, but its route was commented out of the SPA — a headline
capability (chat with your test data against a fully local LLM) invisible
to users. The frontend now registers the route and gates the nav entry on
this flag AND a non-rules AI mode (the page itself renders "switch mode"
guidance when an LLM is unreachable, so a direct URL never dead-ends).

Seeded ``enabled_global=true`` — the additional AI-mode condition in the
sidebar means installs without an LLM configured still show no dead nav
entry; admins can hard-disable from Settings > Feature Flags.
"""
from alembic import op
import sqlalchemy as sa

revision = "0100"
down_revision = "0099"
branch_labels = None
depends_on = None

_KEY = "ask_ai_chat"
_DESCRIPTION = (
    "Show the Ask-AI chat page (natural-language Q&A over runs/failures via "
    "the configured LLM). The sidebar additionally hides the entry when the "
    "AI analysis mode is 'rules'."
)


def upgrade() -> None:
    op.execute(
        sa.text(
            "INSERT INTO feature_flags "
            "(id, key, description, enabled_global, rollout_percent, created_at, updated_at) "
            "VALUES (gen_random_uuid(), :key, :desc, true, 100, now(), now()) "
            "ON CONFLICT (key) DO NOTHING"
        ).bindparams(key=_KEY, desc=_DESCRIPTION)
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM feature_flags WHERE key = :key").bindparams(key=_KEY))
