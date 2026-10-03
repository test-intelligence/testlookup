/**
 * One failure group's details (VIZ-504), in the kit's `SidePanel`: the first
 * line as the server saw it most often, the counts, the trend, the category
 * mix, the tests it hits most and the signature that groups it. "View rows"
 * hands the group to the shared rows panel (the executions behind it, selected
 * by the same SQL signature: BE4's `bucket_error_signature`).
 *
 * Non-modal (SidePanel's default): the plot and the table stay usable beside
 * it; Escape closes it and focus goes back to what opened it. Every string is
 * React text.
 */
import SidePanel from '@/components/ui/SidePanel'
import { formatNumber } from '@/utils/formatters'
import Sparkline from '../Sparkline'
import { WRAP_ANYWHERE } from './plot.model'
import {
  categoryLabel,
  COLLIDE_NOTE,
  formatShare,
  panelTitle,
  trendLabel,
  utcDay,
  type FailureGroup,
  type TrendGrain,
} from './failureGroups.model'

export interface FailureGroupPanelProps {
  /** `null` = closed. */
  group: FailureGroup | null
  trendGrain: TrendGrain | null
  onClose: () => void
  /** Open the rows behind the group. Absent = no "View rows" (nothing to open them in). */
  onViewRows?: (group: FailureGroup) => void
}

const BUTTON =
  'rounded border border-[var(--color-border-light)] px-3 py-1 text-xs text-[var(--color-text)] hover:bg-[var(--color-bg-hover)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]'

export default function FailureGroupPanel({ group, trendGrain, onClose, onViewRows }: FailureGroupPanelProps) {
  if (!group) return null
  const facts: [string, string][] = [
    ['Failures', formatNumber(group.failureCount)],
    ['Share of failures', formatShare(group.share)],
    ['Tests', formatNumber(group.affectedTests)],
    ['Runs', formatNumber(group.affectedRuns)],
    ['First seen (UTC)', utcDay(group.firstSeen)],
    ['Last seen (UTC)', utcDay(group.lastSeen)],
    ['Category', categoryLabel(group.dominantCategory)],
  ]
  return (
    <SidePanel
      open
      onClose={onClose}
      title={panelTitle(group)}
      footer={
        onViewRows ? (
          <button type="button" className={BUTTON} data-group-view-rows="" onClick={() => onViewRows(group)}>
            View rows
          </button>
        ) : undefined
      }
    >
      <div data-group-panel={group.rank} className="flex flex-col gap-4 text-sm text-[var(--color-text)]">
        <p data-group-panel-label="" className="m-0 font-mono text-xs" style={WRAP_ANYWHERE}>
          {group.label}
        </p>
        {group.distinctRawLines !== null && group.distinctRawLines > 1 && (
          <p className="m-0 text-xs text-[var(--color-text-secondary)]">
            {formatNumber(group.distinctRawLines)} {COLLIDE_NOTE}: two causes may have been grouped together.
          </p>
        )}
        <dl className="m-0 grid gap-x-4 gap-y-1 text-xs" style={{ gridTemplateColumns: 'auto 1fr' }}>
          {facts.map(([term, value]) => (
            <div key={term} className="contents">
              <dt className="text-[var(--color-text-secondary)]">{term}</dt>
              <dd className="m-0 tabular-nums" style={WRAP_ANYWHERE}>
                {value}
              </dd>
            </div>
          ))}
        </dl>
        {group.trend.length >= 2 && (
          <section>
            <h3 className="m-0 mb-1 text-xs font-semibold">{trendLabel(trendGrain)}</h3>
            <Sparkline series={group.trend.map((point) => point.y)} label={trendLabel(trendGrain)} tone="bad" area />
          </section>
        )}
        {group.categories.length > 0 && (
          <section>
            <h3 className="m-0 mb-1 text-xs font-semibold">Categories</h3>
            <ul className="m-0 list-none p-0 text-xs">
              {group.categories.map((category) => (
                <li key={category.category} className="flex justify-between gap-3">
                  <span style={WRAP_ANYWHERE}>{categoryLabel(category.category)}</span>
                  <span className="tabular-nums">{formatNumber(category.count)}</span>
                </li>
              ))}
            </ul>
          </section>
        )}
        {group.topTests.length > 0 && (
          <section>
            <h3 className="m-0 mb-1 text-xs font-semibold">Tests it fails most</h3>
            <ol className="m-0 list-decimal pl-5 text-xs">
              {group.topTests.map((test) => (
                <li key={`${test.projectId ?? ''}\u0000${test.fingerprint}`} data-group-top-test="">
                  <span style={WRAP_ANYWHERE}>{test.name || test.fingerprint}</span>{' '}
                  <span className="tabular-nums text-[var(--color-text-secondary)]">({formatNumber(test.count)})</span>
                </li>
              ))}
            </ol>
          </section>
        )}
        <section>
          <h3 className="m-0 mb-1 text-xs font-semibold">Signature</h3>
          <p className="m-0 text-xs text-[var(--color-text-secondary)]">
            The first line with numbers, ids, addresses and timestamps replaced by #, lower-cased: what groups these failures.
          </p>
          <code data-group-signature="" className="mt-1 block text-xs" style={WRAP_ANYWHERE}>
            {group.id}
          </code>
        </section>
      </div>
    </SidePanel>
  )
}
