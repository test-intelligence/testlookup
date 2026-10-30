/**
 * W10 INT-01 (contract §2.1 test 7): Save sends only what the reader changed.
 * The page used to PUT every prefilled field, so a value the server showed from
 * the deployment environment was stored as if typed (§0.3).
 */
import { describe, expect, it } from 'vitest'

import type { IntegrationsConfigRead } from '@/services/appSettingsService'
import {
  ENV_HINT,
  INTEGRATION_FORM_FIELDS,
  SECRET_SET,
  SECRET_SET_IN_ENV,
  SLACK_CHANNEL_HELP,
  changedIntegrationFields,
  environmentFields,
  initialIntegrationForm,
  secretStateLabel,
  SECRET_SET_VAULT,
  showsEnvHint,
} from './integrationsForm'

function cfg(overrides: Partial<IntegrationsConfigRead> = {}): IntegrationsConfigRead {
  return {
    jira_enabled: true,
    jira_domain: 'acme.atlassian.net',
    jira_email: 'qa@acme.test',
    jira_token_set: true,
    jira_default_project_key: 'QA',
    splunk_enabled: false,
    // Shown from the environment: the page must never send it back unasked.
    splunk_base_url: 'https://splunk.env.example:8089',
    splunk_token_set: false,
    ocp_enabled: false,
    ocp_api_url: null,
    ocp_token_set: false,
    ocp_default_namespace: 'qa-testing',
    slack_enabled: false,
    slack_webhook_url: null,
    slack_webhook_set: false,
    slack_default_channel: '#qa-alerts',
    teams_enabled: false,
    teams_webhook_url: null,
    teams_webhook_set: false,
    github_repo: null,
    github_token_set: false,
    environment_fields: ['splunk_base_url', 'ocp_sa_token'],
    ...overrides,
  }
}

describe('changedIntegrationFields', () => {
  const initial = initialIntegrationForm(cfg())

  it.each([
    ['an untouched form', {}, {}],
    ['an echoed environment value', { splunk_base_url: 'https://splunk.env.example:8089' }, {}],
    ['a toggled switch', { splunk_enabled: true }, { splunk_enabled: true }],
    ['a switch toggled back', { jira_enabled: true }, {}],
    ['a changed text', { splunk_base_url: 'https://splunk.corp.example:8089' }, { splunk_base_url: 'https://splunk.corp.example:8089' }],
    ['a cleared text (hands the key back to the environment)', { jira_domain: '' }, { jira_domain: '' }],
    ['an empty token input', { jira_api_token: '' }, {}],
    ['a blank token input', { splunk_api_token: '   ' }, {}],
    ['a typed token', { splunk_api_token: 'tok-1' }, { splunk_api_token: 'tok-1' }],
    ['a typed webhook URL', { slack_webhook_url: 'https://hooks.slack.com/services/T/B/X' }, { slack_webhook_url: 'https://hooks.slack.com/services/T/B/X' }],
  ] as const)('%s', (_name, edit, expected) => {
    expect(changedIntegrationFields(initial, { ...initial, ...edit })).toEqual(expected)
  })

  it('sends exactly the changed fields together, nothing else', () => {
    const body = changedIntegrationFields(initial, {
      ...initial,
      splunk_enabled: true,
      splunk_base_url: 'https://splunk.corp.example:8089',
      splunk_api_token: 'tok-1',
    })
    expect(body).toEqual({
      splunk_enabled: true,
      splunk_base_url: 'https://splunk.corp.example:8089',
      splunk_api_token: 'tok-1',
    })
  })

  it('never carries the Slack default channel (INT-02) or GitHub (configured per project)', () => {
    const loaded = initialIntegrationForm(cfg({ github_repo: 'acme/web' }))
    expect(loaded).not.toHaveProperty('slack_default_channel')
    expect(loaded).not.toHaveProperty('github_repo')
    const body = changedIntegrationFields(loaded, { ...loaded, slack_enabled: true }) as Record<string, unknown>
    expect(Object.keys(body)).toEqual(['slack_enabled'])
  })

  it('reads a null text from the server as empty, so it is not "changed" by loading', () => {
    const loaded = initialIntegrationForm(cfg({ ocp_api_url: null, jira_domain: null }))
    expect(loaded.ocp_api_url).toBe('')
    expect(changedIntegrationFields(loaded, loaded)).toEqual({})
  })
})

describe('the environment hints', () => {
  const config = cfg()
  const env = environmentFields(config)
  const initial = initialIntegrationForm(config)

  it('a text key from the environment shows the hint until the reader changes it', () => {
    expect(ENV_HINT).toBe('From the deployment environment')
    expect(showsEnvHint('splunk_base_url', env, initial, initial)).toBe(true)
    expect(showsEnvHint('splunk_base_url', env, initial, { ...initial, splunk_base_url: 'https://x.example' })).toBe(false)
    expect(showsEnvHint('jira_domain', env, initial, initial)).toBe(false)
  })

  it('a secret from the environment reads "(set in the environment)", a stored one "(set)", none nothing', () => {
    expect(secretStateLabel('ocp_sa_token', config, env)).toBe(SECRET_SET_IN_ENV)
    expect(SECRET_SET_IN_ENV).toBe('(set in the environment)')
    expect(secretStateLabel('jira_api_token', config, env)).toBe(SECRET_SET)
    expect(secretStateLabel('splunk_api_token', config, env)).toBeNull()
  })

  it('a Vault reference reads "(set — Vault)" (W20 ADM-10), even when Vault resolved nothing', () => {
    const vaulted = cfg({ vault_fields: ['splunk_api_token'], splunk_token_set: false })
    expect(secretStateLabel('splunk_api_token', vaulted, environmentFields(vaulted))).toBe(SECRET_SET_VAULT)
    expect(SECRET_SET_VAULT).toBe('(set — Vault)')
  })

  it('an older server without environment_fields shows no hint', () => {
    const old = cfg({ environment_fields: undefined })
    const none = environmentFields(old)
    expect(none.size).toBe(0)
    expect(showsEnvHint('splunk_base_url', none, initial, initial)).toBe(false)
    expect(secretStateLabel('jira_api_token', old, none)).toBe(SECRET_SET)
  })
})

describe('the copy', () => {
  it('the Slack help line names Team channels', () => {
    expect(SLACK_CHANNEL_HELP).toBe('The channel is fixed by the incoming webhook. Route other channels per team in Team channels.')
  })

  it('a field-level 422 may name any field the page edits, and never the dropped Slack channel', () => {
    expect(INTEGRATION_FORM_FIELDS).toContain('splunk_base_url')
    expect(INTEGRATION_FORM_FIELDS).toContain('jira_domain')
    expect(INTEGRATION_FORM_FIELDS).toContain('ocp_default_namespace')
    expect(INTEGRATION_FORM_FIELDS as readonly string[]).not.toContain('slack_default_channel')
  })

  it('W23d: a field-level 422 may name the four Prometheus write fields', () => {
    for (const name of ['prometheus_enabled', 'prometheus_base_url', 'prometheus_bearer_token', 'prometheus_metric_templates']) {
      expect(INTEGRATION_FORM_FIELDS as readonly string[], name).toContain(name)
    }
  })
})

describe('W23d: the Prometheus fields', () => {
  const template = { id: 'http_p95_latency', label: 'p95 latency', unit: 'seconds', promql: 'up{service="{service}"}' }
  const config = cfg({ prometheus_metric_templates: [template], prometheus_templates_source: 'default' } as Partial<IntegrationsConfigRead>)
  const initial = initialIntegrationForm(config)

  it('loads the switch, the URL (null → "") and a COPY of the templates; no token', () => {
    expect(initial.prometheus_enabled).toBe(false)
    expect(initial.prometheus_base_url).toBe('')
    expect(initial.prometheus_metric_templates).toEqual([template])
    expect(initial.prometheus_metric_templates?.[0]).not.toBe(config.prometheus_metric_templates?.[0])
    expect(initial).not.toHaveProperty('prometheus_bearer_token')
  })

  it('an older server without the Prometheus fields loads an empty, unchanged form', () => {
    const old = initialIntegrationForm(cfg())
    expect(old.prometheus_metric_templates).toEqual([])
    expect(changedIntegrationFields(old, old)).toEqual({})
  })

  it.each([
    ['untouched templates', { prometheus_metric_templates: [{ ...template }] }, {}],
    ['an edited label (the whole list is sent)', { prometheus_metric_templates: [{ ...template, label: 'p95' }] }, { prometheus_metric_templates: [{ ...template, label: 'p95' }] }],
    ['"Use the defaults"', { prometheus_metric_templates: [] }, { prometheus_metric_templates: [] }],
    ['a URL', { prometheus_base_url: 'http://10.0.4.7:9090' }, { prometheus_base_url: 'http://10.0.4.7:9090' }],
    ['a typed token', { prometheus_bearer_token: 'prom-local-test-value' }, { prometheus_bearer_token: 'prom-local-test-value' }],
    ['the switch', { prometheus_enabled: true }, { prometheus_enabled: true }],
  ] as const)('%s', (_name, edit, expected) => {
    expect(changedIntegrationFields(initial, { ...initial, ...edit } as typeof initial)).toEqual(expected)
  })

  it('the token state reads prometheus_token_set; a Vault reference reads "(set — Vault)"', () => {
    const env = environmentFields(config)
    expect(secretStateLabel('prometheus_bearer_token', config, env)).toBeNull()
    expect(secretStateLabel('prometheus_bearer_token', cfg({ prometheus_token_set: true } as Partial<IntegrationsConfigRead>), env)).toBe(SECRET_SET)
    expect(secretStateLabel('prometheus_bearer_token', cfg({ vault_fields: ['prometheus_bearer_token'] }), env)).toBe(SECRET_SET_VAULT)
  })
})
