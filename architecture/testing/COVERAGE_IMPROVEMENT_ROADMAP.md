# Coverage Improvement Roadmap

## Now — blocking, deterministic evidence

1. Keep frontend coverage above 60/55/51/61 and investigate every decrease.
2. Keep the three hermetic authentication journeys blocking in Chromium.
3. Hermetic pending/accept/reject review journeys and producer/reviewer
   separation are implemented. Notification redaction remains because its
   security authority is the backend distribution gate.
4. Add a service integration fixture spanning ingest, intelligence persistence,
   defect promotion and release decision with one correlation lineage.

Exit: GAP-01 has blocking coverage for auth, review, release and defect paths;
GAP-03 has one reproducible service-level proof.

## Next — risk-weighted expansion

1. Raise frontend branch/function floors only after tests cover the lifecycle,
   integration settings, RAG and analytics gaps; raise one point at a time.
2. Measure MCP and CLI separately, then ratchet each package without combining
   unlike codebases into one percentage.
3. Add axe and keyboard journeys for the four critical user paths.
4. Version live fixtures and emit a machine-readable manifest of build, data,
   scenario, result, trace and correlation IDs.
5. Add the Investigator narrative review subject and its full distribution
   matrix when the product story is implemented.

Exit: no P0 module below 70% branch coverage without a documented reason; all
critical requirements have an asserted negative case.

## Later — operational confidence

1. Run two-project isolation canaries in staging/production-safe synthetic data.
2. Schedule provider paired evals and drift review; never promote a tier from
   unit-test results.
3. Conduct quarterly broker/database/provider fault exercises and verify alerts
   both fire and clear.
4. Track escaped defects back to a requirement and missing test layer; add the
   smallest durable regression at that layer.

## Governance

- Coverage floors may increase independently; do not update all baselines in a
  single cleanup.
- New critical behavior needs a regression that fails when the behavior is
  reversed, plus authorization and boundary cases where relevant.
- Quarantined tests have an owner, issue and expiry. A retry is diagnostic, not
  evidence of stability.
- Review this map monthly and after each incident, new provider, new public
  status, new migration pattern, or new distribution channel.
