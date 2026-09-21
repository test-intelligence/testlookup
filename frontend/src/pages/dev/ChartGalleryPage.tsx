/**
 * `/__charts` — the chart gallery. DEVELOPMENT BUILDS ONLY.
 *
 * A deterministic page that renders every chart component in the kit, at a
 * fixed size, from fixed data, with animation off. It exists to be looked at
 * by machines: `tests/ci-e2e/chart-gallery.spec.ts` asserts the charts draw
 * (geometry, not text) and `tests/visual/chart-gallery.visual.spec.ts`
 * screenshots each item per theme. Nothing here fetches, so it runs with no
 * backend and no login.
 *
 * `App.tsx` guards both the import and the route with `import.meta.env.DEV`,
 * so a production build carries none of this — `npm run check:bundle` and a
 * grep of `dist/` for `data-gallery-item` are the proof.
 *
 * `?theme=<id>` renders the gallery in one of the app's real themes. The id is
 * checked against the theme registry; anything else leaves the active theme
 * alone. The attribute is set on `<html>` — where `themeStore` puts it, so the
 * derived tokens resolve exactly as they do in the app — but the STORE is not
 * touched: a screenshot run must not rewrite the developer's saved theme.
 */
import { useEffect } from 'react'
import { useSearchParams } from 'react-router-dom'
import TrendChart from '@/components/charts/TrendChart'
import DefectDonut from '@/components/charts/DefectDonut'
import PassRateGauge from '@/components/charts/PassRateGauge'
import { THEMES, type ThemeId } from '@/store/themeStore'
import { GALLERY_CANVAS, GALLERY_ITEMS, type GalleryItem } from './chartGalleryFixtures'

const THEME_QUERY_PARAM = 'theme'

/** A registry id, or `null` for anything that is not one. */
function resolveTheme(requested: string | null): ThemeId | null {
  const match = THEMES.find((theme) => theme.id === requested)
  return match ? match.id : null
}

function renderChart(item: GalleryItem) {
  switch (item.chart) {
    case 'trend':
      return (
        <TrendChart
          data={item.data}
          type={item.variant}
          height={GALLERY_CANVAS.height}
          animate={false}
        />
      )
    case 'donut':
      return <DefectDonut data={item.data} animate={false} />
    case 'gauge':
      // The gauge is square; fill the canvas height so its arc is a real size.
      return <PassRateGauge value={item.value} size={GALLERY_CANVAS.height} animate={false} />
  }
}

export default function ChartGalleryPage() {
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
    <main
      data-testid="chart-gallery"
      data-gallery-theme={theme ?? 'default'}
      className="min-h-screen bg-[var(--color-bg)] px-4 py-6 text-[var(--color-text)]"
    >
      <header className="mb-6">
        <h1 className="text-xl font-semibold">Chart gallery (dev only)</h1>
        <p className="mt-1 text-sm text-[var(--color-text-secondary)]">
          Every chart component from fixed data, {GALLERY_CANVAS.width}×{GALLERY_CANVAS.height}{' '}
          px, animation off. Theme: <code>{theme ?? 'active theme'}</code>
          {theme === null && (
            <>
              {' '}
              — add <code>?theme=</code> one of {THEMES.map((t) => t.id).join(', ')}.
            </>
          )}
        </p>
      </header>

      <div className="flex flex-col gap-6">
        {GALLERY_ITEMS.map((item) => {
          const headingId = `gallery-${item.id}-title`
          return (
            <section
              key={item.id}
              data-gallery-item={item.id}
              data-gallery-empty={item.empty ? 'true' : 'false'}
              aria-labelledby={headingId}
              className="w-fit max-w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-card)] p-4"
            >
              <h2 id={headingId} className="mb-3 text-sm font-medium">
                {item.title}
              </h2>
              {/*
                The canvas is a fixed 640px, wider than a phone. The page must
                not scroll sideways, so the canvas scrolls inside this box —
                and a scrollable box has to be reachable from the keyboard.
              */}
              <div
                role="group"
                aria-label={`${item.title} chart`}
                tabIndex={0}
                className="max-w-full overflow-x-auto"
              >
                <div
                  data-gallery-canvas={item.id}
                  style={{ width: GALLERY_CANVAS.width, height: GALLERY_CANVAS.height }}
                  className="flex items-center justify-center"
                >
                  {renderChart(item)}
                </div>
              </div>
            </section>
          )
        })}
      </div>
    </main>
  )
}
