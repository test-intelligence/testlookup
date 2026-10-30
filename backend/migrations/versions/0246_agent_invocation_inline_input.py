"""The inline input of an agent invocation (W23c AGT-11).

1. ``agent_invocations.input_payload`` -- ``JSONB NULL``, no default: a
   catalogue change, no table rewrite. The inline input of the invocation,
   REDACTED before it is stored (``untrusted_input.scrub`` on every string),
   as canonical JSON; NULL = a stored-subject invocation (the server assembled
   the input from the run). Frozen: a retry / resume replays it.
2. ``agent_invocations.input_sha256`` -- ``VARCHAR(64) NULL``, no default: the
   sha256 of the canonical compact JSON of ``input_payload`` (the redacted
   bytes); the inline review's evidence hash.
3. ``ck_agent_invocations_input_pair`` -- the two come together.
   ``ck_agent_invocations_input_sha256`` -- lower-case hex, 64 characters.
   Both columns are NULL on every existing row, so the CHECKs hold trivially;
   each is added NOT VALID then VALIDATEd, so the validation scan takes SHARE
   UPDATE EXCLUSIVE, not ACCESS EXCLUSIVE, on a table the API writes (the 0146
   pattern). The texts are the ORM ``CheckConstraint`` twins
   (``models/postgres.py`` ``AgentInvocation``).

No index (nothing filters by the new columns; "inline" is read by
``pipeline_run_id`` through the existing unique ``ux_agent_invocations_pipeline_run``).
No backfill (every existing invocation is a stored-subject one: NULL is the
truth). No ``app_settings`` row (AGT-10's enforcement date is unset on upgrade).

The downgrade drops the two CHECKs and the two columns, in reverse order.

Revision ID: 0246
Revises: 0245
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0246"
down_revision = "0245"
branch_labels = None
depends_on = None

TABLE = "agent_invocations"


def upgrade() -> None:
    op.add_column(TABLE, sa.Column("input_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column(TABLE, sa.Column("input_sha256", sa.String(length=64), nullable=True))
    # Both columns are NULL on every existing row, so the CHECKs hold trivially; added NOT VALID then
    # VALIDATEd so the validation scan takes SHARE UPDATE EXCLUSIVE, not ACCESS EXCLUSIVE (the 0146 pattern).
    op.execute(
        "ALTER TABLE agent_invocations ADD CONSTRAINT ck_agent_invocations_input_pair "
        "CHECK ((input_payload IS NULL) = (input_sha256 IS NULL)) NOT VALID"
    )
    op.execute("ALTER TABLE agent_invocations VALIDATE CONSTRAINT ck_agent_invocations_input_pair")
    op.execute(
        "ALTER TABLE agent_invocations ADD CONSTRAINT ck_agent_invocations_input_sha256 "
        "CHECK (input_sha256 IS NULL OR input_sha256 ~ '^[0-9a-f]{64}$') NOT VALID"
    )
    op.execute("ALTER TABLE agent_invocations VALIDATE CONSTRAINT ck_agent_invocations_input_sha256")


def downgrade() -> None:
    op.drop_constraint("ck_agent_invocations_input_sha256", TABLE, type_="check")
    op.drop_constraint("ck_agent_invocations_input_pair", TABLE, type_="check")
    op.drop_column(TABLE, "input_sha256")
    op.drop_column(TABLE, "input_payload")
