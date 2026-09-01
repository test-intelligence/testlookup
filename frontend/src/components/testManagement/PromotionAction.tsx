import { useState } from 'react'
import { mutate } from 'swr'
import toast from 'react-hot-toast'
import { GitMerge } from 'lucide-react'

import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { usePermissions } from '@/hooks/usePermissions'
import { refreshSuites } from '@/hooks/useSuites'
import { suitesService } from '@/services/suitesService'
import type { CanonicalPromotionResponse } from '@/types/suites'

interface PromotionActionProps {
  canonicalId?: string | null
  linkedManagedCaseId?: string | null
  onPromoted?: (result: CanonicalPromotionResponse) => void | Promise<void>
  compact?: boolean
}

function promotionError(error: unknown): string {
  const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  if (typeof detail === 'string' && detail.trim()) return detail
  return 'Could not promote this automation test.'
}

export default function PromotionAction({
  canonicalId,
  linkedManagedCaseId,
  onPromoted,
  compact = false,
}: PromotionActionProps) {
  const { isQaEngineer } = usePermissions()
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [refreshWarning, setRefreshWarning] = useState<string | null>(null)
  const [locallyPromoted, setLocallyPromoted] = useState(false)

  const missingIdentity = !canonicalId
  const alreadyLinked = !!linkedManagedCaseId || locallyPromoted

  async function promote(event: React.MouseEvent<HTMLButtonElement>) {
    event.stopPropagation()
    if (!canonicalId || alreadyLinked) return
    setSaving(true)
    setError(null)
    setRefreshWarning(null)
    let result: CanonicalPromotionResponse
    try {
      result = await suitesService.promoteCanonical(canonicalId)
    } catch (promotionFailure) {
      setError(promotionError(promotionFailure))
      setSaving(false)
      return
    }

    setLocallyPromoted(true)
    setSaving(false)
    toast.success('Automation test promoted to a managed draft')
    try {
      await Promise.all([
        refreshSuites(),
        mutate((key: unknown) => Array.isArray(key) && key[0] === 'tm-cases'),
        onPromoted?.(result),
      ])
    } catch {
      setRefreshWarning('Promotion succeeded, but one or more catalogs could not be refreshed. The promoted control is disabled to prevent a duplicate request.')
    }
  }

  if (!isQaEngineer) return null

  return (
    <span className="inline-flex flex-col items-end gap-1">
      <button
        type="button"
        onClick={promote}
        disabled={saving || missingIdentity || alreadyLinked}
        title={
          missingIdentity
            ? 'Promotion unavailable because this execution has no canonical test identity.'
            : alreadyLinked
              ? 'This automation test is already linked to a managed case.'
              : 'Promote to a managed draft'
        }
        className={compact ? 'inline-flex items-center gap-1 text-xs text-[var(--color-accent)] disabled:text-[var(--color-text-faint)]' : 'btn-secondary inline-flex items-center gap-2 text-sm'}
        aria-label={alreadyLinked ? 'Already promoted' : 'Promote to managed case'}
      >
        {saving ? <LoadingSpinner size="sm" /> : <GitMerge className="h-3.5 w-3.5" />}
        {saving ? 'Promoting…' : alreadyLinked ? 'Promoted' : 'Promote'}
      </button>
      {error && <span role="alert" className="max-w-56 text-right text-[11px] text-[var(--status-failed)]">{error}</span>}
      {refreshWarning && <span role="status" className="max-w-72 text-right text-[11px] text-[var(--status-broken)]">{refreshWarning}</span>}
    </span>
  )
}
