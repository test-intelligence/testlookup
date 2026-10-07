/**
 * `/__ux-primitives` — the UX redesign's primitives (P0). DEVELOPMENT BUILDS ONLY.
 *
 * Every new primitive in its main states, from fixed data: PageHeader v2
 * (help **?**, **⋯** overflow, tabs slot, compact), Tabs with `?tab=`,
 * RouteTabs, Disclosure (closed and open), StatusBanner in every state,
 * KpiStrip of compact MetricCards, WindowPicker. It fetches nothing, so it runs
 * with no backend and no login, and it is the subject of
 * `tests/visual/ux-primitives.visual.spec.ts`.
 *
 * `App.tsx` guards both the import and the route with `import.meta.env.DEV`,
 * as it does for `/__charts` and `/__primitives`. `?theme=<id>` renders it in
 * one of the app's real themes.
 */
import { useEffect, type ReactNode } from 'react'
import { useSearchParams } from 'react-router-dom'
import { CheckCircle2 } from 'lucide-react'
import Disclosure from '@/components/ui/Disclosure'
import KpiStrip from '@/components/ui/KpiStrip'
import MetricCard from '@/components/ui/MetricCard'
import PageHeader from '@/components/ui/PageHeader'
import RouteTabs from '@/components/ui/RouteTabs'
import StatusBanner, { type BannerState } from '@/components/ui/StatusBanner'
import Tabs from '@/components/ui/Tabs'
import { useTabParam } from '@/components/ui/useTabParam'
import WindowPicker from '@/components/ui/WindowPicker'
import { THEMES, type ThemeId } from '@/store/themeStore'

const THEME_QUERY_PARAM = 'theme'
const TAB_IDS = ['overview', 'failures', 'history'] as const
const BANNER_STATES: BannerState[] = ['go', 'conditional', 'no_go', 'pending', 'ok', 'warn', 'fail']

function resolveTheme(requested: string | null): ThemeId | null {
  return THEMES.find((theme) => theme.id === requested)?.id ?? null
}

function Section({ id, title, children }: { id: string; title: string; children: ReactNode }) {
  return (
    <section data-ux-primitive={id} className="min-w-0 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-card)] p-4">
      <h2 className="mb-3 text-sm font-medium">{title}</h2>
      {children}
    </section>
  )
}

/** A fixed sparkline for the compact KPI tiles (no chart engine). */
function Spark({ points }: { points: number[] }) {
  const max = Math.max(...points)
  const min = Math.min(...points)
  const d = points
    .map((v, i) => `${i === 0 ? 'M' : 'L'}${(i / (points.length - 1)) * 60},${18 - ((v - min) / (max - min || 1)) * 16}`)
    .join(' ')
  return (
    <svg aria-hidden="true" width="60" height="20" viewBox="0 0 60 20">
      <path d={d} fill="none" stroke="var(--color-accent)" strokeWidth="1.5" />
    </svg>
  )
}

export default function UxPrimitivesPage() {
  const [params] = useSearchParams()
  const theme = resolveTheme(params.get(THEME_QUERY_PARAM))
  useEffect(() => {
    if (theme === null) return
    const root = document.documentElement
    const previous = root.getAttribute('data-theme')
    root.setAttribute('data-theme', theme)
    return () => {
      if (previous === null) root.removeAttribute('data-theme')
      else root.setAttribute('data-theme', previous)
    }
  }, [theme])
  const [tab, setTab] = useTabParam(TAB_IDS, 'overview')

  return (
    <main data-testid="ux-primitives-gallery" className="min-h-screen bg-[var(--color-bg)] px-6 py-6 text-[var(--color-text)]">
      <header className="mb-6">
        <h1 className="text-xl font-semibold">UX redesign primitives (dev only)</h1>
        <p className="mt-1 text-sm text-[var(--color-text-secondary)]">
          P0 primitives from fixed data. Theme: <code>{theme ?? 'active theme'}</code>
        </p>
      </header>

      <div className="flex flex-col gap-6">
        <Section id="page-header" title="PageHeader v2">
          <PageHeader
            compact
            title="Failures"
            subtitle="What failed in the window, grouped by cause, with the tests that need attention first"
            helpTopic="failure-analysis"
            actions={<button type="button" className="btn-primary text-sm">Triage</button>}
            overflow={[
              { label: 'Export CSV', onClick: () => undefined },
              { label: 'Saved views', onClick: () => undefined },
              { label: 'Delete view', onClick: () => undefined, danger: true },
            ]}
            tabs={
              <Tabs
                ariaLabel="Failure views"
                value={tab}
                onChange={setTab}
                items={[
                  { id: 'overview', label: 'Overview' },
                  { id: 'failures', label: 'Failures', count: 12 },
                  { id: 'history', label: 'History', count: 0 },
                ]}
              />
            }
          />
          <p className="text-xs text-[var(--color-text-muted)]" data-current-tab={tab}>
            Selected tab: {tab}
          </p>
        </Section>

        <Section id="route-tabs" title="RouteTabs">
          <RouteTabs
            ariaLabel="Runs"
            items={[
              { to: '/__ux-primitives', label: 'History' },
              { to: '/live', label: 'Live' },
              { to: '/runs/compare', label: 'Compare' },
              { to: '/intelligence', label: 'AI verdicts', count: 3 },
            ]}
          />
        </Section>

        <Section id="status-banner" title="StatusBanner">
          <div className="flex flex-col gap-2">
            {BANNER_STATES.map((state) => (
              <StatusBanner
                key={state}
                state={state}
                title={state === 'no_go' ? 'Release 2.4' : undefined}
                facts={[
                  { label: 'pass', value: '83.4%' },
                  { label: 'new failures', value: 12 },
                  { label: 'based on', value: '41 runs' },
                ]}
                action={{ label: 'Open', href: '/release-gate' }}
              />
            ))}
          </div>
        </Section>

        <Section id="kpi-strip" title="KpiStrip (compact MetricCard)">
          <KpiStrip>
            <MetricCard compact title="Pass rate" icon={<CheckCircle2 />} metric={{ value: '91.2%', trend_direction: 'down', trend_text: '2.1 pp vs previous period' }} sparkline={<Spark points={[93, 92, 94, 91, 90, 92, 91]} />} />
            <MetricCard compact title="Runs" icon={null} metric={{ value: 412, trend_direction: 'up', trend_text: '8% vs previous period' }} positiveDirection="up" />
            <MetricCard compact title="New failures" icon={null} metric={{ value: 12, trend_direction: 'up', trend_text: '3 vs previous period' }} positiveDirection="down" />
            <MetricCard compact title="Flaky tests" icon={null} metric={{ value: 7, trend_direction: 'flat', trend_text: 'vs previous period' }} />
            <MetricCard compact title="Median duration" icon={null} metric={{ value: '4m 12s', trend_direction: 'none', trend_text: 'not measured last period' }} />
          </KpiStrip>
        </Section>

        <Section id="window-picker" title="WindowPicker">
          <WindowPicker />
        </Section>

        <Section id="disclosure" title="Disclosure">
          <div className="flex flex-col gap-2">
            <Disclosure title="How this was decided" summary="3 checks">
              <p className="text-sm">Hidden until opened.</p>
            </Disclosure>
            <Disclosure title="Pipeline" summary="6 stages" defaultOpen>
              <p className="text-sm">Ingested → analysed → classified → summarised → triaged → published.</p>
            </Disclosure>
          </div>
        </Section>
      </div>
    </main>
  )
}
