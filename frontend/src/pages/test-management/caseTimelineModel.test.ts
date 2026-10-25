/** W13 TCL-05: the Timeline's presets, actor line, links and tones. */
import { describe, expect, it } from 'vitest'
import {
  PRESET_KINDS,
  PRESET_LABELS,
  TIMELINE_PRESET_IDS,
  actorLine,
  entryLink,
  humanStatus,
  pillTone,
} from './caseTimelineModel'

describe('caseTimelineModel', () => {
  it('mirrors the server presets; All sends no kinds', () => {
    expect(TIMELINE_PRESET_IDS.map((id) => PRESET_LABELS[id])).toEqual(['All', 'Changes', 'Reviews', 'Comments', 'Executions', 'Defects'])
    expect(PRESET_KINDS).toEqual({
      all: [],
      changes: ['version', 'requirement'],
      reviews: ['review_requested', 'review_decided'],
      comments: ['comment'],
      executions: ['plan_execution', 'automated_execution'],
      defects: ['defect'],
    })
    // Every kind the server knows is reachable through one preset.
    expect(new Set(Object.values(PRESET_KINDS).flat())).toEqual(new Set([
      'version', 'review_requested', 'review_decided', 'comment', 'plan_execution', 'automated_execution', 'defect', 'requirement',
    ]))
  })

  it('never leaves the actor blank', () => {
    expect(actorLine({ actor_name: 'Priya Shah' })).toBe('by Priya Shah')
    expect(actorLine({ actor_name: null })).toBe('by not recorded')
    expect(actorLine({ actor_name: '   ' })).toBe('by not recorded')
    expect(actorLine({ actor_name: null, actor_source: 'automation' })).toBe('by automation')
  })

  it('builds each link from ids only, encoded, and compares whole values', () => {
    expect(entryLink({ link: { type: 'run_result', run_id: 'r/1', test_case_id: 't?2' } })).toEqual({ to: '/runs/r%2F1/tests/t%3F2', label: 'Open the result' })
    expect(entryLink({ link: { type: 'run_result', run_id: 'r1' } })).toEqual({ to: '/runs/r1', label: 'Open the run' })
    expect(entryLink({ link: { type: 'run', run_id: 'r1' } })).toEqual({ to: '/runs/r1', label: 'Open the run' })
    expect(entryLink({ link: { type: 'plan', plan_id: 'p1' } })).toEqual({ to: '/test-management?tab=plans', label: 'Open the plans' })
    expect(entryLink({ link: { type: 'defect', defect_id: 'd&1', jira_key: 'javascript:alert(1)' } })).toEqual({ to: '/defects?defect=d%261', label: 'Open the defect' })
    expect(entryLink({ link: { type: 'defect' } })).toBeNull()
    expect(entryLink({ link: { type: 'elsewhere', run_id: 'r1' } })).toBeNull()
    expect(entryLink({ link: null })).toBeNull()
  })

  it('tones a review decision and words a status', () => {
    expect(pillTone({ kind: 'review_decided', status: 'approved' })).toContain('--ui-success-ink')
    expect(pillTone({ kind: 'review_decided', status: 'rejected' })).toContain('--ui-danger-ink')
    expect(pillTone({ kind: 'review_decided', status: 'changes_requested' })).toContain('--ui-warning-ink')
    expect(pillTone({ kind: 'defect', status: 'open' })).toContain('--color-text-secondary')
    expect(humanStatus('changes_requested')).toBe('Changes requested')
    expect(humanStatus('OPEN')).toBe('Open')
  })
})
