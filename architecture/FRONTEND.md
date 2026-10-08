# Frontend — SPA architecture

> Companion to [README.md](./README.md). The React 18 + Vite + TypeScript SPA:
> its layering, state model, theme system, and the CI ratchets that hold the
> conventions in place. Verified against the implementation 2026-08-08.

## 1. Layering

One direction of data flow, enforced by quality gates (§5):

```mermaid
flowchart LR
    P["pages/ (38 components, 62 routes)<br/>App.tsx owns the route table"] --> H["hooks/ (58)<br/>useSWR wrappers, one per data need"]
    H --> S["services/ (59)<br/>typed API modules"]
    S --> A["services/api.ts<br/>the ONE shared Axios instance"]
    A --> BE["backend REST API"]
    P --> Z["store/ (4 Zustand stores)<br/>client state only"]
```

- **Pages** are route-level components (`App.tsx` is the single route table —
  check it before editing a page; files get renamed).
- **Hooks** wrap `useSWR` and are the only way pages read server data. A
  page-level data need = a hook; hooks call services, never Axios directly.
- **Services** are typed API modules; every one imports the shared base from
  `services/api.ts` — no ad-hoc `axios.create` anywhere.
- **Reads via SWR, writes via service calls + `mutate`** — optimistic updates
  revalidate the affected SWR keys rather than duplicating server state.

## 2. The shared Axios instance (`services/api.ts`)

Both interceptors carry deliberate policy:

- **Request** — attaches auth; base URL is empty when `VITE_API_BASE_URL` is
  unset, making the production bundle deploy-target agnostic (same-origin
  relative URLs behind any ingress — see
  [DEPLOYMENT.md](./DEPLOYMENT.md)).
- **Response** — on 401, a **single-flight token refresh** (one refresh even
  when many requests fail together, with a queue that replays the rest); then
  the toast policy in `services/apiErrors.ts`: **422s toast** (a silent 422 was
  the historical "empty page, no error" footgun), 401/404 stay quiet, and
  FastAPI's array-form validation `detail` is flattened into a readable
  message instead of `[object Object]`.

## 3. Client state — Zustand, and only client state

Four stores, all cross-cutting *client* concerns — server data never lives in
Zustand:

| Store | Holds |
|---|---|
| `authStore` | the session user + token lifecycle |
| `projectStore` | the active project (`ALL_PROJECTS_ID` sentinel is a named constant — never inline the string) |
| `themeStore` | the selected theme |
| `timeWindowStore` | the **global shared time window** — one window across all analytics pages; each page snaps it to its allowed set (pages must agree on the canonical values or they drift each other) |

Selector discipline: components select **primitive slices**
(`useAuthStore(s => s.user?.role)`) so Zustand's `Object.is` equality prevents
re-render storms; object-returning selectors were a real bug class.

## 4. The theme system

Six themes (`signal` default, `midnight`, `slate`, `console`, `ember`, and
`lab` — the light one) are CSS custom-property sets under `[data-theme="…"]`
in `src/index.css`. Components consume **tokens**, never palette colors:
`--color-*` (surfaces/text/accent), `--status-passed|failed|broken|skipped|flaky`
(+ derived `-bg`/`-bd` via `color-mix`), and `--gate-go|conditional|no-go`.

Raw Tailwind palette classes (`text-emerald-400`, …) bypass the system and
break light themes — a `no-restricted-syntax` ESLint rule flags them (a
warn-ratchet being driven to zero; avatar-color swatches are exempt as user
data). The `.badge-*` primitives already sit on the tokens.

## 5. What CI enforces

- **Quality gates** (`scripts/quality_gate.py`): `frontend.swr-only-fetching`,
  `frontend.single-axios`, `frontend.all-projects-literal`,
  `frontend.clipboard-util` — the layering rules above are checks, not lore.
- **ESLint ratchets, all at `error` after their burn-downs**: the full
  react-hooks v7 React-Compiler set (purity, set-state-in-effect, refs,
  immutability, exhaustive-deps), `@typescript-eslint/no-explicit-any`, and
  `no-non-null-assertion`. The palette rule (§4) is the one remaining `warn`
  ratchet. Promotion regressions (`*.promotion.test.ts`) pin each rule at
  `error` so it can't quietly slip back.
- **`tsc --noEmit` strict** and a production `vite build` (vite 8 bundles with
  **rolldown** — `manualChunks` must be the function form).

## 6. Patterns worth copying (from the ratchet burn-downs)

- **Reset/derive-on-change**: track the previous value and adjust state
  *during render* (the React-endorsed pattern) instead of a `useEffect` —
  this is how the set-state-in-effect ratchet was cleared.
- **Fetch-on-mount** → an SWR hook + `mutate`, not an effect.
- **Narrowing over assertion**: guards/early-returns/`throw` instead of `x!`;
  in JSX, hoist the optional chain into a local so closures capture the
  narrowed value.
- **Stable identities**: wrap derived arrays (`data?.items ?? []`) in
  `useMemo` before using them as dependencies.
- Genuine DOM/network effects that must stay effects carry a **scoped,
  justified** `eslint-disable-next-line` — never a file-wide disable.

## 7. Chart sections and the first-visit bundle (Visualization Upgrade, Waves 2.6-3, Phase D)

- **No chart reads a flag.** The sections shipped behind `viz_chart_data_api`,
  `viz_advanced_charts` and `viz_three_d` through one seam (`useCatalogueRollout.ts`, held by
  `flagSeam.ratchet.test.ts`). Migration 0195 turned the three on everywhere, Phase D (S1-S5)
  removed every gate and flag-off path, and S6 deleted the seam and its ratchet: every chart
  section mounts unconditionally. Migration 0196 (F1) then deleted those three rows and the
  never-read `viz_customize`; `contracts/viz/flags.json` keeps all six as the record of what
  0192 seeded, marking the four `"retired_by": "0196"`.
- **No report scope beyond one release and one page suite.** The report-context panel
  (`viz_report_context`: header, filter bar, chips, metrics strip) and the multi-select
  release/suite filters (`viz_multi_filters`: their stores, `?release=&suites=&window=` URL
  sync, superseded-request aborting) stayed off and were deleted in Phase D (M0-M3); migration
  0197 retired their two rows, so no viz flag is left and `config/vizFlags.ts` is gone. The
  scope is the top-bar project and release (`useReleaseScope`: one id or `null`), the global
  window, and each page's own suite select (`usePageSuiteFilter`), which "Filter page by this"
  writes through `pageSuiteTarget`. Saved views (`SavedViewsMenu`, `useReportViewsMenu`) keep
  the report-route registry (`reportRoutes.ts`).
- **A page change is one import and one mount** of a small static composite
  (`CoverageAdvanced`, `SuiteDetailAdvanced`; the Failures page lazy-loads each section
  in its own tab since UX redesign P3) that holds only ONE
  `lazy(import())` of a `*Sections` module. Why: a `lazy(import())` writes the imported
  chunk's whole preload list into the importing chunk, and anything a composite imports
  statically (`LazySection`, `SectionErrorBoundary`) lands in every first visit of the page.
  Each section sits in its own `LazySection`.
- **Section-only code stays out of page closures.** `components/charts/sectionOnlyModules.test.ts`
  walks each page's static value imports and fails on an edge into a section-only module
  (the chart request queue, the catalogue models, mark activation, the drill URL, the rows
  panel). The fix is a leaf module or injection, not an exception: `ChartCursor` and
  `BarChart` take mark activation from their host (`markKit`), because a kit import there
  would ship it to every page that draws a bar.
- **New shared consumers split shared chunks.** rolldown groups modules by the set of chunks
  that reach them; a new lazy section that uses PART of an existing shared chart chunk splits
  it into more files, and every extra file costs every page that loaded it (a gzip-per-file and
  export/import cost) plus a name in the entry's preload map (eager bytes). Wave 3 measured
  this: +0.19 kB eager and 2-3.5 kB on the chart pages' first visit with no new code on them.
  Measure with `vite build --sourcemap` and compare page closures by module before adding a
  lazy consumer of the chart kit.
- **Drill state lives in the URL** (`hooks/useDrillPath.ts`, C5 in `contracts/viz/README.md`):
  `drill=` levels and the open rows panel `rows=by~<section id>&rows=<dimension>~<value>`; a
  host opens its panel only for its own owner tag (`ownedRows`). It is never written into a
  saved view.
- **d3 is confined** to `components/charts/failureGroups/**`
  (`d3Confinement.ratchet.test.ts`); ECharts to `components/charts/engines/**` (`chart-guard`).

## Related docs

- Route-level UX behaviors users see: [user-guide/](../user-guide/README.md)
- Theme-token remediation plan: `design_handoff_ui_improvements/` (local) + the palette ratchet in the auto-routine
- Build/deploy of the bundle: [DEPLOYMENT.md](./DEPLOYMENT.md)
