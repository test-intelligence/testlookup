/**
 * Releases (`/releases`) and one release's page (`/releases/:releaseId`).
 *
 * UX redesign P5 item 5: on the list, a release's detail is a drill-down in a
 * side panel ("Open full page" goes to `/releases/:releaseId`), not an inline
 * expansion that pushed every card below it down the page. The two header
 * controls that only said "(planned)" (Export schedule, Calendar view) are
 * gone: what is not built is not rendered (P2).
 *
 * UX redesign P6 (the fold budget, `02-design-spec.md` §2): above the list,
 * one toolbar row (the counted status filter and search) and ONE
 * StatusBanner (`ReleaseHealthBanner`) instead of a verdict band beside a KPI
 * strip; the band's lede, the release's top blockers and the four count cards
 * are in the collapsed "Release health · this week" disclosure under the list.
 */
import { useEffect, useMemo, useState } from 'react'
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom'
import {
  CheckCircle2, ExternalLink, Package, Pencil, Plus, RotateCcw, Trash2, TriangleAlert, X,
} from 'lucide-react'
import { clsx } from 'clsx'
import toast from 'react-hot-toast'
import PageHeader from '@/components/ui/PageHeader'
import SidePanel from '@/components/ui/SidePanel'
import { helpTopicParam } from '@/components/help/helpTopics'
import { appMutate } from '@/utils/swrCacheMutate'
import EmptyState from '@/components/ui/EmptyState'
import DataUnavailable from '@/components/ui/DataUnavailable'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import Pagination from '@/components/ui/Pagination'
import SuiteBadge from '@/components/ui/SuiteBadge'
import WorkflowTimeline from '@/components/workflow/WorkflowTimeline'
import Disclosure from '@/components/ui/Disclosure'
import CompliancePackPanel from '@/components/compliance/CompliancePackPanel'
import { buildReleaseWorkflow } from '@/components/workflow/workflowPresets'
import { useReleases, useRelease } from '@/hooks/useReleases'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'
import { releasesService } from '@/services/releasesService'
import { useRuns } from '@/hooks/useRuns'
import { useModalFocus } from '@/hooks/useModalFocus'
import type { LinkedRun, Release, ReleasePhase } from '@/types/releases'
import { deriveRelease, computeStageCounts } from '@/components/releases/mapping'
import { ReleaseHealthBanner, ReleaseHealthDetails } from '@/components/releases/ReleaseHealth'
import ReleaseCard from '@/components/releases/ReleaseCard'
import { ShippingThisWeek, AgingSignals, CompliancePacks, RecentActivity } from '@/components/releases/RightRail'

// ── Constants ───────────────────────────────────────────────────────────────

const PHASE_TYPES = [
  { value: 'planning',    label: 'Planning'    },
  { value: 'development', label: 'Development' },
  { value: 'code_freeze', label: 'Code Freeze' },
  { value: 'qa_testing',  label: 'QA Testing'  },
  { value: 'uat',         label: 'UAT'         },
  { value: 'staging',     label: 'Staging'     },
  { value: 'production',  label: 'Production'  },
]

const PHASE_STATUS_COLOR: Record<string, string> = {
  pending:     'bg-[var(--color-bg-card)]/20 text-[var(--color-text-muted)]',
  in_progress: 'bg-[var(--status-broken-bg)]/20 text-[var(--status-broken)]',
  completed:   'bg-[var(--status-passed-bg)]/20 text-[var(--status-passed)]',
  skipped:     'bg-[var(--color-bg-card)]/10 text-[var(--color-text-muted)]',
}

// ── Helpers ─────────────────────────────────────────────────────────────────

function fmtDate(iso: string | null | undefined) {
  if (!iso) return '—'
  return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
}

// ── Create / Edit Release Modal ─────────────────────────────────────────────

interface ReleaseModalProps {
  projectId: string
  onClose: () => void
  onSaved: () => void
  initial?: Release
}

export function ReleaseModal({ projectId, onClose, onSaved, initial }: ReleaseModalProps) {
  const [name, setName]           = useState(initial?.name ?? '')
  const [version, setVersion]     = useState(initial?.version ?? '')
  const [description, setDescription] = useState(initial?.description ?? '')
  const [status, setStatus]       = useState(initial?.status ?? 'planning')
  const [plannedDate, setPlanned] = useState(
    initial?.planned_date ? initial.planned_date.slice(0, 10) : '',
  )
  const [saving, setSaving] = useState(false)
  const dialogRef = useModalFocus({ onClose, canClose: !saving })

  async function save() {
    if (!name.trim()) { toast.error('Release name is required'); return }
    setSaving(true)
    try {
      if (initial) {
        await releasesService.update(initial.id, {
          name: name.trim(),
          version: version || undefined,
          description: description || undefined,
          status,
          planned_date: plannedDate ? new Date(plannedDate).toISOString() : undefined,
        } as Partial<Release>)
        toast.success('Release updated')
      } else {
        await releasesService.create({
          project_id: projectId,
          name: name.trim(),
          version: version || undefined,
          description: description || undefined,
          status,
          planned_date: plannedDate ? new Date(plannedDate).toISOString() : undefined,
        })
        toast.success('Release created')
      }
      onSaved()
      onClose()
    } catch {
      toast.error('Failed to save release')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="release-form-title" className="fixed inset-0 z-50 flex items-center justify-center bg-[var(--color-bg)]/60" onClick={onClose}>
      <div className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl p-6 w-full max-w-lg shadow-2xl" onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-5">
          <h2 id="release-form-title" className="text-base font-semibold text-[var(--color-text)]">{initial ? 'Edit Release' : 'New Release'}</h2>
          <button type="button" aria-label="Close release dialog" onClick={onClose} className="text-[var(--color-text-muted)] hover:text-[var(--color-text)]"><X className="h-4 w-4" /></button>
        </div>
        <div className="space-y-3">
          <div>
            <label htmlFor="release-field-0" className="block text-xs text-[var(--color-text-muted)] mb-1">Release Name *</label>
            <input id="release-field-0"
              value={name} onChange={e => setName(e.target.value)}
              placeholder="e.g. v2.4.0 — Login Revamp"
              className="input w-full"
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label htmlFor="release-field-1" className="block text-xs text-[var(--color-text-muted)] mb-1">Version</label>
              <input id="release-field-1" value={version} onChange={e => setVersion(e.target.value)} placeholder="e.g. 2.4.0" className="input w-full" />
            </div>
            <div>
              <label htmlFor="release-field-2" className="block text-xs text-[var(--color-text-muted)] mb-1">Status</label>
              <select id="release-field-2" value={status} onChange={e => setStatus(e.target.value)} className="input w-full">
                <option value="planning">Planning</option>
                <option value="in_progress">In Progress</option>
                <option value="released">Released</option>
                <option value="cancelled">Cancelled</option>
              </select>
            </div>
          </div>
          <div>
            <label htmlFor="release-field-3" className="block text-xs text-[var(--color-text-muted)] mb-1">Planned Release Date</label>
            <input id="release-field-3" type="date" value={plannedDate} onChange={e => setPlanned(e.target.value)} className="input w-full" />
          </div>
          <div>
            <label htmlFor="release-field-4" className="block text-xs text-[var(--color-text-muted)] mb-1">Description</label>
            <textarea id="release-field-4"
              value={description} onChange={e => setDescription(e.target.value)}
              rows={3}
              placeholder="What's included in this release?"
              className="input w-full resize-none"
            />
          </div>
        </div>
        <div className="flex gap-3 justify-end mt-5">
          <button onClick={onClose} className="px-4 py-2 text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)]">Cancel</button>
          <button
            onClick={save} disabled={saving}
            className="px-4 py-2 text-sm bg-[var(--color-btn-primary-bg)] hover:bg-[var(--color-btn-primary-hover)] disabled:opacity-50 text-[var(--color-btn-primary-text)] rounded-lg font-medium flex items-center gap-2"
          >
            {saving && <LoadingSpinner size="sm" />}
            {initial ? 'Save Changes' : 'Create Release'}
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Link Run Modal ──────────────────────────────────────────────────────────

export function LinkRunModal({ releaseId, phases, onClose, onSaved }: {
  releaseId: string
  phases: ReleasePhase[]
  onClose: () => void
  onSaved: () => void
}) {
  const { data: runsData } = useRuns()
  const runs = runsData?.items ?? []
  const [selectedRun, setSelectedRun] = useState('')
  const [selectedPhase, setSelectedPhase] = useState('')
  const [saving, setSaving] = useState(false)
  const dialogRef = useModalFocus({ onClose, canClose: !saving })

  async function link() {
    if (!selectedRun) { toast.error('Select a test run'); return }
    setSaving(true)
    try {
      await releasesService.linkRun(releaseId, selectedRun, selectedPhase || undefined)
      toast.success('Test run linked to release')
      onSaved()
      onClose()
    } catch {
      toast.error('Failed to link run')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="link-test-run-title" className="fixed inset-0 z-50 flex items-center justify-center bg-[var(--color-bg)]/60" onClick={onClose}>
      <div className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl p-6 w-full max-w-md shadow-2xl" onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-4">
          <h2 id="link-test-run-title" className="text-base font-semibold text-[var(--color-text)]">Link Test Run</h2>
          <button type="button" aria-label="Close link test run dialog" onClick={onClose} className="text-[var(--color-text-muted)] hover:text-[var(--color-text)]"><X className="h-4 w-4" /></button>
        </div>
        <div className="space-y-3">
          <div>
            <label htmlFor="release-field-5" className="block text-xs text-[var(--color-text-muted)] mb-1">Test Run</label>
            <select id="release-field-5" value={selectedRun} onChange={e => setSelectedRun(e.target.value)} className="input w-full">
              <option value="">— Select run —</option>
              {(runs as Array<{ id: string; build_number?: number; created_at: string }>).map(r => (
                <option key={r.id} value={r.id}>
                  {r.build_number ?? r.id.slice(0, 8)} — {new Date(r.created_at).toLocaleDateString()}
                </option>
              ))}
            </select>
          </div>
          {phases.length > 0 && (
            <div>
              <label htmlFor="release-field-6" className="block text-xs text-[var(--color-text-muted)] mb-1">Phase (optional)</label>
              <select id="release-field-6" value={selectedPhase} onChange={e => setSelectedPhase(e.target.value)} className="input w-full">
                <option value="">— No phase —</option>
                {phases.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
              </select>
            </div>
          )}
        </div>
        <div className="flex gap-3 justify-end mt-5">
          <button onClick={onClose} className="px-4 py-2 text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)]">Cancel</button>
          <button
            onClick={link} disabled={saving || !selectedRun}
            className="px-4 py-2 text-sm bg-[var(--color-btn-primary-bg)] hover:bg-[var(--color-btn-primary-hover)] disabled:opacity-50 text-[var(--color-btn-primary-text)] rounded-lg font-medium flex items-center gap-2"
          >
            {saving && <LoadingSpinner size="sm" />}
            Link Run
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Phase Manager (inline) ──────────────────────────────────────────────────

function AddPhaseRow({ releaseId, existingPhaseNames, onSaved }: {
  releaseId: string
  existingPhaseNames: string[]
  onSaved: () => void
}) {
  const [open, setOpen] = useState(false)
  const [name, setName] = useState('')
  const [type, setType] = useState('qa_testing')
  const [saving, setSaving] = useState(false)

  const trimmedName = name.trim()
  const isDuplicate = trimmedName.length > 0 && existingPhaseNames.some(
    n => n.toLowerCase() === trimmedName.toLowerCase(),
  )
  const canAdd = trimmedName.length > 0 && !isDuplicate && !saving

  async function save() {
    if (!canAdd) return
    setSaving(true)
    try {
      await releasesService.addPhase(releaseId, { name: trimmedName, phase_type: type, status: 'pending' })
      setName('')
      setType('qa_testing')
      setOpen(false)
      onSaved()
      toast.success(`Phase "${trimmedName}" added`)
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      toast.error(msg || 'Failed to add phase')
    } finally {
      setSaving(false)
    }
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === 'Enter') { e.preventDefault(); save() }
    if (e.key === 'Escape') { setOpen(false); setName('') }
  }

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="w-full flex items-center gap-2 py-2 px-3 text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text-secondary)] border border-dashed border-[var(--color-border)] hover:border-[var(--color-border-light)] rounded-lg transition-colors"
      >
        <Plus className="h-3.5 w-3.5" /> Add Phase
      </button>
    )
  }

  return (
    <div className="space-y-2 p-3 bg-[var(--color-bg-hover)]/50 rounded-lg border border-[var(--color-border)]">
      <div className="grid grid-cols-[1fr_auto] gap-2">
        <div>
          <label htmlFor="release-field-7" className="block text-[10px] text-[var(--color-text-muted)] mb-1">Phase Name</label>
          <input id="release-field-7"
            value={name}
            onChange={e => setName(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="e.g. QA Testing"
            autoFocus
            className="bg-[var(--color-bg-card)] border border-[var(--color-border)] text-[var(--color-text)] rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-[var(--color-ring)] focus:border-[var(--color-border)] placeholder:text-[var(--color-text-muted)] w-full"
          />
        </div>
        <div>
          <label htmlFor="release-field-8" className="block text-[10px] text-[var(--color-text-muted)] mb-1">Type</label>
          <select id="release-field-8"
            value={type}
            onChange={e => setType(e.target.value)}
            className="bg-[var(--color-bg-card)] border border-[var(--color-border)] text-[var(--color-text)] rounded-lg px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-[var(--color-ring)]"
          >
            {PHASE_TYPES.map(pt => <option key={pt.value} value={pt.value}>{pt.label}</option>)}
          </select>
        </div>
      </div>
      {isDuplicate && (
        <p className="text-xs text-[var(--status-failed)]">A phase with this name already exists in this release.</p>
      )}
      <div className="flex items-center justify-between">
        <p className="text-[10px] text-[var(--color-text-faint)]">Press Enter to add, Escape to cancel</p>
        <div className="flex gap-2">
          <button
            onClick={() => { setOpen(false); setName('') }}
            className="px-3 py-1 text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={save}
            disabled={!canAdd}
            className="px-4 py-1 text-xs bg-[var(--color-btn-primary-bg)] hover:bg-[var(--color-btn-primary-hover)] disabled:opacity-40 disabled:cursor-not-allowed text-[var(--color-btn-primary-text)] rounded-lg font-medium transition-colors"
          >
            {saving ? 'Adding…' : 'Add Phase'}
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Release Detail Panel ────────────────────────────────────────────────────

/**
 * A release's detail: metrics, compliance packs, production outcome, phases,
 * linked runs, and the collapsed pipeline. The same body in the side panel
 * (`/releases`) and on the release's own page (`/releases/:releaseId`).
 */
function ReleaseDetailPanel({ releaseId }: { releaseId: string }) {
  const navigate = useNavigate()
  const { hash } = useLocation()
  const { data: detail, isLoading, mutate: refetch } = useRelease(releaseId)
  const [showLinkModal, setShowLinkModal] = useState(false)
  const [markingReleased, setMarkingReleased] = useState(false)
  const [outcomeReason, setOutcomeReason] = useState('')
  const [markingOutcome, setMarkingOutcome] = useState<'incident' | 'rollback' | null>(null)
  // Pagination for the linked-runs table — 25 rows per page, client-side
  // slice. The release summary above still aggregates across every linked
  // run because it reads ``detail.metrics`` from the API, not this slice.
  const LINKED_PAGE_SIZE = 25
  const [linkedPage, setLinkedPage] = useState(1)

  useEffect(() => {
    if (!detail || !hash.startsWith('#phase-')) return
    const targetId = decodeURIComponent(hash.slice(1))
    document.getElementById(targetId)?.scrollIntoView({ block: 'center' })
  }, [detail, hash])

  async function updatePhaseStatus(phaseId: string, status: string) {
    try {
      const result = await releasesService.updatePhase(releaseId, phaseId, { status } as Partial<ReleasePhase>)
      refetch()
      if ((result as unknown as { all_phases_completed?: boolean })?.all_phases_completed) {
        toast.success('All phases completed! You can now mark this release as Released.')
      }
    } catch { toast.error('Failed to update phase') }
  }

  async function markAsReleased() {
    setMarkingReleased(true)
    try {
      await releasesService.update(releaseId, { status: 'released' } as Partial<Release>)
      toast.success('Release marked as Released')
      refetch()
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      toast.error(msg || 'Failed to mark as released')
    } finally {
      setMarkingReleased(false)
    }
  }

  async function deletePhase(phaseId: string) {
    try {
      await releasesService.deletePhase(releaseId, phaseId)
      refetch()
    } catch { toast.error('Failed to delete phase') }
  }

  async function markOutcome(outcome: 'incident' | 'rollback') {
    const reason = outcomeReason.trim()
    if (reason.length < 3) {
      toast.error('Add a reason before recording the release outcome')
      return
    }
    setMarkingOutcome(outcome)
    try {
      await releasesService.markOutcome(releaseId, outcome, reason)
      toast.success(outcome === 'incident' ? 'Incident recorded' : 'Rollback recorded')
      setOutcomeReason('')
      await refetch()
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      toast.error(msg || 'Failed to record release outcome')
    } finally {
      setMarkingOutcome(null)
    }
  }

  async function unlinkRun(runId: string) {
    try {
      await releasesService.unlinkRun(releaseId, runId)
      refetch()
    } catch { toast.error('Failed to unlink run') }
  }

  const workflow = detail ? buildReleaseWorkflow(detail) : { stages: [], events: [], stageOrder: [] }

  if (isLoading) {
    return <div className="flex items-center justify-center h-48"><LoadingSpinner size="lg" /></div>
  }
  if (!detail) return null

  const m = detail.metrics ?? {}
  // "—" alone when there is no rate: the unit went on the dash ("—%", browser E2E pass).
  const passRate = m.avg_pass_rate != null ? `${Number(m.avg_pass_rate).toFixed(1)}%` : '—'
  const passColor = m.avg_pass_rate == null ? 'text-[var(--color-text-muted)]'
    : m.avg_pass_rate >= 90 ? 'text-[var(--status-passed)]'
    : m.avg_pass_rate >= 70 ? 'text-[var(--status-broken)]'
    : 'text-[var(--status-failed)]'

  return (
    <div className="space-y-5">
      {showLinkModal && (
        <LinkRunModal
          releaseId={releaseId}
          phases={detail.phases ?? []}
          onClose={() => setShowLinkModal(false)}
          onSaved={() => { refetch(); setShowLinkModal(false) }}
        />
      )}

      {/* Metrics strip */}
      <div className="grid grid-cols-2 sm:grid-cols-5 gap-2">
        {[
          { label: 'Runs',    value: m.total_runs ?? 0,    color: 'text-[var(--color-text-secondary)]' },
          { label: 'Tests',   value: m.total_tests ?? 0,   color: 'text-[var(--color-text-secondary)]' },
          { label: 'Passed',  value: m.total_passed ?? 0,  color: 'text-[var(--status-passed)]' },
          { label: 'Failed',  value: m.total_failed ?? 0,  color: 'text-[var(--status-failed)]' },
          { label: 'Pass Rate', value: passRate,           color: passColor },
        ].map(({ label, value, color }) => (
          <div key={label} className="card py-2.5">
            <p className="text-[10px] text-[var(--color-text-muted)] uppercase tracking-wider">{label}</p>
            <p className={clsx('text-xl font-bold tabular-nums mt-0.5', color)}>{value}</p>
          </div>
        ))}
      </div>

      {/* Tier 1 item 4 — Compliance export pack for audit reviewers. */}
      <CompliancePackPanel releaseId={releaseId} releaseName={detail.name} />

      <section className="card space-y-3" aria-labelledby="release-outcome-title">
        <div>
          <h3 id="release-outcome-title" className="text-sm font-semibold text-[var(--color-text)]">Production outcome</h3>
          <p className="text-xs text-[var(--color-text-muted)] mt-0.5">
            Record incidents and rollbacks so weekly drift checks can compare release decisions with production outcomes.
          </p>
        </div>
        <label className="block text-xs text-[var(--color-text-secondary)]" htmlFor={`release-outcome-reason-${releaseId}`}>
          Reason
        </label>
        <textarea
          id={`release-outcome-reason-${releaseId}`}
          value={outcomeReason}
          onChange={event => setOutcomeReason(event.target.value)}
          rows={2}
          maxLength={2000}
          className="input w-full resize-y"
          placeholder="What happened in production?"
        />
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => void markOutcome('incident')}
            disabled={markingOutcome !== null}
            className="inline-flex items-center gap-1.5 rounded-md border border-[var(--status-failed-bd)] px-3 py-1.5 text-xs font-medium text-[var(--status-failed)] disabled:opacity-50"
          >
            <TriangleAlert className="h-3.5 w-3.5" />
            {markingOutcome === 'incident' ? 'Recording…' : 'Mark incident'}
          </button>
          <button
            type="button"
            onClick={() => void markOutcome('rollback')}
            disabled={markingOutcome !== null}
            className="inline-flex items-center gap-1.5 rounded-md border border-[var(--status-broken-bd)] px-3 py-1.5 text-xs font-medium text-[var(--status-broken)] disabled:opacity-50"
          >
            <RotateCcw className="h-3.5 w-3.5" />
            {markingOutcome === 'rollback' ? 'Recording…' : 'Record rollback'}
          </button>
        </div>
        {(detail.outcomes ?? []).length > 0 && (
          <ul className="divide-y divide-[var(--color-border)] border-t border-[var(--color-border)]">
            {(detail.outcomes ?? []).map(outcome => (
              <li key={outcome.id} className="py-2 text-xs">
                <div className="flex items-center justify-between gap-3">
                  <span className="font-medium capitalize text-[var(--color-text)]">{outcome.outcome_kind}</span>
                  <time className="text-[var(--color-text-faint)]" dateTime={outcome.marked_at}>
                    {new Date(outcome.marked_at).toLocaleString()}
                  </time>
                </div>
                <p className="mt-0.5 text-[var(--color-text-muted)]">{outcome.reason}</p>
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* Auto-complete banner — shown when all phases done and release not yet released */}
      {detail.status !== 'released' && detail.status !== 'cancelled' && (detail.phases ?? []).length > 0 &&
        (detail.phases ?? []).every((p: ReleasePhase) => p.status === 'completed' || p.status === 'skipped') && (
        <div className="card border border-[var(--status-passed-bd)]/40 bg-[var(--status-passed-bg)]/20 flex items-center justify-between gap-3 py-3">
          <div className="flex items-center gap-2">
            <CheckCircle2 className="h-5 w-5 text-[var(--status-passed)] shrink-0" />
            <p className="text-sm text-[var(--status-passed)]">All phases are completed. This release is ready to be marked as Released.</p>
          </div>
          <button
            onClick={markAsReleased}
            disabled={markingReleased}
            className="shrink-0 px-4 py-1.5 text-sm bg-[var(--status-passed-bg)] hover:bg-[var(--status-passed-bg)] disabled:opacity-50 text-[var(--color-text)] rounded-lg font-medium transition-colors"
          >
            {markingReleased ? 'Marking…' : 'Mark as Released'}
          </button>
        </div>
      )}

      {/* Phase timeline */}
      <div className="card">
        <h3 className="text-sm font-semibold text-[var(--color-text)] mb-3">Release Phases</h3>
        <div className="space-y-2">
          {(detail.phases ?? []).map((phase, i) => (
            <div
              id={`phase-${phase.id}`}
              key={phase.id}
              className="scroll-mt-24 flex items-center gap-3 p-2 rounded-lg bg-[var(--color-bg-secondary)]/60 group"
            >
              <div className="flex items-center justify-center h-6 w-6 rounded-full bg-[var(--color-bg-hover)] text-[var(--color-text-muted)] text-xs font-bold flex-shrink-0">
                {i + 1}
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="text-sm font-medium text-[var(--color-text)]">{phase.name}</span>
                  <span className="text-xs text-[var(--color-text-muted)]">{PHASE_TYPES.find(t => t.value === phase.phase_type)?.label ?? phase.phase_type}</span>
                  <span className={clsx('text-xs px-1.5 py-0.5 rounded font-medium', PHASE_STATUS_COLOR[phase.status] ?? PHASE_STATUS_COLOR.pending)}>
                    {phase.status.replace('_', ' ')}
                  </span>
                </div>
                {(phase.planned_start || phase.planned_end) && (
                  <p className="text-xs text-[var(--color-text-muted)] mt-0.5">
                    {fmtDate(phase.planned_start)} → {fmtDate(phase.planned_end)}
                  </p>
                )}
              </div>
              <div className="flex items-center gap-1.5 shrink-0">
                <select
                  value={phase.status}
                  onChange={e => updatePhaseStatus(phase.id, e.target.value)}
                  className="text-xs bg-[var(--color-bg-hover)] border border-[var(--color-border-light)] rounded px-1.5 py-0.5 text-[var(--color-text)] focus:outline-none"
                >
                  <option value="pending">Pending</option>
                  <option value="in_progress">In Progress</option>
                  <option value="completed">Completed</option>
                  <option value="skipped">Skipped</option>
                </select>
                <button
                  onClick={() => {
                    if (confirm(`Remove phase "${phase.name}"?`)) deletePhase(phase.id)
                  }}
                  className="text-[var(--color-text-faint)] hover:text-[var(--status-failed)] transition-colors p-0.5"
                  title="Remove phase"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </div>
            </div>
          ))}
          <AddPhaseRow
            releaseId={releaseId}
            existingPhaseNames={(detail.phases ?? []).map((p: ReleasePhase) => p.name)}
            onSaved={() => refetch()}
          />
        </div>
      </div>

      {/* Linked runs */}
      <div className="card">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold text-[var(--color-text)]">Linked Test Runs ({detail.linked_runs?.length ?? 0})</h3>
          <button
            onClick={() => setShowLinkModal(true)}
            className="flex items-center gap-1.5 text-xs text-[var(--color-text)] hover:text-[var(--color-text-secondary)] transition-colors"
          >
            <Plus className="h-3.5 w-3.5" /> Link Run
          </button>
        </div>
        {(detail.linked_runs?.length ?? 0) === 0 ? (
          <p className="text-sm text-[var(--color-text-muted)] py-4 text-center">No test runs linked yet</p>
        ) : (() => {
          const allLinked = detail.linked_runs ?? []
          const linkedTotalPages = Math.max(1, Math.ceil(allLinked.length / LINKED_PAGE_SIZE))
          // Clamp the current page if data shrinks (e.g. unlink) so we never
          // ask for a page that doesn't exist.
          const safePage = Math.min(linkedPage, linkedTotalPages)
          const start = (safePage - 1) * LINKED_PAGE_SIZE
          const pageRows = allLinked.slice(start, start + LINKED_PAGE_SIZE)
          return (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr>
                  <th className="th text-left">Build</th>
                  <th className="th text-left">Suite</th>
                  <th className="th text-right">Date</th>
                  <th className="th text-right text-[var(--status-passed)]">Passed</th>
                  <th className="th text-right text-[var(--status-failed)]">Failed</th>
                  <th className="th text-right">Pass Rate</th>
                  <th className="th"></th>
                </tr>
              </thead>
              <tbody>
                {pageRows.map((run: LinkedRun) => (
                  <tr key={run.id} className="table-row">
                    <td className="td font-mono text-[var(--color-text-secondary)] text-xs">
                      <button
                        className="text-[var(--color-text)] hover:text-[var(--color-text-secondary)]"
                        onClick={() => navigate(`/runs/${run.id}`)}
                      >
                        {run.build_number ?? run.id.slice(0, 8)}
                      </button>
                    </td>
                    <td className="td">
                      <SuiteBadge primary={run.primary_suite_name} all={run.suite_names} />
                    </td>
                    <td className="td text-right text-xs text-[var(--color-text-muted)]">{fmtDate(run.created_at)}</td>
                    <td className="td text-right tabular-nums text-[var(--status-passed)]">{run.passed_tests}</td>
                    <td className="td text-right tabular-nums text-[var(--status-failed)]">{run.failed_tests}</td>
                    <td className="td text-right tabular-nums text-[var(--color-text-secondary)]">
                      {run.pass_rate != null ? `${Number(run.pass_rate).toFixed(1)}%` : '—'}
                    </td>
                    <td className="td text-right">
                      <button onClick={() => unlinkRun(run.id)} className="text-[var(--color-text-muted)] hover:text-[var(--status-failed)] transition-colors">
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <Pagination
              page={safePage}
              pages={linkedTotalPages}
              total={allLinked.length}
              onChange={setLinkedPage}
            />
          </div>
          )
        })()}
      </div>

      {/* Phase progress / linked runs / readiness as a stage flow — the same
          data as the sections above, collapsed at the end. */}
      <Disclosure title="Pipeline" defaultOpen={false}>
        <WorkflowTimeline
          title="Release workflow"
          subtitle="Phase progress, linked runs, and release readiness"
          stages={workflow.stages}
          events={workflow.events}
          stageOrder={workflow.stageOrder}
          showInspector
        />
      </Disclosure>
    </div>
  )
}

// ── Page ────────────────────────────────────────────────────────────────────

export default function ReleasesPage() {
  const { releaseId } = useParams<{ releaseId: string }>()
  const project   = useProjectStore(s => s.activeProject)
  const projectId = useProjectStore(s => s.activeProjectId)
  const projects = useProjectStore(s => s.projects)

  const { data, isLoading: listLoading, error: releasesError, mutate: refetch } = useReleases()
  const {
    data: routedRelease,
    isLoading: routedReleaseLoading,
    error: routedReleaseError,
    mutate: refetchRoutedRelease,
  } = useRelease(releaseId ?? null)
  // Memoize so the array identity is stable across renders — the downstream
  // `derived` useMemo keys on it. A detail URL resolves through its own
  // authorized fetch, independently of whichever project is persisted in the
  // global picker; the list route remains scoped by that picker.
  const releases: Release[] = useMemo(
    () => releaseId ? (routedRelease ? [routedRelease] : []) : (data?.items ?? []),
    [data, releaseId, routedRelease],
  )
  const isLoading = releaseId ? routedReleaseLoading : listLoading
  const pageError = releaseId ? routedReleaseError : releasesError
  const routedProjectName = projects.find(item => item.id === routedRelease?.project_id)?.name
  const scopeName = releaseId
    ? (routedRelease?.project_name ?? routedProjectName ?? 'the linked project')
    : (project?.name ?? 'this project')

  const navigate = useNavigate()
  const [showModal, setShowModal]     = useState(false)
  const [editRelease, setEditRelease] = useState<Release | undefined>()
  // UX redesign P5 item 5: on the list a release opens in a side panel (a
  // drill-down, §2), not inline under its card. Its own page,
  // `/releases/:releaseId`, shows the same detail as the page itself.
  const [panelId, setPanelId]         = useState<string | null>(null)
  const [statusFilter, setStatusFilter] = useState<string>('all')
  const [search, setSearch] = useState('')

  // Derive the renderer-ready shape once and reuse across the health banner
  // and its disclosure, the list, and the right-rail panels. Pure — no side effects, no extra
  // network calls — so it's cheap to recompute on every render.
  const derived = useMemo(() => releases.map(deriveRelease), [releases])
  const stageCounts = useMemo(() => computeStageCounts(derived), [derived])
  const filteredDerived = useMemo(() => {
    let xs = derived
    if (statusFilter !== 'all') xs = xs.filter(r => r.stage === statusFilter)
    if (search.trim()) {
      const q = search.trim().toLowerCase()
      xs = xs.filter(r =>
        r.name.toLowerCase().includes(q)
        || r.version.toLowerCase().includes(q)
        || r.ownerName.toLowerCase().includes(q),
      )
    }
    return xs
  }, [derived, statusFilter, search])
  // The health banner highlights the in-progress release with the nearest
  // upcoming due date — that's the "what ships next" answer the QA lead
  // wants the page to surface first.
  const highlightedRelease = useMemo(() => {
    const candidates = derived
      .filter(r => r.stage === 'in_progress')
      .sort((a, b) => {
        const aDue = a.dueAt ? new Date(a.dueAt).getTime() : Number.POSITIVE_INFINITY
        const bDue = b.dueAt ? new Date(b.dueAt).getTime() : Number.POSITIVE_INFINITY
        return aDue - bDue
      })
    return candidates[0] ?? null
  }, [derived])
  const inProgressReleases = useMemo(
    () => derived.filter(r => r.stage === 'in_progress'),
    [derived],
  )

  async function deleteRelease(id: string) {
    if (!confirm('Delete this release and all its phases? This cannot be undone.')) return
    try {
      await releasesService.delete(id)
      toast.success('Release deleted')
      if (panelId === id) setPanelId(null)
      if (releaseId === id) {
        navigate('/releases')
        return
      }
      refetch()
    } catch { toast.error('Failed to delete release') }
  }

  function openEdit(source: Release) {
    setEditRelease(source)
    setShowModal(true)
  }

  const activeProjectId = useProjectStore(s => s.activeProjectId)
  const isAllProjects = activeProjectId === ALL_PROJECTS_ID

  // The release in the side panel (list route only). Looked up in the
  // unfiltered list, so a search that hides its card does not close it; a
  // release that is gone (deleted) closes it.
  const panelRelease = !releaseId && panelId ? derived.find(r => r.id === panelId) ?? null : null
  const openPanel = (id: string) => setPanelId(id)
  const closePanel = () => setPanelId(null)

  // Editing needs the release's own project, not the picker's: an edit opened
  // under "All projects" used to render nothing.
  const modalProjectId = editRelease?.project_id ?? (isAllProjects ? null : projectId)
  const releaseModal = showModal && modalProjectId ? (
    <ReleaseModal
      projectId={modalProjectId}
      initial={editRelease}
      onClose={() => { setShowModal(false); setEditRelease(undefined) }}
      // Key off the release that was EDITED, not off the route.
      //
      // `refetch` is the list mutator. The detail data lives under
      // ['release-detail', id] and is read by two different things: the
      // routed /releases/:id view AND `ReleaseDetailPanel` in the side panel
      // on the list route. Refreshing only the routed one fixes the first and
      // leaves the second stale — including the name it passes to
      // CompliancePackPanel — because that key is revalidateOnFocus:false with
      // no interval and the panel does not unmount while the modal is open.
      onSaved={() => Promise.all([
        refetch(),
        editRelease ? appMutate(['release-detail', editRelease.id]) : Promise.resolve(),
      ])}
    />
  ) : null

  // M21: an outage used to render "No releases yet" and a create-release CTA.
  if (pageError && releases.length === 0) {
    const retry = releaseId ? refetchRoutedRelease : refetch
    return <DataUnavailable error={pageError} onRetry={() => void retry()} testId="releases-data-unavailable" />
  }

  if (!releaseId && !project && !isAllProjects) {
    return (
      <EmptyState
        icon={<Package className="h-10 w-10" />}
        title="No project selected"
        description="Select a project from the top bar"
      />
    )
  }

  return (
    <>
      {/* The edit/create dialog sits above the page; while the side panel is
          open it is rendered inside the panel instead (below), so it is not
          drawn under the panel's backdrop. */}
      {!panelRelease && releaseModal}

      <div className="w-full pb-10">
        <PageHeader
          compact
          title="Releases"
          helpTopic={helpTopicParam('/releases')}
          // Active: planning or in progress. It counted every release, the
          // released and cancelled ones too ("11 active" over 4 released and 2
          // cancelled), and the ternary bound the "· in progress · blocked"
          // tail to its last branch only (browser E2E pass, 2026-10-08).
          subtitle={
            `${stageCounts.planning + stageCounts.in_progress} active across `
            + `${isAllProjects && !releaseId ? 'all projects' : scopeName}`
            + ` · ${stageCounts.in_progress} in progress · ${derived.filter(r => r.blockers.some(b => b.severity === 'red')).length} blocked`
          }
          actions={!isAllProjects && !releaseId ? (
            <button
              onClick={() => { setEditRelease(undefined); setShowModal(true) }}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm bg-[var(--color-btn-primary-bg)] hover:bg-[var(--color-btn-primary-hover)] text-[var(--color-btn-primary-text)] rounded-md font-medium"
            >
              <Plus className="h-4 w-4" /> New release
            </button>
          ) : undefined}
        />

        {/* The template (§2, UX redesign P6): header · ONE toolbar row (the
            counted status filter + search) · ONE StatusBanner · the list.
            The verdict band and the KPI strip that stood side by side here
            pushed the list to 466 px; the banner carries the band's sentence
            and the most important counts, and the rest is in the "Release
            health" disclosure under the list. */}

        {/* Counted segmented filter bar */}
        {!isLoading && (
          <div
            className="flex items-center gap-2 mb-3.5 px-3 py-2 rounded-md border flex-wrap"
            style={{ borderColor: 'var(--color-border)', background: 'var(--color-bg-card)' }}
          >
            <div className="flex items-center gap-1">
              {(['all', 'planning', 'in_progress', 'released', 'cancelled'] as const).map(s => {
                const count = s === 'all' ? stageCounts.all : stageCounts[s]
                return (
                  <button
                    key={s}
                    onClick={() => setStatusFilter(s)}
                    className={clsx(
                      'inline-flex items-center gap-1 px-2.5 py-1 rounded-md text-[12px] font-medium transition-colors capitalize',
                      statusFilter === s
                        ? 'bg-[var(--color-accent-muted)] text-[var(--color-text)] border border-[color-mix(in srgb, var(--color-accent) 40%, transparent)]'
                        : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)] border border-transparent',
                    )}
                  >
                    <span>{s === 'all' ? 'All' : s.replace('_', ' ')}</span>
                    <span className="text-[10.5px] text-[var(--color-text-faint)] tabular-nums">{count}</span>
                  </button>
                )
              })}
            </div>
            <div className="ml-auto flex items-center gap-2">
              <label className="inline-flex items-center gap-1.5 px-2 py-1 rounded-md bg-[var(--color-bg)] border border-[var(--color-border)] focus-within:border-[var(--color-ring)]">
                <span className="text-[var(--color-text-muted)]">⌕</span>
                <input
                  type="search"
                  value={search}
                  onChange={e => setSearch(e.target.value)}
                  placeholder="Search releases, owners, versions…"
                  className="bg-transparent outline-none text-[12.5px] text-[var(--color-text)] placeholder:text-[var(--color-text-faint)] w-[260px]"
                />
              </label>
            </div>
          </div>
        )}

        {/* Release health in one line: the release that ships next, its
            gate, and the counts that matter most (all releases, not the
            filtered list). "Open" is the list's drill-down (the side panel);
            on a release's own page the release is already the page. */}
        {!isLoading && (
          <div className="mb-3.5">
            <ReleaseHealthBanner
              highlighted={highlightedRelease}
              inProgressReleases={inProgressReleases}
              releases={derived}
              onOpen={releaseId ? undefined : openPanel}
            />
          </div>
        )}

        {/* Body grid — release list + right rail */}
        {isLoading ? (
          <div className="flex items-center justify-center h-64"><LoadingSpinner size="lg" /></div>
        ) : releases.length === 0 ? (
          <EmptyState
            icon={<Package className="h-8 w-8" />}
            title="No releases yet"
            description={isAllProjects ? 'No releases exist across any project yet' : 'Create your first release or clone from an org template'}
            action={!isAllProjects ? (
              <button
                onClick={() => setShowModal(true)}
                className="mt-4 flex items-center gap-2 px-4 py-2 bg-[var(--color-btn-primary-bg)] hover:bg-[var(--color-btn-primary-hover)] text-[var(--color-btn-primary-text)] rounded-md text-sm font-medium mx-auto"
              >
                <Plus className="h-4 w-4" /> Create release
              </button>
            ) : undefined}
          />
        ) : (
          <div className="grid gap-[18px]" style={{ gridTemplateColumns: 'minmax(0, 1fr) 340px' }}>
            {/* Left — release cards (on a release's own page: its card and its
                detail), then the release health detail, collapsed. */}
            <div className="flex flex-col gap-3.5 min-w-0">
              <div data-primary="" className="flex flex-col gap-2.5 min-w-0">
                {filteredDerived.length === 0 ? (
                  <div
                    className="rounded-md border px-4 py-3 text-[12.5px] text-[var(--color-text-muted)] flex items-center justify-between"
                    style={{ borderColor: 'var(--color-border)', background: 'var(--color-bg-card)' }}
                  >
                    <span>No releases match these filters.</span>
                    <button
                      type="button"
                      onClick={() => { setStatusFilter('all'); setSearch('') }}
                      className="text-[var(--color-accent)] hover:underline"
                    >
                      Clear filters
                    </button>
                  </div>
                ) : releaseId ? (
                  filteredDerived.map(r => (
                    <div key={r.id} data-release-page="">
                      <ReleaseCard release={r} />
                      {/* The release's own page: its detail is the page, not a drill-down. */}
                      <div
                        className="mt-2 rounded-xl border p-4"
                        style={{ borderColor: 'var(--color-border)', background: 'var(--color-bg-card)' }}
                      >
                        {r.source.description && (
                          <p className="text-sm text-[var(--color-text-muted)] mb-3">{r.source.description}</p>
                        )}
                        <div className="flex justify-end gap-2 mb-3">
                          <button
                            onClick={() => openEdit(r.source)}
                            className="text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)] px-2 py-1 rounded hover:bg-[var(--color-bg-hover)] transition-colors"
                          >
                            Edit
                          </button>
                          <button
                            onClick={() => deleteRelease(r.id)}
                            className="text-xs text-[var(--status-failed)]/70 hover:text-[var(--status-failed)] px-2 py-1 rounded hover:bg-[var(--status-failed-bg)]/10 transition-colors"
                          >
                            Delete
                          </button>
                        </div>
                        <ReleaseDetailPanel releaseId={r.id} />
                      </div>
                    </div>
                  ))
                ) : (
                  filteredDerived.map(r => (
                    <ReleaseCard key={r.id} release={r} onClick={() => openPanel(r.id)} />
                  ))
                )}
              </div>
              <ReleaseHealthDetails highlighted={highlightedRelease} releases={derived} />
            </div>

            {/* Right — derived panels */}
            <div className="flex flex-col gap-3.5 min-w-0">
              <ShippingThisWeek releases={derived} onOpen={openPanel} />
              <AgingSignals releases={derived} onOpen={openPanel} />
              <CompliancePacks releases={derived} onOpen={openPanel} />
              <RecentActivity releases={derived} />
            </div>
          </div>
        )}
      </div>

      {/* A release's detail, beside the list. Modal: the page keeps its
          layout (a non-modal panel reserves its width, and this page's
          340 px right rail would squeeze the cards to a sliver), and the
          dialogs opened from it (edit, link a run) stack above it. */}
      <SidePanel
        open={panelRelease !== null}
        modal
        onClose={closePanel}
        title={panelRelease ? releaseTitle(panelRelease) : 'Release'}
        width={640}
        closeLabel="Close release"
        footer={panelRelease && (
          <div className="flex items-center gap-2" data-release-panel-actions="">
            <button
              type="button"
              onClick={() => openEdit(panelRelease.source)}
              className="btn-secondary inline-flex items-center gap-1 text-xs"
            >
              <Pencil className="h-3.5 w-3.5" aria-hidden="true" /> Edit
            </button>
            <button
              type="button"
              onClick={() => deleteRelease(panelRelease.id)}
              className="inline-flex items-center gap-1 rounded-md px-2.5 py-1.5 text-xs text-[var(--status-failed)] hover:bg-[var(--status-failed-bg)]/10"
            >
              <Trash2 className="h-3.5 w-3.5" aria-hidden="true" /> Delete
            </button>
            <Link
              to={`/releases/${panelRelease.id}`}
              onClick={closePanel}
              className="ml-auto inline-flex items-center gap-1 text-xs font-medium text-[var(--color-accent)] hover:underline"
            >
              Open full page <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
            </Link>
          </div>
        )}
      >
        {panelRelease && (
          <div data-release-panel={panelRelease.id} className="space-y-3">
            {panelRelease.source.description && (
              <p className="text-sm text-[var(--color-text-muted)]">{panelRelease.source.description}</p>
            )}
            {/* Keyed: another release starts on its own detail, with its own state. */}
            <ReleaseDetailPanel key={panelRelease.id} releaseId={panelRelease.id} />
            {releaseModal}
          </div>
        )}
      </SidePanel>
    </>
  )
}

/** The side panel's heading: the name, and the version when it says something the name does not. */
function releaseTitle(release: { name: string; version: string }): string {
  return release.version && release.version !== release.name ? `${release.name} ${release.version}` : release.name
}
