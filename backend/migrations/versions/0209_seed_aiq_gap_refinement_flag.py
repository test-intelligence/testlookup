"""Seed ``aiq_gap_refinement`` ON: the deterministic gap/refinement stages run (AGT-16).

The deep pipeline's ``gap_detection`` and ``report_refinement`` stages were
behind the environment switch ``AIQ_GAP_REFINEMENT_ENABLED`` (default off), so
no install ran them and every decision report said "Analysis coverage: not
recorded". The switch becomes the per-project feature flag
``aiq_gap_refinement``, resolved once per deep pipeline and frozen like the
other stage flags.

Both stages are deterministic (no LLM, no egress), so the row is seeded ON for
EVERY install, new or existing — unless ``AIQ_GAP_REFINEMENT_ENABLED`` is set
in the environment at migration time, in which case its parsed value wins
(``1/true/yes/on`` is on, anything else off): an operator who chose a value
keeps it. The variable stays the legacy fallback consulted only while no row
exists (``feature_flags.LEGACY_ENV_VAR_MAP``).

A row that already exists (created by hand) is left untouched (``ON CONFLICT
(key) DO NOTHING``). The downgrade deletes the row only while it still equals
the seed (same description, never edited, no project/role lists, rollout 100),
the rule of 0208.

Revision ID: 0209
Revises: 0208
"""
import os

from alembic import op
import sqlalchemy as sa

revision = "0209"
down_revision = "0208"
branch_labels = None
depends_on = None

LEGACY_ENV = "AIQ_GAP_REFINEMENT_ENABLED"

#: (key, description, legacy env variable). Named ``_FLAGS`` so the
#: feature-flag catalogue guard reads the key.
_FLAGS = (
    (
        "aiq_gap_refinement",
        "Deterministic gap-detection and report-refinement stages of the deep pipeline "
        "(no LLM, no egress); adds the \"Analysis coverage\" line to decision reports.",
        LEGACY_ENV,
    ),
)

_TRUTHY = frozenset({"1", "true", "yes", "on"})

INSERT_SQL = (
    "INSERT INTO feature_flags "
    "(id, key, description, enabled_global, rollout_percent, created_at, updated_at) "
    "SELECT gen_random_uuid(), :key, :description, CAST(:enabled AS boolean), "
    "100, now(), now() "
    "ON CONFLICT (key) DO NOTHING"
)

DELETE_SQL = (
    "DELETE FROM feature_flags WHERE key = :key AND description = :description "
    "AND created_at = updated_at AND enabled_projects IS NULL AND enabled_roles IS NULL "
    "AND rollout_percent = 100"
)


def _env_on(name: str) -> bool:
    """True when the variable is set to 1/true/yes/on (case-insensitive)."""
    return os.environ.get(name, "").strip().lower() in _TRUTHY


def seed_enabled(name: str = LEGACY_ENV) -> bool:
    """ON unless the legacy variable is set; then its parsed value."""
    if os.environ.get(name) is None:
        return True
    return _env_on(name)


def insert_params() -> list[dict]:
    """The bound values of each row's INSERT, with the environment read now."""
    return [
        {"key": key, "description": description, "enabled": seed_enabled(legacy_env)}
        for key, description, legacy_env in _FLAGS
    ]


def upgrade() -> None:
    for params in insert_params():
        op.execute(sa.text(INSERT_SQL).bindparams(**params))


def downgrade() -> None:
    for key, description, _legacy_env in _FLAGS:
        op.execute(
            sa.text(DELETE_SQL).bindparams(key=key, description=description)
        )
