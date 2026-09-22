/**
 * `/__primitives` — the UI-primitives gallery (VIZ-109). DEVELOPMENT BUILDS ONLY.
 *
 * Every VIZ-109 primitive in its main states, from fixed data: MultiSelect
 * (60 options with counts, a cap, a disabled option, a hostile label and a
 * 300-character one; 500 options, windowed; disabled; inside a scroll box),
 * ChipList, Skeleton, Breadcrumbs, SidePanel (both shapes; the non-modal one
 * reserves its width on <main> via `reserveSpaceIn`), the number
 * formatters and the download helper. It is the subject of
 * `tests/ci-e2e/primitives.spec.ts` (axe, keyboard paths, target sizes, phone
 * width) and fetches nothing, so it runs with no backend and no login.
 *
 * `App.tsx` guards both the import and the route with `import.meta.env.DEV`,
 * as it does for `/__charts`.
 *
 * `?theme=<id>` renders the page in one of the app's real themes, validated
 * against the theme registry exactly as the chart gallery does; the attribute
 * goes on `<html>` and the saved theme in the store is left alone.
 */
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { useSearchParams } from 'react-router-dom'
import Breadcrumbs from '@/components/ui/Breadcrumbs'
import ChipList, { type ChipItem } from '@/components/ui/Chip'
import MultiSelect from '@/components/ui/MultiSelect'
import SidePanel from '@/components/ui/SidePanel'
import Skeleton from '@/components/ui/Skeleton'
import { THEMES, type ThemeId } from '@/store/themeStore'
import { downloadBlob } from '@/utils/download'
import { formatCompact, formatNumber, formatPercent, truncateMiddle } from '@/utils/formatters'
import {
  BRANCH_OPTIONS,
  CHIP_LIMIT,
  INITIAL_CHIPS,
  LONG_LABEL,
  RELEASE_CAP,
  RELEASE_OPTIONS,
  SUITE_OPTIONS,
} from './primitivesFixtures'

const THEME_QUERY_PARAM = 'theme'

/** A registry id, or `null` for anything that is not one. */
function resolveTheme(requested: string | null): ThemeId | null {
  const match = THEMES.find((theme) => theme.id === requested)
  return match ? match.id : null
}

const FORMAT_ROWS: Array<[string, string]> = [
  ['formatNumber(1234567)', formatNumber(1234567)],
  ['formatNumber(null)', formatNumber(null)],
  ['formatPercent(87.25)', formatPercent(87.25)],
  ['formatPercent(0)', formatPercent(0)],
  ['formatPercent(null)', formatPercent(null)],
  ['formatCompact(1234567)', formatCompact(1234567)],
  ['formatCompact(1e21)', formatCompact(1e21)],
  ['truncateMiddle(long, 24)', truncateMiddle(LONG_LABEL, 24)],
]

function Section({ id, title, children }: { id: string; title: string; children: ReactNode }) {
  const headingId = `primitive-${id}-title`
  return (
    <section
      data-primitive={id}
      aria-labelledby={headingId}
      className="min-w-0 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-card)] p-4"
    >
      <h2 id={headingId} className="mb-3 text-sm font-medium">
        {title}
      </h2>
      {children}
    </section>
  )
}

const BUTTON =
  'inline-flex min-h-8 items-center rounded-md border border-[var(--color-border)] bg-[var(--color-bg-input)] px-3 text-sm text-[var(--color-text)] hover:bg-[var(--color-bg-hover)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--color-ring)]'

export default function PrimitivesPage() {
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

  const [releases, setReleases] = useState<string[]>([])
  const [suites, setSuites] = useState<string[]>([])
  const [scrolled, setScrolled] = useState<string[]>([])
  const [chips, setChips] = useState<ChipItem[]>(INITIAL_CHIPS)
  const [panel, setPanel] = useState<'none' | 'non-modal' | 'modal'>('none')
  const chipFallbackRef = useRef<HTMLButtonElement>(null)
  /** The page makes room for the non-modal side panel here instead of being covered by it. */
  const mainRef = useRef<HTMLElement>(null)

  return (
    <main
      ref={mainRef}
      data-testid="primitives-gallery"
      data-gallery-theme={theme ?? 'default'}
      className="min-h-screen bg-[var(--color-bg)] px-4 py-6 text-[var(--color-text)]"
    >
      <header className="mb-6">
        <h1 className="text-xl font-semibold">UI primitives (dev only)</h1>
        <p className="mt-1 text-sm text-[var(--color-text-secondary)]">
          VIZ-109 primitives from fixed data. Theme: <code>{theme ?? 'active theme'}</code>
          {theme === null && (
            <>
              {' '}
              — add <code>?theme=</code> one of {THEMES.map((t) => t.id).join(', ')}.
            </>
          )}
        </p>
      </header>

      <div className="flex flex-col gap-6">
        <Section id="multiselect" title="MultiSelect">
          <div className="flex flex-wrap items-start gap-3">
            <MultiSelect
              label="Release"
              options={RELEASE_OPTIONS}
              value={releases}
              onChange={setReleases}
              max={RELEASE_CAP}
              data-testid="ms-release"
            />
            <MultiSelect
              label="Suite"
              options={SUITE_OPTIONS}
              value={suites}
              onChange={setSuites}
              data-testid="ms-suite"
            />
            <MultiSelect
              label="Environment"
              options={[{ value: 'staging', label: 'staging' }]}
              value={[]}
              onChange={() => {}}
              disabled
              disabledReason="Pick a project first"
              data-testid="ms-disabled"
            />
          </div>
          <p className="mt-2 text-xs text-[var(--color-text-secondary)]" data-testid="ms-release-value">
            Selected releases: {releases.length ? releases.join(', ') : 'none'}
          </p>
          <div
            role="group"
            aria-label="MultiSelect inside a scroll container"
            tabIndex={0}
            className="mt-3 h-24 overflow-auto rounded border border-dashed border-[var(--color-border)] p-2"
          >
            <div className="h-48 pt-8">
              <MultiSelect
                label="Branch"
                options={BRANCH_OPTIONS}
                value={scrolled}
                onChange={setScrolled}
                data-testid="ms-scroll"
              />
            </div>
          </div>
        </Section>

        <Section id="chips" title="ChipList">
          <div className="flex flex-wrap items-center gap-2">
            <button ref={chipFallbackRef} type="button" className={BUTTON}>
              Filters
            </button>
            <button type="button" className={BUTTON} onClick={() => setChips(INITIAL_CHIPS)}>
              Reset chips
            </button>
          </div>
          <ChipList
            className="mt-3"
            items={chips}
            limit={CHIP_LIMIT}
            fallbackFocusRef={chipFallbackRef}
            onRemove={(id) => setChips((prev) => prev.filter((chip) => chip.id !== id))}
          />
        </Section>

        <Section id="skeleton" title="Skeleton">
          <div className="grid gap-4 sm:grid-cols-3">
            <Skeleton variant="line" lines={3} data-testid="skeleton-line" />
            <Skeleton variant="block" height={80} data-testid="skeleton-block" />
            <Skeleton variant="chart" height={160} data-testid="skeleton-chart" />
          </div>
        </Section>

        <Section id="breadcrumbs" title="Breadcrumbs">
          <Breadcrumbs
            items={[
              { label: 'Releases', to: '/__primitives?crumb=releases' },
              { label: 'R2 — payments platform quarterly release train', to: '/__primitives?crumb=r2' },
              { label: 'checkout-regression-nightly', to: '/__primitives?crumb=suite' },
              { label: 'test_checkout_applies_discount_code' },
            ]}
          />
        </Section>

        <Section id="side-panel" title="SidePanel">
          <div className="flex flex-wrap gap-2">
            <button type="button" className={BUTTON} onClick={() => setPanel('non-modal')}>
              Open side panel
            </button>
            <button type="button" className={BUTTON} onClick={() => setPanel('modal')}>
              Open modal side panel
            </button>
          </div>
          <SidePanel
            open={panel !== 'none'}
            modal={panel === 'modal'}
            // Wide screens: <main> is padded by the panel's width while it is
            // open, so the page reflows beside it (below 640 px it is modal).
            reserveSpaceIn={mainRef}
            title={panel === 'modal' ? 'Modal run details' : 'Run details'}
            onClose={() => setPanel('none')}
            footer={
              <button type="button" className={BUTTON} onClick={() => setPanel('none')}>
                Done
              </button>
            }
          >
            <p className="text-sm text-[var(--color-text-secondary)]">
              Run 42 · 1,234 tests · {formatPercent(87.25)} passed
            </p>
            <label className="mt-3 flex flex-col gap-1 text-sm">
              Note
              <input className="min-h-8 rounded-md border border-[var(--color-border)] bg-[var(--color-bg-input)] px-2" />
            </label>
          </SidePanel>
        </Section>

        <Section id="formatters" title="Formatters">
          <div role="group" aria-label="Formatter examples" tabIndex={0} className="max-w-full overflow-x-auto">
            <table className="text-sm">
              <thead>
                <tr>
                  <th scope="col" className="pr-4 text-left font-medium">Call</th>
                  <th scope="col" className="text-left font-medium">Output</th>
                </tr>
              </thead>
              <tbody>
                {FORMAT_ROWS.map(([call, output]) => (
                  <tr key={call}>
                    <td className="pr-4 font-mono text-xs">{call}</td>
                    <td className="font-mono text-xs" data-format-call={call}>
                      {output}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>

        <Section id="download" title="downloadBlob">
          <button
            type="button"
            className={BUTTON}
            onClick={() => downloadBlob(new Blob(['release,pass_rate\nR2,87.25\n'], { type: 'text/csv' }), 'R2: pass rate?.csv')}
          >
            Download sample CSV
          </button>
        </Section>
      </div>
    </main>
  )
}
