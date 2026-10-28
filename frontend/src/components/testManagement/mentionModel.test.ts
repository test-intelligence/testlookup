/**
 * W21d ACT-02: the composer's pure `@mention` model. The token-start samples
 * mirror the server's `find_mentions` set (`backend/tests/test_activity_mentions.py`):
 * an `@` begins a mention at the start or after a character outside
 * `[A-Za-z0-9_.@-]`; suggestions follow the server's member rule.
 */
import { describe, expect, it } from 'vitest'
import {
  activeMentionQuery,
  insertMention,
  isMentionable,
  MAX_SUGGESTIONS,
  mentionOptions,
  postedToast,
  type MentionMember,
} from './mentionModel'

const at = (text: string) => activeMentionQuery(text, text.length)

describe('activeMentionQuery (the server sample set, caret at the end)', () => {
  it.each([
    ['@alice', { start: 0, query: 'alice' }],
    ['please @alice', { start: 7, query: 'alice' }],
    ['(@alice', { start: 1, query: 'alice' }],
    ['@Alice', { start: 0, query: 'Alice' }],
    ['thanks @alice.', { start: 7, query: 'alice.' }],
    ['@alice.smith', { start: 0, query: 'alice.smith' }],
    ['@bob@corp.com', { start: 0, query: 'bob@corp.com' }],
    ['@carol-ops', { start: 0, query: 'carol-ops' }],
    ['@dan_qa', { start: 0, query: 'dan_qa' }],
    ['line one\n@bob', { start: 9, query: 'bob' }],
    ['@', { start: 0, query: '' }],
    // The second "@" is inside a word; the first starts a token whose text matches no username.
    ['@@alice', { start: 0, query: '@alice' }],
  ])('%s → a token', (text, expected) => {
    expect(at(text)).toEqual(expected)
  })

  it.each([
    ['email@alice'],
    ['a.b@alice'],
    ['first.@alice'],
    ['first-@alice'],
    ['x_y@bob'],
    ['@ alice'],
    ['@alice '],
    ['no mention at all'],
    [''],
  ])('%s → no token', (text) => {
    expect(at(text)).toBeNull()
  })

  it('reads only up to the caret and clamps an out-of-range caret', () => {
    expect(activeMentionQuery('@alice and more', 3)).toEqual({ start: 0, query: 'al' })
    expect(activeMentionQuery('@al', 99)).toEqual({ start: 0, query: 'al' })
    expect(activeMentionQuery('@al', -4)).toBeNull()
  })

  it('a token longer than any username is no mention', () => {
    expect(at(`@${'a'.repeat(101)}`)).toBeNull()
    expect(at(`@${'a'.repeat(100)}`)).toEqual({ start: 0, query: 'a'.repeat(100) })
  })
})

const SELF = 'u-self'
const MEMBERS: MentionMember[] = [
  { user_id: 'u-alice', username: 'alice', full_name: 'Alice Ng', is_active: true },
  { user_id: 'u-alsmith', username: 'alice.smith', full_name: 'Alice Smith' },
  { user_id: 'u-bob', username: 'bob', full_name: null },
  { user_id: 'u-kal', username: 'kal', full_name: 'Malia Kalani' },
  { user_id: SELF, username: 'albert', full_name: 'Albert Me' },
  { user_id: 'u-old', username: 'alma', full_name: 'Alma Gone', is_active: false },
  { user_id: 'u-syn', username: 'alqa', full_name: 'Default QA lead', is_synthetic: true },
  { user_id: 'u-svc', username: 'alsvc', full_name: 'CI bot', is_service_account: true },
]

describe('mentionOptions', () => {
  it('prefix matches first (username or full name), then substring, never the ineligible', () => {
    expect(mentionOptions(MEMBERS, 'al', SELF)).toEqual([
      { username: 'alice', label: 'Alice Ng · @alice' },
      { username: 'alice.smith', label: 'Alice Smith · @alice.smith' },
      { username: 'kal', label: 'Malia Kalani · @kal' },
    ])
  })

  it('matches the full name and is case-insensitive', () => {
    expect(mentionOptions(MEMBERS, 'SMI', SELF).map(o => o.username)).toEqual(['alice.smith'])
    expect(mentionOptions(MEMBERS, 'malia', SELF).map(o => o.username)).toEqual(['kal'])
  })

  it('a member without a full name is labelled by the username alone', () => {
    expect(mentionOptions(MEMBERS, 'bo', SELF)).toEqual([{ username: 'bob', label: '@bob' }])
  })

  it('an empty query lists the first six eligible members; no members lists nothing', () => {
    const many = Array.from({ length: 9 }, (_, i) => ({ user_id: `u${i}`, username: `user${String.fromCharCode(97 + i)}` }))
    expect(mentionOptions(many, '', null)).toHaveLength(MAX_SUGGESTIONS)
    expect(mentionOptions(undefined, '', null)).toEqual([])
    expect(mentionOptions(MEMBERS, 'zz', SELF)).toEqual([])
  })

  it('isMentionable is the server rule', () => {
    expect(MEMBERS.filter(m => isMentionable(m, SELF)).map(m => m.username)).toEqual(['alice', 'alice.smith', 'bob', 'kal'])
  })
})

describe('insertMention', () => {
  it('replaces the token with @username and a space, caret after it', () => {
    expect(insertMention('hi @al', 3, 6, 'alice')).toEqual({ text: 'hi @alice ', caret: 10 })
  })

  it('keeps the text after the caret and reuses an existing space', () => {
    expect(insertMention('@al please', 0, 3, 'alice')).toEqual({ text: '@alice please', caret: 7 })
    expect(insertMention('@al,ok', 0, 3, 'alice')).toEqual({ text: '@alice ,ok', caret: 7 })
  })
})

describe('postedToast', () => {
  it('names who was notified', () => {
    expect(postedToast([])).toBe('Comment added')
    expect(postedToast(undefined)).toBe('Comment added')
    expect(postedToast([
      { user_id: 'u-alice', username: 'alice', display_name: 'Alice Ng' },
      { user_id: 'u-bob', username: 'bob', display_name: 'bob' },
    ])).toBe('Comment added · notified @alice, @bob')
  })

  it('a malformed answer reads as no mentions', () => {
    expect(postedToast({} as never)).toBe('Comment added')
    expect(postedToast([{ user_id: 'x' } as never])).toBe('Comment added')
  })
})
