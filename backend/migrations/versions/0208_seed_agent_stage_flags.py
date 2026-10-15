"""Seed the deep pipeline's stage flags so admins can see and toggle them (AIC-07).

The planner and ``_create_pipeline_run`` already evaluate six stage flags per
project (``is_enabled(..., fresh=True)``), but no migration created their rows:
Settings -> Feature flags did not list them, so an admin could not switch a
stage on, and a missing row was simply off. This migration inserts the six rows.

Defaults (owner decision D20 and the fresh-install rule of 0201 / 0204):

* ``contract_validation``, ``log_intelligence``, ``regression_watchman``,
  ``change_ownership`` (read-only stages): ON for a new install — no user and
  no project yet, the state ``alembic upgrade head`` runs in before the first
  admin exists — and OFF on an existing install.
* ``defect_commander`` (the one MUTATING stage, it promotes clusters to
  defects): OFF everywhere until Act mode routes its effects through approvals.
* ``async_decision_report_supersession``: OFF (the zero-change state; visible
  and toggleable).

A key that has a legacy environment switch (``feature_flags.LEGACY_ENV_VAR_MAP``)
is seeded ON when that variable is truthy at migration time (``1/true/yes/on``,
case-insensitive, the rule of ``feature_flags._legacy_env_fallback``). That
variable was consulted only while no row existed, so seeding the row OFF would
silently switch off a stage an operator had enabled through it.

Homelab / existing installs: the four read-only rows arrive OFF (unless their
legacy variable was on); the owner flips them ON during the deploy
(``PATCH /api/v1/feature-flags/<key> {"enabled_global": true}``).

A row that already exists (created by hand) is left untouched (``ON CONFLICT
(key) DO NOTHING``). The downgrade deletes a row only while it still equals
the seed: same description, never edited (``created_at = updated_at``; an
admin toggle stamps ``updated_at``), no project/role lists, rollout 100.

Revision ID: 0208
Revises: 0207
"""
import os

from alembic import op
import sqlalchemy as sa

revision = "0208"
down_revision = "0207"
branch_labels = None
depends_on = None

# The fresh-install condition of 0201 / 0204, verbatim.
FRESH_INSTALL = (
    "NOT EXISTS (SELECT 1 FROM users) AND NOT EXISTS (SELECT 1 FROM projects)"
)

#: (key, description, on for a fresh install, legacy env variable or None).
#: Named ``_FLAGS`` so the feature-flag catalogue guard reads the keys.
_FLAGS = (
    (
        "contract_validation",
        "API-contract specialist stage of the deep pipeline (read-only).",
        True,
        "AIQ_CONTRACT_VALIDATION_ENABLED",
    ),
    (
        "log_intelligence",
        "Log and trace evidence stage of the deep pipeline (read-only; needs Splunk, "
        "which stays off while AI_OFFLINE_MODE is on).",
        True,
        None,
    ),
    (
        "regression_watchman",
        "Classifies failures as new regression, known-flaky recurrence or environmental "
        "anomaly (read-only; may use the LLM).",
        True,
        None,
    ),
    (
        "change_ownership",
        "Baseline-diff and ownership stage of the deep pipeline (read-only).",
        True,
        "AIQ_CHANGE_OWNERSHIP_ENABLED",
    ),
    (
        "defect_commander",
        "MUTATING deep-pipeline stage: promotes high-confidence failure clusters to "
        "defects. Off until Act mode routes its effects through approvals.",
        False,
        None,
    ),
    (
        "async_decision_report_supersession",
        "Asynchronous supersession of decision reports in deep pipelines.",
        False,
        "AIQ_ASYNC_DECISION_REPORT_SUPERSESSION_ENABLED",
    ),
)
#: The contract's name for the same rows.
_STAGES = _FLAGS

_TRUTHY = frozenset({"1", "true", "yes", "on"})

INSERT_SQL = (
    "INSERT INTO feature_flags "
    "(id, key, description, enabled_global, rollout_percent, created_at, updated_at) "
    "SELECT gen_random_uuid(), :key, :description, "
    "((CAST(:fresh_on AS boolean)) AND (" + FRESH_INSTALL + ")) OR CAST(:env_on AS boolean), "
    "100, now(), now() "
    "ON CONFLICT (key) DO NOTHING"
)

DELETE_SQL = (
    "DELETE FROM feature_flags WHERE key = :key AND description = :description "
    "AND created_at = updated_at AND enabled_projects IS NULL AND enabled_roles IS NULL "
    "AND rollout_percent = 100"
)


def _env_on(name: str | None) -> bool:
    """True when the legacy variable is set to 1/true/yes/on (case-insensitive)."""
    if not name:
        return False
    return os.environ.get(name, "").strip().lower() in _TRUTHY


def insert_params() -> list[dict]:
    """The bound values of each row's INSERT, with the environment read now."""
    return [
        {
            "key": key,
            "description": description,
            "fresh_on": bool(fresh_on),
            "env_on": _env_on(legacy_env),
        }
        for key, description, fresh_on, legacy_env in _FLAGS
    ]


def upgrade() -> None:
    for params in insert_params():
        op.execute(sa.text(INSERT_SQL).bindparams(**params))


def downgrade() -> None:
    for key, description, _fresh_on, _legacy_env in _FLAGS:
        op.execute(
            sa.text(DELETE_SQL).bindparams(key=key, description=description)
        )
