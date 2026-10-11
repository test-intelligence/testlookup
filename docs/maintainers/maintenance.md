# Documentation maintenance

[Documentation home](../README.md)

## Sources of truth

Use current code, migrations and tests for behavior; use a running instance's policy or configuration only when documenting that specific deployment. Older proposals in `architecture/` may describe earlier or future behavior. Every narrative claim should link to an implementation boundary or say that it is an inference or recommendation.

The generator imports FastAPI to produce OpenAPI and SQLAlchemy metadata without entering the application lifespan. It sets a non-routable database URL, disables telemetry and metrics, and makes no database queries, worker dispatches or model calls. It reads settings, migrations and worker task declarations from tracked source. No real `.env` secrets are emitted. Install the backend dependencies in a dedicated environment before running it.

```bash
python scripts/generate_reference.py
python scripts/generate_reference.py --check
python scripts/check_reference_docs.py
python -m pytest scripts/test_reference_docs.py -q
python -m pytest backend/tests/regression/test_dev_llm_model_pair.py backend/tests/regression/test_role_hierarchy_is_five_tiers.py backend/tests/regression/test_supported_formats_are_documented.py backend/tests/regression/test_shipped_docs_link_to_tracked_files.py -q
python scripts/gen_schema_docs.py --check
python scripts/release/check_image_drift.py
python scripts/quality_gate.py
```

Generated files live under `docs/reference`. Do not hand-edit them; improve the source contracts or the generator. `--check` compares a regeneration, verifies that every generated reference is tracked by Git, and detects obsolete or unexpected files without rewriting anything. Stage newly generated pages before running it; a locally present ignored file is not available in a clean CI checkout. The Agents domain uses `agent-operations.md` to avoid the case-insensitive `AGENTS.md` ignore rule. Normal generation removes only obsolete files carrying the generator ownership banner; unexpected unowned files cause a failure and are kept, and files the checkout ignores are left alone. The migration and worker lists use `git ls-files`, so add a new migration or task module to the index before regenerating.

`check_reference_docs.py` validates local file links, Markdown heading fragments and source-line bounds, JSON `$ref` targets in OpenAPI, that every operation and schema appears in the generated pages, and the request and response examples in the API guide. It covers the top-level entry points (README, GETTING_STARTED, ARCHITECTURE, CONTRIBUTING, SECURITY and the other community files), `docs/README.md`, the maintained `docs/` folders, `user-guide/` and the component READMEs. External URL availability and the contents of the `architecture/` deep dives are not validated, and a pass does not prove business rules or deploy a stack. Mermaid syntax is validated with the existing checker:

```bash
cd scripts/mermaid-check
npm ci
npm run check
```

This scans tracked Markdown; stage new pages before checking so they are included. A documentation-only change does not need the application test suites; pick the checks that match what you changed.

The backend job in [CI](../../.github/workflows/ci.yml) runs the generation check, the documentation checker and the tool regression suite as a blocking step, and the docs workflow runs the Mermaid check on Markdown-only changes.

## Update workflow

When a feature changes, update its narrative page and code links, regenerate the contracts, examine the diff for unexpected API or model changes, and run the link, schema and diagram checks. Do not copy generated counts into narrative pages; link to the generated reference so nothing goes stale. Keep version-specific claims on the [verification page](verification.md) or in `CHANGELOG.md`.

Keep `docs/README.md` as the documentation index. Link only to files tracked by Git: most of `docs/` is ignored, with the maintained folders allow-listed in `.gitignore`, and `scripts/local-only-paths.txt` lists paths that must never be tracked. Publish one maintained source of pages; do not keep independent copies elsewhere that can contradict it.
