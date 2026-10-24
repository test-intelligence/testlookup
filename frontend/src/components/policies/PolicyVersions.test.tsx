/**
 * W11 GATE-02: the editor's Versions section — a closed Disclosure that reads
 * the history only once opened; one row per version, newest first; at most two
 * compared; Compare shows the changed settings; Open navigates. Every name is
 * React text.
 */
import { fireEvent, render, screen, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { DEFAULT_HARD_CAPS, DEFAULT_PASS_RATE_BANDS, DEFAULT_THRESHOLDS, DEFAULT_WEIGHTS, type PolicySummary } from '@/services/policyService'
import PolicyVersions, { VERSIONS_LOAD_ERROR, versionState } from './PolicyVersions'

const history = vi.hoisted(() => ({
  data: undefined as PolicySummary[] | undefined,
  error: undefined as unknown,
  calls: [] as unknown[][],
}))
vi.mock('@/hooks/usePolicyHistory', () => ({
  usePolicyHistory: (projectId: unknown, enabled: unknown) => {
    history.calls.push([projectId, enabled])
    return { data: history.data, error: history.error, isLoading: false }
  },
}))

const DOC = {
  schema_version: 1,
  thresholds: { ...DEFAULT_THRESHOLDS },
  dimension_weights: { ...DEFAULT_WEIGHTS },
  rules: [],
  pass_rate_bands: { ...DEFAULT_PASS_RATE_BANDS },
  hard_caps: { ...DEFAULT_HARD_CAPS },
}

function version(n: number, over: Partial<PolicySummary> = {}): PolicySummary {
  return {
    id: `pol-v${n}`, project_id: 'proj-1', version: n, name: 'Payments gate', description: null,
    rules: { ...DOC, thresholds: { ...DOC.thresholds, go_threshold: 15 + n * 5 } },
    is_active: false, is_draft: false, created_by: 'u-1', created_by_name: 'priya',
    activated_by: null, activated_by_name: null, activated_at: null,
    created_at: '2026-10-01T09:00:00Z', updated_at: null, ...over,
  }
}

const V3 = version(3)
const V4 = version(4, { is_active: true, activated_by: 'u-2', activated_by_name: 'omar', activated_at: '2026-10-02T10:00:00Z' })
const V5 = version(5, { is_draft: true, created_by_name: null })

function open(policy = V4, onOpen = vi.fn()) {
  render(<PolicyVersions policy={policy} onOpen={onOpen} />)
  fireEvent.click(screen.getByRole('button', { name: /^Versions/ }))
  return onOpen
}
const row = (n: number) => document.querySelector(`[data-policy-version="${n}"]`) as HTMLElement

beforeEach(() => {
  history.data = [V3, V5, V4]
  history.error = undefined
  history.calls.length = 0
})

describe('PolicyVersions', () => {
  it('is closed on load and reads nothing until opened', () => {
    render(<PolicyVersions policy={V4} onOpen={vi.fn()} />)
    const toggle = screen.getByRole('button', { name: /^Versions/ })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(toggle).toHaveTextContent("Every version of this policy's scope, and what changed")
    expect(history.calls).toEqual([])
    fireEvent.click(toggle)
    expect(history.calls).toEqual([['proj-1', true]])
  })

  it('lists every version newest first, with its state, author and activation', () => {
    open()
    const rows = Array.from(document.querySelectorAll('[data-policy-version]'), (el) => el.getAttribute('data-policy-version'))
    expect(rows).toEqual(['5', '4', '3'])
    expect(row(5)).toHaveTextContent('v5 · Draft')
    expect(row(5)).toHaveTextContent('created by a removed user · 2026-10-01')
    expect(row(4)).toHaveTextContent('v4 · Active')
    expect(row(4)).toHaveTextContent('created by priya · 2026-10-01')
    expect(row(4)).toHaveTextContent('activated 2026-10-02')
    expect(row(3)).toHaveTextContent('v3 · Published')
    expect(row(3)).not.toHaveTextContent('activated')
    // The open version is marked, the others open.
    expect(within(row(4)).getByText('This version')).toBeInTheDocument()
    expect(within(row(4)).queryByRole('button', { name: 'Open' })).toBeNull()
    expect([versionState(V3), versionState(V4), versionState(V5)]).toEqual(['Published', 'Active', 'Draft'])
  })

  it('Open hands the version id to the page', () => {
    const onOpen = open()
    fireEvent.click(within(row(3)).getByRole('button', { name: 'Open' }))
    expect(onOpen).toHaveBeenCalledWith('pol-v3')
  })

  it('compares at most two, oldest to newest, and shows what changed', () => {
    open()
    fireEvent.click(screen.getByRole('checkbox', { name: 'Compare v5' }))
    fireEvent.click(screen.getByRole('checkbox', { name: 'Compare v4' }))
    expect(screen.getByRole('checkbox', { name: 'Compare v3' })).toBeDisabled()
    expect(screen.queryByRole('list', { name: 'Changed settings' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Compare v4 → v5' }))
    expect(screen.getByRole('list', { name: 'Changed settings' })).toHaveTextContent('GO threshold 35 → 40')
    // Unchecking frees the third and hides the comparison.
    fireEvent.click(screen.getByRole('checkbox', { name: 'Compare v5' }))
    expect(screen.getByRole('checkbox', { name: 'Compare v3' })).toBeEnabled()
    expect(screen.queryByRole('button', { name: /^Compare v/ })).toBeNull()
  })

  it('two equal versions read "No settings differ."', () => {
    history.data = [V3, { ...V4, rules: V3.rules }]
    open()
    fireEvent.click(screen.getByRole('checkbox', { name: 'Compare v3' }))
    fireEvent.click(screen.getByRole('checkbox', { name: 'Compare v4' }))
    fireEvent.click(screen.getByRole('button', { name: 'Compare v3 → v4' }))
    expect(screen.getByText('No settings differ.')).toBeInTheDocument()
  })

  it('the system default reads the system history', () => {
    history.data = [version(2, { project_id: null })]
    open(version(2, { project_id: null }))
    expect(history.calls).toEqual([[null, true]])
  })

  it('a failed read says so', () => {
    history.data = undefined
    history.error = new Error('500')
    open()
    expect(screen.getByText(VERSIONS_LOAD_ERROR)).toBeInTheDocument()
  })

  it('a hostile policy name renders as text', () => {
    history.data = [version(1, { name: '<img src=x onerror=alert(1)>' })]
    open(version(1))
    expect(row(1).querySelector('img')).toBeNull()
    expect(row(1)).toHaveTextContent('<img src=x onerror=alert(1)>')
  })
})
