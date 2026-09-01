/**
 * Storage footprint panel (S3).
 *
 * Answers the question that decides whether an operator turns retention on:
 * how much is this project holding, and what would a purge give back?
 *
 * Two rules the markup exists to honour, both of which the backend already
 * encodes and which it would be easy to throw away here:
 *
 *  1. **A store that could not be reached renders "not measured", never 0.**
 *     Zero and unreachable are opposite findings. Rendering both as "0 B"
 *     tells an operator their project is free when the truth is nothing looked.
 *  2. **An estimate is labelled an estimate.** Only object storage attributes
 *     bytes exactly; Postgres reports no bytes at all, and says why.
 */
import { AlertTriangle, Database, HardDrive, RefreshCw } from 'lucide-react'
import { useDeletedProjectStorage, useProjectStorage } from '@/hooks/useStorage'
import { formatBytes } from '@/utils/formatters'
import type { StoreFootprint } from '@/types/storage'

const STORE_LABELS: Record<string, string> = {
  object_storage: 'Object storage',
  postgres: 'Runs & test cases',
  mongo: 'Run document collections',
}

/** One store's figure — the single place the null rule is applied. */
function StoreValue({ store }: { store: StoreFootprint }) {
  if (!store.measured) {
    return (
      <span
        className="text-[var(--color-text-muted)]"
        title={
          store.unreachable_reason
            ? `Could not reach this store (${store.unreachable_reason}), so its contents were not counted.`
            : 'This store could not be reached, so its contents were not counted.'
        }
      >
        not measured
      </span>
    )
  }
  if (store.bytes === null) {
    // Reached, but bytes are deliberately not reported — Postgres, where a
    // per-project byte figure would be an invention and a delete would not
    // return the disk anyway.
    return (
      <span
        className="text-[var(--color-text)]"
        title={store.estimate_basis ?? undefined}
      >
        {store.items === null ? '—' : `${store.items.toLocaleString()} rows`}
      </span>
    )
  }
  return (
    <span
      className="text-[var(--color-text)]"
      title={store.estimate_basis ?? undefined}
    >
      {formatBytes(store.bytes)}
      {!store.exact && (
        <span className="ml-1 text-[10px] text-[var(--color-text-muted)]">est.</span>
      )}
      {!store.complete && (
        <span className="ml-1 text-[10px] text-[var(--status-broken)]">partial</span>
      )}
    </span>
  )
}

interface Props {
  projectId: string
  /** Deployment-wide reads are ADMIN-only; the page already gates on this. */
  canReadDeployment?: boolean
}

export default function StoragePanel({ projectId, canReadDeployment = true }: Props) {
  const { data, error, isLoading, mutate } = useProjectStorage(projectId)
  const { data: deleted, mutate: mutateDeleted } =
    useDeletedProjectStorage(canReadDeployment)

  return (
    <section className="card space-y-3" data-testid="storage-panel">
      <div className="flex items-center gap-2">
        <HardDrive className="h-4 w-4 text-[var(--color-accent)]" />
        <h2 className="text-sm font-semibold uppercase tracking-wider text-[var(--color-text)]">
          Storage footprint
        </h2>
        <button
          type="button"
          onClick={() => {
            void mutate()
            if (canReadDeployment) void mutateDeleted()
          }}
          aria-label="Refresh storage figures"
          className="ml-auto text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
        >
          <RefreshCw className="h-3.5 w-3.5" />
        </button>
      </div>

      {isLoading && (
        <p className="text-xs text-[var(--color-text-muted)]">Measuring…</p>
      )}

      {error && !data && (
        <p className="text-xs text-[var(--status-failed)]">
          Could not read the storage footprint.
        </p>
      )}

      {data && (
        <>
          <table className="w-full text-xs">
            <tbody>
              {data.stores.map((store) => (
                <tr key={store.store}>
                  <td className="py-1 text-[var(--color-text)]">
                    {STORE_LABELS[store.store] ?? store.store}
                  </td>
                  <td
                    className="py-1 text-right font-mono"
                    data-testid={`store-value-${store.store}`}
                  >
                    <StoreValue store={store} />
                  </td>
                </tr>
              ))}
              <tr className="border-t border-[var(--color-border)]">
                <td className="py-1 font-semibold text-[var(--color-text)]">
                  Total
                  {data.total_is_estimate && (
                    <span className="ml-1 text-[10px] font-normal text-[var(--color-text-muted)]">
                      (estimate)
                    </span>
                  )}
                </td>
                <td
                  className="py-1 text-right font-mono font-semibold text-[var(--color-text)]"
                  data-testid="storage-total"
                >
                  {data.total_bytes === null ? (
                    <span className="font-normal text-[var(--color-text-muted)]">
                      not measured
                    </span>
                  ) : (
                    formatBytes(data.total_bytes)
                  )}
                </td>
              </tr>
            </tbody>
          </table>

          {!data.fully_measured && (
            <p
              className="flex items-start gap-1.5 text-[11px] text-[var(--status-broken)]"
              data-testid="storage-partial-warning"
            >
              <AlertTriangle className="h-3 w-3 mt-0.5 shrink-0" />
              At least one store could not be reached, so this total is
              incomplete — it is a floor, not a measurement.
            </p>
          )}

          {/* The caveat the API states and the UI has to repeat, because an
              operator reading "Database rows" will otherwise assume deleting
              them frees disk. It does not, on its own. */}
          <p className="text-[11px] text-[var(--color-text-muted)]">
            Test-run and test-case rows are counted exactly, but not sized: rows
            share tables across projects, and deleting them does not return disk
            to the operating system without a VACUUM FULL. Bytes for five
            run-scoped document collections are estimated from average document
            size.
          </p>
        </>
      )}

      {deleted && deleted.projects_total > 0 && (
        <div
          className="rounded border border-[var(--status-broken-bd)] bg-[var(--status-broken-bg)] p-2.5"
          data-testid="deleted-projects-line"
        >
          <div className="flex items-center gap-1.5 text-xs text-[var(--color-text)]">
            <Database className="h-3.5 w-3.5 shrink-0" />
            <span className="font-semibold">
              {deleted.total_bytes === null
                ? 'Deleted projects still hold data'
                : `${formatBytes(deleted.total_bytes)} held by deleted projects`}
            </span>
          </div>
          <p className="mt-1 text-[11px] text-[var(--color-text-muted)]">
            {deleted.unreachable_by_retention > 0 ? (
              <>
                <strong className="text-[var(--color-text)]">
                  {deleted.unreachable_by_retention} of {deleted.projects_measured} measured
                </strong>{' '}
                have no retention policy that will ever reclaim them — deleting a
                project does not delete its data.
              </>
            ) : deleted.truncated ? (
              <>
                All {deleted.projects_measured} measured projects are covered by an
                enabled retention policy and will be swept.
              </>
            ) : (
              <>
                All {deleted.projects_total} are covered by an enabled retention
                policy and will be swept.
              </>
            )}
            {deleted.truncated && (
              <>
                {' '}{deleted.projects_total - deleted.projects_measured} projects
                were not measured, so these totals are floors.
              </>
            )}
          </p>
        </div>
      )}
    </section>
  )
}
