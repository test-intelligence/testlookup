"""
Stable enumerations shared across ORM models, Pydantic schemas, and agent code.

Import from here — never duplicate enum values as raw string literals.
"""
from enum import Enum as PyEnum


class WorkflowType(str, PyEnum):
    OFFLINE = "offline"
    DEEP    = "deep"
    LIVE    = "live"


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
