---
title: Add behaviour tests for the deep-investigation MCP tools
labels: [good first issue, mcp, tests]
---

## What

`mcp/tools/deep.py` registers four MCP tools: `trigger_deep_analysis` (lines 12-23), `get_pipeline_status` (lines 25-51), `get_failure_clusters` (lines 53-66) and `get_deep_findings` (lines 68-82). Their only tests are in `mcp/tests/test_mcp_server.py:216-231` (`class TestDeepTools`). Those tests read `deep.py` as text and check that the tool names and the `/deep-investigate/` path appear in it. Nothing calls the tools or checks what they return.

## Why it helps

These tools turn API responses into the Markdown an AI agent reads. Behaviour tests catch a wrong request path, a lost field, or a broken empty-list message. Text matching catches none of those.

## Where to start

- `mcp/tools/deep.py:12-82`. Details worth pinning: the request body `{"mode": "deep"}` (line 18), the `workflow_type` query parameter (line 33), the stage summary line (line 49), the 200-character cut on `representative_error` (line 65) and the 300-character cut on `root_cause_summary` (line 81).
- The client functions to fake: `get` at `mcp/client.py:132` and `post` at `mcp/client.py:147`.
- Test to copy: `mcp/tests/test_mcp_activity.py:23-50`. Its `_FakeMCP` class collects the registered tools, and `monkeypatch.setattr(client, "get", ...)` records each call.

## Done when

- [ ] A new `mcp/tests/test_mcp_deep.py` calls each of the four tools with a faked `client.get` / `client.post`, and checks:
  - the path, query parameters and body sent;
  - the empty-list messages of `get_failure_clusters` and `get_deep_findings`;
  - the truncation of long `representative_error` and `root_cause_summary` values;
  - that `get_pipeline_status` leaves out `Started`, `Completed`, `Error` and `Stages` lines when the response has no such fields.
- [ ] No change to `mcp/tools/deep.py`. Changes to the MCP tool surface need discussion first (CONTRIBUTING.md, "PRs that need discussion first"). If a test finds a bug, describe it in the PR.
- [ ] `pip install -r mcp/requirements.txt`, then `cd mcp && python -m pytest tests -q` passes (`mcp/README.md:394-398`).
- [ ] Commits are signed off (`git commit -s`), per CONTRIBUTING.md

## Size

About 1–2 hours; no Docker stack needed. Python 3.11.
