import { useEffect, useState } from 'react'
import { ArrowLeft, Save } from 'lucide-react'
import { Link } from 'react-router-dom'
import toast from 'react-hot-toast'
import PageHeader from '@/components/ui/PageHeader'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { appSettingsService, type AIConfigRead, type AIConfigUpdate } from '@/services/appSettingsService'
import { usePermissions } from '@/hooks/usePermissions'

const LLM_PROVIDERS = ['ollama', 'openai', 'gemini', 'lmstudio', 'localai', 'vllm']

export default function AIConfigPage() {
  const { isAdmin } = usePermissions()
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
            <label className="flex items-center gap-2 text-sm text-[var(--color-text-secondary)] cursor-pointer">
              <input type="checkbox" checked={form.ai_offline_mode ?? true} disabled={!isAdmin}
                onChange={e => upd('ai_offline_mode', e.target.checked)}
                className="rounded bg-[var(--color-bg-secondary)] border-[var(--color-border-light)]" />
              Offline Mode (air-gapped, Ollama only)
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
