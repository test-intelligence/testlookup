import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Calendar, CheckCircle2, Package, Plus, Trash2, X,
} from 'lucide-react'
import { clsx } from 'clsx'
import toast from 'react-hot-toast'
import PageHeader from '@/components/ui/PageHeader'
import EmptyState from '@/components/ui/EmptyState'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import Pagination from '@/components/ui/Pagination'
import SuiteBadge from '@/components/ui/SuiteBadge'
import WorkflowTimeline from '@/components/workflow/WorkflowTimeline'
import CompliancePackPanel from '@/components/compliance/CompliancePackPanel'
import { buildReleaseWorkflow } from '@/components/workflow/workflowPresets'
import { useReleases, useRelease } from '@/hooks/useReleases'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'
import { releasesService } from '@/services/releasesService'
import { useRuns } from '@/hooks/useRuns'
import type { LinkedRun, Release, ReleasePhase } from '@/types/releases'
import { deriveRelease, computeStageCounts } from '@/components/releases/mapping'
import VerdictBand from '@/components/releases/VerdictBand'
import KpiStrip from '@/components/releases/KpiStrip'
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
  pending:     'bg-neutral-700/20 text-[var(--color-text-muted)]',
  in_progress: 'bg-amber-500/20 text-amber-400',
  completed:   'bg-emerald-500/20 text-emerald-400',
  skipped:     'bg-neutral-700/10 text-[var(--color-text-muted)]',
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

function ReleaseModal({ projectId, onClose, onSaved, initial }: ReleaseModalProps) {
  const [name, setName]           = useState(initial?.name ?? '')
  const [version, setVersion]     = useState(initial?.version ?? '')
  const [description, setDescription] = useState(initial?.description ?? '')
  const [status, setStatus]       = useState(initial?.status ?? 'planning')
  const [plannedDate, setPlanned] = useState(
    initial?.planned_date ? initial.planned_date.slice(0, 10) : '',
  )
  const [saving, setSaving] = useState(false)

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
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-[var(--color-bg)]/60" onClick={onClose}>
      <div className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl p-6 w-full max-w-lg shadow-2xl" onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-5">
          <h2 className="text-base font-semibold text-[var(--color-text)]">{initial ? 'Edit Release' : 'New Release'}</h2>
          <button onClick={onClose} className="text-[var(--color-text-muted)] hover:text-[var(--color-text)]"><X className="h-4 w-4" /></button>
        </div>
        <div className="space-y-3">
          <div>
            <label className="block text-xs text-[var(--color-text-muted)] mb-1">Release Name *</label>
            <input
              value={name} onChange={e => setName(e.target.value)}
              placeholder="e.g. v2.4.0 — Login Revamp"
              className="input w-full"
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs text-[var(--color-text-muted)] mb-1">Version</label>
              <input value={version} onChange={e => setVersion(e.target.value)} placeholder="e.g. 2.4.0" className="input w-full" />
            </div>
            <div>
              <label className="block text-xs text-[var(--color-text-muted)] mb-1">Status</label>
              <select value={status} onChange={e => setStatus(e.target.value)} className="input w-full">
                <option value="planning">Planning</option>
                <option value="in_progress">In Progress</option>
                <option value="released">Released</option>
                <option value="cancelled">Cancelled</option>
              </select>
            </div>
          </div>
          <div>
            <label className="block text-xs text-[var(--color-text-muted)] mb-1">Planned Release Date</label>
            <input type="date" value={plannedDate} onChange={e => setPlanned(e.target.value)} className="input w-full" />
          </div>
          <div>
            <label className="block text-xs text-[var(--color-text-muted)] mb-1">Description</label>
            <textarea
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

function LinkRunModal({ releaseId, phases, onClose, onSaved }: {
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
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-[var(--color-bg)]/60" onClick={onClose}>
      <div className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl p-6 w-full max-w-md shadow-2xl" onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-base font-semibold text-[var(--color-text)]">Link Test Run</h2>
          <button onClick={onClose} className="text-[var(--color-text-muted)] hover:text-[var(--color-text)]"><X className="h-4 w-4" /></button>
        </div>
        <div className="space-y-3">
          <div>
            <label className="block text-xs text-[var(--color-text-muted)] mb-1">Test Run</label>
            <select value={selectedRun} onChange={e => setSelectedRun(e.target.value)} className="input w-full">
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
              <label className="block text-xs text-[var(--color-text-muted)] mb-1">Phase (optional)</label>
              <select value={selectedPhase} onChange={e => setSelectedPhase(e.target.value)} className="input w-full">
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
          <label className="block text-[10px] text-[var(--color-text-muted)] mb-1">Phase Name</label>
          <input
            value={name}
            onChange={e => setName(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="e.g. QA Testing"
            autoFocus
            className="bg-[var(--color-bg-card)] border border-[var(--color-border)] text-[var(--color-text)] rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-[var(--color-ring)] focus:border-neutral-500 placeholder:text-[var(--color-text-muted)] w-full"
          />
        </div>
        <div>
          <label className="block text-[10px] text-[var(--color-text-muted)] mb-1">Type</label>
          <select
            value={type}
            onChange={e => setType(e.target.value)}
            className="bg-[var(--color-bg-card)] border border-[var(--color-border)] text-[var(--color-text)] rounded-lg px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-[var(--color-ring)]"
          >
            {PHASE_TYPES.map(pt => <option key={pt.value} value={pt.value}>{pt.label}</option>)}
          </select>
        </div>
      </div>
      {isDuplicate && (
        <p className="text-xs text-red-400">A phase with this name already exists in this release.</p>
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

function ReleaseDetailPanel({ releaseId, onEdit: _onEdit, projectId: _projectId }: {
  releaseId: string
  projectId: string
  onEdit: (r: Release) => void
}) {
  const navigate = useNavigate()
  const { data: detail, isLoading, mutate: refetch } = useRelease(releaseId)
  const [showLinkModal, setShowLinkModal] = useState(false)
  const [markingReleased, setMarkingReleased] = useState(false)
  // Pagination for the linked-runs table — 25 rows per page, client-side
  // slice. The release summary above still aggregates across every linked
  // run because it reads ``detail.metrics`` from the API, not this slice.
  const LINKED_PAGE_SIZE = 25
  const [linkedPage, setLinkedPage] = useState(1)

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
  const passRate = m.avg_pass_rate != null ? Number(m.avg_pass_rate).toFixed(1) : '—'
  const passColor = m.avg_pass_rate == null ? 'text-[var(--color-text-muted)]'
    : m.avg_pass_rate >= 90 ? 'text-emerald-400'
    : m.avg_pass_rate >= 70 ? 'text-amber-400'
    : 'text-red-400'

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
          { label: 'Passed',  value: m.total_passed ?? 0,  color: 'text-emerald-400' },
          { label: 'Failed',  value: m.total_failed ?? 0,  color: 'text-red-400' },
          { label: 'Pass Rate', value: `${passRate}%`,     color: passColor },
        ].map(({ label, value, color }) => (
          <div key={label} className="card py-2.5">
            <p className="text-[10px] text-[var(--color-text-muted)] uppercase tracking-wider">{label}</p>
            <p className={clsx('text-xl font-bold tabular-nums mt-0.5', color)}>{value}</p>
          </div>
        ))}
      </div>

      <WorkflowTimeline
        title="Release workflow"
        subtitle="Phase progress, linked runs, and release readiness"
        stages={workflow.stages}
        events={workflow.events}
        stageOrder={workflow.stageOrder}
        compact
        showInspector
      />

      {/* Tier 1 item 4 — Compliance export pack for audit reviewers. */}
      <CompliancePackPanel releaseId={releaseId} releaseName={detail.name} />

      {/* Auto-complete banner — shown when all phases done and release not yet released */}
      {detail.status !== 'released' && detail.status !== 'cancelled' && (detail.phases ?? []).length > 0 &&
        (detail.phases ?? []).every((p: ReleasePhase) => p.status === 'completed' || p.status === 'skipped') && (
        <div className="card border border-emerald-700/40 bg-emerald-900/20 flex items-center justify-between gap-3 py-3">
          <div className="flex items-center gap-2">
            <CheckCircle2 className="h-5 w-5 text-emerald-400 shrink-0" />
            <p className="text-sm text-emerald-300">All phases are completed. This release is ready to be marked as Released.</p>
          </div>
          <button
            onClick={markAsReleased}
            disabled={markingReleased}
            className="shrink-0 px-4 py-1.5 text-sm bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-[var(--color-text)] rounded-lg font-medium transition-colors"
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
            <div key={phase.id} className="flex items-center gap-3 p-2 rounded-lg bg-[var(--color-bg-secondary)]/60 group">
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
                  className="text-[var(--color-text-faint)] hover:text-red-400 transition-colors p-0.5"
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
                  <th className="th text-right text-emerald-400">Passed</th>
                  <th className="th text-right text-red-400">Failed</th>
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
                    <td className="td text-right tabular-nums text-emerald-400">{run.passed_tests}</td>
                    <td className="td text-right tabular-nums text-red-400">{run.failed_tests}</td>
                    <td className="td text-right tabular-nums text-[var(--color-text-secondary)]">
                      {run.pass_rate != null ? `${Number(run.pass_rate).toFixed(1)}%` : '—'}
                    </td>
                    <td className="td text-right">
                      <button onClick={() => unlinkRun(run.id)} className="text-[var(--color-text-muted)] hover:text-red-400 transition-colors">
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
    </div>
  )
}

// ── Page ────────────────────────────────────────────────────────────────────

export default function ReleasesPage() {
  const project   = useProjectStore(s => s.activeProject)
  const projectId = useProjectStore(s => s.activeProjectId)

  const { data, isLoading, mutate: refetch } = useReleases()
  const releases: Release[] = data?.items ?? []

  const [showModal, setShowModal]     = useState(false)
  const [editRelease, setEditRelease] = useState<Release | undefined>()
  const [expandedId, setExpandedId]   = useState<string | null>(null)
  const [statusFilter, setStatusFilter] = useState<string>('all')
  const [search, setSearch] = useState('')

  // Derive the renderer-ready shape once and reuse across the verdict band,
  // KPIs, list, and right-rail panels. Pure — no side effects, no extra
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
  // The verdict band highlights the in-progress release with the nearest
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
      if (expandedId === id) setExpandedId(null)
      refetch()
    } catch { toast.error('Failed to delete release') }
  }

  const activeProjectId = useProjectStore(s => s.activeProjectId)
  const isAllProjects = activeProjectId === ALL_PROJECTS_ID

  if (!project && !isAllProjects) {
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
      {showModal && projectId && !isAllProjects && (
        <ReleaseModal
          projectId={projectId}
          initial={editRelease}
          onClose={() => { setShowModal(false); setEditRelease(undefined) }}
          onSaved={() => refetch()}
        />
      )}

      <div className="w-full pb-10">
        <PageHeader
          title="Releases"
          subtitle={
            isAllProjects
              ? `${stageCounts.all} active across all projects`
              : `${stageCounts.all} active across ${project?.name ?? 'this project'}`
            + ` · ${stageCounts.in_progress} in progress · ${derived.filter(r => r.blockers.some(b => b.severity === 'red')).length} blocked`
          }
          actions={
            <div className="flex items-center gap-2 flex-wrap">
              <button
                type="button"
                onClick={() => toast('Export schedule — coming in next iteration', { icon: '📅' })}
                className="inline-flex items-center gap-1.5 text-[12.5px] px-2.5 py-1.5 rounded-md border border-[var(--color-border)] text-[var(--color-text-secondary)] hover:text-[var(--color-text)] hover:border-[var(--color-border-light)]"
              >
                <Calendar className="h-3.5 w-3.5" /> Export schedule
              </button>
              <button
                type="button"
                onClick={() => toast('Calendar view — coming in next iteration', { icon: '🗓' })}
                className="inline-flex items-center gap-1.5 text-[12.5px] px-2.5 py-1.5 rounded-md border border-[var(--color-border)] text-[var(--color-text-secondary)] hover:text-[var(--color-text)] hover:border-[var(--color-border-light)]"
              >
                <Calendar className="h-3.5 w-3.5" /> Calendar view
              </button>
              {!isAllProjects && (
                <button
                  onClick={() => { setEditRelease(undefined); setShowModal(true) }}
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm bg-[var(--color-btn-primary-bg)] hover:bg-[var(--color-btn-primary-hover)] text-[var(--color-btn-primary-text)] rounded-md font-medium"
                >
                  <Plus className="h-4 w-4" /> New release
                </button>
              )}
            </div>
          }
        />

        {/* Top strip — verdict band + KPIs */}
        {!isLoading && (
          <div className="grid gap-3.5 mb-3.5" style={{ gridTemplateColumns: 'minmax(0, 1.35fr) minmax(0, 2fr)' }}>
            <VerdictBand highlighted={highlightedRelease} inProgressReleases={inProgressReleases} />
            <KpiStrip releases={derived} />
          </div>
        )}

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
                        ? 'bg-[var(--color-accent-muted)] text-[var(--color-text)] border border-[rgba(68,147,248,0.40)]'
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
            {/* Left — release cards */}
            <div className="flex flex-col gap-2.5 min-w-0">
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
              ) : (
                filteredDerived.map(r => (
                  <div key={r.id}>
                    <ReleaseCard
                      release={r}
                      onClick={() => setExpandedId(expandedId === r.id ? null : r.id)}
                    />
                    {/* Inline detail panel — preserves the existing
                        expand-to-view-runs flow without forcing a per-release
                        route this redesign explicitly defers. */}
                    {expandedId === r.id && (
                      <div
                        className="mt-2 rounded-xl border p-4"
                        style={{ borderColor: 'var(--color-border)', background: 'var(--color-bg-card)' }}
                      >
                        {r.source.description && (
                          <p className="text-sm text-[var(--color-text-muted)] mb-3">{r.source.description}</p>
                        )}
                        <div className="flex justify-end gap-2 mb-3">
                          <button
                            onClick={() => { setEditRelease(r.source); setShowModal(true) }}
                            className="text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)] px-2 py-1 rounded hover:bg-[var(--color-bg-hover)] transition-colors"
                          >
                            Edit
                          </button>
                          <button
                            onClick={() => deleteRelease(r.id)}
                            className="text-xs text-red-500/70 hover:text-red-400 px-2 py-1 rounded hover:bg-red-500/10 transition-colors"
                          >
                            Delete
                          </button>
                        </div>
                        <ReleaseDetailPanel
                          releaseId={r.id}
                          projectId={projectId ?? ''}
                          onEdit={src => { setEditRelease(src); setShowModal(true) }}
                        />
                      </div>
                    )}
                  </div>
                ))
              )}
            </div>

            {/* Right — derived panels */}
            <div className="flex flex-col gap-3.5 min-w-0">
              <ShippingThisWeek releases={derived} onOpen={(id) => setExpandedId(expandedId === id ? null : id)} />
              <AgingSignals releases={derived} onOpen={(id) => setExpandedId(expandedId === id ? null : id)} />
              <CompliancePacks releases={derived} />
              <RecentActivity releases={derived} />
            </div>
          </div>
        )}
      </div>
    </>
  )
}
