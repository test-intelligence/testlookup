import logging
import uuid
import random
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException

from app.core.deps import require_role
from app.models.postgres import UserRole
from app.models.schemas import SentinelFile
from app.services.mock_generator import generate_mock_allure_results, generate_mock_testng_results
from app.db.storage import get_storage_provider
from app.worker.tasks import ingest_test_run

logger = logging.getLogger(__name__)

router = APIRouter()

@router.post(
    "/generate-test-run",
    status_code=202,
    dependencies=[Depends(require_role(UserRole.ADMIN))],
)
async def generate_mock_test_run(
    project_id: uuid.UUID,
    num_tests: int = 50,
    failure_rate: float = 0.2,
    report_type: Literal["allure", "testng", "both"] = "both",
):
    """
    Generates a synthetic test run and triggers the ingestion pipeline.
    """
    try:
        build_number = str(random.randint(10000, 99999))
        minio_prefix = f"{project_id}/builds/{build_number}/"
        
        storage = get_storage_provider()
        uploaded_files = []
        
        # Generate Allure 
        if report_type in ("allure", "both"):
            allure_files = generate_mock_allure_results(num_tests, failure_rate, str(project_id), build_number)
            for k, content in allure_files:
                full_key = f"{minio_prefix}{k}"
                await storage.put_object(key=full_key, content=content, content_type="application/json")
                uploaded_files.append(full_key)
                
        # Generate TestNG
        if report_type in ("testng", "both"):
            testng_files = generate_mock_testng_results(num_tests, failure_rate, str(project_id), build_number)
            for k, content in testng_files:
                full_key = f"{minio_prefix}{k}"
                await storage.put_object(key=full_key, content=content, content_type="application/xml")
                uploaded_files.append(full_key)
                
        # Trigger Celery Ingestion
        sentinel = SentinelFile(
            build_number=build_number,
            project_id=str(project_id),
            jenkins_job="mock-data-generator",
            trigger_source="api-trigger",
            branch="main",
            commit_hash="mock999hash",
        )
        
        # Dispatch Async Task
        ingest_test_run.delay(sentinel.model_dump(), minio_prefix)
        
        return {
            "status": "accepted",
            "message": "Synthetic test run generated and ingestion triggered.",
            "project_id": project_id,
            "build_number": build_number,
            "files_uploaded": len(uploaded_files),
        }
    except Exception as e:
        logger.error(f"Error generating mock test run: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
