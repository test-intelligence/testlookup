# CLAUDE.md — Frontend

This file provides guidance to Claude Code when working in the `frontend/` directory.

## Quick Reference

```bash
npm run dev          # Vite dev server on port 3000 (proxies /api to localhost:8000)
npm run build        # tsc + vite build → dist/
npm run lint         # eslint src
npm run format       # prettier --write src
npm run type-check   # tsc --noEmit
npm run test         # vitest run
npm run test:watch   # vitest (watch mode)
npm run test:e2e     # playwright test
```

## Path Alias

All imports use `@/` prefix mapped to `src/`:
```typescript
import { api } from '@/services/api'
import { useAuthStore } from '@/store/authStore'
import type { TestRun } from '@/types/runs'
```

Configured in `tsconfig.json` (`"@/*": ["src/*"]`) and `vite.config.ts` (`alias: { '@': path.resolve(__dirname, 'src') }`).

## Adding a New Feature (Full Stack Frontend)

### 1. Types in `src/types/feature.ts`

```typescript
export interface Feature {
  id: string
  name: string
  description: string | null
  created_at: string
}

export interface FeatureListResponse {
  items: Feature[]
  total: number
  page: number
  size: number
}
```

### 2. API Service in `src/services/featureService.ts`

Use the `getData`/`postData` wrappers from `http.ts`:
```typescript
import { getData, postData, patchData, deleteData } from './http'
import type { Feature, FeatureListResponse } from '@/types/feature'

export const featureService = {
  list: (projectId: string | null, params?: Record<string, unknown>) =>
    getData<FeatureListResponse>('/api/v1/features', {
      params: { ...(projectId ? { project_id: projectId } : {}), ...params },
    }),

  get: (id: string) =>
    getData<Feature>(`/api/v1/features/${id}`),

  create: (data: { name: string; description?: string }) =>
    postData<Feature>('/api/v1/features', data),

  update: (id: string, data: Partial<Feature>) =>
    patchData<Feature>(`/api/v1/features/${id}`, data),

  remove: (id: string) =>
    deleteData(`/api/v1/features/${id}`),
}
```

**Key pattern:** Conditional `project_id` param — spread `...(projectId ? { project_id: projectId } : {})`.

### 3. SWR Hook in `src/hooks/useFeature.ts`

```typescript
import useSWR, { mutate } from 'swr'
import { useProjectScopedSWR } from './useProjectScopedSWR'
import { featureService } from '@/services/featureService'

export function useFeatures(params?: Record<string, unknown>) {
  return useProjectScopedSWR(
    'features',
    (projectId) => featureService.list(projectId, params),
    { refreshInterval: 30_000 },
    [params],
  )
}

export function useFeature(id?: string) {
  return useSWR(
    id ? ['feature', id] : null,    // null key = don't fetch
    () => featureService.get(id!),
  )
}

export function refreshFeatures() {
  return mutate((key: unknown) => Array.isArray(key) && key[0] === 'features')
}
```

**`useProjectScopedSWR`** handles:
- Reads `activeProjectId` from `projectStore`
- Passes `null` to fetcher when `ALL_PROJECTS_ID` (backend omits project filter)
- Key includes `[baseKey, projectId, ...deps]` — auto-refetches on project switch
- Returns `null` key (disables fetch) when no project selected

### 4. Page in `src/pages/FeaturePage.tsx`

```typescript
import { useState } from 'react'
import { useProjectStore, ALL_PROJECTS_ID } from '@/store/projectStore'
import { useFeatures } from '@/hooks/useFeature'
import PageHeader from '@/components/ui/PageHeader'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import EmptyState from '@/components/ui/EmptyState'
import Pagination from '@/components/ui/Pagination'

export default function FeaturePage() {
  const [page, setPage] = useState(1)
  const project = useProjectStore(s => s.activeProject)
  const activeProjectId = useProjectStore(s => s.activeProjectId)
  const isAllProjects = activeProjectId === ALL_PROJECTS_ID

  const { data, isLoading } = useFeatures({ page, size: 20 })

  if (!project && !isAllProjects) return <EmptyState title="Select a project" />
  if (isLoading) return <LoadingSpinner size="lg" />

  return (
    <>
      <PageHeader title="Features" subtitle="..." />
      {/* Table / content */}
      <Pagination current={page} total={data?.total ?? 0} size={20} onChange={setPage} />
    </>
  )
}
```

### 5. Route in `src/App.tsx`

```typescript
const FeaturePage = lazy(() => import('./pages/FeaturePage'))

// Inside Routes:
<Route path="features" element={renderLazyRoute(FeaturePage)} />
```

### 6. Sidebar entry in `src/components/layout/Sidebar.tsx`

Add to the appropriate `NavGroup` in the `GROUPS` array (or `MANAGEMENT_GROUP` for admin pages).

Navigation sections: main nav (top) → AI Agents (middle) → Management (bottom, QA_LEAD+ only).

## Code Patterns (Exact)

### State Management

**Zustand stores** — only two global stores:
- `authStore.ts` — token, user, auth state. Persists `token` + `refreshToken` to localStorage.
- `projectStore.ts` — active project selection. Persists to localStorage.

Access via selectors:
```typescript
const user = useAuthStore(s => s.user)
const project = useProjectStore(s => s.activeProject)
const activeProjectId = useProjectStore(s => s.activeProjectId)
```

Per-page state stays local (`useState`). Do not add new Zustand stores without strong reason.

### All-Projects Sentinel

```typescript
import { ALL_PROJECTS_ID } from '@/store/projectStore'

const isAllProjects = activeProjectId === ALL_PROJECTS_ID
// When isAllProjects, fetcher receives null → backend returns cross-project data
```

### API Layer

Single Axios instance in `services/api.ts`:
- Request interceptor attaches JWT from `authStore`
- Response interceptor handles 401 → token refresh with queue
- Base URL: `VITE_API_BASE_URL` env var or `http://localhost:8000`
- Timeout: 120s

Never create a second Axios instance. All services import from `services/api.ts` or use `getData`/`postData` from `services/http.ts`.

### Permissions

```typescript
import { usePermissions } from '@/hooks/usePermissions'

const { isAdmin, canManageUsers, canAccessManagement, hasRole } = usePermissions()

// Guard a route:
if (!canAccessManagement) return <Navigate to="/overview" replace />
```

Role hierarchy matches backend: `VIEWER < TESTER < QA_ENGINEER < QA_LEAD < ADMIN`

### Styling

- **Tailwind CSS** with CSS custom properties for theming: `text-[var(--color-text)]`, `bg-[var(--color-bg-secondary)]`
- **Two dark themes**: "midnight" (GitHub-inspired, blue tints) and "classic" (deep navy, high contrast). Use CSS vars: `var(--color-bg)`, `var(--color-accent)`, `var(--color-border)`, etc. Never hardcode colors.
- **clsx** for conditional classes: `clsx('base-class', isActive && 'active', size === 'lg' && 'h-12')`
- **Icons:** `lucide-react` — import individual icons: `import { Settings, ChevronDown } from 'lucide-react'`
- **Responsive:** Use `md:` and `xl:` breakpoints. Grid: `grid-cols-2 xl:grid-cols-4`
- **Loading skeleton:** `<div className="h-8 w-24 bg-[var(--color-bg-secondary)] rounded animate-pulse" />`

### Component Patterns

- **PageHeader:** `<PageHeader title="..." subtitle="..." actions={<button>...</button>} />`
- **MetricCard:** Accepts `metric: { value, trend, trend_direction }` with auto trend icon
- **EmptyState:** `<EmptyState title="..." description="..." />`
- **Pagination:** `<Pagination current={page} total={total} size={size} onChange={setPage} />`
- **StatusBadge:** For test status display

### Analytics Widgets

Customizable dashboard visualizations using the analytics widget system:

```typescript
import { useAnalyticsView } from '@/hooks/useAnalyticsView'
import AnalyticsGrid from '@/components/analytics/AnalyticsGrid'
import AnalyticsWidget from '@/components/analytics/AnalyticsWidget'

// In a page component:
const { instances, addInstance, removeInstance, save, isDirty } = useAnalyticsView('dashboard')
```

Key files:
- `components/analytics/widgetRegistry.ts` — 30+ widget templates with `id`, `label`, `chartType`, `pages`, `defaultEnabled`
- `components/analytics/AnalyticsGrid.tsx` — responsive grid layout with drag-and-drop
- `components/analytics/WidgetPicker.tsx` — catalog browser (max 12 per page)
- `components/analytics/VisualizationConfigModal.tsx` — per-instance config (title, chart type, metric)
- `hooks/useAnalyticsView.ts` — manages widget state, saved views, dirty tracking, migration from legacy format

### Sortable Tables

```typescript
import { useTableSort } from '@/hooks/useTableSort'
import SortableHeader from '@/components/analytics/SortableHeader'

const { sortKey, sortDir, handleSort, sortedData } = useTableSort(data, 'name')
```

### RAG Components

- `components/rag/KnowledgeSourcePicker.tsx` — multi-select sources with sync status
- `components/rag/GenerationReviewPanel.tsx` — review generated test cases + citations
- `components/rag/CitationDrawer.tsx` — evidence drawer with chunk text, source, relevance

### Error Handling

- `ErrorBoundary.tsx` catches React render errors → POSTs to `/api/v1/observability/frontend`
- `useWebVitals.ts` reports CLS, FID, LCP, FCP, TTFB, INP to backend
- `errorReporting.ts` installs `window.onerror` + `unhandledrejection` handlers — with PII sanitization (strips file paths, redacts emails, Bearer tokens, API keys)
- Toast notifications via `react-hot-toast`: `toast.success(...)`, `toast.error(...)`

### Clipboard copy (HTTP-safe)

`navigator.clipboard.writeText` is gated to **secure contexts** (HTTPS or localhost). The homelab runs on HTTP, so calling it directly fails with a TypeError and looks like "Clipboard access denied" to the user. Use the shared utility:

```typescript
import { copyTextToClipboard } from '@/utils/clipboard'

const ok = await copyTextToClipboard(value)
if (ok) toast.success('Copied!')
else toast.error('Clipboard access denied — copy manually')
```

It tries `navigator.clipboard.writeText` first, falls back to a hidden-textarea `document.execCommand('copy')` on non-secure origins. Apply this everywhere — `navigator.clipboard` direct calls are a regression risk.

### Cross-page degraded mode

`useSystemHealth` (SWR poll of `/health/details` every 60s) returns `{data, unavailable: string[], isDegraded}`. `DegradedBanner.tsx` is mounted once in `AppLayout` between `<TopBar />` and `<main>` — it renders a yellow strip listing the unavailable dependencies (`ChromaDB`, `MongoDB`, …) when any one is `!= "ok"`. Pages that have a per-feature degraded path (Search, Run Intelligence) can read `useSystemHealth` themselves to render scoped notes, but the global banner already covers the cross-page case.

### Destructive controls — typed-name confirmation

`ProjectDataPage.tsx` is the reference. Two-step modal flow:
1. Click a red button in the "Danger zone" card.
2. A confirm modal opens listing what will be deleted vs kept. The Delete button stays disabled until the user types the project name **exactly** (case-sensitive). Backend re-validates the typed name; the frontend gate is a forcing function against autopilot clicks, not a security check.

Use this pattern for any future ADMIN destructive action.

## Critical Rules

- **TypeScript strict mode is ON.** Avoid `any`; use `unknown` + type guards.
- **SWR for all data fetching.** Pages import hooks, never raw service calls.
- **Single Axios instance.** Never create a second one.
- **`api.ts` is the base.** All service files import from `services/api.ts`.
- **Zustand for global state only.** Project selection + auth. Everything else is local state.
- **`build_number` lives on `TestRun`, not `TestCase`.** Use `runId?.slice(0,8)` for breadcrumbs.
- **`ALL_PROJECTS_ID = "all"` must never be sent to the backend as a UUID.** Convert to `null` before API calls.
- **Analytics widget max 12 per page.** `useAnalyticsView` enforces this limit. Legacy widget-ID format auto-migrates to `VisualizationInstance` on load.
- **Dev-only pages check `APP_ENV`.** Pages like `SeedDataPage.tsx` must render nothing in staging/production.
- **Client-side PII sanitization.** `errorReporting.ts` redacts sensitive data before sending to backend. Never skip this for error reports.
- **Avatar color palette.** 12 colors defined in `AVATAR_BG` map (slate, red, orange, amber, lime, emerald, teal, cyan, blue, violet, fuchsia, pink). Display via `TopBar.tsx` using the user's `avatar_color` from auth store.
- **Clipboard via `copyTextToClipboard`.** Never call `navigator.clipboard.writeText` directly — it fails on HTTP origins like the homelab. Use `@/utils/clipboard`. Tests `frontend/src/pages/SearchPage.test.tsx`-style mocks should mock the util.
- **Search chip counts have a no-query fallback.** `SearchPage` reads chip + Index Health counts from `response?.entity_counts ?? totalCounts` where `totalCounts` comes from `searchService.getEntityCounts(projectId)` fetched on mount. Without the fallback the chips render 0 before the user types anything; that's the regression `SearchPage.test.tsx` now pins.
- **`/agents` panels can show `failed` for stale runs.** The backend `routers/agents._apply_effective_status` overrides `running → failed` when a stage has failed or the pipeline is past 30 min with no completion. Don't add UI logic that re-overrides — trust what the API returns.

## SWR Refresh Intervals

| Data Type | Interval | Constant |
|-----------|----------|----------|
| Live execution | 5s | `REFRESH_INTERVALS.REALTIME` |
| Runs | 15s | `REFRESH_INTERVALS.ACTIVE` |
| Dashboard summary | 30s | `REFRESH_INTERVALS.POLLING` |
| Trends, analytics | 60s | `REFRESH_INTERVALS.BACKGROUND` |

Intervals defined in `config/refreshIntervals.ts`. Tab-visibility-aware polling pauses when the browser tab is hidden (see `hooks/usePageVisibility.ts`).

## Settings > AI Configuration Page

The `pages/settings/AIConfigPage.tsx` page allows ADMIN users to configure the analysis engine:

**Analysis Engine section** (radio button group):
- Auto / LLM (AI Agent) / Machine Learning / Rules-Based
- ML mode shows live status badge: "Not Trained" (amber) or "Ready (87% accuracy)" (green)
- Warning banner when ML selected but no model trained
- All inputs `disabled={!isAdmin}`

**Service types** (`services/appSettingsService.ts`):
- `AnalysisMode = 'llm' | 'ml' | 'rules' | 'auto'`
- `AIConfigRead` includes `analysis_mode`, `ml_model_available`, `ml_model_accuracy`, `ml_training_sample_count`
- `AIConfigUpdate` includes `analysis_mode` (validated server-side)

**Knowledge RAG section** (when `KNOWLEDGE_RAG_ENABLED`):
- Toggle to enable/disable RAG feature
- Status showing total sources, batches, chunks

**Pattern**: Radio buttons use the `ANALYSIS_MODES` const array with `value`, `label`, `desc` per option. Form state managed via local `useState<AIConfigUpdate>`, saved via `appSettingsService.updateAIConfig(form)`.

## Settings Pages

| Page | Path | Purpose |
|------|------|---------|
| AI Configuration | `/settings/ai` | Analysis mode, ML status, RAG toggle (ADMIN) |
| Profile | `/settings/profile` | Full name, avatar color, password change |
| API Keys | `/settings/api-keys` | Project-scoped API key generation + revoke |
| Project Data | `/settings/project-data` | Danger zone — reset the active project (delete test runs only, or full reset). ADMIN; typed-name confirmation |
| Seed Data | `/settings/seed-data` | Dev-only: load/reset/delete seed data (ADMIN) |

## Build & Deploy

- Dev server: Vite on port 3000, proxies `/api` and `/webhooks` to `localhost:8000`
- Production: `npm run build` → `dist/` → served by nginx (see Dockerfile)
- Code splitting: Manual chunks for `vendor` (react/router), `charts` (recharts/d3), `ui` (lucide/radix)
