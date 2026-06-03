from typing import Any, List
from structlog.stdlib import get_logger

logger = get_logger()


class KnowledgeSyncService:
    def __init__(self) -> None:
        ...

    def sync_project(self, project_id: str) -> None:
        try:
            self._sync_chroma(project_id=project_id)
        except Exception as exc:
            logger.warning(
                "knowledge_sync_chroma_failed",
                project_id=project_id,
                error=str(exc),
            )
            raise

    def _sync_chroma(self, project_id: str) -> None:
        # actual sync logic here
        ...

    def sync_all_projects(self, project_ids: List[str]) -> None:
        for pid in project_ids:
            try:
                self.sync_project(pid)
            except Exception as exc:
                logger.error(
                    "knowledge_sync_project_failed",
                    project_id=pid,
                    error=str(exc),
                )
