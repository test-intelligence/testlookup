import { useEffect, useState } from 'react'
import { AlertTriangle, ArrowLeft, CheckCircle2, RefreshCw, Save, WifiOff, XCircle } from 'lucide-react'
import { Link } from 'react-router-dom'
import toast from 'react-hot-toast'
import PageHeader from '@/components/ui/PageHeader'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import {
  appSettingsService,
  type AIConfigRead,
  type AIConfigUpdate,
  type AIModelStatusRead,
  type FallbackChainEntry,
} from '@/services/appSettingsService'
import { usePermissions } from '@/hooks/usePermissions'
import { activeTier, useAIModelStatus } from '@/hooks/useAIConfig'

const LLM_PROVIDERS = ['ollama', 'openai', 'gemini', 'lmstudio', 'localai', 'vllm']

// Descriptions state what each engine *is*. Availability is never asserted
// here — it comes from the live fallback chain below (US-13.2). The old
// hardcoded "LLM if available" strings claimed a runtime fact the page had
// never actually checked.
const ANALYSIS_MODES = [
  { value: 'auto', label: 'Auto', desc: 'Resolved per run: first available tier of ML → LLM → Rules' },
  { value: 'llm', label: 'LLM (AI Agent)', desc: 'Full LangChain ReAct agent against the configured model' },
  { value: 'ml', label: 'Machine Learning', desc: 'Trained ML classifier — no LLM needed' },
  { value: 'rules', label: 'Rules-Based', desc: 'Pattern matching + statistics — zero dependencies' },
] as const

/**
 * Shown whenever the backend reports `ai_offline_mode_env_pinned`. Names the
 * exact lever (an environment variable, not a UI setting) and the exact remedy,
 * because the operator who needs this string is usually the one wondering why
 * their click did nothing.
 */
const OFFLINE_PINNED_REASON =
  'Pinned by AI_OFFLINE_MODE in this deployment’s environment — offline mode cannot be ' +
  'turned off from this page. Set AI_OFFLINE_MODE=false in the backend environment and ' +
  'restart to permit cloud LLM egress.'

const TIER_LABELS: Record<string, string> = {
  ml: 'Machine Learning',
  llm: 'LLM (AI Agent)',
  rules: 'Rules-Based',
}

const OK_CLS = 'bg-[var(--status-passed-bg)] text-[var(--status-passed)] border-[var(--status-passed-bd)]'
const BAD_CLS = 'bg-[var(--status-failed-bg)] text-[var(--status-failed)] border-[var(--status-failed-bd)]'
const WARN_CLS = 'bg-[var(--status-broken-bg)] text-[var(--status-broken)] border-[var(--status-broken-bd)]'

function StatusChip({ ok, children }: { ok: boolean; children: React.ReactNode }) {
  return (
    <span className={`text-[10px] font-medium px-1.5 py-0.5 rounded border ${ok ? OK_CLS : BAD_CLS}`}>
      {children}
    </span>
  )
}

/** One tier of the live fallback chain. */
function ChainRow({ entry, isActive }: { entry: FallbackChainEntry; isActive: boolean }) {
  const Icon = entry.available ? CheckCircle2 : XCircle
  return (
    <li className="flex items-start gap-2 py-1.5" data-testid={`chain-${entry.mode}`}>
      <Icon
        className={`mt-0.5 h-4 w-4 flex-shrink-0 ${entry.available ? 'text-[var(--status-passed)]' : 'text-[var(--status-failed)]'}`}
        aria-hidden="true"
      />
      <div className="min-w-0">
        <span className="text-sm text-[var(--color-text)]">{TIER_LABELS[entry.mode] ?? entry.mode}</span>
        <span className="ml-2 text-xs text-[var(--color-text-muted)]">
          {entry.available ? 'available' : 'unavailable'}
        </span>
        {isActive && (
          <span className={`ml-2 text-[10px] font-medium px-1.5 py-0.5 rounded border ${OK_CLS}`}>
            Active
          </span>
        )}
        {entry.reason && (
          <p className="text-xs text-[var(--color-text-muted)] mt-0.5 break-words">{entry.reason}</p>
        )}
      </div>
    </li>
  )
}

/**
 * Live model presence + fallback chain (US-13.2).
 *
 * The three states this card exists to keep apart:
 *   1. backend unreachable → we know nothing, and say so;
 *   2. Ollama unreachable → connectivity problem, no pull recipe offered;
 *   3. Ollama up but a model is missing → model-pack import problem, with
 *      the exact remedy for this runtime.
 */
function ModelStatusCard({
  status,
  loading,
  failed,
  onRefresh,
}: {
  status: AIModelStatusRead | undefined
  loading: boolean
  failed: boolean
  onRefresh: () => void
}) {
  const active = activeTier(status)

  return (
    <div className="card space-y-4">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h3 className="text-sm font-semibold text-[var(--color-text)]">Model Availability &amp; Fallback Chain</h3>
          <p className="text-xs text-[var(--color-text-muted)] mt-0.5">
            Live check of the configured backend — what is installed right now, and which
            analysis tier will actually run.
          </p>
        </div>
        <button
          type="button"
          onClick={onRefresh}
          className="btn-secondary text-xs flex items-center gap-1.5 flex-shrink-0"
        >
          <RefreshCw className="h-3.5 w-3.5" /> Re-check
        </button>
      </div>

      {loading && !status && <p className="text-xs text-[var(--color-text-muted)]">Checking model backend…</p>}

      {failed && (
        <div className={`flex items-start gap-2 px-3 py-2 rounded-lg border text-xs ${WARN_CLS}`}>
          <AlertTriangle className="mt-0.5 h-4 w-4 flex-shrink-0" aria-hidden="true" />
          <span>
            Could not read model status from the API. This says nothing about your models —
            only that this page could not check them.
          </span>
        </div>
      )}

      {status && (
        <>
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <StatusChip ok={status.offline_mode}>
              {status.offline_mode ? 'Offline mode: on (local models only)' : 'Offline mode: off (cloud allowed)'}
            </StatusChip>
            <span className="text-[var(--color-text-muted)]">
              Provider <span className="text-[var(--color-text)]">{status.llm_provider || 'none'}</span>
            </span>
            <span className="text-[var(--color-text-muted)]">
              Mode <span className="text-[var(--color-text)]">{status.analysis_mode}</span>
            </span>
          </div>

          {/* Reachability — kept strictly separate from model presence. */}
          {status.ollama_reachable ? (
            <p className="text-xs text-[var(--color-text-muted)] flex items-center gap-1.5">
              <CheckCircle2 className="h-3.5 w-3.5 text-[var(--status-passed)]" aria-hidden="true" />
              Ollama reachable at <code className="text-[var(--color-text)]">{status.ollama_base_url}</code> —{' '}
              {status.installed_models.length} model{status.installed_models.length === 1 ? '' : 's'} installed
            </p>
          ) : (
            <div className={`flex items-start gap-2 px-3 py-2 rounded-lg border text-xs ${BAD_CLS}`}>
              <WifiOff className="mt-0.5 h-4 w-4 flex-shrink-0" aria-hidden="true" />
              <div>
                <span className="font-medium">Ollama unreachable at {status.ollama_base_url}.</span>{' '}
                <span className="text-[var(--color-text-muted)]">
                  {status.ollama_error ?? 'No detail reported.'} This is a connectivity problem —
                  it does not tell you whether your models are installed.
                </span>
              </div>
            </div>
          )}

          {/* Required models */}
          {status.required.length > 0 && (
            <div className="space-y-2">
              <h4 className="text-xs font-semibold text-[var(--color-text-secondary)]">Required models</h4>
              <ul className="space-y-1.5">
                {status.required.map(m => (
                  <li key={`${m.purpose}-${m.name}`} className="text-xs" data-testid={`required-${m.purpose}`}>
                    <div className="flex flex-wrap items-center gap-2">
                      <code className="text-[var(--color-text)]">{m.name}</code>
                      <span className="text-[var(--color-text-muted)]">{m.purpose}</span>
                      <StatusChip ok={m.present}>{m.present ? 'Installed' : 'Missing'}</StatusChip>
                    </div>
                    {m.remedy && (
                      <p className="text-[var(--color-text-muted)] mt-0.5 break-words">{m.remedy}</p>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Fallback chain */}
          <div className="space-y-1">
            <h4 className="text-xs font-semibold text-[var(--color-text-secondary)]">Fallback chain</h4>
            <ul className="divide-y divide-[var(--color-border)]">
              {status.fallback_chain.map(entry => (
                <ChainRow key={entry.mode} entry={entry} isActive={active?.mode === entry.mode} />
              ))}
            </ul>
            <p className="text-xs text-[var(--color-text-muted)]">
              Rules is the terminal fallback — analysis never stops, it degrades. Air-gapped
              installs side-load models with the offline model pack procedure
              (<code>user-guide/offline-model-pack.md</code>).
            </p>
          </div>
        </>
      )}
    </div>
  )
}

export default function AIConfigPage() {
  const { isAdmin } = usePermissions()
  const {
    data: modelStatus,
    error: modelStatusError,
    isLoading: modelStatusLoading,
    mutate: refreshModelStatus,
  } = useAIModelStatus()
  const [config, setConfig] = useState<AIConfigRead | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [form, setForm] = useState<AIConfigUpdate>({})

  useEffect(() => {
    appSettingsService.getAIConfig()
      .then(cfg => {
        setConfig(cfg)
        setForm({
          llm_provider: cfg.llm_provider,
          llm_model: cfg.llm_model,
          llm_temperature: cfg.llm_temperature,
          llm_max_tokens: cfg.llm_max_tokens,
          ai_offline_mode: cfg.ai_offline_mode,
          embedding_provider: cfg.embedding_provider,
          embedding_model: cfg.embedding_model,
          ai_confidence_threshold: cfg.ai_confidence_threshold,
          ai_timeout_seconds: cfg.ai_timeout_seconds,
          deep_investigation_enabled: cfg.deep_investigation_enabled,
          finetune_enabled: cfg.finetune_enabled,
          analysis_mode: cfg.analysis_mode,
          knowledge_rag_enabled: cfg.knowledge_rag_enabled,
        })
      })
      .catch(() => setError('Failed to load AI configuration'))
      .finally(() => setLoading(false))
  }, [])

  async function handleSave(e: React.FormEvent) {
    e.preventDefault()
    setSaving(true)
    try {
      const updated = await appSettingsService.updateAIConfig(form)
      setConfig(updated)
      toast.success('AI configuration saved')
    } catch {
      toast.error('Failed to save AI configuration')
    } finally {
      setSaving(false)
    }
  }

  function upd(field: keyof AIConfigUpdate, value: unknown) {
    setForm(prev => ({ ...prev, [field]: value }))
  }

  if (loading) return <div className="flex justify-center py-16"><LoadingSpinner size="lg" /></div>
  if (error) return <div className="card text-red-400 text-sm">{error}</div>
  if (!config) return null

  const llmTier = modelStatus?.fallback_chain.find(e => e.mode === 'llm')
  const offlinePinned = config.ai_offline_mode_env_pinned === true

  return (
    <div className="space-y-6">
      <PageHeader
        title="AI Configuration"
        subtitle="LLM provider, model selection, and AI pipeline settings"
        actions={
          <Link to="/settings" className="btn-secondary text-sm flex items-center gap-2">
            <ArrowLeft className="h-4 w-4" /> Back
          </Link>
        }
      />
      <form onSubmit={handleSave} className="space-y-6 max-w-2xl">
        {/* Analysis Engine Mode */}
        <div className="card space-y-4">
          <h3 className="text-sm font-semibold text-[var(--color-text)]">Analysis Engine</h3>
          <p className="text-xs text-[var(--color-text-muted)]">
            Choose how test failures are classified and summarized.
            Non-LLM modes work without any external AI service.
          </p>
          <div className="space-y-2">
            {ANALYSIS_MODES.map(mode => (
              <label
                key={mode.value}
                className={`flex items-start gap-3 p-3 rounded-lg border cursor-pointer transition-colors ${
                  form.analysis_mode === mode.value
                    ? 'border-[var(--color-ring)] bg-[var(--color-ring)]/5'
                    : 'border-[var(--color-border)] hover:border-[var(--color-border-light)]'
                } ${!isAdmin ? 'opacity-50 cursor-not-allowed' : ''}`}
              >
                <input
                  type="radio"
                  name="analysis_mode"
                  value={mode.value}
                  checked={form.analysis_mode === mode.value}
                  onChange={() => upd('analysis_mode', mode.value)}
                  disabled={!isAdmin}
                  className="mt-0.5"
                />
                <div>
                  <span className="text-sm font-medium text-[var(--color-text)]">{mode.label}</span>
                  {/* Live LLM-tier state, straight from the probe — the page
                      used to assert "requires running LLM" and never look. */}
                  {mode.value === 'llm' && llmTier && !llmTier.available && (
                    <span className={`ml-2 text-[10px] font-medium px-1.5 py-0.5 rounded border ${BAD_CLS}`}>
                      {modelStatus?.ollama_reachable === false ? 'Unreachable' : 'Model Missing'}
                    </span>
                  )}
                  {mode.value === 'llm' && llmTier?.available && (
                    <span className={`ml-2 text-[10px] font-medium px-1.5 py-0.5 rounded border ${OK_CLS}`}>
                      Ready
                    </span>
                  )}
                  {mode.value === 'ml' && !config.ml_model_available && (
                    <span className="ml-2 text-[10px] font-medium px-1.5 py-0.5 rounded bg-amber-500/20 text-amber-400">
                      Not Trained
                    </span>
                  )}
                  {mode.value === 'ml' && config.ml_model_available && (
                    <span className="ml-2 text-[10px] font-medium px-1.5 py-0.5 rounded bg-emerald-500/20 text-emerald-400">
                      Ready ({((config.ml_model_accuracy ?? 0) * 100).toFixed(0)}% accuracy)
                    </span>
                  )}
                  {mode.value === 'ml' && config.ml_model_available && config.ml_maturity !== 'human_calibrated' && (
                    <span className="ml-2 text-[10px] font-medium px-1.5 py-0.5 rounded bg-amber-500/20 text-amber-400">
                      Bootstrap (LLM-imitating)
                    </span>
                  )}
                  <p className="text-xs text-[var(--color-text-muted)] mt-0.5">{mode.desc}</p>
                </div>
              </label>
            ))}
          </div>
          {/* ML Model Status Banner */}
          {form.analysis_mode === 'ml' && !config.ml_model_available && (
            <div className="flex items-start gap-2 px-3 py-2 rounded-lg bg-amber-500/10 border border-amber-500/20 text-xs text-amber-300">
              <span className="font-medium">ML model not yet trained.</span>
              <span className="text-[var(--color-text-muted)]">
                Need {config.ml_training_sample_count} / 200 labeled samples.
                The system will use Rules mode as fallback until a model is trained.
              </span>
            </div>
          )}
          {/* AI-F1 honesty caveat: below the human-label floor, ML mode imitates
              the LLM's own labels — do not claim it learns from corrections. */}
          {(form.analysis_mode === 'ml' || form.analysis_mode === 'auto') &&
            config.ml_model_available && config.ml_maturity !== 'human_calibrated' && (
            <div className="flex flex-col gap-1 px-3 py-2 rounded-lg bg-amber-500/10 border border-amber-500/20 text-xs text-amber-300">
              <span className="font-medium">ML classifier is in bootstrap mode (LLM-imitating).</span>
              <span className="text-[var(--color-text-muted)]">
                The current model was trained mostly on the LLM&apos;s own high-confidence
                verdicts, not on human-verified labels
                ({config.ml_human_label_count} of {config.ml_human_label_floor} human
                labels needed). Until your team confirms or corrects more AI verdicts,
                ML mode largely reproduces the LLM&apos;s behavior rather than learning
                from your corrections. Label composition is tracked per model on the
                AI Evaluation dashboard.
              </span>
            </div>
          )}
        </div>

        <ModelStatusCard
          status={modelStatus}
          loading={modelStatusLoading}
          failed={Boolean(modelStatusError)}
          onRefresh={() => { void refreshModelStatus() }}
        />

        {/* LLM Provider */}
        <div className="card space-y-4">
          <h3 className="text-sm font-semibold text-[var(--color-text)]">LLM Provider</h3>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-xs text-[var(--color-text-muted)] mb-1">Provider</label>
              <select value={form.llm_provider ?? ''} onChange={e => upd('llm_provider', e.target.value)} disabled={!isAdmin}
                className="w-full bg-[var(--color-bg-card)] border border-[var(--color-border)] text-[var(--color-text)] text-sm rounded px-3 py-2 disabled:opacity-50">
                {LLM_PROVIDERS.map(p => <option key={p} value={p}>{p}</option>)}
              </select>
            </div>
            <div>
              <label className="block text-xs text-[var(--color-text-muted)] mb-1">Model</label>
              <input value={form.llm_model ?? ''} onChange={e => upd('llm_model', e.target.value)} disabled={!isAdmin}
                className="w-full bg-[var(--color-bg-card)] border border-[var(--color-border)] text-[var(--color-text)] text-sm rounded px-3 py-2 disabled:opacity-50"
                placeholder="e.g. qwen2.5:7b" />
            </div>
            <div>
              <label className="block text-xs text-[var(--color-text-muted)] mb-1">Temperature (0.0 - 2.0)</label>
              <input type="number" step="0.1" min="0" max="2" value={form.llm_temperature ?? 0.1} disabled={!isAdmin}
                onChange={e => upd('llm_temperature', parseFloat(e.target.value))}
                className="w-full bg-[var(--color-bg-card)] border border-[var(--color-border)] text-[var(--color-text)] text-sm rounded px-3 py-2 disabled:opacity-50" />
            </div>
            <div>
              <label className="block text-xs text-[var(--color-text-muted)] mb-1">Max Tokens</label>
              <input type="number" min="256" max="32768" value={form.llm_max_tokens ?? 4096} disabled={!isAdmin}
                onChange={e => upd('llm_max_tokens', parseInt(e.target.value))}
                className="w-full bg-[var(--color-bg-card)] border border-[var(--color-border)] text-[var(--color-text)] text-sm rounded px-3 py-2 disabled:opacity-50" />
            </div>
          </div>
        </div>

        {/* Embedding */}
        <div className="card space-y-4">
          <h3 className="text-sm font-semibold text-[var(--color-text)]">Embedding</h3>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-xs text-[var(--color-text-muted)] mb-1">Provider</label>
              <input value={form.embedding_provider ?? ''} onChange={e => upd('embedding_provider', e.target.value)} disabled={!isAdmin}
                className="w-full bg-[var(--color-bg-card)] border border-[var(--color-border)] text-[var(--color-text)] text-sm rounded px-3 py-2 disabled:opacity-50" />
            </div>
            <div>
              <label className="block text-xs text-[var(--color-text-muted)] mb-1">Model</label>
              <input value={form.embedding_model ?? ''} onChange={e => upd('embedding_model', e.target.value)} disabled={!isAdmin}
                className="w-full bg-[var(--color-bg-card)] border border-[var(--color-border)] text-[var(--color-text)] text-sm rounded px-3 py-2 disabled:opacity-50" />
            </div>
          </div>
        </div>

        {/* Pipeline Settings */}
        <div className="card space-y-4">
          <h3 className="text-sm font-semibold text-[var(--color-text)]">Pipeline Settings</h3>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-xs text-[var(--color-text-muted)] mb-1">Confidence Threshold (0-100)</label>
              <input type="number" min="0" max="100" value={form.ai_confidence_threshold ?? 80} disabled={!isAdmin}
                onChange={e => upd('ai_confidence_threshold', parseInt(e.target.value))}
                className="w-full bg-[var(--color-bg-card)] border border-[var(--color-border)] text-[var(--color-text)] text-sm rounded px-3 py-2 disabled:opacity-50" />
            </div>
            <div>
              <label className="block text-xs text-[var(--color-text-muted)] mb-1">Timeout (seconds)</label>
              <input type="number" min="30" max="1800" value={form.ai_timeout_seconds ?? 300} disabled={!isAdmin}
                onChange={e => upd('ai_timeout_seconds', parseInt(e.target.value))}
                className="w-full bg-[var(--color-bg-card)] border border-[var(--color-border)] text-[var(--color-text)] text-sm rounded px-3 py-2 disabled:opacity-50" />
            </div>
          </div>
          <div className="flex flex-wrap gap-6">
            {/* AI_OFFLINE_MODE in the environment is a hard ceiling on outbound
                LLM egress: this toggle can tighten it, never loosen it. When the
                env pins it on, the control is disabled and says why — silently
                accepting a click the backend will refuse (409) is how an operator
                ends up believing cloud LLM is enabled when it is not. */}
            <label
              className={`flex items-center gap-2 text-sm text-[var(--color-text-secondary)] ${
                offlinePinned ? 'cursor-not-allowed' : 'cursor-pointer'
              }`}
              title={offlinePinned ? OFFLINE_PINNED_REASON : undefined}
            >
              <input type="checkbox" checked={form.ai_offline_mode ?? true} disabled={!isAdmin || offlinePinned}
                onChange={e => upd('ai_offline_mode', e.target.checked)}
                className="rounded bg-[var(--color-bg-secondary)] border-[var(--color-border-light)]" />
              Offline Mode (air-gapped, Ollama only)
              {offlinePinned && (
                <span className={`text-[10px] font-medium px-1.5 py-0.5 rounded border ${OK_CLS}`}>
                  Pinned by environment
                </span>
              )}
            </label>
            <label className="flex items-center gap-2 text-sm text-[var(--color-text-secondary)] cursor-pointer">
              <input type="checkbox" checked={form.deep_investigation_enabled ?? true} disabled={!isAdmin}
                onChange={e => upd('deep_investigation_enabled', e.target.checked)}
                className="rounded bg-[var(--color-bg-secondary)] border-[var(--color-border-light)]" />
              Deep Investigation
            </label>
            <label className="flex items-center gap-2 text-sm text-[var(--color-text-secondary)] cursor-pointer">
              <input type="checkbox" checked={form.finetune_enabled ?? false} disabled={!isAdmin}
                onChange={e => upd('finetune_enabled', e.target.checked)}
                className="rounded bg-[var(--color-bg-secondary)] border-[var(--color-border-light)]" />
              Fine-Tuning Pipeline
            </label>
          </div>
          {offlinePinned && (
            <p
              className="text-xs text-[var(--color-text-muted)]"
              data-testid="offline-mode-pinned-note"
            >
              {OFFLINE_PINNED_REASON}
            </p>
          )}
        </div>

        {/* Knowledge RAG */}
        <div className="card space-y-4">
          <h3 className="text-sm font-semibold text-[var(--color-text)]">Knowledge-Grounded Generation</h3>
          <p className="text-xs text-[var(--color-text-muted)]">
            Enable RAG-based test case generation from Jira stories, Confluence pages, uploaded documents, and approved URLs.
          </p>
          <label className={`flex items-start gap-3 p-3 rounded-lg border cursor-pointer transition-colors ${
            form.knowledge_rag_enabled
              ? 'border-[var(--color-ring)] bg-[var(--color-ring)]/5'
              : 'border-[var(--color-border)] hover:border-[var(--color-border-light)]'
          } ${!isAdmin ? 'opacity-50 cursor-not-allowed' : ''}`}>
            <input
              type="checkbox"
              checked={form.knowledge_rag_enabled ?? false}
              onChange={e => upd('knowledge_rag_enabled', e.target.checked)}
              disabled={!isAdmin}
              className="mt-0.5 rounded bg-[var(--color-bg-secondary)] border-[var(--color-border-light)]"
            />
            <div>
              <span className="text-sm font-medium text-[var(--color-text)]">Enable Knowledge RAG</span>
              {config.knowledge_rag_enabled ? (
                <span className="ml-2 text-[10px] font-medium px-1.5 py-0.5 rounded bg-emerald-500/20 text-emerald-400">Active</span>
              ) : (
                <span className="ml-2 text-[10px] font-medium px-1.5 py-0.5 rounded bg-zinc-500/20 text-zinc-400">Disabled</span>
              )}
              <p className="text-xs text-[var(--color-text-muted)] mt-0.5">
                When enabled, the &quot;Knowledge Generation&quot; tab appears in Test Management,
                allowing QA engineers to generate test cases grounded in synced requirement sources.
              </p>
            </div>
          </label>
        </div>

        {/* API Keys */}
        <div className="card space-y-4">
          <h3 className="text-sm font-semibold text-[var(--color-text)]">Cloud API Keys</h3>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-xs text-[var(--color-text-muted)] mb-1">OpenAI API Key {config.openai_key_set && <span className="text-emerald-400">(set)</span>}</label>
              <input type="password" placeholder={config.openai_key_set ? '••••••••' : 'sk-...'} disabled={!isAdmin}
                onChange={e => upd('openai_api_key', e.target.value || undefined)}
                className="w-full bg-[var(--color-bg-card)] border border-[var(--color-border)] text-[var(--color-text)] text-sm rounded px-3 py-2 disabled:opacity-50" />
            </div>
            <div>
              <label className="block text-xs text-[var(--color-text-muted)] mb-1">Google API Key {config.google_key_set && <span className="text-emerald-400">(set)</span>}</label>
              <input type="password" placeholder={config.google_key_set ? '••••••••' : 'AIza...'} disabled={!isAdmin}
                onChange={e => upd('google_api_key', e.target.value || undefined)}
                className="w-full bg-[var(--color-bg-card)] border border-[var(--color-border)] text-[var(--color-text)] text-sm rounded px-3 py-2 disabled:opacity-50" />
            </div>
          </div>
          <p className="text-xs text-[var(--color-text-muted)]">API keys are stored securely and never returned in responses.</p>
        </div>

        {isAdmin && (
          <button type="submit" disabled={saving}
            className="flex items-center gap-2 bg-[var(--color-btn-primary-bg)] hover:bg-[var(--color-btn-primary-hover)] disabled:opacity-50 text-[var(--color-btn-primary-text)] text-sm px-5 py-2.5 rounded-lg transition-colors">
            <Save className="h-4 w-4" /> {saving ? 'Saving…' : 'Save Configuration'}
          </button>
        )}
      </form>
    </div>
  )
}
