/**
 * NTF-01: the bell follows only the server's two allow-listed path shapes,
 * re-checked here, and `/releases/<id>` (a management route) only for a QA
 * lead or admin. Equality on the whole value, never a substring.
 */
import { describe, expect, it } from 'vitest'
import { notificationChannelLabel, notificationHref } from './notificationHref'

const ID = '0f8fad5b-d9cb-469f-a165-70867728950e'
const LEAD = { canAccessManagement: true }
const VIEWER = { canAccessManagement: false }

describe('notificationHref', () => {
  it('a run decision links for every role', () => {
    expect(notificationHref({ link: `/release-gate/${ID}` }, LEAD)).toBe(`/release-gate/${ID}`)
    expect(notificationHref({ link: `/release-gate/${ID}` }, VIEWER)).toBe(`/release-gate/${ID}`)
  })

  it('W21d: a mention links to its test case for every role', () => {
    expect(notificationHref({ link: `/test-management?case=${ID}` }, LEAD)).toBe(`/test-management?case=${ID}`)
    expect(notificationHref({ link: `/test-management?case=${ID}` }, VIEWER)).toBe(`/test-management?case=${ID}`)
  })

  it('a release links for a QA lead or admin only', () => {
    expect(notificationHref({ link: `/releases/${ID}` }, LEAD)).toBe(`/releases/${ID}`)
    expect(notificationHref({ link: `/releases/${ID}` }, VIEWER)).toBeNull()
  })

  it.each([
    ['no link', {}],
    ['null', { link: null }],
    ['undefined log', undefined],
    ['null log', null],
    ['a number', { link: 42 }],
    ['an object', { link: { href: `/releases/${ID}` } }],
    ['empty', { link: '' }],
    ['not a uuid', { link: '/releases/x' }],
    ['an upper-case uuid', { link: `/releases/${ID.toUpperCase()}` }],
    ['an absolute URL', { link: `https://example.com/releases/${ID}` }],
    ['protocol-relative', { link: '//example.com' }],
    ['protocol-relative with the path', { link: `//example.com/releases/${ID}` }],
    ['another route', { link: '/settings' }],
    ['a trailing segment', { link: `/releases/${ID}/edit` }],
    ['a query', { link: `/releases/${ID}?tab=gate` }],
    ['a trailing slash', { link: `/release-gate/${ID}/` }],
    ['a leading space', { link: ` /release-gate/${ID}` }],
    ['javascript:', { link: 'javascript:alert(1)' }],
    ['a newline after', { link: `/release-gate/${ID}\n` }],
    // W21d: the test-case shape's near misses.
    ['a case that is not a uuid', { link: '/test-management?case=x' }],
    ['a case with another parameter', { link: `/test-management?case=${ID}&tab=comments` }],
    ['a case with an upper-case uuid', { link: `/test-management?case=${ID.toUpperCase()}` }],
    ['a case with a trailing slash', { link: `/test-management?case=${ID}/` }],
    ['a case under a slash', { link: `/test-management/?case=${ID}` }],
    ['the ?test= parameter', { link: `/test-management?test=${ID}` }],
    ['any character for the ?', { link: `/test-managementXcase=${ID}` }],
    ['a case on another host', { link: `https://example.com/test-management?case=${ID}` }],
    ['a case after a newline', { link: `/test-management?case=${ID}\n` }],
  ])('%s → null for every role', (_name, log) => {
    expect(notificationHref(log as never, LEAD)).toBeNull()
    expect(notificationHref(log as never, VIEWER)).toBeNull()
  })
})

describe('notificationChannelLabel', () => {
  it('an in-app mention reads IN APP; the other channels as before', () => {
    expect(notificationChannelLabel('in_app')).toBe('IN APP')
    expect(notificationChannelLabel('email')).toBe('EMAIL')
    expect(notificationChannelLabel('slack')).toBe('SLACK')
    expect(notificationChannelLabel('teams')).toBe('TEAMS')
    expect(notificationChannelLabel(undefined)).toBe('')
  })
})
