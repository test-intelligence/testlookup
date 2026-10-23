/**
 * W10 INT-07 (contract §2.5 test 3): the circuit breaker's model — paused vs a
 * person's Disabled vs enabled, the paused line, the streak, Re-enable's body.
 */
import { describe, expect, it } from 'vitest'

import type { WebhookSubscriptionRead } from '@/services/outboundWebhookService'
import {
  BREAKER_HELP,
  DEFAULT_BREAKER_THRESHOLD,
  THRESHOLD_LABEL,
  isPaused,
  pausedLine,
  reenableBody,
  streakLabel,
  validThreshold,
} from './webhookBreakerModel'

function sub(overrides: Partial<WebhookSubscriptionRead> = {}): WebhookSubscriptionRead {
  return {
    id: 'wh-1',
    project_id: 'proj-1',
    name: 'Pager',
    target_url: 'https://hooks.example.com/x',
    events: ['run.completed'],
    enabled: true,
    has_secret: true,
    max_retries: 0,
    last_delivered_at: null,
    last_failure_at: null,
    last_error: null,
    failure_count: 0,
    total_delivered: 0,
    created_at: '2026-10-01T00:00:00Z',
    updated_at: '2026-10-01T00:00:00Z',
    ...overrides,
  }
}

describe('isPaused', () => {
  it('is the breaker\'s pause only: circuit_open', () => {
    expect(isPaused(sub({ enabled: false, disabled_reason: 'circuit_open' }))).toBe(true)
    expect(isPaused(sub({ enabled: false, disabled_reason: 'manual' }))).toBe(false)
    expect(isPaused(sub({ enabled: false, disabled_reason: null }))).toBe(false)
    expect(isPaused(sub({ enabled: true }))).toBe(false)
    // An older server sends no breaker fields: not paused.
    expect(isPaused(sub({ enabled: false }))).toBe(false)
  })
})

describe('pausedLine', () => {
  it('names the count, that a refused target counts, and the last error', () => {
    expect(pausedLine(sub({ consecutive_failures: 3, last_error: 'HTTP 500 receiver error' }))).toBe(
      'Paused after 3 consecutive failures (a refused or unreachable target counts too). Last error: HTTP 500 receiver error',
    )
  })

  it('leaves the error out when there is none, and reads a missing count as 0', () => {
    expect(pausedLine(sub({ consecutive_failures: 10 }))).toBe(
      'Paused after 10 consecutive failures (a refused or unreachable target counts too).',
    )
    expect(pausedLine(sub())).toBe('Paused after 0 consecutive failures (a refused or unreachable target counts too).')
  })
})

describe('streakLabel', () => {
  it('shows {consecutive}/{threshold} in a row on an enabled row that is failing', () => {
    expect(streakLabel(sub({ consecutive_failures: 2, breaker_threshold: 3 }))).toBe('2/3 in a row')
  })

  it('reads the default threshold when the server sent none', () => {
    expect(streakLabel(sub({ consecutive_failures: 4 }))).toBe(`4/${DEFAULT_BREAKER_THRESHOLD} in a row`)
  })

  it('is null on a healthy row, a paused row, a disabled row and an older server', () => {
    expect(streakLabel(sub({ consecutive_failures: 0, breaker_threshold: 3 }))).toBeNull()
    expect(streakLabel(sub({ enabled: false, consecutive_failures: 3, breaker_threshold: 3, disabled_reason: 'circuit_open' }))).toBeNull()
    expect(streakLabel(sub({ enabled: false, consecutive_failures: 1 }))).toBeNull()
    expect(streakLabel(sub())).toBeNull()
  })
})

describe('reenableBody', () => {
  it('is the full write with enabled: true and the stored threshold', () => {
    expect(reenableBody(sub({ enabled: false, disabled_reason: 'circuit_open', breaker_threshold: 3, max_retries: 2 }))).toEqual({
      name: 'Pager',
      target_url: 'https://hooks.example.com/x',
      events: ['run.completed'],
      enabled: true,
      max_retries: 2,
      breaker_threshold: 3,
    })
  })

  it('leaves the threshold out when the server sent none (the PATCH then keeps the stored one)', () => {
    const body = reenableBody(sub({ enabled: false }))
    expect(body).not.toHaveProperty('breaker_threshold')
    expect(body.enabled).toBe(true)
    // Never the secret, never a read-only field.
    expect(body).not.toHaveProperty('secret')
    expect(body).not.toHaveProperty('id')
    expect(body).not.toHaveProperty('consecutive_failures')
  })
})

describe('the create form threshold', () => {
  it('accepts a whole number from 1 to 100', () => {
    expect(validThreshold(1)).toBe(true)
    expect(validThreshold(10)).toBe(true)
    expect(validThreshold(100)).toBe(true)
    expect(validThreshold(0)).toBe(false)
    expect(validThreshold(101)).toBe(false)
    expect(validThreshold(2.5)).toBe(false)
    expect(validThreshold(Number.NaN)).toBe(false)
    expect(validThreshold(undefined)).toBe(false)
  })

  it('the copy', () => {
    expect(THRESHOLD_LABEL).toBe('Pause after (consecutive failed deliveries)')
    expect(BREAKER_HELP).toBe(
      'a subscription is paused after N deliveries in a row end failed (retries count once); deliveries meanwhile are kept as failed and can be replayed after Re-enable.',
    )
  })
})
