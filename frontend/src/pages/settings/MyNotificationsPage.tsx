/**
 * My notifications (UX redesign P1, split out in P5): the signed-in user's own
 * channels, events and history, for every role.
 *
 * The preference and history endpoints are per-user and open to any active
 * user. Nothing here is the deployment's: the mail server and the shared
 * Slack / Teams channels are the admin page's (`NotificationsPage`, "Email
 * (SMTP) & channels"), and a team's own channel is Team channels'. Until P5
 * the admin page rendered this same body under its SMTP card, so an admin
 * edited their personal preferences on a page named for the server's.
 */
import { useId, useState } from 'react'
import { Bell, Mail, MessageSquare, Users, CheckCircle, XCircle, Send, Trash2, ChevronDown, ChevronUp } from 'lucide-react'
import toast from 'react-hot-toast'
import PageHeader from '@/components/ui/PageHeader'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import DataUnavailable from '@/components/ui/DataUnavailable'
import { helpTopicParam } from '@/components/help/helpTopics'
import {
  useNotificationPreferences,
  useNotificationHistory,
  invalidateNotifications,
} from '@/hooks/useNotifications'
import { notificationService } from '@/services/notificationService'
import type {
  NotificationChannel,
  NotificationEventType,
  NotificationPreference,
  NotificationPreferencePayload,
} from '@/types/notifications'
import { formatCompactDateTime } from '@/utils/formatters'

const HELP_TOPIC = helpTopicParam('/settings/my-notifications')

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
  // Quarantine lifecycle events (US-5.4 / US-5.5)
  'test.quarantine_stale': 'Quarantine past its SLA (lifecycle)',
  'test.ready_to_unquarantine': 'Quarantined test ready to release (lifecycle)',
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
    colour: 'text-[var(--status-passed)]',
    // Blank is delivered to the deployment's shared webhook, when an admin
    // set one (`manager.preference_webhook`): the admin page lists it.
    placeholder: 'Slack incoming webhook URL (leave blank to use the shared Slack channel)',
  },
  teams: {
    label: 'Microsoft Teams',
    icon: Users,
    colour: 'text-[var(--status-flaky)]',
    placeholder: 'Teams incoming webhook URL (leave blank to use the shared Teams channel)',
  },
}

const FIELD_LABEL = 'block text-xs font-medium text-[var(--color-text-muted)] mb-1.5'

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
  // One id per card: with two cards open, fixed ids pointed the second card's
  // labels at the first card's inputs.
  const fieldId = useId()

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
    <div className="card border border-[var(--color-border)] rounded-lg overflow-hidden" data-channel-card={channel}>
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
                ? 'bg-[var(--status-passed-bg)]/40 text-[var(--status-passed)]'
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

          {/* Where, and how loud: two columns at >= 1280 px (UX redesign P5 item 4). */}
          <div className="grid grid-cols-1 gap-5 xl:grid-cols-2" data-form-grid="">
            {/* Webhook / Email field */}
            <div>
              <label htmlFor={`${fieldId}-target`} className={FIELD_LABEL}>
                {channel === 'email' ? 'Email address override' : 'Webhook URL'}
              </label>
              <input
                id={`${fieldId}-target`}
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

            {/* Failure rate threshold */}
            <div>
              <label htmlFor={`${fieldId}-threshold`} className={FIELD_LABEL}>
                High failure rate threshold:{' '}
                <span className="text-[var(--color-text)] font-mono">{threshold}%</span>
              </label>
              <input
                id={`${fieldId}-threshold`}
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
          </div>

          {/* Event selection: the long list keeps the full width. */}
          <div>
            <p className="block text-xs font-medium text-[var(--color-text-muted)] mb-2">Notify me when</p>
            <EventCheckboxes selected={events} onChange={setEvents} />
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
                className="ml-auto flex items-center gap-1.5 text-sm text-[var(--status-failed)] hover:text-[var(--status-failed)] transition-colors"
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
                  <CheckCircle className="w-4 h-4 text-[var(--status-passed)]" />
                ) : (
                  <XCircle className="w-4 h-4 text-[var(--status-failed)]" />
                )}
              </span>
              <div className="flex-1 min-w-0">
                <p className="text-sm text-[var(--color-text)] font-medium truncate">{log.title}</p>
                <p className="text-xs text-[var(--color-text-muted)] mt-0.5">
                  {log.channel.toUpperCase()} · {log.event_type.replace(/_/g, ' ')} ·{' '}
                  {formatCompactDateTime(log.created_at)}
                </p>
              </div>
              {!log.is_read && (
                <button
                  onClick={async () => {
                    await notificationService.markRead(log.id)
                    // Same fan-out as handleMarkAll above. refreshLogs only
                    // touches ['notifications/history', false]; the unread
                    // badge lives on 'notifications/unread' in TopBar, which is
                    // part of the persistent layout and never unmounts.
                    refreshLogs()
                    await invalidateNotifications()
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

// ── Page ──────────────────────────────────────────────────────

const CHANNELS: NotificationChannel[] = ['email', 'slack', 'teams']

export default function MyNotificationsPage() {
  const { data: preferences, mutate: reload, isLoading, error } = useNotificationPreferences()

  const prefByChannel = (ch: NotificationChannel) =>
    preferences?.find(p => p.channel === ch && p.project_id === null)

  return (
    <div className="space-y-4">
      <PageHeader
        compact
        title="My notifications"
        subtitle="Where TestLookup alerts you about test results, and about what"
        helpTopic={HELP_TOPIC}
      />
      {isLoading ? (
        <div className="flex justify-center py-12">
          <LoadingSpinner size="lg" />
        </div>
      ) : error && !preferences ? (
        // A failed read is not "nothing set up": three empty channel cards
        // would invite someone to re-enter what they already have.
        <DataUnavailable error={error} onRetry={() => void reload()} testId="my-notifications-unavailable" />
      ) : (
        <>
          <div className="space-y-3">
            {CHANNELS.map(ch => (
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
