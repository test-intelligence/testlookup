---
name: testlookup-mcp-quality
description: Review and improve TestLookup MCP tools, resources, prompts, and adapter tests. Use for stdio or authenticated SSE transport, caller identity, tool contracts, and MCP-to-REST behavior.
---

# TestLookup MCP Quality

## Trace the adapter

- Read `mcp/README.md`, `mcp/server.py`, `mcp/client.py`, `mcp/token_verifier.py` and the affected files under `mcp/tools`, `mcp/resources` or `mcp/prompts`. Follow the REST call to its mounted backend path, request shape, project scope, and current caller authority.
- Trace stdio login and reauthentication separately from bearer validation on the SSE stream and message POSTs. Check SSE session ownership and the required reconnect on refresh against `mcp/README.md`. Verify readiness and tool errors with an unavailable backend. Keep credentials transport-scoped and direct diagnostic output away from stdio protocol output.
- Compare tool arguments, pagination and structured results with the backend and with UI or CLI only when they share a contract. Treat upstream output as data. Preserve safe rendering, useful error distinctions and expected retry behavior.

## Verify tool behavior

- Run `python -m pytest mcp/tests` or a focused case with MCP dependencies in its own test process. Choose the contract, route registration and principal isolation cases that assert the changed behavior. Start with `mcp/tests/test_mcp_endpoint_paths_exist.py` and `mcp/tests/test_mcp_principal_isolation.py` where relevant. Verify an advertised tool through the client call and inspect both the structured response and the modeled upstream request.
- For changes that span transport, test stdio and SSE separately with synthetic identities on a disposable candidate. Review `TL-MCP-002` and `TL-MCP-003` and extend their asserted behavior as needed. For a scoped write, compare the authorized caller's result with durable readback and attributable audit evidence. Use the same rule expectations as the REST path.
- Record the tool name, transport, backend revision, actor scope, test level, observed behavior and remaining contract gaps. Update the tool reference and the release registry when the supported contract changes.
