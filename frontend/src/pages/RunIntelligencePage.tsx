import { useState, useMemo, useRef } from 'react'
import { useParams, Link } from 'react-router-dom'
import {
  AlertTriangle, Bot, CheckCircle, ChevronRight,
  Layers, TicketCheck, XCircle, AlertCircle,
  GitCompare, ArrowDown, ArrowUp, Minus, Filter,
  HeartPulse, RefreshCw, FileDown, Share2, Package,
} from 'lucide-react'
import { clsx } from 'clsx'
import toast from 'react-hot-toast'
import PageHeader from '@/components/ui/PageHeader'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import EmptyState from '@/components/ui/EmptyState'
import CriticalityMatrix from '@/components/ai/CriticalityMatrix'
import DefectPromotionModal from '@/components/ai/DefectPromotionModal'
import ExecutiveSummaryPanel from '@/components/ai/ExecutiveSummaryPanel'
import { useRunIntelligence, useRunModeSummary } from '@/hooks/useRunIntelligence'
import { useRunTestHealth } from '@/hooks/useTestHealth'
import RoleActionCardShared from '@/components/ai/RoleActionCard'
import WorkflowTimeline from '@/components/workflow/WorkflowTimeline'
import { useProjectChangeRedirect } from '@/hooks/useProjectChange'
import type {
  ReleaseDecisionIntel,
  StructuredSummary,
  FailureClusterIntel,
  BaselineDiff,
  RunModeSummary,
} from '@/services/runIntelligenceService'

// ── Helpers ────────────────────────────────────────────────────────────────────

const RECOMMENDATION_STYLES: Record<string, { bg: string; text: string; icon: React.ReactNode }> = {
  GO:             { bg: 'bg-emerald-900/30 border-emerald-700/50', text: 'text-emerald-300', icon: <CheckCircle className="h-5 w-5" /> },
  CONDITIONAL_GO: { bg: 'bg-amber-900/30 border-amber-700/50',    text: 'text-amber-300',   icon: <AlertCircle className="h-5 w-5" /> },
  NO_GO:          { bg: 'bg-red-900/30 border-red-700/50',        text: 'text-red-300',     icon: <XCircle className="h-5 w-5" /> },
}

const CRITICALITY_COLOUR: Record<string, string> = {
  CRITICAL: 'text-red-400',
  HIGH:     'text-orange-400',
  MEDIUM:   'text-amber-400',
  LOW:      'text-emerald-400',
}

const CRITICALITY_BADGE: Record<string, string> = {
  CRITICAL: 'bg-red-900/40 text-red-300 border border-red-700/40',
  HIGH:     'bg-orange-900/40 text-orange-300 border border-orange-700/40',
  MEDIUM:   'bg-amber-900/30 text-amber-300 border border-amber-700/30',
  LOW:      'bg-emerald-900/30 text-emerald-300 border border-emerald-700/30',
}

const CATEGORY_COLOUR: Record<string, string> = {
  PRODUCT_BUG:       'bg-red-900/30 text-red-300',
  INFRASTRUCTURE:    'bg-orange-900/30 text-orange-300',
  TEST_DATA:         'bg-amber-900/30 text-amber-300',
  AUTOMATION_DEFECT: 'bg-purple-900/30 text-purple-300',
  FLAKY:             'bg-pink-900/30 text-pink-300',
  UNKNOWN:           'bg-[var(--color-bg-secondary)] text-[var(--color-text-muted)]',
}

const REGRESSION_LABEL: Record<string, { text: string; colour: string }> = {
  new_regression:  { text: 'New Regression',      colour: 'text-red-400' },
  known_flaky:     { text: 'Known Flaky',          colour: 'text-amber-400' },
  environmental:   { text: 'Environmental',        colour: 'text-orange-400' },
  product_bug:     { text: 'Product Bug',          colour: 'text-red-400' },
  infrastructure:  { text: 'Infrastructure',       colour: 'text-orange-400' },
  unclassified:    { text: 'Unclassified',         colour: 'text-[var(--color-text-muted)]' },
}

function passRateColour(rate: number): string {
  if (rate >= 90) return 'text-emerald-400'
  if (rate >= 70) return 'text-amber-400'
  return 'text-red-400'
}

// ── Sub-components ─────────────────────────────────────────────────────────────

function ReleaseGateBanner({ decision }: { decision: ReleaseDecisionIntel }) {
  const style = RECOMMENDATION_STYLES[decision.recommendation] ?? RECOMMENDATION_STYLES.CONDITIONAL_GO
  return (
    <div className={clsx('card border flex items-start gap-4', style.bg)}>
      <div className={clsx('mt-0.5', style.text)}>{style.icon}</div>
      <div className="flex-1">
        <div className="flex items-center gap-3 mb-1">
          <span className={clsx('text-lg font-bold', style.text)}>{decision.recommendation.replace('_', ' ')}</span>
          <span className="text-sm text-[var(--color-text-muted)]">Risk score: {decision.risk_score}/100</span>
        </div>
        <p className="text-sm text-[var(--color-text-secondary)]">{decision.reasoning}</p>
        {decision.blocking_issues.length > 0 && (
          <div className="mt-2">
            <p className="text-xs font-medium text-[var(--color-text-muted)] uppercase tracking-wider mb-1">Blocking Issues</p>
            <ul className="space-y-1">
              {decision.blocking_issues.map((issue, i) => (
                <li key={i} className="flex items-start gap-2 text-sm text-[var(--color-text-secondary)]">
                  <XCircle className="h-3.5 w-3.5 text-red-400 flex-shrink-0 mt-0.5" />
                  {issue}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </div>
  )
}

function LayeredSummary({ summary, mode }: { summary: StructuredSummary | RunModeSummary; mode?: string }) {
  const layer2 = summary.layer2_incident
  const layer3 = summary.layer3_evidence
  const layer4 = summary.layer4_action_plan
  // Epic 6: citations and similar failures from mode summary
  const citations = (summary as RunModeSummary).citations ?? []
  const similarFailures = (summary as RunModeSummary).similar_failures ?? []
  const markdownReport = (summary as RunModeSummary).markdown_report
  const fallbackUsed = (summary as RunModeSummary).fallback_used

  const hasLayers = layer2 || layer3 || layer4

  const handleCopy = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text)
      toast.success('Copied to clipboard')
    } catch {
      toast.error('Failed to copy')
    }
  }

  const modeLabel = mode === 'developer' ? 'Developer Summary'
    : mode === 'manager' ? 'Manager Summary'
    : 'Executive Summary'

  return (
    <div className="space-y-4">
      {/* Mode indicator for non-executive views */}
      {mode && mode !== 'executive' && (
        <div className="flex items-center gap-2 text-xs">
          <span className="bg-white/10 text-[var(--color-text-secondary)] border border-[var(--color-border-light)] px-2 py-0.5 rounded">
            {modeLabel}
          </span>
          {fallbackUsed && (
            <span className="bg-amber-900/20 text-amber-400 border border-amber-700/30 px-2 py-0.5 rounded">
              Fallback mode
            </span>
          )}
        </div>
      )}

      {/* Structured executive panel (preferred) or plain-text fallback */}
      {(summary as StructuredSummary).executive_panel ? (
        <ExecutiveSummaryPanel panel={(summary as StructuredSummary).executive_panel as NonNullable<StructuredSummary['executive_panel']>} />
      ) : summary.executive_summary ? (
        <div className="theme-bg-secondary border theme-border rounded-xl p-4">
          <div className="flex items-center justify-between mb-2">
            <p className="text-xs font-medium text-[var(--color-text-muted)] uppercase tracking-wider">{modeLabel}</p>
            <button
              onClick={() => handleCopy(summary.executive_summary ?? '')}
              className="text-xs text-[var(--color-text-faint)] hover:text-[var(--color-text-muted)] transition-colors"
              title="Copy summary"
            >
              Copy
            </button>
          </div>
          <p className="text-sm text-[var(--color-text)] leading-relaxed">{summary.executive_summary}</p>
        </div>
      ) : null}

      {/* Render markdown_report as fallback when layer fields are all null */}
      {!hasLayers && markdownReport && (
        <div className="bg-[var(--color-bg-secondary)]/60 border border-[var(--color-border)] rounded-xl p-4">
          <div className="flex items-center justify-between mb-2">
            <p className="text-xs font-medium text-[var(--color-text-muted)] uppercase tracking-wider">Detailed Analysis</p>
            <button
              onClick={() => handleCopy(markdownReport)}
              className="text-xs text-[var(--color-text-faint)] hover:text-[var(--color-text-muted)] transition-colors"
              title="Copy report"
            >
              Copy
            </button>
          </div>
          <div className="text-sm text-[var(--color-text-secondary)] leading-relaxed whitespace-pre-wrap">
            {markdownReport}
          </div>
        </div>
      )}

      {layer2 && (
        <div className="bg-[var(--color-bg-secondary)]/60 border border-[var(--color-border)] rounded-xl p-4 space-y-3">
          <p className="text-xs font-medium text-[var(--color-text-muted)] uppercase tracking-wider">Incident View</p>
          <div className="grid grid-cols-2 gap-3 text-sm">
            {layer2.what_failed && (
              <div>
                <span className="text-[var(--color-text-muted)] text-xs">What failed</span>
                <p className="text-[var(--color-text)]">{layer2.what_failed}</p>
              </div>
            )}
            {layer2.likely_cause && (
              <div>
                <span className="text-[var(--color-text-muted)] text-xs">Likely cause</span>
                <p className="text-[var(--color-text)]">{layer2.likely_cause}</p>
              </div>
            )}
            {layer2.scope && (
              <div>
                <span className="text-[var(--color-text-muted)] text-xs">Scope</span>
                <p className="text-[var(--color-text)]">{layer2.scope}</p>
              </div>
            )}
            {layer2.criticality && (
              <div>
                <span className="text-[var(--color-text-muted)] text-xs">Criticality</span>
                <p className={clsx('font-semibold', CRITICALITY_COLOUR[layer2.criticality])}>{layer2.criticality}</p>
              </div>
            )}
          </div>
        </div>
      )}

      {layer3 && (
        <div className="bg-[var(--color-bg-secondary)]/60 border border-[var(--color-border)] rounded-xl p-4 space-y-2">
          <p className="text-xs font-medium text-[var(--color-text-muted)] uppercase tracking-wider">Evidence Pack</p>
          {layer3.data_sources_used && layer3.data_sources_used.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {layer3.data_sources_used.map((src) => (
                <span key={src} className="badge bg-white/10 text-[var(--color-text-secondary)] border border-[var(--color-border-light)] text-xs">{src}</span>
              ))}
            </div>
          )}
          {layer3.log_anomalies && layer3.log_anomalies.length > 0 && (
            <ul className="space-y-1">
              {layer3.log_anomalies.map((a, i) => (
                <li key={i} className="text-sm text-[var(--color-text-secondary)] flex items-start gap-2">
                  <AlertTriangle className="h-3.5 w-3.5 text-amber-400 flex-shrink-0 mt-0.5" />
                  {a}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {layer4 && (
        <div className="bg-[var(--color-bg-secondary)]/60 border border-[var(--color-border)] rounded-xl p-4 space-y-3">
          <p className="text-xs font-medium text-[var(--color-text-muted)] uppercase tracking-wider">Action Plan</p>
          {layer4.immediate_mitigation && (
            <div className="bg-amber-900/20 border border-amber-700/30 rounded-lg px-3 py-2 text-sm">
              <span className="text-amber-300 font-medium">Immediate: </span>
              <span className="text-[var(--color-text)]">{layer4.immediate_mitigation}</span>
            </div>
          )}
          {layer4.fix_recommendations && layer4.fix_recommendations.length > 0 && (
            <div>
              <p className="text-xs text-[var(--color-text-muted)] mb-1.5">Fix recommendations</p>
              <ul className="space-y-1">
                {layer4.fix_recommendations.map((r, i) => (
                  <li key={i} className="flex items-start gap-2 text-sm text-[var(--color-text-secondary)]">
                    <CheckCircle className="h-3.5 w-3.5 text-emerald-400 flex-shrink-0 mt-0.5" />
                    {r}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}

      {/* Similar historical failures */}
      {similarFailures.length > 0 && (
        <div className="bg-[var(--color-bg-secondary)]/40 border border-[var(--color-border)]/30 rounded-xl p-4">
          <p className="text-xs font-medium text-[var(--color-text-muted)] uppercase tracking-wider mb-2">
            Similar Historical Failures
          </p>
          <ul className="space-y-1">
            {similarFailures.slice(0, 5).map((sf, i) => (
              <li key={i} className="flex items-start gap-2 text-sm text-[var(--color-text-muted)]">
                <AlertTriangle className="h-3.5 w-3.5 text-amber-500 flex-shrink-0 mt-0.5" />
                {typeof sf === 'string' ? sf : sf.test_name || JSON.stringify(sf)}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Citations */}
      {citations.length > 0 && (
        <div className="bg-[var(--color-bg-secondary)]/40 border border-[var(--color-border)]/30 rounded-xl p-4">
          <p className="text-xs font-medium text-[var(--color-text-muted)] uppercase tracking-wider mb-2">
            Evidence Citations
          </p>
          <ul className="space-y-2">
            {citations.map((c, i) => (
              <li key={i} className="space-y-0.5">
                <span className="text-xs text-[var(--color-text)] font-mono">[{c.source}]</span>
                <p className="text-xs text-[var(--color-text-muted)] pl-2 truncate">{c.excerpt}</p>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

function ClusterCard({
  cluster,
  onPromote,
}: {
  cluster: FailureClusterIntel
  onPromote?: (clusterId: string, label: string) => void
}) {
  const critLevel = cluster.criticality_level ?? 'MEDIUM'
  const [showEvidence, setShowEvidence] = useState(false)

  return (
    <div className="card p-3 space-y-2">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2 min-w-0">
          <Layers className="h-4 w-4 text-[var(--color-text-muted)] shrink-0" />
          <span className="text-sm font-medium text-[var(--color-text)] truncate max-w-[240px]">{cluster.label}</span>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <span className={clsx('badge text-xs', CRITICALITY_BADGE[critLevel])}>{critLevel}</span>
          <span className="badge bg-red-900/30 text-red-300 border border-red-700/30">{cluster.size} failures</span>
        </div>
      </div>

      {cluster.representative_error && (
        <p className="text-xs text-[var(--color-text-muted)] pl-6 truncate">{cluster.representative_error}</p>
      )}

      {/* Cluster action bar */}
      <div className="pl-6 flex items-center gap-3">
        {onPromote && (
          <button
            onClick={() => onPromote(cluster.cluster_id, cluster.label)}
            className="flex items-center gap-1 text-[10px] text-[var(--color-text)] hover:text-[var(--color-text-secondary)] transition-colors"
          >
            <TicketCheck className="h-3 w-3" />
            Promote to Defect
          </button>
        )}
        <button
          onClick={() => setShowEvidence(!showEvidence)}
          className="flex items-center gap-1 text-[10px] text-[var(--color-text-muted)] hover:text-[var(--color-text-secondary)] transition-colors"
        >
          {showEvidence ? 'Hide' : 'View'} Evidence
        </button>
        <Link
          to={`/search?q=${encodeURIComponent(cluster.label)}&search_type=semantic`}
          className="flex items-center gap-1 text-[10px] text-[var(--color-text-muted)] hover:text-[var(--color-text-secondary)] transition-colors"
        >
          Similar Failures
        </Link>
      </div>

      {/* Evidence expansion */}
      {showEvidence && cluster.dimension_scores.length > 0 && (
        <div className="pl-6 space-y-1 pt-1 border-t border-[var(--color-border)]/50">
          <p className="text-[10px] text-[var(--color-text-faint)] uppercase tracking-wider">Criticality Dimensions</p>
          {cluster.dimension_scores.map(d => (
            <div key={d.name} className="flex items-center gap-2 text-[10px]">
              <span className="text-[var(--color-text-muted)] w-24 truncate">{d.label}</span>
              <div className="flex-1 h-1 bg-[var(--color-bg-secondary)] rounded-full overflow-hidden">
                <div className="h-full bg-neutral-300/60 rounded-full" style={{ width: `${d.score}%` }} />
              </div>
              <span className="text-[var(--color-text-faint)] tabular-nums w-8 text-right">{d.score.toFixed(0)}</span>
            </div>
          ))}
          <p className="text-[10px] text-[var(--color-text-faint)]">{cluster.member_test_ids.length} member tests · cohesion {((cluster.cohesion_score ?? 0) * 100).toFixed(0)}%</p>
        </div>
      )}
    </div>
  )
}

function WhatChangedPanel({ diff }: { diff: BaselineDiff }) {
  const [activeFilter, setActiveFilter] = useState<string | null>(null)
  const reg = REGRESSION_LABEL[diff.regression_classification] ?? REGRESSION_LABEL.unclassified
  const deltaPositive = (diff.pass_rate_delta ?? 0) > 0
  const deltaZero = diff.pass_rate_delta === null || diff.pass_rate_delta === 0

  // Collect unique classifications from regression_clusters for filter chips
  const clusterClassifications = useMemo(() => {
    const seen = new Set<string>()
    for (const rc of (diff.regression_clusters ?? [])) {
      seen.add(rc.classification)
    }
    return Array.from(seen)
  }, [diff.regression_clusters])

  // Filter classified_new_failures by active chip
  const classifiedFailures = diff.classified_new_failures ?? []
  const filteredFailures = activeFilter
    ? classifiedFailures.filter(f => f.classification === activeFilter)
    : classifiedFailures

  // Fallback to raw new_failures when classified list is empty
  const displayNames = filteredFailures.length > 0
    ? filteredFailures.map(f => f.name)
    : (activeFilter ? [] : diff.new_failures)

  return (
    <div className="card space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <GitCompare className="h-4 w-4 text-[var(--color-text-muted)]" />
          <p className="text-sm font-medium text-[var(--color-text-secondary)]">What Changed Since Last Good Run</p>
        </div>
        <span className={clsx('text-xs font-semibold', reg.colour)}>{reg.text}</span>
      </div>

      {/* Delta chips */}
      <div className="flex flex-wrap gap-2">
        {diff.pass_rate_delta !== null && (
          <span className={clsx(
            'flex items-center gap-1 text-xs font-mono px-2 py-1 rounded-full',
            deltaZero ? 'bg-[var(--color-bg-secondary)] text-[var(--color-text-muted)]' :
            deltaPositive ? 'bg-emerald-900/30 text-emerald-300' : 'bg-red-900/30 text-red-300',
          )}>
            {deltaZero ? <Minus className="h-3 w-3" /> : deltaPositive ? <ArrowUp className="h-3 w-3" /> : <ArrowDown className="h-3 w-3" />}
            {Math.abs(diff.pass_rate_delta).toFixed(1)}pp pass-rate
          </span>
        )}
        {diff.new_failures.length > 0 && (
          <span className="flex items-center gap-1 text-xs font-mono px-2 py-1 rounded-full bg-red-900/30 text-red-300">
            +{diff.new_failures.length} new failures
          </span>
        )}
        {diff.resolved_failures.length > 0 && (
          <span className="flex items-center gap-1 text-xs font-mono px-2 py-1 rounded-full bg-emerald-900/30 text-emerald-300">
            <CheckCircle className="h-3 w-3" />
            {diff.resolved_failures.length} resolved
          </span>
        )}
        {(diff.suites_impacted_delta ?? 0) !== 0 && (
          <span className={clsx(
            'flex items-center gap-1 text-xs font-mono px-2 py-1 rounded-full',
            (diff.suites_impacted_delta ?? 0) > 0 ? 'bg-orange-900/30 text-orange-300' : 'bg-emerald-900/30 text-emerald-300',
          )}>
            {(diff.suites_impacted_delta ?? 0) > 0 ? '+' : ''}{diff.suites_impacted_delta} suites
          </span>
        )}
        {diff.baseline_build_number && (
          <span className="text-xs text-[var(--color-text-muted)] py-1">
            vs build #{diff.baseline_build_number}
            {diff.selection_reason && diff.selection_reason !== 'latest_passing' && (
              <span className="ml-1 text-[var(--color-text-faint)]">({diff.selection_reason.replace(/_/g, ' ')})</span>
            )}
          </span>
        )}
      </div>

      {/* Commit range */}
      {diff.commit_range && !diff.commit_range.same_commit && (
        <div className="flex items-center gap-2 text-xs bg-[var(--color-bg-secondary)]/60 rounded-lg px-3 py-2">
          <span className="text-[var(--color-text-muted)]">Commits:</span>
          {diff.commit_range.from_commit && (
            <code className="text-[var(--color-text-muted)] font-mono">{diff.commit_range.from_commit}</code>
          )}
          <span className="text-[var(--color-text-faint)]">→</span>
          {diff.commit_range.to_commit && (
            <code className="text-[var(--color-text)] font-mono">{diff.commit_range.to_commit}</code>
          )}
        </div>
      )}
      {diff.commit_range?.same_commit && (
        <div className="flex items-center gap-2 text-xs text-[var(--color-text-muted)] bg-[var(--color-bg-secondary)]/60 rounded-lg px-3 py-2">
          Same commit — environment or data change suspected
        </div>
      )}

      {/* Config drift */}
      {diff.config_drift && diff.config_drift.length > 0 && (
        <div className="space-y-1">
          <p className="text-[10px] text-[var(--color-text-faint)] uppercase tracking-wider">Config Drift Detected</p>
          {diff.config_drift.map((d, i) => (
            <div key={i} className="flex items-center gap-2 text-xs bg-orange-900/10 border border-orange-700/20 rounded-lg px-3 py-1.5">
              <span className="text-orange-400 font-medium">{d.field}</span>
              <span className="text-[var(--color-text-muted)]">{d.old_value ?? '(empty)'}</span>
              <span className="text-[var(--color-text-faint)]">→</span>
              <span className="text-orange-300">{d.new_value ?? '(empty)'}</span>
            </div>
          ))}
        </div>
      )}

      {/* Classification filter chips */}
      {clusterClassifications.length > 1 && diff.new_failures.length > 0 && (
        <div>
          <div className="flex items-center gap-1.5 mb-1.5">
            <Filter className="h-3 w-3 text-[var(--color-text-faint)]" />
            <span className="text-[10px] text-[var(--color-text-faint)] uppercase tracking-wider">Filter by type</span>
          </div>
          <div className="flex flex-wrap gap-1.5">
            <button
              onClick={() => setActiveFilter(null)}
              className={clsx(
                'text-xs px-2 py-0.5 rounded-full border transition-colors',
                activeFilter === null
                  ? 'bg-[var(--color-bg-hover)] border-[var(--color-border-light)] text-[var(--color-text)]'
                  : 'border-[var(--color-border)] text-[var(--color-text-muted)] hover:text-[var(--color-text-secondary)]',
              )}
            >
              All
            </button>
            {clusterClassifications.map(cls => {
              const meta = REGRESSION_LABEL[cls] ?? { text: cls, colour: 'text-[var(--color-text-muted)]' }
              return (
                <button
                  key={cls}
                  onClick={() => setActiveFilter(activeFilter === cls ? null : cls)}
                  className={clsx(
                    'text-xs px-2 py-0.5 rounded-full border transition-colors',
                    activeFilter === cls
                      ? clsx('bg-[var(--color-bg-hover)] border-[var(--color-border-light)]', meta.colour)
                      : 'border-[var(--color-border)] text-[var(--color-text-muted)] hover:text-[var(--color-text-secondary)]',
                  )}
                >
                  {meta.text}
                </button>
              )
            })}
          </div>
        </div>
      )}

      {/* New failure list — filtered by active chip */}
      {displayNames.length > 0 && (
        <div>
          <p className="text-xs text-[var(--color-text-muted)] mb-1">
            {activeFilter
              ? `${filteredFailures.length} ${REGRESSION_LABEL[activeFilter]?.text ?? activeFilter} failures`
              : 'New failures'}
          </p>
          <ul className="space-y-0.5">
            {displayNames.slice(0, 5).map((name, i) => (
              <li key={i} className="text-xs text-[var(--color-text-muted)] flex items-start gap-1.5 truncate">
                <XCircle className="h-3 w-3 text-red-500 flex-shrink-0 mt-0.5" />
                {name}
              </li>
            ))}
            {displayNames.length > 5 && (
              <li className="text-xs text-[var(--color-text-faint)]">+{displayNames.length - 5} more</li>
            )}
          </ul>
          {activeFilter && filteredFailures.length === 0 && (
            <p className="text-xs text-[var(--color-text-faint)] italic">No failures matching this filter.</p>
          )}
        </div>
      )}
    </div>
  )
}


// ── Test Health Panel ──────────────────────────────────────────────────────────

const HEALTH_COLOUR: Record<string, string> = {
  critical: 'text-red-400 bg-red-900/30',
  warning:  'text-amber-400 bg-amber-900/30',
  info:     'text-[var(--color-text)] bg-white/10',
}

function TestHealthPanel({ runId }: { runId: string }) {
  const { health, isLoading } = useRunTestHealth(runId)
  const [expanded, setExpanded] = useState(false)

  if (isLoading) return null
  if (!health || health.total_analyzed === 0) return null

  const scoreColour = (score: number) => {
    if (score >= 70) return 'text-emerald-400'
    if (score >= 40) return 'text-amber-400'
    return 'text-red-400'
  }

  return (
    <div>
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center gap-2 text-sm font-medium text-[var(--color-text-muted)] uppercase tracking-wider mb-3"
      >
        <HeartPulse className="h-4 w-4" />
        Test Health ({health.total_analyzed} analyzed, {health.with_violations} with issues)
        {health.avg_health_score != null && (
          <span className={clsx('ml-auto text-xs font-mono', scoreColour(health.avg_health_score))}>
            avg {health.avg_health_score}
          </span>
        )}
      </button>
      {expanded && (
        <div className="space-y-2">
          {health.findings.slice(0, 10).map((f) => (
            <div key={f.test_case_id} className="bg-[var(--color-bg-secondary)]/60 rounded-lg px-3 py-2">
              <div className="flex items-center justify-between mb-1">
                <span className="text-xs text-[var(--color-text-secondary)] truncate max-w-[200px]" title={f.test_name}>
                  {f.test_name}
                </span>
                <span className={clsx('text-xs font-mono', scoreColour(f.health_score))}>
                  {f.health_score}
                </span>
              </div>
              {f.violations.length > 0 && (
                <div className="space-y-0.5 mt-1">
                  {f.violations.slice(0, 3).map((v, i) => (
                    <div key={i} className="flex items-center gap-1.5">
                      <span className={clsx('text-[10px] px-1 rounded', HEALTH_COLOUR[v.severity] ?? HEALTH_COLOUR.info)}>
                        {v.severity}
                      </span>
                      <span className="text-[10px] text-[var(--color-text-muted)] truncate">{v.pattern}</span>
                    </div>
                  ))}
                </div>
              )}
              {f.anti_patterns.length > 0 && (
                <div className="flex flex-wrap gap-1 mt-1">
                  {f.anti_patterns.map((p) => (
                    <span key={p} className="text-[10px] px-1.5 py-0.5 bg-purple-900/30 text-purple-400 rounded">
                      {p.replace(/_/g, ' ')}
                    </span>
                  ))}
                </div>
              )}
            </div>
          ))}
          {health.findings.length > 10 && (
            <p className="text-xs text-[var(--color-text-faint)]">+{health.findings.length - 10} more</p>
          )}
        </div>
      )}
    </div>
  )
}


// ── Summary mode toggle ────────────────────────────────────────────────────────

type SummaryMode = 'executive' | 'developer' | 'manager'

function SummaryModeContent({
  runId,
  mode,
  defaultSummary,
}: {
  runId: string
  mode: SummaryMode
  defaultSummary: React.ReactNode
}) {
  const isExecutive = mode === 'executive'
  const { summary, isLoading, isError } = useRunModeSummary(isExecutive ? null : runId, mode)

  if (isExecutive) return <>{defaultSummary}</>
  if (isLoading) return <div className="py-6 text-center"><LoadingSpinner size="sm" /></div>
  if (isError) {
    return (
      <div className="bg-red-900/20 border border-red-700/30 rounded-xl p-4 text-sm text-red-300">
        <p className="font-medium mb-1">Failed to load {mode} summary</p>
        <p className="text-xs text-red-400">The AI pipeline may not have generated a {mode} summary for this run yet. Showing default view.</p>
        <div className="mt-3">{defaultSummary}</div>
      </div>
    )
  }
  if (!summary) return <>{defaultSummary}</>
  return <LayeredSummary summary={summary} mode={mode} />
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function RunIntelligencePage() {
  const { runId } = useParams<{ runId: string }>()
  useProjectChangeRedirect('/intelligence', Boolean(runId))
  const { intelligence, isLoading, isError, refresh } = useRunIntelligence(runId ?? null)
  const [summaryMode, setSummaryMode] = useState<SummaryMode>('executive')
  const [promoteClusterId, setPromoteClusterId] = useState<string | null>(null)
  const [promoteClusterLabel, setPromoteClusterLabel] = useState<string>('')
  const baselineDiffRef = useRef<HTMLDivElement>(null)
  const workflowRef = useRef<HTMLDivElement>(null)
  const [refreshing, setRefreshing] = useState(false)

  async function handleRefresh() {
    if (!runId) return
    setRefreshing(true)
    try {
      const { runIntelligenceService } = await import('@/services/runIntelligenceService')
      await runIntelligenceService.refreshIntelligence(runId)
      await refresh()
      toast.success('Intelligence refreshed')
    } catch {
      toast.error('Failed to refresh intelligence')
    } finally {
      setRefreshing(false)
    }
  }

  if (isLoading) return <LoadingSpinner />
  if (isError || !intelligence) {
    return (
      <EmptyState
        icon={<AlertTriangle className="h-8 w-8 text-red-400" />}
        title="Failed to load Run Intelligence"
        description="Could not fetch AI analysis data for this run."
      />
    )
  }

  const {
    run, structured_summary, failure_clusters, category_breakdown,
    affected_suites, release_decision, role_actions, pipeline_stages,
    intelligence_available, all_green, dimension_scores,
    what_changed_since_last_good_run, defect_candidates, provenance,
  } = intelligence

  const summaryModes: SummaryMode[] = ['executive', 'developer', 'manager']

  return (
    <div className="space-y-6">
      <PageHeader
        title="Run Intelligence"
        subtitle={`Build ${run.build_number}${run.branch ? ` · ${run.branch}` : ''}`}
        actions={
          <div className="flex items-center gap-2">
            <button
              onClick={async () => {
                const { downloadPdf } = await import('@/services/reportExportService')
                downloadPdf(run.id, 'executive').catch(() => toast.error('PDF export failed'))
              }}
              className="btn-secondary text-xs flex items-center gap-1.5"
              title="Export Executive PDF"
            >
              <FileDown className="h-3.5 w-3.5" /> PDF
            </button>
            <button
              onClick={async () => {
                const { downloadEvidenceBundle } = await import('@/services/reportExportService')
                downloadEvidenceBundle(run.id).catch(() => toast.error('Bundle export failed'))
              }}
              className="btn-secondary text-xs flex items-center gap-1.5"
              title="Download Evidence Bundle"
            >
              <Package className="h-3.5 w-3.5" /> Evidence
            </button>
            <button
              onClick={async () => {
                const { createShareLink } = await import('@/services/reportExportService')
                try {
                  const link = await createShareLink(run.id, 'executive')
                  await navigator.clipboard.writeText(link.share_url)
                  toast.success('Share link copied to clipboard')
                } catch { toast.error('Failed to create share link') }
              }}
              className="btn-secondary text-xs flex items-center gap-1.5"
              title="Share Report"
            >
              <Share2 className="h-3.5 w-3.5" /> Share
            </button>
            <Link to={`/runs/${run.id}`} className="btn-secondary text-sm flex items-center gap-2">
              View Test Cases <ChevronRight className="h-4 w-4" />
            </Link>
          </div>
        }
      />

      {/* Sticky action bar */}
      {intelligence_available && !all_green && (
        <div className="sticky top-0 z-10 bg-[var(--color-bg)]/90 backdrop-blur-sm border-b border-[var(--color-border)]/60 -mx-6 px-6 py-2.5 flex items-center gap-2 flex-wrap">
          {summaryModes.map((m) => (
            <button
              key={m}
              onClick={() => setSummaryMode(m)}
              className={clsx(
                'text-xs font-medium px-2.5 py-1 rounded-lg border transition-colors',
                summaryMode === m
                  ? 'bg-white/10 text-[var(--color-text-secondary)] border-[var(--color-border-light)]'
                  : 'bg-[var(--color-bg-secondary)] text-[var(--color-text-muted)] border-[var(--color-border)] hover:text-[var(--color-text)]',
              )}
            >
              {m === 'executive' ? 'Executive' : m === 'developer' ? 'Developer' : 'Manager'}
            </button>
          ))}
          <span className="w-px h-4 bg-[var(--color-bg-secondary)] mx-1" />
          {what_changed_since_last_good_run && (
            <button
              onClick={() => baselineDiffRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })}
              className="flex items-center gap-1 text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)] px-2 py-1 rounded-lg border border-[var(--color-border)] hover:border-[var(--color-border-light)] transition-colors"
            >
              <GitCompare className="h-3 w-3" />
              Baseline
            </button>
          )}
          {pipeline_stages.length > 0 && (
            <button
              onClick={() => workflowRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })}
              className="flex items-center gap-1 text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)] px-2 py-1 rounded-lg border border-[var(--color-border)] hover:border-[var(--color-border-light)] transition-colors"
            >
              <Layers className="h-3 w-3" />
              Workflow
            </button>
          )}
          {failure_clusters.length > 0 && (
            <button
              onClick={() => setPromoteClusterId(failure_clusters[0]?.cluster_id)}
              className="flex items-center gap-1 text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)] px-2 py-1 rounded-lg border border-[var(--color-border)] hover:border-[var(--color-border-light)] transition-colors"
            >
              <TicketCheck className="h-3 w-3" />
              Promote Defect
            </button>
          )}
          {release_decision && (
            <Link
              to={`/release-gate/${run.id}`}
              className="flex items-center gap-1 text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)] px-2 py-1 rounded-lg border border-[var(--color-border)] hover:border-[var(--color-border-light)] transition-colors"
            >
              Release Gate
            </Link>
          )}
          <span className="w-px h-4 bg-[var(--color-bg-secondary)] mx-1" />
          <button
            onClick={handleRefresh}
            disabled={refreshing}
            className="flex items-center gap-1 text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)] px-2 py-1 rounded-lg border border-[var(--color-border)] hover:border-[var(--color-border-light)] transition-colors disabled:opacity-50"
            title="Refresh intelligence"
          >
            <RefreshCw className={clsx('h-3 w-3', refreshing && 'animate-spin')} />
            {refreshing ? 'Refreshing…' : 'Refresh'}
          </button>
        </div>
      )}

      {/* Stale snapshot warning */}
      {intelligence._snapshot?.stale && (
        <div className="flex items-center justify-between gap-2 text-xs text-amber-400 bg-amber-900/20 border border-amber-700/30 rounded px-3 py-2">
          <div className="flex items-center gap-2">
            <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
            This analysis may be outdated — a change occurred after it was generated.
          </div>
          <button
            onClick={handleRefresh}
            disabled={refreshing}
            className="shrink-0 text-amber-300 hover:text-amber-200 font-medium disabled:opacity-50"
          >
            {refreshing ? 'Refreshing…' : 'Refresh now'}
          </button>
        </div>
      )}

      {/* Partial errors banner */}
      {intelligence.partial_errors && (
        <div className="flex items-center gap-2 text-xs text-amber-400 bg-amber-900/20 border border-amber-700/30 rounded px-3 py-2">
          <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
          Some data is temporarily unavailable. Showing available sections.
        </div>
      )}

      {/* Run stats row — hidden when executive panel provides these metrics */}
      {!structured_summary?.executive_panel && <div className="grid grid-cols-4 gap-4">
        <div className="card text-center">
          <p className={clsx('text-3xl font-bold tabular-nums', passRateColour(run.pass_rate ?? 0))}>
            {(run.pass_rate ?? 0).toFixed(1)}%
          </p>
          <p className="text-xs text-[var(--color-text-muted)] mt-1">Pass Rate</p>
        </div>
        <div className="card text-center">
          <p className="text-3xl font-bold text-[var(--color-text)]">{run.total_tests}</p>
          <p className="text-xs text-[var(--color-text-muted)] mt-1">Total Tests</p>
        </div>
        <div className="card text-center">
          <p className={clsx('text-3xl font-bold', all_green ? 'text-emerald-400' : 'text-red-400')}>{run.failed_tests}</p>
          <p className="text-xs text-[var(--color-text-muted)] mt-1">{all_green ? 'All Passed' : 'Failures'}</p>
        </div>
        <div className="card text-center">
          <p className="text-3xl font-bold text-[var(--color-text)]">{failure_clusters.length}</p>
          <p className="text-xs text-[var(--color-text-muted)] mt-1">Failure Clusters</p>
        </div>
      </div>}

      {/* All-green fast path notice */}
      {all_green && (
        <div className="card border border-emerald-700/40 bg-emerald-900/20 flex items-center gap-3 py-3">
          <CheckCircle className="h-5 w-5 text-emerald-400 flex-shrink-0" />
          <p className="text-sm text-emerald-300">All tests passed — analysis stages were skipped (no AI work required).</p>
        </div>
      )}

      {/* Release gate banner */}
      {release_decision && <ReleaseGateBanner decision={release_decision} />}

      {/* What Changed panel */}
      {what_changed_since_last_good_run && !all_green && (
        <div ref={baselineDiffRef}>
          <WhatChangedPanel diff={what_changed_since_last_good_run} />
        </div>
      )}

      {pipeline_stages.length > 0 && (
        <div ref={workflowRef} className="card space-y-3">
          <div className="flex items-start justify-between gap-3 flex-wrap">
            <div>
              <p className="text-sm font-semibold text-[var(--color-text)]">Workflow Progress</p>
              <p className="text-xs text-[var(--color-text-muted)]">
                How the AI pipeline moved through ingestion, analysis, clustering, triage, and release scoring.
              </p>
            </div>
            <span className="text-xs text-[var(--color-text-muted)]">
              {pipeline_stages.length} stages · {pipeline_stages.filter(s => s.status === 'completed').length} completed
            </span>
          </div>
          <WorkflowTimeline
            title=""
            stages={pipeline_stages.map(stage => ({
              ...stage,
              label: stage.stage_name.replace(/_/g, ' '),
              description: stage.execution_path
                ? `Path: ${stage.execution_path.replace(/_/g, ' ')}`
                : stage.skipped_reason ?? 'AI workflow stage',
            }))}
            compact
          />
        </div>
      )}

      {!intelligence_available && !all_green && (
        <div className="card text-center py-10">
          <Bot className="h-10 w-10 text-[var(--color-text-faint)] mx-auto mb-3" />
          <p className="text-[var(--color-text-muted)] font-medium">AI analysis not yet available</p>
          <p className="text-sm text-[var(--color-text-muted)] mt-1">
            Trigger the AI pipeline from the Test Runs page to generate intelligence.
          </p>
        </div>
      )}

      {intelligence_available && (
        <div className="grid grid-cols-3 gap-6">
          {/* Left: Summary + clusters (2 cols) */}
          <div className="col-span-2 space-y-6">
            {structured_summary && (
              <div>
                <p className="text-sm font-medium text-[var(--color-text-muted)] mb-3 uppercase tracking-wider">AI Analysis</p>
                {runId && (
                  <SummaryModeContent
                    runId={runId}
                    mode={summaryMode}
                    defaultSummary={<LayeredSummary summary={structured_summary} />}
                  />
                )}
                {provenance?.fallback_used && (
                  <p className="text-xs text-[var(--color-text-faint)] mt-2 flex items-center gap-1">
                    <AlertCircle className="h-3 w-3" />
                    Deterministic fallback — LLM was unavailable when this summary was generated.
                  </p>
                )}
              </div>
            )}

            {/* Failure clusters */}
            {failure_clusters.length > 0 && (
              <div>
                <p className="text-sm font-medium text-[var(--color-text-muted)] mb-3 uppercase tracking-wider">
                  Failure Clusters ({failure_clusters.length})
                </p>
                <div className="space-y-2">
                  {failure_clusters.map((c) => (
                    <ClusterCard
                      key={c.id}
                      cluster={c}
                      onPromote={(cid, label) => {
                        setPromoteClusterId(cid)
                        setPromoteClusterLabel(label)
                      }}
                    />
                  ))}
                </div>
              </div>
            )}

            {/* Defect candidates — promote top clusters */}
            {defect_candidates && defect_candidates.length > 0 && (
              <div>
                <p className="text-sm font-medium text-[var(--color-text-muted)] mb-3 uppercase tracking-wider">
                  Defect Candidates
                </p>
                <div className="space-y-2">
                  {defect_candidates.map((dc) => {
                    const status = dc.status
                    const dupDetected = dc.duplicate_detected
                    const promotedId = dc.promoted_defect_id
                    return (
                      <div
                        key={dc.cluster_id}
                        className={clsx('card p-3 flex items-center justify-between', status === 'promoted' && 'border-emerald-700/40 bg-emerald-900/10')}
                      >
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2">
                            <Layers className="h-4 w-4 text-[var(--color-text-muted)] shrink-0" />
                            <span className="text-sm text-[var(--color-text)] truncate">{dc.label}</span>
                            {/* Lifecycle badge */}
                            {status === 'promoted' && (
                              <span className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-900/40 text-emerald-400 border border-emerald-700/40">Promoted</span>
                            )}
                            {status === 'dismissed' && (
                              <span className="text-[10px] px-1.5 py-0.5 rounded bg-[var(--color-bg-secondary)] text-[var(--color-text-muted)]">Dismissed</span>
                            )}
                            {dupDetected && status !== 'promoted' && (
                              <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-900/30 text-amber-400 border border-amber-700/30">Likely Duplicate</span>
                            )}
                            {!status && !dupDetected && (
                              <span className="text-[10px] px-1.5 py-0.5 rounded bg-white/10 text-[var(--color-text)]">Draft</span>
                            )}
                          </div>
                          <div className="flex items-center gap-2 mt-1 pl-6">
                            <span className={clsx('text-xs', dc.failure_category === 'PRODUCT_BUG' ? 'text-red-400' : 'text-[var(--color-text-muted)]')}>
                              {dc.failure_category.replace('_', ' ')}
                            </span>
                            <span className="text-xs text-[var(--color-text-muted)]">·</span>
                            <span className="text-xs text-[var(--color-text-muted)]">{dc.severity_hint}</span>
                            {dc.confidence > 0 && (
                              <>
                                <span className="text-xs text-[var(--color-text-muted)]">·</span>
                                <span className="text-xs text-[var(--color-text-muted)]">{dc.confidence}% confidence</span>
                              </>
                            )}
                          </div>
                        </div>
                        <div className="flex items-center gap-3 ml-3 shrink-0">
                          <Link
                            to={`/search?q=${encodeURIComponent(dc.label)}&search_type=semantic`}
                            className="text-[10px] text-[var(--color-text-secondary)] hover:text-[var(--color-text)] transition-colors"
                            onClick={(e) => e.stopPropagation()}
                          >
                            Find similar
                          </Link>
                          {runId && status !== 'promoted' && (
                            <button
                              onClick={() => {
                                setPromoteClusterId(dc.cluster_id)
                                setPromoteClusterLabel(dc.label)
                              }}
                              className="flex items-center gap-1.5 text-xs text-[var(--color-text)] hover:text-[var(--color-text-secondary)] transition-colors shrink-0"
                            >
                              <TicketCheck className="h-3.5 w-3.5" />
                              {status === 'pending' ? 'Promote' : 'Promote'}
                            </button>
                          )}
                          {promotedId && (
                            <span className="text-[10px] text-emerald-500">Jira linked</span>
                          )}
                        </div>
                      </div>
                    )
                  })}
                </div>
              </div>
            )}

            {/* Affected suites */}
            {affected_suites.length > 0 && (
              <div>
                <p className="text-sm font-medium text-[var(--color-text-muted)] mb-3 uppercase tracking-wider">Affected Suites</p>
                <div className="space-y-1.5">
                  {affected_suites.map((s) => (
                    <div key={s.suite} className="flex items-center justify-between bg-[var(--color-bg-secondary)]/60 rounded-lg px-3 py-2">
                      <span className="text-sm text-[var(--color-text-secondary)]">{s.suite}</span>
                      <span className="text-sm font-mono text-red-400">{s.failed_count} failures</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>

          {/* Right: sidebar (1 col) */}
          <div className="space-y-5">
            {/* Dimension matrix */}
            <CriticalityMatrix
              dimensionScores={dimension_scores}
              title="Risk Dimension Scores"
            />

            {/* Category breakdown */}
            {Object.keys(category_breakdown).length > 0 && (
              <div>
                <p className="text-sm font-medium text-[var(--color-text-muted)] mb-3 uppercase tracking-wider">By Category</p>
                <div className="space-y-2">
                  {Object.entries(category_breakdown)
                    .sort(([, a], [, b]) => b - a)
                    .map(([cat, count]) => (
                      <div key={cat} className="flex items-center justify-between">
                        <span className={clsx('badge text-xs border-0', CATEGORY_COLOUR[cat] ?? CATEGORY_COLOUR.UNKNOWN)}>
                          {cat.replace('_', ' ')}
                        </span>
                        <span className="text-sm font-mono text-[var(--color-text-muted)]">{count}</span>
                      </div>
                    ))}
                </div>
              </div>
            )}

            {/* Role-aware actions */}
            {Object.keys(role_actions).length > 0 && (
              <div>
                <p className="text-sm font-medium text-[var(--color-text-muted)] mb-3 uppercase tracking-wider">Actions by Role</p>
                <RoleActionCardShared roleActions={role_actions} compact />
              </div>
            )}

            {/* Pipeline stages */}
            {pipeline_stages.length > 0 && (
              <div>
                <p className="text-sm font-medium text-[var(--color-text-muted)] mb-3 uppercase tracking-wider">Pipeline Stages</p>
                <div className="space-y-1.5">
                  {pipeline_stages.map((s) => (
                    <div key={s.stage_name} className="space-y-0.5">
                      <div className="flex items-center gap-2 text-sm">
                        {s.status === 'completed'
                          ? <CheckCircle className="h-4 w-4 text-emerald-400 flex-shrink-0" />
                          : s.status === 'skipped'
                            ? <div className="h-4 w-4 rounded-full border border-[var(--color-border)] flex-shrink-0" />
                            : s.status === 'failed'
                              ? <XCircle className="h-4 w-4 text-red-400 flex-shrink-0" />
                              : <div className="h-4 w-4 rounded-full bg-[var(--color-bg-hover)] flex-shrink-0" />}
                        <span className={
                          s.status === 'skipped' ? 'text-[var(--color-text-faint)]' :
                          s.status === 'completed' ? 'text-[var(--color-text-secondary)]' :
                          s.status === 'failed' ? 'text-red-400' : 'text-[var(--color-text-muted)]'
                        }>
                          {s.stage_name.replace(/_/g, ' ')}
                        </span>
                      </div>
                      {s.skipped_reason && s.status === 'skipped' && (
                        <p className="text-[10px] text-[var(--color-text-faint)] pl-6">{s.skipped_reason}</p>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Test Health Coach */}
            {runId && !all_green && (
              <TestHealthPanel runId={runId} />
            )}

            {/* Provenance & Confidence */}
            {provenance && (
              <div className="bg-[var(--color-bg-card)]/60 rounded-lg p-3 space-y-2">
                <p className="text-[10px] font-semibold uppercase tracking-widest text-[var(--color-text-faint)]">AI Provenance</p>

                {/* Confidence strip */}
                {provenance.confidence != null && (
                  <div className="space-y-1">
                    <div className="flex items-center justify-between">
                      <span className="text-[10px] text-[var(--color-text-muted)]">Confidence</span>
                      <span className={clsx('text-xs font-bold', provenance.confidence >= 70 ? 'text-emerald-400' : provenance.confidence >= 40 ? 'text-amber-400' : 'text-red-400')}>
                        {provenance.confidence}%
                      </span>
                    </div>
                    <div className="w-full h-1 bg-[var(--color-bg-secondary)] rounded-full overflow-hidden">
                      <div
                        className={clsx('h-full rounded-full', provenance.confidence >= 70 ? 'bg-emerald-500' : provenance.confidence >= 40 ? 'bg-amber-500' : 'bg-red-500')}
                        style={{ width: `${provenance.confidence}%` }}
                      />
                    </div>
                    {provenance.confidence_reason && (
                      <p className="text-[10px] text-[var(--color-text-faint)]">{provenance.confidence_reason}</p>
                    )}
                  </div>
                )}

                {/* Source chips */}
                {provenance.sources_used.length > 0 && (
                  <div>
                    <p className="text-[10px] text-[var(--color-text-faint)] mb-1">Sources</p>
                    <div className="flex flex-wrap gap-1">
                      {provenance.sources_used.map(s => (
                        <span key={s} className="text-[9px] px-1.5 py-0.5 bg-white/10 text-[var(--color-text)] rounded">{s}</span>
                      ))}
                    </div>
                  </div>
                )}

                {/* Deterministic checks */}
                {provenance.deterministic_checks_used.length > 0 && (
                  <div>
                    <p className="text-[10px] text-[var(--color-text-faint)] mb-1">Checks</p>
                    <div className="flex flex-wrap gap-1">
                      {provenance.deterministic_checks_used.map(c => (
                        <span key={c} className="text-[9px] px-1.5 py-0.5 bg-emerald-900/30 text-emerald-400 rounded">{c.replace(/_/g, ' ')}</span>
                      ))}
                    </div>
                  </div>
                )}

                {/* Meta */}
                <div className="flex items-center gap-2 text-[10px] text-[var(--color-text-faint)] pt-1 border-t border-[var(--color-border)]/50">
                  <span>v{provenance.schema_version}</span>
                  <span>{provenance.evidence_count} evidence items</span>
                  <span>{provenance.tools_used_count} tools</span>
                </div>
                {provenance.fallback_used && (
                  <p className="text-[10px] text-amber-700">Deterministic fallback — LLM was unavailable</p>
                )}
              </div>
            )}
          </div>
        </div>
      )}

      {/* Defect Promotion Modal */}
      {promoteClusterId && runId && (
        <DefectPromotionModal
          runId={runId}
          clusterId={promoteClusterId}
          clusterLabel={promoteClusterLabel}
          onClose={() => setPromoteClusterId(null)}
          onSuccess={(_defectId, jiraUrl) => {
            toast.success(
              jiraUrl ? 'Defect promoted and Jira ticket created' : 'Defect promoted',
            )
            setPromoteClusterId(null)
          }}
        />
      )}
    </div>
  )
}
