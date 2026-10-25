import { describe, expect, it } from 'vitest'

import { LIBRARY_HEALTH_FIXTURE } from '@/test/testLibraryMocks'
import type { LibraryHealth } from '@/types/test-library'
import {
  automatedPctText,
  bannerFor,
  componentLine,
  healthComponent,
  HEALTH_LOAD_ERROR_TEXT,
  HEALTH_NEEDS_PROJECT_TEXT,
  LIBRARY_EMPTY_TITLE,
} from './libraryHealthModel'

const health = (overrides: Partial<LibraryHealth> = {}): LibraryHealth => ({ ...LIBRARY_HEALTH_FIXTURE, ...overrides })

describe('libraryHealthModel: the banner (W13 TCL-03)', () => {
  it('reads the score, the tag and the facts source from the server answer', () => {
    expect(bannerFor({ projectId: 'project-1', health: health() })).toEqual({
      state: 'warn',
      pillLabel: 'NEEDS ATTENTION',
      title: 'Library health 70/100',
      factsFrom: LIBRARY_HEALTH_FIXTURE,
    })
    expect(bannerFor({ projectId: 'p', health: health({ score: 91, tag: 'Healthy' }) }).state).toBe('ok')
    expect(bannerFor({ projectId: 'p', health: health({ score: 41, tag: 'At risk' }) })).toMatchObject({ state: 'fail', pillLabel: 'AT RISK' })
  })

  it('All Projects asks nothing and says why: pending, no facts', () => {
    expect(bannerFor({ projectId: null, health: undefined })).toEqual({
      state: 'pending', title: HEALTH_NEEDS_PROJECT_TEXT, factsFrom: null,
    })
    expect(HEALTH_NEEDS_PROJECT_TEXT).toBe('Choose a project: library health is per project.')
  })

  it('loading and failed reads are pending with no facts, never zeros', () => {
    expect(bannerFor({ projectId: 'p', health: undefined })).toMatchObject({ state: 'pending', factsFrom: null })
    expect(bannerFor({ projectId: 'p', health: undefined, error: new Error('x') })).toEqual({
      state: 'pending', title: HEALTH_LOAD_ERROR_TEXT, factsFrom: null,
    })
    expect(HEALTH_LOAD_ERROR_TEXT).toBe('Could not load library health.')
  })

  it('an unmeasured library (no authored case) is pending: "No authored test cases yet"', () => {
    const empty = health({ measured: false, score: null, tag: null, total_cases: 0 })
    expect(bannerFor({ projectId: 'p', health: empty })).toEqual({ state: 'pending', title: LIBRARY_EMPTY_TITLE, factsFrom: null })
    expect(LIBRARY_EMPTY_TITLE).toBe('No authored test cases yet')
  })
})

describe('libraryHealthModel: the component lines', () => {
  it('prints the label, the rounded value and the server sentence with both numbers', () => {
    const automation = healthComponent(health(), 'automation')
    expect(automation && componentLine(automation)).toBe('Automated 33 · 1 of 3 cases automated (target 60 %)')
    const review = healthComponent(health(), 'review_freshness')
    expect(review && componentLine(review)).toBe('Review freshness 64 · the oldest of 1 review has waited 5 days (target under 14)')
  })

  it('an unmeasured component says "not measured", never 0', () => {
    expect(componentLine({ key: 'stale_drafts', label: 'Stale drafts', weight: 0.2, measured: false, value: null, detail: '' }))
      .toBe('Stale drafts · not measured')
  })

  it('the Automated percentage is the rounded component value, or not measured', () => {
    expect(automatedPctText(health())).toBe('33%')
    expect(automatedPctText(undefined)).toBe('not measured')
    expect(automatedPctText(health({ components: [] }))).toBe('not measured')
  })
})
