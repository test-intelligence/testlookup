/**
 * "Export" (VIZ-606): one chart as a PNG or SVG image, or its data as CSV,
 * with the scope stamped on every file (see `lib/viz/chartExport.ts`).
 *
 * A menu button (WAI-ARIA APG "menu button"): the trigger says "Export" in
 * words, carries `aria-haspopup="menu"` / `aria-expanded`; opening moves focus
 * to the first item, the arrow keys (and Home / End) move between items,
 * Escape closes and returns focus to the trigger, Tab closes and lets focus
 * move on, and a click outside closes. "Light background" is a
 * `menuitemcheckbox`: it changes how the images are drawn and leaves the menu
 * open.
 *
 * The menu is rendered INLINE, under the trigger — no portal to
 * `document.body` — so it still appears when the frame is in full screen
 * (VIZ-608), where only the full-screen element's subtree is drawn.
 *
 * The export work runs on the click, never on render, and the image code is a
 * dynamic `import()`: nothing of it is downloaded until someone exports. A
 * failure is shown as text under the trigger (and said once through the
 * page's announcer), never thrown into React: an export that fails must not
 * take the chart with it.
 *
 * ECharts draws on a canvas, so its image comes from the engine and is drawn
 * in the page's current colours; "Light background" is offered only for the
 * SVG-drawn (Recharts) charts, and says so rather than exporting light text
 * on a light page.
 */
import { useEffect, useId, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from 'react'
import { ChevronDown, Loader2 } from 'lucide-react'
import type { ChartSeries } from '@/lib/viz/contracts'
import { buildChartCsv, EXPORT_PIXEL_RATIO, exportFilename, type ChartProvenance } from '@/lib/viz/chartExport'
import { csvBlob } from '@/lib/viz/csv'
import { useAppVersion } from '@/lib/viz/useAppVersion'
import { downloadBlob } from '@/utils/download'
import { useChartAnnouncer } from './ChartAnnouncer'
import { useChartFullscreen } from './chartFrameContext'
import type { ChartAxes, SeriesFormat } from './chartText'

export interface ChartExportMenuProps {
  title: string
  /** The `<chart>` part of the file name. Default: a slug of `title`. */
  chartSlug?: string
  /** The chart body element (holds the Recharts `<svg>` or the ECharts container). */
  getBody: () => HTMLElement | null
  series: ChartSeries
  /**
   * Accepted so a frame can hand over what it gives the table view. The CSV
   * deliberately writes RAW values, not formatted ones (see `chartExport.ts`),
   * so it is not read today.
   */
  format?: SeriesFormat
  axes?: ChartAxes
  /**
   * The scope to stamp on the files. `null` → the menu still exports and the
   * footer says "Scope unavailable". An `appVersion` of `null` is filled from
   * the cached health poll (`useAppVersion`).
   */
  provenance: ChartProvenance | null
}

export type ExportFormat = 'png' | 'svg' | 'csv'

/**
 * How an ECharts-owned container is recognised WITHOUT importing the engine
 * (`ECHARTS_INSTANCE_ATTRIBUTE` in `engines/echarts/exportImage.ts`; a test
 * pins the two together).
 */
export const CANVAS_ENGINE_SELECTOR = '[_echarts_instance_]'

export const EXPORT_LABELS = {
  trigger: 'Export',
  busy: 'Exporting…',
  png: 'PNG image',
  svg: 'SVG image',
  csv: 'CSV data',
  light: 'Light background',
  lightUnavailable: 'Light background (not for this chart)',
  dismiss: 'Dismiss',
} as const

const TRIGGER =
  'inline-flex min-h-6 items-center gap-1 rounded border border-[var(--color-border-light)] px-3 py-1 text-xs text-[var(--color-text)] hover:bg-[var(--color-bg-hover)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)] aria-disabled:opacity-60'
const ITEM =
  'flex min-h-6 w-full items-center gap-2 rounded px-2 py-1 text-left text-xs text-[var(--color-text)] hover:bg-[var(--color-bg-hover)] focus:bg-[var(--color-bg-hover)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)] aria-disabled:cursor-not-allowed aria-disabled:opacity-60'

/** The menu's items, in order: the arrow keys' ring. */
const menuItems = (menu: HTMLElement | null) =>
  Array.from(menu?.querySelectorAll<HTMLElement>('[role^="menuitem"]') ?? [])

/** The export itself, off the render path. Throws; the menu catches. */
async function exportChart(
  format: ExportFormat,
  { title, chartSlug, getBody, series, axes }: ChartExportMenuProps,
  provenance: ChartProvenance | null,
  light: boolean,
): Promise<void> {
  const filename = exportFilename(chartSlug ?? title, provenance?.project ?? null, provenance?.generatedAt, format)
  if (format === 'csv') {
    downloadBlob(csvBlob(buildChartCsv(title, series, provenance, axes)), filename)
    return
  }
  const body = getBody()
  if (!body) throw new Error('The chart is not on screen.')
  const image = await import('@/lib/viz/chartImage')
  let source: import('@/lib/viz/chartImage').ChartSource
  if (body.querySelector(CANVAS_ENGINE_SELECTOR)) {
    const { echartsImage } = await import('./engines/echarts/exportImage')
    // Transparent: the export's own background is painted under it.
    const drawn = echartsImage(body, EXPORT_PIXEL_RATIO, 'transparent')
    if (!drawn) throw new Error('The chart has not finished drawing.')
    source = { kind: 'raster', ...drawn }
    light = false
  } else {
    const svg = image.findChartSvg(body)
    if (!svg) throw new Error('The chart has not finished drawing.')
    source = { kind: 'svg', svg }
  }
  const blob = await image.exportChartImage({ title, body, source, provenance, light }, format)
  downloadBlob(blob, filename)
}

export default function ChartExportMenu(props: ChartExportMenuProps) {
  const { title, getBody, provenance: given } = props
  const appVersion = useAppVersion()
  const provenance = given && given.appVersion === null && appVersion ? { ...given, appVersion } : given
  const announcer = useChartAnnouncer()

  const [open, setOpen] = useState(false)
  const [light, setLight] = useState(false)
  const [canvasEngine, setCanvasEngine] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [focusOnOpen, setFocusOnOpen] = useState<'first' | 'last'>('first')

  // The frame going into or out of full screen closes the menu. Under the
  // Fullscreen API the browser takes Escape for itself and leaves at once —
  // the menu's own Escape never runs — and a menu laid out for the full-screen
  // frame must not be left open over the page (VIZ-608).
  const fullscreen = useChartFullscreen()
  const [menuFullscreen, setMenuFullscreen] = useState(fullscreen)
  if (menuFullscreen !== fullscreen) {
    setMenuFullscreen(fullscreen)
    setOpen(false)
  }

  const menuId = useId()
  const errorId = useId()
  const wrapperRef = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)

  // Opening moves focus into the menu (APG): first item, or last for ArrowUp.
  useEffect(() => {
    if (!open) return
    const list = menuItems(menuRef.current)
    list[focusOnOpen === 'last' ? list.length - 1 : 0]?.focus()
  }, [open, focusOnOpen])

  // A pointer press outside closes it, without moving focus.
  useEffect(() => {
    if (!open) return
    const onPointerDown = (event: PointerEvent | MouseEvent) => {
      if (!wrapperRef.current?.contains(event.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onPointerDown)
    return () => document.removeEventListener('mousedown', onPointerDown)
  }, [open])

  const openMenu = (focus: 'first' | 'last') => {
    if (busy) return
    setCanvasEngine(Boolean(getBody()?.querySelector(CANVAS_ENGINE_SELECTOR)))
    setError(null)
    setFocusOnOpen(focus)
    setOpen(true)
  }

  const close = (returnFocus: boolean) => {
    setOpen(false)
    if (returnFocus) triggerRef.current?.focus()
  }

  const onTriggerKeyDown = (event: ReactKeyboardEvent<HTMLButtonElement>) => {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault()
      openMenu(event.key === 'ArrowUp' ? 'last' : 'first')
    }
  }

  const onMenuKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    const list = menuItems(menuRef.current)
    const at = list.indexOf(document.activeElement as HTMLElement)
    const move = (index: number) => {
      event.preventDefault()
      list[(index + list.length) % list.length]?.focus()
    }
    switch (event.key) {
      case 'ArrowDown':
        return move(at + 1)
      case 'ArrowUp':
        return move(at - 1)
      case 'Home':
        return move(0)
      case 'End':
        return move(list.length - 1)
      case 'Escape':
        event.preventDefault()
        event.stopPropagation() // a full-screen frame must not ALSO close on this Escape
        return close(true)
      case 'Tab':
        // Close and let the Tab move focus on from the trigger's place.
        setOpen(false)
        return
    }
  }

  const run = (format: ExportFormat) => {
    close(true)
    setBusy(true)
    setError(null)
    exportChart(format, props, provenance, light)
      .catch((cause: unknown) => {
        const reason = cause instanceof Error && cause.message ? cause.message : 'Something went wrong.'
        const message = `${title}: the ${format.toUpperCase()} export failed. ${reason}`
        setError(message)
        announcer?.assertive(message)
      })
      .finally(() => setBusy(false))
  }

  return (
    <div ref={wrapperRef} className="relative" data-chart-export="">
      <button
        ref={triggerRef}
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        aria-describedby={error ? errorId : undefined}
        aria-disabled={busy || undefined}
        // While busy the NAME says so; the visible word stays "Export" (which
        // the name still contains, SC 2.5.3) so the button keeps its width
        // and the title beside the toolbar never re-wraps mid-export.
        aria-label={busy ? EXPORT_LABELS.busy : undefined}
        onClick={() => (open ? close(false) : openMenu('first'))}
        onKeyDown={onTriggerKeyDown}
        className={TRIGGER}
      >
        {EXPORT_LABELS.trigger}
        {/* The chevron says "this opens a menu", not "this downloads now"
            (baseline review A); a spinner takes its place, at its size, while busy. */}
        {busy ? (
          <Loader2 aria-hidden="true" data-chart-export-busy="" className="h-3 w-3 animate-spin motion-reduce:animate-none" />
        ) : (
          <ChevronDown aria-hidden="true" data-chart-export-chevron="" className="h-3 w-3" />
        )}
      </button>
      {open && (
        <div
          ref={menuRef}
          id={menuId}
          role="menu"
          aria-label={`Export ${title}`}
          onKeyDown={onMenuKeyDown}
          className="absolute right-0 top-full z-20 mt-1 flex min-w-48 flex-col gap-0.5 rounded border border-[var(--color-border)] bg-[var(--color-bg-card)] p-1 shadow-lg"
        >
          <button type="button" role="menuitem" tabIndex={-1} onClick={() => run('png')} className={ITEM}>
            {EXPORT_LABELS.png}
          </button>
          <button type="button" role="menuitem" tabIndex={-1} onClick={() => run('svg')} className={ITEM}>
            {EXPORT_LABELS.svg}
          </button>
          <button type="button" role="menuitem" tabIndex={-1} onClick={() => run('csv')} className={ITEM}>
            {EXPORT_LABELS.csv}
          </button>
          <div role="separator" className="my-0.5 border-t border-[var(--color-border)]" />
          <button
            type="button"
            role="menuitemcheckbox"
            tabIndex={-1}
            aria-checked={light && !canvasEngine}
            aria-disabled={canvasEngine || undefined}
            onClick={() => {
              if (!canvasEngine) setLight((on) => !on)
            }}
            className={ITEM}
          >
            {/* A visible tick, not colour: the state reads without aria. */}
            <span aria-hidden="true" className="inline-block w-3 text-center">
              {light && !canvasEngine ? '✓' : ''}
            </span>
            {canvasEngine ? EXPORT_LABELS.lightUnavailable : EXPORT_LABELS.light}
          </button>
        </div>
      )}
      {error && (
        <p
          id={errorId}
          data-chart-export-error=""
          className="absolute right-0 top-full z-10 mt-1 flex w-64 items-start gap-2 rounded border border-[var(--color-border)] bg-[var(--color-bg-card)] p-2 text-xs text-[var(--color-text)] shadow-lg"
        >
          <span className="flex-1">{error}</span>
          <button
            type="button"
            onClick={() => setError(null)}
            className="min-h-6 rounded border border-[var(--color-border-light)] px-2 text-[var(--color-text)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]"
          >
            {EXPORT_LABELS.dismiss}
          </button>
        </p>
      )}
    </div>
  )
}
