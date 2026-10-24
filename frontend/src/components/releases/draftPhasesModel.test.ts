/**
 * W11 REL-07: the PRD draft form's model — the badge, the editable rows, the
 * consecutive windows from "First phase starts", what blocks Create, and the
 * ONE bulk request Create sends.
 */
import { describe, expect, it } from 'vitest'

import pageSource from '@/pages/ReleasesPage.tsx?raw'
import {
  bulkBody,
  createLabel,
  createdToast,
  draftBadge,
  emptyRow,
  FALLBACK_REASON_TEXT,
  PHASE_TYPE_OPTIONS,
  rowsFromDraft,
  scheduleRows,
  validateRows,
  type DraftRow,
} from './draftPhasesModel'

function row(name: string, days = '', phaseType = 'qa_testing', description = ''): DraftRow {
  return { ...emptyRow(), name, days, phaseType, description }
}

describe('PHASE_TYPE_OPTIONS', () => {
  it('is the New-phase form\'s vocabulary (ReleasesPage PHASE_TYPES), value for value and label for label', () => {
    const block = pageSource.slice(pageSource.indexOf('const PHASE_TYPES = ['), pageSource.indexOf(']', pageSource.indexOf('const PHASE_TYPES = [')))
    const fromPage = [...block.matchAll(/value: '([a-z_]+)',\s*label: '([^']+)'/g)].map(m => [m[1], m[2]])
    expect(PHASE_TYPE_OPTIONS.map(o => [o.value, o.label])).toEqual(fromPage)
    expect(fromPage.map(([v]) => v)).toEqual(['planning', 'development', 'code_freeze', 'qa_testing', 'uat', 'staging', 'production'])
  })
})

describe('draftBadge', () => {
  it('names the model for a model draft, and the reason for a rules draft', () => {
    expect(draftBadge({ source: 'llm', model_name: 'qwen2.5:7b', fallback_reason: null })).toBe('Drafted by qwen2.5:7b')
    expect(draftBadge({ source: 'llm', model_name: null })).toBe('Drafted by the AI model')
    expect(draftBadge({ source: 'rules', fallback_reason: null })).toBe('Drafted by rules')
    expect(draftBadge({ source: 'rules', fallback_reason: 'something-new' })).toBe('Drafted by rules')
    for (const [reason, text] of Object.entries(FALLBACK_REASON_TEXT)) {
      expect(draftBadge({ source: 'rules', fallback_reason: reason })).toBe(`Drafted by rules — ${text}`)
    }
    expect(Object.keys(FALLBACK_REASON_TEXT)).toEqual([
      'offline_mode', 'rules_mode', 'llm_unavailable', 'llm_timeout', 'llm_error', 'llm_unparsable', 'llm_empty',
    ])
  })
})

describe('rowsFromDraft', () => {
  it('makes editable rows; the exit criteria join the description; an unknown type is QA testing', () => {
    const rows = rowsFromDraft([
      { name: 'Smoke testing', phase_type: 'qa_testing', description: 'Run the smoke pack.', exit_criteria_text: 'all green', planned_days: 2 },
      { name: 'UAT', phase_type: 'uat', description: null, exit_criteria_text: null, planned_days: null },
      { name: 'Soak', phase_type: 'soak' },
    ])
    expect(rows.map(r => [r.name, r.phaseType, r.days, r.description])).toEqual([
      ['Smoke testing', 'qa_testing', '2', 'Run the smoke pack.\nExit criteria: all green'],
      ['UAT', 'uat', '', ''],
      ['Soak', 'qa_testing', '', ''],
    ])
    expect(new Set(rows.map(r => r.key)).size).toBe(3)
  })
})

describe('scheduleRows', () => {
  it('lays consecutive windows from the first start; a row without Days counts 5', () => {
    expect(scheduleRows([row('a', '3'), row('b'), row('c', '1')], '2026-11-03')).toEqual([
      { planned_start: '2026-11-03T00:00:00Z', planned_end: '2026-11-05T00:00:00Z' },
      { planned_start: '2026-11-06T00:00:00Z', planned_end: '2026-11-10T00:00:00Z' },
      { planned_start: '2026-11-11T00:00:00Z', planned_end: '2026-11-11T00:00:00Z' },
    ])
  })
  it('no first start, no dates', () => {
    expect(scheduleRows([row('a', '3')], null)).toEqual([{ planned_start: null, planned_end: null }])
    expect(scheduleRows([row('a', '3')], 'soon')).toEqual([{ planned_start: null, planned_end: null }])
  })
})

describe('validateRows', () => {
  it('blocks an empty name, a repeated name (any case), more than 30 rows, bad days, and nothing', () => {
    expect(validateRows([row('Smoke'), row('UAT')])).toBeNull()
    expect(validateRows([row('Smoke'), row('  ')])).toBe('Every phase needs a name.')
    expect(validateRows([row('Smoke'), row(' smoke ')])).toBe('Two phases are named "smoke": each name must be different.')
    expect(validateRows(Array.from({ length: 31 }, (_, i) => row(`P${i}`)))).toBe('At most 30 phases can be created at once.')
    expect(validateRows([row('Smoke', '91')])).toBe('Days must be a whole number from 1 to 90.')
    expect(validateRows([row('Smoke', '2.5')])).toBe('Days must be a whole number from 1 to 90.')
    expect(validateRows([])).toBe('Add at least one phase.')
  })
})

describe('bulkBody', () => {
  it('is the edited rows with their windows, and who drafted them', () => {
    const rows = [row(' Smoke ', '2', 'qa_testing', 'Exit criteria: green'), row('UAT', '', 'uat')]
    expect(bulkBody(rows, { source: 'llm', model_name: 'qwen2.5:7b' }, '2026-11-03')).toEqual({
      phases: [
        { name: 'Smoke', phase_type: 'qa_testing', description: 'Exit criteria: green',
          planned_start: '2026-11-03T00:00:00Z', planned_end: '2026-11-04T00:00:00Z' },
        { name: 'UAT', phase_type: 'uat', description: null,
          planned_start: '2026-11-05T00:00:00Z', planned_end: '2026-11-09T00:00:00Z' },
      ],
      source: 'llm',
      model_name: 'qwen2.5:7b',
    })
    const rules = bulkBody(rows, { source: 'rules', model_name: 'ignored' })
    expect(rules.source).toBe('rules')
    expect(rules.model_name).toBeNull()
    expect(rules.phases[0].planned_start).toBeNull()
  })
})

describe('the copy', () => {
  it('Create N phases and its toast', () => {
    expect(createLabel(3)).toBe('Create 3 phases')
    expect(createLabel(1)).toBe('Create 1 phase')
    expect(createdToast(3)).toBe('Created 3 phases from the draft.')
  })
})
