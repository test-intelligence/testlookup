---
name: testlookup-cost-efficiency
description: Review TestLookup cost efficiency, unit economics, and budget controls. Use for ingestion, storage, reports, hosted model usage, agent budgets, and retention trade-offs.
---

# TestLookup Cost Efficiency

## Define the workload and units

- Choose the profile, workload, baseline and candidate revision, time window, and unit of useful completion. Examples include a persisted run, delivered report, or completed AI evaluation. Hold fixture shape, traffic, feature flags, and acceptance criteria constant for the comparison.
- Identify cost drivers such as compute, storage, queue operations, transfer, or hosted model requests and tokens. Read `docs/reference/api/llm-cost-budget.md` and `docs/operations/database-connection-budget.md` where relevant. Record the usage and rate sources, currency, and how shared or idle costs are allocated.

## Measure and verify the budget

- Divide attributable cost by completed units. Retain offered load, completed work, errors, and retries to explain the result. Separate observed charges from estimates and compare latency, output quality, and accepted work before drawing a conclusion.
- For a model budget change, inspect `backend/tests/services/test_llm_cost_budget.py` and the selected agent workflow. Verify the documented admission and settlement behaviour using a deterministic provider and a fixture budget.
- Report baseline and candidate cost per unit, cost drivers, coverage of the metering, and any configured budget result. Use [Performance](../testlookup-performance-quality/SKILL.md) for workload and latency comparison.
