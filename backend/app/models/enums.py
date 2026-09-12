"""
Stable enumerations shared across ORM models, Pydantic schemas, and agent code.

Import from here — never duplicate enum values as raw string literals.
"""
from enum import Enum as PyEnum


class WorkflowType(str, PyEnum):
    OFFLINE = "offline"
    DEEP    = "deep"
    LIVE    = "live"


class PipelineRunStatus(str, PyEnum):
    """Internal vocabulary of ``agent_pipeline_runs.status`` (architecture E7.1).

    Exactly these six values are allowed by the DB CHECK constraint from
    migration 0173. The public API projects them onto four:
    ``in_progress | completed | failed | passed`` (see
    ``app.services.workflow_run_state.public_status``). ``partial`` and
    ``cancelled`` are legacy inputs that normalise to ``completed`` (with
    ``stage_quality=degraded``) and ``failed`` (with a ``cancelled:`` error)
    respectively; nothing may write them.
    """
    PENDING    = "pending"
    RUNNING    = "running"
    RETRY_WAIT = "retry_wait"
    COMPLETED  = "completed"
    PASSED     = "passed"
    FAILED     = "failed"


class InvestigationDepth(str, PyEnum):
    SHALLOW  = "shallow"
    STANDARD = "standard"
    DEEP     = "deep"


class SearchType(str, PyEnum):
    KEYWORD  = "keyword"
    SEMANTIC = "semantic"
    HYBRID   = "hybrid"


class CriticalityLevel(str, PyEnum):
    CRITICAL = "CRITICAL"
    HIGH     = "HIGH"
    MEDIUM   = "MEDIUM"
    LOW      = "LOW"


class RegressionClassification(str, PyEnum):
    NEW_REGRESSION = "new_regression"
    KNOWN_FLAKY    = "known_flaky"
    ENVIRONMENTAL  = "environmental"
    PRODUCT_BUG    = "product_bug"
    INFRASTRUCTURE = "infrastructure"
    UNCLASSIFIED   = "unclassified"


class ExecutionPath(str, PyEnum):
    """Records why a pipeline stage was reached or skipped."""
    EXECUTED            = "executed"           # stage ran normally
    ALL_GREEN_SKIP      = "all_green_skip"     # no failures — analysis stages bypassed
    LOW_CONFIDENCE_SKIP = "low_confidence_skip"  # no analyses above threshold — triage skipped
    CONDITIONAL_SKIP    = "conditional_skip"   # stage not on active pipeline branch
    DEADLINE_SKIP       = "deadline_skip"      # pipeline wall-clock budget exhausted before the stage ran
