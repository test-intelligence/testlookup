import { useState } from 'react'
import { BarChart2, CheckCircle2, ChevronRight, ChevronUp, Clock, Download, FileText, Sparkles } from 'lucide-react'
import toast from 'react-hot-toast'
import EmptyState from '@/components/ui/EmptyState'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { useStrategies } from '@/hooks/useTestManagement'
import { testManagementService } from '@/services/testManagementService'
import type { TestStrategy } from '@/types/test-management'
import { PRIORITY_COLORS, fmtDate } from './format'
import { ModalWrap, StatusPill } from './tmUi'

// ─── Tab: Strategy (under More ▾ since UX redesign P4) ───────────────────────

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

export default function StrategyTab({ projectId }: StrategyTabProps) {
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
