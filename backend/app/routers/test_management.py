"""Test management router composed from focused subrouters."""

from fastapi import APIRouter

from app.routers import (
    test_management_ai,
    test_management_audit,
    test_management_cases,
    test_management_editing,
    test_management_exports,
    test_management_library,
    test_management_plans,
    test_management_strategies,
    test_management_suite_reviews,
)

router = APIRouter(prefix="/api/v1/test-management", tags=["Test Management"])

# Before the cases router: its static POST /cases/bulk must be matched first.
router.include_router(test_management_editing.router)
router.include_router(test_management_cases.router)
router.include_router(test_management_library.router)
router.include_router(test_management_ai.router)
router.include_router(test_management_plans.router)
router.include_router(test_management_strategies.router)
router.include_router(test_management_audit.router)
router.include_router(test_management_exports.router)
router.include_router(test_management_suite_reviews.router)
