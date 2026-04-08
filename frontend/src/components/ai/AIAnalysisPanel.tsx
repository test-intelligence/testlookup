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
      <div className="flex items-center justify-between">
        <p className="text-xs font-medium text-[var(--color-text-muted)] uppercase tracking-wider">Confidence + Why</p>
        <span className={clsx('text-2xl font-bold tabular-nums', confColor)}>{score}%</span>
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

      {/* Explanation sentence */}
      <p className="text-xs text-[var(--color-text-muted)] leading-relaxed">
        {score >= 80
          ? 'High confidence — the root cause is well-supported by tool evidence.'
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
          <span className="badge bg-amber-900/40 text-amber-300 border border-amber-700/50">
            ⏳ Pending Human Review
          </span>
        )}
      </div>

      {expanded && (
        <>
          {/* Confidence + Why panel */}
          <ConfidencePanel score={result.confidence_score} why={confidenceWhy} />

          <InvestigationTrail
            toolsUsed={toolsUsed}
            confidenceWhy={confidenceWhy}
            evidenceCount={confidenceWhy.evidence_count}
          />

          {/* Root cause summary */}
          <div className="theme-bg-secondary border theme-border rounded-xl p-4">
            <p className="text-xs font-medium text-[var(--color-text-muted)] uppercase tracking-wider mb-2">Root Cause Summary</p>
            <p className="text-sm text-[var(--color-text)] leading-relaxed">{result.root_cause_summary}</p>
          </div>

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

          {/* Evidence references */}
          {result.evidence_references.length > 0 && (
            <div>
              <p className="text-xs font-medium text-[var(--color-text-muted)] uppercase tracking-wider mb-2">Evidence</p>
              <div className="space-y-2">
                {result.evidence_references.map((ev, i) => (
                  <div key={i} className="bg-[var(--color-bg-secondary)] rounded-lg px-3 py-2 text-xs">
                    <span className="text-[var(--color-text)] font-medium uppercase mr-2">{ev.source}</span>
                    <span className="text-[var(--color-text-secondary)]">{ev.excerpt}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </>
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
    </div>
  )
}
