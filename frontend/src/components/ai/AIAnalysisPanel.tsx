import { useState, useEffect } from 'react'
import {
  AlertTriangle, Bot, CheckCircle, ChevronDown, ChevronUp,
  ExternalLink, Loader2, Shield, Zap,
} from 'lucide-react'
import { clsx } from 'clsx'
import toast from 'react-hot-toast'
import { aiService } from '@/services/aiService'
import type { AnalysisResult, ConfidenceWhy, RoleActions } from '@/types/ai'
import { confidenceColor } from '@/utils/formatters'
import RoleActionCard from '@/components/ai/RoleActionCard'
import ReviewStateControl from '@/components/ai/ReviewStateControl'
import AISuggestion, { BasisChip, normalizeProvenance } from '@/components/ai/AISuggestion'
import CorrectClassificationModal from '@/components/ai/CorrectClassificationModal'
import { aiFeedbackService } from '@/services/aiFeedbackService'

interface Props {
  testCaseId: string
  testName: string
  runId: string
  serviceName?: string
  timestamp?: string
  ocpPodName?: string
  ocpNamespace?: string
  projectKey?: string
}

// Human-readable labels for ReAct tool names returned by the backend
const TOOL_LABELS: Record<string, string> = {
  fetch_allure_stacktrace:      'Fetched stack trace',
  fetch_rest_api_payload:       'Retrieved REST payload',
  query_splunk_logs:            'Queried Splunk logs',
  check_test_flakiness:         'Checked flakiness history',
  analyze_openshift_pod_events: 'Analysed OpenShift pod events',
}

const CATEGORY_STYLES: Record<string, string> = {
  PRODUCT_BUG:       'bg-red-900/40 text-red-300 border-red-700/50',
  INFRASTRUCTURE:    'bg-orange-900/40 text-orange-300 border-orange-700/50',
  TEST_DATA:         'bg-amber-900/40 text-amber-300 border-amber-700/50',
  AUTOMATION_DEFECT: 'bg-purple-900/40 text-purple-300 border-purple-700/50',
  FLAKY:             'bg-pink-900/40 text-pink-300 border-pink-700/50',
  UNKNOWN:           'bg-[var(--color-bg-secondary)] text-[var(--color-text-muted)] border-[var(--color-border-light)]',
}

const DEPTH_LABEL: Record<string, { label: string; colour: string }> = {
  fast_path: { label: 'Fast-path classifier',         colour: 'text-[var(--color-text)]' },
  standard:  { label: 'Standard ReAct investigation', colour: 'text-emerald-400' },
  deep:      { label: 'Deep ReAct investigation',     colour: 'text-violet-400' },
}

// ── Confidence + Why sub-component ──────────────────────────────────────────

function ConfidencePanel({ score, why }: { score: number; why: ConfidenceWhy }) {
  const confColor = confidenceColor(score)
  const depthInfo = DEPTH_LABEL[why.investigation_depth] ?? DEPTH_LABEL.fast_path
  const sourceLabels: Record<string, string> = {
    splunk:     'Splunk',
    stacktrace: 'Stack trace',
    ocp_events: 'OCP events',
    flakiness:  'Flakiness DB',
  }

  return (
    <div className="theme-bg-secondary border theme-border rounded-xl p-4 space-y-3">
      {/* US-15.1: the figure and its calibration basis moved up into the
          shared AISuggestion header — one confidence per conclusion, always
          with its basis. This panel is now purely the "why". */}
      <div className="flex items-center justify-between">
        <p className="text-xs font-medium text-[var(--color-text-muted)] uppercase tracking-wider">Confidence + Why</p>
        <span className={clsx('text-xs font-medium tabular-nums', confColor)}>
          {score >= 80 ? 'high' : score >= 60 ? 'medium' : 'low'}
        </span>
      </div>

      {/* Investigation method */}
      <div className="flex items-center gap-2 text-sm">
        <span className={clsx('font-medium', depthInfo.colour)}>{depthInfo.label}</span>
        {why.is_llm_inference
          ? <span className="text-[var(--color-text-muted)] text-xs">· LLM inference</span>
          : <span className="text-[var(--color-text-muted)] text-xs">· Deterministic</span>
        }
      </div>

      {/* Evidence summary */}
      <div className="flex flex-wrap gap-2">
        {why.evidence_count > 0 && (
          <span className="text-xs px-2 py-0.5 rounded-full bg-[var(--color-bg-hover)] text-[var(--color-text-secondary)]">
            {why.evidence_count} evidence {why.evidence_count === 1 ? 'item' : 'items'}
          </span>
        )}
        {why.data_sources.map(src => (
          <span key={src} className="text-xs px-2 py-0.5 rounded-full bg-[var(--color-bg-hover)] text-[var(--color-text-secondary)]">
            {sourceLabels[src] ?? src}
          </span>
        ))}
        {why.evidence_count === 0 && why.data_sources.length === 0 && (
          <span className="text-xs text-[var(--color-text-muted)]">No evidence gathered</span>
        )}
      </div>

      {/* Explanation sentence. US-15.1 copy audit: the old high-confidence
          line asserted "the root cause is well-supported", i.e. that the
          cause is known. It says what the evidence supports instead. */}
      <p className="text-xs text-[var(--color-text-muted)] leading-relaxed">
        {score >= 80
          ? 'High confidence — the suggestion is well-supported by tool evidence. Confirm it before acting.'
          : score >= 60
          ? 'Medium confidence — some evidence gathered; manual verification recommended.'
          : 'Low confidence — insufficient telemetry. Manual investigation required.'}
      </p>
    </div>
  )
}

function InvestigationTrail({
  toolsUsed,
  confidenceWhy,
  evidenceCount,
}: {
  toolsUsed: string[]
  confidenceWhy: ConfidenceWhy
  evidenceCount: number
}) {
  const steps = toolsUsed.length > 0
    ? toolsUsed.map((tool, index) => ({
        id: `${tool}-${index}`,
        label: TOOL_LABELS[tool] ?? tool.replace(/_/g, ' '),
      }))
    : [{ id: 'fast-path', label: 'Fast-path classifier' }]

  steps.push({
    id: 'evidence',
    label: evidenceCount > 0 ? `${evidenceCount} evidence items` : 'No external evidence required',
  })

  steps.push({
    id: 'summary',
    label: confidenceWhy.is_llm_inference ? 'AI summary generated' : 'Deterministic answer returned',
  })

  return (
    <div className="bg-[var(--color-bg-secondary)]/60 border border-[var(--color-border)] rounded-xl p-4 space-y-3">
      <div className="flex items-center justify-between gap-2">
        <p className="text-xs font-medium text-[var(--color-text-muted)] uppercase tracking-wider">Investigation Trail</p>
        <span className="text-[10px] text-[var(--color-text-muted)] uppercase tracking-wider">
          {confidenceWhy.investigation_depth.replace(/_/g, ' ')}
        </span>
      </div>
      <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
        {steps.map((step, index) => (
          <div key={step.id} className="rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-card)]/60 p-3">
            <div className="flex items-center gap-2">
              <div className="flex h-5 w-5 items-center justify-center rounded-full bg-emerald-900/30 text-emerald-300">
                <CheckCircle className="h-3 w-3" />
              </div>
              <p className="text-sm text-[var(--color-text)]">{step.label}</p>
            </div>
            {index < steps.length - 1 && (
              <div className="mt-2 h-px w-full bg-gradient-to-r from-neutral-800 to-transparent" />
            )}
          </div>
        ))}
      </div>
      <div className="flex flex-wrap gap-2 text-[10px] text-[var(--color-text-muted)]">
        <span className="px-2 py-0.5 rounded-full bg-[var(--color-bg-card)]/70 border border-[var(--color-border)]">
          {confidenceWhy.is_llm_inference ? 'LLM inference used' : 'Deterministic path'}
        </span>
        <span className="px-2 py-0.5 rounded-full bg-[var(--color-bg-card)]/70 border border-[var(--color-border)]">
          {evidenceCount} evidence items
        </span>
      </div>
    </div>
  )
}

// ── Main component ───────────────────────────────────────────────────────────

export default function AIAnalysisPanel({
  testCaseId, testName, runId, serviceName, timestamp, ocpPodName, ocpNamespace, projectKey,
}: Props) {
  const [loading, setLoading]         = useState(false)
  const [initialLoading, setInitialLoading] = useState(true)
  const [result, setResult]           = useState<AnalysisResult | null>(null)
  const [jiraUrl, setJiraUrl]         = useState<string | null>(null)
  const [creatingJira, setCreatingJira] = useState(false)
  const [expanded, setExpanded]       = useState(true)
  const [hasError, setHasError]       = useState(false)
  // US-15.1 confirm/correct loop — reuses the existing feedback path
  // (POST /api/v1/feedback/{analysis_id}) and the extracted correction modal.
  const [correctOpen, setCorrectOpen] = useState(false)
  const [confirming, setConfirming]   = useState(false)
  const [confirmed, setConfirmed]     = useState(false)

  // Auto-load any previously stored analysis so the user sees results immediately
  // without having to click "Analyse Root Cause" again.
  useEffect(() => {
    let cancelled = false
    aiService.getAnalysis(testCaseId)
      .then((existing) => {
        if (!cancelled && existing) setResult(existing)
      })
      .catch(() => { /* ignore — panel shows the analyse button as fallback */ })
      .finally(() => { if (!cancelled) setInitialLoading(false) })
    return () => { cancelled = true }
  }, [testCaseId])

  const handleAnalyze = async () => {
    setLoading(true)
    setHasError(false)
    setResult(null)
    setJiraUrl(null)
    try {
      const analysis = await aiService.analyze({
        test_case_id: testCaseId,
        service_name: serviceName,
        timestamp,
        ocp_pod_name: ocpPodName,
        ocp_namespace: ocpNamespace,
      })
      setResult(analysis)
    } catch {
      setHasError(true)
      toast.error('AI analysis failed. Check backend logs.')
    } finally {
      setLoading(false)
    }
  }

  const handleCreateJira = async () => {
    if (!result || !projectKey) return
    setCreatingJira(true)
    try {
      const resp = await aiService.createJiraTicket({
        project_key: projectKey,
        test_case_id: testCaseId,
        test_name: testName,
        run_id: runId,
        ai_summary: result.root_cause_summary,
        recommended_action: result.recommended_actions[0] ?? '',
      })
      setJiraUrl(resp.ticket_url)
      toast.success(`Jira ticket created: ${resp.ticket_key}`)
    } catch {
      toast.error('Failed to create Jira ticket')
    } finally {
      setCreatingJira(false)
    }
  }

  // ── Initial fetch in progress ───────────────────────────────
  if (initialLoading) {
    return (
      <div className="card flex flex-col items-center gap-4 py-10">
        <Loader2 className="h-7 w-7 animate-spin text-[var(--color-text-muted)]" />
        <p className="text-sm text-[var(--color-text-muted)]">Loading analysis…</p>
      </div>
    )
  }

  // ── Idle state (no prior analysis exists) ───────────────────
  if (!loading && !result && !hasError) {
    return (
      <div className="card flex flex-col items-center gap-4 py-10">
        <div className="p-4 bg-white/10 rounded-2xl">
          <Bot className="h-10 w-10 text-[var(--color-text)]" />
        </div>
        <div className="text-center">
          <p className="font-semibold text-[var(--color-text)]">AI Root Cause Analysis</p>
          <p className="text-sm text-[var(--color-text-muted)] mt-1 max-w-xs">
            Powered by {import.meta.env.VITE_LLM_PROVIDER || 'Ollama (local)'} — no data leaves your network
          </p>
        </div>
        <button className="btn-primary flex items-center gap-2" onClick={handleAnalyze}>
          <Zap className="h-4 w-4" />
          Analyse Root Cause
        </button>
      </div>
    )
  }

  // ── Loading state ──────────────────────────────────────────
  if (loading) {
    return (
      <div className="card">
        <div className="flex items-center gap-3 mb-5">
          <Loader2 className="h-5 w-5 text-[var(--color-text)] animate-spin" />
          <p className="font-semibold text-[var(--color-text)]">Investigating…</p>
        </div>
        <p className="text-sm text-[var(--color-text-muted)]">
          The ReAct agent is gathering evidence. Actual tools used will be shown when complete.
        </p>
      </div>
    )
  }

  // ── Error state ────────────────────────────────────────────
  if (hasError) {
    return (
      <div className="card border-red-800/50">
        <div className="flex items-center gap-3 mb-3">
          <AlertTriangle className="h-5 w-5 text-red-400" />
          <p className="font-semibold text-red-300">Analysis Failed</p>
        </div>
        <p className="text-sm text-[var(--color-text-muted)] mb-4">The AI agent encountered an error. Check that your LLM (Ollama) is running.</p>
        <button className="btn-secondary text-sm" onClick={() => { setHasError(false) }}>Try Again</button>
      </div>
    )
  }

  // ── Result state ───────────────────────────────────────────
  if (!result) return null

  const categoryStyle = CATEGORY_STYLES[result.failure_category] ?? CATEGORY_STYLES.UNKNOWN
  const toolsUsed: string[] = result.tools_used ?? []
  const roleActions: RoleActions = result.role_actions ?? { qa: '', developer: '', sre: '', release_manager: '' }
  const confidenceWhy = result.confidence_why ?? {
    evidence_count: result.evidence_references?.length ?? 0,
    data_sources: [],
    is_llm_inference: toolsUsed.length > 0,
    investigation_depth: toolsUsed.length === 0 ? 'fast_path' : toolsUsed.length >= 4 ? 'deep' : 'standard',
  }

  // ── US-15.1 trust chrome inputs ──────────────────────────────────────
  // Everything here is defensive: analysis_id / provenance / low_confidence
  // are all optional on the wire and legitimately absent on older rows.
  const analysisId = result.analysis_id ?? null
  const provenance = normalizeProvenance(result)
  const lowConfidence = result.low_confidence ?? result.requires_human_review
  const evidenceLinks = (result.evidence_references ?? []).map(ev => ({
    label: ev.source,
    detail: ev.excerpt,
  }))

  const handleConfirm = async () => {
    if (!analysisId || confirming) return
    setConfirming(true)
    try {
      await aiFeedbackService.submitFeedback(analysisId, { rating: 'correct' })
      setConfirmed(true)
      toast.success('Confirmed — recorded for the training loop.')
    } catch {
      toast.error('Could not record the confirmation.')
    } finally {
      setConfirming(false)
    }
  }

  return (
    <div className="card space-y-5">
      {/* Header */}
      <div className="flex items-start justify-between">
        <div className="flex items-center gap-3">
          <div className="p-2 bg-white/10 rounded-xl">
            <Bot className="h-5 w-5 text-[var(--color-text)]" />
          </div>
          <div>
            <p className="font-semibold text-[var(--color-text)]">AI Triage Result</p>
            <p className="text-xs text-[var(--color-text-muted)]">{result.llm_provider} · {result.llm_model}</p>
          </div>
        </div>
        <button onClick={() => setExpanded(x => !x)} className="btn-ghost p-1">
          {expanded ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
        </button>
      </div>

      {/* Category + flags */}
      <div className="flex flex-wrap gap-2">
        <span className={clsx('badge border', categoryStyle)}>{result.failure_category.replace('_', ' ')}</span>
        {result.is_flaky && (
          <span className="badge bg-pink-900/40 text-pink-300 border border-pink-700/50">
            ⚠ High Flakiness
          </span>
        )}
        {result.backend_error_found && (
          <span className="badge bg-orange-900/40 text-orange-300 border border-orange-700/50">
            Backend Error Found
          </span>
        )}
        {result.pod_issue_found && (
          <span className="badge bg-red-900/40 text-red-300 border border-red-700/50">
            OCP Pod Issue
          </span>
        )}
        {result.requires_human_review && (
          // ReviewStateControl is the interactive replacement for the
          // previous static "Pending Human Review" badge (2026-05-15
          // feature). It reads the migration-0081 ``test_execution_reviews``
          // row when one exists, otherwise renders the implicit
          // pending_review state. Picking a transition writes via
          // PUT /api/v1/test-cases/{id}/review.
          <ReviewStateControl testCaseId={testCaseId} />
        )}
      </div>

      {expanded && (
        // US-15.1: the ENTIRE conclusion sits inside the shared trust chrome
        // — one AI-suggested badge, one confidence with its calibration
        // basis, routing provenance (with a fallback notice when the LLM did
        // not answer), evidence, and confirm/correct when there is an
        // analysis id to attach the feedback to.
        <AISuggestion
          bare
          label="root cause + actions"
          confidence={result.confidence_score}
          confidenceBasis={confidenceWhy.confidence_basis}
          lowConfidence={lowConfidence}
          provenance={provenance}
          evidence={evidenceLinks}
          analysisId={analysisId}
          onConfirm={handleConfirm}
          onCorrect={() => setCorrectOpen(true)}
          busy={confirming}
          confirmed={confirmed}
          className="space-y-5"
        >
          {/* Suggested root cause */}
          <div className="theme-bg-secondary border theme-border rounded-xl p-4">
            <p className="text-xs font-medium text-[var(--color-text-muted)] uppercase tracking-wider mb-2">
              Suggested root cause
            </p>
            <p className="text-sm text-[var(--color-text)] leading-relaxed">{result.root_cause_summary}</p>
          </div>

          {/* Confidence + Why panel */}
          <ConfidencePanel score={result.confidence_score} why={confidenceWhy} />

          <InvestigationTrail
            toolsUsed={toolsUsed}
            confidenceWhy={confidenceWhy}
            evidenceCount={confidenceWhy.evidence_count}
          />

          {/* Role-aware actions */}
          {Object.values(roleActions).some(v => v.trim()) && (
            <div>
              <p className="text-xs font-medium text-[var(--color-text-muted)] uppercase tracking-wider mb-3">
                Role-Aware Actions
              </p>
              <RoleActionCard roleActions={{ ...roleActions }} />
            </div>
          )}

          {/* Generic recommended actions (compact, as reference) */}
          {result.recommended_actions.length > 0 && (
            <div>
              <p className="text-xs font-medium text-[var(--color-text-muted)] uppercase tracking-wider mb-2">All Recommended Actions</p>
              <ul className="space-y-1.5">
                {result.recommended_actions.map((action, i) => (
                  <li key={i} className="flex items-start gap-2 text-sm text-[var(--color-text-secondary)]">
                    <CheckCircle className="h-4 w-4 text-emerald-400 flex-shrink-0 mt-0.5" />
                    {action}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </AISuggestion>
      )}

      {/* Actions */}
      <div className="flex items-center gap-3 pt-1">
        <button
          className="btn-secondary text-sm flex items-center gap-2"
          onClick={() => { setResult(null); setHasError(false) }}
        >
          <Zap className="h-3.5 w-3.5" />
          Re-analyse
        </button>

        {jiraUrl ? (
          <a
            href={jiraUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="btn-primary text-sm flex items-center gap-2"
          >
            <ExternalLink className="h-3.5 w-3.5" />
            View Jira Ticket
          </a>
        ) : (
          <button
            className="btn-primary text-sm flex items-center gap-2 disabled:opacity-50"
            onClick={handleCreateJira}
            disabled={creatingJira || !projectKey || result.requires_human_review}
            title={result.requires_human_review ? 'Low confidence — manual review required before creating ticket' : ''}
          >
            {creatingJira
              ? <><Loader2 className="h-3.5 w-3.5 animate-spin" /> Creating…</>
              : <><Shield className="h-3.5 w-3.5" /> Create Jira Defect</>
            }
          </button>
        )}
      </div>

      {correctOpen && analysisId && (
        <CorrectClassificationModal
          analysisId={analysisId}
          currentCategory={result.failure_category}
          testName={testName}
          onClose={() => setCorrectOpen(false)}
        />
      )}
    </div>
  )
}
