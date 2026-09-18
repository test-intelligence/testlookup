import { useState, useCallback, useEffect, useMemo, useRef } from 'react'
import useSWR from 'swr'
import { useDataFreshness } from '@/hooks/useDataFreshness'
import { useNow } from '@/hooks/useNow'
import { shortAgo } from '@/utils/formatters'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import {
  ClipboardList, Plus, Sparkles, ChevronDown, ChevronRight,
  Star, Clock, User, CheckCircle2, XCircle, AlertCircle,
  RotateCcw, MessageSquare, History, Shield, FileText,
  ChevronUp, Trash2, BookOpen, BarChart2,
  Download, FileSpreadsheet, Layers,
  Copy, GitMerge, Search as SearchIcon,
} from 'lucide-react'
import { clsx } from 'clsx'
import toast from 'react-hot-toast'
import PageHeader from '@/components/ui/PageHeader'
import EmptyState from '@/components/ui/EmptyState'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import Pagination from '@/components/ui/Pagination'
import EvidenceGapLists from '@/components/testManagement/EvidenceGapLists'
import LifecyclePanel from '@/components/testManagement/LifecyclePanel'
import PromotionAction from '@/components/testManagement/PromotionAction'
import TransitionReasonDialog from '@/components/testManagement/TransitionReasonDialog'
import { useTableSort } from '@/hooks/useTableSort'
import { useFeatureEnabled } from '@/hooks/useFeatureFlags'
import { api } from '@/services/api'
import { useProjectStore } from '@/store/projectStore'
import {
  useTestCases, useTestPlans, useStrategies, useAuditLog,
  useTestCaseHistory, useTestCaseReviews, useTestCaseComments,
  usePlanItems, useUsers, useDuplicateCandidates,
} from '@/hooks/useTestManagement'
import { usePermissions } from '@/hooks/usePermissions'
import { useProjectMembers } from '@/hooks/useUserManagement'
import {
  testManagementService,
} from '@/services/testManagementService'
import type { UserSummary, SuiteReviewItem, SuiteReviewState } from '@/services/testManagementService'
import { deriveTestManagementTotals, describeStatBasis } from '@/utils/testManagementTotals'
import { getTestManagementCaseDetailPath } from '@/utils/testManagementCase'
import KnowledgeGenerationTab from '@/pages/test-management/KnowledgeGenerationTab'
import type {
  AIReviewResult,
  DuplicateBand,
  DuplicateCandidate,
  DuplicateCandidateStatus,
  ManagedTestCase,
  TestCaseComment,
  TestCaseReview,
  TestCaseVersion,
  TestPlan,
  TestPlanItem,
  TestCaseTransitionAction,
  TestStep,
  TestStrategy,
} from '@/types/test-management'
import {
  buildTestCaseListParams,
  LIFECYCLE_STATUS_OPTIONS,
} from '@/utils/testCaseLifecycleUi'

// ─── Constants / helpers ─────────────────────────────────────────────────────

const TABS = ['Test Cases', 'Test Suites', 'Test Plans', 'Strategy', 'Knowledge Generation', 'Reviews', 'Duplicates', 'Audit Log'] as const
type Tab = typeof TABS[number]

const STATUS_COLORS: Record<string, string> = {
  draft:            'bg-[var(--color-bg-hover)] text-[var(--color-text-secondary)] border border-[var(--color-border-light)]',
  review_requested: 'bg-[var(--status-broken-bg)] text-[var(--status-broken)] border border-[var(--status-broken-bd)]',
  under_review:     'bg-[var(--color-bg-secondary)]/80 text-[var(--color-text-secondary)] border border-[var(--color-border-light)]',
  approved:         'bg-[var(--status-passed-bg)] text-[var(--status-passed)] border border-[var(--status-passed-bd)]',
  active:           'bg-[var(--status-passed-bg)] text-[var(--status-passed)] border border-[var(--status-passed-bd)]',
  rejected:         'bg-[var(--status-failed-bg)] text-[var(--status-failed)] border border-[var(--status-failed-bd)]',
  needs_update:     'bg-[var(--status-broken-bg)] text-[var(--status-broken)] border border-[var(--status-broken-bd)]',
  deprecated:       'bg-[var(--color-bg-secondary)] text-[var(--color-text-muted)] border border-[var(--color-border)]',
  archived:         'bg-[var(--color-bg-secondary)] text-[var(--color-text-faint)] border border-[var(--color-border)]',
}

const REVIEW_ACTION_LABELS: Partial<Record<TestCaseTransitionAction, string>> = {
  claim_review: 'Claim review',
  unclaim: 'Unclaim',
  approve: 'Approve',
  request_changes: 'Request changes',
  reject: 'Reject',
}

const REVIEW_DECISION_ACTIONS = new Set<TestCaseTransitionAction>([
  'approve',
  'request_changes',
  'reject',
])

const LEGACY_REVIEW_ACTIONS: TestCaseTransitionAction[] = ['approve', 'request_changes', 'reject']

const PRIORITY_COLORS: Record<string, string> = {
  critical: 'bg-[var(--status-failed-bg)] text-[var(--status-failed)] border border-[var(--status-failed-bd)]',
  high:     'bg-[var(--status-broken-bg)] text-[var(--status-broken)] border border-[var(--status-broken-bd)]',
  medium:   'bg-[var(--status-skipped-bg)] text-[var(--status-skipped)] border border-[var(--status-skipped-bd)]',
  low:      'bg-[var(--color-bg-hover)] text-[var(--color-text-muted)] border border-[var(--color-border-light)]',
}

const PLAN_STATUS_COLORS: Record<string, string> = {
  draft:       'bg-[var(--color-bg-hover)] text-[var(--color-text-secondary)] border border-[var(--color-border-light)]',
  active:      'bg-[var(--color-bg-secondary)]/80 text-[var(--color-text-secondary)] border border-[var(--color-border-light)]',
  in_progress: 'bg-[var(--status-broken-bg)] text-[var(--status-broken)] border border-[var(--status-broken-bd)]',
  completed:   'bg-[var(--status-passed-bg)] text-[var(--status-passed)] border border-[var(--status-passed-bd)]',
  archived:    'bg-[var(--color-bg-secondary)] text-[var(--color-text-muted)] border border-[var(--color-border)]',
}

function StatusPill({ status, map }: { status: string; map: Record<string, string> }) {
  const cls = map[status] ?? 'bg-[var(--color-bg-hover)] text-[var(--color-text-muted)] border border-[var(--color-border-light)]'
  return (
    <span className={clsx('inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium', cls)}>
      {status.replace(/_/g, ' ')}
    </span>
  )
}

function QualityScore({ score }: { score?: number }) {
  if (score == null) return <span className="text-[var(--color-text-faint)] text-xs">—</span>
  const color = score >= 80 ? 'text-[var(--status-passed)]' : score >= 60 ? 'text-[var(--status-broken)]' : 'text-[var(--status-failed)]'
  return <span className={clsx('text-sm font-semibold tabular-nums', color)}>{score}</span>
}

function fmtDate(iso?: string) {
  if (!iso) return '—'
  return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
}

function fmtDateTime(iso?: string) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

// ─── Sub-components (Modals) ──────────────────────────────────────────────────

interface ModalWrapProps { onClose: () => void; title: string; children: React.ReactNode; width?: string }
function ModalWrap({ onClose, title, children, width = 'max-w-2xl' }: ModalWrapProps) {
  return (
    <div role="dialog" aria-modal="true" aria-labelledby="tm-generic-modal-title" className="fixed inset-0 bg-[var(--color-bg)]/60 z-50 flex items-center justify-center p-4">
      <div className={clsx('bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl w-full shadow-2xl flex flex-col max-h-[90vh]', width)}>
        <div className="flex items-center justify-between px-6 py-4 border-b border-[var(--color-border)] flex-shrink-0">
          <h2 id="tm-generic-modal-title" className="text-base font-semibold text-[var(--color-text)]">{title}</h2>
          <button onClick={onClose} className="text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-colors text-xl leading-none">&times;</button>
        </div>
        <div className="overflow-y-auto flex-1 px-6 py-4">
          {children}
        </div>
      </div>
    </div>
  )
}

// ── Create Test Case Modal ────────────────────────────────────────────────────

interface CreateCaseModalProps { projectId: string; onClose: () => void; onCreated: () => void }

function CreateCaseModal({ projectId, onClose, onCreated }: CreateCaseModalProps) {
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
  const { data: users = [] } = useUsers()

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
    <ModalWrap onClose={onClose} title="New Test Case" width="max-w-3xl">
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
            {(users as UserSummary[]).map((u) => (
              <option key={u.id} value={u.id}>
                {u.full_name ?? u.username}
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

// ── AI Generate Modal ─────────────────────────────────────────────────────────

interface AIGenerateModalProps { projectId: string; onClose: () => void }

function AIGenerateModal({ projectId, onClose }: AIGenerateModalProps) {
  const [requirements, setRequirements] = useState('')
  const [loading, setLoading] = useState(false)

  const handleGenerate = async () => {
    if (requirements.trim().length < 3) { toast.error('Please enter at least 3 characters'); return }
    setLoading(true)
    try {
      await testManagementService.aiGenerateAsync({
        project_id: projectId,
        requirements: requirements.trim(),
        persist: true,
      })
      toast.success(
        'Test cases are being generated in the background and will be saved as drafts — refresh the list in a moment.',
        { duration: 7000 }
      )
      onClose()
    } catch {
      toast.error('Failed to start AI generation')
      setLoading(false)
    }
  }

  return (
    <ModalWrap onClose={onClose} title="AI Generate Test Cases" width="max-w-2xl">
      <div className="space-y-4">
        <p className="text-sm text-[var(--color-text-muted)]">
          Describe the feature or paste requirements text. The AI will generate comprehensive test cases and save them as drafts automatically.
        </p>
        <textarea
          className="input w-full h-36 resize-none"
          value={requirements}
          onChange={e => setRequirements(e.target.value)}
          placeholder="e.g. User should be able to log in with email and password, with form validation and error handling for wrong credentials..."
          disabled={loading}
          autoFocus
        />
        <div className="bg-[var(--color-bg-secondary)]/80 rounded-lg px-3 py-2 flex items-start gap-2 text-xs text-[var(--color-text-muted)]">
          <Clock className="h-3.5 w-3.5 mt-0.5 flex-shrink-0 text-[var(--color-text-muted)]" />
          <span>Generation runs in the background (typically 1–2 minutes). You can close this and check the list shortly.</span>
        </div>
        <div className="flex justify-end gap-3 pt-1">
          <button onClick={onClose} disabled={loading} className="btn-secondary">Cancel</button>
          <button onClick={handleGenerate} disabled={loading || requirements.trim().length < 3} className="btn-primary flex items-center gap-2">
            {loading ? <LoadingSpinner size="sm" /> : <Sparkles className="h-4 w-4" />}
            {loading ? 'Submitting…' : 'Generate & Save'}
          </button>
        </div>
      </div>
    </ModalWrap>
  )
}

// ── Case Detail Panel ─────────────────────────────────────────────────────────

type DetailTab = 'details' | 'lifecycle' | 'history' | 'reviews' | 'comments' | 'ai_review'

interface CaseDetailPanelProps {
  caseItem: ManagedTestCase
  onClose: () => void
  onRefresh: () => void
  onCaseChanged: (updated: ManagedTestCase) => void
  lifecycleV2: boolean
}

function CaseDetailPanel({ caseItem, onClose, onRefresh, onCaseChanged, lifecycleV2 }: CaseDetailPanelProps) {
  const [activeTab, setActiveTab] = useState<DetailTab>('details')
  const [comment, setComment] = useState('')
  const [addingComment, setAddingComment] = useState(false)
  const [runningAiReview, setRunningAiReview] = useState(false)
  const [aiResult, setAiResult] = useState<AIReviewResult | null>(caseItem.ai_review_notes ?? null)
  const [requestingReview, setRequestingReview] = useState(false)

  const { data: history, isLoading: histLoading } = useTestCaseHistory(activeTab === 'history' ? caseItem.id : undefined)
  const { data: reviews, isLoading: revLoading, mutate: mutateReviews } = useTestCaseReviews(activeTab === 'reviews' ? caseItem.id : undefined)
  const { data: comments, isLoading: commLoading, mutate: mutateComments } = useTestCaseComments(
    activeTab === 'comments' ? caseItem.id : undefined
  )

  const DETAIL_TABS: { id: DetailTab; label: string; icon: React.ReactNode }[] = [
    { id: 'details',   label: 'Details',    icon: <FileText className="h-3.5 w-3.5" /> },
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

  return (
    <div role="dialog" aria-modal="true" aria-labelledby="tm-case-detail-title" className="fixed inset-0 bg-[var(--color-bg)]/60 z-50 flex items-start justify-end">
      <div className="bg-[var(--color-bg-card)] border-l border-[var(--color-border)] h-full w-full max-w-2xl flex flex-col shadow-2xl">
        {/* Header */}
        <div className="px-6 py-4 border-b border-[var(--color-border)] flex-shrink-0">
          <div className="flex items-start justify-between gap-3">
            <div className="flex-1 min-w-0">
              <h2 id="tm-case-detail-title" className="text-base font-semibold text-[var(--color-text)] leading-snug">{caseItem.title}</h2>
              <div className="flex items-center gap-2 mt-1.5 flex-wrap">
                <StatusPill status={caseItem.status} map={STATUS_COLORS} />
                <StatusPill status={caseItem.priority} map={PRIORITY_COLORS} />
                <span className="text-xs text-[var(--color-text-muted)]">{caseItem.test_type}</span>
                {caseItem.ai_generated && (
                  <span className="text-xs text-[var(--color-purple)] flex items-center gap-0.5"><Sparkles className="h-3 w-3" /> AI</span>
                )}
              </div>
            </div>
            <button onClick={onClose} className="text-[var(--color-text-muted)] hover:text-[var(--color-text)] text-xl leading-none flex-shrink-0">&times;</button>
          </div>
        </div>

        {/* Tab bar */}
        <div className="flex gap-1 px-4 pt-3 border-b border-[var(--color-border)] flex-shrink-0 overflow-x-auto">
          {DETAIL_TABS.map(t => (
            <button
              key={t.id}
              onClick={() => setActiveTab(t.id)}
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
          {/* Details tab */}
          {activeTab === 'details' && (
            <div className="space-y-4">
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
                {[
                  ['Feature Area', caseItem.feature_area ?? '—'],
                  ['Severity', caseItem.severity],
                  ['Version', String(caseItem.version)],
                  ['Automation', caseItem.automation_status.replace(/_/g, ' ')],
                  ['Est. Duration', caseItem.estimated_duration_minutes ? `${caseItem.estimated_duration_minutes} min` : '—'],
                  ['Last Executed', fmtDate(caseItem.last_executed_at)],
                ].map(([label, value]) => (
                  <div key={label} className="bg-[var(--color-bg-secondary)] rounded-lg p-3">
                    <p className="text-xs text-[var(--color-text-muted)] mb-0.5">{label}</p>
                    <p className="text-[var(--color-text-secondary)] capitalize">{value}</p>
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
              {!lifecycleV2 && caseItem.status !== 'review_requested' && caseItem.status !== 'under_review' && (
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
            </div>
          )}

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

          {/* History tab */}
          {activeTab === 'history' && (
            histLoading ? <div className="flex justify-center py-12"><LoadingSpinner /></div> :
            !history || history.length === 0 ? (
              <EmptyState icon={<History className="h-8 w-8" />} title="No history yet" description="Changes to this test case will appear here" />
            ) : (
              <div className="space-y-3">
                {(history as TestCaseVersion[]).map(v => (
                  <div key={v.id} className="bg-[var(--color-bg-secondary)] rounded-lg p-4">
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-sm font-medium text-[var(--color-text)]">v{v.version} — {v.change_type.replace(/_/g, ' ')}</span>
                      <span className="text-xs text-[var(--color-text-muted)]">{fmtDateTime(v.created_at)}</span>
                    </div>
                    {v.change_summary && <p className="text-xs text-[var(--color-text-muted)]">{v.change_summary}</p>}
                    {v.changed_fields && v.changed_fields.length > 0 && (
                      <p className="mt-1 text-[11px] text-[var(--color-text-muted)]">
                        Changed: {v.changed_fields.join(', ')}
                      </p>
                    )}
                  </div>
                ))}
              </div>
            )
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
            </div>
          )}

          {/* AI Review tab */}
          {activeTab === 'ai_review' && (
            <div className="space-y-4">
              <div className="flex items-center justify-between">
                <p className="text-sm text-[var(--color-text-secondary)]">Run AI analysis to score this test case and get improvement suggestions.</p>
                <button onClick={handleAiReview} disabled={runningAiReview} className="btn-primary flex items-center gap-2 text-sm flex-shrink-0">
                  {runningAiReview ? <LoadingSpinner size="sm" /> : <Sparkles className="h-4 w-4" />}
                  {runningAiReview ? 'Reviewing…' : 'Run AI Review'}
                </button>
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
      </div>
    </div>
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

// ── Generate Strategy Modal ───────────────────────────────────────────────────

interface GenerateStrategyModalProps { projectId: string; onClose: () => void }

function GenerateStrategyModal({ projectId, onClose }: GenerateStrategyModalProps) {
  const [context, setContext] = useState('')
  const [strategyName, setStrategyName] = useState('')
  const [loading, setLoading] = useState(false)

  const handleGenerate = async () => {
    if (!context.trim()) { toast.error('Project context is required'); return }
    setLoading(true)
    try {
      await testManagementService.aiGenerateStrategyAsync({
        project_id: projectId,
        project_context: context.trim(),
        strategy_name: strategyName || undefined,
      })
      toast.success(
        'Strategy is being generated in the background and will appear in the list shortly.',
        { duration: 7000 }
      )
      onClose()
    } catch {
      toast.error('Strategy generation failed')
      setLoading(false)
    }
  }

  return (
    <ModalWrap onClose={onClose} title="Generate Test Strategy with AI">
      <div className="space-y-4">
        <div>
          <label htmlFor="tm-field-16" className="block text-xs text-[var(--color-text-muted)] mb-1">Strategy Name (optional)</label>
          <input id="tm-field-16" className="input w-full" value={strategyName} onChange={e => setStrategyName(e.target.value)} placeholder="e.g. v2.0 Release Strategy" />
        </div>
        <div>
          <label htmlFor="tm-field-17" className="block text-xs text-[var(--color-text-muted)] mb-1">Project Context *</label>
          <textarea id="tm-field-17"
            className="input w-full h-40 resize-none"
            value={context}
            onChange={e => setContext(e.target.value)}
            placeholder="Describe your project: technology stack, team size, release cadence, key features, compliance requirements, known risks, etc. The more context, the better the strategy."
            disabled={loading}
            autoFocus
          />
        </div>
        <div className="bg-[var(--color-bg-secondary)]/80 rounded-lg px-3 py-2 flex items-start gap-2 text-xs text-[var(--color-text-muted)]">
          <Clock className="h-3.5 w-3.5 mt-0.5 flex-shrink-0 text-[var(--color-text-muted)]" />
          <span>Covers objectives, scope, test types, risk assessment, entry/exit criteria, environments, and automation approach. Runs in the background — typically 1–2 minutes.</span>
        </div>
        <div className="flex justify-end gap-3 pt-2 border-t border-[var(--color-border)]">
          <button onClick={onClose} disabled={loading} className="btn-secondary">Cancel</button>
          <button onClick={handleGenerate} disabled={loading || !context.trim()} className="btn-primary flex items-center gap-2">
            {loading ? <LoadingSpinner size="sm" /> : <Sparkles className="h-4 w-4" />}
            {loading ? 'Submitting…' : 'Generate & Save'}
          </button>
        </div>
      </div>
    </ModalWrap>
  )
}

// ─── Tab: Test Cases ──────────────────────────────────────────────────────────

interface TestCasesTabProps { projectId: string | null; lifecycleV2: boolean }

export function TestCasesTab({ projectId, lifecycleV2 }: TestCasesTabProps) {
  const navigate = useNavigate()
  const now = useNow()  // captured at mount — avoids impure Date.now() in render
  // ── Filter state ────────────────────────────────────────────────────
  // Single-select today; multi-select chips with a popover are Phase 2 per
  // README §6 "Select chip click opens a popover with checkboxes".
  const [page, setPage] = useState(1)
  const [status, setStatus] = useState('')
  const [testType, setTestType] = useState('')
  const [priority, setPriority] = useState('')
  const [search, setSearch] = useState('')
  const [ownerFilter, setOwnerFilter] = useState('')
  const [suiteFilter, setSuiteFilter] = useState('')
  const [savedView, setSavedView] = useState<string | null>(null)
  const [showCreate, setShowCreate] = useState(false)
  const [showAiGen, setShowAiGen] = useState(false)
  const [selectedCase, setSelectedCase] = useState<ManagedTestCase | null>(null)
  const [pendingDeprecation, setPendingDeprecation] = useState<ManagedTestCase | null>(null)
  const [deprecating, setDeprecating] = useState(false)

  const openCase = useCallback((caseItem: ManagedTestCase) => {
    const executionPath = getTestManagementCaseDetailPath(caseItem)
    if (executionPath) {
      navigate(executionPath)
      return
    }
    if (caseItem.source === 'automation') {
      toast.error('This automation result has no execution detail yet. Promote it from the row to manage its lifecycle.')
      return
    }
    setSelectedCase(caseItem)
  }, [navigate])
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

  const params = useMemo(() => {
    return buildTestCaseListParams({
      page,
      size: 25,
      status,
      testType,
      priority,
      search,
      ownerFilter,
      suiteFilter,
      includeAutomation,
    })
  }, [page, status, testType, priority, search, ownerFilter, suiteFilter, includeAutomation])

  const {
    data,
    isLoading,
    error: casesError,
    mutate: mutateCases,
  } = useTestCases(params)
  // Was a hardcoded "knowledge graph rebuilt just now" -- a fabricated
  // freshness claim (#893) about an event this page cannot observe. Report
  // when the payload actually arrived instead.
  const fetchedAt = useDataFreshness(data)
  const refreshedAt = fetchedAt ? shortAgo(fetchedAt) : 'just now'
  // Wider read used to power Library Verdict + right-rail synthesis (review
  // queue, strategy gaps, coverage matrix). The /test-cases/health endpoint
  // the spec assumes (README §14 q1) doesn't exist yet, so we synthesise.
  const {
    data: healthRoll,
    error: healthError,
    mutate: mutateHealthRoll,
  } = useTestCases({ page: 1, size: 200 })
  const { data: auditRoll } = useAuditLog({ page: 1, size: 5, entity_type: 'test_case' })
  const [caseRetrying, setCaseRetrying] = useState(false)
  const [caseRefreshWarning, setCaseRefreshWarning] = useState<string | null>(null)
  const hasPreviouslyLoadedCases = Boolean(data?.items.length || healthRoll?.items.length)

  // Both rolls must be revalidated. The library-health panel reads its own
  // `useTestCases({page:1,size:200})` roll, a DIFFERENT SWR key from the
  // paginated table, so refreshing only the table left the panel asserting
  // "1 cases" beside a list showing 2 until the 60 s background poll caught up.
  const handleRefresh = useCallback(
    () => { void mutateCases(); void mutateHealthRoll() },
    [mutateCases, mutateHealthRoll],
  )

  const retryCaseData = useCallback(async () => {
    if (caseRetrying) return
    setCaseRetrying(true)
    setCaseRefreshWarning(null)
    try {
      await Promise.all([mutateCases(), mutateHealthRoll()])
    } catch {
      setCaseRefreshWarning('Test-case data is still unavailable. Previously loaded results remain visible.')
    } finally {
      setCaseRetrying(false)
    }
  }, [caseRetrying, mutateCases, mutateHealthRoll])

  const casesRaw = data?.items ?? []
  const { sorted: cases } = useTableSort(casesRaw, 'updated_at', 'desc')

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
    setPendingDeprecation(caseItem)
  }

  async function confirmDeprecation(reason: string) {
    if (!pendingDeprecation) return
    setDeprecating(true)
    try {
      if (lifecycleV2) {
        const updated = await testManagementService.transitionCase(pendingDeprecation.id, {
          action: 'deprecate',
          reason,
          expected_version: pendingDeprecation.version,
        })
        setSelectedCase((current) => current?.id === updated.id ? updated : current)
      } else {
        await testManagementService.deleteCase(pendingDeprecation.id, reason)
      }
      setPendingDeprecation(null)
      handleRefresh()
      toast.success(lifecycleV2 ? 'Test case deprecated' : 'Test case deleted')
    } catch {
      toast.error(lifecycleV2 ? 'Failed to deprecate test case' : 'Failed to delete test case')
    } finally {
      setDeprecating(false)
    }
  }

  // ── Library health model (synthesised) ─────────────────────────────
  const fullList: ManagedTestCase[] = healthRoll?.items ?? casesRaw
  // Two distinct totals — both surfaced separately to fix the recurring
  // "Cases 25 of 0" misread (third regression 2026-05-16). See
  // ``utils/testManagementTotals.ts`` for the helper + unit tests.
  const { authoredTotal, casesTotal } = deriveTestManagementTotals({
    healthRoll,
    data,
    casesLength: cases.length,
  })
  const activeCount       = fullList.filter(c => c.status === 'active').length
  const automatedCount    = fullList.filter(c => c.is_automated).length
  const automatedPct      = fullList.length > 0 ? Math.round((automatedCount / fullList.length) * 100) : 0
  const reviewQueue       = fullList.filter(c => c.status === 'review_requested' || c.status === 'under_review')
  const reviewCount       = reviewQueue.length
  const draftAgeDays = (iso: string) => Math.floor((now - new Date(iso).getTime()) / 86400000)
  const staleDrafts       = fullList.filter(c => c.status === 'draft' && draftAgeDays(c.updated_at) >= 30)
  const staleCount        = staleDrafts.length
  // "Deprecated cases referenced by active suites" — heuristic: deprecated
  // cases that still carry a non-null suite_name are flagged. The real check
  // needs a server-side cross-reference (README §14 q2) — flagged in BACKLOG.
  const deprecatedInActive = fullList.filter(c => c.status === 'deprecated' && !!c.suite_name).length
  const oldestReviewDays = reviewQueue.length === 0
    ? 0
    : Math.max(...reviewQueue.map(c => draftAgeDays(c.updated_at)))
  const avgAgeDays = fullList.length > 0
    ? Math.round(fullList.reduce((s, c) => s + draftAgeDays(c.created_at), 0) / fullList.length)
    : 0
  const olderThan180 = fullList.filter(c => draftAgeDays(c.created_at) > 180).length
  const olderThan180Pct = fullList.length > 0 ? Math.round((olderThan180 / fullList.length) * 100) : 0

  // Health score — weighted: automation (35%) · stale-drafts inverse (20%)
  // · review-queue freshness (20%) · deprecated-in-active inverse (25%).
  // 0-100, higher = healthier.
  const automationScore   = automatedPct
  const staleScore        = fullList.length > 0 ? Math.max(0, 100 - (staleCount / fullList.length) * 400) : 100
  const reviewFreshScore  = oldestReviewDays === 0 ? 100 : Math.max(0, 100 - (oldestReviewDays / 14) * 100)
  const depInActiveScore  = fullList.length > 0 ? Math.max(0, 100 - (deprecatedInActive / fullList.length) * 400) : 100
  const healthScore = Math.round(
    automationScore   * 0.35 +
    staleScore        * 0.20 +
    reviewFreshScore  * 0.20 +
    depInActiveScore  * 0.25,
  )
  const healthTag: 'Healthy' | 'Needs attention' | 'At risk' =
    healthScore >= 85 ? 'Healthy' : healthScore >= 70 ? 'Needs attention' : 'At risk'
  const healthTone = healthScore >= 85 ? 'var(--status-passed)' : healthScore >= 70 ? 'var(--status-broken)' : 'var(--status-failed)'

  // ── Saved views (synthesised) ───────────────────────────────────────
  type SavedViewId = 'my_drafts' | 'p0_p1' | 'unautomated'
  const SAVED_VIEWS: { id: SavedViewId; label: string }[] = [
    { id: 'my_drafts',   label: 'My drafts' },
    { id: 'p0_p1',       label: "My team's P0/P1" },
    { id: 'unautomated', label: 'Unautomated' },
  ]
  const applySavedView = (id: SavedViewId) => {
    setSavedView(id)
    setPage(1)
    // Re-write filter state from the view definition.
    if (id === 'my_drafts')   { setStatus('draft');    setPriority(''); setTestType(''); setSuiteFilter(''); setOwnerFilter('') }
    if (id === 'p0_p1')       { setStatus('');         setPriority('critical'); setTestType(''); setSuiteFilter(''); setOwnerFilter('') }
    if (id === 'unautomated') { setStatus(''); setPriority(''); setTestType(''); setSuiteFilter(''); setOwnerFilter(''); /* automation flag — Phase 2 server-side filter */ }
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
  // What the page can honestly say is how many authored cases are automated,
  // which it already knows from the rows themselves.
  const coverageAuto    = automatedCount
  const coverageManual  = fullList.length - automatedCount

  // ── Strategy gaps synthesis ─────────────────────────────────────────
  const suiteCounts = new Map<string, number>()
  for (const c of fullList) {
    const s = (c.suite_name ?? '').trim() || 'unknown'
    suiteCounts.set(s, (suiteCounts.get(s) ?? 0) + 1)
  }
  const strategyGaps: { severity: 'critical' | 'warn'; title: string; sub: string; pill: string }[] = []
  if (deprecatedInActive > 0) {
    strategyGaps.push({ severity: 'critical', title: 'Deprecated cases in active suites', sub: 'still referenced by run plans', pill: `${deprecatedInActive} active` })
  }
  if (reviewCount > 0 && oldestReviewDays >= 7) {
    strategyGaps.push({ severity: 'warn', title: 'Aging reviews over SLA', sub: `oldest ${oldestReviewDays} days`, pill: `${reviewCount} pending` })
  }
  if (automatedPct < 60) {
    strategyGaps.push({ severity: 'warn', title: 'Automation below target', sub: `${automatedPct}% automated · target 60%`, pill: `${60 - automatedPct}% gap` })
  }
  if (staleCount > 0) {
    strategyGaps.push({ severity: 'warn', title: 'Stale drafts ≥ 30d', sub: 'untouched in the last month', pill: `${staleCount} stale` })
  }

  // ── Recent activity from audit log ──────────────────────────────────
  type AuditEvent = { id: string; action?: string; actor_name?: string; entity_id?: string; created_at: string; event_type?: string }
  const auditEvents: AuditEvent[] = ((auditRoll?.items ?? []) as unknown as AuditEvent[]).slice(0, 5)

  // ── Sort + Pagination ───────────────────────────────────────────────
  const sortLabel = 'updated'   // matches default useTableSort
  const totalShown = cases.length

  // ── Render ──────────────────────────────────────────────────────────
  return (
    <>
      {/* Library verdict */}
      <LibraryVerdictRibbon
        healthScore={healthScore}
        healthTag={healthTag}
        healthTone={healthTone}
        totalCases={authoredTotal}
        reviewCount={reviewCount}
        staleCount={staleCount}
        deprecatedInActive={deprecatedInActive}
        oldestReviewDays={oldestReviewDays}
        activeCount={activeCount}
        automatedPct={automatedPct}
        avgAgeDays={avgAgeDays}
        olderThan180Pct={olderThan180Pct}
        statBasis={describeStatBasis(fullList.length, authoredTotal)}
      />

      {/* Filter bar */}
      <CasesFilterBar
        searchInputRef={searchInputRef}
        search={search}
        onSearchChange={(v) => { setSearch(v); setPage(1); setSavedView(null) }}
        status={status}
        onStatusChange={(v) => { setStatus(v); setPage(1); setSavedView(null) }}
        testType={testType}
        onTypeChange={(v) => { setTestType(v); setPage(1); setSavedView(null) }}
        priority={priority}
        onPriorityChange={(v) => { setPriority(v); setPage(1); setSavedView(null) }}
        ownerFilter={ownerFilter}
        onOwnerChange={(v) => { setOwnerFilter(v); setPage(1); setSavedView(null) }}
        suiteFilter={suiteFilter}
        onSuiteChange={(v) => { setSuiteFilter(v); setPage(1); setSavedView(null) }}
        suiteOptions={Array.from(suiteCounts.keys()).filter(s => s !== 'unknown').sort()}
        savedView={savedView}
        savedViews={SAVED_VIEWS}
        onSavedView={(v) => applySavedView(v.id)}
        onSaveCurrent={() => toast('Save view — coming in Phase 2', { icon: '⭐' })}
        includeAutomation={includeAutomation}
        onToggleAutomation={(v) => { setIncludeAutomation(v); setPage(1) }}
      />

      {(casesError || healthError) && (
        <div
          role="alert"
          className="mt-3 flex items-center justify-between gap-3 rounded-lg border border-[var(--status-failed-bd)] bg-[var(--status-failed-bg)] p-3 text-sm text-[var(--status-failed)]"
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
          className="mt-3 rounded-lg border border-[var(--status-broken-bd)] bg-[var(--status-broken-bg)] p-3 text-sm text-[var(--status-broken)]"
        >
          {caseRefreshWarning}
        </p>
      )}

      {/* Body grid */}
      <div className="grid gap-3.5 mt-3.5" style={{ gridTemplateColumns: 'minmax(0, 1.65fr) minmax(0, 1fr)' }}>
        <div className="flex flex-col gap-3.5 min-w-0">
          {/* Cases table */}
          <div className="rounded-xl overflow-hidden" style={{ background: 'var(--color-bg-card)', border: '1px solid var(--color-border)' }}>
            <div
              className="flex items-center justify-between gap-2 px-4 py-3"
              style={{ borderBottom: '1px solid var(--color-border)' }}
            >
              <h3 className="text-[13px] font-semibold m-0 text-[var(--color-text)]">
                Cases <span className="font-normal text-[var(--color-text-muted)] text-[11.5px] ml-2">{totalShown} of {casesTotal} · sorted by {sortLabel}</span>
              </h3>
              <div className="flex items-center gap-2 flex-wrap">
                <button
                  onClick={handleExportExcel}
                  className="inline-flex items-center gap-1.5 px-2.5 py-1.5 text-[13px] text-[var(--color-text-muted)] hover:text-[var(--color-text)] border rounded-md transition-colors"
                  style={{ borderColor: 'var(--color-border)' }}
                  title="Export to Excel"
                >
                  <FileSpreadsheet className="h-3.5 w-3.5" /> Export
                </button>
                <button
                  onClick={() => toast('Import CSV — coming in Phase 2', { icon: '📥' })}
                  className="inline-flex items-center gap-1.5 px-2.5 py-1.5 text-[13px] text-[var(--color-text-muted)] hover:text-[var(--color-text)] border rounded-md transition-colors"
                  style={{ borderColor: 'var(--color-border)' }}
                >
                  <Download className="h-3.5 w-3.5 rotate-180" /> Import CSV
                </button>
                {projectId && (
                  <>
                    <button
                      onClick={() => setShowAiGen(true)}
                      className="inline-flex items-center gap-1.5 px-2.5 py-1.5 text-[13px] rounded-md border transition-colors"
                      style={{ color: 'var(--status-flaky)', borderColor: 'color-mix(in srgb, var(--status-flaky) 30%, transparent)', background: 'color-mix(in srgb, var(--status-flaky) 6%, transparent)' }}
                    >
                      <Sparkles className="h-3.5 w-3.5" /> AI Generate
                    </button>
                    <button
                      aria-label="New test case from catalog toolbar"
                      onClick={() => setShowCreate(true)}
                      className="inline-flex items-center gap-1.5 px-3 py-1.5 text-[13px] font-medium rounded-md transition-colors"
                      style={{ background: 'var(--color-btn-primary-bg)', color: 'white' }}
                    >
                      <Plus className="h-3.5 w-3.5" /> New test case
                    </button>
                  </>
                )}
              </div>
            </div>

            {casesError && !data ? (
              <div role="status" className="px-4 py-12 text-center text-sm text-[var(--color-text-muted)]">
                No test-case data is available until the request succeeds.
              </div>
            ) : isLoading ? (
              <div className="flex items-center justify-center py-12"><LoadingSpinner size="lg" /></div>
            ) : cases.length === 0 ? (
              <EmptyStateBlock
                projectId={projectId}
                onCreate={() => setShowCreate(true)}
                onAiGenerate={() => setShowAiGen(true)}
                onReset={() => { setSearch(''); setStatus(''); setTestType(''); setPriority(''); setOwnerFilter(''); setSuiteFilter(''); setSavedView(null); setPage(1) }}
              />
            ) : (
              <CasesTableBody
                cases={cases}
                onRowClick={openCase}
                onDeprecate={handleDeprecate}
                onPromoted={handleRefresh}
                lifecycleV2={lifecycleV2}
              />
            )}

            {data && data.total > 0 && (
              <CasesTableFooter
                shown={totalShown}
                total={data.total}
                pages={data.pages}
                page={data.page}
                onPage={setPage}
              />
            )}
          </div>

          {/* Automation split of the authored catalog */}
          <AutomationCoverageCard
            auto={coverageAuto}
            manual={coverageManual}
          />
        </div>

        {/* Right rail */}
        <div className="flex flex-col gap-3.5 min-w-0">
          <ReviewQueueCard
            rows={reviewQueue.slice(0, 5)}
            onPick={openCase}
          />
          {/* The three counts that used to be passed here were 15%, 8% and 50%
              of numbers already on the page. Besides being invented, they were
              wired to `disabled={count === 0}`, so a small library silently
              greyed out working generation entry points. */}
          <GenerateCasesCard onPathClick={() => projectId && setShowAiGen(true)} />
          <StrategyGapsCard gaps={strategyGaps} />
          <RecentActivityCard events={auditEvents} />
        </div>
      </div>

      <EvidenceGapLists projectId={projectId} />

      {/* Provenance */}
      <div
        className="flex items-center justify-between rounded-md text-[11.5px] text-[var(--color-text-muted)] flex-wrap gap-2"
        style={{ padding: '10px 14px', border: '1px dashed var(--color-border)', marginTop: 14 }}
      >
        {/* This row used to read "Library indexed against prd:current ·
            main@HEAD". Neither ref exists: nothing indexes a PRD, and the page
            has no git revision for the catalog. Both were fixed strings, so
            they said the same thing on every deployment of every project. The
            refresh time is real (it comes from useDataFreshness), so it is
            what remains. */}
        <span className="flex items-center gap-1.5 flex-wrap">
          <span>Library refreshed {refreshedAt}</span>
        </span>
        <button
          type="button"
          className="hover:underline inline-flex items-center gap-1"
          style={{ color: 'var(--color-accent)' }}
          onClick={() => toast('Knowledge graph viewer — coming in Phase 2', { icon: '🪪' })}
        >
          Knowledge graph <ChevronRight className="h-3 w-3" />
        </button>
      </div>

      {showCreate && projectId && (
        <CreateCaseModal
          projectId={projectId}
          onClose={() => setShowCreate(false)}
          onCreated={handleRefresh}
        />
      )}
      {showAiGen && projectId && (
        <AIGenerateModal
          projectId={projectId}
          onClose={() => setShowAiGen(false)}
        />
      )}
      {selectedCase && (
        <CaseDetailPanel
          caseItem={selectedCase}
          onClose={() => setSelectedCase(null)}
          onRefresh={handleRefresh}
          onCaseChanged={setSelectedCase}
          lifecycleV2={lifecycleV2}
        />
      )}
      {pendingDeprecation && (
        <TransitionReasonDialog
          title={lifecycleV2 ? 'Deprecate test case' : 'Delete test case'}
          description={`${lifecycleV2 ? 'Deprecate' : 'Delete'} ${pendingDeprecation.title}. The case and its version history will be preserved.`}
          confirmLabel={lifecycleV2 ? 'Deprecate' : 'Delete'}
          busy={deprecating}
          onCancel={() => setPendingDeprecation(null)}
          onConfirm={confirmDeprecation}
        />
      )}
    </>
  )
}

// ── New atoms / cards for the Test Cases redesign ──────────────────────────
// Locally-scoped to keep this redesign isolated from the other tab panels.

interface LibraryVerdictProps {
  healthScore: number
  healthTag: 'Healthy' | 'Needs attention' | 'At risk'
  healthTone: string
  totalCases: number
  reviewCount: number
  staleCount: number
  deprecatedInActive: number
  oldestReviewDays: number
  activeCount: number
  automatedPct: number
  avgAgeDays: number
  olderThan180Pct: number
  /** Set when the rates below describe a capped sample rather than the catalog. */
  statBasis: string | null
}

function LibraryVerdictRibbon(p: LibraryVerdictProps) {
  // Empty-catalog state. The Library health panel aggregates the authored
  // test-case catalog (ManagedTestCase rows from the Test Cases tab). When
  // the catalog is empty, every derived metric collapses to 0 — which
  // looks identical to "data load failed" or "everything is broken."
  // Render a clear explanation instead so the user understands the panel
  // reflects an empty *authored* catalog, NOT empty execution data.
  // Background: the user reported "invalid data" on 2026-05-15 because
  // the same project has 97 executed test cases (in /search) but 0
  // authored cases, and the panel's zeros looked wrong without context.
  const isEmptyCatalog = p.totalCases === 0
  const t = isEmptyCatalog
    ? { border: 'var(--color-border)', glow: 'transparent', bar: 'var(--color-border-light)', eyebrow: 'var(--color-text-muted)' }
    : p.healthTag === 'Healthy'
      ? { border: 'color-mix(in srgb, var(--status-passed) 40%, transparent)', glow: 'radial-gradient(120% 100% at 0% 0%, color-mix(in srgb, var(--status-passed) 10%, transparent), transparent 55%)', bar: 'var(--gate-go)', eyebrow: 'var(--status-passed)' }
      : p.healthTag === 'Needs attention'
        ? { border: 'color-mix(in srgb, var(--status-broken) 40%, transparent)', glow: 'radial-gradient(120% 100% at 0% 0%, var(--gate-conditional-bg-soft), transparent 55%)', bar: 'var(--gate-conditional)', eyebrow: 'var(--status-broken)' }
        : { border: 'color-mix(in srgb, var(--status-failed) 40%, transparent)', glow: 'radial-gradient(120% 100% at 0% 0%, color-mix(in srgb, var(--status-failed) 10%, transparent), transparent 55%)', bar: 'var(--gate-no-go)', eyebrow: 'var(--status-failed)' }

  const blockers: { tone: 'critical' | 'warn'; text: React.ReactNode }[] = []
  if (p.deprecatedInActive > 0) {
    blockers.push({ tone: 'critical', text: <><strong>{p.deprecatedInActive}</strong> deprecated cases referenced by active suites</> })
  }
  if (p.staleCount > 0) {
    blockers.push({ tone: 'warn', text: <><strong>{p.staleCount}</strong> drafts untouched ≥30d</> })
  }
  if (p.reviewCount > 0 && p.oldestReviewDays >= 7) {
    blockers.push({ tone: 'warn', text: <><strong>{p.reviewCount}</strong> reviews aging — oldest {p.oldestReviewDays} days</> })
  }

  return (
    <section
      aria-label="Library verdict"
      className="relative rounded-xl border overflow-hidden grid gap-6 mb-3.5"
      style={{
        gridTemplateColumns: '1.45fr 1fr',
        background: `${t.glow}, var(--color-bg-card)`,
        borderColor: t.border,
        padding: '18px 20px',
      }}
    >
      <span aria-hidden className="absolute left-0 top-0 bottom-0 w-[3px]" style={{ background: t.bar }} />

      <div className="min-w-0" style={{ paddingLeft: 4 }}>
        <span
          className="inline-flex items-center gap-1.5 text-[11px] font-semibold uppercase"
          style={{ color: t.eyebrow, letterSpacing: 'var(--tracking-wider)' }}
        >
          <span
            className="h-1.5 w-1.5 rounded-full"
            style={{
              background: t.bar,
              animation: !isEmptyCatalog && p.healthTag === 'At risk' ? 'testlookup-pulse 1.6s ease-out infinite' : undefined,
            }}
            aria-hidden
          />
          Library health
        </span>
        {isEmptyCatalog ? (
          <>
            <div className="text-[20px] font-semibold mt-1.5 mb-1.5 text-[var(--color-text)]">
              No authored test cases yet
            </div>
            <p className="text-[13px] m-0 max-w-[64ch]" style={{ color: 'var(--color-text-secondary)' }}>
              This panel summarises the <strong>authored</strong> test-case catalog
              (Test Cases tab) — not the execution rows ingested from CI runs.
              Library health only renders once you have at least one authored case.
              Until then, see the <code className="font-mono text-[11.5px] bg-[var(--color-bg-secondary)] border border-[var(--color-border)] px-1.5 py-px rounded-sm">Test Suites</code>
              tab for the executions that have already streamed in.
            </p>
          </>
        ) : (
          <>
            <div className="flex items-baseline gap-3 mt-1.5 mb-1.5">
              <span className="font-bold tabular-nums leading-none" style={{ fontSize: 34, color: p.healthTone, letterSpacing: '-0.02em' }}>
                {p.healthScore}
              </span>
              <span className="text-[14px] text-[var(--color-text-muted)] font-medium">/ 100</span>
              <span
                className="inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-semibold ml-1"
                style={{
                  background: p.healthTag === 'Healthy' ? 'color-mix(in srgb, var(--status-passed) 12%, transparent)' : p.healthTag === 'Needs attention' ? 'var(--gate-conditional-bg)' : 'color-mix(in srgb, var(--status-failed) 12%, transparent)',
                  border: `1px solid ${t.border}`,
                  color: p.healthTone,
                }}
              >
                {p.healthTag}
              </span>
            </div>
            <p className="text-[13px] m-0 max-w-[64ch]" style={{ color: 'var(--color-text-secondary)' }}>
              <strong style={{ color: 'var(--color-text)' }}>{p.totalCases}</strong> case{p.totalCases === 1 ? '' : 's'} · <strong style={{ color: 'var(--color-text)' }}>{p.reviewCount}</strong> awaiting review, <strong style={{ color: 'var(--color-text)' }}>{p.staleCount}</strong> stale drafts over 30 days, <strong style={{ color: 'var(--color-text)' }}>{p.deprecatedInActive}</strong> deprecated still in active suites.
            </p>
          </>
        )}
        {blockers.length > 0 && (
          <div className="flex flex-wrap gap-2 mt-3">
            {blockers.map((b, i) => (
              <span
                key={i}
                className="inline-flex items-center px-2.5 py-1 rounded-full text-[11.5px]"
                style={{
                  background: b.tone === 'critical' ? 'color-mix(in srgb, var(--status-failed) 8%, transparent)' : 'color-mix(in srgb, var(--status-broken) 8%, transparent)',
                  border: b.tone === 'critical' ? '1px solid color-mix(in srgb, var(--status-failed) 25%, transparent)' : '1px solid color-mix(in srgb, var(--status-broken) 25%, transparent)',
                  color: b.tone === 'critical' ? 'var(--status-failed)' : 'var(--status-broken)',
                }}
              >
                <span className="sr-only">{b.tone === 'critical' ? 'Warning: ' : 'Notice: '}</span>
                {b.text}
              </span>
            ))}
          </div>
        )}
      </div>

      {isEmptyCatalog ? (
        // Empty stats grid would just show "0 / 0% / 0d" three times,
        // which reads exactly like a broken data fetch. Replace with a
        // single helper card pointing the user to where they can author
        // a case so the panel does something useful.
        <div
          className="flex flex-col items-start justify-center gap-1.5"
          style={{ padding: '14px 16px' }}
        >
          <div className="text-[11px] uppercase font-medium text-[var(--color-text-muted)]" style={{ letterSpacing: 'var(--tracking-wider)' }}>
            Get started
          </div>
          <div className="text-[13px] text-[var(--color-text-secondary)] leading-snug">
            Author your first case under <strong className="text-[var(--color-text)]">Test Cases</strong> tab,
            or import a batch via the API to populate this dashboard.
          </div>
        </div>
      ) : (
        <div
          className="grid items-stretch"
          style={{ gridTemplateColumns: 'repeat(3, minmax(0, 1fr))' }}
        >
          <VerdictStat label="Active"        value={p.activeCount}                  sub={null} isFirst />
          <VerdictStat label="Automated"     value={`${p.automatedPct}%`}            sub="target 60%" />
          {/* A fourth stat, "Req coverage", stood here reading a constant 87%
              for every project. It is gone rather than replaced: there is no
              requirements data to compute a real one from, and an invented
              number is worse than an absent one. */}
          <VerdictStat label="Avg age"       value={`${p.avgAgeDays}d`}              sub={`${p.olderThan180Pct}% >180d`} isLast />
        </div>
      )}
      {p.statBasis && (
        <p
          className="text-[11px] m-0 text-[var(--color-text-muted)]"
          style={{ padding: '0 16px 12px' }}
          data-testid="library-health-stat-basis"
        >
          {p.statBasis}
        </p>
      )}
    </section>
  )
}

function VerdictStat({ label, value, sub, isFirst, isLast }: { label: string; value: React.ReactNode; sub: React.ReactNode; isFirst?: boolean; isLast?: boolean }) {
  return (
    <div
      className="flex flex-col justify-center gap-0.5"
      style={{
        padding: '14px 16px',
        borderRight: isLast ? '0' : '1px solid var(--color-border)',
        borderLeft: isFirst ? '0' : undefined,
      }}
    >
      <div className="text-[10px] uppercase font-medium text-[var(--color-text-muted)]" style={{ letterSpacing: 'var(--tracking-wider)' }}>
        {label}
      </div>
      <div className="font-bold tabular-nums leading-[1.1] text-[var(--color-text)]" style={{ fontSize: 19, letterSpacing: '-0.01em' }}>
        {value}
      </div>
      {sub && <div className="text-[10.5px] text-[var(--color-text-muted)]">{sub}</div>}
    </div>
  )
}

// ── Filter bar ──────────────────────────────────────────────────────────
interface CasesFilterBarProps {
  searchInputRef: React.RefObject<HTMLInputElement | null>
  search: string
  onSearchChange: (v: string) => void
  status: string
  onStatusChange: (v: string) => void
  testType: string
  onTypeChange: (v: string) => void
  priority: string
  onPriorityChange: (v: string) => void
  ownerFilter: string
  onOwnerChange: (v: string) => void
  suiteFilter: string
  onSuiteChange: (v: string) => void
  suiteOptions: string[]
  savedView: string | null
  savedViews: { id: 'my_drafts' | 'p0_p1' | 'unautomated'; label: string }[]
  onSavedView: (v: { id: 'my_drafts' | 'p0_p1' | 'unautomated'; label: string }) => void
  onSaveCurrent: () => void
  // "Show automation-ingested tests too" toggle — merges per-run TestCase
  // rows (dedup'd by fingerprint) into the listing alongside ManagedTestCase.
  includeAutomation: boolean
  onToggleAutomation: (v: boolean) => void
}

function CasesFilterBar({
  // Destructure props so the RefObject is a named binding. The react-hooks v7
  // `refs` rule treats every member access on a props object that *contains* a
  // ref as a ref-read-during-render; destructuring keeps the ref separate from
  // the plain values that legitimately drive the render.
  searchInputRef,
  search,
  onSearchChange,
  status,
  onStatusChange,
  testType,
  onTypeChange,
  priority,
  onPriorityChange,
  ownerFilter,
  onOwnerChange,
  suiteFilter,
  onSuiteChange,
  suiteOptions,
  savedView,
  savedViews,
  onSavedView,
  onSaveCurrent,
  includeAutomation,
  onToggleAutomation,
}: CasesFilterBarProps) {
  return (
    <div
      className="flex items-center flex-wrap gap-2 rounded-md"
      style={{
        background: 'var(--color-bg-card)',
        border: '1px solid var(--color-border)',
        padding: '10px 12px',
      }}
    >
      {/* Search */}
      <div
        className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md flex-1"
        style={{ background: 'var(--color-bg)', border: '1px solid var(--color-border)', minWidth: 240, height: 32 }}
      >
        <input
          ref={searchInputRef}
          type="search"
          value={search}
          onChange={(e) => onSearchChange(e.target.value)}
          placeholder="Search by ID, title, tags, owner, or requirement…"
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

      <SelectChip
        label="Status"
        value={status}
        options={[{ value: '', label: 'All' }, ...LIFECYCLE_STATUS_OPTIONS]}
        onChange={onStatusChange}
      />

      <SelectChip label="Type" value={testType} options={[
        { value: '', label: 'All' },
        { value: 'functional',   label: 'Functional' },
        { value: 'integration',  label: 'Integration' },
        { value: 'e2e',          label: 'E2E' },
        { value: 'regression',   label: 'Regression' },
        { value: 'smoke',        label: 'Smoke' },
        { value: 'performance',  label: 'Performance' },
        { value: 'security',     label: 'Security' },
        { value: 'usability',    label: 'Usability' },
        { value: 'api',          label: 'API' },
      ]} onChange={onTypeChange} />

      <SelectChip label="Priority" value={priority} options={[
        { value: '', label: 'Any' },
        { value: 'critical', label: 'Critical' },
        { value: 'high', label: 'High' },
        { value: 'medium', label: 'Medium' },
        { value: 'low', label: 'Low' },
      ]} onChange={onPriorityChange} />

      <SelectChip label="Owner" value={ownerFilter} options={[
        { value: '', label: 'Anyone' },
      ]} onChange={onOwnerChange} disabled title="Owner filter — coming in Phase 2" />

      <SelectChip
        label="Suite"
        value={suiteFilter}
        options={[
          { value: '', label: 'All' },
          ...suiteOptions.map(s => ({ value: s, label: s })),
        ]}
        onChange={onSuiteChange}
      />

      <span aria-hidden className="inline-block w-px h-[18px] mx-1" style={{ background: 'var(--color-border)' }} />

      <span
        className="inline-flex items-center text-[10px] uppercase font-medium text-[var(--color-text-muted)]"
        style={{ letterSpacing: 'var(--tracking-wider)' }}
      >
        Views
      </span>
      {savedViews.map(v => {
        const active = savedView === v.id
        return (
          <button
            key={v.id}
            type="button"
            onClick={() => onSavedView(v)}
            className="inline-flex items-center px-2.5 py-1 text-[12.5px] rounded-full border transition-colors"
            style={{
              background: active ? 'color-mix(in srgb, var(--status-flaky) 14%, transparent)' : 'transparent',
              borderColor: active ? 'color-mix(in srgb, var(--status-flaky) 30%, transparent)' : 'var(--color-border)',
              color: active ? 'var(--status-flaky)' : 'var(--color-text-muted)',
            }}
          >
            {v.label}
          </button>
        )
      })}
      <button
        type="button"
        onClick={onSaveCurrent}
        className="inline-flex items-center px-2.5 py-1 text-[12.5px] rounded-full border transition-colors"
        style={{ borderColor: 'var(--color-border)', color: 'var(--color-text-muted)' }}
        title="Save current filters as a view"
      >
        + Save
      </button>
    </div>
  )
}

function SelectChip({
  label, value, options, onChange, disabled, title,
}: {
  label: string
  value: string
  options: { value: string; label: string }[]
  onChange: (v: string) => void
  disabled?: boolean
  title?: string
}) {
  const active = !!value
  return (
    <span
      className="inline-flex items-center px-2.5 py-1 text-[12.5px] rounded-full border transition-colors"
      style={{
        background: active ? 'color-mix(in srgb, var(--color-accent) 10%, transparent)' : 'transparent',
        borderColor: active ? 'color-mix(in srgb, var(--color-accent) 30%, transparent)' : 'var(--color-border)',
        color: active ? 'var(--color-accent)' : 'var(--color-text-muted)',
        opacity: disabled ? 0.55 : 1,
      }}
      title={title}
    >
      <span className="font-medium mr-1">{label}:</span>
      <select
        disabled={disabled}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        aria-label={label}
        className="bg-transparent outline-none border-0 text-[12.5px] tabular-nums"
        style={{ color: 'inherit' }}
      >
        {options.map(o => (
          <option key={o.value} value={o.value} style={{ background: 'var(--color-bg-card)', color: 'var(--color-text)' }}>
            {o.label}
          </option>
        ))}
      </select>
      <span className="font-mono text-[10.5px] tabular-nums ml-1" style={{ color: active ? 'color-mix(in srgb, var(--color-accent) 65%, transparent)' : 'var(--color-text-faint)' }}>
        {value ? '1' : options.length - 1}
      </span>
    </span>
  )
}

// ── Cases table body + footer ─────────────────────────────────────────
// Exported for isolated column-structure tests (same precedent as OwnerCell).
export function CasesTableBody({
  cases, onRowClick, onDeprecate, onPromoted, lifecycleV2,
}: {
  cases: ManagedTestCase[]
  onRowClick: (c: ManagedTestCase) => void
  onDeprecate: (caseItem: ManagedTestCase, e: React.MouseEvent) => void
  onPromoted: () => void
  lifecycleV2: boolean
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
            <Th label="Test case" flex />
            <Th label="Status"     width={100} />
            <Th label="Owner"      width={122} />
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
            />
          ))}
        </tbody>
      </table>
    </div>
  )
}

function Th({ label, width, align, flex }: {
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
      }}
    >
      {label}
    </th>
  )
}

function CaseRow({
  tc,
  onRowClick,
  onDeprecate,
  onPromoted,
  lifecycleV2,
}: {
  tc: ManagedTestCase
  onRowClick: (c: ManagedTestCase) => void
  onDeprecate: (caseItem: ManagedTestCase, e: React.MouseEvent) => void
  onPromoted: () => void
  lifecycleV2: boolean
}) {
  const deprecateAllowed = lifecycleV2 ? tc.allowed_actions?.includes('deprecate') === true : true
  // Only emit the type/suite/tags tail when there is something in it — otherwise
  // the leading separator renders as an orphaned '·'.
  const hasTail = Boolean(
    (tc.test_type ?? '').trim() || tc.suite_name || (tc.tags && tc.tags.length > 0),
  )
  return (
    <tr
      style={{ borderBottom: '1px solid var(--color-border)', cursor: 'pointer' }}
      className="transition-colors hover:bg-[var(--color-bg-hover)]"
      onClick={() => onRowClick(tc)}
    >
      <td style={{ padding: '9px 12px', width: '100%', maxWidth: 0 }}>
        <div className="flex items-center gap-2 min-w-0">
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
      <td style={{ padding: '10px 12px' }}>
        <CaseStatusPill status={tc.status} />
      </td>
      <td style={{ padding: '9px 12px' }} className="min-w-0">
        <OwnerCell owner={tc.owner ?? null} userId={tc.assignee_id ?? tc.author_id ?? null} />
      </td>
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
        ) : (
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
    deprecated:       { bg: 'rgba(120,113,108,0.12)', bd: 'rgba(120,113,108,0.30)', fg: '#a8a29e', label: 'Deprecated', lt: true },
    archived:         { bg: 'rgba(120,113,108,0.08)', bd: 'rgba(120,113,108,0.22)', fg: 'var(--color-text-faint)', label: 'Archived' },
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

function CasePriorityTag({ priority }: { priority: string }) {
  const map: Record<string, { bg: string; bd: string; fg: string; label: string }> = {
    critical: { bg: 'color-mix(in srgb, var(--status-failed) 14%, transparent)',  bd: 'color-mix(in srgb, var(--status-failed) 25%, transparent)',  fg: 'var(--status-failed)', label: 'P0' },
    high:     { bg: 'color-mix(in srgb, var(--status-broken) 14%, transparent)', bd: 'color-mix(in srgb, var(--status-broken) 25%, transparent)', fg: 'var(--status-broken)', label: 'P1' },
    medium:   { bg: 'color-mix(in srgb, var(--color-accent) 10%, transparent)', bd: 'color-mix(in srgb, var(--color-accent) 22%, transparent)', fg: 'var(--color-accent)', label: 'P2' },
    low:      { bg: 'var(--color-bg-secondary)', bd: 'var(--color-border)', fg: 'var(--color-text-muted)', label: 'P3' },
  }
  const p = map[priority] ?? map.low
  return (
    <span
      className="inline-flex items-center px-1.5 py-0.5 rounded-sm text-[10.5px] font-semibold uppercase"
      style={{ background: p.bg, border: `1px solid ${p.bd}`, color: p.fg, letterSpacing: '0.04em' }}
    >
      {p.label}
    </span>
  )
}

const AVATAR_GRADIENTS = [
  'linear-gradient(135deg, #6366f1, #ec4899)',
  'linear-gradient(135deg, #06b6d4, var(--color-accent))',
  'linear-gradient(135deg, var(--status-broken), var(--status-failed))',
  'linear-gradient(135deg, var(--status-passed), #06b6d4)',
  'linear-gradient(135deg, #8b5cf6, #ec4899)',
]

function hashIntoBucket(s: string, buckets: number): number {
  let h = 0
  for (let i = 0; i < s.length; i++) h = ((h << 5) - h) + s.charCodeAt(i)
  return Math.abs(h) % buckets
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
    const grad = AVATAR_GRADIENTS[hashIntoBucket(displayOwner, AVATAR_GRADIENTS.length)]
    return (
      <span className="inline-flex items-center gap-1.5 text-[11.5px] text-[var(--color-text-secondary)]">
        <span
          aria-hidden
          className="inline-flex items-center justify-center rounded-full font-bold text-white"
          style={{ width: 18, height: 18, background: grad, fontSize: 9 }}
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
  const grad = AVATAR_GRADIENTS[hashIntoBucket(userId, AVATAR_GRADIENTS.length)]
  return (
    <span className="inline-flex items-center gap-1.5 text-[11.5px] text-[var(--color-text-secondary)]">
      <span
        aria-hidden
        className="inline-flex items-center justify-center rounded-full font-bold text-white"
        style={{ width: 18, height: 18, background: grad, fontSize: 9 }}
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

function CasesTableFooter({ shown, total, pages, page, onPage }: { shown: number; total: number; pages: number; page: number; onPage: (p: number) => void }) {
  return (
    <div
      className="flex items-center justify-between gap-3 px-4 py-2.5 text-[11.5px] text-[var(--color-text-muted)]"
      style={{ borderTop: '1px solid var(--color-border)' }}
    >
      <span>{shown} of {total} · ↑/↓ navigate · ↵ open · ⌘E bulk edit</span>
      <Pagination page={page} pages={pages} total={total} onChange={onPage} />
    </div>
  )
}

function EmptyStateBlock({
  projectId, onCreate, onAiGenerate, onReset,
}: {
  projectId: string | null
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
        {projectId && (
          <>
            <button
              onClick={onAiGenerate}
              className="inline-flex items-center gap-1.5 px-2.5 py-1.5 text-[13px] rounded-md border"
              style={{ color: 'var(--status-flaky)', borderColor: 'color-mix(in srgb, var(--status-flaky) 30%, transparent)', background: 'color-mix(in srgb, var(--status-flaky) 6%, transparent)' }}
            >
              <Sparkles className="h-3.5 w-3.5" /> AI Generate
            </button>
            <button
              onClick={onCreate}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 text-[13px] font-medium rounded-md"
              style={{ background: 'var(--color-btn-primary-bg)', color: 'white' }}
            >
              <Plus className="h-3.5 w-3.5" /> New test case
            </button>
          </>
        )}
      </div>
    </div>
  )
}

// ── Automation coverage card ────────────────────────────────────────────
//
// Was "Coverage by requirement", reading "<N> requirements tracked · <M>
// covered (87%)". TestLookup has no requirements: N was the test-case count
// relabelled, and the uncovered bucket it was measured against was 15% of that
// same count, invented in the page body. The percentage could therefore never
// be anything but 87%, and the card named a domain the product does not model.
//
// Automated-vs-manual is the split the rows actually carry, so that is what
// this card now reports, under a title that says so.
function AutomationCoverageCard({ auto, manual }: { auto: number; manual: number }) {
  const total = auto + manual
  const pct = (n: number) => (total > 0 ? Math.round((n / total) * 100) : 0)
  return (
    <CasesCardShell title="Automation coverage">
      <div className="px-4 py-3.5">
        {total === 0 ? (
          <p className="text-[12px] text-[var(--color-text-muted)] m-0">
            No authored cases yet — nothing to split.
          </p>
        ) : (
          <>
            <p className="text-[12px] text-[var(--color-text-muted)] m-0 mb-3">
              <strong className="text-[var(--color-text)] font-semibold">{total}</strong> authored case{total === 1 ? '' : 's'} · <strong className="text-[var(--color-text)] font-semibold">{auto}</strong> automated (<strong className="text-[var(--color-text)] font-semibold">{pct(auto)}%</strong>)
            </p>
            <div
              className="flex h-7 rounded-md overflow-hidden border"
              style={{ borderColor: 'var(--color-border)', background: 'var(--color-bg-secondary)' }}
              role="img"
              aria-label={`Automation coverage: ${auto} automated, ${manual} manual`}
            >
              {auto > 0 && (
                <div className="flex items-center justify-center text-[10.5px] font-semibold tabular-nums" style={{ flex: auto, background: 'color-mix(in srgb, var(--status-passed) 55%, transparent)', color: 'white' }}>
                  {auto} auto
                </div>
              )}
              {manual > 0 && (
                <div className="flex items-center justify-center text-[10.5px] font-semibold tabular-nums" style={{ flex: manual, background: 'color-mix(in srgb, var(--color-accent) 50%, transparent)', color: 'white' }}>
                  {manual} manual
                </div>
              )}
            </div>
            <div className="flex flex-wrap gap-3 mt-2 text-[11px] text-[var(--color-text-muted)]">
              <Legend color="color-mix(in srgb, var(--status-passed) 55%, transparent)" label={`Automated · ${pct(auto)}%`} />
              <Legend color="color-mix(in srgb, var(--color-accent) 50%, transparent)" label={`Manual · ${pct(manual)}%`} />
            </div>
          </>
        )}
      </div>
    </CasesCardShell>
  )
}

function Legend({ color, label }: { color: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <i aria-hidden className="inline-block w-2 h-2 rounded-sm" style={{ background: color }} />
      {label}
    </span>
  )
}

// ── Right rail cards ────────────────────────────────────────────────────
function ReviewQueueCard({ rows, onPick }: { rows: ManagedTestCase[]; onPick: (c: ManagedTestCase) => void }) {
  const now = useNow()  // captured at mount — avoids impure Date.now() in render
  return (
    <CasesCardShell title={`Review queue · ${rows.length}`} rightSlot={
      <button
        type="button"
        onClick={() => toast('Review queue viewer — coming in Phase 2', { icon: '📥' })}
        className="hover:underline"
        style={{ color: 'var(--color-accent)' }}
      >
        View all →
      </button>
    }>
      {rows.length === 0 ? (
        <div className="px-4 py-6 text-center text-[12.5px] text-[var(--color-text-secondary)]">
          Caught up — review queue is empty.
        </div>
      ) : (
        <div>
          {rows.map(c => {
            const days = Math.floor((now - new Date(c.updated_at).getTime()) / 86400000)
            const ageColor = days >= 7 ? 'var(--status-failed)' : 'var(--color-text-muted)'
            return (
              <button
                key={c.id}
                type="button"
                onClick={() => onPick(c)}
                className="grid items-center gap-2.5 w-full text-left hover:bg-[var(--color-bg-hover)] transition-colors"
                style={{
                  gridTemplateColumns: '1fr auto',
                  padding: '10px 16px',
                  borderBottom: '1px solid var(--color-border)',
                }}
              >
                <div className="min-w-0">
                  <div className="text-[12.5px] m-0 flex items-center gap-1.5">
                    <code className="font-mono text-[11px]" style={{ color: 'var(--color-accent)' }}>TC-{c.id.slice(0, 6).toUpperCase()}</code>
                    <span className="text-[var(--color-text)] truncate font-medium">{c.title}</span>
                  </div>
                  <div className="text-[10.5px] text-[var(--color-text-muted)] truncate mt-0.5">
                    {c.assignee_id ? c.assignee_id.slice(0, 8) : 'Unassigned'} · requested by {c.author_id ? c.author_id.slice(0, 8) : '—'}
                  </div>
                </div>
                <span className="text-[11px] tabular-nums" style={{ color: ageColor }}>
                  {days < 1 ? `${Math.max(1, Math.floor((now - new Date(c.updated_at).getTime()) / 3600000))}h` : `${days}d`}
                </span>
              </button>
            )
          })}
        </div>
      )}
    </CasesCardShell>
  )
}

/**
 * Entry points into AI case generation.
 *
 * Each path used to carry a backlog count and a source ref (`prd:current`,
 * `main`). None of it was measured: the counts were percentages of unrelated
 * numbers, and the page has no PRD or branch-coverage data to point at. The
 * paths are prompts for the generator, so they are described as prompts —
 * without asserting a backlog nobody counted.
 */
function GenerateCasesCard({ onPathClick }: { onPathClick: () => void }) {
  return (
    <div
      className="overflow-hidden rounded-xl"
      style={{
        background: 'radial-gradient(120% 100% at 0% 0%, color-mix(in srgb, var(--status-flaky) 6%, transparent), transparent 55%), var(--color-bg-card)',
        border: '1px solid color-mix(in srgb, var(--status-flaky) 30%, transparent)',
      }}
    >
      <div
        className="flex items-center justify-between gap-2 px-4 py-3"
        style={{ borderBottom: '1px solid var(--color-border)' }}
      >
        <h3 className="text-[13px] font-semibold m-0 inline-flex items-center gap-2" style={{ color: 'var(--status-flaky)' }}>
          <Sparkles className="h-3.5 w-3.5" />
          Generate test cases
        </h3>
        <span className="text-[11px] text-[var(--color-text-muted)]">drafts → review queue</span>
      </div>
      <div className="px-4 py-3 flex flex-col gap-2">
        <p className="text-[12px] text-[var(--color-text-muted)] m-0 mb-1">
          Pick what to generate from. All generated cases land as drafts in your review queue.
        </p>
        <GeneratePath
          title="From requirements"
          sub="describe the requirement to cover"
          onClick={onPathClick}
        />
        <GeneratePath
          title="From code paths"
          sub="point the generator at a module or path"
          onClick={onPathClick}
        />
        <GeneratePath
          title="From recent defects"
          sub="write regression cases for a defect"
          onClick={onPathClick}
        />
      </div>
    </div>
  )
}

// No `count` prop, and therefore no `disabled={count === 0}`: the counts were
// invented, and gating a working button on an invented number meant a library
// of 6 cases could not reach "From code paths" at all (round(6 * 0.08) === 0).
function GeneratePath({ title, sub, onClick }: { title: string; sub: React.ReactNode; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="grid items-center gap-2.5 rounded-md border text-left transition-colors hover:bg-[var(--color-bg-hover)]"
      style={{
        gridTemplateColumns: '30px 1fr',
        padding: '8px 12px',
        background: 'var(--color-bg)',
        borderColor: 'var(--color-border)',
      }}
    >
      <span className="inline-flex items-center justify-center rounded-full" style={{ width: 30, height: 30, background: 'color-mix(in srgb, var(--status-flaky) 16%, transparent)', color: 'var(--status-flaky)' }}>
        <Sparkles className="h-3.5 w-3.5" />
      </span>
      <div className="min-w-0">
        <div className="text-[12.5px] font-medium text-[var(--color-text)]">{title}</div>
        <div className="text-[11px] text-[var(--color-text-muted)] truncate">{sub}</div>
      </div>
    </button>
  )
}

function StrategyGapsCard({ gaps }: { gaps: { severity: 'critical' | 'warn'; title: string; sub: string; pill: string }[] }) {
  return (
    <CasesCardShell title="Strategy gaps" rightSlot={
      <button
        type="button"
        onClick={() => toast('Strategy viewer — coming in Phase 2', { icon: '🪪' })}
        className="hover:underline"
        style={{ color: 'var(--color-accent)' }}
      >
        Open strategy →
      </button>
    }>
      {gaps.length === 0 ? (
        <div className="px-4 py-6 text-center text-[12.5px] text-[var(--color-text-secondary)]">
          No gaps detected against current strategy.
        </div>
      ) : (
        <div>
          {gaps.map((g, i) => (
            <div
              key={i}
              className="grid items-center gap-2.5"
              style={{
                gridTemplateColumns: '1fr auto',
                padding: '9px 16px',
                borderBottom: i < gaps.length - 1 ? '1px solid var(--color-border)' : '0',
              }}
            >
              <div className="min-w-0">
                <div className="text-[12px] font-medium text-[var(--color-text)]">{g.title}</div>
                <div className="text-[10.5px] text-[var(--color-text-muted)]">{g.sub}</div>
              </div>
              <span
                className="inline-flex items-center px-1.5 py-0.5 rounded-full text-[10.5px] font-semibold"
                style={{
                  background: g.severity === 'critical' ? 'color-mix(in srgb, var(--status-failed) 14%, transparent)' : 'color-mix(in srgb, var(--status-broken) 14%, transparent)',
                  border: g.severity === 'critical' ? '1px solid color-mix(in srgb, var(--status-failed) 30%, transparent)' : '1px solid color-mix(in srgb, var(--status-broken) 30%, transparent)',
                  color: g.severity === 'critical' ? 'var(--status-failed)' : 'var(--status-broken)',
                }}
              >
                {g.pill}
              </span>
            </div>
          ))}
        </div>
      )}
    </CasesCardShell>
  )
}

interface RecentEvent { id: string; action?: string; actor_name?: string; entity_id?: string; created_at: string; event_type?: string }

function RecentActivityCard({ events }: { events: RecentEvent[] }) {
  const now = useNow()  // captured at mount — avoids impure Date.now() in render
  return (
    <CasesCardShell title="Recent activity" rightSlot={
      <button
        type="button"
        onClick={() => toast('Audit log viewer — coming in Phase 2', { icon: '📜' })}
        className="hover:underline"
        style={{ color: 'var(--color-accent)' }}
      >
        Audit log →
      </button>
    }>
      {events.length === 0 ? (
        <div className="px-4 py-6 text-center text-[12.5px] text-[var(--color-text-secondary)]">
          No recent activity.
        </div>
      ) : (
        <div>
          {events.map((e, i) => {
            const ms = now - new Date(e.created_at).getTime()
            const min = Math.max(1, Math.floor(ms / 60000))
            const ageLabel = min < 60 ? `${min}m` : min < 1440 ? `${Math.floor(min / 60)}h` : `${Math.floor(min / 1440)}d`
            const action = (e.action ?? e.event_type ?? 'updated').toLowerCase()
            const palette = /create|new/.test(action) ? { bg: 'color-mix(in srgb, var(--status-passed) 14%, transparent)',    fg: 'var(--status-passed)' }
              : /review|approve/.test(action)        ? { bg: 'color-mix(in srgb, var(--status-broken) 14%, transparent)',  fg: 'var(--status-broken)' }
              : /deprecate|delete/.test(action)      ? { bg: 'rgba(120,113,108,0.16)', fg: '#a8a29e' }
              : /ai|generate/.test(action)           ? { bg: 'color-mix(in srgb, var(--status-flaky) 14%, transparent)', fg: 'var(--status-flaky)' }
              : { bg: 'var(--color-bg-secondary)', fg: 'var(--color-text-muted)' }
            return (
              <div
                key={e.id || i}
                className="grid items-center gap-2.5"
                style={{
                  gridTemplateColumns: '22px 1fr auto',
                  padding: '9px 16px',
                  borderBottom: i < events.length - 1 ? '1px solid var(--color-border)' : '0',
                }}
              >
                <span className="inline-flex items-center justify-center rounded-full" style={{ width: 22, height: 22, background: palette.bg, color: palette.fg }}>
                  <FileText className="h-3 w-3" />
                </span>
                <div className="text-[12px] text-[var(--color-text-secondary)] truncate">
                  <strong className="text-[var(--color-text)] font-medium">{e.actor_name ?? 'Someone'}</strong>
                  {' '}
                  {action.replace(/_/g, ' ')}
                  {' '}
                  {e.entity_id && (
                    <code className="font-mono text-[11px]" style={{ color: 'var(--color-accent)' }}>
                      TC-{e.entity_id.slice(0, 6).toUpperCase()}
                    </code>
                  )}
                </div>
                <span className="text-[10.5px] tabular-nums text-[var(--color-text-muted)]">{ageLabel}</span>
              </div>
            )
          })}
        </div>
      )}
    </CasesCardShell>
  )
}

function CasesCardShell({
  title, rightSlot, children,
}: { title: React.ReactNode; rightSlot?: React.ReactNode; children?: React.ReactNode }) {
  return (
    <div
      className="overflow-hidden rounded-xl"
      style={{ background: 'var(--color-bg-card)', border: '1px solid var(--color-border)' }}
    >
      <div
        className="flex items-center justify-between gap-2.5 px-4 py-3"
        style={{ borderBottom: '1px solid var(--color-border)' }}
      >
        <h3 className="text-[13px] font-semibold m-0 text-[var(--color-text)]">{title}</h3>
        {rightSlot && <div className="flex items-center gap-2.5 text-[12px] text-[var(--color-text-muted)]">{rightSlot}</div>}
      </div>
      {children}
    </div>
  )
}

// ─── Plan Items Expanded View ─────────────────────────────────────────────────

interface PlanItemsViewProps { planId: string; onMutate: () => void; projectId?: string | null }

function PlanItemsView({ planId, onMutate, projectId = null }: PlanItemsViewProps) {
  const { data: items, isLoading, mutate } = usePlanItems(planId)
  const [executing, setExecuting] = useState<string | null>(null)
  const [showLinkSuite, setShowLinkSuite] = useState(false)

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
    skipped: 'text-[var(--color-text-muted)]',
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
            <th className="th text-right">Actions</th>
          </tr>
        </thead>
        <tbody>
          {(items as TestPlanItem[]).map((item, idx) => (
            <tr key={item.id} className="table-row">
              <td className="td text-[var(--color-text-muted)]">{idx + 1}</td>
              <td className="td text-[var(--color-text-secondary)]">{item.test_case_id.slice(0, 8)}…</td>
              <td className="td">
                <span className={clsx('text-xs font-medium capitalize', EXEC_COLORS[item.execution_status] ?? 'text-[var(--color-text-muted)]')}>
                  {item.execution_status.replace(/_/g, ' ')}
                </span>
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

  useEffect(() => {
    testManagementService.listSuites(projectId)
      .then(setSuites)
      .catch(() => toast.error('Failed to load suites'))
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
      let added = 0
      for (const tc of managedCases) {
        try {
          await testManagementService.addPlanItem(planId, { test_case_id: tc.id })
          added++
        } catch {
          // skip duplicates
        }
      }
      toast.success(`Linked ${added} test case${added !== 1 ? 's' : ''} from "${selectedSuite}"`)
      onLinked()
    } catch {
      toast.error('Failed to link suite')
    } finally {
      setLinking(false)
    }
  }

  return (
    <div role="dialog" aria-modal="true" aria-labelledby="tm-link-suite-title" className="fixed inset-0 z-50 flex items-center justify-center bg-[var(--color-bg)]/60" onClick={onClose}>
      <div className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl p-6 w-full max-w-md shadow-2xl" onClick={e => e.stopPropagation()}>
        <h2 id="tm-link-suite-title" className="text-base font-semibold text-[var(--color-text)] mb-1">Link Test Suite to Plan</h2>
        <p className="text-xs text-[var(--color-text-muted)] mb-4">Add all managed test cases from a suite to this test plan.</p>
        {loadingSuites ? (
          <div className="flex justify-center py-4"><LoadingSpinner size="md" /></div>
        ) : (
          <div className="space-y-3">
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
          </div>
        )}
        <div className="flex gap-3 justify-end mt-5">
          <button onClick={onClose} className="px-4 py-2 text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)]">Cancel</button>
          <button
            onClick={handleLink}
            disabled={linking || !selectedSuite}
            className="px-4 py-2 text-sm bg-[var(--color-btn-primary-bg)] hover:bg-[var(--color-btn-primary-hover)] disabled:opacity-50 text-[var(--color-btn-primary-text)] rounded-lg font-medium flex items-center gap-2"
          >
            {linking ? <LoadingSpinner size="sm" /> : <Layers className="h-4 w-4" />}
            {linking ? 'Linking…' : 'Link Suite'}
          </button>
        </div>
      </div>
    </div>
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

      {showCreate && projectId && (
        <CreatePlanModal
          projectId={projectId}
          onClose={() => setShowCreate(false)}
          onCreated={mutate}
        />
      )}
    </>
  )
}

// ─── Tab: Strategy ────────────────────────────────────────────────────────────

interface StrategyTabProps { projectId: string | null }

// Hoisted to module scope (was defined inside StrategyTab) so it isn't a
// component re-created every render — react-hooks/static-components. The
// accordion open-state is passed in rather than closed over.
function AccordionSection({
  id, title, expandedSection, setExpandedSection, children,
}: {
  id: string
  title: string
  expandedSection: string | null
  setExpandedSection: (v: string | null) => void
  children: React.ReactNode
}) {
  return (
    <div className="border border-[var(--color-border)] rounded-lg overflow-hidden">
      <button
        className="w-full flex items-center justify-between p-4 text-left hover:bg-[var(--color-bg-hover)]/50 transition-colors"
        onClick={() => setExpandedSection(expandedSection === id ? null : id)}
      >
        <span className="text-sm font-medium text-[var(--color-text)]">{title}</span>
        {expandedSection === id
          ? <ChevronUp className="h-4 w-4 text-[var(--color-text-muted)]" />
          : <ChevronRight className="h-4 w-4 text-[var(--color-text-muted)]" />
        }
      </button>
      {expandedSection === id && (
        <div className="p-4 border-t border-[var(--color-border)] bg-[var(--color-bg-secondary)]/40">
          {children}
        </div>
      )}
    </div>
  )
}

function StrategyTab({ projectId }: StrategyTabProps) {
  const [showGenerate, setShowGenerate] = useState(false)
  const [expandedSection, setExpandedSection] = useState<string | null>('objective')
  const [updatingStatus, setUpdatingStatus] = useState(false)
  const [downloadingFormat, setDownloadingFormat] = useState<'word' | 'pdf' | null>(null)
  const { data: strategies, isLoading, mutate } = useStrategies()

  const strategy = strategies?.[0] as TestStrategy | undefined

  const handleStatusChange = async (newStatus: string) => {
    if (!strategy) return
    setUpdatingStatus(true)
    try {
      await testManagementService.updateStrategy(strategy.id, { status: newStatus })
      mutate()
      toast.success('Strategy status updated')
    } catch {
      toast.error('Failed to update status')
    } finally {
      setUpdatingStatus(false)
    }
  }

  const handleStrategyDownload = async (format: 'word' | 'pdf') => {
    if (!strategy) return
    setDownloadingFormat(format)
    try {
      if (format === 'word') {
        await testManagementService.downloadStrategyWord(strategy.id)
      } else {
        await testManagementService.downloadStrategyPdf(strategy.id)
      }
    } catch {
      toast.error(`Failed to export strategy as ${format.toUpperCase()}`)
    } finally {
      setDownloadingFormat(null)
    }
  }

  return (
    <>
      <div className="space-y-4">
        {projectId && (
          <div className="flex items-center justify-end">
            <button onClick={() => setShowGenerate(true)} className="btn-primary flex items-center gap-2">
              <Sparkles className="h-4 w-4" /> Generate Strategy
            </button>
          </div>
        )}

        {isLoading ? (
          <div className="flex items-center justify-center h-48"><LoadingSpinner size="lg" /></div>
        ) : !strategy ? (
          <EmptyState
            icon={<BarChart2 className="h-10 w-10" />}
            title="No test strategy"
            description={projectId ? "Generate a comprehensive AI-powered test strategy for your project" : "No test strategies found across all projects"}
            action={projectId ? (
              <button onClick={() => setShowGenerate(true)} className="btn-primary flex items-center gap-2">
                <Sparkles className="h-4 w-4" /> Generate Strategy
              </button>
            ) : undefined}
          />
        ) : (
          <div className="space-y-3">
            {/* Header card */}
            <div className="card">
              <div className="flex items-start justify-between">
                <div>
                  <h3 className="text-base font-semibold text-[var(--color-text)]">{strategy.name}</h3>
                  <div className="flex items-center gap-3 mt-1">
                    <select
                      value={strategy.status}
                      onChange={e => handleStatusChange(e.target.value)}
                      disabled={updatingStatus}
                      className="bg-[var(--color-bg-secondary)] border border-[var(--color-border)] text-[var(--color-text-secondary)] text-xs rounded px-2 py-1 focus:outline-none focus:ring-1 focus:ring-[var(--color-ring)]"
                    >
                      {['draft', 'active', 'suspended', 'closed', 'review', 'approved'].map(s => (
                        <option key={s} value={s} className="capitalize">{s.charAt(0).toUpperCase() + s.slice(1)}</option>
                      ))}
                    </select>
                    <span className="text-xs text-[var(--color-text-muted)]">v{strategy.version_label}</span>
                    {strategy.ai_generated && (
                      <span className="text-xs text-[var(--color-purple)] flex items-center gap-1">
                        <Sparkles className="h-3 w-3" /> AI Generated
                      </span>
                    )}
                    <span className="text-xs text-[var(--color-text-muted)]">Created {fmtDate(strategy.created_at)}</span>
                  </div>
                </div>
                <div className="flex items-center gap-2 flex-shrink-0">
                  <button
                    onClick={() => handleStrategyDownload('word')}
                    disabled={downloadingFormat !== null}
                    className="btn-secondary flex items-center gap-1.5 text-xs whitespace-nowrap"
                    title="Export strategy to Word"
                  >
                    <Download className="h-3.5 w-3.5" />
                    {downloadingFormat === 'word' ? 'Exporting…' : 'Word'}
                  </button>
                  <button
                    onClick={() => handleStrategyDownload('pdf')}
                    disabled={downloadingFormat !== null}
                    className="btn-secondary flex items-center gap-1.5 text-xs whitespace-nowrap"
                    title="Export strategy to PDF"
                  >
                    <FileText className="h-3.5 w-3.5" />
                    {downloadingFormat === 'pdf' ? 'Exporting…' : 'PDF'}
                  </button>
                </div>
              </div>
            </div>

            {/* Accordion sections */}
            {strategy.objective && (
              <AccordionSection id="objective" title="Objective" expandedSection={expandedSection} setExpandedSection={setExpandedSection}>
                <p className="text-sm text-[var(--color-text-secondary)] whitespace-pre-wrap">{strategy.objective}</p>
              </AccordionSection>
            )}
            {(strategy.scope || strategy.out_of_scope) && (
              <AccordionSection id="scope" title="Scope" expandedSection={expandedSection} setExpandedSection={setExpandedSection}>
                {strategy.scope && (
                  <div className="mb-3">
                    <p className="text-xs text-[var(--color-text-muted)] uppercase tracking-wider mb-1">In Scope</p>
                    <p className="text-sm text-[var(--color-text-secondary)] whitespace-pre-wrap">{strategy.scope}</p>
                  </div>
                )}
                {strategy.out_of_scope && (
                  <div>
                    <p className="text-xs text-[var(--color-text-muted)] uppercase tracking-wider mb-1">Out of Scope</p>
                    <p className="text-sm text-[var(--color-text-secondary)] whitespace-pre-wrap">{strategy.out_of_scope}</p>
                  </div>
                )}
              </AccordionSection>
            )}
            {strategy.test_types && strategy.test_types.length > 0 && (
              <AccordionSection id="test_types" title="Test Types" expandedSection={expandedSection} setExpandedSection={setExpandedSection}>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr>
                        <th className="th text-left">Type</th>
                        <th className="th text-left">Priority</th>
                        <th className="th text-left">Tools</th>
                        <th className="th text-right">Coverage Target</th>
                      </tr>
                    </thead>
                    <tbody>
                      {strategy.test_types.map((tt, i) => (
                        <tr key={i} className="table-row">
                          <td className="td font-medium text-[var(--color-text)] capitalize">{tt.type}</td>
                          <td className="td"><StatusPill status={tt.priority} map={PRIORITY_COLORS} /></td>
                          <td className="td text-[var(--color-text-muted)]">{tt.tools.join(', ')}</td>
                          <td className="td text-right tabular-nums text-[var(--color-text)]">{tt.coverage_target_pct}%</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </AccordionSection>
            )}
            {strategy.risk_assessment && strategy.risk_assessment.length > 0 && (
              <AccordionSection id="risks" title="Risk Assessment" expandedSection={expandedSection} setExpandedSection={setExpandedSection}>
                <div className="space-y-2">
                  {strategy.risk_assessment.map((r, i) => (
                    <div key={i} className="bg-[var(--color-bg-secondary)] rounded-lg p-3 grid grid-cols-2 gap-3 text-sm">
                      <div className="col-span-2 font-medium text-[var(--color-text)]">{r.risk}</div>
                      <div><span className="text-xs text-[var(--color-text-muted)]">Likelihood: </span><span className="text-[var(--color-text-secondary)] capitalize">{r.likelihood}</span></div>
                      <div><span className="text-xs text-[var(--color-text-muted)]">Impact: </span><span className="text-[var(--color-text-secondary)] capitalize">{r.impact}</span></div>
                      <div className="col-span-2 text-xs text-[var(--color-text-muted)]"><span className="text-[var(--color-text-muted)]">Mitigation: </span>{r.mitigation}</div>
                    </div>
                  ))}
                </div>
              </AccordionSection>
            )}
            {(strategy.entry_criteria?.length || strategy.exit_criteria?.length) && (
              <AccordionSection id="criteria" title="Entry / Exit Criteria" expandedSection={expandedSection} setExpandedSection={setExpandedSection}>
                <div className="grid grid-cols-2 gap-4">
                  {strategy.entry_criteria && strategy.entry_criteria.length > 0 && (
                    <div>
                      <p className="text-xs text-[var(--color-text-muted)] uppercase tracking-wider mb-2">Entry Criteria</p>
                      <ul className="space-y-1">
                        {strategy.entry_criteria.map((c, i) => (
                          <li key={i} className="flex items-center gap-2 text-xs text-[var(--color-text-secondary)]">
                            <CheckCircle2 className="h-3.5 w-3.5 text-[var(--status-passed)] flex-shrink-0" />{c}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                  {strategy.exit_criteria && strategy.exit_criteria.length > 0 && (
                    <div>
                      <p className="text-xs text-[var(--color-text-muted)] uppercase tracking-wider mb-2">Exit Criteria</p>
                      <ul className="space-y-1">
                        {strategy.exit_criteria.map((c, i) => (
                          <li key={i} className="flex items-center gap-2 text-xs text-[var(--color-text-secondary)]">
                            <CheckCircle2 className="h-3.5 w-3.5 text-[var(--color-text-secondary)] flex-shrink-0" />{c}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
              </AccordionSection>
            )}
            {strategy.environments && strategy.environments.length > 0 && (
              <AccordionSection id="envs" title="Test Environments" expandedSection={expandedSection} setExpandedSection={setExpandedSection}>
                <div className="grid grid-cols-3 gap-3">
                  {strategy.environments.map((env, i) => (
                    <div key={i} className="bg-[var(--color-bg-secondary)] rounded-lg p-3">
                      <p className="text-sm font-medium text-[var(--color-text)]">{env.name}</p>
                      <p className="text-xs text-[var(--color-text-muted)] capitalize mt-0.5">{env.type}</p>
                      <p className="text-xs text-[var(--color-text-muted)] mt-1">{env.purpose}</p>
                    </div>
                  ))}
                </div>
              </AccordionSection>
            )}
            {strategy.automation_approach && (
              <AccordionSection id="automation" title="Automation Approach" expandedSection={expandedSection} setExpandedSection={setExpandedSection}>
                <p className="text-sm text-[var(--color-text-secondary)] whitespace-pre-wrap">{strategy.automation_approach}</p>
              </AccordionSection>
            )}
          </div>
        )}
      </div>

      {showGenerate && projectId && (
        <GenerateStrategyModal
          projectId={projectId}
          onClose={() => { setShowGenerate(false); mutate() }}
        />
      )}
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
}

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
    if (suitesError) toast.error('Failed to load test suites')
  }, [suitesError])
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
    void loadSuiteData(deepLinkSuite)
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
      toast.error('Failed to load suite cases')
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
    skipped: 'text-[var(--color-text-muted)]',
    pending: 'text-[var(--status-broken)]',
  }

  // Add-suite handler. Refreshes the list on success so the user sees their
  // new suite immediately (the legacy aggregated listSuites endpoint still
  // groups by suite_name — first-class TestSuite rows show up once any test
  // case is ingested for that name, OR you can extend listSuites to merge
  // first-class rows; that's a follow-up).
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
    // Refetch suites so the new one (if it has test cases yet) shows up.
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
          className="inline-flex items-center gap-1.5 text-xs bg-[var(--color-btn-primary-bg)] hover:bg-[var(--color-btn-primary-hover)] text-[var(--color-btn-primary-text)] px-3 py-1.5 rounded-lg font-medium"
        >
          <Plus className="h-3.5 w-3.5" />
          Add test suite
        </button>
      )}
    </div>
  )

  if (suites.length === 0) {
    return (
      <>
        {suitesHeader}
        <EmptyState
          icon={<Layers className="h-10 w-10" />}
          title="No test suites found"
          description={projectId ? 'Test suites appear here when automation runs ingest test results grouped by suite, or when manual test cases are assigned a suite name.' : 'No test suites found across all projects'}
        />
        {showAddSuite && projectId && (
          <AddTestSuiteModal
            projectId={projectId}
            ownerCandidates={ownerCandidates}
            existingNames={new Set(suites.map(s => s.suite_name))}
            onClose={() => setShowAddSuite(false)}
            onCreate={handleCreateSuite}
          />
        )}
      </>
    )
  }

  return (
    <>
      {suitesHeader}
      {showAddSuite && projectId && (
        <AddTestSuiteModal
          projectId={projectId}
          ownerCandidates={ownerCandidates}
          existingNames={new Set(suites.map(s => s.suite_name))}
          onClose={() => setShowAddSuite(false)}
          onCreate={handleCreateSuite}
        />
      )}
    <div className="space-y-3">
      {suites.map(suite => {
        const review = reviewForSuite(suite)
        const reviewState: SuiteReviewState = review?.state ?? 'pending'
        const reviewStyle = REVIEW_STATE_STYLES[reviewState]
        const isEditingOwner = editingOwnerFor === suite.suite_name
        return (
        <div
          key={suite.suite_name}
          id={`suite-card-${suite.suite_name}`}
          className={clsx(
            'card p-0',
            deepLinkSuite === suite.suite_name && 'ring-2 ring-[var(--color-accent)] ring-offset-1 ring-offset-[var(--color-bg)]',
          )}
        >
          <div
            className="p-4 cursor-pointer hover:bg-[var(--color-bg-hover)]/50 transition-colors rounded-xl"
            onClick={() => handleExpandSuite(suite.suite_name)}
          >
            <div className="flex items-center justify-between gap-3">
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-3 mb-1">
                  <Layers className="h-4 w-4 text-[var(--color-text)] flex-shrink-0" />
                  <h3 className="text-sm font-semibold text-[var(--color-text)] truncate">{suite.suite_name}</h3>
                  <span className={clsx('text-[10px] px-1.5 py-0.5 rounded font-medium', reviewStyle.cls)}>
                    {reviewStyle.label}
                  </span>
                </div>
                <div className="flex items-center gap-4 text-xs text-[var(--color-text-muted)] flex-wrap">
                {(() => {
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
                  <Link
                    to={`/coverage/suite?name=${encodeURIComponent(suite.suite_name)}&days=30`}
                    className="text-[var(--color-accent)] hover:underline text-[11px]"
                    onClick={e => e.stopPropagation()}
                    title="View per-day trend"
                  >
                    Trend →
                  </Link>
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
                {suite.last_run_id && (
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
              {expandedSuite === suite.suite_name
                ? <ChevronUp className="h-4 w-4 text-[var(--color-text-muted)] flex-shrink-0" />
                : <ChevronDown className="h-4 w-4 text-[var(--color-text-muted)] flex-shrink-0" />
              }
            </div>
          </div>
          {expandedSuite === suite.suite_name && (
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

  return (
    <div role="dialog" aria-modal="true" aria-labelledby="tm-add-suite-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-[var(--color-bg)]/60"
      onClick={onClose}
    >
      <div
        className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl p-6 w-full max-w-lg shadow-2xl"
        onClick={e => e.stopPropagation()}
      >
        <h2 id="tm-add-suite-title" className="text-base font-semibold text-[var(--color-text)] mb-1">Add test suite</h2>
        <p className="text-xs text-[var(--color-text-muted)] mb-4">
          Author a new suite definition. The suite name must be unique within the project.
        </p>
        <div className="space-y-3">
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

        <div className="flex gap-3 justify-end mt-5">
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
            className="px-4 py-2 text-sm bg-[var(--color-btn-primary-bg)] hover:bg-[var(--color-btn-primary-hover)] disabled:opacity-50 text-[var(--color-btn-primary-text)] rounded-lg font-medium flex items-center gap-2"
          >
            {saving ? <LoadingSpinner size="sm" /> : <Plus className="h-4 w-4" />}
            {saving ? 'Creating…' : 'Create suite'}
          </button>
        </div>
      </div>
    </div>
  )
}

// ─── Tab: Reviews ─────────────────────────────────────────────────────────────

interface ReviewsTabProps { projectId: string | null; lifecycleV2: boolean }
type ReviewQueueSource = 'requested' | 'claimed'

export function ReviewsTab({ projectId: _projectId, lifecycleV2 }: ReviewsTabProps) {
  const [reviewingId, setReviewingId] = useState<string | null>(null)
  const [transitioning, setTransitioning] = useState<{ caseId: string; action: TestCaseTransitionAction } | null>(null)
  const [pendingDecision, setPendingDecision] = useState<{ caseItem: ManagedTestCase; action: TestCaseTransitionAction } | null>(null)
  const [reviewOverrides, setReviewOverrides] = useState<Map<string, ManagedTestCase>>(() => new Map())
  const [locallyAiReviewedIds, setLocallyAiReviewedIds] = useState<Set<string>>(() => new Set())
  const [queueRefreshWarning, setQueueRefreshWarning] = useState<string | null>(null)
  const requestedQuery = useTestCases({ status: 'review_requested', size: 50 })
  const claimedQuery = useTestCases({ status: 'under_review', size: 50 })

  const entriesById = new Map<string, { caseItem: ManagedTestCase; source: ReviewQueueSource }>()
  for (const caseItem of requestedQuery.data?.items ?? []) {
    entriesById.set(caseItem.id, { caseItem, source: 'requested' })
  }
  for (const caseItem of claimedQuery.data?.items ?? []) {
    entriesById.set(caseItem.id, { caseItem, source: 'claimed' })
  }
  for (const updated of reviewOverrides.values()) {
    if (updated.status === 'review_requested') {
      entriesById.set(updated.id, { caseItem: updated, source: 'requested' })
    } else if (updated.status === 'under_review') {
      entriesById.set(updated.id, { caseItem: updated, source: 'claimed' })
    } else {
      entriesById.delete(updated.id)
    }
  }
  const reviewEntries = Array.from(entriesById.values())
  const isLoading = requestedQuery.isLoading || claimedQuery.isLoading
  const reviewError = requestedQuery.error ?? claimedQuery.error

  const mutateReviews = () => Promise.all([requestedQuery.mutate(), claimedQuery.mutate()])

  async function retryReviews() {
    setQueueRefreshWarning(null)
    try {
      await mutateReviews()
    } catch {
      setQueueRefreshWarning('The review queue is still stale. Actions remain unavailable for rows from the failed source.')
    }
  }

  const handleAiReview = async (tc: ManagedTestCase) => {
    setReviewingId(tc.id)
    setQueueRefreshWarning(null)
    try {
      await testManagementService.aiReview(tc.id)
    } catch {
      toast.error('AI review failed')
      setReviewingId(null)
      return
    }

    setLocallyAiReviewedIds((current) => new Set(current).add(tc.id))
    setReviewingId(null)
    toast.success('AI review complete')
    try {
      await mutateReviews()
    } catch {
      setQueueRefreshWarning('The AI review completed, but the review queue could not be refreshed. The completed control remains disabled locally.')
    }
  }

  const handleReviewAction = async (
    tc: ManagedTestCase,
    action: TestCaseTransitionAction,
    notes?: string,
  ) => {
    setTransitioning({ caseId: tc.id, action })
    setQueueRefreshWarning(null)
    let updated: ManagedTestCase
    try {
      if (lifecycleV2) {
        updated = await testManagementService.transitionCase(tc.id, {
          action,
          expected_version: tc.version,
          ...(notes ? { notes } : {}),
        })
      } else {
        updated = await testManagementService.reviewAction(tc.id, action, notes)
      }
    } catch {
      toast.error('Action failed')
      setTransitioning(null)
      return
    }

    setReviewOverrides((current) => new Map(current).set(updated.id, updated))
    setPendingDecision(null)
    setTransitioning(null)
    toast.success(`Test case ${action.replace(/_/g, ' ')}`)
    try {
      await mutateReviews()
    } catch {
      setQueueRefreshWarning('The review action was saved, but the queue could not be refreshed. The returned case state is shown locally.')
    }
  }

  function chooseReviewAction(tc: ManagedTestCase, action: TestCaseTransitionAction) {
    if (REVIEW_DECISION_ACTIONS.has(action)) {
      setPendingDecision({ caseItem: tc, action })
      return
    }
    void handleReviewAction(tc, action)
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-[var(--color-text-muted)]">{reviewEntries.length} test cases awaiting review</p>
      </div>

      {reviewError && (
        <div role="alert" className="flex items-center justify-between gap-3 rounded-lg border border-[var(--status-failed-bd)] bg-[var(--status-failed-bg)] p-3 text-sm text-[var(--status-failed)]">
          <span>
            The review queue could not be loaded completely. Any available cases are shown below.
          </span>
          <button type="button" className="btn-secondary flex-shrink-0 text-xs" onClick={() => void retryReviews()}>
            Retry both lists
          </button>
        </div>
      )}

      {queueRefreshWarning && (
        <p role="status" className="rounded-lg border border-[var(--status-broken-bd)] bg-[var(--status-broken-bg)] p-3 text-sm text-[var(--status-broken)]">
          {queueRefreshWarning}
        </p>
      )}

      {isLoading && reviewEntries.length === 0 ? (
        <div className="flex items-center justify-center h-48"><LoadingSpinner size="lg" /></div>
      ) : reviewEntries.length === 0 && !reviewError ? (
        <EmptyState
          icon={<Shield className="h-10 w-10" />}
          title="No pending reviews"
          description="Test cases submitted for review will appear here"
        />
      ) : (
        <div className="space-y-3">
          {reviewEntries.map(({ caseItem: tc, source }) => {
            const sourceFailed = source === 'requested' ? !!requestedQuery.error : !!claimedQuery.error
            const aiReviewSavedLocally = locallyAiReviewedIds.has(tc.id)
            return (
            <div key={tc.id} className="card">
              <div className="flex items-start gap-4">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <h4 className="text-sm font-medium text-[var(--color-text)] truncate">{tc.title}</h4>
                    <StatusPill status={tc.priority} map={PRIORITY_COLORS} />
                    <span className="text-xs text-[var(--color-text-muted)] capitalize">{tc.test_type}</span>
                  </div>
                  {tc.feature_area && <p className="text-xs text-[var(--color-text-muted)] mb-2">{tc.feature_area}</p>}
                  {tc.objective && <p className="text-xs text-[var(--color-text-muted)] line-clamp-2">{tc.objective}</p>}
                  <div className="flex items-center gap-4 mt-2">
                    <div className="flex items-center gap-1.5 text-xs text-[var(--color-text-muted)]">
                      <Clock className="h-3.5 w-3.5" />
                      Requested {fmtDate(tc.updated_at)}
                    </div>
                    {tc.ai_quality_score != null && (
                      <div className="flex items-center gap-1.5 text-xs text-[var(--color-text-muted)]">
                        <Star className="h-3.5 w-3.5" />
                        AI Score: <QualityScore score={tc.ai_quality_score} />
                      </div>
                    )}
                    {tc.steps && (
                      <span className="text-xs text-[var(--color-text-muted)]">{tc.steps.length} steps</span>
                    )}
                  </div>
                </div>
                <div className="flex items-center gap-2 flex-shrink-0 flex-wrap justify-end">
                  {sourceFailed ? (
                    <span className="text-xs text-[var(--status-broken)]">Actions unavailable until this review list refreshes.</span>
                  ) : (
                    <>
                    <button
                      onClick={() => handleAiReview(tc)}
                      disabled={reviewingId === tc.id || aiReviewSavedLocally}
                      className="btn-secondary flex items-center gap-1.5 text-xs"
                    >
                      {reviewingId === tc.id ? <LoadingSpinner size="sm" /> : <Sparkles className="h-3.5 w-3.5" />}
                      {aiReviewSavedLocally ? 'AI reviewed' : 'AI Review'}
                    </button>
                    {(lifecycleV2 ? (tc.allowed_actions ?? []) : LEGACY_REVIEW_ACTIONS).map((action) => {
                    const label = REVIEW_ACTION_LABELS[action]
                    if (!label) return null
                    const saving = transitioning?.caseId === tc.id && transitioning.action === action
                    return (
                      <button
                        key={action}
                        type="button"
                        disabled={transitioning !== null}
                        onClick={() => chooseReviewAction(tc, action)}
                        className="btn-secondary text-xs"
                      >
                        {saving ? 'Saving…' : label}
                      </button>
                    )
                    })}
                    </>
                  )}
                </div>
              </div>
            </div>
            )
          })}
        </div>
      )}

      {pendingDecision && (
        <TransitionReasonDialog
          title={REVIEW_ACTION_LABELS[pendingDecision.action] ?? 'Record review decision'}
          description="Record a nonblank review note for the audit trail."
          confirmLabel={REVIEW_ACTION_LABELS[pendingDecision.action] ?? 'Submit'}
          fieldLabel="Review notes"
          placeholder="Explain the review decision"
          busy={transitioning?.caseId === pendingDecision.caseItem.id}
          onCancel={() => setPendingDecision(null)}
          onConfirm={(notes) => handleReviewAction(pendingDecision.caseItem, pendingDecision.action, notes)}
        />
      )}
    </div>
  )
}

// ─── Tab: Audit Log ───────────────────────────────────────────────────────────

interface AuditTabProps { projectId: string | null }

function AuditTab({ projectId: _projectId }: AuditTabProps) {
  const [page, setPage] = useState(1)
  const [entityType, setEntityType] = useState('')

  const { data, isLoading } = useAuditLog({
    page,
    size: 25,
    entity_type: entityType || undefined,
  })

  const entries = data?.items ?? []

  const ACTION_COLORS: Record<string, string> = {
    created:          'text-[var(--status-passed)]',
    updated:          'text-[var(--color-text)]',
    deleted:          'text-[var(--status-failed)]',
    status_changed:   'text-[var(--status-broken)]',
    review_requested: 'text-[var(--color-purple)]',
    approved:         'text-[var(--status-passed)]',
    rejected:         'text-[var(--status-failed)]',
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <select className="input" value={entityType} onChange={e => { setEntityType(e.target.value); setPage(1) }}>
          <option value="">All Entity Types</option>
          {['test_case','test_plan','test_strategy','test_case_review'].map(t => (
            <option key={t} value={t}>{t.replace(/_/g, ' ')}</option>
          ))}
        </select>
      </div>

      <div className="card p-0">
        {isLoading ? (
          <div className="flex items-center justify-center h-48"><LoadingSpinner size="lg" /></div>
        ) : entries.length === 0 ? (
          <EmptyState
            icon={<History className="h-10 w-10" />}
            title="No audit log entries"
            description="All changes to test cases, plans and strategies are tracked here"
          />
        ) : (
          <>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr>
                    <th className="th text-left">Timestamp</th>
                    <th className="th text-left">Entity Type</th>
                    <th className="th text-left">Action</th>
                    <th className="th text-left">Actor</th>
                    <th className="th text-left">Details</th>
                  </tr>
                </thead>
                <tbody>
                  {entries.map(entry => (
                    <tr key={entry.id} className="table-row">
                      <td className="td text-[var(--color-text-muted)] whitespace-nowrap text-xs">{fmtDateTime(entry.created_at)}</td>
                      <td className="td">
                        <span className="text-xs text-[var(--color-text-muted)] capitalize">{entry.entity_type.replace(/_/g, ' ')}</span>
                      </td>
                      <td className="td">
                        <span className={clsx('text-xs font-medium capitalize', ACTION_COLORS[entry.action] ?? 'text-[var(--color-text-muted)]')}>
                          {entry.action.replace(/_/g, ' ')}
                        </span>
                      </td>
                      <td className="td">
                        <div className="flex items-center gap-1.5 text-xs text-[var(--color-text-muted)]">
                          <User className="h-3.5 w-3.5" />
                          {entry.actor_name ?? entry.actor_id?.slice(0, 8) ?? 'System'}
                        </div>
                      </td>
                      <td className="td text-xs text-[var(--color-text-muted)] max-w-xs truncate">{entry.details ?? '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {data && <Pagination page={data.page} pages={data.pages} total={data.total} onChange={setPage} />}
          </>
        )}
      </div>
    </div>
  )
}

// ─── Tab: Duplicates (Phase 4) ────────────────────────────────────────────────

const DUP_BAND_COLORS: Record<DuplicateBand, string> = {
  exact:    'bg-[var(--status-failed-bg)] text-[var(--status-failed)] border border-[var(--status-failed-bd)]',
  strong:   'bg-[var(--status-broken-bg)] text-[var(--status-broken)] border border-[var(--status-broken-bd)]',
  possible: 'bg-[var(--status-broken-bg)] text-[var(--status-broken)] border border-[var(--status-broken-bd)]',
}

const DUP_METHOD_LABEL: Record<string, string> = {
  fingerprint: 'Fingerprint',
  structural:  'Structural',
  semantic:    'Semantic',
}

function DupBandBadge({ band }: { band: DuplicateBand }) {
  return (
    <span className={clsx('inline-flex items-center px-2 py-0.5 rounded-full text-xs font-semibold capitalize', DUP_BAND_COLORS[band])}>
      {band}
    </span>
  )
}

function fmtScore(score: number) {
  // Service emits a 0..1 ratio; surface as a percentage.
  return `${Math.round(score * 100)}%`
}

interface DupCaseCardProps { ref_: DuplicateCandidate['case_a']; label: string }
function DupCaseCard({ ref_, label }: DupCaseCardProps) {
  return (
    <div className="flex-1 min-w-0 bg-[var(--color-bg-secondary)] rounded-lg p-3 border border-[var(--color-border)]">
      <p className="text-[10px] uppercase tracking-wider text-[var(--color-text-faint)] mb-1">{label}</p>
      <p className="text-sm font-medium text-[var(--color-text)] leading-snug break-words">{ref_.title}</p>
      <div className="flex items-center gap-2 mt-1.5 flex-wrap">
        {ref_.suite_name && (
          <span className="inline-flex items-center gap-1 text-xs text-[var(--color-text-muted)]">
            <Layers className="h-3 w-3" /> {ref_.suite_name}
          </span>
        )}
        {ref_.status && <StatusPill status={ref_.status} map={STATUS_COLORS} />}
      </div>
    </div>
  )
}

interface DuplicateCandidateCardProps {
  projectId: string
  candidate: DuplicateCandidate
  /** Optimistically drop this candidate from the current view, then revalidate. */
  onResolved: (candidateId: string) => Promise<unknown>
}

export function DuplicateCandidateCard({ projectId, candidate, onResolved }: DuplicateCandidateCardProps) {
  const { isQaLead } = usePermissions()
  const [busy, setBusy] = useState<'dismiss' | 'merge' | null>(null)
  const [resolvedLocally, setResolvedLocally] = useState<'dismissed' | 'merged' | null>(null)
  const [refreshWarning, setRefreshWarning] = useState<string | null>(null)
  // Default to keeping case_a; user can flip before merging.
  const [keepCaseId, setKeepCaseId] = useState<string>(candidate.case_a.id)

  const componentScores = candidate.component_scores ?? {}
  const componentEntries = Object.entries(componentScores)

  const handleDismiss = async () => {
    setBusy('dismiss')
    setRefreshWarning(null)
    try {
      await testManagementService.dismissDuplicate(projectId, candidate.id)
    } catch {
      toast.error('Failed to dismiss pair')
      setBusy(null)
      return
    }

    setResolvedLocally('dismissed')
    setBusy(null)
    toast.success('Pair dismissed — it won’t resurface')
    try {
      await onResolved(candidate.id)
    } catch {
      setRefreshWarning('The pair was dismissed, but the duplicate queue could not be refreshed. Actions remain disabled locally.')
    }
  }

  const handleMerge = async () => {
    setBusy('merge')
    setRefreshWarning(null)
    try {
      await testManagementService.mergeDuplicate(projectId, candidate.id, {
        candidate_id: candidate.id,
        keep_case_id: keepCaseId,
        deprecate_loser: true,
      })
    } catch {
      toast.error('Failed to merge pair')
      setBusy(null)
      return
    }

    setResolvedLocally('merged')
    setBusy(null)
    toast.success('Pair merged — losing case soft-deprecated')
    try {
      await onResolved(candidate.id)
    } catch {
      setRefreshWarning('The pair was merged, but the duplicate queue could not be refreshed. Merge remains disabled locally.')
    }
  }

  return (
    <div className="rounded-xl p-4" style={{ background: 'var(--color-bg-card)', border: '1px solid var(--color-border)' }}>
      {/* Header: band + method + score */}
      <div className="flex items-center justify-between gap-2 mb-3 flex-wrap">
        <div className="flex items-center gap-2 flex-wrap">
          <DupBandBadge band={candidate.band} />
          <span className="text-xs text-[var(--color-text-muted)]">
            {DUP_METHOD_LABEL[candidate.method] ?? candidate.method}
          </span>
        </div>
        <span className="text-sm font-semibold tabular-nums text-[var(--color-text)]">
          {fmtScore(candidate.score)} <span className="text-xs font-normal text-[var(--color-text-muted)]">match</span>
        </span>
      </div>

      {/* Side-by-side cases */}
      <div className="flex items-stretch gap-3">
        <DupCaseCard ref_={candidate.case_a} label="Case A" />
        <div className="flex items-center text-[var(--color-text-faint)]">
          <Copy className="h-4 w-4" />
        </div>
        <DupCaseCard ref_={candidate.case_b} label="Case B" />
      </div>

      {/* Reason */}
      {candidate.reason && (
        <div className="mt-3 flex items-start gap-2 bg-[var(--color-bg-secondary)]/70 rounded-lg px-3 py-2">
          <AlertCircle className="h-3.5 w-3.5 mt-0.5 flex-shrink-0 text-[var(--color-text-muted)]" />
          <p className="text-xs text-[var(--color-text-secondary)]">{candidate.reason}</p>
        </div>
      )}

      {/* Component score breakdown */}
      {componentEntries.length > 0 && (
        <div className="mt-3">
          <p className="text-[10px] uppercase tracking-wider text-[var(--color-text-faint)] mb-1.5">Component scores</p>
          <div className="flex flex-wrap gap-1.5">
            {componentEntries.map(([k, v]) => (
              <span
                key={k}
                className="inline-flex items-center gap-1 bg-[var(--color-bg-secondary)] text-[var(--color-text-muted)] text-xs px-2 py-0.5 rounded-full border border-[var(--color-border)]"
              >
                <span className="capitalize">{k.replace(/_/g, ' ')}</span>
                <span className="tabular-nums text-[var(--color-text-secondary)]">
                  {typeof v === 'number' ? fmtScore(v) : String(v)}
                </span>
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Actions (only on open candidates) */}
      {candidate.status === 'open' && !resolvedLocally ? (
        <div className="mt-4 flex items-center justify-between gap-3 flex-wrap border-t border-[var(--color-border)] pt-3">
          {isQaLead ? (
            <label className="flex items-center gap-2 text-xs text-[var(--color-text-muted)]">
              Keep
              <select
                className="input text-xs py-1"
                value={keepCaseId}
                onChange={e => setKeepCaseId(e.target.value)}
                disabled={busy !== null}
              >
                <option value={candidate.case_a.id}>Case A — {candidate.case_a.title}</option>
                <option value={candidate.case_b.id}>Case B — {candidate.case_b.title}</option>
              </select>
            </label>
          ) : <span />}
          <div className="flex items-center gap-2">
            <button
              onClick={handleDismiss}
              disabled={busy !== null}
              className="btn-secondary text-sm flex items-center gap-1.5"
            >
              {busy === 'dismiss' ? <LoadingSpinner size="sm" /> : <XCircle className="h-3.5 w-3.5" />}
              Dismiss
            </button>
            {isQaLead && (
              <button
                onClick={handleMerge}
                disabled={busy !== null}
                className="btn-primary text-sm flex items-center gap-1.5"
              >
                {busy === 'merge' ? <LoadingSpinner size="sm" /> : <GitMerge className="h-3.5 w-3.5" />}
                Merge
              </button>
            )}
          </div>
        </div>
      ) : (
        <div className="mt-3 border-t border-[var(--color-border)] pt-3">
          <StatusPill status={resolvedLocally ?? candidate.status} map={STATUS_COLORS} />
        </div>
      )}
      {refreshWarning && (
        <p role="status" className="mt-3 rounded-md border border-[var(--status-broken-bd)] bg-[var(--status-broken-bg)] p-2 text-xs text-[var(--status-broken)]">
          {refreshWarning}
        </p>
      )}
    </div>
  )
}

interface DuplicatesTabProps { projectId: string | null }

function DuplicatesTab({ projectId }: DuplicatesTabProps) {
  const [band, setBand] = useState<DuplicateBand | ''>('')
  const [statusFilter, setStatusFilter] = useState<DuplicateCandidateStatus>('open')
  const [detecting, setDetecting] = useState(false)

  // Pass undefined in All-Projects mode (projectId === null) → hook short-circuits.
  const params = useMemo(
    () => ({ ...(band ? { band } : {}), status: statusFilter, size: 200 }),
    [band, statusFilter],
  )
  const { data, isLoading, mutate } = useDuplicateCandidates(projectId ?? undefined, params)

  const candidates = data?.items ?? []

  // Optimistically remove a resolved (dismissed/merged) candidate from the
  // current view (the filter is status=open by default, so it disappears),
  // then revalidate against the server to reconcile open_count/total.
  const handleResolved = useCallback(
    (candidateId: string) =>
      mutate(
        (current) =>
          current
            ? {
                ...current,
                items: current.items.filter(c => c.id !== candidateId),
                total: Math.max(0, current.total - 1),
                open_count: Math.max(0, current.open_count - 1),
              }
            : current,
        { revalidate: true },
      ),
    [mutate],
  )

  const handleDetect = async () => {
    if (!projectId) return
    setDetecting(true)
    try {
      const res = await testManagementService.runDuplicateDetection(projectId, { enable_semantic: true })
      const msg = res.sampled
        ? `Scanned ${res.cases_scanned} cases (sampled) — ${res.candidates_created} new candidate(s)`
        : `Scanned ${res.cases_scanned} cases — ${res.candidates_created} new candidate(s)`
      toast.success(msg, { duration: 6000 })
      if (res.note) toast(res.note, { icon: 'ℹ️', duration: 6000 })
      void mutate()
    } catch {
      toast.error('Detection failed — please try again')
    } finally {
      setDetecting(false)
    }
  }

  if (!projectId) {
    return (
      <EmptyState
        icon={<Copy className="h-10 w-10" />}
        title="Select a single project"
        description="Duplicate detection runs per project. Pick a project from the top bar to review duplicate test cases."
      />
    )
  }

  return (
    <div className="space-y-4">
      {/* Header / controls */}
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div>
          <h3 className="text-sm font-semibold text-[var(--color-text)]">Duplicate Test Cases</h3>
          <p className="text-xs text-[var(--color-text-muted)] mt-0.5">
            Banded near-duplicate pairs across this project&rsquo;s authored cases.
            {data ? ` ${data.open_count} open.` : ''}
          </p>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <select
            className="input text-sm py-1.5"
            value={band}
            onChange={e => setBand(e.target.value as DuplicateBand | '')}
          >
            <option value="">All bands</option>
            <option value="exact">Exact</option>
            <option value="strong">Strong</option>
            <option value="possible">Possible</option>
          </select>
          <select
            className="input text-sm py-1.5"
            value={statusFilter}
            onChange={e => setStatusFilter(e.target.value as DuplicateCandidateStatus)}
          >
            <option value="open">Open</option>
            <option value="merged">Merged</option>
            <option value="dismissed">Dismissed</option>
          </select>
          <button
            onClick={handleDetect}
            disabled={detecting}
            className="btn-primary text-sm flex items-center gap-1.5"
          >
            {detecting ? <LoadingSpinner size="sm" /> : <SearchIcon className="h-3.5 w-3.5" />}
            {detecting ? 'Detecting…' : 'Detect duplicates'}
          </button>
        </div>
      </div>

      {/* Body */}
      {isLoading ? (
        <div className="flex items-center justify-center py-16"><LoadingSpinner size="lg" /></div>
      ) : candidates.length === 0 ? (
        <EmptyState
          icon={<Copy className="h-8 w-8" />}
          title={statusFilter === 'open' ? 'No duplicate candidates' : `No ${statusFilter} candidates`}
          description={
            statusFilter === 'open'
              ? 'Run detection to scan this project’s authored test cases for near-duplicate pairs.'
              : 'Nothing to show for this filter.'
          }
        />
      ) : (
        <div className="space-y-3">
          {candidates.map(c => (
            <DuplicateCandidateCard
              key={c.id}
              projectId={projectId}
              candidate={c}
              onResolved={handleResolved}
            />
          ))}
        </div>
      )}
    </div>
  )
}

// ─── Main Page ────────────────────────────────────────────────────────────────

export default function TestManagementPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const rawTab = searchParams.get('tab')
  const activeTab: Tab = (TABS as readonly string[]).includes(rawTab ?? '') ? (rawTab as Tab) : 'Test Cases'
  const setActiveTab = (tab: Tab) => setSearchParams({ tab }, { replace: true })

  const project = useProjectStore(s => s.activeProject)
  const projectId = useProjectStore(s => s.activeProjectId)
  const isAllProjects = projectId === 'all'
  const lifecycleV2 = useFeatureEnabled('test_case_lifecycle_v2')

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
    <div className="space-y-6">
      <PageHeader
        title="Test Case Management"
        subtitle={`Manage test cases, plans, strategies and reviews${project ? ` for ${project.name}` : ' — All Projects'}`}
      />

      {/* Tab navigation */}
      <div className="flex items-center gap-1 border-b border-[var(--color-border)] pb-0">
        {TABS.map(tab => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={clsx(
              'px-4 py-2.5 text-sm font-medium rounded-t-lg transition-colors -mb-px border-b-2',
              activeTab === tab
                ? 'border-[var(--color-border-light)] text-[var(--color-text)] bg-[var(--color-bg-secondary)]/60'
                : 'border-transparent text-[var(--color-text-muted)] hover:text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-secondary)]/30'
            )}
          >
            {tab}
          </button>
        ))}
      </div>

      {/* Tab content */}
      <div>
        {activeTab === 'Test Cases'   && <TestCasesTab projectId={tabProjectId} lifecycleV2={lifecycleV2} />}
        {activeTab === 'Test Suites'  && <TestSuitesTab projectId={tabProjectId} />}
        {activeTab === 'Test Plans'   && <TestPlansTab projectId={tabProjectId} />}
        {activeTab === 'Strategy'     && <StrategyTab projectId={tabProjectId} />}
        {activeTab === 'Reviews'      && <ReviewsTab projectId={tabProjectId} lifecycleV2={lifecycleV2} />}
        {activeTab === 'Knowledge Generation' && <KnowledgeGenerationTab />}
        {activeTab === 'Duplicates'   && <DuplicatesTab projectId={tabProjectId} />}
        {activeTab === 'Audit Log'    && <AuditTab projectId={tabProjectId} />}
      </div>
    </div>
  )
}
