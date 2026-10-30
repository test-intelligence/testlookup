/**
 * W23d AGT-15 — Settings › Integrations › the Prometheus card, through the
 * page's ONE form and ONE Save (contract §4.4 G-D): only the changed
 * `prometheus_*` keys are sent; a 422 on the templates reads as its sentence
 * under the list; `Use the defaults` sends `[]`; `Test connection` POSTs the
 * existing probe with `provider=prometheus` and shows the answer inline; a QA
 * lead sees disabled inputs and can still test.
 */
import { createElement } from 'react'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { IntegrationsConfigRead } from '@/services/appSettingsService'
import { TEMPLATE_ERRORS } from '@/types/prometheus'
import {
  DEFAULTS_AFTER_SAVE,
  DEFAULTS_IN_USE,
  PROMETHEUS_NOT_CONFIGURED,
  PROMETHEUS_TOKEN_LABEL,
  PROMETHEUS_URL_LABEL,
  TEST_FAILED,
  probeLine,
} from './integrationsForm'

const mockGet = vi.fn()
const mockUpdate = vi.fn()
const mockProbe = vi.fn()

vi.mock('@/services/appSettingsService', () => ({
  appSettingsService: {
    getIntegrationsConfig: (...a: unknown[]) => mockGet(...a),
    updateIntegrationsConfig: (...a: unknown[]) => mockUpdate(...a),
  },
}))
vi.mock('@/services/integrationHealthService', () => ({
  triggerProbe: (...a: unknown[]) => mockProbe(...a),
}))
const perms = vi.hoisted(() => ({ isAdmin: true }))
vi.mock('@/hooks/usePermissions', () => ({ usePermissions: () => ({ isAdmin: perms.isAdmin }) }))
const toastMock = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }))
vi.mock('react-hot-toast', () => ({ default: toastMock }))
vi.mock('@/components/ui/PageHeader', () => ({ default: ({ title }: { title: string }) => createElement('h1', null, title) }))
vi.mock('@/components/ui/LoadingSpinner', () => ({ default: () => createElement('div', null, 'loading') }))
vi.mock('react-router-dom', () => ({
  Link: ({ children, to }: { children: React.ReactNode; to: string }) => createElement('a', { href: to }, children),
}))

const DEFAULTS = [
  { id: 'http_p95_latency', label: 'p95 latency', unit: 'seconds', promql: 'histogram_quantile(0.95, sum by (le) (rate(http_server_requests_seconds_bucket{service="{service}"}[1m])))' },
  { id: 'http_5xx_rate', label: '5xx error rate', unit: 'per_second', promql: 'sum(rate(http_server_requests_seconds_count{service="{service}",status=~"5.."}[1m]))' },
  { id: 'memory_working_set', label: 'Memory working set', unit: 'bytes', promql: 'sum(container_memory_working_set_bytes{container="{service}"})' },
  { id: 'cpu_usage', label: 'CPU usage', unit: 'ratio', promql: 'sum(rate(container_cpu_usage_seconds_total{container="{service}"}[1m]))' },
]

function config(over: Partial<IntegrationsConfigRead> = {}): IntegrationsConfigRead {
  return {
    jira_enabled: false, jira_domain: null, jira_email: null, jira_token_set: false, jira_default_project_key: '',
    splunk_enabled: false, splunk_base_url: null, splunk_token_set: false,
    ocp_enabled: false, ocp_api_url: null, ocp_token_set: false, ocp_default_namespace: '',
    slack_enabled: false, slack_webhook_url: null, slack_webhook_set: false, slack_default_channel: '',
    teams_enabled: false, teams_webhook_url: null, teams_webhook_set: false,
    github_repo: null, github_token_set: false,
    prometheus_enabled: false,
    prometheus_base_url: null,
    prometheus_token_set: false,
    prometheus_metric_templates: DEFAULTS.map(t => ({ ...t })),
    prometheus_templates_source: 'default',
    ...over,
  } as IntegrationsConfigRead
}

async function renderPage() {
  const { default: IntegrationsPage } = await import('./IntegrationsPage')
  await act(async () => { render(createElement(IntegrationsPage)) })
  return (await screen.findByRole('heading', { level: 3, name: 'Prometheus' })).closest('[data-prometheus-card]') as HTMLElement
}

const save = () => fireEvent.click(screen.getByRole('button', { name: /save integrations/i }))
const templateRows = (card: HTMLElement) => card.querySelectorAll('[data-prometheus-template]')

beforeEach(() => {
  vi.clearAllMocks()
  perms.isAdmin = true
})

describe('PrometheusCard', () => {
  it('a full-row card: not configured, the defaults in use, four template rows, the saved-settings note', async () => {
    mockGet.mockResolvedValue(config())
    const card = await renderPage()
    expect(card.className).toContain('xl:col-span-2')
    expect(within(card).getByText(PROMETHEUS_NOT_CONFIGURED)).toBeInTheDocument()
    expect(within(card).getByText(DEFAULTS_IN_USE)).toBeInTheDocument()
    expect(templateRows(card)).toHaveLength(4)
    expect(within(card).getByLabelText('Template 1 id')).toHaveValue('http_p95_latency')
    expect(within(card).getByLabelText('Template 1 PromQL')).toHaveValue(DEFAULTS[0].promql)
    expect(within(card).getByText('Tests the saved settings — save first.')).toBeInTheDocument()
  })

  it('the unit select names "per second" in words; the value stays the server\'s per_second', async () => {
    mockGet.mockResolvedValue(config())
    const card = await renderPage()
    const unit = within(card).getByLabelText('Template 2 unit') as HTMLSelectElement
    expect(unit).toHaveValue('per_second')
    expect(unit.selectedOptions[0].textContent).toBe('per second')
    expect(Array.from(unit.options, o => o.textContent)).toEqual(['seconds', 'bytes', 'ratio', 'per second', 'count'])
  })

  it('an admin edits the URL, the token and a label: Save sends ONLY those prometheus_* keys', async () => {
    mockGet.mockResolvedValue(config())
    mockUpdate.mockResolvedValue(config({ prometheus_base_url: 'http://10.0.4.7:9090', prometheus_token_set: true }))
    const card = await renderPage()
    fireEvent.change(within(card).getByLabelText(PROMETHEUS_URL_LABEL), { target: { value: 'http://10.0.4.7:9090' } })
    fireEvent.change(within(card).getByLabelText(new RegExp(`^${PROMETHEUS_TOKEN_LABEL}`)), { target: { value: 'prom-local-test-value' } })
    fireEvent.change(within(card).getByLabelText('Template 1 label'), { target: { value: 'p95 latency (checkout)' } })
    expect(within(card).queryByText(DEFAULTS_IN_USE)).toBeNull()
    save()
    await waitFor(() => expect(mockUpdate).toHaveBeenCalledTimes(1))
    const [body, opts] = mockUpdate.mock.calls[0] as [Record<string, unknown>, unknown]
    expect(opts).toEqual({ suppressToast: true })
    expect(Object.keys(body).sort()).toEqual(['prometheus_base_url', 'prometheus_bearer_token', 'prometheus_metric_templates'])
    expect(body.prometheus_base_url).toBe('http://10.0.4.7:9090')
    expect(body.prometheus_bearer_token).toBe('prom-local-test-value')
    expect(body.prometheus_metric_templates).toEqual([{ ...DEFAULTS[0], label: 'p95 latency (checkout)' }, ...DEFAULTS.slice(1)])
  })

  it('a 422 on prometheus_metric_templates reads as its sentence under the list', async () => {
    mockGet.mockResolvedValue(config())
    mockUpdate.mockRejectedValue(Object.assign(new Error('Request failed with status code 422'), {
      response: { status: 422, data: { detail: [{ loc: ['body', 'prometheus_metric_templates', 0, 'promql'], msg: `Value error, ${TEMPLATE_ERRORS.placeholder}`, type: 'value_error' }] } },
    }))
    const card = await renderPage()
    fireEvent.change(within(card).getByLabelText('Template 1 PromQL'), { target: { value: 'up' } })
    save()
    const alert = await within(card).findByRole('alert')
    expect(alert).toHaveTextContent(TEMPLATE_ERRORS.placeholder)
    expect(alert.getAttribute('data-field-error')).toBe('prometheus_metric_templates')
    expect(card.querySelector('[data-prometheus-templates]')?.contains(alert)).toBe(true)
    expect(toastMock.error).not.toHaveBeenCalled()
    expect(screen.queryByText(/status code/)).toBeNull()
  })

  it('Use the defaults empties the list and Save sends []', async () => {
    mockGet.mockResolvedValue(config({ prometheus_templates_source: 'saved' }))
    mockUpdate.mockResolvedValue(config())
    const card = await renderPage()
    fireEvent.click(within(card).getByRole('button', { name: 'Use the defaults' }))
    expect(templateRows(card)).toHaveLength(0)
    expect(within(card).getByText(DEFAULTS_AFTER_SAVE)).toBeInTheDocument()
    save()
    await waitFor(() => expect(mockUpdate).toHaveBeenCalledTimes(1))
    expect(mockUpdate.mock.calls[0][0]).toEqual({ prometheus_metric_templates: [] })
  })

  it('Add template appends an empty row; Remove drops one', async () => {
    mockGet.mockResolvedValue(config())
    const card = await renderPage()
    fireEvent.click(within(card).getByRole('button', { name: 'Add template' }))
    expect(templateRows(card)).toHaveLength(5)
    fireEvent.click(within(card).getByRole('button', { name: 'Remove template 5' }))
    fireEvent.click(within(card).getByRole('button', { name: 'Remove template 1' }))
    expect(templateRows(card)).toHaveLength(3)
    expect(within(card).getByLabelText('Template 1 id')).toHaveValue('http_5xx_rate')
  })

  it('Test connection POSTs the probe with provider=prometheus and shows "Test connection: healthy (42 ms)"', async () => {
    mockGet.mockResolvedValue(config({ prometheus_enabled: true, prometheus_base_url: 'http://10.0.4.7:9090' }))
    mockProbe.mockResolvedValue([{ provider: 'prometheus', status: 'healthy', response_ms: 42, message: 'Prometheus API reachable', auth_valid: true, payload_valid: null }])
    const card = await renderPage()
    expect(within(card).queryByText(PROMETHEUS_NOT_CONFIGURED)).toBeNull()
    fireEvent.click(within(card).getByRole('button', { name: 'Test connection' }))
    expect(await within(card).findByText('Test connection: healthy (42 ms)')).toBeInTheDocument()
    expect(mockProbe).toHaveBeenCalledWith('prometheus')
    expect(mockUpdate).not.toHaveBeenCalled()
  })

  it('a skipped probe carries its own sentence; a refused probe reads as its sentence, never the status code', async () => {
    mockGet.mockResolvedValue(config())
    mockProbe.mockResolvedValueOnce([{ provider: 'prometheus', status: 'skipped', response_ms: 0, message: 'Prometheus is switched off or has no URL', auth_valid: null, payload_valid: null }])
    const card = await renderPage()
    fireEvent.click(within(card).getByRole('button', { name: 'Test connection' }))
    expect(await within(card).findByText('Test connection: skipped — Prometheus is switched off or has no URL')).toBeInTheDocument()

    mockProbe.mockRejectedValueOnce(Object.assign(new Error('Request failed with status code 403'), { response: { status: 403, data: { detail: 'Requires QA lead role or higher' } } }))
    fireEvent.click(within(card).getByRole('button', { name: 'Test connection' }))
    expect(await within(card).findByText(`${TEST_FAILED} Requires QA lead role or higher`)).toBeInTheDocument()
    expect(within(card).queryByText(/status code/)).toBeNull()
  })

  it('a QA lead sees every card input disabled, no Save, and can still test', async () => {
    perms.isAdmin = false
    mockGet.mockResolvedValue(config({ prometheus_token_set: true }))
    mockProbe.mockResolvedValue([{ provider: 'prometheus', status: 'healthy', response_ms: 42, message: 'ok', auth_valid: true, payload_valid: null }])
    const card = await renderPage()
    expect(screen.queryByRole('button', { name: /save integrations/i })).toBeNull()
    for (const el of Array.from(card.querySelectorAll('input, textarea, select'))) expect(el).toBeDisabled()
    expect(within(card).getByRole('button', { name: 'Add template' })).toBeDisabled()
    expect(card.querySelector('[data-secret-state="prometheus_bearer_token"]')?.textContent).toBe('(set)')
    const test = within(card).getByRole('button', { name: 'Test connection' })
    expect(test).toBeEnabled()
    fireEvent.click(test)
    expect(await within(card).findByText('Test connection: healthy (42 ms)')).toBeInTheDocument()
  })

  it('probeLine: healthy with its time; other statuses with their sentence', () => {
    expect(probeLine({ status: 'healthy', response_ms: 41.6 })).toBe('Test connection: healthy (42 ms)')
    expect(probeLine({ status: 'auth_error', message: 'Prometheus refused the token (HTTP 401)' })).toBe('Test connection: auth_error — Prometheus refused the token (HTTP 401)')
    expect(probeLine({ status: 'timeout', message: '' })).toBe('Test connection: timeout')
  })
})
