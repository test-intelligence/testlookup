# EXP-BUG-073 — Runtime tools and cached evidence bypassed the allowlist

Root Cause ReAct exposed every tool and could reuse cached output produced with
broader grants. Runtime tool construction and cache eligibility now follow the
frozen allowlist; other specialist tool calls are gated at their call sites.
Regression and mutation evidence is in M11.
