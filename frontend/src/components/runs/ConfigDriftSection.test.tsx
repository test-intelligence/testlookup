/**
 * W23b FAIL-13 (contract §4.4 G-D): the run page's "Configuration drift" card
 * and its model. Absence is not health: a missing context reads "Not recorded
 * by this reporter…", and "No configuration drift" is said only for two
 * recorded contexts with nothing different.
 */
import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import type { ConfigDrift, DriftEntry } from '@/types/configDrift'
import { DRIFT_SENTENCES } from '@/types/configDrift'
import { drift, driftEntry } from '@/test/bisectDriftMocks'
import {
  againstLine, cappedLine, contextSentence, driftSummary, entryLine, groupEntries, noDriftLine, onlyRunFieldsLine,
  sectionLabel,
} from './configDriftModel'
import ConfigDriftSection from './ConfigDriftSection'

const ACCEPTANCE: DriftEntry[] = [
  driftEntry('dependencies', 'poetry.lock', 'changed', 'oldhashsenti', 'newhashsenti'),
  driftEntry('runtime', 'python', 'changed', '3.11.8', '3.11.9'),
]

function d(over: Partial<ConfigDrift> = {}): ConfigDrift {
  return drift({
    baseline_run_id: 'run-base', baseline_build_number: '41', context_state: 'recorded',
    entries: ACCEPTANCE, entries_total: ACCEPTANCE.length,
    ...over,
  })
}

function renderCard(value: ConfigDrift) {
  render(<ConfigDriftSection drift={value} />)
  return screen.getByRole('region', { name: 'Configuration drift' })
}

describe('configDriftModel — the copy, pure', () => {
  it('the acceptance summary: "Configuration drift: poetry.lock changed · python 3.11.8 → 3.11.9"', () => {
    expect(driftSummary(d())).toBe('Configuration drift: poetry.lock changed · python 3.11.8 → 3.11.9')
  })

  it('entry lines: changed / added / removed, dependencies by name only', () => {
    expect(entryLine(driftEntry('dependencies', 'uv.lock', 'added', null, 'addhashsenti'))).toBe('uv.lock added')
    expect(entryLine(driftEntry('dependencies', 'yarn.lock', 'removed', 'gonehashsent', null))).toBe('yarn.lock removed')
    expect(entryLine(driftEntry('runtime', 'pytest', 'added', null, '8.3.2'))).toBe('pytest 8.3.2 (added)')
    expect(entryLine(driftEntry('runtime', 'java', 'removed', '21', null))).toBe('java 21 (removed)')
    expect(entryLine(driftEntry('run', 'branch', 'changed', 'main', 'feature/pay'))).toBe('branch main → feature/pay')
    expect(entryLine(driftEntry('environment', 'environment', 'changed', 'staging', 'prod'))).toBe('environment staging → prod')
    expect(entryLine(driftEntry('env', 'TZ', 'changed', 'UTC', 'Europe/Berlin'))).toBe('TZ UTC → Europe/Berlin')
  })

  it('the summary names three entries, then "and n more" counted against entries_total', () => {
    const many = [
      ...ACCEPTANCE,
      driftEntry('env', 'TZ', 'changed', 'UTC', 'Europe/Sentinel'),
      driftEntry('run', 'branch', 'changed', 'main', 'feature/pay'),
    ]
    expect(driftSummary(d({ entries: many, entries_total: 9 }))).toBe(
      'Configuration drift: poetry.lock changed · python 3.11.8 → 3.11.9 · TZ UTC → Europe/Sentinel · and 6 more',
    )
    expect(driftSummary(d({ entries: [], entries_total: 0 }))).toBeNull()
  })

  it('groups follow DRIFT_SECTIONS order whatever the entry order; empty sections are omitted', () => {
    const shuffled = [
      driftEntry('run', 'branch', 'changed', 'main', 'dev'),
      driftEntry('env', 'TZ', 'added', null, 'UTC'),
      driftEntry('dependencies', 'poetry.lock', 'changed', 'oldsentinel', 'newsentinel'),
    ]
    expect(groupEntries(d({ entries: shuffled })).map((g) => g.label)).toEqual(['Dependencies', 'Environment variables', 'Run'])
    expect(['dependencies', 'runtime', 'images', 'environment', 'env', 'run'].map(sectionLabel)).toEqual([
      'Dependencies', 'Runtime', 'Images', 'Environment', 'Environment variables', 'Run',
    ])
  })

  it('context sentences: run / baseline / both missing; recorded has none', () => {
    expect(contextSentence(d({ context_state: 'run_missing' }))).toBe(DRIFT_SENTENCES.run_missing)
    expect(contextSentence(d({ context_state: 'baseline_missing' }))).toBe(
      'Not recorded for the baseline build #41: its reporter sent no runtime or dependency context.',
    )
    expect(contextSentence(d({ context_state: 'both_missing' }))).toBe(
      'Not recorded by this reporter: neither this run nor build #41 sent a runtime or dependency context.',
    )
    expect(contextSentence(d())).toBeNull()
    expect(againstLine(d())).toBe('Against build #41, the baseline above')
  })

  it('"No configuration drift" ONLY for recorded with zero entries', () => {
    expect(noDriftLine(d({ entries: [], entries_total: 0 }))).toBe('No configuration drift against build #41.')
    for (const state of ['run_missing', 'baseline_missing', 'both_missing'] as const) {
      expect(noDriftLine(d({ context_state: state, entries: [], entries_total: 0 }))).toBeNull()
    }
    expect(noDriftLine(d())).toBeNull()
  })

  it('only_run_fields under the run-field entries when a context is missing; the cap line', () => {
    const branch = [driftEntry('run', 'branch', 'changed', 'main', 'feature/pay')]
    expect(onlyRunFieldsLine(d({ context_state: 'both_missing', entries: branch, entries_total: 1 }))).toBe(DRIFT_SENTENCES.only_run_fields)
    expect(onlyRunFieldsLine(d())).toBeNull()
    expect(cappedLine(d({ entries_total: 75, entries: Array.from({ length: 60 }, (_, i) => driftEntry('env', `K${i}`, 'added', null, 'v')) }))).toBe('Showing 60 of 75')
    expect(cappedLine(d())).toBeNull()
  })
})

describe('ConfigDriftSection — the card', () => {
  it('recorded: the title, the baseline it was measured against, the summary and the grouped entries', () => {
    const card = renderCard(d({
      entries: [...ACCEPTANCE, driftEntry('env', 'TZ', 'changed', 'UTC', 'Europe/Sentinel'), driftEntry('run', 'branch', 'changed', 'main', 'feature/pay')],
      entries_total: 4,
    }))
    expect(within(card).getByRole('heading', { level: 2, name: 'Configuration drift' })).toBeInTheDocument()
    expect(card).toHaveTextContent('Against build #41, the baseline above')
    expect(card.querySelector('[data-config-drift-summary]')).toHaveTextContent(
      'Configuration drift: poetry.lock changed · python 3.11.8 → 3.11.9 · TZ UTC → Europe/Sentinel · and 1 more',
    )
    const sections = Array.from(card.querySelectorAll('[data-config-drift-section]'), (s) => s.getAttribute('data-config-drift-section'))
    expect(sections).toEqual(['dependencies', 'runtime', 'env', 'run'])
    const run = card.querySelector('[data-config-drift-section="run"]') as HTMLElement
    expect(within(run).getByText('Run')).toBeInTheDocument()
    expect(within(run).getByRole('listitem')).toHaveTextContent('branch main → feature/pay')
    expect(card.querySelector('[data-config-drift-context]')).toBeNull()
    expect(card).not.toHaveTextContent(/No configuration drift/)
  })

  it('run_missing: "Not recorded by this reporter…", NEVER "No configuration drift", the branch entry still listed', () => {
    const card = renderCard(d({
      context_state: 'run_missing',
      entries: [driftEntry('run', 'branch', 'changed', 'main', 'feature/pay')],
      entries_total: 1,
    }))
    expect(card.querySelector('[data-config-drift-context]')).toHaveTextContent(
      'Not recorded by this reporter: this run sent no runtime or dependency context.',
    )
    expect(card).not.toHaveTextContent(/No configuration drift/)
    expect(card.querySelector('[data-config-drift-section="run"]')).toHaveTextContent('branch main → feature/pay')
    expect(card.querySelector('[data-config-drift-only-run]')).toHaveTextContent(DRIFT_SENTENCES.only_run_fields)
  })

  it('run_missing with nothing else different: still never "No configuration drift"', () => {
    const card = renderCard(d({ context_state: 'run_missing', entries: [], entries_total: 0 }))
    expect(card).toHaveTextContent(DRIFT_SENTENCES.run_missing)
    expect(card).not.toHaveTextContent(/No configuration drift/)
    expect(card.querySelector('[data-config-drift-only-run]')).toBeNull()
  })

  it('both_missing with a branch entry: the entry AND only_run_fields', () => {
    const card = renderCard(d({
      context_state: 'both_missing',
      entries: [driftEntry('run', 'branch', 'changed', 'main', 'feature/pay')],
      entries_total: 1,
    }))
    expect(card.querySelector('[data-config-drift-context]')).toHaveTextContent('neither this run nor build #41')
    expect(card.querySelector('[data-config-drift-entry]')).toHaveTextContent('branch main → feature/pay')
    expect(card.querySelector('[data-config-drift-only-run]')).toHaveTextContent('Runtime and dependency drift: not recorded by this reporter.')
  })

  it('recorded with zero entries: the no-drift sentence and nothing else', () => {
    const card = renderCard(d({ entries: [], entries_total: 0 }))
    expect(card.querySelector('[data-config-drift-none]')).toHaveTextContent('No configuration drift against build #41.')
    expect(card.querySelector('[data-config-drift-summary]')).toBeNull()
    expect(card.querySelectorAll('[data-config-drift-section]')).toHaveLength(0)
  })

  it('capped: "Showing 60 of 75"', () => {
    const entries = Array.from({ length: 60 }, (_, i) => driftEntry('env', `KEY_${String(i).padStart(2, '0')}`, 'added', null, 'v'))
    const card = renderCard(d({ entries, entries_total: 75 }))
    expect(card.querySelector('[data-config-drift-capped]')).toHaveTextContent('Showing 60 of 75')
    expect(card.querySelectorAll('[data-config-drift-entry]')).toHaveLength(60)
  })

  it('a <script> env value renders as text, never as markup', () => {
    const value = '<script>window.__driftSentinel=1</script>'
    const card = renderCard(d({ entries: [driftEntry('env', 'BUILD_IMAGE', 'added', null, value)], entries_total: 1 }))
    expect(card.querySelector('[data-config-drift-entry]')).toHaveTextContent(`BUILD_IMAGE ${value} (added)`)
    expect(card.querySelector('script')).toBeNull()
    expect((window as unknown as Record<string, unknown>).__driftSentinel).toBeUndefined()
  })
})
