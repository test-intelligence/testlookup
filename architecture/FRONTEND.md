# Frontend — SPA architecture

> Companion to [README.md](./README.md). The React 18 + Vite + TypeScript SPA:
> its layering, state model, theme system, and the CI ratchets that hold the
> conventions in place. Verified against the implementation 2026-08-08; the
> theme system and the CI rules (§4, §5) re-verified 2026-10-18 (W29a).

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

Seven themes are CSS custom-property sets under `[data-theme="…"]` in
`src/index.css`: five dark ones (`signal`, the default, `console`, `slate`,
`ember`, `midnight`) and two light ones, `lab` and `paper` (W29a, OR-06:
"Soft light · low glare", a warm `#EAE8E2` page, `#F5F4F0` cards, softened
`#26292E` text, muted text at 4.5:1 or better on every surface and a `#2F5FB3`
accent, for long reading sessions). The registry is `THEMES` in
`store/themeStore.ts` (id, label, hint and the picker swatch); `THEME_IDS` lists
the ids in picker order and `LIGHT_THEMES` / `isLightTheme` name the light ones,
which every contrast test and the solid destructive button key on. A theme is a
`data-theme` value only: nothing is sent to the server. The chart export's
**Light background** always re-resolves on Lab's white card, whichever theme is
active (`EXPORT_LIGHT_THEME` in `components/charts/engines/echarts/exportSvg.ts`,
`LIGHT_THEME` in `lib/viz/chartImage.ts`): Paper is a screen theme.

Components consume **tokens**, never palette colours or literals:

| Family | Meaning |
|---|---|
| `--color-*` | surfaces, borders, text, accent, the primary button (`--color-btn-primary-*`) |
| `--status-passed\|failed\|broken\|skipped\|flaky` (+ `-bg` / `-bd` / `-soft`) | a test, run or suite RESULT only |
| `--status-<s>-ink` | status TEXT on its own tint (75 % hue + 25 % body text; 4.5:1 or better on the tint in every theme) |
| `--ui-danger\|warning\|success\|info` (+ `-bg` / `-bd` / `-ink`) | what a colour MEANS outside results (a destructive action, a warning, an info chip); aliases of the status hues and the accent |
| `--state-quarantined` (+ `-bd`), `--color-text-on-status` | a quarantine (the flaky hue, drawn as an outline); text ON a solid status fill |
| `--color-btn-danger-bg\|bd\|text\|hover` | the destructive button: the failed tint + white on the dark themes, solid failed + white on the light ones (a later `[data-theme="lab"], [data-theme="paper"]` rule) |
| `--gate-go\|conditional\|no-go` | the release decision (conditional is the warning hue, never skipped-gold) |
| `--chart-*` | the chart kit's series, ramps, grid and axis (`components/charts/tokens.ts`) |
| `--text-2xs` … `--text-4xl` | the type scale (`text-2xs`, 11 px, and `text-md`, 16 px, since W29a) |

The component classes sit on the tokens: `.btn-primary`, `.btn-secondary`,
`.btn-ghost` and `.btn-danger` share one 8 px shape (`rounded-md`), `.btn-sm`
is the 28 px size, and the `.badge-*` text reads the `-ink` tokens.

**One map per vocabulary, in one owner file**; a page imports it and never
re-declares it:

| Vocabulary | Owner file | What it gives |
|---|---|---|
| Failure category and kind | `components/charts/failureTaxonomy.ts` (re-exported by `failureGroups/categoryPatterns.ts`), `components/charts/FailureCategoryMark.tsx` | `taxonOf` (every vocabulary onto seven keys), `failureCategoryColor`, `failureKindColor`, `categorySliceStyles` (colour by category, never by rank); a category is a coloured MARK beside neutral text, never coloured text |
| Result status and gate decision | `components/ui/statusVocabulary.ts` | sentence-case `statusLabel` ("Failed"), `RESULT_STATUS_TOKEN`, `DECISION_LABEL` (**Go / Conditional / No-Go**) and `DECISION_TOKEN`; drawn by `StatusBadge`, `DecisionChip` and `StatusBanner` |
| Severity | `components/ui/severity.ts` | `severityLevel` (P0-P3 from every vocabulary) and `SEVERITY_STYLE` on the `--ui-*` tokens |
| Avatar colours | `components/ui/avatarColors.ts` | `avatarColor` (the stored keys) and `avatarColorForName`; initials are never white |
| Dates and numbers | `utils/formatters.ts` | a fixed `en-US` locale (`DATE_LOCALE`): `formatDay`, `formatMonthDay`, `formatWeekday`, `formatClock`, `formatCompactDateTime`, `formatNumber` |

Raw Tailwind palette classes (`text-emerald-400`, …), `text-` / `bg-` /
`border-` / `ring-white|black` and colour literals bypass the system and break a
theme. The `no-restricted-syntax` palette rule is at `error` (every Tailwind
hue on every colour utility, and white / black, in string and template literals
alike); a legitimate white or black (a toggle knob, a scrim, a QR code's quiet
zone) carries a one-line disable with its reason. The `/__ux-primitives`
gallery (`?theme=<id>`) shows the buttons, badges, chips and the loading /
empty / error states in any theme.

## 5. What CI enforces

- **Quality gates** (`scripts/quality_gate.py`): `frontend.swr-only-fetching`,
  `frontend.single-axios`, `frontend.all-projects-literal`,
  `frontend.clipboard-util` — the layering rules above are checks, not lore.
  The UI standard (W29a) adds six. Four are COUNT ratchets, where a file's
  count may only fall (baselines in
  `scripts/quality-gate-baselines/<guard>.counts.json`, lowered with
  `--update-baseline --only <guard>`): `frontend.type-scale` (arbitrary
  `text-[Npx]` sizes and numeric inline `fontSize`),
  `frontend.status-text-on-tint` (status text drawn on its own tint instead of
  the `-ink` token), `frontend.status-hue-semantics` (direct
  `var(--status-failed|broken)` reads instead of `--ui-*` or
  `RESULT_STATUS_TOKEN`) and `frontend.adhoc-spinners` (`animate-spin` outside
  `components/ui`). Two are ABSOLUTE rules at zero:
  `frontend.button-primitives` (a primary-coloured or white-on-tint button
  that is not `.btn-primary` / `.btn-danger`; toggle tracks exempt) and
  `frontend.load-error-wording` (a failed read says "Could not load {thing}.").
- **ESLint ratchets, all at `error` after their burn-downs**: the full
  react-hooks v7 React-Compiler set (purity, set-state-in-effect, refs,
  immutability, exhaustive-deps), `@typescript-eslint/no-explicit-any`,
  `no-non-null-assertion`, the palette rule (§4), and the date rule
  (`no-restricted-properties` over `src/pages/**` and `src/components/**`
  outside the chart kit: no `toLocale(Date|Time)?String`, `Intl.NumberFormat`
  or `Intl.DateTimeFormat`; format through `utils/formatters`). Promotion
  regressions (`*.promotion.test.ts`) pin rules at `error` so they can't
  quietly slip back.
- **`frontend/scripts/check-theme-tokens.mjs`**: every theme block parses; the
  dark themes share one set of status hues and each light theme keeps its own;
  retired colours stay retired; system fonts only; no colour literal in the
  chart kit, which stays behind its engine boundary; the picker swatches equal
  their theme's tokens; and since W29a: no white border and no `text-white` on
  a status / ui tint (rule 7), no `--cat-*` / `--kind-*` token (8), conditional
  never painted skipped-gold (9), no colour literal in a non-test source
  outside the swatches, the Mermaid fallbacks and the chart kit (10), every
  `var(--name)` defined by `index.css` (11), and the derived token families
  present, with the light override naming exactly the light themes (12). Every
  rule self-tests on planted violations first.
- **Contrast tests** (`src/test/chartTokens.test.ts`,
  `components/charts/tokens.contrast.test.ts`,
  `store/presentationStore.tokens.test.ts`, `src/test/themeLegibility.test.ts`)
  measure every theme from `index.css`, and the Playwright axe gate
  (`tests/lib/axe-gate.ts`, `ALL_THEMES`) audits every theme at every impact.
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
  export/import cost) plus a name in the entry's preload map (eager bytes). One measured
  case: +0.19 kB eager and 2-3.5 kB on the chart pages' first visit with no new code on them.
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
- Theme tokens: the palette ESLint rule (§4) and `frontend/scripts/check-theme-tokens.mjs`
- Build/deploy of the bundle: [DEPLOYMENT.md](./DEPLOYMENT.md)
