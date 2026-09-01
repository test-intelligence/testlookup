"""H1 — stop the run CASCADE from destroying protected evidence artifacts.

``retention_service`` deliberately spares evidence artifacts that a **published
decision report** still references: ``_published_report_artifact_ids`` filters
them out of the explicit ``delete(EvidenceArtifact)``, and fails closed so an
error protects everything rather than nothing.

Two steps later the purge runs ``delete(TestRun)``, and
``evidence_artifacts.run_id`` was ``ondelete="CASCADE", nullable=False`` — so
the CASCADE destroyed exactly what the filter had spared. With the shipped
defaults (``artifacts_days=180`` < ``runs_days=365``) that fires on every
project holding runs older than a year, silently, and the purge's
``evidence_artifact_rows`` count under-reports it because the count is taken
after the protective filter.

The same CASCADE also made the artifacts clock behave as
``min(artifacts_days, runs_days)``: an artifact younger than its own retention
window died anyway when its run aged out, which contradicts having two clocks.

SET NULL keeps the row. ``retention_service`` stamps ``project_id`` before the
link detaches — the pattern already used for ``ai_provenance_records`` — so a
surviving artifact stays project-scoped and remains purgeable by the artifacts
clock later, rather than becoming unreachable.

**Migration-number note.** Numbered 0145 against head 0144. The
``feat/retention-s1-activate`` branch also carries a 0145; whichever merges
second must renumber. This is a hotfix for live data loss and should land
first.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0145"
down_revision = "0144"
branch_labels = None
depends_on = None


_FK = "evidence_artifacts_run_id_fkey"


def upgrade() -> None:
    # Metadata-only; no table rewrite.
    op.alter_column(
        "evidence_artifacts",
        "run_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=True,
    )

    # Re-point the FK. Added NOT VALID first so the ACCESS EXCLUSIVE lock is
    # held only for the catalog update rather than for a full scan of what can
    # be a large table; VALIDATE then takes the weaker SHARE UPDATE EXCLUSIVE.
    # Every existing row already satisfies the constraint (it was enforced
    # until a moment ago), so validation cannot fail — it just costs a read.
    op.drop_constraint(_FK, "evidence_artifacts", type_="foreignkey")
    op.execute(
        sa.text(
            f'ALTER TABLE evidence_artifacts ADD CONSTRAINT "{_FK}" '
            "FOREIGN KEY (run_id) REFERENCES test_runs(id) "
            "ON DELETE SET NULL NOT VALID"
        )
    )
    op.execute(sa.text(f'ALTER TABLE evidence_artifacts VALIDATE CONSTRAINT "{_FK}"'))


def downgrade() -> None:
    # Rows whose run was purged while SET NULL was in force cannot satisfy the
    # restored NOT NULL. Deleting them would destroy the very evidence this
    # migration exists to preserve, so they are detached from the constraint
    # instead: the downgrade fails loudly if any exist, and the operator
    # decides. A downgrade that silently drops protected audit evidence would
    # be worse than one that refuses.
    conn = op.get_bind()
    orphaned = conn.execute(
        sa.text("SELECT count(*) FROM evidence_artifacts WHERE run_id IS NULL")
    ).scalar()
    if orphaned:
        raise RuntimeError(
            f"{orphaned} evidence_artifacts row(s) have run_id IS NULL — their "
            "runs were purged while SET NULL was in force. Restoring NOT NULL "
            "would require deleting them, which is the data loss 0145 fixed. "
            "Re-home or export them first, then re-run this downgrade."
        )

    op.drop_constraint(_FK, "evidence_artifacts", type_="foreignkey")
    op.create_foreign_key(
        _FK,
        "evidence_artifacts",
        "test_runs",
        ["run_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.alter_column(
        "evidence_artifacts",
        "run_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=False,
    )
