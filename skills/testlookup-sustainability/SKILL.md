---
name: testlookup-sustainability
description: Review TestLookup environmental resource efficiency. Use for compute, energy, storage growth, transfers, idle capacity, and model workload at equivalent product outcomes.
---

# TestLookup Sustainability and Resource Efficiency

## Establish comparable measurement

- Select the completed unit, workload, profile, and baseline and candidate revisions. Hold result quality, retention, failure handling, and workload shape constant. Useful units include a persisted test run, published report, or completed model evaluation.
- Read `architecture/OBSERVABILITY.md`, `infra/monitoring`, and `docker-compose.monitoring.yml` for signal availability. Choose attributable measures such as CPU time, memory over time, storage retained over time, transferred bytes, and model processing usage. Include idle consumption and the window over which it is allocated.

## Evaluate resource impact

- Use a bounded representative fixture and record successful work, errors, and retries. Identify which signals are resource proxies and which were measured by an energy meter. Derive an emissions estimate only with attributable energy and an applicable location and time-based factor.
- Report comparable resource use per completion, absolute usage over the observation window, and uncertainty in any estimate. Check whether the candidate still meets the specified latency, availability, and retention requirements. Use [Cost efficiency](../testlookup-cost-efficiency/SKILL.md) when an economic measure is also needed.
