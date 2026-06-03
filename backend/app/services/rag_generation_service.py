from structlog.stdlib import get_logger

logger = get_logger()


class RagGenerationService:
    def __init__(self) -> None:
        ...

    def generate(self, project_id: str, query: str) -> str:
        try:
            # generation logic here
            return "generated_answer"
        except Exception as exc:
            logger.warning(
                "rag_generation_failed",
                project_id=project_id,
                query=query,
                error=str(exc),
            )
            raise
