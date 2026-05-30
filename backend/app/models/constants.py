"""
Shared constants — single source of truth for cross-service configuration.

Any module that needs dimension metadata, score weights, or similar
domain constants should import from here rather than defining its own copy.
"""

# ── Risk dimension metadata ──────────────────────────────────────────────────
# Maps dimension key → (display label, weight).
# Weights MUST sum to 1.0 — enforced by the assertion below.

DIMENSION_METADATA: dict[str, tuple[str, float]] = {
    "user_impact":       ("User Impact",          0.25),
    "env_sensitivity":   ("Env Sensitivity",      0.10),
    "reproducibility":   ("Reproducibility",      0.15),
    "regression_likely": ("Regression Likely",     0.20),
    "hist_recurrence":   ("Hist. Recurrence",      0.10),
    "blast_radius":      ("Blast Radius",          0.15),
    "diagnosis_conf":    ("Diagnosis Confidence",  0.05),
}

_weight_sum = sum(w for _, w in DIMENSION_METADATA.values())
assert abs(_weight_sum - 1.0) < 0.01, (
    f"DIMENSION_METADATA weights must sum to 1.0, got {_weight_sum}"
)
