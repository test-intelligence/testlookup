/** W21d ACT-02: Inbox › Activity's copy and rules (one string per state). */
import { describe, expect, it } from 'vitest'

import {
  DEFAULT_VIEW,
  LOAD_ERROR,
  LOAD_OLDER_ERROR,
  VIEW_OPTIONS,
  countLine,
  emptySentence,
  windowPhrase,
} from './activityTabModel'

describe('activityTabModel', () => {
  it('offers Mine · Involving me · All, defaulting to All', () => {
    expect(VIEW_OPTIONS.map((o) => [o.id, o.label])).toEqual([
      ['mine', 'Mine'],
      ['involving', 'Involving me'],
      ['all', 'All'],
    ])
    expect(DEFAULT_VIEW).toBe('all')
  })

  it('counts the server total over the window, singular and plural', () => {
    expect(countLine(37, 7)).toBe('37 events in the last 7 days')
    expect(countLine(1, 1)).toBe('1 event in the last 24 hours')
    expect(countLine(1234, 30)).toBe('1,234 events in the last 30 days')
    expect(countLine(0, 14)).toBe('0 events in the last 14 days')
    expect(windowPhrase(14)).toBe('14 days')
  })

  it('says why a list is empty, per view, and says "no project" before any of them', () => {
    expect(emptySentence('mine', 3, 7)).toBe('Nothing you did in the last 7 days.')
    expect(emptySentence('involving', 3, 7)).toBe('Nothing involving you in the last 7 days.')
    expect(emptySentence('all', null, 7)).toBe('No activity in the last 7 days.')
    expect(emptySentence('all', 2, 1)).toBe('No activity in the last 24 hours.')
    expect(emptySentence('mine', 0, 7)).toBe('You are not a member of any project yet.')
    expect(emptySentence('involving', 0, 30)).toBe('You are not a member of any project yet.')
  })

  it('words a failed read the one way (UIX-11)', () => {
    expect(LOAD_ERROR).toBe('Could not load the activity.')
    expect(LOAD_OLDER_ERROR).toBe('Could not load older activity')
  })
})
