import inspect
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError


@pytest.mark.asyncio
async def test_integrity_conflict_rolls_back_and_hides_database_details():
    from app.routers.scim import _raise_scim_integrity_conflict

    db = AsyncMock()
    error = IntegrityError(
        "INSERT INTO users (email) VALUES (?)",
        {"email": "secret@example.test"},
        RuntimeError("duplicate key users_email_key"),
    )

    with pytest.raises(HTTPException) as raised:
        await _raise_scim_integrity_conflict(db, error)

    assert raised.value.status_code == 409
    assert raised.value.detail == "SCIM resource conflicts with existing directory data"
    assert "secret@example.test" not in raised.value.detail
    assert "users_email_key" not in raised.value.detail
    db.rollback.assert_awaited_once_with()


@pytest.mark.parametrize(
    "endpoint_name",
    ["scim_create", "scim_replace", "scim_patch", "scim_delete"],
)
def test_every_public_scim_mutation_recovers_integrity_errors(endpoint_name):
    from app.routers import scim

    source = inspect.getsource(getattr(scim, endpoint_name))

    assert "except IntegrityError as exc:" in source
    assert "await _raise_scim_integrity_conflict(db, exc)" in source
    assert source.index("await scim_") < source.index("except IntegrityError as exc:")
    assert source.index("await db.commit()") < source.index("except IntegrityError as exc:")
