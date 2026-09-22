import { selectReleaseIds, useReleaseStore } from '@/store/releaseStore'
import { ScopeSummaryButton } from '@/components/ui/ScopeSummaryButton'
import type { Release } from '@/types/releases'
import { UNATTRIBUTED_RELEASE } from './ReleasePicker'

/** The "no release filter" option — the same `''` sentinel `ReleasePicker`'s
 *  `<select>` uses. */
const NO_RELEASE_VALUE = ''

/**
 * `ReleasePicker` with `viz_multi_filters` ON (VIZ-303 / a11y M5): the
 * selection may hold several releases, and a native `<select>` replaces it
 * with ONE on a single ArrowDown. A read-only summary button instead; editing
 * happens in the report filter bar (or, on a page without one, an explicit
 * pick from its menu).
 *
 * Part of the lazy multi-filters runtime (`multiFiltersRuntime.ts`), so a
 * flag-off session never downloads it.
 */
export default function ReleaseSummary({
  releases,
  listLoaded,
  disabledReason,
  scopeProjectId,
}: {
  releases: readonly Release[]
  listLoaded: boolean
  disabledReason: string | undefined
  scopeProjectId: string | null
}) {
  const activeReleaseId = useReleaseStore(s => s.activeReleaseId)
  const activeReleaseIds = useReleaseStore(s => s.activeReleaseIds)
  const setActiveRelease = useReleaseStore(s => s.setActiveRelease)
  const selectedIds = selectReleaseIds({ activeReleaseId, activeReleaseIds })
  const summary =
    selectedIds.length > 1
      ? `${selectedIds.length} releases`
      : activeReleaseId === null
        ? 'All releases'
        : activeReleaseId === UNATTRIBUTED_RELEASE
          ? 'Unattributed'
          : releases.find(r => r.id === activeReleaseId)?.name ?? (listLoaded ? 'Unknown release' : 'Loading…')
  return (
    <ScopeSummaryButton
      dimension="release"
      ariaLabel="Filter by release"
      label={summary}
      title={disabledReason ?? (selectedIds.length > 1 ? `${summary} selected` : undefined)}
      disabled={Boolean(disabledReason)}
      selected={selectedIds}
      options={[
        { value: NO_RELEASE_VALUE, label: 'All releases' },
        ...releases.map(r => ({ value: r.id, label: r.name })),
        { value: UNATTRIBUTED_RELEASE, label: '— Unattributed —' },
      ]}
      onPick={value => setActiveRelease(value === NO_RELEASE_VALUE ? null : value, scopeProjectId)}
    />
  )
}
