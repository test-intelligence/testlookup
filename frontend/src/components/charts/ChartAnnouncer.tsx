/**
 * ONE live region for every chart on a page (VIZ-105).
 *
 * A page of N charts must not be N live regions: a filter change would queue N
 * long "X updated. <summary>" announcements. Instead each `ChartFrame` reports
 * a CHANGE here — never its initial load — and the provider coalesces every
 * change within `ANNOUNCE_DEBOUNCE_MS` into one short polite message:
 *
 *   one frame changed    "Pass rate chart: error"
 *   several changed      "5 charts updated"  /  "3 charts updated, 1 chart: error"
 *
 * The full summary stays in each chart's `aria-describedby`, where a reader
 * finds it when they reach the chart; it is never live text.
 *
 * Only the result of the reader's OWN action (Retry succeeded / failed) is
 * assertive, and it is announced at once, uncoalesced.
 *
 * Mount one provider per page that hosts chart frames. A frame with no
 * provider above it announces nothing (and renders no live region).
 *
 * FULL SCREEN (VIZ-608). Under the Fullscreen API, Chromium drops everything
 * outside the full-screen element from the accessibility tree — these two
 * regions included — and the overlay's `aria-modal` (with the page behind it
 * made `inert`) does the same by design. So while a frame is full screen it
 * renders a `ChartAnnouncerOutlet` inside itself, and the provider speaks
 * THERE instead: the same announcer, the same messages, moved. One place at
 * a time — the page regions are silent while an outlet holds the voice, and
 * each place starts empty when it takes it over, so nothing is said twice
 * and nothing old is re-read.
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'

/** How long changes are collected before one announcement is made. */
export const ANNOUNCE_DEBOUNCE_MS = 800

export interface ChartAnnouncer {
  /**
   * A frame's state changed (not its first load). Coalesced, polite. `kind`
   * `'summary'` is a page-level text (the filtered-dataset line, VIZ-305): it
   * is announced as-is, never counted as a chart.
   */
  report(frameId: string, title: string, change: string, kind?: AnnouncementKind): void
  /** The result of the reader's own action: announced now, assertively. */
  assertive(text: string): void
}

export type AnnouncementKind = 'chart' | 'summary'

const ChartAnnouncerContext = createContext<ChartAnnouncer | null>(null)

/** The page's announcer, or `null` when no provider is mounted. */
export function useChartAnnouncer(): ChartAnnouncer | null {
  return useContext(ChartAnnouncerContext)
}

const chartsNoun = (n: number) => (n === 1 ? '1 chart' : `${n} charts`)

/** The one message for a batch of changes. Exported for tests. */
export function composeAnnouncement(
  changes: readonly { title: string; change: string; kind?: AnnouncementKind }[],
): string {
  const summaries = changes.filter((c) => c.kind === 'summary').map((c) => c.change)
  const charts = changes.filter((c) => c.kind !== 'summary')
  let chartText = ''
  if (charts.length === 1) chartText = `${charts[0].title} chart: ${charts[0].change}`
  else if (charts.length > 1) {
    const counts = new Map<string, number>()
    for (const { change } of charts) counts.set(change, (counts.get(change) ?? 0) + 1)
    chartText = [...counts]
      .map(([change, n]) => (change === 'updated' ? `${chartsNoun(n)} updated` : `${chartsNoun(n)}: ${change}`))
      .join(', ')
  }
  return [...summaries, chartText].filter(Boolean).join('. ')
}

/** U+00A0: invisible, but a different string. */
const NBSP = String.fromCharCode(0xa0)

/** Repeating the same text is not a DOM change a screen reader hears; vary it invisibly. */
const nextText = (previous: string, text: string) => (previous === text ? `${text}${NBSP}` : text)

/** What an outlet needs: whether it holds the voice now, and what to say. */
interface OutletValue {
  /** The outlet speaking now (the last one registered), or `null` for the page's own regions. */
  speaker: string | null
  polite: string
  assertive: string
  register(id: string): () => void
}

const OutletContext = createContext<OutletValue | null>(null)

export function ChartAnnouncerProvider({ children }: { children: ReactNode }) {
  const [polite, setPolite] = useState('')
  const [assertiveText, setAssertiveText] = useState('')
  // Registered outlets, oldest first; the last one speaks.
  const [outlets, setOutlets] = useState<readonly string[]>([])
  const speaker = outlets.length ? outlets[outlets.length - 1] : null
  // A new speaker starts empty: text the previous place already said is not
  // said again by the next one. (Adjusting state while rendering, so no
  // region ever renders the old text in the new place.)
  const [lastSpeaker, setLastSpeaker] = useState<string | null>(speaker)
  if (lastSpeaker !== speaker) {
    setLastSpeaker(speaker)
    setPolite('')
    setAssertiveText('')
  }
  const pending = useRef(new Map<string, { title: string; change: string; kind?: AnnouncementKind }>())
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current)
    },
    [],
  )

  const report = useCallback((frameId: string, title: string, change: string, kind?: AnnouncementKind) => {
    // Latest change per frame: a frame that changes twice in the window counts once.
    pending.current.set(frameId, { title, change, kind })
    if (timer.current) clearTimeout(timer.current)
    timer.current = setTimeout(() => {
      timer.current = null
      const text = composeAnnouncement([...pending.current.values()])
      pending.current.clear()
      setPolite((previous) => nextText(previous, text))
    }, ANNOUNCE_DEBOUNCE_MS)
  }, [])

  const assertive = useCallback((text: string) => {
    setAssertiveText((previous) => nextText(previous, text))
  }, [])

  const value = useMemo<ChartAnnouncer>(() => ({ report, assertive }), [report, assertive])

  const register = useCallback((id: string) => {
    setOutlets((list) => [...list.filter((other) => other !== id), id])
    return () => setOutlets((list) => list.filter((other) => other !== id))
  }, [])
  const outletValue = useMemo<OutletValue>(
    () => ({ speaker, polite, assertive: assertiveText, register }),
    [speaker, polite, assertiveText, register],
  )
  const pageSpeaks = speaker === null

  return (
    <ChartAnnouncerContext.Provider value={value}>
      <OutletContext.Provider value={outletValue}>{children}</OutletContext.Provider>
      <div role="status" className="sr-only" data-chart-announcer="polite">
        {pageSpeaks ? polite : ''}
      </div>
      <div aria-live="assertive" aria-atomic="true" className="sr-only" data-chart-announcer="assertive">
        {pageSpeaks ? assertiveText : ''}
      </div>
    </ChartAnnouncerContext.Provider>
  )
}

/**
 * The page announcer's voice INSIDE a full-screen element (see the header):
 * `ChartFrame` renders one while it is full screen. It is not a region of the
 * chart's own — it says exactly what the page's one announcer says, and only
 * while it holds the voice. Outside a provider it renders nothing.
 */
export function ChartAnnouncerOutlet() {
  const outlet = useContext(OutletContext)
  const id = useId()
  const register = outlet?.register
  // Before paint, so the regions are in place (and speaking) from the first
  // frame of full screen.
  useLayoutEffect(() => register?.(id), [register, id])
  if (!outlet) return null
  const mine = outlet.speaker === id
  return (
    <>
      <div role="status" className="sr-only" data-chart-announcer-outlet="polite">
        {mine ? outlet.polite : ''}
      </div>
      <div aria-live="assertive" aria-atomic="true" className="sr-only" data-chart-announcer-outlet="assertive">
        {mine ? outlet.assertive : ''}
      </div>
    </>
  )
}
