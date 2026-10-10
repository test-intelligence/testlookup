/**
 * Email (SMTP) & channels (UX redesign P5 item 2): the deployment's own
 * notification plumbing, for QA leads and admins.
 *
 *  - the mail server every email notification leaves through (SMTP);
 *  - the shared Slack and Teams channels: where a person's Slack or Teams
 *    notifications are delivered when they set no webhook of their own
 *    (`manager.preference_webhook`). They are EDITED on Integrations, the one
 *    place they are configured; this page says whether they deliver.
 *
 * A person's own channels and events are My notifications
 * (`MyNotificationsPage`, every role); until P5 this page rendered that same
 * body under the SMTP card. A team's own channel is Team channels.
 */
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import ScopedLink from '@/components/ui/ScopedLink'
import useSWR from 'swr'
import { CheckCircle, XCircle, Send, Server, Eye, EyeOff, AlertTriangle, Megaphone, ChevronRight } from 'lucide-react'
import { describeLoadError } from '@/utils/loadError'
import toast from 'react-hot-toast'
import Field from '@/components/ui/Field'
import PageHeader from '@/components/ui/PageHeader'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { helpTopicParam } from '@/components/help/helpTopics'
import { appSettingsService } from '@/services/appSettingsService'
import type { IntegrationsConfigRead, SmtpConfigRead } from '@/services/appSettingsService'
import { tlsMismatch, tlsModeForPort } from './smtpTls'


const HELP_TOPIC = helpTopicParam('/settings/notifications')

const FIELD_LABEL = 'block text-xs font-medium text-[var(--color-text-muted)] mb-1.5'

// ── SMTP configuration card ───────────────────────────────────

/**
 * Shown INSTEAD of the SMTP form when its config could not be read.
 *
 * The form's initial state is a set of placeholders, not the deployment's
 * settings. Rendering it after a failed GET presented those placeholders as
 * the current configuration and left Save armed -- so an admin who opened this
 * page during a backend blip could silently replace a working mail server with
 * `localhost:587`, disabled. Hiding the fields is the point: there is nothing
 * safe to edit until we know what is actually stored.
 */
function SmtpLoadFailure({ error }: { error: unknown }) {
  const info = describeLoadError(error)
  return (
    <div
      role="alert"
      data-testid="smtp-config-unavailable"
      className="flex items-start gap-2 rounded-lg border px-3 py-2.5 text-sm"
      style={{
        borderColor: 'var(--status-broken-bd)',
        background: 'var(--status-broken-bg)',
        color: 'var(--status-broken)',
      }}
    >
      <AlertTriangle className="mt-0.5 h-4 w-4 flex-shrink-0" aria-hidden />
      <div>
        <strong className="font-semibold">{info.title}.</strong>{' '}
        <span className="text-[var(--color-text-secondary)]">
          {info.kind === 'unauthorized'
            ? info.message
            : `${info.message} The form is hidden rather than shown at its placeholder ` +
              'defaults, which would overwrite the stored server on save.'}
        </span>
      </div>
    </div>
  )
}

export function SmtpConfigCard() {
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [showPassword, setShowPassword] = useState(false)

  // A failed GET must not leave the form editable at its constructor defaults.
  // Those defaults (localhost:587, noreply@testlookup.io, disabled) are not the
  // deployment's config -- they are placeholders -- and Save posts the whole
  // object, so one click would overwrite a working production SMTP setup with
  // fiction. When we could not read the config, we do not offer to write it.
  const [loadError, setLoadError] = useState<unknown>(null)

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
      .catch((err: unknown) => setLoadError(err ?? new Error('SMTP config unavailable')))
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
    <section aria-label="Email server (SMTP)" className="card border border-[var(--color-border)] rounded-lg overflow-hidden">
      <div className="flex items-center gap-3 p-4 border-b border-[var(--color-border)]">
        <div className="p-2 rounded-lg bg-[var(--color-bg-secondary)] text-[var(--color-text)]">
          <Server className="w-4 h-4" />
        </div>
        <div className="flex-1">
          <h2 className="font-semibold text-[var(--color-text)]">Email Server (SMTP)</h2>
          <p className="text-xs text-[var(--color-text-muted)] mt-0.5">
            Configure the outgoing mail server for email notifications
          </p>
        </div>
        <span
          className={`text-xs px-2 py-0.5 rounded-full font-medium ${
            enabled
              ? 'bg-[var(--status-passed-bg)]/40 text-[var(--status-passed)]'
              : 'bg-[var(--color-bg-hover)] text-[var(--color-text-muted)]'
          }`}
        >
          {enabled ? 'Active' : 'Disabled'}
        </span>
      </div>

      <div className="p-4 space-y-5">
        {loading ? (
          <div className="flex justify-center py-4"><LoadingSpinner size="sm" /></div>
        ) : loadError ? (
          <SmtpLoadFailure error={loadError} />
        ) : (
          <>
            {/* Enable toggle */}
            <div className="flex items-center justify-between">
              <span className="text-sm text-[var(--color-text-secondary)]">Enable SMTP email delivery</span>
              {/* A switch: named, and its state exposed (it had neither). */}
              <button
                onClick={() => setEnabled(v => !v)}
                role="switch"
                aria-checked={enabled}
                aria-label="Enable SMTP email delivery"
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

            {/*
              The server and the sender beside each other, the credentials
              beside each other: two columns at >= 1280 px (UX redesign P5
              item 4), one below.
            */}
            <div className="grid grid-cols-1 gap-4 xl:grid-cols-2" data-form-grid="">
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                {/* Host */}
                <div className="sm:col-span-2">
                  <Field label="SMTP Host" labelClassName={FIELD_LABEL}>
                    {id => (
                      <input
                        id={id}
                        type="text"
                        value={host}
                        onChange={e => setHost(e.target.value)}
                        placeholder="smtp.example.com"
                        className="input w-full text-sm"
                      />
                    )}
                  </Field>
                </div>
                {/* Port */}
                <div>
                  <Field label="Port" labelClassName={FIELD_LABEL}>
                    {id => (
                      <input
                        id={id}
                        type="number"
                        value={port}
                        onChange={e => {
                          const next = Number(e.target.value)
                          setPort(next)
                          // The two well-known submission ports imply the mode:
                          // 465 = implicit TLS, 587 = STARTTLS. Left as it was,
                          // Gmail on 587 kept implicit TLS and every send failed
                          // with an SSL error (owner report 2026-10-10).
                          const paired = tlsModeForPort(next)
                          if (paired !== null) setImplicitTls(paired)
                        }}
                        min={1}
                        max={65535}
                        className="input w-full text-sm"
                      />
                    )}
                  </Field>
                </div>
              </div>

              {/* From address */}
              <div>
                <Field label="From Address" labelClassName={FIELD_LABEL}>
                  {id => (
                    <input
                      id={id}
                      type="email"
                      value={fromAddress}
                      onChange={e => setFromAddress(e.target.value)}
                      placeholder="noreply@testlookup.io"
                      className="input w-full text-sm"
                    />
                  )}
                </Field>
              </div>

              {/* Username */}
              <div>
                <Field
                  label="Username"
                  hint={<span className="text-[var(--color-text-faint)]"> (optional)</span>}
                  labelClassName={FIELD_LABEL}
                >
                  {id => (
                    <input
                      id={id}
                      type="text"
                      value={user}
                      onChange={e => setUser(e.target.value)}
                      placeholder="smtp-user@example.com"
                      className="input w-full text-sm"
                    />
                  )}
                </Field>
              </div>

              {/* Password */}
              <div>
                <Field
                  label="Password"
                  hint={
                    <span className="text-[var(--color-text-faint)]">
                      {' '}
                      {passwordSet && !clearPassword ? '(stored — leave blank to keep)' : '(optional)'}
                    </span>
                  }
                  labelClassName={FIELD_LABEL}
                >
                  {id => (
                    <div className="relative">
                      <input
                        id={id}
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
                        aria-label={showPassword ? 'Hide password' : 'Show password'}
                        title={showPassword ? 'Hide password' : 'Show password'}
                        className="absolute right-2.5 top-1/2 -translate-y-1/2 text-[var(--color-text-muted)] hover:text-[var(--color-text-secondary)]"
                      >
                        {showPassword ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                      </button>
                    </div>
                  )}
                </Field>
                {passwordSet && password === '' && (
                  <div className="mt-1.5" data-testid="smtp-password-status">
                    {clearPassword ? (
                      <span className="text-xs text-[var(--status-broken)] flex items-center gap-1">
                        <XCircle className="w-3.5 h-3.5" />
                        Password will be cleared on save.{' '}
                        <button
                          type="button"
                          onClick={() => setClearPassword(false)}
                          className="underline hover:text-[var(--status-broken)]"
                        >
                          Undo
                        </button>
                      </span>
                    ) : (
                      // A stored password used to show only a red "Clear stored
                      // password" link under an empty field, which read as an
                      // error: "unable to save the SMTP password" (owner report
                      // 2026-10-10) when it had saved. Say it is stored; keep
                      // Clear as a quiet secondary action.
                      <span className="text-xs text-[var(--color-text-muted)] flex items-center gap-1">
                        <CheckCircle className="w-3.5 h-3.5 text-[var(--status-passed)]" />
                        Password saved. Leave blank to keep it.{' '}
                        <button
                          type="button"
                          onClick={() => setClearPassword(true)}
                          className="underline hover:text-[var(--color-text-secondary)] transition-colors"
                        >
                          Clear
                        </button>
                      </span>
                    )}
                  </div>
                )}
              </div>
            </div>

            {/* TLS / STARTTLS mode toggle */}
            <div className="flex items-center justify-between">
              <div>
                <span className="text-sm text-[var(--color-text-secondary)]">
                  Connection security mode:{' '}
                  <strong data-testid="smtp-tls-mode">{implicitTls ? 'Implicit TLS' : 'STARTTLS'}</strong>
                </span>
                <p className="text-xs text-[var(--color-text-muted)] mt-0.5">
                  Toggle between implicit TLS (typically port 465) and STARTTLS (typically port 587). Plain SMTP is not supported.
                </p>
                {tlsMismatch(port, implicitTls) && (
                  <p className="text-xs text-[var(--status-broken)] mt-1 flex items-center gap-1" data-testid="smtp-tls-mismatch">
                    <AlertTriangle className="w-3.5 h-3.5" />
                    {tlsMismatch(port, implicitTls)}
                  </p>
                )}
              </div>
              <button
                type="button"
                aria-label={implicitTls ? 'Use STARTTLS' : 'Use implicit TLS'}
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
    </section>
  )
}

// ── Shared channels ───────────────────────────────────────────

const PILL = 'shrink-0 text-xs px-2 py-0.5 rounded-full font-medium'

/**
 * One shared webhook's state, as the dispatcher sees it
 * (`manager._enabled_global_webhook`): delivered to only when it is switched
 * on AND has a webhook. On without a webhook delivers nothing, and says so.
 */
function sharedChannelState(enabled: boolean, webhookSet: boolean): { pill: string; pillClass: string; detail: string } {
  if (enabled && webhookSet) {
    return {
      pill: 'Delivering',
      pillClass: 'bg-[var(--status-passed-bg)]/40 text-[var(--status-passed)]',
      detail: 'On, with a webhook',
    }
  }
  if (enabled) {
    return {
      pill: 'No webhook',
      pillClass: 'bg-[var(--status-broken-bg)] text-[var(--status-broken)]',
      detail: 'On, but no webhook is set: nothing is delivered',
    }
  }
  return {
    pill: 'Off',
    pillClass: 'bg-[var(--color-bg-hover)] text-[var(--color-text-muted)]',
    detail: webhookSet ? 'A webhook is set; delivery is switched off' : 'Off, and no webhook is set',
  }
}

/** A failed read is said as one, never drawn as "Off": off is a claim about the deployment. */
function SharedChannelsLoadFailure({ error }: { error: unknown }) {
  const info = describeLoadError(error)
  return (
    <div
      role="alert"
      data-testid="shared-channels-unavailable"
      className="m-4 flex items-start gap-2 rounded-lg border px-3 py-2.5 text-sm"
      style={{ borderColor: 'var(--status-broken-bd)', background: 'var(--status-broken-bg)', color: 'var(--status-broken)' }}
    >
      <AlertTriangle className="mt-0.5 h-4 w-4 flex-shrink-0" aria-hidden />
      <div>
        <strong className="font-semibold">{info.title}.</strong>{' '}
        <span className="text-[var(--color-text-secondary)]">{info.message}</span>
      </div>
    </div>
  )
}

function SharedChannelsCard() {
  const { data, error, isLoading } = useSWR<IntegrationsConfigRead>(
    'settings/shared-notification-channels',
    () => appSettingsService.getIntegrationsConfig(),
    { revalidateOnFocus: false, shouldRetryOnError: false },
  )

  const rows = data
    ? [
        { id: 'slack', label: 'Slack', ...sharedChannelState(data.slack_enabled, data.slack_webhook_set) },
        { id: 'teams', label: 'Microsoft Teams', ...sharedChannelState(data.teams_enabled, data.teams_webhook_set) },
      ]
    : []

  return (
    <section
      aria-labelledby="shared-channels-heading"
      data-shared-channels=""
      className="card border border-[var(--color-border)] rounded-lg overflow-hidden"
    >
      <div className="flex items-center gap-3 p-4 border-b border-[var(--color-border)]">
        <div className="p-2 rounded-lg bg-[var(--color-bg-secondary)] text-[var(--color-text)]">
          <Megaphone className="w-4 h-4" />
        </div>
        <div className="flex-1 min-w-0">
          <h2 id="shared-channels-heading" className="font-semibold text-[var(--color-text)]">Shared channels</h2>
          <p className="text-xs text-[var(--color-text-muted)] mt-0.5">
            Where a person&apos;s Slack or Teams notifications go when they have not set a webhook of their own
          </p>
        </div>
        <Link
          to="/settings/integrations"
          className="shrink-0 inline-flex items-center gap-1 text-xs font-medium text-[var(--color-accent)] hover:underline"
        >
          Edit in Integrations
          <ChevronRight className="w-3.5 h-3.5" aria-hidden="true" />
        </Link>
      </div>

      <ul className="divide-y divide-[var(--color-border)]">
        {isLoading ? (
          <li className="flex justify-center py-4"><LoadingSpinner size="sm" /></li>
        ) : error ? (
          <li><SharedChannelsLoadFailure error={error} /></li>
        ) : (
          rows.map(row => (
            <li key={row.id} data-shared-channel={row.id} className="flex items-center gap-3 px-4 py-2.5 text-sm">
              <span className="w-40 shrink-0 font-medium text-[var(--color-text)]">{row.label}</span>
              <span className="min-w-0 flex-1 truncate text-xs text-[var(--color-text-muted)]">{row.detail}</span>
              <span className={`${PILL} ${row.pillClass}`}>{row.pill}</span>
            </li>
          ))
        )}
        {/* Not part of the read above: a team's channel is per project (Team channels). */}
        <li data-shared-channel="team-channels" className="flex items-center gap-3 px-4 py-2.5 text-sm">
          <span className="w-40 shrink-0 font-medium text-[var(--color-text)]">Team channels</span>
          <span className="min-w-0 flex-1 truncate text-xs text-[var(--color-text-muted)]">
            A team&apos;s transition alerts sent to the team&apos;s own channel, per project
          </span>
          <ScopedLink
            to="/settings/team-channels"
            containerClassName="shrink-0"
            className="inline-flex items-center gap-1 text-xs font-medium text-[var(--color-accent)] hover:underline"
          >
            Team channels
            <ChevronRight className="w-3.5 h-3.5" aria-hidden="true" />
          </ScopedLink>
        </li>
      </ul>
    </section>
  )
}

// ── Page ──────────────────────────────────────────────────────

export default function NotificationsPage() {
  return (
    <div className="space-y-4">
      <PageHeader
        compact
        title="Email (SMTP) & channels"
        subtitle="The mail server, and the channels notifications are shared through"
        helpTopic={HELP_TOPIC}
      />

      <SmtpConfigCard />

      <SharedChannelsCard />

      <p className="text-xs text-[var(--color-text-muted)]">
        Your own channels and events are on{' '}
        <Link to="/settings/my-notifications" className="text-[var(--color-accent)] hover:underline">My notifications</Link>.
      </p>
    </div>
  )
}
