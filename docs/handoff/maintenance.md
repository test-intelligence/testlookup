# Documentation maintenance

[Documentation home](../README.md)

## Sources of truth

Use current code/migrations and tests for behavior; use running-instance policy/config only when documenting that specific deployment. Historical architecture proposals and marketing pages may describe earlier or future behavior. Every narrative claim should link to an implementation boundary or identify itself as inference/recommendation.

The generator imports FastAPI to produce OpenAPI and SQLAlchemy metadata without entering application lifespan. It sets a non-routable database URL, disables telemetry/metrics and uses no DB queries, worker dispatch or model calls. It scans tracked source for contracts, settings, migrations and client registrations. No real `.env` secrets are emitted. Install backend dependencies in a dedicated environment before running it.

```bash
python scripts/generate_handoff_reference.py
python scripts/generate_handoff_reference.py --check
python scripts/check_handoff_docs.py
python scripts/test_handoff_docs.py
python scripts/gen_schema_docs.py --check
python scripts/release/check_image_drift.py
python scripts/quality_gate.py
```

Generated files live under `docs/reference`. Do not hand-edit them; improve source contracts or the generator. `--check` compares regeneration, verifies that every generated reference is tracked by Git, and detects obsolete/unexpected files without rewriting. Stage newly generated pages before running it; a locally present ignored file is not available in a clean CI checkout. The Agents domain uses `agent-operations.md` to avoid the case-insensitive `AGENTS.md` ignore rule. Normal generation removes only obsolete files carrying the generator ownership banner; unexpected unowned files cause a failure and are retained. The inventory uses `git ls-files`, so add newly created source files to the index before regenerating when they should be included. Generator itself is excluded to avoid self-description churn.

`check_handoff_docs.py` validates maintained-suite local file links, Markdown heading fragments and source line bounds, JSON `$ref` targets in OpenAPI, endpoint/schema/table inventory consistency, and the actual request/response examples in the API guide. It includes the four top-level entrypoints and all dated review pages. External URL availability and historical deep-dive page contents are not validated. It does not prove every business rule or deploy a stack. Mermaid syntax is validated with the existing checker:

```bash
cd scripts/mermaid-check
npm ci
npm run check
```

This scans tracked Markdown. Stage new pages before checking so they are included. The documentation-only change does not require rerunning unrelated live/mutation deployments; select relevant checks based on changes.

The backend job in [CI](../../.github/workflows/ci.yml) now executes the generation check, documentation checker and tool regression suite as a blocking step using its installed backend dependencies.

## Update workflow

When a feature changes, update its narrative page and code links, regenerate contracts, examine the diff for unexpected API/model changes, run link/schema/diagram checks and record evidence. Update baseline SHA/counts only when the documented source baseline changes. Avoid making every doc paragraph a version claim; retain the central verification page.

Keep `docs/README.md` as the current handoff entrypoint and preserve dated reports. Add links from older top-level documents rather than deleting historical evidence. Use [wiki export](../wiki/README.md) to publish a single maintained source of pages; do not maintain independent contradictory copies in a wiki.
