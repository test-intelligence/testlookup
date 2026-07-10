import { useState, useEffect } from 'react'
import { Bell, Mail, MessageSquare, Users, CheckCircle, XCircle, Send, Trash2, ChevronDown, ChevronUp, Server, Eye, EyeOff } from 'lucide-react'
import toast from 'react-hot-toast'
import PageHeader from '@/components/ui/PageHeader'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import {
  useNotificationPreferences,
  useNotificationHistory,
  invalidateNotifications,
} from '@/hooks/useNotifications'
import {
  notificationService,
} from '@/services/notificationService'
import { appSettingsService } from '@/services/appSettingsService'
import type { SmtpConfigRead } from '@/services/appSettingsService'
import type {
  NotificationChannel,
  NotificationEventType,
  NotificationPreference,
  NotificationPreferencePayload,
} from '@/types/notifications'

// ── Config ────────────────────────────────────────────────────

const EVENT_LABELS: Record<NotificationEventType, string> = {
  run_failed: 'Run failed',
  run_passed: 'Run passed',
  high_failure_rate: 'High failure rate',
  ai_analysis_complete: 'AI analysis complete',
  quality_gate_failed: 'Quality gate failed',
  flaky_test_detected: 'Flaky test detected',
  // Transition events — fire on state changes only (alert-fatigue fix)
  'test.newly_failing': 'Test newly failing (transition)',
  'test.recovered': 'Test recovered (transition)',
  'test.newly_flaky': 'Test newly flaky (transition)',
  'test.quarantined': 'Test quarantined (transition)',
  'test.unquarantined': 'Test released from quarantine (transition)',
}

const ALL_EVENTS: NotificationEventType[] = Object.keys(EVENT_LABELS) as NotificationEventType[]

const CHANNEL_META: Record<NotificationChannel, { label: string; icon: React.ElementType; colour: string; placeholder: string }> = {
  email: {
    label: 'Email',
    icon: Mail,
    colour: 'text-[var(--color-text)]',
    placeholder: 'Override email (leave blank to use your account email)',
  },
  slack: {
    label: 'Slack',
    icon: MessageSquare,
    colour: 'text-emerald-400',
    placeholder: 'Slack incoming webhook URL',
  },
  teams: {
    label: 'Microsoft Teams',
    icon: Users,
    colour: 'text-violet-400',
    placeholder: 'Teams incoming webhook URL',
  },
}

// ── Sub-components ────────────────────────────────────────────

function EventCheckboxes({
  selected,
  onChange,
}: {
  selected: NotificationEventType[]
  onChange: (v: NotificationEventType[]) => void
}) {
  const toggle = (e: NotificationEventType) =>
    onChange(selected.includes(e) ? selected.filter(x => x !== e) : [...selected, e])

  return (
    <div className="grid grid-cols-2 gap-2">
      {ALL_EVENTS.map(ev => (
        <label key={ev} className="flex items-center gap-2 cursor-pointer group">
          <input
            type="checkbox"
            className="w-4 h-4 rounded border-[var(--color-border-light)] bg-[var(--color-bg-secondary)] text-[var(--color-text-secondary)] focus:ring-[var(--color-ring)]"
            checked={selected.includes(ev)}
            onChange={() => toggle(ev)}
          />
          <span className="text-sm text-[var(--color-text-secondary)] group-hover:text-[var(--color-text)] transition-colors">
            {EVENT_LABELS[ev]}
          </span>
        </label>
      ))}
    </div>
  )
}

function ChannelCard({
  channel,
  preference,
  onSaved,
}: {
  channel: NotificationChannel
  preference?: NotificationPreference
  onSaved: () => void
}) {
  const meta = CHANNEL_META[channel]
  const Icon = meta.icon

  const [expanded, setExpanded] = useState(!!preference)
  const [enabled, setEnabled] = useState(preference?.enabled ?? true)
  const [events, setEvents] = useState<NotificationEventType[]>(
    preference?.events ?? [
      'run_failed',
      'high_failure_rate',
      // Transition events on by default for new preferences — existing
      // projects never emit them (their transition policy is off), so this
      // only lights up for projects using transition-only notifications.
      'test.newly_failing',
      'test.recovered',
      'test.newly_flaky',
      'test.quarantined',
      'test.unquarantined',
    ],
  )
  const [threshold, setThreshold] = useState(preference?.failure_rate_threshold ?? 80)
  const [webhookOrEmail, setWebhookOrEmail] = useState(
    channel === 'email'
      ? (preference?.email_override ?? '')
      : channel === 'slack'
      ? (preference?.slack_webhook_url ?? '')
      : (preference?.teams_webhook_url ?? ''),
  )
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)

  const buildPayload = (): NotificationPreferencePayload => ({
    channel,
    enabled,
    events,
    failure_rate_threshold: threshold,
    project_id: null,
    email_override: channel === 'email' ? webhookOrEmail || null : null,
    slack_webhook_url: channel === 'slack' ? webhookOrEmail || null : null,
    teams_webhook_url: channel === 'teams' ? webhookOrEmail || null : null,
  })

  const handleSave = async () => {
    setSaving(true)
    try {
      if (preference) {
        await notificationService.updatePreference(preference.id, buildPayload())
      } else {
        await notificationService.upsertPreference(buildPayload())
      }
      await invalidateNotifications()
      toast.success(`${meta.label} notifications saved`)
      onSaved()
    } catch {
      toast.error(`Failed to save ${meta.label} settings`)
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async () => {
    if (!preference) return
    if (!confirm(`Remove ${meta.label} notification preference?`)) return
    try {
      await notificationService.deletePreference(preference.id)
      await invalidateNotifications()
      toast.success(`${meta.label} preference removed`)
      onSaved()
    } catch {
      toast.error('Failed to remove preference')
    }
  }

  const handleTest = async () => {
    setTesting(true)
    try {
      await notificationService.sendTest(channel, preference?.id)
      toast.success(`Test ${meta.label} notification sent!`)
    } catch (err: unknown) {
      const axiosErr = err as { response?: { data?: { detail?: string } } }
      toast.error(axiosErr?.response?.data?.detail ?? `Test notification failed`)
    } finally {
      setTesting(false)
    }
  }

  return (
    <div className="card border border-[var(--color-border)] rounded-lg overflow-hidden">
      {/* Header row */}
      <button
        className="w-full flex items-center gap-3 p-4 text-left hover:bg-[var(--color-bg-secondary)]/60 transition-colors"
        onClick={() => setExpanded(v => !v)}
      >
        <div className={`p-2 rounded-lg bg-[var(--color-bg-secondary)] ${meta.colour}`}>
          <Icon className="w-4 h-4" />
        </div>
        <div className="flex-1">
          <p className="font-semibold text-[var(--color-text)]">{meta.label}</p>
          <p className="text-xs text-[var(--color-text-muted)] mt-0.5">
            {preference
              ? `${preference.events.length} event(s) · ${preference.enabled ? 'Active' : 'Paused'}`
              : 'Not configured'}
          </p>
        </div>
        {preference && (
          <span
            className={`text-xs px-2 py-0.5 rounded-full font-medium ${
              preference.enabled
                ? 'bg-emerald-900/40 text-emerald-400'
                : 'bg-[var(--color-bg-hover)] text-[var(--color-text-muted)]'
            }`}
          >
            {preference.enabled ? 'Active' : 'Paused'}
          </span>
        )}
        {expanded ? (
          <ChevronUp className="w-4 h-4 text-[var(--color-text-muted)]" />
        ) : (
          <ChevronDown className="w-4 h-4 text-[var(--color-text-muted)]" />
        )}
      </button>

      {/* Expanded config */}
      {expanded && (
        <div className="border-t border-[var(--color-border)] p-4 space-y-5">
          {/* Enable toggle */}
          <div className="flex items-center justify-between">
            <span className="text-sm text-[var(--color-text-secondary)]">Enable {meta.label} notifications</span>
            <button
              onClick={() => setEnabled(v => !v)}
              className={`relative w-11 h-6 rounded-full transition-colors ${
                enabled ? 'bg-[var(--color-btn-primary-bg)]' : 'bg-[var(--color-bg-hover)]'
              }`}
            >
              <span
                className={`absolute top-0.5 left-0.5 w-5 h-5 rounded-full bg-white shadow transition-transform ${
                  enabled ? 'translate-x-5' : ''
                }`}
              />
            </button>
          </div>

          {/* Webhook / Email field */}
          <div>
            <label className="block text-xs font-medium text-[var(--color-text-muted)] mb-1.5">
              {channel === 'email' ? 'Email address override' : 'Webhook URL'}
            </label>
            <input
              type={channel === 'email' ? 'email' : 'url'}
              value={webhookOrEmail}
              onChange={e => setWebhookOrEmail(e.target.value)}
              placeholder={meta.placeholder}
              className="input w-full text-sm"
            />
            {channel !== 'email' && (
              <p className="text-xs text-[var(--color-text-muted)] mt-1">
                {channel === 'slack'
                  ? 'Create at: Your Slack App → Incoming Webhooks → Add New Webhook'
                  : 'Create at: Teams channel → Connectors → Incoming Webhook'}
              </p>
            )}
          </div>

          {/* Event selection */}
          <div>
            <label className="block text-xs font-medium text-[var(--color-text-muted)] mb-2">
              Notify me when
            </label>
            <EventCheckboxes selected={events} onChange={setEvents} />
          </div>

          {/* Failure rate threshold */}
          <div>
            <label className="block text-xs font-medium text-[var(--color-text-muted)] mb-1.5">
              High failure rate threshold:{' '}
              <span className="text-[var(--color-text)] font-mono">{threshold}%</span>
            </label>
            <input
              type="range"
              min={10}
              max={100}
              step={5}
              value={threshold}
              onChange={e => setThreshold(Number(e.target.value))}
              className="w-full accent-neutral-400"
            />
            <div className="flex justify-between text-xs text-[var(--color-text-faint)] mt-1">
              <span>Alert at any failure</span>
              <span>Only at 100% failure</span>
            </div>
            <p className="text-xs text-[var(--color-text-muted)] mt-1">
              "High failure rate" alerts trigger when pass rate drops below {threshold}%
            </p>
          </div>

          {/* Actions */}
          <div className="flex items-center gap-2 pt-1">
            <button
              onClick={handleSave}
              disabled={saving}
              className="btn-primary flex items-center gap-2 text-sm px-4 py-2"
            >
              {saving ? <LoadingSpinner size="sm" /> : <CheckCircle className="w-4 h-4" />}
              Save
            </button>

            {preference && (
              <button
                onClick={handleTest}
                disabled={testing}
                className="flex items-center gap-2 text-sm px-4 py-2 rounded-lg border border-[var(--color-border-light)] text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)]/50 transition-colors"
              >
                {testing ? <LoadingSpinner size="sm" /> : <Send className="w-4 h-4" />}
                Send test
              </button>
            )}

            {preference && (
              <button
                onClick={handleDelete}
                className="ml-auto flex items-center gap-1.5 text-sm text-red-400 hover:text-red-300 transition-colors"
              >
                <Trash2 className="w-4 h-4" />
                Remove
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

// ── SMTP configuration card ───────────────────────────────────

function SmtpConfigCard() {
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [showPassword, setShowPassword] = useState(false)

  const [enabled, setEnabled] = useState(false)
  const [host, setHost] = useState('localhost')
  const [port, setPort] = useState(587)
  const [user, setUser] = useState('')
  const [password, setPassword] = useState('')
  const [clearPassword, setClearPassword] = useState(false)
  const [fromAddress, setFromAddress] = useState('noreply@testlookup.io')
  const [implicitTls, setImplicitTls] = useState(true)
  const [passwordSet, setPasswordSet] = useState(false)

  useEffect(() => {
    appSettingsService.getSmtpConfig()
      .then((cfg: SmtpConfigRead) => {
        setEnabled(cfg.enabled)
        setHost(cfg.host)
        setPort(cfg.port)
        setUser(cfg.user ?? '')
        setFromAddress(cfg.from_address)
        setImplicitTls(cfg.implicit_tls)
        setPasswordSet(cfg.password_set)
      })
      .catch(() => {/* insufficient role — card stays at defaults */})
      .finally(() => setLoading(false))
  }, [])

  const handleSave = async () => {
    setSaving(true)
    try {
      // password !== '' → use the new value
      // password === '' && clearPassword → send "" to explicitly clear the stored password
      // password === '' && !clearPassword → send null to keep the existing password
      const passwordPayload =
        password !== ''
          ? password
          : clearPassword
            ? ''
            : null

      const updated = await appSettingsService.updateSmtpConfig({
        enabled,
        host,
        port,
        user: user || null,
        password: passwordPayload,
        from_address: fromAddress,
        implicit_tls: implicitTls,
      })
      setPasswordSet(updated.password_set)
      setPassword('')
      setClearPassword(false)
      toast.success('SMTP configuration saved')
    } catch {
      toast.error('Failed to save SMTP configuration')
    } finally {
      setSaving(false)
    }
  }

  const handleTest = async () => {
    setTesting(true)
    try {
      const result = await appSettingsService.testSmtpConfig()
      if (result.success) {
        toast.success(result.message)
      } else {
        toast.error(result.message)
      }
    } catch {
      toast.error('Test connection failed')
    } finally {
      setTesting(false)
    }
  }

  return (
    <div className="card border border-[var(--color-border)] rounded-lg overflow-hidden">
      <div className="flex items-center gap-3 p-4 border-b border-[var(--color-border)]">
        <div className="p-2 rounded-lg bg-[var(--color-bg-secondary)] text-[var(--color-text)]">
          <Server className="w-4 h-4" />
        </div>
        <div className="flex-1">
          <p className="font-semibold text-[var(--color-text)]">Email Server (SMTP)</p>
          <p className="text-xs text-[var(--color-text-muted)] mt-0.5">
            Configure the outgoing mail server for email notifications
          </p>
        </div>
        <span
          className={`text-xs px-2 py-0.5 rounded-full font-medium ${
            enabled
              ? 'bg-emerald-900/40 text-emerald-400'
              : 'bg-[var(--color-bg-hover)] text-[var(--color-text-muted)]'
          }`}
        >
          {enabled ? 'Active' : 'Disabled'}
        </span>
      </div>

      <div className="p-4 space-y-5">
        {loading ? (
          <div className="flex justify-center py-4"><LoadingSpinner size="sm" /></div>
        ) : (
          <>
            {/* Enable toggle */}
            <div className="flex items-center justify-between">
              <span className="text-sm text-[var(--color-text-secondary)]">Enable SMTP email delivery</span>
              <button
                onClick={() => setEnabled(v => !v)}
                className={`relative w-11 h-6 rounded-full transition-colors ${
                  enabled ? 'bg-[var(--color-btn-primary-bg)]' : 'bg-[var(--color-bg-hover)]'
                }`}
              >
                <span
                  className={`absolute top-0.5 left-0.5 w-5 h-5 rounded-full bg-white shadow transition-transform ${
                    enabled ? 'translate-x-5' : ''
                  }`}
                />
              </button>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
              {/* Host */}
              <div className="sm:col-span-2">
                <label className="block text-xs font-medium text-[var(--color-text-muted)] mb-1.5">SMTP Host</label>
                <input
                  type="text"
                  value={host}
                  onChange={e => setHost(e.target.value)}
                  placeholder="smtp.example.com"
                  className="input w-full text-sm"
                />
              </div>
              {/* Port */}
              <div>
                <label className="block text-xs font-medium text-[var(--color-text-muted)] mb-1.5">Port</label>
                <input
                  type="number"
                  value={port}
                  onChange={e => setPort(Number(e.target.value))}
                  min={1}
                  max={65535}
                  className="input w-full text-sm"
                />
              </div>
            </div>

            {/* Username */}
            <div>
              <label className="block text-xs font-medium text-[var(--color-text-muted)] mb-1.5">
                Username <span className="text-[var(--color-text-faint)]">(optional)</span>
              </label>
              <input
                type="text"
                value={user}
                onChange={e => setUser(e.target.value)}
                placeholder="smtp-user@example.com"
                className="input w-full text-sm"
              />
            </div>

            {/* Password */}
            <div>
              <label className="block text-xs font-medium text-[var(--color-text-muted)] mb-1.5">
                Password{' '}
                <span className="text-[var(--color-text-faint)]">
                  {passwordSet && !clearPassword ? '(stored — leave blank to keep)' : '(optional)'}
                </span>
              </label>
              <div className="relative">
                <input
                  type={showPassword ? 'text' : 'password'}
                  value={password}
                  onChange={e => {
                    setPassword(e.target.value)
                    setClearPassword(false)
                  }}
                  placeholder={passwordSet && !clearPassword ? '••••••••' : 'Enter password'}
                  className="input w-full text-sm pr-10"
                />
                <button
                  type="button"
                  onClick={() => setShowPassword(v => !v)}
                  className="absolute right-2.5 top-1/2 -translate-y-1/2 text-[var(--color-text-muted)] hover:text-[var(--color-text-secondary)]"
                >
                  {showPassword ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                </button>
              </div>
              {passwordSet && password === '' && (
                <div className="mt-1.5">
                  {clearPassword ? (
                    <span className="text-xs text-amber-400 flex items-center gap-1">
                      <XCircle className="w-3.5 h-3.5" />
                      Password will be cleared on save.{' '}
                      <button
                        type="button"
                        onClick={() => setClearPassword(false)}
                        className="underline hover:text-amber-300"
                      >
                        Undo
                      </button>
                    </span>
                  ) : (
                    <button
                      type="button"
                      onClick={() => setClearPassword(true)}
                      className="text-xs text-red-400 hover:text-red-300 transition-colors"
                    >
                      Clear stored password
                    </button>
                  )}
                </div>
              )}
            </div>

            {/* From address */}
            <div>
              <label className="block text-xs font-medium text-[var(--color-text-muted)] mb-1.5">From Address</label>
              <input
                type="email"
                value={fromAddress}
                onChange={e => setFromAddress(e.target.value)}
                placeholder="noreply@testlookup.io"
                className="input w-full text-sm"
              />
            </div>

            {/* TLS / STARTTLS mode toggle */}
            <div className="flex items-center justify-between">
              <div>
                <span className="text-sm text-[var(--color-text-secondary)]">Connection security mode</span>
                <p className="text-xs text-[var(--color-text-muted)] mt-0.5">
                  Toggle between implicit TLS (typically port 465) and STARTTLS (typically port 587). Plain SMTP is not supported.
                </p>
              </div>
              <button
                onClick={() => setImplicitTls(v => !v)}
                className={`relative w-11 h-6 rounded-full transition-colors ${
                  implicitTls ? 'bg-[var(--color-btn-primary-bg)]' : 'bg-[var(--color-bg-hover)]'
                }`}
              >
                <span
                  className={`absolute top-0.5 left-0.5 w-5 h-5 rounded-full bg-white shadow transition-transform ${
                    implicitTls ? 'translate-x-5' : ''
                  }`}
                />
              </button>
            </div>

            {/* Actions */}
            <div className="flex items-center gap-2 pt-1">
              <button
                onClick={handleSave}
                disabled={saving}
                className="btn-primary flex items-center gap-2 text-sm px-4 py-2"
              >
                {saving ? <LoadingSpinner size="sm" /> : <CheckCircle className="w-4 h-4" />}
                Save
              </button>
              <button
                onClick={handleTest}
                disabled={testing || !enabled}
                className="flex items-center gap-2 text-sm px-4 py-2 rounded-lg border border-[var(--color-border-light)] text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)]/50 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
              >
                {testing ? <LoadingSpinner size="sm" /> : <Send className="w-4 h-4" />}
                Send test email
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}

// ── Notification history panel ────────────────────────────────

function HistoryPanel() {
  const [open, setOpen] = useState(false)
  const { data: logs, mutate: refreshLogs } = useNotificationHistory(false)

  const handleMarkAll = async () => {
    await notificationService.markAllRead()
    refreshLogs()
    invalidateNotifications()
  }

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="text-sm text-[var(--color-text)] hover:text-[var(--color-text-secondary)] transition-colors"
      >
        View notification history →
      </button>
    )
  }

  return (
    <div className="card space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="font-semibold text-[var(--color-text)] flex items-center gap-2">
          <Bell className="w-4 h-4" /> Recent Notifications
        </h3>
        <div className="flex items-center gap-3">
          <button onClick={handleMarkAll} className="text-xs text-[var(--color-text)] hover:text-[var(--color-text-secondary)]">
            Mark all read
          </button>
          <button onClick={() => setOpen(false)} className="text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text-secondary)]">
            Hide
          </button>
        </div>
      </div>

      {!logs ? (
        <LoadingSpinner size="sm" />
      ) : logs.length === 0 ? (
        <p className="text-sm text-[var(--color-text-muted)]">No notifications yet.</p>
      ) : (
        <ul className="divide-y divide-[var(--color-border)]">
          {logs.map(log => (
            <li
              key={log.id}
              className={`py-3 flex items-start gap-3 ${log.is_read ? 'opacity-60' : ''}`}
            >
              <span className="mt-0.5">
                {log.status === 'sent' ? (
                  <CheckCircle className="w-4 h-4 text-emerald-400" />
                ) : (
                  <XCircle className="w-4 h-4 text-red-400" />
                )}
              </span>
              <div className="flex-1 min-w-0">
                <p className="text-sm text-[var(--color-text)] font-medium truncate">{log.title}</p>
                <p className="text-xs text-[var(--color-text-muted)] mt-0.5">
                  {log.channel.toUpperCase()} · {log.event_type.replace(/_/g, ' ')} ·{' '}
                  {new Date(log.created_at).toLocaleString()}
                </p>
              </div>
              {!log.is_read && (
                <button
                  onClick={async () => {
                    await notificationService.markRead(log.id)
                    refreshLogs()
                  }}
                  className="shrink-0 text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
                >
                  Dismiss
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────

export default function NotificationsPage() {
  const { data: preferences, mutate: reload, isLoading } = useNotificationPreferences()

  const prefByChannel = (ch: NotificationChannel) =>
    preferences?.find(p => p.channel === ch && p.project_id === null)

  const channels: NotificationChannel[] = ['email', 'slack', 'teams']

  return (
    <div className="space-y-6 max-w-2xl">
      <PageHeader
        title="Notification Settings"
        subtitle="Choose how and when TestLookup alerts you about test results"
      />

      <SmtpConfigCard />

      {isLoading ? (
        <div className="flex justify-center py-12">
          <LoadingSpinner size="lg" />
        </div>
      ) : (
        <>
          <div className="space-y-3">
            {channels.map(ch => (
              <ChannelCard
                key={ch}
                channel={ch}
                preference={prefByChannel(ch)}
                onSaved={reload}
              />
            ))}
          </div>

          <div className="card bg-[var(--color-bg-card)]/50 border border-[var(--color-border)]">
            <h4 className="font-medium text-[var(--color-text-secondary)] text-sm mb-2 flex items-center gap-2">
              <Bell className="w-4 h-4 text-[var(--color-text-muted)]" /> Global defaults
            </h4>
            <p className="text-xs text-[var(--color-text-muted)] leading-relaxed">
              Preferences with <em>no project selected</em> apply to all projects.
              You can add project-specific overrides via the API (
              <code className="font-mono bg-[var(--color-bg-secondary)] px-1 rounded">POST /api/v1/notifications/preferences</code>
              {' '}with a <code className="font-mono bg-[var(--color-bg-secondary)] px-1 rounded">project_id</code>).
            </p>
          </div>

          <HistoryPanel />
        </>
      )}
    </div>
  )
}
