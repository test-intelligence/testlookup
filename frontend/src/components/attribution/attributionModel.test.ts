import { describe, expect, it } from 'vitest'
import { ATTRIBUTION_FIELD_FALLBACK, PREVIEW_LANDINGS, type AttributionPreviewResponse } from '@/types/attributionRules'
import {
  CATCH_ALL,
  CREATED_ON_FIRST_MATCH,
  FOOTER_WITHOUT_PREVIEW,
  LADDER_STEPS,
  NO_RUNS_YET,
  effectiveFields,
  emptyForm,
  fieldLabel,
  formFromRule,
  landingText,
  matchesText,
  previewSentences,
  ruleBody,
  tagsText,
  targetNote,
  unattributedFooter,
  validateRule,
  type RuleForm,
} from './attributionModel'

const FIELDS = ATTRIBUTION_FIELD_FALLBACK

function preview(over: Partial<AttributionPreviewResponse> = {}): AttributionPreviewResponse {
  return {
    considered: 200,
    matched: 12,
    field_present: 183,
    sample: [],
    shadowed: 0,
    explicit: 0,
    target_exists: true,
    unattributed_recent: 0,
    ...over,
  }
}

const form = (over: Partial<RuleForm> = {}): RuleForm => ({
  name: 'Hotfix branches',
  match_field: 'branch',
  match_pattern: 'hotfix/*',
  target_release_name: '2.4.1',
  priority: '10',
  is_enabled: true,
  ...over,
})

describe('the field vocabulary', () => {
  it('the fallback is the server vocabulary in declaration order (backend/tests/test_attribution_field_vocab.py pins the text)', () => {
    expect(FIELDS.map((f) => f.value)).toEqual(['branch', 'build_number', 'environment', 'tag'])
    expect(FIELDS.map((f) => f.glob)).toEqual([true, true, false, true])
    expect(FIELDS.find((f) => f.value === 'environment')?.help).toBe(
      "The run's environment, matched exactly (case does not matter), e.g. staging.",
    )
  })

  it('uses the served fields when there are any, else the fallback', () => {
    const served = [{ value: 'branch', label: 'Git branch', glob: true, help: 'x' }]
    expect(effectiveFields(served)).toBe(served)
    expect(effectiveFields([])).toBe(FIELDS)
    expect(effectiveFields(undefined)).toBe(FIELDS)
  })

  it('labels a field, and an unknown one as itself', () => {
    expect(fieldLabel('build_number', FIELDS)).toBe('Build number')
    expect(fieldLabel('commit', FIELDS)).toBe('commit')
    expect(matchesText({ match_field: 'branch', match_pattern: 'release/2.5*' }, FIELDS)).toBe('Branch release/2.5*')
  })

  it('names the landings the server answers', () => {
    expect(PREVIEW_LANDINGS).toEqual(['rule', 'earlier_rule', 'explicit'])
    expect(PREVIEW_LANDINGS.map(landingText)).toEqual(['here', 'earlier rule', 'stays (named)'])
    expect(landingText(undefined)).toBe('here')
  })

  it('states the ladder in the server order', () => {
    expect(LADDER_STEPS).toEqual([
      'The release the run names',
      'A GitHub milestone on its pull request',
      'These rules, top to bottom',
      'A release whose cutoff window holds the run',
      'The release that was active when it ran',
    ])
  })
})

describe('previewSentences', () => {
  it('says how many match, out of how many, and how many carry the field', () => {
    expect(previewSentences(preview(), 'Branch', '2.6')).toEqual(['Matches 12 of the last 200 runs (183 carry a branch).'])
    expect(previewSentences(preview({ field_present: 1 }), 'Environment', '2.6')[0]).toBe(
      'Matches 12 of the last 200 runs (1 carries an environment).',
    )
  })

  it('adds the shadowed, named, missing-target and catch-all sentences when they apply', () => {
    const out = previewSentences(
      preview({ matched: 200, shadowed: 3, explicit: 2, target_exists: false }),
      'Branch',
      ' 2.6 ',
    )
    expect(out).toEqual([
      'Matches 200 of the last 200 runs (183 carry a branch).',
      '3 of them already match an earlier rule, which wins.',
      '2 named their release outright; a rule does not move them.',
      'No release named "2.6" yet: the first matching run creates it.',
      CATCH_ALL,
    ])
  })

  it('speaks of one run in the singular', () => {
    const out = previewSentences(preview({ shadowed: 1, explicit: 1 }), 'Tag', '2.6')
    expect(out).toContain('1 of them already matches an earlier rule, which wins.')
    expect(out).toContain('1 named its release outright; a rule does not move it.')
  })

  it('reads a missing detail field as its default: nothing to say', () => {
    const old: AttributionPreviewResponse = { considered: 10, matched: 2, field_present: 9, sample: [] }
    expect(previewSentences(old, 'Branch', '2.6')).toEqual(['Matches 2 of the last 10 runs (9 carry a branch).'])
  })

  it('a project with no runs gets no fake ratio', () => {
    expect(previewSentences(preview({ considered: 0, matched: 0, field_present: 0 }), 'Branch', 'x')).toEqual([NO_RUNS_YET])
  })
})

describe('targetNote', () => {
  const releases = [{ name: '2.5.0' }, { name: 'Release 2.6' }]
  it('says the first match creates a release no release carries', () => {
    expect(targetNote('2.7', releases)).toBe(CREATED_ON_FIRST_MATCH)
  })
  it('matches like the linker: trimmed, case-insensitive', () => {
    expect(targetNote(' release 2.6 ', releases)).toBeNull()
  })
  it('claims nothing while the list is unknown', () => {
    expect(targetNote('2.7', undefined)).toBeNull()
  })
})

describe('unattributedFooter', () => {
  it('without a preview points at the Runs filter', () => {
    expect(unattributedFooter(null)).toEqual({ text: FOOTER_WITHOUT_PREVIEW, count: 0 })
    expect(unattributedFooter({ considered: 1, matched: 0, field_present: 0, sample: [] }).text).toBe(FOOTER_WITHOUT_PREVIEW)
  })
  it('after one counts the runs with no release', () => {
    expect(unattributedFooter(preview({ unattributed_recent: 4 }))).toEqual({
      text: '4 of the last 200 runs have no release (Unattributed).',
      count: 4,
    })
    expect(unattributedFooter(preview({ unattributed_recent: 1 })).text).toBe(
      '1 of the last 200 runs has no release (Unattributed).',
    )
  })
})

describe('validateRule and ruleBody', () => {
  it('accepts a complete rule and trims what it sends', () => {
    expect(validateRule(form(), FIELDS)).toBeNull()
    expect(ruleBody(form({ name: ' Hotfix ', match_pattern: ' hotfix/* ', target_release_name: ' 2.4.1 ' }))).toEqual({
      name: 'Hotfix',
      match_field: 'branch',
      match_pattern: 'hotfix/*',
      target_release_name: '2.4.1',
      priority: 10,
      is_enabled: true,
    })
  })

  it.each([
    [{ name: '  ' }, 'Name the rule.'],
    [{ match_field: 'commit' }, 'Choose a field to match.'],
    [{ match_pattern: '' }, 'Enter a pattern to match.'],
    [{ target_release_name: ' ' }, 'Name the release the rule assigns.'],
    [{ priority: '-1' }, 'Priority must be a whole number from 0 to 10,000.'],
    [{ priority: '10001' }, 'Priority must be a whole number from 0 to 10,000.'],
    [{ priority: '1.5' }, 'Priority must be a whole number from 0 to 10,000.'],
    [{ priority: '' }, 'Priority must be a whole number from 0 to 10,000.'],
  ] as const)('refuses %o', (over, message) => {
    expect(validateRule(form(over), FIELDS)).toBe(message)
  })

  it('accepts both priority bounds', () => {
    expect(validateRule(form({ priority: '0' }), FIELDS)).toBeNull()
    expect(validateRule(form({ priority: '10000' }), FIELDS)).toBeNull()
  })

  it('starts a new rule on the first field at the server default priority, and edits a rule as saved', () => {
    expect(emptyForm(FIELDS)).toEqual({
      name: '', match_field: 'branch', match_pattern: '', target_release_name: '', priority: '100', is_enabled: true,
    })
    expect(
      formFromRule({
        id: 'r1', project_id: 'p1', name: 'RC', match_field: 'tag', match_pattern: 'rc-*',
        target_release_name: '2.5', priority: 5, is_enabled: false, created_at: null,
      }),
    ).toEqual({ name: 'RC', match_field: 'tag', match_pattern: 'rc-*', target_release_name: '2.5', priority: '5', is_enabled: false })
  })

  it('shows tags as one cell', () => {
    expect(tagsText(['rc-1', 'nightly'])).toBe('rc-1, nightly')
    expect(tagsText([])).toBe('—')
    expect(tagsText(undefined)).toBe('—')
  })
})
