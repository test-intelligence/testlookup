/**
 * W28 CHAT-01: "Hide suggestions" is remembered per viewer in this browser, and
 * a blocked storage never breaks the chat (the row shows, Hide still hides it
 * for the session).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { HIDE_SUGGESTIONS_KEY, readHideSuggestions, writeHideSuggestions } from './chatSuggestionsPreference'

describe('chatSuggestionsPreference', () => {
  beforeEach(() => window.localStorage.clear())
  afterEach(() => {
    vi.restoreAllMocks()
    window.localStorage.clear()
  })

  it('uses the one key, tl.chat.hideSuggestions', () => {
    expect(HIDE_SUGGESTIONS_KEY).toBe('tl.chat.hideSuggestions')
  })

  it('reads false when nothing is stored, and only "1" reads hidden', () => {
    expect(readHideSuggestions()).toBe(false)
    for (const [stored, hidden] of [['1', true], ['0', false], ['true', false], ['', false]] as const) {
      window.localStorage.setItem(HIDE_SUGGESTIONS_KEY, stored)
      expect(readHideSuggestions(), `stored ${JSON.stringify(stored)}`).toBe(hidden)
    }
  })

  it('writes "1" for hidden and "0" for shown, and reads them back', () => {
    writeHideSuggestions(true)
    expect(window.localStorage.getItem(HIDE_SUGGESTIONS_KEY)).toBe('1')
    expect(readHideSuggestions()).toBe(true)
    writeHideSuggestions(false)
    expect(window.localStorage.getItem(HIDE_SUGGESTIONS_KEY)).toBe('0')
    expect(readHideSuggestions()).toBe(false)
  })

  it('a throwing getItem reads false', () => {
    window.localStorage.setItem(HIDE_SUGGESTIONS_KEY, '1')
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new DOMException('blocked', 'SecurityError') })
    expect(readHideSuggestions()).toBe(false)
  })

  it('a throwing setItem does not throw', () => {
    const setItem = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new DOMException('full', 'QuotaExceededError') })
    expect(() => writeHideSuggestions(true)).not.toThrow()
    expect(() => writeHideSuggestions(false)).not.toThrow()
    expect(setItem).toHaveBeenCalledTimes(2)
  })

  it('a window whose localStorage itself throws (blocked site data) reads false and writes nothing', () => {
    vi.spyOn(window, 'localStorage', 'get').mockImplementation(() => { throw new DOMException('denied', 'SecurityError') })
    expect(readHideSuggestions()).toBe(false)
    expect(() => writeHideSuggestions(true)).not.toThrow()
  })
})
