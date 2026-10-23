/**
 * W10 INT-04 (contract §2.4 test 5): grouping by kind and provider, the "Not
 * probed yet" label (never "healthy"), a row's history option, and the global
 * rows the workflow timeline is fed.
 */
import { describe, expect, it } from 'vitest'

import type { IntegrationStatus } from '@/services/integrationHealthService'
import {
  NOT_PROBED_LABEL,
  ROLE_LINE,
  globalRows,
  groupHealthRows,
  historyOptionLabel,
  historyOptionValue,
  historyRows,
  isProbed,
  parseHistoryOption,
  pausedWebhookLine,
  projectSectionHeading,
  rowKey,
  rowKind,
  rowTitle,
  settingsLinkLabel,
  settingsPathFor,
  statusLabel,
  statusTone,
} from './integrationHealthModel'

function row(overrides: Partial<IntegrationStatus>): IntegrationStatus {
  return {
    provider: 'jira',
    status: 'healthy',
    last_checked_at: null,
    message: null,
    response_ms: null,
    consecutive_failures: 0,
    last_success_at: null,
    ...overrides,
  }
}

const JIRA = row({ provider: 'jira' })
// Every pre-W10 fixture: no kind, no probed, no scope_key.
const OLD_GITHUB = row({ provider: 'github', status: 'skipped' })
const GH_PAY = row({ provider: 'github', kind: 'project', scope_key: 'p-1', project_id: 'p-1', project_name: 'Payment Service', label: 'Payment Service', target: 'ghe.corp.example.com/api/v3' })
const GH_CHECKOUT = row({ provider: 'github', kind: 'project', scope_key: 'p-3', project_id: 'p-3', project_name: 'Checkout', label: 'Checkout', status: 'auth_error' })
const GL_GATEWAY = row({ provider: 'gitlab', kind: 'project', scope_key: 'p-2', project_id: 'p-2', project_name: 'API Gateway', label: 'API Gateway', status: 'unknown', probed: false })
const HOOK = row({ provider: 'webhook', kind: 'webhook', scope_key: 'wh-1', project_id: 'p-1', project_name: 'Payment Service', label: 'Release bot', status: 'paused', consecutive_failures: 3, breaker_threshold: 3 })

describe('rowKind / isProbed', () => {
  it('a missing kind reads global, a missing probed reads true', () => {
    expect(rowKind(OLD_GITHUB)).toBe('global')
    expect(isProbed(OLD_GITHUB)).toBe(true)
    expect(rowKind(row({ kind: 'something_new' }))).toBe('global')
    expect(rowKind(GH_PAY)).toBe('project')
    expect(rowKind(HOOK)).toBe('webhook')
    expect(isProbed(GL_GATEWAY)).toBe(false)
  })
})

describe('groupHealthRows / globalRows', () => {
  const rows = [GL_GATEWAY, JIRA, GH_PAY, HOOK, OLD_GITHUB, GH_CHECKOUT]

  it('splits by kind; project rows grouped per provider, GitHub first, in the server\'s order', () => {
    const grouped = groupHealthRows(rows)
    expect(grouped.global).toEqual([JIRA, OLD_GITHUB])
    expect(grouped.projects.map((g) => g.provider)).toEqual(['github', 'gitlab'])
    expect(grouped.projects[0].rows).toEqual([GH_PAY, GH_CHECKOUT])
    expect(grouped.projects[1].rows).toEqual([GL_GATEWAY])
    expect(grouped.webhooks).toEqual([HOOK])
  })

  it('the workflow timeline is fed the global rows only', () => {
    expect(globalRows(rows)).toEqual([JIRA, OLD_GITHUB])
    // A pre-W10 answer is all global.
    expect(globalRows([JIRA, OLD_GITHUB])).toEqual([JIRA, OLD_GITHUB])
  })

  it('nothing to group reads empty', () => {
    expect(groupHealthRows([])).toEqual({ global: [], projects: [], webhooks: [] })
  })
})

describe('the copy', () => {
  it('an unprobed row says "Not probed yet", never healthy, whatever status it carries', () => {
    expect(NOT_PROBED_LABEL).toBe('Not probed yet')
    expect(statusLabel(GL_GATEWAY)).toBe('Not probed yet')
    expect(statusLabel(row({ ...GL_GATEWAY, status: 'healthy' }))).toBe('Not probed yet')
    expect(statusTone(GL_GATEWAY)).toBe('not_probed')
  })

  it('a paused webhook reads paused; a probed row its status', () => {
    expect(statusLabel(HOOK)).toBe('paused')
    expect(statusTone(HOOK)).toBe('paused')
    expect(statusLabel(GH_CHECKOUT)).toBe('auth_error')
    expect(statusLabel(JIRA)).toBe('healthy')
  })

  it('titles: "GitHub · Payment Service", "Webhook Release bot · Payment Service", the provider for a global row', () => {
    expect(rowTitle(GH_PAY)).toBe('GitHub · Payment Service')
    expect(rowTitle(GL_GATEWAY)).toBe('GitLab · API Gateway')
    expect(rowTitle(HOOK)).toBe('Webhook Release bot · Payment Service')
    expect(rowTitle(JIRA)).toBe('jira')
    // No label: the project's name, then its id.
    expect(rowTitle(row({ ...GH_PAY, label: null }))).toBe('GitHub · Payment Service')
    expect(rowTitle(row({ ...GH_PAY, label: null, project_name: null }))).toBe('GitHub · p-1')
  })

  it('a paused webhook\'s count and threshold', () => {
    expect(pausedWebhookLine(HOOK)).toBe('Paused after 3 consecutive failures (threshold 3)')
    expect(pausedWebhookLine(row({ ...HOOK, breaker_threshold: null }))).toBe('Paused after 3 consecutive failures')
  })

  it('sections, links and the role line', () => {
    expect(projectSectionHeading('github')).toBe('GitHub projects')
    expect(projectSectionHeading('gitlab')).toBe('GitLab projects')
    expect(settingsPathFor('github')).toBe('/settings/github')
    expect(settingsPathFor('gitlab')).toBe('/settings/gitlab')
    expect(settingsPathFor('jira')).toBeNull()
    expect(settingsLinkLabel('github')).toBe('Open GitHub settings')
    expect(settingsLinkLabel('gitlab')).toBe('Open GitLab settings')
    expect(ROLE_LINE).toBe('Integration health needs the QA lead role or above.')
  })
})

describe('the History select', () => {
  it('a global row\'s value is its provider; a project row\'s is provider:projectId', () => {
    expect(historyOptionValue(JIRA)).toBe('jira')
    expect(historyOptionValue(GH_PAY)).toBe('github:p-1')
    expect(historyOptionLabel(GH_PAY)).toBe('GitHub · Payment Service')
    expect(historyOptionLabel(JIRA)).toBe('jira')
  })

  it('round-trips through parseHistoryOption', () => {
    expect(parseHistoryOption('github:p-1')).toEqual({ provider: 'github', projectId: 'p-1' })
    expect(parseHistoryOption('jira')).toEqual({ provider: 'jira' })
    expect(parseHistoryOption('')).toEqual({ provider: '' })
  })

  it('offers global and project rows, never a paused webhook', () => {
    expect(historyRows([JIRA, GH_PAY, HOOK, GL_GATEWAY])).toEqual([JIRA, GH_PAY, GL_GATEWAY])
  })

  it('keys stay distinct for a global and a project row of the same provider', () => {
    expect(rowKey(OLD_GITHUB)).not.toBe(rowKey(GH_PAY))
    expect(rowKey(GH_PAY)).not.toBe(rowKey(GH_CHECKOUT))
  })
})
