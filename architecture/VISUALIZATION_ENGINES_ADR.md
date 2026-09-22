# ADR — Chart engines for the Visualization Upgrade

| | |
|---|---|
| **Status** | Accepted — 2026-09-21 |
| **Decision** | Keep **Recharts 3** for simple charts. Add **Apache ECharts 6**, registered per chart type on the canvas renderer, for dense charts. Use **three.js** for the single opt-in 3D scatter. Reject `echarts-gl`, Plotly and Highcharts. |
| **Applies to** | `frontend/` — every chart added by the Visualization Upgrade (stories `VIZ-*`) |
| **Supersedes** | — |

## Context

The Visualization Upgrade adds heatmaps, a treemap, scatter, Sankey and one 3D view to a
product that today draws everything with Recharts (SVG) or by hand. Three constraints
decide the engine choice, and none of them is about chart features:

1. **The security policy is fixed.** The effective Content-Security-Policy is the `<meta>`
   tag in `frontend/index.html`: `script-src 'self'` — no `'unsafe-eval'` — and no
   `worker-src`. nginx adds only `frame-ancestors 'none'`. The policy is pinned by
   `backend/tests/regression/test_frontend_security_headers.py`. Relaxing it to make a chart
   library work is not on the table.
2. **The first load is budgeted.** `frontend/scripts/check-bundle-budget.mjs` fails the build
   above 180 000 bytes gzip of eagerly loaded code and already forbids `recharts` in an eager
   chunk.
3. **The product runs air-gapped.** No CDN, no hosted service, permissive licences only.

## Evidence

A throw-away Vite 8 project was built and opened in Chromium twice: once with no policy,
once with the production CSP `<meta>` tag copied in and a `securitypolicyviolation`
listener attached.

| Candidate | Licence | Result under the production CSP | gzip cost (measured) |
|---|---|---|---|
| ECharts 6.1.0, canvas renderer | Apache-2.0 | **Works.** Heatmap, treemap, `getDataURL` PNG export and `renderToSVGString` all run; zero violations. | One barrel with 7 chart types and both renderers: **253 kB**. Registered per chart type: shared base **≈ 170 kB**, then heatmap 12.8 kB, treemap 10.3 kB, sankey 7.1 kB, scatter 15.3 kB. |
| echarts-gl 2.1.0 (3D for ECharts) | MIT | **Fails.** Renders with no policy; under the policy it throws `Invalid expression` with violation `script-src <- eval` — its WebGL layer generates code with `eval`/`new Function`. Its npm peer range (`echarts ^5.1.2 \|\| ^6.0.0`) was accurate and still not sufficient. | 127 kB |
| three.js 0.186.0 | MIT | **Works.** Zero violations; 5 000 points drawn in 44 ms; canvas `toDataURL` export works. | **131 kB** |
| Plotly.js | MIT | Not tested: multi-megabyte even as a partial bundle. | — |
| Highcharts | Commercial | Excluded on licence. Its accessibility module remains the benchmark to measure against. | — |

Two further findings from the same spike shape the rules below:

- **An ECharts tooltip `formatter` that returns a string is an HTML sink.** A category named
  `<img src=x onerror=…>` executed. The default formatter, a formatter returning a DOM node,
  and SVG export were all safe. Test, suite and release names come from ingested CI files,
  so they are attacker-influenced.
- **ECharts' generated `aria-label` is not useful** ("This is a chart with type Heat map. The
  first 10 items are…"). Its decal patterns are worth keeping; the text summary is ours to write.

The whole eager budget is 180 kB, so the ECharts base alone is the size of today's entire
first load.

## Decision

1. **Recharts stays** for line, area, bar, donut and gauge. It is already shipped, is SVG, and
   has keyboard navigation on by default.
2. **ECharts 6 is added for dense charts**, with these rules:
   - import from `echarts/core` and register **each chart type in the module that draws it**,
     never through one barrel;
   - canvas renderer only; the SVG renderer is imported solely inside an export path that
     needs vector output;
   - `echarts` and `zrender` join `recharts` in `MUST_BE_LAZY` in `check-bundle-budget.mjs`,
     and neither is ever named in `manualChunks` — naming a shared chunk gets it hoisted into
     the entry (`frontend/vite.config.ts` documents this for Recharts).
3. **three.js draws the one 3D view** (an opt-in scatter), as its own lazy chunk behind the
   `viz_three_d` flag. Axes, labels, picking and rotation are ours to build, which is why the
   scope is a scatter only and why a 2D equivalent is always the default.
4. **Tooltips return DOM nodes.** Every ECharts `formatter` is
   `formatter: domTooltipFormatter(…)` from `components/charts/tooltip.ts`. The guards below
   enforce it, and an end-to-end test that ingests a hostile test name covers it.
5. **The CSP does not change.** A library that needs `eval`, `blob:` workers or a remote
   resource is rejected rather than accommodated. This also rules out one ECharts API:
   **never register a map or geo type from a GeoJSON string.** `echarts.registerMap` with
   string GeoJSON is parsed by `GeoJSONResource` with `new Function`, which `script-src 'self'`
   blocks. If a map is ever needed, pass an already-parsed object that we build ourselves.

## Guards

`npm run check:theme` (`frontend/scripts/check-theme-tokens.mjs`, which calls
`frontend/scripts/chart-guard.mjs`) and `npm run check:bundle`
(`frontend/scripts/check-bundle-budget.mjs`) enforce the decisions above. CI and
`scripts/push_check.py` run both. Each one runs a self-test against planted violations before it
scans the real tree.

- **Import boundary.** A value import of `echarts`, `zrender`, `echarts-gl` or `three`, including
  deep paths such as `zrender/lib/core/util`, is allowed only under
  `frontend/src/components/charts/engines/`. `ENGINE_DIRS` in `chart-guard.mjs` is the one list of
  allowed directories. Side-effect imports, dynamic `import()`, `require` and re-exports count as
  value imports. `import type`, `export type` and `typeof import(…)` are erased at build time, so
  they are allowed everywhere. An import with only inline `type` names (`import { type X }`) is
  flagged: under `verbatimModuleSyntax` it compiles to `import {} from 'x'`, which keeps the
  module's side effects. The boundary keeps engine code in one directory, and it stops a partial
  import like `zrender/lib/core/util` from reaching the eager bundle, where it would carry none of
  the markers that `check-bundle-budget.mjs` looks for.
- **Formatter rule.** This rule runs on the TypeScript compiler's AST, not on regex. It scans the
  engine directory, `components/charts/**` and every file that imports an `engines/` module. It
  fails on:
  - a `formatter` or `*Formatter` key whose value is not a direct call to the imported
    `domTooltipFormatter`, whatever the key's form: identifier, string, computed, method,
    `async`, getter or class field;
  - `x.formatter = …` and `x['formatter'] = …`;
  - `x[k] = …` into an object whose name contains tooltip, label, legend or axis;
  - `Object.assign`, `Object.defineProperty(ies)` or `Reflect.set/defineProperty` into a
    tooltip, label or legend;
  - any other string literal that is a formatter key, as in
    `defineProperty(tip, 'formatter', …)` or `fromEntries`;
  - a spread into a tooltip, label or legend object;
  - a `tooltip` value the guard cannot see, such as one imported from another file;
  - `rich` (rich-text templates interpret `{a|…}`), unless the line or the line above has
    `// chart-guard-allow rich: <reason>`;
  - in the engine directory, a computed key that is not a literal;
  - a local declaration that shadows the helper.

  Recharts' JSX `formatter={…}` is not matched, because React escapes it. The source rule cannot
  see an option built from innocently named variables across several files, so a **runtime
  check** closes that gap: `engines/useEChart.ts`, the one place an option reaches the engine,
  calls `assertSafeChartOption` (in `tooltip.ts`) before `init` and before every `setOption`. It
  walks the whole option (own keys incl. non-enumerable, cycle-safe, depth ≤ 32) and refuses any
  `*formatter` key, in any case, whose value is not a function `domTooltipFormatter` produced
  (tracked in a `WeakSet`) — a string included. A refused option puts the chart in its error
  state; ECharts never sees it.
- **Bundle.** `echarts`, `zrender`, `recharts` and `three` must not be in an eagerly preloaded
  chunk. Each is detected by string literals that survive minification. three.js has two markers:
  `__THREE__`, which is a top-level side effect that tree-shaking may drop, and
  `WebGLRenderer: Context Lost.`, which is written from memory of three's source and has not been
  verified. **VIZ-508 must prove both markers with an eager-import build before anyone relies on
  them.** The lazy ECharts base chunk has a ceiling of 198 600 bytes gzip. No production route
  renders ECharts yet, so no build tests that ceiling, and the script's output says so. The script
  also prints the remaining eager headroom. On 2026-09-22 it measured 178 205 of 180 000 bytes,
  which leaves 1 795 bytes (1.0%).

## Consequences

- The first ECharts chart a user opens costs about 185 kB gzip once; nginx serves hashed
  assets `immutable`, so it is paid once per release. Each further chart type costs 7–15 kB.
- Two 2D engines must look like one. Both read the same token module and share one tooltip
  component; a gallery page renders the same fixture in both and is compared by screenshot.
- jsdom has no canvas. Unit tests mock the engine modules and assert the props handed to
  them, the way `TrendChart.test.tsx` already mocks Recharts; real rendering is asserted only
  in Playwright against the gallery.
- The 3D view was verified in Chromium only. Safari and Firefox must be checked before
  `viz_three_d` is enabled anywhere.

## How to re-run the evidence

Create an empty Vite project, `npm install echarts@6 echarts-gl@2 three`, copy the CSP `<meta>`
tag from `frontend/index.html` into its `index.html`, add
`document.addEventListener('securitypolicyviolation', …)`, render one `heatmap`, one
`scatter3D` (echarts-gl) and one three.js `Points` cloud behind dynamic imports, then
`vite build` and `vite preview`. The build output gives the gzip sizes; the browser console
gives the violations.

## Related

- `contracts/viz/README.md` — the data contracts the engines render.
- `architecture/FRONTEND.md` — the token system and the frontend ratchets.
