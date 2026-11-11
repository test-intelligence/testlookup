---
title: Add a VS Code section to the agent cookbook's MCP setup
labels: [good first issue, docs, mcp]
---

## What

The agent cookbook's "Connecting your agent" section has setup steps for Claude Code, Claude Desktop and Cursor (`user-guide/agent-cookbook.md:16-42`), but not for VS Code. The MCP README only mentions VS Code in passing: its `.vscode/mcp.json` names the top-level key `servers` instead of `mcpServers` (`mcp/README.md:66`). A user who copies the Claude Code block into VS Code gets a config with the wrong key.

## Why it helps

VS Code is a common MCP client. CONTRIBUTING.md lists "New MCP client integration guides" among the PRs likely to land quickly.

## Where to start

- `user-guide/agent-cookbook.md:16-42`: the existing client sections. Match their length and tone.
- `mcp/README.md:44-66`: the stdio server entry and the note about `servers`.
- `mcp/README.md:9-26`: installing and starting the server from a clone (`pip install -r mcp/requirements.txt`, `python mcp/server.py`).
- Check VS Code's own MCP documentation for the current file location and schema. Do not rely on this issue for it.
- Two other places list the supported client setups and should name VS Code once the section exists: `user-guide/agent-cookbook.md:3` and `user-guide/cli-sdk-mcp.md:132`.

## Done when

- [ ] A `### VS Code` section after `### Cursor` in `user-guide/agent-cookbook.md`, with a complete `.vscode/mcp.json` example for the stdio server (absolute path to `mcp/server.py`, the `TESTLOOKUP_*` variables, a placeholder password) and one sentence on where VS Code shows the tools.
- [ ] You tried it in VS Code. The server is configured and its tools are listed. The server signs in to the backend only when a tool is called (`mcp/client.py:103-111`), so calling a tool needs a running TestLookup (for example `make dev`); say in the PR whether you did that too.
- [ ] `user-guide/agent-cookbook.md:3` and `user-guide/cli-sdk-mcp.md:132` mention VS Code.
- [ ] `python scripts/check_reference_docs.py` passes (install `backend/requirements.txt` first).
- [ ] Commits are signed off (`git commit -s`), per CONTRIBUTING.md

## Size

About 1–2 hours; no Docker stack needed unless you also test a tool call. Needs VS Code and Python 3.11 with `mcp/requirements.txt`.
