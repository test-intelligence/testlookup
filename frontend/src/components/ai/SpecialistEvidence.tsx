/**
 * W23d AGT-14 / AGT-15 — the decision report's `Specialist evidence` row,
 * under the existing evidence groups: the API contract stage's violations and
 * the log stage's application metrics (a `MetricSpark` per series). Rendered
 * ONLY when the report carries `contract_findings` or `log_findings`, so a
 * report without them (every report before W23d) looks exactly as it did.
 *
 * Cards in the `EvidenceGroup` style (same border, surface and heading);
 * `flex flex-col gap-*` inside each, never `space-y-*` (Tailwind v4 lets the
 * children's `m-0` win). Every string is React text.
 */
import MetricSpark from '@/components/charts/MetricSpark'
import type { ContractFindings, LogFindings } from '@/types/specialistEvidence'
import {
  CONTRACT_TITLE,
  LOG_TITLE,
  METRICS_TITLE,
  SPECIALIST_TITLE,
  contractRows,
  endpointsLine,
  logMetricRows,
  moreLine,
  type ContractView,
  type LogView,
  type SentencePart,
} from './specialistEvidenceModel'

const CARD = 'flex min-w-0 flex-col gap-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-secondary)] p-3'
const HEADING = 'm-0 text-xs font-semibold text-[var(--color-text)]'

export default function SpecialistEvidence({ contractFindings, logFindings }: {
  contractFindings?: ContractFindings | null
  logFindings?: LogFindings | null
}) {
  const contract = contractRows(contractFindings)
  const log = logMetricRows(logFindings)
  if (!contract && !log) return null
  return (
    <section aria-label={SPECIALIST_TITLE} className="mt-3 flex flex-col gap-2" data-specialist-evidence="">
      <h4 className="m-0 text-xs font-semibold uppercase tracking-wider text-[var(--color-text-muted)]">{SPECIALIST_TITLE}</h4>
      <div className="grid gap-3 md:grid-cols-2">
        {contract && <ContractCard view={contract} />}
        {log && <LogCard view={log} />}
      </div>
    </section>
  )
}

function Sentence({ parts }: { parts: SentencePart[] }) {
  return (
    <>
      {parts.map((part, i) => (part.code
        ? <code key={i} className="font-mono">{part.text}</code>
        : <span key={i}>{part.text}</span>))}
    </>
  )
}

function ContractCard({ view }: { view: ContractView }) {
  return (
    <div className={CARD} data-contract-findings="">
      <h5 className={HEADING}>{CONTRACT_TITLE} · {view.count}</h5>
      {view.summary.length > 0 && (
        <p className="m-0 text-xs text-[var(--color-text-secondary)] [overflow-wrap:anywhere]" data-contract-summary="">
          <Sentence parts={view.summary} />
        </p>
      )}
      {view.rows.length > 0 && (
        <ul className="m-0 flex list-none flex-col gap-1.5 p-0 text-xs text-[var(--color-text)]">
          {view.rows.map(row => (
            <li key={row.key} className="min-w-0 [overflow-wrap:anywhere]" data-contract-violation="">
              <Sentence parts={row.parts} />
            </li>
          ))}
          {view.more > 0 && <li className="text-[var(--color-text-muted)]">{moreLine(view.more)}</li>}
        </ul>
      )}
      {view.endpointsChecked > 0 && (
        <p className="m-0 text-2xs text-[var(--color-text-muted)]">{endpointsLine(view.endpointsChecked)}</p>
      )}
    </div>
  )
}

function LogCard({ view }: { view: LogView }) {
  const hasMetrics = view.metricRows.length > 0 || view.metricSentence !== null
  return (
    <div className={CARD} data-log-findings="">
      <h5 className={HEADING}>{LOG_TITLE}</h5>
      {view.summary && (
        <p className="m-0 line-clamp-2 text-xs text-[var(--color-text-secondary)] [overflow-wrap:anywhere]" title={view.summary}>
          {view.summary}
        </p>
      )}
      {hasMetrics && (
        <div className="flex flex-col gap-2" data-app-metrics="">
          <p className="m-0 text-2xs font-semibold uppercase tracking-wider text-[var(--color-text-muted)]">{METRICS_TITLE}</p>
          {view.metricRows.map(row => (
            <div key={row.key} className="flex items-start gap-2" data-metric-row="">
              <MetricSpark
                label={row.label}
                service={row.service}
                unit={row.unit}
                values={row.values}
                windowMean={row.windowMean}
                baselineMean={row.baselineMean}
                tone={row.tone}
              />
              <p className="m-0 min-w-0 flex-1 text-xs text-[var(--color-text)] [overflow-wrap:anywhere]">{row.sentence}</p>
            </div>
          ))}
          {view.metricSentence && (
            <p className="m-0 text-xs text-[var(--color-text-secondary)]" data-metric-outcome="">{view.metricSentence}</p>
          )}
        </div>
      )}
    </div>
  )
}
