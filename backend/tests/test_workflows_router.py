from __future__ import annotations

import inspect
import uuid
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.routers import workflows
from app.services import workflow_definition_service as svc


def _dependency_names(endpoint) -> set[str]:
    names: set[str] = set()
    for dependency in endpoint.__dict__.get("__wrapped_dependencies__", []):
        names.add(getattr(dependency, "__name__", ""))
    return names


def test_every_route_is_project_scoped_and_mutations_require_qa_lead() -> None:
    for route in workflows.router.routes:
        assert "{project_id}" in route.path
        signature = inspect.signature(route.endpoint)
        assert "current_user" in signature.parameters
        if set(route.methods or ()) & {"POST", "PUT", "DELETE"} and not route.path.endswith("/validate"):
            assert "_lead" in signature.parameters


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["update", "delete"])
async def test_builtin_mutations_return_405(operation: str) -> None:
    if operation == "update":
        call = workflows.update_workflow(
            uuid.uuid4(), "offline", svc.WorkflowBodyV1.model_validate({
                "workflow_id": "offline", "name": "No", "base": "offline",
                "steps": [{"id": "ingest", "agent_id": "agent.ingestion.v1"}],
            }), AsyncMock(), object(), object()
        )
    else:
        call = workflows.delete_workflow(uuid.uuid4(), "offline", AsyncMock(), object(), object())
    with pytest.raises(HTTPException) as exc:
        await call
    assert exc.value.status_code == 405


def test_router_is_registered_as_protected() -> None:
    from app import bootstrap

    assert workflows.router in bootstrap.PROTECTED_ROUTERS
