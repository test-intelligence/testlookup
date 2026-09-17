"""Release override audit updates must serialize on their decision row."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy.dialects import postgresql


class _CapturedStatement(RuntimeError):
    pass


class _CaptureSession:
    def __init__(self) -> None:
        self.statement = None

    async def execute(self, statement):
        self.statement = statement
        raise _CapturedStatement


@pytest.mark.asyncio
async def test_override_select_locks_the_decision_row() -> None:
    from app.services.release_council_service import apply_override

    db = _CaptureSession()
    with pytest.raises(_CapturedStatement):
        await apply_override(
            uuid.uuid4(),
            "GO",
            "QA reviewed the conflicting evidence",
            actor=None,
            db=db,  # type: ignore[arg-type]
        )

    assert db.statement is not None
    sql = str(
        db.statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "FOR UPDATE" in sql
