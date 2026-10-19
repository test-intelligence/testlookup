/**
 * `/__ux-primitives` — the UX redesign's primitives (P0). DEVELOPMENT BUILDS ONLY.
 *
 * Every new primitive in its main states, from fixed data: PageHeader v2
 * (help **?**, **⋯** overflow, tabs slot, compact), Tabs with `?tab=`,
 * RouteTabs, Disclosure (closed and open), StatusBanner in every state,
 * KpiStrip of compact MetricCards, WindowPicker; and the W29a UI standard's
 * reading surface (UIX-01/02/03/07/08/11): `buttons` (every `.btn-*`, md and
 * sm), `badges` (result StatusBadges, DecisionChips, severity chips, a
 * FailureCategoryMark per category, an avatar per tone) and `states` (the
 * empty, warning, compact, unavailable and loading looks). It fetches nothing,
 * so it runs with no backend and no login, and it is the subject of
 * `tests/visual/ux-primitives.visual.spec.ts`.
 *
 * `App.tsx` guards both the import and the route with `import.meta.env.DEV`,
 * as it does for `/__charts` and `/__primitives`. `?theme=<id>` renders it in
 * one of the app's real themes.
 */
import { useEffect, type ReactNode } from 'react'
import { useSearchParams } from 'react-router-dom'
import { CheckCircle2, Inbox, Trash2 } from 'lucide-react'
import FailureCategoryMark from '@/components/charts/FailureCategoryMark'
import { FAILURE_CATEGORY_PATTERNS, type FailureCategoryKey } from '@/components/charts/failureTaxonomy'
import { AVATAR_COLORS, type AvatarColorKey } from '@/components/ui/avatarColors'
import DataUnavailable from '@/components/ui/DataUnavailable'
import DecisionChip from '@/components/ui/DecisionChip'
import Disclosure from '@/components/ui/Disclosure'
import EmptyState from '@/components/ui/EmptyState'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { SEVERITY_ORDER, SEVERITY_STYLE } from '@/components/ui/severity'
import StatusBadge from '@/components/ui/StatusBadge'
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

// W29a: the standard's primitives, from fixed data.
const RESULT_STATUSES = ['PASSED', 'FAILED', 'BROKEN', 'SKIPPED', 'FLAKY'] as const
const DECISIONS = ['GO', 'CONDITIONAL_GO', 'NO_GO'] as const
const CATEGORY_WORDS: Readonly<Record<FailureCategoryKey, string>> = {
  product_bug: 'Product bug',
  infrastructure: 'Infrastructure',
  test_data: 'Test data',
  automation_defect: 'Automation defect',
  flaky: 'Flaky',
  unknown: 'Unknown',
  other: 'Other',
}
/** One avatar per tone of the map: neutral, danger, warning, success, info, flaky. */
const AVATAR_TONES: readonly { key: AvatarColorKey; initials: string }[] = [
  { key: 'slate', initials: 'AS' },
  { key: 'red', initials: 'RB' },
  { key: 'orange', initials: 'OK' },
  { key: 'teal', initials: 'TM' },
  { key: 'blue', initials: 'BL' },
  { key: 'violet', initials: 'VP' },
]
/** A fixed 503 for DataUnavailable (the shape an axios error carries). */
const UNAVAILABLE_503 = { response: { status: 503 } }
const noop = () => undefined

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

        <Section id="buttons" title="Buttons (8 px, one primary)">
          <div className="flex flex-col gap-3">
            <div data-button-size="md" className="flex flex-wrap items-center gap-2">
              <button type="button" className="btn-primary">Save changes</button>
              <button type="button" className="btn-secondary">Cancel</button>
              <button type="button" className="btn-ghost">Details</button>
              <button type="button" className="btn-danger inline-flex items-center gap-1.5">
                <Trash2 className="h-4 w-4" aria-hidden />
                Delete runs
              </button>
              <button type="button" className="btn-primary" disabled>
                Disabled
              </button>
            </div>
            <div data-button-size="sm" className="flex flex-wrap items-center gap-2">
              <button type="button" className="btn-primary btn-sm">Save</button>
              <button type="button" className="btn-secondary btn-sm">Cancel</button>
              <button type="button" className="btn-ghost btn-sm">Details</button>
              <button type="button" className="btn-danger btn-sm">Delete</button>
            </div>
          </div>
        </Section>

        <Section id="badges" title="Status, decisions, severity, categories, avatars">
          <div className="flex flex-col gap-3 text-sm">
            <div data-badge-row="status" className="flex flex-wrap items-center gap-2">
              {RESULT_STATUSES.map((status) => (
                <StatusBadge key={status} status={status} />
              ))}
            </div>
            <div data-badge-row="decision" className="flex flex-wrap items-center gap-2">
              {DECISIONS.map((decision) => (
                <DecisionChip key={decision} decision={decision} />
              ))}
            </div>
            <div data-badge-row="severity" className="flex flex-wrap items-center gap-2">
              {SEVERITY_ORDER.map((level) => {
                const style = SEVERITY_STYLE[level]
                return (
                  <span
                    key={level}
                    data-severity={level}
                    className="inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs font-semibold"
                    style={{ color: style.fg, background: style.bg, borderColor: style.bd }}
                  >
                    <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full" style={{ background: style.dot }} />
                    {level}
                  </span>
                )
              })}
            </div>
            <div data-badge-row="category" className="flex flex-wrap items-center gap-x-4 gap-y-2">
              {(Object.keys(FAILURE_CATEGORY_PATTERNS) as FailureCategoryKey[]).map((key) => (
                <span key={key} data-category={key} className="inline-flex items-center gap-1.5 text-[var(--color-text-secondary)]">
                  <FailureCategoryMark category={key} />
                  {CATEGORY_WORDS[key]}
                </span>
              ))}
            </div>
            <div data-badge-row="avatar" className="flex flex-wrap items-center gap-2">
              {AVATAR_TONES.map(({ key, initials }) => {
                const colour = AVATAR_COLORS[key]
                return (
                  <span
                    key={key}
                    data-avatar={key}
                    className="inline-flex h-8 w-8 items-center justify-center rounded-full text-xs font-semibold"
                    style={{ background: colour.bg, color: colour.fg }}
                  >
                    {initials}
                  </span>
                )
              })}
            </div>
          </div>
        </Section>

        <Section id="states" title="Loading, empty and error">
          <div className="grid grid-cols-2 gap-4">
            <div data-state="empty" className="rounded-md border border-[var(--color-border)]">
              <EmptyState
                icon={<Inbox className="h-8 w-8" aria-hidden />}
                title="No runs in this window"
                description="Widen the time window or clear the filters to see older runs."
              />
            </div>
            <div data-state="warning" className="rounded-md border border-[var(--color-border)]">
              <EmptyState
                tone="warning"
                icon={<Inbox className="h-8 w-8" aria-hidden />}
                title="Results are partial"
                description="Two of the five suites have not reported yet."
              />
            </div>
            <div data-state="compact" className="rounded-md border border-[var(--color-border)] px-4">
              <EmptyState
                compact
                title="No comments yet."
                action={<button type="button" className="btn-secondary btn-sm" onClick={noop}>Add one</button>}
              />
            </div>
            <div data-state="loading" className="flex items-center gap-2 rounded-md border border-[var(--color-border)] px-4 py-3 text-sm text-[var(--color-text-muted)]">
              <LoadingSpinner size="sm" />
              Loading…
            </div>
            <div data-state="unavailable" className="col-span-2 rounded-md border border-[var(--color-border)]">
              <DataUnavailable error={UNAVAILABLE_503} onRetry={noop} testId="ux-primitives-unavailable" />
            </div>
          </div>
        </Section>
      </div>
    </main>
  )
}
