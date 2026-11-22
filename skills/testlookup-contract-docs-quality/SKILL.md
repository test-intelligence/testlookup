---
name: testlookup-contract-docs-quality
description: Review and improve TestLookup API, schema, architecture, and operator documentation verification. Use for generated references, shared visualization or SDK contracts, examples, and change-linked product guidance.
---

# TestLookup Contract and Documentation Quality

## Identify the source of truth

- Read `docs/maintainers/maintenance.md` and `docs/maintainers/verification.md`. Find the affected source in mounted routes, Pydantic schemas, SQL models, settings, migrations or worker declarations. For cross-client presentation data also inspect `contracts/viz` and the frontend, backend or MCP contract consumers.
- Run `python scripts/generate_reference.py --check` and `python scripts/check_reference_docs.py` in the documented dependency environment. Regenerate affected references with the documented generator and review their diff against the source. Check that new paths are tracked under the repository's documentation rules.
- Verify operator, client and in-app guide examples against the selected deployment profile and feature state. Use executable fixture examples when available. For changed links, check the targets and anchors at the candidate ref. Use `scripts/mermaid-check` for diagrams and inspect `.github/workflows/docs.yml` for the documentation validation lane.

## Establish observable proof

- Inspect backend, frontend, CLI, MCP and SDK usage where the changed contract is shared. Check field names, units, defaults and enum values, including older serialized data where it remains supported. Run `python -m pytest scripts/test_reference_docs.py` for changes to the checker.
- Record the candidate revision, source files, generated artifacts and validation results. For behavior claims link a matching runtime result from the applicable component or E2E skill. Keep generated-source consistency evidence and runtime acceptance evidence identifiable.
