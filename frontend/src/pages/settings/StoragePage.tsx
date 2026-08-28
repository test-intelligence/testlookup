import { useEffect, useState } from 'react'
import { ArrowLeft, Save } from 'lucide-react'
import { Link } from 'react-router-dom'
import toast from 'react-hot-toast'
import PageHeader from '@/components/ui/PageHeader'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { appSettingsService, type StorageConfigRead, type StorageConfigUpdate } from '@/services/appSettingsService'
import { usePermissions } from '@/hooks/usePermissions'
import Field from '@/components/ui/Field'

export default function StoragePage() {
  const { isAdmin } = usePermissions()
  const [config, setConfig] = useState<StorageConfigRead | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [form, setForm] = useState<StorageConfigUpdate>({})

  useEffect(() => {
    appSettingsService.getStorageConfig()
      .then(cfg => {
        setConfig(cfg)
        setForm({
          storage_backend: cfg.storage_backend,
          chroma_host: cfg.chroma_host,
          chroma_port: cfg.chroma_port,
          chroma_collection: cfg.chroma_collection,
          minio_endpoint: cfg.minio_endpoint,
          minio_bucket_name: cfg.minio_bucket_name,
          minio_use_ssl: cfg.minio_use_ssl,
        })
      })
      .catch(() => toast.error('Failed to load storage configuration'))
      .finally(() => setLoading(false))
  }, [])

  async function handleSave(e: React.FormEvent) {
    e.preventDefault()
    setSaving(true)
    try {
      const updated = await appSettingsService.updateStorageConfig(form)
      setConfig(updated)
      toast.success('Storage configuration saved')
    } catch (err: unknown) {
      // Surface the server's reason. A bare `catch {}` here reported only
      // "Failed to save storage configuration", so a rejected bucket name or a
      // 422 on a specific field gave the operator nothing to act on. The
      // sibling settings pages (SeedDataPage) already read response.data.detail.
      const detail = (err as { response?: { data?: { detail?: string } } })
        ?.response?.data?.detail
      toast.error(
        detail
          ? `Failed to save storage configuration: ${detail}`
          : 'Failed to save storage configuration',
      )
    } finally {
      setSaving(false)
    }
  }

  function upd(field: keyof StorageConfigUpdate, value: unknown) {
    setForm(prev => ({ ...prev, [field]: value }))
  }

  if (loading) return <div className="flex justify-center py-16"><LoadingSpinner size="lg" /></div>
  if (!config) return null

  return (
    <div className="space-y-6">
      <PageHeader title="Data & Storage" subtitle="Database connections, object storage, and vector store"
        actions={<Link to="/settings" className="btn-secondary text-sm flex items-center gap-2"><ArrowLeft className="h-4 w-4" /> Back</Link>} />

      <form onSubmit={handleSave} className="space-y-6 max-w-2xl">
        {/* Infrastructure status (connection details masked for security) */}
        <div className="card space-y-3">
          <h3 className="text-sm font-semibold text-[var(--color-text)]">Infrastructure</h3>
          <p className="text-xs text-[var(--color-text-muted)]">Connection details are configured via environment variables and masked for security.</p>
          <div className="grid grid-cols-3 gap-3">
            <div className="flex items-center gap-2 bg-[var(--color-bg-card)]/50 rounded px-3 py-2">
              <div className={`w-2 h-2 rounded-full ${config.postgres_connected ? 'bg-[var(--status-passed-bg)]' : 'bg-[var(--status-failed-bg)]'}`} />
              <span className="text-sm text-[var(--color-text-muted)]">PostgreSQL</span>
            </div>
            <div className="flex items-center gap-2 bg-[var(--color-bg-card)]/50 rounded px-3 py-2">
              <div className={`w-2 h-2 rounded-full ${config.mongo_connected ? 'bg-[var(--status-passed-bg)]' : 'bg-[var(--status-failed-bg)]'}`} />
              <span className="text-sm text-[var(--color-text-muted)]">MongoDB</span>
            </div>
            <div className="flex items-center gap-2 bg-[var(--color-bg-card)]/50 rounded px-3 py-2">
              <div className={`w-2 h-2 rounded-full ${config.redis_connected ? 'bg-[var(--status-passed-bg)]' : 'bg-[var(--status-failed-bg)]'}`} />
              <span className="text-sm text-[var(--color-text-muted)]">Redis</span>
            </div>
          </div>
        </div>

        {/* MinIO / S3 (editable) */}
        <div className="card space-y-3">
          <h3 className="text-sm font-semibold text-[var(--color-text)]">Object Storage (MinIO / S3)</h3>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <Field label="Backend" labelClassName="block text-xs text-[var(--color-text-muted)] mb-1">
                {id => (
                  <select id={id} value={form.storage_backend ?? ''} onChange={e => upd('storage_backend', e.target.value)} disabled={!isAdmin}
                    className="w-full bg-[var(--color-bg-card)] border border-[var(--color-border)] text-[var(--color-text)] text-sm rounded px-3 py-2 disabled:opacity-50">
                    <option value="minio">MinIO</option>
                    <option value="s3">S3</option>
                    <option value="local">Local</option>
                  </select>
                )}
              </Field>
            </div>
            <div>
              <Field label="Endpoint" labelClassName="block text-xs text-[var(--color-text-muted)] mb-1">
                {id => (
                  <input id={id} value={form.minio_endpoint ?? ''} onChange={e => upd('minio_endpoint', e.target.value)} disabled={!isAdmin}
                    className="w-full bg-[var(--color-bg-card)] border border-[var(--color-border)] text-[var(--color-text)] text-sm rounded px-3 py-2 disabled:opacity-50"
                    placeholder="localhost:9000" />
                )}
              </Field>
            </div>
            <div>
              <Field label="Bucket Name" labelClassName="block text-xs text-[var(--color-text-muted)] mb-1">
                {id => (
                  <input id={id} value={form.minio_bucket_name ?? ''} onChange={e => upd('minio_bucket_name', e.target.value)} disabled={!isAdmin}
                    className="w-full bg-[var(--color-bg-card)] border border-[var(--color-border)] text-[var(--color-text)] text-sm rounded px-3 py-2 disabled:opacity-50" />
                )}
              </Field>
            </div>
            <div className="flex items-end pb-1">
              <label className="flex items-center gap-2 text-sm text-[var(--color-text-secondary)] cursor-pointer">
                <input type="checkbox" checked={form.minio_use_ssl ?? false} disabled={!isAdmin}
                  onChange={e => upd('minio_use_ssl', e.target.checked)}
                  className="rounded bg-[var(--color-bg-secondary)] border-[var(--color-border-light)]" />
                Use SSL
              </label>
            </div>
          </div>
        </div>

        {/* ChromaDB (editable) */}
        <div className="card space-y-3">
          <h3 className="text-sm font-semibold text-[var(--color-text)]">ChromaDB (Vector Store)</h3>
          <div className="grid grid-cols-3 gap-3">
            <div>
              <Field label="Host" labelClassName="block text-xs text-[var(--color-text-muted)] mb-1">
                {id => (
                  <input id={id} value={form.chroma_host ?? ''} onChange={e => upd('chroma_host', e.target.value)} disabled={!isAdmin}
                    className="w-full bg-[var(--color-bg-card)] border border-[var(--color-border)] text-[var(--color-text)] text-sm rounded px-3 py-2 disabled:opacity-50"
                    placeholder="localhost" />
                )}
              </Field>
            </div>
            <div>
              <Field label="Port" labelClassName="block text-xs text-[var(--color-text-muted)] mb-1">
                {id => (
                  <input id={id} type="number" min="1" max="65535" value={form.chroma_port ?? 8001} disabled={!isAdmin}
                    onChange={e => upd('chroma_port', parseInt(e.target.value))}
                    className="w-full bg-[var(--color-bg-card)] border border-[var(--color-border)] text-[var(--color-text)] text-sm rounded px-3 py-2 disabled:opacity-50" />
                )}
              </Field>
            </div>
            <div>
              <Field label="Collection" labelClassName="block text-xs text-[var(--color-text-muted)] mb-1">
                {id => (
                  <input id={id} value={form.chroma_collection ?? ''} onChange={e => upd('chroma_collection', e.target.value)} disabled={!isAdmin}
                    className="w-full bg-[var(--color-bg-card)] border border-[var(--color-border)] text-[var(--color-text)] text-sm rounded px-3 py-2 disabled:opacity-50" />
                )}
              </Field>
            </div>
          </div>
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
