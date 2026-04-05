"""
Scoring Model router.

GET /api/v1/scoring-model
  Returns the 7-dimension risk scoring model: version, thresholds,
  dimension weights, and human-readable descriptions.

Consumed by the frontend CriticalityMatrix component to render
"Why this score?" expandable text without hardcoding descriptions.
"""
from fastapi import APIRouter

from app.services.criticality_service import get_scoring_model_info

router = APIRouter(prefix="/api/v1", tags=["Scoring Model"])


@router.get("/scoring-model")
async def get_scoring_model():
    """
    Return the current risk scoring model.

    Response shape:
    {
      "version": 1,
      "go_threshold": 20,
      "no_go_threshold": 55,
      "dimensions": [
        { "name": "user_impact", "weight": 0.25, "description": "..." },
        ...
      ]
    }
    """
    return get_scoring_model_info()
