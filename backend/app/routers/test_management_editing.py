"""Test-case editing routes (W28): bulk changes (TCM-03) and restoring a version (TCM-04).

Included in ``routers/test_management.py`` BEFORE ``test_management_cases`` so
the static ``/cases/bulk`` is matched before any ``/cases/{case_id}`` route.
Both handlers take ``require_role(UserRole.TESTER)``: a VIEWER changes nothing.
The services stage; these handlers own the commit. Their activity-ledger rows
are recorded by ``services/test_case_editing_service.py`` on this session.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import cast

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_role, resolve_project_scope
from app.db.postgres import get_db
from app.models.postgres import ManagedTestCase, User, UserRole
from app.models.schemas import (
    ManagedTestCaseResponse,
    TestCaseBulkRequest,
    TestCaseBulkResponse,
    TestCaseRestoreRequest,
)
from app.routers.test_management_cases import case_response
from app.routers.test_management_shared import require_case_access
from app.services.test_case_editing_service import (
    apply_bulk_action,
    refuse_retire_below_lead,
    restore_case_version,
)
from app.services.test_case_lifecycle_service import require_lifecycle_v2_enabled
from app.services.test_case_review_policy import project_requires_case_review
from app.services.test_management_metrics_service import emit_staged_test_management_metrics

router = APIRouter()


@router.post("/cases/bulk", response_model=TestCaseBulkResponse)
async def bulk_edit_test_cases(
    payload: TestCaseBulkRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.TESTER)),
):
    """Change the status, owner, priority, tags or suite of selected cases of ONE project.

    The body's ``project_id`` is authorised FIRST (403 before anything is
    loaded); ``case_ids`` are then loaded only within that project, so another
    project's id is ``not_found`` in the per-case results, like an unknown one.
    One 200 answers per case; a refused case never fails the request.
    """
    await resolve_project_scope(db, current_user, str(payload.project_id))
    # Deprecate / Archive / Reinstate stay QA_LEAD or above (W28 AMENDMENT):
    # one 403 for the whole request, nothing written.
    refuse_retire_below_lead(payload, current_user)
    if payload.action == "transition":
        # The direct lifecycle moves exist only where lifecycle v2 is on: the
        # same 404, with the same sentence, as POST /cases/{id}/transition.
        project_only = cast(ManagedTestCase, SimpleNamespace(project_id=payload.project_id))
        await require_lifecycle_v2_enabled(db, project_only, current_user)
    review_required = await project_requires_case_review(db, payload.project_id)
    result = await apply_bulk_action(
        db, payload, current_user, review_required=review_required
    )
    await db.commit()
    await emit_staged_test_management_metrics(db)
    return result


@router.post("/cases/{case_id}/restore", response_model=ManagedTestCaseResponse)
async def restore_test_case_version(
    case_id: uuid.UUID,
    payload: TestCaseRestoreRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.TESTER)),
    _case: ManagedTestCase = Depends(require_case_access),
):
    """Save an older version's content as a new version; status and owner are kept.

    Answers the case unchanged (same version) when that version's content is
    already the current one.
    """
    test_case = await restore_case_version(db, _case, payload, current_user)
    await db.commit()
    await emit_staged_test_management_metrics(db)
    await db.refresh(test_case)
    return await case_response(db, test_case, current_user)
