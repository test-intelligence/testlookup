/**
 * Releases (`/releases`) and one release's page (`/releases/:releaseId`).
 *
 * UX redesign P5 item 5: on the list, a release's detail is a drill-down beside
 * the list ("Open full page" goes to `/releases/:releaseId`), not an inline
 * expansion that pushed every card below it down the page. The two header
 * stub controls were removed then (what is not built is not rendered, P2);
 * W11 built them: the header's List | Calendar toggle (the view rides the URL,
 * `?view=calendar&month=YYYY-MM`; the calendar replaces the cards and reads
 * the list the page already holds) and the ⋯ menu's schedule exports (.ics /
 * .csv, an authenticated download: no token in any URL) and Attribution rules
 * (one project selected). A QA lead copies a release's phases from another
 * release or drafts them from a PRD (REL-05 / REL-07) from a planning card or
 * the release's "Release Phases" while it has none.
 *
 * UX redesign P6 (the fold budget, `02-design-spec.md` §2): above the list,
 * one toolbar row (the counted status filter and search) and ONE
 * StatusBanner (`ReleaseHealthBanner`) instead of a verdict band beside a KPI
 * strip; the band's lede, the release's top blockers and the four count cards
 * are in the collapsed "Release health · this week" disclosure under the list.
 *
 * W05 "release-gate": a release shows its CURRENT recorded gate verdict; a QA
 * lead records one with Evaluate gate (a card, the gate panel) or Evaluate
 * all in progress (the toolbar). The detail has the gate panel (`#gate`) and
 * each phase's live gate chip; a phase-gate 409 opens the reason form
 * (REL-10). A QA lead can make a release the active one and sync releases
 * from GitHub / Jira (REL-08); a release's owner is set in its editor (REL-02).
 *
 * W27 PAN-02 (the in-page rule): nothing here opens over the page. On the
 * list a release opens in a pane BESIDE the cards (`MasterDetail`), in the
 * right rail's place, and rides the URL (`?open=<id>`: a refresh or a
 * shared link reopens it, Back closes it); an id that is malformed, unknown
 * or not the reader's shows the pane's not-found card. Every form opens where
 * its action is, one at a time per section (`useInlineSlot`): New release and
 * Sync releases under the health banner; Edit, Link Run and a phase's reason
 * inside the release. Delete, Remove phase and Set as active release ask in an
 * inline strip (`InlineConfirm`) with the old prompt's sentence; only the
 * strip's button sends the request.
 */
import { Fragment, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { Link, useLocation, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import {
  CheckCircle2, ExternalLink, Package, Pencil, Plus, RefreshCw, RotateCcw, Trash2, TriangleAlert,
} from 'lucide-react'
import { clsx } from 'clsx'
import toast from 'react-hot-toast'
import PageHeader from '@/components/ui/PageHeader'
import { MasterDetail } from '@/components/ui/inPage/MasterDetail'
import { DetailPane } from '@/components/ui/inPage/DetailPane'
import { InlineFormSection } from '@/components/ui/inPage/InlineFormSection'
import { InlineConfirm } from '@/components/ui/inPage/InlineConfirm'
import { useInlineSlot, type InlineSlot } from '@/components/ui/inPage/useInlineSlot'
import { useDetailParam } from '@/components/ui/inPage/useDetailParam'
import { isDetailToken } from '@/components/ui/inPage/ids'
import { SectionErrorBoundary } from '@/components/ui/SectionErrorBoundary'
import { helpTopicParam } from '@/components/help/helpTopics'
import { appMutate } from '@/utils/swrCacheMutate'
import { formatCompactDateTime, formatDay } from '@/utils/formatters'
import EmptyState from '@/components/ui/EmptyState'
import DataUnavailable from '@/components/ui/DataUnavailable'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import Pagination from '@/components/ui/Pagination'
import SuiteBadge from '@/components/ui/SuiteBadge'
import WorkflowTimeline from '@/components/workflow/WorkflowTimeline'
import Disclosure from '@/components/ui/Disclosure'
import CompliancePackPanel from '@/components/compliance/CompliancePackPanel'
import ReleaseHoldStrip from '@/components/retention/ReleaseHoldStrip'
import { buildReleaseWorkflow } from '@/components/workflow/workflowPresets'
import { useReleases, useRelease } from '@/hooks/useReleases'
import { usePhaseGates } from '@/hooks/useReleaseGate'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'
import { releasesService, type PhaseUpdateResult } from '@/services/releasesService'
import { extractErrorMessage } from '@/services/apiErrors'
import { useRuns } from '@/hooks/useRuns'
import { usePermissions } from '@/hooks/usePermissions'
import type { LinkedRun, Release, ReleasePhase } from '@/types/releases'
import { deriveRelease, computeStageCounts } from '@/components/releases/mapping'
import type { DerivedRelease } from '@/components/releases/types'
import { ReleaseHealthBanner, ReleaseHealthDetails } from '@/components/releases/ReleaseHealth'
import ReleaseCard, { type PlanPhasesMode } from '@/components/releases/ReleaseCard'
import ClonePhasesForm, { type ClonePhasesFormProps } from '@/components/releases/ClonePhasesForm'
import DraftPhasesForm from '@/components/releases/DraftPhasesForm'
import ReleaseCalendar from '@/components/releases/ReleaseCalendar'
import { parseMonthParam } from '@/components/releases/releaseCalendarModel'
import { useNow } from '@/hooks/useNow'
import { ShippingThisWeek, AgingSignals, CompliancePacks, RecentActivity } from '@/components/releases/RightRail'
import GatePanel from '@/components/releases/GatePanel'
import PhaseGateChip from '@/components/releases/PhaseGateChip'
import PhaseReasonDialog from '@/components/releases/PhaseReasonDialog'
import SyncReleasesDialog from '@/components/releases/SyncReleasesDialog'
import OwnerSelect from '@/components/releases/OwnerSelect'
import {
  evaluateAllPartialToast, evaluateAllToast, evaluationToast, verdictWord,
} from '@/components/releases/gateCopy'

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

/**
 * Deleting a release, removing a phase and unlinking a run are ADMIN on the
 * API (`routers/releases.py`); the rest of this page is QA lead. A QA lead was
 * offered all three and got a 403 from each.
 */
const ADMIN_REQUIRED = 'Admin role required'

/** The list route's URL key for the release open in the pane (W27 PAN-02; see the page). */
const RELEASE_PANE_PARAM = 'open'

// ── Helpers ─────────────────────────────────────────────────────────────────

function fmtDate(iso: string | null | undefined) {
  return formatDay(iso)
}

type RequestError = { message?: string; response?: { status?: number; data?: { detail?: unknown } } }

/** The server's own sentence for a failed request (the interceptor's wording when it toasts). */
function serverDetail(err: unknown, fallback: string): string {
  const e = err as RequestError
  return extractErrorMessage(e?.response?.data?.detail, e?.message || fallback)
}

/**
 * Revalidate one release's detail and gate reads after a write. The list is
 * the caller's own mutate; these keys are read by the pane beside the list
 * AND the routed page, and neither polls.
 */
function revalidateReleaseReads(releaseId: string) {
  return Promise.all([
    appMutate(['release-detail', releaseId]),
    appMutate(['release-gate', releaseId]),
    appMutate(['release-phase-gates', releaseId]),
  ])
}

// ── Create / Edit Release form ──────────────────────────────────────────────

interface ReleaseModalProps {
  projectId: string
  onClose: () => void
  onSaved: () => void
  initial?: Release
  /** The section's form slot (one open form at a time; a typed form asks before it is replaced). */
  slot?: InlineSlot
}

/**
 * The release editor (New / Edit), in the page (W27 PAN-02): New opens under
 * the health banner, Edit inside the release. Kept from the dialog: its name
 * (`New Release` / `Edit Release`), the heading id, the X's label, every
 * field, the request and the order of `onSaved` then `onClose`.
 */
export function ReleaseModal({ projectId, onClose, onSaved, initial, slot }: ReleaseModalProps) {
  const [name, setName]           = useState(initial?.name ?? '')
  const [version, setVersion]     = useState(initial?.version ?? '')
  const [description, setDescription] = useState(initial?.description ?? '')
  const [status, setStatus]       = useState(initial?.status ?? 'planning')
  const [plannedDate, setPlanned] = useState(
    initial?.planned_date ? initial.planned_date.slice(0, 10) : '',
  )
  // REL-02: the owner as the server resolved it ('' = No owner). Editing only:
  // a new release's owner defaults to its creator on the server, and the
  // member list is asked for only when somebody opens an editor.
  const initialOwnerId = initial?.owner?.id ?? ''
  const [ownerId, setOwnerId] = useState(initialOwnerId)
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
          // Sent only when it changed: re-sending the owner is not an edit,
          // and "" (No owner) clears it.
          ...(ownerId !== initialOwnerId ? { owner_user_id: ownerId || null } : {}),
        })
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
    <InlineFormSection
      title={initial ? 'Edit Release' : 'New Release'}
      titleId="release-form-title"
      closeLabel="Close release dialog"
      onClose={onClose}
      canClose={!saving}
      slot={slot}
      footer={(
        <>
          <button onClick={onClose} className="px-4 py-2 text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)]">Cancel</button>
          <button
            onClick={save} disabled={saving}
            className="btn-primary flex items-center gap-2"
          >
            {saving && <LoadingSpinner size="sm" />}
            {initial ? 'Save Changes' : 'Create Release'}
          </button>
        </>
      )}
    >
      <div className="max-w-2xl space-y-3">
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
        {initial && (
          <div>
            <label htmlFor="release-field-owner" className="block text-xs text-[var(--color-text-muted)] mb-1">Owner</label>
            <OwnerSelect
              id="release-field-owner"
              projectId={projectId}
              value={ownerId}
              onChange={setOwnerId}
              currentOwner={initial.owner ?? null}
            />
          </div>
        )}
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
    </InlineFormSection>
  )
}

// ── Link Run form ───────────────────────────────────────────────────────────

/**
 * Link a run to the release, in the page (W27 PAN-02): it opens under the
 * "Linked Test Runs" card's header. Kept from the dialog: its name (`Link Test
 * Run`), the heading id, the X's label, the fields and the request.
 */
export function LinkRunModal({ releaseId, phases, onClose, onSaved, slot }: {
  releaseId: string
  phases: ReleasePhase[]
  onClose: () => void
  onSaved: () => void
  /** The release's form slot (one open form at a time in a release's detail). */
  slot?: InlineSlot
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
    <InlineFormSection
      title="Link Test Run"
      titleId="link-test-run-title"
      closeLabel="Close link test run dialog"
      onClose={onClose}
      canClose={!saving}
      slot={slot}
      footer={(
        <>
          <button onClick={onClose} className="px-4 py-2 text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)]">Cancel</button>
          <button
            onClick={link} disabled={saving || !selectedRun}
            className="btn-primary flex items-center gap-2"
          >
            {saving && <LoadingSpinner size="sm" />}
            Link Run
          </button>
        </>
      )}
    >
      <div className="max-w-2xl space-y-3">
        <div>
          <label htmlFor="release-field-5" className="block text-xs text-[var(--color-text-muted)] mb-1">Test Run</label>
          <select id="release-field-5" value={selectedRun} onChange={e => setSelectedRun(e.target.value)} className="input w-full">
            <option value="">— Select run —</option>
            {(runs as Array<{ id: string; build_number?: number; created_at: string }>).map(r => (
              <option key={r.id} value={r.id}>
                {r.build_number ?? r.id.slice(0, 8)} — {formatDay(r.created_at)}
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
    </InlineFormSection>
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
            className="btn-primary btn-sm"
          >
            {saving ? 'Adding…' : 'Add Phase'}
          </button>
        </div>
      </div>
    </div>
  )
}

/**
 * Copy phases from… (REL-05) with its sources: the release list the page
 * already holds (the top bar's cached entry: no request of its own), read only
 * while the form is open.
 */
function ClonePhasesHost(props: Omit<ClonePhasesFormProps, 'releases'>) {
  const { data } = useReleases(undefined, { cached: true })
  return <ClonePhasesForm {...props} releases={data?.items ?? []} />
}

// ── Release Detail Panel ────────────────────────────────────────────────────

/** A phase transition the server refused until a reason travels with it (REL-10). */
interface PendingPhaseReason {
  phase: ReleasePhase
  status: 'completed' | 'skipped'
  /** The 409's detail, verbatim. */
  message: string
}

/**
 * A release's detail: metrics, the release gate, compliance packs, production
 * outcome, phases, linked runs, and the collapsed pipeline. The same body in
 * the pane beside the list (`/releases?open=<id>`) and on the release's own
 * page (`/releases/:releaseId`).
 *
 * `onEvaluate` is the page's (QA lead only): the card and the gate panel
 * record a verdict through one handler, with one toast.
 *
 * Its forms open in place, through the release's `slot` (one at a time; the
 * Edit form the page opens shares it): Link Run under the "Linked Test Runs"
 * header, a phase gate's reason under that phase's row. Remove phase asks in a
 * strip under the row.
 */
function ReleaseDetailPanel({ releaseId, onEvaluate, evaluating = false, slot, inPane = false, onPhasesChanged }: {
  releaseId: string
  onEvaluate?: () => void
  evaluating?: boolean
  /** The release's form slot (the pane's, or the release page's). */
  slot?: InlineSlot
  /**
   * In the pane beside the list (≥ 440 px wide): the metrics strip fits its
   * tiles to the pane. `sm:grid-cols-5` is a VIEWPORT breakpoint, which would
   * force five tiles into the pane at any desktop width.
   */
  inPane?: boolean
  /** W11: phases were copied or created in bulk; the page re-reads its list. */
  onPhasesChanged?: () => void
}) {
  const navigate = useNavigate()
  const { hash } = useLocation()
  const { isAdmin, isQaLead } = usePermissions()
  const { data: detail, isLoading, mutate: refetch } = useRelease(releaseId)
  // Each phase's LIVE gate, for the chips in the phase rows (D-2: never on
  // the cards, where N cards x M phases per poll is not affordable).
  const { data: phaseGates } = usePhaseGates(detail ? releaseId : null)
  const ownSlot = useInlineSlot()
  const formSlot = slot ?? ownSlot
  // The slot's latest state, for a form opened after a request returns (the
  // phase-gate 409): the render that sent it may hold a stale `open`.
  const latestSlot = useRef(formSlot)
  useEffect(() => { latestSlot.current = formSlot })
  // Keys are scoped to this release, so a key left in a shared slot never
  // opens a form in another release's detail.
  const linkKey = `link:${releaseId}`
  const reasonKey = (phaseId: string) => `reason:${releaseId}:${phaseId}`
  // W11 REL-05 / REL-07: the two first-schedule forms (the card's buttons open them by these keys too).
  const cloneKey = `clone:${releaseId}`
  const draftKey = `draft:${releaseId}`
  const [confirmingPhase, setConfirmingPhase] = useState<string | null>(null)
  const [markingReleased, setMarkingReleased] = useState(false)
  const [outcomeReason, setOutcomeReason] = useState('')
  const [markingOutcome, setMarkingOutcome] = useState<'incident' | 'rollback' | null>(null)
  const [pendingReason, setPendingReason] = useState<PendingPhaseReason | null>(null)
  const [sendingReason, setSendingReason] = useState(false)
  const [recordingPhaseId, setRecordingPhaseId] = useState<string | null>(null)
  // Pagination for the linked-runs table — 25 rows per page, client-side
  // slice. The release summary above still aggregates across every linked
  // run because it reads ``detail.metrics`` from the API, not this slice.
  const LINKED_PAGE_SIZE = 25
  const [linkedPage, setLinkedPage] = useState(1)

  // A deep link to a phase (`#phase-<id>`) or to the gate (`#gate`, a
  // blocker's "Open gate →") scrolls its section into view.
  useEffect(() => {
    if (!detail) return
    if (hash.startsWith('#phase-')) {
      const targetId = decodeURIComponent(hash.slice(1))
      document.getElementById(targetId)?.scrollIntoView({ block: 'center' })
    } else if (hash === '#gate') {
      document.getElementById('gate')?.scrollIntoView({ block: 'start' })
    }
  }, [detail, hash])

  const phaseGateById = useMemo(
    () => new Map((phaseGates?.phases ?? []).map(p => [p.phase_id, p])),
    [phaseGates],
  )

  function phaseUpdated(result: PhaseUpdateResult | undefined) {
    void refetch()
    void appMutate(['release-phase-gates', releaseId])
    if (result?.all_phases_completed) {
      toast.success('All phases completed! You can now mark this release as Released.')
    }
  }

  /**
   * Move a phase on. The first attempt is quiet (`suppressToast`): with
   * phase-gate enforcement on, completing a phase whose gate is not GO, or
   * skipping one, answers 409 with the server's reason, and that opens the
   * reason form (under the phase's row) instead of a toast. Any other failure
   * toasts the server's own sentence.
   */
  async function updatePhaseStatus(phase: ReleasePhase, status: string) {
    try {
      const result = await releasesService.updatePhase(releaseId, phase.id, { status }, { suppressToast: true })
      phaseUpdated(result)
    } catch (err: unknown) {
      const e = err as RequestError
      if (e?.response?.status === 409 && (status === 'completed' || status === 'skipped')) {
        setPendingReason({ phase, status, message: serverDetail(err, 'The phase gate refused this change.') })
        latestSlot.current.open(reasonKey(phase.id))
        return
      }
      toast.error(serverDetail(err, 'The phase could not be updated.'))
    }
  }

  /** Resend the refused transition with its reason, under the server's exact field name. */
  async function sendPhaseReason(reason: string) {
    if (!pendingReason) return
    const { phase, status } = pendingReason
    setSendingReason(true)
    try {
      const body = status === 'completed'
        ? { status, gate_override_reason: reason }
        : { status, skip_reason: reason }
      // Not quiet: a failure now toasts the server's sentence, and the form stays.
      const result = await releasesService.updatePhase(releaseId, phase.id, body)
      setPendingReason(null)
      if (latestSlot.current.openKey === reasonKey(phase.id)) latestSlot.current.close()
      toast.success(status === 'completed'
        ? `Phase "${phase.name}" completed with an override`
        : `Phase "${phase.name}" skipped`)
      phaseUpdated(result)
    } catch {
      // The interceptor toasted the server's detail.
    } finally {
      setSendingReason(false)
    }
  }

  /** Record one phase's gate verdict (QA lead); the chip stays a live preview. */
  async function recordPhaseGate(phase: ReleasePhase) {
    setRecordingPhaseId(phase.id)
    try {
      const result = await releasesService.evaluatePhaseGate(releaseId, phase.id)
      toast.success(`Recorded the gate verdict for "${phase.name}": ${verdictWord(result.verdict)}`)
      void appMutate(['release-phase-gates', releaseId])
    } catch {
      // The interceptor toasted the server's detail.
    } finally {
      setRecordingPhaseId(null)
    }
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

  /** A first schedule was copied or created (REL-05 / REL-07): close the form, re-read the release everywhere. */
  function phasesPlanned() {
    formSlot.close()
    void refetch()
    void revalidateReleaseReads(releaseId)
    onPhasesChanged?.()
  }

  const workflow = detail ? buildReleaseWorkflow(detail) : { stages: [], events: [], stageOrder: [] }
  const detailStage = detail ? deriveRelease(detail).stage : null
  const canPlanPhases = isQaLead && detail != null && (detail.phases ?? []).length === 0
    && (detailStage === 'planning' || detailStage === 'in_progress')

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
    // In the pane the gate scorecard's four tiles (GatePanel's `sm:grid-cols-4`,
    // a viewport breakpoint) left ~16 px of slack at 1280 on Windows fonts: a
    // "100.0%" overflows its tile on CI's wider DejaVu. Two columns there.
    <div className={inPane ? 'space-y-5 [&_[data-gate-scorecard]>div:first-child]:grid-cols-2' : 'space-y-5'}>
      <ReleaseHoldStrip
        projectId={detail.project_id}
        releaseId={releaseId}
        releaseName={detail.name}
        holds={detail.holds}
        onChanged={() => void refetch()}
      />
      {/* Metrics strip */}
      {/* In the pane: as many tiles a row as fit at 140 px (a "100.0%" in
          text-xl keeps ≥ 40 px of slack on DejaVu): two at 1280, three at 1440. */}
      <div
        className={inPane
          ? 'grid grid-cols-[repeat(auto-fit,minmax(140px,1fr))] gap-2'
          : 'grid grid-cols-2 sm:grid-cols-5 gap-2'}
      >
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

      {/* REL-01: the recorded verdict, its scorecard, every blocking reason
          and the baseline comparison (`#gate`). */}
      <GatePanel releaseId={releaseId} onEvaluate={onEvaluate} evaluating={evaluating} />

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
                    {formatCompactDateTime(outcome.marked_at)}
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
        {canPlanPhases ? (
          // W11: while the release has no phases, a QA lead builds its first
          // schedule here: copied from another release, or drafted from a PRD.
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <h3 className="m-0 text-sm font-semibold text-[var(--color-text)]">Release Phases</h3>
            <div className="flex items-center gap-2" data-plan-phases="">
              <button type="button" onClick={() => formSlot.open(cloneKey)} className="btn-secondary btn-sm">
                Copy phases from…
              </button>
              <button type="button" onClick={() => formSlot.open(draftKey)} className="btn-secondary btn-sm">
                Draft from a PRD…
              </button>
            </div>
          </div>
        ) : (
          <h3 className="text-sm font-semibold text-[var(--color-text)] mb-3">Release Phases</h3>
        )}
        {formSlot.openKey === cloneKey && (
          <div className="mb-3">
            <ClonePhasesHost
              release={detail}
              slot={formSlot}
              onDone={phasesPlanned}
              onCancel={() => formSlot.close()}
            />
          </div>
        )}
        {formSlot.openKey === draftKey && (
          <div className="mb-3">
            <DraftPhasesForm
              release={detail}
              slot={formSlot}
              onDone={phasesPlanned}
              onCancel={() => formSlot.close()}
            />
          </div>
        )}
        <div className="space-y-2">
          {(detail.phases ?? []).map((phase, i) => {
            const phaseGate = phaseGateById.get(phase.id)
            return (
              <Fragment key={phase.id}>
                <div
                  id={`phase-${phase.id}`}
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
                      {phaseGate && <PhaseGateChip result={phaseGate} />}
                    </div>
                    {(phase.planned_start || phase.planned_end) && (
                      <p className="text-xs text-[var(--color-text-muted)] mt-0.5">
                        {fmtDate(phase.planned_start)} → {fmtDate(phase.planned_end)}
                      </p>
                    )}
                  </div>
                  <div className="flex items-center gap-1.5 shrink-0">
                    {isQaLead && (
                      <button
                        type="button"
                        onClick={() => void recordPhaseGate(phase)}
                        disabled={recordingPhaseId !== null}
                        data-testid="phase-record-gate"
                        className="text-[11px] px-1.5 py-0.5 rounded border border-[var(--color-border)] text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-bg-hover)] disabled:opacity-50"
                      >
                        {recordingPhaseId === phase.id ? 'Recording…' : 'Record gate verdict'}
                      </button>
                    )}
                    <select
                      value={phase.status}
                      onChange={e => void updatePhaseStatus(phase, e.target.value)}
                      aria-label={`Status of phase ${phase.name}`}
                      className="text-xs bg-[var(--color-bg-hover)] border border-[var(--color-border-light)] rounded px-1.5 py-0.5 text-[var(--color-text)] focus:outline-none"
                    >
                      <option value="pending">Pending</option>
                      <option value="in_progress">In Progress</option>
                      <option value="completed">Completed</option>
                      <option value="skipped">Skipped</option>
                      <option value="failed">Failed</option>
                    </select>
                    <button
                      onClick={() => setConfirmingPhase(phase.id)}
                      disabled={!isAdmin}
                      aria-label={`Remove phase ${phase.name}`}
                      className="text-[var(--color-text-faint)] hover:text-[var(--status-failed)] transition-colors p-0.5 disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:text-[var(--color-text-faint)]"
                      title={isAdmin ? 'Remove phase' : ADMIN_REQUIRED}
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </div>
                </div>
                {confirmingPhase === phase.id && (
                  <InlineConfirm
                    message={<>Remove phase &quot;<strong>{phase.name}</strong>&quot;?</>}
                    confirmLabel="Remove"
                    tone="danger"
                    onConfirm={() => { setConfirmingPhase(null); void deletePhase(phase.id) }}
                    onCancel={() => setConfirmingPhase(null)}
                  />
                )}
                {/* A phase gate's 409: the reason, right under the phase it is about. */}
                {pendingReason?.phase.id === phase.id && formSlot.openKey === reasonKey(phase.id) && (
                  <PhaseReasonDialog
                    phaseName={pendingReason.phase.name}
                    action={pendingReason.status === 'completed' ? 'complete' : 'skip'}
                    serverMessage={pendingReason.message}
                    busy={sendingReason}
                    slot={formSlot}
                    onSubmit={reason => void sendPhaseReason(reason)}
                    onCancel={() => { setPendingReason(null); formSlot.close() }}
                  />
                )}
              </Fragment>
            )
          })}
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
            onClick={() => formSlot.open(linkKey)}
            className="flex items-center gap-1.5 text-xs text-[var(--color-text)] hover:text-[var(--color-text-secondary)] transition-colors"
          >
            <Plus className="h-3.5 w-3.5" /> Link Run
          </button>
        </div>
        {formSlot.openKey === linkKey && (
          <div className="mb-3">
            <LinkRunModal
              releaseId={releaseId}
              phases={detail.phases ?? []}
              slot={formSlot}
              onClose={() => formSlot.close()}
              onSaved={() => { refetch(); formSlot.close() }}
            />
          </div>
        )}
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
                      <button
                        onClick={() => unlinkRun(run.id)}
                        disabled={!isAdmin}
                        aria-label={`Unlink run ${run.build_number ?? run.id.slice(0, 8)}`}
                        title={isAdmin ? 'Unlink run' : ADMIN_REQUIRED}
                        className="text-[var(--color-text-muted)] hover:text-[var(--status-failed)] transition-colors disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:text-[var(--color-text-muted)]"
                      >
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
  const { isAdmin, isQaLead } = usePermissions()
  // The release being edited, as it was when Edit was clicked (the editor
  // reads its initial values once; a list re-read must not move them).
  const [editRelease, setEditRelease] = useState<Release | undefined>()
  // One open form per section (W27 PAN-02): under the health banner (New
  // release, Sync releases), and in the open release (Edit, Link Run, a
  // phase's reason) — the pane on the list, the release block on its page.
  const topSlot = useInlineSlot<'new' | 'sync'>()
  const releaseSlot = useInlineSlot()
  // The inline confirmations that replaced the native prompts.
  const [confirmingDelete, setConfirmingDelete] = useState<string | null>(null)
  const [confirmingActivate, setConfirmingActivate] = useState<string | null>(null)
  // Releases whose gate evaluation is in flight (a card's, the panel's, or
  // the one "Evaluate all" is on), and the loop's progress.
  const [evaluatingIds, setEvaluatingIds] = useState<ReadonlySet<string>>(() => new Set())
  const [evaluateAllProgress, setEvaluateAllProgress] = useState<{ index: number; total: number } | null>(null)
  // UX redesign P5 item 5, W27 PAN-02: on the list a release opens in a pane
  // beside the cards (a drill-down, §2), not inline under its card, and it
  // rides the URL: `?open=<id>`, pushed on open (Back closes it). Its own
  // page, `/releases/:releaseId`, shows the same detail as the page itself and
  // has no key. The id is checked before any request: a value with `.`, `/`,
  // `?`, `#`, `%` or `&` is never put in a request path (`rejected`).
  //
  // NOT `?release=`: that is the top bar's global release filter on every
  // page (`ReleasePicker`'s RELEASE_PARAM), which adopts any `?release=` it
  // sees and writes its own selection back into the URL. On that key, opening
  // a release set the global filter (every other page then filtered by it),
  // choosing a release in the top bar opened its pane, and a deep-linked pane
  // could not be closed (the picker put the key back).
  const detail = useDetailParam(RELEASE_PANE_PARAM, { accept: isDetailToken })
  const [paneExpanded, setPaneExpanded] = useState(false)
  // A release this page just deleted: its pane closes at once, before the URL
  // pop lands, so nothing asks for the deleted id again.
  const [deletedId, setDeletedId] = useState<string | null>(null)
  const [statusFilter, setStatusFilter] = useState<string>('all')
  const [search, setSearch] = useState('')
  // W11 REL-06: List | Calendar rides the URL (`?view=calendar&month=YYYY-MM`,
  // beside `open`); List is the default and writes no key. The list route only.
  const [searchParams, setSearchParams] = useSearchParams()
  const now = useNow()
  const view: 'list' | 'calendar' = !releaseId && searchParams.get('view') === 'calendar' ? 'calendar' : 'list'
  const calendarMonth = parseMonthParam(searchParams.get('month'), new Date(now))

  /** Switch the view; every other key (`open`, the top bar's) is kept. */
  function showView(next: 'list' | 'calendar') {
    if (next === view) return
    setSearchParams(prev => {
      const params = new URLSearchParams(prev)
      if (next === 'calendar') params.set('view', 'calendar')
      else {
        params.delete('view')
        params.delete('month')
      }
      return params
    })
  }

  /** The calendar's month (Prev / Next / Today): replaces the entry, so Back leaves the calendar. */
  function showMonth(month: string) {
    setSearchParams(prev => {
      const params = new URLSearchParams(prev)
      params.set('month', month)
      return params
    }, { replace: true })
  }

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
      // The owner's name (REL-02), never the "No owner" placeholder: a
      // search for "owner" must not match every unowned release.
      xs = xs.filter(r =>
        r.name.toLowerCase().includes(q)
        || r.version.toLowerCase().includes(q)
        || (r.hasOwner && r.ownerName.toLowerCase().includes(q)),
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

  const markEvaluating = (id: string, on: boolean) => setEvaluatingIds(prev => {
    const next = new Set(prev)
    if (on) next.add(id)
    else next.delete(id)
    return next
  })

  /** Re-read every view of the releases a write touched: the list and each one's detail and gate. */
  function revalidateAfterWrite(ids: string[]) {
    return Promise.all([refetch(), ...ids.map(revalidateReleaseReads)])
  }

  /**
   * Evaluate one release's gate and record the verdict (QA lead; REL-01).
   * The toast quotes the verdict the server recorded; a failure is the
   * interceptor's toast with the server's sentence.
   */
  async function evaluateRelease(id: string, name: string) {
    markEvaluating(id, true)
    try {
      const result = await releasesService.evaluateGate(id)
      toast.success(evaluationToast(name, result))
      await revalidateAfterWrite([id])
    } catch {
      // The interceptor toasted the server's detail.
    } finally {
      markEvaluating(id, false)
    }
  }

  /**
   * "Evaluate all in progress" (D-1): the existing route, one release at a
   * time, and one failure does not stop the rest. Each request is quiet; the
   * one toast at the end says what happened to all of them.
   */
  async function evaluateAllInProgress() {
    const targets = inProgressReleases.map(r => ({ id: r.id, name: r.name }))
    if (targets.length === 0 || evaluateAllProgress) return
    const verdicts: string[] = []
    const failures: string[] = []
    for (const [index, target] of targets.entries()) {
      setEvaluateAllProgress({ index: index + 1, total: targets.length })
      markEvaluating(target.id, true)
      try {
        const result = await releasesService.evaluateGate(target.id, { suppressToast: true })
        verdicts.push(result.verdict)
      } catch (err: unknown) {
        failures.push(serverDetail(err, 'the evaluation failed'))
      } finally {
        markEvaluating(target.id, false)
      }
    }
    setEvaluateAllProgress(null)
    if (failures.length === 0) {
      toast.success(evaluateAllToast(verdicts))
    } else {
      toast.error(evaluateAllPartialToast(verdicts.length, targets.length, failures.length, failures[0]))
    }
    await revalidateAfterWrite(targets.map(t => t.id))
  }

  /**
   * Make a release the project's active one: where runs no other rule claims
   * land (REL-08). Asked first in the strip under its card (`askSetActive`).
   */
  async function setActiveRelease(id: string) {
    const target = derived.find(r => r.id === id)
    const name = target?.name ?? 'this release'
    try {
      const result = await releasesService.activate(id)
      toast.success(result.changed ? `"${name}" is now the active release` : `"${name}" was already the active release`)
      // The previously active release lost its badge: re-read the whole list.
      await revalidateAfterWrite(result.deactivated ? [id, result.deactivated] : [id])
    } catch {
      // A finished release answers 409 with its reason: the interceptor's toast.
    }
  }

  /** Delete a release (admin). Asked first in the strip where its Delete is (`askDelete`). */
  async function deleteRelease(id: string) {
    try {
      await releasesService.delete(id)
      toast.success('Release deleted')
      if (latestPane.current.id === id) {
        setDeletedId(id)
        closeReleaseSlot()
        setPaneExpanded(false)
        latestPane.current.close()
      }
      if (releaseId === id) {
        // The list it lands on still held the deleted release (its card stayed
        // until the 30 s poll): re-read it.
        void refetch()
        navigate('/releases')
        return
      }
      refetch()
    } catch { toast.error('Failed to delete release') }
  }

  /** The first click on Delete asks; only the strip's Delete sends the request. */
  function askDelete(id: string) {
    setConfirmingActivate(null)
    setConfirmingDelete(id)
  }

  /** The first click on "Set as active release" asks, under the card. */
  function askSetActive(id: string) {
    setConfirmingDelete(null)
    setConfirmingActivate(id)
  }

  /** Edit opens inside the release (the pane, or the release's own page). */
  function openEdit(source: Release) {
    const start = () => {
      setEditRelease(source)
      releaseSlot.open(`edit:${source.id}`)
    }
    // The New form and the editor share their field and heading ids: never both at once.
    if (topSlot.openKey === 'new') topSlot.guard(() => { topSlot.close(); start() })
    else start()
  }

  /** New release opens under the health banner. */
  function openNew() {
    const start = () => topSlot.open('new')
    if (releaseSlot.openKey?.startsWith('edit:')) releaseSlot.guard(() => { releaseSlot.close(); start() })
    else start()
  }

  const activeProjectId = useProjectStore(s => s.activeProjectId)
  const isAllProjects = activeProjectId === ALL_PROJECTS_ID
  // The rail's project (§1.5): the picker's on the list (null = All
  // projects), the release's own on its page (a deep link may be into another
  // project than the picker's).
  const railProjectId = releaseId
    ? (routedRelease?.project_id ?? null)
    : (isAllProjects ? null : activeProjectId)
  // QA lead, one project, the list: Sync releases; Evaluate all in progress.
  const canSync = isQaLead && !isAllProjects && !releaseId && Boolean(activeProjectId)
  const canEvaluateAll = isQaLead && !releaseId && inProgressReleases.length > 0
  const onEvaluateCard = isQaLead
    ? (id: string) => void evaluateRelease(id, derived.find(r => r.id === id)?.name ?? 'Release')
    : undefined
  const onSetActiveCard = isQaLead ? (id: string) => askSetActive(id) : undefined

  // The release in the pane (list route only). Looked up in the unfiltered
  // list, so a search that hides its card does not close it. An id the list
  // does not hold (another project's release, a shared link, a release that
  // is gone) is read once through the release's own access-checked route,
  // `useRelease` — the SWR key ReleaseDetailPanel reads too, so it costs no
  // extra request; a 403 / 404 / network failure is the not-found card.
  const paneId = !releaseId && detail.id !== null && detail.id !== deletedId ? detail.id : null
  const listedRelease = paneId ? derived.find(r => r.id === paneId) ?? null : null
  const orphanId = paneId && !listedRelease && data ? paneId : null
  const orphan = useRelease(orphanId)
  const orphanData = orphanId && !orphan.error ? orphan.data : undefined
  const orphanRelease = useMemo(() => (orphanData ? deriveRelease(orphanData) : null), [orphanData])
  const paneRelease = listedRelease ?? orphanRelease
  const paneState: 'closed' | 'release' | 'missing' =
    releaseId ? 'closed'
    : detail.rejected !== null ? 'missing'
    : paneId === null ? 'closed'
    : listedRelease ? 'release'
    : !data ? 'closed' // the list is still loading: the page shows its spinner
    : orphan.error || (!orphan.isLoading && !orphan.data) ? 'missing'
    : 'release' // while the read is in flight: the pane's own spinner
  const paneOpen = paneState !== 'closed'

  const { close: closeReleaseSlot } = releaseSlot
  const { close: closeTopSlot } = topSlot
  // The open pane as the latest render saw it, for a delete that returns later.
  const latestPane = useRef({ id: paneId, close: detail.close })
  useEffect(() => { latestPane.current = { id: paneId, close: detail.close } })

  // A pane closed any way (Close, Escape, Back, the full-page link) takes its
  // forms, its question and its width with it; so does a change of route (the
  // list and a release's page are one component: its state outlives the
  // route). Adjusted while rendering, so no frame shows a stale form.
  const [seen, setSeen] = useState({ paneOpen, releaseId })
  if (seen.paneOpen !== paneOpen || seen.releaseId !== releaseId) {
    const routeChanged = seen.releaseId !== releaseId
    setSeen({ paneOpen, releaseId })
    if (!paneOpen || routeChanged) {
      closeReleaseSlot()
      setConfirmingDelete(null)
      setPaneExpanded(false)
    }
    if (routeChanged) {
      closeTopSlot()
      setConfirmingActivate(null)
    }
  }

  /** A card, the banner's Open or a rail row: open the release in the pane, or switch it there. */
  const openPane = (id: string) => {
    // Its own card again: the pane stays as it is.
    if (id === paneId) return
    // A form typed into inside the pane asks before it goes.
    releaseSlot.guard(() => {
      releaseSlot.close()
      setConfirmingDelete(null)
      detail.open(id)
    })
  }
  /** Close, Escape (DetailPane guards both through the slot) and the not-found card's Close. */
  const closePane = () => {
    releaseSlot.close()
    setConfirmingDelete(null)
    setPaneExpanded(false)
    detail.close()
  }

  /**
   * A card's Copy phases from… / Draft from a PRD… (QA lead; REL-05 / REL-07):
   * on the list, open the release in the pane with that form open in its
   * "Release Phases"; on the release's own page, open the form there.
   */
  const planPhases = (id: string, mode: PlanPhasesMode) => {
    const key = `${mode}:${id}`
    if (releaseId || id === paneId) {
      releaseSlot.open(key)
      return
    }
    // A form typed into in the pane asks first (openPane's guard); the release
    // then opens and its Copy / Draft is one more click.
    if (releaseSlot.dirty) {
      openPane(id)
      return
    }
    // Clean: switch the pane and open the form in one go (the slot keys are
    // per release, so the form appears in the new release's Release Phases).
    setConfirmingDelete(null)
    releaseSlot.close()
    detail.open(id)
    releaseSlot.open(key)
  }
  const onPlanPhasesCard = isQaLead ? planPhases : undefined

  /** The ⋯ menu's schedule exports (REL-06): an authenticated download, every role, All Projects too. */
  async function exportSchedule(format: 'ics' | 'csv') {
    try {
      await releasesService.exportSchedule(format, isAllProjects ? null : activeProjectId)
    } catch {
      toast.error('Could not export the release schedule.')
    }
  }
  const headerOverflow = [
    { label: 'Export schedule (.ics)', onClick: () => void exportSchedule('ics') },
    { label: 'Export schedule (.csv)', onClick: () => void exportSchedule('csv') },
    // REL-09 (G-D's page): single-project, so offered only with one project selected.
    ...(!isAllProjects && activeProjectId
      ? [{ label: 'Attribution rules', onClick: () => navigate('/releases/attribution-rules') }]
      : []),
  ]

  // Focus moves into the pane when it opens (onto its Close button, as the
  // modal panel's did — not into its first field, which can sit far down the
  // release and would scroll the pane to it), and back to the control that
  // opened it when it closes. The pane itself is `manageFocus={false}`.
  const paneRef = useRef<HTMLDivElement | null>(null)
  useLayoutEffect(() => {
    if (!paneOpen) return
    const root = paneRef.current
    if (!root) return
    const active = document.activeElement
    const opener = active instanceof HTMLElement && active !== document.body && !root.contains(active) ? active : null
    if (!root.contains(active)) root.querySelector<HTMLElement>('button[aria-label="Close release"]')?.focus({ preventScroll: true })
    return () => {
      const now = document.activeElement
      const focusWasOurs = !now || now === document.body || root.contains(now)
      if (focusWasOurs && opener?.isConnected) opener.focus({ preventScroll: true })
    }
  }, [paneOpen])

  /**
   * The release editor, New (no release) or Edit, in `slot`. Editing needs the
   * release's own project, not the picker's: an edit opened under "All
   * projects" used to render nothing.
   */
  function releaseForm(editRelease: Release | undefined, slot: InlineSlot) {
    const modalProjectId = editRelease?.project_id ?? (isAllProjects ? null : projectId)
    if (!modalProjectId) return null
    return (
      <ReleaseModal
        projectId={modalProjectId}
        initial={editRelease}
        slot={slot}
        onClose={() => slot.close()}
        // Key off the release that was EDITED, not off the route.
        //
        // `refetch` is the list mutator. The detail data lives under
        // ['release-detail', id] and is read by two different things: the
        // routed /releases/:id view AND `ReleaseDetailPanel` in the pane on
        // the list route. Refreshing only the routed one fixes the first and
        // leaves the second stale — including the name it passes to
        // CompliancePackPanel — because that key is revalidateOnFocus:false
        // with no interval and the pane does not unmount while the editor is open.
        onSaved={() => Promise.all([
          refetch(),
          editRelease ? appMutate(['release-detail', editRelease.id]) : Promise.resolve(),
        ])}
      />
    )
  }

  /** The editor of release `id`, when it is the one open in the release's slot. */
  const editFormFor = (id: string) =>
    releaseSlot.openKey === `edit:${id}` && editRelease?.id === id ? releaseForm(editRelease, releaseSlot) : null

  // Under the health banner: New release, or Sync releases (one at a time).
  const topForm = topSlot.openKey === 'new'
    ? releaseForm(undefined, topSlot)
    : topSlot.openKey === 'sync' && canSync && activeProjectId ? (
      <SyncReleasesDialog
        projectId={activeProjectId}
        slot={topSlot}
        onClose={() => topSlot.close()}
        onDone={() => { topSlot.close(); void refetch() }}
      />
    ) : null

  const emptyList = (
    <EmptyState
      icon={<Package className="h-8 w-8" />}
      title="No releases yet"
      // HON-07: no org template or clone exists to offer (and an empty
      // project has nothing to clone from), so the copy promises neither.
      description={isAllProjects ? 'No releases exist across any project yet' : 'Create your first release'}
      action={!isAllProjects ? (
        <button
          onClick={openNew}
          className="btn-primary mt-4 flex items-center gap-2 mx-auto"
        >
          <Plus className="h-4 w-4" /> Create release
        </button>
      ) : undefined}
    />
  )

  const deleteStrip = (id: string) => (
    <InlineConfirm
      message="Delete this release and all its phases? This cannot be undone."
      confirmLabel="Delete"
      tone="danger"
      onConfirm={() => { setConfirmingDelete(null); void deleteRelease(id) }}
      onCancel={() => setConfirmingDelete(null)}
    />
  )
  const activateStrip = (release: DerivedRelease) => (
    <InlineConfirm
      message={<>Make &quot;<strong>{release.name}</strong>&quot; the active release? Runs that no other rule matches will land here.</>}
      confirmLabel="Make active"
      onConfirm={() => { setConfirmingActivate(null); void setActiveRelease(release.id) }}
      onCancel={() => setConfirmingActivate(null)}
    />
  )

  const pane = paneOpen ? (
    <div ref={paneRef} className="contents">
      <DetailPane
        title={paneRelease ? releaseTitle(paneRelease) : 'Release'}
        onClose={closePane}
        closeLabel="Close release"
        slot={releaseSlot}
        manageFocus={false}
        expanded={paneExpanded}
        // Nothing to expand on the not-found card.
        onToggleExpand={paneState === 'release' ? () => setPaneExpanded(open => !open) : undefined}
        expandLabels={{
          expand: 'Expand the release to full width',
          collapse: 'Show the list beside the release',
          expandTitle: 'Expand',
          collapseTitle: 'Show the list',
        }}
        bodyClassName="px-4 py-3"
        footer={paneState === 'release' && paneRelease ? (
          <>
            {confirmingDelete === paneRelease.id && <div className="mb-2">{deleteStrip(paneRelease.id)}</div>}
            <div className="flex items-center gap-2" data-release-panel-actions="">
              <button
                type="button"
                onClick={() => openEdit(paneRelease.source)}
                className="btn-secondary inline-flex items-center gap-1 text-xs"
              >
                <Pencil className="h-3.5 w-3.5" aria-hidden="true" /> Edit
              </button>
              <button
                type="button"
                onClick={() => askDelete(paneRelease.id)}
                disabled={!isAdmin}
                title={isAdmin ? undefined : ADMIN_REQUIRED}
                className="inline-flex items-center gap-1 rounded-md px-2.5 py-1.5 text-xs text-[var(--status-failed)] hover:bg-[var(--status-failed-bg)]/10 disabled:opacity-40 disabled:cursor-not-allowed"
              >
                <Trash2 className="h-3.5 w-3.5" aria-hidden="true" /> Delete
              </button>
              <Link
                to={`/releases/${paneRelease.id}`}
                onClick={(event) => {
                  // A form typed into inside the pane asks before the page goes.
                  if (!releaseSlot.dirty) return
                  event.preventDefault()
                  releaseSlot.guard(() => navigate(`/releases/${paneRelease.id}`))
                }}
                className="ml-auto inline-flex items-center gap-1 text-xs font-medium text-[var(--color-accent)] hover:underline"
              >
                Open full page <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
              </Link>
            </div>
          </>
        ) : undefined}
      >
        {paneState === 'release' && paneId ? (
          // A pane that throws shows its own fallback; the cards, the banner
          // and the page around it keep working.
          <SectionErrorBoundary key={paneId} message="The release could not be shown">
            <div data-release-panel={paneId} className="space-y-3">
              {editFormFor(paneId)}
              {paneRelease?.source.description && (
                <p className="text-sm text-[var(--color-text-muted)]">{paneRelease.source.description}</p>
              )}
              {/* Keyed: another release starts on its own detail, with its own state. */}
              <ReleaseDetailPanel
                key={paneId}
                releaseId={paneId}
                onEvaluate={isQaLead ? () => void evaluateRelease(paneId, paneRelease?.name ?? 'Release') : undefined}
                evaluating={evaluatingIds.has(paneId)}
                slot={releaseSlot}
                // Expanded to the row's full width, the page's own layout fits again.
                inPane={!paneExpanded}
                onPhasesChanged={() => void refetch()}
              />
            </div>
          </SectionErrorBoundary>
        ) : (
          <div data-release-not-found="" className="space-y-3">
            <p className="text-sm text-[var(--color-text-muted)]">This release could not be found, or you cannot open it.</p>
            <button type="button" onClick={closePane} className="btn-secondary text-xs">Close</button>
          </div>
        )}
      </DetailPane>
    </div>
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
          actions={!releaseId ? (
            <div className="flex items-center gap-2">
              {/* W11 REL-06: the list or a month calendar (the URL's `view`), in All Projects too. */}
              <div role="group" aria-label="View" className="inline-flex items-center gap-1">
                <button
                  type="button"
                  aria-pressed={view === 'list'}
                  onClick={() => showView('list')}
                  className={clsx('btn-sm', view === 'list' ? 'btn-primary' : 'btn-secondary')}
                >
                  List
                </button>
                <button
                  type="button"
                  aria-pressed={view === 'calendar'}
                  onClick={() => showView('calendar')}
                  className={clsx('btn-sm', view === 'calendar' ? 'btn-primary' : 'btn-secondary')}
                >
                  Calendar
                </button>
              </div>
              {canSync && (
                <button
                  type="button"
                  onClick={() => topSlot.open('sync')}
                  className="btn-secondary inline-flex items-center gap-1.5 text-sm"
                >
                  <RefreshCw className="h-4 w-4" aria-hidden="true" /> Sync releases
                </button>
              )}
              {!isAllProjects && (
                <button
                  onClick={openNew}
                  className="btn-primary inline-flex items-center gap-1.5"
                >
                  <Plus className="h-4 w-4" /> New release
                </button>
              )}
            </div>
          ) : undefined}
          overflow={!releaseId ? headerOverflow : undefined}
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
              {canEvaluateAll && (
                <button
                  type="button"
                  data-testid="release-evaluate-all"
                  onClick={() => void evaluateAllInProgress()}
                  disabled={evaluateAllProgress !== null}
                  className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md border border-[var(--color-border)] text-[12px] font-medium text-[var(--color-text)] hover:bg-[var(--color-bg-hover)] disabled:opacity-60"
                >
                  {evaluateAllProgress
                    ? `Evaluating ${evaluateAllProgress.index} of ${evaluateAllProgress.total}…`
                    : 'Evaluate all in progress'}
                </button>
              )}
            </div>
          </div>
        )}

        {/* Release health in one line: the release that ships next, its
            gate, and the counts that matter most (all releases, not the
            filtered list). "Open" is the list's drill-down (the pane); on a
            release's own page the release is already the page. */}
        {!isLoading && (
          <div className="mb-3.5">
            <ReleaseHealthBanner
              highlighted={highlightedRelease}
              inProgressReleases={inProgressReleases}
              releases={derived}
              onOpen={releaseId ? undefined : openPane}
            />
          </div>
        )}

        {/* New release / Sync releases: in the page, between the banner and the list. */}
        {topForm && <div className="mb-3.5">{topForm}</div>}

        {/* Body grid — release list + right rail; while a release is open the
            pane takes the rail's place (the rail is folded, never the cards
            squeezed). */}
        {isLoading ? (
          <div className="flex items-center justify-center h-64"><LoadingSpinner size="lg" /></div>
        ) : releases.length === 0 ? (
          // A deep link into another project's release still opens beside the
          // empty list; with nothing open the empty state is as it was.
          pane ? (
            <MasterDetail gapClassName="gap-[18px]" list={emptyList} detail={pane} expanded={paneExpanded} />
          ) : emptyList
        ) : (
          <MasterDetail
            gapClassName="gap-[18px]"
            railTrack="340px"
            expanded={paneExpanded}
            detail={pane}
            // Left — release cards (on a release's own page: its card and its
            // detail), then the release health detail, collapsed.
            listProps={{ className: 'flex flex-col gap-3.5 min-w-0' }}
            list={(
              <>
                <div data-primary="" className="flex flex-col gap-2.5 min-w-0">
                  {view === 'calendar' ? (
                    // W11 REL-06: the month calendar in the cards' place, from the list the page holds.
                    <ReleaseCalendar
                      releases={filteredDerived.map(r => r.source)}
                      month={calendarMonth}
                      onMonth={showMonth}
                      onOpen={openPane}
                    />
                  ) : filteredDerived.length === 0 ? (
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
                        {/* Its Evaluate is the gate panel's, right below: one button, not two. */}
                        <ReleaseCard release={r} onSetActive={onSetActiveCard} onPlanPhases={onPlanPhasesCard} />
                        {confirmingActivate === r.id && <div className="mt-2">{activateStrip(r)}</div>}
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
                              onClick={() => askDelete(r.id)}
                              disabled={!isAdmin}
                              title={isAdmin ? undefined : ADMIN_REQUIRED}
                              className="text-xs text-[var(--status-failed)]/70 hover:text-[var(--status-failed)] px-2 py-1 rounded hover:bg-[var(--status-failed-bg)]/10 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                            >
                              Delete
                            </button>
                          </div>
                          {confirmingDelete === r.id && <div className="mb-3">{deleteStrip(r.id)}</div>}
                          {/* Edit opens at the top of the release, under its actions. */}
                          {editFormFor(r.id) && <div className="mb-3">{editFormFor(r.id)}</div>}
                          <ReleaseDetailPanel
                            releaseId={r.id}
                            onEvaluate={isQaLead ? () => void evaluateRelease(r.id, r.name) : undefined}
                            evaluating={evaluatingIds.has(r.id)}
                            slot={releaseSlot}
                            onPhasesChanged={() => void refetchRoutedRelease()}
                          />
                        </div>
                      </div>
                    ))
                  ) : (
                    filteredDerived.map(r => (
                      <Fragment key={r.id}>
                        <ReleaseCard
                          release={r}
                          onClick={() => openPane(r.id)}
                          onEvaluate={onEvaluateCard}
                          evaluating={evaluatingIds.has(r.id)}
                          onSetActive={onSetActiveCard}
                          onPlanPhases={onPlanPhasesCard}
                        />
                        {/* Set as active release asks right under its card. */}
                        {confirmingActivate === r.id && activateStrip(r)}
                      </Fragment>
                    ))
                  )}
                </div>
                <ReleaseHealthDetails highlighted={highlightedRelease} releases={derived} />
              </>
            )}
            // Right — derived panels (folded while a release is open).
            rail={(
              <div className="flex flex-col gap-3.5 min-w-0">
                <ShippingThisWeek releases={derived} onOpen={openPane} />
                <AgingSignals releases={derived} onOpen={openPane} />
                <CompliancePacks releases={derived} onOpen={openPane} projectId={railProjectId} />
                <RecentActivity releases={derived} projectId={railProjectId} />
              </div>
            )}
          />
        )}
      </div>
    </>
  )
}

/** The pane's heading: the name, and the version when it says something the name does not. */
function releaseTitle(release: { name: string; version: string }): string {
  return release.version && release.version !== release.name ? `${release.name} ${release.version}` : release.name
}
