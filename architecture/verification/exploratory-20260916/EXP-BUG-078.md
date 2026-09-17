# EXP-BUG-078 — Root Cause ignored its failure-analysis limit

`max_failures_analyzed` was accepted and frozen but never consumed. Root Cause
now sorts by severity, analyzes only the configured count, records skipped IDs
and reports the stage as degraded. Regression and mutation evidence is in M11.
