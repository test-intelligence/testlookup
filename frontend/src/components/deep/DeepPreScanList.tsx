/**
 * W14 AGT-07: the run's failing results grouped by error signature (`GET /deep-investigate/{run}/pre-scan`), one
 * row per group: its representative error (React TEXT — agent / test output is never markup), its failure count,
 * its suites and, when the last deep analysis clustered the run, the cluster most of its members landed in.
 *
 * With `onToggle` every row is a checkbox (the run configuration's selection); without it the rows are read-only
 * (a role that cannot trigger sees what the pre-scan found, not controls it cannot use).
 */
import type { PreScanGroup } from '@/types/agentOps'
import { groupError } from './deepRunModel'

export interface DeepPreScanListProps {
  groups: readonly PreScanGroup[]
  /** Ticked `signature_id`s (only read with `onToggle`). */
  selected?: readonly string[]
  /** Given: each row is a checkbox. */
  onToggle?: (signatureId: string) => void
}

const ROW = 'grid items-start gap-x-3 gap-y-0.5 rounded-md border border-[var(--color-border)] px-2.5 py-2'
const COLUMNS = { gridTemplateColumns: '20px minmax(0, 1fr) 72px minmax(0, 9rem)' }

export default function DeepPreScanList({ groups, selected = [], onToggle }: DeepPreScanListProps) {
  return (
    <ul className="m-0 flex list-none flex-col gap-1.5 p-0" data-prescan-groups="">
      {groups.map((group) => {
        const error = groupError(group)
        const checked = selected.includes(group.signature_id)
        return (
          <li key={group.signature_id} className={ROW} style={COLUMNS} data-prescan-group={group.signature_id}>
            <span className="pt-0.5">
              {onToggle && (
                <input
                  type="checkbox"
                  aria-label={`Select ${group.members} failure${group.members === 1 ? '' : 's'}: ${error}`}
                  checked={checked}
                  onChange={() => onToggle(group.signature_id)}
                />
              )}
            </span>
            <span className="min-w-0">
              <span className="line-clamp-2 break-words font-mono text-xs text-[var(--color-text)]" title={error}>
                {error}
              </span>
              {group.cluster_label && (
                <span className="mt-0.5 block truncate text-2xs text-[var(--color-text-muted)]" title={group.cluster_label}>
                  cluster {group.cluster_label}
                </span>
              )}
            </span>
            <span className="text-right text-xs tabular-nums text-[var(--color-text-secondary)]">
              {group.members} failure{group.members === 1 ? '' : 's'}
            </span>
            <span className="truncate text-2xs text-[var(--color-text-muted)]" title={group.suites.join(', ')}>
              {group.suites.length > 0 ? group.suites.join(', ') : '—'}
            </span>
          </li>
        )
      })}
    </ul>
  )
}
