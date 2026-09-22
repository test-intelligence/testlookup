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
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'

/** How long changes are collected before one announcement is made. */
export const ANNOUNCE_DEBOUNCE_MS = 800

export interface ChartAnnouncer {
  /** A frame's state changed (not its first load). Coalesced, polite. */
  report(frameId: string, title: string, change: string): void
  /** The result of the reader's own action: announced now, assertively. */
  assertive(text: string): void
}

const ChartAnnouncerContext = createContext<ChartAnnouncer | null>(null)

/** The page's announcer, or `null` when no provider is mounted. */
export function useChartAnnouncer(): ChartAnnouncer | null {
  return useContext(ChartAnnouncerContext)
}

const chartsNoun = (n: number) => (n === 1 ? '1 chart' : `${n} charts`)

/** The one message for a batch of changes. Exported for tests. */
export function composeAnnouncement(changes: readonly { title: string; change: string }[]): string {
  if (changes.length === 0) return ''
  if (changes.length === 1) return `${changes[0].title} chart: ${changes[0].change}`
  const counts = new Map<string, number>()
  for (const { change } of changes) counts.set(change, (counts.get(change) ?? 0) + 1)
  return [...counts]
    .map(([change, n]) => (change === 'updated' ? `${chartsNoun(n)} updated` : `${chartsNoun(n)}: ${change}`))
    .join(', ')
}

/** U+00A0: invisible, but a different string. */
const NBSP = String.fromCharCode(0xa0)

/** Repeating the same text is not a DOM change a screen reader hears; vary it invisibly. */
const nextText = (previous: string, text: string) => (previous === text ? `${text}${NBSP}` : text)

export function ChartAnnouncerProvider({ children }: { children: ReactNode }) {
  const [polite, setPolite] = useState('')
  const [assertiveText, setAssertiveText] = useState('')
  const pending = useRef(new Map<string, { title: string; change: string }>())
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current)
    },
    [],
  )

  const report = useCallback((frameId: string, title: string, change: string) => {
    // Latest change per frame: a frame that changes twice in the window counts once.
    pending.current.set(frameId, { title, change })
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

  return (
    <ChartAnnouncerContext.Provider value={value}>
      {children}
      <div role="status" className="sr-only" data-chart-announcer="polite">
        {polite}
      </div>
      <div aria-live="assertive" aria-atomic="true" className="sr-only" data-chart-announcer="assertive">
        {assertiveText}
      </div>
    </ChartAnnouncerContext.Provider>
  )
}
