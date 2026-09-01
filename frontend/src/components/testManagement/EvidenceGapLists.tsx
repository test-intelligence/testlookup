import { useState } from 'react'
import { ChevronDown, ChevronRight, Clock, Link2Off } from 'lucide-react'
import toast from 'react-hot-toast'

import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { usePermissions } from '@/hooks/usePermissions'
import { useOrphanedCanonicalCases } from '@/hooks/useSuites'
import { useTestCaseEvidenceGaps } from '@/hooks/useTestManagement'
import { onboardingService } from '@/services/onboardingService'
import { suitesService } from '@/services/suitesService'
import type { CanonicalTestCase } from '@/types/suites'
import type {
  TestCaseEvidenceGapItem,
  TestCaseEvidenceGapKind,
} from '@/types/test-management'
import TransitionReasonDialog from './TransitionReasonDialog'

type ListKey = TestCaseEvidenceGapKind | 'orphaned'

interface EvidenceGapListsProps {
  projectId: string | null
}

function formatObservedAt(value?: string | null) {
  if (!value) return 'Not observed'
  return new Date(value).toLocaleString()
}

function retirementErrorMessage(error: unknown): string {
  const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  if (typeof detail === 'string' && detail.trim()) return detail
  return 'Could not confirm this automation retirement.'
}

export default function EvidenceGapLists({ projectId }: EvidenceGapListsProps) {
  const { isQaLead } = usePermissions()
  const neverExecuted = useTestCaseEvidenceGaps('never_executed')
  const automationVanished = useTestCaseEvidenceGaps('automation_vanished')
  const orphaned = useOrphanedCanonicalCases()
  const [open, setOpen] = useState<Set<ListKey>>(() => new Set())
  const [retiring, setRetiring] = useState<CanonicalTestCase | null>(null)
  const [savingRetirement, setSavingRetirement] = useState(false)
  const [retirementError, setRetirementError] = useState<string | null>(null)
  const [retirementRefreshWarning, setRetirementRefreshWarning] = useState<string | null>(null)
  const [confirmedRetirementIds, setConfirmedRetirementIds] = useState<Set<string>>(() => new Set())

  const orphanedItems = orphaned.data?.items.filter((item) => !confirmedRetirementIds.has(item.id))
  const locallyHiddenOrphanCount = orphaned.data?.items.filter((item) => confirmedRetirementIds.has(item.id)).length ?? 0
  const orphanedTotal = orphaned.data
    ? Math.max(0, orphaned.data.total - locallyHiddenOrphanCount)
    : undefined

  function toggle(key: ListKey) {
    setOpen((current) => {
      const next = new Set(current)
      if (next.has(key)) {
        next.delete(key)
      } else {
        next.add(key)
        void onboardingService.trackEvent('test_case_evidence_list_opened', projectId ?? undefined, { kind: key })
      }
      return next
    })
  }

  async function confirmRetirement(reason: string) {
    if (!retiring) return
    setSavingRetirement(true)
    setRetirementError(null)
    try {
      await suitesService.confirmRetirement(retiring.id, reason)
    } catch (retirementFailure) {
      const message = retirementErrorMessage(retirementFailure)
      setRetirementError(message)
      toast.error(message)
      setSavingRetirement(false)
      return
    }

    const retiredId = retiring.id
    setConfirmedRetirementIds((current) => new Set(current).add(retiredId))
    setRetiring(null)
    setSavingRetirement(false)
    toast.success('Automation retirement confirmed')
    try {
      await orphaned.mutate()
    } catch {
      setRetirementRefreshWarning('Retirement was confirmed, but the proof list could not be refreshed. The confirmed row remains hidden locally.')
    }
  }

  async function retryOrphans() {
    try {
      await orphaned.mutate()
      setRetirementRefreshWarning(null)
    } catch {
      setRetirementRefreshWarning('The retirement proof list is still stale. Confirmed rows remain hidden locally.')
    }
  }

  return (
    <section aria-label="Execution evidence gaps" className="mt-3.5 grid gap-3.5 lg:grid-cols-3">
      <EvidenceListCard
        listKey="never_executed"
        title="Authored cases never executed"
        description="Managed cases with no execution evidence yet."
        open={open.has('never_executed')}
        onToggle={() => toggle('never_executed')}
        items={neverExecuted.data?.items}
        total={neverExecuted.data?.total}
        loading={neverExecuted.isLoading}
        error={!!neverExecuted.error}
        onRetry={() => void neverExecuted.mutate()}
        icon={<Clock className="h-4 w-4" />}
      />
      <EvidenceListCard
        listKey="automation_vanished"
        title="Authored cases whose automation vanished"
        description="Linked automation is no longer present in recent runs."
        open={open.has('automation_vanished')}
        onToggle={() => toggle('automation_vanished')}
        items={automationVanished.data?.items}
        total={automationVanished.data?.total}
        loading={automationVanished.isLoading}
        error={!!automationVanished.error}
        onRetry={() => void automationVanished.mutate()}
        icon={<Link2Off className="h-4 w-4" />}
      />
      <OrphanedListCard
        open={open.has('orphaned')}
        onToggle={() => toggle('orphaned')}
        items={orphanedItems}
        total={orphanedTotal}
        loading={orphaned.isLoading}
        error={!!orphaned.error}
        onRetry={() => void retryOrphans()}
        canConfirmRetirement={isQaLead}
        onConfirm={(item) => {
          setRetirementError(null)
          setRetiring(item)
        }}
      />

      {retirementError && (
        <p role="alert" className="lg:col-span-3 rounded-md border border-[var(--status-failed-bd)] bg-[var(--status-failed-bg)] p-2 text-sm text-[var(--status-failed)]">
          {retirementError}
        </p>
      )}

      {retirementRefreshWarning && (
        <div role="status" className="lg:col-span-3 flex items-center justify-between gap-3 rounded-md border border-[var(--status-broken-bd)] bg-[var(--status-broken-bg)] p-2 text-sm text-[var(--status-broken)]">
          <span>{retirementRefreshWarning}</span>
          <button type="button" className="btn-secondary flex-shrink-0 text-xs" onClick={() => void retryOrphans()}>
            Retry proof list
          </button>
        </div>
      )}

      {retiring && (
        <TransitionReasonDialog
          title="Confirm automation retirement"
          description={`Confirm that ${retiring.test_name} was intentionally removed. The canonical and managed records will be preserved.`}
          confirmLabel="Confirm retirement"
          busy={savingRetirement}
          onCancel={() => {
            setRetiring(null)
            setRetirementError(null)
          }}
          onConfirm={confirmRetirement}
        />
      )}
    </section>
  )
}

interface EvidenceListCardProps {
  listKey: TestCaseEvidenceGapKind
  title: string
  description: string
  open: boolean
  onToggle: () => void
  items?: TestCaseEvidenceGapItem[]
  total?: number
  loading: boolean
  error: boolean
  onRetry: () => void
  icon: React.ReactNode
}

function EvidenceListCard({
  listKey,
  title,
  description,
  open,
  onToggle,
  items,
  total,
  loading,
  error,
  onRetry,
  icon,
}: EvidenceListCardProps) {
  return (
    <article className="rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-card)]">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={`evidence-list-${listKey}`}
        onClick={onToggle}
        className="flex w-full items-start justify-between gap-3 p-4 text-left"
      >
        <span className="flex min-w-0 gap-2">
          <span className="mt-0.5 text-[var(--color-accent)]">{icon}</span>
          <span>
            <span className="block text-sm font-semibold text-[var(--color-text)]">{title}</span>
            <span className="mt-0.5 block text-xs text-[var(--color-text-muted)]">{description}</span>
          </span>
        </span>
        <span className="inline-flex items-center gap-1 text-xs text-[var(--color-text-muted)]">
          {loading ? 'not measured' : total ?? 'not measured'}
          {open ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
        </span>
      </button>
      {open && (
        <div id={`evidence-list-${listKey}`} className="border-t border-[var(--color-border)] p-3">
          {loading ? <LoadingSpinner size="sm" /> : error ? (
            <button type="button" className="text-xs text-[var(--color-accent)]" onClick={onRetry}>Retry</button>
          ) : !items?.length ? (
            <p className="text-xs text-[var(--color-text-muted)]">No cases in this list.</p>
          ) : (
            <ul className="space-y-2">
              {items.map((item) => (
                <li key={item.id} className="rounded-md bg-[var(--color-bg-secondary)] p-2">
                  <p className="truncate text-xs font-medium text-[var(--color-text)]">{item.title}</p>
                  <p className="mt-0.5 text-[11px] capitalize text-[var(--color-text-muted)]">
                    {item.status.replace(/_/g, ' ')} · {formatObservedAt(item.deleted_observed_at ?? item.last_executed_at)}
                  </p>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </article>
  )
}

interface OrphanedListCardProps {
  open: boolean
  onToggle: () => void
  items?: CanonicalTestCase[]
  total?: number
  loading: boolean
  error: boolean
  onRetry: () => void
  canConfirmRetirement: boolean
  onConfirm: (item: CanonicalTestCase) => void
}

function OrphanedListCard({ open, onToggle, items, total, loading, error, onRetry, canConfirmRetirement, onConfirm }: OrphanedListCardProps) {
  return (
    <article className="rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-card)]">
      <button type="button" aria-expanded={open} aria-controls="evidence-list-orphaned" onClick={onToggle} className="flex w-full items-start justify-between gap-3 p-4 text-left">
        <span className="flex min-w-0 gap-2">
          <Link2Off className="mt-0.5 h-4 w-4 text-[var(--status-broken)]" />
          <span>
            <span className="block text-sm font-semibold text-[var(--color-text)]">Unconfirmed automation retirements</span>
            <span className="mt-0.5 block text-xs text-[var(--color-text-muted)]">Deleted automation waiting for a human reason.</span>
          </span>
        </span>
        <span className="inline-flex items-center gap-1 text-xs text-[var(--color-text-muted)]">
          {loading ? 'not measured' : total ?? 'not measured'}
          {open ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
        </span>
      </button>
      {open && (
        <div id="evidence-list-orphaned" className="border-t border-[var(--color-border)] p-3">
          {loading ? <LoadingSpinner size="sm" /> : error ? (
            <button type="button" className="text-xs text-[var(--color-accent)]" onClick={onRetry}>Retry</button>
          ) : !items?.length ? (
            <p className="text-xs text-[var(--color-text-muted)]">No unconfirmed retirements.</p>
          ) : (
            <ul className="space-y-2">
              {items.map((item) => (
                <li key={item.id} className="flex items-center justify-between gap-2 rounded-md bg-[var(--color-bg-secondary)] p-2">
                  <span className="min-w-0">
                    <span className="block truncate text-xs font-medium text-[var(--color-text)]">{item.test_name}</span>
                    <span className="block text-[11px] text-[var(--color-text-muted)]">Observed {formatObservedAt(item.deleted_observed_at)}</span>
                  </span>
                  {canConfirmRetirement && (
                    <button type="button" className="btn-secondary flex-shrink-0 text-xs" onClick={() => onConfirm(item)}>
                      Confirm retirement
                    </button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </article>
  )
}
