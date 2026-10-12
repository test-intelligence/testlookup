# Reference generation and verification scope

[Documentation home](../README.md)

This page says what the generated reference under `docs/reference/` covers, what the documentation checks prove, and what they do not. The commands are in [documentation maintenance](maintenance.md).

## What is generated

`scripts/generate_reference.py` reads tracked source only. It imports the FastAPI application without entering its lifespan (a non-routable database URL, telemetry and metrics off, no database queries, worker dispatch or model calls) and writes:

- the OpenAPI document, the endpoint index and one page per API tag;
- every OpenAPI schema;
- every registered SQLAlchemy table with its fields and constraints (the data dictionary);
- settings with their source defaults, the migration list, and worker tasks, queues and schedules.

The generator prints its totals (operations, paths, schemas, tables, migrations, worker tasks). They describe the source tree, not a running installation.

## What the checks prove

| Check | Command | What a pass means |
|---|---|---|
| Reference is current | `python scripts/generate_reference.py --check` | Regeneration reproduces the committed text exactly, every generated file is tracked by Git, and there are no obsolete generated files. Settings are read in isolation from any local `.env` |
| Links and contracts | `python scripts/check_reference_docs.py` | Every local link, heading anchor and source-line reference in the maintained pages resolves; OpenAPI `$ref` targets exist; every operation appears exactly once in the index and the domain pages; every schema has a section in the schema page; the data dictionary's index matches its table sections; the API guide's ingest request and response examples validate against the real Pydantic models |
| Checker regression tests | `python -m pytest scripts/test_reference_docs.py` | The checker still rejects broken links and headings, invalid examples, duplicate operations, and stale or unexpected generated files |
| Diagrams | `npm run check` in `scripts/mermaid-check` | Every Mermaid block in tracked Markdown parses |
| Schema docs | `python scripts/gen_schema_docs.py --check` | Every declared table has an entry in the schema documentation |
| Image inventory | `python scripts/release/check_image_drift.py` | Compose, Kubernetes, OpenShift and the mirror scripts agree with `deploy/images.manifest.txt` |

CI runs the first three in the backend job and the diagram check in the docs workflow.

## What they do not prove

None of these checks start the application, apply migrations to a real database, deliver a queued task, call a model or an external connector, run a browser journey or carry production traffic. Narrative pages trace the principal workflows through routes, services, stores, workers and clients; they are not an exhaustive audit of every code path.

Some operations expose responses without a complete structured schema. Their domain pages say so under "Contract limit" and link to the handler, which remains the authority for those fields. Validate a specific environment with the [environment acceptance checklist](README.md#environment-acceptance-checklist).

## Known pitfalls the tooling guards against

- **Case-insensitive ignore rules.** The repository ignores `AGENTS.md`; on a case-insensitive checkout that also ignored a generated `agents.md`, so the page existed locally and was missing in CI. The Agents domain page is named `agent-operations.md`, and `--check` requires every generated output to be tracked.
- **Untracked link targets.** `docs/` is mostly ignored by Git, with the maintained folders allow-listed in `.gitignore`. A link to an ignored file works on the author's machine and breaks for everyone else; `backend/tests/regression/test_shipped_docs_link_to_tracked_files.py` checks the entry-point documents against `git ls-files`.
- **Pinned README content.** Regression tests require the README to name the exact local model tags `make dev-llm` pulls, all five role tiers, and every supported ingest format. Run `python -m pytest backend/tests/regression/test_dev_llm_model_pair.py backend/tests/regression/test_role_hierarchy_is_five_tiers.py backend/tests/regression/test_supported_formats_are_documented.py` after editing it.
