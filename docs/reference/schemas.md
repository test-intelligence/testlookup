# HTTP request and response schemas

[Documentation home](../README.md) · [Regeneration](../maintainers/maintenance.md)

Generated from tracked source by `scripts/generate_reference.py`. Do not edit by hand. What the checks cover is recorded in [verification](../maintainers/verification.md).

These are the full generated JSON Schema definitions, including required fields, defaults, enums, nested references and declared constraints. Custom validators and cross-field checks live in the Pydantic models under `backend/app/models/`; JSON Schema does not encode every service-level rule.

## AIActor

```json
{
  "properties": {
    "name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Name"
    },
    "user_id": {
      "title": "User Id",
      "type": "string"
    }
  },
  "required": [
    "user_id"
  ],
  "title": "AIActor",
  "type": "object"
}
```

## AIAnalysisDiffBrief

```json
{
  "description": "``RunCompareResponse.ai_analysis_diff``. Only ``available`` is required, so\n``{\"available\": false, \"reason\": ...}`` validates.",
  "properties": {
    "ai_changes": {
      "items": {
        "type": "string"
      },
      "title": "Ai Changes",
      "type": "array"
    },
    "available": {
      "title": "Available",
      "type": "boolean"
    },
    "clusters_added": {
      "items": {
        "additionalProperties": true,
        "type": "object"
      },
      "title": "Clusters Added",
      "type": "array"
    },
    "clusters_removed": {
      "items": {
        "additionalProperties": true,
        "type": "object"
      },
      "title": "Clusters Removed",
      "type": "array"
    },
    "counts": {
      "additionalProperties": {
        "type": "integer"
      },
      "title": "Counts",
      "type": "object"
    },
    "left_ref": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Left Ref"
    },
    "provenance": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Provenance"
    },
    "reason": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Reason"
    },
    "right_ref": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Right Ref"
    },
    "test_changes": {
      "items": {
        "additionalProperties": true,
        "type": "object"
      },
      "title": "Test Changes",
      "type": "array"
    },
    "verdict": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Verdict"
    }
  },
  "required": [
    "available"
  ],
  "title": "AIAnalysisDiffBrief",
  "type": "object"
}
```

## AICompareNarrative

```json
{
  "properties": {
    "ai_changes": {
      "items": {
        "type": "string"
      },
      "title": "Ai Changes",
      "type": "array"
    },
    "executive_summary": {
      "default": "",
      "title": "Executive Summary",
      "type": "string"
    },
    "fallback_used": {
      "default": false,
      "title": "Fallback Used",
      "type": "boolean"
    },
    "generated_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Generated At"
    },
    "label": {
      "title": "Label",
      "type": "string"
    },
    "model_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Model Name"
    },
    "prompt_version": {
      "default": "run_compare_v2",
      "title": "Prompt Version",
      "type": "string"
    },
    "report_status": {
      "default": "absent",
      "title": "Report Status",
      "type": "string"
    },
    "risk_level": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Risk Level"
    },
    "source": {
      "title": "Source",
      "type": "string"
    },
    "status": {
      "title": "Status",
      "type": "string"
    }
  },
  "required": [
    "status",
    "source",
    "label"
  ],
  "title": "AICompareNarrative",
  "type": "object"
}
```

## AICompareResponse

```json
{
  "properties": {
    "ai_disclaimer": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Ai Disclaimer"
    },
    "ai_disclaimer_version": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Ai Disclaimer Version"
    },
    "baseline": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Baseline"
    },
    "baseline_ref": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/AIVersionRef"
        },
        {
          "type": "null"
        }
      ]
    },
    "current_ref": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/AIVersionRef"
        },
        {
          "type": "null"
        }
      ]
    },
    "diff": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Diff"
    },
    "message": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Message"
    },
    "narrative": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/AICompareNarrative"
        },
        {
          "type": "null"
        }
      ]
    },
    "project_id": {
      "title": "Project Id",
      "type": "string"
    },
    "reason": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Reason"
    },
    "requires_human_review": {
      "default": true,
      "title": "Requires Human Review",
      "type": "boolean"
    },
    "review": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/AIHistoryReviewBlock"
        },
        {
          "type": "null"
        }
      ]
    },
    "run_id": {
      "title": "Run Id",
      "type": "string"
    },
    "sides": {
      "anyOf": [
        {
          "additionalProperties": {
            "anyOf": [
              {
                "$ref": "#/components/schemas/DigestSide"
              },
              {
                "type": "null"
              }
            ]
          },
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Sides"
    }
  },
  "required": [
    "run_id",
    "project_id"
  ],
  "title": "AICompareResponse",
  "type": "object"
}
```

## AIConfigRead

```json
{
  "description": "AI / LLM configuration returned to the client (no API keys).",
  "properties": {
    "ai_confidence_threshold": {
      "title": "Ai Confidence Threshold",
      "type": "integer"
    },
    "ai_confidence_threshold_source": {
      "default": "env_default",
      "title": "Ai Confidence Threshold Source",
      "type": "string"
    },
    "ai_offline_mode": {
      "title": "Ai Offline Mode",
      "type": "boolean"
    },
    "ai_offline_mode_env_pinned": {
      "default": false,
      "title": "Ai Offline Mode Env Pinned",
      "type": "boolean"
    },
    "ai_offline_mode_source": {
      "default": "not_offline",
      "title": "Ai Offline Mode Source",
      "type": "string"
    },
    "ai_timeout_seconds": {
      "title": "Ai Timeout Seconds",
      "type": "integer"
    },
    "ai_timeout_seconds_source": {
      "default": "env_default",
      "title": "Ai Timeout Seconds Source",
      "type": "string"
    },
    "analysis_mode": {
      "title": "Analysis Mode",
      "type": "string"
    },
    "analysis_mode_source": {
      "default": "env_default",
      "title": "Analysis Mode Source",
      "type": "string"
    },
    "anthropic_key_set": {
      "default": false,
      "title": "Anthropic Key Set",
      "type": "boolean"
    },
    "base_url": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Base Url"
    },
    "deep_investigation_enabled": {
      "title": "Deep Investigation Enabled",
      "type": "boolean"
    },
    "deep_investigation_enabled_source": {
      "default": "env_default",
      "title": "Deep Investigation Enabled Source",
      "type": "string"
    },
    "embedding_index": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/EmbeddingIndexRead"
        },
        {
          "type": "null"
        }
      ]
    },
    "embedding_model": {
      "title": "Embedding Model",
      "type": "string"
    },
    "embedding_provider": {
      "title": "Embedding Provider",
      "type": "string"
    },
    "finetune_enabled": {
      "title": "Finetune Enabled",
      "type": "boolean"
    },
    "finetune_enabled_source": {
      "default": "env_default",
      "title": "Finetune Enabled Source",
      "type": "string"
    },
    "google_key_set": {
      "title": "Google Key Set",
      "type": "boolean"
    },
    "knowledge_rag_enabled": {
      "default": false,
      "title": "Knowledge Rag Enabled",
      "type": "boolean"
    },
    "llm_max_tokens": {
      "title": "Llm Max Tokens",
      "type": "integer"
    },
    "llm_model": {
      "title": "Llm Model",
      "type": "string"
    },
    "llm_provider": {
      "title": "Llm Provider",
      "type": "string"
    },
    "llm_temperature": {
      "title": "Llm Temperature",
      "type": "number"
    },
    "ml_human_label_count": {
      "default": 0,
      "title": "Ml Human Label Count",
      "type": "integer"
    },
    "ml_human_label_floor": {
      "default": 50,
      "title": "Ml Human Label Floor",
      "type": "integer"
    },
    "ml_maturity": {
      "default": "not_trained",
      "title": "Ml Maturity",
      "type": "string"
    },
    "ml_min_training_samples": {
      "default": 200,
      "title": "Ml Min Training Samples",
      "type": "integer"
    },
    "ml_model_accuracy": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Ml Model Accuracy"
    },
    "ml_model_available": {
      "default": false,
      "title": "Ml Model Available",
      "type": "boolean"
    },
    "ml_training_sample_count": {
      "default": 0,
      "title": "Ml Training Sample Count",
      "type": "integer"
    },
    "openai_key_set": {
      "title": "Openai Key Set",
      "type": "boolean"
    },
    "openrouter_key_set": {
      "default": false,
      "title": "Openrouter Key Set",
      "type": "boolean"
    },
    "rag_faithfulness_evaluator": {
      "default": "ollama",
      "title": "Rag Faithfulness Evaluator",
      "type": "string"
    },
    "rag_faithfulness_evaluator_effective": {
      "default": "ollama",
      "title": "Rag Faithfulness Evaluator Effective",
      "type": "string"
    },
    "rag_faithfulness_evaluator_reason": {
      "default": "configured",
      "title": "Rag Faithfulness Evaluator Reason",
      "type": "string"
    },
    "rag_faithfulness_gate_enabled": {
      "default": false,
      "title": "Rag Faithfulness Gate Enabled",
      "type": "boolean"
    }
  },
  "required": [
    "llm_provider",
    "llm_model",
    "llm_temperature",
    "llm_max_tokens",
    "ai_offline_mode",
    "embedding_provider",
    "embedding_model",
    "ai_confidence_threshold",
    "ai_timeout_seconds",
    "deep_investigation_enabled",
    "finetune_enabled",
    "openai_key_set",
    "google_key_set",
    "analysis_mode"
  ],
  "title": "AIConfigRead",
  "type": "object"
}
```

## AIConfigUpdate

```json
{
  "description": "Payload for updating AI configuration. None = keep existing.",
  "properties": {
    "ai_confidence_threshold": {
      "anyOf": [
        {
          "maximum": 100.0,
          "minimum": 0.0,
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Ai Confidence Threshold"
    },
    "ai_offline_mode": {
      "anyOf": [
        {
          "type": "boolean"
        },
        {
          "type": "null"
        }
      ],
      "title": "Ai Offline Mode"
    },
    "ai_timeout_seconds": {
      "anyOf": [
        {
          "maximum": 1800.0,
          "minimum": 5.0,
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Ai Timeout Seconds"
    },
    "analysis_mode": {
      "anyOf": [
        {
          "pattern": "^(llm|ml|rules|auto)$",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Analysis Mode"
    },
    "anthropic_api_key": {
      "anyOf": [
        {
          "maxLength": 500,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Anthropic Api Key"
    },
    "base_url": {
      "anyOf": [
        {
          "maxLength": 500,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Base Url"
    },
    "deep_investigation_enabled": {
      "anyOf": [
        {
          "type": "boolean"
        },
        {
          "type": "null"
        }
      ],
      "title": "Deep Investigation Enabled"
    },
    "embedding_model": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Embedding Model"
    },
    "embedding_provider": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Embedding Provider"
    },
    "finetune_enabled": {
      "anyOf": [
        {
          "type": "boolean"
        },
        {
          "type": "null"
        }
      ],
      "title": "Finetune Enabled"
    },
    "google_api_key": {
      "anyOf": [
        {
          "maxLength": 500,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Google Api Key"
    },
    "knowledge_rag_enabled": {
      "anyOf": [
        {
          "type": "boolean"
        },
        {
          "type": "null"
        }
      ],
      "title": "Knowledge Rag Enabled"
    },
    "llm_max_tokens": {
      "anyOf": [
        {
          "maximum": 32768.0,
          "minimum": 256.0,
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Llm Max Tokens"
    },
    "llm_model": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Llm Model"
    },
    "llm_provider": {
      "anyOf": [
        {
          "pattern": "^(ollama|lmstudio|localai|vllm|openai|gemini|anthropic|openrouter)$",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Llm Provider"
    },
    "llm_temperature": {
      "anyOf": [
        {
          "maximum": 2.0,
          "minimum": 0.0,
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Llm Temperature"
    },
    "openai_api_key": {
      "anyOf": [
        {
          "maxLength": 500,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Openai Api Key"
    },
    "openrouter_api_key": {
      "anyOf": [
        {
          "maxLength": 500,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Openrouter Api Key"
    },
    "rag_faithfulness_evaluator": {
      "anyOf": [
        {
          "pattern": "^(ollama|ragas)$",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Rag Faithfulness Evaluator"
    }
  },
  "title": "AIConfigUpdate",
  "type": "object"
}
```

## AICoverageAnalysisRequest

```json
{
  "properties": {
    "project_id": {
      "format": "uuid",
      "title": "Project Id",
      "type": "string"
    },
    "requirements": {
      "minLength": 3,
      "title": "Requirements",
      "type": "string"
    }
  },
  "required": [
    "project_id",
    "requirements"
  ],
  "title": "AICoverageAnalysisRequest",
  "type": "object"
}
```

## AICoverageAnalysisResponse

```json
{
  "properties": {
    "coverage_score": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Coverage Score"
    },
    "covered_areas": {
      "anyOf": [
        {
          "items": {
            "type": "string"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Covered Areas"
    },
    "error": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Error"
    },
    "partial_coverage": {
      "anyOf": [
        {
          "items": {
            "additionalProperties": true,
            "type": "object"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Partial Coverage"
    },
    "recommended_new_tests": {
      "anyOf": [
        {
          "items": {
            "additionalProperties": true,
            "type": "object"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Recommended New Tests"
    },
    "risk_assessment": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Risk Assessment"
    },
    "summary": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Summary"
    },
    "uncovered_areas": {
      "anyOf": [
        {
          "items": {
            "type": "string"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Uncovered Areas"
    }
  },
  "title": "AICoverageAnalysisResponse",
  "type": "object"
}
```

## AIDigestDiffResponse

```json
{
  "description": "``from`` / ``to`` are emitted under those JSON keys (aliases).",
  "properties": {
    "ai_disclaimer": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Ai Disclaimer"
    },
    "ai_disclaimer_version": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Ai Disclaimer Version"
    },
    "diff": {
      "additionalProperties": true,
      "title": "Diff",
      "type": "object"
    },
    "from": {
      "$ref": "#/components/schemas/AIVersionRef"
    },
    "requires_human_review": {
      "default": true,
      "title": "Requires Human Review",
      "type": "boolean"
    },
    "review": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/AIHistoryReviewBlock"
        },
        {
          "type": "null"
        }
      ]
    },
    "run_id": {
      "title": "Run Id",
      "type": "string"
    },
    "to": {
      "$ref": "#/components/schemas/AIVersionRef"
    }
  },
  "required": [
    "run_id",
    "from",
    "to",
    "diff"
  ],
  "title": "AIDigestDiffResponse",
  "type": "object"
}
```

## AIEvalDatasetCreate

```json
{
  "properties": {
    "description": {
      "anyOf": [
        {
          "maxLength": 50000,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Description"
    },
    "items": {
      "items": {
        "additionalProperties": true,
        "type": "object"
      },
      "title": "Items",
      "type": "array"
    },
    "name": {
      "maxLength": 255,
      "minLength": 2,
      "title": "Name",
      "type": "string"
    },
    "task_type": {
      "pattern": "^(classification|root_cause|release_decision|duplicate_detection)$",
      "title": "Task Type",
      "type": "string"
    }
  },
  "required": [
    "name",
    "task_type"
  ],
  "title": "AIEvalDatasetCreate",
  "type": "object"
}
```

## AIEvalDatasetResponse

```json
{
  "properties": {
    "created_at": {
      "format": "date-time",
      "title": "Created At",
      "type": "string"
    },
    "created_by": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Created By"
    },
    "description": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Description"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "is_active": {
      "title": "Is Active",
      "type": "boolean"
    },
    "item_count": {
      "title": "Item Count",
      "type": "integer"
    },
    "name": {
      "title": "Name",
      "type": "string"
    },
    "task_type": {
      "title": "Task Type",
      "type": "string"
    },
    "updated_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Updated At"
    }
  },
  "required": [
    "id",
    "name",
    "task_type",
    "item_count",
    "is_active",
    "created_at"
  ],
  "title": "AIEvalDatasetResponse",
  "type": "object"
}
```

## AIEvalGateRunListResponse

```json
{
  "description": "Agent-stack history (``gate_type IS NULL``) and the G2 history (W16).",
  "properties": {
    "items": {
      "items": {
        "$ref": "#/components/schemas/AIEvalGateRunResponse"
      },
      "title": "Items",
      "type": "array"
    },
    "total": {
      "title": "Total",
      "type": "integer"
    }
  },
  "required": [
    "items",
    "total"
  ],
  "title": "AIEvalGateRunListResponse",
  "type": "object"
}
```

## AIEvalGateRunResponse

```json
{
  "properties": {
    "agent_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Agent Id"
    },
    "baseline_tier": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Baseline Tier"
    },
    "blocking_gates": {
      "items": {
        "additionalProperties": true,
        "type": "object"
      },
      "title": "Blocking Gates",
      "type": "array"
    },
    "candidate_tier": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Candidate Tier"
    },
    "change_id": {
      "title": "Change Id",
      "type": "string"
    },
    "evaluated_at": {
      "format": "date-time",
      "title": "Evaluated At",
      "type": "string"
    },
    "evaluated_by": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Evaluated By"
    },
    "gate_results": {
      "items": {
        "additionalProperties": true,
        "type": "object"
      },
      "title": "Gate Results",
      "type": "array"
    },
    "gate_type": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Gate Type"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "manifest": {
      "additionalProperties": true,
      "title": "Manifest",
      "type": "object"
    },
    "manifest_checksum_sha256": {
      "title": "Manifest Checksum Sha256",
      "type": "string"
    },
    "project_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Project Id"
    },
    "sample_count": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Sample Count"
    },
    "status": {
      "title": "Status",
      "type": "string"
    },
    "version_changes": {
      "items": {
        "additionalProperties": true,
        "type": "object"
      },
      "title": "Version Changes",
      "type": "array"
    }
  },
  "required": [
    "id",
    "change_id",
    "status",
    "manifest_checksum_sha256",
    "manifest",
    "gate_results",
    "blocking_gates",
    "version_changes",
    "evaluated_at"
  ],
  "title": "AIEvalGateRunResponse",
  "type": "object"
}
```

## AIEvalItemListResponse

```json
{
  "properties": {
    "counts": {
      "additionalProperties": {
        "type": "integer"
      },
      "title": "Counts",
      "type": "object"
    },
    "items": {
      "items": {
        "$ref": "#/components/schemas/AIEvalItemOutputRead"
      },
      "title": "Items",
      "type": "array"
    },
    "total": {
      "title": "Total",
      "type": "integer"
    }
  },
  "required": [
    "items",
    "total",
    "counts"
  ],
  "title": "AIEvalItemListResponse",
  "type": "object"
}
```

## AIEvalItemOutputRead

```json
{
  "properties": {
    "error_code": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Error Code"
    },
    "error_text": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Error Text"
    },
    "expected": {
      "additionalProperties": true,
      "title": "Expected",
      "type": "object"
    },
    "input": {
      "additionalProperties": true,
      "title": "Input",
      "type": "object"
    },
    "item_index": {
      "title": "Item Index",
      "type": "integer"
    },
    "item_key": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Item Key"
    },
    "latency_ms": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Latency Ms"
    },
    "mode_used": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Mode Used"
    },
    "output": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Output"
    },
    "score": {
      "additionalProperties": true,
      "title": "Score",
      "type": "object"
    },
    "status": {
      "title": "Status",
      "type": "string"
    },
    "tokens": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Tokens"
    }
  },
  "required": [
    "item_index",
    "status",
    "expected"
  ],
  "title": "AIEvalItemOutputRead",
  "type": "object"
}
```

## AIEvalManifestResponse

```json
{
  "properties": {
    "eval_manifest_checksum": {
      "title": "Eval Manifest Checksum",
      "type": "string"
    },
    "evaluated_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Evaluated At"
    },
    "gate_run_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Gate Run Id"
    },
    "manifest": {
      "additionalProperties": true,
      "title": "Manifest",
      "type": "object"
    },
    "source": {
      "title": "Source",
      "type": "string"
    },
    "status": {
      "title": "Status",
      "type": "string"
    }
  },
  "required": [
    "eval_manifest_checksum",
    "source",
    "status",
    "manifest"
  ],
  "title": "AIEvalManifestResponse",
  "type": "object"
}
```

## AIEvalRunListResponse

```json
{
  "description": "GET /ai-eval/runs (was a bare list; no client read it).",
  "properties": {
    "items": {
      "items": {
        "$ref": "#/components/schemas/AIEvalRunResponse"
      },
      "title": "Items",
      "type": "array"
    },
    "total": {
      "title": "Total",
      "type": "integer"
    }
  },
  "required": [
    "items",
    "total"
  ],
  "title": "AIEvalRunListResponse",
  "type": "object"
}
```

## AIEvalRunResponse

```json
{
  "properties": {
    "accuracy": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Accuracy"
    },
    "agreement_rate": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Agreement Rate"
    },
    "candidate": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/EvalCandidateResolved"
        },
        {
          "type": "null"
        }
      ]
    },
    "completed_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Completed At"
    },
    "correct_items": {
      "default": 0,
      "title": "Correct Items",
      "type": "integer"
    },
    "dataset_id": {
      "format": "uuid",
      "title": "Dataset Id",
      "type": "string"
    },
    "duration_ms": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Duration Ms"
    },
    "error_code": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Error Code"
    },
    "error_items": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "default": 0,
      "title": "Error Items"
    },
    "error_text": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Error Text"
    },
    "evaluated_at": {
      "format": "date-time",
      "title": "Evaluated At",
      "type": "string"
    },
    "f1_score": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "F1 Score"
    },
    "fallback_used": {
      "default": false,
      "title": "Fallback Used",
      "type": "boolean"
    },
    "heartbeat_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Heartbeat At"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "items_capped": {
      "anyOf": [
        {
          "type": "boolean"
        },
        {
          "type": "null"
        }
      ],
      "default": false,
      "title": "Items Capped"
    },
    "measured": {
      "default": true,
      "title": "Measured",
      "type": "boolean"
    },
    "metric_kind": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": "label_agreement",
      "title": "Metric Kind"
    },
    "metric_label": {
      "default": "",
      "title": "Metric Label",
      "type": "string"
    },
    "metrics": {
      "anyOf": [
        {
          "items": {
            "$ref": "#/components/schemas/EvalMetric"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Metrics"
    },
    "model_name": {
      "title": "Model Name",
      "type": "string"
    },
    "model_version_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Model Version Id"
    },
    "precision": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Precision"
    },
    "project_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Project Id"
    },
    "recall": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Recall"
    },
    "requested_by": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Requested By"
    },
    "scored_items": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "default": 0,
      "title": "Scored Items"
    },
    "started_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Started At"
    },
    "status": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": "completed",
      "title": "Status"
    },
    "task_type": {
      "title": "Task Type",
      "type": "string"
    },
    "total_items": {
      "default": 0,
      "title": "Total Items",
      "type": "integer"
    },
    "unscored_items": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "default": 0,
      "title": "Unscored Items"
    }
  },
  "required": [
    "id",
    "dataset_id",
    "model_name",
    "task_type",
    "evaluated_at"
  ],
  "title": "AIEvalRunResponse",
  "type": "object"
}
```

## AIGenerateStrategyRequest

```json
{
  "properties": {
    "project_context": {
      "minLength": 3,
      "title": "Project Context",
      "type": "string"
    },
    "project_id": {
      "format": "uuid",
      "title": "Project Id",
      "type": "string"
    },
    "strategy_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Strategy Name"
    }
  },
  "required": [
    "project_id",
    "project_context"
  ],
  "title": "AIGenerateStrategyRequest",
  "type": "object"
}
```

## AIGenerateTestCasesRequest

```json
{
  "properties": {
    "persist": {
      "default": false,
      "title": "Persist",
      "type": "boolean"
    },
    "project_id": {
      "format": "uuid",
      "title": "Project Id",
      "type": "string"
    },
    "requirements": {
      "minLength": 3,
      "title": "Requirements",
      "type": "string"
    }
  },
  "required": [
    "project_id",
    "requirements"
  ],
  "title": "AIGenerateTestCasesRequest",
  "type": "object"
}
```

## AIGenerateTestCasesResponse

```json
{
  "properties": {
    "coverage_summary": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Coverage Summary"
    },
    "created_ids": {
      "default": [],
      "items": {
        "type": "string"
      },
      "title": "Created Ids",
      "type": "array"
    },
    "gaps_noted": {
      "default": [],
      "items": {
        "type": "string"
      },
      "title": "Gaps Noted",
      "type": "array"
    },
    "test_cases": {
      "items": {
        "additionalProperties": true,
        "type": "object"
      },
      "title": "Test Cases",
      "type": "array"
    }
  },
  "required": [
    "test_cases"
  ],
  "title": "AIGenerateTestCasesResponse",
  "type": "object"
}
```

## AIHistoryListResponse

```json
{
  "properties": {
    "ai_disclaimer": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Ai Disclaimer"
    },
    "ai_disclaimer_version": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Ai Disclaimer Version"
    },
    "kind": {
      "title": "Kind",
      "type": "string"
    },
    "latest_version": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Latest Version"
    },
    "limit": {
      "title": "Limit",
      "type": "integer"
    },
    "live": {
      "additionalProperties": true,
      "title": "Live",
      "type": "object"
    },
    "requires_human_review": {
      "default": true,
      "title": "Requires Human Review",
      "type": "boolean"
    },
    "review": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/AIHistoryReviewBlock"
        },
        {
          "type": "null"
        }
      ]
    },
    "run_id": {
      "title": "Run Id",
      "type": "string"
    },
    "total": {
      "title": "Total",
      "type": "integer"
    },
    "truncated": {
      "title": "Truncated",
      "type": "boolean"
    },
    "versions": {
      "items": {
        "$ref": "#/components/schemas/AIVersionSummary"
      },
      "title": "Versions",
      "type": "array"
    }
  },
  "required": [
    "run_id",
    "kind",
    "total",
    "limit",
    "truncated"
  ],
  "title": "AIHistoryListResponse",
  "type": "object"
}
```

## AIHistoryReviewBlock

```json
{
  "description": "E8.3 review status of an AI artefact (state is a plain string here).\n\nNamed apart from ``schemas.ReviewBlock`` (whose ``state`` is a Literal) so\nthe OpenAPI schema keeps one component per name.",
  "properties": {
    "message": {
      "title": "Message",
      "type": "string"
    },
    "review_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Review Id"
    },
    "reviewed_at": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Reviewed At"
    },
    "state": {
      "title": "State",
      "type": "string"
    }
  },
  "required": [
    "state",
    "message"
  ],
  "title": "AIHistoryReviewBlock",
  "type": "object"
}
```

## AIModeRead

```json
{
  "description": "What every role's screens branch on: the analysis mode, and whether AI\nstays on this deployment.",
  "properties": {
    "ai_offline_mode": {
      "title": "Ai Offline Mode",
      "type": "boolean"
    },
    "analysis_mode": {
      "title": "Analysis Mode",
      "type": "string"
    },
    "deep_investigation_enabled": {
      "default": true,
      "title": "Deep Investigation Enabled",
      "type": "boolean"
    }
  },
  "required": [
    "analysis_mode",
    "ai_offline_mode"
  ],
  "title": "AIModeRead",
  "type": "object"
}
```

## AIModelStatusRead

```json
{
  "description": "Live model presence + fallback-chain state (US-13.2).\n\n``ollama_reachable=False`` and \"model missing\" are deliberately\nseparate signals: an unreachable daemon is a connectivity problem,\nan empty/incomplete model list on a reachable daemon is a model-pack\nimport problem. The UI must not collapse them.",
  "properties": {
    "analysis_mode": {
      "title": "Analysis Mode",
      "type": "string"
    },
    "checked_at": {
      "title": "Checked At",
      "type": "string"
    },
    "fallback_chain": {
      "items": {
        "$ref": "#/components/schemas/FallbackChainEntry"
      },
      "title": "Fallback Chain",
      "type": "array"
    },
    "installed_models": {
      "items": {
        "type": "string"
      },
      "title": "Installed Models",
      "type": "array"
    },
    "llm_provider": {
      "title": "Llm Provider",
      "type": "string"
    },
    "offline_mode": {
      "title": "Offline Mode",
      "type": "boolean"
    },
    "ollama_base_url": {
      "title": "Ollama Base Url",
      "type": "string"
    },
    "ollama_error": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Ollama Error"
    },
    "ollama_reachable": {
      "title": "Ollama Reachable",
      "type": "boolean"
    },
    "required": {
      "items": {
        "$ref": "#/components/schemas/RequiredModel"
      },
      "title": "Required",
      "type": "array"
    }
  },
  "required": [
    "ollama_reachable",
    "ollama_base_url",
    "installed_models",
    "required",
    "fallback_chain",
    "offline_mode",
    "llm_provider",
    "analysis_mode",
    "checked_at"
  ],
  "title": "AIModelStatusRead",
  "type": "object"
}
```

## AIOptimizePlanRequest

```json
{
  "properties": {
    "constraints": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Constraints"
    },
    "plan_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Plan Name"
    },
    "project_id": {
      "format": "uuid",
      "title": "Project Id",
      "type": "string"
    }
  },
  "required": [
    "project_id"
  ],
  "title": "AIOptimizePlanRequest",
  "type": "object"
}
```

## AIPreviousRunResponse

```json
{
  "properties": {
    "baseline": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/RunBrief"
        },
        {
          "type": "null"
        }
      ]
    },
    "current": {
      "$ref": "#/components/schemas/RunBrief"
    },
    "message": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Message"
    },
    "reason": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Reason"
    },
    "rule": {
      "additionalProperties": true,
      "title": "Rule",
      "type": "object"
    },
    "run_id": {
      "title": "Run Id",
      "type": "string"
    }
  },
  "required": [
    "run_id",
    "rule",
    "current"
  ],
  "title": "AIPreviousRunResponse",
  "type": "object"
}
```

## AIQualityDashboardResponse

```json
{
  "description": "Combined dashboard data for AI quality metrics.",
  "properties": {
    "agreement": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Agreement"
    },
    "drift": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Drift"
    },
    "eval_provenance": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/EvalProvenanceHealthResponse"
        },
        {
          "type": "null"
        }
      ]
    },
    "feedback_summary": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Feedback Summary"
    },
    "label_health": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Label Health"
    },
    "model_versions": {
      "default": [],
      "items": {
        "additionalProperties": true,
        "type": "object"
      },
      "title": "Model Versions",
      "type": "array"
    },
    "recent_eval_runs": {
      "default": [],
      "items": {
        "$ref": "#/components/schemas/AIEvalRunResponse"
      },
      "title": "Recent Eval Runs",
      "type": "array"
    }
  },
  "title": "AIQualityDashboardResponse",
  "type": "object"
}
```

## AIReviewTestCaseResponse

```json
{
  "properties": {
    "best_practices_violations": {
      "anyOf": [
        {
          "items": {
            "type": "string"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Best Practices Violations"
    },
    "coverage_gaps": {
      "anyOf": [
        {
          "items": {
            "type": "string"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Coverage Gaps"
    },
    "error": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Error"
    },
    "grade": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Grade"
    },
    "issues": {
      "anyOf": [
        {
          "items": {
            "additionalProperties": true,
            "type": "object"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Issues"
    },
    "positive_aspects": {
      "anyOf": [
        {
          "items": {
            "type": "string"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Positive Aspects"
    },
    "quality_score": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Quality Score"
    },
    "score_breakdown": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Score Breakdown"
    },
    "suggestions": {
      "anyOf": [
        {
          "items": {
            "additionalProperties": true,
            "type": "object"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Suggestions"
    },
    "summary": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Summary"
    }
  },
  "title": "AIReviewTestCaseResponse",
  "type": "object"
}
```

## AITaskEnqueueResponse

```json
{
  "properties": {
    "status": {
      "default": "queued",
      "title": "Status",
      "type": "string"
    },
    "task_id": {
      "title": "Task Id",
      "type": "string"
    }
  },
  "required": [
    "task_id"
  ],
  "title": "AITaskEnqueueResponse",
  "type": "object"
}
```

## AITaskStatusResponse

```json
{
  "properties": {
    "error": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Error"
    },
    "result": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Result"
    },
    "status": {
      "title": "Status",
      "type": "string"
    },
    "task_id": {
      "title": "Task Id",
      "type": "string"
    }
  },
  "required": [
    "task_id",
    "status"
  ],
  "title": "AITaskStatusResponse",
  "type": "object"
}
```

## AITestHistoryResponse

```json
{
  "properties": {
    "current": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/AITestVersion"
        },
        {
          "type": "null"
        }
      ]
    },
    "run_id": {
      "title": "Run Id",
      "type": "string"
    },
    "test_fingerprint": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Test Fingerprint"
    },
    "test_id": {
      "title": "Test Id",
      "type": "string"
    },
    "test_name": {
      "title": "Test Name",
      "type": "string"
    },
    "total": {
      "default": 0,
      "title": "Total",
      "type": "integer"
    },
    "versions": {
      "items": {
        "$ref": "#/components/schemas/AITestVersion"
      },
      "title": "Versions",
      "type": "array"
    }
  },
  "required": [
    "run_id",
    "test_id",
    "test_name"
  ],
  "title": "AITestHistoryResponse",
  "type": "object"
}
```

## AITestVersion

```json
{
  "properties": {
    "actor": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/AIActor"
        },
        {
          "type": "null"
        }
      ]
    },
    "analysis_mode": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Analysis Mode"
    },
    "confidence_score": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Confidence Score"
    },
    "content_sha256": {
      "title": "Content Sha256",
      "type": "string"
    },
    "created_at": {
      "format": "date-time",
      "title": "Created At",
      "type": "string"
    },
    "failure_category": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Failure Category"
    },
    "is_flaky": {
      "default": false,
      "title": "Is Flaky",
      "type": "boolean"
    },
    "llm_model": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Llm Model"
    },
    "llm_provider": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Llm Provider"
    },
    "pipeline_run_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Pipeline Run Id"
    },
    "requires_human_review": {
      "default": false,
      "title": "Requires Human Review",
      "type": "boolean"
    },
    "root_cause_excerpt": {
      "default": "",
      "title": "Root Cause Excerpt",
      "type": "string"
    },
    "trigger": {
      "title": "Trigger",
      "type": "string"
    },
    "version": {
      "title": "Version",
      "type": "integer"
    },
    "version_id": {
      "title": "Version Id",
      "type": "string"
    }
  },
  "required": [
    "version_id",
    "version",
    "trigger",
    "content_sha256",
    "created_at"
  ],
  "title": "AITestVersion",
  "type": "object"
}
```

## AIVersionRef

```json
{
  "description": "One side of a diff or a compare.",
  "properties": {
    "analysis_mode": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Analysis Mode"
    },
    "build_number": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Build Number"
    },
    "content_sha256": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Content Sha256"
    },
    "generated_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Generated At"
    },
    "kind": {
      "default": "analysis_digest",
      "title": "Kind",
      "type": "string"
    },
    "llm_model": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Llm Model"
    },
    "pipeline_run_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Pipeline Run Id"
    },
    "ref": {
      "title": "Ref",
      "type": "string"
    },
    "run_id": {
      "title": "Run Id",
      "type": "string"
    },
    "source": {
      "title": "Source",
      "type": "string"
    },
    "version": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Version"
    }
  },
  "required": [
    "ref",
    "source",
    "run_id"
  ],
  "title": "AIVersionRef",
  "type": "object"
}
```

## AIVersionResponse

```json
{
  "properties": {
    "ai_disclaimer": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Ai Disclaimer"
    },
    "ai_disclaimer_version": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Ai Disclaimer Version"
    },
    "analysis_mode": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Analysis Mode"
    },
    "categories": {
      "anyOf": [
        {
          "additionalProperties": {
            "type": "integer"
          },
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Categories"
    },
    "cluster_count": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Cluster Count"
    },
    "content_sha256": {
      "title": "Content Sha256",
      "type": "string"
    },
    "created_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Created At"
    },
    "decision_report_ref": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/DecisionReportRef"
        },
        {
          "type": "null"
        }
      ]
    },
    "deterministic": {
      "default": false,
      "title": "Deterministic",
      "type": "boolean"
    },
    "failing_tests": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Failing Tests"
    },
    "fallback_used": {
      "default": false,
      "title": "Fallback Used",
      "type": "boolean"
    },
    "generated_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Generated At"
    },
    "headline": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Headline"
    },
    "is_latest": {
      "default": false,
      "title": "Is Latest",
      "type": "boolean"
    },
    "kind": {
      "title": "Kind",
      "type": "string"
    },
    "llm_model": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Llm Model"
    },
    "llm_provider": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Llm Provider"
    },
    "payload": {
      "additionalProperties": true,
      "title": "Payload",
      "type": "object"
    },
    "pipeline_run_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Pipeline Run Id"
    },
    "requires_human_review": {
      "default": true,
      "title": "Requires Human Review",
      "type": "boolean"
    },
    "review": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/AIHistoryReviewBlock"
        },
        {
          "type": "null"
        }
      ]
    },
    "risk_score": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Risk Score"
    },
    "summary_version": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Summary Version"
    },
    "trigger": {
      "title": "Trigger",
      "type": "string"
    },
    "verdict": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Verdict"
    },
    "version": {
      "title": "Version",
      "type": "integer"
    },
    "version_id": {
      "title": "Version Id",
      "type": "string"
    }
  },
  "required": [
    "version_id",
    "kind",
    "version",
    "trigger",
    "content_sha256",
    "payload"
  ],
  "title": "AIVersionResponse",
  "type": "object"
}
```

## AIVersionSummary

```json
{
  "description": "One row of a run's AI history list, and the head of a single version.",
  "properties": {
    "analysis_mode": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Analysis Mode"
    },
    "categories": {
      "anyOf": [
        {
          "additionalProperties": {
            "type": "integer"
          },
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Categories"
    },
    "cluster_count": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Cluster Count"
    },
    "content_sha256": {
      "title": "Content Sha256",
      "type": "string"
    },
    "created_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Created At"
    },
    "decision_report_ref": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/DecisionReportRef"
        },
        {
          "type": "null"
        }
      ]
    },
    "deterministic": {
      "default": false,
      "title": "Deterministic",
      "type": "boolean"
    },
    "failing_tests": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Failing Tests"
    },
    "fallback_used": {
      "default": false,
      "title": "Fallback Used",
      "type": "boolean"
    },
    "generated_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Generated At"
    },
    "headline": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Headline"
    },
    "is_latest": {
      "default": false,
      "title": "Is Latest",
      "type": "boolean"
    },
    "kind": {
      "title": "Kind",
      "type": "string"
    },
    "llm_model": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Llm Model"
    },
    "llm_provider": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Llm Provider"
    },
    "pipeline_run_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Pipeline Run Id"
    },
    "review": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/AIHistoryReviewBlock"
        },
        {
          "type": "null"
        }
      ]
    },
    "risk_score": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Risk Score"
    },
    "summary_version": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Summary Version"
    },
    "trigger": {
      "title": "Trigger",
      "type": "string"
    },
    "verdict": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Verdict"
    },
    "version": {
      "title": "Version",
      "type": "integer"
    },
    "version_id": {
      "title": "Version Id",
      "type": "string"
    }
  },
  "required": [
    "version_id",
    "kind",
    "version",
    "trigger",
    "content_sha256"
  ],
  "title": "AIVersionSummary",
  "type": "object"
}
```

## AcceptCaseRequest

```json
{
  "properties": {
    "edits": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/RagCaseAcceptEdits"
        },
        {
          "type": "null"
        }
      ]
    }
  },
  "title": "AcceptCaseRequest",
  "type": "object"
}
```

## AcceptInvitationRequest

```json
{
  "properties": {
    "full_name": {
      "anyOf": [
        {
          "maxLength": 255,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Full Name"
    },
    "password": {
      "maxLength": 128,
      "minLength": 8,
      "title": "Password",
      "type": "string"
    },
    "token": {
      "maxLength": 256,
      "minLength": 1,
      "title": "Token",
      "type": "string"
    },
    "username": {
      "maxLength": 50,
      "minLength": 3,
      "title": "Username",
      "type": "string"
    }
  },
  "required": [
    "token",
    "username",
    "password"
  ],
  "title": "AcceptInvitationRequest",
  "type": "object"
}
```

## AcceptReviewRequest

```json
{
  "properties": {
    "notes": {
      "anyOf": [
        {
          "maxLength": 4000,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Notes"
    }
  },
  "title": "AcceptReviewRequest",
  "type": "object"
}
```

## ActionTransitionRequest

```json
{
  "properties": {
    "error_code": {
      "anyOf": [
        {
          "maxLength": 100,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Error Code"
    },
    "notes": {
      "anyOf": [
        {
          "maxLength": 2000,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Notes"
    },
    "reason_code": {
      "anyOf": [
        {
          "maxLength": 40,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Reason Code"
    },
    "status": {
      "enum": [
        "approved",
        "rejected",
        "rolled_back"
      ],
      "title": "Status",
      "type": "string"
    }
  },
  "required": [
    "status"
  ],
  "title": "ActionTransitionRequest",
  "type": "object"
}
```

## ActiveSessionsResponse

```json
{
  "properties": {
    "count": {
      "title": "Count",
      "type": "integer"
    },
    "sessions": {
      "items": {
        "$ref": "#/components/schemas/LiveSessionState"
      },
      "title": "Sessions",
      "type": "array"
    }
  },
  "required": [
    "sessions",
    "count"
  ],
  "title": "ActiveSessionsResponse",
  "type": "object"
}
```

## AddProjectMemberRequest

```json
{
  "properties": {
    "role": {
      "$ref": "#/components/schemas/UserRole",
      "default": "QA_ENGINEER"
    },
    "user_id": {
      "format": "uuid",
      "title": "User Id",
      "type": "string"
    }
  },
  "required": [
    "user_id"
  ],
  "title": "AddProjectMemberRequest",
  "type": "object"
}
```

## AdminCreateUserRequest

```json
{
  "properties": {
    "email": {
      "format": "email",
      "title": "Email",
      "type": "string"
    },
    "full_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Full Name"
    },
    "role": {
      "$ref": "#/components/schemas/UserRole",
      "default": "QA_ENGINEER"
    },
    "username": {
      "maxLength": 50,
      "minLength": 3,
      "title": "Username",
      "type": "string"
    }
  },
  "required": [
    "email",
    "username"
  ],
  "title": "AdminCreateUserRequest",
  "type": "object"
}
```

## AdminCreateUserResponse

```json
{
  "properties": {
    "created_at": {
      "format": "date-time",
      "title": "Created At",
      "type": "string"
    },
    "email": {
      "title": "Email",
      "type": "string"
    },
    "full_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Full Name"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "is_active": {
      "title": "Is Active",
      "type": "boolean"
    },
    "role": {
      "$ref": "#/components/schemas/UserRole"
    },
    "temp_password": {
      "title": "Temp Password",
      "type": "string"
    },
    "username": {
      "title": "Username",
      "type": "string"
    }
  },
  "required": [
    "id",
    "email",
    "username",
    "role",
    "is_active",
    "created_at",
    "temp_password"
  ],
  "title": "AdminCreateUserResponse",
  "type": "object"
}
```

## AgentActionListResponse

```json
{
  "properties": {
    "count": {
      "title": "Count",
      "type": "integer"
    },
    "items": {
      "items": {
        "$ref": "#/components/schemas/AgentActionRead"
      },
      "title": "Items",
      "type": "array"
    },
    "total": {
      "title": "Total",
      "type": "integer"
    }
  },
  "required": [
    "items",
    "count",
    "total"
  ],
  "title": "AgentActionListResponse",
  "type": "object"
}
```

## AgentActionRead

```json
{
  "properties": {
    "action_type": {
      "title": "Action Type",
      "type": "string"
    },
    "approvable": {
      "default": false,
      "title": "Approvable",
      "type": "boolean"
    },
    "approval_required": {
      "title": "Approval Required",
      "type": "boolean"
    },
    "approve_blocked_reason": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Approve Blocked Reason"
    },
    "approved_at": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Approved At"
    },
    "approved_by": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Approved By"
    },
    "approved_by_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Approved By Name"
    },
    "canonical_type": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Canonical Type"
    },
    "created_at": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Created At"
    },
    "error_code": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Error Code"
    },
    "execution_completed_at": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Execution Completed At"
    },
    "execution_started_at": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Execution Started At"
    },
    "id": {
      "title": "Id",
      "type": "string"
    },
    "idempotency_key": {
      "title": "Idempotency Key",
      "type": "string"
    },
    "outcome": {
      "default": "unknown",
      "title": "Outcome",
      "type": "string"
    },
    "outcome_text": {
      "default": "",
      "title": "Outcome Text",
      "type": "string"
    },
    "pipeline_run_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Pipeline Run Id"
    },
    "pipeline_workflow_type": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Pipeline Workflow Type"
    },
    "project_id": {
      "title": "Project Id",
      "type": "string"
    },
    "proposing_agent_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Proposing Agent Id"
    },
    "proposing_agent_label": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Proposing Agent Label"
    },
    "proposing_review_state": {
      "default": "none",
      "title": "Proposing Review State",
      "type": "string"
    },
    "rationale": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Rationale"
    },
    "rejection_reason_code": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Rejection Reason Code"
    },
    "request_payload": {
      "additionalProperties": true,
      "title": "Request Payload",
      "type": "object"
    },
    "request_sha256": {
      "title": "Request Sha256",
      "type": "string"
    },
    "result_link": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/AgentActionResultLink"
        },
        {
          "type": "null"
        }
      ]
    },
    "result_payload": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Result Payload"
    },
    "reversible": {
      "default": false,
      "title": "Reversible",
      "type": "boolean"
    },
    "review_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Review Id"
    },
    "risk": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Risk"
    },
    "rollback_payload": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Rollback Payload"
    },
    "side_effect": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Side Effect"
    },
    "status": {
      "title": "Status",
      "type": "string"
    },
    "target": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/AgentActionTarget"
        },
        {
          "type": "null"
        }
      ]
    },
    "target_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Target Id"
    },
    "target_type": {
      "title": "Target Type",
      "type": "string"
    },
    "test_run_branch": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Test Run Branch"
    },
    "test_run_build_number": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Test Run Build Number"
    },
    "test_run_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Test Run Id"
    },
    "title": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Title"
    }
  },
  "required": [
    "id",
    "project_id",
    "action_type",
    "target_type",
    "status",
    "approval_required",
    "idempotency_key",
    "request_sha256",
    "request_payload"
  ],
  "title": "AgentActionRead",
  "type": "object"
}
```

## AgentActionResultLink

```json
{
  "properties": {
    "href": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Href"
    },
    "id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Id"
    },
    "kind": {
      "title": "Kind",
      "type": "string"
    },
    "label": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Label"
    }
  },
  "required": [
    "kind"
  ],
  "title": "AgentActionResultLink",
  "type": "object"
}
```

## AgentActionTarget

```json
{
  "properties": {
    "href": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Href"
    },
    "id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Id"
    },
    "kind": {
      "title": "Kind",
      "type": "string"
    },
    "label": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Label"
    }
  },
  "required": [
    "kind"
  ],
  "title": "AgentActionTarget",
  "type": "object"
}
```

## AgentCatalogDetail

```json
{
  "properties": {
    "agent_id": {
      "title": "Agent Id",
      "type": "string"
    },
    "concurrency_class": {
      "title": "Concurrency Class",
      "type": "string"
    },
    "default_tier": {
      "enum": [
        "deterministic",
        "slm",
        "llm"
      ],
      "title": "Default Tier",
      "type": "string"
    },
    "dependencies": {
      "items": {
        "type": "string"
      },
      "title": "Dependencies",
      "type": "array"
    },
    "escalation": {
      "items": {
        "enum": [
          "validation_failure",
          "low_confidence",
          "not_enough_evidence",
          "contradictions",
          "multi_artifact_evidence"
        ],
        "type": "string"
      },
      "title": "Escalation",
      "type": "array"
    },
    "execution": {
      "title": "Execution",
      "type": "string"
    },
    "expected_cost_usd": {
      "title": "Expected Cost Usd",
      "type": "number"
    },
    "expected_latency_ms": {
      "title": "Expected Latency Ms",
      "type": "integer"
    },
    "fallback": {
      "title": "Fallback",
      "type": "string"
    },
    "inline_input_json_schema": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Inline Input Json Schema"
    },
    "inline_input_supported": {
      "default": false,
      "description": "Accepts inline_input on POST /api/v1/agents/{agent_id}/invoke (a pasted input anchored to a stored test run of the project); the detail publishes its schema as inline_input_json_schema.",
      "title": "Inline Input Supported",
      "type": "boolean"
    },
    "input_json_schema": {
      "additionalProperties": true,
      "title": "Input Json Schema",
      "type": "object"
    },
    "input_schema": {
      "title": "Input Schema",
      "type": "string"
    },
    "input_schema_resolved": {
      "description": "False when the input schema is a label with no model yet; the wrapper then accepts only a SubjectRef.",
      "title": "Input Schema Resolved",
      "type": "boolean"
    },
    "input_wrapper": {
      "title": "Input Wrapper",
      "type": "string"
    },
    "invokable": {
      "description": "Can be invoked independently; false means the capability requires a surrounding workflow.",
      "title": "Invokable",
      "type": "boolean"
    },
    "max_retries": {
      "title": "Max Retries",
      "type": "integer"
    },
    "output_json_schema": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Output Json Schema"
    },
    "output_schema": {
      "title": "Output Schema",
      "type": "string"
    },
    "output_schema_resolved": {
      "title": "Output Schema Resolved",
      "type": "boolean"
    },
    "permission": {
      "title": "Permission",
      "type": "string"
    },
    "produces_report": {
      "description": "Its output is a report contract, so a run of it needs human review (E8).",
      "title": "Produces Report",
      "type": "boolean"
    },
    "required_evidence": {
      "items": {
        "type": "string"
      },
      "title": "Required Evidence",
      "type": "array"
    },
    "stage_name": {
      "title": "Stage Name",
      "type": "string"
    },
    "sync_eligible": {
      "description": "May be invoked synchronously: deterministic and expected to finish in 5 s or less.",
      "title": "Sync Eligible",
      "type": "boolean"
    },
    "timeout_seconds": {
      "title": "Timeout Seconds",
      "type": "integer"
    },
    "version": {
      "title": "Version",
      "type": "integer"
    }
  },
  "required": [
    "agent_id",
    "version",
    "stage_name",
    "permission",
    "execution",
    "default_tier",
    "escalation",
    "sync_eligible",
    "invokable",
    "produces_report",
    "input_schema",
    "output_schema",
    "input_schema_resolved",
    "output_schema_resolved",
    "dependencies",
    "required_evidence",
    "expected_latency_ms",
    "expected_cost_usd",
    "timeout_seconds",
    "max_retries",
    "fallback",
    "concurrency_class",
    "input_wrapper",
    "input_json_schema"
  ],
  "title": "AgentCatalogDetail",
  "type": "object"
}
```

## AgentCatalogEntry

```json
{
  "properties": {
    "agent_id": {
      "title": "Agent Id",
      "type": "string"
    },
    "concurrency_class": {
      "title": "Concurrency Class",
      "type": "string"
    },
    "default_tier": {
      "enum": [
        "deterministic",
        "slm",
        "llm"
      ],
      "title": "Default Tier",
      "type": "string"
    },
    "dependencies": {
      "items": {
        "type": "string"
      },
      "title": "Dependencies",
      "type": "array"
    },
    "escalation": {
      "items": {
        "enum": [
          "validation_failure",
          "low_confidence",
          "not_enough_evidence",
          "contradictions",
          "multi_artifact_evidence"
        ],
        "type": "string"
      },
      "title": "Escalation",
      "type": "array"
    },
    "execution": {
      "title": "Execution",
      "type": "string"
    },
    "expected_cost_usd": {
      "title": "Expected Cost Usd",
      "type": "number"
    },
    "expected_latency_ms": {
      "title": "Expected Latency Ms",
      "type": "integer"
    },
    "fallback": {
      "title": "Fallback",
      "type": "string"
    },
    "inline_input_supported": {
      "default": false,
      "description": "Accepts inline_input on POST /api/v1/agents/{agent_id}/invoke (a pasted input anchored to a stored test run of the project); the detail publishes its schema as inline_input_json_schema.",
      "title": "Inline Input Supported",
      "type": "boolean"
    },
    "input_schema": {
      "title": "Input Schema",
      "type": "string"
    },
    "input_schema_resolved": {
      "description": "False when the input schema is a label with no model yet; the wrapper then accepts only a SubjectRef.",
      "title": "Input Schema Resolved",
      "type": "boolean"
    },
    "invokable": {
      "description": "Can be invoked independently; false means the capability requires a surrounding workflow.",
      "title": "Invokable",
      "type": "boolean"
    },
    "max_retries": {
      "title": "Max Retries",
      "type": "integer"
    },
    "output_schema": {
      "title": "Output Schema",
      "type": "string"
    },
    "output_schema_resolved": {
      "title": "Output Schema Resolved",
      "type": "boolean"
    },
    "permission": {
      "title": "Permission",
      "type": "string"
    },
    "produces_report": {
      "description": "Its output is a report contract, so a run of it needs human review (E8).",
      "title": "Produces Report",
      "type": "boolean"
    },
    "required_evidence": {
      "items": {
        "type": "string"
      },
      "title": "Required Evidence",
      "type": "array"
    },
    "stage_name": {
      "title": "Stage Name",
      "type": "string"
    },
    "sync_eligible": {
      "description": "May be invoked synchronously: deterministic and expected to finish in 5 s or less.",
      "title": "Sync Eligible",
      "type": "boolean"
    },
    "timeout_seconds": {
      "title": "Timeout Seconds",
      "type": "integer"
    },
    "version": {
      "title": "Version",
      "type": "integer"
    }
  },
  "required": [
    "agent_id",
    "version",
    "stage_name",
    "permission",
    "execution",
    "default_tier",
    "escalation",
    "sync_eligible",
    "invokable",
    "produces_report",
    "input_schema",
    "output_schema",
    "input_schema_resolved",
    "output_schema_resolved",
    "dependencies",
    "required_evidence",
    "expected_latency_ms",
    "expected_cost_usd",
    "timeout_seconds",
    "max_retries",
    "fallback",
    "concurrency_class"
  ],
  "title": "AgentCatalogEntry",
  "type": "object"
}
```

## AgentConfigExtensions

```json
{
  "additionalProperties": false,
  "description": "Typed homes for pre-registry contracts migrated from agent_policies.",
  "properties": {
    "fixer": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/FixerConfigExtension"
        },
        {
          "type": "null"
        }
      ]
    },
    "investigator": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/InvestigatorConfigExtension"
        },
        {
          "type": "null"
        }
      ]
    }
  },
  "title": "AgentConfigExtensions",
  "type": "object"
}
```

## AgentConfigPatch

```json
{
  "additionalProperties": false,
  "description": "What one request may override. Every field may only tighten the project layer.\n\n``mode``, ``temperature``, ``max_tokens``, ``escalation``, most thresholds and\nany provider or endpoint are absent on purpose: ``extra=\"forbid\"`` rejects them.",
  "properties": {
    "budget": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/_BudgetPatch"
        },
        {
          "type": "null"
        }
      ]
    },
    "model": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/_TierPatch"
        },
        {
          "type": "null"
        }
      ]
    },
    "retry": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/_RetryPatch"
        },
        {
          "type": "null"
        }
      ]
    },
    "review": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/_ReviewPatch"
        },
        {
          "type": "null"
        }
      ]
    },
    "thresholds": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/_ThresholdsPatch"
        },
        {
          "type": "null"
        }
      ]
    },
    "timeout_seconds": {
      "anyOf": [
        {
          "minimum": 1.0,
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Timeout Seconds"
    },
    "tools": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/_ToolsPatch"
        },
        {
          "type": "null"
        }
      ]
    }
  },
  "title": "AgentConfigPatch",
  "type": "object"
}
```

## AgentConfigV1

```json
{
  "additionalProperties": false,
  "description": "A project's configuration of one agent (section 4.2).",
  "properties": {
    "agent_id": {
      "title": "Agent Id",
      "type": "string"
    },
    "budget": {
      "$ref": "#/components/schemas/BudgetConfig"
    },
    "enabled": {
      "default": true,
      "title": "Enabled",
      "type": "boolean"
    },
    "extensions": {
      "$ref": "#/components/schemas/AgentConfigExtensions"
    },
    "mode": {
      "default": "shadow",
      "enum": [
        "shadow",
        "suggest",
        "act"
      ],
      "title": "Mode",
      "type": "string"
    },
    "model": {
      "$ref": "#/components/schemas/ModelConfig"
    },
    "override_policy": {
      "$ref": "#/components/schemas/OverridePolicyConfig"
    },
    "retry": {
      "$ref": "#/components/schemas/RetryConfig"
    },
    "review": {
      "$ref": "#/components/schemas/ReviewConfig"
    },
    "shadow": {
      "$ref": "#/components/schemas/ShadowConfig"
    },
    "thresholds": {
      "$ref": "#/components/schemas/ThresholdsConfig"
    },
    "timeout_seconds": {
      "default": 60,
      "minimum": 1.0,
      "title": "Timeout Seconds",
      "type": "integer"
    },
    "tools": {
      "$ref": "#/components/schemas/ToolsConfig"
    }
  },
  "required": [
    "agent_id"
  ],
  "title": "AgentConfigV1",
  "type": "object"
}
```

## AgentEvidenceV1

```json
{
  "additionalProperties": false,
  "properties": {
    "classification": {
      "default": "internal",
      "enum": [
        "internal",
        "restricted"
      ],
      "title": "Classification",
      "type": "string"
    },
    "description": {
      "default": "",
      "maxLength": 2000,
      "title": "Description",
      "type": "string"
    },
    "evidence_id": {
      "maxLength": 160,
      "minLength": 1,
      "title": "Evidence Id",
      "type": "string"
    },
    "kind": {
      "maxLength": 80,
      "minLength": 1,
      "title": "Kind",
      "type": "string"
    },
    "label": {
      "maxLength": 200,
      "minLength": 1,
      "title": "Label",
      "type": "string"
    },
    "schema_version": {
      "const": 1,
      "default": 1,
      "title": "Schema Version",
      "type": "integer"
    },
    "task_id": {
      "maxLength": 240,
      "minLength": 1,
      "title": "Task Id",
      "type": "string"
    }
  },
  "required": [
    "evidence_id",
    "task_id",
    "kind",
    "label"
  ],
  "title": "AgentEvidenceV1",
  "type": "object"
}
```

## AgentFindingV1

```json
{
  "additionalProperties": false,
  "properties": {
    "classification": {
      "enum": [
        "fact",
        "inference",
        "unknown",
        "recommendation"
      ],
      "title": "Classification",
      "type": "string"
    },
    "confidence": {
      "anyOf": [
        {
          "maximum": 1.0,
          "minimum": 0.0,
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Confidence"
    },
    "confidence_basis": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Confidence Basis"
    },
    "counter_evidence_ids": {
      "default": [],
      "items": {
        "type": "string"
      },
      "title": "Counter Evidence Ids",
      "type": "array"
    },
    "evidence_ids": {
      "default": [],
      "items": {
        "type": "string"
      },
      "title": "Evidence Ids",
      "type": "array"
    },
    "finding_id": {
      "title": "Finding Id",
      "type": "string"
    },
    "kind": {
      "title": "Kind",
      "type": "string"
    },
    "schema_version": {
      "const": 1,
      "default": 1,
      "title": "Schema Version",
      "type": "integer"
    },
    "statement": {
      "title": "Statement",
      "type": "string"
    },
    "task_id": {
      "title": "Task Id",
      "type": "string"
    }
  },
  "required": [
    "finding_id",
    "task_id",
    "kind",
    "statement",
    "classification"
  ],
  "title": "AgentFindingV1",
  "type": "object"
}
```

## AgentInvocationResponse

```json
{
  "properties": {
    "agent_id": {
      "title": "Agent Id",
      "type": "string"
    },
    "attempt": {
      "title": "Attempt",
      "type": "integer"
    },
    "config_snapshot": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "description": "Credential-free resolved configuration accepted for this invocation.",
      "title": "Config Snapshot"
    },
    "created_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Created At"
    },
    "error": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Error"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "input": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "description": "The inline input as stored: secrets and personal data redacted before storage. None for stored_run.",
      "title": "Input"
    },
    "input_kind": {
      "default": "stored_run",
      "enum": [
        "stored_run",
        "inline"
      ],
      "title": "Input Kind",
      "type": "string"
    },
    "input_sha256": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "description": "sha256 of the redacted stored inline input; None for a stored-run invocation.",
      "title": "Input Sha256"
    },
    "links": {
      "additionalProperties": {
        "type": "string"
      },
      "title": "Links",
      "type": "object"
    },
    "max_attempts": {
      "title": "Max Attempts",
      "type": "integer"
    },
    "mode": {
      "enum": [
        "sync",
        "async"
      ],
      "title": "Mode",
      "type": "string"
    },
    "next_retry_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Next Retry At"
    },
    "output": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "description": "The invoked agent's stored stage output, once that stage has completed.",
      "title": "Output"
    },
    "pipeline_run_id": {
      "format": "uuid",
      "title": "Pipeline Run Id",
      "type": "string"
    },
    "project_id": {
      "format": "uuid",
      "title": "Project Id",
      "type": "string"
    },
    "requires_human_review": {
      "title": "Requires Human Review",
      "type": "boolean"
    },
    "review": {
      "$ref": "#/components/schemas/ReviewBlock"
    },
    "status": {
      "enum": [
        "in_progress",
        "completed",
        "failed",
        "passed"
      ],
      "title": "Status",
      "type": "string"
    },
    "test_run_id": {
      "format": "uuid",
      "title": "Test Run Id",
      "type": "string"
    }
  },
  "required": [
    "id",
    "project_id",
    "agent_id",
    "test_run_id",
    "pipeline_run_id",
    "mode",
    "status",
    "attempt",
    "max_attempts",
    "requires_human_review",
    "review",
    "links"
  ],
  "title": "AgentInvocationResponse",
  "type": "object"
}
```

## AgentInvokeRequest

```json
{
  "additionalProperties": false,
  "properties": {
    "config_overrides": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/AgentConfigPatch"
        },
        {
          "type": "null"
        }
      ],
      "description": "Per-invocation tighten-only overrides. Provider, endpoint, mode, temperature, max_tokens, escalation, and other ambiguous fields are rejected."
    },
    "correlation_id": {
      "anyOf": [
        {
          "maxLength": 128,
          "pattern": "^[A-Za-z0-9._:-]+$",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Correlation Id"
    },
    "inline_input": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "description": "Inline input for an agent that accepts it (catalog: inline_input_supported). input.payload must then name a stored test run of project_id, which anchors tenancy, review and retention. ≤ 262144 bytes; redacted before storage.",
      "title": "Inline Input"
    },
    "input": {
      "additionalProperties": true,
      "description": "The agent's <StageName>InvokeInput, as published by GET /api/v1/agents/catalog/{agent_id}.",
      "title": "Input",
      "type": "object"
    },
    "mode": {
      "default": "async",
      "description": "sync waits for the result, and only for sync-eligible agents (see the catalog); any other agent runs async and answers 202.",
      "enum": [
        "async",
        "sync"
      ],
      "title": "Mode",
      "type": "string"
    },
    "project_id": {
      "description": "Assertion: must equal the project of the test run named in input, else 400.",
      "format": "uuid",
      "title": "Project Id",
      "type": "string"
    }
  },
  "required": [
    "project_id",
    "input"
  ],
  "title": "AgentInvokeRequest",
  "type": "object"
}
```

## AgentMemoryEntryResponse

```json
{
  "description": "Single memory entry returned to the client.",
  "properties": {
    "confidence": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Confidence"
    },
    "created_at": {
      "format": "date-time",
      "title": "Created At",
      "type": "string"
    },
    "entity_id": {
      "title": "Entity Id",
      "type": "string"
    },
    "entity_type": {
      "title": "Entity Type",
      "type": "string"
    },
    "error_signature": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Error Signature"
    },
    "expires_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Expires At"
    },
    "failure_category": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Failure Category"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "lifecycle_status": {
      "default": "active",
      "title": "Lifecycle Status",
      "type": "string"
    },
    "payload": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Payload"
    },
    "pipeline_run_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Pipeline Run Id"
    },
    "project_id": {
      "format": "uuid",
      "title": "Project Id",
      "type": "string"
    },
    "resolution": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Resolution"
    },
    "root_cause_summary": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Root Cause Summary"
    },
    "run_id": {
      "format": "uuid",
      "title": "Run Id",
      "type": "string"
    },
    "source_hash": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Source Hash"
    },
    "source_snapshot_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Source Snapshot Id"
    },
    "source_type": {
      "default": "pipeline_agent",
      "title": "Source Type",
      "type": "string"
    },
    "superseded_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Superseded At"
    },
    "superseded_by_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Superseded By Id"
    },
    "trust_level": {
      "default": "derived",
      "title": "Trust Level",
      "type": "string"
    }
  },
  "required": [
    "id",
    "project_id",
    "run_id",
    "entity_type",
    "entity_id",
    "created_at"
  ],
  "title": "AgentMemoryEntryResponse",
  "type": "object"
}
```

## AgentMemoryListResponse

```json
{
  "description": "Paginated memory entry list.",
  "properties": {
    "items": {
      "items": {
        "$ref": "#/components/schemas/AgentMemoryEntryResponse"
      },
      "title": "Items",
      "type": "array"
    },
    "page": {
      "title": "Page",
      "type": "integer"
    },
    "size": {
      "title": "Size",
      "type": "integer"
    },
    "total": {
      "title": "Total",
      "type": "integer"
    }
  },
  "required": [
    "total",
    "items",
    "page",
    "size"
  ],
  "title": "AgentMemoryListResponse",
  "type": "object"
}
```

## AgentPipelineResponse

```json
{
  "properties": {
    "attempt": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Attempt"
    },
    "build_number": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Build Number"
    },
    "cancel_requested": {
      "anyOf": [
        {
          "type": "boolean"
        },
        {
          "type": "null"
        }
      ],
      "title": "Cancel Requested"
    },
    "completed_at": {
      "anyOf": [
        {},
        {
          "type": "null"
        }
      ],
      "title": "Completed At"
    },
    "created_at": {
      "title": "Created At"
    },
    "error": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Error"
    },
    "execution_metadata": {
      "anyOf": [
        {},
        {
          "type": "null"
        }
      ],
      "title": "Execution Metadata"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "max_attempts": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Max Attempts"
    },
    "next_retry_at": {
      "anyOf": [
        {},
        {
          "type": "null"
        }
      ],
      "title": "Next Retry At"
    },
    "provenance_metadata": {
      "anyOf": [
        {},
        {
          "type": "null"
        }
      ],
      "title": "Provenance Metadata"
    },
    "public_status": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Public Status"
    },
    "rerun_of": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Rerun Of"
    },
    "review_summary": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/AgentPipelineReviewSummary"
        },
        {
          "type": "null"
        }
      ]
    },
    "run_seq": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Run Seq"
    },
    "started_at": {
      "anyOf": [
        {},
        {
          "type": "null"
        }
      ],
      "title": "Started At"
    },
    "status": {
      "title": "Status",
      "type": "string"
    },
    "suite_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Suite Name"
    },
    "test_run_id": {
      "format": "uuid",
      "title": "Test Run Id",
      "type": "string"
    },
    "workflow_type": {
      "title": "Workflow Type",
      "type": "string"
    }
  },
  "required": [
    "id",
    "test_run_id",
    "workflow_type",
    "status",
    "created_at"
  ],
  "title": "AgentPipelineResponse",
  "type": "object"
}
```

## AgentPipelineReviewSummary

```json
{
  "description": "Minimal review projection for a pipeline card.\n\nReviewer identity deliberately stays out of this API response.  A settled\ntimestamp is enough for the status card to distinguish an accepted report\nfrom a non-report pipeline that passed without human review.",
  "properties": {
    "settled_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Settled At"
    },
    "state": {
      "enum": [
        "pending_review",
        "accepted",
        "rejected"
      ],
      "title": "State",
      "type": "string"
    }
  },
  "required": [
    "state"
  ],
  "title": "AgentPipelineReviewSummary",
  "type": "object"
}
```

## AgentRunSummaryResponse

```json
{
  "properties": {
    "ai_disclaimer": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Ai Disclaimer"
    },
    "ai_disclaimer_version": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Ai Disclaimer Version"
    },
    "analysis_count": {
      "default": 0,
      "title": "Analysis Count",
      "type": "integer"
    },
    "anomaly_count": {
      "default": 0,
      "title": "Anomaly Count",
      "type": "integer"
    },
    "build_number": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Build Number"
    },
    "executive_panel": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Executive Panel"
    },
    "executive_summary": {
      "title": "Executive Summary",
      "type": "string"
    },
    "generated_at": {
      "anyOf": [
        {},
        {
          "type": "null"
        }
      ],
      "title": "Generated At"
    },
    "is_regression": {
      "default": false,
      "title": "Is Regression",
      "type": "boolean"
    },
    "markdown_report": {
      "title": "Markdown Report",
      "type": "string"
    },
    "project_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Project Id"
    },
    "requires_human_review": {
      "default": true,
      "description": "True for AI-generated content: a draft until review.state == accepted.",
      "title": "Requires Human Review",
      "type": "boolean"
    },
    "review": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/ReviewBlock"
        },
        {
          "type": "null"
        }
      ]
    },
    "test_run_id": {
      "title": "Test Run Id",
      "type": "string"
    }
  },
  "required": [
    "test_run_id",
    "executive_summary",
    "markdown_report"
  ],
  "title": "AgentRunSummaryResponse",
  "type": "object"
}
```

## AgentStackReleaseGateRequest

```json
{
  "properties": {
    "change_id": {
      "title": "Change Id",
      "type": "string"
    },
    "model_versions": {
      "additionalProperties": {
        "type": "string"
      },
      "title": "Model Versions",
      "type": "object"
    },
    "persist": {
      "default": true,
      "title": "Persist",
      "type": "boolean"
    },
    "prompt_versions": {
      "additionalProperties": {
        "type": "string"
      },
      "title": "Prompt Versions",
      "type": "object"
    },
    "required_gates": {
      "anyOf": [
        {
          "items": {
            "additionalProperties": {
              "type": "string"
            },
            "type": "object"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Required Gates"
    },
    "routing_versions": {
      "additionalProperties": {
        "type": "string"
      },
      "title": "Routing Versions",
      "type": "object"
    }
  },
  "required": [
    "change_id"
  ],
  "title": "AgentStackReleaseGateRequest",
  "type": "object"
}
```

## AgentTaskV1

```json
{
  "additionalProperties": false,
  "properties": {
    "attempt": {
      "default": 1,
      "minimum": 1.0,
      "title": "Attempt",
      "type": "integer"
    },
    "budget": {
      "$ref": "#/components/schemas/TaskBudgetV1"
    },
    "capability_id": {
      "title": "Capability Id",
      "type": "string"
    },
    "completed_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Completed At"
    },
    "dependencies": {
      "default": [],
      "items": {
        "type": "string"
      },
      "title": "Dependencies",
      "type": "array"
    },
    "enrichment_stop_reason": {
      "anyOf": [
        {
          "enum": [
            "cancelled",
            "llm_call_budget_exhausted",
            "token_budget_exhausted",
            "wall_clock_budget_exhausted",
            "investigation_not_found",
            "budget_reservation_failed",
            "duplicate_reservation",
            "budget_ledger_invalid",
            "budget_reservation_identity_mismatch",
            "budget_settlement_failed",
            "budget_settlement_pending",
            "budget_overrun",
            "reservation_lease_expired",
            "cost_budget_exhausted",
            "cluster_child_join_timeout",
            "cluster_child_cancelled",
            "cluster_child_failed",
            "cluster_child_dispatch_exhausted",
            "project_child_capacity_exhausted"
          ],
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Enrichment Stop Reason"
    },
    "error": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Error"
    },
    "evidence_ids": {
      "default": [],
      "items": {
        "type": "string"
      },
      "title": "Evidence Ids",
      "type": "array"
    },
    "failure_cluster_id": {
      "anyOf": [
        {
          "maxLength": 200,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Failure Cluster Id"
    },
    "finding_ids": {
      "default": [],
      "items": {
        "type": "string"
      },
      "title": "Finding Ids",
      "type": "array"
    },
    "parent_task_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Parent Task Id"
    },
    "required": {
      "default": false,
      "title": "Required",
      "type": "boolean"
    },
    "schema_version": {
      "const": 1,
      "default": 1,
      "title": "Schema Version",
      "type": "integer"
    },
    "selected": {
      "title": "Selected",
      "type": "boolean"
    },
    "selection_reason": {
      "title": "Selection Reason",
      "type": "string"
    },
    "source_pipeline_run_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Source Pipeline Run Id"
    },
    "stage_name": {
      "title": "Stage Name",
      "type": "string"
    },
    "started_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Started At"
    },
    "status": {
      "enum": [
        "pending",
        "running",
        "completed",
        "failed",
        "skipped",
        "cancelled",
        "partial"
      ],
      "title": "Status",
      "type": "string"
    },
    "stop_reason": {
      "anyOf": [
        {
          "enum": [
            "completed",
            "policy_skip",
            "not_selected",
            "budget_exhausted",
            "timeout",
            "capability_error",
            "cancelled",
            "partial"
          ],
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Stop Reason"
    },
    "task_id": {
      "title": "Task Id",
      "type": "string"
    },
    "usage": {
      "$ref": "#/components/schemas/TaskUsageV1"
    }
  },
  "required": [
    "task_id",
    "capability_id",
    "stage_name",
    "status",
    "selected",
    "selection_reason"
  ],
  "title": "AgentTaskV1",
  "type": "object"
}
```

## AgenticRunV1

```json
{
  "additionalProperties": false,
  "properties": {
    "agentic_run_id": {
      "title": "Agentic Run Id",
      "type": "string"
    },
    "child_agentic_run_ids": {
      "default": [],
      "items": {
        "type": "string"
      },
      "title": "Child Agentic Run Ids",
      "type": "array"
    },
    "children_truncated": {
      "default": false,
      "title": "Children Truncated",
      "type": "boolean"
    },
    "completed_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Completed At"
    },
    "evidence": {
      "default": [],
      "items": {
        "$ref": "#/components/schemas/AgentEvidenceV1"
      },
      "title": "Evidence",
      "type": "array"
    },
    "findings": {
      "default": [],
      "items": {
        "$ref": "#/components/schemas/AgentFindingV1"
      },
      "title": "Findings",
      "type": "array"
    },
    "observed_child_runs": {
      "default": 0,
      "minimum": 0.0,
      "title": "Observed Child Runs",
      "type": "integer"
    },
    "observed_task_rows": {
      "default": 0,
      "minimum": 0.0,
      "title": "Observed Task Rows",
      "type": "integer"
    },
    "plan_id": {
      "title": "Plan Id",
      "type": "string"
    },
    "plan_integrity_status": {
      "enum": [
        "verified",
        "failed",
        "legacy_unhashed"
      ],
      "title": "Plan Integrity Status",
      "type": "string"
    },
    "plan_sha256": {
      "anyOf": [
        {
          "pattern": "^[0-9a-f]{64}$",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Plan Sha256"
    },
    "planner_version": {
      "title": "Planner Version",
      "type": "string"
    },
    "public_status": {
      "enum": [
        "in_progress",
        "completed",
        "failed",
        "passed"
      ],
      "title": "Public Status",
      "type": "string"
    },
    "root_task_id": {
      "title": "Root Task Id",
      "type": "string"
    },
    "run_budget": {
      "$ref": "#/components/schemas/TaskBudgetV1"
    },
    "schema_version": {
      "const": 1,
      "default": 1,
      "title": "Schema Version",
      "type": "integer"
    },
    "selected_capability_ids": {
      "default": [],
      "items": {
        "type": "string"
      },
      "title": "Selected Capability Ids",
      "type": "array"
    },
    "skipped_capability_ids": {
      "default": [],
      "items": {
        "type": "string"
      },
      "title": "Skipped Capability Ids",
      "type": "array"
    },
    "source_pipeline_run_id": {
      "title": "Source Pipeline Run Id",
      "type": "string"
    },
    "started_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Started At"
    },
    "status": {
      "enum": [
        "pending",
        "running",
        "retry_wait",
        "completed",
        "passed",
        "failed"
      ],
      "title": "Status",
      "type": "string"
    },
    "tasks": {
      "default": [],
      "items": {
        "$ref": "#/components/schemas/AgentTaskV1"
      },
      "title": "Tasks",
      "type": "array"
    },
    "tasks_truncated": {
      "default": false,
      "title": "Tasks Truncated",
      "type": "boolean"
    },
    "terminal_outcome": {
      "$ref": "#/components/schemas/TerminalOutcomeV1"
    },
    "test_run_id": {
      "title": "Test Run Id",
      "type": "string"
    },
    "workflow_type": {
      "title": "Workflow Type",
      "type": "string"
    }
  },
  "required": [
    "agentic_run_id",
    "source_pipeline_run_id",
    "test_run_id",
    "workflow_type",
    "status",
    "public_status",
    "root_task_id",
    "plan_id",
    "plan_integrity_status",
    "planner_version",
    "terminal_outcome"
  ],
  "title": "AgenticRunV1",
  "type": "object"
}
```

## AllowedTransitionResponse

```json
{
  "properties": {
    "action": {
      "title": "Action",
      "type": "string"
    },
    "allowed": {
      "title": "Allowed",
      "type": "boolean"
    },
    "blocked_reason": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Blocked Reason"
    },
    "requires_reason": {
      "default": false,
      "title": "Requires Reason",
      "type": "boolean"
    }
  },
  "required": [
    "action",
    "allowed"
  ],
  "title": "AllowedTransitionResponse",
  "type": "object"
}
```

## AnalysisLookupResponse

```json
{
  "description": "US-2.4: latest AI analysis for a (project, fingerprint) pair.\n\nAll fields are ``None`` when the test has never been analysed — the UI\nrenders a \"no AI analysis recorded yet\" empty state instead of a 404\n(which axios would surface as a scary error toast).\n\n``failure_category`` is a plain string, NOT the ``FailureCategory``\nenum: the backing column is ``String(30)`` and a strict enum here would\n422 the whole response if a stored value ever drifts out of vocab.",
  "properties": {
    "analysis_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Analysis Id"
    },
    "analyzed_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Analyzed At"
    },
    "failure_category": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Failure Category"
    }
  },
  "title": "AnalysisLookupResponse",
  "type": "object"
}
```

## AnalysisProvenance

```json
{
  "description": "US-15.1 — which engine actually produced this conclusion, and why.\n\nPopulated verbatim from ``AIAnalysis.routing_metadata`` (written by the\nanalysis router). It is NEVER recomputed or inferred: rows analysed before\nthis feature carry no routing metadata, so ``AnalysisResponse.provenance``\nis ``None`` for them rather than a plausible-looking guess.\n\nThe point of this block is a specific honesty case: when the LLM was\nunavailable and the rules engine ran instead, the card must be able to say\nso (``fallback_occurred`` / ``fallback_from`` / ``fallback_reason``)\ninstead of silently presenting heuristics as model output.",
  "properties": {
    "confidence_basis": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Confidence Basis"
    },
    "fallback_from": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Fallback From"
    },
    "fallback_occurred": {
      "default": false,
      "title": "Fallback Occurred",
      "type": "boolean"
    },
    "fallback_reason": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Fallback Reason"
    },
    "hint_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Hint Id"
    },
    "hint_match_field": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Hint Match Field"
    },
    "hint_pattern": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Hint Pattern"
    },
    "hint_set_by": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Hint Set By"
    },
    "llm_model": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Llm Model"
    },
    "llm_provider": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Llm Provider"
    },
    "mode_requested": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Mode Requested"
    },
    "mode_resolved": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Mode Resolved"
    },
    "mode_used": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Mode Used"
    },
    "prompt_versions": {
      "additionalProperties": {
        "type": "string"
      },
      "title": "Prompt Versions",
      "type": "object"
    },
    "threshold_check": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/ThresholdCheck"
        },
        {
          "type": "null"
        }
      ]
    }
  },
  "title": "AnalysisProvenance",
  "type": "object"
}
```

## AnalysisResponse

```json
{
  "properties": {
    "analysis_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Analysis Id"
    },
    "backend_error_found": {
      "title": "Backend Error Found",
      "type": "boolean"
    },
    "confidence_gate": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/ThresholdCheck"
        },
        {
          "type": "null"
        }
      ]
    },
    "confidence_gate_status": {
      "default": "not_evaluated",
      "title": "Confidence Gate Status",
      "type": "string"
    },
    "confidence_score": {
      "title": "Confidence Score",
      "type": "integer"
    },
    "confidence_why": {
      "$ref": "#/components/schemas/ConfidenceWhy"
    },
    "evidence_references": {
      "items": {
        "$ref": "#/components/schemas/EvidenceReference"
      },
      "title": "Evidence References",
      "type": "array"
    },
    "failure_category": {
      "$ref": "#/components/schemas/FailureCategory"
    },
    "is_flaky": {
      "title": "Is Flaky",
      "type": "boolean"
    },
    "kind_evidence": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Kind Evidence"
    },
    "llm_model": {
      "title": "Llm Model",
      "type": "string"
    },
    "llm_provider": {
      "title": "Llm Provider",
      "type": "string"
    },
    "low_confidence": {
      "default": false,
      "title": "Low Confidence",
      "type": "boolean"
    },
    "pod_issue_found": {
      "title": "Pod Issue Found",
      "type": "boolean"
    },
    "provenance": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/AnalysisProvenance"
        },
        {
          "type": "null"
        }
      ]
    },
    "recommended_actions": {
      "items": {
        "type": "string"
      },
      "title": "Recommended Actions",
      "type": "array"
    },
    "requires_human_review": {
      "title": "Requires Human Review",
      "type": "boolean"
    },
    "role_actions": {
      "$ref": "#/components/schemas/RoleActions"
    },
    "root_cause_summary": {
      "title": "Root Cause Summary",
      "type": "string"
    },
    "test_case_id": {
      "format": "uuid",
      "title": "Test Case Id",
      "type": "string"
    },
    "tools_used": {
      "default": [],
      "items": {
        "type": "string"
      },
      "title": "Tools Used",
      "type": "array"
    }
  },
  "required": [
    "test_case_id",
    "root_cause_summary",
    "failure_category",
    "backend_error_found",
    "pod_issue_found",
    "is_flaky",
    "confidence_score",
    "recommended_actions",
    "evidence_references",
    "llm_provider",
    "llm_model",
    "requires_human_review"
  ],
  "title": "AnalysisResponse",
  "type": "object"
}
```

## AnalyzeRequest

```json
{
  "properties": {
    "ocp_namespace": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Ocp Namespace"
    },
    "ocp_pod_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Ocp Pod Name"
    },
    "service_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Service Name"
    },
    "test_case_id": {
      "format": "uuid",
      "title": "Test Case Id",
      "type": "string"
    },
    "timestamp": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Timestamp"
    }
  },
  "required": [
    "test_case_id"
  ],
  "title": "AnalyzeRequest",
  "type": "object"
}
```

## ApiKeyCreate

```json
{
  "properties": {
    "expires_days": {
      "anyOf": [
        {
          "maximum": 365.0,
          "minimum": 1.0,
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Expires Days"
    },
    "name": {
      "maxLength": 100,
      "minLength": 2,
      "title": "Name",
      "type": "string"
    },
    "project_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "description": "Bind key to a single project (ADMIN only)",
      "title": "Project Id"
    },
    "scopes": {
      "items": {
        "type": "string"
      },
      "title": "Scopes",
      "type": "array"
    },
    "target_user_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "description": "Create key for another user (ADMIN only)",
      "title": "Target User Id"
    }
  },
  "required": [
    "name"
  ],
  "title": "ApiKeyCreate",
  "type": "object"
}
```

## ApiKeyCreatedResponse

```json
{
  "description": "Returned ONCE at creation — includes the plaintext key.",
  "properties": {
    "created_at": {
      "format": "date-time",
      "title": "Created At",
      "type": "string"
    },
    "expires_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Expires At"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "is_active": {
      "title": "Is Active",
      "type": "boolean"
    },
    "key_hint": {
      "title": "Key Hint",
      "type": "string"
    },
    "last_used_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Last Used At"
    },
    "name": {
      "title": "Name",
      "type": "string"
    },
    "project_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Project Id"
    },
    "raw_key": {
      "title": "Raw Key",
      "type": "string"
    },
    "scopes": {
      "items": {
        "type": "string"
      },
      "title": "Scopes",
      "type": "array"
    }
  },
  "required": [
    "id",
    "name",
    "key_hint",
    "scopes",
    "is_active",
    "created_at",
    "raw_key"
  ],
  "title": "ApiKeyCreatedResponse",
  "type": "object"
}
```

## ApiKeyResponse

```json
{
  "properties": {
    "created_at": {
      "format": "date-time",
      "title": "Created At",
      "type": "string"
    },
    "expires_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Expires At"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "is_active": {
      "title": "Is Active",
      "type": "boolean"
    },
    "key_hint": {
      "title": "Key Hint",
      "type": "string"
    },
    "last_used_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Last Used At"
    },
    "name": {
      "title": "Name",
      "type": "string"
    },
    "project_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Project Id"
    },
    "scopes": {
      "items": {
        "type": "string"
      },
      "title": "Scopes",
      "type": "array"
    }
  },
  "required": [
    "id",
    "name",
    "key_hint",
    "scopes",
    "is_active",
    "created_at"
  ],
  "title": "ApiKeyResponse",
  "type": "object"
}
```

## AttributionPreviewResponse

```json
{
  "properties": {
    "considered": {
      "title": "Considered",
      "type": "integer"
    },
    "explicit": {
      "default": 0,
      "title": "Explicit",
      "type": "integer"
    },
    "field_present": {
      "title": "Field Present",
      "type": "integer"
    },
    "matched": {
      "title": "Matched",
      "type": "integer"
    },
    "sample": {
      "items": {
        "$ref": "#/components/schemas/AttributionPreviewSample"
      },
      "title": "Sample",
      "type": "array"
    },
    "shadowed": {
      "default": 0,
      "title": "Shadowed",
      "type": "integer"
    },
    "target_exists": {
      "default": true,
      "title": "Target Exists",
      "type": "boolean"
    },
    "unattributed_recent": {
      "default": 0,
      "title": "Unattributed Recent",
      "type": "integer"
    }
  },
  "required": [
    "considered",
    "matched",
    "field_present",
    "sample"
  ],
  "title": "AttributionPreviewResponse",
  "type": "object"
}
```

## AttributionPreviewSample

```json
{
  "properties": {
    "branch": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Branch"
    },
    "build_number": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Build Number"
    },
    "created_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Created At"
    },
    "current_release_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Current Release Name"
    },
    "environment": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Environment"
    },
    "run_id": {
      "title": "Run Id",
      "type": "string"
    },
    "tags": {
      "items": {
        "type": "string"
      },
      "title": "Tags",
      "type": "array"
    },
    "would_land": {
      "default": "rule",
      "title": "Would Land",
      "type": "string"
    }
  },
  "required": [
    "run_id"
  ],
  "title": "AttributionPreviewSample",
  "type": "object"
}
```

## AttributionRuleField

```json
{
  "properties": {
    "glob": {
      "title": "Glob",
      "type": "boolean"
    },
    "help": {
      "title": "Help",
      "type": "string"
    },
    "label": {
      "title": "Label",
      "type": "string"
    },
    "value": {
      "title": "Value",
      "type": "string"
    }
  },
  "required": [
    "value",
    "label",
    "glob",
    "help"
  ],
  "title": "AttributionRuleField",
  "type": "object"
}
```

## AttributionRuleFieldsResponse

```json
{
  "properties": {
    "fields": {
      "items": {
        "$ref": "#/components/schemas/AttributionRuleField"
      },
      "title": "Fields",
      "type": "array"
    }
  },
  "required": [
    "fields"
  ],
  "title": "AttributionRuleFieldsResponse",
  "type": "object"
}
```

## AttributionRuleIn

```json
{
  "properties": {
    "is_enabled": {
      "default": true,
      "title": "Is Enabled",
      "type": "boolean"
    },
    "match_field": {
      "title": "Match Field",
      "type": "string"
    },
    "match_pattern": {
      "maxLength": 255,
      "minLength": 1,
      "title": "Match Pattern",
      "type": "string"
    },
    "name": {
      "maxLength": 255,
      "minLength": 1,
      "title": "Name",
      "type": "string"
    },
    "priority": {
      "default": 100,
      "maximum": 10000.0,
      "minimum": 0.0,
      "title": "Priority",
      "type": "integer"
    },
    "target_release_name": {
      "maxLength": 255,
      "minLength": 1,
      "title": "Target Release Name",
      "type": "string"
    }
  },
  "required": [
    "name",
    "match_field",
    "match_pattern",
    "target_release_name"
  ],
  "title": "AttributionRuleIn",
  "type": "object"
}
```

## AttributionRuleUpdate

```json
{
  "properties": {
    "is_enabled": {
      "anyOf": [
        {
          "type": "boolean"
        },
        {
          "type": "null"
        }
      ],
      "title": "Is Enabled"
    },
    "match_field": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Match Field"
    },
    "match_pattern": {
      "anyOf": [
        {
          "maxLength": 255,
          "minLength": 1,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Match Pattern"
    },
    "name": {
      "anyOf": [
        {
          "maxLength": 255,
          "minLength": 1,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Name"
    },
    "priority": {
      "anyOf": [
        {
          "maximum": 10000.0,
          "minimum": 0.0,
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Priority"
    },
    "target_release_name": {
      "anyOf": [
        {
          "maxLength": 255,
          "minLength": 1,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Target Release Name"
    }
  },
  "title": "AttributionRuleUpdate",
  "type": "object"
}
```

## AuditLogListResponse

```json
{
  "properties": {
    "items": {
      "items": {
        "$ref": "#/components/schemas/AuditLogResponse"
      },
      "title": "Items",
      "type": "array"
    },
    "page": {
      "title": "Page",
      "type": "integer"
    },
    "pages": {
      "title": "Pages",
      "type": "integer"
    },
    "size": {
      "title": "Size",
      "type": "integer"
    },
    "total": {
      "title": "Total",
      "type": "integer"
    }
  },
  "required": [
    "items",
    "total",
    "page",
    "size",
    "pages"
  ],
  "title": "AuditLogListResponse",
  "type": "object"
}
```

## AuditLogResponse

```json
{
  "properties": {
    "action": {
      "title": "Action",
      "type": "string"
    },
    "actor_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Actor Id"
    },
    "actor_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Actor Name"
    },
    "created_at": {
      "format": "date-time",
      "title": "Created At",
      "type": "string"
    },
    "details": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Details"
    },
    "entity_id": {
      "format": "uuid",
      "title": "Entity Id",
      "type": "string"
    },
    "entity_type": {
      "title": "Entity Type",
      "type": "string"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "new_values": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "New Values"
    },
    "old_values": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Old Values"
    },
    "policy_snapshot": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Policy Snapshot"
    },
    "project_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Project Id"
    },
    "reason": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Reason"
    },
    "transition_from": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Transition From"
    },
    "transition_to": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Transition To"
    }
  },
  "required": [
    "id",
    "entity_type",
    "entity_id",
    "action",
    "created_at"
  ],
  "title": "AuditLogResponse",
  "type": "object"
}
```

## BaselineDiff

```json
{
  "properties": {
    "baseline_build_number": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Baseline Build Number"
    },
    "baseline_run_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Baseline Run Id"
    },
    "baseline_suite_count": {
      "default": 0,
      "title": "Baseline Suite Count",
      "type": "integer"
    },
    "classified_new_failures": {
      "default": [],
      "items": {
        "additionalProperties": true,
        "type": "object"
      },
      "title": "Classified New Failures",
      "type": "array"
    },
    "commit_range": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Commit Range"
    },
    "config_drift": {
      "default": [],
      "items": {
        "additionalProperties": true,
        "type": "object"
      },
      "title": "Config Drift",
      "type": "array"
    },
    "current_suite_count": {
      "default": 0,
      "title": "Current Suite Count",
      "type": "integer"
    },
    "new_failures": {
      "default": [],
      "items": {
        "type": "string"
      },
      "title": "New Failures",
      "type": "array"
    },
    "pass_rate_delta": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Pass Rate Delta"
    },
    "regression_classification": {
      "default": "unclassified",
      "title": "Regression Classification",
      "type": "string"
    },
    "regression_clusters": {
      "default": [],
      "items": {
        "additionalProperties": true,
        "type": "object"
      },
      "title": "Regression Clusters",
      "type": "array"
    },
    "resolved_failures": {
      "default": [],
      "items": {
        "type": "string"
      },
      "title": "Resolved Failures",
      "type": "array"
    },
    "selection_reason": {
      "default": "latest_passing",
      "title": "Selection Reason",
      "type": "string"
    },
    "suites_impacted_delta": {
      "default": 0,
      "title": "Suites Impacted Delta",
      "type": "integer"
    }
  },
  "title": "BaselineDiff",
  "type": "object"
}
```

## BatchAcceptRequest

```json
{
  "properties": {
    "case_ids": {
      "items": {
        "format": "uuid",
        "type": "string"
      },
      "title": "Case Ids",
      "type": "array"
    },
    "edits": {
      "anyOf": [
        {
          "additionalProperties": {
            "$ref": "#/components/schemas/RagCaseAcceptEdits"
          },
          "propertyNames": {
            "format": "uuid"
          },
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Edits"
    }
  },
  "required": [
    "case_ids"
  ],
  "title": "BatchAcceptRequest",
  "type": "object"
}
```

## BillingOverviewProject

```json
{
  "properties": {
    "cap_hits": {
      "title": "Cap Hits",
      "type": "integer"
    },
    "covered_usd": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Covered Usd"
    },
    "current_cost_usd": {
      "title": "Current Cost Usd",
      "type": "number"
    },
    "hard_cap_usd": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Hard Cap Usd"
    },
    "included_usd": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Included Usd"
    },
    "overage_charge_usd": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Overage Charge Usd"
    },
    "overage_rate_usd": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Overage Rate Usd"
    },
    "overage_usd": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Overage Usd"
    },
    "project_id": {
      "format": "uuid",
      "title": "Project Id",
      "type": "string"
    },
    "project_name": {
      "title": "Project Name",
      "type": "string"
    },
    "projected_overage_charge_usd": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Projected Overage Charge Usd"
    },
    "projected_overage_usd": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Projected Overage Usd"
    },
    "projected_period_cost_usd": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Projected Period Cost Usd"
    },
    "projection_basis": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Projection Basis"
    },
    "status": {
      "title": "Status",
      "type": "string"
    },
    "utilization_pct": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Utilization Pct"
    }
  },
  "required": [
    "project_id",
    "project_name",
    "current_cost_usd",
    "status",
    "cap_hits"
  ],
  "title": "BillingOverviewProject",
  "type": "object"
}
```

## BillingOverviewResponse

```json
{
  "properties": {
    "period_end": {
      "format": "date-time",
      "title": "Period End",
      "type": "string"
    },
    "period_start": {
      "format": "date-time",
      "title": "Period Start",
      "type": "string"
    },
    "price_table_updated": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Price Table Updated"
    },
    "pricing_is_estimated": {
      "default": true,
      "title": "Pricing Is Estimated",
      "type": "boolean"
    },
    "projects": {
      "items": {
        "$ref": "#/components/schemas/BillingOverviewProject"
      },
      "title": "Projects",
      "type": "array"
    },
    "total_cost_usd": {
      "title": "Total Cost Usd",
      "type": "number"
    },
    "total_llm_calls": {
      "title": "Total Llm Calls",
      "type": "integer"
    }
  },
  "required": [
    "period_start",
    "period_end",
    "total_cost_usd",
    "total_llm_calls",
    "projects"
  ],
  "title": "BillingOverviewResponse",
  "type": "object"
}
```

## BisectCommit

```json
{
  "properties": {
    "author": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Author"
    },
    "commit_url": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Commit Url"
    },
    "committed_at": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Committed At"
    },
    "message": {
      "default": "",
      "title": "Message",
      "type": "string"
    },
    "rationale": {
      "additionalProperties": true,
      "title": "Rationale",
      "type": "object"
    },
    "score": {
      "title": "Score",
      "type": "number"
    },
    "sha": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Sha"
    }
  },
  "required": [
    "sha",
    "score",
    "rationale"
  ],
  "title": "BisectCommit",
  "type": "object"
}
```

## BisectCompare

```json
{
  "properties": {
    "cached": {
      "default": false,
      "title": "Cached",
      "type": "boolean"
    },
    "caveat": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Caveat"
    },
    "commit_count": {
      "default": 0,
      "title": "Commit Count",
      "type": "integer"
    },
    "commits": {
      "items": {
        "$ref": "#/components/schemas/BisectCommit"
      },
      "title": "Commits",
      "type": "array"
    },
    "has_location_signal": {
      "default": false,
      "title": "Has Location Signal",
      "type": "boolean"
    },
    "host": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Host"
    },
    "reason": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Reason"
    },
    "run_level_commit_count": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Run Level Commit Count"
    },
    "state": {
      "title": "State",
      "type": "string"
    },
    "truncated": {
      "default": false,
      "title": "Truncated",
      "type": "boolean"
    }
  },
  "required": [
    "state"
  ],
  "title": "BisectCompare",
  "type": "object"
}
```

## BisectResponse

```json
{
  "properties": {
    "anchor": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Anchor"
    },
    "bad": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/BisectRunRef"
        },
        {
          "type": "null"
        }
      ]
    },
    "bounded": {
      "default": false,
      "title": "Bounded",
      "type": "boolean"
    },
    "compare": {
      "$ref": "#/components/schemas/BisectCompare"
    },
    "fallback_branch": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Fallback Branch"
    },
    "fingerprint": {
      "title": "Fingerprint",
      "type": "string"
    },
    "generated_at": {
      "format": "date-time",
      "title": "Generated At",
      "type": "string"
    },
    "good": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/BisectRunRef"
        },
        {
          "type": "null"
        }
      ]
    },
    "narrowed_from": {
      "default": "unavailable",
      "title": "Narrowed From",
      "type": "string"
    },
    "run_id": {
      "format": "uuid",
      "title": "Run Id",
      "type": "string"
    },
    "runs_read": {
      "default": 0,
      "title": "Runs Read",
      "type": "integer"
    },
    "series_branch": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Series Branch"
    },
    "state": {
      "title": "State",
      "type": "string"
    },
    "streak_length": {
      "default": 0,
      "title": "Streak Length",
      "type": "integer"
    },
    "test_name": {
      "title": "Test Name",
      "type": "string"
    },
    "untested_count": {
      "default": 0,
      "title": "Untested Count",
      "type": "integer"
    },
    "untested_runs": {
      "items": {
        "$ref": "#/components/schemas/BisectRunRef"
      },
      "title": "Untested Runs",
      "type": "array"
    }
  },
  "required": [
    "run_id",
    "fingerprint",
    "test_name",
    "state",
    "compare",
    "generated_at"
  ],
  "title": "BisectResponse",
  "type": "object"
}
```

## BisectRunRef

```json
{
  "properties": {
    "branch": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Branch"
    },
    "build_number": {
      "title": "Build Number",
      "type": "string"
    },
    "ci_run_url": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Ci Run Url"
    },
    "commit_hash": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Commit Hash"
    },
    "created_at": {
      "format": "date-time",
      "title": "Created At",
      "type": "string"
    },
    "outcome": {
      "title": "Outcome",
      "type": "string"
    },
    "run_id": {
      "format": "uuid",
      "title": "Run Id",
      "type": "string"
    }
  },
  "required": [
    "run_id",
    "build_number",
    "created_at",
    "outcome"
  ],
  "title": "BisectRunRef",
  "type": "object"
}
```

## Body_draft_release_phases_api_v1_releases__release_id__phases_generate_post

```json
{
  "properties": {
    "file": {
      "anyOf": [
        {
          "format": "binary",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "File"
    },
    "text": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Text"
    }
  },
  "title": "Body_draft_release_phases_api_v1_releases__release_id__phases_generate_post",
  "type": "object"
}
```

## Body_import_test_cases_api_v1_test_management_cases_import_post

```json
{
  "properties": {
    "file": {
      "format": "binary",
      "title": "File",
      "type": "string"
    }
  },
  "required": [
    "file"
  ],
  "title": "Body_import_test_cases_api_v1_test_management_cases_import_post",
  "type": "object"
}
```

## Body_ingest_file_api_v1_ingest_file_post

```json
{
  "properties": {
    "branch": {
      "maxLength": 255,
      "title": "Branch",
      "type": "string"
    },
    "build_number": {
      "maxLength": 100,
      "minLength": 1,
      "title": "Build Number",
      "type": "string"
    },
    "ci_actor": {
      "maxLength": 120,
      "title": "Ci Actor",
      "type": "string"
    },
    "ci_provider": {
      "maxLength": 30,
      "title": "Ci Provider",
      "type": "string"
    },
    "ci_repo": {
      "maxLength": 300,
      "title": "Ci Repo",
      "type": "string"
    },
    "ci_run_url": {
      "maxLength": 1000,
      "title": "Ci Run Url",
      "type": "string"
    },
    "commit_hash": {
      "maxLength": 64,
      "title": "Commit Hash",
      "type": "string"
    },
    "commit_range": {
      "title": "Commit Range",
      "type": "string"
    },
    "environment": {
      "maxLength": 100,
      "title": "Environment",
      "type": "string"
    },
    "executed_at": {
      "format": "date-time",
      "title": "Executed At",
      "type": "string"
    },
    "file": {
      "format": "binary",
      "title": "File",
      "type": "string"
    },
    "format": {
      "default": "auto",
      "title": "Format",
      "type": "string"
    },
    "jenkins_job": {
      "maxLength": 500,
      "title": "Jenkins Job",
      "type": "string"
    },
    "pr_number": {
      "minimum": 1.0,
      "title": "Pr Number",
      "type": "integer"
    },
    "project_id": {
      "title": "Project Id",
      "type": "string"
    },
    "release_name": {
      "maxLength": 255,
      "title": "Release Name",
      "type": "string"
    },
    "run_ai": {
      "default": true,
      "title": "Run Ai",
      "type": "boolean"
    },
    "run_context": {
      "maxLength": 16384,
      "title": "Run Context",
      "type": "string"
    }
  },
  "required": [
    "file",
    "project_id",
    "build_number"
  ],
  "title": "Body_ingest_file_api_v1_ingest_file_post",
  "type": "object"
}
```

## Body_login_api_v1_auth_login_post

```json
{
  "properties": {
    "client_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Client Id"
    },
    "client_secret": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Client Secret"
    },
    "grant_type": {
      "anyOf": [
        {
          "pattern": "password",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Grant Type"
    },
    "password": {
      "title": "Password",
      "type": "string"
    },
    "scope": {
      "default": "",
      "title": "Scope",
      "type": "string"
    },
    "username": {
      "title": "Username",
      "type": "string"
    }
  },
  "required": [
    "username",
    "password"
  ],
  "title": "Body_login_api_v1_auth_login_post",
  "type": "object"
}
```

## Body_reassign_assigned_failure_api_v1_me_assigned_failures__test_case_id__reassign_put

```json
{
  "properties": {
    "new_assignee_user_id": {
      "format": "uuid",
      "title": "New Assignee User Id",
      "type": "string"
    }
  },
  "required": [
    "new_assignee_user_id"
  ],
  "title": "Body_reassign_assigned_failure_api_v1_me_assigned_failures__test_case_id__reassign_put",
  "type": "object"
}
```

## Body_trigger_finetune_api_v1_training_finetune_post

```json
{
  "properties": {
    "track": {
      "title": "Track",
      "type": "string"
    }
  },
  "required": [
    "track"
  ],
  "title": "Body_trigger_finetune_api_v1_training_finetune_post",
  "type": "object"
}
```

## BudgetConfig

```json
{
  "additionalProperties": false,
  "description": "Budget names are the existing ``DEFAULT_BUDGETS`` keys (section 4.2).",
  "properties": {
    "max_cost_usd_per_run": {
      "default": 5.0,
      "minimum": 0.0,
      "title": "Max Cost Usd Per Run",
      "type": "number"
    },
    "max_llm_calls_per_run": {
      "default": 30,
      "minimum": 0.0,
      "title": "Max Llm Calls Per Run",
      "type": "integer"
    },
    "max_runs_per_day": {
      "default": 10,
      "minimum": 0.0,
      "title": "Max Runs Per Day",
      "type": "integer"
    },
    "max_tokens_per_run": {
      "default": 60000,
      "minimum": 0.0,
      "title": "Max Tokens Per Run",
      "type": "integer"
    }
  },
  "title": "BudgetConfig",
  "type": "object"
}
```

## BulkJobProgress

```json
{
  "properties": {
    "completed": {
      "title": "Completed",
      "type": "integer"
    },
    "failed": {
      "title": "Failed",
      "type": "integer"
    },
    "in_progress": {
      "title": "In Progress",
      "type": "integer"
    },
    "not_started": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Not Started"
    },
    "passed": {
      "title": "Passed",
      "type": "integer"
    },
    "started": {
      "title": "Started",
      "type": "integer"
    }
  },
  "required": [
    "started",
    "in_progress",
    "completed",
    "passed",
    "failed"
  ],
  "title": "BulkJobProgress",
  "type": "object"
}
```

## BulkJobRead

```json
{
  "properties": {
    "bulk_job_id": {
      "title": "Bulk Job Id",
      "type": "string"
    },
    "cancelled_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Cancelled At"
    },
    "counters_source": {
      "title": "Counters Source",
      "type": "string"
    },
    "created_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Created At"
    },
    "matched": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Matched"
    },
    "not_found": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Not Found"
    },
    "note": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Note"
    },
    "progress": {
      "$ref": "#/components/schemas/BulkJobProgress"
    },
    "queued": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Queued"
    },
    "requested": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Requested"
    },
    "skipped_in_progress": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Skipped In Progress"
    },
    "source": {
      "title": "Source",
      "type": "string"
    },
    "state": {
      "title": "State",
      "type": "string"
    },
    "truncated": {
      "default": false,
      "title": "Truncated",
      "type": "boolean"
    },
    "workflow_type": {
      "title": "Workflow Type",
      "type": "string"
    }
  },
  "required": [
    "bulk_job_id",
    "workflow_type",
    "source",
    "state",
    "counters_source",
    "progress"
  ],
  "title": "BulkJobRead",
  "type": "object"
}
```

## BulkRunFilter

```json
{
  "properties": {
    "branch": {
      "anyOf": [
        {
          "items": {
            "type": "string"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Branch"
    },
    "days": {
      "anyOf": [
        {
          "maximum": 365.0,
          "minimum": 0.0,
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "default": 30,
      "title": "Days"
    },
    "project_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Project Id"
    },
    "q": {
      "anyOf": [
        {
          "maxLength": 100,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Q"
    },
    "recommendation": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Recommendation"
    },
    "release_id": {
      "anyOf": [
        {
          "items": {
            "type": "string"
          },
          "maxItems": 20,
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Release Id"
    },
    "status": {
      "anyOf": [
        {
          "maxLength": 20,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Status"
    },
    "suite_name": {
      "anyOf": [
        {
          "items": {
            "type": "string"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Suite Name"
    }
  },
  "title": "BulkRunFilter",
  "type": "object"
}
```

## BulkTriggerRequest

```json
{
  "properties": {
    "client_token": {
      "anyOf": [
        {
          "maxLength": 64,
          "minLength": 8,
          "pattern": "^[A-Za-z0-9_-]+$",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Client Token"
    },
    "run_filter": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/BulkRunFilter"
        },
        {
          "type": "null"
        }
      ]
    },
    "run_ids": {
      "anyOf": [
        {
          "items": {
            "format": "uuid",
            "type": "string"
          },
          "maxItems": 2000,
          "minItems": 1,
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Run Ids"
    },
    "workflow_type": {
      "default": "offline",
      "enum": [
        "offline",
        "deep"
      ],
      "title": "Workflow Type",
      "type": "string"
    }
  },
  "title": "BulkTriggerRequest",
  "type": "object"
}
```

## BulkTriggerResponse

```json
{
  "properties": {
    "bulk_job_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Bulk Job Id"
    },
    "matched": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Matched"
    },
    "not_found": {
      "title": "Not Found",
      "type": "integer"
    },
    "not_found_ids": {
      "items": {
        "type": "string"
      },
      "title": "Not Found Ids",
      "type": "array"
    },
    "queued": {
      "title": "Queued",
      "type": "integer"
    },
    "replayed": {
      "default": false,
      "title": "Replayed",
      "type": "boolean"
    },
    "requested": {
      "default": 0,
      "title": "Requested",
      "type": "integer"
    },
    "skipped_in_progress": {
      "default": 0,
      "title": "Skipped In Progress",
      "type": "integer"
    },
    "skipped_in_progress_ids": {
      "items": {
        "type": "string"
      },
      "title": "Skipped In Progress Ids",
      "type": "array"
    },
    "source": {
      "default": "selection",
      "title": "Source",
      "type": "string"
    },
    "tracking": {
      "default": false,
      "title": "Tracking",
      "type": "boolean"
    },
    "truncated": {
      "default": false,
      "title": "Truncated",
      "type": "boolean"
    },
    "workflow_type": {
      "title": "Workflow Type",
      "type": "string"
    }
  },
  "required": [
    "queued",
    "not_found",
    "workflow_type"
  ],
  "title": "BulkTriggerResponse",
  "type": "object"
}
```

## CadenceWindowStatus

```json
{
  "properties": {
    "deadline": {
      "format": "date-time",
      "title": "Deadline",
      "type": "string"
    },
    "local_label": {
      "default": "",
      "title": "Local Label",
      "type": "string"
    },
    "outcome": {
      "title": "Outcome",
      "type": "string"
    },
    "recorded": {
      "default": false,
      "title": "Recorded",
      "type": "boolean"
    },
    "run_count": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Run Count"
    },
    "window_end": {
      "format": "date-time",
      "title": "Window End",
      "type": "string"
    },
    "window_start": {
      "format": "date-time",
      "title": "Window Start",
      "type": "string"
    }
  },
  "required": [
    "deadline",
    "window_start",
    "window_end",
    "outcome"
  ],
  "title": "CadenceWindowStatus",
  "type": "object"
}
```

## CandidateEvaluationAccepted

```json
{
  "properties": {
    "run_id": {
      "title": "Run Id",
      "type": "string"
    },
    "status": {
      "title": "Status",
      "type": "string"
    }
  },
  "required": [
    "run_id",
    "status"
  ],
  "title": "CandidateEvaluationAccepted",
  "type": "object"
}
```

## CandidateEvaluationRequest

```json
{
  "additionalProperties": false,
  "properties": {
    "candidate": {
      "$ref": "#/components/schemas/EvalCandidateIn"
    },
    "limit": {
      "anyOf": [
        {
          "maximum": 200.0,
          "minimum": 1.0,
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Limit"
    },
    "project_id": {
      "format": "uuid",
      "title": "Project Id",
      "type": "string"
    }
  },
  "required": [
    "candidate",
    "project_id"
  ],
  "title": "CandidateEvaluationRequest",
  "type": "object"
}
```

## CandidateOptionsResponse

```json
{
  "properties": {
    "max_items": {
      "title": "Max Items",
      "type": "integer"
    },
    "offline_mode": {
      "title": "Offline Mode",
      "type": "boolean"
    },
    "prompt_version": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Prompt Version"
    },
    "supported_tiers_by_task": {
      "additionalProperties": {
        "items": {
          "type": "string"
        },
        "type": "array"
      },
      "title": "Supported Tiers By Task",
      "type": "object"
    },
    "tiers": {
      "items": {
        "$ref": "#/components/schemas/CandidateTierOption"
      },
      "title": "Tiers",
      "type": "array"
    }
  },
  "required": [
    "tiers",
    "offline_mode",
    "max_items",
    "supported_tiers_by_task"
  ],
  "title": "CandidateOptionsResponse",
  "type": "object"
}
```

## CandidateTierOption

```json
{
  "properties": {
    "available": {
      "title": "Available",
      "type": "boolean"
    },
    "model": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Model"
    },
    "provider": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Provider"
    },
    "reason": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Reason"
    },
    "tier": {
      "title": "Tier",
      "type": "string"
    }
  },
  "required": [
    "tier",
    "available"
  ],
  "title": "CandidateTierOption",
  "type": "object"
}
```

## CanonicalManagedUnlinkRequest

```json
{
  "properties": {
    "reason": {
      "maxLength": 500,
      "minLength": 1,
      "title": "Reason",
      "type": "string"
    }
  },
  "required": [
    "reason"
  ],
  "title": "CanonicalManagedUnlinkRequest",
  "type": "object"
}
```

## CanonicalPromotionResponse

```json
{
  "properties": {
    "canonical": {
      "$ref": "#/components/schemas/CanonicalTestCaseResponse"
    },
    "managed_case": {
      "$ref": "#/components/schemas/ManagedTestCaseResponse"
    }
  },
  "required": [
    "canonical",
    "managed_case"
  ],
  "title": "CanonicalPromotionResponse",
  "type": "object"
}
```

## CanonicalRetirementConfirmRequest

```json
{
  "properties": {
    "reason": {
      "maxLength": 500,
      "minLength": 1,
      "title": "Reason",
      "type": "string"
    }
  },
  "required": [
    "reason"
  ],
  "title": "CanonicalRetirementConfirmRequest",
  "type": "object"
}
```

## CanonicalTestCaseBulkLinkRequest

```json
{
  "description": "Move multiple canonical test cases to a different suite within the\nsame project. Pair with ``POST /api/v1/canonical-test-cases/bulk-link``.",
  "properties": {
    "canonical_ids": {
      "items": {
        "format": "uuid",
        "type": "string"
      },
      "maxItems": 200,
      "minItems": 1,
      "title": "Canonical Ids",
      "type": "array"
    },
    "target_test_suite_id": {
      "format": "uuid",
      "title": "Target Test Suite Id",
      "type": "string"
    }
  },
  "required": [
    "target_test_suite_id",
    "canonical_ids"
  ],
  "title": "CanonicalTestCaseBulkLinkRequest",
  "type": "object"
}
```

## CanonicalTestCaseBulkLinkResponse

```json
{
  "description": "Outcome of a bulk-link request. ``moved`` and\n``skipped_already_in_target`` always sum to the number of ids that\nactually resolved to a canonical row; ``missing_ids`` lists requested\nids that didn't resolve (stale UI selection, deleted in flight).",
  "properties": {
    "missing_ids": {
      "items": {
        "format": "uuid",
        "type": "string"
      },
      "title": "Missing Ids",
      "type": "array"
    },
    "moved": {
      "title": "Moved",
      "type": "integer"
    },
    "skipped_already_in_target": {
      "title": "Skipped Already In Target",
      "type": "integer"
    }
  },
  "required": [
    "moved",
    "skipped_already_in_target",
    "missing_ids"
  ],
  "title": "CanonicalTestCaseBulkLinkResponse",
  "type": "object"
}
```

## CanonicalTestCaseLinkRequest

```json
{
  "description": "Move a canonical test case to a different suite within the same project.",
  "properties": {
    "test_suite_id": {
      "format": "uuid",
      "title": "Test Suite Id",
      "type": "string"
    }
  },
  "required": [
    "test_suite_id"
  ],
  "title": "CanonicalTestCaseLinkRequest",
  "type": "object"
}
```

## CanonicalTestCaseListResponse

```json
{
  "properties": {
    "items": {
      "items": {
        "$ref": "#/components/schemas/CanonicalTestCaseResponse"
      },
      "title": "Items",
      "type": "array"
    },
    "page": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Page"
    },
    "size": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Size"
    },
    "total": {
      "title": "Total",
      "type": "integer"
    }
  },
  "required": [
    "items",
    "total"
  ],
  "title": "CanonicalTestCaseListResponse",
  "type": "object"
}
```

## CanonicalTestCaseResponse

```json
{
  "properties": {
    "class_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Class Name"
    },
    "created_at": {
      "format": "date-time",
      "title": "Created At",
      "type": "string"
    },
    "deleted_at_run_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Deleted At Run Id"
    },
    "deleted_observed_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Deleted Observed At"
    },
    "first_seen_run_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "First Seen Run Id"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "last_seen_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Last Seen At"
    },
    "last_seen_run_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Last Seen Run Id"
    },
    "last_seen_test_case_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Last Seen Test Case Id"
    },
    "managed_test_case_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Managed Test Case Id"
    },
    "project_id": {
      "format": "uuid",
      "title": "Project Id",
      "type": "string"
    },
    "retirement_confirmed_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Retirement Confirmed At"
    },
    "retirement_confirmed_by_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Retirement Confirmed By Id"
    },
    "retirement_reason": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Retirement Reason"
    },
    "review_tag": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Review Tag"
    },
    "run_count": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Run Count"
    },
    "source": {
      "title": "Source",
      "type": "string"
    },
    "status": {
      "title": "Status",
      "type": "string"
    },
    "tags": {
      "anyOf": [
        {
          "items": {
            "type": "string"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Tags"
    },
    "test_fingerprint": {
      "title": "Test Fingerprint",
      "type": "string"
    },
    "test_name": {
      "title": "Test Name",
      "type": "string"
    },
    "test_suite_id": {
      "format": "uuid",
      "title": "Test Suite Id",
      "type": "string"
    },
    "test_suite_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Test Suite Name"
    },
    "updated_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Updated At"
    }
  },
  "required": [
    "created_at",
    "id",
    "project_id",
    "test_suite_id",
    "test_fingerprint",
    "test_name",
    "status",
    "source"
  ],
  "title": "CanonicalTestCaseResponse",
  "type": "object"
}
```

## ChangePasswordRequest

```json
{
  "properties": {
    "current_password": {
      "maxLength": 128,
      "title": "Current Password",
      "type": "string"
    },
    "new_password": {
      "maxLength": 128,
      "minLength": 8,
      "title": "New Password",
      "type": "string"
    }
  },
  "required": [
    "current_password",
    "new_password"
  ],
  "title": "ChangePasswordRequest",
  "type": "object"
}
```

## ChatMessageResponse

```json
{
  "properties": {
    "content": {
      "title": "Content",
      "type": "string"
    },
    "created_at": {
      "title": "Created At"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "role": {
      "title": "Role",
      "type": "string"
    },
    "session_id": {
      "format": "uuid",
      "title": "Session Id",
      "type": "string"
    },
    "sources": {
      "anyOf": [
        {
          "items": {},
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Sources"
    }
  },
  "required": [
    "id",
    "session_id",
    "role",
    "content",
    "created_at"
  ],
  "title": "ChatMessageResponse",
  "type": "object"
}
```

## ChatSessionCreate

```json
{
  "properties": {
    "active_report_id": {
      "anyOf": [
        {
          "maxLength": 64,
          "minLength": 1,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Active Report Id"
    },
    "active_report_version": {
      "anyOf": [
        {
          "minimum": 1.0,
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Active Report Version"
    },
    "active_test_run_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Active Test Run Id"
    },
    "project_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Project Id"
    },
    "title": {
      "anyOf": [
        {
          "maxLength": 500,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Title"
    }
  },
  "title": "ChatSessionCreate",
  "type": "object"
}
```

## ChatSessionResponse

```json
{
  "properties": {
    "active_report_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Active Report Id"
    },
    "active_report_version": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Active Report Version"
    },
    "active_test_run_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Active Test Run Id"
    },
    "created_at": {
      "title": "Created At"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "project_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Project Id"
    },
    "title": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Title"
    },
    "updated_at": {
      "title": "Updated At"
    }
  },
  "required": [
    "id",
    "created_at",
    "updated_at"
  ],
  "title": "ChatSessionResponse",
  "type": "object"
}
```

## CitationSchema

```json
{
  "properties": {
    "canonical_url": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Canonical Url"
    },
    "case_index": {
      "title": "Case Index",
      "type": "integer"
    },
    "chunk_text_preview": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Chunk Text Preview"
    },
    "relevance_score": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Relevance Score"
    },
    "section_heading": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Section Heading"
    },
    "source_id": {
      "format": "uuid",
      "title": "Source Id",
      "type": "string"
    },
    "source_title": {
      "title": "Source Title",
      "type": "string"
    },
    "vector_id": {
      "title": "Vector Id",
      "type": "string"
    }
  },
  "required": [
    "case_index",
    "vector_id",
    "source_id",
    "source_title"
  ],
  "title": "CitationSchema",
  "type": "object"
}
```

## ClassificationHintCreate

```json
{
  "additionalProperties": false,
  "properties": {
    "category": {
      "maxLength": 30,
      "title": "Category",
      "type": "string"
    },
    "enabled": {
      "default": true,
      "title": "Enabled",
      "type": "boolean"
    },
    "match_field": {
      "maxLength": 20,
      "title": "Match Field",
      "type": "string"
    },
    "note": {
      "anyOf": [
        {
          "maxLength": 500,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Note"
    },
    "pattern": {
      "maxLength": 200,
      "title": "Pattern",
      "type": "string"
    }
  },
  "required": [
    "match_field",
    "pattern",
    "category"
  ],
  "title": "ClassificationHintCreate",
  "type": "object"
}
```

## ClassificationHintList

```json
{
  "properties": {
    "items": {
      "items": {
        "$ref": "#/components/schemas/ClassificationHintResponse"
      },
      "title": "Items",
      "type": "array"
    },
    "max_hints": {
      "title": "Max Hints",
      "type": "integer"
    },
    "total": {
      "title": "Total",
      "type": "integer"
    }
  },
  "required": [
    "items",
    "total",
    "max_hints"
  ],
  "title": "ClassificationHintList",
  "type": "object"
}
```

## ClassificationHintPreview

```json
{
  "properties": {
    "examples": {
      "default": [],
      "items": {
        "additionalProperties": {
          "type": "string"
        },
        "type": "object"
      },
      "title": "Examples",
      "type": "array"
    },
    "lookback_days": {
      "title": "Lookback Days",
      "type": "integer"
    },
    "matched": {
      "title": "Matched",
      "type": "integer"
    },
    "sampled": {
      "title": "Sampled",
      "type": "integer"
    }
  },
  "required": [
    "matched",
    "sampled",
    "lookback_days"
  ],
  "title": "ClassificationHintPreview",
  "type": "object"
}
```

## ClassificationHintResponse

```json
{
  "properties": {
    "category": {
      "title": "Category",
      "type": "string"
    },
    "created_at": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Created At"
    },
    "created_by_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Created By Name"
    },
    "enabled": {
      "title": "Enabled",
      "type": "boolean"
    },
    "id": {
      "title": "Id",
      "type": "string"
    },
    "match_field": {
      "title": "Match Field",
      "type": "string"
    },
    "note": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Note"
    },
    "pattern": {
      "title": "Pattern",
      "type": "string"
    },
    "project_id": {
      "title": "Project Id",
      "type": "string"
    },
    "updated_at": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Updated At"
    }
  },
  "required": [
    "id",
    "project_id",
    "match_field",
    "pattern",
    "category",
    "enabled"
  ],
  "title": "ClassificationHintResponse",
  "type": "object"
}
```

## ClassificationHintUpdate

```json
{
  "additionalProperties": false,
  "properties": {
    "category": {
      "anyOf": [
        {
          "maxLength": 30,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Category"
    },
    "enabled": {
      "anyOf": [
        {
          "type": "boolean"
        },
        {
          "type": "null"
        }
      ],
      "title": "Enabled"
    },
    "match_field": {
      "anyOf": [
        {
          "maxLength": 20,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Match Field"
    },
    "note": {
      "anyOf": [
        {
          "maxLength": 500,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Note"
    },
    "pattern": {
      "anyOf": [
        {
          "maxLength": 200,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Pattern"
    }
  },
  "title": "ClassificationHintUpdate",
  "type": "object"
}
```

## ClassifyUncategorizedRequest

```json
{
  "description": "POST body for /api/v1/analytics/classify-uncategorized — bulk-assign a\nfailure category to every test case currently labelled UNKNOWN (or\nNULL) in the requested project + window.",
  "properties": {
    "category": {
      "$ref": "#/components/schemas/FailureCategory"
    },
    "days": {
      "default": 30,
      "maximum": 365.0,
      "minimum": 1.0,
      "title": "Days",
      "type": "integer"
    },
    "project_id": {
      "format": "uuid",
      "title": "Project Id",
      "type": "string"
    },
    "suite_name": {
      "anyOf": [
        {
          "maxLength": 500,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Suite Name"
    }
  },
  "required": [
    "project_id",
    "category"
  ],
  "title": "ClassifyUncategorizedRequest",
  "type": "object"
}
```

## ClassifyUncategorizedResponse

```json
{
  "properties": {
    "category": {
      "title": "Category",
      "type": "string"
    },
    "days": {
      "title": "Days",
      "type": "integer"
    },
    "project_id": {
      "format": "uuid",
      "title": "Project Id",
      "type": "string"
    },
    "suite_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Suite Name"
    },
    "updated": {
      "title": "Updated",
      "type": "integer"
    }
  },
  "required": [
    "updated",
    "category",
    "project_id",
    "days"
  ],
  "title": "ClassifyUncategorizedResponse",
  "type": "object"
}
```

## ClusterInsightResponse

```json
{
  "properties": {
    "cluster_id": {
      "title": "Cluster Id",
      "type": "string"
    },
    "cohesion_score": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Cohesion Score"
    },
    "criticality_level": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Criticality Level"
    },
    "dimension_scores": {
      "default": [],
      "items": {
        "$ref": "#/components/schemas/DimensionScore"
      },
      "title": "Dimension Scores",
      "type": "array"
    },
    "id": {
      "title": "Id",
      "type": "string"
    },
    "label": {
      "title": "Label",
      "type": "string"
    },
    "member_test_ids": {
      "default": [],
      "items": {
        "type": "string"
      },
      "title": "Member Test Ids",
      "type": "array"
    },
    "representative_error": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Representative Error"
    },
    "size": {
      "title": "Size",
      "type": "integer"
    }
  },
  "required": [
    "id",
    "cluster_id",
    "label",
    "size"
  ],
  "title": "ClusterInsightResponse",
  "type": "object"
}
```

## ClusterResponse

```json
{
  "properties": {
    "cluster_id": {
      "title": "Cluster Id",
      "type": "string"
    },
    "cohesion_score": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Cohesion Score"
    },
    "label": {
      "title": "Label",
      "type": "string"
    },
    "member_test_ids": {
      "items": {
        "type": "string"
      },
      "title": "Member Test Ids",
      "type": "array"
    },
    "representative_error": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Representative Error"
    },
    "size": {
      "title": "Size",
      "type": "integer"
    }
  },
  "required": [
    "cluster_id",
    "label",
    "representative_error",
    "member_test_ids",
    "size"
  ],
  "title": "ClusterResponse",
  "type": "object"
}
```

## CodeownersCoverage

```json
{
  "description": "Coverage of recent failing-test paths by ``path`` ownership rules.",
  "properties": {
    "codeowners_rules": {
      "default": 0,
      "title": "Codeowners Rules",
      "type": "integer"
    },
    "coverage_pct": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Coverage Pct"
    },
    "located": {
      "default": 0,
      "title": "Located",
      "type": "integer"
    },
    "lookback_days": {
      "default": 30,
      "title": "Lookback Days",
      "type": "integer"
    },
    "matched": {
      "default": 0,
      "title": "Matched",
      "type": "integer"
    },
    "path_rules": {
      "default": 0,
      "title": "Path Rules",
      "type": "integer"
    },
    "sampled": {
      "default": 0,
      "title": "Sampled",
      "type": "integer"
    },
    "source_roots": {
      "default": 0,
      "title": "Source Roots",
      "type": "integer"
    }
  },
  "title": "CodeownersCoverage",
  "type": "object"
}
```

## CodeownersImportRequest

```json
{
  "description": "Import a CODEOWNERS file into ``path`` ownership rules (US-8.3).\n\n``source == \"text\"`` carries the raw file body in ``text`` (air-gapped /\npaste-upload). ``source == \"github\"`` fetches it over the configured\nGitHub connector and ``text`` is ignored.",
  "properties": {
    "source": {
      "pattern": "^(github|text)$",
      "title": "Source",
      "type": "string"
    },
    "text": {
      "anyOf": [
        {
          "maxLength": 1000000,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Text"
    }
  },
  "required": [
    "source"
  ],
  "title": "CodeownersImportRequest",
  "type": "object"
}
```

## CodeownersImportResponse

```json
{
  "description": "Summary of a CODEOWNERS import.",
  "properties": {
    "coverage": {
      "$ref": "#/components/schemas/CodeownersCoverage"
    },
    "imported": {
      "title": "Imported",
      "type": "integer"
    },
    "rules_created": {
      "title": "Rules Created",
      "type": "integer"
    },
    "rules_replaced": {
      "title": "Rules Replaced",
      "type": "integer"
    },
    "source": {
      "title": "Source",
      "type": "string"
    }
  },
  "required": [
    "imported",
    "rules_created",
    "rules_replaced",
    "source",
    "coverage"
  ],
  "title": "CodeownersImportResponse",
  "type": "object"
}
```

## CommentMention

```json
{
  "description": "A project member an ``@username`` in a test-case comment resolved to (W21d ACT-02).\n\nIds and display names only: never an e-mail.",
  "properties": {
    "display_name": {
      "title": "Display Name",
      "type": "string"
    },
    "user_id": {
      "format": "uuid",
      "title": "User Id",
      "type": "string"
    },
    "username": {
      "title": "Username",
      "type": "string"
    }
  },
  "required": [
    "user_id",
    "username",
    "display_name"
  ],
  "title": "CommentMention",
  "type": "object"
}
```

## CompliancePackFileCheck

```json
{
  "properties": {
    "actual_sha256": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Actual Sha256"
    },
    "expected_sha256": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Expected Sha256"
    },
    "name": {
      "title": "Name",
      "type": "string"
    },
    "status": {
      "title": "Status",
      "type": "string"
    }
  },
  "required": [
    "name",
    "status"
  ],
  "title": "CompliancePackFileCheck",
  "type": "object"
}
```

## CompliancePackGenerateRequest

```json
{
  "description": "Body for POST /api/v1/releases/{id}/compliance-pack.",
  "properties": {
    "notes": {
      "anyOf": [
        {
          "maxLength": 2000,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Notes"
    },
    "retention_days": {
      "anyOf": [
        {
          "maximum": 3650.0,
          "minimum": 1.0,
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "description": "Override retention window (default 2557 = ~7 years)",
      "title": "Retention Days"
    }
  },
  "title": "CompliancePackGenerateRequest",
  "type": "object"
}
```

## CompliancePackKeyRotateRequest

```json
{
  "additionalProperties": false,
  "properties": {
    "confirmation": {
      "title": "Confirmation",
      "type": "string"
    },
    "reason": {
      "maxLength": 500,
      "minLength": 3,
      "title": "Reason",
      "type": "string"
    }
  },
  "required": [
    "confirmation",
    "reason"
  ],
  "title": "CompliancePackKeyRotateRequest",
  "type": "object"
}
```

## CompliancePackLifecycleResponse

```json
{
  "properties": {
    "pack_id": {
      "format": "uuid",
      "title": "Pack Id",
      "type": "string"
    },
    "purgeable_now": {
      "title": "Purgeable Now",
      "type": "boolean"
    },
    "retention_expires_at": {
      "format": "date-time",
      "title": "Retention Expires At",
      "type": "string"
    }
  },
  "required": [
    "pack_id",
    "retention_expires_at",
    "purgeable_now"
  ],
  "title": "CompliancePackLifecycleResponse",
  "type": "object"
}
```

## CompliancePackListRead

```json
{
  "properties": {
    "items": {
      "items": {
        "$ref": "#/components/schemas/CompliancePackSummaryRead"
      },
      "title": "Items",
      "type": "array"
    },
    "total": {
      "title": "Total",
      "type": "integer"
    }
  },
  "required": [
    "items",
    "total"
  ],
  "title": "CompliancePackListRead",
  "type": "object"
}
```

## CompliancePackRead

```json
{
  "properties": {
    "bytes": {
      "title": "Bytes",
      "type": "integer"
    },
    "file_count": {
      "title": "File Count",
      "type": "integer"
    },
    "generated_at": {
      "format": "date-time",
      "title": "Generated At",
      "type": "string"
    },
    "generated_by_user_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Generated By User Id"
    },
    "holds": {
      "items": {
        "$ref": "#/components/schemas/HoldRefRead"
      },
      "title": "Holds",
      "type": "array"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "manifest_sha256": {
      "title": "Manifest Sha256",
      "type": "string"
    },
    "metadata_snapshot": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Metadata Snapshot"
    },
    "minio_key": {
      "title": "Minio Key",
      "type": "string"
    },
    "notes": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Notes"
    },
    "project_id": {
      "format": "uuid",
      "title": "Project Id",
      "type": "string"
    },
    "release_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Release Id"
    },
    "retention_expires_at": {
      "format": "date-time",
      "title": "Retention Expires At",
      "type": "string"
    },
    "signing_key_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Signing Key Id"
    },
    "test_run_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Test Run Id"
    }
  },
  "required": [
    "id",
    "project_id",
    "minio_key",
    "manifest_sha256",
    "file_count",
    "bytes",
    "retention_expires_at",
    "generated_at"
  ],
  "title": "CompliancePackRead",
  "type": "object"
}
```

## CompliancePackSigningKeyRead

```json
{
  "properties": {
    "active": {
      "title": "Active",
      "type": "boolean"
    },
    "algorithm": {
      "title": "Algorithm",
      "type": "string"
    },
    "created_at": {
      "format": "date-time",
      "title": "Created At",
      "type": "string"
    },
    "key_id": {
      "title": "Key Id",
      "type": "string"
    },
    "public_key_pem": {
      "title": "Public Key Pem",
      "type": "string"
    },
    "retired_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Retired At"
    }
  },
  "required": [
    "key_id",
    "algorithm",
    "public_key_pem",
    "created_at",
    "active"
  ],
  "title": "CompliancePackSigningKeyRead",
  "type": "object"
}
```

## CompliancePackSigningKeysResponse

```json
{
  "properties": {
    "active_key_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Active Key Id"
    },
    "keys": {
      "items": {
        "$ref": "#/components/schemas/CompliancePackSigningKeyRead"
      },
      "title": "Keys",
      "type": "array"
    }
  },
  "required": [
    "keys"
  ],
  "title": "CompliancePackSigningKeysResponse",
  "type": "object"
}
```

## CompliancePackSummaryRead

```json
{
  "description": "One pack in the project-wide list (``GET /api/v1/compliance-packs``, W05 REL-04).\n\n``minio_key``, ``metadata_snapshot`` and ``notes`` are deliberately absent:\nstorage keys and evidence snapshots do not belong in a side rail.",
  "properties": {
    "bytes": {
      "title": "Bytes",
      "type": "integer"
    },
    "file_count": {
      "title": "File Count",
      "type": "integer"
    },
    "generated_at": {
      "format": "date-time",
      "title": "Generated At",
      "type": "string"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "manifest_sha256": {
      "title": "Manifest Sha256",
      "type": "string"
    },
    "project_id": {
      "format": "uuid",
      "title": "Project Id",
      "type": "string"
    },
    "project_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Project Name"
    },
    "release_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Release Id"
    },
    "release_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Release Name"
    },
    "release_version": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Release Version"
    },
    "retention_expires_at": {
      "format": "date-time",
      "title": "Retention Expires At",
      "type": "string"
    },
    "retired": {
      "title": "Retired",
      "type": "boolean"
    }
  },
  "required": [
    "id",
    "project_id",
    "bytes",
    "file_count",
    "manifest_sha256",
    "generated_at",
    "retention_expires_at",
    "retired"
  ],
  "title": "CompliancePackSummaryRead",
  "type": "object"
}
```

## CompliancePackVerification

```json
{
  "properties": {
    "chain": {
      "title": "Chain",
      "type": "string"
    },
    "checked_at": {
      "format": "date-time",
      "title": "Checked At",
      "type": "string"
    },
    "files": {
      "items": {
        "$ref": "#/components/schemas/CompliancePackFileCheck"
      },
      "title": "Files",
      "type": "array"
    },
    "files_checked": {
      "title": "Files Checked",
      "type": "integer"
    },
    "files_ok": {
      "title": "Files Ok",
      "type": "integer"
    },
    "manifest_matches_record": {
      "title": "Manifest Matches Record",
      "type": "boolean"
    },
    "pack_id": {
      "format": "uuid",
      "title": "Pack Id",
      "type": "string"
    },
    "result": {
      "title": "Result",
      "type": "string"
    },
    "signature": {
      "title": "Signature",
      "type": "string"
    },
    "signing_key_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Signing Key Id"
    },
    "summary": {
      "title": "Summary",
      "type": "string"
    }
  },
  "required": [
    "pack_id",
    "result",
    "chain",
    "manifest_matches_record",
    "signature",
    "files_checked",
    "files_ok",
    "files",
    "summary",
    "checked_at"
  ],
  "title": "CompliancePackVerification",
  "type": "object"
}
```

## ConfidenceWhy

```json
{
  "description": "Explains the basis of the confidence score for transparency.",
  "properties": {
    "confidence_basis": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Confidence Basis"
    },
    "data_sources": {
      "default": [],
      "items": {
        "type": "string"
      },
      "title": "Data Sources",
      "type": "array"
    },
    "evidence_count": {
      "default": 0,
      "title": "Evidence Count",
      "type": "integer"
    },
    "investigation_depth": {
      "default": "fast_path",
      "title": "Investigation Depth",
      "type": "string"
    },
    "is_llm_inference": {
      "default": false,
      "title": "Is Llm Inference",
      "type": "boolean"
    }
  },
  "title": "ConfidenceWhy",
  "type": "object"
}
```

## ConnectorConfigTestRequest

```json
{
  "properties": {
    "params": {
      "additionalProperties": true,
      "title": "Params",
      "type": "object"
    },
    "source_type": {
      "title": "Source Type",
      "type": "string"
    }
  },
  "required": [
    "source_type"
  ],
  "title": "ConnectorConfigTestRequest",
  "type": "object"
}
```

## ConnectorTestResult

```json
{
  "properties": {
    "detail": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Detail"
    },
    "error": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Error"
    },
    "latency_ms": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Latency Ms"
    },
    "success": {
      "title": "Success",
      "type": "boolean"
    }
  },
  "required": [
    "success"
  ],
  "title": "ConnectorTestResult",
  "type": "object"
}
```

## CreateShareLinkRequest

```json
{
  "properties": {
    "expiry_days": {
      "default": 7,
      "maximum": 30.0,
      "minimum": 1.0,
      "title": "Expiry Days",
      "type": "integer"
    },
    "layout": {
      "default": "executive",
      "pattern": "^(executive|engineering)$",
      "title": "Layout",
      "type": "string"
    }
  },
  "title": "CreateShareLinkRequest",
  "type": "object"
}
```

## DecisionLogEntry

```json
{
  "description": "A single structured decision made by an agent — mirror of\n``BaseAgent.log_decision`` output stored on AgentStageResult.decision_log.",
  "properties": {
    "alternatives": {
      "anyOf": [
        {
          "items": {
            "type": "string"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Alternatives"
    },
    "at": {
      "title": "At",
      "type": "string"
    },
    "chosen": {
      "title": "Chosen",
      "type": "string"
    },
    "context": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Context"
    },
    "decision_point": {
      "title": "Decision Point",
      "type": "string"
    },
    "rationale": {
      "title": "Rationale",
      "type": "string"
    },
    "test_case_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Test Case Id"
    }
  },
  "required": [
    "at",
    "decision_point",
    "chosen",
    "rationale"
  ],
  "title": "DecisionLogEntry",
  "type": "object"
}
```

## DecisionReportFeedbackRequest

```json
{
  "description": "Utility rating or claim correction for one immutable report version.",
  "properties": {
    "claim_id": {
      "anyOf": [
        {
          "maxLength": 128,
          "minLength": 1,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Claim Id"
    },
    "corrected_value": {
      "anyOf": [
        {},
        {
          "type": "null"
        }
      ],
      "title": "Corrected Value"
    },
    "correction_type": {
      "anyOf": [
        {
          "enum": [
            "category",
            "cause",
            "flaky",
            "release"
          ],
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Correction Type"
    },
    "evidence_ids": {
      "items": {
        "type": "string"
      },
      "maxItems": 5,
      "title": "Evidence Ids",
      "type": "array"
    },
    "feedback_kind": {
      "enum": [
        "utility",
        "claim_correction"
      ],
      "title": "Feedback Kind",
      "type": "string"
    },
    "idempotency_key": {
      "anyOf": [
        {
          "maxLength": 128,
          "minLength": 1,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Idempotency Key"
    },
    "reason": {
      "anyOf": [
        {
          "maxLength": 4000,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Reason"
    },
    "report_version": {
      "minimum": 1.0,
      "title": "Report Version",
      "type": "integer"
    },
    "utility_rating": {
      "anyOf": [
        {
          "enum": [
            "useful",
            "partially_useful",
            "not_useful"
          ],
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Utility Rating"
    }
  },
  "required": [
    "report_version",
    "feedback_kind"
  ],
  "title": "DecisionReportFeedbackRequest",
  "type": "object"
}
```

## DecisionReportRef

```json
{
  "properties": {
    "report_id": {
      "title": "Report Id",
      "type": "string"
    },
    "report_version": {
      "title": "Report Version",
      "type": "integer"
    }
  },
  "required": [
    "report_id",
    "report_version"
  ],
  "title": "DecisionReportRef",
  "type": "object"
}
```

## DecisionTrailResponse

```json
{
  "description": "Full decision trail for a pipeline run — the user-facing audit surface.",
  "properties": {
    "below_threshold_count": {
      "default": 0,
      "title": "Below Threshold Count",
      "type": "integer"
    },
    "completed_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Completed At"
    },
    "fallback_count": {
      "default": 0,
      "title": "Fallback Count",
      "type": "integer"
    },
    "mode_distribution": {
      "additionalProperties": {
        "type": "integer"
      },
      "title": "Mode Distribution",
      "type": "object"
    },
    "per_test": {
      "items": {
        "$ref": "#/components/schemas/PerTestRouting"
      },
      "title": "Per Test",
      "type": "array"
    },
    "pipeline_run_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Pipeline Run Id"
    },
    "pipeline_status": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Pipeline Status"
    },
    "run_id": {
      "format": "uuid",
      "title": "Run Id",
      "type": "string"
    },
    "stages": {
      "items": {
        "$ref": "#/components/schemas/StageDecisionSummary"
      },
      "title": "Stages",
      "type": "array"
    },
    "started_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Started At"
    },
    "total_cost_usd": {
      "default": 0.0,
      "title": "Total Cost Usd",
      "type": "number"
    },
    "total_tokens": {
      "default": 0,
      "title": "Total Tokens",
      "type": "integer"
    },
    "workflow_events": {
      "items": {
        "$ref": "#/components/schemas/WorkflowDecisionEvent"
      },
      "title": "Workflow Events",
      "type": "array"
    },
    "workflow_type": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Workflow Type"
    }
  },
  "required": [
    "run_id"
  ],
  "title": "DecisionTrailResponse",
  "type": "object"
}
```

## DeepCostRead

```json
{
  "properties": {
    "local_model": {
      "title": "Local Model",
      "type": "boolean"
    },
    "model": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Model"
    },
    "note": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Note"
    },
    "price_table_updated": {
      "title": "Price Table Updated",
      "type": "string"
    },
    "priced": {
      "title": "Priced",
      "type": "boolean"
    },
    "provider": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Provider"
    },
    "usd": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Usd"
    }
  },
  "required": [
    "priced",
    "local_model",
    "price_table_updated"
  ],
  "title": "DeepCostRead",
  "type": "object"
}
```

## DeepEstimateResponse

```json
{
  "properties": {
    "configuration": {
      "$ref": "#/components/schemas/DeepRunConfigurationRead"
    },
    "cost": {
      "$ref": "#/components/schemas/DeepCostRead"
    },
    "gate": {
      "$ref": "#/components/schemas/DeepGateRead"
    },
    "measured": {
      "title": "Measured",
      "type": "boolean"
    },
    "minutes_p50": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Minutes P50"
    },
    "reason": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Reason"
    },
    "run_id": {
      "format": "uuid",
      "title": "Run Id",
      "type": "string"
    },
    "sample_pipelines": {
      "title": "Sample Pipelines",
      "type": "integer"
    },
    "stages": {
      "items": {
        "$ref": "#/components/schemas/DeepEstimateStage"
      },
      "title": "Stages",
      "type": "array"
    },
    "tokens_p50": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Tokens P50"
    }
  },
  "required": [
    "run_id",
    "gate",
    "configuration",
    "measured",
    "sample_pipelines",
    "cost",
    "stages"
  ],
  "title": "DeepEstimateResponse",
  "type": "object"
}
```

## DeepEstimateStage

```json
{
  "properties": {
    "duration_seconds_p50": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Duration Seconds P50"
    },
    "input_tokens_p50": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Input Tokens P50"
    },
    "output_tokens_p50": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Output Tokens P50"
    },
    "per_failure": {
      "default": false,
      "title": "Per Failure",
      "type": "boolean"
    },
    "samples": {
      "title": "Samples",
      "type": "integer"
    },
    "stage_name": {
      "title": "Stage Name",
      "type": "string"
    }
  },
  "required": [
    "stage_name",
    "samples"
  ],
  "title": "DeepEstimateStage",
  "type": "object"
}
```

## DeepFindingResponse

```json
{
  "properties": {
    "affected_services": {
      "anyOf": [
        {
          "items": {},
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Affected Services"
    },
    "causal_chain": {
      "anyOf": [
        {
          "items": {},
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Causal Chain"
    },
    "cluster_id": {
      "title": "Cluster Id",
      "type": "string"
    },
    "confidence_basis": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Confidence Basis"
    },
    "confidence_score": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Confidence Score"
    },
    "contract_violations": {
      "anyOf": [
        {
          "items": {},
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Contract Violations"
    },
    "evidence": {
      "anyOf": [
        {
          "items": {},
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Evidence"
    },
    "failure_category": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Failure Category"
    },
    "origin": {
      "default": "unknown",
      "title": "Origin",
      "type": "string"
    },
    "recommended_actions": {
      "anyOf": [
        {
          "items": {},
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Recommended Actions"
    },
    "root_cause": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Root Cause"
    }
  },
  "required": [
    "cluster_id",
    "root_cause",
    "failure_category",
    "confidence_score",
    "causal_chain",
    "evidence",
    "affected_services",
    "contract_violations",
    "recommended_actions"
  ],
  "title": "DeepFindingResponse",
  "type": "object"
}
```

## DeepGateRead

```json
{
  "properties": {
    "allowed": {
      "title": "Allowed",
      "type": "boolean"
    },
    "analysis_mode": {
      "title": "Analysis Mode",
      "type": "string"
    },
    "deep_investigation_enabled": {
      "title": "Deep Investigation Enabled",
      "type": "boolean"
    },
    "message": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Message"
    },
    "reason_code": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Reason Code"
    }
  },
  "required": [
    "allowed",
    "analysis_mode",
    "deep_investigation_enabled"
  ],
  "title": "DeepGateRead",
  "type": "object"
}
```

## DeepHistoryResponse

```json
{
  "properties": {
    "items": {
      "items": {
        "$ref": "#/components/schemas/DeepHistoryRow"
      },
      "title": "Items",
      "type": "array"
    },
    "page": {
      "title": "Page",
      "type": "integer"
    },
    "project_id": {
      "format": "uuid",
      "title": "Project Id",
      "type": "string"
    },
    "size": {
      "title": "Size",
      "type": "integer"
    },
    "total": {
      "title": "Total",
      "type": "integer"
    }
  },
  "required": [
    "project_id",
    "total",
    "page",
    "size",
    "items"
  ],
  "title": "DeepHistoryResponse",
  "type": "object"
}
```

## DeepHistoryRow

```json
{
  "properties": {
    "avg_confidence": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Avg Confidence"
    },
    "build_number": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Build Number"
    },
    "clusters": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Clusters"
    },
    "completed_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Completed At"
    },
    "cost_usd": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Cost Usd"
    },
    "duration_seconds": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Duration Seconds"
    },
    "failures": {
      "title": "Failures",
      "type": "integer"
    },
    "findings": {
      "default": 0,
      "title": "Findings",
      "type": "integer"
    },
    "pipeline_run_id": {
      "format": "uuid",
      "title": "Pipeline Run Id",
      "type": "string"
    },
    "public_status": {
      "title": "Public Status",
      "type": "string"
    },
    "run_created_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Run Created At"
    },
    "run_id": {
      "format": "uuid",
      "title": "Run Id",
      "type": "string"
    },
    "run_seq": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Run Seq"
    },
    "started_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Started At"
    },
    "status": {
      "title": "Status",
      "type": "string"
    },
    "suite_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Suite Name"
    }
  },
  "required": [
    "run_id",
    "failures",
    "pipeline_run_id",
    "status",
    "public_status"
  ],
  "title": "DeepHistoryRow",
  "type": "object"
}
```

## DeepPreScanResponse

```json
{
  "properties": {
    "failing_results": {
      "title": "Failing Results",
      "type": "integer"
    },
    "grouped_by": {
      "default": "error_signature",
      "title": "Grouped By",
      "type": "string"
    },
    "groups": {
      "items": {
        "$ref": "#/components/schemas/PreScanGroup"
      },
      "title": "Groups",
      "type": "array"
    },
    "groups_total": {
      "title": "Groups Total",
      "type": "integer"
    },
    "measured": {
      "title": "Measured",
      "type": "boolean"
    },
    "refinement": {
      "title": "Refinement",
      "type": "string"
    },
    "refinement_note": {
      "title": "Refinement Note",
      "type": "string"
    },
    "run_id": {
      "format": "uuid",
      "title": "Run Id",
      "type": "string"
    }
  },
  "required": [
    "run_id",
    "measured",
    "failing_results",
    "groups_total",
    "groups",
    "refinement",
    "refinement_note"
  ],
  "title": "DeepPreScanResponse",
  "type": "object"
}
```

## DeepRunConfigurationRead

```json
{
  "properties": {
    "allowed_tiers": {
      "items": {
        "type": "string"
      },
      "title": "Allowed Tiers",
      "type": "array"
    },
    "evidence_sources": {
      "items": {
        "type": "string"
      },
      "title": "Evidence Sources",
      "type": "array"
    },
    "failures_analysed": {
      "title": "Failures Analysed",
      "type": "integer"
    },
    "max_failures_analyzed": {
      "title": "Max Failures Analyzed",
      "type": "integer"
    },
    "model_tier": {
      "title": "Model Tier",
      "type": "string"
    },
    "project_evidence_sources": {
      "items": {
        "type": "string"
      },
      "title": "Project Evidence Sources",
      "type": "array"
    },
    "project_max_failures_analyzed": {
      "title": "Project Max Failures Analyzed",
      "type": "integer"
    },
    "project_model_tier": {
      "title": "Project Model Tier",
      "type": "string"
    },
    "selected_failures": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Selected Failures"
    },
    "tool_narrowing_allowed": {
      "title": "Tool Narrowing Allowed",
      "type": "boolean"
    }
  },
  "required": [
    "failures_analysed",
    "max_failures_analyzed",
    "project_max_failures_analyzed",
    "model_tier",
    "project_model_tier",
    "allowed_tiers",
    "evidence_sources",
    "project_evidence_sources",
    "tool_narrowing_allowed"
  ],
  "title": "DeepRunConfigurationRead",
  "type": "object"
}
```

## DefectApprovalRequest

```json
{
  "properties": {
    "action": {
      "title": "Action",
      "type": "string"
    },
    "reason": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Reason"
    }
  },
  "required": [
    "action"
  ],
  "title": "DefectApprovalRequest",
  "type": "object"
}
```

## DefectApprovalResponse

```json
{
  "properties": {
    "approval_status": {
      "title": "Approval Status",
      "type": "string"
    },
    "defect_id": {
      "title": "Defect Id",
      "type": "string"
    },
    "jira_error": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Jira Error"
    },
    "jira_reason": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Jira Reason"
    },
    "jira_status": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Jira Status"
    },
    "jira_ticket": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Jira Ticket"
    },
    "jira_url": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Jira Url"
    },
    "message": {
      "title": "Message",
      "type": "string"
    }
  },
  "required": [
    "defect_id",
    "approval_status",
    "message"
  ],
  "title": "DefectApprovalResponse",
  "type": "object"
}
```

## DefectCandidateResponse

```json
{
  "description": "Pre-assembled defect candidate for a cluster (editable before submission).",
  "properties": {
    "cluster_id": {
      "title": "Cluster Id",
      "type": "string"
    },
    "component": {
      "title": "Component",
      "type": "string"
    },
    "composite_score": {
      "default": 0.0,
      "title": "Composite Score",
      "type": "number"
    },
    "criticality_scores": {
      "additionalProperties": true,
      "default": {},
      "title": "Criticality Scores",
      "type": "object"
    },
    "description": {
      "title": "Description",
      "type": "string"
    },
    "duplicate_defect_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Duplicate Defect Id"
    },
    "duplicate_detected": {
      "default": false,
      "title": "Duplicate Detected",
      "type": "boolean"
    },
    "duplicate_hint": {
      "default": "",
      "title": "Duplicate Hint",
      "type": "string"
    },
    "evidence_bundle": {
      "additionalProperties": true,
      "default": {},
      "title": "Evidence Bundle",
      "type": "object"
    },
    "failure_category": {
      "default": "UNKNOWN",
      "title": "Failure Category",
      "type": "string"
    },
    "labels": {
      "default": [],
      "items": {
        "type": "string"
      },
      "title": "Labels",
      "type": "array"
    },
    "member_count": {
      "default": 0,
      "title": "Member Count",
      "type": "integer"
    },
    "owner_team": {
      "title": "Owner Team",
      "type": "string"
    },
    "run_id": {
      "title": "Run Id",
      "type": "string"
    },
    "severity": {
      "title": "Severity",
      "type": "string"
    },
    "title": {
      "title": "Title",
      "type": "string"
    }
  },
  "required": [
    "cluster_id",
    "run_id",
    "title",
    "description",
    "severity",
    "component",
    "owner_team"
  ],
  "title": "DefectCandidateResponse",
  "type": "object"
}
```

## DefectIntakeRequest

```json
{
  "description": "POST body for /api/v1/analytics/defects — manual defect intake.\n\nSeverity uses the P0–P3 vocabulary the Defects UI renders; it maps to the\n`defects.severity` column's CRITICAL/HIGH/MEDIUM/LOW values server-side.\n`test_name`/`suite_name` are optional — when both are supplied the service\nwill try to attach the new defect to the most-recent matching TestCase row,\notherwise the defect is created standalone (test_case_id NULL).",
  "properties": {
    "affects_releases": {
      "anyOf": [
        {
          "items": {
            "type": "string"
          },
          "maxItems": 100,
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Affects Releases"
    },
    "component": {
      "anyOf": [
        {
          "maxLength": 255,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Component"
    },
    "description": {
      "anyOf": [
        {
          "maxLength": 10000,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Description"
    },
    "failure_category": {
      "$ref": "#/components/schemas/FailureCategory",
      "default": "PRODUCT_BUG"
    },
    "jira_ticket_url": {
      "anyOf": [
        {
          "maxLength": 1000,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Jira Ticket Url"
    },
    "project_id": {
      "format": "uuid",
      "title": "Project Id",
      "type": "string"
    },
    "severity": {
      "enum": [
        "P0",
        "P1",
        "P2",
        "P3"
      ],
      "title": "Severity",
      "type": "string"
    },
    "suite_name": {
      "anyOf": [
        {
          "maxLength": 500,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Suite Name"
    },
    "test_name": {
      "anyOf": [
        {
          "maxLength": 1000,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Test Name"
    },
    "title": {
      "maxLength": 255,
      "minLength": 3,
      "title": "Title",
      "type": "string"
    }
  },
  "required": [
    "project_id",
    "title",
    "severity"
  ],
  "title": "DefectIntakeRequest",
  "type": "object"
}
```

## DefectIntakeResponse

```json
{
  "properties": {
    "ai_confidence_score": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Ai Confidence Score"
    },
    "component": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Component"
    },
    "created_at": {
      "format": "date-time",
      "title": "Created At",
      "type": "string"
    },
    "failure_category": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Failure Category"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "jira_ticket_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Jira Ticket Id"
    },
    "jira_ticket_url": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Jira Ticket Url"
    },
    "project_id": {
      "format": "uuid",
      "title": "Project Id",
      "type": "string"
    },
    "resolution_status": {
      "title": "Resolution Status",
      "type": "string"
    },
    "severity": {
      "title": "Severity",
      "type": "string"
    },
    "suite_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Suite Name"
    },
    "test_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Test Name"
    },
    "title": {
      "title": "Title",
      "type": "string"
    }
  },
  "required": [
    "id",
    "project_id",
    "title",
    "severity",
    "resolution_status",
    "created_at"
  ],
  "title": "DefectIntakeResponse",
  "type": "object"
}
```

## DefectJiraLinkResult

```json
{
  "description": "What linking a Jira key did. ``status``: \"validated\" | \"saved_locally\" | \"unlinked\".",
  "properties": {
    "jira_key": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Jira Key"
    },
    "jira_url": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Jira Url"
    },
    "reason": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Reason"
    },
    "status": {
      "title": "Status",
      "type": "string"
    },
    "summary": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Summary"
    }
  },
  "required": [
    "status"
  ],
  "title": "DefectJiraLinkResult",
  "type": "object"
}
```

## DefectPatchRequest

```json
{
  "additionalProperties": false,
  "description": "PATCH /api/v1/projects/{project_id}/defects/{defect_id}.\n\nOnly the fields SENT are applied (``model_fields_set``); ``null`` or ``\"\"``\nclears ``assignee_user_id``, ``owner_team`` and ``jira_key``. No field takes\na URL: a Jira link is built by the server from the configured domain, and\n``extra=\"forbid\"`` makes a client-sent ``jira_url`` a 422, never stored.",
  "properties": {
    "assignee_user_id": {
      "anyOf": [
        {
          "maxLength": 64,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Assignee User Id"
    },
    "jira_key": {
      "anyOf": [
        {
          "maxLength": 50,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Jira Key"
    },
    "note": {
      "anyOf": [
        {
          "maxLength": 1000,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Note"
    },
    "owner_team": {
      "anyOf": [
        {
          "maxLength": 255,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Owner Team"
    },
    "resolution_status": {
      "anyOf": [
        {
          "maxLength": 20,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Resolution Status"
    }
  },
  "title": "DefectPatchRequest",
  "type": "object"
}
```

## DefectPatchResponse

```json
{
  "description": "The PATCH answer: the defect's list row as the server now reads it (the\n``GET /analytics/defects`` row shape), so the page replaces its row.",
  "properties": {
    "changed_fields": {
      "items": {
        "type": "string"
      },
      "title": "Changed Fields",
      "type": "array"
    },
    "defect": {
      "additionalProperties": true,
      "title": "Defect",
      "type": "object"
    },
    "jira": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/DefectJiraLinkResult"
        },
        {
          "type": "null"
        }
      ]
    },
    "write_back": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/DefectWriteBackResult"
        },
        {
          "type": "null"
        }
      ]
    }
  },
  "required": [
    "defect",
    "changed_fields"
  ],
  "title": "DefectPatchResponse",
  "type": "object"
}
```

## DefectPromotionRequest

```json
{
  "description": "User-editable fields submitted from the promotion modal.",
  "properties": {
    "component": {
      "default": "",
      "title": "Component",
      "type": "string"
    },
    "description": {
      "default": "",
      "title": "Description",
      "type": "string"
    },
    "labels": {
      "default": [],
      "items": {
        "type": "string"
      },
      "title": "Labels",
      "type": "array"
    },
    "owner_team": {
      "default": "",
      "title": "Owner Team",
      "type": "string"
    },
    "project_key": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Project Key"
    },
    "severity": {
      "default": "HIGH",
      "title": "Severity",
      "type": "string"
    },
    "title": {
      "title": "Title",
      "type": "string"
    }
  },
  "required": [
    "title"
  ],
  "title": "DefectPromotionRequest",
  "type": "object"
}
```

## DefectPromotionResponse

```json
{
  "properties": {
    "approval_status": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Approval Status"
    },
    "cluster_id": {
      "title": "Cluster Id",
      "type": "string"
    },
    "component": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Component"
    },
    "defect_id": {
      "title": "Defect Id",
      "type": "string"
    },
    "duplicate_defect_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Duplicate Defect Id"
    },
    "duplicate_detected": {
      "default": false,
      "title": "Duplicate Detected",
      "type": "boolean"
    },
    "jira_ticket": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Jira Ticket"
    },
    "jira_url": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Jira Url"
    },
    "owner_team": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Owner Team"
    },
    "policy_reasons": {
      "anyOf": [
        {
          "items": {
            "type": "string"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Policy Reasons"
    },
    "requires_approval": {
      "anyOf": [
        {
          "type": "boolean"
        },
        {
          "type": "null"
        }
      ],
      "title": "Requires Approval"
    },
    "severity": {
      "title": "Severity",
      "type": "string"
    },
    "title": {
      "title": "Title",
      "type": "string"
    }
  },
  "required": [
    "defect_id",
    "cluster_id",
    "severity",
    "title"
  ],
  "title": "DefectPromotionResponse",
  "type": "object"
}
```

## DefectWriteBackResult

```json
{
  "description": "The Jira status write-back after a status change. ``status``: \"sent\" | \"skipped\" | \"not_sent\" | \"failed\".",
  "properties": {
    "reason": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Reason"
    },
    "status": {
      "title": "Status",
      "type": "string"
    }
  },
  "required": [
    "status"
  ],
  "title": "DefectWriteBackResult",
  "type": "object"
}
```

## DeleteRunAcceptedResponse

```json
{
  "description": "202, not 204.\n\nA synchronous delete of a multi-GB prefix times out at the gateway, and\nbecause the cross-store order is Mongo -> MinIO -> Postgres the caller\nwould get a 504 with the artifacts already gone and the run still listed.\nPoll ``job_id`` on the deletion-jobs endpoint instead.",
  "properties": {
    "floor_applies": {
      "default": false,
      "title": "Floor Applies",
      "type": "boolean"
    },
    "floor_note": {
      "default": "Explicit deletions are not limited by the keep-last floor; it applies to the scheduled purge only.",
      "title": "Floor Note",
      "type": "string"
    },
    "job_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "description": "Deletion job to poll. NULL when the job record could not be written — the deletion still runs; only its bookkeeping failed.",
      "title": "Job Id"
    },
    "references_broken": {
      "items": {
        "$ref": "#/components/schemas/ReferenceBrokenRead"
      },
      "title": "References Broken",
      "type": "array"
    },
    "references_measured": {
      "default": true,
      "title": "References Measured",
      "type": "boolean"
    },
    "refused_prefixes": {
      "description": "Object prefixes NOT deleted because they fall outside the project's scope. Reported rather than silently skipped: storage that does not fall needs an explanation.",
      "items": {
        "type": "string"
      },
      "title": "Refused Prefixes",
      "type": "array"
    },
    "run_id": {
      "format": "uuid",
      "title": "Run Id",
      "type": "string"
    },
    "status": {
      "const": "accepted",
      "default": "accepted",
      "title": "Status",
      "type": "string"
    }
  },
  "required": [
    "run_id"
  ],
  "title": "DeleteRunAcceptedResponse",
  "type": "object"
}
```

## DeleteRunRequest

```json
{
  "description": "Payload for ``DELETE /api/v1/runs/{run_id}``.\n\n``confirm`` is a deliberate second step, not ceremony: this deletes across\nfive stores and is irreversible. ``reason`` is recorded on the deletion job\nand the tombstone, so \"why is this run gone\" has an answer later.",
  "properties": {
    "confirm": {
      "description": "Must be true. A DELETE without it is refused, not assumed.",
      "title": "Confirm",
      "type": "boolean"
    },
    "reason": {
      "maxLength": 500,
      "minLength": 3,
      "title": "Reason",
      "type": "string"
    }
  },
  "required": [
    "confirm",
    "reason"
  ],
  "title": "DeleteRunRequest",
  "type": "object"
}
```

## DeletedProjectStorageEntry

```json
{
  "properties": {
    "footprint": {
      "$ref": "#/components/schemas/ProjectStorageResponse"
    },
    "name": {
      "title": "Name",
      "type": "string"
    },
    "project_id": {
      "title": "Project Id",
      "type": "string"
    },
    "reachable_by_retention": {
      "title": "Reachable By Retention",
      "type": "boolean"
    }
  },
  "required": [
    "project_id",
    "name",
    "reachable_by_retention",
    "footprint"
  ],
  "title": "DeletedProjectStorageEntry",
  "type": "object"
}
```

## DeletedProjectsStorageResponse

```json
{
  "properties": {
    "computed_at": {
      "format": "date-time",
      "title": "Computed At",
      "type": "string"
    },
    "projects": {
      "items": {
        "$ref": "#/components/schemas/DeletedProjectStorageEntry"
      },
      "title": "Projects",
      "type": "array"
    },
    "projects_measured": {
      "title": "Projects Measured",
      "type": "integer"
    },
    "projects_total": {
      "title": "Projects Total",
      "type": "integer"
    },
    "total_bytes": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Total Bytes"
    },
    "total_is_estimate": {
      "default": false,
      "title": "Total Is Estimate",
      "type": "boolean"
    },
    "truncated": {
      "default": false,
      "title": "Truncated",
      "type": "boolean"
    },
    "unreachable_by_retention": {
      "default": 0,
      "title": "Unreachable By Retention",
      "type": "integer"
    }
  },
  "required": [
    "computed_at",
    "projects_total",
    "projects_measured"
  ],
  "title": "DeletedProjectsStorageResponse",
  "type": "object"
}
```

## DeletionAcknowledgeRequest

```json
{
  "description": "Confirms a collected archive was saved; the route answers ``DeletionJobResponse``.",
  "properties": {
    "manifest_sha256": {
      "maxLength": 64,
      "minLength": 64,
      "pattern": "^[0-9a-fA-F]{64}$",
      "title": "Manifest Sha256",
      "type": "string"
    }
  },
  "required": [
    "manifest_sha256"
  ],
  "title": "DeletionAcknowledgeRequest",
  "type": "object"
}
```

## DeletionExecuteAcceptedResponse

```json
{
  "properties": {
    "export_target": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Export Target"
    },
    "export_tier": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Export Tier"
    },
    "job_id": {
      "format": "uuid",
      "title": "Job Id",
      "type": "string"
    },
    "run_count": {
      "title": "Run Count",
      "type": "integer"
    },
    "status": {
      "const": "accepted",
      "default": "accepted",
      "title": "Status",
      "type": "string"
    }
  },
  "required": [
    "job_id",
    "run_count"
  ],
  "title": "DeletionExecuteAcceptedResponse",
  "type": "object"
}
```

## DeletionExecuteRequest

```json
{
  "description": "Execute takes a JOB ID, never a criteria body.\n\nAccepting criteria here would re-resolve them, which is the bug the freeze\nexists to prevent — the set executed would not be the set reviewed.",
  "properties": {
    "confirmation_name": {
      "description": "Must equal the project's name exactly. A typed confirmation, not a checkbox, because this is irreversible across five stores.",
      "maxLength": 255,
      "minLength": 1,
      "title": "Confirmation Name",
      "type": "string"
    },
    "export": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/ExportChoice"
        },
        {
          "type": "null"
        }
      ]
    },
    "job_id": {
      "format": "uuid",
      "title": "Job Id",
      "type": "string"
    }
  },
  "required": [
    "job_id",
    "confirmation_name"
  ],
  "title": "DeletionExecuteRequest",
  "type": "object"
}
```

## DeletionJobListResponse

```json
{
  "properties": {
    "jobs": {
      "items": {
        "$ref": "#/components/schemas/DeletionJobResponse"
      },
      "title": "Jobs",
      "type": "array"
    },
    "total": {
      "default": 0,
      "title": "Total",
      "type": "integer"
    }
  },
  "title": "DeletionJobListResponse",
  "type": "object"
}
```

## DeletionJobResponse

```json
{
  "description": "One deletion that ran (or is running).\n\n`bytes_reclaimed` is nullable and is NOT defaulted to 0 — \"reclaimed\nnothing\" and \"nobody measured\" are opposite findings, and this is the\nsurface that tells an operator whether a purge was worth running.",
  "properties": {
    "acknowledged_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Acknowledged At"
    },
    "bytes_reclaimed": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Bytes Reclaimed"
    },
    "collect_expires_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Collect Expires At"
    },
    "collected_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Collected At"
    },
    "counts": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Counts"
    },
    "criteria": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Criteria"
    },
    "error": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Error"
    },
    "export_bytes": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Export Bytes"
    },
    "export_manifest_sha256": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Export Manifest Sha256"
    },
    "export_summary": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Export Summary"
    },
    "export_target": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Export Target"
    },
    "export_tier": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Export Tier"
    },
    "export_verified_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Export Verified At"
    },
    "finished_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Finished At"
    },
    "holds_honoured": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Holds Honoured"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "job_kind": {
      "title": "Job Kind",
      "type": "string"
    },
    "project_id": {
      "format": "uuid",
      "title": "Project Id",
      "type": "string"
    },
    "references_broken": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "References Broken"
    },
    "requested_at": {
      "format": "date-time",
      "title": "Requested At",
      "type": "string"
    },
    "started_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Started At"
    },
    "status": {
      "title": "Status",
      "type": "string"
    }
  },
  "required": [
    "id",
    "project_id",
    "job_kind",
    "status",
    "requested_at"
  ],
  "title": "DeletionJobResponse",
  "type": "object"
}
```

## DeletionPreviewResponse

```json
{
  "description": "A frozen candidate set an ADMIN authorises from.\n\nThe ids are materialized and hashed here, and ``execute`` replays them.\nRe-resolving at execute time would delete a different set from the one\nthat was reviewed: criteria read columns other code rewrites while the job\nis queued (``TestRun.status`` by aggregate updates and live-session close,\n``primary_suite_name`` at session close).",
  "properties": {
    "blocked": {
      "description": "Runs that cannot be deleted and why — in flight, or cited by a compliance pack, release or decision report. Surfaced at preview so an ADMIN sees them before authorising, not after.",
      "items": {
        "additionalProperties": true,
        "type": "object"
      },
      "title": "Blocked",
      "type": "array"
    },
    "candidate_hash": {
      "title": "Candidate Hash",
      "type": "string"
    },
    "export_estimate": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/ExportEstimate"
        },
        {
          "type": "null"
        }
      ]
    },
    "floor_applies": {
      "default": false,
      "title": "Floor Applies",
      "type": "boolean"
    },
    "floor_note": {
      "default": "Explicit deletions are not limited by the keep-last floor; it applies to the scheduled purge only.",
      "title": "Floor Note",
      "type": "string"
    },
    "job_id": {
      "format": "uuid",
      "title": "Job Id",
      "type": "string"
    },
    "project_id": {
      "format": "uuid",
      "title": "Project Id",
      "type": "string"
    },
    "references_broken": {
      "items": {
        "$ref": "#/components/schemas/ReferenceBrokenRead"
      },
      "title": "References Broken",
      "type": "array"
    },
    "references_measured": {
      "default": true,
      "title": "References Measured",
      "type": "boolean"
    },
    "refused_prefixes": {
      "items": {
        "type": "string"
      },
      "title": "Refused Prefixes",
      "type": "array"
    },
    "run_count": {
      "title": "Run Count",
      "type": "integer"
    },
    "run_ids": {
      "items": {
        "format": "uuid",
        "type": "string"
      },
      "title": "Run Ids",
      "type": "array"
    },
    "truncated": {
      "default": false,
      "description": "The candidate set exceeded the reviewable bound and was cut. Reported rather than silently deleting the first N.",
      "title": "Truncated",
      "type": "boolean"
    }
  },
  "required": [
    "job_id",
    "project_id",
    "run_count",
    "run_ids",
    "candidate_hash"
  ],
  "title": "DeletionPreviewResponse",
  "type": "object"
}
```

## DigestContentResponse

```json
{
  "description": "Actionable digest content for a project or saved view scope.",
  "properties": {
    "action_items": {
      "default": [],
      "items": {
        "type": "string"
      },
      "title": "Action Items",
      "type": "array"
    },
    "avg_pass_rate": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Avg Pass Rate"
    },
    "changes_since": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Changes Since"
    },
    "delta": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Delta"
    },
    "flaky_test_count": {
      "default": 0,
      "title": "Flaky Test Count",
      "type": "integer"
    },
    "generated_at": {
      "title": "Generated At",
      "type": "string"
    },
    "is_zero_change": {
      "anyOf": [
        {
          "type": "boolean"
        },
        {
          "type": "null"
        }
      ],
      "title": "Is Zero Change"
    },
    "latest_run_total_tests": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Latest Run Total Tests"
    },
    "new_regressions": {
      "default": 0,
      "title": "New Regressions",
      "type": "integer"
    },
    "pass_rate_trend": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Pass Rate Trend"
    },
    "period": {
      "title": "Period",
      "type": "string"
    },
    "project_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Project Name"
    },
    "release_decisions": {
      "default": [],
      "items": {
        "additionalProperties": true,
        "type": "object"
      },
      "title": "Release Decisions",
      "type": "array"
    },
    "release_verdict": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/DigestReleaseVerdict"
        },
        {
          "type": "null"
        }
      ]
    },
    "scope": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/DigestScopeInfo"
        },
        {
          "type": "null"
        }
      ]
    },
    "top_blockers": {
      "default": [],
      "items": {
        "type": "string"
      },
      "title": "Top Blockers",
      "type": "array"
    },
    "top_clusters": {
      "default": [],
      "items": {
        "additionalProperties": true,
        "type": "object"
      },
      "title": "Top Clusters",
      "type": "array"
    },
    "total_runs": {
      "default": 0,
      "title": "Total Runs",
      "type": "integer"
    }
  },
  "required": [
    "period",
    "generated_at"
  ],
  "title": "DigestContentResponse",
  "type": "object"
}
```

## DigestReleaseVerdict

```json
{
  "properties": {
    "blocker_count": {
      "default": 0,
      "title": "Blocker Count",
      "type": "integer"
    },
    "decided_at": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Decided At"
    },
    "release_id": {
      "title": "Release Id",
      "type": "string"
    },
    "release_name": {
      "title": "Release Name",
      "type": "string"
    },
    "source": {
      "title": "Source",
      "type": "string"
    },
    "verdict": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Verdict"
    }
  },
  "required": [
    "release_id",
    "release_name",
    "source"
  ],
  "title": "DigestReleaseVerdict",
  "type": "object"
}
```

## DigestScopeInfo

```json
{
  "description": "What a digest was filtered by (W09 NTF-04); ``digest_scope_service.scope_info``.",
  "properties": {
    "dropped_release_count": {
      "default": 0,
      "title": "Dropped Release Count",
      "type": "integer"
    },
    "release_ids": {
      "default": [],
      "items": {
        "type": "string"
      },
      "title": "Release Ids",
      "type": "array"
    },
    "release_names": {
      "default": [],
      "items": {
        "type": "string"
      },
      "title": "Release Names",
      "type": "array"
    },
    "source": {
      "title": "Source",
      "type": "string"
    },
    "suites": {
      "default": [],
      "items": {
        "type": "string"
      },
      "title": "Suites",
      "type": "array"
    },
    "view_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "View Id"
    },
    "view_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "View Name"
    },
    "window_days": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Window Days"
    }
  },
  "required": [
    "source"
  ],
  "title": "DigestScopeInfo",
  "type": "object"
}
```

## DigestSide

```json
{
  "description": "Compact per-side summary for the Run Compare columns (core.digest_side).",
  "properties": {
    "analysed_tests": {
      "default": 0,
      "title": "Analysed Tests",
      "type": "integer"
    },
    "avg_confidence": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Avg Confidence"
    },
    "categories": {
      "additionalProperties": {
        "type": "integer"
      },
      "title": "Categories",
      "type": "object"
    },
    "composite_risk": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Composite Risk"
    },
    "decision_report_version": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Decision Report Version"
    },
    "deterministic": {
      "default": false,
      "title": "Deterministic",
      "type": "boolean"
    },
    "engine": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Engine"
    },
    "fallback_used": {
      "default": false,
      "title": "Fallback Used",
      "type": "boolean"
    },
    "generated_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Generated At"
    },
    "llm_model": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Llm Model"
    },
    "llm_provider": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Llm Provider"
    },
    "risk_score": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Risk Score"
    },
    "top_clusters": {
      "items": {
        "additionalProperties": true,
        "type": "object"
      },
      "title": "Top Clusters",
      "type": "array"
    },
    "verdict": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Verdict"
    }
  },
  "title": "DigestSide",
  "type": "object"
}
```

## DigestSubscriptionCreate

```json
{
  "properties": {
    "channel": {
      "default": "email",
      "pattern": "^(email|slack|teams)$",
      "title": "Channel",
      "type": "string"
    },
    "name": {
      "maxLength": 255,
      "minLength": 2,
      "title": "Name",
      "type": "string"
    },
    "project_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Project Id"
    },
    "report_attachment": {
      "default": false,
      "title": "Report Attachment",
      "type": "boolean"
    },
    "saved_view_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Saved View Id"
    },
    "schedule": {
      "default": "WEEKLY",
      "pattern": "^(DAILY|WEEKLY|WEEKLY_RETRO|PER_RUN|PER_RELEASE|PER_SUITE)$",
      "title": "Schedule",
      "type": "string"
    },
    "scope_type": {
      "anyOf": [
        {
          "pattern": "^(project|release|suite|global)$",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": "project",
      "title": "Scope Type"
    },
    "scope_value": {
      "anyOf": [
        {
          "maxLength": 255,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Scope Value"
    },
    "send_when_unchanged": {
      "default": true,
      "title": "Send When Unchanged",
      "type": "boolean"
    },
    "trigger_filter": {
      "anyOf": [
        {
          "pattern": "^(all|failed_only|degraded_only)$",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": "all",
      "title": "Trigger Filter"
    }
  },
  "required": [
    "name"
  ],
  "title": "DigestSubscriptionCreate",
  "type": "object"
}
```

## DigestSubscriptionResponse

```json
{
  "properties": {
    "channel": {
      "title": "Channel",
      "type": "string"
    },
    "created_at": {
      "format": "date-time",
      "title": "Created At",
      "type": "string"
    },
    "delivery_count": {
      "default": 0,
      "title": "Delivery Count",
      "type": "integer"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "is_active": {
      "title": "Is Active",
      "type": "boolean"
    },
    "is_paused": {
      "title": "Is Paused",
      "type": "boolean"
    },
    "last_delivered_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Last Delivered At"
    },
    "name": {
      "title": "Name",
      "type": "string"
    },
    "next_delivery_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Next Delivery At"
    },
    "project_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Project Id"
    },
    "report_attachment": {
      "default": false,
      "title": "Report Attachment",
      "type": "boolean"
    },
    "saved_view_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Saved View Id"
    },
    "saved_view_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Saved View Name"
    },
    "schedule": {
      "title": "Schedule",
      "type": "string"
    },
    "scope_label": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Scope Label"
    },
    "scope_problem": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Scope Problem"
    },
    "scope_type": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": "project",
      "title": "Scope Type"
    },
    "scope_value": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Scope Value"
    },
    "send_when_unchanged": {
      "default": true,
      "title": "Send When Unchanged",
      "type": "boolean"
    },
    "trigger_filter": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": "all",
      "title": "Trigger Filter"
    },
    "updated_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Updated At"
    },
    "user_id": {
      "format": "uuid",
      "title": "User Id",
      "type": "string"
    }
  },
  "required": [
    "id",
    "user_id",
    "name",
    "schedule",
    "channel",
    "is_active",
    "is_paused",
    "created_at"
  ],
  "title": "DigestSubscriptionResponse",
  "type": "object"
}
```

## DigestSubscriptionUpdate

```json
{
  "properties": {
    "channel": {
      "anyOf": [
        {
          "pattern": "^(email|slack|teams)$",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Channel"
    },
    "is_active": {
      "anyOf": [
        {
          "type": "boolean"
        },
        {
          "type": "null"
        }
      ],
      "title": "Is Active"
    },
    "is_paused": {
      "anyOf": [
        {
          "type": "boolean"
        },
        {
          "type": "null"
        }
      ],
      "title": "Is Paused"
    },
    "name": {
      "anyOf": [
        {
          "maxLength": 255,
          "minLength": 2,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Name"
    },
    "report_attachment": {
      "anyOf": [
        {
          "type": "boolean"
        },
        {
          "type": "null"
        }
      ],
      "title": "Report Attachment"
    },
    "saved_view_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Saved View Id"
    },
    "schedule": {
      "anyOf": [
        {
          "pattern": "^(DAILY|WEEKLY|WEEKLY_RETRO|PER_RUN|PER_RELEASE|PER_SUITE)$",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Schedule"
    },
    "scope_type": {
      "anyOf": [
        {
          "pattern": "^(project|release|suite|global)$",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Scope Type"
    },
    "scope_value": {
      "anyOf": [
        {
          "maxLength": 255,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Scope Value"
    },
    "send_when_unchanged": {
      "anyOf": [
        {
          "type": "boolean"
        },
        {
          "type": "null"
        }
      ],
      "title": "Send When Unchanged"
    },
    "trigger_filter": {
      "anyOf": [
        {
          "pattern": "^(all|failed_only|degraded_only)$",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Trigger Filter"
    }
  },
  "title": "DigestSubscriptionUpdate",
  "type": "object"
}
```

## DimensionScore

```json
{
  "properties": {
    "contribution": {
      "title": "Contribution",
      "type": "number"
    },
    "label": {
      "title": "Label",
      "type": "string"
    },
    "name": {
      "title": "Name",
      "type": "string"
    },
    "score": {
      "title": "Score",
      "type": "number"
    },
    "weight": {
      "title": "Weight",
      "type": "number"
    }
  },
  "required": [
    "name",
    "label",
    "score",
    "weight",
    "contribution"
  ],
  "title": "DimensionScore",
  "type": "object"
}
```

## DuplicateActionResponse

```json
{
  "description": "Result of a dismiss / merge action on a candidate pair.",
  "properties": {
    "candidate_id": {
      "format": "uuid",
      "title": "Candidate Id",
      "type": "string"
    },
    "deprecated_case_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Deprecated Case Id"
    },
    "status": {
      "enum": [
        "open",
        "merged",
        "dismissed"
      ],
      "title": "Status",
      "type": "string"
    }
  },
  "required": [
    "candidate_id",
    "status"
  ],
  "title": "DuplicateActionResponse",
  "type": "object"
}
```

## DuplicateCandidateListResponse

```json
{
  "description": "Paginated list of duplicate candidates for a project's review queue.",
  "properties": {
    "items": {
      "items": {
        "$ref": "#/components/schemas/DuplicateCandidateResponse"
      },
      "title": "Items",
      "type": "array"
    },
    "open_count": {
      "default": 0,
      "title": "Open Count",
      "type": "integer"
    },
    "total": {
      "default": 0,
      "title": "Total",
      "type": "integer"
    }
  },
  "title": "DuplicateCandidateListResponse",
  "type": "object"
}
```

## DuplicateCandidateResponse

```json
{
  "description": "One detected near-duplicate pair with its explainable score breakdown.",
  "properties": {
    "band": {
      "enum": [
        "exact",
        "strong",
        "possible"
      ],
      "title": "Band",
      "type": "string"
    },
    "case_a": {
      "$ref": "#/components/schemas/DuplicateCaseRef"
    },
    "case_b": {
      "$ref": "#/components/schemas/DuplicateCaseRef"
    },
    "component_scores": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Component Scores"
    },
    "detected_at": {
      "format": "date-time",
      "title": "Detected At",
      "type": "string"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "method": {
      "enum": [
        "fingerprint",
        "structural",
        "semantic"
      ],
      "title": "Method",
      "type": "string"
    },
    "project_id": {
      "format": "uuid",
      "title": "Project Id",
      "type": "string"
    },
    "reason": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Reason"
    },
    "score": {
      "title": "Score",
      "type": "number"
    },
    "status": {
      "enum": [
        "open",
        "merged",
        "dismissed"
      ],
      "title": "Status",
      "type": "string"
    }
  },
  "required": [
    "id",
    "project_id",
    "band",
    "score",
    "method",
    "status",
    "detected_at",
    "case_a",
    "case_b"
  ],
  "title": "DuplicateCandidateResponse",
  "type": "object"
}
```

## DuplicateCaseRef

```json
{
  "description": "Minimal reference to one ManagedTestCase in a duplicate pair.",
  "properties": {
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "status": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Status"
    },
    "suite_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Suite Name"
    },
    "title": {
      "title": "Title",
      "type": "string"
    }
  },
  "required": [
    "id",
    "title"
  ],
  "title": "DuplicateCaseRef",
  "type": "object"
}
```

## DuplicateDetectionRunResponse

```json
{
  "description": "Summary of a triggered detection sweep over a project's authored cases.",
  "properties": {
    "candidates_created": {
      "default": 0,
      "title": "Candidates Created",
      "type": "integer"
    },
    "candidates_total": {
      "default": 0,
      "title": "Candidates Total",
      "type": "integer"
    },
    "cases_scanned": {
      "default": 0,
      "title": "Cases Scanned",
      "type": "integer"
    },
    "note": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Note"
    },
    "project_id": {
      "format": "uuid",
      "title": "Project Id",
      "type": "string"
    },
    "sampled": {
      "default": false,
      "title": "Sampled",
      "type": "boolean"
    }
  },
  "required": [
    "project_id"
  ],
  "title": "DuplicateDetectionRunResponse",
  "type": "object"
}
```

## DuplicateMergeRequest

```json
{
  "description": "Non-destructive merge: flip the candidate to ``merged`` and optionally\nsoft-deprecate the losing case. NEVER deletes a case this phase.",
  "properties": {
    "candidate_id": {
      "format": "uuid",
      "title": "Candidate Id",
      "type": "string"
    },
    "deprecate_loser": {
      "default": false,
      "title": "Deprecate Loser",
      "type": "boolean"
    },
    "keep_case_id": {
      "format": "uuid",
      "title": "Keep Case Id",
      "type": "string"
    }
  },
  "required": [
    "candidate_id",
    "keep_case_id"
  ],
  "title": "DuplicateMergeRequest",
  "type": "object"
}
```

## DurationRegressionsResponse

```json
{
  "description": "A run's duration regressions. ``measured: false`` + ``reason`` when\nneither check could be made -- never an empty list read as \"no\nregressions\". ``stored: false`` = computed at read time for a run that\nwas not evaluated on ingest (nothing was written).",
  "properties": {
    "evaluated_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Evaluated At"
    },
    "measured": {
      "title": "Measured",
      "type": "boolean"
    },
    "notified": {
      "additionalProperties": true,
      "title": "Notified",
      "type": "object"
    },
    "reason": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Reason"
    },
    "run_id": {
      "format": "uuid",
      "title": "Run Id",
      "type": "string"
    },
    "schema_version": {
      "default": 1,
      "title": "Schema Version",
      "type": "integer"
    },
    "spike_count": {
      "default": 0,
      "title": "Spike Count",
      "type": "integer"
    },
    "spikes": {
      "items": {
        "$ref": "#/components/schemas/DurationSpikeResponse"
      },
      "title": "Spikes",
      "type": "array"
    },
    "stored": {
      "default": false,
      "title": "Stored",
      "type": "boolean"
    },
    "suite_check": {
      "additionalProperties": true,
      "title": "Suite Check",
      "type": "object"
    },
    "suite_regression_count": {
      "default": 0,
      "title": "Suite Regression Count",
      "type": "integer"
    },
    "suites": {
      "items": {
        "$ref": "#/components/schemas/SuiteDurationRegressionResponse"
      },
      "title": "Suites",
      "type": "array"
    },
    "test_check": {
      "additionalProperties": true,
      "title": "Test Check",
      "type": "object"
    }
  },
  "required": [
    "run_id",
    "measured"
  ],
  "title": "DurationRegressionsResponse",
  "type": "object"
}
```

## DurationSpikeResponse

```json
{
  "description": "A PASSED test slower than its Welford baseline (>= 3 sigma, >= 10 samples).",
  "properties": {
    "baseline_mean_ms": {
      "title": "Baseline Mean Ms",
      "type": "number"
    },
    "baseline_p95_ms": {
      "default": 0.0,
      "title": "Baseline P95 Ms",
      "type": "number"
    },
    "baseline_stddev_ms": {
      "default": 0.0,
      "title": "Baseline Stddev Ms",
      "type": "number"
    },
    "delta_ms": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Delta Ms"
    },
    "new": {
      "default": false,
      "title": "New",
      "type": "boolean"
    },
    "observed_ms": {
      "title": "Observed Ms",
      "type": "integer"
    },
    "previous_ms": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Previous Ms"
    },
    "ratio": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Ratio"
    },
    "sample_count": {
      "default": 0,
      "title": "Sample Count",
      "type": "integer"
    },
    "sigma": {
      "title": "Sigma",
      "type": "number"
    },
    "suite_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Suite Name"
    },
    "test_fingerprint": {
      "title": "Test Fingerprint",
      "type": "string"
    },
    "test_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Test Name"
    },
    "was_slow_before": {
      "default": false,
      "title": "Was Slow Before",
      "type": "boolean"
    }
  },
  "required": [
    "test_fingerprint",
    "observed_ms",
    "baseline_mean_ms",
    "sigma"
  ],
  "title": "DurationSpikeResponse",
  "type": "object"
}
```

## EmailAvailability

```json
{
  "properties": {
    "available": {
      "title": "Available",
      "type": "boolean"
    },
    "code": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Code"
    },
    "reason": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Reason"
    }
  },
  "required": [
    "available"
  ],
  "title": "EmailAvailability",
  "type": "object"
}
```

## EmailTrendsRequest

```json
{
  "properties": {
    "chart_ids": {
      "default": [],
      "items": {
        "type": "string"
      },
      "title": "Chart Ids",
      "type": "array"
    },
    "days": {
      "default": 30,
      "title": "Days",
      "type": "integer"
    },
    "project_id": {
      "title": "Project Id",
      "type": "string"
    },
    "recipient_email": {
      "anyOf": [
        {
          "format": "email",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Recipient Email"
    },
    "recipient_emails": {
      "items": {
        "format": "email",
        "type": "string"
      },
      "maxItems": 5,
      "title": "Recipient Emails",
      "type": "array"
    },
    "recipient_user_ids": {
      "items": {
        "format": "uuid",
        "type": "string"
      },
      "maxItems": 10,
      "title": "Recipient User Ids",
      "type": "array"
    },
    "release_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "items": {
            "type": "string"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Release Id"
    },
    "suite_name": {
      "anyOf": [
        {
          "items": {
            "type": "string"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Suite Name"
    }
  },
  "required": [
    "project_id"
  ],
  "title": "EmailTrendsRequest",
  "type": "object"
}
```

## EmailTrendsResponse

```json
{
  "properties": {
    "recipient": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Recipient"
    },
    "sent_to": {
      "title": "Sent To",
      "type": "integer"
    },
    "status": {
      "title": "Status",
      "type": "string"
    },
    "window_days": {
      "title": "Window Days",
      "type": "integer"
    }
  },
  "required": [
    "status",
    "sent_to",
    "window_days"
  ],
  "title": "EmailTrendsResponse",
  "type": "object"
}
```

## EmbeddingFamilyProgress

```json
{
  "properties": {
    "family": {
      "title": "Family",
      "type": "string"
    },
    "processed": {
      "default": 0,
      "title": "Processed",
      "type": "integer"
    },
    "state": {
      "title": "State",
      "type": "string"
    },
    "total": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Total"
    }
  },
  "required": [
    "family",
    "state"
  ],
  "title": "EmbeddingFamilyProgress",
  "type": "object"
}
```

## EmbeddingGenerationRead

```json
{
  "properties": {
    "key": {
      "title": "Key",
      "type": "string"
    },
    "model": {
      "title": "Model",
      "type": "string"
    },
    "prefix": {
      "title": "Prefix",
      "type": "string"
    },
    "provider": {
      "title": "Provider",
      "type": "string"
    },
    "tag": {
      "title": "Tag",
      "type": "string"
    }
  },
  "required": [
    "provider",
    "model",
    "prefix",
    "key",
    "tag"
  ],
  "title": "EmbeddingGenerationRead",
  "type": "object"
}
```

## EmbeddingIndexRead

```json
{
  "properties": {
    "active": {
      "$ref": "#/components/schemas/EmbeddingGenerationRead"
    },
    "configured": {
      "$ref": "#/components/schemas/EmbeddingGenerationRead"
    },
    "configured_note": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Configured Note"
    },
    "families": {
      "items": {
        "$ref": "#/components/schemas/EmbeddingFamilyProgress"
      },
      "title": "Families",
      "type": "array"
    },
    "in_sync": {
      "title": "In Sync",
      "type": "boolean"
    },
    "last_error": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Last Error"
    },
    "processed": {
      "default": 0,
      "title": "Processed",
      "type": "integer"
    },
    "requested_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Requested At"
    },
    "stalled": {
      "anyOf": [
        {
          "type": "boolean"
        },
        {
          "type": "null"
        }
      ],
      "title": "Stalled"
    },
    "status": {
      "title": "Status",
      "type": "string"
    },
    "switched_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Switched At"
    },
    "target": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/EmbeddingGenerationRead"
        },
        {
          "type": "null"
        }
      ]
    },
    "total": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Total"
    }
  },
  "required": [
    "active",
    "configured",
    "in_sync",
    "status"
  ],
  "title": "EmbeddingIndexRead",
  "type": "object"
}
```

## EnrichedTestCaseDetailResponse

```json
{
  "description": "Versioned additive contract for an executed test case.\n\nInherited flat fields intentionally remain present for existing clients.\nStructured sections provide stable extension points for richer metadata.",
  "properties": {
    "assigned_to_user_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Assigned To User Id"
    },
    "attachments": {
      "items": {
        "$ref": "#/components/schemas/TestAttachmentResponse"
      },
      "title": "Attachments",
      "type": "array"
    },
    "call_ms": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Call Ms"
    },
    "class_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Class Name"
    },
    "classification": {
      "$ref": "#/components/schemas/TestCaseClassificationSection"
    },
    "contract": {
      "const": "test-case-detail",
      "default": "test-case-detail",
      "title": "Contract",
      "type": "string"
    },
    "contract_version": {
      "const": "1",
      "default": "1",
      "title": "Contract Version",
      "type": "string"
    },
    "created_at": {
      "format": "date-time",
      "title": "Created At",
      "type": "string"
    },
    "definition": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Definition"
    },
    "duration_ms": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Duration Ms"
    },
    "epic": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Epic"
    },
    "error_message": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Error Message"
    },
    "execution": {
      "$ref": "#/components/schemas/TestCaseExecutionSection"
    },
    "extensions": {
      "additionalProperties": true,
      "title": "Extensions",
      "type": "object"
    },
    "failure_category": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Failure Category"
    },
    "failure_kind": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "description": "Derived failure-kind triad (US-9.1): product / test_code /\ninfrastructure / unknown. AI-derived from the stored\n``failure_category`` + the FAILED-vs-BROKEN status distinction —\nsee ``app/services/failure_kind.py`` for the mapping rationale.\nNone for non-failing rows (a kind only makes sense for failures).",
      "readOnly": true,
      "title": "Failure Kind"
    },
    "feature": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Feature"
    },
    "finished_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Finished At"
    },
    "full_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Full Name"
    },
    "has_attachments": {
      "default": false,
      "title": "Has Attachments",
      "type": "boolean"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "identity": {
      "$ref": "#/components/schemas/TestCaseIdentitySection"
    },
    "links": {
      "items": {
        "additionalProperties": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ]
        },
        "type": "object"
      },
      "title": "Links",
      "type": "array"
    },
    "minio_s3_prefix": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Minio S3 Prefix"
    },
    "owner": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Owner"
    },
    "package_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Package Name"
    },
    "provenance": {
      "$ref": "#/components/schemas/TestCaseProvenanceSection"
    },
    "schema_version": {
      "default": 1,
      "title": "Schema Version",
      "type": "integer"
    },
    "setup_ms": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Setup Ms"
    },
    "severity": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Severity"
    },
    "started_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Started At"
    },
    "status": {
      "$ref": "#/components/schemas/TestStatus"
    },
    "step_count": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Step Count"
    },
    "steps": {
      "items": {
        "$ref": "#/components/schemas/TestStepResponse"
      },
      "title": "Steps",
      "type": "array"
    },
    "steps_present": {
      "default": false,
      "title": "Steps Present",
      "type": "boolean"
    },
    "story": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Story"
    },
    "suite_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Suite Name"
    },
    "tags": {
      "anyOf": [
        {
          "items": {
            "type": "string"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Tags"
    },
    "teardown_ms": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Teardown Ms"
    },
    "test_name": {
      "title": "Test Name",
      "type": "string"
    },
    "test_run_id": {
      "format": "uuid",
      "title": "Test Run Id",
      "type": "string"
    },
    "timing_source": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Timing Source"
    },
    "worker_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Worker Id"
    }
  },
  "required": [
    "id",
    "test_run_id",
    "test_name",
    "status",
    "created_at",
    "identity",
    "classification",
    "execution",
    "provenance",
    "failure_kind"
  ],
  "title": "EnrichedTestCaseDetailResponse",
  "type": "object"
}
```

## EscalationConfig

```json
{
  "additionalProperties": false,
  "properties": {
    "max_escalations": {
      "default": 1,
      "maximum": 3.0,
      "minimum": 0.0,
      "title": "Max Escalations",
      "type": "integer"
    },
    "on_confidence_below": {
      "default": 70,
      "maximum": 100.0,
      "minimum": 0.0,
      "title": "On Confidence Below",
      "type": "integer"
    },
    "on_validation_failure": {
      "default": true,
      "title": "On Validation Failure",
      "type": "boolean"
    }
  },
  "title": "EscalationConfig",
  "type": "object"
}
```

## EvalCandidateIn

```json
{
  "additionalProperties": false,
  "properties": {
    "model": {
      "anyOf": [
        {
          "maxLength": 200,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Model"
    },
    "prompt_version": {
      "anyOf": [
        {
          "maxLength": 400,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Prompt Version"
    },
    "tier": {
      "maxLength": 20,
      "minLength": 2,
      "title": "Tier",
      "type": "string"
    }
  },
  "required": [
    "tier"
  ],
  "title": "EvalCandidateIn",
  "type": "object"
}
```

## EvalCandidateResolved

```json
{
  "description": "What the run ACTUALLY replays (stored in ``ai_eval_runs.candidate``).",
  "properties": {
    "model": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Model"
    },
    "offline_mode": {
      "title": "Offline Mode",
      "type": "boolean"
    },
    "prompt_version": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Prompt Version"
    },
    "provider": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Provider"
    },
    "requested": {
      "$ref": "#/components/schemas/EvalCandidateIn"
    },
    "tier": {
      "title": "Tier",
      "type": "string"
    }
  },
  "required": [
    "tier",
    "offline_mode",
    "requested"
  ],
  "title": "EvalCandidateResolved",
  "type": "object"
}
```

## EvalMetric

```json
{
  "properties": {
    "kind": {
      "title": "Kind",
      "type": "string"
    },
    "measured": {
      "title": "Measured",
      "type": "boolean"
    },
    "n": {
      "default": 0,
      "title": "N",
      "type": "integer"
    },
    "name": {
      "title": "Name",
      "type": "string"
    },
    "value": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Value"
    }
  },
  "required": [
    "name",
    "measured",
    "kind"
  ],
  "title": "EvalMetric",
  "type": "object"
}
```

## EvalProvenanceHealthResponse

```json
{
  "properties": {
    "has_unresolvable_checksums": {
      "title": "Has Unresolvable Checksums",
      "type": "boolean"
    },
    "missing_checksum_count": {
      "title": "Missing Checksum Count",
      "type": "integer"
    },
    "resolved_runs": {
      "title": "Resolved Runs",
      "type": "integer"
    },
    "stamped_runs": {
      "title": "Stamped Runs",
      "type": "integer"
    },
    "total_runs": {
      "title": "Total Runs",
      "type": "integer"
    },
    "unresolvable_checksums": {
      "items": {
        "type": "string"
      },
      "title": "Unresolvable Checksums",
      "type": "array"
    },
    "unresolvable_run_count": {
      "title": "Unresolvable Run Count",
      "type": "integer"
    },
    "window_days": {
      "title": "Window Days",
      "type": "integer"
    },
    "window_end": {
      "format": "date-time",
      "title": "Window End",
      "type": "string"
    },
    "window_start": {
      "format": "date-time",
      "title": "Window Start",
      "type": "string"
    }
  },
  "required": [
    "window_days",
    "window_start",
    "window_end",
    "total_runs",
    "stamped_runs",
    "resolved_runs",
    "missing_checksum_count",
    "unresolvable_run_count",
    "unresolvable_checksums",
    "has_unresolvable_checksums"
  ],
  "title": "EvalProvenanceHealthResponse",
  "type": "object"
}
```

## EvidenceGapItem

```json
{
  "properties": {
    "canonical_status": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Canonical Status"
    },
    "canonical_test_case_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Canonical Test Case Id"
    },
    "deleted_observed_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Deleted Observed At"
    },
    "flaky_confidence": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Flaky Confidence"
    },
    "flaky_score": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Flaky Score"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "last_executed_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Last Executed At"
    },
    "owner_state": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Owner State"
    },
    "project_id": {
      "format": "uuid",
      "title": "Project Id",
      "type": "string"
    },
    "status": {
      "title": "Status",
      "type": "string"
    },
    "title": {
      "title": "Title",
      "type": "string"
    }
  },
  "required": [
    "id",
    "project_id",
    "title",
    "status"
  ],
  "title": "EvidenceGapItem",
  "type": "object"
}
```

## EvidenceGapListResponse

```json
{
  "properties": {
    "items": {
      "items": {
        "$ref": "#/components/schemas/EvidenceGapItem"
      },
      "title": "Items",
      "type": "array"
    },
    "total": {
      "title": "Total",
      "type": "integer"
    }
  },
  "required": [
    "items",
    "total"
  ],
  "title": "EvidenceGapListResponse",
  "type": "object"
}
```

## EvidenceReference

```json
{
  "properties": {
    "excerpt": {
      "default": "",
      "title": "Excerpt",
      "type": "string"
    },
    "reference_id": {
      "default": "",
      "title": "Reference Id",
      "type": "string"
    },
    "source": {
      "title": "Source",
      "type": "string"
    }
  },
  "required": [
    "source"
  ],
  "title": "EvidenceReference",
  "type": "object"
}
```

## ExecuteTestPlanItemRequest

```json
{
  "properties": {
    "actual_duration_minutes": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Actual Duration Minutes"
    },
    "execution_notes": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Execution Notes"
    },
    "execution_status": {
      "enum": [
        "passed",
        "failed",
        "blocked",
        "skipped"
      ],
      "title": "Execution Status",
      "type": "string"
    }
  },
  "required": [
    "execution_status"
  ],
  "title": "ExecuteTestPlanItemRequest",
  "type": "object"
}
```

## ExecutionDayPoint

```json
{
  "properties": {
    "day": {
      "title": "Day",
      "type": "string"
    },
    "runs": {
      "default": 0,
      "title": "Runs",
      "type": "integer"
    },
    "summed_ms": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Summed Ms"
    },
    "summed_runs": {
      "default": 0,
      "title": "Summed Runs",
      "type": "integer"
    },
    "wall_clock_ms": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Wall Clock Ms"
    },
    "wall_runs": {
      "default": 0,
      "title": "Wall Runs",
      "type": "integer"
    }
  },
  "required": [
    "day"
  ],
  "title": "ExecutionDayPoint",
  "type": "object"
}
```

## ExecutionDurationHistogram

```json
{
  "description": "TRND-09: the window's test executions by duration, over fixed\nlog-spaced bucket edges (every bucket present, zero-filled).",
  "properties": {
    "buckets": {
      "items": {
        "$ref": "#/components/schemas/ExecutionHistogramBucket"
      },
      "title": "Buckets",
      "type": "array"
    },
    "measured": {
      "default": false,
      "title": "Measured",
      "type": "boolean"
    },
    "n": {
      "default": 0,
      "title": "N",
      "type": "integer"
    },
    "p50_ms": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "P50 Ms"
    },
    "p95_ms": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "P95 Ms"
    },
    "reason": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Reason"
    }
  },
  "title": "ExecutionDurationHistogram",
  "type": "object"
}
```

## ExecutionHistogramBucket

```json
{
  "description": "One duration bucket: ``lower_ms <= duration < upper_ms``.",
  "properties": {
    "count": {
      "default": 0,
      "title": "Count",
      "type": "integer"
    },
    "lower_ms": {
      "title": "Lower Ms",
      "type": "integer"
    },
    "upper_ms": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Upper Ms"
    }
  },
  "required": [
    "lower_ms"
  ],
  "title": "ExecutionHistogramBucket",
  "type": "object"
}
```

## ExecutionMetricsResponse

```json
{
  "description": "GET /api/v1/analytics/execution-metrics.",
  "properties": {
    "branch": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Branch"
    },
    "days": {
      "title": "Days",
      "type": "integer"
    },
    "duration_histogram": {
      "$ref": "#/components/schemas/ExecutionDurationHistogram"
    },
    "measured": {
      "default": false,
      "title": "Measured",
      "type": "boolean"
    },
    "meta": {
      "additionalProperties": true,
      "title": "Meta",
      "type": "object"
    },
    "per_day": {
      "$ref": "#/components/schemas/ExecutionPerDay"
    },
    "previous_window_start": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Previous Window Start"
    },
    "project_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Project Id"
    },
    "reason": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Reason"
    },
    "regressions": {
      "$ref": "#/components/schemas/ExecutionRegressions"
    },
    "slowest_tests": {
      "$ref": "#/components/schemas/ExecutionSlowTests"
    },
    "suites": {
      "$ref": "#/components/schemas/ExecutionSuites"
    },
    "totals": {
      "$ref": "#/components/schemas/ExecutionTotals"
    },
    "window_end": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Window End"
    },
    "window_start": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Window Start"
    }
  },
  "required": [
    "days",
    "suites",
    "regressions",
    "slowest_tests"
  ],
  "title": "ExecutionMetricsResponse",
  "type": "object"
}
```

## ExecutionPerDay

```json
{
  "properties": {
    "grain": {
      "default": "run",
      "title": "Grain",
      "type": "string"
    },
    "points": {
      "items": {
        "$ref": "#/components/schemas/ExecutionDayPoint"
      },
      "title": "Points",
      "type": "array"
    }
  },
  "title": "ExecutionPerDay",
  "type": "object"
}
```

## ExecutionPercentiles

```json
{
  "description": "p50 / p95 of one figure over a set of executions.",
  "properties": {
    "measured": {
      "default": false,
      "title": "Measured",
      "type": "boolean"
    },
    "n": {
      "default": 0,
      "title": "N",
      "type": "integer"
    },
    "p50_ms": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "P50 Ms"
    },
    "p95_ms": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "P95 Ms"
    },
    "reason": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Reason"
    }
  },
  "title": "ExecutionPercentiles",
  "type": "object"
}
```

## ExecutionRegressionRule

```json
{
  "properties": {
    "baseline_runs": {
      "title": "Baseline Runs",
      "type": "integer"
    },
    "min_baseline_runs": {
      "title": "Min Baseline Runs",
      "type": "integer"
    },
    "ratio": {
      "title": "Ratio",
      "type": "number"
    },
    "recent_runs": {
      "title": "Recent Runs",
      "type": "integer"
    },
    "same_branch": {
      "default": true,
      "title": "Same Branch",
      "type": "boolean"
    },
    "test_min_samples": {
      "title": "Test Min Samples",
      "type": "integer"
    },
    "test_ratio": {
      "title": "Test Ratio",
      "type": "number"
    }
  },
  "required": [
    "recent_runs",
    "baseline_runs",
    "min_baseline_runs",
    "ratio",
    "test_min_samples",
    "test_ratio"
  ],
  "title": "ExecutionRegressionRule",
  "type": "object"
}
```

## ExecutionRegressions

```json
{
  "properties": {
    "rule": {
      "$ref": "#/components/schemas/ExecutionRegressionRule"
    },
    "suites": {
      "$ref": "#/components/schemas/ExecutionSuiteRegressions"
    },
    "tests": {
      "$ref": "#/components/schemas/ExecutionTestRegressions"
    }
  },
  "required": [
    "rule",
    "suites",
    "tests"
  ],
  "title": "ExecutionRegressions",
  "type": "object"
}
```

## ExecutionSlowTest

```json
{
  "properties": {
    "max_ms": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Max Ms"
    },
    "n": {
      "default": 0,
      "title": "N",
      "type": "integer"
    },
    "p50_ms": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "P50 Ms"
    },
    "p95_ms": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "P95 Ms"
    },
    "suite_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Suite Name"
    },
    "test_fingerprint": {
      "title": "Test Fingerprint",
      "type": "string"
    },
    "test_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Test Name"
    }
  },
  "required": [
    "test_fingerprint"
  ],
  "title": "ExecutionSlowTest",
  "type": "object"
}
```

## ExecutionSlowTests

```json
{
  "properties": {
    "items": {
      "items": {
        "$ref": "#/components/schemas/ExecutionSlowTest"
      },
      "title": "Items",
      "type": "array"
    },
    "limit": {
      "title": "Limit",
      "type": "integer"
    },
    "measured": {
      "default": false,
      "title": "Measured",
      "type": "boolean"
    },
    "reason": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Reason"
    }
  },
  "required": [
    "limit"
  ],
  "title": "ExecutionSlowTests",
  "type": "object"
}
```

## ExecutionSuiteRegression

```json
{
  "properties": {
    "baseline_median_ms": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Baseline Median Ms"
    },
    "baseline_runs": {
      "default": 0,
      "title": "Baseline Runs",
      "type": "integer"
    },
    "branch": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Branch"
    },
    "latest_run_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Latest Run At"
    },
    "latest_run_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Latest Run Id"
    },
    "metric": {
      "title": "Metric",
      "type": "string"
    },
    "observed_ms": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Observed Ms"
    },
    "ratio": {
      "title": "Ratio",
      "type": "number"
    },
    "recent_median_ms": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Recent Median Ms"
    },
    "recent_runs": {
      "default": 0,
      "title": "Recent Runs",
      "type": "integer"
    },
    "suite_key": {
      "title": "Suite Key",
      "type": "string"
    },
    "suite_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Suite Name"
    },
    "was_slow_before": {
      "default": false,
      "title": "Was Slow Before",
      "type": "boolean"
    }
  },
  "required": [
    "suite_key",
    "metric",
    "ratio"
  ],
  "title": "ExecutionSuiteRegression",
  "type": "object"
}
```

## ExecutionSuiteRegressions

```json
{
  "properties": {
    "considered": {
      "default": 0,
      "title": "Considered",
      "type": "integer"
    },
    "evaluated": {
      "default": 0,
      "title": "Evaluated",
      "type": "integer"
    },
    "items": {
      "items": {
        "$ref": "#/components/schemas/ExecutionSuiteRegression"
      },
      "title": "Items",
      "type": "array"
    },
    "measured": {
      "default": false,
      "title": "Measured",
      "type": "boolean"
    },
    "reason": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Reason"
    },
    "total": {
      "default": 0,
      "title": "Total",
      "type": "integer"
    }
  },
  "title": "ExecutionSuiteRegressions",
  "type": "object"
}
```

## ExecutionSuiteRow

```json
{
  "properties": {
    "current": {
      "$ref": "#/components/schemas/ExecutionSuiteWindow"
    },
    "previous": {
      "$ref": "#/components/schemas/ExecutionSuiteWindow"
    },
    "suite_key": {
      "title": "Suite Key",
      "type": "string"
    },
    "suite_name": {
      "title": "Suite Name",
      "type": "string"
    },
    "summed_p50_change_pct": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Summed P50 Change Pct"
    },
    "summed_p95_change_pct": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Summed P95 Change Pct"
    },
    "wall_p50_change_pct": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Wall P50 Change Pct"
    },
    "wall_p95_change_pct": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Wall P95 Change Pct"
    }
  },
  "required": [
    "suite_key",
    "suite_name",
    "current",
    "previous"
  ],
  "title": "ExecutionSuiteRow",
  "type": "object"
}
```

## ExecutionSuiteWindow

```json
{
  "properties": {
    "runs": {
      "default": 0,
      "title": "Runs",
      "type": "integer"
    },
    "summed": {
      "$ref": "#/components/schemas/ExecutionPercentiles"
    },
    "wall": {
      "$ref": "#/components/schemas/ExecutionPercentiles"
    }
  },
  "title": "ExecutionSuiteWindow",
  "type": "object"
}
```

## ExecutionSuites

```json
{
  "properties": {
    "items": {
      "items": {
        "$ref": "#/components/schemas/ExecutionSuiteRow"
      },
      "title": "Items",
      "type": "array"
    },
    "limit": {
      "title": "Limit",
      "type": "integer"
    },
    "total": {
      "default": 0,
      "title": "Total",
      "type": "integer"
    },
    "truncated": {
      "default": false,
      "title": "Truncated",
      "type": "boolean"
    }
  },
  "required": [
    "limit"
  ],
  "title": "ExecutionSuites",
  "type": "object"
}
```

## ExecutionTestRegression

```json
{
  "properties": {
    "baseline_mean_ms": {
      "title": "Baseline Mean Ms",
      "type": "number"
    },
    "baseline_p95_ms": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Baseline P95 Ms"
    },
    "ratio": {
      "title": "Ratio",
      "type": "number"
    },
    "sample_count": {
      "default": 0,
      "title": "Sample Count",
      "type": "integer"
    },
    "suite_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Suite Name"
    },
    "test_fingerprint": {
      "title": "Test Fingerprint",
      "type": "string"
    },
    "test_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Test Name"
    },
    "window_n": {
      "default": 0,
      "title": "Window N",
      "type": "integer"
    },
    "window_p50_ms": {
      "title": "Window P50 Ms",
      "type": "number"
    }
  },
  "required": [
    "test_fingerprint",
    "baseline_mean_ms",
    "window_p50_ms",
    "ratio"
  ],
  "title": "ExecutionTestRegression",
  "type": "object"
}
```

## ExecutionTestRegressions

```json
{
  "properties": {
    "considered": {
      "default": 0,
      "title": "Considered",
      "type": "integer"
    },
    "items": {
      "items": {
        "$ref": "#/components/schemas/ExecutionTestRegression"
      },
      "title": "Items",
      "type": "array"
    },
    "measured": {
      "default": false,
      "title": "Measured",
      "type": "boolean"
    },
    "reason": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Reason"
    },
    "total": {
      "default": 0,
      "title": "Total",
      "type": "integer"
    }
  },
  "title": "ExecutionTestRegressions",
  "type": "object"
}
```

## ExecutionTotals

```json
{
  "properties": {
    "parallelism": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Parallelism"
    },
    "runs": {
      "default": 0,
      "title": "Runs",
      "type": "integer"
    },
    "summed_ms": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Summed Ms"
    },
    "summed_runs": {
      "default": 0,
      "title": "Summed Runs",
      "type": "integer"
    },
    "wall_clock_ms": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Wall Clock Ms"
    },
    "wall_runs": {
      "default": 0,
      "title": "Wall Runs",
      "type": "integer"
    }
  },
  "title": "ExecutionTotals",
  "type": "object"
}
```

## ExportChoice

```json
{
  "description": "``DeletionExecuteRequest.export``. Validated in the service (fixed 422 sentence), not by ``Literal``.",
  "properties": {
    "target": {
      "title": "Target",
      "type": "string"
    },
    "tier": {
      "title": "Tier",
      "type": "string"
    }
  },
  "required": [
    "tier",
    "target"
  ],
  "title": "ExportChoice",
  "type": "object"
}
```

## ExportEstimate

```json
{
  "description": "What archiving the DELETABLE runs of a criteria preview would cost and reclaim.",
  "properties": {
    "collect_ttl_hours": {
      "title": "Collect Ttl Hours",
      "type": "integer"
    },
    "exportable": {
      "title": "Exportable",
      "type": "boolean"
    },
    "max_bytes": {
      "title": "Max Bytes",
      "type": "integer"
    },
    "note": {
      "default": "Archive, not a backup — it cannot be re-imported.",
      "title": "Note",
      "type": "string"
    },
    "reclaim_bytes": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Reclaim Bytes"
    },
    "reclaim_measured": {
      "default": false,
      "title": "Reclaim Measured",
      "type": "boolean"
    },
    "reclaim_mongo_bytes": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Reclaim Mongo Bytes"
    },
    "reclaim_object_bytes": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Reclaim Object Bytes"
    },
    "refusal": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Refusal"
    },
    "run_count": {
      "title": "Run Count",
      "type": "integer"
    },
    "tiers": {
      "items": {
        "$ref": "#/components/schemas/ExportTierEstimate"
      },
      "title": "Tiers",
      "type": "array"
    }
  },
  "required": [
    "run_count",
    "exportable",
    "max_bytes",
    "collect_ttl_hours"
  ],
  "title": "ExportEstimate",
  "type": "object"
}
```

## ExportTierEstimate

```json
{
  "description": "One tier's archive size estimate. ``None`` = not measured, never 0.",
  "properties": {
    "archive_bytes": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Archive Bytes"
    },
    "measured": {
      "default": false,
      "title": "Measured",
      "type": "boolean"
    },
    "over_cap": {
      "default": false,
      "title": "Over Cap",
      "type": "boolean"
    },
    "tier": {
      "title": "Tier",
      "type": "string"
    }
  },
  "required": [
    "tier"
  ],
  "title": "ExportTierEstimate",
  "type": "object"
}
```

## FailureCategory

```json
{
  "enum": [
    "PRODUCT_BUG",
    "INFRASTRUCTURE",
    "TEST_DATA",
    "AUTOMATION_DEFECT",
    "FLAKY",
    "UNKNOWN"
  ],
  "title": "FailureCategory",
  "type": "string"
}
```

## FallbackChainEntry

```json
{
  "description": "One analysis tier and whether it can actually run right now.",
  "properties": {
    "available": {
      "title": "Available",
      "type": "boolean"
    },
    "mode": {
      "title": "Mode",
      "type": "string"
    },
    "reason": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Reason"
    },
    "reason_code": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Reason Code"
    }
  },
  "required": [
    "mode",
    "available"
  ],
  "title": "FallbackChainEntry",
  "type": "object"
}
```

## FeatureFlagCreate

```json
{
  "properties": {
    "description": {
      "anyOf": [
        {
          "maxLength": 2000,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Description"
    },
    "enabled_global": {
      "default": false,
      "title": "Enabled Global",
      "type": "boolean"
    },
    "enabled_projects": {
      "anyOf": [
        {
          "items": {
            "format": "uuid",
            "type": "string"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Enabled Projects"
    },
    "enabled_roles": {
      "anyOf": [
        {
          "items": {
            "type": "string"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Enabled Roles"
    },
    "key": {
      "maxLength": 80,
      "minLength": 2,
      "pattern": "^[a-z][a-z0-9_]*$",
      "title": "Key",
      "type": "string"
    },
    "reason": {
      "anyOf": [
        {
          "maxLength": 500,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Reason"
    },
    "rollout_percent": {
      "default": 100,
      "maximum": 100.0,
      "minimum": 0.0,
      "title": "Rollout Percent",
      "type": "integer"
    }
  },
  "required": [
    "key"
  ],
  "title": "FeatureFlagCreate",
  "type": "object"
}
```

## FeatureFlagHistoryEntry

```json
{
  "description": "One audit row of a flag (W17 ADM-07): what changed, who, when and why. Field names only.",
  "properties": {
    "action": {
      "title": "Action",
      "type": "string"
    },
    "actor_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Actor Name"
    },
    "changed_fields": {
      "items": {
        "type": "string"
      },
      "title": "Changed Fields",
      "type": "array"
    },
    "created_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Created At"
    },
    "reason": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Reason"
    }
  },
  "required": [
    "action"
  ],
  "title": "FeatureFlagHistoryEntry",
  "type": "object"
}
```

## FeatureFlagResponse

```json
{
  "properties": {
    "created_at": {
      "format": "date-time",
      "title": "Created At",
      "type": "string"
    },
    "description": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Description"
    },
    "enabled_global": {
      "title": "Enabled Global",
      "type": "boolean"
    },
    "enabled_projects": {
      "anyOf": [
        {
          "items": {
            "format": "uuid",
            "type": "string"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Enabled Projects"
    },
    "enabled_roles": {
      "anyOf": [
        {
          "items": {
            "type": "string"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Enabled Roles"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "key": {
      "title": "Key",
      "type": "string"
    },
    "last_change_reason": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Last Change Reason"
    },
    "rollout_percent": {
      "title": "Rollout Percent",
      "type": "integer"
    },
    "updated_at": {
      "format": "date-time",
      "title": "Updated At",
      "type": "string"
    },
    "updated_by_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Updated By Name"
    },
    "updated_by_user_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Updated By User Id"
    }
  },
  "required": [
    "id",
    "key",
    "enabled_global",
    "rollout_percent",
    "created_at",
    "updated_at"
  ],
  "title": "FeatureFlagResponse",
  "type": "object"
}
```

## FeedbackRating

```json
{
  "enum": [
    "correct",
    "incorrect",
    "partially_correct"
  ],
  "title": "FeedbackRating",
  "type": "string"
}
```

## FeedbackRequest

```json
{
  "properties": {
    "comment": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Comment"
    },
    "corrected_category": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/FailureCategory"
        },
        {
          "type": "null"
        }
      ]
    },
    "corrected_root_cause": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Corrected Root Cause"
    },
    "rating": {
      "$ref": "#/components/schemas/FeedbackRating"
    }
  },
  "required": [
    "rating"
  ],
  "title": "FeedbackRequest",
  "type": "object"
}
```

## FirstTimeResetRequest

```json
{
  "description": "Used for forced password reset on first login — no current password required.",
  "properties": {
    "confirm_password": {
      "maxLength": 128,
      "minLength": 8,
      "title": "Confirm Password",
      "type": "string"
    },
    "new_password": {
      "maxLength": 128,
      "minLength": 8,
      "title": "New Password",
      "type": "string"
    }
  },
  "required": [
    "new_password",
    "confirm_password"
  ],
  "title": "FirstTimeResetRequest",
  "type": "object"
}
```

## FixOutcomeRequest

```json
{
  "description": "Outcome of a fix informed by TestLookup's diagnosis.\n\n``outcome`` is a closed vocabulary validated here (the values map onto\nFeedbackRating in the service); ``fingerprint`` is the stable test\nidentity (sha256(class::test)[:16]) the analytics surface uses.",
  "properties": {
    "comment": {
      "anyOf": [
        {
          "maxLength": 4000,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Comment"
    },
    "fingerprint": {
      "maxLength": 64,
      "minLength": 1,
      "title": "Fingerprint",
      "type": "string"
    },
    "outcome": {
      "enum": [
        "fixed",
        "not_fixed",
        "reverted"
      ],
      "title": "Outcome",
      "type": "string"
    },
    "reference": {
      "anyOf": [
        {
          "maxLength": 500,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Reference"
    }
  },
  "required": [
    "fingerprint",
    "outcome"
  ],
  "title": "FixOutcomeRequest",
  "type": "object"
}
```

## FixerConfigExtension

```json
{
  "additionalProperties": false,
  "properties": {
    "budgets": {
      "$ref": "#/components/schemas/FixerPolicyBudgets"
    },
    "runner": {
      "$ref": "#/components/schemas/FixerRunnerExtension"
    },
    "schedule": {
      "default": "off",
      "title": "Schedule",
      "type": "string"
    },
    "test_globs": {
      "items": {
        "type": "string"
      },
      "title": "Test Globs",
      "type": "array"
    }
  },
  "title": "FixerConfigExtension",
  "type": "object"
}
```

## FixerPolicyBudgets

```json
{
  "additionalProperties": false,
  "properties": {
    "max_attempts_per_test": {
      "default": 2,
      "maximum": 100.0,
      "minimum": 0.0,
      "title": "Max Attempts Per Test",
      "type": "integer"
    },
    "max_concurrent_open_prs": {
      "default": 2,
      "maximum": 100.0,
      "minimum": 0.0,
      "title": "Max Concurrent Open Prs",
      "type": "integer"
    },
    "max_tests_per_run": {
      "default": 3,
      "maximum": 1000.0,
      "minimum": 0.0,
      "title": "Max Tests Per Run",
      "type": "integer"
    },
    "validation_reruns": {
      "default": 5,
      "maximum": 100.0,
      "minimum": 1.0,
      "title": "Validation Reruns",
      "type": "integer"
    }
  },
  "title": "FixerPolicyBudgets",
  "type": "object"
}
```

## FixerRunnerExtension

```json
{
  "additionalProperties": false,
  "properties": {
    "command_template": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Command Template"
    },
    "runner_image": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Runner Image"
    },
    "type": {
      "default": "none",
      "title": "Type",
      "type": "string"
    },
    "workflow_ref": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Workflow Ref"
    }
  },
  "title": "FixerRunnerExtension",
  "type": "object"
}
```

## FlakyCoachEntry

```json
{
  "description": "Single flaky test with coaching recommendation.",
  "properties": {
    "error_signature_diversity": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Error Signature Diversity"
    },
    "failed_runs": {
      "default": 0,
      "title": "Failed Runs",
      "type": "integer"
    },
    "failing_step": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Failing Step"
    },
    "failing_step_detail": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Failing Step Detail"
    },
    "failure_rate": {
      "title": "Failure Rate",
      "type": "number"
    },
    "flaky_confidence_high": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Flaky Confidence High"
    },
    "flaky_confidence_low": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Flaky Confidence Low"
    },
    "flaky_likely_cause": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Flaky Likely Cause"
    },
    "flaky_likely_cause_code": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Flaky Likely Cause Code"
    },
    "flaky_score": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/FlakyScoreBrief"
        },
        {
          "type": "null"
        }
      ]
    },
    "flaky_since": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Flaky Since"
    },
    "impact_score": {
      "default": 0.0,
      "title": "Impact Score",
      "type": "number"
    },
    "in_run_retry_rate": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "In Run Retry Rate"
    },
    "intermittency_label": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Intermittency Label"
    },
    "is_flaky_confidence": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Is Flaky Confidence"
    },
    "last_failure_at": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Last Failure At"
    },
    "quarantine_recommendation": {
      "default": "MONITOR",
      "title": "Quarantine Recommendation",
      "type": "string"
    },
    "stabilization_actions": {
      "default": [],
      "items": {
        "type": "string"
      },
      "title": "Stabilization Actions",
      "type": "array"
    },
    "stack_trace_diversity": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Stack Trace Diversity"
    },
    "status_history": {
      "default": [],
      "items": {
        "type": "string"
      },
      "title": "Status History",
      "type": "array"
    },
    "status_volatility": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Status Volatility"
    },
    "suite_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Suite Name"
    },
    "test_fingerprint": {
      "title": "Test Fingerprint",
      "type": "string"
    },
    "test_name": {
      "title": "Test Name",
      "type": "string"
    },
    "total_runs": {
      "default": 0,
      "title": "Total Runs",
      "type": "integer"
    }
  },
  "required": [
    "test_fingerprint",
    "test_name",
    "failure_rate"
  ],
  "title": "FlakyCoachEntry",
  "type": "object"
}
```

## FlakyCoachResponse

```json
{
  "description": "Project-level flaky coach leaderboard.",
  "properties": {
    "entries": {
      "default": [],
      "items": {
        "$ref": "#/components/schemas/FlakyCoachEntry"
      },
      "title": "Entries",
      "type": "array"
    },
    "project_id": {
      "title": "Project Id",
      "type": "string"
    },
    "quarantine_candidates": {
      "default": 0,
      "title": "Quarantine Candidates",
      "type": "integer"
    },
    "scope": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/FlakyCoachScope"
        },
        {
          "type": "null"
        }
      ]
    },
    "total_flaky": {
      "default": 0,
      "title": "Total Flaky",
      "type": "integer"
    }
  },
  "required": [
    "project_id"
  ],
  "title": "FlakyCoachResponse",
  "type": "object"
}
```

## FlakyCoachScope

```json
{
  "description": "What each number in the leaderboard is scoped to.\n\nA filtered list LOOKS release-scoped, and here only half of it is:\nmembership is, the impact score is not. Stated in the payload rather than\nonly in the docs, because the number is what gets read — a reader who takes\na score as \"how flaky during 2.4.0\" would be wrong, and nothing in a bare\nfiltered list would tell them.",
  "properties": {
    "membership": {
      "default": "project",
      "title": "Membership",
      "type": "string"
    },
    "note": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Note"
    },
    "release_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Release Id"
    },
    "score": {
      "default": "project_window",
      "title": "Score",
      "type": "string"
    }
  },
  "title": "FlakyCoachScope",
  "type": "object"
}
```

## FlakyCountCriteria

```json
{
  "description": "What a flaky-test count actually measured.\n\nA bare count cannot distinguish \"this project has no flaky tests\" from\n\"no test cleared this particular bar\", and surfaces applying different bars\nthen look like they contradict each other.",
  "properties": {
    "max_failure_ratio": {
      "description": "Upper bound of the failure ratio. Above it the test is treated as broken rather than flaky — it is not intermittent, it is failing.",
      "title": "Max Failure Ratio",
      "type": "number"
    },
    "min_failure_ratio": {
      "description": "Lower bound of the failure ratio. Below it the test is treated as healthy rather than flaky.",
      "title": "Min Failure Ratio",
      "type": "number"
    },
    "min_flips": {
      "description": "Pass<->fail transitions required, in run order. This is what separates a flaky test from a persistent regression, which a failure ratio alone cannot do.",
      "title": "Min Flips",
      "type": "integer"
    },
    "min_runs": {
      "description": "Runs a test must have inside the window to be judged at all. A test with fewer is not counted as flaky and not counted as healthy — there is not enough history to say.",
      "title": "Min Runs",
      "type": "integer"
    },
    "window_runs": {
      "description": "How many of each test's most recent runs were examined.",
      "title": "Window Runs",
      "type": "integer"
    }
  },
  "required": [
    "window_runs",
    "min_runs",
    "min_flips",
    "min_failure_ratio",
    "max_failure_ratio"
  ],
  "title": "FlakyCountCriteria",
  "type": "object"
}
```

## FlakyQuarantineRead

```json
{
  "properties": {
    "approved_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Approved At"
    },
    "approved_by_user_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Approved By User Id"
    },
    "consecutive_passes": {
      "default": 0,
      "title": "Consecutive Passes",
      "type": "integer"
    },
    "created_at": {
      "format": "date-time",
      "title": "Created At",
      "type": "string"
    },
    "defect_external_status": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Defect External Status"
    },
    "defect_external_status_conflict": {
      "default": false,
      "title": "Defect External Status Conflict",
      "type": "boolean"
    },
    "defect_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Defect Id"
    },
    "defect_jira_key": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Defect Jira Key"
    },
    "defect_jira_url": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Defect Jira Url"
    },
    "detected_at": {
      "format": "date-time",
      "title": "Detected At",
      "type": "string"
    },
    "detection_method": {
      "title": "Detection Method",
      "type": "string"
    },
    "fail_count": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Fail Count"
    },
    "flip_rate": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Flip Rate"
    },
    "flip_window_size": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Flip Window Size"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "last_failure_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Last Failure At"
    },
    "owner_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Owner Name"
    },
    "owner_user_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Owner User Id"
    },
    "pass_count": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Pass Count"
    },
    "project_id": {
      "format": "uuid",
      "title": "Project Id",
      "type": "string"
    },
    "proposed_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Proposed At"
    },
    "quarantine_duration_days": {
      "title": "Quarantine Duration Days",
      "type": "integer"
    },
    "quarantine_expires_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Quarantine Expires At"
    },
    "quarantine_start": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Quarantine Start"
    },
    "rationale": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Rationale"
    },
    "ready_to_promote": {
      "default": false,
      "title": "Ready To Promote",
      "type": "boolean"
    },
    "recheck_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Recheck At"
    },
    "rejected_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Rejected At"
    },
    "rejected_by_user_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Rejected By User Id"
    },
    "reviewer_notes": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Reviewer Notes"
    },
    "sla_days": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Sla Days"
    },
    "stale": {
      "default": false,
      "title": "Stale",
      "type": "boolean"
    },
    "stale_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Stale At"
    },
    "status": {
      "title": "Status",
      "type": "string"
    },
    "suite_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Suite Name"
    },
    "test_fingerprint": {
      "title": "Test Fingerprint",
      "type": "string"
    },
    "test_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Test Name"
    },
    "updated_at": {
      "format": "date-time",
      "title": "Updated At",
      "type": "string"
    }
  },
  "required": [
    "id",
    "project_id",
    "test_fingerprint",
    "status",
    "detection_method",
    "detected_at",
    "quarantine_duration_days",
    "created_at",
    "updated_at"
  ],
  "title": "FlakyQuarantineRead",
  "type": "object"
}
```

## FlakyScoreBrief

```json
{
  "description": "The four-signal score of a Detected row; plain ``str`` vocabularies (W09 FLK-03).",
  "properties": {
    "components": {
      "default": [],
      "items": {
        "$ref": "#/components/schemas/FlakyScoreComponent"
      },
      "title": "Components",
      "type": "array"
    },
    "computed_at": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Computed At"
    },
    "confidence": {
      "default": "none",
      "title": "Confidence",
      "type": "string"
    },
    "min_observations": {
      "default": 5,
      "title": "Min Observations",
      "type": "integer"
    },
    "observations": {
      "default": 0,
      "title": "Observations",
      "type": "integer"
    },
    "score": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Score"
    },
    "status": {
      "title": "Status",
      "type": "string"
    },
    "window_days": {
      "default": 30,
      "title": "Window Days",
      "type": "integer"
    }
  },
  "required": [
    "status"
  ],
  "title": "FlakyScoreBrief",
  "type": "object"
}
```

## FlakyScoreComponent

```json
{
  "description": "One of the four signals of the nightly flakiness score (W09 FLK-03).",
  "properties": {
    "name": {
      "title": "Name",
      "type": "string"
    },
    "value": {
      "title": "Value",
      "type": "number"
    },
    "weight": {
      "title": "Weight",
      "type": "number"
    }
  },
  "required": [
    "name",
    "value",
    "weight"
  ],
  "title": "FlakyScoreComponent",
  "type": "object"
}
```

## FrontendError

```json
{
  "properties": {
    "component_stack": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Component Stack"
    },
    "context": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Context"
    },
    "message": {
      "title": "Message",
      "type": "string"
    },
    "stack": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Stack"
    },
    "timestamp": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Timestamp"
    },
    "type": {
      "title": "Type",
      "type": "string"
    },
    "url": {
      "title": "Url",
      "type": "string"
    },
    "user_agent": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "User Agent"
    }
  },
  "required": [
    "type",
    "message",
    "url"
  ],
  "title": "FrontendError",
  "type": "object"
}
```

## FrontendTelemetryBatch

```json
{
  "properties": {
    "errors": {
      "default": [],
      "items": {
        "$ref": "#/components/schemas/FrontendError"
      },
      "title": "Errors",
      "type": "array"
    },
    "vitals": {
      "default": [],
      "items": {
        "$ref": "#/components/schemas/WebVitalReport"
      },
      "title": "Vitals",
      "type": "array"
    }
  },
  "title": "FrontendTelemetryBatch",
  "type": "object"
}
```

## GatedChannel

```json
{
  "properties": {
    "channel": {
      "title": "Channel",
      "type": "string"
    },
    "label": {
      "title": "Label",
      "type": "string"
    }
  },
  "required": [
    "channel",
    "label"
  ],
  "title": "GatedChannel",
  "type": "object"
}
```

## GenerationBatchResponse

```json
{
  "properties": {
    "cases_accepted": {
      "title": "Cases Accepted",
      "type": "integer"
    },
    "cases_generated": {
      "title": "Cases Generated",
      "type": "integer"
    },
    "cases_rejected": {
      "title": "Cases Rejected",
      "type": "integer"
    },
    "completed_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Completed At"
    },
    "coverage_score": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Coverage Score"
    },
    "created_at": {
      "format": "date-time",
      "title": "Created At",
      "type": "string"
    },
    "created_by_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Created By Id"
    },
    "generation_mode": {
      "title": "Generation Mode",
      "type": "string"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "llm_model_used": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Llm Model Used"
    },
    "project_id": {
      "format": "uuid",
      "title": "Project Id",
      "type": "string"
    },
    "status": {
      "title": "Status",
      "type": "string"
    }
  },
  "required": [
    "id",
    "project_id",
    "generation_mode",
    "cases_generated",
    "cases_accepted",
    "cases_rejected",
    "status",
    "created_at"
  ],
  "title": "GenerationBatchResponse",
  "type": "object"
}
```

## GitHubAppInstallUrl

```json
{
  "properties": {
    "expires_in": {
      "title": "Expires In",
      "type": "integer"
    },
    "url": {
      "title": "Url",
      "type": "string"
    }
  },
  "required": [
    "url",
    "expires_in"
  ],
  "title": "GitHubAppInstallUrl",
  "type": "object"
}
```

## GitHubAppSettingsRead

```json
{
  "description": "W19 INT-06: the deployment's GitHub App. Secrets are never returned, only whether they are set.",
  "properties": {
    "app_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "App Id"
    },
    "configured": {
      "default": false,
      "title": "Configured",
      "type": "boolean"
    },
    "private_key_set": {
      "default": false,
      "title": "Private Key Set",
      "type": "boolean"
    },
    "slug": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Slug"
    },
    "webhook_secret_set": {
      "default": false,
      "title": "Webhook Secret Set",
      "type": "boolean"
    }
  },
  "title": "GitHubAppSettingsRead",
  "type": "object"
}
```

## GitHubAppSettingsWrite

```json
{
  "properties": {
    "app_id": {
      "maxLength": 20,
      "minLength": 1,
      "pattern": "^[0-9]+$",
      "title": "App Id",
      "type": "string"
    },
    "private_key": {
      "anyOf": [
        {
          "maxLength": 10000,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Private Key"
    },
    "slug": {
      "maxLength": 100,
      "minLength": 1,
      "pattern": "^[A-Za-z0-9-]+$",
      "title": "Slug",
      "type": "string"
    },
    "webhook_secret": {
      "anyOf": [
        {
          "maxLength": 500,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Webhook Secret"
    }
  },
  "required": [
    "app_id",
    "slug"
  ],
  "title": "GitHubAppSettingsWrite",
  "type": "object"
}
```

## GitHubAppSetupCallback

```json
{
  "properties": {
    "installation_id": {
      "minimum": 1.0,
      "title": "Installation Id",
      "type": "integer"
    },
    "state": {
      "maxLength": 600,
      "minLength": 10,
      "title": "State",
      "type": "string"
    }
  },
  "required": [
    "installation_id",
    "state"
  ],
  "title": "GitHubAppSetupCallback",
  "type": "object"
}
```

## GitHubAppSetupResult

```json
{
  "properties": {
    "auth_kind": {
      "title": "Auth Kind",
      "type": "string"
    },
    "installation_id": {
      "title": "Installation Id",
      "type": "integer"
    },
    "project_id": {
      "format": "uuid",
      "title": "Project Id",
      "type": "string"
    }
  },
  "required": [
    "project_id",
    "installation_id",
    "auth_kind"
  ],
  "title": "GitHubAppSetupResult",
  "type": "object"
}
```

## GitHubConnectionTestResponse

```json
{
  "properties": {
    "auth_kind": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Auth Kind"
    },
    "message": {
      "title": "Message",
      "type": "string"
    },
    "repo_html_url": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Repo Html Url"
    },
    "status_code": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Status Code"
    },
    "success": {
      "title": "Success",
      "type": "boolean"
    }
  },
  "required": [
    "success",
    "message"
  ],
  "title": "GitHubConnectionTestResponse",
  "type": "object"
}
```

## GitHubIntegrationRead

```json
{
  "properties": {
    "api_base_url": {
      "title": "Api Base Url",
      "type": "string"
    },
    "auth_kind": {
      "default": "pat",
      "title": "Auth Kind",
      "type": "string"
    },
    "created_at": {
      "format": "date-time",
      "title": "Created At",
      "type": "string"
    },
    "enabled": {
      "title": "Enabled",
      "type": "boolean"
    },
    "has_pat": {
      "title": "Has Pat",
      "type": "boolean"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "installation_id": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Installation Id"
    },
    "last_error": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Last Error"
    },
    "last_error_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Last Error At"
    },
    "last_posted_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Last Posted At"
    },
    "pr_comment_mode": {
      "default": "failures_only",
      "title": "Pr Comment Mode",
      "type": "string"
    },
    "project_id": {
      "format": "uuid",
      "title": "Project Id",
      "type": "string"
    },
    "repo_name": {
      "title": "Repo Name",
      "type": "string"
    },
    "repo_owner": {
      "title": "Repo Owner",
      "type": "string"
    },
    "updated_at": {
      "format": "date-time",
      "title": "Updated At",
      "type": "string"
    }
  },
  "required": [
    "id",
    "project_id",
    "enabled",
    "repo_owner",
    "repo_name",
    "api_base_url",
    "has_pat",
    "created_at",
    "updated_at"
  ],
  "title": "GitHubIntegrationRead",
  "type": "object"
}
```

## GitHubIntegrationWrite

```json
{
  "properties": {
    "api_base_url": {
      "default": "https://api.github.com",
      "maxLength": 500,
      "title": "Api Base Url",
      "type": "string"
    },
    "auth_kind": {
      "anyOf": [
        {
          "enum": [
            "pat",
            "app"
          ],
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Auth Kind"
    },
    "enabled": {
      "default": true,
      "title": "Enabled",
      "type": "boolean"
    },
    "pat": {
      "anyOf": [
        {
          "maxLength": 200,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Pat"
    },
    "pr_comment_mode": {
      "default": "failures_only",
      "enum": [
        "off",
        "failures_only",
        "always"
      ],
      "title": "Pr Comment Mode",
      "type": "string"
    },
    "repo_name": {
      "maxLength": 255,
      "minLength": 1,
      "pattern": "^[A-Za-z0-9._-]+$",
      "title": "Repo Name",
      "type": "string"
    },
    "repo_owner": {
      "maxLength": 255,
      "minLength": 1,
      "pattern": "^[A-Za-z0-9._-]+$",
      "title": "Repo Owner",
      "type": "string"
    }
  },
  "required": [
    "repo_owner",
    "repo_name"
  ],
  "title": "GitHubIntegrationWrite",
  "type": "object"
}
```

## GitLabConfigRead

```json
{
  "description": "GET/PUT response for ``/projects/{id}/integrations/gitlab``.\n\nStructurally token-free — the PAT can never leak through this model;\n``has_token`` is the only token signal. ``mr_comment_mode`` is a plain\n``str`` on the read side (GitHub-sibling pattern): the column is an\nunconstrained ``String(20)``, and a drifted row value must degrade\ngracefully instead of turning GET into a ResponseValidationError 500.",
  "properties": {
    "base_url": {
      "default": "https://gitlab.com",
      "maxLength": 500,
      "title": "Base Url",
      "type": "string"
    },
    "commit_status_enabled": {
      "default": true,
      "title": "Commit Status Enabled",
      "type": "boolean"
    },
    "enabled": {
      "default": false,
      "title": "Enabled",
      "type": "boolean"
    },
    "has_token": {
      "default": false,
      "title": "Has Token",
      "type": "boolean"
    },
    "last_error": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Last Error"
    },
    "last_error_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Last Error At"
    },
    "mr_comment_mode": {
      "default": "failures_only",
      "title": "Mr Comment Mode",
      "type": "string"
    },
    "project_path": {
      "default": "",
      "maxLength": 500,
      "title": "Project Path",
      "type": "string"
    }
  },
  "title": "GitLabConfigRead",
  "type": "object"
}
```

## GitLabConfigWrite

```json
{
  "description": "PUT body for ``/projects/{id}/integrations/gitlab``.\n\n``token`` is the optional write-only field: ``None`` leaves the stored\nsecret alone, ``\"\"`` clears it, any value sets/rotates it. The PAT is\nNEVER present on the read side (see ``GitLabConfigRead``).",
  "properties": {
    "base_url": {
      "default": "https://gitlab.com",
      "maxLength": 500,
      "pattern": "^https?://",
      "title": "Base Url",
      "type": "string"
    },
    "commit_status_enabled": {
      "default": true,
      "title": "Commit Status Enabled",
      "type": "boolean"
    },
    "enabled": {
      "default": false,
      "title": "Enabled",
      "type": "boolean"
    },
    "mr_comment_mode": {
      "default": "failures_only",
      "enum": [
        "off",
        "failures_only",
        "always"
      ],
      "title": "Mr Comment Mode",
      "type": "string"
    },
    "project_path": {
      "default": "",
      "maxLength": 500,
      "title": "Project Path",
      "type": "string"
    },
    "token": {
      "anyOf": [
        {
          "maxLength": 200,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Token"
    }
  },
  "title": "GitLabConfigWrite",
  "type": "object"
}
```

## GitLabConnectionTestResponse

```json
{
  "properties": {
    "detail": {
      "title": "Detail",
      "type": "string"
    },
    "ok": {
      "title": "Ok",
      "type": "boolean"
    },
    "project_id_resolved": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Project Id Resolved"
    }
  },
  "required": [
    "ok",
    "detail"
  ],
  "title": "GitLabConnectionTestResponse",
  "type": "object"
}
```

## GroupMappingIn

```json
{
  "properties": {
    "project_id": {
      "format": "uuid",
      "title": "Project Id",
      "type": "string"
    },
    "role": {
      "title": "Role",
      "type": "string"
    }
  },
  "required": [
    "project_id",
    "role"
  ],
  "title": "GroupMappingIn",
  "type": "object"
}
```

## GroupMappingsIn

```json
{
  "properties": {
    "mappings": {
      "items": {
        "$ref": "#/components/schemas/GroupMappingIn"
      },
      "maxItems": 200,
      "title": "Mappings",
      "type": "array"
    }
  },
  "title": "GroupMappingsIn",
  "type": "object"
}
```

## HTTPValidationError

```json
{
  "properties": {
    "detail": {
      "items": {
        "$ref": "#/components/schemas/ValidationError"
      },
      "title": "Detail",
      "type": "array"
    }
  },
  "title": "HTTPValidationError",
  "type": "object"
}
```

## HoldRefRead

```json
{
  "description": "A live retention hold as it appears inside another answer.",
  "properties": {
    "entity_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Entity Id"
    },
    "entity_label": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Entity Label"
    },
    "entity_type": {
      "title": "Entity Type",
      "type": "string"
    },
    "expires_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Expires At"
    },
    "hold_id": {
      "format": "uuid",
      "title": "Hold Id",
      "type": "string"
    },
    "reason": {
      "title": "Reason",
      "type": "string"
    }
  },
  "required": [
    "hold_id",
    "entity_type",
    "reason"
  ],
  "title": "HoldRefRead",
  "type": "object"
}
```

## HttpExchangeIn

```json
{
  "description": "One HTTP request/response a test made (AGT-14). Lenient on purpose: a bad exchange is DROPPED by the\nlist pre-filter (IngestTestResult / LiveHttpExchangesIn), never a 422 that costs the batch its results.",
  "properties": {
    "duration_ms": {
      "anyOf": [
        {
          "maximum": 86400000.0,
          "minimum": 0.0,
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Duration Ms"
    },
    "endpoint": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Endpoint"
    },
    "error": {
      "anyOf": [
        {
          "maxLength": 200,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Error"
    },
    "method": {
      "title": "Method",
      "type": "string"
    },
    "redaction_counts": {
      "additionalProperties": {
        "type": "integer"
      },
      "title": "Redaction Counts",
      "type": "object"
    },
    "request": {
      "$ref": "#/components/schemas/HttpMessageIn"
    },
    "response": {
      "$ref": "#/components/schemas/HttpMessageIn"
    },
    "response_schema": {
      "anyOf": [
        {
          "additionalProperties": {
            "type": "string"
          },
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Response Schema"
    },
    "response_schema_state": {
      "default": "empty",
      "title": "Response Schema State",
      "type": "string"
    },
    "started_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Started At"
    },
    "status": {
      "anyOf": [
        {
          "maximum": 599.0,
          "minimum": 100.0,
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Status"
    },
    "url": {
      "minLength": 1,
      "title": "Url",
      "type": "string"
    }
  },
  "required": [
    "method",
    "url"
  ],
  "title": "HttpExchangeIn",
  "type": "object"
}
```

## HttpExchangeListResponse

```json
{
  "description": "GET /api/v1/runs/{run_id}/tests/{test_id}/http-exchanges.",
  "properties": {
    "dropped": {
      "title": "Dropped",
      "type": "integer"
    },
    "items": {
      "items": {
        "$ref": "#/components/schemas/HttpExchangeRead"
      },
      "title": "Items",
      "type": "array"
    },
    "run_cap_reached": {
      "title": "Run Cap Reached",
      "type": "boolean"
    },
    "total": {
      "title": "Total",
      "type": "integer"
    }
  },
  "required": [
    "items",
    "total",
    "dropped",
    "run_cap_reached"
  ],
  "title": "HttpExchangeListResponse",
  "type": "object"
}
```

## HttpExchangeRead

```json
{
  "properties": {
    "captured_at": {
      "format": "date-time",
      "title": "Captured At",
      "type": "string"
    },
    "duration_ms": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Duration Ms"
    },
    "endpoint": {
      "title": "Endpoint",
      "type": "string"
    },
    "error": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Error"
    },
    "host": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Host"
    },
    "method": {
      "title": "Method",
      "type": "string"
    },
    "redacted": {
      "title": "Redacted",
      "type": "boolean"
    },
    "request": {
      "$ref": "#/components/schemas/HttpMessageRead"
    },
    "response": {
      "$ref": "#/components/schemas/HttpMessageRead"
    },
    "response_schema_state": {
      "title": "Response Schema State",
      "type": "string"
    },
    "seq": {
      "title": "Seq",
      "type": "integer"
    },
    "source": {
      "title": "Source",
      "type": "string"
    },
    "started_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Started At"
    },
    "status": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Status"
    },
    "url": {
      "title": "Url",
      "type": "string"
    }
  },
  "required": [
    "seq",
    "method",
    "endpoint",
    "url",
    "request",
    "response",
    "response_schema_state",
    "source",
    "captured_at",
    "redacted"
  ],
  "title": "HttpExchangeRead",
  "type": "object"
}
```

## HttpMessageIn

```json
{
  "description": "One side of an exchange, as the SDK sends it (cut + redacted by ``HttpExchangeIn``).",
  "properties": {
    "body": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Body"
    },
    "body_bytes": {
      "default": 0,
      "title": "Body Bytes",
      "type": "integer"
    },
    "body_truncated": {
      "default": false,
      "title": "Body Truncated",
      "type": "boolean"
    },
    "headers": {
      "additionalProperties": {
        "type": "string"
      },
      "title": "Headers",
      "type": "object"
    }
  },
  "title": "HttpMessageIn",
  "type": "object"
}
```

## HttpMessageRead

```json
{
  "properties": {
    "body": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Body"
    },
    "body_bytes": {
      "default": 0,
      "title": "Body Bytes",
      "type": "integer"
    },
    "body_truncated": {
      "default": false,
      "title": "Body Truncated",
      "type": "boolean"
    },
    "headers": {
      "additionalProperties": {
        "type": "string"
      },
      "title": "Headers",
      "type": "object"
    }
  },
  "required": [
    "headers"
  ],
  "title": "HttpMessageRead",
  "type": "object"
}
```

## IdentityEventListResponse

```json
{
  "properties": {
    "items": {
      "items": {
        "$ref": "#/components/schemas/IdentityEventResponse"
      },
      "title": "Items",
      "type": "array"
    },
    "total": {
      "title": "Total",
      "type": "integer"
    }
  },
  "required": [
    "total",
    "items"
  ],
  "title": "IdentityEventListResponse",
  "type": "object"
}
```

## IdentityEventResponse

```json
{
  "properties": {
    "actor_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Actor Id"
    },
    "actor_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Actor Name"
    },
    "created_at": {
      "format": "date-time",
      "title": "Created At",
      "type": "string"
    },
    "detail": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Detail"
    },
    "error_message": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Error Message"
    },
    "event_type": {
      "$ref": "#/components/schemas/IdentityEventType"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "ip_address": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Ip Address"
    },
    "sso_config_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Sso Config Id"
    },
    "success": {
      "title": "Success",
      "type": "boolean"
    },
    "user_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "User Id"
    }
  },
  "required": [
    "id",
    "event_type",
    "success",
    "created_at"
  ],
  "title": "IdentityEventResponse",
  "type": "object"
}
```

## IdentityEventType

```json
{
  "enum": [
    "SSO_LOGIN",
    "SSO_LOGIN_FAILED",
    "SSO_CONFIG_CREATED",
    "SSO_CONFIG_UPDATED",
    "SSO_CONFIG_DELETED",
    "SSO_TEST_CONNECTION",
    "SCIM_USER_CREATED",
    "SCIM_USER_UPDATED",
    "SCIM_USER_DEACTIVATED",
    "SCIM_USER_REACTIVATED",
    "SCIM_SYNC_ERROR",
    "SCIM_TOKEN_CREATED",
    "SCIM_TOKEN_REVOKED",
    "ADMIN_FALLBACK_LOGIN",
    "JIT_PROVISIONED",
    "ROLE_MAPPED",
    "SSO_LOGOUT",
    "MFA_ENROLL_STARTED",
    "MFA_ENABLED",
    "MFA_DISABLED",
    "MFA_VERIFY_SUCCESS",
    "MFA_VERIFY_FAILED",
    "MFA_RECOVERY_CODE_USED",
    "MFA_RECOVERY_CODES_REISSUED",
    "MFA_BREAKGLASS_RESET",
    "MFA_POLICY_UPDATED",
    "ACCOUNT_LOCKED",
    "ACCOUNT_UNLOCKED",
    "API_KEY_REVOKED"
  ],
  "title": "IdentityEventType",
  "type": "string"
}
```

## IdentitySyncStatus

```json
{
  "description": "Aggregated sync health for the admin dashboard.",
  "properties": {
    "last_scim_sync_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Last Scim Sync At"
    },
    "last_sso_login_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Last Sso Login At"
    },
    "recent_events": {
      "default": [],
      "items": {
        "$ref": "#/components/schemas/IdentityEventResponse"
      },
      "title": "Recent Events",
      "type": "array"
    },
    "recent_failures": {
      "default": 0,
      "title": "Recent Failures",
      "type": "integer"
    },
    "sso_config_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Sso Config Id"
    },
    "sso_display_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Sso Display Name"
    },
    "total_federated_users": {
      "default": 0,
      "title": "Total Federated Users",
      "type": "integer"
    }
  },
  "title": "IdentitySyncStatus",
  "type": "object"
}
```

## ImportDuplicateOf

```json
{
  "properties": {
    "case_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Case Id"
    },
    "row": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Row"
    },
    "title": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Title"
    }
  },
  "title": "ImportDuplicateOf",
  "type": "object"
}
```

## ImportHeaderReport

```json
{
  "properties": {
    "ignored": {
      "items": {
        "type": "string"
      },
      "title": "Ignored",
      "type": "array"
    },
    "recognised": {
      "items": {
        "type": "string"
      },
      "title": "Recognised",
      "type": "array"
    },
    "unknown": {
      "items": {
        "type": "string"
      },
      "title": "Unknown",
      "type": "array"
    }
  },
  "required": [
    "recognised",
    "ignored",
    "unknown"
  ],
  "title": "ImportHeaderReport",
  "type": "object"
}
```

## ImportRowResult

```json
{
  "properties": {
    "case_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Case Id"
    },
    "duplicate_of": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/ImportDuplicateOf"
        },
        {
          "type": "null"
        }
      ]
    },
    "outcome": {
      "title": "Outcome",
      "type": "string"
    },
    "reasons": {
      "items": {
        "type": "string"
      },
      "title": "Reasons",
      "type": "array"
    },
    "row": {
      "title": "Row",
      "type": "integer"
    },
    "title": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Title"
    },
    "warnings": {
      "items": {
        "type": "string"
      },
      "title": "Warnings",
      "type": "array"
    }
  },
  "required": [
    "row",
    "outcome"
  ],
  "title": "ImportRowResult",
  "type": "object"
}
```

## ImportSummary

```json
{
  "properties": {
    "create": {
      "title": "Create",
      "type": "integer"
    },
    "created": {
      "default": 0,
      "title": "Created",
      "type": "integer"
    },
    "duplicate": {
      "title": "Duplicate",
      "type": "integer"
    },
    "invalid": {
      "title": "Invalid",
      "type": "integer"
    },
    "rows": {
      "title": "Rows",
      "type": "integer"
    }
  },
  "required": [
    "rows",
    "create",
    "duplicate",
    "invalid"
  ],
  "title": "ImportSummary",
  "type": "object"
}
```

## IngestPayload

```json
{
  "description": "JSON batch ingest request body for POST /api/v1/ingest.",
  "properties": {
    "branch": {
      "anyOf": [
        {
          "maxLength": 255,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Branch"
    },
    "build_number": {
      "maxLength": 100,
      "minLength": 1,
      "title": "Build Number",
      "type": "string"
    },
    "ci_actor": {
      "anyOf": [
        {
          "maxLength": 120,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Ci Actor"
    },
    "ci_provider": {
      "anyOf": [
        {
          "maxLength": 30,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Ci Provider"
    },
    "ci_repo": {
      "anyOf": [
        {
          "maxLength": 300,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Ci Repo"
    },
    "ci_run_url": {
      "anyOf": [
        {
          "maxLength": 1000,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Ci Run Url"
    },
    "commit_hash": {
      "anyOf": [
        {
          "maxLength": 64,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Commit Hash"
    },
    "commit_range": {
      "anyOf": [
        {
          "items": {
            "$ref": "#/components/schemas/SuppliedCommit"
          },
          "type": "array"
        },
        {
          "$ref": "#/components/schemas/SuppliedCommitRange"
        },
        {
          "type": "null"
        }
      ],
      "title": "Commit Range"
    },
    "environment": {
      "anyOf": [
        {
          "maxLength": 100,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Environment"
    },
    "framework": {
      "anyOf": [
        {
          "maxLength": 50,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Framework"
    },
    "jenkins_job": {
      "anyOf": [
        {
          "maxLength": 500,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Jenkins Job"
    },
    "pr_number": {
      "anyOf": [
        {
          "minimum": 1.0,
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Pr Number"
    },
    "project_id": {
      "description": "Project UUID",
      "title": "Project Id",
      "type": "string"
    },
    "release_name": {
      "anyOf": [
        {
          "maxLength": 255,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Release Name"
    },
    "results": {
      "items": {
        "$ref": "#/components/schemas/IngestTestResult"
      },
      "maxItems": 50000,
      "minItems": 1,
      "title": "Results",
      "type": "array"
    },
    "run_ai": {
      "default": true,
      "description": "False skips the AI analysis and the suite comparison for this run.",
      "title": "Run Ai",
      "type": "boolean"
    },
    "run_context": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/RunContext"
        },
        {
          "type": "null"
        }
      ]
    },
    "trigger_source": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": "api",
      "title": "Trigger Source"
    }
  },
  "required": [
    "project_id",
    "build_number",
    "results"
  ],
  "title": "IngestPayload",
  "type": "object"
}
```

## IngestResponse

```json
{
  "description": "Response for accepted ingest request.",
  "properties": {
    "run_id": {
      "title": "Run Id",
      "type": "string"
    },
    "status": {
      "default": "accepted",
      "title": "Status",
      "type": "string"
    },
    "task_id": {
      "title": "Task Id",
      "type": "string"
    },
    "total_results": {
      "title": "Total Results",
      "type": "integer"
    }
  },
  "required": [
    "run_id",
    "task_id",
    "total_results"
  ],
  "title": "IngestResponse",
  "type": "object"
}
```

## IngestTestResult

```json
{
  "description": "A single test result in a JSON batch ingest.",
  "properties": {
    "call_ms": {
      "anyOf": [
        {
          "minimum": 0.0,
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Call Ms"
    },
    "class_name": {
      "anyOf": [
        {
          "maxLength": 500,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Class Name"
    },
    "duration_ms": {
      "anyOf": [
        {
          "minimum": 0.0,
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Duration Ms"
    },
    "error_message": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Error Message"
    },
    "finished_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Finished At"
    },
    "http_exchanges": {
      "anyOf": [
        {
          "items": {
            "$ref": "#/components/schemas/HttpExchangeIn"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Http Exchanges"
    },
    "http_exchanges_dropped": {
      "anyOf": [
        {
          "minimum": 0.0,
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Http Exchanges Dropped"
    },
    "metadata": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Metadata"
    },
    "service_name": {
      "anyOf": [
        {
          "maxLength": 255,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Service Name"
    },
    "setup_ms": {
      "anyOf": [
        {
          "minimum": 0.0,
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Setup Ms"
    },
    "stack_trace": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Stack Trace"
    },
    "started_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Started At"
    },
    "status": {
      "pattern": "^(PASSED|FAILED|SKIPPED|BROKEN)$",
      "title": "Status",
      "type": "string"
    },
    "suite_name": {
      "anyOf": [
        {
          "maxLength": 500,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Suite Name"
    },
    "tags": {
      "anyOf": [
        {
          "items": {
            "type": "string"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Tags"
    },
    "teardown_ms": {
      "anyOf": [
        {
          "minimum": 0.0,
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Teardown Ms"
    },
    "test_name": {
      "maxLength": 1000,
      "minLength": 1,
      "title": "Test Name",
      "type": "string"
    },
    "worker_id": {
      "anyOf": [
        {
          "maxLength": 64,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Worker Id"
    }
  },
  "required": [
    "test_name",
    "status"
  ],
  "title": "IngestTestResult",
  "type": "object"
}
```

## InsightDetectorStatus

```json
{
  "properties": {
    "evaluated_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Evaluated At"
    },
    "firing": {
      "default": 0,
      "title": "Firing",
      "type": "integer"
    },
    "kind": {
      "title": "Kind",
      "type": "string"
    },
    "measured": {
      "default": false,
      "title": "Measured",
      "type": "boolean"
    },
    "reason": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Reason"
    },
    "subjects_measured": {
      "default": 0,
      "title": "Subjects Measured",
      "type": "integer"
    }
  },
  "required": [
    "kind"
  ],
  "title": "InsightDetectorStatus",
  "type": "object"
}
```

## InsightItem

```json
{
  "properties": {
    "detection_count": {
      "default": 1,
      "title": "Detection Count",
      "type": "integer"
    },
    "dismissed_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Dismissed At"
    },
    "dismissed_by_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Dismissed By Name"
    },
    "evidence": {
      "additionalProperties": true,
      "title": "Evidence",
      "type": "object"
    },
    "first_seen_at": {
      "format": "date-time",
      "title": "First Seen At",
      "type": "string"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "kind": {
      "title": "Kind",
      "type": "string"
    },
    "last_seen_at": {
      "format": "date-time",
      "title": "Last Seen At",
      "type": "string"
    },
    "link": {
      "$ref": "#/components/schemas/InsightLink"
    },
    "resolved_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Resolved At"
    },
    "resolved_reason": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Resolved Reason"
    },
    "run_path": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Run Path"
    },
    "severity": {
      "title": "Severity",
      "type": "string"
    },
    "status": {
      "default": "open",
      "title": "Status",
      "type": "string"
    },
    "subject_label": {
      "default": "",
      "title": "Subject Label",
      "type": "string"
    },
    "title": {
      "default": "",
      "title": "Title",
      "type": "string"
    },
    "window_key": {
      "default": "",
      "title": "Window Key",
      "type": "string"
    }
  },
  "required": [
    "id",
    "kind",
    "severity",
    "first_seen_at",
    "last_seen_at"
  ],
  "title": "InsightItem",
  "type": "object"
}
```

## InsightLink

```json
{
  "properties": {
    "label": {
      "default": "",
      "title": "Label",
      "type": "string"
    },
    "path": {
      "default": "",
      "title": "Path",
      "type": "string"
    }
  },
  "title": "InsightLink",
  "type": "object"
}
```

## InsightListResponse

```json
{
  "properties": {
    "detectors": {
      "items": {
        "$ref": "#/components/schemas/InsightDetectorStatus"
      },
      "title": "Detectors",
      "type": "array"
    },
    "dismissed_total": {
      "default": 0,
      "title": "Dismissed Total",
      "type": "integer"
    },
    "enabled": {
      "default": false,
      "title": "Enabled",
      "type": "boolean"
    },
    "generated_at": {
      "format": "date-time",
      "title": "Generated At",
      "type": "string"
    },
    "items": {
      "items": {
        "$ref": "#/components/schemas/InsightItem"
      },
      "title": "Items",
      "type": "array"
    },
    "range": {
      "default": "7d",
      "title": "Range",
      "type": "string"
    },
    "total": {
      "default": 0,
      "title": "Total",
      "type": "integer"
    }
  },
  "required": [
    "generated_at"
  ],
  "title": "InsightListResponse",
  "type": "object"
}
```

## InstanceEnablePreviewItem

```json
{
  "properties": {
    "effective_after": {
      "additionalProperties": {
        "type": "integer"
      },
      "title": "Effective After",
      "type": "object"
    },
    "has_policy_row": {
      "title": "Has Policy Row",
      "type": "boolean"
    },
    "is_active": {
      "title": "Is Active",
      "type": "boolean"
    },
    "name": {
      "title": "Name",
      "type": "string"
    },
    "project_id": {
      "format": "uuid",
      "title": "Project Id",
      "type": "string"
    },
    "runs_past_window": {
      "title": "Runs Past Window",
      "type": "integer"
    }
  },
  "required": [
    "project_id",
    "name",
    "is_active",
    "has_policy_row",
    "effective_after",
    "runs_past_window"
  ],
  "title": "InstanceEnablePreviewItem",
  "type": "object"
}
```

## InstanceEnablePreviewResponse

```json
{
  "properties": {
    "already_enabled": {
      "title": "Already Enabled",
      "type": "boolean"
    },
    "confirmation_phrase": {
      "title": "Confirmation Phrase",
      "type": "string"
    },
    "items": {
      "items": {
        "$ref": "#/components/schemas/InstanceEnablePreviewItem"
      },
      "title": "Items",
      "type": "array"
    },
    "preview_hash": {
      "title": "Preview Hash",
      "type": "string"
    },
    "total": {
      "title": "Total",
      "type": "integer"
    },
    "truncated": {
      "title": "Truncated",
      "type": "boolean"
    }
  },
  "required": [
    "total",
    "truncated",
    "preview_hash",
    "confirmation_phrase",
    "already_enabled"
  ],
  "title": "InstanceEnablePreviewResponse",
  "type": "object"
}
```

## InstanceEnableRequest

```json
{
  "properties": {
    "confirmation": {
      "maxLength": 200,
      "title": "Confirmation",
      "type": "string"
    },
    "preview_hash": {
      "maxLength": 64,
      "minLength": 64,
      "title": "Preview Hash",
      "type": "string"
    }
  },
  "required": [
    "confirmation",
    "preview_hash"
  ],
  "title": "InstanceEnableRequest",
  "type": "object"
}
```

## InstanceRetentionPolicyRead

```json
{
  "description": "GET/PUT ``/api/v1/settings/retention-defaults``. A NULL value = the code default.",
  "properties": {
    "artifacts_days": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Artifacts Days"
    },
    "audit_days": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Audit Days"
    },
    "code_defaults": {
      "additionalProperties": {
        "anyOf": [
          {
            "type": "boolean"
          },
          {
            "type": "integer"
          }
        ]
      },
      "title": "Code Defaults",
      "type": "object"
    },
    "effective": {
      "additionalProperties": {
        "anyOf": [
          {
            "type": "boolean"
          },
          {
            "type": "integer"
          }
        ]
      },
      "title": "Effective",
      "type": "object"
    },
    "enabled": {
      "title": "Enabled",
      "type": "boolean"
    },
    "enabled_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Enabled At"
    },
    "enabled_by_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Enabled By Name"
    },
    "inheriting_projects": {
      "additionalProperties": {
        "type": "integer"
      },
      "title": "Inheriting Projects",
      "type": "object"
    },
    "keep_last_runs": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Keep Last Runs"
    },
    "purge_window_start_utc": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Purge Window Start Utc"
    },
    "raw_events_days": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Raw Events Days"
    },
    "runs_days": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Runs Days"
    },
    "updated_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Updated At"
    }
  },
  "required": [
    "enabled",
    "code_defaults",
    "effective",
    "inheriting_projects"
  ],
  "title": "InstanceRetentionPolicyRead",
  "type": "object"
}
```

## InstanceRetentionPolicyWrite

```json
{
  "description": "PUT body. ``enabled: true`` is refused (422 ``enable_via_flow``); ``false`` switches it off.",
  "properties": {
    "artifacts_days": {
      "anyOf": [
        {
          "maximum": 3650.0,
          "minimum": 7.0,
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Artifacts Days"
    },
    "audit_days": {
      "anyOf": [
        {
          "maximum": 3650.0,
          "minimum": 365.0,
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Audit Days"
    },
    "clear": {
      "anyOf": [
        {
          "items": {
            "type": "string"
          },
          "maxItems": 6,
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Clear"
    },
    "enabled": {
      "anyOf": [
        {
          "type": "boolean"
        },
        {
          "type": "null"
        }
      ],
      "title": "Enabled"
    },
    "keep_last_runs": {
      "anyOf": [
        {
          "maximum": 1000.0,
          "minimum": 0.0,
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Keep Last Runs"
    },
    "purge_window_start_utc": {
      "anyOf": [
        {
          "maximum": 23.0,
          "minimum": 0.0,
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Purge Window Start Utc"
    },
    "raw_events_days": {
      "anyOf": [
        {
          "maximum": 3650.0,
          "minimum": 7.0,
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Raw Events Days"
    },
    "runs_days": {
      "anyOf": [
        {
          "maximum": 3650.0,
          "minimum": 30.0,
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Runs Days"
    }
  },
  "title": "InstanceRetentionPolicyWrite",
  "type": "object"
}
```

## IntegrationHealthRow

```json
{
  "properties": {
    "breaker_threshold": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Breaker Threshold"
    },
    "consecutive_failures": {
      "default": 0,
      "title": "Consecutive Failures",
      "type": "integer"
    },
    "kind": {
      "default": "global",
      "title": "Kind",
      "type": "string"
    },
    "label": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Label"
    },
    "last_checked_at": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Last Checked At"
    },
    "last_success_at": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Last Success At"
    },
    "message": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Message"
    },
    "probed": {
      "default": true,
      "title": "Probed",
      "type": "boolean"
    },
    "project_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Project Id"
    },
    "project_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Project Name"
    },
    "provider": {
      "title": "Provider",
      "type": "string"
    },
    "response_ms": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Response Ms"
    },
    "scope_key": {
      "default": "",
      "title": "Scope Key",
      "type": "string"
    },
    "status": {
      "title": "Status",
      "type": "string"
    },
    "target": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Target"
    }
  },
  "required": [
    "provider",
    "status"
  ],
  "title": "IntegrationHealthRow",
  "type": "object"
}
```

## IntegrationsConfigRead

```json
{
  "description": "External integrations configuration (no tokens).",
  "properties": {
    "environment_fields": {
      "items": {
        "type": "string"
      },
      "title": "Environment Fields",
      "type": "array"
    },
    "github_repo": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Github Repo"
    },
    "github_token_set": {
      "title": "Github Token Set",
      "type": "boolean"
    },
    "jira_default_project_key": {
      "title": "Jira Default Project Key",
      "type": "string"
    },
    "jira_domain": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Jira Domain"
    },
    "jira_email": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Jira Email"
    },
    "jira_enabled": {
      "title": "Jira Enabled",
      "type": "boolean"
    },
    "jira_token_set": {
      "title": "Jira Token Set",
      "type": "boolean"
    },
    "ocp_api_url": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Ocp Api Url"
    },
    "ocp_default_namespace": {
      "title": "Ocp Default Namespace",
      "type": "string"
    },
    "ocp_enabled": {
      "title": "Ocp Enabled",
      "type": "boolean"
    },
    "ocp_token_set": {
      "title": "Ocp Token Set",
      "type": "boolean"
    },
    "prometheus_base_url": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Prometheus Base Url"
    },
    "prometheus_enabled": {
      "default": false,
      "title": "Prometheus Enabled",
      "type": "boolean"
    },
    "prometheus_metric_templates": {
      "items": {
        "$ref": "#/components/schemas/PrometheusMetricTemplate"
      },
      "title": "Prometheus Metric Templates",
      "type": "array"
    },
    "prometheus_templates_source": {
      "default": "default",
      "title": "Prometheus Templates Source",
      "type": "string"
    },
    "prometheus_token_set": {
      "default": false,
      "title": "Prometheus Token Set",
      "type": "boolean"
    },
    "slack_default_channel": {
      "deprecated": true,
      "title": "Slack Default Channel",
      "type": "string"
    },
    "slack_enabled": {
      "title": "Slack Enabled",
      "type": "boolean"
    },
    "slack_webhook_set": {
      "title": "Slack Webhook Set",
      "type": "boolean"
    },
    "slack_webhook_url": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Slack Webhook Url"
    },
    "splunk_base_url": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Splunk Base Url"
    },
    "splunk_enabled": {
      "title": "Splunk Enabled",
      "type": "boolean"
    },
    "splunk_token_set": {
      "title": "Splunk Token Set",
      "type": "boolean"
    },
    "teams_enabled": {
      "title": "Teams Enabled",
      "type": "boolean"
    },
    "teams_webhook_set": {
      "title": "Teams Webhook Set",
      "type": "boolean"
    },
    "teams_webhook_url": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Teams Webhook Url"
    },
    "vault_fields": {
      "items": {
        "type": "string"
      },
      "title": "Vault Fields",
      "type": "array"
    }
  },
  "required": [
    "jira_enabled",
    "jira_domain",
    "jira_email",
    "jira_token_set",
    "jira_default_project_key",
    "splunk_enabled",
    "splunk_base_url",
    "splunk_token_set",
    "ocp_enabled",
    "ocp_api_url",
    "ocp_token_set",
    "ocp_default_namespace",
    "slack_enabled",
    "slack_webhook_set",
    "slack_default_channel",
    "teams_enabled",
    "teams_webhook_set",
    "github_repo",
    "github_token_set"
  ],
  "title": "IntegrationsConfigRead",
  "type": "object"
}
```

## IntegrationsConfigUpdate

```json
{
  "description": "Payload for updating integrations. None = keep existing.",
  "properties": {
    "github_repo": {
      "anyOf": [
        {
          "maxLength": 500,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Github Repo"
    },
    "github_token": {
      "anyOf": [
        {
          "maxLength": 500,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Github Token"
    },
    "jira_api_token": {
      "anyOf": [
        {
          "maxLength": 500,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Jira Api Token"
    },
    "jira_default_project_key": {
      "anyOf": [
        {
          "maxLength": 50,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Jira Default Project Key"
    },
    "jira_domain": {
      "anyOf": [
        {
          "maxLength": 500,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Jira Domain"
    },
    "jira_email": {
      "anyOf": [
        {
          "maxLength": 255,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Jira Email"
    },
    "jira_enabled": {
      "anyOf": [
        {
          "type": "boolean"
        },
        {
          "type": "null"
        }
      ],
      "title": "Jira Enabled"
    },
    "ocp_api_url": {
      "anyOf": [
        {
          "maxLength": 500,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Ocp Api Url"
    },
    "ocp_default_namespace": {
      "anyOf": [
        {
          "maxLength": 255,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Ocp Default Namespace"
    },
    "ocp_enabled": {
      "anyOf": [
        {
          "type": "boolean"
        },
        {
          "type": "null"
        }
      ],
      "title": "Ocp Enabled"
    },
    "ocp_sa_token": {
      "anyOf": [
        {
          "maxLength": 1000,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Ocp Sa Token"
    },
    "prometheus_base_url": {
      "anyOf": [
        {
          "maxLength": 500,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Prometheus Base Url"
    },
    "prometheus_bearer_token": {
      "anyOf": [
        {
          "maxLength": 4000,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Prometheus Bearer Token"
    },
    "prometheus_enabled": {
      "anyOf": [
        {
          "type": "boolean"
        },
        {
          "type": "null"
        }
      ],
      "title": "Prometheus Enabled"
    },
    "prometheus_metric_templates": {
      "anyOf": [
        {
          "items": {
            "$ref": "#/components/schemas/PrometheusMetricTemplateIn"
          },
          "maxItems": 12,
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Prometheus Metric Templates"
    },
    "slack_default_channel": {
      "anyOf": [
        {
          "maxLength": 100,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "deprecated": true,
      "title": "Slack Default Channel"
    },
    "slack_enabled": {
      "anyOf": [
        {
          "type": "boolean"
        },
        {
          "type": "null"
        }
      ],
      "title": "Slack Enabled"
    },
    "slack_webhook_url": {
      "anyOf": [
        {
          "maxLength": 1000,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Slack Webhook Url"
    },
    "splunk_api_token": {
      "anyOf": [
        {
          "maxLength": 500,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Splunk Api Token"
    },
    "splunk_base_url": {
      "anyOf": [
        {
          "maxLength": 500,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Splunk Base Url"
    },
    "splunk_enabled": {
      "anyOf": [
        {
          "type": "boolean"
        },
        {
          "type": "null"
        }
      ],
      "title": "Splunk Enabled"
    },
    "teams_enabled": {
      "anyOf": [
        {
          "type": "boolean"
        },
        {
          "type": "null"
        }
      ],
      "title": "Teams Enabled"
    },
    "teams_webhook_url": {
      "anyOf": [
        {
          "maxLength": 1000,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Teams Webhook Url"
    }
  },
  "title": "IntegrationsConfigUpdate",
  "type": "object"
}
```

## InvestigatorActConfig

```json
{
  "additionalProperties": false,
  "description": "W15 AGT-03 (D16): the action types Act may run in this project, each opt-in.\n\nInert outside Act (kept when the mode is lowered, so re-entering Act\nrestores the ticks). A missing block on a stored document = ``[]``.",
  "properties": {
    "allowed_action_types": {
      "items": {
        "type": "string"
      },
      "maxItems": 8,
      "title": "Allowed Action Types",
      "type": "array"
    }
  },
  "title": "InvestigatorActConfig",
  "type": "object"
}
```

## InvestigatorConfigExtension

```json
{
  "additionalProperties": false,
  "properties": {
    "act": {
      "$ref": "#/components/schemas/InvestigatorActConfig"
    },
    "budgets": {
      "$ref": "#/components/schemas/InvestigatorPolicyBudgets"
    },
    "promotion_note": {
      "anyOf": [
        {
          "maxLength": 2000,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Promotion Note"
    },
    "shadow_runs_completed": {
      "default": 0,
      "minimum": 0.0,
      "title": "Shadow Runs Completed",
      "type": "integer"
    }
  },
  "title": "InvestigatorConfigExtension",
  "type": "object"
}
```

## InvestigatorPolicyBudgets

```json
{
  "additionalProperties": false,
  "description": "The complete legacy Investigator budget contract preserved by E4.4.",
  "properties": {
    "max_active_cluster_children_per_project": {
      "default": 2,
      "maximum": 20.0,
      "minimum": 0.0,
      "title": "Max Active Cluster Children Per Project",
      "type": "integer"
    },
    "max_cluster_child_cost_usd_per_parent": {
      "default": 2.0,
      "maximum": 1000000.0,
      "minimum": 0.0,
      "title": "Max Cluster Child Cost Usd Per Parent",
      "type": "number"
    },
    "max_cluster_child_llm_calls_per_parent": {
      "default": 6,
      "maximum": 1000.0,
      "minimum": 0.0,
      "title": "Max Cluster Child Llm Calls Per Parent",
      "type": "integer"
    },
    "max_cluster_child_seconds_per_parent": {
      "default": 180,
      "maximum": 86400.0,
      "minimum": 0.0,
      "title": "Max Cluster Child Seconds Per Parent",
      "type": "integer"
    },
    "max_cluster_child_tokens_per_parent": {
      "default": 12000,
      "maximum": 10000000.0,
      "minimum": 0.0,
      "title": "Max Cluster Child Tokens Per Parent",
      "type": "integer"
    },
    "max_cluster_children_per_day": {
      "default": 20,
      "maximum": 1000.0,
      "minimum": 0.0,
      "title": "Max Cluster Children Per Day",
      "type": "integer"
    },
    "max_cluster_children_per_run": {
      "default": 1,
      "maximum": 20.0,
      "minimum": 0.0,
      "title": "Max Cluster Children Per Run",
      "type": "integer"
    },
    "max_cluster_members_per_child": {
      "default": 50,
      "maximum": 500.0,
      "minimum": 1.0,
      "title": "Max Cluster Members Per Child",
      "type": "integer"
    },
    "max_cost_usd_per_run": {
      "default": 5.0,
      "maximum": 1000000.0,
      "minimum": 0.0,
      "title": "Max Cost Usd Per Run",
      "type": "number"
    },
    "max_llm_calls_per_run": {
      "default": 30,
      "maximum": 100000.0,
      "minimum": 0.0,
      "title": "Max Llm Calls Per Run",
      "type": "integer"
    },
    "max_runs_per_day": {
      "default": 10,
      "maximum": 10000.0,
      "minimum": 0.0,
      "title": "Max Runs Per Day",
      "type": "integer"
    },
    "max_seconds_per_run": {
      "default": 300,
      "maximum": 86400.0,
      "minimum": 0.0,
      "title": "Max Seconds Per Run",
      "type": "integer"
    },
    "max_tokens_per_run": {
      "default": 60000,
      "maximum": 100000000.0,
      "minimum": 0.0,
      "title": "Max Tokens Per Run",
      "type": "integer"
    }
  },
  "title": "InvestigatorPolicyBudgets",
  "type": "object"
}
```

## InvitationMembershipView

```json
{
  "properties": {
    "project_id": {
      "format": "uuid",
      "title": "Project Id",
      "type": "string"
    },
    "project_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Project Name"
    },
    "role": {
      "title": "Role",
      "type": "string"
    }
  },
  "required": [
    "project_id",
    "role"
  ],
  "title": "InvitationMembershipView",
  "type": "object"
}
```

## InvitationPreviewResponse

```json
{
  "properties": {
    "email": {
      "title": "Email",
      "type": "string"
    },
    "expires_at": {
      "format": "date-time",
      "title": "Expires At",
      "type": "string"
    },
    "invited_by_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Invited By Name"
    },
    "project_names": {
      "items": {
        "type": "string"
      },
      "title": "Project Names",
      "type": "array"
    },
    "role": {
      "$ref": "#/components/schemas/UserRole"
    },
    "sso_required": {
      "default": false,
      "title": "Sso Required",
      "type": "boolean"
    }
  },
  "required": [
    "email",
    "role",
    "expires_at"
  ],
  "title": "InvitationPreviewResponse",
  "type": "object"
}
```

## InvitationSummary

```json
{
  "description": "W17b ADM-01: one row of the Pending invitations tab. Never carries a token.",
  "properties": {
    "created_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Created At"
    },
    "email": {
      "title": "Email",
      "type": "string"
    },
    "expires_at": {
      "format": "date-time",
      "title": "Expires At",
      "type": "string"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "invited_by_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Invited By Name"
    },
    "project_memberships": {
      "items": {
        "$ref": "#/components/schemas/InvitationMembershipView"
      },
      "title": "Project Memberships",
      "type": "array"
    },
    "revoked_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Revoked At"
    },
    "role": {
      "$ref": "#/components/schemas/UserRole"
    },
    "status": {
      "title": "Status",
      "type": "string"
    },
    "used_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Used At"
    }
  },
  "required": [
    "id",
    "email",
    "role",
    "status",
    "expires_at"
  ],
  "title": "InvitationSummary",
  "type": "object"
}
```

## InvitationTokenRequest

```json
{
  "properties": {
    "token": {
      "maxLength": 256,
      "minLength": 1,
      "title": "Token",
      "type": "string"
    }
  },
  "required": [
    "token"
  ],
  "title": "InvitationTokenRequest",
  "type": "object"
}
```

## InviteProjectMembership

```json
{
  "description": "W17b ADM-01: a project the invitee joins on accept, with its project role.",
  "properties": {
    "project_id": {
      "format": "uuid",
      "title": "Project Id",
      "type": "string"
    },
    "role": {
      "$ref": "#/components/schemas/UserRole",
      "default": "QA_ENGINEER"
    }
  },
  "required": [
    "project_id"
  ],
  "title": "InviteProjectMembership",
  "type": "object"
}
```

## InviteUserRequest

```json
{
  "properties": {
    "email": {
      "format": "email",
      "title": "Email",
      "type": "string"
    },
    "project_memberships": {
      "items": {
        "$ref": "#/components/schemas/InviteProjectMembership"
      },
      "maxItems": 50,
      "title": "Project Memberships",
      "type": "array"
    },
    "role": {
      "$ref": "#/components/schemas/UserRole",
      "default": "QA_ENGINEER"
    }
  },
  "required": [
    "email"
  ],
  "title": "InviteUserRequest",
  "type": "object"
}
```

## InviteUserResponse

```json
{
  "properties": {
    "email": {
      "title": "Email",
      "type": "string"
    },
    "email_sent": {
      "default": false,
      "title": "Email Sent",
      "type": "boolean"
    },
    "expires_at": {
      "format": "date-time",
      "title": "Expires At",
      "type": "string"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "invitation_link": {
      "title": "Invitation Link",
      "type": "string"
    },
    "role": {
      "$ref": "#/components/schemas/UserRole"
    }
  },
  "required": [
    "id",
    "email",
    "role",
    "expires_at",
    "invitation_link"
  ],
  "title": "InviteUserResponse",
  "type": "object"
}
```

## JiraDefectContext

```json
{
  "properties": {
    "branch": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Branch"
    },
    "build_number": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Build Number"
    },
    "ci_run_url": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Ci Run Url"
    }
  },
  "title": "JiraDefectContext",
  "type": "object"
}
```

## JiraDefectCreateRequest

```json
{
  "description": "Body for POST /projects/{project_id}/defects/jira.\n\nExactly one of ``fingerprint`` / ``cluster_id`` identifies the failure.\n``target`` picks the delivery: \"jira\" calls the Jira REST API, \"webhook\"\nemits the ``defect.create_requested`` outbound-webhook event instead\n(US-6.3).",
  "properties": {
    "assignee": {
      "anyOf": [
        {
          "maxLength": 128,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Assignee"
    },
    "cluster_id": {
      "anyOf": [
        {
          "maxLength": 255,
          "minLength": 1,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Cluster Id"
    },
    "confirm_not_filed": {
      "default": false,
      "title": "Confirm Not Filed",
      "type": "boolean"
    },
    "extra_comment": {
      "anyOf": [
        {
          "maxLength": 2000,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Extra Comment"
    },
    "fingerprint": {
      "anyOf": [
        {
          "maxLength": 64,
          "minLength": 1,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Fingerprint"
    },
    "issue_type": {
      "default": "Bug",
      "maxLength": 100,
      "title": "Issue Type",
      "type": "string"
    },
    "jira_project_key": {
      "anyOf": [
        {
          "maxLength": 50,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Jira Project Key"
    },
    "target": {
      "default": "jira",
      "pattern": "^(jira|webhook)$",
      "title": "Target",
      "type": "string"
    }
  },
  "title": "JiraDefectCreateRequest",
  "type": "object"
}
```

## JiraDefectCreateResponse

```json
{
  "properties": {
    "deduplicated": {
      "default": false,
      "title": "Deduplicated",
      "type": "boolean"
    },
    "defect_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Defect Id"
    },
    "external_status": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "External Status"
    },
    "jira_key": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Jira Key"
    },
    "jira_url": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Jira Url"
    },
    "message": {
      "default": "",
      "title": "Message",
      "type": "string"
    },
    "recurrence_comment_posted": {
      "default": false,
      "title": "Recurrence Comment Posted",
      "type": "boolean"
    },
    "recurrence_count": {
      "default": 0,
      "title": "Recurrence Count",
      "type": "integer"
    },
    "subscriptions_notified": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Subscriptions Notified"
    },
    "target": {
      "title": "Target",
      "type": "string"
    }
  },
  "required": [
    "target"
  ],
  "title": "JiraDefectCreateResponse",
  "type": "object"
}
```

## JiraDefectExistingLink

```json
{
  "properties": {
    "defect_id": {
      "title": "Defect Id",
      "type": "string"
    },
    "external_status": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "External Status"
    },
    "jira_key": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Jira Key"
    },
    "jira_url": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Jira Url"
    }
  },
  "required": [
    "defect_id"
  ],
  "title": "JiraDefectExistingLink",
  "type": "object"
}
```

## JiraDefectMetadataResponse

```json
{
  "description": "Dialog-picker metadata. ``available=false`` + ``reason`` instead of\nan HTTP error when Jira is offline-gated/unconfigured/unreachable —\nthe UI uses it to disable the action with a tooltip.",
  "properties": {
    "available": {
      "title": "Available",
      "type": "boolean"
    },
    "default_project_key": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Default Project Key"
    },
    "issue_types": {
      "default": [],
      "items": {
        "type": "string"
      },
      "title": "Issue Types",
      "type": "array"
    },
    "projects": {
      "default": [],
      "items": {
        "$ref": "#/components/schemas/JiraProjectOption"
      },
      "title": "Projects",
      "type": "array"
    },
    "reason": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Reason"
    },
    "webhook_available": {
      "default": false,
      "title": "Webhook Available",
      "type": "boolean"
    }
  },
  "required": [
    "available"
  ],
  "title": "JiraDefectMetadataResponse",
  "type": "object"
}
```

## JiraDefectOccurrences

```json
{
  "properties": {
    "failing_runs": {
      "default": 0,
      "title": "Failing Runs",
      "type": "integer"
    },
    "first_seen": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "First Seen"
    },
    "last_seen": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Last Seen"
    }
  },
  "title": "JiraDefectOccurrences",
  "type": "object"
}
```

## JiraDefectPreviewResponse

```json
{
  "description": "Pre-filled payload shown (read-only) in the create dialog.",
  "properties": {
    "ai_analysis": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Ai Analysis"
    },
    "cluster_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Cluster Id"
    },
    "context": {
      "$ref": "#/components/schemas/JiraDefectContext"
    },
    "deep_link": {
      "title": "Deep Link",
      "type": "string"
    },
    "description": {
      "title": "Description",
      "type": "string"
    },
    "error_message": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Error Message"
    },
    "existing_defect": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/JiraDefectExistingLink"
        },
        {
          "type": "null"
        }
      ]
    },
    "latest_run_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Latest Run Id"
    },
    "occurrences": {
      "$ref": "#/components/schemas/JiraDefectOccurrences"
    },
    "signature": {
      "title": "Signature",
      "type": "string"
    },
    "suite_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Suite Name"
    },
    "summary": {
      "title": "Summary",
      "type": "string"
    },
    "test_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Test Name"
    }
  },
  "required": [
    "signature",
    "summary",
    "description",
    "occurrences",
    "context",
    "deep_link"
  ],
  "title": "JiraDefectPreviewResponse",
  "type": "object"
}
```

## JiraIssueRequest

```json
{
  "properties": {
    "ai_summary": {
      "title": "Ai Summary",
      "type": "string"
    },
    "project_key": {
      "title": "Project Key",
      "type": "string"
    },
    "recommended_action": {
      "title": "Recommended Action",
      "type": "string"
    },
    "run_id": {
      "format": "uuid",
      "title": "Run Id",
      "type": "string"
    },
    "test_case_id": {
      "format": "uuid",
      "title": "Test Case Id",
      "type": "string"
    },
    "test_name": {
      "title": "Test Name",
      "type": "string"
    }
  },
  "required": [
    "project_key",
    "test_case_id",
    "test_name",
    "run_id",
    "ai_summary",
    "recommended_action"
  ],
  "title": "JiraIssueRequest",
  "type": "object"
}
```

## JiraIssueResponse

```json
{
  "properties": {
    "approval_status": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Approval Status"
    },
    "defect_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Defect Id"
    },
    "mutating_action": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Mutating Action"
    },
    "policy_reasons": {
      "items": {
        "type": "string"
      },
      "title": "Policy Reasons",
      "type": "array"
    },
    "requires_approval": {
      "default": false,
      "title": "Requires Approval",
      "type": "boolean"
    },
    "ticket_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Ticket Id"
    },
    "ticket_key": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Ticket Key"
    },
    "ticket_url": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Ticket Url"
    }
  },
  "title": "JiraIssueResponse",
  "type": "object"
}
```

## JiraProjectOption

```json
{
  "properties": {
    "key": {
      "title": "Key",
      "type": "string"
    },
    "name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Name"
    }
  },
  "required": [
    "key"
  ],
  "title": "JiraProjectOption",
  "type": "object"
}
```

## KnowledgeDomainAllowlistUpdate

```json
{
  "properties": {
    "domains": {
      "description": "FQDN list, e.g. ['confluence.corp.com', 'jira.corp.com']",
      "items": {
        "type": "string"
      },
      "title": "Domains",
      "type": "array"
    }
  },
  "required": [
    "domains"
  ],
  "title": "KnowledgeDomainAllowlistUpdate",
  "type": "object"
}
```

## KnowledgeSourceCreate

```json
{
  "properties": {
    "canonical_url": {
      "maxLength": 2000,
      "minLength": 1,
      "title": "Canonical Url",
      "type": "string"
    },
    "classification": {
      "default": "internal",
      "maxLength": 20,
      "title": "Classification",
      "type": "string"
    },
    "external_id": {
      "anyOf": [
        {
          "maxLength": 500,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "External Id"
    },
    "source_type": {
      "maxLength": 30,
      "title": "Source Type",
      "type": "string"
    },
    "title": {
      "maxLength": 500,
      "minLength": 1,
      "title": "Title",
      "type": "string"
    }
  },
  "required": [
    "source_type",
    "title",
    "canonical_url"
  ],
  "title": "KnowledgeSourceCreate",
  "type": "object"
}
```

## KnowledgeSourceFreshnessResponse

```json
{
  "properties": {
    "active_chunk_count": {
      "title": "Active Chunk Count",
      "type": "integer"
    },
    "content_changed_on_last_sync": {
      "title": "Content Changed On Last Sync",
      "type": "boolean"
    },
    "hours_since_sync": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Hours Since Sync"
    },
    "is_stale": {
      "title": "Is Stale",
      "type": "boolean"
    },
    "last_sync_status": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Last Sync Status"
    },
    "source_id": {
      "format": "uuid",
      "title": "Source Id",
      "type": "string"
    },
    "stale_since": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Stale Since"
    },
    "staleness_threshold_hours": {
      "title": "Staleness Threshold Hours",
      "type": "integer"
    },
    "sync_event_count": {
      "title": "Sync Event Count",
      "type": "integer"
    }
  },
  "required": [
    "source_id",
    "is_stale",
    "staleness_threshold_hours",
    "content_changed_on_last_sync",
    "active_chunk_count",
    "sync_event_count"
  ],
  "title": "KnowledgeSourceFreshnessResponse",
  "type": "object"
}
```

## KnowledgeSourceListResponse

```json
{
  "properties": {
    "items": {
      "items": {
        "$ref": "#/components/schemas/KnowledgeSourceResponse"
      },
      "title": "Items",
      "type": "array"
    },
    "page": {
      "title": "Page",
      "type": "integer"
    },
    "page_size": {
      "title": "Page Size",
      "type": "integer"
    },
    "total": {
      "title": "Total",
      "type": "integer"
    }
  },
  "required": [
    "items",
    "total",
    "page",
    "page_size"
  ],
  "title": "KnowledgeSourceListResponse",
  "type": "object"
}
```

## KnowledgeSourceResponse

```json
{
  "properties": {
    "canonical_url": {
      "title": "Canonical Url",
      "type": "string"
    },
    "classification": {
      "title": "Classification",
      "type": "string"
    },
    "content_hash": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Content Hash"
    },
    "created_at": {
      "format": "date-time",
      "title": "Created At",
      "type": "string"
    },
    "external_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "External Id"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "is_archived": {
      "title": "Is Archived",
      "type": "boolean"
    },
    "last_synced_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Last Synced At"
    },
    "owner_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Owner Id"
    },
    "project_id": {
      "format": "uuid",
      "title": "Project Id",
      "type": "string"
    },
    "source_type": {
      "title": "Source Type",
      "type": "string"
    },
    "storage_path": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Storage Path"
    },
    "sync_error": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Sync Error"
    },
    "sync_status": {
      "title": "Sync Status",
      "type": "string"
    },
    "title": {
      "title": "Title",
      "type": "string"
    },
    "updated_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Updated At"
    }
  },
  "required": [
    "id",
    "project_id",
    "source_type",
    "title",
    "canonical_url",
    "sync_status",
    "classification",
    "is_archived",
    "created_at"
  ],
  "title": "KnowledgeSourceResponse",
  "type": "object"
}
```

## KnowledgeSourceSyncResponse

```json
{
  "properties": {
    "source_id": {
      "format": "uuid",
      "title": "Source Id",
      "type": "string"
    },
    "sync_status": {
      "title": "Sync Status",
      "type": "string"
    },
    "task_id": {
      "title": "Task Id",
      "type": "string"
    }
  },
  "required": [
    "source_id",
    "task_id",
    "sync_status"
  ],
  "title": "KnowledgeSourceSyncResponse",
  "type": "object"
}
```

## KnowledgeSourceUpdate

```json
{
  "properties": {
    "classification": {
      "anyOf": [
        {
          "maxLength": 20,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Classification"
    },
    "is_archived": {
      "anyOf": [
        {
          "type": "boolean"
        },
        {
          "type": "null"
        }
      ],
      "title": "Is Archived"
    },
    "title": {
      "anyOf": [
        {
          "maxLength": 500,
          "minLength": 1,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Title"
    }
  },
  "title": "KnowledgeSourceUpdate",
  "type": "object"
}
```

## KnowledgeSyncEventResponse

```json
{
  "properties": {
    "chunk_count": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Chunk Count"
    },
    "content_changed": {
      "title": "Content Changed",
      "type": "boolean"
    },
    "content_hash": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Content Hash"
    },
    "created_at": {
      "format": "date-time",
      "title": "Created At",
      "type": "string"
    },
    "duration_ms": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Duration Ms"
    },
    "error_message": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Error Message"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "previous_hash": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Previous Hash"
    },
    "project_id": {
      "format": "uuid",
      "title": "Project Id",
      "type": "string"
    },
    "source_id": {
      "format": "uuid",
      "title": "Source Id",
      "type": "string"
    },
    "status": {
      "title": "Status",
      "type": "string"
    },
    "trigger": {
      "title": "Trigger",
      "type": "string"
    }
  },
  "required": [
    "id",
    "source_id",
    "project_id",
    "trigger",
    "status",
    "content_changed",
    "created_at"
  ],
  "title": "KnowledgeSyncEventResponse",
  "type": "object"
}
```

## LibraryCaseList

```json
{
  "properties": {
    "items": {
      "items": {
        "$ref": "#/components/schemas/LibraryCaseRow"
      },
      "title": "Items",
      "type": "array"
    },
    "measured": {
      "default": true,
      "title": "Measured",
      "type": "boolean"
    },
    "reason": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Reason"
    },
    "total": {
      "title": "Total",
      "type": "integer"
    }
  },
  "required": [
    "total",
    "items"
  ],
  "title": "LibraryCaseList",
  "type": "object"
}
```

## LibraryCaseRow

```json
{
  "properties": {
    "age_days": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Age Days"
    },
    "detail": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Detail"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "owner": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Owner"
    },
    "status": {
      "title": "Status",
      "type": "string"
    },
    "title": {
      "title": "Title",
      "type": "string"
    }
  },
  "required": [
    "id",
    "title",
    "status"
  ],
  "title": "LibraryCaseRow",
  "type": "object"
}
```

## LibraryHealthComponent

```json
{
  "properties": {
    "denominator": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Denominator"
    },
    "detail": {
      "title": "Detail",
      "type": "string"
    },
    "key": {
      "title": "Key",
      "type": "string"
    },
    "label": {
      "title": "Label",
      "type": "string"
    },
    "measured": {
      "title": "Measured",
      "type": "boolean"
    },
    "numerator": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Numerator"
    },
    "value": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Value"
    },
    "weight": {
      "title": "Weight",
      "type": "number"
    }
  },
  "required": [
    "key",
    "label",
    "weight",
    "measured",
    "detail"
  ],
  "title": "LibraryHealthComponent",
  "type": "object"
}
```

## LibraryHealthResponse

```json
{
  "properties": {
    "active_cases": {
      "title": "Active Cases",
      "type": "integer"
    },
    "automated_cases": {
      "title": "Automated Cases",
      "type": "integer"
    },
    "automation_target_pct": {
      "title": "Automation Target Pct",
      "type": "integer"
    },
    "avg_age_days": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Avg Age Days"
    },
    "components": {
      "items": {
        "$ref": "#/components/schemas/LibraryHealthComponent"
      },
      "title": "Components",
      "type": "array"
    },
    "computed_at": {
      "format": "date-time",
      "title": "Computed At",
      "type": "string"
    },
    "flaky_active": {
      "$ref": "#/components/schemas/LibraryCaseList"
    },
    "measured": {
      "title": "Measured",
      "type": "boolean"
    },
    "no_owner": {
      "$ref": "#/components/schemas/LibraryCaseList"
    },
    "older_than_180_days": {
      "default": 0,
      "title": "Older Than 180 Days",
      "type": "integer"
    },
    "older_than_180_pct": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Older Than 180 Pct"
    },
    "project_id": {
      "format": "uuid",
      "title": "Project Id",
      "type": "string"
    },
    "requirements": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/RequirementCoverageSummary"
        },
        {
          "type": "null"
        }
      ]
    },
    "retired_cases": {
      "title": "Retired Cases",
      "type": "integer"
    },
    "retired_in_open_plans": {
      "title": "Retired In Open Plans",
      "type": "integer"
    },
    "review_queue": {
      "$ref": "#/components/schemas/ReviewQueueSummary"
    },
    "score": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Score"
    },
    "stale_drafts": {
      "title": "Stale Drafts",
      "type": "integer"
    },
    "strategy_gaps": {
      "items": {
        "$ref": "#/components/schemas/StrategyGapRead"
      },
      "title": "Strategy Gaps",
      "type": "array"
    },
    "suite_names": {
      "items": {
        "type": "string"
      },
      "title": "Suite Names",
      "type": "array"
    },
    "suite_names_total": {
      "title": "Suite Names Total",
      "type": "integer"
    },
    "tag": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Tag"
    },
    "total_cases": {
      "title": "Total Cases",
      "type": "integer"
    }
  },
  "required": [
    "project_id",
    "computed_at",
    "measured",
    "total_cases",
    "active_cases",
    "retired_cases",
    "automated_cases",
    "automation_target_pct",
    "stale_drafts",
    "retired_in_open_plans",
    "components",
    "review_queue",
    "strategy_gaps",
    "flaky_active",
    "no_owner",
    "suite_names",
    "suite_names_total"
  ],
  "title": "LibraryHealthResponse",
  "type": "object"
}
```

## LinkRunRequest

```json
{
  "properties": {
    "phase_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Phase Id"
    },
    "test_run_id": {
      "title": "Test Run Id",
      "type": "string"
    }
  },
  "required": [
    "test_run_id"
  ],
  "title": "LinkRunRequest",
  "type": "object"
}
```

## LiveEvent

```json
{
  "description": "A single test execution event from a client machine.",
  "properties": {
    "call_ms": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "description": "The test body's own time, ms (0..24 h).",
      "title": "Call Ms"
    },
    "class_name": {
      "anyOf": [
        {
          "maxLength": 500,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Class Name"
    },
    "duration_ms": {
      "anyOf": [
        {
          "minimum": 0.0,
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Duration Ms"
    },
    "error_message": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Error Message"
    },
    "event_type": {
      "description": "run_start | test_start | test_result | log | metric | run_complete | live_heartbeat. live_heartbeat is a no-op refresh emitted by SDK clients during long inter-test gaps — it only bumps the Redis last_event_at field so the reaper doesn't close the session as idle.",
      "title": "Event Type",
      "type": "string"
    },
    "metadata": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Metadata"
    },
    "setup_ms": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "description": "Fixture / before-hook time of this test, ms (0..24 h).",
      "title": "Setup Ms"
    },
    "stack_trace": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Stack Trace"
    },
    "started_at_ms": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "description": "Client epoch ms when the test started (its setup included), sent on test_result.",
      "title": "Started At Ms"
    },
    "status": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "description": "PASSED | FAILED | SKIPPED | BROKEN",
      "title": "Status"
    },
    "suite_name": {
      "anyOf": [
        {
          "maxLength": 500,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Suite Name"
    },
    "tags": {
      "anyOf": [
        {
          "items": {
            "type": "string"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Tags"
    },
    "teardown_ms": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "description": "Fixture / after-hook time of this test, ms (0..24 h).",
      "title": "Teardown Ms"
    },
    "test_name": {
      "anyOf": [
        {
          "maxLength": 1000,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Test Name"
    },
    "timestamp_ms": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Timestamp Ms"
    },
    "worker_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "description": "The parallel worker / runner slot that ran the test (at most 64 characters).",
      "title": "Worker Id"
    }
  },
  "required": [
    "event_type"
  ],
  "title": "LiveEvent",
  "type": "object"
}
```

## LiveEventBatch

```json
{
  "description": "A batch of events sent from a client machine.\nBatching amortises HTTP overhead — 50–1000 events per call is recommended.",
  "properties": {
    "batch_id": {
      "anyOf": [
        {
          "maxLength": 255,
          "minLength": 1,
          "pattern": "^[^\\x00-\\x1f\\x7f]+$",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "description": "Client-generated identity for this batch. The same value must be reused for every HTTP retry; omitted for legacy callers.",
      "title": "Batch Id"
    },
    "events": {
      "items": {
        "$ref": "#/components/schemas/LiveEvent"
      },
      "maxItems": 1000,
      "minItems": 1,
      "title": "Events",
      "type": "array"
    },
    "run_id": {
      "maxLength": 100,
      "minLength": 1,
      "title": "Run Id",
      "type": "string"
    },
    "session_id": {
      "maxLength": 255,
      "minLength": 1,
      "title": "Session Id",
      "type": "string"
    }
  },
  "required": [
    "session_id",
    "run_id",
    "events"
  ],
  "title": "LiveEventBatch",
  "type": "object"
}
```

## LiveEventBatchResponse

```json
{
  "properties": {
    "accepted": {
      "title": "Accepted",
      "type": "integer"
    },
    "run_id": {
      "title": "Run Id",
      "type": "string"
    },
    "session_id": {
      "title": "Session Id",
      "type": "string"
    }
  },
  "required": [
    "accepted",
    "run_id",
    "session_id"
  ],
  "title": "LiveEventBatchResponse",
  "type": "object"
}
```

## LiveHttpExchangesIn

```json
{
  "description": "Body of POST /api/v1/stream/http-exchanges (§3.1 — no path id, on purpose): one test's exchanges\nof a live session, posted once by the reporter after the test's result.",
  "properties": {
    "class_name": {
      "anyOf": [
        {
          "maxLength": 500,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Class Name"
    },
    "exchanges": {
      "items": {
        "$ref": "#/components/schemas/HttpExchangeIn"
      },
      "maxItems": 20,
      "title": "Exchanges",
      "type": "array"
    },
    "exchanges_dropped": {
      "default": 0,
      "minimum": 0.0,
      "title": "Exchanges Dropped",
      "type": "integer"
    },
    "run_id": {
      "maxLength": 100,
      "minLength": 1,
      "title": "Run Id",
      "type": "string"
    },
    "session_id": {
      "maxLength": 255,
      "minLength": 1,
      "title": "Session Id",
      "type": "string"
    },
    "test_name": {
      "maxLength": 1000,
      "minLength": 1,
      "title": "Test Name",
      "type": "string"
    }
  },
  "required": [
    "session_id",
    "run_id",
    "test_name",
    "exchanges"
  ],
  "title": "LiveHttpExchangesIn",
  "type": "object"
}
```

## LiveHttpExchangesResponse

```json
{
  "properties": {
    "dropped": {
      "title": "Dropped",
      "type": "integer"
    },
    "stored": {
      "title": "Stored",
      "type": "integer"
    },
    "test_case_id": {
      "title": "Test Case Id",
      "type": "string"
    },
    "truncated": {
      "title": "Truncated",
      "type": "integer"
    }
  },
  "required": [
    "stored",
    "truncated",
    "dropped",
    "test_case_id"
  ],
  "title": "LiveHttpExchangesResponse",
  "type": "object"
}
```

## LiveSessionCreate

```json
{
  "description": "Request body to register a new live execution session.\n\n``project_id`` accepts either a project UUID *or* a human-readable project\nname (case-insensitive exact match). The server resolves it to a real UUID\nin ``stream_service.create_session``. Keeping the field name ``project_id``\npreserves wire compatibility with SDK callers that already map their\n``testlookup.project`` config (conventionally a name, à la\n``rp.project``) onto this field.",
  "properties": {
    "branch": {
      "anyOf": [
        {
          "maxLength": 255,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Branch"
    },
    "build_number": {
      "anyOf": [
        {
          "maxLength": 100,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Build Number"
    },
    "ci_actor": {
      "anyOf": [
        {
          "maxLength": 120,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Ci Actor"
    },
    "ci_provider": {
      "anyOf": [
        {
          "maxLength": 30,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Ci Provider"
    },
    "ci_repo": {
      "anyOf": [
        {
          "maxLength": 300,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Ci Repo"
    },
    "ci_run_url": {
      "anyOf": [
        {
          "maxLength": 1000,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Ci Run Url"
    },
    "client_name": {
      "maxLength": 255,
      "minLength": 1,
      "title": "Client Name",
      "type": "string"
    },
    "commit_hash": {
      "anyOf": [
        {
          "maxLength": 64,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Commit Hash"
    },
    "commit_range": {
      "anyOf": [
        {
          "items": {
            "$ref": "#/components/schemas/SuppliedCommit"
          },
          "type": "array"
        },
        {
          "$ref": "#/components/schemas/SuppliedCommitRange"
        },
        {
          "type": "null"
        }
      ],
      "title": "Commit Range"
    },
    "framework": {
      "anyOf": [
        {
          "maxLength": 50,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Framework"
    },
    "launch_name": {
      "anyOf": [
        {
          "maxLength": 255,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Launch Name"
    },
    "machine_id": {
      "anyOf": [
        {
          "maxLength": 255,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Machine Id"
    },
    "metadata": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Metadata"
    },
    "pr_number": {
      "anyOf": [
        {
          "minimum": 1.0,
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Pr Number"
    },
    "project_id": {
      "maxLength": 255,
      "minLength": 1,
      "title": "Project Id",
      "type": "string"
    },
    "release_name": {
      "anyOf": [
        {
          "maxLength": 255,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Release Name"
    },
    "run_context": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/RunContext"
        },
        {
          "type": "null"
        }
      ]
    },
    "suite_name": {
      "anyOf": [
        {
          "maxLength": 500,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Suite Name"
    },
    "total_tests": {
      "anyOf": [
        {
          "minimum": 0.0,
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Total Tests"
    }
  },
  "required": [
    "project_id",
    "client_name"
  ],
  "title": "LiveSessionCreate",
  "type": "object"
}
```

## LiveSessionResponse

```json
{
  "description": "Response returned when a session is created.",
  "properties": {
    "created_at": {
      "format": "date-time",
      "title": "Created At",
      "type": "string"
    },
    "expires_in": {
      "title": "Expires In",
      "type": "integer"
    },
    "project_id": {
      "title": "Project Id",
      "type": "string"
    },
    "run_id": {
      "title": "Run Id",
      "type": "string"
    },
    "session_id": {
      "title": "Session Id",
      "type": "string"
    },
    "session_token": {
      "title": "Session Token",
      "type": "string"
    }
  },
  "required": [
    "session_id",
    "session_token",
    "run_id",
    "project_id",
    "expires_in",
    "created_at"
  ],
  "title": "LiveSessionResponse",
  "type": "object"
}
```

## LiveSessionState

```json
{
  "description": "Live state of an active or recently completed session.",
  "properties": {
    "broken": {
      "title": "Broken",
      "type": "integer"
    },
    "build_number": {
      "title": "Build Number",
      "type": "string"
    },
    "client_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Client Name"
    },
    "completed_at": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Completed At"
    },
    "current_test": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Current Test"
    },
    "failed": {
      "title": "Failed",
      "type": "integer"
    },
    "last_event_at": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Last Event At"
    },
    "launch_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Launch Name"
    },
    "pass_rate": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Pass Rate"
    },
    "passed": {
      "title": "Passed",
      "type": "integer"
    },
    "project_id": {
      "title": "Project Id",
      "type": "string"
    },
    "release_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Release Name"
    },
    "run_id": {
      "title": "Run Id",
      "type": "string"
    },
    "run_seq": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Run Seq"
    },
    "skipped": {
      "title": "Skipped",
      "type": "integer"
    },
    "started_at": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Started At"
    },
    "status": {
      "title": "Status",
      "type": "string"
    },
    "suite_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Suite Name"
    },
    "test_run_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Test Run Id"
    },
    "total": {
      "title": "Total",
      "type": "integer"
    }
  },
  "required": [
    "run_id",
    "project_id",
    "build_number",
    "status",
    "total",
    "passed",
    "failed",
    "skipped",
    "broken"
  ],
  "title": "LiveSessionState",
  "type": "object"
}
```

## LiveStreamIngestRequest

```json
{
  "description": "API-key-authenticated streaming ingest. Server auto-manages the session.\n\nA client-chosen ``run_id`` (any stable identifier — CI build id, UUID, etc.)\nkeys the live session along with the API key's bound project. The first\ncall for a given ``(project_id, run_id)`` pair auto-creates the session;\nsubsequent calls reuse it. Clients never call ``/sessions`` themselves.",
  "properties": {
    "batch_id": {
      "anyOf": [
        {
          "maxLength": 255,
          "minLength": 1,
          "pattern": "^[^\\x00-\\x1f\\x7f]+$",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "description": "Client-generated identity for this batch. The same value must be reused for every HTTP retry; omitted for legacy callers.",
      "title": "Batch Id"
    },
    "events": {
      "items": {
        "$ref": "#/components/schemas/LiveEvent"
      },
      "maxItems": 1000,
      "minItems": 1,
      "title": "Events",
      "type": "array"
    },
    "meta": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/LiveStreamMeta"
        },
        {
          "type": "null"
        }
      ]
    },
    "run_id": {
      "maxLength": 100,
      "minLength": 1,
      "title": "Run Id",
      "type": "string"
    }
  },
  "required": [
    "run_id",
    "events"
  ],
  "title": "LiveStreamIngestRequest",
  "type": "object"
}
```

## LiveStreamIngestResponse

```json
{
  "properties": {
    "accepted": {
      "title": "Accepted",
      "type": "integer"
    },
    "created_session": {
      "title": "Created Session",
      "type": "boolean"
    },
    "run_id": {
      "title": "Run Id",
      "type": "string"
    },
    "session_id": {
      "title": "Session Id",
      "type": "string"
    }
  },
  "required": [
    "accepted",
    "run_id",
    "session_id",
    "created_session"
  ],
  "title": "LiveStreamIngestResponse",
  "type": "object"
}
```

## LiveStreamMeta

```json
{
  "description": "Optional CI/run metadata that enriches the auto-created session.\n\nAll fields are optional — when omitted the server falls back to the API\nkey's name (for client_name) and the run_id (for build_number).",
  "properties": {
    "branch": {
      "anyOf": [
        {
          "maxLength": 255,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Branch"
    },
    "build_number": {
      "anyOf": [
        {
          "maxLength": 100,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Build Number"
    },
    "commit_hash": {
      "anyOf": [
        {
          "maxLength": 64,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Commit Hash"
    },
    "framework": {
      "anyOf": [
        {
          "maxLength": 50,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Framework"
    },
    "launch_name": {
      "anyOf": [
        {
          "maxLength": 255,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Launch Name"
    },
    "machine_id": {
      "anyOf": [
        {
          "maxLength": 255,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Machine Id"
    },
    "metadata": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Metadata"
    },
    "release_name": {
      "anyOf": [
        {
          "maxLength": 255,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Release Name"
    },
    "run_context": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/RunContext"
        },
        {
          "type": "null"
        }
      ]
    },
    "total_tests": {
      "anyOf": [
        {
          "minimum": 0.0,
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Total Tests"
    }
  },
  "title": "LiveStreamMeta",
  "type": "object"
}
```

## LlmQuotaRead

```json
{
  "properties": {
    "at_cap_action": {
      "default": "AUTO_DOWNGRADE_TO_ML",
      "pattern": "^(SOFT_WARN|AUTO_DOWNGRADE_TO_ML|AUTO_DOWNGRADE_TO_RULES|HARD_BLOCK)$",
      "title": "At Cap Action",
      "type": "string"
    },
    "created_at": {
      "format": "date-time",
      "title": "Created At",
      "type": "string"
    },
    "enabled": {
      "default": true,
      "title": "Enabled",
      "type": "boolean"
    },
    "hard_cap_usd": {
      "default": 0.0,
      "minimum": 0.0,
      "title": "Hard Cap Usd",
      "type": "number"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "included_usd": {
      "default": 0.0,
      "minimum": 0.0,
      "title": "Included Usd",
      "type": "number"
    },
    "overage_rate_usd": {
      "default": 1.0,
      "minimum": 0.0,
      "title": "Overage Rate Usd",
      "type": "number"
    },
    "period_type": {
      "default": "MONTHLY",
      "pattern": "^(MONTHLY)$",
      "title": "Period Type",
      "type": "string"
    },
    "project_id": {
      "format": "uuid",
      "title": "Project Id",
      "type": "string"
    },
    "soft_warn_threshold_pct": {
      "default": 100,
      "maximum": 100.0,
      "minimum": 1.0,
      "title": "Soft Warn Threshold Pct",
      "type": "integer"
    },
    "updated_at": {
      "format": "date-time",
      "title": "Updated At",
      "type": "string"
    },
    "updated_by_user_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Updated By User Id"
    }
  },
  "required": [
    "id",
    "project_id",
    "created_at",
    "updated_at"
  ],
  "title": "LlmQuotaRead",
  "type": "object"
}
```

## LlmQuotaWrite

```json
{
  "description": "Admin-editable billing config for a project.",
  "properties": {
    "at_cap_action": {
      "default": "AUTO_DOWNGRADE_TO_ML",
      "pattern": "^(SOFT_WARN|AUTO_DOWNGRADE_TO_ML|AUTO_DOWNGRADE_TO_RULES|HARD_BLOCK)$",
      "title": "At Cap Action",
      "type": "string"
    },
    "enabled": {
      "default": true,
      "title": "Enabled",
      "type": "boolean"
    },
    "hard_cap_usd": {
      "default": 0.0,
      "minimum": 0.0,
      "title": "Hard Cap Usd",
      "type": "number"
    },
    "included_usd": {
      "default": 0.0,
      "minimum": 0.0,
      "title": "Included Usd",
      "type": "number"
    },
    "overage_rate_usd": {
      "default": 1.0,
      "minimum": 0.0,
      "title": "Overage Rate Usd",
      "type": "number"
    },
    "period_type": {
      "default": "MONTHLY",
      "pattern": "^(MONTHLY)$",
      "title": "Period Type",
      "type": "string"
    },
    "soft_warn_threshold_pct": {
      "default": 100,
      "maximum": 100.0,
      "minimum": 1.0,
      "title": "Soft Warn Threshold Pct",
      "type": "integer"
    }
  },
  "title": "LlmQuotaWrite",
  "type": "object"
}
```

## LlmUsageHistoryEntry

```json
{
  "properties": {
    "cap_hits": {
      "title": "Cap Hits",
      "type": "integer"
    },
    "period_end": {
      "format": "date-time",
      "title": "Period End",
      "type": "string"
    },
    "period_start": {
      "format": "date-time",
      "title": "Period Start",
      "type": "string"
    },
    "total_cost_usd": {
      "title": "Total Cost Usd",
      "type": "number"
    },
    "total_llm_calls": {
      "title": "Total Llm Calls",
      "type": "integer"
    }
  },
  "required": [
    "period_start",
    "period_end",
    "total_cost_usd",
    "total_llm_calls",
    "cap_hits"
  ],
  "title": "LlmUsageHistoryEntry",
  "type": "object"
}
```

## LlmUsageRead

```json
{
  "properties": {
    "cap_hits": {
      "title": "Cap Hits",
      "type": "integer"
    },
    "covered_usd": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Covered Usd"
    },
    "hard_cap_usd": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Hard Cap Usd"
    },
    "included_usd": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Included Usd"
    },
    "overage_charge_usd": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Overage Charge Usd"
    },
    "overage_rate_usd": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Overage Rate Usd"
    },
    "overage_usd": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Overage Usd"
    },
    "period_end": {
      "format": "date-time",
      "title": "Period End",
      "type": "string"
    },
    "period_start": {
      "format": "date-time",
      "title": "Period Start",
      "type": "string"
    },
    "project_id": {
      "format": "uuid",
      "title": "Project Id",
      "type": "string"
    },
    "projected_overage_charge_usd": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Projected Overage Charge Usd"
    },
    "projected_overage_usd": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Projected Overage Usd"
    },
    "projected_period_cost_usd": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Projected Period Cost Usd"
    },
    "projection_basis": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Projection Basis"
    },
    "status": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Status"
    },
    "total_cost_usd": {
      "title": "Total Cost Usd",
      "type": "number"
    },
    "total_input_tokens": {
      "title": "Total Input Tokens",
      "type": "integer"
    },
    "total_llm_calls": {
      "title": "Total Llm Calls",
      "type": "integer"
    },
    "total_output_tokens": {
      "title": "Total Output Tokens",
      "type": "integer"
    },
    "utilization_pct": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Utilization Pct"
    }
  },
  "required": [
    "project_id",
    "period_start",
    "period_end",
    "total_cost_usd",
    "total_input_tokens",
    "total_output_tokens",
    "total_llm_calls",
    "cap_hits"
  ],
  "title": "LlmUsageRead",
  "type": "object"
}
```

## MLLastRun

```json
{
  "description": "The outcome of the most recent training attempt (``ml:retrain:last``).",
  "properties": {
    "accuracy": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Accuracy"
    },
    "finished_at": {
      "format": "date-time",
      "title": "Finished At",
      "type": "string"
    },
    "message": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Message"
    },
    "sample_count": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Sample Count"
    },
    "status": {
      "title": "Status",
      "type": "string"
    },
    "triggered_by": {
      "title": "Triggered By",
      "type": "string"
    },
    "version": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Version"
    }
  },
  "required": [
    "status",
    "finished_at",
    "triggered_by"
  ],
  "title": "MLLastRun",
  "type": "object"
}
```

## MLRetrainAccepted

```json
{
  "description": "``202`` of ``POST /settings/ai/ml/retrain``.",
  "properties": {
    "already_running": {
      "title": "Already Running",
      "type": "boolean"
    },
    "state": {
      "title": "State",
      "type": "string"
    },
    "task_id": {
      "title": "Task Id",
      "type": "string"
    }
  },
  "required": [
    "task_id",
    "state",
    "already_running"
  ],
  "title": "MLRetrainAccepted",
  "type": "object"
}
```

## MLStatusRead

```json
{
  "description": "What the Settings > AI ML card shows (QA lead and above).",
  "properties": {
    "accuracy": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Accuracy"
    },
    "available_samples": {
      "title": "Available Samples",
      "type": "integer"
    },
    "can_train": {
      "title": "Can Train",
      "type": "boolean"
    },
    "cannot_train_message": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Cannot Train Message"
    },
    "cannot_train_reason": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Cannot Train Reason"
    },
    "deps_installed": {
      "title": "Deps Installed",
      "type": "boolean"
    },
    "human_label_floor": {
      "title": "Human Label Floor",
      "type": "integer"
    },
    "human_labels": {
      "title": "Human Labels",
      "type": "integer"
    },
    "last_run": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/MLLastRun"
        },
        {
          "type": "null"
        }
      ]
    },
    "maturity": {
      "title": "Maturity",
      "type": "string"
    },
    "min_training_samples": {
      "title": "Min Training Samples",
      "type": "integer"
    },
    "model_available": {
      "title": "Model Available",
      "type": "boolean"
    },
    "model_sample_count": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Model Sample Count"
    },
    "retrain_enabled": {
      "title": "Retrain Enabled",
      "type": "boolean"
    },
    "trained_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Trained At"
    },
    "training": {
      "$ref": "#/components/schemas/MLTrainingState"
    },
    "version": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Version"
    }
  },
  "required": [
    "model_available",
    "available_samples",
    "min_training_samples",
    "human_labels",
    "human_label_floor",
    "maturity",
    "deps_installed",
    "retrain_enabled",
    "can_train",
    "training"
  ],
  "title": "MLStatusRead",
  "type": "object"
}
```

## MLTrainingState

```json
{
  "description": "Whether a training job holds the retrain lock right now.",
  "properties": {
    "queued_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Queued At"
    },
    "started_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Started At"
    },
    "state": {
      "title": "State",
      "type": "string"
    },
    "task_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Task Id"
    },
    "triggered_by": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Triggered By"
    }
  },
  "required": [
    "state"
  ],
  "title": "MLTrainingState",
  "type": "object"
}
```

## ManagedTestCaseCreate

```json
{
  "properties": {
    "assignee_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Assignee Id"
    },
    "automation_status": {
      "default": "not_automated",
      "title": "Automation Status",
      "type": "string"
    },
    "description": {
      "anyOf": [
        {
          "maxLength": 50000,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Description"
    },
    "estimated_duration_minutes": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Estimated Duration Minutes"
    },
    "expected_result": {
      "anyOf": [
        {
          "maxLength": 50000,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Expected Result"
    },
    "feature_area": {
      "anyOf": [
        {
          "maxLength": 500,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Feature Area"
    },
    "is_automated": {
      "default": false,
      "title": "Is Automated",
      "type": "boolean"
    },
    "objective": {
      "anyOf": [
        {
          "maxLength": 50000,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Objective"
    },
    "parameters": {
      "anyOf": [
        {
          "items": {
            "$ref": "#/components/schemas/TestCaseParameterSchema"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Parameters"
    },
    "preconditions": {
      "anyOf": [
        {
          "maxLength": 50000,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Preconditions"
    },
    "priority": {
      "default": "medium",
      "title": "Priority",
      "type": "string"
    },
    "project_id": {
      "format": "uuid",
      "title": "Project Id",
      "type": "string"
    },
    "severity": {
      "default": "major",
      "title": "Severity",
      "type": "string"
    },
    "steps": {
      "anyOf": [
        {
          "items": {
            "$ref": "#/components/schemas/TestCaseStepSchema"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Steps"
    },
    "suite_name": {
      "anyOf": [
        {
          "maxLength": 500,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Suite Name"
    },
    "tags": {
      "anyOf": [
        {
          "items": {
            "type": "string"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Tags"
    },
    "test_data": {
      "anyOf": [
        {
          "maxLength": 50000,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Test Data"
    },
    "test_type": {
      "default": "functional",
      "title": "Test Type",
      "type": "string"
    },
    "title": {
      "maxLength": 500,
      "minLength": 3,
      "title": "Title",
      "type": "string"
    }
  },
  "required": [
    "project_id",
    "title"
  ],
  "title": "ManagedTestCaseCreate",
  "type": "object"
}
```

## ManagedTestCaseListResponse

```json
{
  "properties": {
    "automation_rows_excluded": {
      "default": false,
      "title": "Automation Rows Excluded",
      "type": "boolean"
    },
    "items": {
      "items": {
        "$ref": "#/components/schemas/ManagedTestCaseResponse"
      },
      "title": "Items",
      "type": "array"
    },
    "page": {
      "title": "Page",
      "type": "integer"
    },
    "pages": {
      "title": "Pages",
      "type": "integer"
    },
    "size": {
      "title": "Size",
      "type": "integer"
    },
    "total": {
      "title": "Total",
      "type": "integer"
    }
  },
  "required": [
    "items",
    "total",
    "page",
    "size",
    "pages"
  ],
  "title": "ManagedTestCaseListResponse",
  "type": "object"
}
```

## ManagedTestCaseResponse

```json
{
  "properties": {
    "ai_generated": {
      "title": "Ai Generated",
      "type": "boolean"
    },
    "ai_quality_score": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Ai Quality Score"
    },
    "ai_review_notes": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Ai Review Notes"
    },
    "allowed_actions": {
      "items": {
        "type": "string"
      },
      "title": "Allowed Actions",
      "type": "array"
    },
    "approved_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Approved At"
    },
    "approved_by_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Approved By Id"
    },
    "archived_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Archived At"
    },
    "archived_by_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Archived By Id"
    },
    "assignee_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Assignee Id"
    },
    "author_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Author Id"
    },
    "automation_status": {
      "title": "Automation Status",
      "type": "string"
    },
    "canonical_test_case_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Canonical Test Case Id"
    },
    "created_at": {
      "format": "date-time",
      "title": "Created At",
      "type": "string"
    },
    "deprecated_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Deprecated At"
    },
    "deprecated_by_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Deprecated By Id"
    },
    "deprecation_reason": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Deprecation Reason"
    },
    "description": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Description"
    },
    "edited_after_approval": {
      "default": false,
      "title": "Edited After Approval",
      "type": "boolean"
    },
    "edited_since_approval_version": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Edited Since Approval Version"
    },
    "estimated_duration_minutes": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Estimated Duration Minutes"
    },
    "expected_result": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Expected Result"
    },
    "feature_area": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Feature Area"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "is_automated": {
      "title": "Is Automated",
      "type": "boolean"
    },
    "last_executed_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Last Executed At"
    },
    "last_execution_status": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Last Execution Status"
    },
    "latest_run_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Latest Run Id"
    },
    "latest_test_case_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Latest Test Case Id"
    },
    "lifecycle_state_changed_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Lifecycle State Changed At"
    },
    "needs_update_reason": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Needs Update Reason"
    },
    "objective": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Objective"
    },
    "owner": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Owner"
    },
    "parameters": {
      "anyOf": [
        {
          "items": {
            "$ref": "#/components/schemas/TestCaseParameterSchema"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Parameters"
    },
    "preconditions": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Preconditions"
    },
    "priority": {
      "title": "Priority",
      "type": "string"
    },
    "project_id": {
      "format": "uuid",
      "title": "Project Id",
      "type": "string"
    },
    "reason_required_actions": {
      "items": {
        "type": "string"
      },
      "title": "Reason Required Actions",
      "type": "array"
    },
    "review_required": {
      "default": false,
      "title": "Review Required",
      "type": "boolean"
    },
    "reviewer_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Reviewer Id"
    },
    "severity": {
      "title": "Severity",
      "type": "string"
    },
    "source": {
      "default": "managed",
      "title": "Source",
      "type": "string"
    },
    "status": {
      "title": "Status",
      "type": "string"
    },
    "steps": {
      "anyOf": [
        {
          "items": {
            "$ref": "#/components/schemas/TestCaseStepSchema"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Steps"
    },
    "suite_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Suite Name"
    },
    "tags": {
      "anyOf": [
        {
          "items": {},
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Tags"
    },
    "test_data": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Test Data"
    },
    "test_fingerprint": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Test Fingerprint"
    },
    "test_suite_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Test Suite Id"
    },
    "test_type": {
      "title": "Test Type",
      "type": "string"
    },
    "title": {
      "title": "Title",
      "type": "string"
    },
    "updated_at": {
      "format": "date-time",
      "title": "Updated At",
      "type": "string"
    },
    "version": {
      "title": "Version",
      "type": "integer"
    }
  },
  "required": [
    "id",
    "project_id",
    "title",
    "test_type",
    "priority",
    "severity",
    "status",
    "version",
    "is_automated",
    "automation_status",
    "ai_generated",
    "created_at",
    "updated_at"
  ],
  "title": "ManagedTestCaseResponse",
  "type": "object"
}
```

## ManagedTestCaseUpdate

```json
{
  "properties": {
    "assignee_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Assignee Id"
    },
    "automation_status": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Automation Status"
    },
    "change_summary": {
      "anyOf": [
        {
          "maxLength": 500,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Change Summary"
    },
    "description": {
      "anyOf": [
        {
          "maxLength": 50000,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Description"
    },
    "estimated_duration_minutes": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Estimated Duration Minutes"
    },
    "expected_result": {
      "anyOf": [
        {
          "maxLength": 50000,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Expected Result"
    },
    "expected_version": {
      "minimum": 1.0,
      "title": "Expected Version",
      "type": "integer"
    },
    "feature_area": {
      "anyOf": [
        {
          "maxLength": 500,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Feature Area"
    },
    "is_automated": {
      "anyOf": [
        {
          "type": "boolean"
        },
        {
          "type": "null"
        }
      ],
      "title": "Is Automated"
    },
    "objective": {
      "anyOf": [
        {
          "maxLength": 50000,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Objective"
    },
    "parameters": {
      "anyOf": [
        {
          "items": {
            "$ref": "#/components/schemas/TestCaseParameterSchema"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Parameters"
    },
    "preconditions": {
      "anyOf": [
        {
          "maxLength": 50000,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Preconditions"
    },
    "priority": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Priority"
    },
    "severity": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Severity"
    },
    "steps": {
      "anyOf": [
        {
          "items": {
            "$ref": "#/components/schemas/TestCaseStepSchema"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Steps"
    },
    "suite_name": {
      "anyOf": [
        {
          "maxLength": 500,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Suite Name"
    },
    "tags": {
      "anyOf": [
        {
          "items": {
            "type": "string"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Tags"
    },
    "test_data": {
      "anyOf": [
        {
          "maxLength": 50000,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Test Data"
    },
    "test_type": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Test Type"
    },
    "title": {
      "anyOf": [
        {
          "maxLength": 500,
          "minLength": 3,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Title"
    }
  },
  "required": [
    "expected_version"
  ],
  "title": "ManagedTestCaseUpdate",
  "type": "object"
}
```

## MemoryReference

```json
{
  "description": "Canonical pointer from generated output back to an auditable memory row.",
  "properties": {
    "entity_id": {
      "title": "Entity Id",
      "type": "string"
    },
    "entity_type": {
      "title": "Entity Type",
      "type": "string"
    },
    "evidence_refs": {
      "items": {
        "additionalProperties": true,
        "type": "object"
      },
      "title": "Evidence Refs",
      "type": "array"
    },
    "memory_entry_id": {
      "format": "uuid",
      "title": "Memory Entry Id",
      "type": "string"
    },
    "memory_reference_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Memory Reference Id"
    },
    "payload_sha256": {
      "title": "Payload Sha256",
      "type": "string"
    },
    "retrieval_audit": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Retrieval Audit"
    },
    "retrieval_audit_sha256": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Retrieval Audit Sha256"
    },
    "source_snapshot_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Source Snapshot Id"
    }
  },
  "required": [
    "memory_entry_id",
    "entity_type",
    "entity_id",
    "payload_sha256"
  ],
  "title": "MemoryReference",
  "type": "object"
}
```

## MemoryTimelineResponse

```json
{
  "description": "Timeline of memory entries for a run, grouped by entity type.",
  "properties": {
    "entries_by_type": {
      "additionalProperties": true,
      "title": "Entries By Type",
      "type": "object"
    },
    "project_id": {
      "format": "uuid",
      "title": "Project Id",
      "type": "string"
    },
    "run_id": {
      "format": "uuid",
      "title": "Run Id",
      "type": "string"
    },
    "total_entries": {
      "title": "Total Entries",
      "type": "integer"
    }
  },
  "required": [
    "run_id",
    "project_id",
    "entries_by_type",
    "total_entries"
  ],
  "title": "MemoryTimelineResponse",
  "type": "object"
}
```

## MfaChallengeResponse

```json
{
  "description": "Password accepted; a second factor is required to finish.\n\n``challenge_token`` is NOT an access token — it carries ``type:\n\"mfa_challenge\"`` and is rejected by ``get_current_user`` at the decode\nlayer. It is only accepted by ``POST /auth/mfa/verify``.",
  "properties": {
    "challenge_token": {
      "title": "Challenge Token",
      "type": "string"
    },
    "expires_in": {
      "title": "Expires In",
      "type": "integer"
    },
    "methods": {
      "items": {
        "type": "string"
      },
      "title": "Methods",
      "type": "array"
    },
    "mfa_required": {
      "const": true,
      "default": true,
      "title": "Mfa Required",
      "type": "boolean"
    }
  },
  "required": [
    "challenge_token",
    "expires_in"
  ],
  "title": "MfaChallengeResponse",
  "type": "object"
}
```

## MfaDisableRequest

```json
{
  "description": "Disabling requires the password *and* a live second factor.\n\nPassword alone would let anyone holding a stolen session strip the factor\nthat session was supposed to be protected by.",
  "properties": {
    "code": {
      "anyOf": [
        {
          "maxLength": 12,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Code"
    },
    "password": {
      "maxLength": 128,
      "title": "Password",
      "type": "string"
    },
    "recovery_code": {
      "anyOf": [
        {
          "maxLength": 64,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Recovery Code"
    }
  },
  "required": [
    "password"
  ],
  "title": "MfaDisableRequest",
  "type": "object"
}
```

## MfaEnrollConfirmRequest

```json
{
  "properties": {
    "code": {
      "maxLength": 12,
      "title": "Code",
      "type": "string"
    },
    "enrollment_token": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Enrollment Token"
    }
  },
  "required": [
    "code"
  ],
  "title": "MfaEnrollConfirmRequest",
  "type": "object"
}
```

## MfaEnrollConfirmResponse

```json
{
  "properties": {
    "enabled": {
      "const": true,
      "default": true,
      "title": "Enabled",
      "type": "boolean"
    },
    "recovery_codes": {
      "items": {
        "type": "string"
      },
      "title": "Recovery Codes",
      "type": "array"
    },
    "tokens": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/TokenResponse"
        },
        {
          "type": "null"
        }
      ]
    }
  },
  "required": [
    "recovery_codes"
  ],
  "title": "MfaEnrollConfirmResponse",
  "type": "object"
}
```

## MfaEnrollStartRequest

```json
{
  "properties": {
    "enrollment_token": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Enrollment Token"
    }
  },
  "title": "MfaEnrollStartRequest",
  "type": "object"
}
```

## MfaEnrollStartResponse

```json
{
  "properties": {
    "account_name": {
      "title": "Account Name",
      "type": "string"
    },
    "digits": {
      "title": "Digits",
      "type": "integer"
    },
    "issuer": {
      "title": "Issuer",
      "type": "string"
    },
    "otpauth_uri": {
      "title": "Otpauth Uri",
      "type": "string"
    },
    "period_seconds": {
      "title": "Period Seconds",
      "type": "integer"
    },
    "secret": {
      "title": "Secret",
      "type": "string"
    }
  },
  "required": [
    "secret",
    "otpauth_uri",
    "issuer",
    "account_name",
    "digits",
    "period_seconds"
  ],
  "title": "MfaEnrollStartResponse",
  "type": "object"
}
```

## MfaEnrollmentRequiredResponse

```json
{
  "description": "Password accepted; workspace policy requires MFA and the user has none.\n\n``enrollment_token`` carries ``type: \"mfa_enroll\"`` and is accepted only by\nthe two enrollment endpoints.",
  "properties": {
    "enrollment_token": {
      "title": "Enrollment Token",
      "type": "string"
    },
    "expires_in": {
      "title": "Expires In",
      "type": "integer"
    },
    "mfa_enrollment_required": {
      "const": true,
      "default": true,
      "title": "Mfa Enrollment Required",
      "type": "boolean"
    },
    "required_for_role": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Required For Role"
    }
  },
  "required": [
    "enrollment_token",
    "expires_in"
  ],
  "title": "MfaEnrollmentRequiredResponse",
  "type": "object"
}
```

## MfaPolicyRead

```json
{
  "properties": {
    "lockout_duration_minutes": {
      "title": "Lockout Duration Minutes",
      "type": "integer"
    },
    "lockout_enabled": {
      "title": "Lockout Enabled",
      "type": "boolean"
    },
    "lockout_threshold": {
      "title": "Lockout Threshold",
      "type": "integer"
    },
    "require_mfa": {
      "title": "Require Mfa",
      "type": "boolean"
    },
    "required_for_role": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/UserRole"
        },
        {
          "type": "null"
        }
      ]
    }
  },
  "required": [
    "require_mfa",
    "lockout_enabled",
    "lockout_threshold",
    "lockout_duration_minutes"
  ],
  "title": "MfaPolicyRead",
  "type": "object"
}
```

## MfaPolicyUpdate

```json
{
  "properties": {
    "clear_required_for_role": {
      "default": false,
      "title": "Clear Required For Role",
      "type": "boolean"
    },
    "lockout_duration_minutes": {
      "anyOf": [
        {
          "maximum": 1440.0,
          "minimum": 1.0,
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Lockout Duration Minutes"
    },
    "lockout_enabled": {
      "anyOf": [
        {
          "type": "boolean"
        },
        {
          "type": "null"
        }
      ],
      "title": "Lockout Enabled"
    },
    "lockout_threshold": {
      "anyOf": [
        {
          "maximum": 100.0,
          "minimum": 3.0,
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Lockout Threshold"
    },
    "require_mfa": {
      "anyOf": [
        {
          "type": "boolean"
        },
        {
          "type": "null"
        }
      ],
      "title": "Require Mfa"
    },
    "required_for_role": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/UserRole"
        },
        {
          "type": "null"
        }
      ]
    }
  },
  "title": "MfaPolicyUpdate",
  "type": "object"
}
```

## MfaRecoveryCodesRequest

```json
{
  "properties": {
    "code": {
      "anyOf": [
        {
          "maxLength": 12,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Code"
    },
    "password": {
      "maxLength": 128,
      "title": "Password",
      "type": "string"
    },
    "recovery_code": {
      "anyOf": [
        {
          "maxLength": 64,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Recovery Code"
    }
  },
  "required": [
    "password"
  ],
  "title": "MfaRecoveryCodesRequest",
  "type": "object"
}
```

## MfaRecoveryCodesResponse

```json
{
  "properties": {
    "recovery_codes": {
      "items": {
        "type": "string"
      },
      "title": "Recovery Codes",
      "type": "array"
    }
  },
  "required": [
    "recovery_codes"
  ],
  "title": "MfaRecoveryCodesResponse",
  "type": "object"
}
```

## MfaStatusResponse

```json
{
  "properties": {
    "enabled": {
      "title": "Enabled",
      "type": "boolean"
    },
    "enrolled_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Enrolled At"
    },
    "recovery_codes_remaining": {
      "default": 0,
      "title": "Recovery Codes Remaining",
      "type": "integer"
    },
    "required_by_policy": {
      "default": false,
      "title": "Required By Policy",
      "type": "boolean"
    },
    "secret_unreadable": {
      "default": false,
      "title": "Secret Unreadable",
      "type": "boolean"
    },
    "sso_managed": {
      "default": false,
      "title": "Sso Managed",
      "type": "boolean"
    }
  },
  "required": [
    "enabled"
  ],
  "title": "MfaStatusResponse",
  "type": "object"
}
```

## MfaVerifyRequest

```json
{
  "properties": {
    "challenge_token": {
      "title": "Challenge Token",
      "type": "string"
    },
    "code": {
      "anyOf": [
        {
          "maxLength": 12,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Code"
    },
    "recovery_code": {
      "anyOf": [
        {
          "maxLength": 64,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Recovery Code"
    }
  },
  "required": [
    "challenge_token"
  ],
  "title": "MfaVerifyRequest",
  "type": "object"
}
```

## ModelConfig

```json
{
  "additionalProperties": false,
  "properties": {
    "escalation": {
      "$ref": "#/components/schemas/EscalationConfig"
    },
    "llm": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/ModelEndpointConfig"
        },
        {
          "type": "null"
        }
      ]
    },
    "slm": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/ModelEndpointConfig"
        },
        {
          "type": "null"
        }
      ]
    },
    "tier": {
      "default": "auto",
      "enum": [
        "auto",
        "deterministic",
        "slm",
        "llm"
      ],
      "title": "Tier",
      "type": "string"
    }
  },
  "title": "ModelConfig",
  "type": "object"
}
```

## ModelEndpointConfig

```json
{
  "additionalProperties": false,
  "description": "One tier's model. Provider endpoints come only from the environment.",
  "properties": {
    "max_tokens": {
      "default": 1024,
      "maximum": 200000.0,
      "minimum": 1.0,
      "title": "Max Tokens",
      "type": "integer"
    },
    "model": {
      "maxLength": 200,
      "minLength": 1,
      "title": "Model",
      "type": "string"
    },
    "provider": {
      "maxLength": 40,
      "minLength": 1,
      "title": "Provider",
      "type": "string"
    },
    "temperature": {
      "default": 0.0,
      "maximum": 2.0,
      "minimum": 0.0,
      "title": "Temperature",
      "type": "number"
    }
  },
  "required": [
    "provider",
    "model"
  ],
  "title": "ModelEndpointConfig",
  "type": "object"
}
```

## MutationClass

```json
{
  "enum": [
    "wrong_label",
    "missing_required_field",
    "unsupported_claim",
    "unsupported_causal_claim",
    "plausible_wrong_category",
    "correct_numbers_wrong_conclusion"
  ],
  "title": "MutationClass",
  "type": "string"
}
```

## MyFailureItem

```json
{
  "description": "A single auto-assigned failure surfaced on the calling user's inbox.\n\nCarries enough context to render a triage row without a follow-up fetch:\ntest name + suite + run identity + project label + relative age. The\n``navigation_url`` is the canonical deep link to the run-detail page's\ntest-case drawer.",
  "properties": {
    "assignment_reason": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Assignment Reason"
    },
    "build_number": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Build Number"
    },
    "class_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Class Name"
    },
    "created_at": {
      "format": "date-time",
      "title": "Created At",
      "type": "string"
    },
    "duration_ms": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Duration Ms"
    },
    "error_message": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Error Message"
    },
    "failure_category": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Failure Category"
    },
    "failure_count": {
      "default": 1,
      "title": "Failure Count",
      "type": "integer"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "last_failure_step": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Last Failure Step"
    },
    "navigation_url": {
      "title": "Navigation Url",
      "type": "string"
    },
    "project_id": {
      "format": "uuid",
      "title": "Project Id",
      "type": "string"
    },
    "project_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Project Name"
    },
    "run_seq": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Run Seq"
    },
    "severity": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Severity"
    },
    "status": {
      "$ref": "#/components/schemas/TestStatus"
    },
    "suite_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Suite Name"
    },
    "test_name": {
      "title": "Test Name",
      "type": "string"
    },
    "test_run_id": {
      "format": "uuid",
      "title": "Test Run Id",
      "type": "string"
    },
    "triage_notes": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Triage Notes"
    },
    "triage_status": {
      "default": "PENDING_REVIEW",
      "title": "Triage Status",
      "type": "string"
    }
  },
  "required": [
    "id",
    "test_name",
    "status",
    "created_at",
    "test_run_id",
    "project_id",
    "navigation_url"
  ],
  "title": "MyFailureItem",
  "type": "object"
}
```

## MyFailureListResponse

```json
{
  "properties": {
    "items": {
      "items": {
        "$ref": "#/components/schemas/MyFailureItem"
      },
      "title": "Items",
      "type": "array"
    },
    "page": {
      "title": "Page",
      "type": "integer"
    },
    "pages": {
      "title": "Pages",
      "type": "integer"
    },
    "size": {
      "title": "Size",
      "type": "integer"
    },
    "total": {
      "title": "Total",
      "type": "integer"
    },
    "unresolved_total": {
      "title": "Unresolved Total",
      "type": "integer"
    }
  },
  "required": [
    "items",
    "total",
    "page",
    "size",
    "pages",
    "unresolved_total"
  ],
  "title": "MyFailureListResponse",
  "type": "object"
}
```

## NotificationChannel

```json
{
  "enum": [
    "email",
    "slack",
    "teams"
  ],
  "title": "NotificationChannel",
  "type": "string"
}
```

## NotificationLogResponse

```json
{
  "properties": {
    "body": {
      "title": "Body",
      "type": "string"
    },
    "channel": {
      "title": "Channel",
      "type": "string"
    },
    "created_at": {
      "format": "date-time",
      "title": "Created At",
      "type": "string"
    },
    "event_type": {
      "title": "Event Type",
      "type": "string"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "is_read": {
      "title": "Is Read",
      "type": "boolean"
    },
    "link": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Link"
    },
    "sent_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Sent At"
    },
    "status": {
      "title": "Status",
      "type": "string"
    },
    "title": {
      "title": "Title",
      "type": "string"
    }
  },
  "required": [
    "id",
    "channel",
    "event_type",
    "title",
    "body",
    "status",
    "is_read",
    "sent_at",
    "created_at"
  ],
  "title": "NotificationLogResponse",
  "type": "object"
}
```

## NotificationPreferenceCreate

```json
{
  "description": "Create or replace a single channel preference.",
  "properties": {
    "channel": {
      "$ref": "#/components/schemas/NotificationChannel"
    },
    "email_override": {
      "anyOf": [
        {
          "format": "email",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Email Override"
    },
    "enabled": {
      "default": true,
      "title": "Enabled",
      "type": "boolean"
    },
    "events": {
      "description": "List of NotificationEventType values",
      "items": {
        "type": "string"
      },
      "title": "Events",
      "type": "array"
    },
    "failure_rate_threshold": {
      "default": 80.0,
      "maximum": 100.0,
      "minimum": 0.0,
      "title": "Failure Rate Threshold",
      "type": "number"
    },
    "project_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Project Id"
    },
    "slack_webhook_url": {
      "anyOf": [
        {
          "maxLength": 2000,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Slack Webhook Url"
    },
    "teams_webhook_url": {
      "anyOf": [
        {
          "maxLength": 2000,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Teams Webhook Url"
    }
  },
  "required": [
    "channel"
  ],
  "title": "NotificationPreferenceCreate",
  "type": "object"
}
```

## NotificationPreferenceResponse

```json
{
  "properties": {
    "channel": {
      "title": "Channel",
      "type": "string"
    },
    "created_at": {
      "format": "date-time",
      "title": "Created At",
      "type": "string"
    },
    "email_override": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Email Override"
    },
    "enabled": {
      "title": "Enabled",
      "type": "boolean"
    },
    "events": {
      "items": {
        "type": "string"
      },
      "title": "Events",
      "type": "array"
    },
    "failure_rate_threshold": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Failure Rate Threshold"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "project_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Project Id"
    },
    "slack_webhook_url": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Slack Webhook Url"
    },
    "teams_webhook_url": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Teams Webhook Url"
    },
    "updated_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Updated At"
    },
    "user_id": {
      "format": "uuid",
      "title": "User Id",
      "type": "string"
    }
  },
  "required": [
    "id",
    "user_id",
    "project_id",
    "channel",
    "enabled",
    "events",
    "failure_rate_threshold",
    "email_override",
    "slack_webhook_url",
    "teams_webhook_url",
    "created_at"
  ],
  "title": "NotificationPreferenceResponse",
  "type": "object"
}
```

## NotificationTransitionPolicyResponse

```json
{
  "properties": {
    "consecutive_failure_threshold": {
      "title": "Consecutive Failure Threshold",
      "type": "integer"
    },
    "enabled_events": {
      "items": {
        "type": "string"
      },
      "title": "Enabled Events",
      "type": "array"
    },
    "is_default": {
      "default": false,
      "title": "Is Default",
      "type": "boolean"
    },
    "per_run_events_enabled": {
      "title": "Per Run Events Enabled",
      "type": "boolean"
    },
    "project_id": {
      "format": "uuid",
      "title": "Project Id",
      "type": "string"
    },
    "transitions_enabled": {
      "title": "Transitions Enabled",
      "type": "boolean"
    }
  },
  "required": [
    "project_id",
    "transitions_enabled",
    "per_run_events_enabled",
    "enabled_events",
    "consecutive_failure_threshold"
  ],
  "title": "NotificationTransitionPolicyResponse",
  "type": "object"
}
```

## NotificationTransitionPolicyUpdate

```json
{
  "description": "Per-project transition-notification policy (PMF US-7.1).",
  "properties": {
    "consecutive_failure_threshold": {
      "default": 2,
      "maximum": 20.0,
      "minimum": 1.0,
      "title": "Consecutive Failure Threshold",
      "type": "integer"
    },
    "enabled_events": {
      "description": "Transition NotificationEventType values enabled for the project",
      "items": {
        "type": "string"
      },
      "title": "Enabled Events",
      "type": "array"
    },
    "per_run_events_enabled": {
      "default": false,
      "title": "Per Run Events Enabled",
      "type": "boolean"
    },
    "transitions_enabled": {
      "default": true,
      "title": "Transitions Enabled",
      "type": "boolean"
    }
  },
  "title": "NotificationTransitionPolicyUpdate",
  "type": "object"
}
```

## NotifyTestOwnerRequest

```json
{
  "description": "POST body for /api/v1/analytics/notify-owner — fires an email at the\nsuite owner of the test that's been failing repeatedly.",
  "properties": {
    "days": {
      "default": 30,
      "maximum": 365.0,
      "minimum": 1.0,
      "title": "Days",
      "type": "integer"
    },
    "fail_count": {
      "anyOf": [
        {
          "maximum": 10000.0,
          "minimum": 0.0,
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Fail Count"
    },
    "project_id": {
      "format": "uuid",
      "title": "Project Id",
      "type": "string"
    },
    "test_name": {
      "maxLength": 1000,
      "minLength": 1,
      "title": "Test Name",
      "type": "string"
    }
  },
  "required": [
    "project_id",
    "test_name"
  ],
  "title": "NotifyTestOwnerRequest",
  "type": "object"
}
```

## NotifyTestOwnerResponse

```json
{
  "properties": {
    "is_fallback_owner": {
      "default": false,
      "title": "Is Fallback Owner",
      "type": "boolean"
    },
    "owner_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Owner Name"
    },
    "queued": {
      "title": "Queued",
      "type": "boolean"
    },
    "reason": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Reason"
    },
    "sent_to": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Sent To"
    },
    "suite_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Suite Name"
    }
  },
  "required": [
    "queued"
  ],
  "title": "NotifyTestOwnerResponse",
  "type": "object"
}
```

## OutboxRequeueRequest

```json
{
  "description": "Which failed run-outbox intents to put back.",
  "properties": {
    "dry_run": {
      "default": true,
      "description": "List what would be requeued, and change nothing. The default.",
      "title": "Dry Run",
      "type": "boolean"
    },
    "last_error": {
      "description": "Only intents that failed with exactly this error, for example broker_TypeError. Required: a requeue without it re-ran every failed intent of the operation, including notifications and webhooks that had executed and failed for their own reasons (code review round 3).",
      "maxLength": 200,
      "minLength": 1,
      "title": "Last Error",
      "type": "string"
    },
    "limit": {
      "default": 100,
      "description": "At most this many, oldest first.",
      "maximum": 500.0,
      "minimum": 1.0,
      "title": "Limit",
      "type": "integer"
    },
    "operation": {
      "description": "The post-ingestion operation, for example agent_pipeline.",
      "title": "Operation",
      "type": "string"
    },
    "run_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "description": "Only this run's intents.",
      "title": "Run Id"
    }
  },
  "required": [
    "operation",
    "last_error"
  ],
  "title": "OutboxRequeueRequest",
  "type": "object"
}
```

## OverrideAuditEntry

```json
{
  "description": "Single entry in the override audit trail.",
  "properties": {
    "actor_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Actor Id"
    },
    "actor_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Actor Name"
    },
    "after_recommendation": {
      "title": "After Recommendation",
      "type": "string"
    },
    "before_recommendation": {
      "title": "Before Recommendation",
      "type": "string"
    },
    "before_risk_score": {
      "title": "Before Risk Score",
      "type": "integer"
    },
    "policy_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Policy Id"
    },
    "policy_version": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Policy Version"
    },
    "reason": {
      "title": "Reason",
      "type": "string"
    },
    "timestamp": {
      "title": "Timestamp",
      "type": "string"
    }
  },
  "required": [
    "timestamp",
    "before_recommendation",
    "before_risk_score",
    "after_recommendation",
    "reason"
  ],
  "title": "OverrideAuditEntry",
  "type": "object"
}
```

## OverridePolicyConfig

```json
{
  "additionalProperties": false,
  "properties": {
    "allow_retry_decrease": {
      "default": true,
      "title": "Allow Retry Decrease",
      "type": "boolean"
    },
    "allow_tier_downgrade": {
      "default": true,
      "title": "Allow Tier Downgrade",
      "type": "boolean"
    },
    "allow_tool_narrowing": {
      "default": true,
      "title": "Allow Tool Narrowing",
      "type": "boolean"
    }
  },
  "title": "OverridePolicyConfig",
  "type": "object"
}
```

## OwnershipBulkImportItem

```json
{
  "description": "Single item for bulk import.",
  "properties": {
    "match_pattern": {
      "maxLength": 500,
      "minLength": 1,
      "title": "Match Pattern",
      "type": "string"
    },
    "match_type": {
      "pattern": "^(suite_name|component|package|path|label)$",
      "title": "Match Type",
      "type": "string"
    },
    "priority": {
      "default": 0,
      "title": "Priority",
      "type": "integer"
    },
    "service_name": {
      "maxLength": 255,
      "minLength": 1,
      "title": "Service Name",
      "type": "string"
    },
    "team_contact": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Team Contact"
    },
    "team_name": {
      "maxLength": 255,
      "minLength": 1,
      "title": "Team Name",
      "type": "string"
    }
  },
  "required": [
    "match_type",
    "match_pattern",
    "service_name",
    "team_name"
  ],
  "title": "OwnershipBulkImportItem",
  "type": "object"
}
```

## OwnershipBulkImportRequest

```json
{
  "description": "Bulk import of ownership rules.",
  "properties": {
    "replace_existing": {
      "default": false,
      "title": "Replace Existing",
      "type": "boolean"
    },
    "rules": {
      "items": {
        "$ref": "#/components/schemas/OwnershipBulkImportItem"
      },
      "maxItems": 500,
      "minItems": 1,
      "title": "Rules",
      "type": "array"
    }
  },
  "required": [
    "rules"
  ],
  "title": "OwnershipBulkImportRequest",
  "type": "object"
}
```

## OwnershipResolution

```json
{
  "description": "Result of resolving ownership for a test/cluster.",
  "properties": {
    "confidence": {
      "default": "none",
      "title": "Confidence",
      "type": "string"
    },
    "fallback_reason": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Fallback Reason"
    },
    "match_source": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Match Source"
    },
    "matched_rule_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Matched Rule Id"
    },
    "service_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Service Name"
    },
    "team_contact": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Team Contact"
    },
    "team_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Team Name"
    }
  },
  "title": "OwnershipResolution",
  "type": "object"
}
```

## OwnershipRuleCreate

```json
{
  "description": "Create a new ownership rule.",
  "properties": {
    "match_pattern": {
      "maxLength": 500,
      "minLength": 1,
      "title": "Match Pattern",
      "type": "string"
    },
    "match_type": {
      "pattern": "^(suite_name|component|package|path|label)$",
      "title": "Match Type",
      "type": "string"
    },
    "priority": {
      "default": 0,
      "maximum": 1000.0,
      "minimum": 0.0,
      "title": "Priority",
      "type": "integer"
    },
    "service_name": {
      "maxLength": 255,
      "minLength": 1,
      "title": "Service Name",
      "type": "string"
    },
    "team_contact": {
      "anyOf": [
        {
          "maxLength": 500,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Team Contact"
    },
    "team_name": {
      "maxLength": 255,
      "minLength": 1,
      "title": "Team Name",
      "type": "string"
    }
  },
  "required": [
    "match_type",
    "match_pattern",
    "service_name",
    "team_name"
  ],
  "title": "OwnershipRuleCreate",
  "type": "object"
}
```

## OwnershipRuleResponse

```json
{
  "description": "Ownership rule response.",
  "properties": {
    "created_at": {
      "format": "date-time",
      "title": "Created At",
      "type": "string"
    },
    "created_by": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Created By"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "is_active": {
      "title": "Is Active",
      "type": "boolean"
    },
    "match_pattern": {
      "title": "Match Pattern",
      "type": "string"
    },
    "match_type": {
      "title": "Match Type",
      "type": "string"
    },
    "priority": {
      "title": "Priority",
      "type": "integer"
    },
    "project_id": {
      "format": "uuid",
      "title": "Project Id",
      "type": "string"
    },
    "service_name": {
      "title": "Service Name",
      "type": "string"
    },
    "team_contact": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Team Contact"
    },
    "team_name": {
      "title": "Team Name",
      "type": "string"
    },
    "updated_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Updated At"
    }
  },
  "required": [
    "id",
    "project_id",
    "match_type",
    "match_pattern",
    "service_name",
    "team_name",
    "priority",
    "is_active",
    "created_at"
  ],
  "title": "OwnershipRuleResponse",
  "type": "object"
}
```

## OwnershipRuleUpdate

```json
{
  "description": "Partial update for an ownership rule.",
  "properties": {
    "is_active": {
      "anyOf": [
        {
          "type": "boolean"
        },
        {
          "type": "null"
        }
      ],
      "title": "Is Active"
    },
    "match_pattern": {
      "anyOf": [
        {
          "maxLength": 500,
          "minLength": 1,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Match Pattern"
    },
    "match_type": {
      "anyOf": [
        {
          "pattern": "^(suite_name|component|package|path|label)$",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Match Type"
    },
    "priority": {
      "anyOf": [
        {
          "maximum": 1000.0,
          "minimum": 0.0,
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Priority"
    },
    "service_name": {
      "anyOf": [
        {
          "maxLength": 255,
          "minLength": 1,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Service Name"
    },
    "team_contact": {
      "anyOf": [
        {
          "maxLength": 500,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Team Contact"
    },
    "team_name": {
      "anyOf": [
        {
          "maxLength": 255,
          "minLength": 1,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Team Name"
    }
  },
  "title": "OwnershipRuleUpdate",
  "type": "object"
}
```

## PasswordResetLinkResponse

```json
{
  "description": "W17b ADM-02: an admin-issued reset link. The admin never sees or sets the password.",
  "properties": {
    "email_sent": {
      "default": false,
      "title": "Email Sent",
      "type": "boolean"
    },
    "expires_at": {
      "format": "date-time",
      "title": "Expires At",
      "type": "string"
    },
    "link": {
      "title": "Link",
      "type": "string"
    }
  },
  "required": [
    "link",
    "expires_at"
  ],
  "title": "PasswordResetLinkResponse",
  "type": "object"
}
```

## PendingDefectItem

```json
{
  "description": "One defect awaiting approval (GET /deep-investigate/defects/pending-review).",
  "properties": {
    "ai_confidence_score": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Ai Confidence Score"
    },
    "cluster_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Cluster Id"
    },
    "component": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Component"
    },
    "created_at": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Created At"
    },
    "defect_id": {
      "title": "Defect Id",
      "type": "string"
    },
    "jira_project_key": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Jira Project Key"
    },
    "owner_team": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Owner Team"
    },
    "policy_reasons": {
      "items": {
        "type": "string"
      },
      "title": "Policy Reasons",
      "type": "array"
    },
    "project_id": {
      "title": "Project Id",
      "type": "string"
    },
    "run_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Run Id"
    },
    "severity": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Severity"
    },
    "title": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Title"
    }
  },
  "required": [
    "defect_id",
    "project_id"
  ],
  "title": "PendingDefectItem",
  "type": "object"
}
```

## PendingDefectList

```json
{
  "description": "A page of the approval queue. ``total`` is a COUNT over the same scope, not ``len(items)``.",
  "properties": {
    "items": {
      "items": {
        "$ref": "#/components/schemas/PendingDefectItem"
      },
      "title": "Items",
      "type": "array"
    },
    "page": {
      "title": "Page",
      "type": "integer"
    },
    "page_size": {
      "title": "Page Size",
      "type": "integer"
    },
    "total": {
      "title": "Total",
      "type": "integer"
    }
  },
  "required": [
    "items",
    "total",
    "page",
    "page_size"
  ],
  "title": "PendingDefectList",
  "type": "object"
}
```

## PerTestRouting

```json
{
  "properties": {
    "analysis_mode": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Analysis Mode"
    },
    "confidence_adjustments": {
      "anyOf": [
        {
          "items": {
            "additionalProperties": true,
            "type": "object"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Confidence Adjustments"
    },
    "duration_seconds": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Duration Seconds"
    },
    "fallback_from": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Fallback From"
    },
    "fallback_reason": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Fallback Reason"
    },
    "mode_requested": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Mode Requested"
    },
    "retry_count": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Retry Count"
    },
    "test_case_id": {
      "format": "uuid",
      "title": "Test Case Id",
      "type": "string"
    },
    "test_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Test Name"
    },
    "threshold_check": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/ThresholdCheck"
        },
        {
          "type": "null"
        }
      ]
    }
  },
  "required": [
    "test_case_id"
  ],
  "title": "PerTestRouting",
  "type": "object"
}
```

## PhaseDraft

```json
{
  "properties": {
    "description": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Description"
    },
    "exit_criteria_text": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Exit Criteria Text"
    },
    "heading": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Heading"
    },
    "name": {
      "title": "Name",
      "type": "string"
    },
    "phase_type": {
      "title": "Phase Type",
      "type": "string"
    },
    "planned_days": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Planned Days"
    }
  },
  "required": [
    "name",
    "phase_type"
  ],
  "title": "PhaseDraft",
  "type": "object"
}
```

## PhaseDraftResponse

```json
{
  "properties": {
    "fallback_reason": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Fallback Reason"
    },
    "model_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Model Name"
    },
    "phases": {
      "items": {
        "$ref": "#/components/schemas/PhaseDraft"
      },
      "title": "Phases",
      "type": "array"
    },
    "release_id": {
      "format": "uuid",
      "title": "Release Id",
      "type": "string"
    },
    "source": {
      "title": "Source",
      "type": "string"
    },
    "text_chars": {
      "title": "Text Chars",
      "type": "integer"
    },
    "warnings": {
      "items": {
        "type": "string"
      },
      "title": "Warnings",
      "type": "array"
    }
  },
  "required": [
    "release_id",
    "source",
    "phases",
    "text_chars"
  ],
  "title": "PhaseDraftResponse",
  "type": "object"
}
```

## PhaseIn

```json
{
  "properties": {
    "actual_end": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Actual End"
    },
    "actual_start": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Actual Start"
    },
    "description": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Description"
    },
    "exit_criteria": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Exit Criteria"
    },
    "name": {
      "title": "Name",
      "type": "string"
    },
    "notes": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Notes"
    },
    "order_index": {
      "default": 0,
      "title": "Order Index",
      "type": "integer"
    },
    "phase_type": {
      "default": "qa_testing",
      "title": "Phase Type",
      "type": "string"
    },
    "planned_end": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Planned End"
    },
    "planned_start": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Planned Start"
    },
    "status": {
      "default": "pending",
      "title": "Status",
      "type": "string"
    }
  },
  "required": [
    "name"
  ],
  "title": "PhaseIn",
  "type": "object"
}
```

## PhaseUpdate

```json
{
  "properties": {
    "actual_end": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Actual End"
    },
    "actual_start": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Actual Start"
    },
    "description": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Description"
    },
    "exit_criteria": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Exit Criteria"
    },
    "gate_override_reason": {
      "anyOf": [
        {
          "maxLength": 1000,
          "minLength": 3,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Gate Override Reason"
    },
    "name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Name"
    },
    "notes": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Notes"
    },
    "order_index": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Order Index"
    },
    "phase_type": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Phase Type"
    },
    "planned_end": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Planned End"
    },
    "planned_start": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Planned Start"
    },
    "skip_reason": {
      "anyOf": [
        {
          "maxLength": 1000,
          "minLength": 3,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Skip Reason"
    },
    "status": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Status"
    }
  },
  "title": "PhaseUpdate",
  "type": "object"
}
```

## PipelineCompareResponse

```json
{
  "properties": {
    "a": {
      "$ref": "#/components/schemas/PipelineCompareSide"
    },
    "b": {
      "$ref": "#/components/schemas/PipelineCompareSide"
    },
    "changed_stages": {
      "title": "Changed Stages",
      "type": "integer"
    },
    "output": {
      "title": "Output",
      "type": "string"
    },
    "same_test_run": {
      "title": "Same Test Run",
      "type": "boolean"
    },
    "stages": {
      "items": {
        "$ref": "#/components/schemas/PipelineCompareStageRow"
      },
      "title": "Stages",
      "type": "array"
    },
    "total": {
      "title": "Total",
      "type": "integer"
    }
  },
  "required": [
    "a",
    "b",
    "same_test_run",
    "output",
    "changed_stages",
    "total",
    "stages"
  ],
  "title": "PipelineCompareResponse",
  "type": "object"
}
```

## PipelineCompareSide

```json
{
  "properties": {
    "analysis_mode_resolved": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Analysis Mode Resolved"
    },
    "attempt": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Attempt"
    },
    "completed_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Completed At"
    },
    "cost_usd": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Cost Usd"
    },
    "created_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Created At"
    },
    "duration_seconds": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Duration Seconds"
    },
    "pipeline_run_id": {
      "format": "uuid",
      "title": "Pipeline Run Id",
      "type": "string"
    },
    "public_status": {
      "title": "Public Status",
      "type": "string"
    },
    "release_verdict": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Release Verdict"
    },
    "rerun_of": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Rerun Of"
    },
    "stages_truncated": {
      "default": false,
      "title": "Stages Truncated",
      "type": "boolean"
    },
    "started_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Started At"
    },
    "status": {
      "title": "Status",
      "type": "string"
    },
    "test_run_id": {
      "format": "uuid",
      "title": "Test Run Id",
      "type": "string"
    },
    "total_tokens": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Total Tokens"
    },
    "workflow_ref": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Workflow Ref"
    },
    "workflow_type": {
      "title": "Workflow Type",
      "type": "string"
    }
  },
  "required": [
    "pipeline_run_id",
    "test_run_id",
    "workflow_type",
    "status",
    "public_status"
  ],
  "title": "PipelineCompareSide",
  "type": "object"
}
```

## PipelineCompareStageCell

```json
{
  "properties": {
    "completed_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Completed At"
    },
    "confidence_score": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Confidence Score"
    },
    "cost_usd": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Cost Usd"
    },
    "duration_seconds": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Duration Seconds"
    },
    "execution_path": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Execution Path"
    },
    "fallback_used": {
      "anyOf": [
        {
          "type": "boolean"
        },
        {
          "type": "null"
        }
      ],
      "title": "Fallback Used"
    },
    "output_checksum_sha256": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Output Checksum Sha256"
    },
    "started_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Started At"
    },
    "status": {
      "title": "Status",
      "type": "string"
    },
    "total_tokens": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Total Tokens"
    }
  },
  "required": [
    "status"
  ],
  "title": "PipelineCompareStageCell",
  "type": "object"
}
```

## PipelineCompareStageRow

```json
{
  "properties": {
    "a": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/PipelineCompareStageCell"
        },
        {
          "type": "null"
        }
      ]
    },
    "b": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/PipelineCompareStageCell"
        },
        {
          "type": "null"
        }
      ]
    },
    "changed": {
      "items": {
        "type": "string"
      },
      "title": "Changed",
      "type": "array"
    },
    "cost_delta_usd": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Cost Delta Usd"
    },
    "duration_delta_seconds": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Duration Delta Seconds"
    },
    "output_checksum_equal": {
      "anyOf": [
        {
          "type": "boolean"
        },
        {
          "type": "null"
        }
      ],
      "title": "Output Checksum Equal"
    },
    "stage_name": {
      "title": "Stage Name",
      "type": "string"
    },
    "tokens_delta": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Tokens Delta"
    }
  },
  "required": [
    "stage_name"
  ],
  "title": "PipelineCompareStageRow",
  "type": "object"
}
```

## PipelineEventLogHealthResponse

```json
{
  "properties": {
    "dead_letter_count": {
      "default": 0,
      "title": "Dead Letter Count",
      "type": "integer"
    },
    "dead_letter_limit": {
      "default": 0,
      "title": "Dead Letter Limit",
      "type": "integer"
    },
    "recent_dead_letters": {
      "items": {
        "additionalProperties": true,
        "type": "object"
      },
      "title": "Recent Dead Letters",
      "type": "array"
    },
    "status": {
      "default": "healthy",
      "title": "Status",
      "type": "string"
    },
    "write_failure_count": {
      "default": 0,
      "title": "Write Failure Count",
      "type": "integer"
    }
  },
  "title": "PipelineEventLogHealthResponse",
  "type": "object"
}
```

## PipelineReplayAuditGaps

```json
{
  "properties": {
    "missing_checkpoints": {
      "items": {
        "type": "string"
      },
      "title": "Missing Checkpoints",
      "type": "array"
    },
    "missing_final_state_checksum": {
      "default": false,
      "title": "Missing Final State Checksum",
      "type": "boolean"
    },
    "missing_replay_checksums": {
      "items": {
        "type": "string"
      },
      "title": "Missing Replay Checksums",
      "type": "array"
    },
    "missing_start_events": {
      "items": {
        "type": "string"
      },
      "title": "Missing Start Events",
      "type": "array"
    },
    "missing_terminal_events": {
      "items": {
        "type": "string"
      },
      "title": "Missing Terminal Events",
      "type": "array"
    }
  },
  "title": "PipelineReplayAuditGaps",
  "type": "object"
}
```

## PipelineReplayEventResponse

```json
{
  "properties": {
    "detail": {
      "additionalProperties": true,
      "title": "Detail",
      "type": "object"
    },
    "event_type": {
      "title": "Event Type",
      "type": "string"
    },
    "source": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Source"
    },
    "stage_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Stage Name"
    },
    "test_case_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Test Case Id"
    },
    "timestamp": {
      "anyOf": [
        {},
        {
          "type": "null"
        }
      ],
      "title": "Timestamp"
    }
  },
  "required": [
    "event_type"
  ],
  "title": "PipelineReplayEventResponse",
  "type": "object"
}
```

## PipelineReplayIntegritySummary

```json
{
  "properties": {
    "audit_gaps": {
      "$ref": "#/components/schemas/PipelineReplayAuditGaps"
    },
    "replayable": {
      "default": false,
      "title": "Replayable",
      "type": "boolean"
    }
  },
  "title": "PipelineReplayIntegritySummary",
  "type": "object"
}
```

## PipelineReplayResponse

```json
{
  "properties": {
    "analysis_mode_requested": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Analysis Mode Requested"
    },
    "analysis_mode_resolution": {
      "additionalProperties": true,
      "title": "Analysis Mode Resolution",
      "type": "object"
    },
    "analysis_mode_resolved": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Analysis Mode Resolved"
    },
    "audit_gaps": {
      "$ref": "#/components/schemas/PipelineReplayAuditGaps"
    },
    "completed_at": {
      "anyOf": [
        {},
        {
          "type": "null"
        }
      ],
      "title": "Completed At"
    },
    "event_counts": {
      "additionalProperties": {
        "type": "integer"
      },
      "title": "Event Counts",
      "type": "object"
    },
    "events": {
      "items": {
        "$ref": "#/components/schemas/PipelineReplayEventResponse"
      },
      "title": "Events",
      "type": "array"
    },
    "final_state_checksum_sha256": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Final State Checksum Sha256"
    },
    "memory_references": {
      "items": {
        "$ref": "#/components/schemas/MemoryReference"
      },
      "title": "Memory References",
      "type": "array"
    },
    "pipeline_run_id": {
      "format": "uuid",
      "title": "Pipeline Run Id",
      "type": "string"
    },
    "replayable": {
      "default": false,
      "title": "Replayable",
      "type": "boolean"
    },
    "route_decisions": {
      "items": {
        "additionalProperties": true,
        "type": "object"
      },
      "title": "Route Decisions",
      "type": "array"
    },
    "runtime_versions": {
      "additionalProperties": {
        "type": "string"
      },
      "title": "Runtime Versions",
      "type": "object"
    },
    "schema_version": {
      "default": 1,
      "title": "Schema Version",
      "type": "integer"
    },
    "stage_replay": {
      "items": {
        "$ref": "#/components/schemas/PipelineReplayStageResponse"
      },
      "title": "Stage Replay",
      "type": "array"
    },
    "started_at": {
      "anyOf": [
        {},
        {
          "type": "null"
        }
      ],
      "title": "Started At"
    },
    "status": {
      "title": "Status",
      "type": "string"
    },
    "test_run_id": {
      "format": "uuid",
      "title": "Test Run Id",
      "type": "string"
    },
    "workflow_plan": {
      "additionalProperties": true,
      "title": "Workflow Plan",
      "type": "object"
    },
    "workflow_type": {
      "title": "Workflow Type",
      "type": "string"
    },
    "workflow_verification": {
      "additionalProperties": true,
      "title": "Workflow Verification",
      "type": "object"
    }
  },
  "required": [
    "pipeline_run_id",
    "test_run_id",
    "workflow_type",
    "status"
  ],
  "title": "PipelineReplayResponse",
  "type": "object"
}
```

## PipelineReplayStageResponse

```json
{
  "properties": {
    "checkpoint_available": {
      "default": false,
      "title": "Checkpoint Available",
      "type": "boolean"
    },
    "completed_at": {
      "anyOf": [
        {},
        {
          "type": "null"
        }
      ],
      "title": "Completed At"
    },
    "decision_count": {
      "default": 0,
      "title": "Decision Count",
      "type": "integer"
    },
    "input_checksum_sha256": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Input Checksum Sha256"
    },
    "output_checksum_sha256": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Output Checksum Sha256"
    },
    "restored_from_checkpoint": {
      "default": false,
      "title": "Restored From Checkpoint",
      "type": "boolean"
    },
    "runtime_versions": {
      "additionalProperties": {
        "type": "string"
      },
      "title": "Runtime Versions",
      "type": "object"
    },
    "stage_name": {
      "title": "Stage Name",
      "type": "string"
    },
    "started_at": {
      "anyOf": [
        {},
        {
          "type": "null"
        }
      ],
      "title": "Started At"
    },
    "status": {
      "title": "Status",
      "type": "string"
    }
  },
  "required": [
    "stage_name",
    "status"
  ],
  "title": "PipelineReplayStageResponse",
  "type": "object"
}
```

## PipelineTimelineEventResponse

```json
{
  "properties": {
    "detail": {
      "additionalProperties": true,
      "title": "Detail",
      "type": "object"
    },
    "event_type": {
      "title": "Event Type",
      "type": "string"
    },
    "stage_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Stage Name"
    },
    "test_case_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Test Case Id"
    },
    "timestamp": {
      "format": "date-time",
      "title": "Timestamp",
      "type": "string"
    }
  },
  "required": [
    "event_type",
    "timestamp"
  ],
  "title": "PipelineTimelineEventResponse",
  "type": "object"
}
```

## PipelineTimelineResponse

```json
{
  "properties": {
    "agent_observability": {
      "additionalProperties": true,
      "title": "Agent Observability",
      "type": "object"
    },
    "alerts": {
      "items": {
        "additionalProperties": true,
        "type": "object"
      },
      "title": "Alerts",
      "type": "array"
    },
    "completed_at": {
      "anyOf": [
        {},
        {
          "type": "null"
        }
      ],
      "title": "Completed At"
    },
    "cost_summary": {
      "additionalProperties": true,
      "title": "Cost Summary",
      "type": "object"
    },
    "duration_seconds": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Duration Seconds"
    },
    "events": {
      "items": {
        "$ref": "#/components/schemas/PipelineTimelineEventResponse"
      },
      "title": "Events",
      "type": "array"
    },
    "pipeline_run_id": {
      "format": "uuid",
      "title": "Pipeline Run Id",
      "type": "string"
    },
    "replay_integrity": {
      "$ref": "#/components/schemas/PipelineReplayIntegritySummary"
    },
    "schema_version": {
      "default": 2,
      "title": "Schema Version",
      "type": "integer"
    },
    "stages": {
      "items": {
        "additionalProperties": true,
        "type": "object"
      },
      "title": "Stages",
      "type": "array"
    },
    "started_at": {
      "anyOf": [
        {},
        {
          "type": "null"
        }
      ],
      "title": "Started At"
    },
    "status": {
      "title": "Status",
      "type": "string"
    },
    "summary": {
      "$ref": "#/components/schemas/PipelineTimelineSummary"
    },
    "workflow_ref": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Workflow Ref"
    },
    "workflow_source": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Workflow Source"
    },
    "workflow_type": {
      "title": "Workflow Type",
      "type": "string"
    }
  },
  "required": [
    "pipeline_run_id",
    "workflow_type",
    "status"
  ],
  "title": "PipelineTimelineResponse",
  "type": "object"
}
```

## PipelineTimelineSummary

```json
{
  "properties": {
    "completed_stages": {
      "default": 0,
      "title": "Completed Stages",
      "type": "integer"
    },
    "failed_stages": {
      "default": 0,
      "title": "Failed Stages",
      "type": "integer"
    },
    "pending_stages": {
      "default": 0,
      "title": "Pending Stages",
      "type": "integer"
    },
    "progress_percent": {
      "default": 0.0,
      "title": "Progress Percent",
      "type": "number"
    },
    "running_stages": {
      "default": 0,
      "title": "Running Stages",
      "type": "integer"
    },
    "skipped_stages": {
      "default": 0,
      "title": "Skipped Stages",
      "type": "integer"
    },
    "total_stages": {
      "default": 0,
      "title": "Total Stages",
      "type": "integer"
    }
  },
  "title": "PipelineTimelineSummary",
  "type": "object"
}
```

## PolicyDimensionWeights

```json
{
  "description": "Weights for 7-dimension risk scoring (should sum to 1.0).",
  "properties": {
    "blast_radius": {
      "default": 0.15,
      "maximum": 1.0,
      "minimum": 0.0,
      "title": "Blast Radius",
      "type": "number"
    },
    "diagnosis_conf": {
      "default": 0.05,
      "maximum": 1.0,
      "minimum": 0.0,
      "title": "Diagnosis Conf",
      "type": "number"
    },
    "env_sensitivity": {
      "default": 0.1,
      "maximum": 1.0,
      "minimum": 0.0,
      "title": "Env Sensitivity",
      "type": "number"
    },
    "hist_recurrence": {
      "default": 0.1,
      "maximum": 1.0,
      "minimum": 0.0,
      "title": "Hist Recurrence",
      "type": "number"
    },
    "regression_likely": {
      "default": 0.2,
      "maximum": 1.0,
      "minimum": 0.0,
      "title": "Regression Likely",
      "type": "number"
    },
    "reproducibility": {
      "default": 0.15,
      "maximum": 1.0,
      "minimum": 0.0,
      "title": "Reproducibility",
      "type": "number"
    },
    "user_impact": {
      "default": 0.25,
      "maximum": 1.0,
      "minimum": 0.0,
      "title": "User Impact",
      "type": "number"
    }
  },
  "title": "PolicyDimensionWeights",
  "type": "object"
}
```

## PolicyDocument

```json
{
  "description": "The full policy rule document stored as JSON in release_gate_policies.rules.",
  "properties": {
    "dimension_weights": {
      "$ref": "#/components/schemas/PolicyDimensionWeights"
    },
    "hard_caps": {
      "$ref": "#/components/schemas/PolicyHardCaps"
    },
    "kind_rules": {
      "$ref": "#/components/schemas/PolicyKindRules"
    },
    "pass_rate_bands": {
      "$ref": "#/components/schemas/PolicyPassRateBands"
    },
    "rules": {
      "items": {
        "$ref": "#/components/schemas/PolicyRule"
      },
      "title": "Rules",
      "type": "array"
    },
    "schema_version": {
      "default": 1,
      "title": "Schema Version",
      "type": "integer"
    },
    "thresholds": {
      "$ref": "#/components/schemas/PolicyThresholds"
    }
  },
  "title": "PolicyDocument",
  "type": "object"
}
```

## PolicyHardCaps

```json
{
  "description": "Hard caps that downgrade the pass-rate band before the verdict map.\n\nEach cap is a (count) threshold. **Breaching any cap pins the band to red\nand forces NO_GO**, regardless of how green the pass rate was — a hard cap\nis a hard blocker, not a one-step downgrade. ``downgrades`` records which\ncaps fired.\n\n**Zero does not mean the same thing for every cap**, and the difference is\nload-bearing, so it is stated per field below rather than summarised here.\n``max_p0_defects=0`` means \"no P0 defects allowed\" and blocks on the first\none; ``max_flaky_count=0`` and ``max_new_failures_24h=0`` *disable* their\ncaps, because a literal zero would over-fire on real projects. That\nasymmetry is deliberate and pinned by\n``tests/test_classify_with_policy.py``.",
  "properties": {
    "max_flaky_count": {
      "default": 10,
      "description": "Flaky tests allowed before the gate blocks. 0 DISABLES this cap (any flaky count passes) rather than forbidding flakiness — set 1 to block on the first flaky test.",
      "minimum": 0.0,
      "title": "Max Flaky Count",
      "type": "integer"
    },
    "max_new_failures_24h": {
      "default": 20,
      "description": "New failures in the last 24h allowed before the gate blocks. 0 DISABLES this cap rather than forbidding new failures — set 1 to block on the first one.",
      "minimum": 0.0,
      "title": "Max New Failures 24H",
      "type": "integer"
    },
    "max_p0_defects": {
      "default": 0,
      "description": "Active P0 defects allowed before the gate blocks. 0 means NONE allowed — one open P0 forces red/NO_GO. This cap cannot be disabled by setting it to 0; raise it to permit P0 defects.",
      "minimum": 0.0,
      "title": "Max P0 Defects",
      "type": "integer"
    }
  },
  "title": "PolicyHardCaps",
  "type": "object"
}
```

## PolicyKindBudget

```json
{
  "description": "Failure budget for one excludable failure kind (US-9.3).\n\n``max_failures`` is the count of failures of this kind the gate will\nexcuse from the NO_GO trigger. ``downgrade_to`` is deliberately a\nsingle-value Literal: a NO_GO may be softened at most to CONDITIONAL_GO —\nnever to GO — so the schema itself makes the hard rule unrepresentable.\n\n``min_confidence_to_excuse`` (AI-4, optional): when set, a failure only\ncounts toward this kind's excusable budget if its per-failure kind\nconfidence (the evidence-checklist confidence, falling back to the\nclassifier confidence) is at or above the floor. Below-floor and\nunknown-confidence failures count as product — conservative. ``None``\n(the default) applies no floor: verdicts are byte-identical to the\npre-AI-4 US-9.3 behaviour (pinned by tests/test_kind_gate_policy.py).",
  "properties": {
    "downgrade_to": {
      "const": "CONDITIONAL_GO",
      "default": "CONDITIONAL_GO",
      "title": "Downgrade To",
      "type": "string"
    },
    "max_failures": {
      "default": 0,
      "minimum": 0.0,
      "title": "Max Failures",
      "type": "integer"
    },
    "min_confidence_to_excuse": {
      "anyOf": [
        {
          "maximum": 100.0,
          "minimum": 0.0,
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Min Confidence To Excuse"
    }
  },
  "title": "PolicyKindBudget",
  "type": "object"
}
```

## PolicyKindRules

```json
{
  "description": "Opt-in failure-kind weighting for the release gate (US-9.3).\n\nSTRICTLY OPT-IN: ``enabled`` defaults to False and the evaluator treats\na disabled/absent block as byte-identical to today's behaviour (pinned by\n``tests/test_kind_gate_policy.py``). Kinds come from the derived triad in\n``app/services/failure_kind.py`` (AI-classified — never ground truth).\n\nOnly ``infrastructure`` and ``test_code`` may carry budgets. ``product``\nfailures always count and ``unknown`` failures are conservatively counted\nas product — neither is representable here on purpose.",
  "properties": {
    "enabled": {
      "default": false,
      "title": "Enabled",
      "type": "boolean"
    },
    "infrastructure": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/PolicyKindBudget"
        },
        {
          "type": "null"
        }
      ]
    },
    "test_code": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/PolicyKindBudget"
        },
        {
          "type": "null"
        }
      ]
    }
  },
  "title": "PolicyKindRules",
  "type": "object"
}
```

## PolicyPassRateBands

```json
{
  "description": "Project-level 4-band classification for the build colour and verdict.\n\nBands are defined by the *lower* edge of each colour and must be strictly\nincreasing: ``orange_min < yellow_min < green_min``. A pass rate below\n``orange_min`` is red; ``[orange_min, yellow_min)`` is orange;\n``[yellow_min, green_min)`` is yellow; ``>= green_min`` is green.\n\nDefaults match the user-requested levels (red <90, orange 90-95,\nyellow 95-99, green >=99). Verdict mapping is fixed: green = GO,\nyellow = GO with watch, orange = CONDITIONAL, red = NO_GO.\n\nA breached hard cap (``PolicyHardCaps``) does **not** step the band down\none or two notches — it pins the band straight to red and forces NO_GO,\nwhatever the pass rate was. The stepped behaviour was abandoned because\nyellow still mapped to GO, which made a \"hard cap\" advisory at best.",
  "properties": {
    "green_min": {
      "default": 99.0,
      "maximum": 100.0,
      "minimum": 0.0,
      "title": "Green Min",
      "type": "number"
    },
    "orange_min": {
      "default": 90.0,
      "maximum": 100.0,
      "minimum": 0.0,
      "title": "Orange Min",
      "type": "number"
    },
    "yellow_min": {
      "default": 95.0,
      "maximum": 100.0,
      "minimum": 0.0,
      "title": "Yellow Min",
      "type": "number"
    }
  },
  "title": "PolicyPassRateBands",
  "type": "object"
}
```

## PolicyRule

```json
{
  "description": "A single rule within a release gate policy.",
  "properties": {
    "enabled": {
      "default": true,
      "title": "Enabled",
      "type": "boolean"
    },
    "id": {
      "maxLength": 100,
      "minLength": 1,
      "title": "Id",
      "type": "string"
    },
    "name": {
      "maxLength": 255,
      "minLength": 1,
      "title": "Name",
      "type": "string"
    },
    "params": {
      "additionalProperties": true,
      "title": "Params",
      "type": "object"
    },
    "type": {
      "title": "Type",
      "type": "string"
    }
  },
  "required": [
    "id",
    "name",
    "type"
  ],
  "title": "PolicyRule",
  "type": "object"
}
```

## PolicySimulateRequest

```json
{
  "description": "Simulate a draft policy against a past run.",
  "properties": {
    "policy_document": {
      "$ref": "#/components/schemas/PolicyDocument"
    },
    "run_id": {
      "format": "uuid",
      "title": "Run Id",
      "type": "string"
    }
  },
  "required": [
    "run_id",
    "policy_document"
  ],
  "title": "PolicySimulateRequest",
  "type": "object"
}
```

## PolicySimulateResponse

```json
{
  "description": "Side-by-side comparison: original vs simulated policy result.",
  "properties": {
    "diff_summary": {
      "title": "Diff Summary",
      "type": "string"
    },
    "original_composite": {
      "title": "Original Composite",
      "type": "number"
    },
    "original_recommendation": {
      "title": "Original Recommendation",
      "type": "string"
    },
    "rule_evaluations": {
      "default": [],
      "items": {
        "$ref": "#/components/schemas/RuleEvaluationResponse"
      },
      "title": "Rule Evaluations",
      "type": "array"
    },
    "simulated_composite": {
      "title": "Simulated Composite",
      "type": "number"
    },
    "simulated_recommendation": {
      "title": "Simulated Recommendation",
      "type": "string"
    }
  },
  "required": [
    "original_recommendation",
    "simulated_recommendation",
    "original_composite",
    "simulated_composite",
    "diff_summary"
  ],
  "title": "PolicySimulateResponse",
  "type": "object"
}
```

## PolicyThresholds

```json
{
  "description": "Risk score thresholds for GO/NO_GO classification.",
  "properties": {
    "go_threshold": {
      "default": 20.0,
      "maximum": 100.0,
      "minimum": 0.0,
      "title": "Go Threshold",
      "type": "number"
    },
    "no_go_threshold": {
      "default": 55.0,
      "maximum": 100.0,
      "minimum": 0.0,
      "title": "No Go Threshold",
      "type": "number"
    },
    "pass_rate_hard_floor_factor": {
      "default": 0.7,
      "maximum": 1.0,
      "minimum": 0.0,
      "title": "Pass Rate Hard Floor Factor",
      "type": "number"
    },
    "pass_rate_minimum": {
      "default": 90.0,
      "maximum": 100.0,
      "minimum": 0.0,
      "title": "Pass Rate Minimum",
      "type": "number"
    }
  },
  "title": "PolicyThresholds",
  "type": "object"
}
```

## PreReleaseGateRequest

```json
{
  "properties": {
    "agent_name": {
      "default": "AnalysisAgent",
      "title": "Agent Name",
      "type": "string"
    },
    "candidate_run_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Candidate Run Id"
    },
    "dataset_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Dataset Id"
    },
    "task_type": {
      "default": "classification",
      "title": "Task Type",
      "type": "string"
    }
  },
  "title": "PreReleaseGateRequest",
  "type": "object"
}
```

## PreScanGroup

```json
{
  "properties": {
    "cluster_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Cluster Id"
    },
    "cluster_label": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Cluster Label"
    },
    "member_result_ids": {
      "items": {
        "format": "uuid",
        "type": "string"
      },
      "title": "Member Result Ids",
      "type": "array"
    },
    "members": {
      "title": "Members",
      "type": "integer"
    },
    "members_truncated": {
      "default": false,
      "title": "Members Truncated",
      "type": "boolean"
    },
    "representative_error": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Representative Error"
    },
    "signature_id": {
      "title": "Signature Id",
      "type": "string"
    },
    "suites": {
      "items": {
        "type": "string"
      },
      "title": "Suites",
      "type": "array"
    }
  },
  "required": [
    "signature_id",
    "members",
    "member_result_ids"
  ],
  "title": "PreScanGroup",
  "type": "object"
}
```

## ProjectCreate

```json
{
  "properties": {
    "component_owner_map": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Component Owner Map"
    },
    "default_qa_lead_user_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Default Qa Lead User Id"
    },
    "description": {
      "anyOf": [
        {
          "maxLength": 2000,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Description"
    },
    "jenkins_job_pattern": {
      "anyOf": [
        {
          "maxLength": 500,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "description": "Stored only; TestLookup does not use it (W10 D22).",
      "title": "Jenkins Job Pattern"
    },
    "jira_project_key": {
      "anyOf": [
        {
          "maxLength": 50,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Jira Project Key"
    },
    "name": {
      "maxLength": 255,
      "minLength": 2,
      "title": "Name",
      "type": "string"
    },
    "ocp_namespace": {
      "anyOf": [
        {
          "maxLength": 255,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Ocp Namespace"
    },
    "slug": {
      "maxLength": 100,
      "minLength": 2,
      "pattern": "^[a-z0-9-]+$",
      "title": "Slug",
      "type": "string"
    },
    "splunk_index": {
      "anyOf": [
        {
          "maxLength": 255,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Splunk Index"
    }
  },
  "required": [
    "name",
    "slug"
  ],
  "title": "ProjectCreate",
  "type": "object"
}
```

## ProjectMemberResponse

```json
{
  "properties": {
    "created_at": {
      "format": "date-time",
      "title": "Created At",
      "type": "string"
    },
    "email": {
      "title": "Email",
      "type": "string"
    },
    "full_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Full Name"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "is_active": {
      "default": true,
      "title": "Is Active",
      "type": "boolean"
    },
    "is_service_account": {
      "default": false,
      "title": "Is Service Account",
      "type": "boolean"
    },
    "is_synthetic": {
      "default": false,
      "title": "Is Synthetic",
      "type": "boolean"
    },
    "project_id": {
      "format": "uuid",
      "title": "Project Id",
      "type": "string"
    },
    "role": {
      "$ref": "#/components/schemas/UserRole"
    },
    "source": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Source"
    },
    "user_id": {
      "format": "uuid",
      "title": "User Id",
      "type": "string"
    },
    "username": {
      "title": "Username",
      "type": "string"
    }
  },
  "required": [
    "id",
    "user_id",
    "project_id",
    "role",
    "created_at",
    "email",
    "username"
  ],
  "title": "ProjectMemberResponse",
  "type": "object"
}
```

## ProjectResetRequest

```json
{
  "description": "Destructive reset payload. ``mode`` selects the wipe scope; the\nbackend rejects any request whose ``confirmation_name`` doesn't\nexactly equal the project's ``name`` — a typed-confirmation guard\nagainst autopilot clicks. See services/project_reset_service.py for\nthe table list per mode.",
  "properties": {
    "confirmation_name": {
      "maxLength": 255,
      "minLength": 1,
      "title": "Confirmation Name",
      "type": "string"
    },
    "mode": {
      "enum": [
        "runs",
        "full"
      ],
      "title": "Mode",
      "type": "string"
    }
  },
  "required": [
    "mode",
    "confirmation_name"
  ],
  "title": "ProjectResetRequest",
  "type": "object"
}
```

## ProjectResetResponse

```json
{
  "properties": {
    "deleted": {
      "additionalProperties": {
        "type": "integer"
      },
      "title": "Deleted",
      "type": "object"
    },
    "mode": {
      "enum": [
        "runs",
        "full"
      ],
      "title": "Mode",
      "type": "string"
    }
  },
  "required": [
    "mode",
    "deleted"
  ],
  "title": "ProjectResetResponse",
  "type": "object"
}
```

## ProjectResponse

```json
{
  "properties": {
    "archived_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Archived At"
    },
    "archived_by_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Archived By Name"
    },
    "archived_by_user_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Archived By User Id"
    },
    "automation_target_pct": {
      "default": 60,
      "title": "Automation Target Pct",
      "type": "integer"
    },
    "component_owner_map": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Component Owner Map"
    },
    "created_at": {
      "format": "date-time",
      "title": "Created At",
      "type": "string"
    },
    "default_qa_lead_user_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Default Qa Lead User Id"
    },
    "description": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Description"
    },
    "end_date": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "End Date"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "is_active": {
      "title": "Is Active",
      "type": "boolean"
    },
    "jenkins_job_pattern": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Jenkins Job Pattern"
    },
    "jira_project_key": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Jira Project Key"
    },
    "manager_user_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Manager User Id"
    },
    "name": {
      "title": "Name",
      "type": "string"
    },
    "ocp_namespace": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Ocp Namespace"
    },
    "require_case_review": {
      "default": false,
      "title": "Require Case Review",
      "type": "boolean"
    },
    "retention_status": {
      "anyOf": [
        {
          "enum": [
            "enabled",
            "disabled",
            "unconfigured"
          ],
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Retention Status"
    },
    "slug": {
      "title": "Slug",
      "type": "string"
    },
    "splunk_index": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Splunk Index"
    },
    "start_date": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Start Date"
    },
    "tags": {
      "anyOf": [
        {
          "items": {},
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Tags"
    },
    "updated_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Updated At"
    }
  },
  "required": [
    "created_at",
    "id",
    "name",
    "slug",
    "is_active"
  ],
  "title": "ProjectResponse",
  "type": "object"
}
```

## ProjectRestoreResponse

```json
{
  "description": "W17 ADM-04: a restored project and what deliberately stayed revoked.",
  "properties": {
    "api_keys_still_revoked": {
      "title": "Api Keys Still Revoked",
      "type": "integer"
    },
    "default_qa_lead_reactivated": {
      "default": false,
      "title": "Default Qa Lead Reactivated",
      "type": "boolean"
    },
    "project_id": {
      "format": "uuid",
      "title": "Project Id",
      "type": "string"
    },
    "restored": {
      "default": true,
      "title": "Restored",
      "type": "boolean"
    }
  },
  "required": [
    "project_id",
    "api_keys_still_revoked"
  ],
  "title": "ProjectRestoreResponse",
  "type": "object"
}
```

## ProjectStorageResponse

```json
{
  "properties": {
    "computed_at": {
      "format": "date-time",
      "title": "Computed At",
      "type": "string"
    },
    "fully_measured": {
      "default": true,
      "title": "Fully Measured",
      "type": "boolean"
    },
    "project_id": {
      "title": "Project Id",
      "type": "string"
    },
    "stores": {
      "items": {
        "$ref": "#/components/schemas/StoreFootprintResponse"
      },
      "title": "Stores",
      "type": "array"
    },
    "total_bytes": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Total Bytes"
    },
    "total_is_estimate": {
      "default": false,
      "title": "Total Is Estimate",
      "type": "boolean"
    }
  },
  "required": [
    "project_id",
    "computed_at",
    "stores"
  ],
  "title": "ProjectStorageResponse",
  "type": "object"
}
```

## ProjectUpdate

```json
{
  "description": "Partial update for project attributes. None = keep existing.",
  "properties": {
    "automation_target_pct": {
      "anyOf": [
        {
          "maximum": 100.0,
          "minimum": 0.0,
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Automation Target Pct"
    },
    "component_owner_map": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Component Owner Map"
    },
    "default_qa_lead_user_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Default Qa Lead User Id"
    },
    "description": {
      "anyOf": [
        {
          "maxLength": 2000,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Description"
    },
    "end_date": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "End Date"
    },
    "jenkins_job_pattern": {
      "anyOf": [
        {
          "maxLength": 500,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "description": "Stored only; TestLookup does not use it (W10 D22).",
      "title": "Jenkins Job Pattern"
    },
    "jira_project_key": {
      "anyOf": [
        {
          "maxLength": 50,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Jira Project Key"
    },
    "manager_user_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Manager User Id"
    },
    "name": {
      "anyOf": [
        {
          "maxLength": 255,
          "minLength": 2,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Name"
    },
    "ocp_namespace": {
      "anyOf": [
        {
          "maxLength": 255,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Ocp Namespace"
    },
    "require_case_review": {
      "anyOf": [
        {
          "type": "boolean"
        },
        {
          "type": "null"
        }
      ],
      "title": "Require Case Review"
    },
    "splunk_index": {
      "anyOf": [
        {
          "maxLength": 255,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Splunk Index"
    },
    "start_date": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Start Date"
    },
    "tags": {
      "anyOf": [
        {
          "items": {
            "type": "string"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Tags"
    }
  },
  "title": "ProjectUpdate",
  "type": "object"
}
```

## PrometheusMetricTemplate

```json
{
  "additionalProperties": false,
  "description": "One admin-defined metric template; the server substitutes ``{service}`` (escaped) — never model PromQL.",
  "properties": {
    "id": {
      "pattern": "^[a-z][a-z0-9_]{1,39}$",
      "title": "Id",
      "type": "string"
    },
    "label": {
      "maxLength": 80,
      "minLength": 1,
      "title": "Label",
      "type": "string"
    },
    "promql": {
      "maxLength": 1000,
      "minLength": 1,
      "title": "Promql",
      "type": "string"
    },
    "unit": {
      "default": "count",
      "title": "Unit",
      "type": "string"
    }
  },
  "required": [
    "id",
    "label",
    "promql"
  ],
  "title": "PrometheusMetricTemplate",
  "type": "object"
}
```

## PrometheusMetricTemplateIn

```json
{
  "additionalProperties": false,
  "description": "A template as an ADMIN saves it (IntegrationsConfigUpdate): the placeholder, control characters and\nthe unit are checked per item, so a 422's loc is ``prometheus_metric_templates.N.<field>``.",
  "properties": {
    "id": {
      "pattern": "^[a-z][a-z0-9_]{1,39}$",
      "title": "Id",
      "type": "string"
    },
    "label": {
      "maxLength": 80,
      "minLength": 1,
      "title": "Label",
      "type": "string"
    },
    "promql": {
      "maxLength": 1000,
      "minLength": 1,
      "title": "Promql",
      "type": "string"
    },
    "unit": {
      "default": "count",
      "title": "Unit",
      "type": "string"
    }
  },
  "required": [
    "id",
    "label",
    "promql"
  ],
  "title": "PrometheusMetricTemplateIn",
  "type": "object"
}
```

## PromoteModelRequest

```json
{
  "properties": {
    "baseline_accuracy": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Baseline Accuracy"
    },
    "eval_accuracy": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Eval Accuracy"
    },
    "model_name": {
      "title": "Model Name",
      "type": "string"
    },
    "track": {
      "title": "Track",
      "type": "string"
    }
  },
  "required": [
    "track",
    "model_name"
  ],
  "title": "PromoteModelRequest",
  "type": "object"
}
```

## QuarantineDecisionRequest

```json
{
  "description": "Body for approve / reject / release endpoints.",
  "properties": {
    "notes": {
      "anyOf": [
        {
          "maxLength": 2000,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Notes"
    },
    "quarantine_duration_days": {
      "anyOf": [
        {
          "maximum": 90.0,
          "minimum": 1.0,
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Quarantine Duration Days"
    }
  },
  "title": "QuarantineDecisionRequest",
  "type": "object"
}
```

## QuarantineLifecyclePolicyResponse

```json
{
  "properties": {
    "auto_create_defect": {
      "title": "Auto Create Defect",
      "type": "boolean"
    },
    "auto_promote": {
      "title": "Auto Promote",
      "type": "boolean"
    },
    "detection_flip_rate_threshold": {
      "title": "Detection Flip Rate Threshold",
      "type": "number"
    },
    "detection_min_runs": {
      "title": "Detection Min Runs",
      "type": "integer"
    },
    "is_default": {
      "default": false,
      "title": "Is Default",
      "type": "boolean"
    },
    "max_active_quarantined": {
      "default": 8,
      "title": "Max Active Quarantined",
      "type": "integer"
    },
    "project_id": {
      "format": "uuid",
      "title": "Project Id",
      "type": "string"
    },
    "promote_after_passes": {
      "title": "Promote After Passes",
      "type": "integer"
    },
    "sla_days": {
      "title": "Sla Days",
      "type": "integer"
    }
  },
  "required": [
    "project_id",
    "sla_days",
    "auto_create_defect",
    "auto_promote",
    "promote_after_passes",
    "detection_flip_rate_threshold",
    "detection_min_runs"
  ],
  "title": "QuarantineLifecyclePolicyResponse",
  "type": "object"
}
```

## QuarantineLifecyclePolicyUpdate

```json
{
  "description": "Per-project quarantine lifecycle policy (PMF US-5.4 / US-5.5 / US-5.6).",
  "properties": {
    "auto_create_defect": {
      "default": false,
      "title": "Auto Create Defect",
      "type": "boolean"
    },
    "auto_promote": {
      "default": false,
      "title": "Auto Promote",
      "type": "boolean"
    },
    "detection_flip_rate_threshold": {
      "default": 0.2,
      "maximum": 1.0,
      "minimum": 0.0,
      "title": "Detection Flip Rate Threshold",
      "type": "number"
    },
    "detection_min_runs": {
      "default": 10,
      "maximum": 1000.0,
      "minimum": 1.0,
      "title": "Detection Min Runs",
      "type": "integer"
    },
    "max_active_quarantined": {
      "default": 8,
      "maximum": 1000.0,
      "minimum": 0.0,
      "title": "Max Active Quarantined",
      "type": "integer"
    },
    "promote_after_passes": {
      "default": 20,
      "maximum": 1000.0,
      "minimum": 1.0,
      "title": "Promote After Passes",
      "type": "integer"
    },
    "sla_days": {
      "default": 14,
      "maximum": 365.0,
      "minimum": 1.0,
      "title": "Sla Days",
      "type": "integer"
    }
  },
  "title": "QuarantineLifecyclePolicyUpdate",
  "type": "object"
}
```

## QuarantineManifestEntry

```json
{
  "description": "One currently-quarantined test in the CI manifest (US-5.1).\n\nIdentity tuple for CI-side matching: ``fingerprint`` (primary key —\n``sha256(class_name::test_name)[:16]``, same formula as ingestion's\n``make_test_fingerprint``) plus the human-readable ``test_name`` /\n``suite_name`` / ``class_name`` for name-based fallback matching.",
  "properties": {
    "class_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Class Name"
    },
    "expires_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Expires At"
    },
    "fingerprint": {
      "title": "Fingerprint",
      "type": "string"
    },
    "quarantined_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Quarantined At"
    },
    "ready_to_promote": {
      "default": false,
      "title": "Ready To Promote",
      "type": "boolean"
    },
    "reason": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Reason"
    },
    "stale": {
      "default": false,
      "title": "Stale",
      "type": "boolean"
    },
    "status": {
      "title": "Status",
      "type": "string"
    },
    "suite_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Suite Name"
    },
    "test_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Test Name"
    }
  },
  "required": [
    "fingerprint",
    "status"
  ],
  "title": "QuarantineManifestEntry",
  "type": "object"
}
```

## QuarantineManifestResponse

```json
{
  "description": "Versioned quarantine manifest consumed by CI (``testlookup ci-verdict``).\n\nContains ONLY currently-effective quarantines (QUARANTINED /\nRECHECK_SCHEDULED / RE_QUARANTINED) — released, rejected, and expired\nrows never appear, nor do un-reviewed proposals.",
  "properties": {
    "count": {
      "title": "Count",
      "type": "integer"
    },
    "entries": {
      "items": {
        "$ref": "#/components/schemas/QuarantineManifestEntry"
      },
      "title": "Entries",
      "type": "array"
    },
    "etag": {
      "title": "Etag",
      "type": "string"
    },
    "generated_at": {
      "format": "date-time",
      "title": "Generated At",
      "type": "string"
    },
    "project_id": {
      "format": "uuid",
      "title": "Project Id",
      "type": "string"
    },
    "version": {
      "default": 1,
      "title": "Version",
      "type": "integer"
    }
  },
  "required": [
    "project_id",
    "generated_at",
    "etag",
    "count",
    "entries"
  ],
  "title": "QuarantineManifestResponse",
  "type": "object"
}
```

## QuarantineProposeRequest

```json
{
  "description": "Manual proposal — rarely used. Detection agent is the primary path.",
  "properties": {
    "detection_method": {
      "default": "manual",
      "maxLength": 50,
      "title": "Detection Method",
      "type": "string"
    },
    "fail_count": {
      "anyOf": [
        {
          "minimum": 0.0,
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Fail Count"
    },
    "flip_rate": {
      "anyOf": [
        {
          "maximum": 1.0,
          "minimum": 0.0,
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Flip Rate"
    },
    "flip_window_size": {
      "anyOf": [
        {
          "minimum": 1.0,
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Flip Window Size"
    },
    "pass_count": {
      "anyOf": [
        {
          "minimum": 0.0,
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Pass Count"
    },
    "project_id": {
      "format": "uuid",
      "title": "Project Id",
      "type": "string"
    },
    "quarantine_duration_days": {
      "default": 14,
      "maximum": 90.0,
      "minimum": 1.0,
      "title": "Quarantine Duration Days",
      "type": "integer"
    },
    "rationale": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Rationale"
    },
    "suite_name": {
      "anyOf": [
        {
          "maxLength": 500,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Suite Name"
    },
    "test_fingerprint": {
      "maxLength": 64,
      "minLength": 1,
      "title": "Test Fingerprint",
      "type": "string"
    },
    "test_name": {
      "anyOf": [
        {
          "maxLength": 500,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Test Name"
    }
  },
  "required": [
    "project_id",
    "test_fingerprint"
  ],
  "title": "QuarantineProposeRequest",
  "type": "object"
}
```

## QuarantineStatsResponse

```json
{
  "description": "Counts per status for the /quarantine page header tiles.",
  "properties": {
    "approved": {
      "default": 0,
      "title": "Approved",
      "type": "integer"
    },
    "detected": {
      "default": 0,
      "title": "Detected",
      "type": "integer"
    },
    "expired": {
      "default": 0,
      "title": "Expired",
      "type": "integer"
    },
    "meta": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Meta"
    },
    "proposed": {
      "default": 0,
      "title": "Proposed",
      "type": "integer"
    },
    "quarantined": {
      "default": 0,
      "title": "Quarantined",
      "type": "integer"
    },
    "re_quarantined": {
      "default": 0,
      "title": "Re Quarantined",
      "type": "integer"
    },
    "recheck_scheduled": {
      "default": 0,
      "title": "Recheck Scheduled",
      "type": "integer"
    },
    "rejected": {
      "default": 0,
      "title": "Rejected",
      "type": "integer"
    },
    "released": {
      "default": 0,
      "title": "Released",
      "type": "integer"
    },
    "total_live": {
      "default": 0,
      "title": "Total Live",
      "type": "integer"
    }
  },
  "title": "QuarantineStatsResponse",
  "type": "object"
}
```

## RagCaseAcceptEdits

```json
{
  "additionalProperties": false,
  "description": "Content fields a reviewer may change while accepting generated work.",
  "properties": {
    "automation_status": {
      "anyOf": [
        {
          "maxLength": 30,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Automation Status"
    },
    "description": {
      "anyOf": [
        {
          "maxLength": 50000,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Description"
    },
    "estimated_duration_minutes": {
      "anyOf": [
        {
          "minimum": 0.0,
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Estimated Duration Minutes"
    },
    "expected_result": {
      "anyOf": [
        {
          "maxLength": 50000,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Expected Result"
    },
    "feature_area": {
      "anyOf": [
        {
          "maxLength": 500,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Feature Area"
    },
    "is_automated": {
      "anyOf": [
        {
          "type": "boolean"
        },
        {
          "type": "null"
        }
      ],
      "title": "Is Automated"
    },
    "objective": {
      "anyOf": [
        {
          "maxLength": 50000,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Objective"
    },
    "parameters": {
      "anyOf": [
        {
          "items": {
            "$ref": "#/components/schemas/TestCaseParameterSchema"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Parameters"
    },
    "preconditions": {
      "anyOf": [
        {
          "maxLength": 50000,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Preconditions"
    },
    "priority": {
      "anyOf": [
        {
          "maxLength": 30,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Priority"
    },
    "severity": {
      "anyOf": [
        {
          "maxLength": 30,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Severity"
    },
    "steps": {
      "anyOf": [
        {
          "items": {
            "$ref": "#/components/schemas/TestCaseStepSchema"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Steps"
    },
    "tags": {
      "anyOf": [
        {
          "items": {
            "type": "string"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Tags"
    },
    "test_data": {
      "anyOf": [
        {
          "maxLength": 50000,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Test Data"
    },
    "test_type": {
      "anyOf": [
        {
          "maxLength": 50,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Test Type"
    },
    "title": {
      "anyOf": [
        {
          "maxLength": 500,
          "minLength": 3,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Title"
    }
  },
  "title": "RagCaseAcceptEdits",
  "type": "object"
}
```

## RagGenerateRequest

```json
{
  "properties": {
    "generation_config": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Generation Config"
    },
    "persist": {
      "default": false,
      "title": "Persist",
      "type": "boolean"
    },
    "project_id": {
      "format": "uuid",
      "title": "Project Id",
      "type": "string"
    },
    "prompt_text": {
      "default": "",
      "maxLength": 10000,
      "title": "Prompt Text",
      "type": "string"
    },
    "source_ids": {
      "items": {
        "format": "uuid",
        "type": "string"
      },
      "title": "Source Ids",
      "type": "array"
    }
  },
  "required": [
    "project_id"
  ],
  "title": "RagGenerateRequest",
  "type": "object"
}
```

## RagGenerateResponse

```json
{
  "properties": {
    "batch_id": {
      "format": "uuid",
      "title": "Batch Id",
      "type": "string"
    },
    "citations": {
      "items": {
        "$ref": "#/components/schemas/CitationSchema"
      },
      "title": "Citations",
      "type": "array"
    },
    "coverage_summary": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Coverage Summary"
    },
    "created_ids": {
      "items": {
        "type": "string"
      },
      "title": "Created Ids",
      "type": "array"
    },
    "gaps_noted": {
      "items": {
        "type": "string"
      },
      "title": "Gaps Noted",
      "type": "array"
    },
    "generation_mode": {
      "title": "Generation Mode",
      "type": "string"
    },
    "test_cases": {
      "items": {
        "additionalProperties": true,
        "type": "object"
      },
      "title": "Test Cases",
      "type": "array"
    }
  },
  "required": [
    "batch_id",
    "generation_mode",
    "test_cases",
    "citations"
  ],
  "title": "RagGenerateResponse",
  "type": "object"
}
```

## RagRetrieveRequest

```json
{
  "properties": {
    "min_score": {
      "default": 0.0,
      "maximum": 1.0,
      "minimum": 0.0,
      "title": "Min Score",
      "type": "number"
    },
    "project_id": {
      "format": "uuid",
      "title": "Project Id",
      "type": "string"
    },
    "query_text": {
      "maxLength": 5000,
      "minLength": 1,
      "title": "Query Text",
      "type": "string"
    },
    "source_ids": {
      "anyOf": [
        {
          "items": {
            "format": "uuid",
            "type": "string"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Source Ids"
    },
    "top_k": {
      "default": 10,
      "maximum": 50.0,
      "minimum": 1.0,
      "title": "Top K",
      "type": "integer"
    }
  },
  "required": [
    "project_id",
    "query_text"
  ],
  "title": "RagRetrieveRequest",
  "type": "object"
}
```

## RagRetrieveResponse

```json
{
  "properties": {
    "chunks": {
      "items": {
        "$ref": "#/components/schemas/RetrievedChunkSchema"
      },
      "title": "Chunks",
      "type": "array"
    },
    "total": {
      "title": "Total",
      "type": "integer"
    }
  },
  "required": [
    "chunks",
    "total"
  ],
  "title": "RagRetrieveResponse",
  "type": "object"
}
```

## RagStatusResponse

```json
{
  "properties": {
    "enabled": {
      "title": "Enabled",
      "type": "boolean"
    },
    "feature_flag": {
      "default": "KNOWLEDGE_RAG_ENABLED",
      "title": "Feature Flag",
      "type": "string"
    },
    "total_batches": {
      "default": 0,
      "title": "Total Batches",
      "type": "integer"
    },
    "total_chunks": {
      "default": 0,
      "title": "Total Chunks",
      "type": "integer"
    },
    "total_sources": {
      "default": 0,
      "title": "Total Sources",
      "type": "integer"
    }
  },
  "required": [
    "enabled"
  ],
  "title": "RagStatusResponse",
  "type": "object"
}
```

## RecentSearchRead

```json
{
  "properties": {
    "counts_exact": {
      "default": true,
      "title": "Counts Exact",
      "type": "boolean"
    },
    "mode": {
      "title": "Mode",
      "type": "string"
    },
    "project_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Project Id"
    },
    "q": {
      "title": "Q",
      "type": "string"
    },
    "result_count": {
      "title": "Result Count",
      "type": "integer"
    },
    "scope": {
      "title": "Scope",
      "type": "string"
    },
    "ts": {
      "format": "date-time",
      "title": "Ts",
      "type": "string"
    }
  },
  "required": [
    "q",
    "scope",
    "mode",
    "result_count",
    "ts"
  ],
  "title": "RecentSearchRead",
  "type": "object"
}
```

## RecentSearchesResponse

```json
{
  "properties": {
    "items": {
      "items": {
        "$ref": "#/components/schemas/RecentSearchRead"
      },
      "title": "Items",
      "type": "array"
    },
    "max": {
      "title": "Max",
      "type": "integer"
    },
    "retention_days": {
      "title": "Retention Days",
      "type": "integer"
    }
  },
  "required": [
    "items",
    "max",
    "retention_days"
  ],
  "title": "RecentSearchesResponse",
  "type": "object"
}
```

## RedeemResetLinkRequest

```json
{
  "properties": {
    "new_password": {
      "maxLength": 128,
      "minLength": 8,
      "title": "New Password",
      "type": "string"
    },
    "token": {
      "maxLength": 256,
      "minLength": 1,
      "title": "Token",
      "type": "string"
    }
  },
  "required": [
    "token",
    "new_password"
  ],
  "title": "RedeemResetLinkRequest",
  "type": "object"
}
```

## ReferenceBrokenRead

```json
{
  "description": "A link a deletion breaks (a SET NULL foreign key to ``test_runs``) — a warning, never a block.",
  "properties": {
    "column": {
      "title": "Column",
      "type": "string"
    },
    "count": {
      "title": "Count",
      "type": "integer"
    },
    "label": {
      "title": "Label",
      "type": "string"
    },
    "table": {
      "title": "Table",
      "type": "string"
    }
  },
  "required": [
    "table",
    "column",
    "count",
    "label"
  ],
  "title": "ReferenceBrokenRead",
  "type": "object"
}
```

## RefreshRequest

```json
{
  "properties": {
    "refresh_token": {
      "title": "Refresh Token",
      "type": "string"
    }
  },
  "required": [
    "refresh_token"
  ],
  "title": "RefreshRequest",
  "type": "object"
}
```

## RejectCaseRequest

```json
{
  "properties": {
    "reason": {
      "anyOf": [
        {
          "maxLength": 500,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Reason"
    }
  },
  "title": "RejectCaseRequest",
  "type": "object"
}
```

## RejectReviewRequest

```json
{
  "properties": {
    "notes": {
      "anyOf": [
        {
          "maxLength": 4000,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Notes"
    },
    "reason_code": {
      "enum": [
        "wrong_category",
        "unsupported_claim",
        "missing_evidence",
        "contradiction",
        "stale_data",
        "other"
      ],
      "title": "Reason Code",
      "type": "string"
    }
  },
  "required": [
    "reason_code"
  ],
  "title": "RejectReviewRequest",
  "type": "object"
}
```

## ReleaseCouncilOverrideRequest

```json
{
  "description": "Override request with enhanced audit fields.",
  "properties": {
    "override_recommendation": {
      "title": "Override Recommendation",
      "type": "string"
    },
    "reason": {
      "title": "Reason",
      "type": "string"
    }
  },
  "required": [
    "override_recommendation",
    "reason"
  ],
  "title": "ReleaseCouncilOverrideRequest",
  "type": "object"
}
```

## ReleaseCouncilResponse

```json
{
  "description": "Extended release decision with council context.",
  "properties": {
    "band_downgrades": {
      "default": [],
      "items": {
        "type": "string"
      },
      "title": "Band Downgrades",
      "type": "array"
    },
    "band_policy_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Band Policy Id"
    },
    "band_policy_level": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Band Policy Level"
    },
    "band_policy_version": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Band Policy Version"
    },
    "baseline_diff": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/BaselineDiff"
        },
        {
          "type": "null"
        }
      ]
    },
    "blocking_issues": {
      "default": [],
      "items": {
        "type": "string"
      },
      "title": "Blocking Issues",
      "type": "array"
    },
    "build_number": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Build Number"
    },
    "cluster_insights": {
      "default": [],
      "items": {
        "$ref": "#/components/schemas/ClusterInsightResponse"
      },
      "title": "Cluster Insights",
      "type": "array"
    },
    "composite_risk": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Composite Risk"
    },
    "conditions_for_go": {
      "default": [],
      "items": {
        "type": "string"
      },
      "title": "Conditions For Go",
      "type": "array"
    },
    "created_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Created At"
    },
    "dimension_scores": {
      "default": [],
      "items": {
        "$ref": "#/components/schemas/DimensionScore"
      },
      "title": "Dimension Scores",
      "type": "array"
    },
    "draft_recommendation": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Draft Recommendation"
    },
    "human_override": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Human Override"
    },
    "input_snapshot": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Input Snapshot"
    },
    "open_defects_by_component": {
      "default": [],
      "items": {
        "additionalProperties": true,
        "type": "object"
      },
      "title": "Open Defects By Component",
      "type": "array"
    },
    "original_recommendation": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Original Recommendation"
    },
    "original_risk_score": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Original Risk Score"
    },
    "overridden_by": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Overridden By"
    },
    "override_audit": {
      "default": [],
      "items": {
        "$ref": "#/components/schemas/OverrideAuditEntry"
      },
      "title": "Override Audit",
      "type": "array"
    },
    "pass_rate": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Pass Rate"
    },
    "policy_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Policy Id"
    },
    "policy_level": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Policy Level"
    },
    "policy_version": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Policy Version"
    },
    "reasoning": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Reasoning"
    },
    "recommendation": {
      "title": "Recommendation",
      "type": "string"
    },
    "release_readiness_band": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Release Readiness Band"
    },
    "requires_human_review": {
      "default": false,
      "title": "Requires Human Review",
      "type": "boolean"
    },
    "review": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/ReviewBlock"
        },
        {
          "type": "null"
        }
      ]
    },
    "review_gate_enforced": {
      "default": false,
      "title": "Review Gate Enforced",
      "type": "boolean"
    },
    "risk_score": {
      "title": "Risk Score",
      "type": "integer"
    },
    "rule_evaluations": {
      "default": [],
      "items": {
        "$ref": "#/components/schemas/RuleEvaluationResponse"
      },
      "title": "Rule Evaluations",
      "type": "array"
    },
    "run_id": {
      "title": "Run Id",
      "type": "string"
    },
    "score_model_version": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Score Model Version"
    },
    "synthesized": {
      "default": false,
      "title": "Synthesized",
      "type": "boolean"
    },
    "updated_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Updated At"
    }
  },
  "required": [
    "run_id",
    "recommendation",
    "risk_score"
  ],
  "title": "ReleaseCouncilResponse",
  "type": "object"
}
```

## ReleaseGatePolicyCreate

```json
{
  "description": "Create a new draft policy.",
  "properties": {
    "description": {
      "anyOf": [
        {
          "maxLength": 50000,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Description"
    },
    "name": {
      "maxLength": 255,
      "minLength": 2,
      "title": "Name",
      "type": "string"
    },
    "project_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Project Id"
    },
    "rules": {
      "$ref": "#/components/schemas/PolicyDocument"
    }
  },
  "required": [
    "name"
  ],
  "title": "ReleaseGatePolicyCreate",
  "type": "object"
}
```

## ReleaseGatePolicyResponse

```json
{
  "description": "Policy summary response.",
  "properties": {
    "activated_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Activated At"
    },
    "activated_by": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Activated By"
    },
    "activated_by_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Activated By Name"
    },
    "created_at": {
      "format": "date-time",
      "title": "Created At",
      "type": "string"
    },
    "created_by": {
      "format": "uuid",
      "title": "Created By",
      "type": "string"
    },
    "created_by_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Created By Name"
    },
    "description": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Description"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "is_active": {
      "title": "Is Active",
      "type": "boolean"
    },
    "is_draft": {
      "title": "Is Draft",
      "type": "boolean"
    },
    "name": {
      "title": "Name",
      "type": "string"
    },
    "project_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Project Id"
    },
    "rules": {
      "additionalProperties": true,
      "title": "Rules",
      "type": "object"
    },
    "updated_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Updated At"
    },
    "version": {
      "title": "Version",
      "type": "integer"
    }
  },
  "required": [
    "id",
    "version",
    "name",
    "rules",
    "is_active",
    "is_draft",
    "created_by",
    "created_at"
  ],
  "title": "ReleaseGatePolicyResponse",
  "type": "object"
}
```

## ReleaseGatePolicyUpdate

```json
{
  "description": "Update a draft policy (fails if already published).",
  "properties": {
    "description": {
      "anyOf": [
        {
          "maxLength": 50000,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Description"
    },
    "name": {
      "anyOf": [
        {
          "maxLength": 255,
          "minLength": 2,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Name"
    },
    "rules": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/PolicyDocument"
        },
        {
          "type": "null"
        }
      ]
    }
  },
  "title": "ReleaseGatePolicyUpdate",
  "type": "object"
}
```

## ReleaseIn

```json
{
  "properties": {
    "baseline_release_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Baseline Release Id"
    },
    "cutoff_end_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Cutoff End At"
    },
    "cutoff_start_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Cutoff Start At"
    },
    "description": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Description"
    },
    "name": {
      "title": "Name",
      "type": "string"
    },
    "owner_user_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Owner User Id"
    },
    "phases": {
      "default": [],
      "items": {
        "$ref": "#/components/schemas/PhaseIn"
      },
      "title": "Phases",
      "type": "array"
    },
    "planned_date": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Planned Date"
    },
    "project_id": {
      "title": "Project Id",
      "type": "string"
    },
    "release_type": {
      "anyOf": [
        {
          "enum": [
            "major",
            "minor",
            "patch",
            "hotfix",
            "rc"
          ],
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Release Type"
    },
    "status": {
      "default": "planning",
      "title": "Status",
      "type": "string"
    },
    "target_environment": {
      "anyOf": [
        {
          "maxLength": 100,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Target Environment"
    },
    "version": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Version"
    }
  },
  "required": [
    "project_id",
    "name"
  ],
  "title": "ReleaseIn",
  "type": "object"
}
```

## ReleaseOutcomeIn

```json
{
  "additionalProperties": false,
  "description": "A production outcome reported by a release owner for G5 drift.",
  "properties": {
    "outcome": {
      "enum": [
        "incident",
        "rollback"
      ],
      "title": "Outcome",
      "type": "string"
    },
    "reason": {
      "maxLength": 2000,
      "minLength": 3,
      "title": "Reason",
      "type": "string"
    }
  },
  "required": [
    "outcome",
    "reason"
  ],
  "title": "ReleaseOutcomeIn",
  "type": "object"
}
```

## ReleasePhaseBulkItem

```json
{
  "additionalProperties": false,
  "properties": {
    "description": {
      "anyOf": [
        {
          "maxLength": 4000,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Description"
    },
    "exit_criteria": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Exit Criteria"
    },
    "name": {
      "maxLength": 255,
      "minLength": 1,
      "title": "Name",
      "type": "string"
    },
    "notes": {
      "anyOf": [
        {
          "maxLength": 4000,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Notes"
    },
    "phase_type": {
      "default": "qa_testing",
      "maxLength": 50,
      "title": "Phase Type",
      "type": "string"
    },
    "planned_end": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Planned End"
    },
    "planned_start": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Planned Start"
    }
  },
  "required": [
    "name"
  ],
  "title": "ReleasePhaseBulkItem",
  "type": "object"
}
```

## ReleasePhasesBulkRequest

```json
{
  "additionalProperties": false,
  "properties": {
    "model_name": {
      "anyOf": [
        {
          "maxLength": 200,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Model Name"
    },
    "phases": {
      "items": {
        "$ref": "#/components/schemas/ReleasePhaseBulkItem"
      },
      "maxItems": 30,
      "minItems": 1,
      "title": "Phases",
      "type": "array"
    },
    "source": {
      "default": "manual",
      "title": "Source",
      "type": "string"
    }
  },
  "required": [
    "phases"
  ],
  "title": "ReleasePhasesBulkRequest",
  "type": "object"
}
```

## ReleasePhasesCloneRequest

```json
{
  "additionalProperties": false,
  "properties": {
    "from_release_id": {
      "format": "uuid",
      "title": "From Release Id",
      "type": "string"
    },
    "shift_to": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Shift To"
    }
  },
  "required": [
    "from_release_id"
  ],
  "title": "ReleasePhasesCloneRequest",
  "type": "object"
}
```

## ReleasePhasesCreatedResponse

```json
{
  "properties": {
    "created": {
      "title": "Created",
      "type": "integer"
    },
    "phases": {
      "items": {
        "additionalProperties": true,
        "type": "object"
      },
      "title": "Phases",
      "type": "array"
    },
    "release_id": {
      "format": "uuid",
      "title": "Release Id",
      "type": "string"
    },
    "shifted_by_days": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Shifted By Days"
    },
    "source_release_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Source Release Id"
    }
  },
  "required": [
    "release_id",
    "created",
    "phases"
  ],
  "title": "ReleasePhasesCreatedResponse",
  "type": "object"
}
```

## ReleasePortfolioTotals

```json
{
  "properties": {
    "conditional_go": {
      "title": "Conditional Go",
      "type": "integer"
    },
    "evaluated": {
      "title": "Evaluated",
      "type": "integer"
    },
    "go": {
      "title": "Go",
      "type": "integer"
    },
    "no_go": {
      "title": "No Go",
      "type": "integer"
    },
    "not_evaluated": {
      "title": "Not Evaluated",
      "type": "integer"
    },
    "released": {
      "title": "Released",
      "type": "integer"
    },
    "released_with_pack": {
      "title": "Released With Pack",
      "type": "integer"
    }
  },
  "required": [
    "evaluated",
    "go",
    "conditional_go",
    "no_go",
    "not_evaluated",
    "released",
    "released_with_pack"
  ],
  "title": "ReleasePortfolioTotals",
  "type": "object"
}
```

## ReleasePortfolioTrend

```json
{
  "properties": {
    "approximated": {
      "items": {
        "type": "string"
      },
      "title": "Approximated",
      "type": "array"
    },
    "blocked": {
      "items": {
        "anyOf": [
          {
            "type": "integer"
          },
          {
            "type": "null"
          }
        ]
      },
      "title": "Blocked",
      "type": "array"
    },
    "evaluated_in_week": {
      "items": {
        "type": "integer"
      },
      "title": "Evaluated In Week",
      "type": "array"
    },
    "generated_at": {
      "format": "date-time",
      "title": "Generated At",
      "type": "string"
    },
    "go_in_week": {
      "items": {
        "type": "integer"
      },
      "title": "Go In Week",
      "type": "array"
    },
    "in_progress": {
      "items": {
        "anyOf": [
          {
            "type": "integer"
          },
          {
            "type": "null"
          }
        ]
      },
      "title": "In Progress",
      "type": "array"
    },
    "points_at": {
      "items": {
        "format": "date-time",
        "type": "string"
      },
      "title": "Points At",
      "type": "array"
    },
    "project_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Project Id"
    },
    "ready_to_ship": {
      "items": {
        "anyOf": [
          {
            "type": "integer"
          },
          {
            "type": "null"
          }
        ]
      },
      "title": "Ready To Ship",
      "type": "array"
    },
    "released_30d": {
      "items": {
        "anyOf": [
          {
            "type": "integer"
          },
          {
            "type": "null"
          }
        ]
      },
      "title": "Released 30D",
      "type": "array"
    },
    "totals": {
      "$ref": "#/components/schemas/ReleasePortfolioTotals"
    },
    "weeks": {
      "title": "Weeks",
      "type": "integer"
    }
  },
  "required": [
    "weeks",
    "points_at",
    "in_progress",
    "ready_to_ship",
    "blocked",
    "released_30d",
    "evaluated_in_week",
    "go_in_week",
    "totals",
    "approximated",
    "generated_at"
  ],
  "title": "ReleasePortfolioTrend",
  "type": "object"
}
```

## ReleaseSyncIn

```json
{
  "properties": {
    "jira_project_key": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Jira Project Key"
    },
    "project_id": {
      "title": "Project Id",
      "type": "string"
    },
    "source": {
      "enum": [
        "github",
        "jira",
        "gitlab"
      ],
      "title": "Source",
      "type": "string"
    }
  },
  "required": [
    "project_id",
    "source"
  ],
  "title": "ReleaseSyncIn",
  "type": "object"
}
```

## ReleaseUpdate

```json
{
  "properties": {
    "baseline_release_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Baseline Release Id"
    },
    "cutoff_end_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Cutoff End At"
    },
    "cutoff_start_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Cutoff Start At"
    },
    "description": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Description"
    },
    "name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Name"
    },
    "owner_user_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Owner User Id"
    },
    "planned_date": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Planned Date"
    },
    "release_type": {
      "anyOf": [
        {
          "enum": [
            "major",
            "minor",
            "patch",
            "hotfix",
            "rc"
          ],
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Release Type"
    },
    "released_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Released At"
    },
    "status": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Status"
    },
    "target_environment": {
      "anyOf": [
        {
          "maxLength": 100,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Target Environment"
    },
    "version": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Version"
    }
  },
  "title": "ReleaseUpdate",
  "type": "object"
}
```

## ReportEvalCycleRequest

```json
{
  "properties": {
    "authorized_evidence_ids": {
      "items": {
        "type": "string"
      },
      "maxItems": 5000,
      "title": "Authorized Evidence Ids",
      "type": "array"
    },
    "corpus_version": {
      "maxLength": 120,
      "minLength": 1,
      "title": "Corpus Version",
      "type": "string"
    },
    "cycle_key": {
      "anyOf": [
        {
          "maxLength": 128,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Cycle Key"
    },
    "project_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Project Id"
    },
    "reports": {
      "items": {
        "additionalProperties": true,
        "type": "object"
      },
      "maxItems": 200,
      "title": "Reports",
      "type": "array"
    },
    "test_run_ids": {
      "items": {
        "format": "uuid",
        "type": "string"
      },
      "maxItems": 200,
      "title": "Test Run Ids",
      "type": "array"
    }
  },
  "required": [
    "corpus_version"
  ],
  "title": "ReportEvalCycleRequest",
  "type": "object"
}
```

## ReportExportOut

```json
{
  "description": "VIZ-607: one background report export.",
  "properties": {
    "attempts": {
      "title": "Attempts",
      "type": "integer"
    },
    "download_url": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Download Url"
    },
    "error": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Error"
    },
    "expires_at": {
      "format": "date-time",
      "title": "Expires At",
      "type": "string"
    },
    "filename": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Filename"
    },
    "finished_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Finished At"
    },
    "format": {
      "enum": [
        "pdf",
        "xlsx"
      ],
      "title": "Format",
      "type": "string"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "params": {
      "additionalProperties": true,
      "title": "Params",
      "type": "object"
    },
    "project_id": {
      "format": "uuid",
      "title": "Project Id",
      "type": "string"
    },
    "requested_at": {
      "format": "date-time",
      "title": "Requested At",
      "type": "string"
    },
    "retryable": {
      "title": "Retryable",
      "type": "boolean"
    },
    "size_bytes": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Size Bytes"
    },
    "started_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Started At"
    },
    "status": {
      "enum": [
        "queued",
        "running",
        "completed",
        "failed"
      ],
      "title": "Status",
      "type": "string"
    }
  },
  "required": [
    "id",
    "project_id",
    "format",
    "status",
    "attempts",
    "params",
    "requested_at",
    "expires_at",
    "retryable"
  ],
  "title": "ReportExportOut",
  "type": "object"
}
```

## ReportExportRequestOut

```json
{
  "description": "VIZ-607: what ``POST /exports`` decided.",
  "properties": {
    "delivery": {
      "enum": [
        "download",
        "background"
      ],
      "title": "Delivery",
      "type": "string"
    },
    "dispatched": {
      "anyOf": [
        {
          "type": "boolean"
        },
        {
          "type": "null"
        }
      ],
      "title": "Dispatched"
    },
    "estimated_tests": {
      "title": "Estimated Tests",
      "type": "integer"
    },
    "export": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/ReportExportOut"
        },
        {
          "type": "null"
        }
      ]
    }
  },
  "required": [
    "delivery",
    "estimated_tests"
  ],
  "title": "ReportExportRequestOut",
  "type": "object"
}
```

## RequiredModel

```json
{
  "description": "One model the effective configuration depends on.",
  "properties": {
    "in_use": {
      "anyOf": [
        {
          "type": "boolean"
        },
        {
          "type": "null"
        }
      ],
      "title": "In Use"
    },
    "name": {
      "title": "Name",
      "type": "string"
    },
    "present": {
      "title": "Present",
      "type": "boolean"
    },
    "purpose": {
      "title": "Purpose",
      "type": "string"
    },
    "remedy": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Remedy"
    }
  },
  "required": [
    "name",
    "purpose",
    "present"
  ],
  "title": "RequiredModel",
  "type": "object"
}
```

## RequirementCoverageSchema

```json
{
  "properties": {
    "batch_id": {
      "format": "uuid",
      "title": "Batch Id",
      "type": "string"
    },
    "coverage_status": {
      "title": "Coverage Status",
      "type": "string"
    },
    "covered_by_case_ids": {
      "anyOf": [
        {
          "items": {
            "type": "string"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Covered By Case Ids"
    },
    "created_at": {
      "format": "date-time",
      "title": "Created At",
      "type": "string"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "project_id": {
      "format": "uuid",
      "title": "Project Id",
      "type": "string"
    },
    "requirement_id": {
      "title": "Requirement Id",
      "type": "string"
    },
    "requirement_text": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Requirement Text"
    }
  },
  "required": [
    "id",
    "batch_id",
    "project_id",
    "requirement_id",
    "coverage_status",
    "created_at"
  ],
  "title": "RequirementCoverageSchema",
  "type": "object"
}
```

## RequirementCoverageSummary

```json
{
  "properties": {
    "gaps": {
      "title": "Gaps",
      "type": "integer"
    },
    "known": {
      "title": "Known",
      "type": "integer"
    },
    "measured": {
      "title": "Measured",
      "type": "boolean"
    },
    "partly_verified": {
      "title": "Partly Verified",
      "type": "integer"
    },
    "reason": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Reason"
    },
    "top_gaps": {
      "items": {
        "$ref": "#/components/schemas/RequirementGapRead"
      },
      "title": "Top Gaps",
      "type": "array"
    },
    "truncated": {
      "default": false,
      "title": "Truncated",
      "type": "boolean"
    },
    "verified": {
      "title": "Verified",
      "type": "integer"
    }
  },
  "required": [
    "measured",
    "known",
    "verified",
    "partly_verified",
    "gaps"
  ],
  "title": "RequirementCoverageSummary",
  "type": "object"
}
```

## RequirementGapRead

```json
{
  "properties": {
    "batch_claimed_cases": {
      "default": 0,
      "title": "Batch Claimed Cases",
      "type": "integer"
    },
    "key": {
      "title": "Key",
      "type": "string"
    },
    "linked_cases": {
      "default": 0,
      "title": "Linked Cases",
      "type": "integer"
    },
    "normalized_key": {
      "title": "Normalized Key",
      "type": "string"
    },
    "reason": {
      "title": "Reason",
      "type": "string"
    },
    "sources": {
      "items": {
        "type": "string"
      },
      "title": "Sources",
      "type": "array"
    }
  },
  "required": [
    "key",
    "normalized_key",
    "reason",
    "sources"
  ],
  "title": "RequirementGapRead",
  "type": "object"
}
```

## RequirementLinkCreate

```json
{
  "properties": {
    "link_type": {
      "default": "verifies",
      "maxLength": 20,
      "title": "Link Type",
      "type": "string"
    },
    "requirement_key": {
      "maxLength": 200,
      "minLength": 1,
      "title": "Requirement Key",
      "type": "string"
    },
    "requirement_title": {
      "anyOf": [
        {
          "maxLength": 500,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Requirement Title"
    }
  },
  "required": [
    "requirement_key"
  ],
  "title": "RequirementLinkCreate",
  "type": "object"
}
```

## RequirementLinkListResponse

```json
{
  "properties": {
    "items": {
      "items": {
        "$ref": "#/components/schemas/RequirementLinkRead"
      },
      "title": "Items",
      "type": "array"
    },
    "total": {
      "title": "Total",
      "type": "integer"
    }
  },
  "required": [
    "items",
    "total"
  ],
  "title": "RequirementLinkListResponse",
  "type": "object"
}
```

## RequirementLinkRead

```json
{
  "properties": {
    "created_at": {
      "format": "date-time",
      "title": "Created At",
      "type": "string"
    },
    "created_by_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Created By Name"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "link_type": {
      "title": "Link Type",
      "type": "string"
    },
    "normalized_key": {
      "title": "Normalized Key",
      "type": "string"
    },
    "requirement_key": {
      "title": "Requirement Key",
      "type": "string"
    },
    "requirement_title": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Requirement Title"
    },
    "test_case_id": {
      "format": "uuid",
      "title": "Test Case Id",
      "type": "string"
    }
  },
  "required": [
    "id",
    "test_case_id",
    "requirement_key",
    "normalized_key",
    "link_type",
    "created_at"
  ],
  "title": "RequirementLinkRead",
  "type": "object"
}
```

## ResetLinkRequest

```json
{
  "properties": {
    "email": {
      "format": "email",
      "title": "Email",
      "type": "string"
    }
  },
  "required": [
    "email"
  ],
  "title": "ResetLinkRequest",
  "type": "object"
}
```

## RetentionCriteria

```json
{
  "additionalProperties": false,
  "description": "What to delete. Every field is optional; at least one must be given.\n\n``suite_match`` decides what \"in this suite\" means for a run that contains\nseveral. ``only`` — the default — matches the run's own suite label and is\nthe conservative reading: it will not delete a multi-suite run because one\nof its suites was selected. ``any`` matches a run with any test case in the\nsuite, and is the destructive reading, so it is opt-in.",
  "properties": {
    "branches": {
      "anyOf": [
        {
          "items": {
            "type": "string"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Branches"
    },
    "date_from": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Date From"
    },
    "date_to": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Date To"
    },
    "environments": {
      "anyOf": [
        {
          "items": {
            "type": "string"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Environments"
    },
    "older_than_days": {
      "anyOf": [
        {
          "maximum": 3650.0,
          "minimum": 1.0,
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Older Than Days"
    },
    "run_ids": {
      "anyOf": [
        {
          "items": {
            "format": "uuid",
            "type": "string"
          },
          "maxItems": 500,
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Run Ids"
    },
    "statuses": {
      "anyOf": [
        {
          "items": {
            "type": "string"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Statuses"
    },
    "suite_match": {
      "default": "only",
      "enum": [
        "only",
        "any"
      ],
      "title": "Suite Match",
      "type": "string"
    },
    "suite_names": {
      "anyOf": [
        {
          "items": {
            "type": "string"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Suite Names"
    }
  },
  "title": "RetentionCriteria",
  "type": "object"
}
```

## RetentionHoldCreate

```json
{
  "description": "POST ``/projects/{id}/retention-holds`` body.",
  "properties": {
    "entity_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Entity Id"
    },
    "entity_type": {
      "title": "Entity Type",
      "type": "string"
    },
    "expires_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Expires At"
    },
    "reason": {
      "maxLength": 2000,
      "title": "Reason",
      "type": "string"
    }
  },
  "required": [
    "entity_type",
    "reason"
  ],
  "title": "RetentionHoldCreate",
  "type": "object"
}
```

## RetentionHoldListResponse

```json
{
  "properties": {
    "counts": {
      "additionalProperties": {
        "type": "integer"
      },
      "title": "Counts",
      "type": "object"
    },
    "items": {
      "items": {
        "$ref": "#/components/schemas/RetentionHoldRead"
      },
      "title": "Items",
      "type": "array"
    },
    "total": {
      "default": 0,
      "title": "Total",
      "type": "integer"
    }
  },
  "title": "RetentionHoldListResponse",
  "type": "object"
}
```

## RetentionHoldRead

```json
{
  "description": "One hold; ``state`` (live / expired / released) is computed by the service for ``now``.",
  "properties": {
    "entity_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Entity Id"
    },
    "entity_label": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Entity Label"
    },
    "entity_type": {
      "title": "Entity Type",
      "type": "string"
    },
    "expires_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Expires At"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "placed_at": {
      "format": "date-time",
      "title": "Placed At",
      "type": "string"
    },
    "placed_by_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Placed By Name"
    },
    "project_id": {
      "format": "uuid",
      "title": "Project Id",
      "type": "string"
    },
    "reason": {
      "title": "Reason",
      "type": "string"
    },
    "release_reason": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Release Reason"
    },
    "released_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Released At"
    },
    "released_by_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Released By Name"
    },
    "state": {
      "title": "State",
      "type": "string"
    }
  },
  "required": [
    "id",
    "project_id",
    "entity_type",
    "reason",
    "state",
    "placed_at"
  ],
  "title": "RetentionHoldRead",
  "type": "object"
}
```

## RetentionHoldRelease

```json
{
  "description": "POST ``/projects/{id}/retention-holds/{hold_id}/release`` body.",
  "properties": {
    "reason": {
      "maxLength": 2000,
      "title": "Reason",
      "type": "string"
    }
  },
  "required": [
    "reason"
  ],
  "title": "RetentionHoldRelease",
  "type": "object"
}
```

## RetentionLastPurge

```json
{
  "description": "Latest execute-mode purge, parsed from the settings_audit_log\npurge-audit rows (``setting_key = \"retention_purge:{project_id}\"``).",
  "properties": {
    "at": {
      "format": "date-time",
      "title": "At",
      "type": "string"
    },
    "counts": {
      "additionalProperties": true,
      "title": "Counts",
      "type": "object"
    },
    "mode": {
      "default": "execute",
      "title": "Mode",
      "type": "string"
    }
  },
  "required": [
    "at"
  ],
  "title": "RetentionLastPurge",
  "type": "object"
}
```

## RetentionPolicyRead

```json
{
  "description": "GET/PUT response — the EFFECTIVE policy plus its source\n(``default`` = no row, ``custom`` = project row exists).",
  "properties": {
    "ai_versions_per_run": {
      "default": 20,
      "title": "Ai Versions Per Run",
      "type": "integer"
    },
    "ai_versions_per_test": {
      "default": 10,
      "title": "Ai Versions Per Test",
      "type": "integer"
    },
    "artifacts_days": {
      "title": "Artifacts Days",
      "type": "integer"
    },
    "audit_days": {
      "title": "Audit Days",
      "type": "integer"
    },
    "enabled": {
      "title": "Enabled",
      "type": "boolean"
    },
    "field_sources": {
      "additionalProperties": {
        "type": "string"
      },
      "title": "Field Sources",
      "type": "object"
    },
    "inherit_values": {
      "additionalProperties": {
        "anyOf": [
          {
            "type": "boolean"
          },
          {
            "type": "integer"
          }
        ]
      },
      "title": "Inherit Values",
      "type": "object"
    },
    "keep_last_runs": {
      "default": 10,
      "title": "Keep Last Runs",
      "type": "integer"
    },
    "last_purge": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/RetentionLastPurge"
        },
        {
          "type": "null"
        }
      ]
    },
    "next_scheduled_purge_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Next Scheduled Purge At"
    },
    "purge_paused_until": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Purge Paused Until"
    },
    "purge_window_start_utc": {
      "default": 2,
      "title": "Purge Window Start Utc",
      "type": "integer"
    },
    "raw_events_days": {
      "title": "Raw Events Days",
      "type": "integer"
    },
    "runs_days": {
      "title": "Runs Days",
      "type": "integer"
    },
    "source": {
      "default": "default",
      "title": "Source",
      "type": "string"
    }
  },
  "required": [
    "enabled",
    "raw_events_days",
    "runs_days",
    "artifacts_days",
    "audit_days"
  ],
  "title": "RetentionPolicyRead",
  "type": "object"
}
```

## RetentionPolicyWrite

```json
{
  "description": "PUT body for ``/projects/{id}/retention-policy``.\n\nAll fields optional — omitted fields keep their current (or default)\nvalue. Per-field bounds 422 here; the cross-field ``audit_days >=\nruns_days`` check runs in the service on the MERGED values (a partial\nbody can't be validated field-locally).",
  "properties": {
    "ai_versions_per_run": {
      "anyOf": [
        {
          "maximum": 100.0,
          "minimum": 1.0,
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Ai Versions Per Run"
    },
    "ai_versions_per_test": {
      "anyOf": [
        {
          "maximum": 100.0,
          "minimum": 1.0,
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Ai Versions Per Test"
    },
    "artifacts_days": {
      "anyOf": [
        {
          "maximum": 3650.0,
          "minimum": 7.0,
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Artifacts Days"
    },
    "audit_days": {
      "anyOf": [
        {
          "maximum": 3650.0,
          "minimum": 365.0,
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Audit Days"
    },
    "enabled": {
      "anyOf": [
        {
          "type": "boolean"
        },
        {
          "type": "null"
        }
      ],
      "title": "Enabled"
    },
    "inherit": {
      "anyOf": [
        {
          "items": {
            "type": "string"
          },
          "maxItems": 7,
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Inherit"
    },
    "keep_last_runs": {
      "anyOf": [
        {
          "maximum": 1000.0,
          "minimum": 0.0,
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Keep Last Runs"
    },
    "pause_days": {
      "anyOf": [
        {
          "maximum": 90.0,
          "minimum": 1.0,
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Pause Days"
    },
    "purge_window_start_utc": {
      "anyOf": [
        {
          "maximum": 23.0,
          "minimum": 0.0,
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Purge Window Start Utc"
    },
    "raw_events_days": {
      "anyOf": [
        {
          "maximum": 3650.0,
          "minimum": 7.0,
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Raw Events Days"
    },
    "resume": {
      "anyOf": [
        {
          "type": "boolean"
        },
        {
          "type": "null"
        }
      ],
      "title": "Resume"
    },
    "runs_days": {
      "anyOf": [
        {
          "maximum": 3650.0,
          "minimum": 30.0,
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Runs Days"
    }
  },
  "title": "RetentionPolicyWrite",
  "type": "object"
}
```

## RetentionPreviewCandidates

```json
{
  "description": "Per-class candidate counts a purge WOULD delete right now.\n\n**Every class ``run_purge`` counts must have a field here.** FastAPI\nfilters the handler's return through this model, so a class the service\ncounts but the model omits is dropped from the response *silently* — no\nerror, no warning, just a smaller object.\n\nThat happened: the service computed twelve counts and this model declared\neight, so ``evidence_artifact_rows``, ``analysis_cache_entries``,\n``memory_entries_expired`` and ``search_index_documents`` never reached\nthe operator. The preview is what an ADMIN authorises an irreversible\ncross-store purge from, so under-reporting it is not cosmetic — four\ncategories of data were deleted by execute without ever appearing in the\ndry run.",
  "properties": {
    "ai_history_versions": {
      "default": 0,
      "title": "Ai History Versions",
      "type": "integer"
    },
    "analysis_cache_entries": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Analysis Cache Entries"
    },
    "audit_rows": {
      "title": "Audit Rows",
      "type": "integer"
    },
    "compliance_packs_expired": {
      "title": "Compliance Packs Expired",
      "type": "integer"
    },
    "event_archive_rows": {
      "title": "Event Archive Rows",
      "type": "integer"
    },
    "evidence_artifact_rows": {
      "default": 0,
      "title": "Evidence Artifact Rows",
      "type": "integer"
    },
    "export_archives_expired": {
      "default": 0,
      "title": "Export Archives Expired",
      "type": "integer"
    },
    "memory_entries_expired": {
      "default": 0,
      "title": "Memory Entries Expired",
      "type": "integer"
    },
    "minio_objects": {
      "title": "Minio Objects",
      "type": "integer"
    },
    "mongo_docs": {
      "additionalProperties": {
        "type": "integer"
      },
      "title": "Mongo Docs",
      "type": "object"
    },
    "provenance_rows": {
      "title": "Provenance Rows",
      "type": "integer"
    },
    "revoked_share_links": {
      "default": 0,
      "title": "Revoked Share Links",
      "type": "integer"
    },
    "runs": {
      "title": "Runs",
      "type": "integer"
    },
    "search_index_documents": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Search Index Documents"
    },
    "search_usage_rows": {
      "default": 0,
      "title": "Search Usage Rows",
      "type": "integer"
    },
    "shadow_pairs_unlabelled": {
      "default": 0,
      "title": "Shadow Pairs Unlabelled",
      "type": "integer"
    },
    "test_cases": {
      "title": "Test Cases",
      "type": "integer"
    }
  },
  "required": [
    "runs",
    "test_cases",
    "minio_objects",
    "event_archive_rows",
    "audit_rows",
    "provenance_rows",
    "compliance_packs_expired"
  ],
  "title": "RetentionPreviewCandidates",
  "type": "object"
}
```

## RetentionPreviewFloor

```json
{
  "properties": {
    "keep_last_runs": {
      "title": "Keep Last Runs",
      "type": "integer"
    },
    "latest_passed_kept": {
      "title": "Latest Passed Kept",
      "type": "boolean"
    },
    "runs_kept": {
      "title": "Runs Kept",
      "type": "integer"
    },
    "suites_kept": {
      "title": "Suites Kept",
      "type": "integer"
    }
  },
  "required": [
    "keep_last_runs",
    "runs_kept",
    "latest_passed_kept",
    "suites_kept"
  ],
  "title": "RetentionPreviewFloor",
  "type": "object"
}
```

## RetentionPreviewHold

```json
{
  "properties": {
    "entity_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Entity Id"
    },
    "entity_label": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Entity Label"
    },
    "entity_type": {
      "title": "Entity Type",
      "type": "string"
    },
    "expires_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Expires At"
    },
    "hold_id": {
      "format": "uuid",
      "title": "Hold Id",
      "type": "string"
    },
    "packs_kept": {
      "default": 0,
      "title": "Packs Kept",
      "type": "integer"
    },
    "reason": {
      "title": "Reason",
      "type": "string"
    },
    "runs_kept": {
      "default": 0,
      "title": "Runs Kept",
      "type": "integer"
    }
  },
  "required": [
    "hold_id",
    "entity_type",
    "reason"
  ],
  "title": "RetentionPreviewHold",
  "type": "object"
}
```

## RetentionPreviewHolds

```json
{
  "properties": {
    "active": {
      "items": {
        "$ref": "#/components/schemas/RetentionPreviewHold"
      },
      "title": "Active",
      "type": "array"
    },
    "packs_kept": {
      "default": 0,
      "title": "Packs Kept",
      "type": "integer"
    },
    "project_held": {
      "default": false,
      "title": "Project Held",
      "type": "boolean"
    },
    "runs_kept": {
      "default": 0,
      "title": "Runs Kept",
      "type": "integer"
    }
  },
  "title": "RetentionPreviewHolds",
  "type": "object"
}
```

## RetentionPreviewResponse

```json
{
  "description": "POST ``.../retention-policy/preview`` — dry-run, writes nothing.",
  "properties": {
    "candidates": {
      "$ref": "#/components/schemas/RetentionPreviewCandidates"
    },
    "cutoffs": {
      "additionalProperties": {
        "format": "date-time",
        "type": "string"
      },
      "title": "Cutoffs",
      "type": "object"
    },
    "floor": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/RetentionPreviewFloor"
        },
        {
          "type": "null"
        }
      ]
    },
    "holds": {
      "$ref": "#/components/schemas/RetentionPreviewHolds"
    },
    "references_broken": {
      "items": {
        "$ref": "#/components/schemas/ReferenceBrokenRead"
      },
      "title": "References Broken",
      "type": "array"
    },
    "references_measured": {
      "default": true,
      "title": "References Measured",
      "type": "boolean"
    },
    "unmeasured": {
      "items": {
        "type": "string"
      },
      "title": "Unmeasured",
      "type": "array"
    }
  },
  "required": [
    "cutoffs",
    "candidates"
  ],
  "title": "RetentionPreviewResponse",
  "type": "object"
}
```

## RetentionPurgeQueued

```json
{
  "description": "202 body — the execute-mode purge was enqueued to Celery.",
  "properties": {
    "queued": {
      "default": true,
      "title": "Queued",
      "type": "boolean"
    }
  },
  "title": "RetentionPurgeQueued",
  "type": "object"
}
```

## RetentionPurgeRequest

```json
{
  "description": "POST ``.../retention-policy/purge`` — typed-name confirmation\n(``project_reset`` convention): must equal the project name exactly.",
  "properties": {
    "confirmation_name": {
      "maxLength": 255,
      "minLength": 1,
      "title": "Confirmation Name",
      "type": "string"
    }
  },
  "required": [
    "confirmation_name"
  ],
  "title": "RetentionPurgeRequest",
  "type": "object"
}
```

## RetireCompliancePackRequest

```json
{
  "description": "Bring a pack's retention window forward to now.\n\nTyped confirmation rather than a checkbox: a pack is audit evidence, and\nretiring it early hands it to the next nightly purge.",
  "properties": {
    "confirmation_id": {
      "description": "Must equal the pack id exactly.",
      "maxLength": 64,
      "minLength": 1,
      "title": "Confirmation Id",
      "type": "string"
    },
    "reason": {
      "maxLength": 500,
      "minLength": 3,
      "title": "Reason",
      "type": "string"
    }
  },
  "required": [
    "confirmation_id",
    "reason"
  ],
  "title": "RetireCompliancePackRequest",
  "type": "object"
}
```

## RetrievedChunkSchema

```json
{
  "properties": {
    "canonical_url": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Canonical Url"
    },
    "chunk_text": {
      "title": "Chunk Text",
      "type": "string"
    },
    "relevance_score": {
      "title": "Relevance Score",
      "type": "number"
    },
    "requirement_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Requirement Id"
    },
    "section_heading": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Section Heading"
    },
    "source_id": {
      "format": "uuid",
      "title": "Source Id",
      "type": "string"
    },
    "source_title": {
      "title": "Source Title",
      "type": "string"
    },
    "vector_id": {
      "title": "Vector Id",
      "type": "string"
    }
  },
  "required": [
    "vector_id",
    "source_id",
    "source_title",
    "chunk_text",
    "relevance_score"
  ],
  "title": "RetrievedChunkSchema",
  "type": "object"
}
```

## RetryConfig

```json
{
  "additionalProperties": false,
  "properties": {
    "base_seconds": {
      "minimum": 1.0,
      "title": "Base Seconds",
      "type": "integer"
    },
    "cap_seconds": {
      "minimum": 1.0,
      "title": "Cap Seconds",
      "type": "integer"
    },
    "jitter": {
      "default": 0.2,
      "exclusiveMaximum": 1.0,
      "minimum": 0.0,
      "title": "Jitter",
      "type": "number"
    },
    "max_attempts": {
      "minimum": 1.0,
      "title": "Max Attempts",
      "type": "integer"
    },
    "retry_on": {
      "items": {
        "type": "string"
      },
      "title": "Retry On",
      "type": "array"
    }
  },
  "title": "RetryConfig",
  "type": "object"
}
```

## ReviewActionRequest

```json
{
  "properties": {
    "action": {
      "enum": [
        "approve",
        "reject",
        "request_changes"
      ],
      "title": "Action",
      "type": "string"
    },
    "notes": {
      "anyOf": [
        {
          "maxLength": 50000,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Notes"
    }
  },
  "required": [
    "action"
  ],
  "title": "ReviewActionRequest",
  "type": "object"
}
```

## ReviewBlock

```json
{
  "description": "Human-review status of an AI report (architecture section 8.2, E8.3).\n\nCarries WHETHER and WHEN a report was reviewed, never WHO: reviewer identity\nstays in-app, out of API and export payloads.",
  "properties": {
    "message": {
      "title": "Message",
      "type": "string"
    },
    "review_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Review Id"
    },
    "reviewed_at": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Reviewed At"
    },
    "state": {
      "description": "pending_review: a draft until a human accepts it (also used when no review was ever recorded). not_applicable: the content was not AI-generated.",
      "enum": [
        "pending_review",
        "accepted",
        "rejected",
        "superseded",
        "not_applicable"
      ],
      "title": "State",
      "type": "string"
    }
  },
  "required": [
    "state",
    "message"
  ],
  "title": "ReviewBlock",
  "type": "object"
}
```

## ReviewConfig

```json
{
  "additionalProperties": false,
  "properties": {
    "auto_reviewer": {
      "default": false,
      "title": "Auto Reviewer",
      "type": "boolean"
    },
    "policy": {
      "default": "human_required",
      "enum": [
        "human_required",
        "human_required_plus_auto_reviewer"
      ],
      "title": "Policy",
      "type": "string"
    },
    "second_model_check": {
      "default": false,
      "title": "Second Model Check",
      "type": "boolean"
    }
  },
  "title": "ReviewConfig",
  "type": "object"
}
```

## ReviewGateRead

```json
{
  "description": "GET/PUT ``/api/v1/settings/review-gate``. Writes nothing on GET.",
  "properties": {
    "channels": {
      "items": {
        "$ref": "#/components/schemas/GatedChannel"
      },
      "title": "Channels",
      "type": "array"
    },
    "enforced": {
      "title": "Enforced",
      "type": "boolean"
    },
    "enforced_from": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Enforced From"
    },
    "env_override": {
      "title": "Env Override",
      "type": "boolean"
    },
    "source": {
      "title": "Source",
      "type": "string"
    },
    "updated_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Updated At"
    },
    "would_refuse": {
      "$ref": "#/components/schemas/WouldRefuseSummary"
    }
  },
  "required": [
    "enforced",
    "source",
    "env_override",
    "would_refuse",
    "channels"
  ],
  "title": "ReviewGateRead",
  "type": "object"
}
```

## ReviewGateWrite

```json
{
  "additionalProperties": false,
  "description": "PUT body. ``None`` clears (back to audit only); a naive datetime is the route's 422 ``naive_datetime``.",
  "properties": {
    "enforced_from": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Enforced From"
    }
  },
  "title": "ReviewGateWrite",
  "type": "object"
}
```

## ReviewQueueSummary

```json
{
  "properties": {
    "items": {
      "items": {
        "$ref": "#/components/schemas/LibraryCaseRow"
      },
      "title": "Items",
      "type": "array"
    },
    "measured": {
      "default": true,
      "title": "Measured",
      "type": "boolean"
    },
    "oldest_age_days": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Oldest Age Days"
    },
    "reason": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Reason"
    },
    "total": {
      "title": "Total",
      "type": "integer"
    }
  },
  "required": [
    "total",
    "items"
  ],
  "title": "ReviewQueueSummary",
  "type": "object"
}
```

## ReviewResponse

```json
{
  "description": "A review request as clients see it. No reviewer identity (section 8.2).",
  "properties": {
    "ai_disclaimer": {
      "title": "Ai Disclaimer",
      "type": "string"
    },
    "ai_disclaimer_version": {
      "title": "Ai Disclaimer Version",
      "type": "string"
    },
    "created_at": {
      "format": "date-time",
      "title": "Created At",
      "type": "string"
    },
    "evidence_bundle_sha256": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Evidence Bundle Sha256"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "kind": {
      "title": "Kind",
      "type": "string"
    },
    "notes": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Notes"
    },
    "pipeline_run_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Pipeline Run Id"
    },
    "project_id": {
      "format": "uuid",
      "title": "Project Id",
      "type": "string"
    },
    "reason_code": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Reason Code"
    },
    "requires_human_review": {
      "default": true,
      "title": "Requires Human Review",
      "type": "boolean"
    },
    "reviewed": {
      "title": "Reviewed",
      "type": "boolean"
    },
    "reviewed_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Reviewed At"
    },
    "state": {
      "enum": [
        "pending_review",
        "accepted",
        "rejected",
        "superseded"
      ],
      "title": "State",
      "type": "string"
    },
    "subject_id": {
      "title": "Subject Id",
      "type": "string"
    },
    "subject_type": {
      "title": "Subject Type",
      "type": "string"
    },
    "superseded_by": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Superseded By"
    },
    "test_run_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Test Run Id"
    },
    "workflow_type": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Workflow Type"
    }
  },
  "required": [
    "id",
    "project_id",
    "kind",
    "subject_type",
    "subject_id",
    "state",
    "reviewed",
    "created_at",
    "ai_disclaimer",
    "ai_disclaimer_version"
  ],
  "title": "ReviewResponse",
  "type": "object"
}
```

## ReviewerCleanObservation

```json
{
  "additionalProperties": false,
  "properties": {
    "flagged_families": {
      "items": {
        "type": "integer"
      },
      "title": "Flagged Families",
      "type": "array",
      "uniqueItems": true
    },
    "observed_at": {
      "format": "date-time",
      "title": "Observed At",
      "type": "string"
    },
    "sample_id": {
      "maxLength": 160,
      "minLength": 1,
      "title": "Sample Id",
      "type": "string"
    }
  },
  "required": [
    "sample_id",
    "observed_at"
  ],
  "title": "ReviewerCleanObservation",
  "type": "object"
}
```

## ReviewerHumanOutcome

```json
{
  "additionalProperties": false,
  "properties": {
    "human_rejected": {
      "title": "Human Rejected",
      "type": "boolean"
    },
    "observed_at": {
      "format": "date-time",
      "title": "Observed At",
      "type": "string"
    },
    "review_id": {
      "maxLength": 160,
      "minLength": 1,
      "title": "Review Id",
      "type": "string"
    },
    "reviewer_passed": {
      "title": "Reviewer Passed",
      "type": "boolean"
    }
  },
  "required": [
    "review_id",
    "reviewer_passed",
    "human_rejected",
    "observed_at"
  ],
  "title": "ReviewerHumanOutcome",
  "type": "object"
}
```

## ReviewerMutationObservation

```json
{
  "additionalProperties": false,
  "properties": {
    "detected_families": {
      "items": {
        "type": "integer"
      },
      "title": "Detected Families",
      "type": "array",
      "uniqueItems": true
    },
    "mutation_class": {
      "$ref": "#/components/schemas/MutationClass"
    },
    "observed_at": {
      "format": "date-time",
      "title": "Observed At",
      "type": "string"
    },
    "sample_id": {
      "maxLength": 160,
      "minLength": 1,
      "title": "Sample Id",
      "type": "string"
    }
  },
  "required": [
    "sample_id",
    "mutation_class",
    "observed_at"
  ],
  "title": "ReviewerMutationObservation",
  "type": "object"
}
```

## ReviewerQualityRequest

```json
{
  "additionalProperties": false,
  "properties": {
    "agent_id": {
      "default": "agent.reviewer.v1",
      "maxLength": 80,
      "minLength": 1,
      "title": "Agent Id",
      "type": "string"
    },
    "auto_disable": {
      "default": true,
      "title": "Auto Disable",
      "type": "boolean"
    },
    "clean": {
      "items": {
        "$ref": "#/components/schemas/ReviewerCleanObservation"
      },
      "maxItems": 10000,
      "title": "Clean",
      "type": "array"
    },
    "human_outcomes": {
      "items": {
        "$ref": "#/components/schemas/ReviewerHumanOutcome"
      },
      "maxItems": 10000,
      "title": "Human Outcomes",
      "type": "array"
    },
    "mutations": {
      "items": {
        "$ref": "#/components/schemas/ReviewerMutationObservation"
      },
      "maxItems": 10000,
      "title": "Mutations",
      "type": "array"
    },
    "persist": {
      "default": true,
      "title": "Persist",
      "type": "boolean"
    },
    "project_id": {
      "format": "uuid",
      "title": "Project Id",
      "type": "string"
    }
  },
  "required": [
    "project_id"
  ],
  "title": "ReviewerQualityRequest",
  "type": "object"
}
```

## ReviewerQualityRunListResponse

```json
{
  "properties": {
    "items": {
      "items": {
        "$ref": "#/components/schemas/ReviewerQualityRunRead"
      },
      "title": "Items",
      "type": "array"
    },
    "total": {
      "title": "Total",
      "type": "integer"
    }
  },
  "required": [
    "items",
    "total"
  ],
  "title": "ReviewerQualityRunListResponse",
  "type": "object"
}
```

## ReviewerQualityRunRead

```json
{
  "description": "An ``AIEvalReviewerQuality`` row WITHOUT its observation arrays (W16).",
  "properties": {
    "agent_id": {
      "title": "Agent Id",
      "type": "string"
    },
    "auto_disable_applied": {
      "title": "Auto Disable Applied",
      "type": "boolean"
    },
    "auto_disable_eligible": {
      "title": "Auto Disable Eligible",
      "type": "boolean"
    },
    "clean_sample_count": {
      "title": "Clean Sample Count",
      "type": "integer"
    },
    "deterministic_recall": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Deterministic Recall"
    },
    "evaluated_at": {
      "format": "date-time",
      "title": "Evaluated At",
      "type": "string"
    },
    "false_flag_rate": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "False Flag Rate"
    },
    "false_omission_rate": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "False Omission Rate"
    },
    "human_outcome_count": {
      "title": "Human Outcome Count",
      "type": "integer"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "metrics": {
      "items": {
        "additionalProperties": true,
        "type": "object"
      },
      "title": "Metrics",
      "type": "array"
    },
    "project_id": {
      "format": "uuid",
      "title": "Project Id",
      "type": "string"
    },
    "recall_delta": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Recall Delta"
    },
    "regressions": {
      "items": {
        "additionalProperties": true,
        "type": "object"
      },
      "title": "Regressions",
      "type": "array"
    },
    "second_model_recall": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Second Model Recall"
    },
    "semantic_sample_counts": {
      "additionalProperties": true,
      "title": "Semantic Sample Counts",
      "type": "object"
    },
    "source": {
      "title": "Source",
      "type": "string"
    },
    "status": {
      "title": "Status",
      "type": "string"
    },
    "window_ended_at": {
      "format": "date-time",
      "title": "Window Ended At",
      "type": "string"
    },
    "window_started_at": {
      "format": "date-time",
      "title": "Window Started At",
      "type": "string"
    }
  },
  "required": [
    "id",
    "project_id",
    "agent_id",
    "source",
    "status",
    "window_started_at",
    "window_ended_at",
    "evaluated_at",
    "metrics",
    "regressions",
    "semantic_sample_counts",
    "clean_sample_count",
    "human_outcome_count",
    "auto_disable_eligible",
    "auto_disable_applied"
  ],
  "title": "ReviewerQualityRunRead",
  "type": "object"
}
```

## RoleActions

```json
{
  "description": "Role-aware recommended actions generated by the ReAct agent.",
  "properties": {
    "developer": {
      "default": "",
      "title": "Developer",
      "type": "string"
    },
    "qa": {
      "default": "",
      "title": "Qa",
      "type": "string"
    },
    "release_manager": {
      "default": "",
      "title": "Release Manager",
      "type": "string"
    },
    "sre": {
      "default": "",
      "title": "Sre",
      "type": "string"
    }
  },
  "title": "RoleActions",
  "type": "object"
}
```

## RuleEvaluationResponse

```json
{
  "description": "Result of evaluating a single policy rule.",
  "properties": {
    "action": {
      "title": "Action",
      "type": "string"
    },
    "actual_value": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Actual Value"
    },
    "message": {
      "title": "Message",
      "type": "string"
    },
    "passed": {
      "title": "Passed",
      "type": "boolean"
    },
    "rule_id": {
      "title": "Rule Id",
      "type": "string"
    },
    "rule_name": {
      "title": "Rule Name",
      "type": "string"
    },
    "rule_type": {
      "title": "Rule Type",
      "type": "string"
    },
    "threshold_value": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Threshold Value"
    }
  },
  "required": [
    "rule_id",
    "rule_name",
    "rule_type",
    "passed",
    "action",
    "message"
  ],
  "title": "RuleEvaluationResponse",
  "type": "object"
}
```

## RunBranchItem

```json
{
  "properties": {
    "branch": {
      "title": "Branch",
      "type": "string"
    },
    "last_run_at": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Last Run At"
    },
    "runs": {
      "title": "Runs",
      "type": "integer"
    }
  },
  "required": [
    "branch",
    "runs"
  ],
  "title": "RunBranchItem",
  "type": "object"
}
```

## RunBranchListResponse

```json
{
  "properties": {
    "items": {
      "items": {
        "$ref": "#/components/schemas/RunBranchItem"
      },
      "title": "Items",
      "type": "array"
    },
    "total": {
      "title": "Total",
      "type": "integer"
    },
    "truncated": {
      "title": "Truncated",
      "type": "boolean"
    },
    "window_days": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Window Days"
    }
  },
  "required": [
    "items",
    "total",
    "truncated"
  ],
  "title": "RunBranchListResponse",
  "type": "object"
}
```

## RunBrief

```json
{
  "properties": {
    "branch": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Branch"
    },
    "build_number": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Build Number"
    },
    "created_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Created At"
    },
    "finished_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Finished At"
    },
    "primary_suite_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Primary Suite Name"
    },
    "run_id": {
      "title": "Run Id",
      "type": "string"
    },
    "run_seq": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Run Seq"
    },
    "status": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Status"
    }
  },
  "required": [
    "run_id"
  ],
  "title": "RunBrief",
  "type": "object"
}
```

## RunCadenceCreate

```json
{
  "additionalProperties": false,
  "properties": {
    "cron": {
      "anyOf": [
        {
          "maxLength": 100,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Cron"
    },
    "expected_by": {
      "anyOf": [
        {
          "pattern": "^([01]\\d|2[0-3]):[0-5]\\d$",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Expected By"
    },
    "grace_minutes": {
      "default": 60,
      "maximum": 720.0,
      "minimum": 0.0,
      "title": "Grace Minutes",
      "type": "integer"
    },
    "mode": {
      "default": "weekly",
      "title": "Mode",
      "type": "string"
    },
    "suite_name": {
      "anyOf": [
        {
          "maxLength": 500,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Suite Name"
    },
    "timezone": {
      "maxLength": 64,
      "minLength": 1,
      "title": "Timezone",
      "type": "string"
    },
    "weekdays": {
      "anyOf": [
        {
          "items": {
            "type": "string"
          },
          "maxItems": 7,
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Weekdays"
    }
  },
  "required": [
    "timezone"
  ],
  "title": "RunCadenceCreate",
  "type": "object"
}
```

## RunCadenceList

```json
{
  "properties": {
    "items": {
      "items": {
        "$ref": "#/components/schemas/RunCadenceRead"
      },
      "title": "Items",
      "type": "array"
    },
    "total": {
      "default": 0,
      "title": "Total",
      "type": "integer"
    }
  },
  "title": "RunCadenceList",
  "type": "object"
}
```

## RunCadencePatch

```json
{
  "additionalProperties": false,
  "description": "Every field optional; an empty PATCH changes nothing.",
  "properties": {
    "clear_suite": {
      "default": false,
      "title": "Clear Suite",
      "type": "boolean"
    },
    "cron": {
      "anyOf": [
        {
          "maxLength": 100,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Cron"
    },
    "expected_by": {
      "anyOf": [
        {
          "pattern": "^([01]\\d|2[0-3]):[0-5]\\d$",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Expected By"
    },
    "grace_minutes": {
      "anyOf": [
        {
          "maximum": 720.0,
          "minimum": 0.0,
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Grace Minutes"
    },
    "mode": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Mode"
    },
    "suite_name": {
      "anyOf": [
        {
          "maxLength": 500,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Suite Name"
    },
    "timezone": {
      "anyOf": [
        {
          "maxLength": 64,
          "minLength": 1,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Timezone"
    },
    "weekdays": {
      "anyOf": [
        {
          "items": {
            "type": "string"
          },
          "maxItems": 7,
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Weekdays"
    }
  },
  "title": "RunCadencePatch",
  "type": "object"
}
```

## RunCadencePauseRequest

```json
{
  "additionalProperties": false,
  "properties": {
    "reason": {
      "maxLength": 200,
      "title": "Reason",
      "type": "string"
    },
    "until": {
      "format": "date-time",
      "title": "Until",
      "type": "string"
    }
  },
  "required": [
    "until",
    "reason"
  ],
  "title": "RunCadencePauseRequest",
  "type": "object"
}
```

## RunCadenceRead

```json
{
  "properties": {
    "created_at": {
      "format": "date-time",
      "title": "Created At",
      "type": "string"
    },
    "created_by_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Created By Name"
    },
    "cron": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Cron"
    },
    "effective_from": {
      "format": "date-time",
      "title": "Effective From",
      "type": "string"
    },
    "expected_by": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Expected By"
    },
    "grace_minutes": {
      "default": 60,
      "title": "Grace Minutes",
      "type": "integer"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "mode": {
      "default": "weekly",
      "title": "Mode",
      "type": "string"
    },
    "next_deadline": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Next Deadline"
    },
    "paused": {
      "default": false,
      "title": "Paused",
      "type": "boolean"
    },
    "paused_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Paused At"
    },
    "paused_by_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Paused By Name"
    },
    "paused_reason": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Paused Reason"
    },
    "paused_until": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Paused Until"
    },
    "project_id": {
      "format": "uuid",
      "title": "Project Id",
      "type": "string"
    },
    "suite_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Suite Name"
    },
    "summary": {
      "default": "",
      "title": "Summary",
      "type": "string"
    },
    "timezone": {
      "default": "UTC",
      "title": "Timezone",
      "type": "string"
    },
    "updated_at": {
      "format": "date-time",
      "title": "Updated At",
      "type": "string"
    },
    "weekdays": {
      "anyOf": [
        {
          "items": {
            "type": "string"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Weekdays"
    }
  },
  "required": [
    "id",
    "project_id",
    "effective_from",
    "created_at",
    "updated_at"
  ],
  "title": "RunCadenceRead",
  "type": "object"
}
```

## RunCadenceStatusItem

```json
{
  "properties": {
    "cadence": {
      "$ref": "#/components/schemas/RunCadenceRead"
    },
    "expected": {
      "default": 0,
      "title": "Expected",
      "type": "integer"
    },
    "last_run_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Last Run At"
    },
    "measured": {
      "default": false,
      "title": "Measured",
      "type": "boolean"
    },
    "met": {
      "default": 0,
      "title": "Met",
      "type": "integer"
    },
    "missed": {
      "default": 0,
      "title": "Missed",
      "type": "integer"
    },
    "missed_windows": {
      "items": {
        "$ref": "#/components/schemas/CadenceWindowStatus"
      },
      "title": "Missed Windows",
      "type": "array"
    },
    "paused": {
      "default": 0,
      "title": "Paused",
      "type": "integer"
    },
    "pending": {
      "default": 0,
      "title": "Pending",
      "type": "integer"
    },
    "reason": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Reason"
    }
  },
  "required": [
    "cadence"
  ],
  "title": "RunCadenceStatusItem",
  "type": "object"
}
```

## RunCadenceStatusResponse

```json
{
  "properties": {
    "configured": {
      "default": false,
      "title": "Configured",
      "type": "boolean"
    },
    "days": {
      "default": 14,
      "title": "Days",
      "type": "integer"
    },
    "expected": {
      "default": 0,
      "title": "Expected",
      "type": "integer"
    },
    "items": {
      "items": {
        "$ref": "#/components/schemas/RunCadenceStatusItem"
      },
      "title": "Items",
      "type": "array"
    },
    "met": {
      "default": 0,
      "title": "Met",
      "type": "integer"
    },
    "missed": {
      "default": 0,
      "title": "Missed",
      "type": "integer"
    },
    "paused": {
      "default": 0,
      "title": "Paused",
      "type": "integer"
    },
    "total": {
      "default": 0,
      "title": "Total",
      "type": "integer"
    },
    "window_end": {
      "format": "date-time",
      "title": "Window End",
      "type": "string"
    },
    "window_start": {
      "format": "date-time",
      "title": "Window Start",
      "type": "string"
    }
  },
  "required": [
    "window_start",
    "window_end"
  ],
  "title": "RunCadenceStatusResponse",
  "type": "object"
}
```

## RunCompareAIReport

```json
{
  "properties": {
    "ai_changes": {
      "items": {
        "type": "string"
      },
      "title": "Ai Changes",
      "type": "array"
    },
    "confidence": {
      "default": 0,
      "title": "Confidence",
      "type": "integer"
    },
    "confidence_reason": {
      "default": "",
      "title": "Confidence Reason",
      "type": "string"
    },
    "duration_concerns": {
      "items": {
        "type": "string"
      },
      "title": "Duration Concerns",
      "type": "array"
    },
    "executive_summary": {
      "default": "",
      "title": "Executive Summary",
      "type": "string"
    },
    "fallback_used": {
      "default": false,
      "title": "Fallback Used",
      "type": "boolean"
    },
    "key_differences": {
      "items": {
        "type": "string"
      },
      "title": "Key Differences",
      "type": "array"
    },
    "markdown_report": {
      "default": "",
      "title": "Markdown Report",
      "type": "string"
    },
    "message": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Message"
    },
    "new_risks": {
      "items": {
        "type": "string"
      },
      "title": "New Risks",
      "type": "array"
    },
    "recommended_actions": {
      "items": {
        "type": "string"
      },
      "title": "Recommended Actions",
      "type": "array"
    },
    "resolved_risks": {
      "items": {
        "type": "string"
      },
      "title": "Resolved Risks",
      "type": "array"
    },
    "risk_level": {
      "default": "LOW",
      "enum": [
        "LOW",
        "MEDIUM",
        "HIGH",
        "CRITICAL"
      ],
      "title": "Risk Level",
      "type": "string"
    },
    "status": {
      "default": "ready",
      "enum": [
        "ready",
        "queued",
        "failed"
      ],
      "title": "Status",
      "type": "string"
    }
  },
  "title": "RunCompareAIReport",
  "type": "object"
}
```

## RunCompareResponse

```json
{
  "properties": {
    "ai_analysis_diff": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/AIAnalysisDiffBrief"
        },
        {
          "type": "null"
        }
      ]
    },
    "ai_report": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/RunCompareAIReport"
        },
        {
          "type": "null"
        }
      ]
    },
    "delta_broken": {
      "default": 0,
      "title": "Delta Broken",
      "type": "integer"
    },
    "delta_duration_ms": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Delta Duration Ms"
    },
    "delta_failed": {
      "default": 0,
      "title": "Delta Failed",
      "type": "integer"
    },
    "delta_pass_rate": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Delta Pass Rate"
    },
    "delta_passed": {
      "default": 0,
      "title": "Delta Passed",
      "type": "integer"
    },
    "delta_skipped": {
      "default": 0,
      "title": "Delta Skipped",
      "type": "integer"
    },
    "delta_total": {
      "default": 0,
      "title": "Delta Total",
      "type": "integer"
    },
    "duration_spikes": {
      "default": 0,
      "title": "Duration Spikes",
      "type": "integer"
    },
    "fixed": {
      "default": 0,
      "title": "Fixed",
      "type": "integer"
    },
    "improved": {
      "default": 0,
      "title": "Improved",
      "type": "integer"
    },
    "left": {
      "$ref": "#/components/schemas/RunCompareSummary"
    },
    "new_failures": {
      "default": 0,
      "title": "New Failures",
      "type": "integer"
    },
    "new_tests": {
      "default": 0,
      "title": "New Tests",
      "type": "integer"
    },
    "regressed": {
      "default": 0,
      "title": "Regressed",
      "type": "integer"
    },
    "removed_tests": {
      "default": 0,
      "title": "Removed Tests",
      "type": "integer"
    },
    "renamed": {
      "default": 0,
      "title": "Renamed",
      "type": "integer"
    },
    "right": {
      "$ref": "#/components/schemas/RunCompareSummary"
    },
    "scope": {
      "default": "run",
      "enum": [
        "run",
        "suite"
      ],
      "title": "Scope",
      "type": "string"
    },
    "selection": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/RunCompareSelection"
        },
        {
          "type": "null"
        }
      ]
    },
    "still_failing": {
      "default": 0,
      "title": "Still Failing",
      "type": "integer"
    },
    "suite_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Suite Name"
    },
    "test_deltas": {
      "items": {
        "$ref": "#/components/schemas/RunCompareTestDelta"
      },
      "title": "Test Deltas",
      "type": "array"
    },
    "transitions": {
      "items": {
        "$ref": "#/components/schemas/RunCompareTransition"
      },
      "title": "Transitions",
      "type": "array"
    },
    "truncated": {
      "default": false,
      "title": "Truncated",
      "type": "boolean"
    }
  },
  "required": [
    "left",
    "right"
  ],
  "title": "RunCompareResponse",
  "type": "object"
}
```

## RunCompareSelection

```json
{
  "properties": {
    "branch": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Branch"
    },
    "branch_mismatch": {
      "default": false,
      "title": "Branch Mismatch",
      "type": "boolean"
    },
    "mode": {
      "default": "explicit",
      "enum": [
        "latest_vs_previous",
        "explicit"
      ],
      "title": "Mode",
      "type": "string"
    },
    "project_id": {
      "format": "uuid",
      "title": "Project Id",
      "type": "string"
    },
    "release_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Release Name"
    },
    "scope": {
      "default": "run",
      "enum": [
        "run",
        "suite"
      ],
      "title": "Scope",
      "type": "string"
    },
    "selection_reason": {
      "default": "",
      "title": "Selection Reason",
      "type": "string"
    },
    "suite_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Suite Name"
    }
  },
  "required": [
    "project_id"
  ],
  "title": "RunCompareSelection",
  "type": "object"
}
```

## RunCompareSummary

```json
{
  "description": "One side of the compare view — the subset of TestRun fields used\nby the diff UI. Kept tiny so the JSON payload is fast even on big runs.",
  "properties": {
    "branch": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Branch"
    },
    "broken_tests": {
      "default": 0,
      "title": "Broken Tests",
      "type": "integer"
    },
    "build_number": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Build Number"
    },
    "commit_hash": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Commit Hash"
    },
    "duration_ms": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Duration Ms"
    },
    "end_time": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "End Time"
    },
    "failed_tests": {
      "default": 0,
      "title": "Failed Tests",
      "type": "integer"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "pass_rate": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Pass Rate"
    },
    "passed_tests": {
      "default": 0,
      "title": "Passed Tests",
      "type": "integer"
    },
    "primary_suite_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Primary Suite Name"
    },
    "project_id": {
      "format": "uuid",
      "title": "Project Id",
      "type": "string"
    },
    "skipped_tests": {
      "default": 0,
      "title": "Skipped Tests",
      "type": "integer"
    },
    "start_time": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Start Time"
    },
    "status": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Status"
    },
    "suite_names": {
      "anyOf": [
        {
          "items": {
            "type": "string"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Suite Names"
    },
    "total_tests": {
      "default": 0,
      "title": "Total Tests",
      "type": "integer"
    },
    "unknown_tests": {
      "default": 0,
      "title": "Unknown Tests",
      "type": "integer"
    }
  },
  "required": [
    "id",
    "project_id"
  ],
  "title": "RunCompareSummary",
  "type": "object"
}
```

## RunCompareTestDelta

```json
{
  "description": "A single test whose status or duration differed between the two runs.",
  "properties": {
    "classification": {
      "title": "Classification",
      "type": "string"
    },
    "delta_duration_ms": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Delta Duration Ms"
    },
    "left_duration_ms": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Left Duration Ms"
    },
    "left_status": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Left Status"
    },
    "paired_by": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Paired By"
    },
    "previous_test_fingerprint": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Previous Test Fingerprint"
    },
    "previous_test_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Previous Test Name"
    },
    "right_duration_ms": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Right Duration Ms"
    },
    "right_status": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Right Status"
    },
    "suite_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Suite Name"
    },
    "test_fingerprint": {
      "title": "Test Fingerprint",
      "type": "string"
    },
    "test_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Test Name"
    }
  },
  "required": [
    "test_fingerprint",
    "classification"
  ],
  "title": "RunCompareTestDelta",
  "type": "object"
}
```

## RunCompareTransition

```json
{
  "description": "VIZ-507: how many tests went from one status to another between the runs.\n\n``absent`` on a side means the test is not in that run (new / removed).\nEvery test is in exactly one transition: the counts add up to each run's\ntest count, which is what makes the Sankey's flows conserve.",
  "properties": {
    "after": {
      "enum": [
        "passed",
        "failed",
        "broken",
        "skipped",
        "unknown",
        "absent"
      ],
      "title": "After",
      "type": "string"
    },
    "before": {
      "enum": [
        "passed",
        "failed",
        "broken",
        "skipped",
        "unknown",
        "absent"
      ],
      "title": "Before",
      "type": "string"
    },
    "count": {
      "minimum": 1.0,
      "title": "Count",
      "type": "integer"
    }
  },
  "required": [
    "before",
    "after",
    "count"
  ],
  "title": "RunCompareTransition",
  "type": "object"
}
```

## RunContext

```json
{
  "description": "``run_context`` on ``IngestPayload`` / ``LiveSessionCreate`` / ``LiveStreamMeta`` and the\n``/ingest/file`` form (W23b FAIL-13). An unknown future SECTION is dropped, never stored;\ninside a section the rules are strict. Keys are compared EXACTLY (``ENV_ALLOWLIST`` is\ncase-sensitive, as environments are on Linux). ``model_dump(exclude_none=True)`` is the\nstored form (env values already redacted).",
  "properties": {
    "dependency_fingerprints": {
      "anyOf": [
        {
          "additionalProperties": {
            "type": "string"
          },
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Dependency Fingerprints"
    },
    "env": {
      "anyOf": [
        {
          "additionalProperties": {
            "type": "string"
          },
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Env"
    },
    "image_digests": {
      "anyOf": [
        {
          "additionalProperties": {
            "type": "string"
          },
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Image Digests"
    },
    "runtime": {
      "anyOf": [
        {
          "additionalProperties": {
            "type": "string"
          },
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Runtime"
    },
    "v": {
      "default": 1,
      "title": "V",
      "type": "integer"
    }
  },
  "title": "RunContext",
  "type": "object"
}
```

## RunSignatureBatchResponse

```json
{
  "properties": {
    "items": {
      "items": {
        "$ref": "#/components/schemas/RunSignatureItem"
      },
      "title": "Items",
      "type": "array"
    },
    "max_ids": {
      "title": "Max Ids",
      "type": "integer"
    },
    "rows_per_run_cap": {
      "title": "Rows Per Run Cap",
      "type": "integer"
    },
    "unavailable": {
      "items": {
        "type": "string"
      },
      "title": "Unavailable",
      "type": "array"
    }
  },
  "required": [
    "items",
    "unavailable",
    "max_ids",
    "rows_per_run_cap"
  ],
  "title": "RunSignatureBatchResponse",
  "type": "object"
}
```

## RunSignatureEntry

```json
{
  "properties": {
    "count": {
      "title": "Count",
      "type": "integer"
    },
    "label": {
      "title": "Label",
      "type": "string"
    },
    "signature": {
      "title": "Signature",
      "type": "string"
    },
    "test_count": {
      "title": "Test Count",
      "type": "integer"
    },
    "tests": {
      "items": {
        "type": "string"
      },
      "title": "Tests",
      "type": "array"
    }
  },
  "required": [
    "signature",
    "label",
    "count",
    "test_count",
    "tests"
  ],
  "title": "RunSignatureEntry",
  "type": "object"
}
```

## RunSignatureItem

```json
{
  "properties": {
    "capped": {
      "title": "Capped",
      "type": "boolean"
    },
    "dominant": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Dominant"
    },
    "failing_tests": {
      "title": "Failing Tests",
      "type": "integer"
    },
    "read": {
      "title": "Read",
      "type": "integer"
    },
    "run_id": {
      "title": "Run Id",
      "type": "string"
    },
    "signatures": {
      "items": {
        "$ref": "#/components/schemas/RunSignatureEntry"
      },
      "title": "Signatures",
      "type": "array"
    }
  },
  "required": [
    "run_id",
    "failing_tests",
    "read",
    "capped",
    "signatures"
  ],
  "title": "RunSignatureItem",
  "type": "object"
}
```

## RunTimeline

```json
{
  "description": "Tests with a start and a finish, for the waterfall. When more than\n``limit`` qualify, the ``limit`` LONGEST are kept (``truncated``).",
  "properties": {
    "end": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "End"
    },
    "items": {
      "items": {
        "$ref": "#/components/schemas/RunTimelineItem"
      },
      "title": "Items",
      "type": "array"
    },
    "limit": {
      "title": "Limit",
      "type": "integer"
    },
    "measured": {
      "default": false,
      "title": "Measured",
      "type": "boolean"
    },
    "origin": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Origin"
    },
    "reason": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Reason"
    },
    "total": {
      "default": 0,
      "title": "Total",
      "type": "integer"
    },
    "truncated": {
      "default": false,
      "title": "Truncated",
      "type": "boolean"
    }
  },
  "required": [
    "limit"
  ],
  "title": "RunTimeline",
  "type": "object"
}
```

## RunTimelineItem

```json
{
  "properties": {
    "duration_ms": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Duration Ms"
    },
    "finished_at": {
      "format": "date-time",
      "title": "Finished At",
      "type": "string"
    },
    "offset_ms": {
      "title": "Offset Ms",
      "type": "integer"
    },
    "started_at": {
      "format": "date-time",
      "title": "Started At",
      "type": "string"
    },
    "status": {
      "title": "Status",
      "type": "string"
    },
    "suite_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Suite Name"
    },
    "test_id": {
      "format": "uuid",
      "title": "Test Id",
      "type": "string"
    },
    "test_name": {
      "title": "Test Name",
      "type": "string"
    },
    "worker_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Worker Id"
    }
  },
  "required": [
    "test_id",
    "test_name",
    "status",
    "started_at",
    "finished_at",
    "offset_ms"
  ],
  "title": "RunTimelineItem",
  "type": "object"
}
```

## RunTimingFigures

```json
{
  "description": "The run's own figures (EXEC-02 columns on ``test_runs``).",
  "properties": {
    "duration_count": {
      "default": 0,
      "title": "Duration Count",
      "type": "integer"
    },
    "exec_finished_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Exec Finished At"
    },
    "exec_started_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Exec Started At"
    },
    "parallelism": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Parallelism"
    },
    "summed_ms": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Summed Ms"
    },
    "test_count": {
      "default": 0,
      "title": "Test Count",
      "type": "integer"
    },
    "timed_count": {
      "default": 0,
      "title": "Timed Count",
      "type": "integer"
    },
    "timing_source": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Timing Source"
    },
    "wall_clock_ms": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Wall Clock Ms"
    }
  },
  "title": "RunTimingFigures",
  "type": "object"
}
```

## RunTimingLane

```json
{
  "description": "One worker lane: the timed tests one worker ran.",
  "properties": {
    "busy_ms": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Busy Ms"
    },
    "finished_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Finished At"
    },
    "span_ms": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Span Ms"
    },
    "started_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Started At"
    },
    "test_count": {
      "default": 0,
      "title": "Test Count",
      "type": "integer"
    },
    "worker_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Worker Id"
    }
  },
  "title": "RunTimingLane",
  "type": "object"
}
```

## RunTimingPhases

```json
{
  "description": "Setup / test body / teardown totals over the run's tests.",
  "properties": {
    "call_ms": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Call Ms"
    },
    "measured": {
      "default": false,
      "title": "Measured",
      "type": "boolean"
    },
    "reason": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Reason"
    },
    "setup_ms": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Setup Ms"
    },
    "setup_share": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Setup Share"
    },
    "teardown_ms": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Teardown Ms"
    },
    "teardown_share": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Teardown Share"
    },
    "tests_with_phases": {
      "default": 0,
      "title": "Tests With Phases",
      "type": "integer"
    }
  },
  "title": "RunTimingPhases",
  "type": "object"
}
```

## RunTimingResponse

```json
{
  "description": "GET /api/v1/runs/{run_id}/timing.",
  "properties": {
    "branch": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Branch"
    },
    "build_number": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Build Number"
    },
    "created_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Created At"
    },
    "duration_findings": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/DurationRegressionsResponse"
        },
        {
          "type": "null"
        }
      ]
    },
    "phases": {
      "$ref": "#/components/schemas/RunTimingPhases"
    },
    "project_id": {
      "format": "uuid",
      "title": "Project Id",
      "type": "string"
    },
    "run": {
      "$ref": "#/components/schemas/RunTimingFigures"
    },
    "run_id": {
      "format": "uuid",
      "title": "Run Id",
      "type": "string"
    },
    "slowest": {
      "$ref": "#/components/schemas/RunTimingSlowest"
    },
    "status": {
      "title": "Status",
      "type": "string"
    },
    "suites": {
      "$ref": "#/components/schemas/RunTimingSuites"
    },
    "timeline": {
      "$ref": "#/components/schemas/RunTimeline"
    },
    "workers": {
      "$ref": "#/components/schemas/RunTimingWorkers"
    }
  },
  "required": [
    "run_id",
    "project_id",
    "status",
    "run",
    "phases",
    "suites",
    "slowest",
    "workers",
    "timeline"
  ],
  "title": "RunTimingResponse",
  "type": "object"
}
```

## RunTimingSlowest

```json
{
  "properties": {
    "items": {
      "items": {
        "$ref": "#/components/schemas/RunTimingTestResponse"
      },
      "title": "Items",
      "type": "array"
    },
    "limit": {
      "title": "Limit",
      "type": "integer"
    },
    "measured": {
      "default": false,
      "title": "Measured",
      "type": "boolean"
    },
    "reason": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Reason"
    },
    "total": {
      "default": 0,
      "title": "Total",
      "type": "integer"
    }
  },
  "required": [
    "limit"
  ],
  "title": "RunTimingSlowest",
  "type": "object"
}
```

## RunTimingSuites

```json
{
  "properties": {
    "items": {
      "items": {
        "$ref": "#/components/schemas/SuiteExecutionResponse"
      },
      "title": "Items",
      "type": "array"
    },
    "measured": {
      "default": false,
      "title": "Measured",
      "type": "boolean"
    },
    "reason": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Reason"
    },
    "total": {
      "default": 0,
      "title": "Total",
      "type": "integer"
    },
    "truncated": {
      "default": false,
      "title": "Truncated",
      "type": "boolean"
    }
  },
  "title": "RunTimingSuites",
  "type": "object"
}
```

## RunTimingTestResponse

```json
{
  "description": "One test of the run, as the slowest list shows it.",
  "properties": {
    "baseline": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/TestDurationBaselineResponse"
        },
        {
          "type": "null"
        }
      ]
    },
    "baseline_ratio": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Baseline Ratio"
    },
    "call_ms": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Call Ms"
    },
    "duration_ms": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Duration Ms"
    },
    "finished_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Finished At"
    },
    "setup_ms": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Setup Ms"
    },
    "started_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Started At"
    },
    "status": {
      "title": "Status",
      "type": "string"
    },
    "suite_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Suite Name"
    },
    "teardown_ms": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Teardown Ms"
    },
    "test_fingerprint": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Test Fingerprint"
    },
    "test_id": {
      "format": "uuid",
      "title": "Test Id",
      "type": "string"
    },
    "test_name": {
      "title": "Test Name",
      "type": "string"
    },
    "timing_source": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Timing Source"
    },
    "worker_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Worker Id"
    }
  },
  "required": [
    "test_id",
    "test_name",
    "status"
  ],
  "title": "RunTimingTestResponse",
  "type": "object"
}
```

## RunTimingWorkers

```json
{
  "properties": {
    "lanes": {
      "items": {
        "$ref": "#/components/schemas/RunTimingLane"
      },
      "title": "Lanes",
      "type": "array"
    },
    "measured": {
      "default": false,
      "title": "Measured",
      "type": "boolean"
    },
    "reason": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Reason"
    },
    "total": {
      "default": 0,
      "title": "Total",
      "type": "integer"
    },
    "truncated": {
      "default": false,
      "title": "Truncated",
      "type": "boolean"
    }
  },
  "title": "RunTimingWorkers",
  "type": "object"
}
```

## RunVerdictBatchResponse

```json
{
  "properties": {
    "items": {
      "items": {
        "$ref": "#/components/schemas/RunVerdictItem"
      },
      "title": "Items",
      "type": "array"
    },
    "max_ids": {
      "title": "Max Ids",
      "type": "integer"
    },
    "unavailable": {
      "items": {
        "type": "string"
      },
      "title": "Unavailable",
      "type": "array"
    }
  },
  "required": [
    "items",
    "unavailable",
    "max_ids"
  ],
  "title": "RunVerdictBatchResponse",
  "type": "object"
}
```

## RunVerdictItem

```json
{
  "description": "One run of ``GET /runs/ai-verdicts``: what its AI-verdict pill is computed from.",
  "properties": {
    "analysed": {
      "title": "Analysed",
      "type": "boolean"
    },
    "generated_at": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Generated At"
    },
    "review": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Review"
    },
    "run_id": {
      "title": "Run Id",
      "type": "string"
    },
    "snapshot": {
      "title": "Snapshot",
      "type": "boolean"
    },
    "stale": {
      "anyOf": [
        {
          "type": "boolean"
        },
        {
          "type": "null"
        }
      ],
      "title": "Stale"
    },
    "verdict_inputs": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Verdict Inputs"
    }
  },
  "required": [
    "run_id",
    "snapshot",
    "analysed"
  ],
  "title": "RunVerdictItem",
  "type": "object"
}
```

## SCIMEmail

```json
{
  "properties": {
    "primary": {
      "default": false,
      "title": "Primary",
      "type": "boolean"
    },
    "type": {
      "anyOf": [
        {
          "maxLength": 100,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": "work",
      "title": "Type"
    },
    "value": {
      "maxLength": 255,
      "minLength": 1,
      "title": "Value",
      "type": "string"
    }
  },
  "required": [
    "value"
  ],
  "title": "SCIMEmail",
  "type": "object"
}
```

## SCIMGroup

```json
{
  "properties": {
    "display": {
      "anyOf": [
        {
          "maxLength": 500,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Display"
    },
    "value": {
      "maxLength": 1000,
      "minLength": 1,
      "title": "Value",
      "type": "string"
    }
  },
  "required": [
    "value"
  ],
  "title": "SCIMGroup",
  "type": "object"
}
```

## SCIMName

```json
{
  "properties": {
    "familyName": {
      "anyOf": [
        {
          "maxLength": 255,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Familyname"
    },
    "formatted": {
      "anyOf": [
        {
          "maxLength": 255,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Formatted"
    },
    "givenName": {
      "anyOf": [
        {
          "maxLength": 255,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Givenname"
    }
  },
  "title": "SCIMName",
  "type": "object"
}
```

## SCIMPatchOp

```json
{
  "properties": {
    "op": {
      "title": "Op",
      "type": "string"
    },
    "path": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Path"
    },
    "value": {
      "anyOf": [
        {},
        {
          "type": "null"
        }
      ],
      "title": "Value"
    }
  },
  "required": [
    "op"
  ],
  "title": "SCIMPatchOp",
  "type": "object"
}
```

## SCIMPatchRequestPayload

```json
{
  "description": "Inbound PATCH payload; the protocol schemas member is required.",
  "properties": {
    "Operations": {
      "items": {
        "$ref": "#/components/schemas/SCIMPatchOp"
      },
      "maxItems": 100,
      "minItems": 1,
      "title": "Operations",
      "type": "array"
    },
    "schemas": {
      "items": {
        "type": "string"
      },
      "title": "Schemas",
      "type": "array"
    }
  },
  "required": [
    "schemas",
    "Operations"
  ],
  "title": "SCIMPatchRequestPayload",
  "type": "object"
}
```

## SCIMTokenCreate

```json
{
  "description": "Create a new SCIM bearer token.",
  "properties": {
    "expires_days": {
      "anyOf": [
        {
          "maximum": 365.0,
          "minimum": 1.0,
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Expires Days"
    },
    "name": {
      "maxLength": 255,
      "minLength": 2,
      "title": "Name",
      "type": "string"
    },
    "sso_config_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Sso Config Id"
    }
  },
  "required": [
    "name"
  ],
  "title": "SCIMTokenCreate",
  "type": "object"
}
```

## SCIMTokenCreatedResponse

```json
{
  "description": "Returned once at creation — includes the plaintext token.",
  "properties": {
    "created_at": {
      "format": "date-time",
      "title": "Created At",
      "type": "string"
    },
    "expires_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Expires At"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "is_active": {
      "title": "Is Active",
      "type": "boolean"
    },
    "last_used_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Last Used At"
    },
    "name": {
      "title": "Name",
      "type": "string"
    },
    "raw_token": {
      "title": "Raw Token",
      "type": "string"
    },
    "sso_config_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Sso Config Id"
    },
    "token_hint": {
      "title": "Token Hint",
      "type": "string"
    }
  },
  "required": [
    "id",
    "name",
    "token_hint",
    "is_active",
    "created_at",
    "raw_token"
  ],
  "title": "SCIMTokenCreatedResponse",
  "type": "object"
}
```

## SCIMTokenResponse

```json
{
  "properties": {
    "created_at": {
      "format": "date-time",
      "title": "Created At",
      "type": "string"
    },
    "expires_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Expires At"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "is_active": {
      "title": "Is Active",
      "type": "boolean"
    },
    "last_used_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Last Used At"
    },
    "name": {
      "title": "Name",
      "type": "string"
    },
    "sso_config_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Sso Config Id"
    },
    "token_hint": {
      "title": "Token Hint",
      "type": "string"
    }
  },
  "required": [
    "id",
    "name",
    "token_hint",
    "is_active",
    "created_at"
  ],
  "title": "SCIMTokenResponse",
  "type": "object"
}
```

## SCIMUserRequest

```json
{
  "description": "Inbound SCIM user payload; the protocol schemas member is required.",
  "properties": {
    "active": {
      "default": true,
      "title": "Active",
      "type": "boolean"
    },
    "displayName": {
      "anyOf": [
        {
          "maxLength": 255,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Displayname"
    },
    "emails": {
      "default": [],
      "items": {
        "$ref": "#/components/schemas/SCIMEmail"
      },
      "maxItems": 100,
      "title": "Emails",
      "type": "array"
    },
    "externalId": {
      "anyOf": [
        {
          "maxLength": 1000,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Externalid"
    },
    "groups": {
      "default": [],
      "items": {
        "$ref": "#/components/schemas/SCIMGroup"
      },
      "maxItems": 1000,
      "title": "Groups",
      "type": "array"
    },
    "id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Id"
    },
    "meta": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Meta"
    },
    "name": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/SCIMName"
        },
        {
          "type": "null"
        }
      ]
    },
    "schemas": {
      "items": {
        "type": "string"
      },
      "title": "Schemas",
      "type": "array"
    },
    "userName": {
      "maxLength": 100,
      "minLength": 1,
      "title": "Username",
      "type": "string"
    }
  },
  "required": [
    "schemas",
    "userName"
  ],
  "title": "SCIMUserRequest",
  "type": "object"
}
```

## SSOConfigCreate

```json
{
  "description": "Create a new SSO configuration.\n\nW20 ADM-08b: a SAML config needs the IdP entity id / SSO URL / certificate and\nthe SP entity id / ACS URL; an OIDC config needs the issuer, client id and client\nsecret (write-only — stored in ``secret_refs``, never returned).",
  "properties": {
    "audience": {
      "anyOf": [
        {
          "maxLength": 1000,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Audience"
    },
    "default_role": {
      "$ref": "#/components/schemas/UserRole",
      "default": "VIEWER"
    },
    "display_name": {
      "maxLength": 255,
      "minLength": 2,
      "title": "Display Name",
      "type": "string"
    },
    "enforcement_mode": {
      "$ref": "#/components/schemas/SSOEnforcementMode",
      "default": "OPTIONAL"
    },
    "group_attribute": {
      "anyOf": [
        {
          "maxLength": 255,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Group Attribute"
    },
    "idp_certificate": {
      "anyOf": [
        {
          "minLength": 1,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Idp Certificate"
    },
    "idp_entity_id": {
      "anyOf": [
        {
          "maxLength": 1000,
          "minLength": 1,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Idp Entity Id"
    },
    "idp_slo_url": {
      "anyOf": [
        {
          "maxLength": 2000,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Idp Slo Url"
    },
    "idp_sso_url": {
      "anyOf": [
        {
          "maxLength": 2000,
          "minLength": 1,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Idp Sso Url"
    },
    "oidc_client_id": {
      "anyOf": [
        {
          "maxLength": 255,
          "minLength": 1,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Oidc Client Id"
    },
    "oidc_client_secret": {
      "anyOf": [
        {
          "maxLength": 4000,
          "minLength": 1,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Oidc Client Secret"
    },
    "oidc_groups_claim": {
      "anyOf": [
        {
          "maxLength": 255,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Oidc Groups Claim"
    },
    "oidc_issuer": {
      "anyOf": [
        {
          "maxLength": 1000,
          "minLength": 1,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Oidc Issuer"
    },
    "oidc_scopes": {
      "anyOf": [
        {
          "maxLength": 500,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Oidc Scopes"
    },
    "provider_type": {
      "$ref": "#/components/schemas/SSOProviderType",
      "default": "SAML"
    },
    "role_mapping": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Role Mapping"
    },
    "sp_acs_url": {
      "anyOf": [
        {
          "maxLength": 2000,
          "minLength": 1,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Sp Acs Url"
    },
    "sp_entity_id": {
      "anyOf": [
        {
          "maxLength": 1000,
          "minLength": 1,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Sp Entity Id"
    }
  },
  "required": [
    "display_name"
  ],
  "title": "SSOConfigCreate",
  "type": "object"
}
```

## SSOConfigResponse

```json
{
  "description": "SSO configuration response (certificate is masked).",
  "properties": {
    "audience": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Audience"
    },
    "created_at": {
      "format": "date-time",
      "title": "Created At",
      "type": "string"
    },
    "default_role": {
      "$ref": "#/components/schemas/UserRole"
    },
    "display_name": {
      "title": "Display Name",
      "type": "string"
    },
    "enforcement_mode": {
      "$ref": "#/components/schemas/SSOEnforcementMode"
    },
    "group_attribute": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Group Attribute"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "idp_certificate_fingerprint": {
      "default": "",
      "title": "Idp Certificate Fingerprint",
      "type": "string"
    },
    "idp_entity_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Idp Entity Id"
    },
    "idp_slo_url": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Idp Slo Url"
    },
    "idp_sso_url": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Idp Sso Url"
    },
    "is_active": {
      "title": "Is Active",
      "type": "boolean"
    },
    "last_test_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Last Test At"
    },
    "last_test_error": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Last Test Error"
    },
    "last_test_success": {
      "anyOf": [
        {
          "type": "boolean"
        },
        {
          "type": "null"
        }
      ],
      "title": "Last Test Success"
    },
    "oidc_client_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Oidc Client Id"
    },
    "oidc_client_secret_set": {
      "default": false,
      "title": "Oidc Client Secret Set",
      "type": "boolean"
    },
    "oidc_groups_claim": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Oidc Groups Claim"
    },
    "oidc_issuer": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Oidc Issuer"
    },
    "oidc_redirect_uri": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Oidc Redirect Uri"
    },
    "oidc_scopes": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Oidc Scopes"
    },
    "provider_type": {
      "$ref": "#/components/schemas/SSOProviderType"
    },
    "role_mapping": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Role Mapping"
    },
    "sp_acs_url": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Sp Acs Url"
    },
    "sp_entity_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Sp Entity Id"
    },
    "updated_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Updated At"
    }
  },
  "required": [
    "id",
    "display_name",
    "provider_type",
    "default_role",
    "enforcement_mode",
    "is_active",
    "created_at"
  ],
  "title": "SSOConfigResponse",
  "type": "object"
}
```

## SSOConfigUpdate

```json
{
  "description": "Partial update of SSO configuration.",
  "properties": {
    "audience": {
      "anyOf": [
        {
          "maxLength": 1000,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Audience"
    },
    "default_role": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/UserRole"
        },
        {
          "type": "null"
        }
      ]
    },
    "display_name": {
      "anyOf": [
        {
          "maxLength": 255,
          "minLength": 2,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Display Name"
    },
    "enforcement_mode": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/SSOEnforcementMode"
        },
        {
          "type": "null"
        }
      ]
    },
    "group_attribute": {
      "anyOf": [
        {
          "maxLength": 255,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Group Attribute"
    },
    "idp_certificate": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Idp Certificate"
    },
    "idp_entity_id": {
      "anyOf": [
        {
          "maxLength": 1000,
          "minLength": 1,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Idp Entity Id"
    },
    "idp_slo_url": {
      "anyOf": [
        {
          "maxLength": 2000,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Idp Slo Url"
    },
    "idp_sso_url": {
      "anyOf": [
        {
          "maxLength": 2000,
          "minLength": 1,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Idp Sso Url"
    },
    "is_active": {
      "anyOf": [
        {
          "type": "boolean"
        },
        {
          "type": "null"
        }
      ],
      "title": "Is Active"
    },
    "oidc_client_id": {
      "anyOf": [
        {
          "maxLength": 255,
          "minLength": 1,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Oidc Client Id"
    },
    "oidc_client_secret": {
      "anyOf": [
        {
          "maxLength": 4000,
          "minLength": 1,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Oidc Client Secret"
    },
    "oidc_groups_claim": {
      "anyOf": [
        {
          "maxLength": 255,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Oidc Groups Claim"
    },
    "oidc_issuer": {
      "anyOf": [
        {
          "maxLength": 1000,
          "minLength": 1,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Oidc Issuer"
    },
    "oidc_scopes": {
      "anyOf": [
        {
          "maxLength": 500,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Oidc Scopes"
    },
    "role_mapping": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Role Mapping"
    },
    "sp_acs_url": {
      "anyOf": [
        {
          "maxLength": 2000,
          "minLength": 1,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Sp Acs Url"
    },
    "sp_entity_id": {
      "anyOf": [
        {
          "maxLength": 1000,
          "minLength": 1,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Sp Entity Id"
    }
  },
  "title": "SSOConfigUpdate",
  "type": "object"
}
```

## SSOEnforcementMode

```json
{
  "enum": [
    "OPTIONAL",
    "SSO_REQUIRED"
  ],
  "title": "SSOEnforcementMode",
  "type": "string"
}
```

## SSOExchangeRequest

```json
{
  "description": "The one-time code the ACS hands the browser after an SSO sign-in.",
  "properties": {
    "code": {
      "maxLength": 128,
      "minLength": 1,
      "title": "Code",
      "type": "string"
    }
  },
  "required": [
    "code"
  ],
  "title": "SSOExchangeRequest",
  "type": "object"
}
```

## SSOLoginResponse

```json
{
  "description": "Token response after successful SSO authentication.",
  "properties": {
    "access_token": {
      "title": "Access Token",
      "type": "string"
    },
    "expires_in": {
      "title": "Expires In",
      "type": "integer"
    },
    "is_new_user": {
      "default": false,
      "title": "Is New User",
      "type": "boolean"
    },
    "refresh_token": {
      "title": "Refresh Token",
      "type": "string"
    },
    "token_type": {
      "default": "bearer",
      "title": "Token Type",
      "type": "string"
    },
    "user": {
      "$ref": "#/components/schemas/UserResponse"
    }
  },
  "required": [
    "access_token",
    "refresh_token",
    "expires_in",
    "user"
  ],
  "title": "SSOLoginResponse",
  "type": "object"
}
```

## SSOProviderType

```json
{
  "enum": [
    "SAML",
    "OIDC"
  ],
  "title": "SSOProviderType",
  "type": "string"
}
```

## SSORolePreviewRequest

```json
{
  "description": "W20 ADM-08a: preview the role a user in ``groups`` gets. ``role_mapping`` / ``default_role`` preview an\nunsaved edit; omitted, the stored configuration answers.",
  "properties": {
    "default_role": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Default Role"
    },
    "groups": {
      "items": {
        "type": "string"
      },
      "maxItems": 200,
      "title": "Groups",
      "type": "array"
    },
    "role_mapping": {
      "anyOf": [
        {
          "additionalProperties": {
            "type": "string"
          },
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Role Mapping"
    }
  },
  "title": "SSORolePreviewRequest",
  "type": "object"
}
```

## SSORolePreviewResponse

```json
{
  "properties": {
    "ceiling": {
      "title": "Ceiling",
      "type": "string"
    },
    "clamped": {
      "title": "Clamped",
      "type": "boolean"
    },
    "matched_groups": {
      "items": {
        "type": "string"
      },
      "title": "Matched Groups",
      "type": "array"
    },
    "role": {
      "title": "Role",
      "type": "string"
    },
    "source": {
      "enum": [
        "mapping",
        "default"
      ],
      "title": "Source",
      "type": "string"
    }
  },
  "required": [
    "role",
    "matched_groups",
    "source",
    "ceiling",
    "clamped"
  ],
  "title": "SSORolePreviewResponse",
  "type": "object"
}
```

## SSOTestConnectionResponse

```json
{
  "description": "Result of testing SSO configuration.",
  "properties": {
    "certificate_expires_at": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Certificate Expires At"
    },
    "certificate_valid": {
      "anyOf": [
        {
          "type": "boolean"
        },
        {
          "type": "null"
        }
      ],
      "title": "Certificate Valid"
    },
    "idp_entity_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Idp Entity Id"
    },
    "message": {
      "title": "Message",
      "type": "string"
    },
    "success": {
      "title": "Success",
      "type": "boolean"
    }
  },
  "required": [
    "success",
    "message"
  ],
  "title": "SSOTestConnectionResponse",
  "type": "object"
}
```

## SavedViewCreate

```json
{
  "properties": {
    "description": {
      "anyOf": [
        {
          "maxLength": 50000,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Description"
    },
    "filters": {
      "additionalProperties": true,
      "title": "Filters",
      "type": "object"
    },
    "is_default": {
      "default": false,
      "title": "Is Default",
      "type": "boolean"
    },
    "is_shared": {
      "default": false,
      "title": "Is Shared",
      "type": "boolean"
    },
    "name": {
      "maxLength": 255,
      "minLength": 2,
      "title": "Name",
      "type": "string"
    },
    "page": {
      "anyOf": [
        {
          "enum": [
            "dashboard",
            "trends",
            "coverage",
            "defects",
            "failures",
            "summary_report",
            "explore",
            "search",
            "test_management"
          ],
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Page"
    },
    "project_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Project Id"
    }
  },
  "required": [
    "name"
  ],
  "title": "SavedViewCreate",
  "type": "object"
}
```

## SavedViewRelease

```json
{
  "description": "Whether THIS reader may apply the release stored in a view (S5-3f-ii).\n\nA saved view outlives the state it was saved in, and a shared view is read\nby people who did not save it — so the stored id is an id somebody else\nsupplied. The verdict is reported rather than the view being refused:\nlosing one filter is recoverable, refusing to open a view because one field\nwent stale is not. ``reason`` is what lets the UI say the result set is\nwider than the view's author intended.",
  "properties": {
    "applied": {
      "default": false,
      "title": "Applied",
      "type": "boolean"
    },
    "reason": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Reason"
    },
    "release_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Release Id"
    }
  },
  "title": "SavedViewRelease",
  "type": "object"
}
```

## SavedViewResponse

```json
{
  "properties": {
    "created_at": {
      "format": "date-time",
      "title": "Created At",
      "type": "string"
    },
    "description": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Description"
    },
    "filters": {
      "additionalProperties": true,
      "title": "Filters",
      "type": "object"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "is_default": {
      "title": "Is Default",
      "type": "boolean"
    },
    "is_shared": {
      "title": "Is Shared",
      "type": "boolean"
    },
    "name": {
      "title": "Name",
      "type": "string"
    },
    "page": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Page"
    },
    "project_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Project Id"
    },
    "release": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/SavedViewRelease"
        },
        {
          "type": "null"
        }
      ]
    },
    "updated_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Updated At"
    },
    "user_id": {
      "format": "uuid",
      "title": "User Id",
      "type": "string"
    }
  },
  "required": [
    "id",
    "user_id",
    "name",
    "filters",
    "is_shared",
    "is_default",
    "created_at"
  ],
  "title": "SavedViewResponse",
  "type": "object"
}
```

## SavedViewUpdate

```json
{
  "properties": {
    "description": {
      "anyOf": [
        {
          "maxLength": 50000,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Description"
    },
    "filters": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Filters"
    },
    "is_default": {
      "anyOf": [
        {
          "type": "boolean"
        },
        {
          "type": "null"
        }
      ],
      "title": "Is Default"
    },
    "is_shared": {
      "anyOf": [
        {
          "type": "boolean"
        },
        {
          "type": "null"
        }
      ],
      "title": "Is Shared"
    },
    "name": {
      "anyOf": [
        {
          "maxLength": 255,
          "minLength": 2,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Name"
    },
    "page": {
      "anyOf": [
        {
          "enum": [
            "dashboard",
            "trends",
            "coverage",
            "defects",
            "failures",
            "summary_report",
            "explore",
            "search",
            "test_management"
          ],
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Page"
    }
  },
  "title": "SavedViewUpdate",
  "type": "object"
}
```

## SearchSuggestion

```json
{
  "properties": {
    "detail": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Detail"
    },
    "kind": {
      "title": "Kind",
      "type": "string"
    },
    "label": {
      "title": "Label",
      "type": "string"
    },
    "q": {
      "title": "Q",
      "type": "string"
    },
    "scope": {
      "title": "Scope",
      "type": "string"
    }
  },
  "required": [
    "kind",
    "label",
    "q",
    "scope"
  ],
  "title": "SearchSuggestion",
  "type": "object"
}
```

## SearchSuggestionsResponse

```json
{
  "properties": {
    "active_release": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/SearchSuggestion"
        },
        {
          "type": "null"
        }
      ]
    },
    "failing_tests": {
      "items": {
        "$ref": "#/components/schemas/SearchSuggestion"
      },
      "title": "Failing Tests",
      "type": "array"
    },
    "open_defects": {
      "items": {
        "$ref": "#/components/schemas/SearchSuggestion"
      },
      "title": "Open Defects",
      "type": "array"
    },
    "project_id": {
      "format": "uuid",
      "title": "Project Id",
      "type": "string"
    },
    "window_days": {
      "title": "Window Days",
      "type": "integer"
    }
  },
  "required": [
    "project_id",
    "window_days",
    "failing_tests",
    "open_defects"
  ],
  "title": "SearchSuggestionsResponse",
  "type": "object"
}
```

## SearchUsageRead

```json
{
  "properties": {
    "days": {
      "title": "Days",
      "type": "integer"
    },
    "fallback_searches": {
      "default": 0,
      "title": "Fallback Searches",
      "type": "integer"
    },
    "measured": {
      "title": "Measured",
      "type": "boolean"
    },
    "p50_latency_ms": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "P50 Latency Ms"
    },
    "p95_latency_ms": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "P95 Latency Ms"
    },
    "project_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Project Id"
    },
    "retention_days": {
      "title": "Retention Days",
      "type": "integer"
    },
    "searches": {
      "title": "Searches",
      "type": "integer"
    },
    "since": {
      "format": "date-time",
      "title": "Since",
      "type": "string"
    },
    "zero_result_rate": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Zero Result Rate"
    },
    "zero_result_searches": {
      "title": "Zero Result Searches",
      "type": "integer"
    }
  },
  "required": [
    "days",
    "since",
    "measured",
    "searches",
    "zero_result_searches",
    "retention_days"
  ],
  "title": "SearchUsageRead",
  "type": "object"
}
```

## SelfUpdateProfileRequest

```json
{
  "description": "Fields a user can update about themselves (no role/status changes).",
  "properties": {
    "avatar_color": {
      "anyOf": [
        {
          "maxLength": 20,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Avatar Color"
    },
    "full_name": {
      "anyOf": [
        {
          "maxLength": 255,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Full Name"
    }
  },
  "title": "SelfUpdateProfileRequest",
  "type": "object"
}
```

## SendMessageRequest

```json
{
  "properties": {
    "message": {
      "maxLength": 4000,
      "minLength": 1,
      "title": "Message",
      "type": "string"
    },
    "project_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Project Id"
    }
  },
  "required": [
    "message"
  ],
  "title": "SendMessageRequest",
  "type": "object"
}
```

## SendMessageResponse

```json
{
  "properties": {
    "reply": {
      "title": "Reply",
      "type": "string"
    },
    "session_id": {
      "format": "uuid",
      "title": "Session Id",
      "type": "string"
    },
    "sources": {
      "default": [],
      "items": {},
      "title": "Sources",
      "type": "array"
    },
    "suggested_actions": {
      "default": [],
      "items": {},
      "title": "Suggested Actions",
      "type": "array"
    },
    "tool_trace": {
      "default": [],
      "items": {},
      "title": "Tool Trace",
      "type": "array"
    }
  },
  "required": [
    "session_id",
    "reply"
  ],
  "title": "SendMessageResponse",
  "type": "object"
}
```

## SetBaselineRequest

```json
{
  "properties": {
    "agent_name": {
      "default": "AnalysisAgent",
      "title": "Agent Name",
      "type": "string"
    },
    "dataset_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Dataset Id"
    },
    "eval_run_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Eval Run Id"
    },
    "max_regression_pct": {
      "default": 5.0,
      "title": "Max Regression Pct",
      "type": "number"
    },
    "min_accuracy": {
      "default": 0.8,
      "title": "Min Accuracy",
      "type": "number"
    },
    "min_f1": {
      "default": 0.75,
      "title": "Min F1",
      "type": "number"
    },
    "model_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Model Name"
    },
    "prompt_version": {
      "default": "v1",
      "title": "Prompt Version",
      "type": "string"
    },
    "task_type": {
      "default": "classification",
      "title": "Task Type",
      "type": "string"
    }
  },
  "title": "SetBaselineRequest",
  "type": "object"
}
```

## ShadowConfig

```json
{
  "additionalProperties": false,
  "properties": {
    "daily_token_budget": {
      "default": 200000,
      "minimum": 0.0,
      "title": "Daily Token Budget",
      "type": "integer"
    },
    "sample_rate": {
      "default": 0.1,
      "maximum": 1.0,
      "minimum": 0.0,
      "title": "Sample Rate",
      "type": "number"
    }
  },
  "title": "ShadowConfig",
  "type": "object"
}
```

## ShadowPairLabelRequest

```json
{
  "additionalProperties": false,
  "properties": {
    "failure_category": {
      "anyOf": [
        {
          "maxLength": 40,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Failure Category"
    },
    "notes": {
      "anyOf": [
        {
          "maxLength": 500,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Notes"
    },
    "verdict": {
      "maxLength": 20,
      "minLength": 3,
      "title": "Verdict",
      "type": "string"
    }
  },
  "required": [
    "verdict"
  ],
  "title": "ShadowPairLabelRequest",
  "type": "object"
}
```

## ShadowPairListResponse

```json
{
  "properties": {
    "items": {
      "items": {
        "$ref": "#/components/schemas/ShadowPairRead"
      },
      "title": "Items",
      "type": "array"
    },
    "live_tier_pairs_seen": {
      "title": "Live Tier Pairs Seen",
      "type": "boolean"
    },
    "pending_total": {
      "title": "Pending Total",
      "type": "integer"
    },
    "total": {
      "title": "Total",
      "type": "integer"
    }
  },
  "required": [
    "items",
    "total",
    "pending_total",
    "live_tier_pairs_seen"
  ],
  "title": "ShadowPairListResponse",
  "type": "object"
}
```

## ShadowPairRead

```json
{
  "properties": {
    "agent_id": {
      "title": "Agent Id",
      "type": "string"
    },
    "agent_label": {
      "title": "Agent Label",
      "type": "string"
    },
    "candidate_output": {
      "additionalProperties": true,
      "title": "Candidate Output",
      "type": "object"
    },
    "candidate_tier": {
      "title": "Candidate Tier",
      "type": "string"
    },
    "candidate_tokens": {
      "title": "Candidate Tokens",
      "type": "integer"
    },
    "created_at": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Created At"
    },
    "expires_at": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Expires At"
    },
    "id": {
      "title": "Id",
      "type": "string"
    },
    "incumbent_output": {
      "additionalProperties": true,
      "title": "Incumbent Output",
      "type": "object"
    },
    "incumbent_tier": {
      "title": "Incumbent Tier",
      "type": "string"
    },
    "incumbent_tokens": {
      "title": "Incumbent Tokens",
      "type": "integer"
    },
    "label": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Label"
    },
    "label_status": {
      "title": "Label Status",
      "type": "string"
    },
    "labelled_at": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Labelled At"
    },
    "labelled_by_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Labelled By Name"
    },
    "project_id": {
      "title": "Project Id",
      "type": "string"
    },
    "sample_key": {
      "title": "Sample Key",
      "type": "string"
    },
    "source": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/ShadowPairSource"
        },
        {
          "type": "null"
        }
      ]
    }
  },
  "required": [
    "id",
    "project_id",
    "agent_id",
    "agent_label",
    "sample_key",
    "incumbent_tier",
    "candidate_tier",
    "incumbent_output",
    "candidate_output",
    "incumbent_tokens",
    "candidate_tokens",
    "label_status"
  ],
  "title": "ShadowPairRead",
  "type": "object"
}
```

## ShadowPairSource

```json
{
  "properties": {
    "build_number": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Build Number"
    },
    "href": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Href"
    },
    "id": {
      "title": "Id",
      "type": "string"
    },
    "kind": {
      "title": "Kind",
      "type": "string"
    },
    "label": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Label"
    },
    "run_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Run Id"
    }
  },
  "required": [
    "kind",
    "id"
  ],
  "title": "ShadowPairSource",
  "type": "object"
}
```

## ShareLinkResponse

```json
{
  "properties": {
    "access_count": {
      "default": 0,
      "title": "Access Count",
      "type": "integer"
    },
    "created_at": {
      "title": "Created At",
      "type": "string"
    },
    "created_by_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Created By Name"
    },
    "expires_at": {
      "title": "Expires At",
      "type": "string"
    },
    "id": {
      "title": "Id",
      "type": "string"
    },
    "is_revoked": {
      "default": false,
      "title": "Is Revoked",
      "type": "boolean"
    },
    "report_layout": {
      "title": "Report Layout",
      "type": "string"
    },
    "share_url": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Share Url"
    },
    "snapshot_ready": {
      "anyOf": [
        {
          "type": "boolean"
        },
        {
          "type": "null"
        }
      ],
      "title": "Snapshot Ready"
    },
    "token": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Token"
    },
    "warning": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Warning"
    }
  },
  "required": [
    "id",
    "report_layout",
    "expires_at",
    "created_at"
  ],
  "title": "ShareLinkResponse",
  "type": "object"
}
```

## SimilarMemoryRecallRequest

```json
{
  "description": "Request body for semantic similarity recall.",
  "properties": {
    "entity_type": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Entity Type"
    },
    "error_signature": {
      "maxLength": 5000,
      "minLength": 5,
      "title": "Error Signature",
      "type": "string"
    },
    "limit": {
      "default": 10,
      "maximum": 50.0,
      "minimum": 1.0,
      "title": "Limit",
      "type": "integer"
    }
  },
  "required": [
    "error_signature"
  ],
  "title": "SimilarMemoryRecallRequest",
  "type": "object"
}
```

## SimilarMemoryRecallResponse

```json
{
  "description": "Response with ranked similar memories.",
  "properties": {
    "query_signature": {
      "title": "Query Signature",
      "type": "string"
    },
    "results": {
      "items": {
        "$ref": "#/components/schemas/SimilarMemoryResponse"
      },
      "title": "Results",
      "type": "array"
    },
    "retrieval_audit": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Retrieval Audit"
    },
    "total_found": {
      "title": "Total Found",
      "type": "integer"
    }
  },
  "required": [
    "query_signature",
    "results",
    "total_found"
  ],
  "title": "SimilarMemoryRecallResponse",
  "type": "object"
}
```

## SimilarMemoryResponse

```json
{
  "description": "A memory entry with a similarity score from vector recall.",
  "properties": {
    "memory": {
      "$ref": "#/components/schemas/AgentMemoryEntryResponse"
    },
    "memory_reference": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/MemoryReference"
        },
        {
          "type": "null"
        }
      ]
    },
    "retrieval_audit": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Retrieval Audit"
    },
    "similarity": {
      "maximum": 1.0,
      "minimum": 0.0,
      "title": "Similarity",
      "type": "number"
    }
  },
  "required": [
    "memory",
    "similarity"
  ],
  "title": "SimilarMemoryResponse",
  "type": "object"
}
```

## SmtpConfigRead

```json
{
  "description": "SMTP server configuration returned to the client (no password).",
  "properties": {
    "enabled": {
      "title": "Enabled",
      "type": "boolean"
    },
    "from_address": {
      "title": "From Address",
      "type": "string"
    },
    "host": {
      "title": "Host",
      "type": "string"
    },
    "implicit_tls": {
      "description": "When True, implicit TLS (SSL/TLS on connect, typically port 465) is used. When False, STARTTLS (upgrade after connect, typically port 587) is used. Plain (unencrypted) SMTP is not supported.",
      "title": "Implicit Tls",
      "type": "boolean"
    },
    "password_set": {
      "title": "Password Set",
      "type": "boolean"
    },
    "port": {
      "title": "Port",
      "type": "integer"
    },
    "user": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "User"
    }
  },
  "required": [
    "enabled",
    "host",
    "port",
    "user",
    "from_address",
    "implicit_tls",
    "password_set"
  ],
  "title": "SmtpConfigRead",
  "type": "object"
}
```

## SmtpConfigUpdate

```json
{
  "description": "Payload for updating SMTP server configuration.",
  "properties": {
    "enabled": {
      "default": false,
      "title": "Enabled",
      "type": "boolean"
    },
    "from_address": {
      "default": "noreply@testlookup.io",
      "maxLength": 255,
      "title": "From Address",
      "type": "string"
    },
    "host": {
      "default": "localhost",
      "maxLength": 255,
      "title": "Host",
      "type": "string"
    },
    "implicit_tls": {
      "default": true,
      "description": "When True, implicit TLS (SSL/TLS on connect, typically port 465) is used. When False, STARTTLS (upgrade after connect, typically port 587) is used. Plain (unencrypted) SMTP is not supported.",
      "title": "Implicit Tls",
      "type": "boolean"
    },
    "password": {
      "anyOf": [
        {
          "maxLength": 1000,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Password"
    },
    "port": {
      "default": 587,
      "maximum": 65535.0,
      "minimum": 1.0,
      "title": "Port",
      "type": "integer"
    },
    "user": {
      "anyOf": [
        {
          "maxLength": 255,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "User"
    }
  },
  "title": "SmtpConfigUpdate",
  "type": "object"
}
```

## SmtpTestResult

```json
{
  "properties": {
    "message": {
      "title": "Message",
      "type": "string"
    },
    "success": {
      "title": "Success",
      "type": "boolean"
    }
  },
  "required": [
    "success",
    "message"
  ],
  "title": "SmtpTestResult",
  "type": "object"
}
```

## SourceRoot

```json
{
  "additionalProperties": false,
  "properties": {
    "prefix": {
      "default": "",
      "maxLength": 255,
      "title": "Prefix",
      "type": "string"
    },
    "root": {
      "maxLength": 500,
      "title": "Root",
      "type": "string"
    }
  },
  "required": [
    "root"
  ],
  "title": "SourceRoot",
  "type": "object"
}
```

## SourceRootsPreview

```json
{
  "properties": {
    "examples": {
      "default": [],
      "items": {
        "additionalProperties": {
          "type": "string"
        },
        "type": "object"
      },
      "title": "Examples",
      "type": "array"
    },
    "java_frames": {
      "title": "Java Frames",
      "type": "integer"
    },
    "located": {
      "title": "Located",
      "type": "integer"
    },
    "lookback_days": {
      "title": "Lookback Days",
      "type": "integer"
    },
    "matched": {
      "title": "Matched",
      "type": "integer"
    },
    "sampled": {
      "title": "Sampled",
      "type": "integer"
    }
  },
  "required": [
    "sampled",
    "java_frames",
    "located",
    "matched",
    "lookback_days"
  ],
  "title": "SourceRootsPreview",
  "type": "object"
}
```

## SourceRootsResponse

```json
{
  "properties": {
    "max_roots": {
      "default": 20,
      "title": "Max Roots",
      "type": "integer"
    },
    "roots": {
      "items": {
        "$ref": "#/components/schemas/SourceRoot"
      },
      "title": "Roots",
      "type": "array"
    }
  },
  "required": [
    "roots"
  ],
  "title": "SourceRootsResponse",
  "type": "object"
}
```

## SourceRootsUpdate

```json
{
  "additionalProperties": false,
  "properties": {
    "roots": {
      "items": {
        "$ref": "#/components/schemas/SourceRoot"
      },
      "title": "Roots",
      "type": "array"
    }
  },
  "title": "SourceRootsUpdate",
  "type": "object"
}
```

## StageDecisionSummary

```json
{
  "properties": {
    "analysis_mode": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Analysis Mode"
    },
    "completed_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Completed At"
    },
    "confidence_score": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Confidence Score"
    },
    "cost_usd": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Cost Usd"
    },
    "decision_log": {
      "items": {
        "$ref": "#/components/schemas/DecisionLogEntry"
      },
      "title": "Decision Log",
      "type": "array"
    },
    "duration_seconds": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Duration Seconds"
    },
    "error_category": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Error Category"
    },
    "evidence_count": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Evidence Count"
    },
    "execution_path": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Execution Path"
    },
    "fallback_reason": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Fallback Reason"
    },
    "fallback_used": {
      "anyOf": [
        {
          "type": "boolean"
        },
        {
          "type": "null"
        }
      ],
      "title": "Fallback Used"
    },
    "input_tokens": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Input Tokens"
    },
    "output_tokens": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Output Tokens"
    },
    "route_rationale": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Route Rationale"
    },
    "skipped_reason": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Skipped Reason"
    },
    "stage_name": {
      "title": "Stage Name",
      "type": "string"
    },
    "started_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Started At"
    },
    "status": {
      "title": "Status",
      "type": "string"
    }
  },
  "required": [
    "stage_name",
    "status"
  ],
  "title": "StageDecisionSummary",
  "type": "object"
}
```

## StepAction

```json
{
  "properties": {
    "step_key": {
      "title": "Step Key",
      "type": "string"
    }
  },
  "required": [
    "step_key"
  ],
  "title": "StepAction",
  "type": "object"
}
```

## StorageConfigRead

```json
{
  "description": "Data & storage configuration returned to the client.\nInfrastructure connection details are masked to prevent credential/topology disclosure.",
  "properties": {
    "active_collection_prefix": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Active Collection Prefix"
    },
    "chroma_collection": {
      "title": "Chroma Collection",
      "type": "string"
    },
    "chroma_host": {
      "title": "Chroma Host",
      "type": "string"
    },
    "chroma_port": {
      "title": "Chroma Port",
      "type": "integer"
    },
    "collection_prefix": {
      "default": "",
      "title": "Collection Prefix",
      "type": "string"
    },
    "collection_prefix_note": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Collection Prefix Note"
    },
    "minio_bucket_name": {
      "title": "Minio Bucket Name",
      "type": "string"
    },
    "minio_endpoint": {
      "title": "Minio Endpoint",
      "type": "string"
    },
    "minio_use_ssl": {
      "title": "Minio Use Ssl",
      "type": "boolean"
    },
    "mongo_connected": {
      "title": "Mongo Connected",
      "type": "boolean"
    },
    "postgres_connected": {
      "title": "Postgres Connected",
      "type": "boolean"
    },
    "redis_connected": {
      "title": "Redis Connected",
      "type": "boolean"
    },
    "storage_backend": {
      "title": "Storage Backend",
      "type": "string"
    }
  },
  "required": [
    "storage_backend",
    "postgres_connected",
    "mongo_connected",
    "redis_connected",
    "minio_endpoint",
    "minio_bucket_name",
    "minio_use_ssl",
    "chroma_host",
    "chroma_port",
    "chroma_collection"
  ],
  "title": "StorageConfigRead",
  "type": "object"
}
```

## StorageConfigUpdate

```json
{
  "description": "Payload for updating storage config. None = keep existing.",
  "properties": {
    "chroma_collection": {
      "anyOf": [
        {
          "maxLength": 255,
          "minLength": 0,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Chroma Collection"
    },
    "chroma_host": {
      "anyOf": [
        {
          "maxLength": 255,
          "minLength": 1,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Chroma Host"
    },
    "chroma_port": {
      "anyOf": [
        {
          "maximum": 65535.0,
          "minimum": 1.0,
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Chroma Port"
    },
    "minio_bucket_name": {
      "anyOf": [
        {
          "maxLength": 255,
          "minLength": 1,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Minio Bucket Name"
    },
    "minio_endpoint": {
      "anyOf": [
        {
          "maxLength": 500,
          "minLength": 1,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Minio Endpoint"
    },
    "minio_use_ssl": {
      "anyOf": [
        {
          "type": "boolean"
        },
        {
          "type": "null"
        }
      ],
      "title": "Minio Use Ssl"
    },
    "storage_backend": {
      "anyOf": [
        {
          "enum": [
            "minio",
            "s3",
            "local"
          ],
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Storage Backend"
    }
  },
  "title": "StorageConfigUpdate",
  "type": "object"
}
```

## StoreFootprintResponse

```json
{
  "description": "One store's contribution to a project's storage footprint.\n\n``measured=False`` means the store could not be reached — ``bytes`` and\n``items`` are then ``None``, never ``0``. Zero and unreachable are opposite\nfindings and must not render alike.\n\n``exact=False`` means the byte figure is a proportional estimate over a\nstore shared with other projects; ``estimate_basis`` says how.",
  "properties": {
    "bytes": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Bytes"
    },
    "complete": {
      "default": true,
      "title": "Complete",
      "type": "boolean"
    },
    "estimate_basis": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Estimate Basis"
    },
    "exact": {
      "title": "Exact",
      "type": "boolean"
    },
    "items": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Items"
    },
    "measured": {
      "title": "Measured",
      "type": "boolean"
    },
    "store": {
      "title": "Store",
      "type": "string"
    },
    "unreachable_reason": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Unreachable Reason"
    }
  },
  "required": [
    "store",
    "measured",
    "exact"
  ],
  "title": "StoreFootprintResponse",
  "type": "object"
}
```

## StrategyGapRead

```json
{
  "properties": {
    "count": {
      "title": "Count",
      "type": "integer"
    },
    "detail": {
      "title": "Detail",
      "type": "string"
    },
    "kind": {
      "title": "Kind",
      "type": "string"
    },
    "pill": {
      "title": "Pill",
      "type": "string"
    },
    "severity": {
      "title": "Severity",
      "type": "string"
    },
    "title": {
      "title": "Title",
      "type": "string"
    }
  },
  "required": [
    "kind",
    "severity",
    "title",
    "detail",
    "count",
    "pill"
  ],
  "title": "StrategyGapRead",
  "type": "object"
}
```

## StreamMessageRequest

```json
{
  "description": "One streamed chat turn: a new question, or ``retry`` to answer the\nsession's unanswered (or stopped) last question again.",
  "properties": {
    "message": {
      "anyOf": [
        {
          "maxLength": 4000,
          "minLength": 1,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Message"
    },
    "retry": {
      "default": false,
      "title": "Retry",
      "type": "boolean"
    }
  },
  "title": "StreamMessageRequest",
  "type": "object"
}
```

## StreamTicketResponse

```json
{
  "properties": {
    "expires_in": {
      "title": "Expires In",
      "type": "integer"
    },
    "links": {
      "additionalProperties": {
        "type": "string"
      },
      "title": "Links",
      "type": "object"
    },
    "ticket": {
      "title": "Ticket",
      "type": "string"
    }
  },
  "required": [
    "ticket",
    "expires_in",
    "links"
  ],
  "title": "StreamTicketResponse",
  "type": "object"
}
```

## SuiteDurationRegressionResponse

```json
{
  "description": "A suite whose median over its last 5 runs is >= 1.5x the 20 before.",
  "properties": {
    "baseline_median_ms": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Baseline Median Ms"
    },
    "baseline_runs": {
      "default": 0,
      "title": "Baseline Runs",
      "type": "integer"
    },
    "metric": {
      "default": "summed",
      "title": "Metric",
      "type": "string"
    },
    "new": {
      "default": false,
      "title": "New",
      "type": "boolean"
    },
    "observed_ms": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Observed Ms"
    },
    "ratio": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Ratio"
    },
    "recent_median_ms": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Recent Median Ms"
    },
    "recent_runs": {
      "default": 0,
      "title": "Recent Runs",
      "type": "integer"
    },
    "suite_key": {
      "title": "Suite Key",
      "type": "string"
    },
    "suite_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Suite Name"
    },
    "was_slow_before": {
      "default": false,
      "title": "Was Slow Before",
      "type": "boolean"
    }
  },
  "required": [
    "suite_key"
  ],
  "title": "SuiteDurationRegressionResponse",
  "type": "object"
}
```

## SuiteExecutionHistoryResponse

```json
{
  "description": "GET /api/v1/analytics/execution/suite-history.",
  "properties": {
    "branch": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Branch"
    },
    "days": {
      "title": "Days",
      "type": "integer"
    },
    "limit": {
      "title": "Limit",
      "type": "integer"
    },
    "measured": {
      "default": false,
      "title": "Measured",
      "type": "boolean"
    },
    "meta": {
      "additionalProperties": true,
      "title": "Meta",
      "type": "object"
    },
    "points": {
      "items": {
        "$ref": "#/components/schemas/SuiteHistoryPoint"
      },
      "title": "Points",
      "type": "array"
    },
    "project_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Project Id"
    },
    "reason": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Reason"
    },
    "stats": {
      "$ref": "#/components/schemas/SuiteHistoryStats"
    },
    "suite_key": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Suite Key"
    },
    "suite_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Suite Name"
    },
    "total": {
      "default": 0,
      "title": "Total",
      "type": "integer"
    },
    "truncated": {
      "default": false,
      "title": "Truncated",
      "type": "boolean"
    }
  },
  "required": [
    "days",
    "limit"
  ],
  "title": "SuiteExecutionHistoryResponse",
  "type": "object"
}
```

## SuiteExecutionResponse

```json
{
  "description": "One ``suite_executions`` row: how long one suite of one run took.",
  "properties": {
    "broken": {
      "default": 0,
      "title": "Broken",
      "type": "integer"
    },
    "failed": {
      "default": 0,
      "title": "Failed",
      "type": "integer"
    },
    "finished_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Finished At"
    },
    "parallelism": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Parallelism"
    },
    "passed": {
      "default": 0,
      "title": "Passed",
      "type": "integer"
    },
    "setup_ms": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Setup Ms"
    },
    "skipped": {
      "default": 0,
      "title": "Skipped",
      "type": "integer"
    },
    "source": {
      "title": "Source",
      "type": "string"
    },
    "started_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Started At"
    },
    "suite_key": {
      "title": "Suite Key",
      "type": "string"
    },
    "suite_name": {
      "title": "Suite Name",
      "type": "string"
    },
    "summed_ms": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Summed Ms"
    },
    "teardown_ms": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Teardown Ms"
    },
    "test_count": {
      "default": 0,
      "title": "Test Count",
      "type": "integer"
    },
    "timed_count": {
      "default": 0,
      "title": "Timed Count",
      "type": "integer"
    },
    "unknown": {
      "default": 0,
      "title": "Unknown",
      "type": "integer"
    },
    "wall_ms": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Wall Ms"
    }
  },
  "required": [
    "suite_name",
    "suite_key",
    "source"
  ],
  "title": "SuiteExecutionResponse",
  "type": "object"
}
```

## SuiteHistoryPoint

```json
{
  "description": "One run of the suite.",
  "properties": {
    "branch": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Branch"
    },
    "broken": {
      "default": 0,
      "title": "Broken",
      "type": "integer"
    },
    "build_number": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Build Number"
    },
    "failed": {
      "default": 0,
      "title": "Failed",
      "type": "integer"
    },
    "finished_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Finished At"
    },
    "parallelism": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Parallelism"
    },
    "passed": {
      "default": 0,
      "title": "Passed",
      "type": "integer"
    },
    "run_created_at": {
      "format": "date-time",
      "title": "Run Created At",
      "type": "string"
    },
    "run_id": {
      "format": "uuid",
      "title": "Run Id",
      "type": "string"
    },
    "run_status": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Run Status"
    },
    "skipped": {
      "default": 0,
      "title": "Skipped",
      "type": "integer"
    },
    "source": {
      "title": "Source",
      "type": "string"
    },
    "started_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Started At"
    },
    "summed_ms": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Summed Ms"
    },
    "test_count": {
      "default": 0,
      "title": "Test Count",
      "type": "integer"
    },
    "timed_count": {
      "default": 0,
      "title": "Timed Count",
      "type": "integer"
    },
    "unknown": {
      "default": 0,
      "title": "Unknown",
      "type": "integer"
    },
    "wall_ms": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Wall Ms"
    }
  },
  "required": [
    "run_id",
    "run_created_at",
    "source"
  ],
  "title": "SuiteHistoryPoint",
  "type": "object"
}
```

## SuiteHistoryStats

```json
{
  "description": "Window percentiles over EVERY execution in the window (not only the\nreturned points).",
  "properties": {
    "parallelism_p50": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Parallelism P50"
    },
    "summed": {
      "$ref": "#/components/schemas/ExecutionPercentiles"
    },
    "wall": {
      "$ref": "#/components/schemas/ExecutionPercentiles"
    }
  },
  "title": "SuiteHistoryStats",
  "type": "object"
}
```

## SuiteOwnerResponse

```json
{
  "properties": {
    "is_fallback": {
      "default": false,
      "title": "Is Fallback",
      "type": "boolean"
    },
    "owner_email": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Owner Email"
    },
    "owner_full_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Owner Full Name"
    },
    "owner_user_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Owner User Id"
    },
    "project_id": {
      "format": "uuid",
      "title": "Project Id",
      "type": "string"
    },
    "suite_name": {
      "title": "Suite Name",
      "type": "string"
    }
  },
  "required": [
    "project_id",
    "suite_name"
  ],
  "title": "SuiteOwnerResponse",
  "type": "object"
}
```

## SuiteOwnerUpdate

```json
{
  "description": "PUT body for setting/clearing a suite's explicit owner.",
  "properties": {
    "owner_user_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Owner User Id"
    }
  },
  "title": "SuiteOwnerUpdate",
  "type": "object"
}
```

## SuiteReviewResponse

```json
{
  "properties": {
    "created_at": {
      "format": "date-time",
      "title": "Created At",
      "type": "string"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "note": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Note"
    },
    "project_id": {
      "format": "uuid",
      "title": "Project Id",
      "type": "string"
    },
    "reviewed_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Reviewed At"
    },
    "reviewer_email": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Reviewer Email"
    },
    "reviewer_user_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Reviewer User Id"
    },
    "state": {
      "title": "State",
      "type": "string"
    },
    "suite_name": {
      "title": "Suite Name",
      "type": "string"
    },
    "test_run_id": {
      "format": "uuid",
      "title": "Test Run Id",
      "type": "string"
    },
    "updated_at": {
      "format": "date-time",
      "title": "Updated At",
      "type": "string"
    }
  },
  "required": [
    "id",
    "project_id",
    "suite_name",
    "test_run_id",
    "state",
    "created_at",
    "updated_at"
  ],
  "title": "SuiteReviewResponse",
  "type": "object"
}
```

## SuiteReviewUpdate

```json
{
  "properties": {
    "note": {
      "anyOf": [
        {
          "maxLength": 4000,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Note"
    },
    "state": {
      "enum": [
        "pending",
        "confirmed",
        "acknowledged",
        "review_later"
      ],
      "title": "State",
      "type": "string"
    }
  },
  "required": [
    "state"
  ],
  "title": "SuiteReviewUpdate",
  "type": "object"
}
```

## SummaryReportResponse

```json
{
  "properties": {
    "avg_duration_ms": {
      "title": "Avg Duration Ms",
      "type": "integer"
    },
    "flaky_criteria": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/FlakyCountCriteria"
        },
        {
          "type": "null"
        }
      ]
    },
    "flaky_rate_pct": {
      "title": "Flaky Rate Pct",
      "type": "number"
    },
    "flaky_test_count": {
      "description": "Not scoped by release_id. A test is flaky by its last N executions across the project's history (flaky_criteria) -- a run-count window, not a set of runs -- and the dashboard's flaky count and the release gate's flaky cap read that same project-wide figure; restricting it to one release's runs would be a different metric. Scoped by suite_name.",
      "title": "Flaky Test Count",
      "type": "integer"
    },
    "generated_at": {
      "title": "Generated At",
      "type": "string"
    },
    "last_run_before_window_at": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Last Run Before Window At"
    },
    "latest_run_at": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Latest Run At"
    },
    "meta": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Meta"
    },
    "mode": {
      "enum": [
        "window",
        "latest"
      ],
      "title": "Mode",
      "type": "string"
    },
    "period_end": {
      "title": "Period End",
      "type": "string"
    },
    "period_start": {
      "title": "Period Start",
      "type": "string"
    },
    "project_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Project Id"
    },
    "project_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Project Name"
    },
    "run_count": {
      "title": "Run Count",
      "type": "integer"
    },
    "runs_per_day": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Runs Per Day"
    },
    "suites": {
      "items": {
        "$ref": "#/components/schemas/SummarySuiteRow"
      },
      "title": "Suites",
      "type": "array"
    },
    "top_failing_tests": {
      "items": {
        "$ref": "#/components/schemas/SummaryTopFailingTest"
      },
      "title": "Top Failing Tests",
      "type": "array"
    },
    "totals": {
      "$ref": "#/components/schemas/SummaryTotals"
    },
    "window_days": {
      "title": "Window Days",
      "type": "integer"
    }
  },
  "required": [
    "mode",
    "window_days",
    "generated_at",
    "period_start",
    "period_end",
    "totals",
    "run_count",
    "avg_duration_ms",
    "flaky_test_count",
    "flaky_rate_pct",
    "suites",
    "top_failing_tests"
  ],
  "title": "SummaryReportResponse",
  "type": "object"
}
```

## SummaryStepBreakdownRow

```json
{
  "description": "One captured granular step for a failing test (Phase 5 enrichment).",
  "properties": {
    "assertion_message": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Assertion Message"
    },
    "name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Name"
    },
    "status": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Status"
    }
  },
  "title": "SummaryStepBreakdownRow",
  "type": "object"
}
```

## SummarySuiteRow

```json
{
  "properties": {
    "broken": {
      "title": "Broken",
      "type": "integer"
    },
    "failed": {
      "title": "Failed",
      "type": "integer"
    },
    "last_run_at": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Last Run At"
    },
    "pass_rate_pct": {
      "title": "Pass Rate Pct",
      "type": "number"
    },
    "passed": {
      "title": "Passed",
      "type": "integer"
    },
    "passed_steps": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Passed Steps"
    },
    "skipped": {
      "title": "Skipped",
      "type": "integer"
    },
    "step_success_rate": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Step Success Rate"
    },
    "suite_name": {
      "title": "Suite Name",
      "type": "string"
    },
    "total": {
      "title": "Total",
      "type": "integer"
    },
    "total_steps": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Total Steps"
    },
    "weighted_pass_rate_pct": {
      "title": "Weighted Pass Rate Pct",
      "type": "number"
    }
  },
  "required": [
    "suite_name",
    "total",
    "passed",
    "failed",
    "skipped",
    "broken",
    "pass_rate_pct",
    "weighted_pass_rate_pct"
  ],
  "title": "SummarySuiteRow",
  "type": "object"
}
```

## SummaryTopFailingTest

```json
{
  "properties": {
    "class_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Class Name"
    },
    "failure_step": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Failure Step"
    },
    "failures": {
      "title": "Failures",
      "type": "integer"
    },
    "step_breakdown": {
      "anyOf": [
        {
          "items": {
            "$ref": "#/components/schemas/SummaryStepBreakdownRow"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Step Breakdown"
    },
    "suite_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Suite Name"
    },
    "test_name": {
      "title": "Test Name",
      "type": "string"
    }
  },
  "required": [
    "test_name",
    "failures"
  ],
  "title": "SummaryTopFailingTest",
  "type": "object"
}
```

## SummaryTotals

```json
{
  "properties": {
    "broken": {
      "title": "Broken",
      "type": "integer"
    },
    "broken_rate_pct": {
      "title": "Broken Rate Pct",
      "type": "number"
    },
    "evaluated": {
      "title": "Evaluated",
      "type": "integer"
    },
    "fail_rate_pct": {
      "title": "Fail Rate Pct",
      "type": "number"
    },
    "failed": {
      "title": "Failed",
      "type": "integer"
    },
    "pass_rate_basis": {
      "default": "unique_tests",
      "title": "Pass Rate Basis",
      "type": "string"
    },
    "pass_rate_basis_label": {
      "default": "per unique test",
      "title": "Pass Rate Basis Label",
      "type": "string"
    },
    "pass_rate_pct": {
      "title": "Pass Rate Pct",
      "type": "number"
    },
    "passed": {
      "title": "Passed",
      "type": "integer"
    },
    "skip_rate_pct": {
      "title": "Skip Rate Pct",
      "type": "number"
    },
    "skipped": {
      "title": "Skipped",
      "type": "integer"
    },
    "total_test_cases": {
      "title": "Total Test Cases",
      "type": "integer"
    },
    "weighted_pass_rate_pct": {
      "title": "Weighted Pass Rate Pct",
      "type": "number"
    }
  },
  "required": [
    "total_test_cases",
    "passed",
    "failed",
    "skipped",
    "broken",
    "evaluated",
    "pass_rate_pct",
    "fail_rate_pct",
    "skip_rate_pct",
    "broken_rate_pct",
    "weighted_pass_rate_pct"
  ],
  "title": "SummaryTotals",
  "type": "object"
}
```

## SuppliedCommit

```json
{
  "description": "One commit in a caller-supplied commit range (US-8.1 air-gapped path).\n\nCI/SDK callers that already know the commits landed since the last green\nrun can push them on ingest so TestLookup needs NO outbound VCS call.\nBounded so a payload can't balloon: ``files`` is capped and the enclosing\n``commit_range`` list is capped on each ingest schema.",
  "properties": {
    "author": {
      "anyOf": [
        {
          "maxLength": 255,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Author"
    },
    "committed_at": {
      "anyOf": [
        {
          "maxLength": 40,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Committed At"
    },
    "files": {
      "anyOf": [
        {
          "items": {
            "type": "string"
          },
          "maxItems": 500,
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Files"
    },
    "message": {
      "anyOf": [
        {
          "maxLength": 2000,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Message"
    },
    "sha": {
      "maxLength": 64,
      "minLength": 1,
      "title": "Sha",
      "type": "string"
    }
  },
  "required": [
    "sha"
  ],
  "title": "SuppliedCommit",
  "type": "object"
}
```

## SuppliedCommitRange

```json
{
  "description": "A caller-supplied commit range WITH its boundary refs.\n\nThe original US-8.1 wire shape for ``commit_range`` was a bare list of\ncommits, which threw away the range's boundary: the backend stored\n``base_commit = NULL`` and nobody downstream could reconstruct what the\nrange was *relative to*. That makes the row useless as test-impact\n(Epic 10) training data. This object form carries the boundary the\ncaller already knows (it ran ``git log base..head`` to build the list).\n\nBoth wire shapes stay accepted — ``commit_range`` is\n``list[SuppliedCommit] | SuppliedCommitRange`` on every ingest schema,\nso existing SDK/CLI callers that push a bare list are unaffected.\nField aliases accept ``base``/``base_commit`` and ``head``/``head_commit``\nso callers do not have to guess which spelling we wanted.",
  "properties": {
    "base": {
      "anyOf": [
        {
          "maxLength": 64,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "description": "Commit the range starts AFTER (exclusive) — the baseline ref.",
      "title": "Base"
    },
    "commits": {
      "items": {
        "$ref": "#/components/schemas/SuppliedCommit"
      },
      "maxItems": 100,
      "title": "Commits",
      "type": "array"
    },
    "head": {
      "anyOf": [
        {
          "maxLength": 64,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "description": "Commit the range ends AT (inclusive) — usually this run's commit.",
      "title": "Head"
    }
  },
  "title": "SuppliedCommitRange",
  "type": "object"
}
```

## TaskBudgetV1

```json
{
  "additionalProperties": false,
  "properties": {
    "max_cost_usd": {
      "anyOf": [
        {
          "minimum": 0.0,
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Max Cost Usd"
    },
    "max_llm_calls": {
      "anyOf": [
        {
          "minimum": 0.0,
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Max Llm Calls"
    },
    "max_retries": {
      "anyOf": [
        {
          "minimum": 0.0,
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Max Retries"
    },
    "max_seconds": {
      "anyOf": [
        {
          "minimum": 0.0,
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Max Seconds"
    },
    "max_tokens": {
      "anyOf": [
        {
          "minimum": 0.0,
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Max Tokens"
    }
  },
  "title": "TaskBudgetV1",
  "type": "object"
}
```

## TaskUsageV1

```json
{
  "additionalProperties": false,
  "properties": {
    "cost_usd": {
      "default": 0,
      "minimum": 0.0,
      "title": "Cost Usd",
      "type": "number"
    },
    "duration_ms": {
      "default": 0,
      "minimum": 0.0,
      "title": "Duration Ms",
      "type": "integer"
    },
    "input_tokens": {
      "default": 0,
      "minimum": 0.0,
      "title": "Input Tokens",
      "type": "integer"
    },
    "llm_calls": {
      "default": 0,
      "minimum": 0.0,
      "title": "Llm Calls",
      "type": "integer"
    },
    "output_tokens": {
      "default": 0,
      "minimum": 0.0,
      "title": "Output Tokens",
      "type": "integer"
    },
    "total_tokens": {
      "default": 0,
      "minimum": 0.0,
      "title": "Total Tokens",
      "type": "integer"
    }
  },
  "title": "TaskUsageV1",
  "type": "object"
}
```

## TeamChannelResponse

```json
{
  "description": "Team → notification channel mapping (PMF US-7.3).",
  "properties": {
    "channel_type": {
      "title": "Channel Type",
      "type": "string"
    },
    "created_at": {
      "format": "date-time",
      "title": "Created At",
      "type": "string"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "is_active": {
      "title": "Is Active",
      "type": "boolean"
    },
    "project_id": {
      "format": "uuid",
      "title": "Project Id",
      "type": "string"
    },
    "target": {
      "title": "Target",
      "type": "string"
    },
    "team_name": {
      "title": "Team Name",
      "type": "string"
    },
    "updated_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Updated At"
    }
  },
  "required": [
    "id",
    "project_id",
    "team_name",
    "channel_type",
    "target",
    "is_active",
    "created_at"
  ],
  "title": "TeamChannelResponse",
  "type": "object"
}
```

## TeamChannelUpsert

```json
{
  "description": "Create/replace the notification channel for one ownership team\n(PMF US-7.3). The team is keyed by name in the URL path.",
  "properties": {
    "channel_type": {
      "pattern": "^(email|slack|teams)$",
      "title": "Channel Type",
      "type": "string"
    },
    "is_active": {
      "default": true,
      "title": "Is Active",
      "type": "boolean"
    },
    "target": {
      "maxLength": 2000,
      "minLength": 1,
      "title": "Target",
      "type": "string"
    }
  },
  "required": [
    "channel_type",
    "target"
  ],
  "title": "TeamChannelUpsert",
  "type": "object"
}
```

## TerminalOutcomeV1

```json
{
  "additionalProperties": false,
  "properties": {
    "budget_exhausted": {
      "default": false,
      "title": "Budget Exhausted",
      "type": "boolean"
    },
    "budget_stop_reasons": {
      "default": [],
      "items": {
        "type": "string"
      },
      "title": "Budget Stop Reasons",
      "type": "array"
    },
    "decision_report_verification": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Decision Report Verification"
    },
    "error": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Error"
    },
    "status": {
      "enum": [
        "pending",
        "running",
        "retry_wait",
        "completed",
        "passed",
        "failed"
      ],
      "title": "Status",
      "type": "string"
    },
    "workflow_verification": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Workflow Verification"
    }
  },
  "required": [
    "status"
  ],
  "title": "TerminalOutcomeV1",
  "type": "object"
}
```

## TestAttachmentResponse

```json
{
  "description": "Index-only attachment metadata (Phase 1 stores refs, not bytes).",
  "properties": {
    "created_at": {
      "format": "date-time",
      "title": "Created At",
      "type": "string"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "media_type": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Media Type"
    },
    "name": {
      "title": "Name",
      "type": "string"
    },
    "source_ref": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Source Ref"
    },
    "source_test_run_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Source Test Run Id"
    },
    "test_step_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Test Step Id"
    }
  },
  "required": [
    "id",
    "name",
    "created_at"
  ],
  "title": "TestAttachmentResponse",
  "type": "object"
}
```

## TestCaseBulkItemResult

```json
{
  "properties": {
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "outcome": {
      "title": "Outcome",
      "type": "string"
    },
    "reason": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Reason"
    },
    "status": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Status"
    },
    "version": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Version"
    }
  },
  "required": [
    "id",
    "outcome"
  ],
  "title": "TestCaseBulkItemResult",
  "type": "object"
}
```

## TestCaseBulkRequest

```json
{
  "properties": {
    "action": {
      "maxLength": 30,
      "title": "Action",
      "type": "string"
    },
    "assignee_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Assignee Id"
    },
    "case_ids": {
      "items": {
        "format": "uuid",
        "type": "string"
      },
      "maxItems": 200,
      "minItems": 1,
      "title": "Case Ids",
      "type": "array"
    },
    "expected_versions": {
      "additionalProperties": {
        "type": "integer"
      },
      "propertyNames": {
        "format": "uuid"
      },
      "title": "Expected Versions",
      "type": "object"
    },
    "priority": {
      "anyOf": [
        {
          "maxLength": 20,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Priority"
    },
    "project_id": {
      "format": "uuid",
      "title": "Project Id",
      "type": "string"
    },
    "reason": {
      "anyOf": [
        {
          "maxLength": 500,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Reason"
    },
    "suite_name": {
      "anyOf": [
        {
          "maxLength": 500,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Suite Name"
    },
    "tags": {
      "anyOf": [
        {
          "items": {
            "type": "string"
          },
          "maxItems": 20,
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Tags"
    },
    "transition": {
      "anyOf": [
        {
          "maxLength": 30,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Transition"
    }
  },
  "required": [
    "project_id",
    "case_ids",
    "action"
  ],
  "title": "TestCaseBulkRequest",
  "type": "object"
}
```

## TestCaseBulkResponse

```json
{
  "properties": {
    "action": {
      "title": "Action",
      "type": "string"
    },
    "changed": {
      "title": "Changed",
      "type": "integer"
    },
    "not_found": {
      "title": "Not Found",
      "type": "integer"
    },
    "requested": {
      "title": "Requested",
      "type": "integer"
    },
    "results": {
      "items": {
        "$ref": "#/components/schemas/TestCaseBulkItemResult"
      },
      "title": "Results",
      "type": "array"
    },
    "skipped": {
      "title": "Skipped",
      "type": "integer"
    },
    "unchanged": {
      "title": "Unchanged",
      "type": "integer"
    }
  },
  "required": [
    "action",
    "requested",
    "changed",
    "unchanged",
    "skipped",
    "not_found",
    "results"
  ],
  "title": "TestCaseBulkResponse",
  "type": "object"
}
```

## TestCaseClassificationSection

```json
{
  "description": "Classification fields supplied by the report or existing catalog.",
  "properties": {
    "class_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Class Name"
    },
    "component_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Component Name"
    },
    "components": {
      "items": {
        "additionalProperties": true,
        "type": "object"
      },
      "title": "Components",
      "type": "array"
    },
    "epic": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Epic"
    },
    "feature": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Feature"
    },
    "file_path": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "File Path"
    },
    "framework": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Framework"
    },
    "labels": {
      "items": {
        "additionalProperties": {
          "type": "string"
        },
        "type": "object"
      },
      "title": "Labels",
      "type": "array"
    },
    "language": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Language"
    },
    "owner": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Owner"
    },
    "package_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Package Name"
    },
    "service": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Service"
    },
    "service_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Service Name"
    },
    "severity": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Severity"
    },
    "story": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Story"
    },
    "suite": {
      "anyOf": [
        {
          "additionalProperties": {
            "anyOf": [
              {
                "type": "string"
              },
              {
                "type": "null"
              }
            ]
          },
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Suite"
    },
    "suite_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Suite Name"
    },
    "tags": {
      "items": {
        "type": "string"
      },
      "title": "Tags",
      "type": "array"
    }
  },
  "title": "TestCaseClassificationSection",
  "type": "object"
}
```

## TestCaseCommentCreate

```json
{
  "properties": {
    "comment_type": {
      "default": "general",
      "title": "Comment Type",
      "type": "string"
    },
    "content": {
      "maxLength": 50000,
      "minLength": 1,
      "title": "Content",
      "type": "string"
    },
    "parent_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Parent Id"
    },
    "step_number": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Step Number"
    }
  },
  "required": [
    "content"
  ],
  "title": "TestCaseCommentCreate",
  "type": "object"
}
```

## TestCaseCommentResponse

```json
{
  "properties": {
    "author_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Author Id"
    },
    "comment_type": {
      "title": "Comment Type",
      "type": "string"
    },
    "content": {
      "title": "Content",
      "type": "string"
    },
    "created_at": {
      "format": "date-time",
      "title": "Created At",
      "type": "string"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "is_resolved": {
      "title": "Is Resolved",
      "type": "boolean"
    },
    "mentions": {
      "default": [],
      "items": {
        "$ref": "#/components/schemas/CommentMention"
      },
      "title": "Mentions",
      "type": "array"
    },
    "parent_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Parent Id"
    },
    "step_number": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Step Number"
    },
    "test_case_id": {
      "format": "uuid",
      "title": "Test Case Id",
      "type": "string"
    },
    "updated_at": {
      "format": "date-time",
      "title": "Updated At",
      "type": "string"
    }
  },
  "required": [
    "id",
    "test_case_id",
    "content",
    "comment_type",
    "is_resolved",
    "created_at",
    "updated_at"
  ],
  "title": "TestCaseCommentResponse",
  "type": "object"
}
```

## TestCaseDeprecateRequest

```json
{
  "properties": {
    "reason": {
      "maxLength": 500,
      "minLength": 1,
      "title": "Reason",
      "type": "string"
    }
  },
  "required": [
    "reason"
  ],
  "title": "TestCaseDeprecateRequest",
  "type": "object"
}
```

## TestCaseDurationStatsResponse

```json
{
  "description": "EXEC-03: duration percentiles of the test over the history window\n(every execution in the window, not only the 50 points returned).",
  "properties": {
    "max_ms": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Max Ms"
    },
    "measured": {
      "default": false,
      "title": "Measured",
      "type": "boolean"
    },
    "n": {
      "default": 0,
      "title": "N",
      "type": "integer"
    },
    "p50_ms": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "P50 Ms"
    },
    "p95_ms": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "P95 Ms"
    },
    "reason": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Reason"
    },
    "window_days": {
      "default": 30,
      "title": "Window Days",
      "type": "integer"
    }
  },
  "title": "TestCaseDurationStatsResponse",
  "type": "object"
}
```

## TestCaseExecutionSection

```json
{
  "description": "Outcome and bounded execution metadata for the result snapshot.",
  "properties": {
    "call_ms": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Call Ms"
    },
    "duration_ms": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Duration Ms"
    },
    "error_message": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Error Message"
    },
    "failure_category": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Failure Category"
    },
    "finished_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Finished At"
    },
    "has_attachments": {
      "default": false,
      "title": "Has Attachments",
      "type": "boolean"
    },
    "is_flaky_run": {
      "anyOf": [
        {
          "type": "boolean"
        },
        {
          "type": "null"
        }
      ],
      "title": "Is Flaky Run"
    },
    "parameters": {
      "items": {
        "additionalProperties": true,
        "type": "object"
      },
      "title": "Parameters",
      "type": "array"
    },
    "retry_count": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Retry Count"
    },
    "setup_ms": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Setup Ms"
    },
    "stack_trace": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Stack Trace"
    },
    "started_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Started At"
    },
    "status": {
      "$ref": "#/components/schemas/TestStatus"
    },
    "step_count": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Step Count"
    },
    "steps_present": {
      "default": false,
      "title": "Steps Present",
      "type": "boolean"
    },
    "teardown_ms": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Teardown Ms"
    },
    "timing_source": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Timing Source"
    },
    "worker_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Worker Id"
    }
  },
  "required": [
    "status"
  ],
  "title": "TestCaseExecutionSection",
  "type": "object"
}
```

## TestCaseFlakinessResponse

```json
{
  "description": "Computed flakiness for the in-window timeline.\n\n``failure_rate``/``failure_rate_pct`` match ``analytics_service.flaky_tests``;\n``classification`` + ``impact_score`` reuse ``test_health_coach_service``\nthresholds (no new formula).",
  "properties": {
    "classification": {
      "default": "HEALTHY",
      "title": "Classification",
      "type": "string"
    },
    "failed": {
      "default": 0,
      "title": "Failed",
      "type": "integer"
    },
    "failure_rate": {
      "default": 0.0,
      "title": "Failure Rate",
      "type": "number"
    },
    "failure_rate_pct": {
      "default": 0.0,
      "title": "Failure Rate Pct",
      "type": "number"
    },
    "impact_score": {
      "default": 0.0,
      "title": "Impact Score",
      "type": "number"
    },
    "is_flaky": {
      "default": false,
      "title": "Is Flaky",
      "type": "boolean"
    },
    "passed": {
      "default": 0,
      "title": "Passed",
      "type": "integer"
    },
    "total_runs": {
      "default": 0,
      "title": "Total Runs",
      "type": "integer"
    },
    "window_days": {
      "default": 30,
      "title": "Window Days",
      "type": "integer"
    }
  },
  "title": "TestCaseFlakinessResponse",
  "type": "object"
}
```

## TestCaseHistoryPointResponse

```json
{
  "description": "One cross-run point in a logical test's timeline (most-recent-first).",
  "properties": {
    "ai": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/TestHistoryAI"
        },
        {
          "type": "null"
        }
      ]
    },
    "build_number": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Build Number"
    },
    "call_ms": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Call Ms"
    },
    "created_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Created At"
    },
    "duration_ms": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Duration Ms"
    },
    "finished_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Finished At"
    },
    "run_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Run Id"
    },
    "run_label": {
      "title": "Run Label",
      "type": "string"
    },
    "run_seq": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Run Seq"
    },
    "setup_ms": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Setup Ms"
    },
    "started_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Started At"
    },
    "status": {
      "title": "Status",
      "type": "string"
    },
    "teardown_ms": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Teardown Ms"
    },
    "timing_source": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Timing Source"
    },
    "worker_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Worker Id"
    }
  },
  "required": [
    "run_label",
    "status"
  ],
  "title": "TestCaseHistoryPointResponse",
  "type": "object"
}
```

## TestCaseHistoryResponse

```json
{
  "description": "Wrapper for GET /runs/{run_id}/tests/{test_id}/history.",
  "properties": {
    "ai_changes": {
      "default": [],
      "items": {
        "$ref": "#/components/schemas/TestHistoryAIChange"
      },
      "title": "Ai Changes",
      "type": "array"
    },
    "baseline": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/TestDurationBaselineResponse"
        },
        {
          "type": "null"
        }
      ]
    },
    "duration_stats": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/TestCaseDurationStatsResponse"
        },
        {
          "type": "null"
        }
      ]
    },
    "flakiness": {
      "$ref": "#/components/schemas/TestCaseFlakinessResponse"
    },
    "history": {
      "default": [],
      "items": {
        "$ref": "#/components/schemas/TestCaseHistoryPointResponse"
      },
      "title": "History",
      "type": "array"
    },
    "metadata": {
      "$ref": "#/components/schemas/TestCaseMetadataResponse"
    },
    "run_id": {
      "title": "Run Id",
      "type": "string"
    },
    "test_fingerprint": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Test Fingerprint"
    },
    "test_id": {
      "title": "Test Id",
      "type": "string"
    },
    "test_name": {
      "title": "Test Name",
      "type": "string"
    }
  },
  "required": [
    "run_id",
    "test_id",
    "test_name",
    "flakiness",
    "metadata"
  ],
  "title": "TestCaseHistoryResponse",
  "type": "object"
}
```

## TestCaseIdentitySection

```json
{
  "description": "Stable and source-native identifiers for one executed test result.",
  "properties": {
    "canonical_test_case_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Canonical Test Case Id"
    },
    "display_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Display Name"
    },
    "fingerprint": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Fingerprint"
    },
    "full_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Full Name"
    },
    "history_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "History Id"
    },
    "source_history_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Source History Id"
    },
    "source_test_case_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Source Test Case Id"
    },
    "source_uuid": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Source Uuid"
    },
    "test_case_id": {
      "format": "uuid",
      "title": "Test Case Id",
      "type": "string"
    },
    "test_fingerprint": {
      "title": "Test Fingerprint",
      "type": "string"
    },
    "test_name": {
      "title": "Test Name",
      "type": "string"
    },
    "test_run_id": {
      "format": "uuid",
      "title": "Test Run Id",
      "type": "string"
    }
  },
  "required": [
    "test_case_id",
    "test_run_id",
    "test_fingerprint",
    "test_name"
  ],
  "title": "TestCaseIdentitySection",
  "type": "object"
}
```

## TestCaseImportResponse

```json
{
  "properties": {
    "dry_run": {
      "title": "Dry Run",
      "type": "boolean"
    },
    "file_name": {
      "title": "File Name",
      "type": "string"
    },
    "format": {
      "title": "Format",
      "type": "string"
    },
    "headers": {
      "$ref": "#/components/schemas/ImportHeaderReport"
    },
    "project_id": {
      "format": "uuid",
      "title": "Project Id",
      "type": "string"
    },
    "rows": {
      "items": {
        "$ref": "#/components/schemas/ImportRowResult"
      },
      "title": "Rows",
      "type": "array"
    },
    "summary": {
      "$ref": "#/components/schemas/ImportSummary"
    }
  },
  "required": [
    "project_id",
    "dry_run",
    "file_name",
    "format",
    "headers",
    "summary",
    "rows"
  ],
  "title": "TestCaseImportResponse",
  "type": "object"
}
```

## TestCaseLifecycleState

```json
{
  "description": "Stored lifecycle vocabulary for authored ``ManagedTestCase`` rows.",
  "enum": [
    "draft",
    "review_requested",
    "under_review",
    "approved",
    "active",
    "rejected",
    "needs_update",
    "deprecated",
    "archived"
  ],
  "title": "TestCaseLifecycleState",
  "type": "string"
}
```

## TestCaseListResponse

```json
{
  "properties": {
    "items": {
      "items": {
        "$ref": "#/components/schemas/TestCaseSummary"
      },
      "title": "Items",
      "type": "array"
    },
    "page": {
      "title": "Page",
      "type": "integer"
    },
    "pages": {
      "title": "Pages",
      "type": "integer"
    },
    "size": {
      "title": "Size",
      "type": "integer"
    },
    "total": {
      "title": "Total",
      "type": "integer"
    }
  },
  "required": [
    "items",
    "total",
    "page",
    "size",
    "pages"
  ],
  "title": "TestCaseListResponse",
  "type": "object"
}
```

## TestCaseMetadataResponse

```json
{
  "description": "Identity metadata: owner, effective suite, first/last seen, timestamps.",
  "properties": {
    "assigned_to_user_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Assigned To User Id"
    },
    "created_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Created At"
    },
    "feature": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Feature"
    },
    "first_seen_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "First Seen At"
    },
    "first_seen_run_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "First Seen Run Id"
    },
    "first_seen_run_label": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "First Seen Run Label"
    },
    "last_seen_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Last Seen At"
    },
    "last_seen_run_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Last Seen Run Id"
    },
    "last_seen_run_label": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Last Seen Run Label"
    },
    "owner": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Owner"
    },
    "severity": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Severity"
    },
    "suite": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Suite"
    },
    "updated_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Updated At"
    }
  },
  "title": "TestCaseMetadataResponse",
  "type": "object"
}
```

## TestCaseParameterSchema

```json
{
  "description": "Optional authored input metadata; sensitive values are never required.",
  "properties": {
    "masked": {
      "default": false,
      "title": "Masked",
      "type": "boolean"
    },
    "mode": {
      "anyOf": [
        {
          "maxLength": 30,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Mode"
    },
    "name": {
      "maxLength": 255,
      "minLength": 1,
      "title": "Name",
      "type": "string"
    },
    "value": {
      "anyOf": [
        {
          "maxLength": 50000,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Value"
    }
  },
  "required": [
    "name"
  ],
  "title": "TestCaseParameterSchema",
  "type": "object"
}
```

## TestCaseProvenanceSection

```json
{
  "description": "Where the current normalized detail came from.",
  "properties": {
    "field_sources": {
      "additionalProperties": {
        "type": "string"
      },
      "title": "Field Sources",
      "type": "object"
    },
    "format": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Format"
    },
    "minio_s3_prefix": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Minio S3 Prefix"
    },
    "parser_format": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Parser Format"
    },
    "parser_version": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Parser Version"
    },
    "source_file": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Source File"
    },
    "source_test_run_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Source Test Run Id"
    },
    "warnings": {
      "items": {
        "type": "string"
      },
      "title": "Warnings",
      "type": "array"
    }
  },
  "title": "TestCaseProvenanceSection",
  "type": "object"
}
```

## TestCaseRestoreRequest

```json
{
  "properties": {
    "change_summary": {
      "anyOf": [
        {
          "maxLength": 500,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Change Summary"
    },
    "expected_version": {
      "minimum": 1.0,
      "title": "Expected Version",
      "type": "integer"
    },
    "version": {
      "minimum": 1.0,
      "title": "Version",
      "type": "integer"
    }
  },
  "required": [
    "version",
    "expected_version"
  ],
  "title": "TestCaseRestoreRequest",
  "type": "object"
}
```

## TestCaseReviewResponse

```json
{
  "properties": {
    "ai_quality_score": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Ai Quality Score"
    },
    "ai_review_completed": {
      "title": "Ai Review Completed",
      "type": "boolean"
    },
    "ai_review_notes": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Ai Review Notes"
    },
    "ai_reviewed_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Ai Reviewed At"
    },
    "created_at": {
      "format": "date-time",
      "title": "Created At",
      "type": "string"
    },
    "human_notes": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Human Notes"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "requested_by_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Requested By Id"
    },
    "reviewed_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Reviewed At"
    },
    "reviewer_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Reviewer Id"
    },
    "status": {
      "title": "Status",
      "type": "string"
    },
    "test_case_id": {
      "format": "uuid",
      "title": "Test Case Id",
      "type": "string"
    },
    "updated_at": {
      "format": "date-time",
      "title": "Updated At",
      "type": "string"
    }
  },
  "required": [
    "id",
    "test_case_id",
    "status",
    "ai_review_completed",
    "created_at",
    "updated_at"
  ],
  "title": "TestCaseReviewResponse",
  "type": "object"
}
```

## TestCaseStepSchema

```json
{
  "description": "Authored step shape; expected outcomes are optional by design.",
  "properties": {
    "action": {
      "maxLength": 50000,
      "minLength": 1,
      "title": "Action",
      "type": "string"
    },
    "expected_result": {
      "anyOf": [
        {
          "maxLength": 50000,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Expected Result"
    },
    "step_number": {
      "minimum": 1.0,
      "title": "Step Number",
      "type": "integer"
    }
  },
  "required": [
    "step_number",
    "action"
  ],
  "title": "TestCaseStepSchema",
  "type": "object"
}
```

## TestCaseSummary

```json
{
  "properties": {
    "assigned_to_user_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Assigned To User Id"
    },
    "class_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Class Name"
    },
    "created_at": {
      "format": "date-time",
      "title": "Created At",
      "type": "string"
    },
    "duration_ms": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Duration Ms"
    },
    "failure_category": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Failure Category"
    },
    "failure_kind": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "description": "Derived failure-kind triad (US-9.1): product / test_code /\ninfrastructure / unknown. AI-derived from the stored\n``failure_category`` + the FAILED-vs-BROKEN status distinction —\nsee ``app/services/failure_kind.py`` for the mapping rationale.\nNone for non-failing rows (a kind only makes sense for failures).",
      "readOnly": true,
      "title": "Failure Kind"
    },
    "feature": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Feature"
    },
    "has_attachments": {
      "default": false,
      "title": "Has Attachments",
      "type": "boolean"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "severity": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Severity"
    },
    "status": {
      "$ref": "#/components/schemas/TestStatus"
    },
    "step_count": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Step Count"
    },
    "suite_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Suite Name"
    },
    "test_name": {
      "title": "Test Name",
      "type": "string"
    },
    "test_run_id": {
      "format": "uuid",
      "title": "Test Run Id",
      "type": "string"
    }
  },
  "required": [
    "id",
    "test_run_id",
    "test_name",
    "status",
    "created_at",
    "failure_kind"
  ],
  "title": "TestCaseSummary",
  "type": "object"
}
```

## TestCaseTimelineResponse

```json
{
  "properties": {
    "automation_linked": {
      "default": false,
      "title": "Automation Linked",
      "type": "boolean"
    },
    "case_id": {
      "format": "uuid",
      "title": "Case Id",
      "type": "string"
    },
    "items": {
      "items": {
        "$ref": "#/components/schemas/TimelineEntry"
      },
      "title": "Items",
      "type": "array"
    },
    "kinds": {
      "items": {
        "type": "string"
      },
      "title": "Kinds",
      "type": "array"
    },
    "next_cursor": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Next Cursor"
    }
  },
  "required": [
    "case_id",
    "kinds",
    "items"
  ],
  "title": "TestCaseTimelineResponse",
  "type": "object"
}
```

## TestCaseTransitionRequest

```json
{
  "properties": {
    "action": {
      "enum": [
        "request_review",
        "claim_review",
        "withdraw_review",
        "unclaim",
        "approve",
        "reject",
        "request_changes",
        "activate",
        "flag_stale",
        "revise",
        "deprecate",
        "reinstate",
        "archive"
      ],
      "title": "Action",
      "type": "string"
    },
    "expected_version": {
      "minimum": 1.0,
      "title": "Expected Version",
      "type": "integer"
    },
    "notes": {
      "anyOf": [
        {
          "maxLength": 50000,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Notes"
    },
    "reason": {
      "anyOf": [
        {
          "maxLength": 500,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Reason"
    }
  },
  "required": [
    "action",
    "expected_version"
  ],
  "title": "TestCaseTransitionRequest",
  "type": "object"
}
```

## TestCaseVersionExecution

```json
{
  "description": "A plan execution recorded against one case version (W28 TCM-05).",
  "properties": {
    "executed_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Executed At"
    },
    "execution_status": {
      "title": "Execution Status",
      "type": "string"
    },
    "item_id": {
      "format": "uuid",
      "title": "Item Id",
      "type": "string"
    },
    "plan_id": {
      "format": "uuid",
      "title": "Plan Id",
      "type": "string"
    },
    "plan_name": {
      "title": "Plan Name",
      "type": "string"
    }
  },
  "required": [
    "plan_id",
    "plan_name",
    "item_id",
    "execution_status"
  ],
  "title": "TestCaseVersionExecution",
  "type": "object"
}
```

## TestCaseVersionResponse

```json
{
  "properties": {
    "assignee_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Assignee Id"
    },
    "automation_status": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Automation Status"
    },
    "change_summary": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Change Summary"
    },
    "change_type": {
      "title": "Change Type",
      "type": "string"
    },
    "changed_by_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Changed By Id"
    },
    "changed_by_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Changed By Name"
    },
    "changed_fields": {
      "anyOf": [
        {
          "items": {
            "type": "string"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Changed Fields"
    },
    "created_at": {
      "format": "date-time",
      "title": "Created At",
      "type": "string"
    },
    "description": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Description"
    },
    "estimated_duration_minutes": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Estimated Duration Minutes"
    },
    "executions": {
      "items": {
        "$ref": "#/components/schemas/TestCaseVersionExecution"
      },
      "title": "Executions",
      "type": "array"
    },
    "expected_result": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Expected Result"
    },
    "feature_area": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Feature Area"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "is_automated": {
      "anyOf": [
        {
          "type": "boolean"
        },
        {
          "type": "null"
        }
      ],
      "title": "Is Automated"
    },
    "objective": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Objective"
    },
    "parameters": {
      "anyOf": [
        {
          "items": {
            "$ref": "#/components/schemas/TestCaseParameterSchema"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Parameters"
    },
    "preconditions": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Preconditions"
    },
    "priority": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Priority"
    },
    "severity": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Severity"
    },
    "source": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Source"
    },
    "status": {
      "title": "Status",
      "type": "string"
    },
    "steps": {
      "anyOf": [
        {
          "items": {
            "$ref": "#/components/schemas/TestCaseStepSchema"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Steps"
    },
    "suite_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Suite Name"
    },
    "tags": {
      "anyOf": [
        {
          "items": {},
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Tags"
    },
    "test_case_id": {
      "format": "uuid",
      "title": "Test Case Id",
      "type": "string"
    },
    "test_data": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Test Data"
    },
    "test_fingerprint": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Test Fingerprint"
    },
    "test_suite_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Test Suite Id"
    },
    "test_type": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Test Type"
    },
    "title": {
      "title": "Title",
      "type": "string"
    },
    "version": {
      "title": "Version",
      "type": "integer"
    }
  },
  "required": [
    "id",
    "test_case_id",
    "version",
    "title",
    "status",
    "change_type",
    "created_at"
  ],
  "title": "TestCaseVersionResponse",
  "type": "object"
}
```

## TestDurationBaselineResponse

```json
{
  "description": "A test's running duration statistics (``perf_baselines``).",
  "properties": {
    "last_observed_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Last Observed At"
    },
    "last_observed_ms": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Last Observed Ms"
    },
    "mean_ms": {
      "title": "Mean Ms",
      "type": "number"
    },
    "p95_ms": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "P95 Ms"
    },
    "sample_count": {
      "default": 0,
      "title": "Sample Count",
      "type": "integer"
    },
    "stddev_ms": {
      "default": 0.0,
      "title": "Stddev Ms",
      "type": "number"
    }
  },
  "required": [
    "mean_ms"
  ],
  "title": "TestDurationBaselineResponse",
  "type": "object"
}
```

## TestExecutionReviewRead

```json
{
  "description": "Current review state for an AI-flagged failure.",
  "properties": {
    "created_at": {
      "format": "date-time",
      "title": "Created At",
      "type": "string"
    },
    "defect_link": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Defect Link"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "note": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Note"
    },
    "project_id": {
      "format": "uuid",
      "title": "Project Id",
      "type": "string"
    },
    "reviewed_by_full_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Reviewed By Full Name"
    },
    "reviewed_by_user_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Reviewed By User Id"
    },
    "reviewed_by_username": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Reviewed By Username"
    },
    "state": {
      "enum": [
        "pending_review",
        "reviewed",
        "defect_filed",
        "false_positive",
        "reproducible"
      ],
      "title": "State",
      "type": "string"
    },
    "test_case_id": {
      "format": "uuid",
      "title": "Test Case Id",
      "type": "string"
    },
    "transitioned_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Transitioned At"
    },
    "updated_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Updated At"
    }
  },
  "required": [
    "id",
    "test_case_id",
    "project_id",
    "state",
    "created_at"
  ],
  "title": "TestExecutionReviewRead",
  "type": "object"
}
```

## TestExecutionReviewUpdate

```json
{
  "description": "Transition the review state. ``state`` is required; other fields are\noptional context the reviewer can attach (e.g. defect URL on\n``defect_filed``, freeform note explaining the verdict).",
  "properties": {
    "defect_link": {
      "anyOf": [
        {
          "maxLength": 2000,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Defect Link"
    },
    "note": {
      "anyOf": [
        {
          "maxLength": 4000,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Note"
    },
    "state": {
      "enum": [
        "pending_review",
        "reviewed",
        "defect_filed",
        "false_positive",
        "reproducible"
      ],
      "title": "State",
      "type": "string"
    }
  },
  "required": [
    "state"
  ],
  "title": "TestExecutionReviewUpdate",
  "type": "object"
}
```

## TestHealthFinding

```json
{
  "description": "Per-test health finding.",
  "properties": {
    "anti_patterns": {
      "default": [],
      "items": {
        "type": "string"
      },
      "title": "Anti Patterns",
      "type": "array"
    },
    "critical_count": {
      "default": 0,
      "title": "Critical Count",
      "type": "integer"
    },
    "health_score": {
      "title": "Health Score",
      "type": "integer"
    },
    "recommendation": {
      "default": "",
      "title": "Recommendation",
      "type": "string"
    },
    "test_case_id": {
      "title": "Test Case Id",
      "type": "string"
    },
    "test_name": {
      "title": "Test Name",
      "type": "string"
    },
    "violations": {
      "default": [],
      "items": {
        "$ref": "#/components/schemas/TestHealthViolation"
      },
      "title": "Violations",
      "type": "array"
    },
    "warning_count": {
      "default": 0,
      "title": "Warning Count",
      "type": "integer"
    }
  },
  "required": [
    "test_case_id",
    "test_name",
    "health_score"
  ],
  "title": "TestHealthFinding",
  "type": "object"
}
```

## TestHealthResponse

```json
{
  "description": "Test health findings for a run.",
  "properties": {
    "avg_health_score": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Avg Health Score"
    },
    "findings": {
      "default": [],
      "items": {
        "$ref": "#/components/schemas/TestHealthFinding"
      },
      "title": "Findings",
      "type": "array"
    },
    "run_id": {
      "title": "Run Id",
      "type": "string"
    },
    "total_analyzed": {
      "default": 0,
      "title": "Total Analyzed",
      "type": "integer"
    },
    "with_violations": {
      "default": 0,
      "title": "With Violations",
      "type": "integer"
    }
  },
  "required": [
    "run_id"
  ],
  "title": "TestHealthResponse",
  "type": "object"
}
```

## TestHealthViolation

```json
{
  "properties": {
    "occurrences": {
      "default": 1,
      "title": "Occurrences",
      "type": "integer"
    },
    "pattern": {
      "title": "Pattern",
      "type": "string"
    },
    "severity": {
      "title": "Severity",
      "type": "string"
    }
  },
  "required": [
    "pattern",
    "severity"
  ],
  "title": "TestHealthViolation",
  "type": "object"
}
```

## TestHistoryAI

```json
{
  "properties": {
    "analysis_mode": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Analysis Mode"
    },
    "category": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Category"
    },
    "confidence": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Confidence"
    },
    "is_flaky": {
      "default": false,
      "title": "Is Flaky",
      "type": "boolean"
    },
    "model": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Model"
    },
    "requires_human_review": {
      "default": false,
      "title": "Requires Human Review",
      "type": "boolean"
    },
    "root_cause_excerpt": {
      "default": "",
      "title": "Root Cause Excerpt",
      "type": "string"
    },
    "root_cause_sha": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Root Cause Sha"
    },
    "test_id": {
      "title": "Test Id",
      "type": "string"
    },
    "version_count": {
      "default": 0,
      "title": "Version Count",
      "type": "integer"
    }
  },
  "required": [
    "test_id"
  ],
  "title": "TestHistoryAI",
  "type": "object"
}
```

## TestHistoryAIChange

```json
{
  "properties": {
    "from_category": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "From Category"
    },
    "from_confidence": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "From Confidence"
    },
    "kinds": {
      "items": {
        "type": "string"
      },
      "title": "Kinds",
      "type": "array"
    },
    "run_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Run Id"
    },
    "run_label": {
      "title": "Run Label",
      "type": "string"
    },
    "to_category": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "To Category"
    },
    "to_confidence": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "To Confidence"
    }
  },
  "required": [
    "run_label"
  ],
  "title": "TestHistoryAIChange",
  "type": "object"
}
```

## TestNotificationRequest

```json
{
  "properties": {
    "channel": {
      "$ref": "#/components/schemas/NotificationChannel"
    },
    "preference_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Preference Id"
    }
  },
  "required": [
    "channel"
  ],
  "title": "TestNotificationRequest",
  "type": "object"
}
```

## TestPlanCreate

```json
{
  "properties": {
    "assigned_to_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Assigned To Id"
    },
    "description": {
      "anyOf": [
        {
          "maxLength": 50000,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Description"
    },
    "name": {
      "maxLength": 500,
      "minLength": 3,
      "title": "Name",
      "type": "string"
    },
    "objective": {
      "anyOf": [
        {
          "maxLength": 50000,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Objective"
    },
    "planned_end_date": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Planned End Date"
    },
    "planned_start_date": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Planned Start Date"
    },
    "project_id": {
      "format": "uuid",
      "title": "Project Id",
      "type": "string"
    },
    "tags": {
      "anyOf": [
        {
          "items": {
            "type": "string"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Tags"
    }
  },
  "required": [
    "project_id",
    "name"
  ],
  "title": "TestPlanCreate",
  "type": "object"
}
```

## TestPlanItemCreate

```json
{
  "properties": {
    "order_index": {
      "default": 0,
      "title": "Order Index",
      "type": "integer"
    },
    "priority_override": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Priority Override"
    },
    "test_case_id": {
      "format": "uuid",
      "title": "Test Case Id",
      "type": "string"
    }
  },
  "required": [
    "test_case_id"
  ],
  "title": "TestPlanItemCreate",
  "type": "object"
}
```

## TestPlanItemResponse

```json
{
  "properties": {
    "actual_duration_minutes": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Actual Duration Minutes"
    },
    "case_version": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Case Version"
    },
    "created_at": {
      "format": "date-time",
      "title": "Created At",
      "type": "string"
    },
    "executed_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Executed At"
    },
    "executed_by_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Executed By Id"
    },
    "executed_case_version": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Executed Case Version"
    },
    "execution_notes": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Execution Notes"
    },
    "execution_status": {
      "title": "Execution Status",
      "type": "string"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "order_index": {
      "title": "Order Index",
      "type": "integer"
    },
    "plan_id": {
      "format": "uuid",
      "title": "Plan Id",
      "type": "string"
    },
    "priority_override": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Priority Override"
    },
    "test_case_id": {
      "format": "uuid",
      "title": "Test Case Id",
      "type": "string"
    }
  },
  "required": [
    "id",
    "plan_id",
    "test_case_id",
    "order_index",
    "execution_status",
    "created_at"
  ],
  "title": "TestPlanItemResponse",
  "type": "object"
}
```

## TestPlanListResponse

```json
{
  "properties": {
    "items": {
      "items": {
        "$ref": "#/components/schemas/TestPlanResponse"
      },
      "title": "Items",
      "type": "array"
    },
    "page": {
      "title": "Page",
      "type": "integer"
    },
    "pages": {
      "title": "Pages",
      "type": "integer"
    },
    "size": {
      "title": "Size",
      "type": "integer"
    },
    "total": {
      "title": "Total",
      "type": "integer"
    }
  },
  "required": [
    "items",
    "total",
    "page",
    "size",
    "pages"
  ],
  "title": "TestPlanListResponse",
  "type": "object"
}
```

## TestPlanResponse

```json
{
  "properties": {
    "actual_end_date": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Actual End Date"
    },
    "actual_start_date": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Actual Start Date"
    },
    "ai_generated": {
      "title": "Ai Generated",
      "type": "boolean"
    },
    "assigned_to_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Assigned To Id"
    },
    "blocked_cases": {
      "title": "Blocked Cases",
      "type": "integer"
    },
    "created_at": {
      "format": "date-time",
      "title": "Created At",
      "type": "string"
    },
    "created_by_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Created By Id"
    },
    "description": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Description"
    },
    "executed_cases": {
      "title": "Executed Cases",
      "type": "integer"
    },
    "failed_cases": {
      "title": "Failed Cases",
      "type": "integer"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "name": {
      "title": "Name",
      "type": "string"
    },
    "objective": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Objective"
    },
    "passed_cases": {
      "title": "Passed Cases",
      "type": "integer"
    },
    "planned_end_date": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Planned End Date"
    },
    "planned_start_date": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Planned Start Date"
    },
    "project_id": {
      "format": "uuid",
      "title": "Project Id",
      "type": "string"
    },
    "status": {
      "title": "Status",
      "type": "string"
    },
    "tags": {
      "anyOf": [
        {
          "items": {
            "type": "string"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Tags"
    },
    "total_cases": {
      "title": "Total Cases",
      "type": "integer"
    },
    "updated_at": {
      "format": "date-time",
      "title": "Updated At",
      "type": "string"
    }
  },
  "required": [
    "id",
    "project_id",
    "name",
    "status",
    "ai_generated",
    "total_cases",
    "executed_cases",
    "passed_cases",
    "failed_cases",
    "blocked_cases",
    "created_at",
    "updated_at"
  ],
  "title": "TestPlanResponse",
  "type": "object"
}
```

## TestPlanUpdate

```json
{
  "properties": {
    "actual_end_date": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Actual End Date"
    },
    "actual_start_date": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Actual Start Date"
    },
    "assigned_to_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Assigned To Id"
    },
    "description": {
      "anyOf": [
        {
          "maxLength": 50000,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Description"
    },
    "name": {
      "anyOf": [
        {
          "maxLength": 500,
          "minLength": 3,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Name"
    },
    "objective": {
      "anyOf": [
        {
          "maxLength": 50000,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Objective"
    },
    "planned_end_date": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Planned End Date"
    },
    "planned_start_date": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Planned Start Date"
    },
    "status": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Status"
    },
    "tags": {
      "anyOf": [
        {
          "items": {
            "type": "string"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Tags"
    }
  },
  "title": "TestPlanUpdate",
  "type": "object"
}
```

## TestStatus

```json
{
  "enum": [
    "PASSED",
    "FAILED",
    "SKIPPED",
    "BROKEN",
    "UNKNOWN"
  ],
  "title": "TestStatus",
  "type": "string"
}
```

## TestStepResponse

```json
{
  "description": "One granular step in a logical test's latest-run snapshot.\n\n``steps`` carries the nested child steps (Allure before/after + sub-steps).",
  "properties": {
    "actual_value": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Actual Value"
    },
    "assertion_message": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Assertion Message"
    },
    "assertion_trace": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Assertion Trace"
    },
    "attachments": {
      "items": {
        "$ref": "#/components/schemas/TestAttachmentResponse"
      },
      "title": "Attachments",
      "type": "array"
    },
    "created_at": {
      "format": "date-time",
      "title": "Created At",
      "type": "string"
    },
    "depth": {
      "title": "Depth",
      "type": "integer"
    },
    "duration_ms": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Duration Ms"
    },
    "expected_value": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Expected Value"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "keyword": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Keyword"
    },
    "name": {
      "title": "Name",
      "type": "string"
    },
    "ordinal": {
      "title": "Ordinal",
      "type": "integer"
    },
    "parameters": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "items": {},
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Parameters"
    },
    "parent_step_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Parent Step Id"
    },
    "start_ms": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Start Ms"
    },
    "status": {
      "$ref": "#/components/schemas/TestStatus"
    },
    "steps": {
      "items": {
        "$ref": "#/components/schemas/TestStepResponse"
      },
      "title": "Steps",
      "type": "array"
    }
  },
  "required": [
    "id",
    "ordinal",
    "depth",
    "name",
    "status",
    "created_at"
  ],
  "title": "TestStepResponse",
  "type": "object"
}
```

## TestStrategyResponse

```json
{
  "properties": {
    "ai_generated": {
      "title": "Ai Generated",
      "type": "boolean"
    },
    "ai_model_used": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Ai Model Used"
    },
    "approved_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Approved At"
    },
    "approved_by_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Approved By Id"
    },
    "automation_approach": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Automation Approach"
    },
    "created_at": {
      "format": "date-time",
      "title": "Created At",
      "type": "string"
    },
    "created_by_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Created By Id"
    },
    "defect_management": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Defect Management"
    },
    "entry_criteria": {
      "anyOf": [
        {
          "items": {},
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Entry Criteria"
    },
    "environments": {
      "anyOf": [
        {
          "items": {},
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Environments"
    },
    "exit_criteria": {
      "anyOf": [
        {
          "items": {},
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Exit Criteria"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "name": {
      "title": "Name",
      "type": "string"
    },
    "objective": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Objective"
    },
    "out_of_scope": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Out Of Scope"
    },
    "project_id": {
      "format": "uuid",
      "title": "Project Id",
      "type": "string"
    },
    "risk_assessment": {
      "anyOf": [
        {
          "items": {},
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Risk Assessment"
    },
    "scope": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Scope"
    },
    "status": {
      "title": "Status",
      "type": "string"
    },
    "test_approach": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Test Approach"
    },
    "test_types": {
      "anyOf": [
        {
          "items": {},
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Test Types"
    },
    "updated_at": {
      "format": "date-time",
      "title": "Updated At",
      "type": "string"
    },
    "version_label": {
      "title": "Version Label",
      "type": "string"
    }
  },
  "required": [
    "id",
    "project_id",
    "name",
    "version_label",
    "status",
    "ai_generated",
    "created_at",
    "updated_at"
  ],
  "title": "TestStrategyResponse",
  "type": "object"
}
```

## TestStrategyUpdate

```json
{
  "properties": {
    "automation_approach": {
      "anyOf": [
        {
          "maxLength": 50000,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Automation Approach"
    },
    "defect_management": {
      "anyOf": [
        {
          "maxLength": 50000,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Defect Management"
    },
    "entry_criteria": {
      "anyOf": [
        {
          "items": {
            "type": "string"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Entry Criteria"
    },
    "environments": {
      "anyOf": [
        {
          "items": {
            "additionalProperties": true,
            "type": "object"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Environments"
    },
    "exit_criteria": {
      "anyOf": [
        {
          "items": {
            "type": "string"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Exit Criteria"
    },
    "name": {
      "anyOf": [
        {
          "maxLength": 500,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Name"
    },
    "objective": {
      "anyOf": [
        {
          "maxLength": 50000,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Objective"
    },
    "out_of_scope": {
      "anyOf": [
        {
          "maxLength": 50000,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Out Of Scope"
    },
    "risk_assessment": {
      "anyOf": [
        {
          "items": {
            "additionalProperties": true,
            "type": "object"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Risk Assessment"
    },
    "scope": {
      "anyOf": [
        {
          "maxLength": 50000,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Scope"
    },
    "status": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Status"
    },
    "test_approach": {
      "anyOf": [
        {
          "maxLength": 50000,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Test Approach"
    },
    "test_types": {
      "anyOf": [
        {
          "items": {
            "additionalProperties": true,
            "type": "object"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Test Types"
    },
    "version_label": {
      "anyOf": [
        {
          "maxLength": 50,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Version Label"
    }
  },
  "title": "TestStrategyUpdate",
  "type": "object"
}
```

## TestSuiteCreate

```json
{
  "properties": {
    "description": {
      "anyOf": [
        {
          "maxLength": 50000,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Description"
    },
    "name": {
      "maxLength": 500,
      "minLength": 1,
      "title": "Name",
      "type": "string"
    },
    "owner_user_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Owner User Id"
    },
    "project_id": {
      "format": "uuid",
      "title": "Project Id",
      "type": "string"
    },
    "tags": {
      "anyOf": [
        {
          "items": {
            "type": "string"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Tags"
    }
  },
  "required": [
    "project_id",
    "name"
  ],
  "title": "TestSuiteCreate",
  "type": "object"
}
```

## TestSuiteListResponse

```json
{
  "properties": {
    "items": {
      "items": {
        "$ref": "#/components/schemas/TestSuiteResponse"
      },
      "title": "Items",
      "type": "array"
    },
    "total": {
      "title": "Total",
      "type": "integer"
    }
  },
  "required": [
    "items",
    "total"
  ],
  "title": "TestSuiteListResponse",
  "type": "object"
}
```

## TestSuiteResponse

```json
{
  "properties": {
    "created_at": {
      "format": "date-time",
      "title": "Created At",
      "type": "string"
    },
    "description": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Description"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "is_default": {
      "title": "Is Default",
      "type": "boolean"
    },
    "name": {
      "title": "Name",
      "type": "string"
    },
    "project_id": {
      "format": "uuid",
      "title": "Project Id",
      "type": "string"
    },
    "tags": {
      "anyOf": [
        {
          "items": {
            "type": "string"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Tags"
    },
    "test_case_count": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Test Case Count"
    },
    "updated_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Updated At"
    }
  },
  "required": [
    "created_at",
    "id",
    "project_id",
    "name",
    "is_default"
  ],
  "title": "TestSuiteResponse",
  "type": "object"
}
```

## TestSuiteUpdate

```json
{
  "description": "None = keep existing value.",
  "properties": {
    "description": {
      "anyOf": [
        {
          "maxLength": 50000,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Description"
    },
    "name": {
      "anyOf": [
        {
          "maxLength": 500,
          "minLength": 1,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Name"
    },
    "tags": {
      "anyOf": [
        {
          "items": {
            "type": "string"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Tags"
    }
  },
  "title": "TestSuiteUpdate",
  "type": "object"
}
```

## ThresholdCheck

```json
{
  "description": "US-15.2 — the recorded confidence-gate evaluation for one AI output.\n\nPersisted verbatim in ``AIAnalysis.routing_metadata[\"threshold_check\"]``\nand ``Defect.policy_evaluation[\"threshold_check\"]``. Built by\n``services.confidence_gate.build_threshold_check`` — keep the shapes in\nsync (four keys, no more).",
  "properties": {
    "observed_confidence": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Observed Confidence"
    },
    "passed": {
      "title": "Passed",
      "type": "boolean"
    },
    "source": {
      "title": "Source",
      "type": "string"
    },
    "threshold": {
      "title": "Threshold",
      "type": "integer"
    }
  },
  "required": [
    "threshold",
    "passed",
    "source"
  ],
  "title": "ThresholdCheck",
  "type": "object"
}
```

## ThresholdsConfig

```json
{
  "additionalProperties": false,
  "properties": {
    "confidence_min": {
      "default": 80,
      "maximum": 100.0,
      "minimum": 0.0,
      "title": "Confidence Min",
      "type": "integer"
    },
    "degraded_ratio": {
      "default": 0.3,
      "maximum": 1.0,
      "minimum": 0.0,
      "title": "Degraded Ratio",
      "type": "number"
    },
    "max_failures_analyzed": {
      "default": 50,
      "maximum": 1000.0,
      "minimum": 1.0,
      "title": "Max Failures Analyzed",
      "type": "integer"
    }
  },
  "title": "ThresholdsConfig",
  "type": "object"
}
```

## TierComparisonRequest

```json
{
  "additionalProperties": false,
  "properties": {
    "agent_id": {
      "maxLength": 80,
      "minLength": 1,
      "title": "Agent Id",
      "type": "string"
    },
    "candidate_tier": {
      "pattern": "^(deterministic|slm|llm|auto)$",
      "title": "Candidate Tier",
      "type": "string"
    },
    "capability": {
      "maxLength": 80,
      "minLength": 1,
      "title": "Capability",
      "type": "string"
    },
    "delta": {
      "default": 0.05,
      "maximum": 1.0,
      "minimum": 0.0,
      "title": "Delta",
      "type": "number"
    },
    "include_live_labels": {
      "default": false,
      "title": "Include Live Labels",
      "type": "boolean"
    },
    "incumbent_tier": {
      "pattern": "^(deterministic|slm|llm|auto)$",
      "title": "Incumbent Tier",
      "type": "string"
    },
    "pairs": {
      "items": {
        "$ref": "#/components/schemas/TierOutputPairRequest"
      },
      "maxItems": 1000,
      "title": "Pairs",
      "type": "array"
    },
    "persist": {
      "default": true,
      "title": "Persist",
      "type": "boolean"
    },
    "project_id": {
      "format": "uuid",
      "title": "Project Id",
      "type": "string"
    }
  },
  "required": [
    "project_id",
    "agent_id",
    "capability",
    "incumbent_tier",
    "candidate_tier"
  ],
  "title": "TierComparisonRequest",
  "type": "object"
}
```

## TierOutputPairRequest

```json
{
  "additionalProperties": false,
  "properties": {
    "candidate_cost_usd": {
      "default": 0.0,
      "minimum": 0.0,
      "title": "Candidate Cost Usd",
      "type": "number"
    },
    "candidate_latency_ms": {
      "default": 0,
      "minimum": 0.0,
      "title": "Candidate Latency Ms",
      "type": "integer"
    },
    "candidate_output": {
      "title": "Candidate Output"
    },
    "candidate_tokens": {
      "default": 0,
      "minimum": 0.0,
      "title": "Candidate Tokens",
      "type": "integer"
    },
    "incumbent_cost_usd": {
      "default": 0.0,
      "minimum": 0.0,
      "title": "Incumbent Cost Usd",
      "type": "number"
    },
    "incumbent_latency_ms": {
      "default": 0,
      "minimum": 0.0,
      "title": "Incumbent Latency Ms",
      "type": "integer"
    },
    "incumbent_output": {
      "title": "Incumbent Output"
    },
    "incumbent_tokens": {
      "default": 0,
      "minimum": 0.0,
      "title": "Incumbent Tokens",
      "type": "integer"
    },
    "sample_id": {
      "maxLength": 160,
      "minLength": 1,
      "title": "Sample Id",
      "type": "string"
    }
  },
  "required": [
    "sample_id",
    "incumbent_output",
    "candidate_output"
  ],
  "title": "TierOutputPairRequest",
  "type": "object"
}
```

## TimelineEntry

```json
{
  "properties": {
    "actor_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Actor Id"
    },
    "actor_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Actor Name"
    },
    "actor_source": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Actor Source"
    },
    "detail": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Detail"
    },
    "id": {
      "title": "Id",
      "type": "string"
    },
    "kind": {
      "title": "Kind",
      "type": "string"
    },
    "link": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/TimelineLink"
        },
        {
          "type": "null"
        }
      ]
    },
    "occurred_at": {
      "format": "date-time",
      "title": "Occurred At",
      "type": "string"
    },
    "status": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Status"
    },
    "title": {
      "title": "Title",
      "type": "string"
    },
    "version": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Version"
    }
  },
  "required": [
    "id",
    "kind",
    "occurred_at",
    "title"
  ],
  "title": "TimelineEntry",
  "type": "object"
}
```

## TimelineLink

```json
{
  "properties": {
    "defect_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Defect Id"
    },
    "jira_key": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Jira Key"
    },
    "plan_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Plan Id"
    },
    "run_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Run Id"
    },
    "test_case_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Test Case Id"
    },
    "type": {
      "title": "Type",
      "type": "string"
    }
  },
  "required": [
    "type"
  ],
  "title": "TimelineLink",
  "type": "object"
}
```

## TimelineNoteCreate

```json
{
  "additionalProperties": false,
  "properties": {
    "ends_on": {
      "anyOf": [
        {
          "format": "date",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Ends On"
    },
    "starts_on": {
      "format": "date",
      "title": "Starts On",
      "type": "string"
    },
    "suite_name": {
      "anyOf": [
        {
          "maxLength": 500,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Suite Name"
    },
    "text": {
      "maxLength": 280,
      "title": "Text",
      "type": "string"
    }
  },
  "required": [
    "starts_on",
    "text"
  ],
  "title": "TimelineNoteCreate",
  "type": "object"
}
```

## TimelineNoteList

```json
{
  "properties": {
    "end": {
      "title": "End",
      "type": "string"
    },
    "items": {
      "items": {
        "$ref": "#/components/schemas/TimelineNoteResponse"
      },
      "title": "Items",
      "type": "array"
    },
    "start": {
      "title": "Start",
      "type": "string"
    },
    "total": {
      "title": "Total",
      "type": "integer"
    },
    "truncated": {
      "title": "Truncated",
      "type": "boolean"
    }
  },
  "required": [
    "items",
    "total",
    "truncated",
    "start",
    "end"
  ],
  "title": "TimelineNoteList",
  "type": "object"
}
```

## TimelineNoteResponse

```json
{
  "properties": {
    "can_edit": {
      "title": "Can Edit",
      "type": "boolean"
    },
    "created_at": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Created At"
    },
    "created_by_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Created By Name"
    },
    "ends_on": {
      "title": "Ends On",
      "type": "string"
    },
    "id": {
      "title": "Id",
      "type": "string"
    },
    "project_id": {
      "title": "Project Id",
      "type": "string"
    },
    "starts_on": {
      "title": "Starts On",
      "type": "string"
    },
    "suite_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Suite Name"
    },
    "text": {
      "title": "Text",
      "type": "string"
    },
    "updated_at": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Updated At"
    }
  },
  "required": [
    "id",
    "project_id",
    "starts_on",
    "ends_on",
    "text",
    "can_edit"
  ],
  "title": "TimelineNoteResponse",
  "type": "object"
}
```

## TimelineNoteUpdate

```json
{
  "additionalProperties": false,
  "description": "Only the fields SENT are applied (``model_fields_set``).",
  "properties": {
    "ends_on": {
      "anyOf": [
        {
          "format": "date",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Ends On"
    },
    "starts_on": {
      "anyOf": [
        {
          "format": "date",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Starts On"
    },
    "suite_name": {
      "anyOf": [
        {
          "maxLength": 500,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Suite Name"
    },
    "text": {
      "anyOf": [
        {
          "maxLength": 280,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Text"
    }
  },
  "title": "TimelineNoteUpdate",
  "type": "object"
}
```

## TokenResponse

```json
{
  "properties": {
    "access_token": {
      "title": "Access Token",
      "type": "string"
    },
    "expires_in": {
      "title": "Expires In",
      "type": "integer"
    },
    "must_change_password": {
      "default": false,
      "title": "Must Change Password",
      "type": "boolean"
    },
    "refresh_token": {
      "title": "Refresh Token",
      "type": "string"
    },
    "token_type": {
      "default": "bearer",
      "title": "Token Type",
      "type": "string"
    }
  },
  "required": [
    "access_token",
    "refresh_token",
    "expires_in"
  ],
  "title": "TokenResponse",
  "type": "object"
}
```

## ToolsConfig

```json
{
  "additionalProperties": false,
  "properties": {
    "allowlist": {
      "items": {
        "type": "string"
      },
      "title": "Allowlist",
      "type": "array"
    }
  },
  "title": "ToolsConfig",
  "type": "object"
}
```

## TraceabilityGapsResponse

```json
{
  "properties": {
    "cases_without_requirements": {
      "items": {
        "$ref": "#/components/schemas/LibraryCaseRow"
      },
      "title": "Cases Without Requirements",
      "type": "array"
    },
    "cases_without_requirements_total": {
      "title": "Cases Without Requirements Total",
      "type": "integer"
    },
    "gaps": {
      "items": {
        "$ref": "#/components/schemas/RequirementGapRead"
      },
      "title": "Gaps",
      "type": "array"
    },
    "gaps_total": {
      "title": "Gaps Total",
      "type": "integer"
    },
    "page": {
      "title": "Page",
      "type": "integer"
    },
    "project_id": {
      "format": "uuid",
      "title": "Project Id",
      "type": "string"
    },
    "size": {
      "title": "Size",
      "type": "integer"
    },
    "summary": {
      "$ref": "#/components/schemas/RequirementCoverageSummary"
    }
  },
  "required": [
    "project_id",
    "summary",
    "gaps",
    "gaps_total",
    "page",
    "size",
    "cases_without_requirements_total",
    "cases_without_requirements"
  ],
  "title": "TraceabilityGapsResponse",
  "type": "object"
}
```

## TrackEventRequest

```json
{
  "properties": {
    "event_name": {
      "title": "Event Name",
      "type": "string"
    },
    "payload": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Payload"
    },
    "project_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Project Id"
    }
  },
  "required": [
    "event_name"
  ],
  "title": "TrackEventRequest",
  "type": "object"
}
```

## TriageStatusUpdate

```json
{
  "description": "Body of ``PUT /api/v1/me/assigned-failures/{id}/triage``.\n\n``status`` is validated against the ``TriageStatus`` enum at the\nservice layer (the regex form here keeps the OpenAPI schema readable\nwhile still rejecting arbitrary strings; we don't gain anything\nfrom using a Pydantic Enum directly because the service maps to the\ncanonical enum anyway).",
  "properties": {
    "notes": {
      "anyOf": [
        {
          "maxLength": 2000,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "description": "Free-form context. Typically a defect link for DEFECT_CREATED or a rationale for WONT_FIX / REVIEWED_APPROVED.",
      "title": "Notes"
    },
    "status": {
      "description": "New triage status. Any value other than PENDING_REVIEW drops the row from the assignee's /my-failures inbox.",
      "pattern": "^(PENDING_REVIEW|REVIEWED_APPROVED|DEFECT_CREATED|WONT_FIX|AUTOMATION_SCRIPT_ISSUE|FLAKY_TEST)$",
      "title": "Status",
      "type": "string"
    }
  },
  "required": [
    "status"
  ],
  "title": "TriageStatusUpdate",
  "type": "object"
}
```

## TriggerDeepRequest

```json
{
  "properties": {
    "evidence_sources": {
      "anyOf": [
        {
          "items": {
            "type": "string"
          },
          "maxItems": 64,
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Evidence Sources"
    },
    "failure_ids": {
      "anyOf": [
        {
          "items": {
            "format": "uuid",
            "type": "string"
          },
          "maxItems": 500,
          "minItems": 1,
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Failure Ids"
    },
    "max_failures_analyzed": {
      "anyOf": [
        {
          "minimum": 1.0,
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Max Failures Analyzed"
    },
    "mode": {
      "default": "deep",
      "title": "Mode",
      "type": "string"
    },
    "model_tier": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Model Tier"
    }
  },
  "title": "TriggerDeepRequest",
  "type": "object"
}
```

## TriggerDeepResponse

```json
{
  "properties": {
    "already_in_progress": {
      "default": false,
      "title": "Already In Progress",
      "type": "boolean"
    },
    "message": {
      "title": "Message",
      "type": "string"
    },
    "pipeline_run_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Pipeline Run Id"
    },
    "run_configuration": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/DeepRunConfigurationRead"
        },
        {
          "type": "null"
        }
      ]
    },
    "run_id": {
      "title": "Run Id",
      "type": "string"
    },
    "task_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Task Id"
    }
  },
  "required": [
    "message",
    "run_id"
  ],
  "title": "TriggerDeepResponse",
  "type": "object"
}
```

## TriggerPipelineRequest

```json
{
  "properties": {
    "test_run_id": {
      "format": "uuid",
      "title": "Test Run Id",
      "type": "string"
    },
    "workflow_id": {
      "anyOf": [
        {
          "maxLength": 80,
          "minLength": 3,
          "pattern": "^(?:wf\\.[a-z0-9_.-]+|offline|deep|live)$",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Workflow Id"
    },
    "workflow_version": {
      "anyOf": [
        {
          "minimum": 1.0,
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Workflow Version"
    }
  },
  "required": [
    "test_run_id"
  ],
  "title": "TriggerPipelineRequest",
  "type": "object"
}
```

## UIDismissalCreate

```json
{
  "description": "Dismiss a UI prompt for the authenticated user.\n\n``dismissal_key`` is validated against a closed allowlist in\n``services/ui_dismissal_service.KNOWN_DISMISSAL_KEYS`` rather than by a\nPydantic enum here: a strict enum over a ``String(100)`` column silently\n422s when the vocabularies drift, and the UI shows nothing at all.",
  "properties": {
    "dismissal_key": {
      "maxLength": 100,
      "minLength": 1,
      "title": "Dismissal Key",
      "type": "string"
    }
  },
  "required": [
    "dismissal_key"
  ],
  "title": "UIDismissalCreate",
  "type": "object"
}
```

## UIDismissalListResponse

```json
{
  "properties": {
    "dismissed": {
      "items": {
        "type": "string"
      },
      "title": "Dismissed",
      "type": "array"
    }
  },
  "title": "UIDismissalListResponse",
  "type": "object"
}
```

## UpdateProjectMemberRoleRequest

```json
{
  "properties": {
    "role": {
      "$ref": "#/components/schemas/UserRole"
    }
  },
  "required": [
    "role"
  ],
  "title": "UpdateProjectMemberRoleRequest",
  "type": "object"
}
```

## UpdateUserProfileRequest

```json
{
  "description": "Editable user attributes. Only non-None fields are applied.",
  "properties": {
    "email": {
      "anyOf": [
        {
          "format": "email",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Email"
    },
    "full_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Full Name"
    },
    "is_active": {
      "anyOf": [
        {
          "type": "boolean"
        },
        {
          "type": "null"
        }
      ],
      "title": "Is Active"
    },
    "role": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/UserRole"
        },
        {
          "type": "null"
        }
      ]
    },
    "username": {
      "anyOf": [
        {
          "maxLength": 50,
          "minLength": 3,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Username"
    }
  },
  "title": "UpdateUserProfileRequest",
  "type": "object"
}
```

## UpdateUserRoleRequest

```json
{
  "properties": {
    "role": {
      "$ref": "#/components/schemas/UserRole"
    }
  },
  "required": [
    "role"
  ],
  "title": "UpdateUserRoleRequest",
  "type": "object"
}
```

## UpdateUserStatusRequest

```json
{
  "properties": {
    "is_active": {
      "title": "Is Active",
      "type": "boolean"
    }
  },
  "required": [
    "is_active"
  ],
  "title": "UpdateUserStatusRequest",
  "type": "object"
}
```

## UploadStatusResponse

```json
{
  "description": "Async status of an uploaded report (GET /api/v1/ingest/uploads/{task_id}).\n\nstate: pending | parsing | ingesting | succeeded | failed.",
  "properties": {
    "error": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Error"
    },
    "progress": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Progress"
    },
    "result": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Result"
    },
    "run_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Run Id"
    },
    "state": {
      "title": "State",
      "type": "string"
    },
    "task_id": {
      "title": "Task Id",
      "type": "string"
    }
  },
  "required": [
    "task_id",
    "state"
  ],
  "title": "UploadStatusResponse",
  "type": "object"
}
```

## UserCreate

```json
{
  "properties": {
    "email": {
      "format": "email",
      "title": "Email",
      "type": "string"
    },
    "full_name": {
      "anyOf": [
        {
          "maxLength": 255,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Full Name"
    },
    "password": {
      "maxLength": 128,
      "minLength": 8,
      "title": "Password",
      "type": "string"
    },
    "username": {
      "maxLength": 50,
      "minLength": 3,
      "title": "Username",
      "type": "string"
    }
  },
  "required": [
    "email",
    "username",
    "password"
  ],
  "title": "UserCreate",
  "type": "object"
}
```

## UserListResponse

```json
{
  "properties": {
    "created_at": {
      "format": "date-time",
      "title": "Created At",
      "type": "string"
    },
    "email": {
      "title": "Email",
      "type": "string"
    },
    "full_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Full Name"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "is_active": {
      "title": "Is Active",
      "type": "boolean"
    },
    "role": {
      "$ref": "#/components/schemas/UserRole"
    },
    "username": {
      "title": "Username",
      "type": "string"
    }
  },
  "required": [
    "id",
    "email",
    "username",
    "role",
    "is_active",
    "created_at"
  ],
  "title": "UserListResponse",
  "type": "object"
}
```

## UserResponse

```json
{
  "properties": {
    "avatar_color": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Avatar Color"
    },
    "created_at": {
      "format": "date-time",
      "title": "Created At",
      "type": "string"
    },
    "email": {
      "title": "Email",
      "type": "string"
    },
    "full_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Full Name"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "is_active": {
      "title": "Is Active",
      "type": "boolean"
    },
    "must_change_password": {
      "default": false,
      "title": "Must Change Password",
      "type": "boolean"
    },
    "role": {
      "$ref": "#/components/schemas/UserRole"
    },
    "updated_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Updated At"
    },
    "username": {
      "title": "Username",
      "type": "string"
    }
  },
  "required": [
    "created_at",
    "id",
    "email",
    "username",
    "role",
    "is_active"
  ],
  "title": "UserResponse",
  "type": "object"
}
```

## UserRole

```json
{
  "enum": [
    "VIEWER",
    "TESTER",
    "QA_ENGINEER",
    "QA_LEAD",
    "ADMIN"
  ],
  "title": "UserRole",
  "type": "string"
}
```

## ValidationError

```json
{
  "properties": {
    "loc": {
      "items": {
        "anyOf": [
          {
            "type": "string"
          },
          {
            "type": "integer"
          }
        ]
      },
      "title": "Location",
      "type": "array"
    },
    "msg": {
      "title": "Message",
      "type": "string"
    },
    "type": {
      "title": "Error Type",
      "type": "string"
    }
  },
  "required": [
    "loc",
    "msg",
    "type"
  ],
  "title": "ValidationError",
  "type": "object"
}
```

## ValueMetricAssumptionsRead

```json
{
  "description": "GET/PUT response — the EFFECTIVE assumptions plus their source\n(``default`` = no row, ``custom`` = project row exists).",
  "properties": {
    "blocked_run_wait_minutes": {
      "title": "Blocked Run Wait Minutes",
      "type": "number"
    },
    "defect_filing_minutes": {
      "title": "Defect Filing Minutes",
      "type": "number"
    },
    "source": {
      "default": "default",
      "title": "Source",
      "type": "string"
    },
    "triage_minutes_per_failure": {
      "title": "Triage Minutes Per Failure",
      "type": "number"
    }
  },
  "required": [
    "triage_minutes_per_failure",
    "blocked_run_wait_minutes",
    "defect_filing_minutes"
  ],
  "title": "ValueMetricAssumptionsRead",
  "type": "object"
}
```

## ValueMetricAssumptionsWrite

```json
{
  "description": "PUT body for ``/projects/{id}/value-metrics/assumptions`` (US-12.1).\n\nAll fields optional — omitted fields keep their current (or default)\nvalue. Bounds: 0 < x <= 480 minutes; anything outside 422s.",
  "properties": {
    "blocked_run_wait_minutes": {
      "anyOf": [
        {
          "exclusiveMinimum": 0.0,
          "maximum": 480.0,
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Blocked Run Wait Minutes"
    },
    "defect_filing_minutes": {
      "anyOf": [
        {
          "exclusiveMinimum": 0.0,
          "maximum": 480.0,
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Defect Filing Minutes"
    },
    "triage_minutes_per_failure": {
      "anyOf": [
        {
          "exclusiveMinimum": 0.0,
          "maximum": 480.0,
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Triage Minutes Per Failure"
    }
  },
  "title": "ValueMetricAssumptionsWrite",
  "type": "object"
}
```

## WebVitalReport

```json
{
  "properties": {
    "delta": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Delta"
    },
    "name": {
      "title": "Name",
      "type": "string"
    },
    "rating": {
      "title": "Rating",
      "type": "string"
    },
    "url": {
      "title": "Url",
      "type": "string"
    },
    "value": {
      "title": "Value",
      "type": "number"
    }
  },
  "required": [
    "name",
    "value",
    "rating",
    "url"
  ],
  "title": "WebVitalReport",
  "type": "object"
}
```

## WebhookDeliveryRead

```json
{
  "properties": {
    "attempt_count": {
      "title": "Attempt Count",
      "type": "integer"
    },
    "created_at": {
      "format": "date-time",
      "title": "Created At",
      "type": "string"
    },
    "delivered_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Delivered At"
    },
    "error": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Error"
    },
    "event_payload": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Event Payload"
    },
    "event_type": {
      "title": "Event Type",
      "type": "string"
    },
    "http_status": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Http Status"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "response_preview": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Response Preview"
    },
    "status": {
      "title": "Status",
      "type": "string"
    },
    "subscription_id": {
      "format": "uuid",
      "title": "Subscription Id",
      "type": "string"
    }
  },
  "required": [
    "id",
    "subscription_id",
    "event_type",
    "status",
    "attempt_count",
    "created_at"
  ],
  "title": "WebhookDeliveryRead",
  "type": "object"
}
```

## WebhookDeliveryReplayResponse

```json
{
  "description": "Result of POST /webhooks/{sub_id}/deliveries/{delivery_id}/replay.",
  "properties": {
    "delivery_id": {
      "format": "uuid",
      "title": "Delivery Id",
      "type": "string"
    },
    "status": {
      "default": "PENDING",
      "title": "Status",
      "type": "string"
    }
  },
  "required": [
    "delivery_id"
  ],
  "title": "WebhookDeliveryReplayResponse",
  "type": "object"
}
```

## WebhookEventCatalogEntry

```json
{
  "properties": {
    "description": {
      "title": "Description",
      "type": "string"
    },
    "event_type": {
      "title": "Event Type",
      "type": "string"
    }
  },
  "required": [
    "event_type",
    "description"
  ],
  "title": "WebhookEventCatalogEntry",
  "type": "object"
}
```

## WebhookEventCatalogResponse

```json
{
  "properties": {
    "events": {
      "items": {
        "$ref": "#/components/schemas/WebhookEventCatalogEntry"
      },
      "title": "Events",
      "type": "array"
    }
  },
  "required": [
    "events"
  ],
  "title": "WebhookEventCatalogResponse",
  "type": "object"
}
```

## WebhookSubscriptionRead

```json
{
  "properties": {
    "breaker_threshold": {
      "default": 10,
      "title": "Breaker Threshold",
      "type": "integer"
    },
    "consecutive_failures": {
      "default": 0,
      "title": "Consecutive Failures",
      "type": "integer"
    },
    "created_at": {
      "format": "date-time",
      "title": "Created At",
      "type": "string"
    },
    "disabled_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Disabled At"
    },
    "disabled_reason": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Disabled Reason"
    },
    "enabled": {
      "title": "Enabled",
      "type": "boolean"
    },
    "events": {
      "items": {
        "type": "string"
      },
      "title": "Events",
      "type": "array"
    },
    "failure_count": {
      "title": "Failure Count",
      "type": "integer"
    },
    "has_secret": {
      "title": "Has Secret",
      "type": "boolean"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "last_delivered_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Last Delivered At"
    },
    "last_error": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Last Error"
    },
    "last_failure_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Last Failure At"
    },
    "max_retries": {
      "title": "Max Retries",
      "type": "integer"
    },
    "name": {
      "title": "Name",
      "type": "string"
    },
    "project_id": {
      "format": "uuid",
      "title": "Project Id",
      "type": "string"
    },
    "target_url": {
      "title": "Target Url",
      "type": "string"
    },
    "total_delivered": {
      "title": "Total Delivered",
      "type": "integer"
    },
    "updated_at": {
      "format": "date-time",
      "title": "Updated At",
      "type": "string"
    }
  },
  "required": [
    "id",
    "project_id",
    "name",
    "target_url",
    "events",
    "enabled",
    "has_secret",
    "max_retries",
    "failure_count",
    "total_delivered",
    "created_at",
    "updated_at"
  ],
  "title": "WebhookSubscriptionRead",
  "type": "object"
}
```

## WebhookSubscriptionWrite

```json
{
  "properties": {
    "breaker_threshold": {
      "anyOf": [
        {
          "maximum": 100.0,
          "minimum": 1.0,
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Breaker Threshold"
    },
    "enabled": {
      "default": true,
      "title": "Enabled",
      "type": "boolean"
    },
    "events": {
      "items": {
        "type": "string"
      },
      "minItems": 1,
      "title": "Events",
      "type": "array"
    },
    "max_retries": {
      "default": 5,
      "maximum": 10.0,
      "minimum": 0.0,
      "title": "Max Retries",
      "type": "integer"
    },
    "name": {
      "maxLength": 255,
      "minLength": 1,
      "title": "Name",
      "type": "string"
    },
    "secret": {
      "anyOf": [
        {
          "maxLength": 200,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Secret"
    },
    "target_url": {
      "maxLength": 1000,
      "minLength": 8,
      "pattern": "^https?://",
      "title": "Target Url",
      "type": "string"
    }
  },
  "required": [
    "name",
    "target_url",
    "events"
  ],
  "title": "WebhookSubscriptionWrite",
  "type": "object"
}
```

## WebhookTestResponse

```json
{
  "properties": {
    "latency_ms": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Latency Ms"
    },
    "message": {
      "title": "Message",
      "type": "string"
    },
    "status_code": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Status Code"
    },
    "success": {
      "title": "Success",
      "type": "boolean"
    }
  },
  "required": [
    "success",
    "message"
  ],
  "title": "WebhookTestResponse",
  "type": "object"
}
```

## WorkflowBodyV1

```json
{
  "additionalProperties": false,
  "properties": {
    "base": {
      "enum": [
        "offline",
        "deep",
        "live"
      ],
      "title": "Base",
      "type": "string"
    },
    "deadline_seconds": {
      "default": 1500,
      "maximum": 86400.0,
      "minimum": 1.0,
      "title": "Deadline Seconds",
      "type": "integer"
    },
    "description": {
      "anyOf": [
        {
          "maxLength": 4000,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Description"
    },
    "edges": {
      "items": {
        "$ref": "#/components/schemas/WorkflowEdgeV1"
      },
      "maxItems": 300,
      "title": "Edges",
      "type": "array"
    },
    "loops": {
      "items": {
        "$ref": "#/components/schemas/WorkflowLoopV1"
      },
      "maxItems": 50,
      "title": "Loops",
      "type": "array"
    },
    "name": {
      "maxLength": 120,
      "minLength": 1,
      "title": "Name",
      "type": "string"
    },
    "retry_policy": {
      "$ref": "#/components/schemas/WorkflowRetryPolicyV1"
    },
    "review_policy": {
      "default": "human_required",
      "enum": [
        "human_required",
        "human_required_plus_auto_reviewer"
      ],
      "title": "Review Policy",
      "type": "string"
    },
    "steps": {
      "items": {
        "$ref": "#/components/schemas/WorkflowStepV1"
      },
      "maxItems": 100,
      "minItems": 1,
      "title": "Steps",
      "type": "array"
    },
    "workflow_id": {
      "maxLength": 80,
      "minLength": 3,
      "pattern": "^(?:wf\\.[a-z0-9_.-]+|offline|deep|live)$",
      "title": "Workflow Id",
      "type": "string"
    }
  },
  "required": [
    "workflow_id",
    "name",
    "base",
    "steps"
  ],
  "title": "WorkflowBodyV1",
  "type": "object"
}
```

## WorkflowDecisionEvent

```json
{
  "properties": {
    "alternatives": {
      "anyOf": [
        {
          "items": {
            "type": "string"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Alternatives"
    },
    "at": {
      "title": "At",
      "type": "string"
    },
    "chosen": {
      "title": "Chosen",
      "type": "string"
    },
    "context": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Context"
    },
    "decision_point": {
      "title": "Decision Point",
      "type": "string"
    },
    "rationale": {
      "title": "Rationale",
      "type": "string"
    }
  },
  "required": [
    "at",
    "decision_point",
    "chosen",
    "rationale"
  ],
  "title": "WorkflowDecisionEvent",
  "type": "object"
}
```

## WorkflowDefaultRead

```json
{
  "properties": {
    "base": {
      "title": "Base",
      "type": "string"
    },
    "model_stages": {
      "items": {
        "type": "string"
      },
      "title": "Model Stages",
      "type": "array"
    },
    "reason": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Reason"
    },
    "resolved_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Resolved Name"
    },
    "resolved_ref": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Resolved Ref"
    },
    "source": {
      "title": "Source",
      "type": "string"
    },
    "state": {
      "title": "State",
      "type": "string"
    },
    "updated_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Updated At"
    },
    "updated_by": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Updated By"
    },
    "workflow_id": {
      "title": "Workflow Id",
      "type": "string"
    },
    "workflow_version": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Workflow Version"
    }
  },
  "required": [
    "base",
    "source",
    "workflow_id",
    "state"
  ],
  "title": "WorkflowDefaultRead",
  "type": "object"
}
```

## WorkflowDefaultUpdate

```json
{
  "properties": {
    "workflow_id": {
      "maxLength": 80,
      "minLength": 3,
      "pattern": "^wf\\.[a-z0-9_.-]+$",
      "title": "Workflow Id",
      "type": "string"
    },
    "workflow_version": {
      "anyOf": [
        {
          "minimum": 1.0,
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Workflow Version"
    }
  },
  "required": [
    "workflow_id"
  ],
  "title": "WorkflowDefaultUpdate",
  "type": "object"
}
```

## WorkflowDefaultsResponse

```json
{
  "properties": {
    "defaults": {
      "items": {
        "$ref": "#/components/schemas/WorkflowDefaultRead"
      },
      "title": "Defaults",
      "type": "array"
    },
    "project_id": {
      "format": "uuid",
      "title": "Project Id",
      "type": "string"
    }
  },
  "required": [
    "project_id",
    "defaults"
  ],
  "title": "WorkflowDefaultsResponse",
  "type": "object"
}
```

## WorkflowEdgeV1

```json
{
  "additionalProperties": false,
  "properties": {
    "from": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "items": {
            "type": "string"
          },
          "type": "array"
        }
      ],
      "title": "From"
    },
    "join": {
      "anyOf": [
        {
          "enum": [
            "all",
            "any"
          ],
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Join"
    },
    "to": {
      "maxLength": 80,
      "minLength": 1,
      "title": "To",
      "type": "string"
    },
    "when": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "When"
    }
  },
  "required": [
    "from",
    "to"
  ],
  "title": "WorkflowEdgeV1",
  "type": "object"
}
```

## WorkflowEvaluateV1

```json
{
  "additionalProperties": false,
  "properties": {
    "sample_limit": {
      "default": 20,
      "maximum": 100.0,
      "minimum": 20.0,
      "title": "Sample Limit",
      "type": "integer"
    },
    "version": {
      "anyOf": [
        {
          "minimum": 1.0,
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Version"
    }
  },
  "title": "WorkflowEvaluateV1",
  "type": "object"
}
```

## WorkflowForkV1

```json
{
  "additionalProperties": false,
  "properties": {
    "description": {
      "anyOf": [
        {
          "maxLength": 4000,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Description"
    },
    "name": {
      "maxLength": 120,
      "minLength": 1,
      "title": "Name",
      "type": "string"
    },
    "workflow_id": {
      "maxLength": 80,
      "minLength": 3,
      "pattern": "^wf\\.[a-z0-9_.-]+$",
      "title": "Workflow Id",
      "type": "string"
    }
  },
  "required": [
    "workflow_id",
    "name"
  ],
  "title": "WorkflowForkV1",
  "type": "object"
}
```

## WorkflowLoopV1

```json
{
  "additionalProperties": false,
  "properties": {
    "from": {
      "maxLength": 80,
      "minLength": 1,
      "title": "From",
      "type": "string"
    },
    "max_iterations": {
      "maximum": 100.0,
      "minimum": 1.0,
      "title": "Max Iterations",
      "type": "integer"
    },
    "to": {
      "maxLength": 80,
      "minLength": 1,
      "title": "To",
      "type": "string"
    },
    "when": {
      "additionalProperties": true,
      "title": "When",
      "type": "object"
    }
  },
  "required": [
    "from",
    "to",
    "when",
    "max_iterations"
  ],
  "title": "WorkflowLoopV1",
  "type": "object"
}
```

## WorkflowPublishV1

```json
{
  "additionalProperties": false,
  "properties": {
    "accept_regression": {
      "default": false,
      "title": "Accept Regression",
      "type": "boolean"
    },
    "definition_sha256": {
      "maxLength": 64,
      "minLength": 64,
      "pattern": "^[0-9a-f]{64}$",
      "title": "Definition Sha256",
      "type": "string"
    },
    "eval_manifest_checksum": {
      "anyOf": [
        {
          "maxLength": 64,
          "minLength": 64,
          "pattern": "^[0-9a-f]{64}$",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Eval Manifest Checksum"
    },
    "reason": {
      "anyOf": [
        {
          "maxLength": 2000,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Reason"
    },
    "version": {
      "minimum": 1.0,
      "title": "Version",
      "type": "integer"
    }
  },
  "required": [
    "version",
    "definition_sha256"
  ],
  "title": "WorkflowPublishV1",
  "type": "object"
}
```

## WorkflowRetryPolicyV1

```json
{
  "additionalProperties": false,
  "properties": {
    "base_seconds": {
      "default": 30,
      "maximum": 3600.0,
      "minimum": 0.0,
      "title": "Base Seconds",
      "type": "integer"
    },
    "cap_seconds": {
      "default": 600,
      "maximum": 86400.0,
      "minimum": 1.0,
      "title": "Cap Seconds",
      "type": "integer"
    },
    "max_attempts": {
      "default": 5,
      "maximum": 10.0,
      "minimum": 1.0,
      "title": "Max Attempts",
      "type": "integer"
    }
  },
  "title": "WorkflowRetryPolicyV1",
  "type": "object"
}
```

## WorkflowStepV1

```json
{
  "additionalProperties": false,
  "properties": {
    "agent_id": {
      "maxLength": 80,
      "minLength": 1,
      "title": "Agent Id",
      "type": "string"
    },
    "config_ref": {
      "anyOf": [
        {
          "maxLength": 80,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Config Ref"
    },
    "id": {
      "maxLength": 80,
      "minLength": 1,
      "pattern": "^[a-z][a-z0-9_]*$",
      "title": "Id",
      "type": "string"
    },
    "model": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Model"
    },
    "reviews": {
      "items": {
        "type": "string"
      },
      "maxItems": 64,
      "title": "Reviews",
      "type": "array"
    },
    "tools": {
      "items": {
        "type": "string"
      },
      "maxItems": 64,
      "title": "Tools",
      "type": "array"
    }
  },
  "required": [
    "id",
    "agent_id"
  ],
  "title": "WorkflowStepV1",
  "type": "object"
}
```

## WouldRefuseChannel

```json
{
  "properties": {
    "channel": {
      "title": "Channel",
      "type": "string"
    },
    "decisions": {
      "minimum": 0.0,
      "title": "Decisions",
      "type": "integer"
    }
  },
  "required": [
    "channel",
    "decisions"
  ],
  "title": "WouldRefuseChannel",
  "type": "object"
}
```

## WouldRefuseSummary

```json
{
  "description": "The ``ai_report.distribution_would_refuse`` audit rows of the last 30 days.\n\nAbsence is not health: ``measured`` is False when the audit read failed or\ntimed out, and then every number below is None (never 0).",
  "properties": {
    "by_channel": {
      "anyOf": [
        {
          "items": {
            "$ref": "#/components/schemas/WouldRefuseChannel"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "By Channel"
    },
    "decisions": {
      "anyOf": [
        {
          "minimum": 0.0,
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Decisions"
    },
    "measured": {
      "title": "Measured",
      "type": "boolean"
    },
    "runs": {
      "anyOf": [
        {
          "minimum": 0.0,
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Runs"
    },
    "since": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Since"
    },
    "window_days": {
      "default": 30,
      "title": "Window Days",
      "type": "integer"
    }
  },
  "required": [
    "measured"
  ],
  "title": "WouldRefuseSummary",
  "type": "object"
}
```

## _BudgetPatch

```json
{
  "additionalProperties": false,
  "properties": {
    "max_cost_usd_per_run": {
      "anyOf": [
        {
          "minimum": 0.0,
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Max Cost Usd Per Run"
    },
    "max_llm_calls_per_run": {
      "anyOf": [
        {
          "minimum": 0.0,
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Max Llm Calls Per Run"
    },
    "max_runs_per_day": {
      "anyOf": [
        {
          "minimum": 0.0,
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Max Runs Per Day"
    },
    "max_tokens_per_run": {
      "anyOf": [
        {
          "minimum": 0.0,
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Max Tokens Per Run"
    }
  },
  "title": "_BudgetPatch",
  "type": "object"
}
```

## _RetryPatch

```json
{
  "additionalProperties": false,
  "properties": {
    "max_attempts": {
      "minimum": 1.0,
      "title": "Max Attempts",
      "type": "integer"
    }
  },
  "required": [
    "max_attempts"
  ],
  "title": "_RetryPatch",
  "type": "object"
}
```

## _ReviewPatch

```json
{
  "additionalProperties": false,
  "properties": {
    "add_auto_reviewer": {
      "default": false,
      "title": "Add Auto Reviewer",
      "type": "boolean"
    }
  },
  "title": "_ReviewPatch",
  "type": "object"
}
```

## _ThresholdsPatch

```json
{
  "additionalProperties": false,
  "properties": {
    "max_failures_analyzed": {
      "minimum": 1.0,
      "title": "Max Failures Analyzed",
      "type": "integer"
    }
  },
  "required": [
    "max_failures_analyzed"
  ],
  "title": "_ThresholdsPatch",
  "type": "object"
}
```

## _TierPatch

```json
{
  "additionalProperties": false,
  "properties": {
    "tier": {
      "enum": [
        "auto",
        "deterministic",
        "slm",
        "llm"
      ],
      "title": "Tier",
      "type": "string"
    }
  },
  "required": [
    "tier"
  ],
  "title": "_TierPatch",
  "type": "object"
}
```

## _ToolsPatch

```json
{
  "additionalProperties": false,
  "properties": {
    "allowlist": {
      "items": {
        "type": "string"
      },
      "title": "Allowlist",
      "type": "array"
    }
  },
  "required": [
    "allowlist"
  ],
  "title": "_ToolsPatch",
  "type": "object"
}
```

## app__models__schemas__FeatureFlagUpdate

```json
{
  "description": "Partial update.\n\nOmitted fields keep their value. Explicit null clears project/role\nallow-lists; for the scalar fields null is ignored.",
  "properties": {
    "description": {
      "anyOf": [
        {
          "maxLength": 2000,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Description"
    },
    "enabled_global": {
      "anyOf": [
        {
          "type": "boolean"
        },
        {
          "type": "null"
        }
      ],
      "title": "Enabled Global"
    },
    "enabled_projects": {
      "anyOf": [
        {
          "items": {
            "format": "uuid",
            "type": "string"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Enabled Projects"
    },
    "enabled_roles": {
      "anyOf": [
        {
          "items": {
            "type": "string"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Enabled Roles"
    },
    "reason": {
      "anyOf": [
        {
          "maxLength": 500,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Reason"
    },
    "rollout_percent": {
      "anyOf": [
        {
          "maximum": 100.0,
          "minimum": 0.0,
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Rollout Percent"
    }
  },
  "title": "FeatureFlagUpdate",
  "type": "object"
}
```

## app__routers__app_settings__FeatureFlagUpdate

```json
{
  "properties": {
    "config": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Config"
    },
    "description": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Description"
    },
    "enabled": {
      "title": "Enabled",
      "type": "boolean"
    },
    "scope": {
      "default": "global",
      "title": "Scope",
      "type": "string"
    }
  },
  "required": [
    "enabled"
  ],
  "title": "FeatureFlagUpdate",
  "type": "object"
}
```
