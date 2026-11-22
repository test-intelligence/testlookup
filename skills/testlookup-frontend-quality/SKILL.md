---
name: testlookup-frontend-quality
description: Review and improve TestLookup React and TypeScript UI tests. Use for routed features, role-aware controls, forms, charts, state changes, accessibility, and browser regressions.
---

# TestLookup Frontend Quality

## Trace the user journey

- Start at `frontend/src/App.tsx`, the routed page, shared components, timing/state hooks and the fetch wrapper in `frontend/src/services/api.ts`. Identify the expected role, selected project or release, flag state, URL and data source. Use the existing application router, shared controls and query conventions.
- Check data and mutation state across a refresh, route change, project switch and permission change. Inspect SWR keys, disabled queries, cache invalidation and late responses. Keep loading, empty, permission and failure states distinct. Verify that role-specific actions and their outcomes match API authority. Exercise a slow response from the previous project when a change affects scope or cache keys.
- For changed UI, exercise the real user path with stable accessible roles and labels. Assert meaningful content or persisted readback, plus the request/outcome when the boundary matters. Include keyboard operation and focus for interactive changes, and appropriate narrow or desktop viewport behavior.

## Run the right browser lane

- From `frontend`, use `npm run type-check` and focused `npm test -- <file>` for TypeScript and Vitest. For browser behavior with stable fixtures, use `npm run test:e2e:ci -- <spec>` under `playwright.ci.config.ts`. Use `frontend/tests/ci-e2e/accessibility-keyboard.spec.ts` as a repository example of keyboard verification.
- Inspect intercepted endpoints. Match explicit mock responses to their documented contracts and assert the intended request and visible result. Verify backend scope, asynchronous persistence and live freshness in the relevant integration or disposable candidate lane. For changes to charts or tables, cover the applicable empty, partial and numeric boundary states against the shared data contract.
- For intentional visual changes, inspect `frontend/playwright.visual.config.ts` and the image under the supported browser and viewport before updating a baseline. Retain a direct semantic assertion when correctness involves labels, totals or access.
- Record the spec, browser and role, fixture versus deployed layer, first-attempt result and regression IDs. Update the E2E coverage record for changed high-risk journeys.
