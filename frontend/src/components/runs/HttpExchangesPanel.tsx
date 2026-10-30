/**
 * W23d AGT-14 — the `HTTP exchanges` tab of a test result (the full test page
 * only; `TestCaseBody` mounts it while the tab is open, so its ONE read,
 * `GET …/tests/{id}/http-exchanges`, is asked only then).
 *
 * The requests the test made, as its reporter captured them (opt-in per run)
 * and the server redacted them before storing: one collapsed row per exchange
 * (method · endpoint · status · duration · Redacted), opened into Request /
 * Response with both header lists and bodies. Every header, URL and body is
 * React text — a `<script>` body shows literally; no link is built from
 * captured data. A whole-value `[REDACTED]` is a neutral badge.
 */
import Disclosure from '@/components/ui/Disclosure'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { useHttpExchanges } from '@/hooks/useHttpExchanges'
import { parseExchangeList, type HttpExchange, type HttpMessage } from '@/types/httpExchanges'
import {
  EXCHANGES_LOAD_ERROR,
  EXCHANGES_LOADING,
  REDACTED,
  REDACTED_BADGE,
  bodyNote,
  headerRows,
  isRedactedValue,
  panelSentences,
  prettyBody,
  summaryLine,
} from './httpExchangesModel'

const NEUTRAL_BADGE = 'badge border border-[var(--color-border)] bg-[var(--color-bg-secondary)] text-[var(--color-text-muted)]'
const MUTED = 'm-0 text-xs text-[var(--color-text-muted)]'

export default function HttpExchangesPanel({ runId, testId }: { runId: string; testId: string }) {
  const { data, error, mutate } = useHttpExchanges(runId, testId, true)
  const list = data != null ? parseExchangeList(data) : null
  // Answered, but not a list (no `items` array): a load error, never "none captured".
  const malformed = data !== undefined && !list

  if (error || malformed) {
    return (
      <div className="flex flex-col items-start gap-2" data-http-exchanges="error">
        <p className="m-0 text-sm text-[var(--color-text-secondary)]">{EXCHANGES_LOAD_ERROR}</p>
        <button type="button" className="btn-secondary btn-sm" onClick={() => { void mutate() }}>Retry</button>
      </div>
    )
  }
  if (!list) {
    return (
      <div className="flex items-center gap-2 text-sm text-[var(--color-text-muted)]" data-http-exchanges="loading">
        <LoadingSpinner size="sm" />
        {EXCHANGES_LOADING}
      </div>
    )
  }

  const sentences = panelSentences(list)
  if (sentences.empty) {
    return (
      <div className="flex flex-col gap-1" data-http-exchanges="empty">
        {sentences.empty.map(line => (
          <p key={line} className="m-0 text-sm text-[var(--color-text-secondary)]">{line}</p>
        ))}
        {sentences.dropped && <p className={MUTED}>{sentences.dropped}</p>}
      </div>
    )
  }
  return (
    <section aria-label="HTTP exchanges" className="flex flex-col gap-2" data-http-exchanges="list">
      <p className={MUTED}>{sentences.header}</p>
      <div className="flex flex-col gap-2">
        {list.items.map(ex => <ExchangeRow key={ex.seq} ex={ex} />)}
      </div>
      {sentences.dropped && <p className={MUTED} data-http-exchanges-dropped="">{sentences.dropped}</p>}
    </section>
  )
}

function ExchangeRow({ ex }: { ex: HttpExchange }) {
  const s = summaryLine(ex)
  const title = (
    <span className="flex min-w-0 items-center gap-2" data-http-exchange-summary="">
      <span className={`${NEUTRAL_BADGE} shrink-0 font-mono`}>{s.method}</span>
      <span className="min-w-0 flex-1 truncate font-mono text-xs" title={s.url}>{s.endpoint}</span>
      <span className="shrink-0 font-mono text-xs">{s.status}</span>
      {s.duration && <span className="shrink-0 text-xs text-[var(--color-text-muted)]">{s.duration}</span>}
      {s.redacted && <span className={`${NEUTRAL_BADGE} shrink-0`}>{REDACTED_BADGE}</span>}
    </span>
  )
  return (
    <div data-http-exchange={ex.seq}>
      <Disclosure title={title}>
        <div className="grid gap-4 xl:grid-cols-2">
          <MessageSide heading="Request" msg={ex.request} url={ex.url} />
          <MessageSide heading="Response" msg={ex.response} />
        </div>
      </Disclosure>
    </div>
  )
}

function MessageSide({ heading, msg, url }: { heading: string; msg: HttpMessage; url?: string }) {
  const headers = headerRows(msg?.headers)
  const body = prettyBody(msg?.body)
  const note = bodyNote(msg)
  return (
    <div className="flex min-w-0 flex-col gap-2" data-http-side={heading.toLowerCase()}>
      <h4 className="m-0 text-xs font-semibold uppercase tracking-wider text-[var(--color-text-muted)]">{heading}</h4>
      {url && <p className="m-0 font-mono text-xs text-[var(--color-text)] [overflow-wrap:anywhere]">{url}</p>}
      {headers.length > 0 && (
        <dl className="m-0 grid grid-cols-[minmax(0,10rem)_1fr] gap-x-3 gap-y-1 text-xs" data-http-headers="">
          {headers.map(([name, value]) => (
            <div key={name} className="contents">
              <dt className="min-w-0 truncate font-mono text-[var(--color-text-muted)]" title={name}>{name}</dt>
              <dd className="m-0 min-w-0 font-mono text-[var(--color-text)] [overflow-wrap:anywhere]">
                {isRedactedValue(value) ? <span className={NEUTRAL_BADGE} data-redacted="">{REDACTED}</span> : value}
              </dd>
            </div>
          ))}
        </dl>
      )}
      {body !== null && (
        <pre className="m-0 max-h-80 overflow-auto whitespace-pre-wrap rounded-md border border-[var(--color-border)] bg-[var(--color-bg-secondary)] p-2 font-mono text-xs text-[var(--color-text)] [overflow-wrap:anywhere]" data-http-body="">
          {body}
        </pre>
      )}
      {note && <p className={MUTED} data-http-body-note="">{note}</p>}
    </div>
  )
}
