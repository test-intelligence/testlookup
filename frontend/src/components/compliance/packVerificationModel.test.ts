import { describe, expect, it } from 'vitest'

import {
  chipText,
  nonOkLines,
  RESULT_TONE_CLASS,
  resultTone,
  STATUS_PHRASES,
  UNSIGNED_CHIP,
} from './packVerificationModel'

const verification = (over: Record<string, unknown> = {}) => ({
  result: 'verified',
  files_checked: 12,
  files_ok: 12,
  files: [{ name: 'decision.json', status: 'ok' }],
  ...over,
})

describe('chipText', () => {
  it('names the signing key by its first ten characters, the full id in the title', () => {
    expect(chipText({ signing_key_id: 'k-1a2b3c4d5e6f7a8b' })).toEqual({
      text: 'Signed · k-1a2b3c4d',
      title: 'k-1a2b3c4d5e6f7a8b',
      signed: true,
    })
  })

  it('reads null as the server saying unsigned', () => {
    expect(chipText({ signing_key_id: null })).toMatchObject({ text: UNSIGNED_CHIP, signed: false })
    expect(UNSIGNED_CHIP).toBe('Unsigned (tamper-evident only)')
  })

  it('shows nothing when the field is absent (the server did not say)', () => {
    expect(chipText({})).toBeNull()
  })
})

describe('resultTone', () => {
  it.each([
    ['verified', 'success', 'text-[var(--ui-success-ink)]'],
    ['tampered', 'danger', 'text-[var(--ui-danger-ink)]'],
    ['unreadable', 'muted', 'text-[var(--color-text-muted)]'],
    ['something new', 'muted', 'text-[var(--color-text-muted)]'],
  ])('%s -> %s', (result, tone, cls) => {
    expect(resultTone({ result })).toBe(tone)
    expect(RESULT_TONE_CLASS[resultTone({ result })]).toBe(cls)
  })

  it('never reads a --status-* token', () => {
    expect(Object.values(RESULT_TONE_CLASS).join(' ')).not.toMatch(/--status-/)
  })
})

describe('nonOkLines', () => {
  it('counts the matches when every file is ok', () => {
    expect(nonOkLines(verification())).toEqual(['12 of 12 files match'])
  })

  it('names each file that is not ok, in the server order', () => {
    const lines = nonOkLines(verification({
      result: 'tampered',
      files_ok: 10,
      files: [
        { name: 'decision.json', status: 'mismatch' },
        { name: 'clusters.json', status: 'missing' },
        { name: 'README.md', status: 'ok' },
        { name: 'notes.txt', status: 'unexpected' },
      ],
    }))
    expect(lines).toEqual([
      'decision.json — does not match the manifest',
      'clusters.json — is missing from the pack',
      'notes.txt — is not listed in the manifest',
    ])
  })

  it('says nothing about files for an unreadable pack', () => {
    expect(nonOkLines(verification({ result: 'unreadable', files: [], files_ok: 0, files_checked: 0 }))).toEqual([])
  })

  it('words the server statuses as the CLI and the MCP tool do', () => {
    expect(STATUS_PHRASES).toEqual({
      mismatch: 'does not match the manifest',
      missing: 'is missing from the pack',
      unexpected: 'is not listed in the manifest',
    })
  })
})
