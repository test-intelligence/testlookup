/**
 * SuiteParityCard — "Suite membership cutover" on /settings/storage (W25a HYG-04 phase 1, contract §4.1-§4.3).
 *
 * The human-readable face of the daily parity harness: whether the old suite-membership tables and the test catalog
 * agree, day by day (UTC), and how far the 7-day cutover clock has run. ADMIN only — StoragePage renders it for an
 * admin, so a QA lead neither sees it nor requests the history.
 *
 *   - ONE read (`useSuiteParityHistory`), re-polled every 15 s only while the newest comparison runs, or while a
 *     comparison this card just queued has not appeared yet (at most two minutes).
 *   - "Compare now" is the ONE write (POST, enqueues a read-only comparison): a 202 toasts "Comparison queued." and
 *     re-reads; a 409 / 503 shows the server's sentence inline — never "Request failed with status code …".
 *   - "Show differences" on a day with differences reads that comparison and lists its rows INLINE under the day (no
 *     modal, no side panel); the same button hides them.
 *   - A failed read renders "Could not load the cutover comparisons." here (StoragePage stays `known-silent`).
 *   - Suite, test and project names come from customer data: React text only.
 *
 * W25d HYG-04 (the cutover, contract §4.1-§4.2), from the history's `cutover` object (`cardView`):
 *   - Before the cutover the card is the W25a card, element for element (its visual region must not move).
 *   - Ready (legacy writes on, `cutover_ready`): "Cut over now" beside "Compare now" opens a confirmation INLINE under
 *     the status line (no modal, no `confirm(`); its "Cut over" is the second write — one POST, disabled while in
 *     flight; a 200 toasts "Cut over." and re-reads; a 409 shows the server's sentence in the refusal line.
 *   - Cut over: no button; "Cut over on … after 7 clean days.", who did it (React text, truncated with its title),
 *     and the 7 dates ending at the cut-over date. Not written (the switch off with no time): said loudly.
 */
import { useEffect, useRef, useState } from 'react'
import toast from 'react-hot-toast'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { Table, Td, Th, Tr } from '@/components/ui/Table'
import { isParityRunning, useSuiteParityHistory, useSuiteParityRun } from '@/hooks/useSuiteParity'
import { suiteParityService } from '@/services/suiteParityService'
import type { SuiteParityRunDetail } from '@/types/suiteParity'
import {
  cardView,
  cutoverRefusalSentence,
  PARITY_TEXT,
  queueRefusalSentence,
  sampleView,
  type DayKind,
  type StatusKind,
} from './suiteParityModel'

/** How long the card polls for a comparison it queued before giving up (the worker may be down). */
const AWAIT_RUN_MS = 120_000

const STATUS_INK: Record<StatusKind, string> = {
  none: 'text-[var(--color-text)]',
  running: 'text-[var(--color-text)]',
  streak: 'text-[var(--color-text)]',
  ready: 'text-[var(--ui-success-ink)]',
  diffs: 'text-[var(--ui-danger-ink)]',
  failed: 'text-[var(--ui-danger-ink)]',
  stale: 'text-[var(--ui-warning-ink)]',
  cut_over: 'text-[var(--color-text)]',
  not_written: 'text-[var(--ui-warning-ink)]',
}

const DAY_INK: Record<DayKind, string> = {
  clean: 'text-[var(--color-text)]',
  diffs: 'text-[var(--ui-danger-ink)]',
  failed: 'text-[var(--ui-danger-ink)]',
  running: 'text-[var(--color-text-muted)]',
  not_run: 'text-[var(--color-text-muted)]',
  not_run_yet: 'text-[var(--color-text-muted)]',
}

export default function SuiteParityCard() {
  // The newest run's id when "Compare now" queued one ('' = no run yet): poll until a newer run appears.
  const [awaitFrom, setAwaitFrom] = useState<string | null>(null)
  const [queuing, setQueuing] = useState(false)
  const [refusal, setRefusal] = useState<string | null>(null)
  const [openRunId, setOpenRunId] = useState<string | null>(null)
  // W25d: the inline cutover confirmation, and the one cutover POST in flight.
  const [confirming, setConfirming] = useState(false)
  const [cutting, setCutting] = useState(false)
  const cutInFlight = useRef(false)

  // The hook polls while `awaitFrom` is still the newest id; a newer run (or the timeout below) ends it.
  const { data: history, error, mutate } = useSuiteParityHistory(true, { awaitRunAfter: awaitFrom })
  const { data: detail, error: detailError } = useSuiteParityRun(openRunId)
  const newestId = history?.runs?.[0]?.id ?? ''
  const running = isParityRunning(history)

  useEffect(() => {
    if (awaitFrom === null) return undefined
    const timer = setTimeout(() => setAwaitFrom(null), AWAIT_RUN_MS)
    return () => clearTimeout(timer)
  }, [awaitFrom])

  async function compareNow() {
    if (queuing) return
    setQueuing(true)
    setRefusal(null)
    const from = newestId
    try {
      await suiteParityService.runNow()
      toast.success(PARITY_TEXT.queued)
      setAwaitFrom(from)
    } catch (err: unknown) {
      setRefusal(queueRefusalSentence(err))
    } finally {
      setQueuing(false)
      void mutate()
    }
  }

  /**
   * The confirmation's "Cut over": ONE POST — a ref, not the render's `cutting`, refuses a second click that lands
   * before the button re-renders disabled — then the re-read.
   */
  async function cutOver() {
    if (cutInFlight.current) return
    cutInFlight.current = true
    setCutting(true)
    setRefusal(null)
    try {
      await suiteParityService.cutOver()
      toast.success(PARITY_TEXT.cutOverDone)
    } catch (err: unknown) {
      setRefusal(cutoverRefusalSentence(err))
    } finally {
      cutInFlight.current = false
      setCutting(false)
      setConfirming(false)
      void mutate()
    }
  }

  const view = history ? cardView(history, new Date()) : null
  const line = view?.line ?? null
  const days = view?.days ?? []

  const compareButton = (
    <button
      type="button"
      className="btn-secondary flex-shrink-0 disabled:cursor-not-allowed disabled:opacity-50"
      onClick={compareNow}
      disabled={!history || running || queuing}
    >
      {running || queuing ? PARITY_TEXT.comparing : PARITY_TEXT.compareNow}
    </button>
  )

  return (
    <section
      className="card flex flex-col gap-3"
      data-suite-parity=""
      data-parity-status={error ? 'error' : line?.kind ?? 'loading'}
      data-parity-cutover={view && view.state !== 'before' ? view.state : undefined}
      aria-labelledby="suite-parity-heading"
    >
      <div className="flex items-center justify-between gap-4">
        <h3 id="suite-parity-heading" className="m-0 text-sm font-semibold text-[var(--color-text)]">
          {PARITY_TEXT.heading}
        </h3>
        {view?.canCutOver ? (
          <div className="flex flex-shrink-0 items-center gap-2">
            {compareButton}
            <button
              type="button"
              className="btn-primary flex-shrink-0 disabled:cursor-not-allowed disabled:opacity-50"
              onClick={() => {
                setRefusal(null)
                setConfirming(true)
              }}
              aria-expanded={confirming}
              disabled={running || queuing || cutting}
            >
              {PARITY_TEXT.cutOverNow}
            </button>
          </div>
        ) : !view || view.canCompare ? (
          compareButton
        ) : null}
      </div>
      <p className="m-0 break-words text-xs text-[var(--color-text-muted)]">{PARITY_TEXT.explainer}</p>

      {error ? (
        <p className="m-0 text-sm text-[var(--ui-danger-ink)]">{PARITY_TEXT.loadError}</p>
      ) : !history || !view || !line ? (
        <LoadingSpinner size="sm" />
      ) : (
        <>
          <div className="flex flex-col gap-1" data-parity-status-line="">
            <p className={`m-0 text-sm ${STATUS_INK[line.kind]}`}>{line.text}</p>
            {line.detail && <p className="m-0 text-xs text-[var(--color-text-muted)]">{line.detail}</p>}
            {view.byLine && (
              <p className="m-0 truncate text-xs text-[var(--color-text-muted)]" title={view.byLine} data-parity-cut-over-by="">
                {view.byLine}
              </p>
            )}
          </div>
          {confirming && view.canCutOver && (
            <div
              className="flex flex-col gap-2 rounded border border-[var(--color-border)] px-3 py-2"
              data-parity-confirm=""
            >
              <p className="m-0 text-xs text-[var(--color-text)]">{PARITY_TEXT.confirmQuestion}</p>
              <p className="m-0 text-xs text-[var(--color-text-muted)]">{PARITY_TEXT.confirmNote}</p>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  className="btn-primary btn-sm disabled:cursor-not-allowed disabled:opacity-50"
                  onClick={cutOver}
                  disabled={cutting}
                >
                  {PARITY_TEXT.confirmCutOver}
                </button>
                <button type="button" className="btn-ghost btn-sm" onClick={() => setConfirming(false)} disabled={cutting}>
                  {PARITY_TEXT.cancel}
                </button>
              </div>
            </div>
          )}
          {view.daysHeading && (
            <p className="m-0 text-xs text-[var(--color-text-muted)]" data-parity-days-heading="">
              {view.daysHeading}
            </p>
          )}
          <ul
            className="m-0 flex list-none flex-col gap-1 p-0"
            aria-label={view.daysHeading ?? PARITY_TEXT.daysHeading}
            data-parity-days=""
          >
            {days.map((day) => {
              const runId = day.kind === 'diffs' ? day.run?.id ?? null : null
              const open = runId !== null && runId === openRunId
              return (
                <li key={day.date} className="m-0 flex flex-col gap-2" data-parity-day={day.date} data-day-kind={day.kind}>
                  <div className="grid grid-cols-[96px_minmax(0,1fr)_auto] items-center gap-2 text-xs">
                    <span className="text-[var(--color-text-muted)]">{day.label}</span>
                    <span className={`truncate ${DAY_INK[day.kind]}`}>{day.result}</span>
                    {runId ? (
                      <button
                        type="button"
                        className="btn-ghost btn-sm"
                        aria-expanded={open}
                        onClick={() => setOpenRunId(open ? null : runId)}
                      >
                        {open ? PARITY_TEXT.hideDifferences : PARITY_TEXT.showDifferences}
                      </button>
                    ) : (
                      <span />
                    )}
                  </div>
                  {open && <ParitySample detail={detail?.id === runId ? detail : undefined} failed={!!detailError} />}
                </li>
              )
            })}
          </ul>
        </>
      )}

      {refusal && (
        <p role="alert" data-parity-refusal="" className="m-0 text-xs text-[var(--ui-danger-ink)]">
          {refusal}
        </p>
      )}
    </section>
  )
}

/**
 * One comparison's differences, inline under its day: the counts per reason (the expected ones as information),
 * then the sampled rows — Reason · Project · Suite · Test, each cell truncated with its full text as the title.
 * Column widths were measured under DejaVu at 1280 (the Reason column holds "Missing from the old tables").
 */
function ParitySample({ detail, failed }: { detail: SuiteParityRunDetail | undefined; failed: boolean }) {
  if (failed && !detail) {
    return <p className="m-0 text-xs text-[var(--ui-danger-ink)]">{PARITY_TEXT.detailLoadError}</p>
  }
  if (!detail) return <LoadingSpinner size="sm" />
  const view = sampleView(detail)
  const cell = 'truncate text-xs'
  return (
    <div className="flex flex-col gap-2" data-parity-sample="">
      {view.blockingLine && <p className="m-0 text-xs text-[var(--color-text)]">{view.blockingLine}</p>}
      {view.expectedLine && <p className="m-0 text-xs text-[var(--color-text-muted)]">{view.expectedLine}</p>}
      {view.rows.length > 0 && (
        <Table density="compact" className="table-fixed">
          <colgroup>
            <col className="w-[224px]" />
            <col className="w-[152px]" />
            <col className="w-[152px]" />
            <col />
          </colgroup>
          <thead>
            <tr>
              <Th>{PARITY_TEXT.columns.reason}</Th>
              <Th>{PARITY_TEXT.columns.project}</Th>
              <Th>{PARITY_TEXT.columns.suite}</Th>
              <Th>{PARITY_TEXT.columns.test}</Th>
            </tr>
          </thead>
          <tbody>
            {view.rows.map((row) => (
              <Tr key={row.key} interactive={false}>
                <Td className={cell} title={row.reason}>{row.reason}</Td>
                <Td className={cell} title={row.project}>{row.project}</Td>
                <Td className={cell} title={row.suite}>{row.suite}</Td>
                <Td className={cell} title={row.test}>{row.test}</Td>
              </Tr>
            ))}
          </tbody>
        </Table>
      )}
      {view.moreLine && <p className="m-0 text-xs text-[var(--color-text-muted)]">{view.moreLine}</p>}
    </div>
  )
}
