/**
 * Outbound webhooks, the circuit breaker's model (W10 INT-07). Pure: the page
 * and its tests import the same strings.
 *
 * A subscription is paused (`disabled_reason === 'circuit_open'`) after
 * `breaker_threshold` deliveries in a row END failed — a retried attempt counts
 * once, when its retries are spent; a refused or unreachable target counts too.
 * Re-enable sends the full row back with `enabled: true` (the PATCH takes the
 * whole write), which resets the counter on the server.
 */
import type { WebhookSubscriptionRead, WebhookSubscriptionWrite } from '@/services/outboundWebhookService'

/** The server's default threshold for a new subscription. */
export const DEFAULT_BREAKER_THRESHOLD = 10
export const MIN_BREAKER_THRESHOLD = 1
export const MAX_BREAKER_THRESHOLD = 100

/** The create form's field label. */
export const THRESHOLD_LABEL = 'Pause after (consecutive failed deliveries)'
/** What the ENABLED / DISABLED toggle reads on a paused row. */
export const PAUSED_CHIP = 'PAUSED'
export const REENABLE_LABEL = 'Re-enable'
export const REENABLED_TOAST = 'Webhook re-enabled'
/** The page's help, after `BREAKER_HELP_LEAD` in bold. */
export const BREAKER_HELP_LEAD = 'Circuit breaker:'
export const BREAKER_HELP =
  'a subscription is paused after N deliveries in a row end failed (retries count once); deliveries meanwhile are kept as failed and can be replayed after Re-enable.'

/** Paused by the breaker (not a person's "Disabled", whose reason is `manual` or none). */
export function isPaused(sub: Pick<WebhookSubscriptionRead, 'disabled_reason'>): boolean {
  return sub.disabled_reason === 'circuit_open'
}

/** The line under a paused row's header. A missing count reads 0; a missing error is left out. */
export function pausedLine(sub: Pick<WebhookSubscriptionRead, 'consecutive_failures' | 'last_error'>): string {
  const n = sub.consecutive_failures ?? 0
  const head = `Paused after ${n} consecutive failures (a refused or unreachable target counts too).`
  return sub.last_error ? `${head} Last error: ${sub.last_error}` : head
}

/**
 * The streak beside "✓ n · ✗ n" on an ENABLED row that is failing:
 * `{consecutive}/{threshold} in a row`; null otherwise (and for an older server).
 */
export function streakLabel(sub: Pick<WebhookSubscriptionRead, 'enabled' | 'consecutive_failures' | 'breaker_threshold'>): string | null {
  const n = sub.consecutive_failures ?? 0
  if (!sub.enabled || n <= 0) return null
  return `${n}/${sub.breaker_threshold ?? DEFAULT_BREAKER_THRESHOLD} in a row`
}

/** The PATCH body that re-enables a row: the full write, `enabled: true`, the stored threshold (left out when the server sent none). */
export function reenableBody(sub: WebhookSubscriptionRead): WebhookSubscriptionWrite {
  return {
    name: sub.name,
    target_url: sub.target_url,
    events: sub.events,
    enabled: true,
    max_retries: sub.max_retries,
    ...(typeof sub.breaker_threshold === 'number' ? { breaker_threshold: sub.breaker_threshold } : {}),
  }
}

/** The create form refuses (with this toast) a threshold that is not a whole number from 1 to 100. */
export const THRESHOLD_INVALID = 'Pause after must be a whole number from 1 to 100.'

export function validThreshold(value: number | undefined): boolean {
  return typeof value === 'number' && Number.isInteger(value) && value >= MIN_BREAKER_THRESHOLD && value <= MAX_BREAKER_THRESHOLD
}
