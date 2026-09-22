/**
 * VIZ-109: the one download helper. The object URL is revoked every time —
 * also when the click throws — and only after the click has been handed to
 * the browser; the filename it offers is safe on every desktop OS.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { FALLBACK_FILENAME, MAX_FILENAME_LENGTH, REVOKE_DELAY_MS, downloadBlob, sanitizeFilename } from './download'

it('revokes after 40 s (FileSaver.js precedent)', () => {
  expect(REVOKE_DELAY_MS).toBe(40_000)
})

describe('downloadBlob', () => {
  const created: Blob[] = []
  let revokeObjectURL: ReturnType<typeof vi.fn>
  let clicks: Array<{ href: string; download: string; connected: boolean }>

  beforeEach(() => {
    vi.useFakeTimers()
    created.length = 0
    clicks = []
    const createObjectURL = vi.fn((blob: Blob) => {
      created.push(blob)
      return `blob:test/${created.length}`
    })
    revokeObjectURL = vi.fn()
    // jsdom implements neither.
    Object.defineProperty(URL, 'createObjectURL', { value: createObjectURL, configurable: true, writable: true })
    Object.defineProperty(URL, 'revokeObjectURL', { value: revokeObjectURL, configurable: true, writable: true })
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      clicks.push({ href: this.href, download: this.download, connected: this.isConnected })
    })
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('clicks an attached anchor for the blob, then removes it and revokes the URL after the click', () => {
    const blob = new Blob(['a,b\n1,2\n'], { type: 'text/csv' })
    downloadBlob(blob, 'trend.csv')

    expect(created).toEqual([blob])
    expect(clicks).toEqual([{ href: 'blob:test/1', download: 'trend.csv', connected: true }])
    // The anchor is gone at once...
    expect(document.querySelector('a[download]')).toBeNull()
    // ...but the URL survives the click's task: revoking synchronously cancels
    // the download in some browsers.
    expect(revokeObjectURL).not.toHaveBeenCalled()
    vi.runAllTimers()
    expect(revokeObjectURL).toHaveBeenCalledTimes(1)
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:test/1')
  })

  it('still removes the anchor and revokes the URL when the click throws', () => {
    vi.mocked(HTMLAnchorElement.prototype.click).mockImplementation(() => {
      throw new Error('blocked by the browser')
    })
    expect(() => downloadBlob(new Blob(['x']), 'x.txt')).toThrow('blocked by the browser')
    expect(document.querySelector('a[download]')).toBeNull()
    vi.runAllTimers()
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:test/1')
  })

  it('keeps the object URL alive for 40 s before revoking it (review fix 13)', () => {
    // Safari and older Firefox read the blob AFTER the click's task; an early
    // revoke loses the download. 40 s is FileSaver.js's figure.
    downloadBlob(new Blob(['x']), 'x.txt')
    vi.advanceTimersByTime(0)
    expect(revokeObjectURL).not.toHaveBeenCalled()
    vi.advanceTimersByTime(REVOKE_DELAY_MS - 1)
    expect(revokeObjectURL).not.toHaveBeenCalled()
    vi.advanceTimersByTime(1)
    expect(revokeObjectURL).toHaveBeenCalledTimes(1)
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:test/1')
  })

  it('revokes on the same 40 s schedule when the click throws', () => {
    vi.mocked(HTMLAnchorElement.prototype.click).mockImplementation(() => {
      throw new Error('blocked by the browser')
    })
    expect(() => downloadBlob(new Blob(['x']), 'x.txt')).toThrow('blocked by the browser')
    vi.advanceTimersByTime(REVOKE_DELAY_MS - 1)
    expect(revokeObjectURL).not.toHaveBeenCalled()
    vi.advanceTimersByTime(1)
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:test/1')
  })

  it('offers the sanitised filename, not the raw one', () => {
    downloadBlob(new Blob(['x']), 'releases/R2: payments?.csv')
    expect(clicks[0].download).toBe('releases_R2_ payments_.csv')
    vi.runAllTimers()
  })
})

describe('sanitizeFilename', () => {
  it.each<[string, string]>([
    ['trend.csv', 'trend.csv'],
    ['release R2 — payments.json', 'release R2 — payments.json'],
    ['a/b\\c.csv', 'a_b_c.csv'],
    ['../up/one.csv', '_up_one.csv'],
    ['what?*<>|":.csv', 'what_______.csv'],
    ['tab\there.csv', 'tabhere.csv'],
    [`nul${String.fromCharCode(0)}byte.csv`, 'nulbyte.csv'],
    [`bell${String.fromCharCode(7)}.csv`, 'bell.csv'],
    [`c1${String.fromCharCode(0x85)}.csv`, 'c1.csv'],
    // A bidi override would make the name display in a different order.
    [`report${String.fromCharCode(0x202e)}vsc.txt`, 'reportvsc.txt'],
    [`iso${String.fromCharCode(0x2067)}late.csv`, 'isolate.csv'],
    ['.hidden', 'hidden'],
    ['...env', 'env'],
    ['trailing. . .', 'trailing'],
    ['  padded.csv  ', 'padded.csv'],
    ['CON', '_CON'],
    ['con.txt', '_con.txt'],
    ['Lpt1.tar.gz', '_Lpt1.tar.gz'],
    ['com9', '_com9'],
    ['console.log', 'console.log'],
    ['aux-report.csv', 'aux-report.csv'],
  ])('%j → %j', (input, expected) => {
    expect(sanitizeFilename(input)).toBe(expected)
  })

  it.each(['', '   ', '...', '. . .', String.fromCharCode(1, 2, 3)])(
    '%j sanitises to nothing and falls back',
    (input) => {
      expect(sanitizeFilename(input)).toBe(FALLBACK_FILENAME)
      expect(sanitizeFilename(input, 'export.csv')).toBe('export.csv')
      // A fallback that is itself unusable still yields a usable name.
      expect(sanitizeFilename(input, '...')).toBe(FALLBACK_FILENAME)
    },
  )

  it('caps the length and keeps the extension', () => {
    const out = sanitizeFilename(`${'r'.repeat(400)}.xlsx`)
    expect(Array.from(out)).toHaveLength(MAX_FILENAME_LENGTH)
    expect(out.endsWith('.xlsx')).toBe(true)
  })

  it('caps by code point, never splitting a surrogate pair', () => {
    const smile = String.fromCodePoint(0x1f600)
    const out = sanitizeFilename(`${smile.repeat(200)}.csv`)
    expect(Array.from(out)).toHaveLength(MAX_FILENAME_LENGTH)
    expect(out.endsWith(`${smile}.csv`)).toBe(true)
  })

  it('treats a long dotted tail as part of the name, not an extension', () => {
    const out = sanitizeFilename(`report.${'v'.repeat(300)}`)
    expect(Array.from(out)).toHaveLength(MAX_FILENAME_LENGTH)
    expect(out.startsWith('report.')).toBe(true)
  })
})
