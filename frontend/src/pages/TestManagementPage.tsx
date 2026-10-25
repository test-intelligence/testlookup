import { Fragment, useState, useCallback, useEffect, useMemo, useRef, type ReactNode } from 'react'
import useSWR from 'swr'
import { useDataFreshness } from '@/hooks/useDataFreshness'
import { useNow } from '@/hooks/useNow'
import { shortAgo } from '@/utils/formatters'
import { Link, useLocation, useSearchParams } from 'react-router-dom'
import {
  ClipboardList, Plus, Sparkles, ChevronDown,
  User, CheckCircle2, AlertCircle,
  RotateCcw, MessageSquare, History, Shield, FileText,
  ChevronUp, Trash2, BookOpen,
  Download, FileSpreadsheet, Layers, Pencil, Upload, ListOrdered,
} from 'lucide-react'
import { clsx } from 'clsx'
import toast from 'react-hot-toast'
import PageHeader from '@/components/ui/PageHeader'
import EmptyState from '@/components/ui/EmptyState'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import Pagination from '@/components/ui/Pagination'
import StatusBanner from '@/components/ui/StatusBanner'
import { avatarColorForName } from '@/components/ui/avatarColors'
import { SEVERITY_STYLE, severityLevel } from '@/components/ui/severity'
import { useTabParam } from '@/components/ui/useTabParam'
import { DetailPane } from '@/components/ui/inPage/DetailPane'
import { isDetailToken } from '@/components/ui/inPage/ids'
import { InlineFormSection } from '@/components/ui/inPage/InlineFormSection'
import { InlineRowSection } from '@/components/ui/inPage/InlineRowSection'
import { MasterDetail } from '@/components/ui/inPage/MasterDetail'
import { useDetailParam } from '@/components/ui/inPage/useDetailParam'
import { useInlineSlot, type InlineSlot } from '@/components/ui/inPage/useInlineSlot'
import { useSlotCloser } from '@/hooks/useSlotCloser'
import { helpTopicParam } from '@/components/help/helpTopics'
import EvidenceGapLists from '@/components/testManagement/EvidenceGapLists'
import LifecyclePanel from '@/components/testManagement/LifecyclePanel'
import PromotionAction from '@/components/testManagement/PromotionAction'
import TransitionReasonDialog from '@/components/testManagement/TransitionReasonDialog'
import { useTableSort } from '@/hooks/useTableSort'
import { useFeatureEnabled } from '@/hooks/useFeatureFlags'
import { api } from '@/services/api'
import { useProjectStore } from '@/store/projectStore'
import { useAuthStore } from '@/store/authStore'
import { appMutate } from '@/utils/swrCacheMutate'
import { testCaseEditingService } from '@/services/testCaseEditingService'
import {
  useTestCases, useTestPlans, useAuditLog,
  useTestCaseHistory, useTestCaseReviews, useTestCaseComments,
  usePlanItems, useUsers } from '@/hooks/useTestManagement'
import { useSuites } from '@/hooks/useSuites'
import { usePermissions } from '@/hooks/usePermissions'
import { useProjectMembers } from '@/hooks/useUserManagement'
import {
  testManagementService,
} from '@/services/testManagementService'
import type { UserSummary, SuiteReviewItem, SuiteReviewState } from '@/services/testManagementService'
import { deriveTestManagementTotals } from '@/utils/testManagementTotals'
import { latestResultPath, runHistoryPath } from '@/utils/testManagementCase'
import KnowledgeGenerationTab from '@/pages/test-management/KnowledgeGenerationTab'
import AIGenerateModal from '@/pages/test-management/AIGenerateModal'
import ApprovalsTab from '@/pages/test-management/ApprovalsTab'
import AuditTab from '@/pages/test-management/AuditTab'
import DuplicatesTab from '@/pages/test-management/DuplicatesTab'
import InsightsPanel, {
  AutomationCoverageCard,
  HealthPendingCard,
  LibraryHealthCard,
  RecentActivityCard,
  ReviewQueueCard,
  StrategyGapsCard,
  type RecentEvent,
} from '@/pages/test-management/InsightsPanel'
// W13 TCL-03: the server's library health (G-A's hook), its copy (G-D's model)
// and the per-project automation target editor (G-E).
import { automatedPctText, bannerFor } from '@/pages/test-management/libraryHealthModel'
import AutomationTargetEditor from '@/pages/test-management/AutomationTargetEditor'
import StrategyTab from '@/pages/test-management/StrategyTab'
import TestCasesTabBar from '@/pages/test-management/TestCasesTabBar'
// W28 (TCM-01..05): G-C's edit form and Status moves; G-D's bulk bar, history panel and plan item version.
import CaseEditForm from '@/pages/test-management/CaseEditForm'
import CaseStatusControl from '@/pages/test-management/CaseStatusControl'
import BulkActionBar from '@/pages/test-management/BulkActionBar'
import { isSelectable, pruneSelection } from '@/pages/test-management/bulkActionModel'
import CaseHistoryPanel from '@/pages/test-management/CaseHistoryPanel'
import { PlanItemVersionCell, PlanItemVersionSteps } from '@/pages/test-management/PlanItemVersion'
import { STATUS_COLORS, PRIORITY_COLORS, fmtDate, fmtDateTime } from '@/pages/test-management/format'
import { ModalWrap, QualityScore, StatusPill } from '@/pages/test-management/tmUi'
import { TM_TAB_IDS, tabAlias, type TmTab } from '@/pages/test-management/tabs'
import {
  RETIRED_STATES,
  RUNNABLE_STATES,
  type AIReviewResult,
  type ManagedTestCase,
  type TestCaseBulkResponse,
  type TestCaseComment,
  type TestCaseReview,
  type TestPlan,
  type TestPlanItem,
  type TestStep,
} from '@/types/test-management'
import {
  buildTestCaseListParams,
  LIFECYCLE_STATUS_OPTIONS,
} from '@/utils/testCaseLifecycleUi'
// W13 "test-library": the filters' URL state (G-A), the Owner options read (G-A), the
// reviewed multi-value control (VIZ-109) and the Views bar (G-E).
import MultiSelect from '@/components/ui/MultiSelect'
import { useCaseOwnerOptions, useLibraryHealth } from '@/hooks/useTestLibrary'
import {
  CASE_PRESET_IDS,
  CASE_PRESET_LABELS,
  CASE_TYPE_OPTIONS,
  EMPTY_CASE_FILTERS,
  activePreset,
  caseFiltersKey,
  readCaseFilters,
  writeCaseFilters,
  type CaseFilterState,
} from '@/pages/test-management/caseFilterState'
import CaseViewsBar from '@/pages/test-management/CaseViewsBar'
// W13 TCL-04 / 05 / 06: G-E's import section, case timeline and requirements mounts.
import ImportCasesSection from '@/pages/test-management/ImportCasesSection'
import CaseTimeline from '@/pages/test-management/CaseTimeline'
import CaseRequirementsSection from '@/pages/test-management/CaseRequirementsSection'
import RequirementCoverageCard from '@/pages/test-management/RequirementCoverageCard'

// ─── Constants / helpers ─────────────────────────────────────────────────────
//
// UX redesign P4 item 7: eight tabs became Cases · Suites · Plans · Approvals
// · More ▾ (Strategy, Duplicates, Audit log, Knowledge). Strategy, Duplicates,
// Audit log, Approvals (was Reviews) and the AI Generate modal are in
// `./test-management/`; the shared pills, colours and date formats too.

const HELP_TOPIC = helpTopicParam('/test-management')

const PLAN_STATUS_COLORS: Record<string, string> = {
  draft:       'bg-[var(--color-bg-hover)] text-[var(--color-text-secondary)] border border-[var(--color-border-light)]',
  active:      'bg-[var(--color-bg-secondary)]/80 text-[var(--color-text-secondary)] border border-[var(--color-border-light)]',
  in_progress: 'bg-[var(--status-broken-bg)] text-[var(--status-broken)] border border-[var(--status-broken-bd)]',
  completed:   'bg-[var(--status-passed-bg)] text-[var(--status-passed)] border border-[var(--status-passed-bd)]',
  archived:    'bg-[var(--color-bg-secondary)] text-[var(--color-text-muted)] border border-[var(--color-border)]',
}

// ─── Sub-components (the forms) ───────────────────────────────────────────────
//
// W27b PAN-05: every form of this page renders IN THE PAGE, where its action
// lives (`ModalWrap` and the two forms below are in-page sections; the reason
// form opens under its case row), never a modal over it.

// ── Create Test Case form ─────────────────────────────────────────────────────

interface CreateCaseModalProps { projectId: string; onClose: () => void; onCreated: () => void; slot?: InlineSlot }

function CreateCaseModal({ projectId, onClose, onCreated, slot }: CreateCaseModalProps) {
  const [title, setTitle] = useState('')
  const [testType, setTestType] = useState('functional')
  const [priority, setPriority] = useState('medium')
  const [assigneeId, setAssigneeId] = useState<string>('')
  const [featureArea, setFeatureArea] = useState('')
  const [suiteName, setSuiteName] = useState('')
  const [objective, setObjective] = useState('')
  const [preconditions, setPreconditions] = useState('')
  const [steps, setSteps] = useState<TestStep[]>([{ step_number: 1, action: '', expected_result: '' }])
  const [expectedResult, setExpectedResult] = useState('')
  const [testData, setTestData] = useState('')
  const [estimatedDuration, setEstimatedDuration] = useState('')
  const [saving, setSaving] = useState(false)
  // The project's active members: who the backend accepts as an assignee.
  // It listed every user on the instance, and the pick was then dropped
  // (browser E2E pass, 2026-10-08).
  const { data: members } = useProjectMembers(projectId || null)
  const assignees = useMemo(
    () => (members ?? [])
      .map(m => ({ id: m.user_id, name: m.full_name || m.username }))
      .sort((x, y) => x.name.localeCompare(y.name)),
    [members],
  )

  const addStep = () => setSteps(s => [...s, { step_number: s.length + 1, action: '', expected_result: '' }])
  const removeStep = (i: number) => setSteps(s => s.filter((_, idx) => idx !== i).map((st, idx) => ({ ...st, step_number: idx + 1 })))
  const updateStep = (i: number, field: keyof TestStep, value: string) =>
    setSteps(s => s.map((st, idx) => idx === i ? { ...st, [field]: value } : st))

  const handleSubmit = async () => {
    if (title.trim().length < 3) { toast.error('Title must be at least 3 characters'); return }
    setSaving(true)
    try {
      await testManagementService.createCase({
        project_id: projectId,
        title: title.trim(),
        test_type: testType,
        priority,
        assignee_id: assigneeId || undefined,
        feature_area: featureArea || undefined,
        suite_name: suiteName || undefined,
        objective: objective || undefined,
        preconditions: preconditions || undefined,
        steps: steps.filter(s => s.action.trim()),
        expected_result: expectedResult || undefined,
        test_data: testData || undefined,
        estimated_duration_minutes: estimatedDuration ? Math.max(1, Math.round(Number(estimatedDuration))) : undefined,
        severity: 'medium',
        is_automated: false,
        automation_status: 'not_automated',
      })
      toast.success('Test case created')
      onCreated()
      onClose()
    } catch {
      toast.error('Failed to create test case')
    } finally {
      setSaving(false)
    }
  }

  return (
    <ModalWrap onClose={onClose} title="New Test Case" width="max-w-3xl" slot={slot}>
      <div className="space-y-4">
        <div>
          <label htmlFor="tm-field-0" className="block text-xs text-[var(--color-text-muted)] mb-1">Title *</label>
          <input id="tm-field-0" className="input w-full" value={title} onChange={e => setTitle(e.target.value)} placeholder="Describe what this test verifies" />
        </div>
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label htmlFor="tm-field-1" className="block text-xs text-[var(--color-text-muted)] mb-1">Test Type</label>
            <select id="tm-field-1" className="input w-full" value={testType} onChange={e => setTestType(e.target.value)}>
              {['functional','integration','e2e','regression','smoke','performance','security','usability','accessibility','api'].map(t => (
                <option key={t} value={t}>{t.charAt(0).toUpperCase() + t.slice(1)}</option>
              ))}
            </select>
          </div>
          <div>
            <label htmlFor="tm-field-2" className="block text-xs text-[var(--color-text-muted)] mb-1">Priority</label>
            <select id="tm-field-2" className="input w-full" value={priority} onChange={e => setPriority(e.target.value)}>
              {['critical','high','medium','low'].map(p => (
                <option key={p} value={p}>{p.charAt(0).toUpperCase() + p.slice(1)}</option>
              ))}
            </select>
          </div>
        </div>
        <div>
          <label htmlFor="tm-field-3" className="block text-xs text-[var(--color-text-muted)] mb-1">Assignee (optional)</label>
          <select id="tm-field-3" className="input w-full" value={assigneeId} onChange={e => setAssigneeId(e.target.value)}>
            <option value="">Unassigned</option>
            {assignees.map((u) => (
              <option key={u.id} value={u.id}>
                {u.name}
              </option>
            ))}
          </select>
        </div>
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label htmlFor="tm-field-4" className="block text-xs text-[var(--color-text-muted)] mb-1">Feature Area</label>
            <input id="tm-field-4" className="input w-full" value={featureArea} onChange={e => setFeatureArea(e.target.value)} placeholder="e.g. Authentication, Checkout" />
          </div>
          <div>
            <label htmlFor="tm-field-5" className="block text-xs text-[var(--color-text-muted)] mb-1">Suite Name</label>
            <input id="tm-field-5" className="input w-full" value={suiteName} onChange={e => setSuiteName(e.target.value)} placeholder="e.g. LoginSuite, CheckoutTests" />
          </div>
        </div>
        <div>
          <label htmlFor="tm-field-6" className="block text-xs text-[var(--color-text-muted)] mb-1">Objective</label>
          <textarea id="tm-field-6" className="input w-full h-16 resize-none" value={objective} onChange={e => setObjective(e.target.value)} placeholder="What is the goal of this test?" />
        </div>
        <div>
          <label htmlFor="tm-field-7" className="block text-xs text-[var(--color-text-muted)] mb-1">Preconditions</label>
          <textarea id="tm-field-7" className="input w-full h-16 resize-none" value={preconditions} onChange={e => setPreconditions(e.target.value)} placeholder="Required state before executing" />
        </div>

        {/* Steps editor */}
        <div>
          <div className="flex items-center justify-between mb-2">
            <label className="text-xs text-[var(--color-text-muted)]">Test Steps</label>
            <button onClick={addStep} className="text-xs text-[var(--color-text)] hover:text-[var(--color-text-secondary)] flex items-center gap-1">
              <Plus className="h-3 w-3" /> Add Step
            </button>
          </div>
          <div className="space-y-2">
            {steps.map((step, i) => (
              <div key={i} className="flex gap-2 items-start bg-[var(--color-bg-secondary)] rounded-lg p-2">
                <span className="text-xs text-[var(--color-text-muted)] w-6 mt-2 flex-shrink-0">#{i + 1}</span>
                <div className="flex-1 grid grid-cols-2 gap-2">
                  <input
                    className="input text-xs"
                    value={step.action}
                    onChange={e => updateStep(i, 'action', e.target.value)}
                    placeholder="Action"
                  />
                  <input
                    className="input text-xs"
                    value={step.expected_result}
                    onChange={e => updateStep(i, 'expected_result', e.target.value)}
                    placeholder="Expected result"
                  />
                </div>
                {steps.length > 1 && (
                  <button onClick={() => removeStep(i)} className="text-[var(--color-text-faint)] hover:text-[var(--status-failed)] mt-2">
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                )}
              </div>
            ))}
          </div>
        </div>

        <div>
          <label htmlFor="tm-field-8" className="block text-xs text-[var(--color-text-muted)] mb-1">Overall Expected Result</label>
          <textarea id="tm-field-8" className="input w-full h-16 resize-none" value={expectedResult} onChange={e => setExpectedResult(e.target.value)} placeholder="Overall expected outcome" />
        </div>
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label htmlFor="tm-field-9" className="block text-xs text-[var(--color-text-muted)] mb-1">Test Data</label>
            <textarea id="tm-field-9" className="input w-full h-16 resize-none" value={testData} onChange={e => setTestData(e.target.value)} placeholder="Test data or data setup notes" />
          </div>
          <div>
            <label htmlFor="tm-field-10" className="block text-xs text-[var(--color-text-muted)] mb-1">Estimated Duration (min)</label>
            <input id="tm-field-10" className="input w-full" type="number" min="1" step="1" value={estimatedDuration} onChange={e => setEstimatedDuration(e.target.value)} placeholder="e.g. 5" />
          </div>
        </div>

        <div className="flex justify-end gap-3 pt-2 border-t border-[var(--color-border)]">
          <button onClick={onClose} className="btn-secondary">Cancel</button>
          <button onClick={handleSubmit} disabled={saving} className="btn-primary flex items-center gap-2">
            {saving && <LoadingSpinner size="sm" />}
            {saving ? 'Saving…' : 'Create Test Case'}
          </button>
        </div>
      </div>
    </ModalWrap>
  )
}

// ── Case Detail Panel ─────────────────────────────────────────────────────────

/** The case's latest automation result and the two rich pages behind it:
 *  one run's detail, and the test across runs. Shown for automation rows and
 *  for authored cases linked to automation; absent for a purely manual case. */
function LatestResultBlock({ caseItem }: { caseItem: ManagedTestCase }) {
  const resultPath = latestResultPath(caseItem)
  const historyPath = runHistoryPath(caseItem)
  if (!resultPath && !historyPath) {
    if (caseItem.source !== 'automation') return null
    return (
      <p data-testid="tm-case-no-result" className="text-xs text-[var(--color-text-muted)]">
        No execution detail is stored for this result yet.
      </p>
    )
  }
  return (
    <div
      data-testid="tm-case-latest-result"
      className="flex items-center justify-between gap-3 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-secondary)] p-3"
    >
      <div>
        <p className="text-xs text-[var(--color-text-muted)] uppercase tracking-wider mb-0.5">Latest result</p>
        <LastRunCell status={caseItem.last_execution_status ?? undefined} at={caseItem.last_executed_at ?? undefined} />
      </div>
      <div className="flex flex-wrap justify-end gap-2">
        {resultPath && (
          <Link to={resultPath} className="btn-secondary text-xs">Open latest result →</Link>
        )}
        {historyPath && (
          <Link to={historyPath} className="btn-secondary text-xs">Run history →</Link>
        )}
      </div>
    </div>
  )
}

/** W28 TCM-01: the Details toolbar's Edit (pencil + Edit); a retired case says how to edit it. */
function EditCaseButton({ retired, onEdit }: { retired: boolean; onEdit: () => void }) {
  return (
    <button
      type="button"
      onClick={onEdit}
      disabled={retired}
      title={retired ? 'Reinstate it to edit it' : undefined}
      className="btn-secondary text-xs inline-flex items-center gap-1 flex-shrink-0 disabled:cursor-not-allowed disabled:opacity-50"
    >
      <Pencil aria-hidden="true" className="h-3.5 w-3.5" />
      Edit
    </button>
  )
}

// W13 TCL-05: `timeline` (second, after Details; Details stays the default).
type DetailTab = 'details' | 'timeline' | 'lifecycle' | 'history' | 'reviews' | 'comments' | 'ai_review'

/** W28: the case pane's one open form: the edit form, or a Status move's reason. */
type CasePaneSlotKey = 'edit' | 'reason'

const isRetired = (status: string) => (RETIRED_STATES as readonly string[]).includes(status)

interface CaseDetailPanelProps {
  caseItem: ManagedTestCase
  onClose: () => void
  onRefresh: () => void
  onCaseChanged: (updated: ManagedTestCase) => void
  lifecycleV2: boolean
  /** W28: the selected project (null = All Projects, read-only). */
  projectId: string | null
  /** W28: the pane's slot (owned by the Cases tab): typed input asks before the pane closes, steps or switches. */
  slot: InlineSlot<CasePaneSlotKey>
  /** W28: the page's suite names (the edit form's Suite suggestions). */
  suiteOptions: string[]
  /** Where the case sits in the visible page of the list ("3 of 25"); absent when it is not on it. */
  position?: { index: number; total: number }
  /** Step through the list without leaving the panel; absent at either end. */
  onPrev?: () => void
  onNext?: () => void
  /** The case takes the full width (the list folds away) for long steps and history. */
  expanded: boolean
  onToggleExpand: () => void
  /** W13 TCL-06: the traceability flag (read once by the page); off = nothing of it renders. */
  requirementTraceability?: boolean
  /** W13 TCL-06: filter the case list by a requirement key (a key in the Requirements section). */
  onFilterRequirement?: (normalizedKey: string) => void
}

/**
 * The selected case, docked beside the list (master-detail).
 *
 * It was a modal pinned to the top-right corner: a backdrop covered the list,
 * so reading the next case meant closing this one first. Docked, the list
 * stays visible and clickable, a click on another row swaps the case in place,
 * previous / next step through the page, and Expand gives it the full width.
 */
function CaseDetailPanel({
  caseItem, onClose, onRefresh, onCaseChanged, lifecycleV2, projectId, slot, suiteOptions,
  position, onPrev, onNext, expanded, onToggleExpand,
  requirementTraceability = false, onFilterRequirement = () => undefined,
}: CaseDetailPanelProps) {
  const [activeTab, setActiveTab] = useState<DetailTab>('details')
  const [comment, setComment] = useState('')
  const [addingComment, setAddingComment] = useState(false)
  const [runningAiReview, setRunningAiReview] = useState(false)
  const [aiResult, setAiResult] = useState<AIReviewResult | null>(caseItem.ai_review_notes ?? null)
  const [requestingReview, setRequestingReview] = useState(false)
  // An automation row has no catalog record yet: lifecycle, history, reviews,
  // comments and AI review all need one, so its panel is Details + Promote.
  const isAutomation = caseItem.source === 'automation'

  // W28 (OR-04): the UI hides what the role cannot do; the server is the authority.
  // Writing a case (edit, Status moves, comments, AI review, request review) is TESTER+
  // in one project; All Projects is read-only. Deprecate / Archive / Reinstate stay
  // QA_LEAD+ (the contract's AMENDMENT).
  const { hasRole } = usePermissions()
  const currentUserId = useAuthStore((s) => s.user?.id ?? null)
  const isTesterUp = hasRole('TESTER')
  const canWrite = isTesterUp && Boolean(projectId)
  const canRetire = hasRole('QA_LEAD')
  const retired = isRetired(caseItem.status)
  const canEdit = canWrite && !isAutomation
  const showMoves = lifecycleV2 && canWrite && !isAutomation
  const editing = activeTab === 'details' && canEdit && !retired && slot.openKey === 'edit'
  const editedAfterApproval = caseItem.review_required === true && caseItem.edited_after_approval === true

  // A tab switch inside the pane asks first while its form has typed input, and takes the form away.
  const selectTab = (tab: DetailTab) => {
    if (tab === activeTab) return
    slot.guard(() => {
      slot.close()
      setActiveTab(tab)
    })
  }

  const { data: history, isLoading: histLoading, mutate: mutateHistory } = useTestCaseHistory(activeTab === 'history' ? caseItem.id : undefined)
  const { data: reviews, isLoading: revLoading, mutate: mutateReviews } = useTestCaseReviews(activeTab === 'reviews' ? caseItem.id : undefined)
  const { data: comments, isLoading: commLoading, mutate: mutateComments } = useTestCaseComments(
    activeTab === 'comments' ? caseItem.id : undefined
  )

  const DETAIL_TABS: { id: DetailTab; label: string; icon: React.ReactNode }[] = isAutomation ? [
    { id: 'details',   label: 'Details',    icon: <FileText className="h-3.5 w-3.5" /> },
  ] : [
    { id: 'details',   label: 'Details',    icon: <FileText className="h-3.5 w-3.5" /> },
    // W13 TCL-05: one stream of what happened to the case (History keeps compare / restore).
    { id: 'timeline',  label: 'Timeline',   icon: <ListOrdered className="h-3.5 w-3.5" /> },
    ...(lifecycleV2 ? [{ id: 'lifecycle' as const, label: 'Lifecycle', icon: <RotateCcw className="h-3.5 w-3.5" /> }] : []),
    { id: 'history',   label: 'History',    icon: <History className="h-3.5 w-3.5" /> },
    { id: 'reviews',   label: 'Reviews',    icon: <Shield className="h-3.5 w-3.5" /> },
    { id: 'comments',  label: 'Comments',   icon: <MessageSquare className="h-3.5 w-3.5" /> },
    { id: 'ai_review', label: 'AI Review',  icon: <Sparkles className="h-3.5 w-3.5" /> },
  ]

  const handleAddComment = async () => {
    if (!comment.trim()) return
    setAddingComment(true)
    try {
      await testManagementService.addComment(caseItem.id, { content: comment.trim(), comment_type: 'general' })
      setComment('')
      mutateComments()
      toast.success('Comment added')
    } catch {
      toast.error('Failed to add comment')
    } finally {
      setAddingComment(false)
    }
  }

  const handleAiReview = async () => {
    setRunningAiReview(true)
    try {
      const result = await testManagementService.aiReview(caseItem.id)
      setAiResult(result)
      onRefresh()
      toast.success('AI review complete')
    } catch {
      toast.error('AI review failed')
    } finally {
      setRunningAiReview(false)
    }
  }

  const handleLegacyRequestReview = async () => {
    setRequestingReview(true)
    try {
      await testManagementService.requestReview(caseItem.id)
      onRefresh()
      void mutateReviews()
      toast.success('Review requested')
    } catch {
      toast.error('Failed to request review')
    } finally {
      setRequestingReview(false)
    }
  }

  // The pane is the shared in-page DetailPane (W27 PAN-01) with this panel's
  // exact markup: it never moves focus (manageFocus false) and leaves Escape
  // to the page's own document-level handler (escape "none").
  return (
    <DetailPane
      title={caseItem.title}
      titleId="tm-case-detail-title"
      subtitle={
        <>
          <StatusPill status={caseItem.status} map={STATUS_COLORS} />
          <StatusPill status={caseItem.priority} map={PRIORITY_COLORS} />
          <span className="text-xs text-[var(--color-text-muted)]">{caseItem.test_type}</span>
          <span
            data-testid="tm-case-source"
            className="text-[10.5px] font-semibold uppercase tracking-wider text-[var(--color-accent)]"
          >
            {isAutomation ? 'From automation' : 'Authored'}
          </span>
          {caseItem.ai_generated && (
            <span className="text-xs text-[var(--color-purple)] flex items-center gap-0.5"><Sparkles className="h-3 w-3" /> AI</span>
          )}
          {editedAfterApproval && (
            <span data-edited-after-approval-mark="" className="text-xs text-[var(--color-text-muted)]">edited after approval</span>
          )}
        </>
      }
      onClose={onClose}
      closeLabel="Close case"
      closeTitle="Close (Esc)"
      expanded={expanded}
      onToggleExpand={onToggleExpand}
      expandLabels={{
        expand: 'Expand the case to full width',
        collapse: 'Show the list beside the case',
        expandTitle: 'Expand',
        collapseTitle: 'Show the list',
      }}
      position={position}
      onPrev={onPrev}
      onNext={onNext}
      prevLabel="Previous case"
      nextLabel="Next case"
      escape="none"
      manageFocus={false}
      slot={slot}
      bodyLayout="host"
      testIds={{ actions: 'tm-case-panel-actions', position: 'tm-case-position' }}
      dataAttributes={{ 'data-case-panel': '', 'data-expanded': expanded ? 'true' : 'false' }}
    >
        {/* Tab bar */}
        <div className="flex gap-1 px-4 pt-3 border-b border-[var(--color-border)] flex-shrink-0 overflow-x-auto">
          {DETAIL_TABS.map(t => (
            <button
              key={t.id}
              onClick={() => selectTab(t.id)}
              className={clsx(
                'flex items-center gap-1.5 px-3 py-2 text-xs font-medium rounded-t border-b-2 transition-colors whitespace-nowrap',
                activeTab === t.id
                  ? 'border-[var(--color-border-light)] text-[var(--color-text)]'
                  : 'border-transparent text-[var(--color-text-muted)] hover:text-[var(--color-text-secondary)]'
              )}
            >
              {t.icon}{t.label}
            </button>
          ))}
        </div>

        {/* Tab content */}
        <div className="flex-1 overflow-y-auto p-6">
          {/* Details tab: W28 the edit form opens HERE, in place of the read-only details (the toolbar hides). */}
          {editing && (
            <CaseEditForm
              caseItem={caseItem}
              suiteOptions={suiteOptions}
              slot={slot}
              onCancel={() => slot.close()}
              onSaved={(updated) => {
                slot.close()
                onCaseChanged(updated)
                onRefresh()
                void appMutate(['tm-case-history', updated.id])
              }}
            />
          )}
          {activeTab === 'details' && !editing && (
            <div className="space-y-4">
              {/* W28 TCM-01 / TCM-02: the Status moves on the left, Edit on the right. */}
              {showMoves ? (
                <CaseStatusControl
                  caseItem={caseItem}
                  currentUserId={currentUserId}
                  slot={slot}
                  canRetire={canRetire}
                  onChanged={(updated) => {
                    onCaseChanged(updated)
                    onRefresh()
                    void mutateReviews()
                  }}
                  edit={canEdit ? <EditCaseButton retired={retired} onEdit={() => slot.open('edit')} /> : undefined}
                />
              ) : canEdit ? (
                <div role="group" aria-label="Case actions" data-case-toolbar="" className="flex flex-wrap items-start justify-end gap-2">
                  <EditCaseButton retired={retired} onEdit={() => slot.open('edit')} />
                </div>
              ) : null}
              <LatestResultBlock caseItem={caseItem} />
              {isAutomation && (
                <div
                  data-testid="tm-case-promote"
                  className="flex items-center justify-between gap-3 rounded-lg border border-[var(--color-border)] p-3"
                >
                  <p className="text-xs text-[var(--color-text-muted)]">
                    Discovered from automation, not yet in the catalog. Promote it to add steps,
                    an owner, reviews, comments and lifecycle.
                  </p>
                  <PromotionAction
                    canonicalId={caseItem.canonical_test_case_id}
                    onPromoted={() => { onRefresh(); onClose() }}
                  />
                </div>
              )}
              {caseItem.objective && (
                <div>
                  <p className="text-xs text-[var(--color-text-muted)] uppercase tracking-wider mb-1">Objective</p>
                  <p className="text-sm text-[var(--color-text-secondary)]">{caseItem.objective}</p>
                </div>
              )}
              {caseItem.preconditions && (
                <div>
                  <p className="text-xs text-[var(--color-text-muted)] uppercase tracking-wider mb-1">Preconditions</p>
                  <p className="text-sm text-[var(--color-text-secondary)]">{caseItem.preconditions}</p>
                </div>
              )}
              {caseItem.steps && caseItem.steps.length > 0 && (
                <div>
                  <p className="text-xs text-[var(--color-text-muted)] uppercase tracking-wider mb-2">Test Steps</p>
                  <div className="space-y-2">
                    {caseItem.steps.map(step => (
                      <div key={step.step_number} className="bg-[var(--color-bg-secondary)] rounded-lg p-3">
                        <div className="flex items-start gap-3">
                          <span className="text-xs text-[var(--color-text-muted)] w-5 flex-shrink-0 mt-0.5">#{step.step_number}</span>
                          <div className="flex-1 grid grid-cols-2 gap-3 text-sm">
                            <div>
                              <span className="text-xs text-[var(--color-text-muted)] block mb-0.5">Action</span>
                              <span className="text-[var(--color-text-secondary)]">{step.action}</span>
                            </div>
                            <div>
                              <span className="text-xs text-[var(--color-text-muted)] block mb-0.5">Expected</span>
                              <span className="text-[var(--color-text-secondary)]">{step.expected_result}</span>
                            </div>
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
              {caseItem.expected_result && (
                <div>
                  <p className="text-xs text-[var(--color-text-muted)] uppercase tracking-wider mb-1">Expected Result</p>
                  <p className="text-sm text-[var(--color-text-secondary)]">{caseItem.expected_result}</p>
                </div>
              )}
              {caseItem.test_data && (
                <div>
                  <p className="text-xs text-[var(--color-text-muted)] uppercase tracking-wider mb-1">Test Data</p>
                  <pre className="text-xs text-[var(--color-text-secondary)] bg-[var(--color-bg-secondary)] rounded-lg p-3 overflow-x-auto whitespace-pre-wrap">{caseItem.test_data}</pre>
                </div>
              )}
              <div className="grid grid-cols-2 gap-3 text-sm">
                {(isAutomation ? [
                  // What an automation result actually carries; severity,
                  // version and duration are placeholders until it is promoted.
                  ['Suite', caseItem.suite_name ?? '—'],
                  ['Class', caseItem.feature_area ?? '—'],
                  ['Owner', caseItem.owner ?? 'Unassigned'],
                  ['Last Executed', fmtDate(caseItem.last_executed_at)],
                ] : [
                  ['Feature Area', caseItem.feature_area ?? '—'],
                  ['Severity', caseItem.severity],
                  ['Version', String(caseItem.version)],
                  ['Automation', caseItem.automation_status.replace(/_/g, ' ')],
                  ['Est. Duration', caseItem.estimated_duration_minutes ? `${caseItem.estimated_duration_minutes} min` : '—'],
                  ['Last Executed', fmtDate(caseItem.last_executed_at)],
                ]).map(([label, value]) => (
                  <div key={label} className="bg-[var(--color-bg-secondary)] rounded-lg p-3">
                    <p className="text-xs text-[var(--color-text-muted)] mb-0.5">{label}</p>
                    <p className={clsx('text-[var(--color-text-secondary)] break-words', !isAutomation && 'capitalize')}>{value}</p>
                  </div>
                ))}
              </div>
              {caseItem.tags && caseItem.tags.length > 0 && (
                <div>
                  <p className="text-xs text-[var(--color-text-muted)] uppercase tracking-wider mb-1">Tags</p>
                  <div className="flex flex-wrap gap-1.5">
                    {caseItem.tags.map(tag => (
                      <span key={tag} className="bg-[var(--color-bg-secondary)] text-[var(--color-text-muted)] text-xs px-2 py-0.5 rounded-full border border-[var(--color-border)]">
                        {tag}
                      </span>
                    ))}
                  </div>
                </div>
              )}
              {canWrite && !isAutomation && !lifecycleV2 && caseItem.status !== 'review_requested' && caseItem.status !== 'under_review' && (
                <div className="pt-2">
                  <button
                    type="button"
                    onClick={() => void handleLegacyRequestReview()}
                    disabled={requestingReview}
                    className="btn-secondary flex items-center gap-2 text-sm"
                  >
                    {requestingReview ? <LoadingSpinner size="sm" /> : <Shield className="h-4 w-4" />}
                    Request Review
                  </button>
                </div>
              )}
              {/* W13 TCL-06: an authored case's requirement keys, only with the
                  traceability flag on (read once by the page); hidden while the
                  edit form is open (this whole body is). */}
              {requirementTraceability && !isAutomation && (
                <CaseRequirementsSection
                  caseItem={caseItem}
                  canEdit={canWrite && !retired}
                  onFilterRequirement={onFilterRequirement}
                />
              )}
            </div>
          )}

          {/* Mounted only while open: the timeline asks nothing before. */}
          {activeTab === 'timeline' && !isAutomation && <CaseTimeline caseId={caseItem.id} />}

          {activeTab === 'lifecycle' && (
            caseItem.source === 'automation' ? (
              <p className="text-sm text-[var(--color-text-muted)]">
                Promote this automation test to a managed draft before applying lifecycle governance.
              </p>
            ) : (
              <LifecyclePanel
                caseItem={caseItem}
                onChanged={(updated) => {
                  onCaseChanged(updated)
                  onRefresh()
                  void mutateReviews()
                }}
              />
            )
          )}

          {/* History tab (W28 TCM-04): what changed per version, compare with current, restore. */}
          {activeTab === 'history' && (
            <CaseHistoryPanel
              caseItem={caseItem}
              versions={history}
              isLoading={histLoading}
              canRestore={canEdit && !retired}
              onRestored={(updated) => {
                onCaseChanged(updated)
                onRefresh()
                void mutateHistory()
              }}
              onStale={() => {
                void testCaseEditingService.getCaseFresh(caseItem.id).then(onCaseChanged, () => undefined)
                void mutateHistory()
              }}
            />
          )}

          {/* Reviews tab */}
          {activeTab === 'reviews' && (
            revLoading ? <div className="flex justify-center py-12"><LoadingSpinner /></div> :
            !reviews || reviews.length === 0 ? (
              <EmptyState icon={<Shield className="h-8 w-8" />} title="No reviews" description="Request a review to get feedback on this test case" />
            ) : (
              <div className="space-y-3">
                {(reviews as TestCaseReview[]).map(r => (
                  <div key={r.id} className="bg-[var(--color-bg-secondary)] rounded-lg p-4 space-y-2">
                    <div className="flex items-center justify-between">
                      <StatusPill status={r.status} map={STATUS_COLORS} />
                      <span className="text-xs text-[var(--color-text-muted)]">{fmtDateTime(r.created_at)}</span>
                    </div>
                    {r.ai_review_completed && r.ai_quality_score != null && (
                      <div className="flex items-center gap-2">
                        <Sparkles className="h-3.5 w-3.5 text-[var(--color-purple)]" />
                        <span className="text-xs text-[var(--color-text-muted)]">AI Score:</span>
                        <QualityScore score={r.ai_quality_score} />
                      </div>
                    )}
                    {r.human_notes && (
                      <p className="text-xs text-[var(--color-text-secondary)] border-t border-[var(--color-border)] pt-2">{r.human_notes}</p>
                    )}
                  </div>
                ))}
              </div>
            )
          )}

          {/* Comments tab */}
          {activeTab === 'comments' && (
            <div className="space-y-4">
              {commLoading ? <div className="flex justify-center py-8"><LoadingSpinner /></div> :
               !comments || comments.length === 0 ? (
                <EmptyState icon={<MessageSquare className="h-8 w-8" />} title="No comments yet" description="Start a discussion about this test case" />
               ) : (
                <div className="space-y-3">
                  {(comments as TestCaseComment[]).map(c => (
                    <div key={c.id} className={clsx('bg-[var(--color-bg-secondary)] rounded-lg p-3', c.is_resolved && 'opacity-60')}>
                      <div className="flex items-center justify-between mb-1">
                        <span className="text-xs text-[var(--color-text-muted)]">{c.comment_type}</span>
                        <span className="text-xs text-[var(--color-text-muted)]">{fmtDateTime(c.created_at)}</span>
                      </div>
                      <p className="text-sm text-[var(--color-text-secondary)]">{c.content}</p>
                      {c.step_number && <p className="text-xs text-[var(--color-text-muted)] mt-1">Step #{c.step_number}</p>}
                    </div>
                  ))}
                </div>
               )
              }
              {canWrite ? (
                <div className="border-t border-[var(--color-border)] pt-3">
                  <textarea
                    className="input w-full h-20 resize-none text-sm"
                    value={comment}
                    onChange={e => setComment(e.target.value)}
                    placeholder="Add a comment…"
                  />
                  <div className="flex justify-end mt-2">
                    <button onClick={handleAddComment} disabled={addingComment || !comment.trim()} className="btn-primary text-sm flex items-center gap-2">
                      {addingComment && <LoadingSpinner size="sm" />}
                      Post Comment
                    </button>
                  </div>
                </div>
              ) : (
                <p data-comments-read-only="" className="border-t border-[var(--color-border)] pt-3 m-0 text-xs text-[var(--color-text-muted)]">
                  {isTesterUp ? 'Choose a project to add a comment.' : 'Viewers can read comments.'}
                </p>
              )}
            </div>
          )}

          {/* AI Review tab */}
          {activeTab === 'ai_review' && (
            <div className="space-y-4">
              <div className="flex items-center justify-between">
                <p className="text-sm text-[var(--color-text-secondary)]">Run AI analysis to score this test case and get improvement suggestions.</p>
                {canWrite ? (
                  <button onClick={handleAiReview} disabled={runningAiReview} className="btn-primary flex items-center gap-2 text-sm flex-shrink-0">
                    {runningAiReview ? <LoadingSpinner size="sm" /> : <Sparkles className="h-4 w-4" />}
                    {runningAiReview ? 'Reviewing…' : 'Run AI Review'}
                  </button>
                ) : (
                  <p data-ai-review-read-only="" className="m-0 text-xs text-[var(--color-text-muted)] flex-shrink-0">
                    {isTesterUp ? 'Choose a project to run an AI review.' : 'AI review needs the Tester role or above.'}
                  </p>
                )}
              </div>
              {aiResult && (
                <div className="space-y-3">
                  <div className="flex items-center gap-4 bg-[var(--color-bg-secondary)] rounded-lg p-4">
                    <div className="text-center">
                      <p className="text-3xl font-bold tabular-nums text-[var(--color-text)]">{aiResult.quality_score ?? '—'}</p>
                      <p className="text-xs text-[var(--color-text-muted)]">Quality Score</p>
                    </div>
                    {aiResult.grade && (
                      <div className="text-center">
                        <p className="text-3xl font-bold text-[var(--status-passed)]">{aiResult.grade}</p>
                        <p className="text-xs text-[var(--color-text-muted)]">Grade</p>
                      </div>
                    )}
                    {aiResult.summary && <p className="text-sm text-[var(--color-text-secondary)] flex-1">{aiResult.summary}</p>}
                  </div>
                  {aiResult.issues && aiResult.issues.length > 0 && (
                    <div>
                      <p className="text-xs text-[var(--color-text-muted)] uppercase tracking-wider mb-2">Issues Found</p>
                      <div className="space-y-1.5">
                        {aiResult.issues.map((issue, i) => (
                          <div key={i} className="flex items-start gap-2 bg-[var(--color-bg-secondary)] rounded p-2.5">
                            <AlertCircle className={clsx('h-3.5 w-3.5 mt-0.5 flex-shrink-0',
                              issue.severity === 'critical' ? 'text-[var(--status-failed)]' : issue.severity === 'major' ? 'text-[var(--status-broken)]' : 'text-[var(--status-broken)]'
                            )} />
                            <div>
                              <p className="text-xs font-medium text-[var(--color-text-secondary)]">{issue.category}{issue.step ? ` (Step #${issue.step})` : ''}</p>
                              <p className="text-xs text-[var(--color-text-muted)]">{issue.description}</p>
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                  {aiResult.suggestions && aiResult.suggestions.length > 0 && (
                    <div>
                      <p className="text-xs text-[var(--color-text-muted)] uppercase tracking-wider mb-2">Suggestions</p>
                      <div className="space-y-1.5">
                        {aiResult.suggestions.map((s, i) => (
                          <div key={i} className="bg-[var(--color-bg-secondary)] rounded p-2.5">
                            <p className="text-xs font-medium text-[var(--color-text)] capitalize">{s.field}</p>
                            <p className="text-xs text-[var(--color-text-secondary)] mt-0.5">{s.suggestion}</p>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                  {aiResult.positive_aspects && aiResult.positive_aspects.length > 0 && (
                    <div>
                      <p className="text-xs text-[var(--color-text-muted)] uppercase tracking-wider mb-2">Positive Aspects</p>
                      <ul className="space-y-1">
                        {aiResult.positive_aspects.map((p, i) => (
                          <li key={i} className="flex items-center gap-2 text-xs text-[var(--status-passed)]">
                            <CheckCircle2 className="h-3.5 w-3.5 text-[var(--status-passed)] flex-shrink-0" />
                            {p}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
    </DetailPane>
  )
}

// ── Create Plan Modal ─────────────────────────────────────────────────────────

interface CreatePlanModalProps { projectId: string; onClose: () => void; onCreated: () => void }

function CreatePlanModal({ projectId, onClose, onCreated }: CreatePlanModalProps) {
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [objective, setObjective] = useState('')
  const [startDate, setStartDate] = useState('')
  const [endDate, setEndDate] = useState('')
  const [saving, setSaving] = useState(false)

  const handleSubmit = async () => {
    if (!name.trim()) { toast.error('Name is required'); return }
    setSaving(true)
    try {
      await testManagementService.createPlan({
        project_id: projectId,
        name: name.trim(),
        description: description || undefined,
        objective: objective || undefined,
        planned_start_date: startDate || undefined,
        planned_end_date: endDate || undefined,
        status: 'draft',
        ai_generated: false,
        total_cases: 0,
        executed_cases: 0,
        passed_cases: 0,
        failed_cases: 0,
        blocked_cases: 0,
      })
      toast.success('Test plan created')
      onCreated()
      onClose()
    } catch {
      toast.error('Failed to create plan')
    } finally {
      setSaving(false)
    }
  }

  return (
    <ModalWrap onClose={onClose} title="New Test Plan">
      <div className="space-y-4">
        <div>
          <label htmlFor="tm-field-11" className="block text-xs text-[var(--color-text-muted)] mb-1">Plan Name *</label>
          <input id="tm-field-11" className="input w-full" value={name} onChange={e => setName(e.target.value)} placeholder="e.g. Sprint 42 Regression" />
        </div>
        <div>
          <label htmlFor="tm-field-12" className="block text-xs text-[var(--color-text-muted)] mb-1">Description</label>
          <textarea id="tm-field-12" className="input w-full h-20 resize-none" value={description} onChange={e => setDescription(e.target.value)} placeholder="What does this plan cover?" />
        </div>
        <div>
          <label htmlFor="tm-field-13" className="block text-xs text-[var(--color-text-muted)] mb-1">Objective</label>
          <textarea id="tm-field-13" className="input w-full h-16 resize-none" value={objective} onChange={e => setObjective(e.target.value)} placeholder="Goals for this test plan" />
        </div>
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label htmlFor="tm-field-14" className="block text-xs text-[var(--color-text-muted)] mb-1">Planned Start</label>
            <input id="tm-field-14" type="date" className="input w-full" value={startDate} onChange={e => setStartDate(e.target.value)} />
          </div>
          <div>
            <label htmlFor="tm-field-15" className="block text-xs text-[var(--color-text-muted)] mb-1">Planned End</label>
            <input id="tm-field-15" type="date" className="input w-full" value={endDate} onChange={e => setEndDate(e.target.value)} />
          </div>
        </div>
        <div className="flex justify-end gap-3 pt-2 border-t border-[var(--color-border)]">
          <button onClick={onClose} className="btn-secondary">Cancel</button>
          <button onClick={handleSubmit} disabled={saving} className="btn-primary flex items-center gap-2">
            {saving && <LoadingSpinner size="sm" />}
            {saving ? 'Creating…' : 'Create Plan'}
          </button>
        </div>
      </div>
    </ModalWrap>
  )
}

// ─── Tab: Test Cases ──────────────────────────────────────────────────────────

/** = the server's `AUTOMATION_ROWS_EXCLUDED_TEXT` (W13 TCL-01): the note under the filter bar. */
const AUTOMATION_ROWS_EXCLUDED_TEXT = 'Authored cases only: automation-ingested tests are left out under these filters.'

interface TestCasesTabProps {
  projectId: string | null
  lifecycleV2: boolean
  /**
   * W13 TCL-06: the `test_case_requirement_traceability` flag, read ONCE by the
   * page. Default off, so a test rendering the tab directly sees none of it.
   */
  requirementTraceability?: boolean
}

/** The search box's words; with traceability on it also matches requirement keys. */
const SEARCH_PLACEHOLDER = 'Search by title, tags or ID…'
const SEARCH_PLACEHOLDER_REQUIREMENTS = 'Search by title, tags, ID or requirement…'

export function TestCasesTab({ projectId, lifecycleV2, requirementTraceability = false }: TestCasesTabProps) {
  // ── Filter state (W13 TCL-01) ───────────────────────────────────────
  // Every filter lives in the URL (`caseFilterState.ts`: `status`, `priority`,
  // `type`, `owner`, `automation`, `cases_suite`, `q`, `ai`, `requirement`),
  // written with `replace` so a click adds no history entry; `tab` and `case`
  // are kept. Unknown values are dropped on read, so they are never sent; a
  // reload restores the exact list. Any change resets the page to 1 (and the
  // bulk selection, whose scope key is built from this state).
  const [page, setPage] = useState(1)
  const [searchParams, setSearchParams] = useSearchParams()
  const routeState: unknown = useLocation().state
  const filters = useMemo(() => readCaseFilters(searchParams), [searchParams])
  // The search box types into local state (a URL write is a transition, which
  // a controlled text input must not wait on); each keystroke also writes `q`.
  const [searchText, setSearchText] = useState(filters.q)
  const applyFilters = useCallback((next: CaseFilterState) => {
    setPage(1)
    setSearchText(next.q)
    setSearchParams((prev) => writeCaseFilters(prev, next), { replace: true, state: routeState })
  }, [routeState, setSearchParams])
  const onSearchChange = useCallback((text: string) => {
    setPage(1)
    setSearchText(text)
    setSearchParams(
      (prev) => writeCaseFilters(prev, { ...readCaseFilters(prev), q: text }),
      { replace: true, state: routeState },
    )
  }, [routeState, setSearchParams])
  // W13 TCL-06: a requirement key (a link in a case, a gap in Insights) filters
  // the list by it alone on top of the other filters (the note line's chip).
  const filterByRequirement = useCallback(
    (key: string) => applyFilters({ ...filters, requirement: key }),
    [applyFilters, filters],
  )
  const presetId = activePreset(filters)
  // `Me`, then the project's members: the bulk bar's members request (one shared key).
  const { options: ownerOptions } = useCaseOwnerOptions(projectId)
  // W27b PAN-05: New test case and AI Generate open one at a time, in the
  // page, between the alerts and the cases row (the tab's form slot: a form
  // with typed input asks before the other replaces it). W13 TCL-04: Import
  // opens in the same slot (`'import'`).
  const topSlot = useInlineSlot<'create' | 'ai' | 'import'>()
  const closeTop = useSlotCloser(topSlot)
  const [showInsights, setShowInsights] = useState(false)
  const [selectedCase, setSelectedCase] = useState<ManagedTestCase | null>(null)
  // The reason form opens under its case's row. The table's slot carries the
  // typed-input question; the case is the one the trash button was clicked on.
  const rowSlot = useInlineSlot()
  const [pendingDeprecation, setPendingDeprecation] = useState<ManagedTestCase | null>(null)
  const [deprecating, setDeprecating] = useState<string | null>(null)

  // W28 (OR-04): creating, editing, the Status moves and bulk edits are TESTER+ in one
  // project (All Projects is read-only); a row's Deprecate / Delete is QA_LEAD+ (the
  // contract's AMENDMENT: the retire moves stay with QA leads and administrators).
  const { hasRole } = usePermissions()
  const canWrite = Boolean(projectId) && hasRole('TESTER')
  const canRetire = hasRole('QA_LEAD')
  // The case pane's one open form (the edit form, or a Status move's reason): closing the pane,
  // stepping to another case, a click on another row or a tab switch asks while it has typed input.
  const paneSlot = useInlineSlot<CasePaneSlotKey>()

  // One interaction for every row: the case panel, in place, so the list keeps
  // its search, filters and page. Automation rows used to leave the page for
  // one run's result (or a toast); the panel links there instead.
  // The panel docks beside the list (master-detail) and the open case rides
  // the URL (?case=<id>), so a refresh or a shared link reopens it. Every
  // write REPLACES (Back leaves the page, as it always did here). A value
  // that could walk a request path (`..`, `/`) is never read as a case: it is
  // dropped at once, without the getCase it used to cost.
  const [caseExpanded, setCaseExpanded] = useState(false)
  const caseDetail = useDetailParam('case', { history: 'replace', accept: isDetailToken })
  const caseParam = caseDetail.id
  const { open: openCaseParam, close: closeCaseParam, rejected: rejectedCaseParam } = caseDetail
  const openCase = useCallback((caseItem: ManagedTestCase) => {
    setSelectedCase(caseItem)
    openCaseParam(caseItem.id)
  }, [openCaseParam])
  const closeCase = useCallback(() => {
    setSelectedCase(null)
    setCaseExpanded(false)
    closeCaseParam()
  }, [closeCaseParam, setCaseExpanded])
  useEffect(() => {
    if (rejectedCaseParam !== null) closeCaseParam()
  }, [rejectedCaseParam, closeCaseParam])
  // Default ON so users land on a populated list — the managed_test_cases
  // table is often empty in fresh deployments, and the "Test Cases tab
  // shows nothing while runs are full of tests" surprise was the top
  // complaint pre-rollout. Persist the toggle so power users who only
  // care about authored cases can keep it off.
  const [includeAutomation, setIncludeAutomation] = useState<boolean>(() => {
    const saved = localStorage.getItem('tl.tm.includeAutomation')
    return saved === null ? true : saved === '1'
  })
  useEffect(() => {
    localStorage.setItem('tl.tm.includeAutomation', includeAutomation ? '1' : '0')
  }, [includeAutomation])
  const searchInputRef = useRef<HTMLInputElement>(null)

  // `/` shortcut focuses search per README §6.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key !== '/') return
      const t = e.target as HTMLElement | null
      const tag = t?.tagName?.toLowerCase()
      if (tag === 'input' || tag === 'textarea' || t?.isContentEditable) return
      e.preventDefault()
      searchInputRef.current?.focus()
      searchInputRef.current?.select()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  // One value of a list filter is sent as today's plain string, several as
  // repeated keys; the owner as `owner` (never the `assignee_id` the route ignored).
  const params = useMemo(() => {
    return buildTestCaseListParams({
      page,
      size: 25,
      status: filters.statuses,
      testType: filters.types,
      priority: filters.priorities,
      search: filters.q.trim(),
      // `me` works in every scope (My drafts in All Projects); another person
      // only within one project (the server refuses it otherwise).
      ownerFilter: filters.owner && (projectId || filters.owner === 'me') ? filters.owner : '',
      suiteFilter: filters.suite ?? '',
      includeAutomation,
      // W28: the AI drafts view (draft + ai_generated, a list filter the API already has).
      aiGenerated: filters.aiGenerated,
      automation: filters.automation,
      requirement: filters.requirement,
    })
  }, [page, filters, projectId, includeAutomation])

  const {
    data,
    isLoading,
    error: casesError,
    mutate: mutateCases,
  } = useTestCases(params)
  // Was a hardcoded "knowledge graph rebuilt just now" -- a fabricated
  // freshness claim (#893) about an event this page cannot observe. Report
  // when the payload actually arrived instead.
  // W13 TCL-03: the library health is the SERVER's, over the whole library
  // (`GET /test-management/health`, one read on load at the old roll's
  // background cadence; none in All Projects). It replaced a 200-row
  // `useTestCases` roll the page synthesised the banner, Insights, the Suite
  // filter's options and the review queue from (capped, so wrong for any
  // larger library). The page computes none of those numbers now.
  const {
    data: health,
    error: healthError,
    mutate: mutateHealth,
  } = useLibraryHealth(projectId)
  // When the health answer arrived ("Library refreshed 2m ago" in Insights).
  const fetchedAt = useDataFreshness(health)
  const refreshedAt = fetchedAt ? shortAgo(fetchedAt) : 'just now'
  const { data: auditRoll } = useAuditLog({ page: 1, size: 5, entity_type: 'test_case' })
  const [caseRetrying, setCaseRetrying] = useState(false)
  const [caseRefreshWarning, setCaseRefreshWarning] = useState<string | null>(null)
  const hasPreviouslyLoadedCases = Boolean(data?.items.length)

  // Both reads must be revalidated after a write: the paginated table and the
  // library health are DIFFERENT SWR keys, so refreshing only the table left
  // the health asserting "1 cases" beside a list showing 2 until the 60 s
  // background poll caught up.
  const handleRefresh = useCallback(
    () => { void mutateCases(); void mutateHealth() },
    [mutateCases, mutateHealth],
  )

  const retryCaseData = useCallback(async () => {
    if (caseRetrying) return
    setCaseRetrying(true)
    setCaseRefreshWarning(null)
    try {
      await Promise.all([mutateCases(), mutateHealth()])
    } catch {
      setCaseRefreshWarning('Test-case data is still unavailable. Previously loaded results remain visible.')
    } finally {
      setCaseRetrying(false)
    }
  }, [caseRetrying, mutateCases, mutateHealth])

  const casesRaw = data?.items ?? []
  const { sorted: cases } = useTableSort(casesRaw, 'updated_at', 'desc')

  // ── The open case ───────────────────────────────────────────────────
  // The row clicked; or, arriving with ?case=<id>, that row on this page;
  // or the case fetched by id when the link names one the page does not show.
  const [fetchedCase, setFetchedCase] = useState<ManagedTestCase | null>(null)
  const caseOnPage = !selectedCase && caseParam ? cases.find((c) => c.id === caseParam) : undefined
  const activeCase: ManagedTestCase | null =
    selectedCase ?? caseOnPage ?? (caseParam && fetchedCase?.id === caseParam ? fetchedCase : null)
  const needsCaseFetch = Boolean(caseParam) && !activeCase && !isLoading
  useEffect(() => {
    if (!needsCaseFetch || !caseParam) return
    let cancelled = false
    testManagementService.getCase(caseParam).then(
      (found) => { if (!cancelled) setFetchedCase(found) },
      // Gone, or not this user's: drop the stale link rather than show an empty panel.
      () => { if (!cancelled) closeCaseParam() },
    )
    return () => { cancelled = true }
  }, [needsCaseFetch, caseParam, closeCaseParam])

  // A click on the open case's own row folds the panel; any other row swaps it in.
  // W28: through the pane's slot, so a typed edit form asks first; the form goes with the case.
  const toggleCase = useCallback((caseItem: ManagedTestCase) => {
    paneSlot.guard(() => {
      paneSlot.close()
      if (activeCase?.id === caseItem.id) closeCase()
      else openCase(caseItem)
    })
  }, [activeCase?.id, closeCase, openCase, paneSlot])
  // Leaving the open case from the pane itself (Close, Previous / Next): DetailPane runs these
  // through the same slot's guard; each takes the pane's form away with the case.
  const leaveCase = useCallback((action: () => void) => () => {
    paneSlot.close()
    action()
  }, [paneSlot])
  // A case named by id (an Insights row: the review queue, a Strategy gaps
  // list): through the pane's slot, then `?case=` — the row on this page, else
  // the case fetched by id (`needsCaseFetch` below).
  const pickCaseById = useCallback((caseId: string) => {
    paneSlot.guard(() => {
      paneSlot.close()
      setSelectedCase(null)
      openCaseParam(caseId)
    })
  }, [openCaseParam, paneSlot])

  // "3 of 25" and previous / next, within the page the list shows.
  const activeIndex = activeCase ? cases.findIndex((c) => c.id === activeCase.id) : -1
  const casePosition = activeIndex >= 0 ? { index: activeIndex + 1, total: cases.length } : undefined
  const prevCase = activeIndex > 0 ? cases[activeIndex - 1] : undefined
  const nextCase = activeIndex >= 0 && activeIndex < cases.length - 1 ? cases[activeIndex + 1] : undefined

  // Esc closes the case, unless a field is being edited or a dialog above it owns the key.
  useEffect(() => {
    if (!activeCase) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape' || e.defaultPrevented) return
      const target = e.target as HTMLElement | null
      if (target?.closest?.('input, textarea, select, [contenteditable="true"]')) return
      if (document.querySelector('[role="dialog"][aria-modal="true"]')) return
      paneSlot.guard(() => {
        paneSlot.close()
        closeCase()
      })
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [activeCase, closeCase, paneSlot])

  // Stepping through cases keeps the matching row in view in the list.
  useEffect(() => {
    if (!activeCase?.id) return
    const row = Array.from(document.querySelectorAll<HTMLElement>('[data-case-row]'))
      .find((el) => el.dataset.caseRow === activeCase.id)
    if (row && typeof row.scrollIntoView === 'function') row.scrollIntoView({ block: 'nearest' })
  }, [activeCase?.id])

  async function handleExportExcel() {
    try {
      const url = testManagementService.exportCasesExcelUrl(projectId, params)
      const response = await api.get(url, { responseType: 'blob' })
      const blob = new Blob([response.data], { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' })
      const a = document.createElement('a')
      a.href = URL.createObjectURL(blob)
      a.download = 'test-cases.xlsx'
      a.click()
      URL.revokeObjectURL(a.href)
    } catch {
      toast.error('Export failed — please try again')
    }
  }

  function handleDeprecate(caseItem: ManagedTestCase, e: React.MouseEvent) {
    e.stopPropagation()
    if (pendingDeprecation?.id === caseItem.id) return
    // Another row's form with a typed reason asks first (the slot's question, in that form).
    rowSlot.guard(() => setPendingDeprecation(caseItem))
  }

  async function confirmDeprecation(target: ManagedTestCase, reason: string) {
    setDeprecating(target.id)
    try {
      if (lifecycleV2) {
        const updated = await testManagementService.transitionCase(target.id, {
          action: 'deprecate',
          reason,
          expected_version: target.version,
        })
        setSelectedCase((current) => current?.id === updated.id ? updated : current)
      } else {
        await testManagementService.deleteCase(target.id, reason)
      }
      // A late answer closes only its own form (another row's may have replaced it).
      setPendingDeprecation((current) => (current?.id === target.id ? null : current))
      handleRefresh()
      toast.success(lifecycleV2 ? 'Test case deprecated' : 'Test case deleted')
    } catch {
      toast.error(lifecycleV2 ? 'Failed to deprecate test case' : 'Failed to delete test case')
    } finally {
      setDeprecating((current) => (current === target.id ? null : current))
    }
  }

  // The reason form under its row: the whole width of the cases table (5 columns, 4 beside an open case).
  const deprecationRow = (tc: ManagedTestCase): ReactNode => {
    if (pendingDeprecation?.id !== tc.id) return null
    const target = pendingDeprecation
    return (
      <InlineRowSection colSpan={activeCase ? 4 : 5}>
        <TransitionReasonDialog
          title={lifecycleV2 ? 'Deprecate test case' : 'Delete test case'}
          description={`${lifecycleV2 ? 'Deprecate' : 'Delete'} ${target.title}. The case and its version history will be preserved.`}
          confirmLabel={lifecycleV2 ? 'Deprecate' : 'Delete'}
          busy={deprecating === target.id}
          slot={rowSlot}
          onCancel={() => setPendingDeprecation(null)}
          onConfirm={(reason) => confirmDeprecation(target, reason)}
        />
      </InlineRowSection>
    )
  }

  // ── Library health (the server's, W13 TCL-03) ──────────────────────
  // Two distinct totals — both surfaced separately to fix the recurring
  // "Cases 25 of 0" misread (third regression 2026-05-16). See
  // ``utils/testManagementTotals.ts`` for the helper + unit tests. The
  // authored total is the health answer's (every non-retired authored case).
  const { authoredTotal, casesTotal } = deriveTestManagementTotals({
    healthRoll: health ? { total: health.total_cases } : null,
    data,
    casesLength: cases.length,
  })
  const automatedCount = health?.automated_cases ?? 0
  // The banner's state, pill and title (`libraryHealthModel.ts`): pending with
  // no facts while there is no answer or nothing to grade — never zeros.
  const banner = bannerFor({ projectId, health, error: healthError })
  // The Suite filter's options: the authored suite names the answer carries.
  const suiteOptions = health?.suite_names ?? []

  // ── Views (W13 TCL-01) ──────────────────────────────────────────────
  // The four presets REALLY filter (`CASE_PRESETS`): My drafts, My P0/P1 (was
  // "My team's P0/P1", which filtered by nothing team-shaped: owner = me now),
  // Unautomated (`automation=not_automated`, a server filter since W13) and AI
  // drafts. A chip is active exactly when the URL state equals its filters;
  // clicking one replaces every filter key (`CaseViewsBar`, which also holds the
  // saved case views).
  const presets = CASE_PRESET_IDS.map((id) => ({ id, label: CASE_PRESET_LABELS[id] }))

  // ── W28 TCM-03: the bulk selection ──────────────────────────────────
  // Per page and local: cleared whenever the page, a filter (any key of the URL
  // state: a multi-select, the owner, automation, the search, a view),
  // Automation tests or the project changes (what you see is what you edit; no
  // "all N matching" selection). The scope key does it without an effect: a
  // selection made under another scope reads as empty.
  const selectionScope = JSON.stringify([projectId, page, caseFiltersKey(filters), includeAutomation])
  const [bulk, setBulk] = useState<{ scope: string; ids: string[]; open: boolean }>({ scope: selectionScope, ids: [], open: false })
  const bulkState = canWrite && bulk.scope === selectionScope ? bulk : { scope: selectionScope, ids: [] as string[], open: false }
  const selectableOnPage = cases.filter((tc) => isSelectable(tc, canWrite))
  const selectedCases = cases.filter((tc) => bulkState.ids.includes(tc.id))
  const setSelection = (ids: string[], open = bulkState.open) => setBulk({ scope: selectionScope, ids, open })
  const toggleSelected = (tc: ManagedTestCase) =>
    setSelection(bulkState.ids.includes(tc.id) ? bulkState.ids.filter((id) => id !== tc.id) : [...bulkState.ids, tc.id])
  const pageSelectionState: 'none' | 'some' | 'all' =
    selectedCases.length === 0 ? 'none' : selectedCases.length >= selectableOnPage.length ? 'all' : 'some'
  const togglePageSelection = () =>
    setSelection(pageSelectionState === 'all' ? [] : selectableOnPage.map((tc) => tc.id))
  const bulkActionRef = useRef<HTMLSelectElement>(null)
  const [bulkFocusRequest, setBulkFocusRequest] = useState(0)
  const showBulkBar = canWrite && Boolean(projectId) && (selectedCases.length > 0 || bulkState.open)

  // ⌘E (Mac) / Ctrl+E (elsewhere) opens the bulk bar and focuses its Action select — the
  // footer has promised it since the redesign. Only on this tab, only where bulk works, and
  // never while typing in a field (a ticked checkbox is not typing); elsewhere the browser keeps it.
  useEffect(() => {
    if (!canWrite) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'e' && e.key !== 'E') return
      if (!(e.metaKey || e.ctrlKey) || e.shiftKey || e.altKey || e.defaultPrevented) return
      const target = e.target as HTMLElement | null
      if (target?.closest?.('input:not([type="checkbox"]):not([type="radio"]), textarea, select, [contenteditable="true"]')) return
      e.preventDefault()
      setBulk((current) => ({
        scope: selectionScope,
        ids: current.scope === selectionScope ? current.ids : [],
        open: true,
      }))
      setBulkFocusRequest((n) => n + 1)
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [canWrite, selectionScope])
  useEffect(() => {
    if (bulkFocusRequest > 0) bulkActionRef.current?.focus()
  }, [bulkFocusRequest])

  const handleBulkApplied = (result: TestCaseBulkResponse) => {
    handleRefresh()
    // The bar stays (it shows what happened); the selection keeps only what was not changed.
    setSelection(pruneSelection(bulkState.ids, result), true)
    // The open case was changed: the pane re-reads it from the refreshed page.
    if (activeCase && result.results.some((r) => r.id === activeCase.id && r.outcome === 'changed')) {
      setSelectedCase(null)
    }
  }

  // ── Automation split ────────────────────────────────────────────────
  // There used to be a third bucket here, `coverageUncov`, defined as 15% of
  // the case count. It was not a measurement: TestLookup has no requirements
  // entity, no requirements table and no req-coverage endpoint, so nothing
  // anywhere could know how many requirements are uncovered. Worse, it made
  // the headline percentage a CONSTANT — covered / total reduced to
  // N / 1.15N = 87% for every project, every day, forever — which a QA lead
  // could reasonably have reported to management as a coverage figure.
  //
  // What the page can honestly say is how many authored cases are automated:
  // since W13 the server counts them over the whole library (health answer).
  const coverageAuto    = automatedCount
  const coverageManual  = Math.max(0, authoredTotal - automatedCount)

  // ── Recent activity from audit log ──────────────────────────────────
  const auditEvents: RecentEvent[] = ((auditRoll?.items ?? []) as unknown as RecentEvent[]).slice(0, 5)

  // ── Sort + Pagination ───────────────────────────────────────────────
  const sortLabel = 'updated'   // matches default useTableSort
  const totalShown = cases.length

  // ── The health banner (UX redesign P4 item 7) ───────────────────────
  // The 48 px strip above the table: the library-health verdict and four of
  // its inputs, all read from the server's answer (no sample any more, so no
  // stat-basis line). The full breakdown is the Insights drawer's first card.
  const healthSource = { projectId, health, error: healthError }

  // ── Render ──────────────────────────────────────────────────────────
  return (
    <div className="space-y-3">
      <StatusBanner
        state={banner.state}
        // The tag is the pill ("AT RISK", not the state's "FAILING"); the title the score.
        pillLabel={banner.pillLabel}
        title={banner.title}
        facts={banner.factsFrom ? [
          { label: authoredTotal === 1 ? 'Case' : 'Cases', value: authoredTotal },
          { label: 'Awaiting review', value: banner.factsFrom.review_queue.total },
          { label: 'Automated', value: automatedPctText(banner.factsFrom) },
          { label: 'Stale drafts', value: banner.factsFrom.stale_drafts },
        ] : []}
        action={{ label: 'Insights', onClick: () => setShowInsights(true) }}
      />

      {/* The former right rail (UX redesign P4 item 7), opened from the
          banner's "Insights": a read-only section right here, under the
          banner (W27b PAN-05), closed by default. Its "Generate test cases"
          card is the AI Generate form's sources now (a description, or
          documents). Opening it sends nothing: every card reads the health
          answer or the audit roll. */}
      <InsightsPanel open={showInsights} onClose={() => setShowInsights(false)}>
        <LibraryHealthCard
          {...healthSource}
          totalCases={authoredTotal}
          refreshedAt={refreshedAt}
          targetEditor={projectId && health?.measured ? (
            <AutomationTargetEditor
              projectId={projectId}
              target={health.automation_target_pct}
              onSaved={() => { void mutateHealth() }}
            />
          ) : null}
        />
        <ReviewQueueCard {...healthSource} onPick={pickCaseById} />
        <StrategyGapsCard {...healthSource} onOpenCase={pickCaseById} />
        <RecentActivityCard events={auditEvents} />
        {/* Automation split of the authored catalog */}
        {health ? (
          <AutomationCoverageCard
            auto={coverageAuto}
            manual={coverageManual}
          />
        ) : (
          <HealthPendingCard title="Automation coverage" projectId={projectId} error={healthError} />
        )}
        {/* W13 TCL-06: a sixth card only with the traceability flag on (the
            answer then carries `requirements`; null = no card). */}
        {requirementTraceability && projectId && health?.requirements && (
          <RequirementCoverageCard
            projectId={projectId}
            summary={health.requirements}
            onFilterRequirement={filterByRequirement}
            onOpenCase={pickCaseById}
          />
        )}
      </InsightsPanel>

      {/* Filter bar */}
      <CasesFilterBar
        searchInputRef={searchInputRef}
        search={searchText}
        onSearchChange={onSearchChange}
        filters={filters}
        onFiltersChange={applyFilters}
        suiteOptions={suiteOptions}
        ownerOptions={projectId ? ownerOptions : null}
        includeAutomation={includeAutomation}
        onToggleAutomation={(v) => { setIncludeAutomation(v); setPage(1) }}
        searchPlaceholder={requirementTraceability ? SEARCH_PLACEHOLDER_REQUIREMENTS : SEARCH_PLACEHOLDER}
      />
      {/* One line under the bar, only when there is something to say: the
          authored-only answer (a filter the merged automation path cannot
          apply) and the requirement filter's removable chip (set from a link
          or a gap, TCL-06). Absent in every default world. */}
      {(data?.automation_rows_excluded || filters.requirement) && (
        <div className="flex items-center gap-3 flex-wrap text-xs text-[var(--color-text-muted)]" data-cases-filter-note>
          {filters.requirement && (
            <span className="inline-flex items-center gap-1 rounded-full border border-[var(--color-border)] px-2 py-0.5 text-[var(--color-text)]">
              <span className="max-w-[28ch] truncate" title={filters.requirement}>Requirement: {filters.requirement}</span>
              <button
                type="button"
                className="text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
                aria-label={`Remove the requirement filter ${filters.requirement}`}
                onClick={() => applyFilters({ ...filters, requirement: null })}
              >
                ×
              </button>
            </span>
          )}
          {data?.automation_rows_excluded && <span role="note">{AUTOMATION_ROWS_EXCLUDED_TEXT}</span>}
        </div>
      )}

      {/* The cases read's failure (a failed health read says so in the banner
          and the Insights cards instead); Retry re-reads both. */}
      {casesError && (
        <div
          role="alert"
          className="flex items-center justify-between gap-3 rounded-lg border border-[var(--status-failed-bd)] bg-[var(--status-failed-bg)] p-3 text-sm text-[var(--status-failed)]"
        >
          <span>
            {hasPreviouslyLoadedCases
              ? 'Test cases could not be refreshed. Previously loaded results remain visible.'
              : 'Test cases could not be loaded.'}
          </span>
          <button
            type="button"
            className="btn-secondary flex-shrink-0 text-xs"
            disabled={caseRetrying}
            onClick={() => void retryCaseData()}
          >
            {caseRetrying ? 'Retrying…' : 'Retry case data'}
          </button>
        </div>
      )}

      {caseRefreshWarning && (
        <p
          role="status"
          className="rounded-lg border border-[var(--status-broken-bd)] bg-[var(--status-broken-bg)] p-3 text-sm text-[var(--status-broken)]"
        >
          {caseRefreshWarning}
        </p>
      )}

      {/* W28 TCM-03: the bulk bar, between the alerts and the cases row (above the
          create / AI forms that share this area), whenever a case is selected or
          ⌘E / Ctrl+E opened it. */}
      {showBulkBar && projectId && (
        <BulkActionBar
          projectId={projectId}
          selected={selectedCases}
          pageSelectableCount={selectableOnPage.length}
          lifecycleV2={lifecycleV2}
          canRetire={canRetire}
          suiteOptions={suiteOptions}
          onSelectPage={() => setSelection(selectableOnPage.map((tc) => tc.id))}
          onClear={() => setSelection([], false)}
          onApplied={handleBulkApplied}
          actionRef={bulkActionRef}
        />
      )}

      {/* New test case / AI Generate (the list toolbar and the empty state):
          in the page, full width, above the cases row; one at a time. */}
      {topSlot.openKey === 'create' && projectId && (
        <CreateCaseModal
          projectId={projectId}
          slot={topSlot}
          onClose={() => closeTop('create')}
          onCreated={handleRefresh}
        />
      )}
      {topSlot.openKey === 'ai' && projectId && (
        <AIGenerateModal
          projectId={projectId}
          slot={topSlot}
          onClose={() => closeTop('ai')}
        />
      )}
      {/* W13 TCL-04: Import (a dry run first, every case a Draft), in the
          same slot; after an apply both reads refresh (the cases and health). */}
      {topSlot.openKey === 'import' && projectId && (
        <ImportCasesSection
          projectId={projectId}
          slot={topSlot}
          onClose={() => closeTop('import')}
          onImported={() => handleRefresh()}
          onOpenCase={pickCaseById}
        />
      )}

      {/* The cases table, the page's primary content, at full width (the
          right rail it shared the row with is the Insights drawer). With a case
          open it shares the row with the case panel (master-detail); expanded,
          the panel takes the row and the list folds away. */}
      <MasterDetail
        rowProps={{ 'data-cases-layout': activeCase ? (caseExpanded ? 'expanded' : 'split') : 'list' }}
        expanded={caseExpanded}
        listProps={{
          'data-primary': '',
          className: 'rounded-xl overflow-hidden min-w-0',
          style: { background: 'var(--color-bg-card)', border: '1px solid var(--color-border)' },
        }}
        detail={activeCase ? (
          <CaseDetailPanel
            key={activeCase.id}
            caseItem={activeCase}
            onClose={leaveCase(closeCase)}
            onRefresh={handleRefresh}
            onCaseChanged={setSelectedCase}
            lifecycleV2={lifecycleV2}
            projectId={projectId}
            slot={paneSlot}
            suiteOptions={suiteOptions}
            position={casePosition}
            onPrev={prevCase ? leaveCase(() => openCase(prevCase)) : undefined}
            onNext={nextCase ? leaveCase(() => openCase(nextCase)) : undefined}
            expanded={caseExpanded}
            onToggleExpand={() => setCaseExpanded((v) => !v)}
            requirementTraceability={requirementTraceability}
            onFilterRequirement={filterByRequirement}
          />
        ) : null}
        list={<>
            {/* The Cases card's header: the title and the toolbar, then (W13
                TCL-01) its second row, Views — inside the primary card, so a
                wrap of the views never moves the table's top. */}
            <div className="px-4 py-3 space-y-2" style={{ borderBottom: '1px solid var(--color-border)' }}>
            <div className="flex items-center justify-between gap-2">
              <div className="flex items-center gap-4 flex-wrap min-w-0">
                <h3 className="text-[13px] font-semibold m-0 text-[var(--color-text)]">
                  Cases <span className="font-normal text-[var(--color-text-muted)] text-[11.5px] ml-2">{totalShown} of {casesTotal} · sorted by {sortLabel}</span>
                </h3>
              </div>
              <div className="flex items-center gap-2 flex-shrink-0">
                <button
                  onClick={handleExportExcel}
                  className="inline-flex items-center gap-1.5 px-2.5 py-1.5 text-[13px] text-[var(--color-text-muted)] hover:text-[var(--color-text)] border rounded-md transition-colors"
                  style={{ borderColor: 'var(--color-border)' }}
                  title="Export to Excel"
                  aria-label="Export"
                >
                  {/* Beside the case panel the list header is narrower: icons, the names in tooltips. */}
                  <FileSpreadsheet className="h-3.5 w-3.5" />{!activeCase && ' Export'}
                </button>                {canWrite && (
                  <>
                    {/* W13 TCL-04: TESTER+ with one project (the canWrite rule); icon-only beside a case, like Export. */}
                    <button
                      type="button"
                      onClick={() => topSlot.open('import')}
                      className="btn-secondary btn-sm inline-flex items-center gap-1.5"
                      title="Import test cases from CSV or Excel"
                      aria-label="Import"
                    >
                      <Upload className="h-3.5 w-3.5" />{!activeCase && ' Import'}
                    </button>
                    <button
                      onClick={() => topSlot.open('ai')}
                      aria-label="AI Generate"
                      title="AI Generate"
                      className="inline-flex items-center gap-1.5 px-2.5 py-1.5 text-[13px] rounded-md border transition-colors"
                      style={{ color: 'var(--status-flaky)', borderColor: 'color-mix(in srgb, var(--status-flaky) 30%, transparent)', background: 'color-mix(in srgb, var(--status-flaky) 6%, transparent)' }}
                    >
                      <Sparkles className="h-3.5 w-3.5" />{!activeCase && ' AI Generate'}
                    </button>
                    {/* .btn-sm: the toolbar's controls are 31 px; md would grow the row (W29a UIX-08). */}
                    <button
                      aria-label="New test case from catalog toolbar"
                      onClick={() => topSlot.open('create')}
                      className="btn-primary btn-sm inline-flex items-center gap-1.5"
                    >
                      <Plus className="h-3.5 w-3.5" /> New test case
                    </button>
                  </>
                )}
              </div>
            </div>
            <CaseViewsBar
              projectId={projectId}
              current={filters}
              activePresetId={presetId}
              presets={presets}
              onApply={applyFilters}
              canSave={Boolean(projectId)}
            />
            </div>

            {casesError && !data ? (
              <div role="status" className="px-4 py-12 text-center text-sm text-[var(--color-text-muted)]">
                No test-case data is available until the request succeeds.
              </div>
            ) : isLoading ? (
              <div className="flex items-center justify-center py-12"><LoadingSpinner size="lg" /></div>
            ) : cases.length === 0 ? (
              <EmptyStateBlock
                canCreate={canWrite}
                onCreate={() => topSlot.open('create')}
                onAiGenerate={() => topSlot.open('ai')}
                onReset={() => applyFilters(EMPTY_CASE_FILTERS)}
              />
            ) : (
              <CasesTableBody
                cases={cases}
                onRowClick={toggleCase}
                onDeprecate={handleDeprecate}
                onPromoted={handleRefresh}
                lifecycleV2={lifecycleV2}
                selectedId={activeCase?.id ?? null}
                compact={Boolean(activeCase)}
                belowRow={deprecationRow}
                canDeprecate={canRetire}
                selection={canWrite ? {
                  isSelected: (id) => bulkState.ids.includes(id),
                  canSelect: (tc) => isSelectable(tc, canWrite),
                  onToggle: toggleSelected,
                  pageState: pageSelectionState,
                  onTogglePage: togglePageSelection,
                } : undefined}
              />
            )}

            {data && data.total > 0 && (
              <CasesTableFooter
                shown={totalShown}
                total={data.total}
                pages={data.pages}
                page={data.page}
                onPage={setPage}
                bulkHint={canWrite ? (isMacPlatform() ? '⌘E bulk edit' : 'Ctrl+E bulk edit') : null}
              />
            )}
        </>}
      />

      <EvidenceGapLists projectId={projectId} />
    </div>
  )
}

// ── Filter bar ──────────────────────────────────────────────────────────

/** The footer's bulk-edit hint names the platform's key (⌘ on a Mac, Ctrl elsewhere). */
function isMacPlatform(): boolean {
  if (typeof navigator === 'undefined') return false
  return /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent)
}

interface CasesFilterBarProps {
  searchInputRef: React.RefObject<HTMLInputElement | null>
  search: string
  onSearchChange: (v: string) => void
  /** W13 TCL-01: the URL state; every control writes a whole new state. */
  filters: CaseFilterState
  onFiltersChange: (next: CaseFilterState) => void
  suiteOptions: string[]
  /** `Me`, then the project's members; null in All Projects (no Owner control). */
  ownerOptions: { value: string; label: string }[] | null
  // "Show automation-ingested tests too" toggle — merges per-run TestCase
  // rows (dedup'd by fingerprint) into the listing alongside ManagedTestCase.
  includeAutomation: boolean
  onToggleAutomation: (v: boolean) => void
  /** W13 TCL-06: the search also matches requirement keys while traceability is on. */
  searchPlaceholder?: string
}

/** The Priority filter's options (= the server's `CASE_PRIORITIES`). */
const PRIORITY_FILTER_OPTIONS = [
  { value: 'critical', label: 'Critical' },
  { value: 'high', label: 'High' },
  { value: 'medium', label: 'Medium' },
  { value: 'low', label: 'Low' },
]
const TYPE_FILTER_OPTIONS = CASE_TYPE_OPTIONS.map((o) => ({ value: o.value, label: o.label }))

/** A single-value MultiSelect's value (`max={1}`): the list form of one value or none. */
const one = (value: string | null): string[] => (value ? [value] : [])

function CasesFilterBar({
  // Destructure props so the RefObject is a named binding. The react-hooks v7
  // `refs` rule treats every member access on a props object that *contains* a
  // ref as a ref-read-during-render; destructuring keeps the ref separate from
  // the plain values that legitimately drive the render.
  searchInputRef,
  search,
  onSearchChange,
  filters,
  onFiltersChange,
  suiteOptions,
  ownerOptions,
  includeAutomation,
  onToggleAutomation,
  searchPlaceholder = 'Search by title, tags or ID…',
}: CasesFilterBarProps) {
  const suiteChoices = useMemo(() => suiteOptions.map((s) => ({ value: s, label: s })), [suiteOptions])
  return (
    <div
      className="flex items-center flex-wrap gap-2 rounded-md"
      style={{
        background: 'var(--color-bg-card)',
        border: '1px solid var(--color-border)',
        padding: '10px 12px',
      }}
    >
      {/* Search. 200 px minimum, not 240: at 1280 on CI's DejaVu the bar has
          22 px of slack with two filters set and wrapped with three. */}
      <div
        className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md flex-1"
        style={{ background: 'var(--color-bg)', border: '1px solid var(--color-border)', minWidth: 200, height: 32 }}
      >
        <input
          ref={searchInputRef}
          type="search"
          value={search}
          onChange={(e) => onSearchChange(e.target.value)}
          placeholder={searchPlaceholder}
          className="bg-transparent text-[13px] text-[var(--color-text)] outline-none flex-1"
          aria-label="Search cases"
        />
        <kbd
          className="font-mono text-[10.5px] px-1.5 py-px rounded-sm"
          style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)', color: 'var(--color-text-secondary)' }}
        >
          /
        </kbd>
      </div>

      {/* Include automation-ingested tests — merges synthesised rows from
          per-run test_cases into the listing alongside ManagedTestCase. */}
      <label
        className="inline-flex items-center gap-1.5 px-2 py-1 rounded-md text-[12px] text-[var(--color-text-secondary)] cursor-pointer select-none"
        style={{ background: 'var(--color-bg)', border: '1px solid var(--color-border)', height: 32 }}
        title="Include test cases discovered via automation runs (dedup'd by fingerprint)"
      >
        <input
          type="checkbox"
          checked={includeAutomation}
          onChange={(e) => onToggleAutomation(e.target.checked)}
          className="accent-[var(--color-accent)]"
        />
        Automation tests
      </label>

      {/* W13 TCL-01: the reviewed multi-value control (anchored popovers, no
          new one): Status / Type / Priority take several values, Suite and
          Owner one (`max={1}`). Owner is per project (absent in All Projects). */}
      <MultiSelect
        label="Status"
        options={LIFECYCLE_STATUS_OPTIONS}
        value={filters.statuses}
        onChange={(statuses) => onFiltersChange({ ...filters, statuses })}
        triggerData={{ 'data-case-filter': 'status' }}
      />
      <MultiSelect
        label="Type"
        options={TYPE_FILTER_OPTIONS}
        value={filters.types}
        onChange={(types) => onFiltersChange({ ...filters, types })}
        triggerData={{ 'data-case-filter': 'type' }}
      />
      <MultiSelect
        label="Priority"
        options={PRIORITY_FILTER_OPTIONS}
        value={filters.priorities}
        onChange={(priorities) => onFiltersChange({ ...filters, priorities })}
        triggerData={{ 'data-case-filter': 'priority' }}
      />
      <MultiSelect
        label="Suite"
        options={suiteChoices}
        value={one(filters.suite)}
        max={1}
        onChange={(next) => onFiltersChange({ ...filters, suite: next[0] ?? null })}
        triggerData={{ 'data-case-filter': 'suite' }}
      />
      {ownerOptions && (
        <MultiSelect
          label="Owner"
          options={ownerOptions}
          value={one(filters.owner)}
          max={1}
          onChange={(next) => onFiltersChange({ ...filters, owner: next[0] ?? null })}
          triggerData={{ 'data-case-filter': 'owner' }}
        />
      )}
    </div>
  )
}
// ── Cases table body + footer ─────────────────────────────────────────

/** W28 TCM-03: the bulk selection's hooks into the table (absent: no checkboxes, the table as before). */
export interface CasesTableSelection {
  isSelected: (id: string) => boolean
  /** Authored rows the viewer may edit (automation rows get no checkbox). */
  canSelect: (tc: ManagedTestCase) => boolean
  onToggle: (tc: ManagedTestCase) => void
  /** The header checkbox: checked when every selectable row of the page is, indeterminate for some. */
  pageState: 'none' | 'some' | 'all'
  onTogglePage: () => void
}

const SELECT_BOX = 'h-3.5 w-3.5 flex-shrink-0 cursor-pointer accent-[var(--color-accent)]'

/** The page checkbox, inside the Test case header (never a sixth column). */
function PageSelectBox({ state, onToggle }: { state: 'none' | 'some' | 'all'; onToggle: () => void }) {
  const ref = useRef<HTMLInputElement>(null)
  useEffect(() => {
    if (ref.current) ref.current.indeterminate = state === 'some'
  }, [state])
  return (
    <input
      ref={ref}
      type="checkbox"
      aria-label="Select all on this page"
      checked={state === 'all'}
      onChange={onToggle}
      className={SELECT_BOX}
    />
  )
}

// Exported for isolated column-structure tests (same precedent as OwnerCell).
export function CasesTableBody({
  cases, onRowClick, onDeprecate, onPromoted, lifecycleV2, selectedId = null, compact = false, belowRow,
  canDeprecate = true, selection,
}: {
  cases: ManagedTestCase[]
  onRowClick: (c: ManagedTestCase) => void
  onDeprecate: (caseItem: ManagedTestCase, e: React.MouseEvent) => void
  onPromoted: () => void
  lifecycleV2: boolean
  /** The case open in the panel: its row is marked. */
  selectedId?: string | null
  /** Beside the case panel the list is narrower: Owner gives its width to the title (the panel shows it). */
  compact?: boolean
  /** W27b PAN-05: the `<tr>` rendered right after a case's row (its reason form, an `InlineRowSection`), or nothing. */
  belowRow?: (tc: ManagedTestCase) => ReactNode
  /** W28: false below QA lead: the row's Deprecate / Delete is not offered (its cell stays). Default true. */
  canDeprecate?: boolean
  /** W28 TCM-03: the bulk selection; absent, no checkboxes. */
  selection?: CasesTableSelection
}) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[12.5px]" role="table">
        <thead>
          <tr style={{ background: 'var(--color-bg-secondary)', borderBottom: '1px solid var(--color-border)' }}>
            {/* ID, Priority and Automation lost their fixed columns — they now
                ride the meta line under the title. Seven fixed widths (660px)
                plus Title's minWidth:280 demanded ~940px, so Title collapsed to
                a few characters and Last run + actions scrolled out of view. */}
            <Th
              label="Test case"
              flex
              leading={selection && cases.some(selection.canSelect)
                ? <PageSelectBox state={selection.pageState} onToggle={selection.onTogglePage} />
                : undefined}
            />
            <Th label="Status"     width={100} />
            {!compact && <Th label="Owner" width={122} />}
            <Th label="Last run"   width={110} align="right" />
            <Th label="" align="right" width={40} />
          </tr>
        </thead>
        <tbody>
          {cases.map(tc => (
            <CaseRow
              key={tc.id}
              tc={tc}
              onRowClick={onRowClick}
              onDeprecate={onDeprecate}
              onPromoted={onPromoted}
              lifecycleV2={lifecycleV2}
              selected={tc.id === selectedId}
              compact={compact}
              belowRow={belowRow}
              canDeprecate={canDeprecate}
              selection={selection && selection.canSelect(tc)
                ? { checked: selection.isSelected(tc.id), onToggle: () => selection.onToggle(tc) }
                : undefined}
            />
          ))}
        </tbody>
      </table>
    </div>
  )
}

function Th({ label, width, align, flex, leading }: {
  label: string
  width?: number
  align?: 'right'
  /**
   * Marks THE flexible column. `width: 100%` claims the leftover space and
   * `maxWidth: 0` lets the browser shrink the column below its content's
   * min-content width, which is what allows the inner `truncate` to engage.
   * Without maxWidth the column sizes to its longest title and pushes the
   * trailing columns out of the scroll wrapper.
   */
  flex?: boolean
  /** W28: a control before the label, inside the same header cell (the page checkbox). */
  leading?: ReactNode
}) {
  return (
    <th
      style={{
        padding: '8px 12px',
        textAlign: align ?? 'left',
        color: 'var(--color-text-muted)',
        fontWeight: 500,
        fontSize: 10.5,
        textTransform: 'uppercase',
        letterSpacing: 'var(--tracking-wider)',
        width: flex ? '100%' : width,
        maxWidth: flex ? 0 : undefined,
        whiteSpace: 'nowrap',
      }}
    >
      {/* A block flex row, not an inline one: an inline box around the checkbox grew the header's line box (+4 px). */}
      {leading ? <div className="flex items-center gap-2">{leading}{label}</div> : label}
    </th>
  )
}

function CaseRow({
  tc,
  onRowClick,
  onDeprecate,
  onPromoted,
  lifecycleV2,
  selected = false,
  compact = false,
  belowRow,
  canDeprecate = true,
  selection,
}: {
  tc: ManagedTestCase
  onRowClick: (c: ManagedTestCase) => void
  onDeprecate: (caseItem: ManagedTestCase, e: React.MouseEvent) => void
  onPromoted: () => void
  lifecycleV2: boolean
  selected?: boolean
  compact?: boolean
  belowRow?: (tc: ManagedTestCase) => ReactNode
  canDeprecate?: boolean
  /** W28: this row's checkbox (inside the Test case cell, before the title). */
  selection?: { checked: boolean; onToggle: () => void }
}) {
  const deprecateAllowed = lifecycleV2 ? tc.allowed_actions?.includes('deprecate') === true : true
  const editedAfterApproval = tc.review_required === true && tc.edited_after_approval === true
  // Only emit the type/suite/tags tail when there is something in it — otherwise
  // the leading separator renders as an orphaned '·'.
  const hasTail = Boolean(
    (tc.test_type ?? '').trim() || tc.suite_name || (tc.tags && tc.tags.length > 0),
  )
  return (
    <>
    <tr
      data-case-row={tc.id}
      data-selected={selected ? 'true' : undefined}
      aria-current={selected ? 'true' : undefined}
      style={{
        borderBottom: '1px solid var(--color-border)',
        cursor: 'pointer',
        // The open case: a tinted row with an accent edge, so the list says which case the panel shows.
        background: selected ? 'color-mix(in srgb, var(--color-accent) 9%, transparent)' : undefined,
        boxShadow: selected ? 'inset 3px 0 0 var(--color-accent)' : undefined,
      }}
      className={clsx('transition-colors', !selected && 'hover:bg-[var(--color-bg-hover)]')}
      onClick={() => onRowClick(tc)}
    >
      <td style={{ padding: '9px 12px', width: '100%', maxWidth: 0 }}>
        <div className="flex items-center gap-2 min-w-0">
          {selection && (
            <input
              type="checkbox"
              aria-label={`Select ${tc.title}`}
              checked={selection.checked}
              onChange={selection.onToggle}
              // Ticking a case never opens it.
              onClick={(event) => event.stopPropagation()}
              className={SELECT_BOX}
            />
          )}
          <span className="text-[12.5px] font-medium text-[var(--color-text)] truncate" title={tc.title}>{tc.title}</span>
          {tc.ai_generated && <Sparkles className="h-3 w-3 text-[var(--status-flaky)] flex-shrink-0" aria-label="AI generated" />}
          {tc.source === 'automation' && (
            <span
              title="Discovered via an automation run — not authored in the test catalog"
              className="inline-flex items-center px-1.5 py-0 rounded-full text-[9.5px] font-semibold uppercase tracking-wider flex-shrink-0"
              style={{ background: 'color-mix(in srgb, var(--color-accent) 10%, transparent)', border: '1px solid color-mix(in srgb, var(--color-accent) 30%, transparent)', color: 'var(--color-accent)', letterSpacing: '0.06em' }}
            >
              Auto
            </span>
          )}
        </div>
        <div className="flex items-center gap-1.5 text-[11px] text-[var(--color-text-muted)] mt-0.5 min-w-0">
          <span className="font-mono flex-shrink-0">TC-{tc.id.slice(0, 6).toUpperCase()}</span>
          <span aria-hidden className="flex-shrink-0">·</span>
          <span className="flex-shrink-0"><CasePriorityTag priority={tc.priority} /></span>
          <span aria-hidden className="flex-shrink-0">·</span>
          <span className="inline-flex items-center gap-1 flex-shrink-0">
            <i
              aria-hidden
              style={{
                width: 6,
                height: 6,
                borderRadius: 999,
                background: tc.is_automated ? 'var(--status-passed)' : 'var(--color-text-muted)',
              }}
            />
            {tc.is_automated ? 'Automated' : 'Manual'}
          </span>
          {editedAfterApproval && (
            <span className="flex-shrink-0" data-edited-after-approval-mark="">
              <span aria-hidden>· </span>edited after approval
            </span>
          )}
          {hasTail && (
            <span className="truncate">
              <span aria-hidden>· </span>
              {(tc.test_type ?? '').replace(/_/g, ' ')}
              {tc.suite_name ? <> · suite: <span className="text-[var(--color-text-secondary)]">{tc.suite_name}</span></> : null}
              {tc.tags && tc.tags.length > 0 ? <> · tags: <span className="text-[var(--color-text-secondary)]">{tc.tags.slice(0, 3).join(', ')}</span></> : null}
            </span>
          )}
        </div>
      </td>
      <td style={{ padding: '10px 12px', whiteSpace: 'nowrap' }}>
        <CaseStatusPill status={tc.status} />
      </td>
      {!compact && <td style={{ padding: '9px 12px' }} className="min-w-0">
        <OwnerCell owner={tc.owner ?? null} userId={tc.assignee_id ?? tc.author_id ?? null} />
      </td>}
      <td
        style={{ padding: '9px 12px', textAlign: 'right' }}
        title={tc.last_executed_at ? new Date(tc.last_executed_at).toISOString() : undefined}
      >
        <LastRunCell status={tc.last_execution_status} at={tc.last_executed_at} />
      </td>
      <td style={{ padding: '10px 12px', textAlign: 'right' }}>
        {tc.source === 'automation' ? (
          <PromotionAction
            canonicalId={tc.canonical_test_case_id}
            compact
            onPromoted={onPromoted}
          />
        ) : canDeprecate && (
          <button
            type="button"
            onClick={(event) => onDeprecate(tc, event)}
            disabled={!deprecateAllowed}
            title={deprecateAllowed ? (lifecycleV2 ? 'Deprecate' : 'Delete') : 'Deprecation is not allowed from the current state'}
            aria-label={`${lifecycleV2 ? 'Deprecate' : 'Delete'} ${tc.title}`}
            className="text-[var(--color-text-faint)] transition-colors p-1 enabled:hover:text-[var(--status-failed)] disabled:cursor-not-allowed disabled:opacity-40"
          >
            <Trash2 className="h-3.5 w-3.5" />
          </button>
        )}
      </td>
    </tr>
    {belowRow?.(tc)}
    </>
  )
}

function CaseStatusPill({ status }: { status: string }) {
  const map: Record<string, { bg: string; bd: string; fg: string; label: string; lt?: boolean }> = {
    active:           { bg: 'color-mix(in srgb, var(--status-passed) 10%, transparent)',  bd: 'color-mix(in srgb, var(--status-passed) 30%, transparent)',  fg: 'var(--status-passed)', label: 'Active' },
    approved:         { bg: 'color-mix(in srgb, var(--color-accent) 10%, transparent)', bd: 'color-mix(in srgb, var(--color-accent) 30%, transparent)', fg: 'var(--color-accent)', label: 'Approved' },
    review_requested: { bg: 'color-mix(in srgb, var(--status-broken) 10%, transparent)', bd: 'color-mix(in srgb, var(--status-broken) 30%, transparent)', fg: 'var(--status-broken)', label: 'Review' },
    under_review:     { bg: 'color-mix(in srgb, var(--status-broken) 10%, transparent)', bd: 'color-mix(in srgb, var(--status-broken) 30%, transparent)', fg: 'var(--status-broken)', label: 'Under review' },
    needs_update:     { bg: 'color-mix(in srgb, var(--status-broken) 10%, transparent)', bd: 'color-mix(in srgb, var(--status-broken) 30%, transparent)', fg: 'var(--status-broken)', label: 'Needs update' },
    draft:            { bg: 'var(--color-bg-secondary)', bd: 'var(--color-border)', fg: 'var(--color-text-muted)', label: 'Draft' },
    // W29a UIX-13: the retired states on theme tokens (were a fixed stone grey).
    deprecated:       { bg: 'color-mix(in srgb, var(--color-text-muted) 12%, transparent)', bd: 'color-mix(in srgb, var(--color-text-muted) 30%, transparent)', fg: 'var(--color-text-secondary)', label: 'Deprecated', lt: true },
    archived:         { bg: 'color-mix(in srgb, var(--color-text-muted) 8%, transparent)', bd: 'color-mix(in srgb, var(--color-text-muted) 22%, transparent)', fg: 'var(--color-text-faint)', label: 'Archived' },
    rejected:         { bg: 'color-mix(in srgb, var(--status-failed) 10%, transparent)', bd: 'color-mix(in srgb, var(--status-failed) 30%, transparent)', fg: 'var(--status-failed)', label: 'Rejected' },
  }
  const p = map[status] ?? { bg: 'var(--color-bg-secondary)', bd: 'var(--color-border)', fg: 'var(--color-text-muted)', label: status.replace(/_/g, ' ') }
  return (
    <span
      className="inline-flex items-center gap-1.5 px-1.5 py-0.5 rounded-full text-[10.5px]"
      style={{
        background: p.bg,
        border: `1px solid ${p.bd}`,
        color: p.fg,
        textDecoration: p.lt ? 'line-through' : undefined,
      }}
    >
      <i aria-hidden style={{ width: 5, height: 5, borderRadius: 999, background: 'currentColor' }} />
      {p.label}
    </span>
  )
}

/**
 * A case's priority as its P-level, in the ONE severity palette (W29a
 * UIX-07): critical P0, high P1, medium P2, low P3 (any vocabulary
 * `severityLevel` knows); an unknown word reads as P3, as before.
 */
export function CasePriorityTag({ priority }: { priority: string }) {
  const level = severityLevel(priority) ?? 'P3'
  const p = SEVERITY_STYLE[level]
  return (
    <span
      data-priority-level={level}
      className="inline-flex items-center px-1.5 py-0.5 rounded-sm text-[10.5px] font-semibold uppercase"
      style={{ background: p.bg, border: `1px solid ${p.bd}`, color: p.fg, letterSpacing: '0.04em' }}
    >
      {level}
    </span>
  )
}

export function OwnerCell({ owner, userId }: { owner: string | null; userId: string | null }) {
  const displayOwner = owner?.trim()
  if (displayOwner) {
    const initials = displayOwner
      .split(/\s+/)
      .filter(Boolean)
      .slice(0, 2)
      .map(part => part[0])
      .join('')
      .toUpperCase()
    // W29a UIX-01 / UIX-13: the one avatar map — legible fg initials on its bg, never white.
    const c = avatarColorForName(displayOwner)
    return (
      <span className="inline-flex items-center gap-1.5 text-[11.5px] text-[var(--color-text-secondary)]">
        <span
          aria-hidden
          data-owner-avatar=""
          className="inline-flex items-center justify-center rounded-full font-bold"
          style={{ width: 18, height: 18, background: c.bg, color: c.fg, fontSize: 9 }}
        >
          {initials || '?'}
        </span>
        <span className="truncate" title={displayOwner}>{displayOwner}</span>
      </span>
    )
  }
  if (!userId) {
    return (
      <span className="inline-flex items-center gap-1.5 text-[11.5px] text-[var(--color-text-muted)]" aria-label="Unassigned">
        <span
          aria-hidden
          className="inline-flex items-center justify-center rounded-full text-[9px]"
          style={{ width: 18, height: 18, background: 'var(--color-bg-secondary)', border: '1px dashed var(--color-border-light)', color: 'var(--color-text-muted)' }}
        >?</span>
        Unassigned
      </span>
    )
  }
  const initials = userId.slice(0, 2).toUpperCase()
  const c = avatarColorForName(userId)
  return (
    <span className="inline-flex items-center gap-1.5 text-[11.5px] text-[var(--color-text-secondary)]">
      <span
        aria-hidden
        data-owner-avatar=""
        className="inline-flex items-center justify-center rounded-full font-bold"
        style={{ width: 18, height: 18, background: c.bg, color: c.fg, fontSize: 9 }}
      >
        {initials}
      </span>
      <span className="truncate" title={userId}>{userId.slice(0, 8)}</span>
    </span>
  )
}

function LastRunCell({ status, at }: { status: string | undefined; at: string | undefined }) {
  const now = useNow()  // hook before any early return — avoids impure Date.now() in render
  if (!at) return <span className="text-[11.5px] text-[var(--color-text-faint)]">—</span>
  const ms = now - new Date(at).getTime()
  if (Number.isNaN(ms) || ms < 0) return <span className="text-[11.5px] text-[var(--color-text-faint)]">—</span>
  const m = Math.floor(ms / 60000)
  const ageLabel = m < 1 ? 'just now'
    : m < 60 ? `${m}m`
    : m < 1440 ? `${Math.floor(m / 60)}h`
    : `${Math.floor(m / 1440)}d`
  const s = (status ?? '').toLowerCase()
  const isPass = /pass/i.test(s)
  const isFail = /fail|error|broken/i.test(s)
  if (isFail) {
    return <span className="text-[11.5px]" style={{ color: 'var(--status-failed)' }}>FAIL · {ageLabel}</span>
  }
  if (isPass) {
    return <span className="text-[11.5px]" style={{ color: 'var(--status-passed)' }}>PASS · {ageLabel}</span>
  }
  return <span className="text-[11.5px] text-[var(--color-text-muted)]">{ageLabel} ago</span>
}

function CasesTableFooter({ shown, total, pages, page, onPage, bulkHint = null }: {
  shown: number
  total: number
  pages: number
  page: number
  onPage: (p: number) => void
  /** W28: '⌘E bulk edit' / 'Ctrl+E bulk edit' where bulk works; null elsewhere. The ↑/↓ and ↵ hints had no
   *  handler (a placeholder shipped as fact) and are gone. */
  bulkHint?: string | null
}) {
  return (
    <div
      className="flex items-center justify-between gap-3 px-4 py-2.5 text-[11.5px] text-[var(--color-text-muted)]"
      style={{ borderTop: '1px solid var(--color-border)' }}
    >
      <span data-cases-footer-count="">{shown} of {total}{bulkHint ? ` · ${bulkHint}` : ''}</span>
      <Pagination page={page} pages={pages} total={total} onChange={onPage} />
    </div>
  )
}

function EmptyStateBlock({
  canCreate, onCreate, onAiGenerate, onReset,
}: {
  /** W28: TESTER+ in one project (it was: a project is selected). */
  canCreate: boolean
  onCreate: () => void
  onAiGenerate: () => void
  onReset: () => void
}) {
  return (
    <div className="flex flex-col items-center text-center px-4 py-12">
      <ClipboardList className="h-10 w-10 text-[var(--color-text-faint)] mb-3" />
      <p className="text-[13px] m-0 font-medium text-[var(--color-text)]">No test cases match your filters.</p>
      <p className="text-[12px] m-0 mt-1 text-[var(--color-text-muted)]">Reset filters, or generate cases from your knowledge graph.</p>
      <div className="flex gap-2 mt-3 flex-wrap justify-center">
        <button
          onClick={onReset}
          className="inline-flex items-center gap-1.5 px-2.5 py-1.5 text-[13px] text-[var(--color-text-muted)] hover:text-[var(--color-text)] border rounded-md"
          style={{ borderColor: 'var(--color-border)' }}
        >
          Reset filters
        </button>
        {canCreate && (
          <>
            <button
              onClick={onAiGenerate}
              className="inline-flex items-center gap-1.5 px-2.5 py-1.5 text-[13px] rounded-md border"
              style={{ color: 'var(--status-flaky)', borderColor: 'color-mix(in srgb, var(--status-flaky) 30%, transparent)', background: 'color-mix(in srgb, var(--status-flaky) 6%, transparent)' }}
            >
              <Sparkles className="h-3.5 w-3.5" /> AI Generate
            </button>
            {/* .btn-sm: beside two 31 px controls (W29a UIX-08). */}
            <button
              onClick={onCreate}
              className="btn-primary btn-sm inline-flex items-center gap-1.5"
            >
              <Plus className="h-3.5 w-3.5" /> New test case
            </button>
          </>
        )}
      </div>
    </div>
  )
}

// ─── Plan Items Expanded View ─────────────────────────────────────────────────

interface PlanItemsViewProps { planId: string; onMutate: () => void; projectId?: string | null }

function PlanItemsView({ planId, onMutate, projectId = null }: PlanItemsViewProps) {
  const { data: items, isLoading, mutate } = usePlanItems(planId)
  const [executing, setExecuting] = useState<string | null>(null)
  const [showLinkSuite, setShowLinkSuite] = useState(false)
  // W28 TCM-05: one item's old steps open at a time, in a row under it.
  const [openVersion, setOpenVersion] = useState<string | null>(null)

  const handleExecute = async (itemId: string, execStatus: string) => {
    setExecuting(itemId)
    try {
      await testManagementService.executeItem(planId, itemId, { execution_status: execStatus })
      mutate()
      onMutate()
      toast.success('Execution recorded')
    } catch {
      toast.error('Failed to record execution')
    } finally {
      setExecuting(null)
    }
  }

  const EXEC_COLORS: Record<string, string> = {
    not_run: 'text-[var(--color-text-muted)]',
    passed:  'text-[var(--status-passed)]',
    failed:  'text-[var(--status-failed)]',
    blocked: 'text-[var(--status-broken)]',
    // W29a UIX-03: a skipped RESULT is the skipped hue (was muted grey).
    skipped: 'text-[var(--status-skipped)]',
  }

  if (isLoading) return <div className="flex justify-center py-4"><LoadingSpinner size="sm" /></div>

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-end">
        <button
          onClick={() => setShowLinkSuite(true)}
          className="btn-secondary flex items-center gap-1.5 text-xs"
        >
          <Layers className="h-3.5 w-3.5" /> Link Suite
        </button>
      </div>
      {showLinkSuite && (
        <LinkSuiteModal
          planId={planId}
          projectId={projectId}
          onClose={() => setShowLinkSuite(false)}
          onLinked={() => { mutate(); onMutate(); setShowLinkSuite(false) }}
        />
      )}
      {(!items || items.length === 0) ? (
        <p className="text-sm text-[var(--color-text-muted)] text-center py-4">No test cases in this plan yet. Link a suite or add test cases.</p>
      ) : (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr>
            <th className="th text-left">#</th>
            <th className="th text-left">Test Case</th>
            <th className="th text-left">Status</th>
            {/* W28 TCM-05: the case version the recorded execution ran against (after Status: its cell stays index 2). */}
            <th className="th text-left whitespace-nowrap">Ran against</th>
            <th className="th text-right">Actions</th>
          </tr>
        </thead>
        <tbody>
          {(items as TestPlanItem[]).map((item, idx) => (
            <Fragment key={item.id}>
            <tr className="table-row">
              <td className="td text-[var(--color-text-muted)]">{idx + 1}</td>
              <td className="td text-[var(--color-text-secondary)]">{item.test_case_id.slice(0, 8)}…</td>
              <td className="td">
                <span className={clsx('text-xs font-medium capitalize', EXEC_COLORS[item.execution_status] ?? 'text-[var(--color-text-muted)]')}>
                  {item.execution_status.replace(/_/g, ' ')}
                </span>
              </td>
              <td className="td whitespace-nowrap">
                <PlanItemVersionCell
                  item={item}
                  open={openVersion === item.id}
                  onToggle={() => setOpenVersion((current) => (current === item.id ? null : item.id))}
                />
              </td>
              <td className="td text-right">
                <div className="flex items-center justify-end gap-1">
                  {['passed','failed','blocked'].map(s => (
                    <button
                      key={s}
                      disabled={executing === item.id}
                      onClick={() => handleExecute(item.id, s)}
                      className={clsx(
                        'text-xs px-2 py-0.5 rounded transition-colors',
                        s === 'passed'  ? 'bg-[var(--status-passed-bg)] text-[var(--status-passed)] hover:bg-[var(--status-passed-bd)]' :
                        s === 'failed'  ? 'bg-[var(--status-failed-bg)] text-[var(--status-failed)] hover:bg-[var(--status-failed-bd)]' :
                                          'bg-[var(--status-broken-bg)] text-[var(--status-broken)] hover:bg-[var(--status-broken-bd)]'
                      )}
                    >
                      {s}
                    </button>
                  ))}
                </div>
              </td>
            </tr>
            {openVersion === item.id && (
              <InlineRowSection colSpan={5} dataAttributes={{ 'data-plan-item-version-row': item.id }}>
                <PlanItemVersionSteps item={item} />
              </InlineRowSection>
            )}
            </Fragment>
          ))}
        </tbody>
      </table>
    </div>
      )}
    </div>
  )
}

// ─── Link Suite Modal ─────────────────────────────────────────────────────────

interface LinkSuiteModalProps {
  planId: string
  projectId: string | null
  onClose: () => void
  onLinked: () => void
}

function LinkSuiteModal({ planId, projectId, onClose, onLinked }: LinkSuiteModalProps) {
  const [suites, setSuites] = useState<Array<{ suite_name: string; test_count: number }>>([])
  const [selectedSuite, setSelectedSuite] = useState('')
  const [linking, setLinking] = useState(false)
  const [loadingSuites, setLoadingSuites] = useState(true)
  // W28 TCM-02: plans pick Active cases (approved + active, the AI plan's rule) by default.
  const [includeNotActive, setIncludeNotActive] = useState(false)

  useEffect(() => {
    testManagementService.listSuites(projectId)
      .then(setSuites)
      .catch(() => toast.error('Could not load suites'))
      .finally(() => setLoadingSuites(false))
  }, [projectId])

  async function handleLink() {
    if (!selectedSuite) { toast.error('Select a suite'); return }
    setLinking(true)
    try {
      // Get all managed test cases from this suite. Bulk-link wants
      // the FULL list — request the server cap (500) and warn the user
      // if their suite has more cases than that fit on one page.
      const casesResp = await testManagementService.getSuiteCases(selectedSuite, projectId, { size: 500 })
      const cases = casesResp.items
      if (casesResp.total > casesResp.items.length) {
        toast('Note: this suite has more cases than the bulk-link cap (500). Some may need to be added manually.', { icon: '⚠' })
      }
      const managedCases = cases.filter(c => (c as unknown as { source?: string }).source === 'manual' || !('source' in c))
      if (managedCases.length === 0) {
        toast.error('No managed test cases found in this suite to link')
        return
      }
      // W28 TCM-02: Active (approved / active) cases by default; the box adds the other
      // non-retired ones; a deprecated / archived case is never linked; a row that does
      // not say its lifecycle state counts as not Active.
      const stateOf = (c: { lifecycle_status?: string | null }) => c.lifecycle_status ?? ''
      const retired = (c: { lifecycle_status?: string | null }) => (RETIRED_STATES as readonly string[]).includes(stateOf(c))
      const runnable = (c: { lifecycle_status?: string | null }) => (RUNNABLE_STATES as readonly string[]).includes(stateOf(c))
      const notActive = managedCases.filter(c => !retired(c) && !runnable(c))
      const toLink = managedCases.filter(c => !retired(c) && (includeNotActive || runnable(c)))
      const leftOut = includeNotActive ? 0 : notActive.length
      if (toLink.length === 0) {
        toast.error(leftOut > 0
          ? `No Active test cases in "${selectedSuite}". Tick "Include cases that are not Active" to link the others.`
          : 'No managed test cases found in this suite to link')
        return
      }
      let added = 0
      for (const tc of toLink) {
        try {
          await testManagementService.addPlanItem(planId, { test_case_id: tc.id })
          added++
        } catch {
          // skip duplicates
        }
      }
      toast.success(`Linked ${added} test case${added !== 1 ? 's' : ''} from "${selectedSuite}"${leftOut > 0 ? ` · ${leftOut} not Active left out` : ''}`)
      onLinked()
    } catch {
      toast.error('Failed to link suite')
    } finally {
      setLinking(false)
    }
  }

  // In the page (W27b PAN-05), between the plan's button row and its items; no X (it had none).
  return (
    <InlineFormSection
      title="Link Test Suite to Plan"
      titleId="tm-link-suite-title"
      description="Add a suite's Active managed test cases to this test plan (the others when you tick the box)."
      onClose={onClose}
      showClose={false}
      footer={(
        <>
          <button onClick={onClose} className="px-4 py-2 text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)]">Cancel</button>
          <button
            onClick={handleLink}
            disabled={linking || !selectedSuite}
            className="btn-primary flex items-center gap-2"
          >
            {linking ? <LoadingSpinner size="sm" /> : <Layers className="h-4 w-4" />}
            {linking ? 'Linking…' : 'Link Suite'}
          </button>
        </>
      )}
    >
      {loadingSuites ? (
        <div className="flex justify-center py-4"><LoadingSpinner size="md" /></div>
      ) : (
        <div className="max-w-md space-y-3">
          <select
            value={selectedSuite}
            onChange={e => setSelectedSuite(e.target.value)}
            className="w-full bg-[var(--color-bg-secondary)] border border-[var(--color-border-light)] rounded-lg px-3 py-2 text-sm text-[var(--color-text)] focus:outline-none focus:border-[var(--color-border)]"
          >
            <option value="">— Select a suite —</option>
            {suites.map(s => (
              <option key={s.suite_name} value={s.suite_name}>
                {s.suite_name} ({s.test_count} cases)
              </option>
            ))}
          </select>
          <label className="flex items-center gap-2 text-sm text-[var(--color-text-secondary)]">
            <input
              type="checkbox"
              checked={includeNotActive}
              onChange={(e) => setIncludeNotActive(e.target.checked)}
              className="accent-[var(--color-accent)]"
            />
            Include cases that are not Active
          </label>
        </div>
      )}
    </InlineFormSection>
  )
}

// ─── Tab: Test Plans ──────────────────────────────────────────────────────────

interface TestPlansTabProps { projectId: string | null }

function TestPlansTab({ projectId }: TestPlansTabProps) {
  const [page, setPage] = useState(1)
  const [showCreate, setShowCreate] = useState(false)
  const [expandedPlan, setExpandedPlan] = useState<string | null>(null)
  const [aiLoading, setAiLoading] = useState(false)
  const [downloadingPlan, setDownloadingPlan] = useState<{ id: string; format: 'word' | 'pdf' } | null>(null)

  const { data, isLoading, mutate } = useTestPlans({ page, size: 10 })

  const handleAiCreatePlan = async () => {
    setAiLoading(true)
    try {
      await testManagementService.aiCreatePlanAsync({ project_id: projectId ?? '' })
      toast.success(
        'AI is building the test plan in the background — refresh the list in a moment.',
        { duration: 7000 }
      )
    } catch {
      toast.error('AI plan creation failed')
    } finally {
      setAiLoading(false)
    }
  }

  const plans = data?.items ?? []

  const handlePlanDownload = async (planId: string, format: 'word' | 'pdf') => {
    setDownloadingPlan({ id: planId, format })
    try {
      if (format === 'word') {
        await testManagementService.downloadPlanWord(planId)
      } else {
        await testManagementService.downloadPlanPdf(planId)
      }
    } catch {
      toast.error(`Failed to export plan as ${format.toUpperCase()}`)
    } finally {
      setDownloadingPlan(null)
    }
  }

  const PlanProgressBar = ({ plan }: { plan: TestPlan }) => {
    const total = plan.total_cases || 1
    const passedPct = (plan.passed_cases / total) * 100
    const failedPct = (plan.failed_cases / total) * 100
    const blockedPct = (plan.blocked_cases / total) * 100
    const notRunPct = ((total - plan.executed_cases) / total) * 100
    return (
      <div className="w-full h-2 bg-[var(--color-bg-hover)] rounded-full overflow-hidden flex">
        <div className="h-full bg-[var(--status-passed)]" style={{ width: `${passedPct}%` }} title={`Passed: ${plan.passed_cases}`} />
        <div className="h-full bg-[var(--status-failed)]" style={{ width: `${failedPct}%` }} title={`Failed: ${plan.failed_cases}`} />
        <div className="h-full bg-[var(--status-broken)]" style={{ width: `${blockedPct}%` }} title={`Blocked: ${plan.blocked_cases}`} />
        <div className="h-full bg-[var(--color-bg-card)]" style={{ width: `${notRunPct}%` }} title="Not run" />
      </div>
    )
  }

  return (
    <>
      <div className="space-y-4">
        {projectId && (
          <div className="flex items-center justify-end gap-3">
            <button
              onClick={handleAiCreatePlan}
              disabled={aiLoading}
              className="btn-secondary flex items-center gap-2"
            >
              {aiLoading ? <LoadingSpinner size="sm" /> : <Sparkles className="h-4 w-4" />}
              AI Create Plan
            </button>
            <button onClick={() => setShowCreate(true)} className="btn-primary flex items-center gap-2">
              <Plus className="h-4 w-4" /> New Plan
            </button>
          </div>
        )}

        {/* New Plan (the toolbar and the empty state): in the page, under the toolbar row (W27b PAN-05). */}
        {showCreate && projectId && (
          <CreatePlanModal
            projectId={projectId}
            onClose={() => setShowCreate(false)}
            onCreated={mutate}
          />
        )}

        {isLoading ? (
          <div className="flex items-center justify-center h-48"><LoadingSpinner size="lg" /></div>
        ) : plans.length === 0 ? (
          <EmptyState
            icon={<BookOpen className="h-10 w-10" />}
            title="No test plans"
            description={projectId ? "Create a test plan to organize and track test execution" : "No test plans found across all projects"}
            action={projectId ? (
              <button onClick={() => setShowCreate(true)} className="btn-primary flex items-center gap-2">
                <Plus className="h-4 w-4" /> New Plan
              </button>
            ) : undefined}
          />
        ) : (
          <div className="space-y-3">
            {plans.map(plan => (
              <div key={plan.id} className="card p-0">
                <div
                  className="p-4 cursor-pointer hover:bg-[var(--color-bg-hover)]/50 transition-colors rounded-xl"
                  onClick={() => setExpandedPlan(expandedPlan === plan.id ? null : plan.id)}
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-3 mb-1">
                        <h3 className="text-sm font-semibold text-[var(--color-text)] truncate">{plan.name}</h3>
                        {/* Status selector — click stops propagation so it doesn't toggle expand */}
                        <div onClick={e => e.stopPropagation()}>
                          <select
                            value={plan.status}
                            onChange={async e => {
                              try {
                                await testManagementService.updatePlan(plan.id, { status: e.target.value })
                                await mutate()
                                toast.success('Status updated')
                              } catch {
                                toast.error('Failed to update status')
                              }
                            }}
                            className={clsx(
                              'text-xs font-medium rounded-full px-2 py-0.5 border cursor-pointer focus:outline-none focus:ring-1 focus:ring-[var(--color-ring)]',
                              PLAN_STATUS_COLORS[plan.status] ?? 'bg-[var(--color-bg-hover)] text-[var(--color-text-secondary)] border-[var(--color-border-light)]',
                            )}
                          >
                            {(['draft', 'active', 'in_progress', 'completed', 'archived'] as const).map(s => (
                              <option key={s} value={s} className="bg-[var(--color-bg-card)] text-[var(--color-text)]">
                                {s.replace(/_/g, ' ')}
                              </option>
                            ))}
                          </select>
                        </div>
                        {plan.ai_generated && <Sparkles className="h-3.5 w-3.5 text-[var(--color-purple)]" aria-label="AI generated" />}
                      </div>
                      {plan.description && <p className="text-xs text-[var(--color-text-muted)] mb-2 truncate">{plan.description}</p>}
                      <div className="space-y-1">
                        <PlanProgressBar plan={plan} />
                        <div className="flex items-center gap-4 text-xs text-[var(--color-text-muted)]">
                          <span className="text-[var(--status-passed)]">{plan.passed_cases} passed</span>
                          <span className="text-[var(--status-failed)]">{plan.failed_cases} failed</span>
                          <span className="text-[var(--status-broken)]">{plan.blocked_cases} blocked</span>
                          <span>{plan.total_cases - plan.executed_cases} not run</span>
                          <span className="ml-auto">{plan.executed_cases}/{plan.total_cases} executed</span>
                        </div>
                      </div>
                    </div>
                    <div className="flex items-center gap-3 flex-shrink-0">
                      <div className="text-right text-xs text-[var(--color-text-muted)]">
                        {plan.planned_start_date && <p>Start: {fmtDate(plan.planned_start_date)}</p>}
                        {plan.planned_end_date && <p>End: {fmtDate(plan.planned_end_date)}</p>}
                      </div>
                      {expandedPlan === plan.id
                        ? <ChevronUp className="h-4 w-4 text-[var(--color-text-muted)]" />
                        : <ChevronDown className="h-4 w-4 text-[var(--color-text-muted)]" />
                      }
                    </div>
                  </div>
                </div>
                {expandedPlan === plan.id && (
                  <div className="border-t border-[var(--color-border)] p-4 space-y-3">
                    <div className="flex items-center gap-2 justify-end">
                      <button
                        onClick={() => handlePlanDownload(plan.id, 'word')}
                        disabled={downloadingPlan !== null}
                        className="btn-secondary flex items-center gap-1.5 text-xs whitespace-nowrap"
                        title="Export plan to Word"
                      >
                        <Download className="h-3.5 w-3.5" />
                        {downloadingPlan?.id === plan.id && downloadingPlan.format === 'word' ? 'Exporting…' : 'Word'}
                      </button>
                      <button
                        onClick={() => handlePlanDownload(plan.id, 'pdf')}
                        disabled={downloadingPlan !== null}
                        className="btn-secondary flex items-center gap-1.5 text-xs whitespace-nowrap"
                        title="Export plan to PDF"
                      >
                        <FileText className="h-3.5 w-3.5" />
                        {downloadingPlan?.id === plan.id && downloadingPlan.format === 'pdf' ? 'Exporting…' : 'PDF'}
                      </button>
                    </div>
                    <PlanItemsView planId={plan.id} onMutate={mutate} projectId={projectId} />
                  </div>
                )}
              </div>
            ))}
            {data && <Pagination page={data.page} pages={data.pages} total={data.total} onChange={setPage} />}
          </div>
        )}
      </div>
    </>
  )
}

// ─── Tab: Test Suites ─────────────────────────────────────────────────────────

interface SuiteItem {
  suite_name: string
  test_count: number
  passed_count: number
  failed_count: number
  last_run_at: string | null
  last_run_id: string | null
  pass_rate: number | null
  // Cumulative aggregates surfaced as the new "# Runs / Pass / Fail /
  // Skip" cells. Optional so older API responses without these fields
  // render zero rather than NaN.
  run_count?: number
  total_executions?: number
  total_passed?: number
  total_failed?: number
  total_skipped?: number
  total_broken?: number
  owner_user_id?: string | null
  owner_email?: string | null
  owner_full_name?: string | null
  owner_is_fallback?: boolean
  // W13 TCL-07: a first-class suite no result reports yet (one project, no
  // release filter). Absent on every aggregated row: absent = an aggregate.
  empty?: boolean
  suite_id?: string | null
}

/** The line an empty suite's card shows in place of its counts (TCL-07). */
const EMPTY_SUITE_LINE ='Waiting for its first run: no result reports this suite yet.'

interface SuiteCase {
  id: string
  test_name: string
  suite_name: string
  status: string
  duration_ms: number | null
  class_name: string | null
  package_name: string | null
  /** TestRun.id of the latest execution. Present on automation rows;
   *  manual managed cases don't carry one. Used to deep-link the
   *  test-name cell to ``/runs/<run_id>/tests/<id>``. */
  test_run_id?: string | null
  created_at: string | null
  execution_count?: number
  last_execution_at?: string | null
  source?: 'automation' | 'manual'
}

interface TestSuitesTabProps { projectId: string | null }

const REVIEW_STATE_STYLES: Record<SuiteReviewState, { label: string; cls: string }> = {
  pending:       { label: 'Pending review',  cls: 'bg-[var(--status-broken-bg)] text-[var(--status-broken)]' },
  confirmed:     { label: 'Confirmed',        cls: 'bg-[var(--status-passed-bg)] text-[var(--status-passed)]' },
  acknowledged:  { label: 'Acknowledged',     cls: 'bg-[var(--color-accent-bg-soft)] text-[var(--color-accent)]' },
  review_later:  { label: 'Review later',     cls: 'bg-[var(--status-flaky-bg)]/30 text-[var(--status-flaky)]' },
}

function TestSuitesTab({ projectId }: TestSuitesTabProps) {
  const { isQaLead } = usePermissions()
  const { data: users } = useUsers()
  // Memoize so the array identity is stable across renders — the
  // `ownerCandidates` useMemo below keys on it, and a fresh `users ?? []`
  // literal each render would defeat that memo (exhaustive-deps).
  const userList = useMemo(() => (users ?? []) as UserSummary[], [users])

  // Suite-owner candidates must match the backend rule in
  // ``assert_user_is_qa_lead_on_project``: a user is eligible if they're an
  // instance ADMIN OR have ``ProjectMember.role=QA_LEAD`` on this project.
  // Surfacing anyone else in the picker just produces 400s. ``UserSummary``
  // doesn't carry the global role, so we cross-reference with the project
  // members API (which gives the per-project role) when a project is set.
  // Project members via SWR (keyed on the project) rather than a load-on-change
  // effect — feeds the QA_LEAD owner-candidate list below.
  const { data: projectMembersData } = useProjectMembers(projectId || null)
  const projectMembers = useMemo(() => projectMembersData ?? [], [projectMembersData])
  const ownerCandidates: UserSummary[] = useMemo(() => {
    const qaLeadIds = new Set(
      projectMembers.filter(m => m.role === 'QA_LEAD').map(m => m.user_id),
    )
    // Map QA_LEAD project members straight onto UserSummary shape. Falling
    // back to the project-member row's own fields means the picker still
    // works even if the global users list hasn't loaded.
    const byId = new Map(userList.map(u => [u.id, u]))
    const qaLeads: UserSummary[] = projectMembers
      .filter(m => qaLeadIds.has(m.user_id))
      .map(m => byId.get(m.user_id) ?? {
        id: m.user_id,
        username: m.username,
        full_name: m.full_name ?? undefined,
        email: m.email,
      })
    return qaLeads.sort((a, b) =>
      (a.full_name || a.username).localeCompare(b.full_name || b.username),
    )
  }, [projectMembers, userList])
  const [searchParams] = useSearchParams()
  const deepLinkSuite = searchParams.get('suite')
  // Suites via SWR (keyed on the project) rather than a load-on-change effect;
  // local edits below patch the cache via mutate.
  const {
    data: suitesData,
    isLoading: loading,
    error: suitesError,
    mutate: mutateSuites,
  } = useSWR(['test-suites', projectId], () => testManagementService.listSuites(projectId))
  const suites = useMemo(() => suitesData ?? [], [suitesData])
  useEffect(() => {
    if (suitesError) toast.error('Could not load test suites')
  }, [suitesError])
  // The list above is aggregated by suite NAME; the suite page is keyed by
  // the suite record's id, so names are looked up in the project's suites.
  const { data: suiteRecords } = useSuites()
  const suiteIdByName = useMemo(
    () => new Map((suiteRecords?.items ?? []).map(s => [s.name, s.id] as const)),
    [suiteRecords],
  )
  const [expandedSuite, setExpandedSuite] = useState<string | null>(deepLinkSuite)
  const [suiteCases, setSuiteCases] = useState<Record<string, SuiteCase[]>>({})
  // Per-suite pagination state for the cases inline-expand. Keyed by
  // suite_name so each open suite tracks its own page independently.
  const [suiteCasesPage, setSuiteCasesPage] = useState<Record<string, number>>({})
  const [suiteCasesPages, setSuiteCasesPages] = useState<Record<string, number>>({})
  const [suiteCasesTotal, setSuiteCasesTotal] = useState<Record<string, number>>({})
  const SUITE_CASES_PAGE_SIZE = 25
  const [loadingCases, setLoadingCases] = useState<string | null>(null)
  const [suiteDeleted, setSuiteDeleted] = useState<Record<string, Array<{ id: string; test_name: string; class_name: string | null; review_tag: string | null; deleted_at_run_id: string | null }>>>({})
  const [suiteChanges, setSuiteChanges] = useState<Record<string, Array<{ event_type: string; test_name: string; details: string | null }>>>({})
  const [reviewsByRun, setReviewsByRun] = useState<Record<string, SuiteReviewItem[]>>({})
  const [editingOwnerFor, setEditingOwnerFor] = useState<string | null>(null)
  const [savingReview, setSavingReview] = useState<string | null>(null)
  const deepLinkAppliedRef = useRef(false)
  // "Add test suite" modal state. Opens when isQaEngineer+ user clicks the
  // header button. Persists name/description/owner during edit so an
  // accidental close-and-reopen doesn't lose the user's typing — we reset
  // on a successful create.
  const [showAddSuite, setShowAddSuite] = useState(false)

  // When arriving via /test-management?tab=Test+Suites&suite=<name>, auto-load
  // the deep-linked suite's cases and scroll its card into view once. Subsequent
  // suite changes from the URL also re-apply; user-initiated collapses don't
  // re-trigger because we gate on the ref + suiteCases cache.
  useEffect(() => {
    if (!deepLinkSuite || deepLinkAppliedRef.current) return
    if (suites.length === 0) return
    deepLinkAppliedRef.current = true
    // Coordinated one-time deep-link side effect: expand the card, load its
    // cases, and scroll it into view. The scrollIntoView requires the rendered
    // DOM, so this legitimately belongs in an effect — the expand setState is
    // an intentional part of that one-shot, not a derive-during-render case.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setExpandedSuite(deepLinkSuite)
    // An empty suite (TCL-07) has no cases to load.
    const linked: SuiteItem | undefined = suites.find(s => s.suite_name === deepLinkSuite)
    if (!linked?.empty) void loadSuiteData(deepLinkSuite)
    const el = document.getElementById(`suite-card-${deepLinkSuite}`)
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [deepLinkSuite, suites])

  // Bulk-fetch reviews for every suite's latest run, keyed by run id so a
  // single test_run that hosts multiple suites only triggers one request.
  useEffect(() => {
    const runIds = Array.from(new Set(suites.map(s => s.last_run_id).filter((x): x is string => !!x)))
    if (runIds.length === 0) return
    let cancelled = false
    Promise.all(
      runIds.map(rid =>
        testManagementService.listReviewsForRun(rid)
          .then(items => [rid, items] as const)
          .catch(() => [rid, [] as SuiteReviewItem[]] as const),
      ),
    ).then(entries => {
      if (cancelled) return
      const map: Record<string, SuiteReviewItem[]> = {}
      for (const [rid, items] of entries) map[rid] = items
      setReviewsByRun(map)
    })
    return () => { cancelled = true }
  }, [suites])

  function reviewForSuite(suite: SuiteItem): SuiteReviewItem | undefined {
    if (!suite.last_run_id) return undefined
    return reviewsByRun[suite.last_run_id]?.find(r => r.suite_name === suite.suite_name)
  }

  async function handleAssignOwner(suite: SuiteItem, ownerUserId: string | null) {
    if (!projectId) {
      toast.error('Select a project to assign suite owners')
      return
    }
    try {
      const updated = await testManagementService.setSuiteOwner(suite.suite_name, projectId, ownerUserId)
      mutateSuites(prev => (prev ?? []).map(s => s.suite_name === suite.suite_name ? {
        ...s,
        owner_user_id: updated.owner_user_id,
        owner_email: updated.owner_email,
        owner_full_name: updated.owner_full_name,
        owner_is_fallback: updated.is_fallback,
      } : s), { revalidate: false })
      setEditingOwnerFor(null)
      toast.success(ownerUserId ? 'Suite owner assigned' : 'Suite owner cleared')
    } catch (err: unknown) {
      toast.error((err as Error).message || 'Failed to assign owner')
    }
  }

  async function handleReview(suite: SuiteItem, state: SuiteReviewState) {
    const runId = suite.last_run_id
    if (!runId) {
      toast.error('No automation runs to review yet')
      return
    }
    setSavingReview(suite.suite_name)
    try {
      const updated = await testManagementService.upsertSuiteReview(runId, suite.suite_name, state)
      setReviewsByRun(prev => {
        const list = prev[runId] ?? []
        const filtered = list.filter(r => r.suite_name !== suite.suite_name)
        return { ...prev, [runId]: [...filtered, updated] }
      })
      toast.success(`Marked ${REVIEW_STATE_STYLES[state].label.toLowerCase()}`)
    } catch (err: unknown) {
      toast.error((err as Error).message || 'Failed to save review')
    } finally {
      setSavingReview(null)
    }
  }

  async function loadSuiteData(suiteName: string, page: number = 1) {
    // Page-1 only seeds deleted + changes (those don't paginate). Later
    // pages skip the side queries — we already have them cached.
    const seedSideData = page === 1 && !suiteCases[suiteName]
    setLoadingCases(suiteName)
    try {
      if (seedSideData) {
        const [casesResp, deleted, changes] = await Promise.all([
          testManagementService.getSuiteCases(suiteName, projectId, { page, size: SUITE_CASES_PAGE_SIZE }),
          testManagementService.getSuiteDeleted(suiteName, projectId).catch(() => []),
          testManagementService.getSuiteChanges(suiteName, projectId).catch(() => []),
        ])
        setSuiteCases(prev => ({ ...prev, [suiteName]: casesResp.items }))
        setSuiteCasesPage(prev => ({ ...prev, [suiteName]: casesResp.page }))
        setSuiteCasesPages(prev => ({ ...prev, [suiteName]: casesResp.pages }))
        setSuiteCasesTotal(prev => ({ ...prev, [suiteName]: casesResp.total }))
        setSuiteDeleted(prev => ({ ...prev, [suiteName]: deleted }))
        setSuiteChanges(prev => ({ ...prev, [suiteName]: changes }))
      } else {
        // Subsequent page changes — just refresh the cases slice.
        const casesResp = await testManagementService.getSuiteCases(
          suiteName, projectId, { page, size: SUITE_CASES_PAGE_SIZE },
        )
        setSuiteCases(prev => ({ ...prev, [suiteName]: casesResp.items }))
        setSuiteCasesPage(prev => ({ ...prev, [suiteName]: casesResp.page }))
        setSuiteCasesPages(prev => ({ ...prev, [suiteName]: casesResp.pages }))
        setSuiteCasesTotal(prev => ({ ...prev, [suiteName]: casesResp.total }))
      }
    } catch {
      toast.error('Could not load suite cases')
    } finally {
      setLoadingCases(null)
    }
  }

  async function handleExpandSuite(suiteName: string) {
    if (expandedSuite === suiteName) {
      setExpandedSuite(null)
      return
    }
    setExpandedSuite(suiteName)
    // Skip the network call when this suite's first page is already
    // cached — pagination only re-fetches when the user changes pages
    // via ``handleSuiteCasesPageChange``.
    if (!suiteCases[suiteName]) {
      await loadSuiteData(suiteName, 1)
    }
  }

  async function handleSuiteCasesPageChange(suiteName: string, page: number) {
    await loadSuiteData(suiteName, page)
  }

  const CASE_STATUS_COLORS: Record<string, string> = {
    passed:  'text-[var(--status-passed)]',
    failed:  'text-[var(--status-failed)]',
    error:   'text-[var(--status-failed)]',
    // W29a UIX-03: a skipped RESULT is the skipped hue (was muted grey).
    skipped: 'text-[var(--status-skipped)]',
    pending: 'text-[var(--status-broken)]',
  }

  // Add-suite handler. Refreshes the list on success so the user sees their
  // new suite immediately: since W13 TCL-07 the aggregated listSuites answer
  // also carries a first-class suite no result reports yet, as an `empty`
  // row (one project, no release filter), rendered as the Empty card below.
  async function handleCreateSuite(payload: {
    name: string; description: string; owner_user_id: string | null; tags: string[]
  }) {
    if (!projectId) throw new Error('No active project')
    const { suitesService } = await import('@/services/suitesService')
    await suitesService.create({
      project_id: projectId,
      name: payload.name,
      description: payload.description || null,
      owner_user_id: payload.owner_user_id,
      tags: payload.tags.length > 0 ? payload.tags : null,
    })
    // Refetch suites so the new one shows up at once (as an Empty card).
    await mutateSuites()
  }

  if (loading) return <div className="flex items-center justify-center h-48"><LoadingSpinner size="lg" /></div>

  // Header bar with the "Add test suite" affordance. Lives above both the
  // empty-state and the populated list so a fresh project can still author
  // a suite before any ingest has happened.
  const suitesHeader = (
    <div className="flex items-center justify-between gap-3 mb-3">
      <div className="text-[12.5px] text-[var(--color-text-muted)]">
        {suites.length === 0 ? 'No test suites yet' : `${suites.length} suite${suites.length === 1 ? '' : 's'}`}
        {projectId ? null : ' · pick a project to add a new suite'}
      </div>
      {isQaLead && projectId && (
        <button
          type="button"
          onClick={() => setShowAddSuite(true)}
          className="btn-primary btn-sm inline-flex items-center gap-1.5"
        >
          <Plus className="h-3.5 w-3.5" />
          Add test suite
        </button>
      )}
    </div>
  )

  // "Add test suite" opens in the page (W27b PAN-05), right under the header
  // row, in either state of the tab: one form, defined once.
  const addSuiteForm = showAddSuite && projectId ? (
    <AddTestSuiteModal
      projectId={projectId}
      ownerCandidates={ownerCandidates}
      existingNames={new Set(suites.map(s => s.suite_name))}
      onClose={() => setShowAddSuite(false)}
      onCreate={handleCreateSuite}
    />
  ) : null

  if (suites.length === 0) {
    return (
      <>
        {suitesHeader}
        {addSuiteForm}
        <EmptyState
          icon={<Layers className="h-10 w-10" />}
          title="No test suites found"
          description={projectId ? 'Test suites appear here when automation runs ingest test results grouped by suite, or when manual test cases are assigned a suite name.' : 'No test suites found across all projects'}
        />
      </>
    )
  }

  return (
    <>
      {suitesHeader}
      {addSuiteForm}
    <div className="space-y-3">
      {suites.map(suite => {
        const review = reviewForSuite(suite)
        const reviewState: SuiteReviewState = review?.state ?? 'pending'
        const reviewStyle = REVIEW_STATE_STYLES[reviewState]
        const isEditingOwner = editingOwnerFor === suite.suite_name
        // TCL-07: a first-class suite no result reports yet. Nothing to
        // count, chart or expand: its badge, the waiting line and its owner.
        const asItem: SuiteItem = suite
        const isEmpty = asItem.empty === true
        const suitePageId = asItem.suite_id || suiteIdByName.get(suite.suite_name)
        return (
        <div
          key={suite.suite_name}
          id={`suite-card-${suite.suite_name}`}
          data-suite-empty={isEmpty ? 'true' : undefined}
          className={clsx(
            'card p-0',
            deepLinkSuite === suite.suite_name && 'ring-2 ring-[var(--color-accent)] ring-offset-1 ring-offset-[var(--color-bg)]',
          )}
        >
          <div
            className={clsx(
              'p-4 rounded-xl',
              !isEmpty && 'cursor-pointer hover:bg-[var(--color-bg-hover)]/50 transition-colors',
            )}
            onClick={isEmpty ? undefined : () => handleExpandSuite(suite.suite_name)}
          >
            <div className="flex items-center justify-between gap-3">
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-3 mb-1">
                  <Layers className="h-4 w-4 text-[var(--color-text)] flex-shrink-0" />
                  <h3 className="text-sm font-semibold text-[var(--color-text)] truncate">
                    {/* The suite's own page (UX redesign P4): its tests, runs
                        and charts. A name with no suite record (aggregated
                        from runs only) has no page, so it stays text; the
                        row still expands its cases here. */}
                    {suitePageId ? (
                      <Link
                        to={`/suites/${encodeURIComponent(suitePageId)}`}
                        className="hover:text-[var(--color-accent)] hover:underline"
                        onClick={e => e.stopPropagation()}
                      >
                        {suite.suite_name}
                      </Link>
                    ) : suite.suite_name}
                  </h3>
                  {isEmpty ? (
                    <span className="text-xs px-1.5 py-0.5 rounded font-medium bg-[var(--ui-info-bg)] text-[var(--ui-info-ink)]">
                      Empty
                    </span>
                  ) : (
                  <span className={clsx('text-[10px] px-1.5 py-0.5 rounded font-medium', reviewStyle.cls)}>
                    {reviewStyle.label}
                  </span>
                  )}
                </div>
                {isEmpty && (
                  <p className="text-xs text-[var(--color-text-muted)] mb-1">{EMPTY_SUITE_LINE}</p>
                )}
                <div className="flex items-center gap-4 text-xs text-[var(--color-text-muted)] flex-wrap">
                {!isEmpty && (() => {
                  // Headline counts. The snapshot columns
                  // (``test_count``/``passed_count``/``failed_count``) are
                  // DISTINCT-fingerprint with the *latest* status, which
                  // collapses a parametrised test run 139× into "1 test,
                  // last passed" and hides every failure (a suite with 12
                  // real failures rendered "0 failed, 100% pass rate").
                  // When cumulative run history exists, show the
                  // execution-based totals instead — they match /runs and
                  // /live (what users compare against) and never hide
                  // failures. Unique-test count is surfaced as a secondary
                  // annotation so the "test catalog" size is still visible.
                  const hasExecHistory =
                    (suite.run_count ?? 0) > 0 && (suite.total_executions ?? 0) > 0
                  const displayTotal = hasExecHistory
                    ? (suite.total_executions ?? 0)
                    : suite.test_count
                  const displayPassed = hasExecHistory
                    ? (suite.total_passed ?? 0)
                    : suite.passed_count
                  const displayFailed = hasExecHistory
                    ? (suite.total_failed ?? 0)
                    : suite.failed_count
                  const evaluated = displayPassed + displayFailed + (suite.total_broken ?? 0)
                  const displayPassRate = hasExecHistory
                    ? (evaluated > 0 ? (displayPassed / evaluated) * 100 : null)
                    : suite.pass_rate
                  const uniqueCount = suite.test_count
                  return (
                    <>
                      <span>
                        {displayTotal} {hasExecHistory ? 'executions' : 'tests'}
                        {hasExecHistory && uniqueCount > 0 && uniqueCount !== displayTotal && (
                          <span className="text-[var(--color-text-faint)]"> · {uniqueCount} unique</span>
                        )}
                      </span>
                      <span className="text-[var(--status-passed)]">{displayPassed} passed</span>
                      <span className="text-[var(--status-failed)]">{displayFailed} failed</span>
                      {displayPassRate != null && (
                        <span className={displayPassRate >= 80 ? 'text-[var(--status-passed)] font-medium' : displayPassRate >= 60 ? 'text-[var(--status-broken)] font-medium' : 'text-[var(--status-failed)] font-medium'}>
                          {displayPassRate.toFixed(1)}% pass rate
                        </span>
                      )}
                    </>
                  )
                })()}
                  {/* Cumulative run history — total runs that included this
                      suite plus per-status totals across those runs. Skipped
                      when run_count is zero so the row stays compact for
                      manual-only suites. */}
                  {(suite.run_count ?? 0) > 0 && (
                    <span
                      className="text-[var(--color-text-faint)]"
                      title={`${suite.run_count} runs · ${suite.total_executions ?? 0} executions`}
                    >
                      {suite.run_count} run{suite.run_count === 1 ? '' : 's'}
                      {(suite.total_skipped ?? 0) > 0 && (
                        <span className="text-[var(--status-skipped)]/80 ml-2">{suite.total_skipped} skipped</span>
                      )}
                      {(suite.total_broken ?? 0) > 0 && (
                        <span className="text-[var(--status-broken)]/80 ml-2">{suite.total_broken} broken</span>
                      )}
                    </span>
                  )}
                  {/* The per-day trend is the suite page's Charts tab (P4:
                      `/coverage/suite?name=` only redirects there now). */}
                  {!isEmpty && suiteIdByName.get(suite.suite_name) && (
                    <Link
                      to={`/suites/${suiteIdByName.get(suite.suite_name)}?tab=charts`}
                      className="text-[var(--color-accent)] hover:underline text-[11px]"
                      onClick={e => e.stopPropagation()}
                      title="View per-day trend"
                    >
                      Trend →
                    </Link>
                  )}
                  <span className="flex items-center gap-1" onClick={e => e.stopPropagation()}>
                    <User className="h-3 w-3" />
                    {isEditingOwner ? (
                      ownerCandidates.length === 0 ? (
                        <span
                          className="text-[10.5px] italic text-[var(--status-broken)]"
                          title="Add a project member with role QA_LEAD before assigning."
                        >
                          No QA_LEAD members on this project
                        </span>
                      ) : (
                        <select
                          autoFocus
                          className="input h-6 py-0 text-[11px]"
                          defaultValue={suite.owner_user_id ?? ''}
                          onChange={e => handleAssignOwner(suite, e.target.value || null)}
                          onBlur={() => setEditingOwnerFor(null)}
                        >
                          <option value="">— Unassigned (use default) —</option>
                          {ownerCandidates.map(u => (
                            <option key={u.id} value={u.id}>{u.full_name || u.username} ({u.email})</option>
                          ))}
                        </select>
                      )
                    ) : (
                      <>
                        <span className={suite.owner_is_fallback ? 'italic text-[var(--color-text-faint)]' : ''}>
                          {suite.owner_full_name || suite.owner_email || 'Unassigned'}
                          {suite.owner_is_fallback && ' (default)'}
                        </span>
                        {isQaLead && (
                          <button
                            type="button"
                            onClick={() => setEditingOwnerFor(suite.suite_name)}
                            className="text-[var(--color-text-faint)] hover:text-[var(--color-text-muted)] underline text-[11px]"
                          >
                            change
                          </button>
                        )}
                      </>
                    )}
                  </span>
                  {suite.last_run_at && <span className="ml-auto">Last run: {fmtDate(suite.last_run_at)}</span>}
                </div>
                {!isEmpty && suite.last_run_id && (
                  <div className="mt-2 flex items-center gap-1.5" onClick={e => e.stopPropagation()}>
                    <span className="text-[10px] uppercase tracking-wider text-[var(--color-text-faint)] mr-1">AI verdict:</span>
                    {(['confirmed', 'acknowledged', 'review_later'] as SuiteReviewState[]).map(s => (
                      <button
                        key={s}
                        type="button"
                        disabled={savingReview === suite.suite_name}
                        onClick={() => handleReview(suite, s)}
                        className={clsx(
                          'text-[11px] px-2 py-0.5 rounded font-medium transition-colors disabled:opacity-50',
                          reviewState === s
                            ? REVIEW_STATE_STYLES[s].cls
                            : 'bg-[var(--color-bg-secondary)] text-[var(--color-text-muted)] hover:text-[var(--color-text)]',
                        )}
                      >
                        {REVIEW_STATE_STYLES[s].label}
                      </button>
                    ))}
                    {review?.reviewer_email && (
                      <span className="text-[10px] text-[var(--color-text-faint)] ml-1">
                        by {review.reviewer_email}{review.reviewed_at ? ` · ${fmtDate(review.reviewed_at)}` : ''}
                      </span>
                    )}
                  </div>
                )}
              </div>
              {isEmpty ? null : expandedSuite === suite.suite_name
                ? <ChevronUp className="h-4 w-4 text-[var(--color-text-muted)] flex-shrink-0" />
                : <ChevronDown className="h-4 w-4 text-[var(--color-text-muted)] flex-shrink-0" />
              }
            </div>
          </div>
          {!isEmpty && expandedSuite === suite.suite_name && (
            <div className="border-t border-[var(--color-border)]">
              {loadingCases === suite.suite_name ? (
                <div className="flex items-center justify-center py-8"><LoadingSpinner size="md" /></div>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead className="bg-[var(--color-bg-secondary)]/80">
                      <tr>
                        <th className="px-4 py-2 text-left text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider">Test Name</th>
                        <th className="px-4 py-2 text-left text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider">Class / Package</th>
                        <th className="px-4 py-2 text-left text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider">Latest Status</th>
                        <th className="px-4 py-2 text-right text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider">Duration</th>
                        <th className="px-4 py-2 text-right text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider">Executions</th>
                        <th className="px-4 py-2 text-right text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider">Last Run</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-[var(--color-border)]/60">
                      {(suiteCases[suite.suite_name] ?? []).map(tc => (
                        <tr key={tc.id} className="hover:bg-[var(--color-bg-secondary)]/40 transition-colors">
                          <td className="px-4 py-2.5 text-[var(--color-text)] font-medium text-xs truncate max-w-xs">
                            {/* Automation rows link to the per-test
                                detail page; manual managed cases
                                aren't run-scoped so they stay plain
                                text (their canonical detail surface
                                is the Test Cases tab, not the per-run
                                drawer). */}
                            {tc.test_run_id ? (
                              <Link
                                to={`/runs/${tc.test_run_id}/tests/${tc.id}`}
                                className="hover:text-[var(--color-accent)] hover:underline"
                              >
                                {tc.test_name}
                              </Link>
                            ) : (
                              tc.test_name
                            )}
                          </td>
                          <td className="px-4 py-2.5 text-[var(--color-text-muted)] text-xs">
                            {tc.class_name && <span>{tc.class_name}</span>}
                            {tc.package_name && <span className="text-[var(--color-text-faint)]"> · {tc.package_name}</span>}
                            {!tc.class_name && !tc.package_name && <span className="text-[var(--color-text-faint)]">—</span>}
                          </td>
                          <td className="px-4 py-2.5">
                            <span className={clsx('text-xs font-medium capitalize', CASE_STATUS_COLORS[tc.status] ?? 'text-[var(--color-text-muted)]')}>
                              {tc.status}
                            </span>
                          </td>
                          <td className="px-4 py-2.5 text-right text-xs text-[var(--color-text-muted)] tabular-nums">
                            {tc.duration_ms != null ? `${(tc.duration_ms / 1000).toFixed(2)}s` : '—'}
                          </td>
                          <td className="px-4 py-2.5 text-right text-xs text-[var(--color-text)] tabular-nums">
                            {tc.execution_count ?? '—'}
                          </td>
                          <td className="px-4 py-2.5 text-right text-xs text-[var(--color-text-muted)]">
                            {tc.last_execution_at ? fmtDate(tc.last_execution_at) : tc.created_at ? fmtDate(tc.created_at) : '—'}
                          </td>
                        </tr>
                      ))}
                      {(suiteCases[suite.suite_name] ?? []).length === 0 && (
                        <tr>
                          <td colSpan={6} className="px-4 py-6 text-center text-xs text-[var(--color-text-muted)]">
                            {suite.test_count > 0 ? (
                              // Suite-card aggregate counted ``test_count``
                              // tests from a TestRun.total_tests fallback
                              // (HINCRBY counters at session close), but
                              // the per-test rows never persisted —
                              // typically because the live-stream SDK
                              // didn't send ``test_result`` events or
                              // the Redis buffer was already evicted by
                              // the time persist_live_session ran. The
                              // count is real; the per-test data isn't
                              // recoverable for this run.
                              <>
                                {suite.test_count} test{suite.test_count === 1 ? '' : 's'} reported by the run, but per-test rows are missing.
                                <br />
                                <span className="text-[var(--color-text-faint)]">
                                  This happens when the SDK doesn't emit
                                  ``test_result`` events or the buffer evicts
                                  before persistence. Re-run the suite to
                                  populate detail rows.
                                </span>
                              </>
                            ) : (
                              'No test cases in this suite'
                            )}
                          </td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                  {/* Pagination — visible only when there's more than
                      one page of cases. The total / range banner sits
                      below the controls for a quick "showing N of M"
                      confirmation. */}
                  {(suiteCasesPages[suite.suite_name] ?? 1) > 1 && (
                    <div className="px-4 py-2.5 border-t border-[var(--color-border)] flex items-center justify-between gap-2 flex-wrap">
                      <span className="text-[11px] text-[var(--color-text-muted)]">
                        Page {suiteCasesPage[suite.suite_name] ?? 1} of {suiteCasesPages[suite.suite_name] ?? 1}
                        {' · '}
                        {suiteCasesTotal[suite.suite_name] ?? 0} test case{(suiteCasesTotal[suite.suite_name] ?? 0) === 1 ? '' : 's'} total
                      </span>
                      <div className="flex items-center gap-1">
                        <button
                          type="button"
                          disabled={(suiteCasesPage[suite.suite_name] ?? 1) <= 1 || loadingCases === suite.suite_name}
                          onClick={() => handleSuiteCasesPageChange(suite.suite_name, (suiteCasesPage[suite.suite_name] ?? 1) - 1)}
                          className="text-[11px] px-2.5 py-1 rounded border border-[var(--color-border)] text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)] disabled:opacity-40"
                        >
                          Prev
                        </button>
                        <button
                          type="button"
                          disabled={
                            (suiteCasesPage[suite.suite_name] ?? 1) >= (suiteCasesPages[suite.suite_name] ?? 1)
                            || loadingCases === suite.suite_name
                          }
                          onClick={() => handleSuiteCasesPageChange(suite.suite_name, (suiteCasesPage[suite.suite_name] ?? 1) + 1)}
                          className="text-[11px] px-2.5 py-1 rounded border border-[var(--color-border)] text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)] disabled:opacity-40"
                        >
                          Next
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              )}
              {/* Recent Changes */}
              {(suiteChanges[suite.suite_name] ?? []).length > 0 && (
                <div className="border-t border-[var(--color-border)] px-4 py-3">
                  <p className="text-xs font-medium text-[var(--color-text-muted)] uppercase tracking-wider mb-2">Recent Changes</p>
                  <div className="space-y-1">
                    {(suiteChanges[suite.suite_name] ?? []).slice(0, 5).map((evt, i) => (
                      <div key={i} className="flex items-center gap-2 text-xs">
                        <span className={clsx('px-1.5 py-0.5 rounded font-medium',
                          evt.event_type === 'added' ? 'bg-[var(--status-passed-bg)] text-[var(--status-passed)]' :
                          evt.event_type === 'deleted' ? 'bg-[var(--status-failed-bg)] text-[var(--status-failed)]' :
                          evt.event_type === 'modified' ? 'bg-[var(--status-broken-bg)] text-[var(--status-broken)]' :
                          'bg-[var(--color-accent-bg-soft)] text-[var(--color-accent)]'
                        )}>{evt.event_type}</span>
                        <span className="text-[var(--color-text-secondary)] truncate">{evt.test_name}</span>
                        {evt.details && <span className="text-[var(--color-text-faint)] truncate ml-auto">{evt.details}</span>}
                      </div>
                    ))}
                  </div>
                </div>
              )}
              {/* Deleted Tests (needs_review) */}
              {(suiteDeleted[suite.suite_name] ?? []).length > 0 && (
                <div className="border-t border-[var(--status-failed-bd)] bg-[var(--status-failed-bg)] px-4 py-3">
                  <p className="text-xs font-medium text-[var(--status-failed)] uppercase tracking-wider mb-2">
                    Deleted from Suite ({(suiteDeleted[suite.suite_name] ?? []).length} tests need review)
                  </p>
                  <div className="space-y-1">
                    {(suiteDeleted[suite.suite_name] ?? []).map(d => (
                      <div key={d.id} className="flex items-center gap-2 text-xs">
                        <span className="text-[var(--status-failed)]">✕</span>
                        <span className="text-[var(--color-text-secondary)]">{d.test_name}</span>
                        {d.review_tag && (
                          <span className="bg-[var(--status-broken-bg)] text-[var(--status-broken)] px-1.5 py-0.5 rounded text-[10px] font-medium">{d.review_tag}</span>
                        )}
                        {d.class_name && <span className="text-[var(--color-text-faint)] ml-auto">{d.class_name}</span>}
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
        )
      })}
    </div>
    </>
  )
}

// ─── Add Test Suite modal ─────────────────────────────────────────────────────

interface AddTestSuiteModalProps {
  projectId: string
  ownerCandidates: UserSummary[]
  existingNames: Set<string>
  onClose: () => void
  onCreate: (payload: {
    name: string; description: string; owner_user_id: string | null; tags: string[]
  }) => Promise<void>
}

function AddTestSuiteModal({
  projectId: _projectId, ownerCandidates, existingNames, onClose, onCreate,
}: AddTestSuiteModalProps) {
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [ownerUserId, setOwnerUserId] = useState<string>('')
  const [tagsText, setTagsText] = useState('')
  const [saving, setSaving] = useState(false)

  const trimmedName = name.trim()
  const isDuplicate = trimmedName.length > 0 && existingNames.has(trimmedName)
  const canSubmit = !saving && trimmedName.length >= 2 && !isDuplicate

  async function handleSubmit() {
    if (!canSubmit) return
    setSaving(true)
    try {
      // Tags as comma-separated, trimmed, deduped, non-empty.
      const tags = Array.from(new Set(
        tagsText.split(',').map(t => t.trim()).filter(Boolean),
      ))
      await onCreate({
        name: trimmedName,
        description: description.trim(),
        owner_user_id: ownerUserId || null,
        tags,
      })
      toast.success(`Test suite "${trimmedName}" created`)
      onClose()
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      toast.error(msg || 'Failed to create test suite')
    } finally {
      setSaving(false)
    }
  }

  // In the page (W27b PAN-05), right under the Suites tab's header row; no X (it had none).
  return (
    <InlineFormSection
      title="Add test suite"
      titleId="tm-add-suite-title"
      description="Author a new suite definition. The suite name must be unique within the project."
      onClose={onClose}
      canClose={!saving}
      showClose={false}
      className="mb-3"
      footer={(
        <>
          <button
            type="button"
            onClick={onClose}
            disabled={saving}
            className="px-4 py-2 text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)] disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={handleSubmit}
            disabled={!canSubmit}
            className="btn-primary flex items-center gap-2"
          >
            {saving ? <LoadingSpinner size="sm" /> : <Plus className="h-4 w-4" />}
            {saving ? 'Creating…' : 'Create suite'}
          </button>
        </>
      )}
    >
        <div className="max-w-lg space-y-3">
          <div>
            <label htmlFor="tm-field-18" className="block text-xs text-[var(--color-text-muted)] mb-1">
              Suite name <span className="text-[var(--status-failed)]">*</span>
            </label>
            <input id="tm-field-18"
              type="text"
              value={name}
              onChange={e => setName(e.target.value)}
              placeholder="e.g. com.example.SmokeTests"
              className="w-full bg-[var(--color-bg-secondary)] border border-[var(--color-border-light)] rounded-lg px-3 py-2 text-sm text-[var(--color-text)] focus:outline-none focus:border-[var(--color-accent)]"
              autoFocus
              maxLength={500}
            />
            {isDuplicate && (
              <p className="text-[11px] text-[var(--status-failed)] mt-1">
                A suite named “{trimmedName}” already exists in this project.
              </p>
            )}
          </div>

          <div>
            <label htmlFor="tm-field-19" className="block text-xs text-[var(--color-text-muted)] mb-1">Description</label>
            <textarea id="tm-field-19"
              value={description}
              onChange={e => setDescription(e.target.value)}
              placeholder="Optional. What does this suite cover?"
              rows={3}
              className="w-full bg-[var(--color-bg-secondary)] border border-[var(--color-border-light)] rounded-lg px-3 py-2 text-sm text-[var(--color-text)] focus:outline-none focus:border-[var(--color-accent)]"
              maxLength={2000}
            />
          </div>

          <div>
            <label htmlFor="tm-suite-owner" className="block text-xs text-[var(--color-text-muted)] mb-1">Owner</label>
            {ownerCandidates.length === 0 ? (
              <p className="text-[11px] text-[var(--status-broken)] bg-[var(--status-broken-bg)] border border-[var(--status-broken-bd)] rounded px-2 py-1.5">
                No project members have the QA_LEAD role yet. Leave unset to inherit the project's default QA lead, or add a QA_LEAD member first.
              </p>
            ) : (
              <select
                id="tm-suite-owner"
                value={ownerUserId}
                onChange={e => setOwnerUserId(e.target.value)}
                className="w-full bg-[var(--color-bg-secondary)] border border-[var(--color-border-light)] rounded-lg px-3 py-2 text-sm text-[var(--color-text)] focus:outline-none focus:border-[var(--color-accent)]"
              >
                <option value="">— Use project default QA Lead —</option>
                {ownerCandidates.map(u => (
                  <option key={u.id} value={u.id}>
                    {u.full_name || u.username} ({u.email})
                  </option>
                ))}
              </select>
            )}
          </div>

          <div>
            <label htmlFor="tm-field-20" className="block text-xs text-[var(--color-text-muted)] mb-1">
              Tags <span className="text-[var(--color-text-faint)]">(comma-separated)</span>
            </label>
            <input id="tm-field-20"
              type="text"
              value={tagsText}
              onChange={e => setTagsText(e.target.value)}
              placeholder="smoke, api, regression"
              className="w-full bg-[var(--color-bg-secondary)] border border-[var(--color-border-light)] rounded-lg px-3 py-2 text-sm text-[var(--color-text)] focus:outline-none focus:border-[var(--color-accent)]"
            />
          </div>
        </div>
    </InlineFormSection>
  )
}

// ─── Main Page ────────────────────────────────────────────────────────────────

export default function TestManagementPage() {
  const [searchParams] = useSearchParams()
  const [tabParam, setTab] = useTabParam<TmTab>(TM_TAB_IDS, 'cases')
  // A link from before P4 (`?tab=Test+Suites&suite=<name>`, `?tab=reviews`)
  // opens the tab that has its content now; choosing a tab writes the new id.
  const activeTab: TmTab = tabAlias(searchParams.get('tab')) ?? tabParam

  const project = useProjectStore(s => s.activeProject)
  const projectId = useProjectStore(s => s.activeProjectId)
  const isAllProjects = projectId === 'all'
  const lifecycleV2 = useFeatureEnabled('test_case_lifecycle_v2')
  // W13 TCL-06: read ONCE here, beside the lifecycle flag, and passed down.
  const requirementTraceability = useFeatureEnabled('test_case_requirement_traceability')

  if (!project && !isAllProjects) {
    return (
      <EmptyState
        icon={<ClipboardList className="h-10 w-10" />}
        title="No project selected"
        description="Select a project from the top bar to manage test cases"
      />
    )
  }

  // Pass null to tabs in All Projects mode — disables mutations, enables read-only list
  const tabProjectId = isAllProjects ? null : projectId

  return (
    <div className="space-y-4">
      <PageHeader
        compact
        title="Test Case Management"
        subtitle={`Manage test cases, suites, plans and approvals${project ? ` for ${project.name}` : ' — All Projects'}`}
        helpTopic={HELP_TOPIC}
        tabs={<TestCasesTabBar value={activeTab} onChange={setTab} />}
      />

      <div data-tab-panel={activeTab}>
        {activeTab === 'cases'      && (
          <TestCasesTab projectId={tabProjectId} lifecycleV2={lifecycleV2} requirementTraceability={requirementTraceability} />
        )}
        {activeTab === 'suites'     && <TestSuitesTab projectId={tabProjectId} />}
        {activeTab === 'plans'      && <TestPlansTab projectId={tabProjectId} />}
        {activeTab === 'approvals'  && <ApprovalsTab projectId={tabProjectId} lifecycleV2={lifecycleV2} />}
        {activeTab === 'strategy'   && <StrategyTab projectId={tabProjectId} />}
        {activeTab === 'duplicates' && <DuplicatesTab projectId={tabProjectId} />}
        {activeTab === 'audit'      && <AuditTab projectId={tabProjectId} />}
        {activeTab === 'knowledge'  && <KnowledgeGenerationTab />}
      </div>
    </div>
  )
}
