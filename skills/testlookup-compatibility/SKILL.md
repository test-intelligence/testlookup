---
name: testlookup-compatibility
description: Review TestLookup co-existence, interoperability, integrability, and backward compatibility. Use for API, event, SDK, CLI, MCP, report, and integration contract changes across supported versions.
---

# TestLookup Compatibility and Interoperability

## Identify supported version pairs

- Identify the producer, consumer, transport, and supported versions from the release baseline and current contracts. Read `docs/reference/api-index.md`, `contracts/viz`, or `sdk-vectors` for the affected surface. Include the data format and any shared runtime or dependency relevant to co-existence.
- Check changes to fields, defaults, enums, error semantics, pagination, wire encoding, and protocol negotiation. Trace how supported consumers handle absent fields, extra fields, or changed values. Derive the expected result for each supported version pair from the documented contract.

## Verify interoperability and co-existence

- Test the supported previous consumer with the candidate server for a changed server contract. Test the candidate consumer with an older server when the support policy promises that pairing. For adapters or presenters, compare the same fixture across the two actual endpoints.
- For focused evidence, check `backend/tests/regression/test_cli_and_mcp_wire_contract.py`, `backend/tests/integration/test_mcp_chart_data_parity_postgres.py`, or `backend/tests/test_activity_live_vocab_frontend_parity.py`. Use the integration skill for real handoffs with configured providers or fixture substitutes.
- When changes share an API, queue, datastore, or proxy, run both supported workloads with a matched control. Measure expected completion and the affected errors or latency budgets. Record the version matrix, contracts exercised, shared dependency, and any supported pair that still lacks evidence.
