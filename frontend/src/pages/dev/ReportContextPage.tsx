/**
 * `/__report-context` — the report chrome gallery (VIZ-301/302/303/304/305).
 * DEVELOPMENT BUILDS ONLY, like `/__charts` and `/__primitives`.
 *
 * Every edge case of the context header, filter bar, chips, filtered summary
 * and metrics strip from fixed data (`reportContextFixtures.ts`): unfiltered;
 * two releases + two suites; more than three suites (popover); more than
 * eight chips; All projects; unmeasured; huge counts; totals missing; an
 * ignored filter; archived + unattributed releases; loading. Each case keeps
 * its own selection in local state so chips can really be removed. It is the
 * subject of `tests/ci-e2e/report-context.spec.ts` and fetches nothing.
 *
 * `?theme=<id>` renders the page in one of the app's themes, validated
 * against the theme registry exactly as the other galleries do.
 */
import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { ChartAnnouncerProvider } from '@/components/charts/ChartAnnouncer'
import ReportChromeView from '@/components/reports/ReportChromeView'
import { THEMES, type ThemeId } from '@/store/themeStore'
import {
  GALLERY_CASES,
  GALLERY_DEFAULT_WINDOW,
  GALLERY_RELEASE_CAP,
  GALLERY_RELEASE_OPTIONS,
  GALLERY_SUITE_CAP,
  GALLERY_SUITE_OPTIONS,
  galleryReleaseLabel,
  type GalleryCase,
} from './reportContextFixtures'

const THEME_QUERY_PARAM = 'theme'

function resolveTheme(requested: string | null): ThemeId | null {
  const match = THEMES.find((theme) => theme.id === requested)
  return match ? match.id : null
}

function CaseSection({ fixture }: { fixture: GalleryCase }) {
  const [releaseIds, setReleaseIds] = useState(fixture.releaseIds)
  const [suiteNames, setSuiteNames] = useState(fixture.suiteNames)
  const [windowDays, setWindowDays] = useState(fixture.windowDays)
  const [notice, setNotice] = useState(fixture.droppedNotice ?? [])
  const headingId = `report-case-${fixture.id}-title`

  return (
    <section
      data-report-case={fixture.id}
      aria-labelledby={headingId}
      className="min-w-0 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)] p-4"
    >
      <h2 id={headingId} className="mb-3 text-sm font-medium">
        {fixture.title}
      </h2>
      <ReportChromeView
        meta={fixture.meta}
        allProjects={fixture.allProjects}
        loading={fixture.loading}
        bar={{
          releaseOptions: GALLERY_RELEASE_OPTIONS,
          suiteOptions: GALLERY_SUITE_OPTIONS,
          releaseIds,
          suiteNames,
          windowDays,
          defaultWindowDays: GALLERY_DEFAULT_WINDOW,
          releaseCap: GALLERY_RELEASE_CAP,
          suiteCap: GALLERY_SUITE_CAP,
          allProjects: fixture.allProjects,
          onReleaseChange: setReleaseIds,
          onSuiteChange: setSuiteNames,
          onWindowChange: setWindowDays,
          droppedNotice: notice,
          onDismissNotice: () => setNotice([]),
        }}
        chips={{
          releases: (fixture.allProjects ? [] : releaseIds).map((id) => ({ id, label: galleryReleaseLabel(id) })),
          suites: suiteNames,
          windowDays,
          defaultWindowDays: GALLERY_DEFAULT_WINDOW,
          onRemoveRelease: (id) => setReleaseIds((ids) => ids.filter((r) => r !== id)),
          onRemoveSuite: (name) => setSuiteNames((names) => names.filter((s) => s !== name)),
          onResetWindow: () => setWindowDays(GALLERY_DEFAULT_WINDOW),
          onClearAll: () => {
            setReleaseIds([])
            setSuiteNames([])
            setWindowDays(GALLERY_DEFAULT_WINDOW)
          },
        }}
        metrics={fixture.metrics}
      />
    </section>
  )
}

export default function ReportContextPage() {
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

  return (
    <ChartAnnouncerProvider>
      <main
        data-testid="report-context-gallery"
        data-gallery-theme={theme ?? 'default'}
        className="min-h-screen bg-[var(--color-bg)] px-4 py-6 text-[var(--color-text)]"
      >
        <header className="mb-6">
          <h1 className="text-xl font-semibold">Report context (dev only)</h1>
          <p className="mt-1 text-sm text-[var(--color-text-secondary)]">
            VIZ-301–305 report chrome from fixed data. Theme: <code>{theme ?? 'active theme'}</code>
          </p>
        </header>
        <div className="flex flex-col gap-6">
          {GALLERY_CASES.map((fixture) => (
            <CaseSection key={fixture.id} fixture={fixture} />
          ))}
        </div>
      </main>
    </ChartAnnouncerProvider>
  )
}
