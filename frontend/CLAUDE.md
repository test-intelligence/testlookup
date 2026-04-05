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

### Error Handling

- `ErrorBoundary.tsx` catches React render errors → POSTs to `/api/v1/observability/frontend`
- `useWebVitals.ts` reports CLS, FID, LCP, FCP, TTFB, INP to backend
- `errorReporting.ts` installs `window.onerror` + `unhandledrejection` handlers
- Toast notifications via `react-hot-toast`: `toast.success(...)`, `toast.error(...)`

## Critical Rules

- **TypeScript strict mode is ON.** Avoid `any`; use `unknown` + type guards.
- **SWR for all data fetching.** Pages import hooks, never raw service calls.
- **Single Axios instance.** Never create a second one.
- **`api.ts` is the base.** All service files import from `services/api.ts`.
- **Zustand for global state only.** Project selection + auth. Everything else is local state.
- **`build_number` lives on `TestRun`, not `TestCase`.** Use `runId?.slice(0,8)` for breadcrumbs.
- **`ALL_PROJECTS_ID = "all"` must never be sent to the backend as a UUID.** Convert to `null` before API calls.

## SWR Refresh Intervals

| Data Type | Interval |
|-----------|----------|
| Runs | 15s |
| Dashboard summary | 30s |
| Trends | 60s |
| Analytics (flaky, coverage) | 120s |

## Build & Deploy

- Dev server: Vite on port 3000, proxies `/api` and `/webhooks` to `localhost:8000`
- Production: `npm run build` → `dist/` → served by nginx (see Dockerfile)
- Code splitting: Manual chunks for `vendor` (react/router), `charts` (recharts/d3), `ui` (lucide/radix)
