"""Regression coverage for the notification history ORM projection.

The notification history endpoint loads complete ``NotificationLog`` rows.
Keeping the ORM projection aligned with the migrations is therefore an API
availability invariant: a mapped column that is absent from PostgreSQL makes
the otherwise read-only endpoint fail with ``UndefinedColumn``.
"""

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql import select

from app.models.postgres import NotificationLog


@pytest.mark.integration
def test_notification_log_projection_contains_only_migrated_columns() -> None:
    expected = {
        "id",
        "user_id",
        "project_id",
        "run_id",
        "preference_id",
        "delivery_key",
        "delivery_metadata",
        "delivery_attempts",
        "delivery_token",
        "delivery_started_at",
        "delivery_lease_expires_at",
        "next_delivery_at",
        "channel",
        "event_type",
        "title",
        "body",
        "status",
        "error_detail",
        "routed_team",
        "routing_fallback",
        "is_read",
        "sent_at",
        "created_at",
        "updated_at",
    }

    assert {column.name for column in NotificationLog.__table__.columns} == expected

    # Compile the exact shape used by list_notification_history.  This keeps
    # the guard independent of a live database while still exercising the
    # complete SELECT projection that previously referenced missing columns.
    statement = select(NotificationLog).where(NotificationLog.user_id.is_not(None)).limit(50)
    sql = str(statement.compile(dialect=postgresql.dialect()))
    assert 'notification_logs' in sql
    assert 'active_test_run_id' not in sql
    assert 'active_report_id' not in sql
    assert 'active_report_version' not in sql
