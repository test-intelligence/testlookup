"""Seed the ``manual_upload`` feature flag (MRU-17 rollout gate).

Revision ID: 0092
Revises: 0091
Create Date: 2026-06-07

Gates the manual report-upload UI (the "Upload report" button + sidebar entry +
the ``/runs?upload=1`` deep-link). Defaults OFF so the feature rolls out by an
ADMIN enabling it per environment / project / role from Settings > Feature
Flags — mirroring the cypress_ingest / playwright_ingest flags. The underlying
``POST /api/v1/ingest/file`` endpoint is unaffected (API/CI clients are not
gated by this UI flag).
"""
from alembic import op
import sqlalchemy as sa


revision = "0092"
down_revision = "0091"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "INSERT INTO feature_flags "
            "(id, key, description, enabled_global, rollout_percent, created_at, updated_at) "
            "VALUES (gen_random_uuid(), 'manual_upload', "
            "'Manual test-report upload UI (upload JUnit/TestNG/Allure/Playwright/Cypress "
            "files or an Allure zip from /runs). MRU-17.', "
            "false, 100, now(), now()) "
            "ON CONFLICT (key) DO NOTHING"
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM feature_flags WHERE key = 'manual_upload'"))
