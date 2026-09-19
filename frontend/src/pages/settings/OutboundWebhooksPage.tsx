import { useEffect, useState } from 'react'
import {
  ActivitySquare,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Clock,
  Plus,
  RefreshCw,
  Send,
  Trash2,
  Webhook,
  XCircle,
} from 'lucide-react'
import ExperimentalBadge from '@/components/ui/ExperimentalBadge'
import toast from 'react-hot-toast'
import PageHeader from '@/components/ui/PageHeader'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import EmptyState from '@/components/ui/EmptyState'
import ProjectRequiredEmptyState from '@/components/ui/ProjectRequiredEmptyState'
import { useProjectStore, ALL_PROJECTS_ID } from '@/store/projectStore'
import { usePermissions } from '@/hooks/usePermissions'
import {
  outboundWebhookService,
  type WebhookDeliveryRead,
  type WebhookEventCatalogEntry,
  type WebhookEventType,
  type WebhookSubscriptionRead,
  type WebhookSubscriptionWrite,
} from '@/services/outboundWebhookService'
import { formatCompactDateTime } from '@/utils/formatters'

const EMPTY_FORM: WebhookSubscriptionWrite = {
  name: '',
  target_url: '',
  events: [],
  enabled: true,
  max_retries: 5,
}

/**
 * Outbound Webhooks settings page — Tier 2 item 6.
 *
 * Per-project CRUD over ``webhook_subscriptions`` plus a delivery history
 * drawer per subscription. QA_LEAD+ can create/edit/delete/test; any
 * project member sees the list and history.
 */
export default function OutboundWebhooksPage() {
  const { canAccessManagement: canEdit } = usePermissions()
  const activeProjectId = useProjectStore((s) => s.activeProjectId)
  const activeProject = useProjectStore((s) => s.activeProject)
  const isAllProjects = activeProjectId === ALL_PROJECTS_ID || !activeProjectId

  const [loading, setLoading] = useState(true)
  const [subs, setSubs] = useState<WebhookSubscriptionRead[]>([])
  const [catalog, setCatalog] = useState<WebhookEventCatalogEntry[]>([])
  const [showCreate, setShowCreate] = useState(false)
  const [form, setForm] = useState<WebhookSubscriptionWrite>(EMPTY_FORM)
  const [expandedSub, setExpandedSub] = useState<string | null>(null)
  const [deliveries, setDeliveries] = useState<Record<string, WebhookDeliveryRead[]>>({})

  useEffect(() => {
    let cancelled = false
    async function load() {
      if (isAllProjects || !activeProjectId) {
        setLoading(false)
        return
      }
      setLoading(true)
      try {
        const [list, cat] = await Promise.all([
          outboundWebhookService.list(activeProjectId),
          outboundWebhookService.events(),
        ])
        if (cancelled) return
        setSubs(list)
        setCatalog(cat.events)
      } catch (err) {
        if (!cancelled) toast.error(`Failed to load webhooks: ${(err as Error).message}`)
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    load()
    return () => {
      cancelled = true
    }
  }, [activeProjectId, isAllProjects])

  async function refresh() {
    if (!activeProjectId || isAllProjects) return
    const list = await outboundWebhookService.list(activeProjectId)
    setSubs(list)
  }

  async function createSub() {
    if (!activeProjectId || isAllProjects) return
    if (!form.name.trim() || !form.target_url.trim() || form.events.length === 0) {
      toast.error('Name, target URL, and at least one event type are required')
      return
    }
    try {
      await outboundWebhookService.create(activeProjectId, form)
      setShowCreate(false)
      setForm(EMPTY_FORM)
      await refresh()
      toast.success('Webhook created')
    } catch (err) {
      const detail = (err as { response?: { data?: { detail?: string } } }).response?.data?.detail
      toast.error(detail || `Failed to create: ${(err as Error).message}`)
    }
  }

  async function toggleEnabled(sub: WebhookSubscriptionRead) {
    try {
      await outboundWebhookService.update(sub.id, {
        name: sub.name,
        target_url: sub.target_url,
        events: sub.events,
        enabled: !sub.enabled,
        max_retries: sub.max_retries,
      })
      await refresh()
    } catch (err) {
      toast.error(`Failed to update: ${(err as Error).message}`)
    }
  }

  async function remove(sub: WebhookSubscriptionRead) {
    if (!confirm(`Delete webhook "${sub.name}"?`)) return
    try {
      await outboundWebhookService.remove(sub.id)
      await refresh()
      toast.success('Deleted')
    } catch (err) {
      toast.error(`Failed to delete: ${(err as Error).message}`)
    }
  }

  async function test(sub: WebhookSubscriptionRead) {
    try {
      const result = await outboundWebhookService.test(sub.id)
      if (result.success) toast.success(result.message)
      else toast.error(result.message)
      setTimeout(() => loadDeliveries(sub.id), 1500)
    } catch (err) {
      toast.error(`Test failed: ${(err as Error).message}`)
    }
  }

  async function loadDeliveries(subId: string) {
    try {
      const list = await outboundWebhookService.deliveries(subId, 20)
      setDeliveries((prev) => ({ ...prev, [subId]: list }))
    } catch {
      /* silent */
    }
  }

  async function replayDelivery(subId: string, deliveryId: string) {
    try {
      await outboundWebhookService.replayDelivery(subId, deliveryId)
      toast.success('Replay enqueued')
      // Give the worker a beat to pick up the new row before refreshing.
      setTimeout(() => loadDeliveries(subId), 1500)
    } catch (err) {
      toast.error(`Replay failed: ${(err as Error).message}`)
    }
  }

  async function toggleExpand(subId: string) {
    if (expandedSub === subId) {
      setExpandedSub(null)
      return
    }
    setExpandedSub(subId)
    if (!deliveries[subId]) {
      await loadDeliveries(subId)
    }
  }

  if (isAllProjects) {
    return (
      <div className="space-y-4">
        <PageHeader
          title="Outbound Webhooks"
          subtitle="Subscribe external systems to TestLookup events"
        />
        <ProjectRequiredEmptyState
          description="Outbound webhooks are configured per project."
        />
      </div>
    )
  }

  if (loading) return <LoadingSpinner size="lg" />

  return (
    <div className="space-y-4">
      <PageHeader
        title="Outbound Webhooks"
        subtitle={`HMAC-signed event delivery for ${activeProject?.name || 'this project'}`}
        actions={
          <div className="flex items-center gap-2">
          <ExperimentalBadge />
          {canEdit && (
            <button
              type="button"
              onClick={() => setShowCreate(true)}
              className="btn-primary text-xs flex items-center gap-1.5"
            >
              <Plus className="h-3 w-3" /> New webhook
            </button>
          )}
          </div>
        }
      />

      {showCreate && (
        <CreateForm
          form={form}
          onChange={setForm}
          catalog={catalog}
          onCancel={() => {
            setShowCreate(false)
            setForm(EMPTY_FORM)
          }}
          onSubmit={createSub}
        />
      )}

      {subs.length === 0 && !showCreate && (
        <EmptyState
          title="No webhooks configured"
          description="Create a subscription to fan out TestLookup events to Slack, PagerDuty, Jira, or any HTTP endpoint."
        />
      )}

      <div className="space-y-2">
        {subs.map((sub) => (
          <div
            key={sub.id}
            className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg-card)]"
          >
            <div className="flex items-center gap-2 px-3 py-2.5">
              <Webhook className="h-4 w-4 text-[var(--color-accent)]" />
              <button
                type="button"
                onClick={() => toggleExpand(sub.id)}
                className="flex items-center gap-1 text-[var(--color-text)] hover:underline"
              >
                {expandedSub === sub.id ? (
                  <ChevronDown className="h-3 w-3" />
                ) : (
                  <ChevronRight className="h-3 w-3" />
                )}
                <span className="text-sm font-semibold">{sub.name}</span>
              </button>
              <span className="font-mono text-[10px] text-[var(--color-text-faint)] truncate max-w-[240px]">
                {sub.target_url}
              </span>
              <div className="flex flex-wrap gap-1 ml-2">
                {sub.events.map((e) => (
                  <span
                    key={e}
                    className="text-[10px] px-1.5 py-0.5 rounded border border-[var(--color-border)] text-[var(--color-text-muted)]"
                  >
                    {e}
                  </span>
                ))}
              </div>
              <span className="ml-auto flex items-center gap-2 text-[11px] text-[var(--color-text-faint)]">
                <span title={`Delivered ${sub.total_delivered} · Failed ${sub.failure_count}`}>
                  ✓ {sub.total_delivered} · ✗ {sub.failure_count}
                </span>
                <button
                  type="button"
                  onClick={() => toggleEnabled(sub)}
                  disabled={!canEdit}
                  className={`text-[10px] px-2 py-0.5 rounded border ${
                    sub.enabled
                      ? 'border-[var(--status-passed-bd)]/40 text-[var(--status-passed)] bg-[var(--status-passed-bg)]/10'
                      : 'border-[var(--color-border)] text-[var(--color-text-muted)]'
                  }`}
                >
                  {sub.enabled ? 'ENABLED' : 'DISABLED'}
                </button>
                {canEdit && (
                  <>
                    <button
                      type="button"
                      onClick={() => test(sub)}
                      className="text-[var(--color-accent)] hover:underline flex items-center gap-0.5"
                    >
                      <Send className="h-3 w-3" /> Test
                    </button>
                    <button
                      type="button"
                      onClick={() => remove(sub)}
                      className="text-[var(--status-failed)] hover:underline"
                      aria-label={`Delete ${sub.name}`}
                    >
                      <Trash2 className="h-3 w-3" />
                    </button>
                  </>
                )}
              </span>
            </div>

            {sub.last_error && (
              <div className="px-3 pb-2 text-[11px] text-[var(--status-broken)]">
                Last error: {sub.last_error}
              </div>
            )}

            {expandedSub === sub.id && (
              <DeliveryHistoryPanel
                deliveries={deliveries[sub.id] ?? []}
                onReplay={(deliveryId) => replayDelivery(sub.id, deliveryId)}
              />
            )}
          </div>
        ))}
      </div>

      <section className="text-xs text-[var(--color-text-muted)] space-y-1">
        <p>
          <strong>Signature:</strong> Every delivery carries an{' '}
          <code>X-TestLookup-Signature</code> header containing an HMAC-SHA256
          hex digest of the raw body, keyed with the subscription's secret.
          Verify with{' '}
          <code>hmac.new(SECRET, request.body, hashlib.sha256).hexdigest()</code>.
        </p>
        <p>
          <strong>Retries:</strong> Non-2xx retryable statuses (408/425/429/5xx)
          and network errors retry with exponential backoff up to{' '}
          <code>max_retries</code> attempts. 4xx (except 408/425/429) is
          treated as a customer bug and marked FAILED immediately.
        </p>
        <p>
          <strong>Offline mode:</strong> when <code>AI_OFFLINE_MODE=true</code>{' '}
          the whole subsystem is a silent no-op regardless of the{' '}
          <code>outbound_webhooks</code> feature flag.
        </p>
      </section>
    </div>
  )
}

// ── Create form ────────────────────────────────────────────────────────────

function CreateForm({
  form,
  onChange,
  catalog,
  onCancel,
  onSubmit,
}: {
  form: WebhookSubscriptionWrite
  onChange: (f: WebhookSubscriptionWrite) => void
  catalog: WebhookEventCatalogEntry[]
  onCancel: () => void
  onSubmit: () => void
}) {
  function toggleEvent(e: WebhookEventType) {
    const has = form.events.includes(e)
    onChange({
      ...form,
      events: has ? form.events.filter((v) => v !== e) : [...form.events, e],
    })
  }

  return (
    <div className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg-card)] p-4 space-y-3">
      <h3 className="text-sm font-semibold text-[var(--color-text)]">New webhook subscription</h3>
      <div className="grid grid-cols-2 gap-3">
        <label className="text-xs block">
          <span className="text-[var(--color-text-muted)]">Name</span>
          <input
            type="text"
            value={form.name}
            onChange={(e) => onChange({ ...form, name: e.target.value })}
            placeholder="PagerDuty for regressions"
            className="mt-1 w-full px-2 py-1.5 text-sm bg-[var(--color-bg-secondary)] border border-[var(--color-border)] rounded"
          />
        </label>
        <label className="text-xs block">
          <span className="text-[var(--color-text-muted)]">Max retries</span>
          <input
            type="number"
            min={0}
            max={10}
            value={form.max_retries}
            onChange={(e) => onChange({ ...form, max_retries: Number(e.target.value) })}
            className="mt-1 w-full px-2 py-1.5 text-sm bg-[var(--color-bg-secondary)] border border-[var(--color-border)] rounded"
          />
        </label>
      </div>
      <label className="text-xs block">
        <span className="text-[var(--color-text-muted)]">Target URL (https only)</span>
        <input
          type="text"
          value={form.target_url}
          onChange={(e) => onChange({ ...form, target_url: e.target.value })}
          placeholder="https://events.example.com/hooks/testlookup"
          className="mt-1 w-full px-2 py-1.5 text-sm bg-[var(--color-bg-secondary)] border border-[var(--color-border)] rounded"
        />
      </label>
      <label className="text-xs block">
        <span className="text-[var(--color-text-muted)]">HMAC secret (used to sign deliveries)</span>
        <input
          type="password"
          value={form.secret ?? ''}
          onChange={(e) => onChange({ ...form, secret: e.target.value })}
          placeholder="Shared secret — stored encrypted"
          className="mt-1 w-full px-2 py-1.5 text-sm bg-[var(--color-bg-secondary)] border border-[var(--color-border)] rounded"
        />
      </label>

      <fieldset className="space-y-1.5">
        <legend className="text-[10px] uppercase tracking-wide text-[var(--color-text-muted)]">
          Events
        </legend>
        {catalog.map((e) => (
          <label key={e.event_type} className="flex items-start gap-2 text-xs">
            <input
              type="checkbox"
              className="mt-0.5"
              checked={form.events.includes(e.event_type)}
              onChange={() => toggleEvent(e.event_type)}
            />
            <span>
              <span className="block font-mono text-[var(--color-text)]">{e.event_type}</span>
              <span className="block text-[var(--color-text-muted)]">{e.description}</span>
            </span>
          </label>
        ))}
      </fieldset>

      <div className="flex justify-end gap-2 pt-2">
        <button type="button" onClick={onCancel} className="btn-secondary text-xs">
          Cancel
        </button>
        <button type="button" onClick={onSubmit} className="btn-primary text-xs">
          Create
        </button>
      </div>
    </div>
  )
}

// ── Delivery history drawer ───────────────────────────────────────────────

function DeliveryHistoryPanel({
  deliveries,
  onReplay,
}: {
  deliveries: WebhookDeliveryRead[]
  onReplay: (deliveryId: string) => void | Promise<void>
}) {
  if (deliveries.length === 0) {
    return (
      <div className="px-3 pb-3 text-xs text-[var(--color-text-faint)]">
        No deliveries yet.
      </div>
    )
  }
  return (
    <div className="border-t border-[var(--color-border)] px-3 py-2 space-y-1">
      <div className="text-[10px] uppercase tracking-wide text-[var(--color-text-muted)] flex items-center gap-1">
        <ActivitySquare className="h-3 w-3" /> Recent deliveries
      </div>
      <ul className="space-y-0.5">
        {deliveries.map((d) => {
          const replayable = d.status === 'FAILED' || d.status === 'DLQ'
          return (
          <li key={d.id} className="flex items-center gap-2 text-[11px]">
            {d.status === 'SUCCESS' ? (
              <CheckCircle2 className="h-3 w-3 text-[var(--status-passed)]" />
            ) : d.status === 'PENDING' ? (
              <Clock className="h-3 w-3 text-[var(--status-broken)]" />
            ) : (
              <XCircle className="h-3 w-3 text-[var(--status-failed)]" />
            )}
            <span className="font-mono text-[var(--color-text)]">{d.event_type}</span>
            <span className="text-[var(--color-text-muted)]">
              {d.http_status ? `HTTP ${d.http_status}` : d.status}
            </span>
            <span className="text-[var(--color-text-faint)] ml-auto">
              attempt {d.attempt_count} · {formatCompactDateTime(d.created_at)}
            </span>
            {d.error && (
              <span
                className="text-[var(--color-text-muted)] max-w-[240px] truncate"
                title={d.error}
              >
                {d.error}
              </span>
            )}
            {replayable && (
              <button
                type="button"
                onClick={() => { void onReplay(d.id) }}
                className="text-[10px] px-1.5 py-0.5 rounded border border-[var(--color-accent)]/40 text-[var(--color-accent)] hover:bg-[var(--color-accent)]/10 inline-flex items-center gap-1"
                title="Replay this failed delivery as a fresh attempt"
              >
                <RefreshCw className="h-2.5 w-2.5" /> Replay
              </button>
            )}
          </li>
          )
        })}
      </ul>
    </div>
  )
}
